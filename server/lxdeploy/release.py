"""lx-deploy release operations."""

import json

from . import layout


def validate_release(text):
    """Returns (release, errors). release.json is data, never code (DEPLOYMENT.md § 3)."""
    if text is None:
        return None, ["release.json is missing"]
    if len(text.encode()) > 4096:
        return None, ["release.json is larger than 4 KB"]
    try:
        data = json.loads(text)
    except ValueError:
        return None, ["release.json is not valid JSON"]
    if not isinstance(data, dict):
        return None, ["release.json must be an object"]
    errors = [f"unknown field {key!r}" for key in data if key not in ("images", "checks", "run")]
    images = data.get("images")
    if not isinstance(images, dict):
        errors.append("images must be an object of service: image")
        images = {}
    for name, reference in images.items():
        if name not in layout.KNOWN:
            errors.append(f"unknown service {name!r}")
            continue
        match = layout.IMAGE.fullmatch(reference) if isinstance(reference, str) else None
        if not match or match["name"] != name:
            errors.append(f"{name}: the image must be ghcr.io/gzjwyg/{name}@sha256:<64 hex>")
    for name in layout.SERVICES:
        if name not in images:
            errors.append(f"images must name {name}")
    checks = data.get("checks", "basic")
    if checks not in ("basic", "full"):
        errors.append("checks must be basic or full")
    run = data.get("run", "")
    if not isinstance(run, str) or len(run) > 100 or not run.isprintable():
        errors.append("run must be a short line of text")
    if errors:
        return None, errors
    return {"images": dict(images), "checks": checks, "run": run}, []


def short(reference):
    return reference.split("@sha256:")[1][:12] if reference and "@sha256:" in reference else "-"


def release_image(name):
    release, _ = validate_release(
        (layout.L.repo / "release.json").read_text() if (layout.L.repo / "release.json").exists() else None
    )
    return release["images"].get(name) if release else None
