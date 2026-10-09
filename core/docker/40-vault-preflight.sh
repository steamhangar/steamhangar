#!/bin/sh
# SteamHangar vault-core container preflight (Phase 1, WP 1.9).
#
# Runs from the official nginx image's /docker-entrypoint.d/ hook directory,
# AFTER 20-envsubst-on-templates.sh has rendered
# /etc/nginx/templates/nginx.conf.template -> /etc/nginx/nginx.conf, and BEFORE
# nginx itself is exec'd. docker-entrypoint.sh runs with `set -e`, so a non-zero
# exit here aborts container start -- every check below is therefore a hard,
# fail-fast gate, not a warning.
#
# It exists because three of vault-core's correctness/security properties are
# established by the DEPLOYMENT (volume layout, env values), not by the config
# file, and would otherwise fail silently or late:
#
#   1. Rendering actually happened. A too-narrow NGINX_ENVSUBST_FILTER leaves
#      "${VAULT_RESOLVER}" literally in the config, and nginx would then fail
#      with an obscure parse error instead of the explanation below. (Removing
#      the filter entirely is the opposite failure and is NOT caught here: it
#      renders fine today, and only misfires if some future lowercase env var
#      collides with an nginx runtime variable name -- see core/Dockerfile.)
#   2. VAULT_RESOLVER is substituted VERBATIM into an nginx config file, so an
#      operator value containing ';' or '{' is config injection. Validated
#      against a strict IP-address character allowlist here.
#   3. cache/ and tmp/ must share one filesystem: proxy_store completes a
#      download by rename()-ing the temp file into cache/depot/... Split across
#      two mounts, rename() fails and nginx silently degrades to a full copy
#      (slower, briefly doubles disk usage). st_dev is compared here so a split
#      mount is a loud boot failure instead of a quiet performance/space bug.
#      (core/README.md "Same-filesystem requirement", binding for WP 1.9.)
#
# Plus a plain writability check as the nginx worker user, because the single
# most common deployment mistake with a bind-mounted cache is host-side
# ownership that the worker cannot write to -- which otherwise shows up much
# later as "every request is a MISS and nothing is ever cached".

set -eu

ME="40-vault-preflight.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

CONF="/etc/nginx/nginx.conf"
PREFIX="/vault"
CACHE_DIR="$PREFIX/cache"
TMP_DIR="$PREFIX/tmp"
DEPOT_DIR="$CACHE_DIR/depot"
WORKER_USER="nginx"

# --- 1. the template was rendered, and rendered completely ------------------
[ -f "$CONF" ] || die "$CONF does not exist -- the nginx.conf template was never rendered.
  Expected /etc/nginx/templates/nginx.conf.template + NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx."

# PRE-FREEZE REVIEW S5: existence is not enough. The stock image ships its
# own /etc/nginx/nginx.conf at exactly this path, and the stock envsubst hook
# SOFT-FAILS (returns 0 without rendering when the template dir is missing
# or the output dir unwritable -- docs/LEARNINGS.md, WP 5.1). Every check
# below would then pass against a config that caches nothing. Only the
# SteamHangar template declares the vault_event log format, so its absence
# means the file is the stock one. CI has guarded this since WP 5.1; the
# running container now does too.
grep -q 'log_format vault_event' "$CONF" || die "$CONF is NOT the SteamHangar config (no 'log_format vault_event' in it) --
  it is the base image's stock nginx.conf, i.e. the template was never rendered
  over it. Check NGINX_ENVSUBST_TEMPLATE_DIR/NGINX_ENVSUBST_OUTPUT_DIR and that
  /etc/nginx is writable at start. Refusing to start a non-caching nginx."

# Comment lines are excluded on purpose: this file's own header explains the
# ${VAULT_...} mechanism and would otherwise match its own guard. Only DIRECTIVE
# lines matter -- a placeholder surviving in a comment is inert.
CONF_DIRECTIVES=$(grep -v '^[[:space:]]*#' "$CONF" || true)

