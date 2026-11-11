"""lx-deploy config operations."""

import datetime
import difflib
import shutil

from . import checks as health_checks
from . import git as git_ops
from . import layout, system
from . import state as state_store


def config_files():
    """(file in the repository, file on the server, mode). apply-config is the only writer."""
    files = [
        ("server/compose.yaml", layout.L.etc / "compose.yaml", 0o644),
        ("caddy/Caddyfile", layout.L.etc / "caddy" / "Caddyfile", 0o644),
        ("caddy/sites.caddy", layout.L.etc / "caddy" / "sites.caddy", 0o644),
        ("caddy/common.caddy", layout.L.etc / "caddy" / "common.caddy", 0o644),
        ("caddy/account.caddy", layout.L.etc / "caddy" / "account.caddy", 0o644),
        ("server/watch.json", layout.L.etc / "watch.json", 0o600),
        ("server/monitor/watch.py", layout.L.monitor / "watch.py", 0o755),
        ("server/lx-deploy", layout.L.program, 0o755),
    ]
    for name in ('__init__.py', 'backup.py', 'checks.py', 'commands.py', 'config.py', 'deploy.py', 'git.py', 'layout.py', 'release.py', 'reporting.py', 'secrets.py', 'state.py', 'system.py', 'www.py'):
        files.append((f"server/lxdeploy/{name}", layout.L.program.parent / "lxdeploy" / name, 0o644))

    units = layout.L.repo / "server" / "systemd"
    if units.is_dir():
        for unit in sorted(units.iterdir()):
            if unit.suffix in (".service", ".timer"):
                files.append((f"server/systemd/{unit.name}", layout.L.systemd / unit.name, 0o644))
    return files


def config_drift():
    return [
        (source, target, mode)
        for source, target, mode in config_files()
        if (layout.L.repo / source).exists()
        and (not target.exists() or target.read_bytes() != (layout.L.repo / source).read_bytes())
    ]


def show_difference(source, target):
    new = source.read_text(encoding="utf-8", errors="replace").splitlines()
    old = target.read_text(encoding="utf-8", errors="replace").splitlines() if target.exists() else []
    diff = list(
        difflib.unified_diff(
            old, new, str(target), f"repository {source.relative_to(layout.L.repo)}", lineterm="", n=2
        )
    )
    for line in diff[:300]:
        system.say(line)
    if len(diff) > 300:
        system.say(f"... and {len(diff) - 300} more lines")


def cmd_apply_config(server, yes=False):
    git_ops.fetch(server)
    changes = config_drift()
    main_caddyfile = not layout.L.caddyfile.exists() or layout.L.caddyfile.read_text() != layout.MAIN_CADDYFILE
    if not changes and not main_caddyfile:
        system.say("The server already has every file in the repository. Nothing to do.")
        return 0
    for source, target, _ in changes:
        system.say(f"\n=== {target}" + ("" if target.exists() else " (new)"))
        show_difference(layout.L.repo / source, target)
    if main_caddyfile:
        system.say(f"\n=== {layout.L.caddyfile}: will contain only the line `{layout.MAIN_CADDYFILE.strip()}`")
    system.say("")
    if not yes:
        answer = input("Apply these changes? Type yes to apply: ").strip().lower()
        if answer != "yes":
            system.say("Nothing was changed.")
            return 1
    saved = layout.L.state / "config-backups" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    saved.mkdir(parents=True, exist_ok=True)
    targets = [target for _, target, _ in changes] + ([layout.L.caddyfile] if main_caddyfile else [])
    existed = {}
    for target in targets:
        existed[target] = target.exists()
        if target.exists():
            copy = saved / target.relative_to(target.anchor)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, copy)

    def restore():
        for target in targets:
            copy = saved / target.relative_to(target.anchor)
            if existed[target]:
                shutil.copy2(copy, target)
            else:
                target.unlink(missing_ok=True)

    for source, target, mode in changes:
        state_store.atomic_write(target, (layout.L.repo / source).read_text(encoding="utf-8"), mode)
    if main_caddyfile:
        state_store.atomic_write(layout.L.caddyfile, layout.MAIN_CADDYFILE, 0o644)
    try:
        server.sh(["caddy", "validate", "--adapter", "caddyfile", "--config", str(layout.L.caddyfile)])
        if (layout.L.etc / ".env").exists():
            server.compose("config", "--quiet")
    except system.CommandFailed as error:
        restore()
        system.say(f"A new file did not validate ({error}). The previous files are back; nothing changed.")
        return 1
    server.sh(["systemctl", "daemon-reload"])
    for target in targets:
        if target.suffix == ".timer" and not existed[target]:
            server.sh(["systemctl", "enable", "--now", target.name])
    if any(target.parent in (layout.L.etc / "caddy", layout.L.caddyfile.parent) for target in targets):
        server.sh(["systemctl", "reload-or-restart", "caddy"])
    if layout.L.etc / "compose.yaml" in targets and (layout.L.etc / ".env").exists():
        server.compose("up", "-d", "--wait", "--wait-timeout", "180", *layout.SERVICES, check=False, timeout=600)
    system.say(f"Applied {len(targets)} file(s). The old ones are in {saved}.")
    if (layout.L.etc / ".env").exists():
        return health_checks.cmd_check(server, "basic")
    return 0
