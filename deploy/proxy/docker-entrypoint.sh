#!/bin/sh
# SteamHangar vault-proxy container entrypoint (WP EG-1, ADR-0011).
#
# Renders /run/tinyproxy/tinyproxy.conf (from the read-only template in
# /etc/tinyproxy/) and /run/tinyproxy/filter -- the destination allowlist
# tinyproxy.conf's `Filter` + `FilterDefaultDeny Yes` enforce -- from
# VAULT_EGRESS_SUBNET, VAULT_EGRESS_ALLOW and ONE baked-in host, then execs
# tinyproxy. Runs entirely as the unprivileged `tinyproxy` account
# (Dockerfile's `USER tinyproxy:tinyproxy`, no root phase at any point in
# this container's life), which is why only /run/tinyproxy is chowned to that
# account at image build time. /etc/tinyproxy stays root-owned, so this
# process can rewrite its running config but never the template it is
# rebuilt from at each start.
#
# Pattern shape: each allowed host becomes an ANCHORED, dot-escaped POSIX
# basic regular expression (tinyproxy.conf's `FilterType bre`) -- e.g.
# "api.steampowered.com" renders as "^api\.steampowered\.com$". Anchoring
# stops a pattern from matching as a substring of an unrelated host; escaping
# the dots stops "." (a BRE metacharacter meaning "any one character") from
# accidentally allowing lookalike hosts a plain, unescaped copy would let
# through.

set -eu

ME="vault-proxy-entrypoint"
log() { echo "$ME: $*"; }
die() { echo "$ME: FATAL: $*" >&2; exit 1; }

# The hostname-validation function this script's loop uses below --
# extracted to its own file (round-2 review S2/N4) so
# api/tests/test_eg1_egress_lock.py can invoke the REAL validation logic
# directly, via a real `sh` subprocess, instead of a Python reimplementation
# that could silently drift from what actually ships. See that file's own
# header comment for the three real disagreements this fixed.
# shellcheck source=./validate-hostname.sh
. "$(dirname "$0")/validate-hostname.sh"
# Strict IPv4 CIDR check for VAULT_EGRESS_SUBNET (WP DEPLOY-FIX-2), same
# extraction pattern and the same reason.
# shellcheck source=./validate-subnet.sh
. "$(dirname "$0")/validate-subnet.sh"

# Overridable ONLY so api/tests/test_eg1_egress_lock.py can run this ENTIRE
# script end to end (rendering into a throwaway temp file, then a harmless
# final command instead of tinyproxy) on a plain CI runner with no Docker
# and no root -- round-2 review N4/S2's point that the shell logic must be
# pinned as the REAL artifact, not a Python reimplementation of it, applies
# to the whole rendering loop (including the `set -f` fix below), not just
# the character-validation function `validate-hostname.sh` already isolates.
# The shipped container never sets this variable, so `/run/tinyproxy/filter`
# is what every real deployment actually gets.
FILTER_FILE="${VAULT_PROXY_FILTER_FILE_FOR_TESTS:-/run/tinyproxy/filter}"
# Same test-only override rule for the rendered tinyproxy config and the
# read-only template it is rendered from (WP DEPLOY-FIX-2).
CONF_TEMPLATE="${VAULT_PROXY_CONF_TEMPLATE_FOR_TESTS:-/etc/tinyproxy/tinyproxy.conf.template}"
CONF_FILE="${VAULT_PROXY_CONF_FILE_FOR_TESTS:-/run/tinyproxy/tinyproxy.conf}"

# --- client allowlist: render tinyproxy.conf's `Allow <cidr>` line -----------
# WP DEPLOY-FIX-2 (ADR-0011 addendum 2026-10-01). vault-egress's subnet is
# configurable (deploy/compose.yaml, VAULT_EGRESS_SUBNET, default
# 172.30.238.0/24) so two stacks can share one host; compose forwards the SAME
# expression here, so the proxy admits exactly the range vault-api sits in.
# Fail closed at every step: an unset/blank value, or anything that is not a
# strict IPv4 CIDR with prefix 8-30, refuses to start -- there is no fallback
# here, because compose always supplies a value and a silent default would
# reintroduce the drift this replaced. The template's ONE static `Allow
# a.b.c.d/n` line is replaced (deterministically: the template is read-only
# and never this script's own previous output), then the result is checked.
egress_subnet=${VAULT_EGRESS_SUBNET-}
[ -n "$egress_subnet" ] || die "VAULT_EGRESS_SUBNET is unset or blank -- deploy/compose.yaml forwards it to this container (default 172.30.238.0/24); a container started without it is misconfigured."
validate_egress_subnet "$egress_subnet" || die "VAULT_EGRESS_SUBNET '$egress_subnet' is not a strict IPv4 CIDR (a.b.c.d/n, octets 0-255 without leading zeros, prefix 8-30) -- fix deploy/.env's VAULT_EGRESS_SUBNET."
[ -r "$CONF_TEMPLATE" ] || die "tinyproxy config template '$CONF_TEMPLATE' is missing or unreadable."

