#!/usr/bin/env bash
# Docker-free test of core/docker/31-vault-debug-throughput.sh (WP
# THROUGHPUT-DEBUG-1). Runs the real hook under `sh` and asserts:
#   - OFF (unset, empty, 0, false, off, no): both marker lines are deleted, no
#     vault_tp directive survives, nothing is created on disk and a stale
#     debug directory is left alone;
#   - ON in render-only mode: markers stripped, both directives kept, limits
#     validated;
#   - anything else for the flag, and out-of-range / non-numeric limits, is
#     refused with a FATAL line naming the variable;
#   - --prune deletes by age and by total size, oldest first, and ONLY files
#     named throughput.<epoch>.log (the current file and strangers stay).
# Not covered here (needs a real nginx and /vault): the directory creation,
# the startup line, rotation -- verify-core-nginx.sh runs those live.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$(cd "$script_dir/.." && pwd)/docker/31-vault-debug-throughput.sh"
[ -f "$HOOK" ] || { echo "missing $HOOK" >&2; exit 1; }
unset VAULT_DEBUG_THROUGHPUT VAULT_DEBUG_THROUGHPUT_MAX_MB VAULT_DEBUG_THROUGHPUT_MAX_DAYS VAULT_DEBUG_NOW

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pass=0; fail=0
ok()  { echo "ok:   $1"; pass=$((pass + 1)); }
bad() { echo "FAIL: $1"; fail=$((fail + 1)); }

mkconf() {
    cat > "$1" <<'CONF'
http {
    location /depot/ {
        access_log /dev/stdout vault;
        access_log /vault/logs/throughput-debug/throughput.log vault_tp buffer=64k flush=5s; # VAULT_DEBUG_TP_LINE
    }
    location @miss {
        access_log /dev/stdout vault;
        access_log /vault/logs/throughput-debug/throughput.log vault_tp buffer=64k flush=5s; # VAULT_DEBUG_TP_LINE
    }
}
CONF
}

# off <value-label> <env assignment or ''>
for v in "" 0 false off no; do
    conf="$work/off.conf"; mkconf "$conf"
    out=$(env VAULT_DEBUG_THROUGHPUT="$v" sh "$HOOK" "$conf" 2>&1) || { bad "off '$v' exited non-zero: $out"; continue; }
    if [ "$(grep -c vault_tp "$conf" || true)" = 0 ] && [ "$(grep -c VAULT_DEBUG_TP_LINE "$conf" || true)" = 0 ] \
        && [ "$(grep -c 'access_log /dev/stdout vault;' "$conf")" = 2 ]; then
        ok "off '$v': marker lines removed, the other access_log lines untouched"
    else bad "off '$v': unexpected config: $(cat "$conf")"; fi
done
conf="$work/unset.conf"; mkconf "$conf"
sh "$HOOK" "$conf" >/dev/null 2>&1 && [ "$(grep -c vault_tp "$conf" || true)" = 0 ] \
    && ok "flag unset behaves as off" || bad "flag unset did not behave as off"

# off + stale directory: untouched (the hook has no path to it when off).
stale="$work/stale"; mkdir "$stale"; echo old > "$stale/throughput.1.log"
conf="$work/stale.conf"; mkconf "$conf"
VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$conf" >/dev/null 2>&1
[ -f "$stale/throughput.1.log" ] && ok "off: stale debug files are not touched" || bad "off: stale file vanished"

# on, render only
for v in 1 true on yes; do
    conf="$work/on.conf"; mkconf "$conf"
    if out=$(VAULT_DEBUG_THROUGHPUT="$v" sh "$HOOK" "$conf" 2>&1) \
        && [ "$(grep -c 'access_log .*vault_tp' "$conf")" = 2 ] && [ "$(grep -c VAULT_DEBUG_TP_LINE "$conf" || true)" = 0 ]; then
        ok "on '$v' (render only): 2 directives kept, markers stripped"
    else bad "on '$v': $out"; fi
done

# refusals
refuse() {
    local label="$1" want="$2"; shift 2
    conf="$work/ref.conf"; mkconf "$conf"
    if out=$(env "$@" sh "$HOOK" "$conf" 2>&1); then bad "$label: accepted"
    elif printf '%s' "$out" | grep -q "$want"; then ok "$label: refused naming $want"
    else bad "$label: refused, but without $want: $out"; fi
}
refuse "flag 'ON'" VAULT_DEBUG_THROUGHPUT VAULT_DEBUG_THROUGHPUT=ON
refuse "flag 'maybe'" VAULT_DEBUG_THROUGHPUT VAULT_DEBUG_THROUGHPUT=maybe
for bv in 0 -1 abc 1.5 2000 07 99999; do
    refuse "MAX_MB=$bv" VAULT_DEBUG_THROUGHPUT_MAX_MB VAULT_DEBUG_THROUGHPUT=on VAULT_DEBUG_THROUGHPUT_MAX_MB="$bv"
