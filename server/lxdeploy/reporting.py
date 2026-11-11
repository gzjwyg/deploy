"""lx-deploy reporting operations."""

import json
import urllib.error
import urllib.request

from . import layout, system
from . import release as release_rules


def report(commit, state, title, lines=(), comment=True):
    """A commit status, and for results an issue comment, on gzjwyg/deploy. Only pass/fail lines
    and versions: never logs, settings, addresses or secrets. A failed report never stops a
    deployment."""
    system.say(f"{state}: {title}")
    for line in lines:
        system.say(f"  {line}")
    token_file = layout.L.etc / "report.token"
    if not token_file.exists():
        return
    settings = json.loads((layout.L.etc / "deploy.json").read_text()) if (layout.L.etc / "deploy.json").exists() else {}
    token = token_file.read_text().strip()

    def post(path, body):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{layout.REPOSITORY}{path}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "lx-deploy",
            },
        )
        try:
            urllib.request.urlopen(request, timeout=20).close()
        except (urllib.error.URLError, OSError) as error:
            system.say(f"  (the report to GitHub failed: {type(error).__name__})")

    post(f"/statuses/{commit}", {"state": state, "context": "lx-deploy", "description": title[:140]})
    issue = settings.get("report_issue")
    if comment and state != "pending" and isinstance(issue, int):
        mark = {"success": "✅", "failure": "❌", "error": "❌"}.get(state, "")
        body = "\n".join([f"{mark} **{title}** (`{commit[:12]}`)", "", *[f"- {line}" for line in lines]])
        post(f"/issues/{issue}/comments", {"body": body[:60000]})


def check_lines(checks):
    return [f"{'pass' if ok else 'FAIL'}: {text}" for ok, text in checks.lines]


def version_lines(images, changed=()):
    return [f"{name} `{release_rules.short(image)}`{' (new)' if name in changed else ''}" for name, image in images.items()]