if printf '%s\n' "$CONF_DIRECTIVES" | grep -q '\${VAULT_'; then
    leftover=$(printf '%s\n' "$CONF_DIRECTIVES" | grep -o '\${VAULT_[A-Za-z0-9_]*}' | sort -u | tr '\n' ' ')
    die "unsubstituted placeholder(s) left in $CONF: $leftover
  envsubst did not replace them. NGINX_ENVSUBST_FILTER (currently '${NGINX_ENVSUBST_FILTER:-<unset>}')
  must match those variable names, and they must be present in the environment."
fi

# --- 1b. the upstream rate include matches VAULT_UPSTREAM_RATE (WP TH-1a) --
# 27-vault-upstream-rate.sh validates the env and self-checks its render;
# this is a second look, not a full re-derivation: the include and the
# proxy_limit_rate line are wired in, and with VAULT_UPSTREAM_RATE set the
# file holds a connection-count map with a positive default. It exists
# because the failure it guards is
# silent: nginx reads an empty or unparseable proxy_limit_rate as 0 =
# UNLIMITED (TH-0b, measured). A cap that is configured but not rendered --
# hook missing, renamed, or out of order -- must stop the boot, not ship an
# uncapped vault.
RATE_CONF="/etc/nginx/vault-upstream-rate.conf"
[ -f "$RATE_CONF" ] || die "$RATE_CONF is missing. nginx.conf includes it for the upstream rate cap;
  /docker-entrypoint.d/27-vault-upstream-rate.sh renders it at start. Refusing to start."
printf '%s\n' "$CONF_DIRECTIVES" | grep -q '^[[:space:]]*include[[:space:]][[:space:]]*vault-upstream-rate\.conf;' \
    || die "$CONF has no 'include vault-upstream-rate.conf;' -- the upstream rate cap is not wired in."
printf '%s\n' "$CONF_DIRECTIVES" | grep -q '^[[:space:]]*proxy_limit_rate[[:space:]][[:space:]]*\$vault_upstream_rate;' \
    || die "$CONF has no 'proxy_limit_rate \$vault_upstream_rate;' -- the upstream rate cap is not wired in."
if [ -n "${VAULT_UPSTREAM_RATE:-}" ]; then
    grep -q '^map \$connections_[a-z]* \$vault_upstream_rate[_a-z]* {$' "$RATE_CONF" \
        && grep -qE '^    default [1-9][0-9]*;$' "$RATE_CONF" \
        || die "VAULT_UPSTREAM_RATE='$VAULT_UPSTREAM_RATE' is set but $RATE_CONF holds no capped
  connection-share map with a positive default -- the cap would not apply.
  Refusing to start an uncapped vault-core."
    log "upstream rate cap rendered ($RATE_CONF, VAULT_UPSTREAM_RATE=$VAULT_UPSTREAM_RATE)"
else
    log "upstream rate cap off ($RATE_CONF renders 0 = unlimited)"
fi

# --- 1b2. the pooled edge and the global cap match the env (CORE-FIX-4a) --
# 28-vault-upstream-pool.sh validates VAULT_UPSTREAM_EDGE / _MAX_CONNS and
# self-checks its renders; this is a second look at the RESULT (ADR-0021).
# The failure it guards is quiet in both directions: an include missing or
# out of order leaves @miss with no cap (the CGNAT port quota is then the
# only limit), or edge mode configured but not rendered would silently dial
# per-name again.
POOL_CONF="/etc/nginx/vault-upstream-pool.conf"
CAP_CONF="/etc/nginx/vault-upstream-cap.conf"
[ -f "$POOL_CONF" ] || die "$POOL_CONF is missing; /docker-entrypoint.d/28-vault-upstream-pool.sh renders it at start. Refusing to start."
[ -f "$CAP_CONF" ] || die "$CAP_CONF is missing; /docker-entrypoint.d/28-vault-upstream-pool.sh renders it at start. Refusing to start."
printf '%s\n' "$CONF_DIRECTIVES" | grep -q '^[[:space:]]*include[[:space:]][[:space:]]*vault-upstream-cap\.conf;' \
    || die "$CONF has no 'include vault-upstream-cap.conf;' -- the global upstream connection cap is not wired in."
