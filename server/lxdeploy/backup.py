"""lx-deploy backup operations."""

import contextlib
import os
import sqlite3
import subprocess

from . import layout, system
from . import state as state_store


def backup(server, name):
    """Runs the service's own backup command (its image's README) into /srv/lx/backups/<name>."""
    folder = layout.L.srv / "backups" / name
    folder.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        os.chown(folder, layout.APP_UID, layout.APP_UID)
    os.chmod(folder, 0o700)
    commands = (layout.SERVICES.get(name) or layout.JOBS[name])["backup"]
    for command in commands:
        server.compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--volume",
            f"{folder}:/data/backups",
            name,
            *command,
            timeout=3600,
        )


def newest(folder, pattern):
    files = [path for path in folder.glob(pattern) if path.is_file()]
    return max(files, key=lambda path: (path.stat().st_mtime, path.name)) if files else None


def counts(database, tables, integrity=False):
    with contextlib.closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as db:
        if integrity and db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("integrity check failed")
        return {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}


def cmd_backup(server, which):
    names = layout.KNOWN if which == "all" else [which]
    env = state_store.read_env(layout.L.etc / ".env")
    failed = False
    for name in names:
        if name in layout.JOBS:
            data = layout.L.srv / name
            if f"LX_IMAGE_{name.upper()}" not in env or not data.is_dir() or not any(data.iterdir()):
                system.say(f"{name}: not set up yet; skipped")
                continue
        try:
            backup(server, name)
            system.say(f"{name}: backed up")
        except (system.CommandFailed, OSError, subprocess.TimeoutExpired) as error:
            reason = error if isinstance(error, system.CommandFailed) else type(error).__name__
            system.say(f"{name}: BACKUP FAILED ({reason})")
            failed = True
    return 1 if failed else 0
