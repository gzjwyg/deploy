# deploy

How the owner's Hong Kong server runs the ecosystem: Rat-webs (the Library), Nobody (the forum),
the shared account service, and the Concept Map's and Article-database's jobs. Maintained by Claude.

| Path | What | State |
|---|---|---|
| [`GUIDE.md`](GUIDE.md) | **The owner's guide**: the read-only check, the one-time setup, everyday commands | Ready |
| `release.json` | Which image runs, per service: the only file agents change to deploy | Ready for the first deployment (5 Nov): images with sign-in version 2 and the new addresses |
| `server/lx-deploy` | The updater (Python, standard library): `run` twice a day (04:00 and 16:00 Hong Kong time), `apply-config`, `init-secrets`, `check`, `backup`, `rollback`, `status` | Ready; tested with a fake server |
| `server/compose.yaml` | How the three services and the article-library job run: host network on 127.0.0.1, user 10001, read-only root, no capabilities, their own data folder | Ready |
| `server/systemd/` | Timers: deployments (twice a day), backups (daily), alerts (15 min) and the monthly restore test | Ready |
| `server/watch.json`, `server/monitor/watch.py` | Package H's alerts with this deployment's names; `watch.py` is a copy of shared-account `deploy/watch.py` (67f3c60): change it there first | Ready |
| `server/inspect.sh`, `server/install.sh` | The read-only server check, and the installer | Ready |
| `caddy/` | The web server: `Caddyfile` → `sites.caddy` (one block per name), `common.caddy` (probes, `/internal/`, headers, a log without addresses), `account.caddy` (only the public sign-in paths) | Ready |
| `home/` | The homepage at `lixingzhuyi.com` | Ready |

Nothing here changes the server by itself. The owner decides when anything is installed.

## The homepage

Static files only: no scripts, no outside fonts or files (Google Fonts and most foreign CDNs are
blocked in mainland China), light and dark colour schemes, Chinese at `/` and English at `/en/`.
To change the wording, edit `home/index.html` and `home/en/index.html` together; the styles are
in `home/home.css`.

Caddy serves it from `/srv/lx/www/home`, which `lx-deploy` copies from `home/` (only static file
types) on each run; `caddy/sites.caddy` has the redirects for `www.`, `理性主义.com` and `arena.`.

## Checks

Run these before pushing; CI (`.github/workflows/check.yml`) runs the same.

```
python3 -m unittest discover -s server/tests -v   # lx-deploy with a fake server
bash caddy/check.sh                               # the real Caddy files, with stand-in applications
bash server/tests/compose-check.sh                # the compose file, with synthetic settings
```

## How a deployment works

1. An agent changes `release.json` (`ghcr.io/gzjwyg/<service>@sha256:…` per service, `checks`
   `basic` or `full`, and a free `run` label; changing the label alone re-runs the checks).
2. At the next of 04:00 or 16:00 (Hong Kong), or at once with `sudo lx-deploy run`, the server fetches it with its read-only key. Anything else in the file,
   or any other image, is refused and reported, and nothing changes.
3. It downloads the images, backs up each service it is about to change, copies the two sites'
   static files out of their images (refusing links), starts the new versions and runs the
   checks: health, HTTPS on every name, `/internal/` and probes refused, host-only cookies, the
   staff origin rule; `full` adds stopping sign-in, restarting everything and restoring each
   fresh backup.
4. If a check fails, it starts the previous versions again and checks those.
5. The result goes to the commit's status and the report issue: pass/fail lines and short
   digests, never logs or settings.