printf '%s\n' "$CONF_DIRECTIVES" | grep -q '^[[:space:]]*limit_conn_zone[[:space:]][[:space:]]*\$server_port[[:space:]][[:space:]]*zone=vault_upstream_total:' \
    || die "$CONF defines no limit_conn_zone vault_upstream_total -- the global upstream connection cap is not wired in."
grep -qE '^limit_conn vault_upstream_total ([1-9]|[1-5][0-9]|6[0-4]);$' "$CAP_CONF" \
    || die "$CAP_CONF holds no 'limit_conn vault_upstream_total <1..64>;' -- the cap would not apply. Refusing to start."
if [ -n "${VAULT_UPSTREAM_EDGE:-}" ]; then
    grep -qx "upstream $VAULT_UPSTREAM_EDGE {" "$POOL_CONF" \
        && grep -qx "    default $VAULT_UPSTREAM_EDGE;" "$POOL_CONF" \
        || die "VAULT_UPSTREAM_EDGE='$VAULT_UPSTREAM_EDGE' is set but $POOL_CONF holds no such pooled group as map default -- edge mode would not apply."
    log "upstream edge mode: all MISS -> $VAULT_UPSTREAM_EDGE, $(grep '^limit_conn ' "$CAP_CONF")"
else
    log "upstream edge mode off (per-name legacy), $(grep '^limit_conn ' "$CAP_CONF")"
fi

# --- 1c. the HTTPS passthrough matches VAULT_TLS_PASSTHROUGH (CORE-FIX-3) --
# 26-vault-tls-passthrough.sh keeps or deletes the stream {} block; this is
# a second, independent look at the RESULT, in both directions, because
# both failures are quiet: ON without the block means HTTPS to a rewritten
# CDN name fails exactly as before the fix; OFF with the block left in
# means port 443 is served although the operator switched it off. ON also
# re-checks the allowlist line verbatim, so a widened or deleted SNI
# allowlist (an open TCP relay on the LAN) cannot boot.
TLS_ALLOW_LINE='"~*^(?=.{1,253}\z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+steamcontent\.com\z" $ssl_preread_server_name:443;'
tls_count() { printf '%s\n' "$CONF_DIRECTIVES" | grep -c -E "$1" || true; }
TLS_MODE=on
case "${VAULT_TLS_PASSTHROUGH:-1}" in
    0|false|off|no)
        TLS_MODE=off
        n=$(tls_count '^[[:space:]]*(stream[[:space:]]*[{]|ssl_preread[[:space:]]|listen[[:space:]]+([^;]*:)?443[[:space:];])')
        [ "$n" = "0" ] || die "VAULT_TLS_PASSTHROUGH=${VAULT_TLS_PASSTHROUGH} (off) but $CONF still has $n
  stream/ssl_preread/listen 443 directive(s). Refusing to serve port 443."
        log "HTTPS passthrough off (no stream block in $CONF)" ;;
    *)
        for re in '^[[:space:]]*stream[[:space:]]*[{]' \
                  '^[[:space:]]*listen[[:space:]]+443;' \
                  '^[[:space:]]*ssl_preread[[:space:]]+on;' \
                  '^[[:space:]]*proxy_next_upstream[[:space:]]+off;' \
                  '^[[:space:]]*proxy_pass[[:space:]]+\$vault_tls_upstream;'; do
            n=$(tls_count "$re")
            [ "$n" = "1" ] || die "VAULT_TLS_PASSTHROUGH is on but $n lines match '$re' in $CONF
  (expected exactly 1) -- the HTTPS passthrough is missing or altered."
        done
        # The map's entries, whitespace-normalised, must be exactly the
        # reviewed two: an empty default and the one full-match pattern.
        tls_map=$(printf '%s\n' "$CONF_DIRECTIVES" \
            | awk '/^[[:space:]]*map[[:space:]]+[$]ssl_preread_server_name[[:space:]]+[$]vault_tls_upstream[[:space:]]*[{]/ { f = 1; next } f && /^[[:space:]]*[}]/ { exit } f && NF { print }' \
            | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]][[:space:]]*/ /g')
        tls_map_want=$(printf '%s\n%s' 'default "";' "$TLS_ALLOW_LINE")
        [ "$tls_map" = "$tls_map_want" ] || die "VAULT_TLS_PASSTHROUGH is on but the SNI allowlist map in $CONF is not the
  reviewed one. Expected exactly these two entries:
