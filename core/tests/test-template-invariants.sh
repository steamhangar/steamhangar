#!/bin/sh
# SteamHangar vault-core -- invariants of the ONE nginx config source
# (NGINX-DUP-1, ADR-0001 addendum 2026-11-21).
#
#   core/docker/nginx.conf.template   what the image renders and runs
#   core/docker/golden/*.conf         committed default renders of the hooks
#
# Until rc23 a native twin (core/nginx/nginx.conf, run by the Windows rig
# core/tests/test-core.ps1) existed and core/docker/check-config-drift.sh
# diffed the two. Both are retired. This script keeps every check of the
# drift checker that says something about the template, the hooks or the
# goldens -- the step numbers below are the drift checker's, so a reader of
# an old ADR that cites "drift step 2d" finds the pin under the same number.
# It adds (step 2g) exact pins for the behaviour the native rig proved
# against the real Steam CDN and that the drift diff carried over to the
# template, so that behaviour cannot change without a test edit either:
#   1b. the vault_event log_format keeps its 8 literal TABs (the sweeper's
#       tab-split parser)
#   2.  the container plumbing lines, each with its exact count
#   2b. the WP TH-1a upstream rate cap: include + @miss lines, BUCKETS >=
#       worker_processes x worker_connections, no `proxy_buffering off`,
#       the cap-off render equals golden/vault-upstream-rate.conf
#   2c. the retry policy inside @miss (CORE-FIX-2 / CORE-FEAT-1b2)
#   2d. the keepalive pool include (ADR-0017): place, hook constants, the
#       allowlist map lines the hook mirrors, empty render == golden (cmp),
#       two-edge render shape, no DNS directive in a render or the golden
#   2d2. the edge target + global connection cap (ADR-0021): pins, hook
#       constants, default render == golden (cmp), edge render shape
#   2e. the HTTPS passthrough (CORE-FIX-3): exact stream block, the copy in
#       40-vault-preflight.sh, no TLS termination, BEGIN/END markers
#   2f. the worker connection budget (CORE-FIX-3 + CORE-FEAT-1 + 4a + 4d)
#   2g. exact blocks for the native rig's behaviour groups: Host allowlist
#       map, store guard map (retried "502, 200"), nocache map, BYPASS map,
#       the bounded event URI, location /depot/ and @miss (Range /
#       Accept-Encoding / If-Range stripped, loop guard, 403 guard), the
#       temp path and the catch-all, the heartbeat location
#   2h. core/Dockerfile's build-time marker counts match the template
#
# Usage:  sh core/tests/test-template-invariants.sh   [from anywhere]
# Exit:   0 = all invariants hold, 1 = an invariant broke, 2 = usage/IO error
#
# POSIX sh (dash in CI, busybox ash); no bashisms, no GNU-only sed features.

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)

TEMPLATE="$core_dir/docker/nginx.conf.template"
GOLDEN="$core_dir/docker/golden"
RATE_HOOK="$core_dir/docker/27-vault-upstream-rate.sh"
POOL_HOOK="$core_dir/docker/28-vault-upstream-pool.sh"
TLS_HOOK="$core_dir/docker/26-vault-tls-passthrough.sh"
PREFLIGHT="$core_dir/docker/40-vault-preflight.sh"
DOCKERFILE="$core_dir/Dockerfile"

for f in "$TEMPLATE" "$RATE_HOOK" "$POOL_HOOK" "$TLS_HOOK" "$PREFLIGHT" "$DOCKERFILE" \
         "$GOLDEN/vault-upstream-rate.conf" "$GOLDEN/vault-upstream-pool.conf" \
         "$GOLDEN/vault-upstream-cap.conf"; do
    [ -f "$f" ] || { echo "test-template-invariants: missing $f" >&2; exit 2; }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

ME="test-template-invariants"
fail=0
FAIL() { echo "$ME: FAIL: $*" >&2; fail=1; }

# --- 1. normalise ------------------------------------------------------------
# Strip CR, drop comment-only and blank lines, trim, collapse whitespace runs
# (none of which is semantic in nginx). Trailing comments stay: the marker
# comments (# VAULT_EVENT_LOG_LINE, # VAULT_DEBUG_TP_LINE) are what the hooks
# key on.
normalise() {
    tr -d '\r' < "$1" \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    | grep -v '^#' \
    | grep -v '^$' \
    | sed -e 's/[[:space:]][[:space:]]*/ /g'
}
normalise "$TEMPLATE" > "$work/template.norm"
T="$work/template.norm"

# expect_count <file> <expected-count> <fixed-string> <human description>
expect_count() {
    n=$(grep -F -c -x -- "$3" "$1" || true)
    [ "$n" = "$2" ] || FAIL "expected exactly $2 occurrence(s) of '$3' in the template ($4), found $n"
}

# block <opening line> <normalised file>: the block from its opening line to
# the matching brace (brace-depth count).
block() {
    awk -v open="$1" '$0 == open { f = 1 } f { print; d += gsub(/[{]/, "&") - gsub(/[}]/, "&"); if (d <= 0) exit }' "$2"
}
miss_block() { block 'location @miss {' "$1"; }

