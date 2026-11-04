#!/usr/bin/env bash
# Runs the real sites.caddy with a local Caddy (no network, no real services, no certificates):
# every public name answers on 127.0.0.1:19200 over plain HTTP, and three stand-in applications
# record every request that reaches them. Each case checks the status, whether the request
# reached an application (and which), and for a few, the headers the application saw.
# Run: caddy/check.sh   (needs caddy, curl and python3)
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'kill $(jobs -p) 2>/dev/null || true; rm -rf "$work"' EXIT
log=$work/app.log; : > "$log"

# Static files as lx-deploy lays them out under /srv/lx/www.
mkdir -p "$work/www/home" "$work/www/rat-webs/_astro" "$work/www/rat-webs/exports" "$work/www/rat-webs/vendor/katex" \
  "$work/www/nobody/assets"
echo home > "$work/www/home/index.html"; echo home-404 > "$work/www/home/404.html"
echo library > "$work/www/rat-webs/index.html"; echo library-404 > "$work/www/rat-webs/404.html"
echo 'a()' > "$work/www/rat-webs/_astro/a.js"; echo '{"schema_version":1}' > "$work/www/rat-webs/exports/items.json"
echo ok > "$work/www/rat-webs/vendor/katex/katex.min.css"
echo forum > "$work/www/nobody/index.html"; echo 'b()' > "$work/www/nobody/assets/b.js"

# Stand-in applications: one line per request, "port path real-ip forwarded-for secret auth".
python3 - "$log" <<'PY' &
import sys, threading, http.server
log = open(sys.argv[1], 'a', buffering=1)
def handler(port):
    class App(http.server.BaseHTTPRequestHandler):
        def answer(self):
            length = int(self.headers.get('Content-Length') or 0)
            if length: self.rfile.read(length)
            h = lambda name: (self.headers.get(name) or '-').replace(' ', '_')
            log.write(f"{port} {self.path} {h('X-Real-IP')} {h('X-Forwarded-For')} {h('X-Internal-Secret')} {h('Authorization')}\n")
            try:
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'ok')
            except OSError:
                pass  # Caddy gave up on the request; the log line above is what counts
        do_GET = do_POST = do_PATCH = do_OPTIONS = answer
        def log_message(self, *a): pass
    return App
servers = [http.server.ThreadingHTTPServer(('127.0.0.1', p), handler(p)) for p in (19790, 19787, 19800)]
for s in servers[1:]: threading.Thread(target=s.serve_forever, daemon=True).start()
servers[0].serve_forever()
PY

# The real file, with each site address turned into plain HTTP on port 19200.
python3 - "$here/sites.caddy" "$work/sites.caddy" <<'PY'
import re, sys
out = []
for line in open(sys.argv[1], encoding='utf-8'):
    m = re.match(r'^([a-z0-9.-]+(?:, [a-z0-9.-]+)*) \{$', line.rstrip('\n'))
    if m:
        line = ', '.join(f'http://{h}:19200' for h in m.group(1).split(', ')) + ' {\n'
    out.append(line)
open(sys.argv[2], 'w', encoding='utf-8').write(''.join(out))
PY
cat > "$work/Caddyfile" <<CADDY
{
	admin off
	auto_https off
	servers {
		protocols h1 h2
	}
}
import $work/sites.caddy
CADDY
export LX_CADDY=$here LX_WWW=$work/www
export LX_ACCOUNT_UPSTREAM=127.0.0.1:19790 LX_LIBRARY_UPSTREAM=127.0.0.1:19787 LX_FORUM_UPSTREAM=127.0.0.1:19800
caddy run --adapter caddyfile --config "$work/Caddyfile" > "$work/caddy.log" 2>&1 &
for _ in $(seq 1 50); do curl -s -o /dev/null --resolve lixingzhuyi.com:19200:127.0.0.1 http://lixingzhuyi.com:19200/ && break; sleep 0.1; done

failed=0
fail() { echo "FAIL $*"; failed=1; }
# request HOST METHOD PATH [curl args...]: sets code, body, headers, reached (the port or "no").
request() {
  local host=$1 method=$2 path=$3; shift 3
  local before; before=$(wc -l < "$log")
  code=$(curl -s -o "$work/body" -D "$work/headers" -w '%{http_code}' -X "$method" \
    --resolve "$host:19200:127.0.0.1" "$@" "http://$host:19200$path")
  body=$(cat "$work/body"); headers=$(tr -d '\r' < "$work/headers")
  reached=$(tail -n +"$((before + 1))" "$log" | head -1 | cut -d' ' -f1); reached=${reached:-no}
}
while read -r host method path want_code want_reach want_body; do
  [ -z "$host" ] || [ "${host:0:1}" = "#" ] && continue
  request "$host" "$method" "$path"
  [ "$code" = "$want_code" ] && [ "$reached" = "$want_reach" ] || fail "$method $host$path: got $code reaching $reached (want $want_code reaching $want_reach)"
  if [ "$want_body" != "-" ] && [ "$body" != "$want_body" ]; then fail "$method $host$path: body '$body' (want '$want_body')"; fi