$(printf '%s\n' "$tls_map_want" | sed 's/^/      /')
  found:
$(printf '%s\n' "$tls_map" | sed 's/^/      /')
  Refusing to start a TCP relay on port 443 with an unknown allowlist."
        log "HTTPS passthrough on: port 443, SNI allowlist *.steamcontent.com, TLS passed through (not terminated)" ;;
esac
if printf '%s\n' "$CONF_DIRECTIVES" | grep -q -E '^[[:space:]]*(ssl_certificate|proxy_ssl[a-z_]*)[[:space:]]|^[[:space:]]*listen[[:space:]][^;]*[[:space:]]ssl[[:space:];]'; then
    die "$CONF terminates TLS (ssl_certificate / proxy_ssl / 'listen ... ssl'). vault-core
  never holds a certificate; the passthrough copies TLS bytes unchanged (ADR-0020)."
fi

# --- 2. VAULT_RESOLVER is a plain IP-address list ---------------------------
# Substituted verbatim into nginx.conf, so anything that could terminate a
# directive (';') or open a block ('{') would be config injection. Allowed
# characters: hex digits, '.', ':' and '[' ']' (IPv6 -- nginx's resolver
# syntax needs the brackets whenever a port follows an IPv6 address, e.g.
# '[2606:4700::1111]:53'; review N3 found the earlier allowlist claimed IPv6
# support while rejecting exactly those brackets), space (nginx accepts
# several addresses) and '-'. Hostnames are deliberately NOT accepted --
# nginx would have to resolve them with the OS resolver at config-parse
# time, which is exactly the dependency core/nginx.conf's loop-safety note
# avoids.
RESOLVER="${VAULT_RESOLVER:-}"
case "$RESOLVER" in
    "")
        die "VAULT_RESOLVER is empty. It is substituted into nginx.conf's 'resolver'
  directive, which cannot be empty. Set it to an upstream DNS server IP
  (default 1.1.1.1) in deploy/.env." ;;
    *[!]0-9a-fA-F.:[\ -]*)
        die "VAULT_RESOLVER='$RESOLVER' contains characters that are not part of an
  IP address list. This value is written verbatim into nginx.conf; refusing
  rather than risking config injection. Use e.g. '1.1.1.1', '10.0.0.53 10.0.0.54'
  or '[2606:4700::1111]:53'." ;;
esac
log "upstream resolver (ADR-0001 req 4): $RESOLVER"

