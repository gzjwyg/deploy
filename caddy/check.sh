#!/usr/bin/env bash
# Checks common.caddy and account.caddy with a local Caddy (no network, no real services):
# a stand-in application logs every request it receives; each probe must be refused by Caddy
# without reaching it, and ordinary requests must still pass. Run: caddy/check.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'kill $(jobs -p) 2>/dev/null || true; rm -rf "$work"' EXIT
log=$work/app.log; : > "$log"
mkdir -p "$work/www/vendor/katex"; echo ok > "$work/www/vendor/katex/katex.min.css"
python3 - "$log" <<'PY' &
import sys, http.server
log = open(sys.argv[1], 'a', buffering=1)
class App(http.server.BaseHTTPRequestHandler):
    def answer(self):
        log.write(self.path + '\n')
        self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'ok')
    do_GET = do_POST = answer
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(('127.0.0.1', 19790), App).serve_forever()
PY
sed 's/127.0.0.1:8790/127.0.0.1:19790/' "$here/account.caddy" > "$work/account.caddy"
cat > "$work/Caddyfile" <<CADDY
{
	admin off
	auto_https off
}
import $here/common.caddy
import $work/account.caddy
http://:19101 {
	import lx_common
	handle /api/* {
		reverse_proxy 127.0.0.1:19790
	}
	handle {
		root * $work/www
		file_server
	}
}
http://:19102 {
	import lx_common
	import lx_account
}
CADDY
caddy run --adapter caddyfile --config "$work/Caddyfile" > "$work/caddy.log" 2>&1 &
for _ in $(seq 1 50); do curl -s -o /dev/null http://127.0.0.1:19101/ && break; sleep 0.1; done
failed=0
while read -r port method path code reaches; do
  before=$(wc -l < "$log")
  got=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "http://127.0.0.1:$port$path")
  after=$(wc -l < "$log"); reached=no; [ "$after" -gt "$before" ] && reached=yes
  if [ "$got" != "$code" ] || [ "$reached" != "$reaches" ]; then
    echo "FAIL $method $path on :$port: got $got, reached app: $reached (want $code, $reaches)"; failed=1
  fi
done <<'CASES'
19101 GET /wp-admin/setup-config.php 404 no
19101 GET /api/upload.php 404 no
19101 GET /api/.env 404 no
19101 GET /api/wp-login.php 404 no
19101 GET /.git/config 404 no
19101 GET /internal/accounts/names 404 no
19101 GET /vendor/katex/katex.min.css 200 no
19101 GET /api/health 200 yes
19102 GET /.well-known/openid-configuration 200 yes
19102 GET /authorize 200 yes
19102 GET /account/login 200 yes
19102 POST /token 404 no
19102 GET /userinfo 404 no
19102 GET /internal/accounts/names 404 no
19102 GET /account/staff/verifications 404 no
CASES
[ "$failed" = 0 ] && echo "caddy check: all cases pass"
exit "$failed"
