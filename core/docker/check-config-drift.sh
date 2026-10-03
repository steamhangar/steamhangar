#!/bin/sh
# SteamHangar WP 1.9 -- drift check between the native and container nginx configs.
#
#   core/nginx/nginx.conf              (WP 1.1, reviewed, test-covered by
#                                       core/tests/test-core.ps1 against the real
#                                       Steam CDN -- the source of truth)
#   core/docker/nginx.conf.template    (WP 1.9, what actually runs in the image)
#
# The container variant exists because a handful of directives cannot be
# shared (log destinations, pid path, worker user, and the resolver and
# event-log path becoming env placeholders). The deltas enumerated in step 2
# below are the ONLY allowed differences -- seven kinds today; this list, not
# any prose count elsewhere, is authoritative. Everything else -- every map,
# every proxy_set_header, the store guard, the Host allowlist, the nocache
# bypass, the request guards, the log_format -- MUST stay identical, or the
# container silently stops being the thing that was reviewed and tested.
#
# This script makes that contract executable:
#   1. normalise both files (drop comments and blank lines, trim, collapse runs
#      of whitespace -- none of which is semantic in nginx)
#   1b. assert the vault_event log_format line keeps its LITERAL tabs in both
#      raw files (step 1's whitespace collapse would otherwise hide a
#      TAB -> space regression, which breaks the sweeper's tab-split parser)
#   2. un-apply the enumerated container deltas from the template, asserting
#      each one was present EXACTLY the expected number of times (so a delta
#      that silently disappears is also a failure, not just an unexpected
#      extra line)
#   2b. pin the WP TH-1a upstream rate cap, which is NOT a textual delta:
#      both files carry the identical `include vault-upstream-rate.conf;`
#      and the identical @miss directives (proxy_buffering on,
#      proxy_ignore_headers X-Accel-Buffering, proxy_limit_rate
#      $vault_upstream_rate). What differs is the INCLUDED FILE: the
#      container renders /etc/nginx/vault-upstream-rate.conf at start
#      (27-vault-upstream-rate.sh), the native rig uses the static
#      core/nginx/vault-upstream-rate.conf. Asserted here: each pinned line
#      exactly once in each file AND inside the location @miss block (awk
#      brace-depth extraction; @miss inherits nothing from /depot/), BUCKETS
#      in the hook >= worker_processes x worker_connections of the template,
#      no `proxy_buffering off` anywhere, and the
#      static native include identical (comments aside) to the script's
#      cap-off render -- so "native = container with no cap configured"
#      stays true by machine check
#   2c. pin the upstream retry policy inside location @miss in both files
#      (WP CORE-FIX-2, `error` restored by WP CORE-FEAT-1b2 per ADR-0017
#      decision 6A): the exact `proxy_next_upstream error timeout http_502
#      http_503 http_504;` line, proxy_next_upstream_tries 2, and no second
#      proxy_next_upstream* line anywhere else in the http block (the stream
#      block has its own `proxy_next_upstream off;`, pinned in 2e)
#   2d. pin the WP CORE-FEAT-1b upstream keepalive pool (ADR-0017), the same
#      include contract as 2b: `include vault-upstream-pool.conf;` exactly
#      once in each file and directly after the rate include; the static
#      native core/nginx/vault-upstream-pool.conf byte-identical (cmp, not
#      normalised -- the empty render is comments only) to the hook's empty
#      render; a rendered list and the static file free of any `resolver`
#      directive (groups must inherit the http-level one, ADR-0017 (c));
#      the hook's keepalive-per-group and idle-ceiling constants at the
#      ADR-0017 decision 3A values
#   2e. pin the WP CORE-FIX-3 HTTPS passthrough (the stream {} block, port
#      443) in both files: the SNI allowlist map is exactly one full-match
#      *.steamcontent.com regex plus an empty default, ssl_preread on,
#      proxy_pass of the map result, one upstream connection per client
#      (proxy_next_upstream off), the connection caps, and NO TLS
#      termination anywhere (no ssl_certificate, no `listen ... ssl`, no
#      proxy_ssl). In the raw template, the BEGIN/END marker comments must
#      enclose exactly the stream block, and deleting that range (what
#      26-vault-tls-passthrough.sh does for VAULT_TLS_PASSTHROUGH=0) must
#      leave no stream/443 trace and the http block untouched
#   2f. pin the worker connection budget both features are sized against:
#      worker_processes 1 and worker_connections 1024 exactly, the stream
#      total cap at 256 (2 x 256 = half of 1024), and the pool's idle
#      ceiling (MAX_IDLE_TOTAL) fitting into the HTTP half with room left
#   3. diff. Any remaining difference fails with a unified diff.
#
# Usage:  sh core/docker/check-config-drift.sh   [from anywhere]
# Exit:   0 = in sync, 1 = drift (diff printed), 2 = usage/IO error
#
# Runs on any POSIX shell (verified in WSL2/Ubuntu 26.04 and inside the built
# image); no bashisms, no GNU-only sed features.

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)