# --- 2a. the rendered stream block is exactly the reviewed one (CORE-FIX-3) --
# Review S1: section 1c pins the lines the passthrough NEEDS; this pins that
# nothing else is there either (an added `set`, a server-level `resolver`,
# `proxy_protocol on;` ...). The rendered block, comments dropped and
# whitespace collapsed, must equal this list with VAULT_RESOLVER filled in.
# core/docker/check-config-drift.sh keeps this copy identical to its own.
# CORE-FIX-4d: the two limit_conn values are filled in from
# VAULT_TLS_CLIENT_MAX_CONNS / VAULT_TLS_MAX_CONNS (empty = 64 / 256), which
# 26-vault-tls-passthrough.sh validated and rendered; a rendered value that
# disagrees with the env shows up in the diff below.
if [ "$TLS_MODE" = "on" ]; then
    tls_client_cap="${VAULT_TLS_CLIENT_MAX_CONNS:-64}"
    tls_total_cap="${VAULT_TLS_MAX_CONNS:-256}"
    case "$tls_client_cap/$tls_total_cap" in
        *[!0-9/]*|0*|*/0*|*/) die "VAULT_TLS_CLIENT_MAX_CONNS='$tls_client_cap' / VAULT_TLS_MAX_CONNS='$tls_total_cap'
  are not whole numbers -- 26-vault-tls-passthrough.sh should have refused them. Refusing to start." ;;
    esac
    tls_expected=$(cat <<'VAULT_TLS_STREAM_EOF'
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
)
    resolver_norm=$(printf '%s' "$RESOLVER" | sed -e 's/[[:space:]][[:space:]]*/ /g')
    tls_expected=$(printf '%s\n' "$tls_expected" | sed -e "s|@RESOLVER@|$resolver_norm|" -e "s|@ACCESS_LOG@|/dev/stdout|" \
        -e "s|@TLS_CLIENT@|$tls_client_cap|" -e "s|@TLS_TOTAL@|$tls_total_cap|")
    tls_rendered=$(printf '%s\n' "$CONF_DIRECTIVES" \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | grep -v '^$' | sed -e 's/[[:space:]][[:space:]]*/ /g' \
        | awk '$0 == "stream {" { f = 1 } f { print; d += gsub(/[{]/, "&") - gsub(/[}]/, "&"); if (d <= 0) exit }')
    if [ "$tls_rendered" != "$tls_expected" ]; then
        printf '%s\n' "$tls_expected" > /tmp/vault-tls-expected.$$
        printf '%s\n' "$tls_rendered" > /tmp/vault-tls-rendered.$$
        tls_diff=$(diff /tmp/vault-tls-expected.$$ /tmp/vault-tls-rendered.$$ || true)
        rm -f /tmp/vault-tls-expected.$$ /tmp/vault-tls-rendered.$$
        die "the rendered stream block in $CONF is not the reviewed directive list
  (HTTPS passthrough, port 443). Differences (< expected, > rendered):
$(printf '%s\n' "$tls_diff" | sed 's/^/      /')
  Refusing to start a TCP relay that is not the reviewed one."
    fi
    log "stream block matches the reviewed directive list"
fi

# --- 2b. the resolver does not point Steam's CDN names back at a LAN host --
# PRE-FREEZE REVIEW S1 (belt; the braces are the X-SteamHangar-Hop guard in
# the config). Scenario: a LAN router transparently intercepts port 53 (DNAT
# to a Pi-hole/AdGuard that rewrites *.steamcontent.com to vault-core), or
# the operator pointed VAULT_RESOLVER at vault-dns despite the warning. Then
# every MISS resolves an edge hostname to THIS server and proxy_pass dials
# itself. Ask the first configured resolver for a real edge name the config
# would dial, and refuse to boot if the answer is a private/loopback/link-
# local address -- a real Valve edge is never one of those.
#
# Robust OFFLINE by design: an unreachable resolver, a timeout, NXDOMAIN, an
# unparseable answer or a missing nslookup all log a note and CONTINUE. The
# only outcome that stops the boot is a positive private answer. busybox
# nslookup queries the given server directly (raw DNS, not the OS resolver
# or /etc/hosts), which is the same path nginx's `resolver` takes.
#
# PROBE_NAME is a single edge. If Valve retires it (NXDOMAIN), the probe
# takes the "no A answer" branch below on every boot: the belt degrades to a
# logged no-op, it never fails the boot. The 508 braces still hold.
#
# WP CORE-FIX-3: the HTTPS passthrough (stream {} block, port 443) resolves
# the SNI name through the SAME VAULT_RESOLVER, so this one probe guards
# both paths. It matters more there: TLS carries no header the 508 guard
# could stamp, so a looping passthrough is only bounded by its connection
# caps (limit_conn in the stream block), not stopped after one hop. Besides
# private answers, an answer equal to one of this container's OWN interface
# addresses is refused too (relevant with network_mode: host, where those
# can be public).
PROBE_NAME="cache2-ams1.steamcontent.com"