# The value is validated above to contain only digits, '.' and '/', so it is
# safe inside a sed replacement with '|' as the delimiter.
sed -E "s|^Allow[[:space:]]+[0-9.]+/[0-9]+[[:space:]]*\$|Allow $egress_subnet|" \
    "$CONF_TEMPLATE" > "$CONF_FILE.tmp" || die "could not render $CONF_FILE"

# Post-render assertion: the Allow lines must be EXACTLY the loopback line and
# one line naming the validated subnet -- no second CIDR, no missing line, no
# leftover default. tinyproxy directives are case-insensitive and may be
# indented, so count them the same way.
allow_lines=$(grep -Ei '^[[:space:]]*allow[[:space:]]' "$CONF_FILE.tmp" || true)
expected_allow=$(printf 'Allow 127.0.0.1\nAllow %s' "$egress_subnet")
if [ "$allow_lines" != "$expected_allow" ]; then
    rm -f "$CONF_FILE.tmp"
    die "rendered tinyproxy config does not have exactly one client Allow line equal to VAULT_EGRESS_SUBNET ($egress_subnet) next to the loopback line; got: $(printf '%s' "$allow_lines" | tr '\n' ';')"
fi
mv -f "$CONF_FILE.tmp" "$CONF_FILE" || die "could not move the rendered config into place at $CONF_FILE"
log "client allowlist rendered: Allow $egress_subnet (VAULT_EGRESS_SUBNET) + loopback"

# --- baked-in mandatory host --------------------------------------------------
# api.steampowered.com: the ONE outbound host the shipped PRODUCT ITSELF
# needs, once an operator configures a Steam Web API relay key (ADR-0004
# addendum; api/README.md "Steam Web API relay";
# docs/security/threat-model.md §5 item 1). Deliberately NOT gated behind
# VAULT_EGRESS_ALLOW, unlike the manifest oracle and webhook targets below:
# the relay key is a runtime, DB-stored setting (ADR-0009, `PATCH
# /v1/settings`) that can be turned on with no vault-api restart at all, so a
# boot-time env-only allowlist cannot reliably track whether it is "in use"
# the way it can for the oracle (an env-only switch, fixed for the process's
# whole life -- see vault_api/config.py's own startup check for that case).
# Baking this one host in trades a marginally wider always-on default for
# the relay actually working the first time an operator turns it on, rather
# than failing with a filtered-403 the operator would have no reason to
# connect to a missing allowlist entry. See docs/adr/0011-egress-lock.md for
# the full argument. The manifest oracle's host is NOT baked in either: since
# WP UPD-1 (user decision 2026-10-13, Weg A) the default oracle host is added
# below only while VAULT_MANIFEST_ORACLE is on and VAULT_MANIFEST_ORACLE_URL
# points at it, and never when the oracle is off.
# Since WP API-FIX-5 vault-api also calls this host on EVERY install, with no
# key: the background cover-art lookup (IStoreBrowseService/GetItems,
# api/vault_api/cover_art.py; threat-model §5 item 6; ADR-0011 addendum
# 2026-10-10). Same host, so nothing here changed.
# WP D19-1c: store.steampowered.com is baked in too (card enrichment:
# appdetails and appreviews, one app id per opened card). Baked rather than
# switch-derived for the same reason as api.steampowered.com: the switch lives
# in vault-api, not here. The traffic itself stays off unless the operator sets
# VAULT_STORE_DETAILS (env-only, default false); the host only becomes
# reachable, no data flows without that switch (docs/security/privacy-review.md
# O9).
BAKED_HOSTS="api.steampowered.com shared.akamai.steamstatic.com cdn.akamai.steamstatic.com store.steampowered.com"

# Turns "host.name" into the anchored, dot-escaped BRE pattern described
# above. Deliberately narrow: escapes only ".", the one BRE metacharacter a
# syntactically valid hostname can ever contain (the character-allowlist
# check below already rejects everything else a regex could interpret
# specially).
to_pattern() {
    printf '^%s$\n' "$(printf '%s' "$1" | sed 's/\./\\./g')"
}

