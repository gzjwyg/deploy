"""lx-deploy www operations."""

import hashlib
import os
import secrets
import shutil
from pathlib import Path

from . import layout
from . import release as release_rules


def switch_link(link, target):
    """Points link at target in one step, so Caddy never sees a half-copied folder."""
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.{secrets.token_hex(4)}")
    os.symlink(target, temporary)
    os.replace(temporary, link)


def link_target(link):
    return os.readlink(link) if link.is_symlink() else None


def make_public(folder):
    """Plain files and folders only, readable by Caddy. A link in an image could point Caddy at
    any file on the server, so links are refused."""
    for root, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = Path(root) / name
            if path.is_symlink():
                raise ValueError(f"the static files contain a link ({path.relative_to(folder)})")
        os.chmod(root, 0o755)
        for name in files:
            os.chmod(Path(root) / name, 0o644)


def extract_www(server, name, image):
    """Copies /app/www out of an image (DEPLOYMENT.md § 9a) without running it."""
    releases = layout.L.www / ".releases"
    target = releases / f"{name}-{release_rules.short(image)}"
    if target.is_dir():
        return target
    releases.mkdir(parents=True, exist_ok=True)
    temporary = releases / f".{target.name}.{secrets.token_hex(4)}"
    container = server.sh(["docker", "create", image]).stdout.decode().strip()
    try:
        server.sh(["docker", "cp", f"{container}:/app/www", str(temporary)])
    finally:
        server.sh(["docker", "rm", container], check=False)
    try:
        if not (temporary / "index.html").is_file():
            raise ValueError(f"{name}: the image has no /app/www/index.html")
        make_public(temporary)
        os.replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def sync_home():
    """The homepage is this repository's home/: static files only, copied, never run."""
    source = layout.L.repo / "home"
    if not (source / "index.html").is_file():
        return None
    digest = hashlib.sha256()
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("home/ contains a link")
        if path.is_file():
            if path.suffix.lower() not in layout.HOME_FILES:
                raise ValueError(f"home/ contains a file type that is not served: {path.name}")
            relative = path.relative_to(source)
            files.append(relative)
            digest.update(str(relative).encode() + b"\0" + path.read_bytes() + b"\0")
    target = layout.L.www / ".releases" / f"home-{digest.hexdigest()[:12]}"
    if not target.is_dir():
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}")
        for relative in files:
            (temporary / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, temporary / relative)
        make_public(temporary)
        os.replace(temporary, target)
    changed = link_target(layout.L.www / "home") != str(target)
    switch_link(layout.L.www / "home", target)
    return changed


def prune(server, keep):
    """Keeps the static files and images of the current and previous release only."""
    releases = layout.L.www / ".releases"
    wanted = {Path(link_target(layout.L.www / name) or "").name for name in ("home", "rat-webs", "nobody")}
    wanted |= {f"{name}-{release_rules.short(image)}" for images in keep for name, image in images.items()}
    if releases.is_dir():
        for folder in releases.iterdir():
            if folder.is_dir() and folder.name not in wanted and not folder.name.startswith("."):
                shutil.rmtree(folder, ignore_errors=True)
    digests = {image.split("@")[1] for images in keep for image in images.values()}
    for name in layout.KNOWN:
        listing = server.sh(
            [
                "docker",
                "image",
                "ls",
                "--digests",
                "--format",
                "{{.Digest}} {{.ID}}",
                f"ghcr.io/gzjwyg/{name}",
            ],
            check=False,
        )
        for line in listing.stdout.decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] not in digests:
                server.sh(["docker", "image", "rm", parts[1]], check=False)
