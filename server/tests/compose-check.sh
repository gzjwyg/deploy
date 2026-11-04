#!/usr/bin/env bash
# Validates server/compose.yaml with Docker Compose and synthetic settings (no daemon needed).
set -euo pipefail
here=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
mkdir -p "$work/env"
sed "s#/etc/lx-deploy/env/#$work/env/#" "$here/server/compose.yaml" > "$work/compose.yaml"
for service in shared-account rat-webs nobody; do
  printf "ACCOUNT_INTERNAL_SECRET='synthetic'\nRATWEBS_ADMIN_PASSWORD_HASH='\$2b\$12\$synthetic'\n" > "$work/env/$service.env"
done
python3 - "$here/release.json" > "$work/.env" <<'PY'
import json, sys
for name, image in json.load(open(sys.argv[1]))["images"].items():
    print(f"LX_IMAGE_{name.upper().replace('-', '_')}={image}")
PY
config=$(docker compose --project-directory "$work" config)
[ "$(docker compose --project-directory "$work" config --services | sort | tr '\n' ' ')" = "nobody rat-webs shared-account " ]
[ "$(docker compose --project-directory "$work" --profile jobs config --services | sort | tr '\n' ' ')" = "articledb nobody rat-webs shared-account " ]
for want in 'network_mode: host' 'read_only: true' 'user: 10001:10001' 'no-new-privileges:true'; do
  [ "$(grep -c -- "$want" <<<"$config")" -ge 3 ] || { echo "compose check: '$want' missing on a service"; exit 1; }
done
grep -q 'RATWEBS_ADMIN_PASSWORD_HASH: \$\$2b\$\$12\$\$synthetic' <<<"$config" || { echo "compose check: a bcrypt hash would be mangled"; exit 1; }
if grep -qE '^\s+ports:' <<<"$config"; then echo "compose check: a service publishes a port"; exit 1; fi
echo "compose check: passes"
