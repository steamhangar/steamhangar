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
# below are the ONLY allowed differences -- six kinds today; this list, not
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
expect_once "$work/template.norm" 'resolver ${VAULT_RESOLVER} ipv6=off valid=30s;' "delta 4: resolver placeholder"
# delta 5 appears 3 times: once at http level (inherited by /health,
# /lancache-heartbeat, /tmp/) and once each re-stated inside location
# /depot/ and location @miss (WP 3.10 blocker fix -- declaring the event
# log's own access_log in those locations REPLACES, not adds to, the
# inherited one unless the "vault" log is re-stated alongside it; see the
# blocker-fix comments at both sites in nginx.conf.template).
expect_count "$work/template.norm" 3 "access_log /dev/stdout vault;"           "delta 5: access log to stdout (http level + re-stated in /depot/ and @miss)"
expect_count "$work/template.norm" 2 'access_log ${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE' \
    "delta 6: WP 3.10 cache-event log placeholder, one per location (/depot/, @miss)"

# The native config must NOT already contain the container forms (would mean the
# two files drifted in the other direction, e.g. someone containerised the
# native config in place).
expect_once "$work/native.norm" "pid logs/nginx.pid;"                          "native: pid under the prefix"
expect_once "$work/native.norm" "error_log logs/error.log warn;"               "native: error log to a file"
expect_once "$work/native.norm" "resolver 1.1.1.1 ipv6=off valid=30s;"         "native: literal resolver"
expect_count "$work/native.norm" 3 "access_log logs/access.log vault;"         "native: access log to a file (http level + re-stated in /depot/ and @miss)"
expect_count "$work/native.norm" 2 "access_log logs/event.log vault_event buffer=64k flush=5s;" \
    "native: WP 3.10 cache-event log, hardcoded ON, one per location (/depot/, @miss)"

[ "$fail" = "0" ] || exit 1

sed \
    -e '/^user nginx;$/d' \
    -e 's|^pid /var/run/nginx\.pid;$|pid logs/nginx.pid;|' \
    -e 's|^error_log /dev/stderr warn;$|error_log logs/error.log warn;|' \
    -e 's|^resolver \${VAULT_RESOLVER} ipv6=off valid=30s;$|resolver 1.1.1.1 ipv6=off valid=30s;|' \
    -e 's|^access_log /dev/stdout vault;$|access_log logs/access.log vault;|' \
    -e 's|^access_log \${VAULT_EVENT_LOG} vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE$|access_log logs/event.log vault_event buffer=64k flush=5s;|' \
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
