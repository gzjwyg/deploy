"""lx-deploy layout operations."""

import dataclasses
import os
import re
from pathlib import Path

REPOSITORY = "gzjwyg/deploy"

BRANCH = "main"

APP_UID = 10001

DOMAIN = "lixingzhuyi.com"

SERVICES = {
    "shared-account": {
        "port": 8790,
        "host": f"account.{DOMAIN}",
        "www": False,
        "database": "accounts.sqlite3",
        "tables": ["accounts", "sessions"],
        "backup": [
            ["python", "-m", "service.cli", "maintain"],
            ["python", "-m", "service.cli", "backup", "/data/backups"],
        ],
        "backup_glob": "*.sqlite3",
    },
    "rat-webs": {
        "port": 8787,
        "host": f"library.{DOMAIN}",
        "www": True,
        "database": "rat-webs.sqlite3",
        "tables": ["users", "passages", "community_credits"],
        "backup": [
            ["python", "-m", "server.cli", "maintain"],
            ["python", "-m", "server.cli", "backup", "/data/backups"],
        ],
        "backup_glob": "*.sqlite3",
    },
    "nobody": {
        "port": 8000,
        "host": f"forum.{DOMAIN}",
        "www": True,
        "database": "nobody.sqlite",
        "tables": ["metadata", "readers", "issues"],
        "backup": [["node", "/app/backup.mjs", "/data/backups"]],
        "backup_glob": "*.sqlite",
    },
}

JOBS = {"articledb": {"backup": [["backup", "--to", "/data/backups", "--verify", "--json"]]}}

KNOWN = list(SERVICES) + list(JOBS)

PUBLIC_HOSTS = [DOMAIN, f"library.{DOMAIN}", f"forum.{DOMAIN}", f"account.{DOMAIN}"]

TIMERS = ["lx-deploy.timer", "lx-backup.timer", "lx-watch.timer", "lx-restore-test.timer"]

IMAGE = re.compile(r"ghcr\.io/gzjwyg/(?P<name>[a-z0-9-]+)@sha256:(?P<digest>[0-9a-f]{64})")

HOME_FILES = {".html", ".css", ".svg", ".txt", ".xml", ".ico", ".png", ".webp", ".json", ".webmanifest"}

BASE_RESERVED_NAMES = ["鼠鼠", "小理", "管理员", "admin", "administrator", "Shushu"]

MAIN_CADDYFILE = "import /etc/lx-deploy/caddy/Caddyfile\n"


@dataclasses.dataclass
class Layout:
    """Where things live. Tests point every path into a temporary folder."""

    etc: Path
    state: Path
    srv: Path
    systemd: Path
    program: Path
    monitor: Path
    caddyfile: Path

    @classmethod
    def from_env(cls):
        get = lambda name, default: Path(os.environ.get(name, default))  # noqa: E731
        return cls(
            etc=get("LX_ETC", "/etc/lx-deploy"),
            state=get("LX_STATE", "/var/lib/lx-deploy"),
            srv=get("LX_SRV", "/srv/lx"),
            systemd=get("LX_SYSTEMD", "/etc/systemd/system"),
            program=get("LX_PROGRAM", "/usr/local/sbin/lx-deploy"),
            monitor=get("LX_MONITOR", "/opt/lx-monitor"),
            caddyfile=get("LX_CADDYFILE", "/etc/caddy/Caddyfile"),
        )

    @property
    def repo(self):
        return self.state / "repo"

    @property
    def env(self):
        return self.etc / "env"

    @property
    def www(self):
        return self.srv / "www"


L = Layout.from_env()
