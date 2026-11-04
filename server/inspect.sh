#!/usr/bin/env bash
# The read-only server check (GUIDE.md, part 1). It only looks: it changes, starts, stops and
# installs nothing, and it prints no settings or secrets (no environment variables, no .env or
# key files). Run it as: sudo bash inspect.sh > check.txt 2>&1   and send check.txt to Claude.
set -u
section() { printf '\n== %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

section "When and where"
date -u '+%Y-%m-%d %H:%M UTC'
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
uname -r
echo "CPUs: $(nproc)"
free -h | sed -n '1,3p'
df -h / /srv 2>/dev/null | awk '!seen[$0]++'

section "Who answers on ports 80 and 443 (the web) and 8000, 8787, 8790 (our services)"
ss -ltnpH '( sport = :80 or sport = :443 or sport = :8000 or sport = :8787 or sport = :8790 )' 2>/dev/null \
  | awk '{print $4, $6}' || echo "(ss failed)"

section "Docker"
if have docker; then
  docker --version
  docker compose version 2>/dev/null || echo "docker compose: missing"
  echo "-- containers (all, also stopped)"
  docker ps -a --format '{{.Names}} | {{.Image}} | {{.Status}} | {{.Ports}}'
  echo "-- containers that publish a port, with their folders and restart rule"
  for id in $(docker ps -aq); do
    docker inspect --format '{{.Name}} restart={{.HostConfig.RestartPolicy.Name}} network={{.HostConfig.NetworkMode}} ports={{range $p, $b := .HostConfig.PortBindings}}{{$p}} {{end}}mounts={{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}' "$id"
  done
  echo "-- compose projects"
  docker compose ls -a 2>/dev/null
  echo "-- images"
  docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' | head -40
  echo "-- volumes"
  docker volume ls --format '{{.Name}}'
  echo "-- daemon settings (/etc/docker/daemon.json)"
  cat /etc/docker/daemon.json 2>/dev/null || echo "(none)"
  echo "-- signed in to GitHub's registry?"
  grep -q '"ghcr.io"' /root/.docker/config.json 2>/dev/null && echo yes || echo no
else
  echo "docker: missing"
fi

section "Caddy and nginx"
have caddy && caddy version || echo "caddy: missing"
for unit in caddy nginx; do
  printf '%s: enabled=%s active=%s\n' "$unit" "$(systemctl is-enabled "$unit" 2>/dev/null)" "$(systemctl is-active "$unit" 2>/dev/null)"
done
ls -la /etc/caddy 2>/dev/null
echo "-- /etc/caddy/Caddyfile (site names and imports only)"
grep -nE '^[^#[:space:]].*\{$|^\s*import ' /etc/caddy/Caddyfile 2>/dev/null || echo "(none)"
ls /etc/nginx/sites-enabled 2>/dev/null

section "Services from the older manual guides"
systemctl list-units --all --no-pager --plain --no-legend 'rat-webs*' 'nobody*' 'shared-account*' 'lx-*' 2>/dev/null || true
systemctl list-unit-files --no-pager --plain --no-legend 'rat-webs*' 'nobody*' 'shared-account*' 'lx-*' 2>/dev/null || true
for path in /srv /srv/lx /etc/lx-deploy /var/lib/lx-deploy /var/lib/rat-webs /var/lib/nobody /var/lib/shared-account \
  /etc/rat-webs /etc/nobody /etc/shared-account /var/backups/rat-webs /var/backups/nobody /var/backups/shared-account; do
  [ -e "$path" ] && echo "$path: $(stat -c '%U:%G %a' "$path"), $(du -sh "$path" 2>/dev/null | cut -f1)"
done
ls -la /srv 2>/dev/null

section "Timers and scheduled jobs"
systemctl list-timers --all --no-pager --no-legend 2>/dev/null | awk '{print $(NF-1), $NF}'
echo "-- root crontab (commands only)"
crontab -l 2>/dev/null | grep -v '^\s*#' | grep . || echo "(none)"
ls /etc/cron.d 2>/dev/null

section "Firewall"
have ufw && ufw status 2>/dev/null | head -20 || echo "ufw: missing"

section "Names (DNS) as this server sees them"
for name in lixingzhuyi.com www.lixingzhuyi.com library.lixingzhuyi.com forum.lixingzhuyi.com \
  account.lixingzhuyi.com arena.lixingzhuyi.com xn--tiq2a255imxs.com nobody.lixingzhuyi.com; do
  printf '%s -> %s\n' "$name" "$(getent ahostsv4 "$name" | awk '{print $1}' | sort -u | tr '\n' ' ')"
done
echo "this server's own addresses: $(hostname -I 2>/dev/null)"

section "Can it reach what it needs (connect only, nothing sent)"
for target in github.com:22 github.com:443 ghcr.io:443 api.github.com:443 smtp.qq.com:465 \
  acme-v02.api.letsencrypt.org:443; do
  host=${target%:*} port=${target#*:}
  if timeout 6 bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null; then echo "$target: ok"; else echo "$target: NO"; fi
done

section "Tools"
for tool in git python3 curl sqlite3 ssh; do printf '%s: %s\n' "$tool" "$(command -v "$tool" || echo missing)"; done
python3 --version 2>/dev/null

section "Done"
echo "Nothing was changed."