# --- 1b. the vault_event log_format keeps its literal tabs (review P7) ------
# normalise() collapses whitespace runs, so after step 1 a TAB and a SPACE
# look the same. A TAB -> space edit breaks vault-api's `line.split("\t")`.
tab=$(printf '\t')
event_line="'v1${tab}\$time_iso8601${tab}\$remote_addr${tab}\$vault_event_status${tab}\$vault_event_depot${tab}\$vault_event_uri${tab}\$bytes_sent${tab}\$host${tab}\$status';"
n=$(tr -d '\r' < "$TEMPLATE" | sed -e 's/^[[:space:]]*//' | grep -F -c -x -- "$event_line" || true)
[ "$n" = "1" ] || FAIL "expected exactly 1 vault_event log_format line with 8 literal TABs, found $n (a TAB -> space edit breaks the sweeper's tab-split parser)"

# --- 2. the container plumbing lines -----------------------------------------
# Formerly the drift checker's "deltas" (the only lines allowed to differ from
# the native file). They still matter on their own: each count is what a hook
# or vault-api relies on, and a missing one fails as hard as an extra one.
expect_count "$T" 1 "user nginx;"                                "explicit worker user"
expect_count "$T" 1 "pid /var/run/nginx.pid;"                    "pid outside the volume"
expect_count "$T" 1 "error_log /dev/stderr warn;"                "error log to stderr"
expect_count "$T" 2 'resolver ${VAULT_RESOLVER} ipv6=off valid=30s;' "resolver placeholder (http + stream)"
expect_count "$T" 4 "access_log /dev/stdout vault;"              "access log to stdout (http level + re-stated in /depot/, @miss and the runner marker; inheritance is REPLACE)"
expect_count "$T" 3 'access_log ${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE' \
    "WP 3.10 cache-event log placeholder, one per location (/depot/, @miss, runner marker)"
expect_count "$T" 1 "access_log /dev/stdout vault_tls;"          "WP CORE-FIX-3 stream log to stdout"
expect_count "$T" 2 'access_log /vault/logs/throughput-debug/throughput.log vault_tp buffer=64k flush=5s; # VAULT_DEBUG_TP_LINE' \
    "WP THROUGHPUT-DEBUG-1 opt-in debug log, one per location (/depot/, @miss)"
expect_count "$T" 1 'access_log /vault/logs/throughput-debug/throughput.log vault_tp_hb buffer=64k flush=5s; # VAULT_DEBUG_TP_LINE' \
    "WP TUNE-1a heartbeat timing line (location = /lancache-heartbeat)"
expect_count "$T" 1 'access_log /dev/stdout vault; # VAULT_DEBUG_TP_LINE' \
    "WP TUNE-1a restated normal log in the heartbeat location"
# No file-based log or pid under the prefix (the native forms): the image's
# logs go to stdout/stderr, rotated by the json-file driver.
if grep -E -q '^(pid logs/|error_log logs/|access_log logs/)' "$T"; then
    FAIL "a log or pid path under the prefix (logs/...) -- the container logs to stdout/stderr:"
    grep -E '^(pid logs/|error_log logs/|access_log logs/)' "$T" | sed 's/^/    /' >&2
fi

# --- 2b. WP TH-1a upstream rate cap ------------------------------------------
expect_count "$T" 1 "include vault-upstream-rate.conf;"         "TH-1a: upstream rate include (http level)"
expect_count "$T" 1 "proxy_buffering on;"                       "TH-1a: buffering on, required by proxy_limit_rate and proxy_store"
expect_count "$T" 1 "proxy_ignore_headers X-Accel-Buffering;"   "TH-1a: the upstream may not switch buffering off"
expect_count "$T" 1 'proxy_limit_rate $vault_upstream_rate;'    "TH-1a: the cap itself, in @miss"
if grep -q '^proxy_buffering off' "$T"; then
    FAIL "'proxy_buffering off' -- proxy_limit_rate (the upstream cap) only acts on buffered responses"
fi
# Inside location @miss: a named location inherits nothing from /depot/.
miss_block "$T" > "$work/miss.block"
if [ ! -s "$work/miss.block" ]; then
    FAIL "no 'location @miss {' block"
fi
for want in "proxy_buffering on;" "proxy_ignore_headers X-Accel-Buffering;" 'proxy_limit_rate $vault_upstream_rate;'; do
    n=$(grep -F -c -x -- "$want" "$work/miss.block" || true)
    [ "$n" = "1" ] || FAIL "'$want' must be inside location @miss (found $n there) -- @miss does not inherit it from /depot/"
done
# One share bucket per possible connection (27-vault-upstream-rate.sh).
wp=$(sed -n 's/^worker_processes \([0-9][0-9]*\);$/\1/p' "$T")
wc_=$(sed -n 's/^worker_connections \([0-9][0-9]*\);$/\1/p' "$T")
bk=$(sed -n 's/^BUCKETS=\([0-9][0-9]*\)$/\1/p' "$RATE_HOOK" 2>/dev/null || true)
case "$wp:$wc_:$bk" in
    *[!0-9:]*|:*|*::*|*:)
        FAIL "need numeric worker_processes, worker_connections (template) and BUCKETS= (27-vault-upstream-rate.sh); got '$wp', '$wc_', '$bk'" ;;
    *)
        [ "$bk" -ge $((wp * wc_)) ] || FAIL "BUCKETS=$bk in 27-vault-upstream-rate.sh is below worker_processes x worker_connections = $wp x $wc_ = $((wp * wc_)); counts above $bk would hit the default and overshoot the cap" ;;