NATIVE="$core_dir/nginx/nginx.conf"
TEMPLATE="$core_dir/docker/nginx.conf.template"

[ -f "$NATIVE" ]   || { echo "check-config-drift: missing $NATIVE" >&2;   exit 2; }
[ -f "$TEMPLATE" ] || { echo "check-config-drift: missing $TEMPLATE" >&2; exit 2; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

# --- 1. normalise ------------------------------------------------------------
# Strip CR (a Windows checkout of the .conf is legitimate -- it is run natively
# on Windows), drop comment-only and blank lines, trim, collapse whitespace runs.
normalise() {
    tr -d '\r' < "$1" \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    | grep -v '^#' \
    | grep -v '^$' \
    | sed -e 's/[[:space:]][[:space:]]*/ /g'
}

normalise "$NATIVE"   > "$work/native.norm"
normalise "$TEMPLATE" > "$work/template.norm"

fail=0

# --- 1b. the vault_event log_format keeps its literal tabs (review P7) ------
# normalise() collapses whitespace runs, so after step 1 a TAB between two
# fields and a SPACE between them look the same -- a regression that turns
# the 9-field TSV into a space-separated line would pass the diff in step 3
# while breaking vault-api's `line.split("\t")` parser. Assert the raw line
# (CR stripped, nothing else) with all 8 literal tabs, exactly once per file.
tab=$(printf '\t')
event_line="'v1${tab}\$time_iso8601${tab}\$remote_addr${tab}\$vault_event_status${tab}\$vault_event_depot${tab}\$vault_event_uri${tab}\$bytes_sent${tab}\$host${tab}\$status';"
for f in "$NATIVE" "$TEMPLATE"; do
    n=$(tr -d '\r' < "$f" | sed -e 's/^[[:space:]]*//' | grep -F -c -x -- "$event_line" || true)
    if [ "$n" != "1" ]; then
        echo "check-config-drift: FAIL: expected exactly 1 vault_event log_format line with 8 literal TABs in $f, found $n (a TAB -> space edit breaks the sweeper's tab-split parser)" >&2
        fail=1
    fi
done

# --- 2. un-apply the enumerated container deltas ----------------------------

# expect_once <file> <fixed-string> <human description>
expect_once() {
    n=$(grep -F -c -x -- "$2" "$1" || true)
    if [ "$n" != "1" ]; then
        echo "check-config-drift: FAIL: expected exactly 1 occurrence of '$2' in the container template ($3), found $n" >&2
        fail=1
    fi
}

# expect_count <file> <expected-count> <fixed-string> <human description>
# Same as expect_once but for deltas that legitimately appear more than
# once -- the WP 3.10 event-log access_log line is declared once in
# location /depot/ and once in location @miss (see core/README.md "Cache
# event log" and the CONTAINER DELTA comments at each site), so "found 1"
# would be just as much a drift signal there as "found 0".
expect_count() {
    n=$(grep -F -c -x -- "$3" "$1" || true)
    if [ "$n" != "$2" ]; then
        echo "check-config-drift: FAIL: expected exactly $2 occurrence(s) of '$3' in $1 ($4), found $n" >&2
        fail=1
    fi
}

expect_once "$work/template.norm" "user nginx;"                                "delta 1: explicit worker user"
expect_once "$work/template.norm" "pid /var/run/nginx.pid;"                    "delta 2: pid outside the volume"
expect_once "$work/template.norm" "error_log /dev/stderr warn;"                "delta 3: error log to stderr"
# delta 4 appears twice: http {} and the WP CORE-FIX-3 stream {} block.
expect_count "$work/template.norm" 2 'resolver ${VAULT_RESOLVER} ipv6=off valid=30s;' "delta 4: resolver placeholder (http + stream)"
# delta 5 appears 3 times: once at http level (inherited by /health,
# /lancache-heartbeat, /tmp/) and once each re-stated inside location
# /depot/ and location @miss (WP 3.10 blocker fix -- declaring the event
# log's own access_log in those locations REPLACES, not adds to, the
# inherited one unless the "vault" log is re-stated alongside it; see the
# blocker-fix comments at both sites in nginx.conf.template).
expect_count "$work/template.norm" 3 "access_log /dev/stdout vault;"           "delta 5: access log to stdout (http level + re-stated in /depot/ and @miss)"
expect_count "$work/template.norm" 2 'access_log ${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE' \
    "delta 6: WP 3.10 cache-event log placeholder, one per location (/depot/, @miss)"
expect_once "$work/template.norm" "access_log /dev/stdout vault_tls;"          "delta 7: WP CORE-FIX-3 stream log to stdout"

# The native config must NOT already contain the container forms (would mean the
# two files drifted in the other direction, e.g. someone containerised the
# native config in place).
expect_once "$work/native.norm" "pid logs/nginx.pid;"                          "native: pid under the prefix"
expect_once "$work/native.norm" "error_log logs/error.log warn;"               "native: error log to a file"
expect_count "$work/native.norm" 2 "resolver 1.1.1.1 ipv6=off valid=30s;"      "native: literal resolver (http + stream)"
expect_count "$work/native.norm" 3 "access_log logs/access.log vault;"         "native: access log to a file (http level + re-stated in /depot/ and @miss)"
expect_count "$work/native.norm" 2 "access_log logs/event.log vault_event buffer=64k flush=5s;" \
    "native: WP 3.10 cache-event log, hardcoded ON, one per location (/depot/, @miss)"
expect_once "$work/native.norm" "access_log logs/tls.log vault_tls;"           "native: WP CORE-FIX-3 stream log to a file"

# --- 2b. WP TH-1a upstream rate cap: shared lines + the native include ----
# Identical in both files (so step 3's diff alone would also pass if BOTH
# lost them) -- hence explicit presence pins, in both normalised files.
for f in "$work/native.norm" "$work/template.norm"; do
    expect_count "$f" 1 "include vault-upstream-rate.conf;"         "TH-1a: upstream rate include (http level)"
    expect_count "$f" 1 "proxy_buffering on;"                       "TH-1a: buffering on, required by proxy_limit_rate and proxy_store"
    expect_count "$f" 1 "proxy_ignore_headers X-Accel-Buffering;"   "TH-1a: the upstream may not switch buffering off"
    expect_count "$f" 1 'proxy_limit_rate $vault_upstream_rate;'    "TH-1a: the cap itself, in @miss"
    if grep -q '^proxy_buffering off' "$f"; then
        echo "check-config-drift: FAIL: 'proxy_buffering off' in $f -- proxy_limit_rate (the upstream cap) only acts on buffered responses" >&2
        fail=1
    fi
done

# The three cap lines must sit INSIDE location @miss: a named location
# inherits nothing from location /depot/, so moving them one block up would
# keep every count above at 1 and still drop the cap. miss_block prints the
# normalised @miss block, from its opening line to its matching brace.
miss_block() {
    awk '/^location @miss [{]$/ { f = 1 } f { print; d += gsub(/[{]/, "&") - gsub(/[}]/, "&"); if (d <= 0) exit }' "$1"
}
# top_block <name> <normalised file>: the normalised top-level `<name> {`
# block (http or stream), from its opening line to its matching brace.
top_block() {
    awk -v open="$1 {" '$0 == open { f = 1 } f { print; d += gsub(/[{]/, "&") - gsub(/[}]/, "&"); if (d <= 0) exit }' "$2"
}
for f in "$work/native.norm" "$work/template.norm"; do
    miss_block "$f" > "$work/miss.block"
    if [ ! -s "$work/miss.block" ]; then
        echo "check-config-drift: FAIL: no 'location @miss {' block in $f" >&2
        fail=1
        continue
    fi
    for want in "proxy_buffering on;" "proxy_ignore_headers X-Accel-Buffering;" 'proxy_limit_rate $vault_upstream_rate;'; do
        n=$(grep -F -c -x -- "$want" "$work/miss.block" || true)
        if [ "$n" != "1" ]; then
            echo "check-config-drift: FAIL: '$want' must be inside location @miss in $f (found $n there) -- @miss does not inherit it from /depot/" >&2
            fail=1
        fi
    done
done

RATE_HOOK="$core_dir/docker/27-vault-upstream-rate.sh"
NATIVE_RATE="$core_dir/nginx/vault-upstream-rate.conf"

# The share map has one bucket per possible connection, so $connections_writing
# never falls through to the (overshooting) default. That only holds while
# BUCKETS >= worker_processes x worker_connections of the template; a later
# bump of either must also raise BUCKETS in the hook.
wp=$(sed -n 's/^worker_processes \([0-9][0-9]*\);$/\1/p' "$work/template.norm")
wc_=$(sed -n 's/^worker_connections \([0-9][0-9]*\);$/\1/p' "$work/template.norm")
bk=$(sed -n 's/^BUCKETS=\([0-9][0-9]*\)$/\1/p' "$RATE_HOOK" 2>/dev/null || true)
case "$wp:$wc_:$bk" in
    *[!0-9:]*|:*|*::*|*:)
        echo "check-config-drift: FAIL: need numeric worker_processes, worker_connections (template) and BUCKETS= (27-vault-upstream-rate.sh); got '$wp', '$wc_', '$bk'" >&2
        fail=1 ;;
    *)
        if [ "$bk" -lt $((wp * wc_)) ]; then
            echo "check-config-drift: FAIL: BUCKETS=$bk in 27-vault-upstream-rate.sh is below worker_processes x worker_connections = $wp x $wc_ = $((wp * wc_)); counts above $bk would hit the default and overshoot the cap" >&2
            fail=1
        fi ;;