done
for bv in 0 31 x; do
    refuse "MAX_DAYS=$bv" VAULT_DEBUG_THROUGHPUT_MAX_DAYS VAULT_DEBUG_THROUGHPUT=on VAULT_DEBUG_THROUGHPUT_MAX_DAYS="$bv"
done
# limits are only validated when ON
conf="$work/offlim.conf"; mkconf "$conf"
VAULT_DEBUG_THROUGHPUT=off VAULT_DEBUG_THROUGHPUT_MAX_MB=abc sh "$HOOK" "$conf" >/dev/null 2>&1 \
    && ok "off ignores limit variables" || bad "off refused on a limit variable"

# a config with a missing/duplicated directive is refused when ON
printf 'http {\n access_log x vault_tp; # VAULT_DEBUG_TP_LINE\n}\n' > "$work/one.conf"
VAULT_DEBUG_THROUGHPUT=on sh "$HOOK" "$work/one.conf" >/dev/null 2>&1 \
    && bad "on accepted a config with one directive" || ok "on refuses a config without exactly 2 directives"
# off with a stray unmarked vault_tp directive is refused (half-disabled)
printf 'http {\n access_log x vault_tp;\n}\n' > "$work/stray.conf"
VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$work/stray.conf" >/dev/null 2>&1 \
    && bad "off accepted a stray vault_tp directive" || ok "off refuses a stray vault_tp directive"

# --prune: age
d="$work/prune-age"; mkdir "$d"
now=2000000000
echo cur > "$d/throughput.log"; echo stranger > "$d/notes.txt"; echo x > "$d/throughput.abc.log"
echo new > "$d/throughput.$((now - 3600)).log"
echo old > "$d/throughput.$((now - 4 * 86400)).log"
VAULT_DEBUG_NOW=$now VAULT_DEBUG_THROUGHPUT_MAX_DAYS=3 sh "$HOOK" --prune "$d" >/dev/null
[ ! -e "$d/throughput.$((now - 4 * 86400)).log" ] && [ -e "$d/throughput.$((now - 3600)).log" ] \
    && [ -e "$d/throughput.log" ] && [ -e "$d/notes.txt" ] && [ -e "$d/throughput.abc.log" ] \
    && ok "prune by age: only the old rotation is deleted; current file and strangers stay" \
    || bad "prune by age: $(ls "$d")"

# --prune: size (MAX_MB=1, three 600 KB rotations -> oldest go first)
d="$work/prune-size"; mkdir "$d"
for e in 100 200 300; do head -c 614400 /dev/zero > "$d/throughput.$((now - 3600 + e)).log"; done
VAULT_DEBUG_NOW=$now VAULT_DEBUG_THROUGHPUT_MAX_MB=1 sh "$HOOK" --prune "$d" >/dev/null
left=$(ls "$d" | wc -l | tr -d ' ')
[ "$left" = 1 ] && [ -e "$d/throughput.$((now - 3600 + 300)).log" ] \
    && ok "prune by size: oldest rotations deleted until the directory fits" || bad "prune by size left: $(ls "$d")"

# --prune: the current file counts toward the size but is never deleted
d="$work/prune-cur"; mkdir "$d"
head -c 2097152 /dev/zero > "$d/throughput.log"
echo r > "$d/throughput.$((now - 10)).log"
VAULT_DEBUG_NOW=$now VAULT_DEBUG_THROUGHPUT_MAX_MB=1 sh "$HOOK" --prune "$d" >/dev/null
[ -e "$d/throughput.log" ] && [ ! -e "$d/throughput.$((now - 10)).log" ] \
    && ok "prune: oversized current file stays, rotations go" || bad "prune current: $(ls "$d")"

# --prune: symlinks are never followed or deleted
d="$work/prune-link"; mkdir "$d"; echo keep > "$work/target"
ln -s "$work/target" "$d/throughput.1.log"
VAULT_DEBUG_NOW=$now sh "$HOOK" --prune "$d" >/dev/null
[ -L "$d/throughput.1.log" ] && [ -f "$work/target" ] && ok "prune leaves symlinks alone" || bad "prune touched a symlink"

echo "debug-throughput hook: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
