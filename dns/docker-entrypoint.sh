#!/bin/sh
# SteamHangar vault-dns container entrypoint (Phase 1, WP 1.9).
#
# Implements the placeholder contract dns/README.md defines for
# dns/dnsmasq.conf.template ("Placeholder contract"):
#
#   ${CACHE_IP}         required, NO default -- fail fast if unset
#   ${UPSTREAM_DNS_1}   optional, defaults to 1.1.1.1 (Cloudflare)
#   ${UPSTREAM_DNS_2}   optional, defaults to 8.8.8.8 (Google)
#
# The defaults are supplied HERE, not in the template: envsubst has no
# "use this if unset" syntax, and dns/README.md explicitly makes the entrypoint
# responsible for exporting them. CACHE_IP deliberately gets no silent default --
# emitting `address=/steamcontent.com/` with an empty address would produce a
# dnsmasq config that either refuses to start or, worse, starts and answers
# nothing useful while looking healthy.
#
# All three values are substituted VERBATIM into a config file, so each is
# validated as a plain dotted-quad IPv4 address first. That is not paranoia
# theatre: a value containing a newline could inject arbitrary dnsmasq
# directives (dnsmasq's config format is one directive per line), and an
# unvalidated CACHE_IP is operator input arriving via .env.
#
# IPv4 only, on purpose. CACHE_IP is the A-record answer for
# *.steamcontent.com; the whole point of this container (ADR-0001 req 6) is that
# AAAA must resolve to NODATA so IPv6-capable clients cannot bypass the cache.
# Accepting an IPv6 CACHE_IP would defeat that.

set -eu

ME="vault-dns-entrypoint"

log() { echo "$ME: $*"; }
die() { echo "$ME: FATAL: $*" >&2; exit 1; }

TEMPLATE="/etc/vault-dns/dnsmasq.conf.template"
RENDERED="/run/vault-dns/dnsmasq.conf"

# --- IPv4 validation ---------------------------------------------------------
validate_ipv4() {
    _name="$1"
    _value="$2"
    _hint="$3"

    case "$_value" in
        "")           die "$_name is not set. $_hint" ;;
        *[!0-9.]*)    die "$_name='$_value' is not a plain IPv4 address (unexpected characters). $_hint" ;;
    esac

    # exactly four 0-255 octets
    _rest="$_value"
    _count=0
    while [ -n "$_rest" ]; do
        _octet="${_rest%%.*}"
        if [ "$_octet" = "$_rest" ]; then
            _rest=""
        else
            _rest="${_rest#*.}"
        fi
        case "$_octet" in
            ""|*[!0-9]*) die "$_name='$_value' is not a plain IPv4 address (empty or non-numeric octet). $_hint" ;;
        esac
        [ "$_octet" -le 255 ] || die "$_name='$_value' is not a plain IPv4 address (octet > 255). $_hint"
        _count=$((_count + 1))
    done
    [ "$_count" -eq 4 ] || die "$_name='$_value' is not a plain IPv4 address ($_count octets, expected 4). $_hint"
}

# --- required: CACHE_IP ------------------------------------------------------
validate_ipv4 "CACHE_IP" "${CACHE_IP:-}" \
"CACHE_IP must be the LAN IPv4 address of your SteamHangar cache server
  (the host running vault-core), e.g. CACHE_IP=10.0.0.50. It is the address
  every *.steamcontent.com A query gets answered with, so there is deliberately
  no default -- every deployment's is different. Set it in deploy/.env."

# --- optional: upstream forwarders (documented defaults) ---------------------
: "${UPSTREAM_DNS_1:=1.1.1.1}"
: "${UPSTREAM_DNS_2:=8.8.8.8}"
validate_ipv4 "UPSTREAM_DNS_1" "$UPSTREAM_DNS_1" "Set a resolver IPv4 address in deploy/.env (default 1.1.1.1)."
validate_ipv4 "UPSTREAM_DNS_2" "$UPSTREAM_DNS_2" "Set a resolver IPv4 address in deploy/.env (default 8.8.8.8)."

export CACHE_IP UPSTREAM_DNS_1 UPSTREAM_DNS_2

# --- render ------------------------------------------------------------------
[ -f "$TEMPLATE" ] || die "$TEMPLATE is missing from the image."

mkdir -p "$(dirname "$RENDERED")"

# A bare `envsubst` (no allowlist) is safe for THIS template specifically: it
# contains exactly the three ${...} placeholders above and no other '$' text at
# all -- dns/README.md states and relies on that property.
envsubst < "$TEMPLATE" > "$RENDERED"

# Belt and braces: nothing unsubstituted may survive into the running config.
# Comment lines are excluded because the template's own commentary discusses the
# placeholders by name.
if grep -v '^[[:space:]]*#' "$RENDERED" | grep -q '\${'; then
    die "unsubstituted placeholder(s) left in the rendered config: $(grep -v '^[[:space:]]*#' "$RENDERED" | grep -o '\${[A-Za-z0-9_]*}' | sort -u | tr '\n' ' ')"
fi

# The two lines that are the entire reason this container exists (ADR-0001
# req 6): address= supplies the A answer, local= makes dnsmasq authoritative for
# the zone so AAAA becomes NODATA instead of leaking Valve's real IPv6 address
# upstream. Asserted at every start so a template edit cannot quietly drop one.
grep -q "^address=/steamcontent.com/${CACHE_IP}\$" "$RENDERED" \
    || die "rendered config lacks the expected 'address=/steamcontent.com/${CACHE_IP}' line."
grep -q '^local=/steamcontent.com/$' "$RENDERED" \
    || die "rendered config lacks 'local=/steamcontent.com/' -- without it dnsmasq forwards
  AAAA queries for the zone upstream and IPv6-capable clients silently bypass the
  cache (ADR-0001 requirement 6). Refusing to start."

# dnsmasq's own syntax check, before we commit to running it.
dnsmasq --test --conf-file="$RENDERED"

log "steamcontent.com -> $CACHE_IP (A), NODATA (AAAA); upstream $UPSTREAM_DNS_1, $UPSTREAM_DNS_2"
log "config rendered to $RENDERED"

exec "$@"