esac
if [ ! -f "$RATE_HOOK" ] || [ ! -f "$NATIVE_RATE" ]; then
    echo "check-config-drift: FAIL: missing $RATE_HOOK or $NATIVE_RATE (WP TH-1a)" >&2
    fail=1
elif ! VAULT_UPSTREAM_RATE= VAULT_UPSTREAM_RATE_WINDOW= sh "$RATE_HOOK" "$work/rate-off.conf" > "$work/rate-off.log" 2>&1; then
    echo "check-config-drift: FAIL: $RATE_HOOK could not render the cap-off include:" >&2
    cat "$work/rate-off.log" >&2
    fail=1
else
    normalise "$NATIVE_RATE"        > "$work/rate-native.norm"
    normalise "$work/rate-off.conf" > "$work/rate-off.norm"
    if ! diff -u "$work/rate-native.norm" "$work/rate-off.norm" > "$work/rate.diff" 2>&1; then
        echo "check-config-drift: FAIL: core/nginx/vault-upstream-rate.conf is not the cap-off render of 27-vault-upstream-rate.sh (left = native, right = render):" >&2
        cat "$work/rate.diff" >&2
        fail=1
    fi
fi

# --- 2c. WP CORE-FIX-2 / CORE-FEAT-1b2: the upstream retry policy ---------
# Identical in both files, so step 3's diff would also pass if BOTH drifted
# -- hence explicit pins on the exact lines. At most one retry (tries 2),
# and none when the name resolves to a single address: nginx zeroes
# `tries` for a single-peer group (ngx_http_upstream_round_robin.c), so
# tries 2 only bites with two or more A records.
# `error` IS in the list (ADR-0017 decision 6A, WP CORE-FEAT-1b2): with the
# keepalive pool (2d) a pooled connection the edge closed while idle fails
# as an `error` on its next use, and nginx retries a failure on a cached
# connection only if `error` is listed (CHANGES 1.9.13); that retry does
# not consume a try (ngx_http_upstream_next: tries++ for the cached case,
# 0 -> 1 even for a single peer), so the stale pooled connection keeps its
# one free retry while a real connect failure behind a full carrier-grade
# NAT ("113: Host is unreachable") still gets at most one retry, as
# CORE-FIX-2 (stage 1) set it. Stage 1 had removed `error` while every
# attempt was a NEW connection; the exact-line pin keeps the list from
# growing or shrinking either way.
for f in "$work/native.norm" "$work/template.norm"; do
    miss_block "$f" > "$work/miss.block"
    for want in "proxy_connect_timeout 3s;" \
                "proxy_next_upstream error timeout http_502 http_503 http_504;" \
                "proxy_next_upstream_tries 2;" \
                "proxy_next_upstream_timeout 6s;"; do
        n=$(grep -F -c -x -- "$want" "$work/miss.block" || true)
        if [ "$n" != "1" ]; then
            echo "check-config-drift: FAIL: '$want' must appear exactly once inside location @miss in $f (found $n) -- CORE-FIX-2 / CORE-FEAT-1b2 retry policy (ADR-0017 decision 6A)" >&2
            fail=1
        fi
    done
    # Counted inside http {} only: the stream {} block (CORE-FIX-3) has its
    # own `proxy_next_upstream off;`, pinned in step 2e.
    top_block http "$f" > "$work/http.block"
    n=$(grep -E -c '^proxy_next_upstream(_tries)? ' "$work/http.block" || true)
    if [ "$n" != "2" ]; then
        echo "check-config-drift: FAIL: expected exactly one proxy_next_upstream and one proxy_next_upstream_tries line in the http block of $f, found $n lines -- a second one elsewhere would override or add retries (CORE-FIX-2)" >&2
        fail=1
    fi
