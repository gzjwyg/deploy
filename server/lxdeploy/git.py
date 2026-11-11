"""lx-deploy git operations."""

import re

from . import layout


def git(server, *args):
    return server.sh(["git", "-C", str(layout.L.repo), "-c", "core.hooksPath=/dev/null", *args])


def fetch(server):
    """Brings the checkout to the newest commit of main and returns it. Nothing in it is run."""
    git(server, "fetch", "--quiet", "origin", layout.BRANCH)
    git(server, "checkout", "--quiet", "--force", "--detach", "FETCH_HEAD")
    git(server, "clean", "-fdxq")
    commit = git(server, "rev-parse", "HEAD").stdout.decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("git returned an unexpected commit id")
    return commit
