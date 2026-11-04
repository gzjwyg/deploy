#!/usr/bin/env bash
# Installs lx-deploy on the server (GUIDE.md, part 3). Run it as root from the read-only clone:
#   sudo bash /var/lib/lx-deploy/repo/server/install.sh
# It stops, changing nothing, if something other than Caddy answers on ports 80 or 443 or
# something already uses the services' ports. It asks before it changes anything, and the
# secrets it asks for are typed without being shown and stored only under /etc/lx-deploy.
set -euo pipefail
repo=/var/lib/lx-deploy/repo
program=$repo/server/lx-deploy
stop() { printf '\n%s\nNothing more was changed.\n' "$1" >&2; exit 1; }
ask_yes() { local answer; read -r -p "$1 Type yes to go on: " answer; [ "$answer" = yes ] || stop "Stopped at your request."; }

[ "$(id -u)" = 0 ] || stop "Run it with sudo: sudo bash $repo/server/install.sh"
[ "$(cd "$(dirname "$0")/.." && pwd)" = "$repo" ] || stop "Clone gzjwyg/deploy to $repo first (GUIDE.md, step 3.2)."
[ -f /etc/lx-deploy/deploy-key ] || stop "The read-only deploy key /etc/lx-deploy/deploy-key is missing (GUIDE.md, step 3.1)."

if grep -q '"run": *"not for the first deployment' "$repo/release.json"; then
  stop "release.json is not ready for the first deployment yet (its run line says so). Claude changes it when it is."
fi

echo "== Checking the server (nothing is changed yet)"
# shellcheck source=/dev/null
. /etc/os-release
[ "${VERSION_ID:-}" = 24.04 ] || echo "Note: this server runs $PRETTY_NAME; lx-deploy was written for Ubuntu 24.04."
for tool in docker git python3 caddy ss; do
  command -v "$tool" >/dev/null || stop "$tool is missing. GUIDE.md, 'Before you start', says how to install it."
done
docker compose version >/dev/null 2>&1 || stop "Docker Compose is missing (the docker-compose-plugin package)."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || stop "python3 is older than 3.10."
web=$(ss -ltnpH '( sport = :80 or sport = :443 )' | grep -v '"caddy"' || true)
if [ -n "$web" ]; then
  echo "$web"
  stop "Something other than Caddy answers on port 80 or 443 (shown above). GUIDE.md, 'If something already answers on 80 or 443', says what to do."
fi
apps=$(ss -ltnpH '( sport = :8000 or sport = :8787 or sport = :8790 )' || true)
if [ -n "$apps" ] && ! docker compose --project-directory /etc/lx-deploy ps -q 2>/dev/null | grep -q .; then
  echo "$apps"
  stop "Something already uses port 8000, 8787 or 8790 (shown above), probably a service from an older manual guide. GUIDE.md, 'Older manual installs', says what to do."
fi
echo "Ports 80 and 443 are free or Caddy's; ports 8000, 8787 and 8790 are free."

echo
echo "== This will:"
echo "  - make the folders /etc/lx-deploy, /srv/lx (the services' data, owned by user 10001) and /var/lib/lx-watch"
echo "  - store the two GitHub tokens you paste (they are not shown on the screen)"
echo "  - create the services' secrets and ask for the mail, administrator and alert settings"
echo "  - install lx-deploy, the compose file, Caddy's files and the timers (you will see each file first)"
ask_yes "Go on?"

install -d -m 755 /etc/lx-deploy /etc/lx-deploy/caddy /srv/lx /srv/lx/www
install -d -m 700 /etc/lx-deploy/env /var/lib/lx-deploy /var/lib/lx-watch /srv/lx/backups
for service in shared-account rat-webs nobody articledb; do
  install -d -o 10001 -g 10001 -m 700 "/srv/lx/$service" "/srv/lx/backups/$service"
done

echo
echo "== GitHub's registry (the read:packages token, GUIDE.md step 2.3)"
if grep -q '"ghcr.io"' /root/.docker/config.json 2>/dev/null; then
  echo "Already signed in to ghcr.io."
else
  read -r -s -p "Paste the read:packages token, then press Enter: " registry; echo
  printf '%s' "$registry" | docker login ghcr.io --username gzjwyg --password-stdin >/dev/null \
    || stop "GitHub's registry did not accept that token."
  unset registry
  echo "Signed in to ghcr.io."
fi

echo
echo "== Reports (the token for gzjwyg/deploy, GUIDE.md step 2.4)"
if [ -s /etc/lx-deploy/report.token ]; then
  echo "A report token is already stored."
else
  read -r -s -p "Paste the gzjwyg/deploy report token, then press Enter: " report; echo
  (umask 077; printf '%s\n' "$report" > /etc/lx-deploy/report.token)
  unset report
fi
if [ ! -f /etc/lx-deploy/deploy.json ]; then
  read -r -p "The number of the issue on gzjwyg/deploy that collects the reports [1]: " issue
  issue=${issue:-1}
  [[ "$issue" =~ ^[0-9]+$ ]] || stop "That is not an issue number."
  printf '{"report_issue": %s}\n' "$issue" > /etc/lx-deploy/deploy.json
fi

echo
echo "== Secrets and settings"
python3 "$program" init-secrets

echo
echo "== The server's files"
python3 "$program" apply-config

echo
echo "== The first deployment"
/usr/local/sbin/lx-deploy run --force || true
echo
echo "Installed. From now on the server looks for a new release at 04:00 and 16:00 (Hong Kong) and reports"
echo "each result on gzjwyg/deploy. Status: sudo lx-deploy status"
