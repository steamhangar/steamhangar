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

set -eu

ME="26-vault-tls-passthrough.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

CONF="/etc/nginx/nginx.conf"
BEGIN="# VAULT_TLS_PASSTHROUGH_BEGIN"
END="# VAULT_TLS_PASSTHROUGH_END"
VALUE="${VAULT_TLS_PASSTHROUGH:-1}"

[ -f "$CONF" ] || die "$CONF does not exist -- expected to run after envsubst rendering
  (after /docker-entrypoint.d/20-envsubst-on-templates.sh)."

case "$VALUE" in
    1|true|on|yes)   mode=on ;;
    0|false|off|no)  mode=off ;;
    *) die "VAULT_TLS_PASSTHROUGH='$VALUE' is not one of 1/true/on/yes or 0/false/off/no.
  Refusing to guess whether the HTTPS passthrough on port 443 should run." ;;
esac

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

log "HTTPS passthrough ON (VAULT_TLS_PASSTHROUGH=$VALUE): port 443, SNI *.steamcontent.com only, TLS not terminated"