done

# --- 2d. WP CORE-FEAT-1b (ADR-0017): the upstream keepalive pool include ---
# Same shape as 2b. The include line is identical in both files (so step 3
# alone would also pass if BOTH lost it), hence explicit pins; the static
# native file is the hook's EMPTY render, compared with cmp because the empty
# render is a header comment only and normalise() would reduce both sides to
# nothing. The groups must not carry their own `resolver`: they inherit the
# http-level one (delta 4) so ${VAULT_RESOLVER} stays the only DNS address.
POOL_HOOK="$core_dir/docker/28-vault-upstream-pool.sh"
NATIVE_POOL="$core_dir/nginx/vault-upstream-pool.conf"

for f in "$work/native.norm" "$work/template.norm"; do
    expect_count "$f" 1 "include vault-upstream-pool.conf;" "CORE-FEAT-1b: upstream keepalive pool include (http level)"
    # Directly after the rate include: both sit at http level, after the
    # resolver the groups inherit. awk prints the line following the rate
    # include; it must be the pool include.
    after=$(awk 'f { print; exit } /^include vault-upstream-rate\.conf;$/ { f = 1 }' "$f")
    if [ "$after" != "include vault-upstream-pool.conf;" ]; then
        echo "check-config-drift: FAIL: 'include vault-upstream-pool.conf;' must directly follow 'include vault-upstream-rate.conf;' in $f (found '$after')" >&2
        fail=1
    fi