esac
# The cap-off render equals the committed golden (comments aside).
if ! VAULT_UPSTREAM_RATE='' VAULT_UPSTREAM_RATE_WINDOW='' sh "$RATE_HOOK" "$work/rate-off.conf" > "$work/rate-off.log" 2>&1; then
    FAIL "$RATE_HOOK could not render the cap-off include:"; cat "$work/rate-off.log" >&2
else
    normalise "$GOLDEN/vault-upstream-rate.conf" > "$work/rate-golden.norm"
    normalise "$work/rate-off.conf"              > "$work/rate-off.norm"
    if ! diff -u "$work/rate-golden.norm" "$work/rate-off.norm" > "$work/rate.diff" 2>&1; then
        FAIL "core/docker/golden/vault-upstream-rate.conf is not the cap-off render of 27-vault-upstream-rate.sh (left = golden, right = render):"
        cat "$work/rate.diff" >&2
    fi
fi

# --- 2c. WP CORE-FIX-2 / CORE-FEAT-1b2: the upstream retry policy ---------
# At most one retry, `error` in the list (ADR-0017 decision 6A: a stale
# pooled connection keeps its free retry); see ADR-0017 and LEARNINGS
# core-nginx.md for why the list must neither grow nor shrink.
for want in "proxy_connect_timeout 3s;" \
            "proxy_next_upstream error timeout http_502 http_503 http_504;" \
            "proxy_next_upstream_tries 2;" \
            "proxy_next_upstream_timeout 6s;"; do
    n=$(grep -F -c -x -- "$want" "$work/miss.block" || true)
    [ "$n" = "1" ] || FAIL "'$want' must appear exactly once inside location @miss (found $n) -- CORE-FIX-2 / CORE-FEAT-1b2 retry policy (ADR-0017 decision 6A)"
done
block 'http {' "$T" > "$work/http.block"
n=$(grep -E -c '^proxy_next_upstream(_tries)? ' "$work/http.block" || true)
[ "$n" = "2" ] || FAIL "expected exactly one proxy_next_upstream and one proxy_next_upstream_tries line in the http block, found $n lines -- a second one elsewhere would override or add retries (CORE-FIX-2)"

# --- 2d. WP CORE-FEAT-1b (ADR-0017): the upstream keepalive pool include ---
expect_count "$T" 1 "include vault-upstream-pool.conf;" "CORE-FEAT-1b: upstream keepalive pool include (http level)"
after=$(awk 'f { print; exit } /^include vault-upstream-rate\.conf;$/ { f = 1 }' "$T")
[ "$after" = "include vault-upstream-pool.conf;" ] || FAIL "'include vault-upstream-pool.conf;' must directly follow 'include vault-upstream-rate.conf;' (found '$after')"
ka=$(sed -n 's/^KEEPALIVE_PER_GROUP=\([0-9][0-9]*\)$/\1/p' "$POOL_HOOK")
ceil=$(sed -n 's/^MAX_IDLE_TOTAL=\([0-9][0-9]*\)$/\1/p' "$POOL_HOOK")
if [ "$ka" != "8" ] || [ "$ceil" != "32" ]; then
    FAIL "28-vault-upstream-pool.sh must define KEEPALIVE_PER_GROUP=8 and MAX_IDLE_TOTAL=32 (ADR-0017 decision 3A); got '$ka' and '$ceil'"
fi
# The hook validates against the allowlist families and the marker of
# `map $host $vault_upstream_host` from its own constants: pin both sides.
expect_count "$T" 1 '"lancache.steamcontent.com" dist-fra1.discovery.steamserver.net;' \
    "CORE-FEAT-1b: the marker line of the Host allowlist map (the hook refuses the marker and names its target)"
expect_count "$T" 1 '"~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamcontent\.com$" $host;' \
    "CORE-FEAT-1b: allowlist family 1 (the hook requires *.steamcontent.com)"
expect_count "$T" 1 '"~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamserver\.net$" $host;' \
    "CORE-FEAT-1b: allowlist family 2 (the hook requires *.steamserver.net)"
for want in "FAMILY_1=steamcontent.com" "FAMILY_2=steamserver.net" "MARKER=lancache.steamcontent.com"; do
    n=$(grep -F -c -x -- "$want" "$POOL_HOOK" || true)
    [ "$n" = "1" ] || FAIL "expected exactly 1 line '$want' in 28-vault-upstream-pool.sh (found $n) -- the hook's allowlist constants must match the \$vault_upstream_host map"
done
if ! VAULT_UPSTREAM_EDGE='' VAULT_UPSTREAM_POOL_HOSTS='' VAULT_UPSTREAM_MAX_CONNS='' VAULT_PREFILL_MAX_THREADS='' \
        sh "$POOL_HOOK" "$work/pool-empty.conf" > "$work/pool-empty.log" 2>&1; then
    FAIL "$POOL_HOOK could not render the empty include:"; cat "$work/pool-empty.log" >&2
elif ! cmp -s "$GOLDEN/vault-upstream-pool.conf" "$work/pool-empty.conf"; then
    FAIL "core/docker/golden/vault-upstream-pool.conf is not byte-identical to the empty render of 28-vault-upstream-pool.sh (left = golden, right = render):"
    diff -u "$GOLDEN/vault-upstream-pool.conf" "$work/pool-empty.conf" >&2 || true
