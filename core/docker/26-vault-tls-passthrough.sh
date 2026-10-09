#!/bin/sh
# SteamHangar vault-core container hook -- HTTPS passthrough on/off switch
# (WP CORE-FIX-3, ADR-0020).
#
# Runs from the official nginx image's /docker-entrypoint.d/ hook directory,
# AFTER 20-envsubst-on-templates.sh has rendered nginx.conf.template ->
# nginx.conf and 25-vault-eventlog.sh has done its pass, and BEFORE
# 40-vault-preflight.sh re-checks the result (sort order 20 < 25 < 26 < 27
# < 40). docker-entrypoint.sh runs with `set -e`, so a non-zero exit here
# aborts container start.
#
# The template carries the passthrough -- the whole `stream {}` block that
# listens on port 443 -- between two marker comment lines:
#     # VAULT_TLS_PASSTHROUGH_BEGIN
#     ...
#     # VAULT_TLS_PASSTHROUGH_END
# VAULT_TLS_PASSTHROUGH decides what happens to it:
#     1, true, on, yes   (or unset/empty: the default)  -> keep it
#     0, false, off, no                                  -> delete the range
# Anything else stops the boot: a typo must not silently pick a side.
# Empty counts as the default (on) because deploy/compose.yaml forwards the
# key with `${VAULT_TLS_PASSTHROUGH:-1}`, which also turns a blank .env line
# into 1 -- the hook keeps the same meaning for a plain `docker run -e`.
# Turning it off therefore always needs an explicit 0/false/off/no.
#
# WP CORE-FIX-4d (ADR-0021 addendum 2026-10-09): the two connection caps of
# the passthrough are operator-tunable. The template carries the defaults
# as literal lines (so core/nginx/nginx.conf and the template stay
# byte-identical, check-config-drift.sh); with the passthrough ON this hook
# validates the env and rewrites exactly those two lines:
#     VAULT_TLS_CLIENT_MAX_CONNS -> limit_conn vault_tls_client N;
#         per client address, whole number 1..256, empty/unset = 64
#     VAULT_TLS_MAX_CONNS        -> limit_conn vault_tls_total  N;
#         all clients together, whole number 1..400, empty/unset = 256
# and the per-client cap must not exceed the total. 400 is the ceiling
# because every passthrough session holds two of the single worker's 1024
# worker_connections (2 x 400 = 800); the default 256 keeps the HTTP cache
# its half (ADR-0020 "Loop bound"). Digits only, no leading zero, no off
# switch; anything else stops the boot (fail-closed, the same style as
# VAULT_UPSTREAM_MAX_CONNS in 28-vault-upstream-pool.sh). The values are
# validated in both modes, so a typo surfaces even while the passthrough
# is off.
#
# The rewrite edits the rendered nginx.conf in place, so it relies on
# 20-envsubst-on-templates.sh re-rendering the template on every start
# (the same dependency as the OFF path's range delete); on a config that
# was already rewritten the default line is gone and the hook dies
# fail-closed instead of guessing.
#
# Optional argument: the config to edit (default /etc/nginx/nginx.conf),
# so core/tests/test-tls-caps-hook.sh can run the hook without Docker.

set -eu

ME="26-vault-tls-passthrough.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

CONF="${1:-/etc/nginx/nginx.conf}"
BEGIN="# VAULT_TLS_PASSTHROUGH_BEGIN"
END="# VAULT_TLS_PASSTHROUGH_END"
VALUE="${VAULT_TLS_PASSTHROUGH:-1}"

# CORE-FIX-4d: defaults (= the literal values in the template) and ranges.
TLS_CLIENT_DEFAULT=64
TLS_CLIENT_MAX=256
TLS_TOTAL_DEFAULT=256
TLS_TOTAL_MAX=400

[ -f "$CONF" ] || die "$CONF does not exist -- expected to run after envsubst rendering
  (after /docker-entrypoint.d/20-envsubst-on-templates.sh)."

case "$VALUE" in
    1|true|on|yes)   mode=on ;;
    0|false|off|no)  mode=off ;;
    *) die "VAULT_TLS_PASSTHROUGH='$VALUE' is not one of 1/true/on/yes or 0/false/off/no.
  Refusing to guess whether the HTTPS passthrough on port 443 should run." ;;
esac

