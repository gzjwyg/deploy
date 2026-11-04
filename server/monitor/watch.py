#!/usr/bin/env python3
"""Host-only monitoring. SMTP is reused from account env; never import account/content data."""

import argparse
import glob
import io
import json
import os
import shutil
import smtplib
import sqlite3
import ssl
import subprocess
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path

SERVICE_NAMES = {"shared-account", "rat-webs", "nobody", "articledb", "concept-map", "11111"}
SIX_HOURS = 6 * 3600


def alerts(metrics):
    """Pure counts-to-alerts boundary. No log text/URL/user field can become mail content."""
    result = []
    for disk in metrics["disks"]:
        if disk["percent"] >= 80:
            level = 90 if disk["percent"] >= 90 else 80
            result.append(
                (
                    f"disk:{disk['name']}:{level}",
                    f"Disk {disk['name']} is {disk['percent']:.0f}% full (threshold {level}%). First: df -h; then inspect backup/cache sizes. Never delete live data.",
                )
            )
    for service in metrics["services"]:
        name = service["name"]
        if name not in SERVICE_NAMES:
            raise ValueError("Unknown monitored service")
        command = "sudo docker compose --project-directory /etc/lx-deploy logs --since 1h " + name
        errors, crashes = service["errors"], service["crashes"]
        if errors is None or crashes is None:
            result.append(
                (
                    name + ":observations",
                    f"{name}: error/crash counts unavailable. Check Caddy access log and Docker permissions; then {command}",
                )
            )
        elif errors + crashes > 20:
            result.append(
                (
                    name + ":errors",
                    f"{name}: {errors} HTTP 5xx and {crashes} crashes in the last hour. First: {command}",
                )
            )
        age = service["backup_age"]
        if age is None or age > 26 * 3600:
            hours = "missing" if age is None else f"{age / 3600:.1f} hours old"
            result.append(
                (
                    name + ":backup",
                    f"{name}: latest backup is {hours}. First: systemctl list-timers --all; inspect the backup job result; run the project's backup command after checking disk space.",
                )
            )
        if name == "rat-webs":
            worker = service.get("worker")
            if worker is None or not worker["running"]:
                result.append(
                    (
                        name + ":credits",
                        "rat-webs: credits stopped or heartbeat unavailable. First: run python -m server.cli ops in the Rat-webs container; repair the shared internal secret/contract before restarting rat-webs. Never print the secret.",
                    )
                )
            if worker and worker.get("failed", 0):
                result.append(
                    (
                        name + ":failed-credits",
                        f"rat-webs: {worker['failed']} rejected credits retained for operator review. First: run python -m server.cli ops; inspect the failed credits locally, never publish account IDs or keys.",
                    )
                )
            if worker and worker["oldest_age"] is not None and worker["oldest_age"] > 86400:
                result.append(
                    (
                        name + ":queue",
                        f"rat-webs: oldest pending credit is {worker['oldest_age'] / 3600:.1f} hours old. First: run python -m server.cli ops; inspect Nobody health and the credit worker status.",
                    )
                )
    return result


def deliver(problems, state, notify, now):
    """Successful delivery alone starts cooldown. Recovery is quiet; recurrence keeps cooldown."""
    sent = []
    for key, message in problems:
        if key in state and now - state[key] < SIX_HOURS:
            continue
        notify("Community server alert", message)
        state[key] = now
        sent.append(key)
    return sent


def send_mail(subject, body):
    message = EmailMessage()
    message["From"] = os.environ["ACCOUNT_SMTP_USER"]
    message["To"] = os.environ["WATCH_TO"]
    message["Subject"] = subject
    message.set_content(
        body
        + "\n\nAsk the owner / server operator; do not paste logs or secrets into a public issue.\n"
    )
    with smtplib.SMTP_SSL(
        "smtp.qq.com", 465, timeout=15, context=ssl.create_default_context()
    ) as smtp:
        smtp.login(os.environ["ACCOUNT_SMTP_USER"], os.environ["ACCOUNT_SMTP_PASSWORD"])
        smtp.send_message(message)


def newest(pattern):
    files = [Path(name) for name in glob.glob(pattern, recursive=True) if Path(name).is_file()]
    return max(files, key=lambda path: (path.stat().st_mtime, path.name)) if files else None


def caddy_errors(path, services, now, *, text=None):
    counts = {service["name"]: 0 for service in services}
    routes = sorted(services, key=lambda service: -len(service.get("prefix", "/")))
    with Path(path).open(encoding="utf-8") if text is None else io.StringIO(text) as source:
        for line in source:
            if not line.endswith("\n"):
                continue  # the active writer may not have finished the final line
            row = json.loads(line)
            if (
                row.get("ts", 0) < now - 3600
                or row.get("ts", 0) > now
                or not 500 <= row.get("status", 0) < 600
            ):
                continue
            request = row.get("request", {})
            for service in routes:
                if request.get("host") == service.get("host") and request.get("uri", "").startswith(
                    service.get("prefix", "/")
                ):
                    counts[service["name"]] += 1
                    break
    return counts


