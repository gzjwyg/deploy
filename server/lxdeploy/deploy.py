"""lx-deploy deploy operations."""

import subprocess
from pathlib import Path

from . import backup as backups
from . import checks as health_checks
from . import config as configuration
from . import git as git_ops
from . import layout, reporting, system
from . import release as release_rules
from . import state as state_store
from . import www as static_files


def start(server, images, commit, links):
    state_store.write_dotenv(images, commit)
    for name, target in links.items():
        static_files.switch_link(layout.L.www / name, target)
    return (
        server.compose(
            "up", "-d", "--wait", "--wait-timeout", "180", *layout.SERVICES, check=False, timeout=600
        ).returncode
        == 0
    )


def cmd_run(server, force=False):
    commit = git_ops.fetch(server)
    text = (layout.L.repo / "release.json").read_text() if (layout.L.repo / "release.json").exists() else None
    release, errors = release_rules.validate_release(text)
    state = state_store.load_state()
    key = f"{commit} {release['run'] if release else ''}"
    if state.get("handled") == key and not force:
        return 0
    state["handled"] = key
    state_store.save_state(state)
    if errors:
        reporting.report(commit, "failure", "release.json refused; nothing was changed", errors)
        return 1
    try:
        return deploy(server, commit, release, state, force)
    except Exception as error:
        reporting.report(
            commit,
            "error",
            f"lx-deploy stopped unexpectedly ({type(error).__name__}); "
            "the owner can look with: sudo journalctl -u lx-deploy",
        )
        raise


def deploy(server, commit, release, state, force):
    drift = configuration.config_drift()
    note = (
        [
            f"{len(drift)} server file(s) changed in the repository and wait for the owner: "
            "sudo lx-deploy apply-config"
        ]
        if drift
        else []
    )
    current = state.get("current", {}).get("images", {})
    images = release["images"]
    changed = [name for name in images if current.get(name) != images[name]]
    try:
        home_changed = static_files.sync_home()
    except (ValueError, OSError) as error:
        reporting.report(commit, "failure", "Not deployed: home/ was refused; nothing was changed", [str(error), *note])
        return 1
    if not changed and release["run"] == state.get("run") and not force:
        state_store.save_state(state)
        lines = note + (["the homepage was updated"] if home_changed else [])
        reporting.report(commit, "success", "No version changed", lines, comment=bool(lines))
        return 0
    reporting.report(commit, "pending", "Deploying")
    try:
        for name in changed:
            server.sh(["docker", "pull", "--quiet", images[name]], timeout=1800)
        if current:
            for name in changed:
                if name in layout.SERVICES:
                    backups.backup(server, name)
        links = {
            name: static_files.extract_www(server, name, images[name])
            for name in changed
            if layout.SERVICES.get(name, {}).get("www")
        }
    except (system.CommandFailed, ValueError, OSError, subprocess.TimeoutExpired) as error:
        state_store.save_state(state)
        reporting.report(
            commit,
            "failure",
            "Not deployed: preparing the new versions failed; nothing was changed",
            [str(error) if isinstance(error, (system.CommandFailed, ValueError)) else type(error).__name__, *note],
        )
        return 1
    old_links = {name: static_files.link_target(layout.L.www / name) for name in links}
    started = start(server, images, commit, links)
    checks = health_checks.run_checks(server, release["checks"])
    lines = reporting.version_lines(images, changed) + [f"checks: {release['checks']}"] + reporting.check_lines(checks) + note
    if started and checks.passed:
        state.update(
            previous=state.get("current"), current={"commit": commit, "images": images}, run=release["run"]
        )
        state_store.save_state(state)
        static_files.prune(server, [images, current])
        reporting.report(commit, "success", f"Deployed and checked ({release['checks']})", lines)
        return 0
    if not started:
        lines.insert(0, "FAIL: the new versions did not all start healthy")
    if not current:
        state_store.save_state(state)
        reporting.report(commit, "failure", "The first deployment failed its checks; nothing to go back to", lines)
        return 1
    back = {name: Path(target) for name, target in old_links.items() if target}
    restarted = start(server, current, state["current"]["commit"], back)
    after = health_checks.run_checks(server, "basic")
    lines += ["", "Put the previous versions back:"] + reporting.version_lines(current) + reporting.check_lines(after)
    if not restarted:
        lines.append("FAIL: the previous versions did not all start healthy either")
    state_store.save_state(state)
    reporting.report(commit, "failure", "Checks failed; the previous versions are running again", lines)
    return 1


def cmd_rollback(server):
    state = state_store.load_state()
    previous = (state.get("previous") or {}).get("images")
    if not previous:
        system.say("There is no previous release to go back to.")
        return 1
    links = {
        name: static_files.extract_www(server, name, previous[name])
        for name in layout.SERVICES
        if layout.SERVICES[name]["www"] and name in previous
    }
    started = start(server, previous, state["previous"]["commit"], links)
    checks = health_checks.run_checks(server, "basic")
    lines = reporting.version_lines(previous) + reporting.check_lines(checks)
    if started and checks.passed:
        state["previous"], state["current"] = state["current"], state["previous"]
        state_store.save_state(state)
        reporting.report(state["current"]["commit"], "success", "Rolled back by the owner", lines)
        return 0
    reporting.report(state["previous"]["commit"], "failure", "Rollback by the owner failed its checks", lines)
    return 1
