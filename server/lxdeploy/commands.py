"""lx-deploy: keeps the server on the versions that gzjwyg/deploy's release.json names."""

import argparse
import os

from . import backup as backups
from . import checks as health_checks
from . import config as configuration
from . import deploy as deployment
from . import layout, reporting, system
from . import secrets as provisioning
from . import state as state_store


def cmd_status(_server):
    state = state_store.load_state()
    current = state.get("current") or {}
    system.say(f"Release: {current.get('commit', 'none yet')[:12]}")
    for line in reporting.version_lines(current.get("images", {})):
        system.say(f"  {line}")
    drift = configuration.config_drift() if layout.L.repo.exists() else []
    if drift:
        system.say(f"{len(drift)} server file(s) wait for `sudo lx-deploy apply-config`.")
    return 0


def main(argv=None, server=None):
    parser = argparse.ArgumentParser(prog="lx-deploy", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="deploy release.json if it changed (the timer runs this)")
    run.add_argument("--force", action="store_true", help="deploy and check again even if nothing changed")
    apply = commands.add_parser("apply-config", help="show and apply changed server files (asks first)")
    apply.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    commands.add_parser("init-secrets", help="create missing secrets; ask for mail, admin and alert settings")
    check = commands.add_parser("check", help="run the checks now")
    check.add_argument("level", nargs="?", choices=["basic", "full"], default="basic")
    backup_parser = commands.add_parser("backup", help="back up one service, or all")
    backup_parser.add_argument("service", nargs="?", choices=["all", *layout.KNOWN], default="all")
    commands.add_parser("rollback", help="go back to the previous release")
    commands.add_parser("status", help="show what runs now")
    args = parser.parse_args(argv)
    server = server or system.Server()
    if os.geteuid() != 0 and not os.environ.get("LX_TEST"):
        system.say("Run lx-deploy as root: sudo lx-deploy ...")
        return 2
    os.umask(0o022)
    try:
        with state_store.lock(wait=args.command != "run"):
            if args.command == "run":
                return deployment.cmd_run(server, args.force)
            if args.command == "apply-config":
                return configuration.cmd_apply_config(server, args.yes)
            if args.command == "init-secrets":
                return provisioning.cmd_init_secrets(server)
            if args.command == "check":
                return health_checks.cmd_check(server, args.level)
            if args.command == "backup":
                return backups.cmd_backup(server, args.service)
            if args.command == "rollback":
                return deployment.cmd_rollback(server)
            return cmd_status(server)
    except system.Busy:
        return 0  # another run holds the lock; the timer comes back at its next time