fi
mkdir "$work/two"
if ! VAULT_UPSTREAM_EDGE='' VAULT_UPSTREAM_POOL_HOSTS='cache1-fra2.steamcontent.com dist-fra1.discovery.steamserver.net' \
        sh "$POOL_HOOK" "$work/two/pool-two.conf" > "$work/pool-two.log" 2>&1; then
    FAIL "$POOL_HOOK could not render a two-edge list:"; cat "$work/pool-two.log" >&2
else
    n=$(grep -c '^upstream ' "$work/two/pool-two.conf" || true)
    [ "$n" = "2" ] || FAIL "a two-edge list rendered $n upstream blocks, expected 2"
fi
for f in "$GOLDEN/vault-upstream-pool.conf" "$work/two/pool-two.conf"; do
    # Anchored to the DIRECTIVE (LEARNINGS core-nginx.md: a guard grepping a
    # bare name also matches comments).
    if [ -f "$f" ] && grep -q '^[[:space:]]*resolver[[:space:]]' "$f"; then
        FAIL "a 'resolver' directive in $f -- pool groups must inherit the http-level resolver (ADR-0017 (c))"
    fi
done

# --- 2d2. WP CORE-FIX-4a (ADR-0021): edge target + global connection cap ---
expect_count "$T" 1 "include vault-upstream-cap.conf;" "CORE-FIX-4a: the global upstream cap include (inside @miss)"
n=$(grep -F -c -x -- "include vault-upstream-cap.conf;" "$work/miss.block" || true)
[ "$n" = "1" ] || FAIL "'include vault-upstream-cap.conf;' must be inside location @miss (found $n there)"
expect_count "$T" 1 'limit_conn_zone $server_port zone=vault_upstream_total:64k;' "CORE-FIX-4a: the constant-key cap zone"
expect_count "$T" 1 "limit_conn_status 503;" "CORE-FIX-4a: over-limit answer is 503"
expect_count "$T" 1 'proxy_pass http://$vault_upstream_target$request_uri;' "CORE-FIX-4a: @miss dials the pooled target"
expect_count "$T" 1 'proxy_set_header Host $vault_upstream_target;' "CORE-FIX-4a: Host header = the dial target"
for want in "CAP_DEFAULT=16" "CAP_MIN=1" "CAP_MAX=64" "CAP_ZONE=vault_upstream_total"; do
    n=$(grep -F -c -x -- "$want" "$POOL_HOOK" || true)
    [ "$n" = "1" ] || FAIL "expected exactly 1 line '$want' in 28-vault-upstream-pool.sh (found $n) -- ADR-0021 decision 3"
done
# The pool hook writes the cap include next to its output file.
if [ ! -f "$work/vault-upstream-cap.conf" ]; then
    FAIL "the empty pool render wrote no vault-upstream-cap.conf next to it"
elif ! cmp -s "$GOLDEN/vault-upstream-cap.conf" "$work/vault-upstream-cap.conf"; then
    FAIL "core/docker/golden/vault-upstream-cap.conf is not byte-identical to the default render of 28-vault-upstream-pool.sh (left = golden, right = render):"
    diff -u "$GOLDEN/vault-upstream-cap.conf" "$work/vault-upstream-cap.conf" >&2 || true
fi
mkdir "$work/edge"
if ! VAULT_UPSTREAM_EDGE='dist-fra1.discovery.steamserver.net' VAULT_UPSTREAM_MAX_CONNS='' VAULT_PREFILL_MAX_THREADS='' \
        sh "$POOL_HOOK" "$work/edge/edge.conf" > "$work/edge.log" 2>&1; then
    FAIL "$POOL_HOOK could not render the edge mode:"; cat "$work/edge.log" >&2
else
    n=$(grep -c '^upstream ' "$work/edge/edge.conf" || true)
    if [ "$n" != "1" ] || ! grep -qx '    default dist-fra1.discovery.steamserver.net;' "$work/edge/edge.conf"; then
        FAIL "the edge render must hold exactly one upstream group and the edge as map default (found $n groups)"
    fi
fi