done

if [ ! -f "$POOL_HOOK" ] || [ ! -f "$NATIVE_POOL" ]; then
    echo "check-config-drift: FAIL: missing $POOL_HOOK or $NATIVE_POOL (WP CORE-FEAT-1b)" >&2
    fail=1
else
    # ADR-0017 decision 3A: keepalive 8 per group, at most 32 idle in total.
    ka=$(sed -n 's/^KEEPALIVE_PER_GROUP=\([0-9][0-9]*\)$/\1/p' "$POOL_HOOK")
    ceil=$(sed -n 's/^MAX_IDLE_TOTAL=\([0-9][0-9]*\)$/\1/p' "$POOL_HOOK")
    if [ "$ka" != "8" ] || [ "$ceil" != "32" ]; then
        echo "check-config-drift: FAIL: 28-vault-upstream-pool.sh must define KEEPALIVE_PER_GROUP=8 and MAX_IDLE_TOTAL=32 (ADR-0017 decision 3A); got '$ka' and '$ceil'" >&2
        fail=1
    fi
    # The hook validates against the allowlist families and the marker of
    # `map $host $vault_upstream_host`, but reads them from its own constants.
    # Pin BOTH sides: the three map lines (normalised, in both files) and the
    # hook's constants, so a change to either without the other trips here.
    for f in "$work/native.norm" "$work/template.norm"; do
        expect_count "$f" 1 '"lancache.steamcontent.com" dist-fra1.discovery.steamserver.net;' \
            "CORE-FEAT-1b: the marker line of the Host allowlist map (the hook refuses the marker and names its target)"
        expect_count "$f" 1 '"~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamcontent\.com$" $host;' \
            "CORE-FEAT-1b: allowlist family 1 (the hook requires *.steamcontent.com)"
        expect_count "$f" 1 '"~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamserver\.net$" $host;' \
            "CORE-FEAT-1b: allowlist family 2 (the hook requires *.steamserver.net)"
    done
    for want in "FAMILY_1=steamcontent.com" "FAMILY_2=steamserver.net" "MARKER=lancache.steamcontent.com"; do
        n=$(grep -F -c -x -- "$want" "$POOL_HOOK" || true)
        if [ "$n" != "1" ]; then
            echo "check-config-drift: FAIL: expected exactly 1 line '$want' in 28-vault-upstream-pool.sh (found $n) -- the hook's allowlist constants must match the \$vault_upstream_host map" >&2
            fail=1
        fi
    done
    if ! VAULT_UPSTREAM_POOL_HOSTS='' sh "$POOL_HOOK" "$work/pool-empty.conf" > "$work/pool-empty.log" 2>&1; then
        echo "check-config-drift: FAIL: $POOL_HOOK could not render the empty include:" >&2
        cat "$work/pool-empty.log" >&2
        fail=1
    elif ! cmp -s "$NATIVE_POOL" "$work/pool-empty.conf"; then
        echo "check-config-drift: FAIL: core/nginx/vault-upstream-pool.conf is not byte-identical to the empty render of 28-vault-upstream-pool.sh (left = native, right = render):" >&2
        diff -u "$NATIVE_POOL" "$work/pool-empty.conf" >&2 || true
        fail=1
    fi
    if ! VAULT_UPSTREAM_POOL_HOSTS='cache1-fra2.steamcontent.com dist-fra1.discovery.steamserver.net' \
            sh "$POOL_HOOK" "$work/pool-two.conf" > "$work/pool-two.log" 2>&1; then
        echo "check-config-drift: FAIL: $POOL_HOOK could not render a two-edge list:" >&2
        cat "$work/pool-two.log" >&2
        fail=1
    else
        n=$(grep -c '^upstream ' "$work/pool-two.conf" || true)
        if [ "$n" != "2" ]; then
            echo "check-config-drift: FAIL: a two-edge list rendered $n upstream blocks, expected 2" >&2
            fail=1
        fi
    fi
    for f in "$NATIVE_POOL" "$work/pool-two.conf"; do
        # Anchored to the DIRECTIVE (LEARNINGS: a guard grepping a bare name
        # also matches comments), so a future header comment cannot trip it;
        # the hook's own self-check stays stricter (the word anywhere).
        if [ -f "$f" ] && grep -q '^[[:space:]]*resolver[[:space:]]' "$f"; then
            echo "check-config-drift: FAIL: a 'resolver' directive in $f -- pool groups must inherit the http-level resolver (ADR-0017 (c), delta 4)" >&2
            fail=1
        fi
    done