done <<'CASES'
# host                         method path                              code reach body
lixingzhuyi.com                GET  /                                   200 no    home
lixingzhuyi.com                GET  /missing                            404 no    home-404
lixingzhuyi.com                GET  /wp-login.php                       404 no    -
lixingzhuyi.com                GET  /.env                               404 no    -
www.lixingzhuyi.com            GET  /en/                                301 no    -
xn--tiq2a255imxs.com           GET  /                                   301 no    -
www.xn--tiq2a255imxs.com       GET  /                                   301 no    -
arena.lixingzhuyi.com          GET  /issues                             302 no    -
library.lixingzhuyi.com        GET  /                                   200 no    library
library.lixingzhuyi.com        GET  /nope                               404 no    library-404
library.lixingzhuyi.com        GET  /exports/items.json                 200 no    {"schema_version":1}
library.lixingzhuyi.com        GET  /vendor/katex/katex.min.css         200 no    ok
library.lixingzhuyi.com        GET  /api/health                         200 19787 ok
library.lixingzhuyi.com        GET  /api/companion/v1/companion.js      200 19787 ok
library.lixingzhuyi.com        POST /api/auth/callback                  200 19787 ok
library.lixingzhuyi.com        GET  /staff/comment-reports              200 19787 ok
library.lixingzhuyi.com        GET  /admin                              200 19787 ok
library.lixingzhuyi.com        GET  /healthz                            200 19787 ok
library.lixingzhuyi.com        GET  /internal/oidc/logout               404 no    -
library.lixingzhuyi.com        POST /internal/oidc/logout               404 no    -
library.lixingzhuyi.com        GET  /api/.env                           404 no    -
library.lixingzhuyi.com        GET  /api/upload.php                     404 no    -
library.lixingzhuyi.com        GET  /.git/config                        404 no    -
forum.lixingzhuyi.com          GET  /                                   200 no    forum
forum.lixingzhuyi.com          GET  /arena/issues                       200 no    forum
forum.lixingzhuyi.com          GET  /assets/b.js                        200 no    b()
forum.lixingzhuyi.com          GET  /api/v1/feed                        200 19800 ok
forum.lixingzhuyi.com          GET  /exports/items.json                 200 19800 ok
forum.lixingzhuyi.com          GET  /exports/related.json               200 19800 ok
forum.lixingzhuyi.com          GET  /llms.txt                           200 19800 ok
forum.lixingzhuyi.com          GET  /healthz                            200 19800 ok
forum.lixingzhuyi.com          POST /internal/season/credits            404 no    -
forum.lixingzhuyi.com          POST /internal/oidc/logout               404 no    -
forum.lixingzhuyi.com          GET  /api/wp-login.php                   404 no    -
account.lixingzhuyi.com        GET  /.well-known/openid-configuration   200 19790 ok
account.lixingzhuyi.com        GET  /authorize                          200 19790 ok
account.lixingzhuyi.com        GET  /account/login                      200 19790 ok
account.lixingzhuyi.com        POST /account/auth/email                 200 19790 ok
account.lixingzhuyi.com        POST /account/auth/login                 200 19790 ok
account.lixingzhuyi.com        POST /logout                             200 19790 ok
account.lixingzhuyi.com        GET  /account/me                         200 19790 ok
account.lixingzhuyi.com        GET  /healthz                            200 19790 ok
account.lixingzhuyi.com        GET  /robots.txt                         200 19790 ok
account.lixingzhuyi.com        POST /token                              404 no    -
account.lixingzhuyi.com        GET  /userinfo                           404 no    -
account.lixingzhuyi.com        GET  /internal/accounts/names            404 no    -
account.lixingzhuyi.com        GET  /account/staff/verifications        404 no    -
account.lixingzhuyi.com        GET  /account/companion/catalog          404 no    -
account.lixingzhuyi.com        GET  /account/health                     404 no    -
account.lixingzhuyi.com        GET  /                                   404 no    -
CASES