# --- 2e. WP CORE-FIX-3: the HTTPS passthrough (stream {}, port 443) -------
# The SNI allowlist map is the only thing between this listener and an open
# TCP relay (ADR-0020), so the whole block is pinned as an exact directive
# list (review S1: required-line pins let an added `set`, a server-level
# `resolver` or `proxy_protocol on;` through). @TLS_CLIENT@/@TLS_TOTAL@ are
# the literal defaults here, the env values in the rendered config
# (26-vault-tls-passthrough.sh); 40-vault-preflight.sh carries the same list
# for the rendered config at boot, and the check below keeps the copies equal.
expected_stream() {
    cat <<'VAULT_TLS_STREAM_EOF' | sed -e "s|@RESOLVER@|$1|" -e "s|@ACCESS_LOG@|$2|" -e "s|@TLS_CLIENT@|$3|" -e "s|@TLS_TOTAL@|$4|"
stream {
resolver @RESOLVER@ ipv6=off valid=30s;
resolver_timeout 5s;
map $ssl_preread_server_name $vault_tls_upstream {
default "";
"~*^(?=.{1,253}\z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+steamcontent\.com\z" $ssl_preread_server_name:443;
}
limit_conn_zone $binary_remote_addr zone=vault_tls_client:1m;
limit_conn_zone $server_port zone=vault_tls_total:1m;
log_format vault_tls escape=default
'$time_local tls client=$remote_addr sni="$ssl_preread_server_name" '
'target="$vault_tls_upstream" upstream=$upstream_addr status=$status '
'bytes_sent=$bytes_sent bytes_received=$bytes_received '
'session_time=$session_time';
access_log @ACCESS_LOG@ vault_tls;
server {
listen 443;
ssl_preread on;
preread_timeout 5s;
limit_conn vault_tls_client @TLS_CLIENT@;
limit_conn vault_tls_total @TLS_TOTAL@;
proxy_connect_timeout 3s;
proxy_next_upstream off;
proxy_timeout 5m;
proxy_pass $vault_tls_upstream;
}
}
VAULT_TLS_STREAM_EOF
}
n=$(grep -c -x -F 'stream {' "$T" || true)
[ "$n" = "1" ] || FAIL "expected exactly one top-level 'stream {' block, found $n (CORE-FIX-3)"
expected_stream '${VAULT_RESOLVER}' '/dev/stdout' 64 256 > "$work/stream.expected"
block 'stream {' "$T" > "$work/stream.block"
if ! diff -u "$work/stream.expected" "$work/stream.block" > "$work/stream.diff" 2>&1; then
    FAIL "the stream block is not the reviewed directive list (left = expected, right = template) -- CORE-FIX-3:"
    cat "$work/stream.diff" >&2
fi
sed -n "/<<'VAULT_TLS_STREAM_EOF'\$/,/^VAULT_TLS_STREAM_EOF\$/p" "$PREFLIGHT" | sed '1d;$d' > "$work/stream.preflight"
expected_stream '@RESOLVER@' '@ACCESS_LOG@' '@TLS_CLIENT@' '@TLS_TOTAL@' > "$work/stream.expected.raw"
if ! diff -u "$work/stream.expected.raw" "$work/stream.preflight" > "$work/stream-pf.diff" 2>&1; then
    FAIL "40-vault-preflight.sh's expected stream block differs from this script's (left = here, right = preflight):"
    cat "$work/stream-pf.diff" >&2
fi
# Port 80 is the only HTTP listener: 443 belongs to the passthrough only.
n=$(grep '^listen ' "$work/http.block" | grep -c -v -x -F 'listen 80;' || true)
[ "$n" = "0" ] || FAIL "the http block listens on something other than port 80 (CORE-FIX-3: 443 belongs to the passthrough only)"
# No TLS termination, anywhere.
if grep -E -q '^(ssl_certificate|ssl_certificate_key|proxy_ssl[a-z_]*) |^listen .*[[:space:]]ssl([[:space:];]|$)' "$T"; then
    FAIL "the template terminates TLS (ssl_certificate / proxy_ssl / 'listen ... ssl') -- the passthrough must never decrypt (CORE-FIX-3, ADR-0020):"
    grep -E '^(ssl_certificate|ssl_certificate_key|proxy_ssl[a-z_]*) |^listen .*[[:space:]]ssl([[:space:];]|$)' "$T" | sed 's/^/    /' >&2
fi
# The on/off switch: the raw template's marker comments enclose exactly the
# stream block, and deleting that range leaves the http block untouched.
for m in "# VAULT_TLS_PASSTHROUGH_BEGIN" "# VAULT_TLS_PASSTHROUGH_END"; do
    n=$(tr -d '\r' < "$TEMPLATE" | grep -c -x -F -- "$m" || true)
    [ "$n" = "1" ] || FAIL "expected exactly 1 line '$m', found $n (26-vault-tls-passthrough.sh deletes the stream block by these markers)"
done
tr -d '\r' < "$TEMPLATE" | sed -n '/^# VAULT_TLS_PASSTHROUGH_BEGIN$/,/^# VAULT_TLS_PASSTHROUGH_END$/p' > "$work/tls-marked.raw"
normalise "$work/tls-marked.raw" > "$work/tls-marked.norm"
if ! diff -u "$work/stream.block" "$work/tls-marked.norm" > "$work/tls-marked.diff" 2>&1; then
    FAIL "the VAULT_TLS_PASSTHROUGH_BEGIN/END markers do not enclose exactly the stream block (left = stream block, right = marked range):"
    cat "$work/tls-marked.diff" >&2
fi
tr -d '\r' < "$TEMPLATE" | sed '/^# VAULT_TLS_PASSTHROUGH_BEGIN$/,/^# VAULT_TLS_PASSTHROUGH_END$/d' > "$work/tls-off.raw"
normalise "$work/tls-off.raw" > "$work/tls-off.norm"
if grep -E -q '^stream [{]$|^listen 443|^ssl_preread|vault_tls' "$work/tls-off.norm"; then
    FAIL "deleting the marked range leaves a stream/443/vault_tls trace -- VAULT_TLS_PASSTHROUGH=0 would not switch the passthrough off"