fi

# --- 2e. WP CORE-FIX-3: the HTTPS passthrough (stream {}, port 443) -------
# Identical in both files apart from deltas 4 and 7, so step 3's diff alone
# would also pass if BOTH lost the allowlist -- hence explicit pins. The
# allowlist map is the only thing between this listener and an open TCP
# relay (ADR-0020).
TLS_MAP_RE='"~*^(?=.{1,253}\z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+steamcontent\.com\z" $ssl_preread_server_name:443;'
for f in "$work/native.norm" "$work/template.norm"; do
    n=$(grep -c -x -F 'stream {' "$f" || true)
    if [ "$n" != "1" ]; then
        echo "check-config-drift: FAIL: expected exactly one top-level 'stream {' block in $f, found $n (CORE-FIX-3)" >&2
        fail=1
        continue
    fi
    top_block stream "$f" > "$work/stream.block"
    for want in "resolver_timeout 5s;" \
                'map $ssl_preread_server_name $vault_tls_upstream {' \
                'default "";' \
                "$TLS_MAP_RE" \
                'limit_conn_zone $binary_remote_addr zone=vault_tls_client:1m;' \
                'limit_conn_zone $server_port zone=vault_tls_total:1m;' \
                "server {" \
                "listen 443;" \
                "ssl_preread on;" \
                "preread_timeout 5s;" \
                "limit_conn vault_tls_client 64;" \
                "limit_conn vault_tls_total 256;" \
                "proxy_connect_timeout 3s;" \
                "proxy_next_upstream off;" \
                "proxy_timeout 5m;" \
                'proxy_pass $vault_tls_upstream;'; do
        n=$(grep -F -c -x -- "$want" "$work/stream.block" || true)
        if [ "$n" != "1" ]; then
            echo "check-config-drift: FAIL: '$want' must appear exactly once inside the stream block of $f (found $n) -- CORE-FIX-3 HTTPS passthrough" >&2
            fail=1
        fi
    done
    # The map holds exactly its default and the one regex: a second entry
    # would widen the allowlist past *.steamcontent.com.
    awk '/^map [$]ssl_preread_server_name [$]vault_tls_upstream [{]$/ { f = 1 } f { print; if ($0 == "}") exit }' \
        "$work/stream.block" > "$work/tls-map.block"
    n=$(wc -l < "$work/tls-map.block" | tr -d ' ')
    if [ "$n" != "4" ]; then
        echo "check-config-drift: FAIL: the SNI allowlist map in $f has $((n - 2)) entries, expected exactly 2 (default \"\" and the *.steamcontent.com regex) -- CORE-FIX-3:" >&2
        sed 's/^/    /' "$work/tls-map.block" >&2
        fail=1
    fi
    # Exactly one listener in the stream block, and port 80 the only one in
    # the http block: 443 is never terminated by the HTTP server.
    n=$(grep -c '^listen ' "$work/stream.block" || true)
    [ "$n" = "1" ] || { echo "check-config-drift: FAIL: $n listen lines in the stream block of $f, expected 1 (CORE-FIX-3)" >&2; fail=1; }
    top_block http "$f" > "$work/http.block"
    n=$(grep '^listen ' "$work/http.block" | grep -c -v -x -F 'listen 80;' || true)
    [ "$n" = "0" ] || { echo "check-config-drift: FAIL: the http block of $f listens on something other than port 80 (CORE-FIX-3: 443 belongs to the passthrough only)" >&2; fail=1; }
    # No TLS termination, anywhere.
    if grep -E -q '^(ssl_certificate|ssl_certificate_key|proxy_ssl[a-z_]*) |^listen .*[[:space:]]ssl([[:space:];]|$)' "$f"; then
        echo "check-config-drift: FAIL: $f terminates TLS (ssl_certificate / proxy_ssl / 'listen ... ssl') -- the passthrough must never decrypt (CORE-FIX-3, ADR-0020):" >&2
        grep -E '^(ssl_certificate|ssl_certificate_key|proxy_ssl[a-z_]*) |^listen .*[[:space:]]ssl([[:space:];]|$)' "$f" | sed 's/^/    /' >&2
        fail=1
    fi