# Redirect targets keep the path and query.
request www.lixingzhuyi.com GET '/en/?a=1'
grep -qx 'Location: https://lixingzhuyi.com/en/?a=1' <<<"$headers" || fail "www. redirect: $(grep -i '^location' <<<"$headers")"
request arena.lixingzhuyi.com GET /issues/7
grep -qx 'Location: https://forum.lixingzhuyi.com/arena/issues/7' <<<"$headers" || fail "arena. redirect: $(grep -i '^location' <<<"$headers")"

# Headers on every host; long caching only for the fingerprinted files.
for host in lixingzhuyi.com library.lixingzhuyi.com forum.lixingzhuyi.com account.lixingzhuyi.com; do
  request "$host" GET /
  for want in 'Strict-Transport-Security: max-age=31536000' 'X-Content-Type-Options: nosniff' \
    "Content-Security-Policy: frame-ancestors 'none'"; do
    grep -qx "$want" <<<"$headers" || fail "$host: missing $want"
  done
  if grep -qi '^Server:' <<<"$headers"; then fail "$host: sends a Server header"; fi
done
request library.lixingzhuyi.com GET /_astro/a.js
grep -qx 'Cache-Control: public, max-age=31536000, immutable' <<<"$headers" || fail "library /_astro/: not cached for a year"
request forum.lixingzhuyi.com GET /arena/issues
grep -qx 'Cache-Control: no-cache' <<<"$headers" || fail "forum fallback page: may be cached"
request account.lixingzhuyi.com GET /authorize
grep -qx 'X-Robots-Tag: noindex' <<<"$headers" || fail "account.: missing X-Robots-Tag"

# The account service takes the visitor's address from X-Real-IP: a forged one must be replaced
# by the real peer, and the internal headers removed. Applications get Caddy's X-Forwarded-For.
request account.lixingzhuyi.com POST /account/auth/email -H 'X-Real-IP: 203.0.113.9' \
  -H 'X-Internal-Secret: forged' -H 'Authorization: Bearer forged' -H 'X-Forwarded-For: 203.0.113.9'
seen=$(tail -1 "$log")
[ "$(cut -d' ' -f3 <<<"$seen")" = 127.0.0.1 ] || fail "account.: X-Real-IP passed through as $(cut -d' ' -f3 <<<"$seen")"
[ "$(cut -d' ' -f5-6 <<<"$seen")" = '- -' ] || fail "account.: internal headers reached the service: $seen"
request library.lixingzhuyi.com GET /api/health -H 'X-Forwarded-For: 203.0.113.9'
[ "$(tail -1 "$log" | cut -d' ' -f4)" = 127.0.0.1 ] || fail "library: X-Forwarded-For passed through: $(tail -1 "$log")"

# Bodies over the limit are cut off at the limit and refused. Caddy streams the body, so the
# application sees the start of the request; newer Caddy versions answer 413, 2.6 answers 502.
over_limit() {
  local host=$1 path=$2 size=$3
  head -c "$size" /dev/zero > "$work/big"
  request "$host" POST "$path" --data-binary @"$work/big"
  case $code in 413|502) ;; *) fail "$host$path: a $size-byte body got $code" ;; esac
}
over_limit account.lixingzhuyi.com /account/auth/email 40000
over_limit library.lixingzhuyi.com /api/comments 300000
over_limit forum.lixingzhuyi.com /api/v1/posts 300000
request account.lixingzhuyi.com POST /account/auth/email --data-binary '{"email":"a@example.com"}'
[ "$code" = 200 ] || fail "account.: a small body got $code"

# Access log: JSON, one line per request, without addresses, headers or query strings.
request library.lixingzhuyi.com GET '/api/search?q=private-words' -H 'Cookie: __Host-library=secret-cookie'
sleep 0.3
access=$(grep '"http.log.access' "$work/caddy.log" || true)
[ -n "$access" ] || fail "no access log lines"
python3 - "$work/caddy.log" <<'PY' || failed=1
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if '"http.log.access' in l]
bad = [r for r in rows if {'remote_ip', 'client_ip', 'remote_port', 'headers'} & set(r.get('request', {}))
       or 'resp_headers' in r or '?' in r.get('request', {}).get('uri', '')]
text = open(sys.argv[1]).read()
for secret in ('private-words', 'secret-cookie', '203.0.113.9', 'forged'):
    if secret in text: print(f'FAIL access log contains {secret}'); sys.exit(1)
if bad: print(f'FAIL {len(bad)} access log lines keep addresses, headers or queries'); sys.exit(1)
if not all({'host', 'uri'} <= set(r['request']) and 'status' in r for r in rows):
    print('FAIL access log lines lack host, uri or status (needed by watch.py)'); sys.exit(1)
PY

[ "$failed" = 0 ] && echo "caddy check: all cases pass"
exit "$failed"
