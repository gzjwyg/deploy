"""lx-deploy secrets operations."""

import getpass
import json
import os
import re
import secrets
import subprocess

from . import layout, system
from . import release as release_rules
from . import state as state_store


def token():
    return secrets.token_hex(32)


def ask(prompt, secret=False):
    reader = getpass.getpass if secret else input
    return reader(prompt).strip()


def cmd_init_secrets(server):
    """Creates every random secret that is missing and asks the owner for the rest. Never
    replaces a value that exists, never prints one."""
    layout.L.env.mkdir(parents=True, exist_ok=True)
    os.chmod(layout.L.env, 0o700)
    files = {name: state_store.read_env(layout.L.env / f"{name}.env") for name in layout.SERVICES}
    account, library, forum = files["shared-account"], files["rat-webs"], files["nobody"]
    watch = state_store.read_env(layout.L.etc / "watch.env")
    report_lines = []

    def shared(key, holders):
        existing = {holder[key] for holder in holders if holder.get(key)}
        if len(existing) > 1:
            raise SystemExit(f"{key} differs between the settings files. Make them equal by hand first.")
        value = existing.pop() if existing else token()
        kept = any(holder.get(key) == value for holder in holders)
        report_lines.append(f"{key}: {'kept' if kept else 'created'}")
        for holder in holders:
            holder[key] = value

    shared("ACCOUNT_INTERNAL_SECRET", [account, library, forum])
    shared("NOBODY_INTERNAL_SECRET", [library, forum])
    for holder, label in ((account, "ACCOUNT_EMAIL_HASH_KEY"),):
        if not holder.get(label):
            holder[label] = token()
            report_lines.append(
                f"{label}: created. Never change it once accounts exist; keep a "
                "private copy with the off-server backups"
            )
        else:
            report_lines.append(f"{label}: kept")
    for holder, site in ((library, "Library"), (forum, "Nobody")):
        if not holder.get("ACCOUNT_CLIENT_SECRET"):
            holder["ACCOUNT_CLIENT_SECRET"] = token()
            report_lines.append(f"{site} sign-in client secret: created")

    if "ACCOUNT_SMTP_USER" not in account:
        system.say("\nSign-in codes are sent from a QQ mailbox (shared-account deploy/README.md § 2).")
        user = ask("  The QQ address (numbers@qq.com), or Enter to set it later: ")
        account["ACCOUNT_SMTP_USER"] = user
        account["ACCOUNT_SMTP_PASSWORD"] = (
            ask("  Its authorisation code (授权码, not the password): ", secret=True) if user else ""
        )
        report_lines.append("QQ mail: " + ("set" if user else "not set yet (no codes can be sent)"))

    if not library.get("RATWEBS_ADMIN_NAME") or not library.get("RATWEBS_ADMIN_PASSWORD_HASH"):
        system.say("\nThe Library's administrator page (/admin) needs a name and a password.")
        name = ask("  Administrator name: ")
        while True:
            password = ask("  Password (12 to 72 characters): ", secret=True)
            if 12 <= len(password.encode()) <= 72 and password == ask(
                "  The same password again: ", secret=True
            ):
                break
            system.say("  Too short, too long, or the two did not match. Again.")
        library["RATWEBS_ADMIN_NAME"] = name
        library["RATWEBS_ADMIN_PASSWORD_HASH"] = bcrypt_hash(server, password)
        report_lines.append("Library administrator: set (only the password's bcrypt hash is stored)")

    for key, label in (("DEEPSEEK_API_KEY", "DeepSeek"), ("ZHIPU_API_KEY", "Zhipu (embeddings)")):
        if key not in library:
            library[key] = ask(
                f"\n{label} API key for the Library's Ask, or Enter to leave Ask off: ", secret=True
            )
            report_lines.append(f"{key}: {'set' if library[key] else 'empty (Ask stays off)'}")

    if not watch.get("WATCH_TO"):
        watch["WATCH_TO"] = ask("\nEmail address for the server's alerts (package H): ")
        report_lines.append("alert address: " + ("set" if watch["WATCH_TO"] else "not set"))

    for name, values in files.items():
        state_store.write_env(layout.L.env / f"{name}.env", values, f"Settings and secrets for {name} (lx-deploy init-secrets).")
    state_store.write_env(layout.L.etc / "watch.env", watch, "Where the server's alerts go (lx-deploy init-secrets).")
    write_clients(library["ACCOUNT_CLIENT_SECRET"], forum["ACCOUNT_CLIENT_SECRET"])
    write_data_files(server)
    system.say("")
    for line in report_lines:
        system.say(f"- {line}")
    system.say("Done. No value was printed; they are only in /etc/lx-deploy/env/ (root, mode 600).")
    return 0