done

# The stream block as an EXACT directive list (review S1): the per-line pins
# above only prove the required lines exist, so an added directive -- a
# `set $vault_tls_upstream 10.0.0.1:22;`, a server-level `resolver`, a
# `proxy_protocol on;` -- would pass them in both files at once. This list
# is the whole normalised block; @RESOLVER@ and @ACCESS_LOG@ are the two
# per-file deltas (4 and 7). 40-vault-preflight.sh carries the same list
# for the rendered config at boot; the last check below keeps the two
# copies identical.
expected_stream() {
    cat <<'VAULT_TLS_STREAM_EOF' | sed -e "s|@RESOLVER@|$1|" -e "s|@ACCESS_LOG@|$2|"
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
limit_conn vault_tls_client 64;
limit_conn vault_tls_total 256;
proxy_connect_timeout 3s;
proxy_next_upstream off;
proxy_timeout 5m;
proxy_pass $vault_tls_upstream;
}
}
VAULT_TLS_STREAM_EOF
}
expected_stream '1.1.1.1' 'logs/tls.log' > "$work/stream.expected.native"
expected_stream '${VAULT_RESOLVER}' '/dev/stdout' > "$work/stream.expected.template"
for pair in "native:$work/native.norm" "template:$work/template.norm"; do
    which=${pair%%:*}; f=${pair#*:}
    top_block stream "$f" > "$work/stream.block"
    if ! diff -u "$work/stream.expected.$which" "$work/stream.block" > "$work/stream.diff" 2>&1; then
        echo "check-config-drift: FAIL: the stream block of $f is not the reviewed directive list (left = expected, right = file) -- CORE-FIX-3:" >&2
        cat "$work/stream.diff" >&2
        fail=1
    fi
done
PREFLIGHT="$core_dir/docker/40-vault-preflight.sh"
sed -n "/<<'VAULT_TLS_STREAM_EOF'\$/,/^VAULT_TLS_STREAM_EOF\$/p" "$PREFLIGHT" | sed '1d;$d' > "$work/stream.preflight"
expected_stream '@RESOLVER@' '@ACCESS_LOG@' > "$work/stream.expected.raw"
if ! diff -u "$work/stream.expected.raw" "$work/stream.preflight" > "$work/stream-pf.diff" 2>&1; then
    echo "check-config-drift: FAIL: 40-vault-preflight.sh's expected stream block differs from this script's (left = here, right = preflight):" >&2
    cat "$work/stream-pf.diff" >&2
    fail=1
fi

# The container's on/off switch: the raw template's marker comments must
# enclose exactly the stream block (comments are dropped by normalise(), so
# this reads the raw file).
for m in "# VAULT_TLS_PASSTHROUGH_BEGIN" "# VAULT_TLS_PASSTHROUGH_END"; do
    n=$(tr -d '\r' < "$TEMPLATE" | grep -c -x -F -- "$m" || true)
    if [ "$n" != "1" ]; then
        echo "check-config-drift: FAIL: expected exactly 1 line '$m' in $TEMPLATE, found $n (26-vault-tls-passthrough.sh deletes the stream block by these markers)" >&2
        fail=1
    fi
done
tr -d '\r' < "$TEMPLATE" | sed -n '/^# VAULT_TLS_PASSTHROUGH_BEGIN$/,/^# VAULT_TLS_PASSTHROUGH_END$/p' > "$work/tls-marked.raw"
normalise "$work/tls-marked.raw" > "$work/tls-marked.norm"
top_block stream "$work/template.norm" > "$work/stream.block"
if ! diff -u "$work/stream.block" "$work/tls-marked.norm" > "$work/tls-marked.diff" 2>&1; then
    echo "check-config-drift: FAIL: the VAULT_TLS_PASSTHROUGH_BEGIN/END markers in $TEMPLATE do not enclose exactly the stream block (left = stream block, right = marked range):" >&2
    cat "$work/tls-marked.diff" >&2
    fail=1
fi
tr -d '\r' < "$TEMPLATE" | sed '/^# VAULT_TLS_PASSTHROUGH_BEGIN$/,/^# VAULT_TLS_PASSTHROUGH_END$/d' > "$work/tls-off.raw"
normalise "$work/tls-off.raw" > "$work/tls-off.norm"
if grep -E -q '^stream [{]$|^listen 443|^ssl_preread|vault_tls' "$work/tls-off.norm"; then
    echo "check-config-drift: FAIL: deleting the marked range from $TEMPLATE leaves a stream/443/vault_tls trace -- VAULT_TLS_PASSTHROUGH=0 would not switch the passthrough off" >&2
    fail=1
fi
top_block http "$work/tls-off.norm" > "$work/http-off.block"
top_block http "$work/template.norm" > "$work/http.block"
if ! cmp -s "$work/http.block" "$work/http-off.block"; then
    echo "check-config-drift: FAIL: deleting the marked range from $TEMPLATE changes the http block -- VAULT_TLS_PASSTHROUGH=0 must not touch the HTTP cache" >&2
    fail=1
fi

# --- 2f. the worker connection budget (CORE-FIX-3 + CORE-FEAT-1) -----------
# Both features size their caps against ONE nginx worker with 1024
# connections, and nothing else pinned those two numbers:
#   - the stream block's `limit_conn vault_tls_total 256` keeps half of
#     worker_connections for the HTTP cache (each passthrough session holds
#     two connections: 2 x 256 = 512 of 1024; ADR-0020 "Loop bound");
#   - the pool's ceiling MAX_IDLE_TOTAL=32 counts idle upstream connections
#     of that one worker (`keepalive 8` is per group PER WORKER; ADR-0017
#     decision 3A counts 32 idle + 8 in flight against the CGNAT's measured
#     safe 50) -- a second worker would double it silently;
#   - so the HTTP half (512) also holds the pool's idle connections,
#     leaving at least 480 for live HTTP requests.
# Pinned as exact lines in both files, plus the arithmetic, so a change to
# any of these numbers fails here until the docs' accounting is redone.
TLS_TOTAL=256
for f in "$work/native.norm" "$work/template.norm"; do
    expect_count "$f" 1 "worker_processes 1;"       "budget: one worker (the pool ceiling and the stream cap assume it)"
    expect_count "$f" 1 "worker_connections 1024;"  "budget: 1024 connections (2 x stream cap = half of it)"
    expect_count "$f" 1 "limit_conn vault_tls_total $TLS_TOTAL;" "budget: the stream total cap the arithmetic below uses"
done
pool_ceil=$(sed -n 's/^MAX_IDLE_TOTAL=\([0-9][0-9]*\)$/\1/p' "$POOL_HOOK" 2>/dev/null || true)
case "$wc_:$pool_ceil" in
    *[!0-9:]*|:*|*:)
        echo "check-config-drift: FAIL: need numeric worker_connections (template) and MAX_IDLE_TOTAL= (28-vault-upstream-pool.sh) for the budget; got '$wc_', '$pool_ceil'" >&2
        fail=1 ;;
    *)
        if [ $((2 * TLS_TOTAL)) -gt $((wc_ / 2)) ]; then
            echo "check-config-drift: FAIL: budget: the stream cap ($TLS_TOTAL sessions = $((2 * TLS_TOTAL)) connections) exceeds half of worker_connections ($wc_) -- the HTTP cache no longer keeps half (ADR-0020)" >&2
            fail=1
        fi
        if [ $((wc_ / 2 - pool_ceil)) -lt 256 ]; then
            echo "check-config-drift: FAIL: budget: the HTTP half of worker_connections ($((wc_ / 2))) minus the pool's idle ceiling ($pool_ceil) leaves fewer than 256 connections for live HTTP requests" >&2
            fail=1
        fi ;;
