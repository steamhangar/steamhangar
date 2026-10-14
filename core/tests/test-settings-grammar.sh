#!/usr/bin/env bash
# Docker-free runner of the shared D16 grammar table
# core/tests/fixtures/settings-grammar.tsv against the REAL vault-core hooks
# (D16c). The same table drives vault-api's Python twins
# (api/tests/test_d16b_core_grammar.py), so the shell and Python grammars are
# pinned to ONE set of rows (LEARNINGS "Twin config files need twin pins").
#
# Per row (key, raw value, verdict, normalised value):
#   unset    -- the boot hook 15-vault-settings.envsh treats the value as "not
#               set" (vs_blank): env/file fall through to the default.
#   ok       -- the hook that renders the key accepts it, and its render shows
#               the normalised value (27: bucket 1 / no cap / the window
#               comment; 28: the group, legacy map or cap line; 26: the
#               limit_conn line; 40 --grammar; 45 --grammar's output).
#   refused  -- that hook exits non-zero with a FATAL line.
# VAULT_SCHEDULE_WINDOW rows are vault-api's own key (it reaches vault-core
# only as VAULT_UPSTREAM_RATE_WINDOW) and are skipped here.
#
#     bash core/tests/test-settings-grammar.sh
#
# Run by .github/scripts/verify-core-nginx.sh (dev.sh test-core, CI).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
core_dir="$(cd "$script_dir/.." && pwd)"
docker_dir="$core_dir/docker"
table="$script_dir/fixtures/settings-grammar.tsv"
[ -f "$table" ] || { echo "missing $table" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

pass=0
fail=0
ok()  { pass=$((pass + 1)); }
bad() { echo "FAIL: $1" >&2; fail=$((fail + 1)); }

# run_hook <hook> <args...> with a clean environment plus the given VAR=VALUE
# assignments (before a literal --). Output in $work/out, exit code in $rc.
run_hook() {
    local -a envs=()
    while [ "$1" != "--" ]; do envs+=("$1"); shift; done
    shift
    rc=0
    env -i PATH="$PATH" "${envs[@]}" sh "$@" > "$work/out" 2>&1 || rc=$?
}

# is_blank <value>: "not set" the way vault-core's boot hook treats an env or
# file value (vs_blank in core/docker/vault-settings-lib.sh, pinned there by
# core/tests/test-settings-lib.sh): empty, or only the ASCII whitespace set
# 27-'s strip() removes. Kept here so this runner needs nothing but the hooks.
is_blank() {
    local ws
    ws=$(printf ' \t\n\v\f\r\034\035\036\037X')
    ws=${ws%X}
    case "$1" in
        *[!$ws]*) return 1 ;;
    esac
    return 0
}

