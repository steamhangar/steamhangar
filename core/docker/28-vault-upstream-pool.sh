#!/bin/sh
# SteamHangar vault-core container hook -- upstream keepalive pool per Steam
# CDN edge (WP CORE-FEAT-1b, docs/adr/0017-upstream-keepalive-pool.md).
#
# Renders /etc/nginx/vault-upstream-pool.conf, which nginx.conf pulls in with
# `include vault-upstream-pool.conf;` (http level, directly after the rate
# include). It holds one `upstream <edge> { ... }` group per edge named in
# VAULT_UPSTREAM_POOL_HOSTS. Why that helps: @miss proxies with a VARIABLE
# proxy_pass (`http://$vault_upstream_host$request_uri`), and nginx attaches
# its keepalive cache only to configured upstream groups -- a host without a
# group gets a throw-away peer per request and therefore a new TCP connection
# per chunk (ADR-0017 "Context"). A host WITH a group matches by name
# (nginx strips :port from $host before the map runs, so $vault_upstream_host
# never carries a port and an `upstream` block registered without a port
# matches it; ADR-0017 "Mechanism") and reuses pooled connections. Hosts not
# listed keep today's behaviour: one connection per chunk, no error, no gain.
#
# Input (env only, read once here at container start -- see core/README.md
# "Upstream keepalive pool"; a change needs a container recreate, ADR-0017
# decision 4B):
#   VAULT_UPSTREAM_POOL_HOSTS   space-separated edge host names, e.g.
#                               "cache1-fra2.steamcontent.com
#                                dist-fra1.discovery.steamserver.net".
#                               Empty/unset = no pool (header-only render).
#
# Rendered per host (ADR-0017 decisions 1A, 3A; (b), (c), (e)):
#     upstream <host> {
#         zone vault_edges 256k;              # size on the FIRST group only
#         server <host> resolve max_fails=0;
#         keepalive 8;
#         keepalive_timeout 50s;
#     }
#   - `zone`: `server ... resolve` requires the group to live in shared memory.
#     All groups share ONE zone; nginx docs (ngx_http_upstream_module, `zone`,
#     read 2026-10-02): "Several groups may share the same zone. In this case,
#     it is enough to specify the size only once." So the size is written on
#     the first group and omitted on the rest. (ngx_cycle.c accepts a later
#     size-less declaration of a zone that already has a size, and a zone that
#     never gets a size fails `nginx -t` with the EMERG `zero size shared
#     memory zone "vault_edges"` (ngx_init_cycle, nginx 1.29.8) -- so the one
#     size is load-bearing and the self-check below asserts it.)
#   - `resolve`: one peer per A record, re-resolved every `valid` of the
#     http-level DNS directive (30s). `max_fails=0` ("disables the accounting
#     of attempts", nginx docs): with several peers the default max_fails=1
#     would turn one CGNAT connect failure into 10s of "no live upstreams"
#     for every request to that edge (ADR-0017 (b)).
#   - NO DNS directive inside any group (ADR-0017 (c)): a group without its
#     own takes the http-level one including `ipv6=off valid=30s`, so
#     ${VAULT_RESOLVER} stays the single place that names a DNS server
#     (check-config-drift.sh delta 4). The self-check refuses a render that
#     contains that directive name anywhere.
#   - `keepalive 8` idle connections per group per worker (worker_processes 1)
#     and a render-time ceiling of 32 idle connections in total, i.e. at most
#     4 groups (ADR-0017 decision 3A: the only measured safe point behind the
#     CGNAT is 50 parallel connections; 32 idle + 8 in flight stays under it).
#   - `keepalive_timeout 50s`: the edges measured on 2026-10-02 keep an idle
#     connection for at least 60s (ADR-0017 "Measurement"); 50s lets nginx
#     close first, so the stale-connection path is rarely taken.
#
# Validation, all FAIL-CLOSED (docker-entrypoint.sh runs hooks under `set -e`,
# so a non-zero exit stops the boot; the message names the offending value):
#   - whitespace runs separate tokens (POSIX word splitting on space, tab,
#     newline), so leading/trailing/double spaces are tolerated and an empty
#     token cannot arise; globbing is OFF (`set -f`) so a `*` token is refused
#     by the charset rule instead of expanding against the working directory;
#   - no `:port` (the group name must equal what $vault_upstream_host yields,
#     and that never carries a port);
#   - lowercase only. nginx matches group names case-insensitively, but the
#     list must equal what $vault_upstream_host yields and that is lowercase
#     ($host is lowercased by nginx); kept strict rather than normalised here;
#   - hostname charset: labels of [a-z0-9-] joined by single dots, no leading
#     or trailing dot (so no FQDN trailing dot either), no empty label;
#   - DNS length limits (RFC 1035): no label longer than 63 characters, no
#     name longer than 253. nginx accepts an over-long name at config time,
#     but the zone's resolve handler then sends malformed queries and logs
#     "could not be resolved" every resolver_timeout for the container's
#     lifetime (review finding, WP CORE-FEAT-1b);
#   - the name must end in one of the two allowlist families of the
#     `map $host $vault_upstream_host` block in nginx.conf, read from there,
#     not invented: `.steamcontent.com` and `.steamserver.net`, with at least
#     one label in front (the map's `^[a-z0-9-]+(\.[a-z0-9-]+)*\.` prefix);
#     a foreign name could never be dialled but would cost a DNS query every
#     30s for nothing;
#   - not the marker `lancache.steamcontent.com`: the map rewrites that exact
#     string to dist-fra1.discovery.steamserver.net, so $vault_upstream_host
#     never yields it and a group of that name could never match (it would
#     only NXDOMAIN every 30s). List the edge it maps to instead;
#   - no duplicates (nginx refuses a duplicate upstream name anyway; refused
#     here with a readable message before nginx sees it);
#   - at most MAX_HOSTS (= MAX_IDLE_TOTAL / KEEPALIVE_PER_GROUP) names;
#   - the rendered file is re-read and its shape asserted before nginx ever
#     sees it (block count == host count, one sized zone, no DNS directive).
#
# With no list configured the file is the header comment only, so the
# directive in nginx.conf is always present and the native core/nginx/
# nginx.conf carries the identical include line (its static core/nginx/
# vault-upstream-pool.conf is byte-for-byte this script's empty render --
# check-config-drift.sh asserts that with cmp).
#
# Usage: 28-vault-upstream-pool.sh [output-file]
#   (no argument when run by docker-entrypoint.sh; the drift check, the
#   Dockerfile build check and core/tests/test-upstream-pool-hook.sh pass a
#   temp path)
#
# POSIX sh (busybox ash in the image, dash in CI); no bashisms.

