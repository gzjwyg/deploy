"""lx-deploy state operations."""

import contextlib
import fcntl
import json
import os
import re
import tempfile

from . import layout, system


@contextlib.contextmanager
def lock(wait=False):
    layout.L.state.mkdir(parents=True, exist_ok=True)
    with open(layout.L.state / "lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise system.Busy() from None
        yield


def read_env(path):
    """KEY=VALUE lines, as Docker Compose and systemd both read them (single quotes optional)."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def write_env(path, values, header):
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"bad setting name {key!r}")
        if "'" in value or "\n" in value or "\r" in value:
            raise ValueError(f"{key}: a value cannot contain a single quote or a line break")
    lines = [f"# {header}", "# Root only (mode 600). Never copy it into Git, a chat or a screenshot."]
    lines += [f"{key}='{value}'" for key, value in values.items()]
    atomic_write(path, "\n".join(lines) + "\n", 0o600)


def atomic_write(path, text, mode, owner=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        if owner is not None:
            os.chown(temporary, owner, owner)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def load_state():
    path = layout.L.state / "state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(state):
    atomic_write(layout.L.state / "state.json", json.dumps(state, indent=2) + "\n", 0o600)


def write_dotenv(images, commit):
    """Docker Compose reads the image references from .env in the project folder."""
    lines = [f"# Written by lx-deploy from release.json at {commit[:12]}. Do not edit."]
    for name in layout.KNOWN:
        if name in images:
            lines.append(f"LX_IMAGE_{name.upper().replace('-', '_')}={images[name]}")
    atomic_write(layout.L.etc / ".env", "\n".join(lines) + "\n", 0o644)