esac

[ "$fail" = "0" ] || exit 1

sed \
    -e '/^user nginx;$/d' \
    -e 's|^pid /var/run/nginx\.pid;$|pid logs/nginx.pid;|' \
    -e 's|^error_log /dev/stderr warn;$|error_log logs/error.log warn;|' \
    -e 's|^resolver \${VAULT_RESOLVER} ipv6=off valid=30s;$|resolver 1.1.1.1 ipv6=off valid=30s;|' \
    -e 's|^access_log /dev/stdout vault;$|access_log logs/access.log vault;|' \
    -e 's|^access_log \${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE$|access_log logs/event.log vault_event buffer=64k flush=5s;|' \
    -e 's|^access_log /dev/stdout vault_tls;$|access_log logs/tls.log vault_tls;|' \
    "$work/template.norm" > "$work/template.unapplied"

# --- 3. diff -----------------------------------------------------------------
if diff -u "$work/native.norm" "$work/template.unapplied" > "$work/diff" 2>&1; then
    lines=$(wc -l < "$work/native.norm" | tr -d ' ')
    echo "check-config-drift: OK -- $lines normalised directive lines identical"
    echo "  native:   $NATIVE"
    echo "  template: $TEMPLATE"
    exit 0
fi

echo "check-config-drift: FAIL -- the container template diverges from core/nginx/nginx.conf" >&2
echo "  (left = core/nginx/nginx.conf, right = core/docker/nginx.conf.template with the" >&2
echo "   enumerated container deltas un-applied)" >&2
echo >&2
cat "$work/diff" >&2
exit 1