def owned(path, text, mode=0o600):
    state_store.atomic_write(path, text, mode, owner=layout.APP_UID if os.geteuid() == 0 else None)


def write_clients(library_secret, forum_secret):
    """The provider's client registry (IDENTITY-V2.md § 3), in the account service's /data."""

    def client(host, port, secret):
        return {
            "secret": secret,
            "redirect_uri": f"https://{host}/api/auth/callback",
            "post_logout_redirect_uri": f"https://{host}/",
            "logout_uri": f"http://127.0.0.1:{port}/internal/oidc/logout",
        }

    registry = {
        "library": client(f"library.{layout.DOMAIN}", 8787, library_secret),
        "forum": client(f"forum.{layout.DOMAIN}", 8000, forum_secret),
    }
    owned(layout.L.srv / "shared-account" / "clients.json", json.dumps(registry, indent=2) + "\n")


def bcrypt_hash(server, password):
    """Hashes with the Library's own bcrypt, inside its image, without network; the password
    goes in on standard input, never on a command line."""
    image = release_rules.release_image("rat-webs")
    if not image:
        raise SystemExit("release.json names no Library image yet; run init-secrets again after it does.")
    server.sh(["docker", "pull", "--quiet", image], timeout=1800)
    program = "import bcrypt, sys; print(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(12)).decode())"
    result = server.sh(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "python",
            image,
            "-c",
            program,
        ],
        input=password.encode(),
    )
    hashed = result.stdout.decode().strip()
    if not re.fullmatch(r"\$2[aby]\$12\$[./A-Za-z0-9]{53}", hashed):
        raise SystemExit("The Library's image did not return a bcrypt hash.")
    return hashed


def write_data_files(server):
    names = layout.L.srv / "shared-account" / "reserved-names.txt"
    if not names.exists():
        residents = resident_names(server)
        if not residents:
            system.say(
                "Note: Nobody's resident names could not be read; add them to "
                f"{names} by hand (shared-account deploy/README.md § 2)."
            )
        lines = ["# One complete name per line (shared-account deploy/README.md § 2)."]
        lines += list(dict.fromkeys(layout.BASE_RESERVED_NAMES + residents))
        owned(names, "\n".join(lines) + "\n")
    moderation = layout.L.srv / "rat-webs" / "moderation.txt"
    if not moderation.exists():
        owned(moderation, "")


def resident_names(server):
    """Nobody's residents' names and handles, from the content pack in its image."""
    image = release_rules.release_image("nobody")
    if not image:
        return []
    program = (
        "const p=JSON.parse(require('fs').readFileSync('/app/web/src/content/pack/pack.json','utf8'));"
        "for(const r of p.personas){console.log(r.name.zh);console.log(r.name.en);console.log(r.handle)}"
    )
    try:
        server.sh(["docker", "pull", "--quiet", image], timeout=1800)
        result = server.sh(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "node",
                image,
                "-e",
                program,
            ]
        )
    except (system.CommandFailed, OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in result.stdout.decode().splitlines() if line.strip()]
