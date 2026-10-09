#!/usr/bin/env bash
# Docker-free test of the HTTPS passthrough connection caps in
# core/docker/26-vault-tls-passthrough.sh (WP CORE-FIX-4d, ADR-0021 addendum
# 2026-10-09). Runs the real hook (under `sh`, the way the container runs
# it) against a copy of core/docker/nginx.conf.template and asserts:
#   - empty/unset values render the defaults 64 (per client) / 256 (total);
#   - valid values render exactly one `limit_conn vault_tls_client N;` and
#     one `limit_conn vault_tls_total N;` line, nothing else changes;
#   - every invalid value (non-integer, 0, leading zero, above the range,
#     client above total, sign, blank inside) is refused with the hook's
#     FATAL line naming the variable, and the config is left untouched;
#   - with the passthrough OFF an invalid cap is still refused (a typo
#     surfaces before someone switches the passthrough back on).
#
# Runs standalone from anywhere with no arguments:
#     bash core/tests/test-tls-caps-hook.sh
# and from .github/scripts/verify-core-nginx.sh's docker-free step 0, so
# `dev.sh test-core` and the CI gate run it. Exit 0 = all cases pass.
#
# What this does NOT prove: that nginx accepts the rendered values or that
# the cap bites live -- verify-core-nginx.sh's tuned-caps render_and_test
# scenario (nginx -t + tls-sni-probe.sh) does that.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
core_dir="$(cd "$script_dir/.." && pwd)"
HOOK="$core_dir/docker/26-vault-tls-passthrough.sh"
TEMPLATE="$core_dir/docker/nginx.conf.template"

[ -f "$HOOK" ]     || { echo "missing $HOOK" >&2; exit 1; }
[ -f "$TEMPLATE" ] || { echo "missing $TEMPLATE" >&2; exit 1; }

unset VAULT_TLS_CLIENT_MAX_CONNS VAULT_TLS_MAX_CONNS VAULT_TLS_PASSTHROUGH

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

pass=0
fail=0
ok()   { echo "ok:   $1"; pass=$((pass + 1)); }
bad()  { echo "FAIL: $1: $2" >&2; fail=$((fail + 1)); }

# Directive lines (comments dropped, whitespace collapsed) of a config.
directives() { grep -v '^[[:space:]]*#' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]][[:space:]]*/ /g'; }

# run_hook <case> <client|UNSET> <total|UNSET> [switch] -> $rc, $conf, $log
# (files are numbered per case: case names may contain '/')
n_case=0
run_hook() {
    local client="$2" total="$3" switch="${4:-1}"
    n_case=$((n_case + 1))
    conf="$work/case$n_case.conf"; log="$work/case$n_case.log"
    cp "$TEMPLATE" "$conf"
    rc=0
    (
        [ "$client" = "UNSET" ] && unset VAULT_TLS_CLIENT_MAX_CONNS || export VAULT_TLS_CLIENT_MAX_CONNS="$client"
        [ "$total" = "UNSET" ] && unset VAULT_TLS_MAX_CONNS || export VAULT_TLS_MAX_CONNS="$total"
        VAULT_TLS_PASSTHROUGH="$switch" sh "$HOOK" "$conf"
    ) > "$log" 2>&1 || rc=$?
}

# expect_render <case> <client|UNSET> <total|UNSET> <want client> <want total>
expect_render() {
    local name="$1" wc="$4" wt="$5"
    run_hook "$name" "$2" "$3"
    if [ "$rc" -ne 0 ]; then
        bad "$name" "exit $rc: $(head -n1 "$log")"; return
    fi
    local nc nt all
    nc=$(directives "$conf" | grep -c -x -F "limit_conn vault_tls_client $wc;" || true)
    nt=$(directives "$conf" | grep -c -x -F "limit_conn vault_tls_total $wt;" || true)
    all=$(directives "$conf" | grep -c '^limit_conn vault_tls_' || true)
    if [ "$nc" != "1" ] || [ "$nt" != "1" ] || [ "$all" != "2" ]; then
        bad "$name" "want client $wc / total $wt, found $nc / $nt matching of $all vault_tls limit_conn lines"; return
    fi
    # Nothing but those two lines may differ from the template.
    local changed
    changed=$(diff "$TEMPLATE" "$conf" | grep -c '^[<>]' || true)
    local expect_changed=0
    [ "$wc" != "64" ] && expect_changed=$((expect_changed + 2))
    [ "$wt" != "256" ] && expect_changed=$((expect_changed + 2))
    if [ "$changed" != "$expect_changed" ]; then
        bad "$name" "$changed diff lines against the template, expected $expect_changed"; return
    fi
    if ! grep -qF "HTTPS passthrough caps: $wc per client address, $wt in total" "$log"; then
        bad "$name" "no caps log line for $wc/$wt"; return
    fi
    ok "$name: client $wc, total $wt"
}