# check_row <key> <raw> <verdict> <norm>
check_row() {
    local key="$1" raw="$2" verdict="$3" norm="$4" label
    label="$key='$raw' ($verdict${norm:+ $norm})"
    if [ "$verdict" = unset ]; then
        if is_blank "$raw"; then ok; else bad "$label: 15- does not treat it as unset"; fi
        return
    fi
    local conf="$work/conf"
    rm -f "$conf"/* 2>/dev/null || true
    mkdir -p "$conf"
    case "$key" in
        VAULT_UPSTREAM_RATE)
            run_hook "VAULT_UPSTREAM_RATE=$raw" VAULT_UPSTREAM_RATE_WINDOW= -- "$docker_dir/27-vault-upstream-rate.sh" "$conf/rate.conf"
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                if [ "$norm" = 0 ]; then
                    grep -qx '    default 0;' "$conf/rate.conf" && ! grep -q connections_ "$conf/rate.conf" \
                        && { ok; return; }
                else
                    grep -qx "    1 $norm;" "$conf/rate.conf" && { ok; return; }
                fi
                bad "$label: rendered, but not as $norm"; return
            fi ;;
        VAULT_UPSTREAM_RATE_WINDOW)
            run_hook VAULT_UPSTREAM_RATE=800k "VAULT_UPSTREAM_RATE_WINDOW=$raw" -- "$docker_dir/27-vault-upstream-rate.sh" "$conf/rate.conf"
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                if [ "$norm" = off ]; then
                    grep -q '^# Cap 819200 bytes/s aggregate, around the clock' "$conf/rate.conf" && { ok; return; }
                else
                    grep -q "^# $norm (container local time" "$conf/rate.conf" && { ok; return; }
                fi
                bad "$label: rendered, but not as $norm"; return
            fi ;;
        VAULT_UPSTREAM_EDGE)
            run_hook "VAULT_UPSTREAM_EDGE=$raw" -- "$docker_dir/28-vault-upstream-pool.sh" "$conf/pool.conf"
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                if [ "$norm" = off ]; then
                    # shellcheck disable=SC2016 # a literal nginx variable
                    grep -qxF '    default $vault_upstream_host;' "$conf/pool.conf" && ! grep -q '^upstream ' "$conf/pool.conf" \
                        && { ok; return; }
                else
                    grep -qx "upstream $norm {" "$conf/pool.conf" && grep -qx "    default $norm;" "$conf/pool.conf" \
                        && { ok; return; }
                fi
                bad "$label: rendered, but not as $norm"; return
            fi ;;
        VAULT_UPSTREAM_POOL_HOSTS)
            run_hook VAULT_UPSTREAM_EDGE=off "VAULT_UPSTREAM_POOL_HOSTS=$raw" -- "$docker_dir/28-vault-upstream-pool.sh" "$conf/pool.conf"
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                local got
                got=$(sed -n 's/^upstream \([^ ]*\) {$/\1/p' "$conf/pool.conf" | tr '\n' ' ' | sed 's/ $//')
                if [ "$norm" = off ]; then
                    [ -z "$got" ] && { ok; return; }
                else
                    [ "$got" = "$norm" ] && { ok; return; }
                fi
                bad "$label: rendered groups '$got', expected $norm"; return
            fi ;;
        VAULT_UPSTREAM_MAX_CONNS)
            run_hook VAULT_UPSTREAM_EDGE=off VAULT_PREFILL_MAX_THREADS=1 "VAULT_UPSTREAM_MAX_CONNS=$raw" -- "$docker_dir/28-vault-upstream-pool.sh" "$conf/pool.conf"
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                grep -qx "limit_conn vault_upstream_total $norm;" "$conf/vault-upstream-cap.conf" && { ok; return; }
                bad "$label: rendered, but not as $norm"; return
            fi ;;
        VAULT_TLS_CLIENT_MAX_CONNS|VAULT_TLS_MAX_CONNS)
            cp "$docker_dir/nginx.conf.template" "$conf/nginx.conf"
            run_hook VAULT_TLS_PASSTHROUGH=1 "$key=$raw" -- "$docker_dir/26-vault-tls-passthrough.sh" "$conf/nginx.conf"
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                local zone=vault_tls_client
                [ "$key" = VAULT_TLS_CLIENT_MAX_CONNS ] || zone=vault_tls_total
                grep -qE "^[[:space:]]*limit_conn ${zone}[[:space:]]+${norm};$" "$conf/nginx.conf" && { ok; return; }
                bad "$label: rendered, but not as $norm"; return
            fi ;;
        VAULT_RESOLVER)
            run_hook "VAULT_RESOLVER=$raw" -- "$docker_dir/40-vault-preflight.sh" --grammar
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                grep -qxF "40-vault-preflight.sh: VAULT_RESOLVER grammar OK: $norm" "$work/out" && { ok; return; }
                bad "$label: accepted, but not as $norm"; return
            fi ;;
        VAULT_EVENT_LOG_ROTATE_BYTES)
            run_hook "VAULT_EVENT_LOG_ROTATE_BYTES=$raw" -- "$docker_dir/45-vault-eventlog-rotate.sh" --grammar
            if [ "$verdict" = ok ] && [ "$rc" = 0 ]; then
                [ "$(cat "$work/out")" = "$norm" ] && { ok; return; }
                bad "$label: accepted as '$(cat "$work/out")'"; return
            fi ;;
        *)
            bad "$label: no hook mapped for $key (extend this runner)"; return ;;
    esac
    if [ "$verdict" = refused ]; then
        if [ "$rc" != 0 ] && grep -q 'FATAL' "$work/out"; then ok; return; fi
        bad "$label: not refused (rc=$rc): $(head -c 300 "$work/out" | tr '\n' ' ')"
        return
    fi
    bad "$label: refused (rc=$rc): $(grep -m1 FATAL "$work/out" | head -c 300)"
}

rows=0
keys_seen=" "
while IFS=$'\t' read -r key raw verdict norm || [ -n "$key" ]; do
    case "$key" in ''|'#'*) continue ;; esac
    [ "$key" != VAULT_SCHEDULE_WINDOW ] || continue
    [ "$raw" != "<blank>" ] || raw=""
    [ "$norm" != "-" ] || norm=""
    rows=$((rows + 1))
    case "$keys_seen" in *" $key "*) : ;; *) keys_seen="$keys_seen$key " ;; esac
    check_row "$key" "$raw" "$verdict" "$norm"
done < "$table"

# Every core key of vault-api's settings file allowlist (FILE_KEYS in
# api/vault_api/core_settings.py) has rows: a key without rows would be a
# twin without a pin.
api_keys=$(awk '/^FILE_KEYS = \(/ { f = 1; next } f && /^\)/ { exit } f { gsub(/[ ",]/, ""); if ($0 != "") print }' \
    "$core_dir/../api/vault_api/core_settings.py")
[ -n "$api_keys" ] || bad "could not read FILE_KEYS from api/vault_api/core_settings.py"
for k in $api_keys; do
    [ "$k" = VAULT_PREFILL_MAX_THREADS ] && continue   # vault-api's own key, its floor check is in 28's tests
    case "$keys_seen" in
        *" $k "*) ok ;;
        *) bad "settings key $k has no rows in $table" ;;
    esac
done

echo "test-settings-grammar: $rows table rows, $pass checks passed, $fail failed"
[ "$fail" = 0 ] && [ "$rows" -ge 90 ]