{
    echo "# Rendered by $ME at container start. DO NOT EDIT THIS FILE --"
    echo "# it is regenerated, from scratch, every time this container starts."
    echo "# Add hosts via VAULT_EGRESS_ALLOW in deploy/.env instead, then"
    echo "# \`docker compose up -d\` (a plain restart re-reads the same value;"
    echo "# a compose.yaml env change needs 'up -d' to actually take effect)."
    echo "#"
    echo "# FilterDefaultDeny Yes (tinyproxy.conf) means every hostname NOT"
    echo "# listed below is refused -- for both plain HTTP forwarding and"
    echo "# HTTPS CONNECT tunnels. This file is the complete, exhaustive"
    echo "# destination allowlist for everything vault-api can reach beyond"
    echo "# the LAN. See deploy/README.md \"verify in five minutes\" for how"
    echo "# to check that claim against the running container yourself."
    echo ""
    echo "# --- baked in: the Steam Web API relay / cover lookup host and"
    echo "# Steam's two cover image hosts (see this script's own comment on"
    echo "# BAKED_HOSTS above for why they are not gated behind"
    echo "# VAULT_EGRESS_ALLOW like the ones below) ---"
    for h in $BAKED_HOSTS; do
        to_pattern "$h"
    done
} > "$FILTER_FILE"

# --- the default manifest oracle host (WP UPD-1, user decision 2026-10-13) --
# The oracle is ON by default: VAULT_MANIFEST_ORACLE unset/empty or
# 'steamcmd_api' means on, 'off' or 'none' means off, anything else refuses to
# start (the same grammar as vault_api/config.py's `_env_manifest_oracle`,
# trimmed and case-insensitive).
#
# While it is on, exactly ONE host is allowed on its own: the default oracle
# host ORACLE_AUTO_ALLOWED_HOST (= config.py's constant of the same name),
# and only when VAULT_MANIFEST_ORACLE_URL is https:// to that host
# (config.py's `oracle_url_is_auto_allowed`). The URL is only ever COMPARED
# with that constant, never turned into a pattern itself, so it cannot open
# any other host (ADR-0011 review). Plain http:// to the same host, a private
# mirror or a LAN proxy still need an explicit VAULT_EGRESS_ALLOW entry below
# (vault-api refuses to boot without one). Everything else stays default-deny.
#
# The URL is validated like config.py's `_env_manifest_oracle_url` does it,
# even while the oracle is off: no whitespace or control characters, scheme
# http or https, balanced IPv6 brackets -- a value vault-api refuses to boot
# on stops this container too. api/tests/test_upd_1_oracle_egress.py runs this
# script against config.py for a table of values so the two cannot drift.
ORACLE_AUTO_ALLOWED_HOST="api.steamcmd.net"
DEFAULT_ORACLE_URL="https://$ORACLE_AUTO_ALLOWED_HOST/v1/info"

trim() {
    printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

# urlsplit(url).scheme: the text before the first ':' if it starts with a
# letter and holds only scheme characters, lowercased; else empty.
oracle_url_scheme() {
    case "$1" in
        *:*) _s=${1%%:*} ;;
        *) return 0 ;;
    esac
    case "$_s" in
        [A-Za-z]*) ;;
        *) return 0 ;;
    esac
    case "$_s" in
        *[!A-Za-z0-9+.-]*) return 0 ;;
    esac
    printf '%s\n' "$_s" | tr '[:upper:]' '[:lower:]'
}

# urlsplit(url).hostname, given the netloc computed below: minus userinfo (up
# to the last '@'), then either the inside of an IPv6 [bracket] or
# everything before the first ':', lowercased.
oracle_netloc_host() {
    _u=${1##*@}
    case "$_u" in
        \[*\]*) _u=${_u#\[}; _u=${_u%%\]*} ;;
        *) _u=${_u%%:*} ;;
    esac
    printf '%s\n' "$_u" | tr '[:upper:]' '[:lower:]'
}

oracle_selector=$(trim "${VAULT_MANIFEST_ORACLE:-}" | tr '[:upper:]' '[:lower:]')
case "$oracle_selector" in
    ""|steamcmd_api|off|none) ;;
    *)
        die "VAULT_MANIFEST_ORACLE='$oracle_selector' is not a supported manifest oracle (known: 'steamcmd_api'; 'off' or 'none' disables it; unset or empty means the default, on) -- vault-api refuses the same value."
        ;;
esac

oracle_url=$(trim "${VAULT_MANIFEST_ORACLE_URL:-}")
[ -n "$oracle_url" ] || oracle_url=$DEFAULT_ORACLE_URL
case "$oracle_url" in
    *[[:space:][:cntrl:]]*)
        die "VAULT_MANIFEST_ORACLE_URL contains whitespace or control characters -- vault-api refuses the same value; fix deploy/.env."
        ;;
esac
oracle_scheme=$(oracle_url_scheme "$oracle_url")
case "$oracle_scheme" in
    http|https) ;;
    *) die "VAULT_MANIFEST_ORACLE_URL '$oracle_url' must be an http:// or https:// URL (got scheme '$oracle_scheme') -- vault-api refuses the same value." ;;