# expect_refused <case> <client|UNSET> <total|UNSET> <fragment> [switch]
expect_refused() {
    local name="$1" fragment="$4" switch="${5:-1}"
    run_hook "$name" "$2" "$3" "$switch"
    if [ "$rc" -eq 0 ]; then
        bad "$name" "accepted client='$2' total='$3' (exit 0)"; return
    fi
    if ! grep -q '^26-vault-tls-passthrough.sh: FATAL: ' "$log"; then
        bad "$name" "exit $rc but no '26-vault-tls-passthrough.sh: FATAL:' line: $(head -n1 "$log")"; return
    fi
    if ! grep -qF -- "$fragment" "$log"; then
        bad "$name" "FATAL message does not mention '$fragment': $(head -n1 "$log")"; return
    fi
    if ! cmp -s "$TEMPLATE" "$conf"; then
        bad "$name" "refused but the config was changed"; return
    fi
    ok "$name: refused ($(grep -m1 'FATAL' "$log" | cut -c1-90)...)"
}

# --- 1. defaults -------------------------------------------------------------
expect_render "unset/unset = defaults"   UNSET UNSET 64 256
expect_render "blank/blank = defaults"   ""    ""    64 256
expect_render "explicit defaults"        64    256   64 256

# --- 2. valid tuning ---------------------------------------------------------
expect_render "lowered both (ADR-0021's 16/32)" 16 32 16 32
expect_render "client only"              128   ""    128 256
expect_render "total only"               ""    400   64 400
expect_render "range floor 1/1"          1     1     1 1
expect_render "client ceiling 256/256"   256   256   256 256
expect_render "client = total"           100   100   100 100

# --- 3. refusals -------------------------------------------------------------
expect_refused "client 0"                0     ""    "VAULT_TLS_CLIENT_MAX_CONNS='0'"
expect_refused "total 0"                 ""    0     "VAULT_TLS_MAX_CONNS='0'"
expect_refused "client leading zero"     064   ""    "leading zeros"
expect_refused "client 257"              257   400   "VAULT_TLS_CLIENT_MAX_CONNS=257 is outside 1..256"
expect_refused "total 401"               ""    401   "VAULT_TLS_MAX_CONNS=401 is outside 1..400"
expect_refused "total 4 digits"          ""    1000  "is outside 1..400"
expect_refused "client non-numeric"      abc   ""    "is not a whole number"
expect_refused "client negative"         -5    ""    "is not a whole number"
expect_refused "client with blank"       " 64" ""    "is not a whole number"
expect_refused "total with unit"         ""    256k  "is not a whole number"
expect_refused "client with plus sign"   "+64" ""    "is not a whole number"
expect_refused "client trailing blank"   "64 " ""    "is not a whole number"
expect_refused "total exponent form"     ""    1e2   "is not a whole number"
expect_refused "client injection"        "64;listen 8443" "" "is not a whole number"
expect_refused "client above total"      128   64    "is above VAULT_TLS_MAX_CONNS=64"
expect_refused "client 300 (above its own ceiling)" 300 "" "is outside 1..256"
expect_refused "client above default total (in range)" 200 100 "is above"
expect_refused "invalid cap with passthrough OFF" abc "" "is not a whole number" 0

# --- 4. passthrough OFF with valid caps: block removed, nothing rendered ------
run_hook "off with caps" 32 128 0
if [ "$rc" -eq 0 ] && [ "$(directives "$conf" | grep -c 'vault_tls' || true)" = "0" ]; then
    ok "off with caps: stream block removed, no cap line left"
else
    bad "off with caps" "exit $rc, $(directives "$conf" | grep -c 'vault_tls' || true) vault_tls line(s)"
fi

# --- 5. the template lost its default line: refused, not silently default ----
cp "$TEMPLATE" "$work/tampered.conf"
sed -i 's/limit_conn vault_tls_client 64;/limit_conn vault_tls_client 63;/' "$work/tampered.conf"
rc=0
VAULT_TLS_CLIENT_MAX_CONNS=32 sh "$HOOK" "$work/tampered.conf" > "$work/tampered.log" 2>&1 || rc=$?
if [ "$rc" -ne 0 ] && grep -qF "expected exactly one 'limit_conn vault_tls_client 64;'" "$work/tampered.log"; then
    ok "template without the default line: refused"
else
    bad "template without the default line" "exit $rc: $(head -n1 "$work/tampered.log")"
fi

echo "test-tls-caps-hook: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