fi
block 'http {' "$work/tls-off.norm" > "$work/http-off.block"
cmp -s "$work/http.block" "$work/http-off.block" || FAIL "deleting the marked range changes the http block -- VAULT_TLS_PASSTHROUGH=0 must not touch the HTTP cache"

# --- 2f. the worker connection budget (CORE-FIX-3 + CORE-FEAT-1) -----------
# One worker with 1024 connections; the stream cap keeps half for HTTP
# (2 x 256 = 512, ADR-0020 "Loop bound"); the pool's idle ceiling and the
# upstream cap's 2C live in the HTTP half; at the env ceilings of the
# passthrough caps live HTTP requests keep at least HTTP_LIVE_MIN.
TLS_TOTAL=256
expect_count "$T" 1 "worker_processes 1;"       "budget: one worker (the pool ceiling and the stream cap assume it)"
expect_count "$T" 1 "worker_connections 1024;"  "budget: 1024 connections (2 x stream cap = half of it)"
expect_count "$T" 1 "limit_conn vault_tls_total $TLS_TOTAL;" "budget: the stream total cap the arithmetic below uses"
pool_ceil=$ceil
case "$wc_:$pool_ceil" in
    *[!0-9:]*|:*|*:)
        FAIL "need numeric worker_connections (template) and MAX_IDLE_TOTAL= (28-vault-upstream-pool.sh) for the budget; got '$wc_', '$pool_ceil'" ;;
    *)
        [ $((2 * TLS_TOTAL)) -le $((wc_ / 2)) ] || FAIL "budget: the stream cap ($TLS_TOTAL sessions = $((2 * TLS_TOTAL)) connections) exceeds half of worker_connections ($wc_) -- the HTTP cache no longer keeps half (ADR-0020)"
        [ $((wc_ / 2 - pool_ceil)) -ge 256 ] || FAIL "budget: the HTTP half of worker_connections ($((wc_ / 2))) minus the pool's idle ceiling ($pool_ceil) leaves fewer than 256 connections for live HTTP requests" ;;
esac
cap_max=$(sed -n 's/^CAP_MAX=\([0-9][0-9]*\)$/\1/p' "$POOL_HOOK" 2>/dev/null || true)
case "$wc_:$cap_max" in
    *[!0-9:]*|:*|*:)
        FAIL "need numeric worker_connections and CAP_MAX= (28-vault-upstream-pool.sh) for the cap budget; got '$wc_', '$cap_max'" ;;
    *)
        [ $((wc_ / 2 - 2 * cap_max)) -ge 256 ] || FAIL "budget: the HTTP half of worker_connections ($((wc_ / 2))) minus 2 x CAP_MAX ($((2 * cap_max))) leaves fewer than 256 connections for live HTTP requests (ADR-0021)" ;;
esac
HTTP_LIVE_MIN=64
hook_num() { sed -n "s/^$1=\([0-9][0-9]*\)\$/\1/p" "$TLS_HOOK" 2>/dev/null || true; }
t_cd=$(hook_num TLS_CLIENT_DEFAULT); t_cm=$(hook_num TLS_CLIENT_MAX)
t_td=$(hook_num TLS_TOTAL_DEFAULT); t_tm=$(hook_num TLS_TOTAL_MAX)
case "$t_cd:$t_cm:$t_td:$t_tm:$wc_:$cap_max:$pool_ceil" in
    *[!0-9:]*|:*|*::*|*:)
        FAIL "need numeric TLS_CLIENT_DEFAULT/_MAX, TLS_TOTAL_DEFAULT/_MAX= (26-vault-tls-passthrough.sh), worker_connections, CAP_MAX and MAX_IDLE_TOTAL for the passthrough budget; got '$t_cd' '$t_cm' '$t_td' '$t_tm' '$wc_' '$cap_max' '$pool_ceil'" ;;
    *)
        if [ "$t_cd" != "64" ] || [ "$t_td" != "$TLS_TOTAL" ]; then
            FAIL "26-vault-tls-passthrough.sh's defaults ($t_cd/$t_td) are not the literal caps in the template (64/$TLS_TOTAL) -- the hook would refuse to boot"
        fi
        if [ "$t_cd" -gt "$t_cm" ] || [ "$t_td" -gt "$t_tm" ] || [ "$t_cm" -gt "$t_tm" ]; then
            FAIL "passthrough cap ranges inconsistent: client default $t_cd / max $t_cm, total default $t_td / max $t_tm"
        fi
        [ $((wc_ - 2 * t_tm - 2 * cap_max - pool_ceil)) -ge "$HTTP_LIVE_MIN" ] || FAIL "budget: at the env ceilings the passthrough ($((2 * t_tm)) connections), the upstream cap ($((2 * cap_max))) and the pool's idle ceiling ($pool_ceil) leave $((wc_ - 2 * t_tm - 2 * cap_max - pool_ceil)) of $wc_ worker connections for live HTTP requests, fewer than $HTTP_LIVE_MIN" ;;
esac