esac
# The netloc, as urlsplit cuts it: only after "scheme://", up to the first
# '/', '?' or '#' (no "//" = no netloc, nothing to check).
oracle_netloc=${oracle_url#*:}
case "$oracle_netloc" in
    //*) oracle_netloc=${oracle_netloc#//} ;;
    *) oracle_netloc="" ;;
esac
oracle_netloc=${oracle_netloc%%/*}
oracle_netloc=${oracle_netloc%%\?*}
oracle_netloc=${oracle_netloc%%#*}
case "$oracle_netloc" in
    *\[*) case "$oracle_netloc" in *\]*) ;; *) die "VAULT_MANIFEST_ORACLE_URL '$oracle_url' has an unbalanced IPv6 bracket -- vault-api refuses the same value." ;; esac ;;
    *\]*) die "VAULT_MANIFEST_ORACLE_URL '$oracle_url' has an unbalanced IPv6 bracket -- vault-api refuses the same value." ;;
esac

case "$oracle_selector" in
    off|none)
        log "manifest oracle: off (VAULT_MANIFEST_ORACLE=$oracle_selector); $ORACLE_AUTO_ALLOWED_HOST is not allowed"
        ;;
    *)
        oracle_host=$(oracle_netloc_host "$oracle_netloc")
        if [ "$oracle_scheme" = "https" ] && [ "$oracle_host" = "$ORACLE_AUTO_ALLOWED_HOST" ]; then
            {
                echo ""
                echo "# --- default manifest oracle host (VAULT_MANIFEST_ORACLE on and"
                echo "# VAULT_MANIFEST_ORACLE_URL is https:// to it; =off drops it) ---"
                to_pattern "$ORACLE_AUTO_ALLOWED_HOST"
            } >> "$FILTER_FILE"
            log "manifest oracle: on, allowing the default oracle host $ORACLE_AUTO_ALLOWED_HOST"
        else
            # Printed with %s and no pattern rendering: an IPv6 literal or any
            # other host only ever shows up here, in quotes.
            log "manifest oracle: on, but VAULT_MANIFEST_ORACLE_URL is $oracle_scheme:// to host '$oracle_host', not https://$ORACLE_AUTO_ALLOWED_HOST: nothing allowed automatically, list that host in VAULT_EGRESS_ALLOW"
        fi
        ;;
esac

# --- operator-configured extras (VAULT_EGRESS_ALLOW) -------------------------
# The documented case (deploy/.env.example, deploy/README.md): webhook
# receivers (any of them, LAN or WAN -- see deploy/README.md's egress-lock
# section for why a LAN target is not a special case here). The manifest
# oracle's DEFAULT host no longer needs an entry here (WP UPD-1, see above);
# a private oracle mirror named in VAULT_MANIFEST_ORACLE_URL still does. Comma-separated, blank/unset = no extras at all
# (decision: the shipped default is empty, not a wide-open allowlist).
echo "" >> "$FILTER_FILE"
echo "# --- operator-configured extras (VAULT_EGRESS_ALLOW) ---" >> "$FILTER_FILE"

allow_count=0
old_ifs=$IFS
IFS=,
# set -f (noglob) BEFORE this expansion, not after: round-2 review found
# that a literal "*" entry underwent the SHELL's OWN pathname expansion at
# this "for raw in $VAR" line itself, before the loop body -- let alone
# normalize_egress_hostname's character check -- ever saw it, turning one
# malicious/typo'd entry into one iteration PER FILENAME in this
# container's working directory. Restored with `set +f` after the loop so
# it does not leak into anything sourced/run later in this script's life
# (there is nothing after it today, but that is not a reason to leave a
# shell-wide mode flipped past the one loop that needs it).
set -f
for raw in ${VAULT_EGRESS_ALLOW:-}; do
    IFS=$old_ifs
    host=$(normalize_egress_hostname "$raw") && rc=0 || rc=$?
    IFS=,
    if [ "$rc" -eq 2 ]; then
        continue
    elif [ "$rc" -ne 0 ]; then
        set +f
        die "VAULT_EGRESS_ALLOW entry '$raw' is not a plausible hostname (letters, digits, '.', '-' only; no leading/trailing '.'/'-'; no empty label) -- fix deploy/.env's VAULT_EGRESS_ALLOW."
    fi
    IFS=$old_ifs
    to_pattern "$host" >> "$FILTER_FILE"
    IFS=,
    allow_count=$((allow_count + 1))
done
set +f
IFS=$old_ifs

log "filter file rendered: ${BAKED_HOSTS} (baked-in) + ${allow_count} operator host(s) from VAULT_EGRESS_ALLOW"
log "--- $FILTER_FILE ---"
sed 's/^/    /' "$FILTER_FILE"

exec "$@"