def crash_counts(services, now):
    process = subprocess.run(
        [
            "docker",
            "events",
            "--since",
            str(int(now - 3600)),
            "--until",
            str(int(now)),
            "--filter",
            "event=die",
            "--format",
            "{{json .}}",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    counts = {service["name"]: 0 for service in services}
    for line in process.stdout.splitlines():
        attributes = json.loads(line).get("Actor", {}).get("Attributes", {})
        for service in services:
            if attributes.get("com.docker.compose.service") == service.get(
                "compose_service", service["name"]
            ) and attributes.get("exitCode") not in (None, "0"):
                counts[service["name"]] += 1
    return counts


def credit_metrics(path, now):
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        row = db.execute("SELECT value FROM ops_state WHERE key='credit_worker'").fetchone()
        heartbeat = json.loads(row[0]) if row else None
        oldest = db.execute(
            "SELECT min(created_at) FROM community_credits WHERE sent=0"
        ).fetchone()[0]
    return {
        "running": bool(
            heartbeat and heartbeat["state"] == "running" and 0 <= now - heartbeat["at"] <= 120
        ),
        "oldest_age": None if oldest is None else max(0, now - oldest),
        "failed": int(heartbeat.get("failed", 0)) if heartbeat else 0,
    }


def collect(config, now):
    services = config["services"]
    try:
        if config.get("caddy_log"):
            errors = caddy_errors(config["caddy_log"], services, now)
        else:
            journal = subprocess.run(
                [
                    "journalctl",
                    "--unit",
                    config["caddy_unit"],
                    "--since",
                    "@" + str(int(now - 3600)),
                    "--until",
                    "@" + str(int(now)),
                    "--output=cat",
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            errors = caddy_errors(None, services, now, text=journal.stdout)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        errors = {}
    try:
        crashes = crash_counts(services, now)
    except (OSError, ValueError, subprocess.SubprocessError):
        crashes = {}
    metrics = {"disks": [], "services": []}
    for disk in config["disks"]:
        usage = shutil.disk_usage(disk["path"])
        metrics["disks"].append(
            {"name": disk["name"], "percent": (usage.total - usage.free) / usage.total * 100}
        )
    for service in services:
        backup = newest(service["backup_glob"])
        value = {
            "name": service["name"],
            "errors": errors.get(service["name"]),
            "crashes": crashes.get(service["name"]),
            "backup_age": None if backup is None else max(0, now - backup.stat().st_mtime),
        }
        if service["name"] == "rat-webs":
            try:
                value["worker"] = credit_metrics(service["database"], now)
            except (OSError, ValueError, TypeError, sqlite3.Error):
                value["worker"] = None
        metrics["services"].append(value)
    return metrics


def restore_sqlite(source, required_tables=()):
    """Copy a completed backup; never open or replace a live database."""
    if source is None:
        raise ValueError("Backup missing")
    with tempfile.TemporaryDirectory(prefix="lx-restore-") as folder:
        copy = Path(folder) / "restored.sqlite"
        shutil.copyfile(source, copy)
        with sqlite3.connect(copy) as db:
            if (
                db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                or db.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ValueError("Restore integrity check failed")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not set(required_tables) <= tables:
                raise ValueError("Wrong or incomplete backup")


def restore_jobs(jobs, notify):
    failed = []
    for job in jobs:
        name = job["name"]
        if name not in SERVICE_NAMES:
            raise ValueError("Unknown restore job")
        try:
            if job["kind"] == "sqlite":
                restore_sqlite(newest(job["backup_glob"]), job["tables"])
            elif job["kind"] == "command":
                # The repository's check restores in its own temporary directory. No shell,
                # output or exception strings leave the host (they may contain paths/data).
                subprocess.run(
                    job["command"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=7200,
                    check=True,
                )
            else:
                raise ValueError("Unknown restore checker")
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
            failed.append(name)
    notify(
        "Community restore test",
        "Restore test failed: "
        + ", ".join(failed)
        + ". First: inspect backup job status and the repository RUNBOOK; preserve the live data."
        if failed
        else f"Restore test passed for {len(jobs)} databases. All temporary copies deleted.",
    )
    return not failed


def save_state(path, state):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["watch", "restore"])
    parser.add_argument("--config", type=Path, default=Path("/etc/lx-deploy/watch.json"))
    parser.add_argument("--state", type=Path, default=Path("/var/lib/lx-watch/state.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    notify = (lambda subject, body: print(subject + "\n" + body)) if args.dry_run else send_mail
    try:
        config = json.loads(args.config.read_text())
        if args.operation == "restore":
            return 0 if restore_jobs(config["restore_jobs"], notify) else 1
        state = json.loads(args.state.read_text()) if args.state.exists() else {}
        pending = alerts(collect(config, time.time()))
        if args.dry_run:
            for _, message in pending:
                notify("Dry run; no email", message)
        else:
            # Persist after each successful email so a later delivery failure cannot repeat it.
            for problem in pending:
                deliver([problem], state, send_mail, time.time())
                save_state(args.state, state)
        return 0
    except Exception:
        print(
            "Monitoring failed; check configuration, permissions, disk and SMTP. No raw error or log text is printed.",
            file=__import__("sys").stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