# tls_cap <variable name> <raw value> <default> <max>: prints the cap or dies.
tls_cap() {
    case "$2" in
        "") echo "$3"; return 0 ;;
        *[!0-9]*)
            die "$1='$2' is not a whole number (digits only, 1..$4, empty = $3).
  It caps the HTTPS passthrough on port 443 (ADR-0021 addendum, CORE-FIX-4d).
  Refusing to start." ;;
        0*)
            die "$1='$2': 0 and leading zeros are not accepted (whole number 1..$4,
  empty = $3, no off switch). Refusing to start." ;;
    esac
    if [ "${#2}" -gt 3 ] || [ "$2" -gt "$4" ]; then
        die "$1=$2 is outside 1..$4 (empty = $3). Refusing to start."
    fi
    echo "$2"
}
TLS_CLIENT=$(tls_cap VAULT_TLS_CLIENT_MAX_CONNS "${VAULT_TLS_CLIENT_MAX_CONNS:-}" "$TLS_CLIENT_DEFAULT" "$TLS_CLIENT_MAX")
TLS_TOTAL=$(tls_cap VAULT_TLS_MAX_CONNS "${VAULT_TLS_MAX_CONNS:-}" "$TLS_TOTAL_DEFAULT" "$TLS_TOTAL_MAX")
[ "$TLS_CLIENT" -le "$TLS_TOTAL" ] || die "VAULT_TLS_CLIENT_MAX_CONNS=$TLS_CLIENT is above VAULT_TLS_MAX_CONNS=$TLS_TOTAL.
  One client could never reach its own cap; set the per-client cap at most
  to the total. Refusing to start."

# Exactly one marker of each kind, BEGIN before END -- otherwise the range
# delete below could take too much, too little, or run to the end of file.
nb=$(grep -c -x -F -- "$BEGIN" "$CONF" || true)
ne=$(grep -c -x -F -- "$END" "$CONF" || true)
[ "$nb" = "1" ] && [ "$ne" = "1" ] || die "expected exactly one '$BEGIN' and one '$END' line in $CONF,
  found $nb and $ne. The template no longer marks the stream block; refusing to start."
lb=$(grep -n -x -F -- "$BEGIN" "$CONF" | cut -d: -f1)
le=$(grep -n -x -F -- "$END" "$CONF" | cut -d: -f1)
[ "$lb" -lt "$le" ] || die "'$END' (line $le) comes before '$BEGIN' (line $lb) in $CONF."

if [ "$mode" = "off" ]; then
    sed -i "/^${BEGIN}\$/,/^${END}\$/d" "$CONF"
    # Second look at the DIRECTIVES, independent of the markers (same
    # lesson as 25-vault-eventlog.sh's review finding N1): nothing that
    # would still open port 443 may survive.
    survivors=$(grep -v '^[[:space:]]*#' "$CONF" | grep -c -E '^[[:space:]]*(stream[[:space:]]*[{]|ssl_preread[[:space:]]|listen[[:space:]]+([^;]*:)?443[[:space:];])' || true)
    [ "$survivors" = "0" ] || die "VAULT_TLS_PASSTHROUGH=$VALUE but $survivors stream/ssl_preread/listen 443
  directive(s) remain in $CONF after removing the marked block. Refusing to
  start a passthrough the operator switched off."
    log "HTTPS passthrough OFF (VAULT_TLS_PASSTHROUGH=$VALUE): no stream block, nothing listens on 443"
    exit 0
fi

# CORE-FIX-4d: rewrite the two cap lines. Each default line must be there
# exactly once as a directive (a template edit that moved or renamed it
# must stop the boot, not ship the default silently), and afterwards each
# configured line exactly once.
# tls_set_cap <zone> <default> <value>
tls_set_cap() {
    _re="^[[:space:]]*limit_conn[[:space:]][[:space:]]*$1[[:space:]][[:space:]]*"
    _n=$(grep -c -- "${_re}$2;\$" "$CONF" || true)
    _all=$(grep -c -- "${_re}" "$CONF" || true)
    [ "$_n" = "1" ] && [ "$_all" = "1" ] || die "expected exactly one 'limit_conn $1 $2;' line (and no other
  'limit_conn $1') in $CONF, found $_n of $_all. The template's stream
  block changed; refusing to start."
    sed -i "s/^\([[:space:]]*limit_conn[[:space:]][[:space:]]*$1[[:space:]][[:space:]]*\)$2;\$/\1$3;/" "$CONF"
    _n=$(grep -c -- "${_re}$3;\$" "$CONF" || true)
    _all=$(grep -c -- "${_re}" "$CONF" || true)
    [ "$_n" = "1" ] && [ "$_all" = "1" ] || die "rendering 'limit_conn $1 $3;' into $CONF failed
  (found $_n of $_all). Refusing to start."
}
tls_set_cap vault_tls_client "$TLS_CLIENT_DEFAULT" "$TLS_CLIENT"
tls_set_cap vault_tls_total "$TLS_TOTAL_DEFAULT" "$TLS_TOTAL"

log "HTTPS passthrough ON (VAULT_TLS_PASSTHROUGH=$VALUE): port 443, SNI *.steamcontent.com only, TLS not terminated"
log "HTTPS passthrough caps: $TLS_CLIENT per client address, $TLS_TOTAL in total (VAULT_TLS_CLIENT_MAX_CONNS='${VAULT_TLS_CLIENT_MAX_CONNS:-}', VAULT_TLS_MAX_CONNS='${VAULT_TLS_MAX_CONNS:-}', empty = 64/256)"