# --- 2g. exact blocks for the behaviour the native rig proved (NGINX-DUP-1) --
# core/tests/test-core.ps1 (WP 1.1, retired) ran these against the real
# Steam CDN and the drift diff carried them to the template. Pinned as exact
# directive lists in the debug-off view (the # VAULT_DEBUG_TP_LINE lines
# removed, as 31-vault-debug-throughput.sh does by default), so an added
# directive fails as hard as a removed one. Live coverage on the template:
# deploy/tests/verify-stack.sh 5b-5f, 5i, 10e (core/README.md "Testing").
# Covered exactly: every map the cache path or the event log keys on
# ($vault_upstream_host, $vault_host_allowed, $vault_store_path,
# $vault_try_target, $vault_event_status, $vault_event_depot,
# $vault_event_uri), the port-80 server's own directives and its location
# list, and every location in it (/depot/, @miss, /tmp/, the catch-all,
# the runner marker, /health, the heartbeat). An extra access_log anywhere
# (a server- or http-level event log would also log the heartbeat) fails
# the total count below. Not pinned exactly: the log_format bodies other
# than vault_event (step 1b) and the http-level temp paths.
# expect_block <name> <opening line> <<EOF ... EOF (expected block on stdin)
expect_block() {
    cat > "$work/want.block"
    block "$2" "$T" | grep -v '# VAULT_DEBUG_TP_LINE$' > "$work/have.block" || true
    if ! diff -u "$work/want.block" "$work/have.block" > "$work/block.diff" 2>&1; then
        FAIL "$1: '$2' is not the reviewed directive list (left = expected, right = template):"
        cat "$work/block.diff" >&2
    fi
}
# Groups 5/6 (Host allowlist, ADR req 4): exactly the marker and two
# families; a fourth entry would widen the proxy past Valve's CDN.
expect_block "Host allowlist" 'map $host $vault_upstream_host {' <<'EOF'
map $host $vault_upstream_host {
default "";
"lancache.steamcontent.com" dist-fra1.discovery.steamserver.net;
"~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamcontent\.com$" $host;
"~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamserver\.net$" $host;
}
EOF
expect_block "Host allowlist" 'map $vault_upstream_host $vault_host_allowed {' <<'EOF'
map $vault_upstream_host $vault_host_allowed {
default 1;
"" 0;
}
EOF
# Group 7 (B1): a retried "502, 200" is still stored; nothing but 200 is.
expect_block "store guard" 'map $upstream_status $vault_store_path {' <<'EOF'
map $upstream_status $vault_store_path {
200 "$document_root$uri";
"~, 200$" "$document_root$uri";
default "";
}
EOF
# Group 4 (ADR req 3): ?nocache=1 skips the stored file.
expect_block "nocache bypass" 'map $arg_nocache $vault_try_target {' <<'EOF'
map $arg_nocache $vault_try_target {
default $uri;
"1" "/__steamhangar_force_miss__$uri";
}
EOF
# Group 11: a nocache request is typed BYPASS in the event log.
expect_block "event status" 'map "$vault_cache_status:$arg_nocache" $vault_event_status {' <<'EOF'
map "$vault_cache_status:$arg_nocache" $vault_event_status {
default $vault_cache_status;
"MISS:1" BYPASS;
"HIT:1" BYPASS;
}
EOF
# The depot id the event log and vault-api's mapping key on: digits only,
# taken from the /depot/<id>/ prefix (a wider regex would log non-depot
# paths under a depot id).
expect_block "event depot" 'map $uri $vault_event_depot {' <<'EOF'
map $uri $vault_event_depot {
default "-";
"~^/depot/(?<depot_id>[0-9]+)/" $depot_id;
}
EOF
# Group 13: the logged URI is bounded across newlines ((?s), LEARNINGS
# core-nginx.md) and escape=default turns control bytes into \xXX.
expect_block "event uri" 'map $uri $vault_event_uri {' <<'EOF'
map $uri $vault_event_uri {
default "";
"~^(?s)(?<bounded_uri>.{1,300})" $bounded_uri;
}
EOF
expect_count "$T" 1 "log_format vault_event escape=default" "group 13: control bytes escaped in the event log"
# The HIT path: loop guard (508), GET only, no directory listing.
expect_block "location /depot/" 'location /depot/ {' <<'EOF'
location /depot/ {
set $vault_cache_status HIT;
if ($http_x_steamhangar_hop) {
return 508;
}
if ($request_method != GET) {
return 405;
}
if ($uri ~ /$) {
return 404;
}
try_files $vault_try_target @miss;
access_log /dev/stdout vault;
access_log ${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE
}
EOF
# The MISS path. Groups 3 and 8: Range, Accept-Encoding and If-Range are
# stripped upstream (a stored gzip or partial body is served corrupt);
# group 5: an off-list Host gets 403 before any proxy_pass; the hop header
# feeds the loop guard above. Every retry/cap/rate line is pinned again here
# in place, so a new directive in @miss needs this list edited too.
expect_block "location @miss" 'location @miss {' <<'EOF'
location @miss {
set $vault_cache_status MISS;
access_log /dev/stdout vault;
access_log ${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE
if ($vault_host_allowed = 0) {
return 403;
}
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $vault_upstream_target;
proxy_set_header X-SteamHangar-Hop "1";
proxy_set_header Range "";
proxy_set_header Accept-Encoding "";
proxy_set_header If-Range "";
proxy_connect_timeout 3s;
proxy_next_upstream error timeout http_502 http_503 http_504;
proxy_next_upstream_tries 2;
proxy_next_upstream_timeout 6s;
include vault-upstream-cap.conf;
proxy_pass http://$vault_upstream_target$request_uri;
proxy_store $vault_store_path;
proxy_store_access user:rw group:r all:r;
proxy_buffering on;
proxy_ignore_headers X-Accel-Buffering;
proxy_limit_rate $vault_upstream_rate;
}
EOF
# Group 9 (S3): temp files live outside the root and are never served; the
# SEC-HARDEN-1 catch-all answers everything else outside /depot/ with 404.
expect_count "$T" 1 "root cache;"               "group 9: the document root is cache/"
expect_count "$T" 1 "proxy_temp_path tmp/proxy;" "group 9: temp files outside the root, same volume"
expect_block "temp path" 'location ^~ /tmp/ {' <<'EOF'
location ^~ /tmp/ {
return 404;
}
EOF
expect_block "catch-all" 'location / {' <<'EOF'
location / {
return 404;
}
EOF
# Groups 1 and 12: the heartbeat SteamPrefill requires, with NO event-log
# line (debug-off view; the opt-in timing lines are pinned in step 2).
expect_block "heartbeat" 'location = /lancache-heartbeat {' <<'EOF'
location = /lancache-heartbeat {
add_header X-LanCache-Processed-By "steamhangar";
return 200;
}
EOF

# The runner marker (WP D15-RUNNER): answered locally, logged to both logs.
expect_block "runner marker" 'location ^~ /steamhangar/runner-mark/ {' <<'EOF'
location ^~ /steamhangar/runner-mark/ {
set $vault_cache_status RUNNER;
access_log /dev/stdout vault;
access_log ${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE
add_header X-SteamHangar-Runner-Mark "steamhangar";
return 204;
}
EOF
# /health: liveness plus the settings generation header (D16c).
expect_block "health" 'location = /health {' <<'EOF'
location = /health {
include vault-settings-generation.conf;
return 200 "ok\n";
}
EOF
# The port-80 server's own directives (depth 1, nested blocks skipped) and
# its location list, in order. A server-level access_log -- which every
# location without its own, the heartbeat included, would inherit -- or a
# new location fails here.
http_server() { block 'server {' "$work/http.block"; }
http_server | awk 'NR == 1 { d = 1; next } { if (d == 1 && $0 !~ /[{]$/ && $0 != "}") print; d += gsub(/[{]/, "&") - gsub(/[}]/, "&") }' > "$work/server.own"
cat > "$work/server.own.want" <<'EOF'
listen 80;
root cache;
proxy_temp_path tmp/proxy;
set $vault_cache_status "-";
EOF
if ! diff -u "$work/server.own.want" "$work/server.own" > "$work/server.diff" 2>&1; then
    FAIL "the port-80 server's own directives are not the reviewed list (left = expected, right = template):"
    cat "$work/server.diff" >&2
fi
http_server | grep '^location ' > "$work/server.locs" || true
cat > "$work/server.locs.want" <<'EOF'
location /depot/ {
location @miss {
location ^~ /tmp/ {
location / {
location ^~ /steamhangar/runner-mark/ {
location = /health {
location = /lancache-heartbeat {
EOF
if ! diff -u "$work/server.locs.want" "$work/server.locs" > "$work/locs.diff" 2>&1; then
    FAIL "the port-80 server's locations are not the reviewed list (left = expected, right = template):"
    cat "$work/locs.diff" >&2
fi
# Every access_log in the file, debug lines included: http 1, /depot/ 3,
# @miss 3, runner marker 2, heartbeat 2 (both debug-marked), stream 1. The
# per-line counts in step 2 pin each known line; this pins that no other
# access_log exists anywhere (group 12: the heartbeat writes no event line
# only while no enclosing level declares one).
expect_count_re() {
    n=$(grep -E -c -- "$3" "$1" || true)
    [ "$n" = "$2" ] || FAIL "expected exactly $2 line(s) matching '$3' in the template ($4), found $n"
}
expect_count_re "$T" 12 '^access_log ' "every access_log: http 1, /depot/ 3, @miss 3, runner 2, heartbeat 2, stream 1"

# --- 2h. core/Dockerfile's build-time marker counts ---------------------------
# The image build asserts how many marker lines the template has. That RUN
# step only executes in a docker build, which the docker-free gate does not
# do (WP D15-RUNNER added a third event-log line and only the CI image build
# noticed), so the numbers are cross-checked here.
for marker in VAULT_EVENT_LOG_LINE VAULT_DEBUG_TP_LINE; do
    want=$(sed -n "s/.*grep -c '# $marker' [^)]*)\" = \"\([0-9][0-9]*\)\" ].*/\1/p" "$DOCKERFILE")
    have=$(grep -c "# $marker" "$TEMPLATE" || true)
    if [ -z "$want" ]; then
        FAIL "no '# $marker' count assert found in $DOCKERFILE (GRAMMAR drift: the assert moved or changed shape -- re-point this check)"
    elif [ "$want" != "$have" ]; then
        FAIL "$DOCKERFILE asserts $want '# $marker' line(s), the template has $have -- the image build would fail; update the Dockerfile assert"
    fi
done

if [ "$fail" != "0" ]; then
    echo "$ME: FAIL -- see the messages above" >&2
    exit 1
fi
lines=$(wc -l < "$T" | tr -d ' ')
echo "$ME: OK -- every invariant holds ($lines normalised directive lines in core/docker/nginx.conf.template)"