# vault_is_private_ipv4 <dotted quad> -> 0 if the address is loopback,
# RFC1918, link-local, 0/8 or CGNAT (100.64/10); 1 otherwise (incl. garbage).
vault_is_private_ipv4() {
    case "$1" in
        *[!0-9.]*) return 1 ;;
    esac
    _o1=${1%%.*}; _r=${1#*.}; _o2=${_r%%.*}
    case "$_o1" in
        ""|*[!0-9]*) return 1 ;;
    esac
    case "$_o2" in
        ""|*[!0-9]*) return 1 ;;
    esac
    [ "$_o1" = "10" ] && return 0
    [ "$_o1" = "127" ] && return 0
    [ "$_o1" = "0" ] && return 0
    [ "$_o1" = "192" ] && [ "$_o2" = "168" ] && return 0
    [ "$_o1" = "169" ] && [ "$_o2" = "254" ] && return 0
    [ "$_o1" = "172" ] && [ "$_o2" -ge 16 ] && [ "$_o2" -le 31 ] && return 0
    [ "$_o1" = "100" ] && [ "$_o2" -ge 64 ] && [ "$_o2" -le 127 ] && return 0
    return 1
}

# vault_nslookup_answers: stdin = busybox nslookup output, stdout = the A
# answers (the "Address: x.x.x.x" lines AFTER the first "Name:" line; the
# "Address:" line before it is the server itself and must not count).
vault_nslookup_answers() {
    awk '/^Name:/ { seen = 1; next }
         seen && /^Address:[[:space:]]*[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+[[:space:]]*$/ { print $2 }'
}

# vault_own_ipv4s: this container's IPv4 interface addresses, one per line
# (empty if `ip` is unavailable -- the private check alone still applies).
vault_own_ipv4s() {
    ip -4 -o addr show 2>/dev/null | awk '{ sub(/\/.*/, "", $4); print $4 }'
}

probe_server=${RESOLVER%% *}
if ! command -v nslookup >/dev/null 2>&1; then
    log "note: nslookup not available, skipping the resolver loop probe"
else
    probe_out=$(timeout 5 nslookup -type=a "$PROBE_NAME" "$probe_server" 2>/dev/null) || probe_out=""
    probe_answers=$(printf '%s\n' "$probe_out" | vault_nslookup_answers)
    if [ -z "$probe_answers" ]; then
        log "note: resolver $probe_server gave no A answer for $PROBE_NAME (unreachable, timed out or NXDOMAIN) -- loop probe skipped, not a boot failure"
    else
        own_addrs=$(vault_own_ipv4s)
        for a in $probe_answers; do
            for o in $own_addrs; do
                if [ "$a" = "$o" ]; then
                    die "resolver $probe_server answers $PROBE_NAME with $a, which is one of
  this container's own addresses -- every cache MISS and every HTTPS
  passthrough connection would be sent back into vault-core. Point
  VAULT_RESOLVER at a truthful resolver (deploy/README.md)."
                fi
            done
            if vault_is_private_ipv4 "$a"; then
                die "resolver $probe_server answers $PROBE_NAME with $a, a private/loopback
  address, so it rewrites *.steamcontent.com instead of answering truthfully.
  This probe cannot tell WHO answers: it may be this host (a router DNATs port
  53 to a Pi-hole/AdGuard rewriting to vault-core, or VAULT_RESOLVER points at
  vault-dns), another LAN cache (e.g. a lancache instance), or a blocker that
  answers 0.0.0.0. In every case cache MISSes and HTTPS passthrough
  connections cannot reach Valve. Point
  VAULT_RESOLVER at a truthful resolver (deploy/README.md), or exempt the
  vault-core host from any port-53 redirect on the router."
            fi
        done
        log "resolver loop probe OK: $PROBE_NAME -> $(printf '%s' "$probe_answers" | tr '\n' ' ')(public)"
    fi
fi

# --- 3. cache/ and tmp/ must be on ONE filesystem ---------------------------
[ -d "$CACHE_DIR" ] || die "$CACHE_DIR is missing. Mount the SteamHangar cache volume at $PREFIX."
[ -d "$TMP_DIR" ]   || die "$TMP_DIR is missing. Mount the SteamHangar cache volume at $PREFIX
  (it must contain both cache/ and tmp/)."

cache_dev=$(stat -c %d "$CACHE_DIR")
tmp_dev=$(stat -c %d "$TMP_DIR")
if [ "$cache_dev" != "$tmp_dev" ]; then
    die "$CACHE_DIR (st_dev=$cache_dev) and $TMP_DIR (st_dev=$tmp_dev) are on DIFFERENT
  filesystems. proxy_store finishes every cached object by rename()-ing it from
  tmp/ into cache/depot/..., which only works within one filesystem; across two
  it falls back to a full copy (slower, briefly doubles disk usage per chunk).
  Mount ONE volume at $PREFIX instead of separate mounts for cache/ and tmp/.
  See core/README.md 'Same-filesystem requirement'."
fi
log "cache/ and tmp/ share one filesystem (st_dev=$cache_dev) -- proxy_store rename() is atomic"

# --- 4. the depot root exists ------------------------------------------------
# vault-api's deletion guard (DELETE /v1/cache/{appid}) refuses to operate on a
# cache root that has no depot/ directory, and it reads this same volume.
# 21-vault-volume-ownership.sh creates it (owned by the worker user) once
# cache/ is root-only; this hook used to `mkdir -p` + `chown` it as root
# inside a cache/ uid 101 owned (WP SEC-FIX-5). Only checked here.
if [ -L "$DEPOT_DIR" ] || [ ! -d "$DEPOT_DIR" ]; then
    die "$DEPOT_DIR is missing or not a real directory. 21-vault-volume-ownership.sh
  creates it at start; see its output above."
fi

# --- 5. the worker user can actually write ----------------------------------
# nginx's master runs as root (it must bind :80), workers as $WORKER_USER -- and
# it is the workers that proxy_store into cache/depot/... and write their temp
# files into tmp/proxy/. Testing as root would prove nothing, so probe as the
# worker user itself. Since WP SEC-FIX-5 cache/ and tmp/ themselves are
# root-only (21-vault-volume-ownership.sh), so the probe targets the two
# directories the workers really write. Deliberately no automatic chown of
# cache/depot: silently rewriting ownership of an operator's cached data is a
# surprise; telling them exactly what to run is not.
for d in "$DEPOT_DIR" "$TMP_DIR/proxy"; do
    probe="$d/.vault-write-probe.$$"
    if ! su -s /bin/sh "$WORKER_USER" -c "touch '$probe'" 2>/dev/null; then
        die "$d is not writable by the nginx worker user '$WORKER_USER'
  (uid $(id -u "$WORKER_USER"), gid $(id -g "$WORKER_USER")). Nothing would ever be
  cached. If this is a bind mount, fix it on the host:
      chown -R $(id -u "$WORKER_USER"):$(id -g "$WORKER_USER") <host cache dir>${d#"$PREFIX"}
  The cache directory itself, cache/, tmp/ and logs/ stay root:root 0755
  (vault-core sets that at start). See deploy/README.md."
    fi
    su -s /bin/sh "$WORKER_USER" -c "rm -f '$probe'" 2>/dev/null || true
done
log "cache/depot and tmp/proxy are writable by '$WORKER_USER' (uid $(id -u "$WORKER_USER"))"

log "preflight OK"