set -eu

ME="28-vault-upstream-pool.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

OUT="${1:-/etc/nginx/vault-upstream-pool.conf}"

# ADR-0017 decision 3A. check-config-drift.sh pins both numbers.
KEEPALIVE_PER_GROUP=8
MAX_IDLE_TOTAL=32
MAX_HOSTS=$((MAX_IDLE_TOTAL / KEEPALIVE_PER_GROUP))
# ADR-0017 (e) + "Measurement": below the edges' observed >= 60s idle timeout.
KEEPALIVE_TIMEOUT=50s
ZONE_NAME=vault_edges
ZONE_SIZE=256k
# The allowlist families of `map $host $vault_upstream_host` (nginx.conf).
FAMILY_1=steamcontent.com
FAMILY_2=steamserver.net
# The exact-string map entry that never yields itself.
MARKER=lancache.steamcontent.com

HOSTS_RAW="${VAULT_UPSTREAM_POOL_HOSTS:-}"

# --- validate ----------------------------------------------------------------
HOSTS=""
COUNT=0
# No globbing: a `*` token must reach the charset check as a literal.
set -f
# Word splitting on the default IFS (space, tab, newline) is the tokenizer;
# runs of whitespace collapse and empty tokens cannot occur.
# shellcheck disable=SC2086
for h in $HOSTS_RAW; do
    case "$h" in
        *:*)
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' carries a scheme or port. Group names must
  equal the bare host name \$vault_upstream_host yields (no http://, and nginx
  strips :port from \$host before the allowlist map runs). Refusing to start." ;;
    esac
    case "$h" in
        *[A-Z]*)
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' contains uppercase. The list must equal
  what \$vault_upstream_host yields, which is lowercase; write the name in
  lowercase. Refusing to start." ;;
    esac
    case "$h" in
        *[!a-z0-9.-]*)
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' is not a host name (allowed: labels of
  a-z 0-9 '-' joined by '.'). Also check for invisible characters, e.g. a CR
  from a Windows-edited .env. Refusing to start." ;;
        .*|*.|*..*)
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' has an empty label (leading, trailing or
  doubled '.'). Refusing to start." ;;
    esac
    # RFC 1035 lengths: labels <= 63, whole name <= 253. Labels are non-empty
    # at this point, so the dot-split loop ends on the last label.
    if [ "${#h}" -gt 253 ]; then
        die "VAULT_UPSTREAM_POOL_HOSTS: '$h' is ${#h} characters long; a host name may have
  at most 253. nginx would accept it and then log 'could not be resolved' for
  the container's lifetime. Refusing to start."
    fi
    _rest=$h
    while :; do
        _label=${_rest%%.*}
        if [ "${#_label}" -gt 63 ]; then
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' has a label of ${#_label} characters
  ('$_label'); a DNS label may have at most 63. Refusing to start."
        fi
        case "$_rest" in
            *.*) _rest=${_rest#*.} ;;
            *)   break ;;
        esac
    done
    case "$h" in
        "$FAMILY_1"|"$FAMILY_2")
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' is a bare family name, not an edge. The
  allowlist map needs at least one label in front, e.g. cache1-fra2.$FAMILY_1.
  Refusing to start." ;;
        *."$FAMILY_1"|*."$FAMILY_2") : ;;
        *)
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' is outside the Host allowlist families
  (*.$FAMILY_1, *.$FAMILY_2 -- the \`map \$host \$vault_upstream_host\` block in
  nginx.conf). vault-core would never dial it, but would re-resolve it every
  30s for nothing. Refusing to start." ;;
    esac
    case "$h" in
        "$MARKER")
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' is the client-side discovery marker, not an
  edge: the allowlist map rewrites it to dist-fra1.discovery.$FAMILY_2, so a
  group of that name can never match and the name has no public A record. List
  dist-fra1.discovery.$FAMILY_2 instead. Refusing to start." ;;
    esac
    case " $HOSTS " in
        *" $h "*)
            die "VAULT_UPSTREAM_POOL_HOSTS: '$h' is listed twice. Refusing to start." ;;
    esac
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -gt "$MAX_HOSTS" ]; then
        die "VAULT_UPSTREAM_POOL_HOSTS: '$h' is edge number $COUNT, above the ceiling of
  $MAX_HOSTS edges (keepalive $KEEPALIVE_PER_GROUP idle connections per group, at most
  $MAX_IDLE_TOTAL idle in total -- ADR-0017 decision 3A, sized against the CGNAT port
  quota measured in the first rollout). Shorten the list. Refusing to start."
    fi
    HOSTS="${HOSTS:+$HOSTS }$h"
done
set +f

# --- render ------------------------------------------------------------------
# The header is the ENTIRE empty render and must stay byte-identical to
# core/nginx/vault-upstream-pool.conf (check-config-drift.sh step 2d, cmp).
# Rendered text must not contain the DNS directive's name anywhere (the
# self-check greps the whole file, comments included).
tmp="$OUT.tmp.$$"
trap 'rm -f "$tmp"' EXIT INT TERM
{
    echo "# SteamHangar vault-core -- upstream keepalive pool include (ADR-0017)."
    echo "# Rendered at container start by /docker-entrypoint.d/$ME from"
    echo "# VAULT_UPSTREAM_POOL_HOSTS -- do not edit; see core/README.md \"Upstream"
    echo "# keepalive pool\". Natively (core/nginx/vault-upstream-pool.conf) this file is"
    echo "# the empty render, byte for byte (check-config-drift.sh asserts it)."
    if [ "$COUNT" -eq 0 ]; then
        echo "# Pooled edges: 0 (no pool; every MISS opens its own upstream connection)."
    else
        echo "# Pooled edges: $COUNT (keepalive $KEEPALIVE_PER_GROUP idle per group, ceiling $MAX_IDLE_TOTAL idle = at most $MAX_HOSTS groups)."
        echo "# The shared zone's size is given once, on the first group (nginx docs, zone:"
        echo "# \"Several groups may share the same zone. In this case, it is enough to"
        echo "# specify the size only once.\"). No DNS directive inside a group: each one"
        echo "# inherits the http-level setting, ipv6=off valid=30s included (ADR-0017 (c))."
        _i=0
        # shellcheck disable=SC2086
        for h in $HOSTS; do
            _i=$((_i + 1))
            echo "upstream $h {"
            if [ "$_i" -eq 1 ]; then
                echo "    zone $ZONE_NAME $ZONE_SIZE;"
            else
                echo "    zone $ZONE_NAME;"
            fi
            echo "    server $h resolve max_fails=0;"
            echo "    keepalive $KEEPALIVE_PER_GROUP;"
            echo "    keepalive_timeout $KEEPALIVE_TIMEOUT;"
            echo "}"
        done
    fi
} > "$tmp"

# --- assert the render before nginx sees it ----------------------------------
# Independent of how the file was produced: re-read it and check the shape.
assert_fail() { die "rendered $OUT failed its self-check: $*. Refusing to start
  with a pool include that does not match VAULT_UPSTREAM_POOL_HOSTS."; }

count_lines() { grep -c -- "$1" "$tmp" || true; }

blocks=$(count_lines '^upstream [a-z0-9.-]* {$')
[ "$blocks" = "$COUNT" ] || assert_fail "expected $COUNT upstream blocks, found $blocks"
opens=$(count_lines '{$')
closes=$(count_lines '^}$')
if [ "$opens" != "$COUNT" ] || [ "$closes" != "$COUNT" ]; then
    assert_fail "unbalanced braces ($opens open, $closes close, $COUNT expected)"
fi
if grep -qw 'resolver' "$tmp"; then
    assert_fail "a DNS directive appears inside the pool include; groups must inherit the http-level one (ADR-0017 (c))"
fi
servers=$(count_lines '^    server [a-z0-9.-]* resolve max_fails=0;$')
[ "$servers" = "$COUNT" ] || assert_fail "expected $COUNT 'server <host> resolve max_fails=0;' lines, found $servers"
ka=$(count_lines "^    keepalive $KEEPALIVE_PER_GROUP;$")
[ "$ka" = "$COUNT" ] || assert_fail "expected $COUNT 'keepalive $KEEPALIVE_PER_GROUP;' lines, found $ka"
kt=$(count_lines "^    keepalive_timeout $KEEPALIVE_TIMEOUT;$")
[ "$kt" = "$COUNT" ] || assert_fail "expected $COUNT 'keepalive_timeout $KEEPALIVE_TIMEOUT;' lines, found $kt"
zones=$(count_lines "^    zone $ZONE_NAME\( $ZONE_SIZE\)\{0,1\};$")
[ "$zones" = "$COUNT" ] || assert_fail "expected $COUNT 'zone $ZONE_NAME' lines, found $zones"
sized=$(count_lines "^    zone $ZONE_NAME $ZONE_SIZE;$")
if [ "$COUNT" -eq 0 ]; then
    [ "$sized" = "0" ] || assert_fail "a zone line in an empty render"
else
    [ "$sized" = "1" ] || assert_fail "the shared zone's size must be given exactly once, found $sized"
fi
# Nothing but comments and the five line shapes above may be present.
if grep -v '^#' "$tmp" | grep -qvE '^(upstream [a-z0-9.-]+ \{|    (zone|server|keepalive|keepalive_timeout) [^;]+;|\})$'; then
    assert_fail "an unexpected line is present"
fi
# Every host of the validated list has its block, in order.
_i=0
# shellcheck disable=SC2086
for h in $HOSTS; do
    _i=$((_i + 1))
    grep -qx "upstream $h {" "$tmp" || assert_fail "no block for '$h'"
    grep -qx "    server $h resolve max_fails=0;" "$tmp" || assert_fail "no server line for '$h'"
done
[ "$_i" = "$COUNT" ] || assert_fail "host count mismatch ($_i vs $COUNT)"

mv "$tmp" "$OUT"
trap - EXIT INT TERM

if [ "$COUNT" -eq 0 ]; then
    log "VAULT_UPSTREAM_POOL_HOSTS unset/empty -- no upstream keepalive pool (every MISS opens its own connection); rendered $OUT"
else
    log "upstream keepalive pool ON: $COUNT edge group(s) [$HOSTS], keepalive $KEEPALIVE_PER_GROUP idle per group (ceiling $MAX_IDLE_TOTAL), keepalive_timeout $KEEPALIVE_TIMEOUT; rendered $OUT"
fi
