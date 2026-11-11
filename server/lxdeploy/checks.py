"""lx-deploy checks operations."""

import contextlib
import json
import shutil
import tempfile
from pathlib import Path

from . import backup as backups
from . import layout, reporting, system


class Checks:
    def __init__(self, server):
        self.server = server
        self.lines = []

    @property
    def passed(self):
        return all(ok for ok, _ in self.lines)

    def check(self, text, function):
        try:
            ok = bool(function())
        except Exception as error:  # a check that cannot run is a failed check
            ok, text = False, f"{text} ({type(error).__name__})"
        self.lines.append((ok, text))
        return ok

    def get(self, target, path, method="GET", headers=None):
        return self.server.request(target, method, path, headers)

    def healthy(self, name):
        status, _, body = self.get(layout.SERVICES[name]["port"], "/healthz")
        return status == 200 and body.strip() == b"ok"

    def wait_healthy(self, name, seconds=90):
        for _ in range(seconds // 3):
            with contextlib.suppress(Exception):
                if self.healthy(name):
                    return True
            self.server.wait(3)
        return self.healthy(name)

    def basic(self):
        for name in layout.SERVICES:
            self.check(f"{name} answers its health check", lambda name=name: self.healthy(name))
        self.check(f"the homepage opens over HTTPS ({layout.DOMAIN})", lambda: self.page(layout.DOMAIN, "/"))
        self.check("the Library opens over HTTPS", lambda: self.page(f"library.{layout.DOMAIN}", "/"))
        self.check("Nobody opens over HTTPS", lambda: self.page(f"forum.{layout.DOMAIN}", "/"))
        self.check("the sign-in service answers over HTTPS", self.provider)
        for host in layout.PUBLIC_HOSTS:
            self.check(
                f"{host} refuses /internal/, PHP probes and dotfiles", lambda host=host: self.refuses(host)
            )
        self.check(f"account.{layout.DOMAIN} refuses /token and /userinfo", self.provider_private)
        for host, cookie in ((f"library.{layout.DOMAIN}", "__Host-library"), (f"forum.{layout.DOMAIN}", "__Host-forum")):
            self.check(
                f"{host} sign-in cookies are host-only, Secure and HttpOnly",
                lambda host=host, cookie=cookie: self.cookies(host, cookie),
            )
        self.check("the Library's staff paths allow Nobody's origin and refuse others", self.staff_origin)

    def page(self, host, path):
        status, headers, _ = self.get(host, path)
        names = {key.lower() for key, _ in headers}
        return status == 200 and "strict-transport-security" in names

    def provider(self):
        status, _, body = self.get(f"account.{layout.DOMAIN}", "/.well-known/openid-configuration")
        return status == 200 and json.loads(body).get("issuer") == f"https://account.{layout.DOMAIN}"

    def refuses(self, host):
        paths = ["/internal/accounts/names", "/wp-login.php", "/api/.env", "/.git/config"]
        return all(self.get(host, path)[0] == 404 for path in paths)

    def provider_private(self):
        host = f"account.{layout.DOMAIN}"
        return self.get(host, "/token", "POST")[0] == 404 and self.get(host, "/userinfo")[0] == 404

    def cookies(self, host, name):
        status, headers, _ = self.get(host, "/api/auth/login?return_to=/")
        location = dict((k.lower(), v) for k, v in headers).get("location", "")
        set_cookies = [value for key, value in headers if key.lower() == "set-cookie"]
        if status not in (302, 303, 307) or not location.startswith(f"https://account.{layout.DOMAIN}/authorize?"):
            return False
        if not set_cookies:
            return False
        for value in set_cookies:
            parts = [part.strip().lower() for part in value.split(";")]
            if not value.startswith(name) or "secure" not in parts or "httponly" not in parts:
                return False
            if any(part.startswith("domain=") for part in parts):
                return False
        return True

    def staff_origin(self):
        host, path = f"library.{layout.DOMAIN}", "/staff/comment-reports"
        forum = f"https://forum.{layout.DOMAIN}"
        preflight = {"Access-Control-Request-Method": "POST"}
        status, headers, _ = self.get(host, path, "OPTIONS", {"Origin": forum, **preflight})
        allowed = dict((k.lower(), v) for k, v in headers).get("access-control-allow-origin")
        if status not in (200, 204) or allowed != forum:
            return False
        stranger = "https://example.invalid"
        status, headers, _ = self.get(host, path, "OPTIONS", {"Origin": stranger, **preflight})
        if status != 403 or "access-control-allow-origin" in {k.lower() for k, _ in headers}:
            return False
        return self.get(host, f"{path}/lx-deploy-check", "POST", {"Origin": stranger})[0] == 403

    def full(self):
        before = {}
        self.check("the databases can be read", lambda: before.update(self.live_counts()) or True)
        self.server.compose("stop", "shared-account")
        try:
            self.check(
                "with sign-in stopped, the Library still opens",
                lambda: self.page(f"library.{layout.DOMAIN}", "/") and self.healthy("rat-webs"),
            )
            self.check(
                "with sign-in stopped, Nobody still opens",
                lambda: self.page(f"forum.{layout.DOMAIN}", "/") and self.healthy("nobody"),
            )
        finally:
            self.server.compose("start", "shared-account")
        self.check("sign-in comes back after a restart", lambda: self.wait_healthy("shared-account"))
        self.server.compose("restart")
        for name in layout.SERVICES:
            self.check(
                f"{name} comes back after a restart of everything", lambda name=name: self.wait_healthy(name)
            )
        self.check("the data is still there after the restart", lambda: self.live_counts() == before)
        for name, facts in layout.SERVICES.items():
            self.check(
                f"{name}: a fresh backup restores with the same counts",
                lambda name=name, facts=facts: self.restores(name, facts),
            )
        self.check("the backup, watch and update timers are scheduled", self.timers)

    def live_counts(self):
        return {
            name: backups.counts(layout.L.srv / name / facts["database"], facts["tables"])
            for name, facts in layout.SERVICES.items()
        }

    def restores(self, name, facts):
        backups.backup(self.server, name)
        latest = backups.newest(layout.L.srv / "backups" / name, facts["backup_glob"])
        if latest is None:
            return False
        with tempfile.TemporaryDirectory(prefix="lx-restore-") as scratch:
            copy = Path(scratch) / latest.name
            shutil.copyfile(latest, copy)
            live = backups.counts(layout.L.srv / name / facts["database"], facts["tables"])
            return backups.counts(copy, facts["tables"], integrity=True) == live

    def timers(self):
        listing = self.server.sh(["systemctl", "list-timers", "--all", "--no-legend", "--no-pager"])
        text = listing.stdout.decode()
        return all(timer in text for timer in layout.TIMERS)


def run_checks(server, level):
    checks = Checks(server)
    try:
        checks.basic()
        if level == "full":
            checks.full()
    except Exception as error:  # e.g. a compose command in the full checks failed
        checks.lines.append((False, f"the checks stopped early ({type(error).__name__})"))
    return checks


def cmd_check(server, level):
    checks = run_checks(server, level)
    for line in reporting.check_lines(checks):
        system.say(line)
    return 0 if checks.passed else 1
