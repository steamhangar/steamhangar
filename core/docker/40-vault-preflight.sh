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

probe_server=${RESOLVER%% *}
if ! command -v nslookup >/dev/null 2>&1; then
    log "note: nslookup not available, skipping the resolver loop probe"
else
    probe_out=$(timeout 5 nslookup -type=a "$PROBE_NAME" "$probe_server" 2>/dev/null) || probe_out=""
    probe_answers=$(printf '%s\n' "$probe_out" | vault_nslookup_answers)
    if [ -z "$probe_answers" ]; then
        log "note: resolver $probe_server gave no A answer for $PROBE_NAME (unreachable, timed out or NXDOMAIN) -- loop probe skipped, not a boot failure"
    else
        for a in $probe_answers; do
            if vault_is_private_ipv4 "$a"; then
                die "resolver $probe_server answers $PROBE_NAME with $a, a private/loopback
  address, so it rewrites *.steamcontent.com instead of answering truthfully.
  This probe cannot tell WHO answers: it may be this host (a router DNATs port
  53 to a Pi-hole/AdGuard rewriting to vault-core, or VAULT_RESOLVER points at
  vault-dns), another LAN cache (e.g. a lancache instance), or a blocker that
  answers 0.0.0.0. In every case cache MISSes cannot reach Valve. Point
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
# cache root that has no depot/ directory, and it reads this same volume. Create
# it here so a fresh install is consistent from the first boot.
if [ ! -d "$DEPOT_DIR" ]; then
    mkdir -p "$DEPOT_DIR"
    chown "$WORKER_USER:$WORKER_USER" "$DEPOT_DIR"
    log "created $DEPOT_DIR"
fi

# --- 5. the worker user can actually write ----------------------------------
# nginx's master runs as root (it must bind :80), workers as $WORKER_USER -- and
# it is the workers that proxy_store into cache/ and tmp/. Testing as root would
# prove nothing, so probe as the worker user itself. Deliberately no automatic
# chown: silently rewriting ownership of an operator's bind-mounted data is a
# surprise; telling them exactly what to run is not.
for d in "$CACHE_DIR" "$TMP_DIR"; do
    probe="$d/.vault-write-probe.$$"
    if ! su -s /bin/sh "$WORKER_USER" -c "touch '$probe'" 2>/dev/null; then
        die "$d is not writable by the nginx worker user '$WORKER_USER'
  (uid $(id -u "$WORKER_USER"), gid $(id -g "$WORKER_USER")). Nothing would ever be
  cached. If this is a bind mount, fix it on the host:
      chown -R $(id -u "$WORKER_USER"):$(id -g "$WORKER_USER") <host cache dir>
  Named volumes get this right automatically -- see deploy/README.md."
    fi
    su -s /bin/sh "$WORKER_USER" -c "rm -f '$probe'" 2>/dev/null || true
done
log "cache/ and tmp/ are writable by '$WORKER_USER' (uid $(id -u "$WORKER_USER"))"

log "preflight OK"
