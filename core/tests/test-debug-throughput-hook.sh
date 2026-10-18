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
#   - WP TUNE-1a: the heartbeat line (vault_tp_hb) is kept/stripped like the
#     other two; VAULT_NGINX_AIO / VAULT_NGINX_WORKERS are validated and work
#     with the debug flag off;
#   - WP AIO-DEFAULT-1b (ADR-0030): unset/empty VAULT_NGINX_AIO renders
#     `aio threads;` + `sendfile off;` (the default since rc21); `off` is the
#     opt-out and renders byte-identically to the pre-rc21 default (template
#     with the marker lines deleted, and main's own `off` render);
#   - --prune deletes by age and by total size, oldest first, and ONLY files
#     named throughput.<epoch>.log (the current file and strangers stay).
# Not covered here (needs a real nginx and /vault): the directory creation,
# the startup line, rotation -- verify-core-nginx.sh runs those live.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$(cd "$script_dir/.." && pwd)/docker/31-vault-debug-throughput.sh"
[ -f "$HOOK" ] || { echo "missing $HOOK" >&2; exit 1; }
unset VAULT_NGINX_AIO VAULT_NGINX_WORKERS VAULT_DEBUG_THROUGHPUT VAULT_DEBUG_THROUGHPUT_MAX_MB VAULT_DEBUG_THROUGHPUT_MAX_DAYS VAULT_DEBUG_NOW

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pass=0; fail=0
ok()  { echo "ok:   $1"; pass=$((pass + 1)); }
bad() { echo "FAIL: $1"; fail=$((fail + 1)); }

mkconf() {
    cat > "$1" <<'CONF'
http {
    sendfile      on;
    location /depot/ {
        access_log /dev/stdout vault;
        access_log /vault/logs/throughput-debug/throughput.log vault_tp buffer=64k flush=5s; # VAULT_DEBUG_TP_LINE
    }
    location @miss {
        access_log /dev/stdout vault;
        access_log /vault/logs/throughput-debug/throughput.log vault_tp buffer=64k flush=5s; # VAULT_DEBUG_TP_LINE
    }
    location = /lancache-heartbeat {
        access_log /dev/stdout vault; # VAULT_DEBUG_TP_LINE
        access_log /vault/logs/throughput-debug/throughput.log vault_tp_hb buffer=64k flush=5s; # VAULT_DEBUG_TP_LINE
        return 200;
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
        && [ "$(grep -c 'access_log .*vault_tp' "$conf")" = 3 ] && [ "$(grep -c 'access_log .*vault_tp_hb' "$conf")" = 1 ] \
        && [ "$(grep -c 'access_log /dev/stdout vault;' "$conf")" = 3 ] && [ "$(grep -c VAULT_DEBUG_TP_LINE "$conf" || true)" = 0 ]; then
        ok "on '$v' (render only): 3 directives (incl. heartbeat + restated vault log) kept, markers stripped"
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
VAULT_NGINX_AIO=off VAULT_DEBUG_THROUGHPUT=on sh "$HOOK" "$work/one.conf" >/dev/null 2>&1 \
    && bad "on accepted a config with one directive" || ok "on refuses a config without exactly 2 directives"
# off with a stray unmarked vault_tp directive is refused (half-disabled)
printf 'http {\n access_log x vault_tp;\n}\n' > "$work/stray.conf"
VAULT_NGINX_AIO=off VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$work/stray.conf" >/dev/null 2>&1 \
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


# --- WP TUNE-1a: heartbeat guard and the opt-in tuning switches -------------------
# ON with the heartbeat line missing is refused (3 directives are expected).
mkconf "$work/nohb.conf"; grep -v 'vault_tp_hb' "$work/nohb.conf" > "$work/nohb2.conf"
VAULT_NGINX_AIO=off VAULT_DEBUG_THROUGHPUT=on sh "$HOOK" "$work/nohb2.conf" >/dev/null 2>&1 \
    && bad "on accepted a config without the heartbeat directive" || ok "on refuses a config without the vault_tp_hb directive"

tconf() { cat > "$1" <<'CONF'
worker_processes  1;
events {
    worker_connections  1024;
}
http {
    sendfile      on;
    location / {
        access_log /dev/stdout vault;
    }
}
CONF
}
# AIO-DEFAULT-1b: `off` (the opt-out) leaves the file untouched, byte for byte
tconf "$work/t0.conf"; cp "$work/t0.conf" "$work/t0.ref"
out=$(VAULT_NGINX_AIO=off VAULT_NGINX_WORKERS= sh "$HOOK" "$work/t0.conf" 2>&1)
if cmp -s "$work/t0.conf" "$work/t0.ref"; then ok "tuning 'off': config byte-identical (aio opted out, workers unset)"; else bad "tuning 'off': config changed: $out"; fi
printf '%s' "$out" | grep -q "VAULT_NGINX_AIO=off: no aio" && ok "tuning 'off': the opt-out is logged" || bad "tuning 'off': no opt-out log line: $out"
# unset / empty / threads: aio threads right after 'http {', sendfile off,
# workers untouched -- the default since rc21, also with the debug flag OFF.
# All three renders are byte-identical to each other.
for v in unset "" threads; do
    tconf "$work/t1.conf"
    if [ "$v" = unset ]; then out=$(VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$work/t1.conf" 2>&1)
    else out=$(VAULT_NGINX_AIO="$v" VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$work/t1.conf" 2>&1); fi
    if [ "$(grep -c '^    aio threads;$' "$work/t1.conf")" = 1 ] \
        && awk '/^http \{$/ { getline; if ($0 == "    aio threads;") ok_=1 } END { exit !ok_ }' "$work/t1.conf" \
        && [ "$(grep -c '^    sendfile      off;$' "$work/t1.conf")" = 1 ] && ! grep -q '^    sendfile      on;$' "$work/t1.conf" \
        && [ "$(grep -c '^worker_processes  1;$' "$work/t1.conf")" = 1 ]; then
        ok "AIO '$v' (debug flag off): aio threads right after 'http {', sendfile switched off, workers untouched"
    else bad "AIO '$v', debug off: $out"; fi
    [ "$v" = unset ] && cp "$work/t1.conf" "$work/t1.default"
    cmp -s "$work/t1.conf" "$work/t1.default" && ok "AIO '$v': same render as unset" || bad "AIO '$v': render differs from unset"
done
# the default render is exactly the opt-out render plus the two AIO edits
awk '{ if ($0 == "    sendfile      on;") print "    sendfile      off;"; else print } /^http \{$/ { print "    aio threads;" }' "$work/t0.ref" > "$work/t1.expect"
cmp -s "$work/t1.default" "$work/t1.expect" && ok "default render = opt-out render + 'aio threads;' + 'sendfile off;', nothing else" \
    || bad "default render has other changes: $(diff "$work/t1.expect" "$work/t1.default" | head -5)"
# AIO threads plus debug ON on a full config: both land
mkconf "$work/t2.conf"; sed -i '1i worker_processes  1;' "$work/t2.conf"
VAULT_NGINX_AIO=threads VAULT_DEBUG_THROUGHPUT=on sh "$HOOK" "$work/t2.conf" >/dev/null 2>&1 \
    && [ "$(grep -c '^    aio threads;$' "$work/t2.conf")" = 1 ] && [ "$(grep -c 'vault_tp_hb' "$work/t2.conf")" = 1 ] \
    && ok "AIO=threads and debug on together" || bad "AIO=threads + debug on: $(cat "$work/t2.conf")"
# workers
for w in auto 1 2 8 16; do
    tconf "$work/t3.conf"
    if out=$(VAULT_NGINX_AIO=off VAULT_NGINX_WORKERS="$w" sh "$HOOK" "$work/t3.conf" 2>&1) \
        && [ "$(grep -c "^worker_processes  $w;\$" "$work/t3.conf")" = 1 ] && [ "$(grep -c '^worker_processes' "$work/t3.conf")" = 1 ] \
        && ! grep -q aio "$work/t3.conf"; then
        ok "WORKERS=$w: worker_processes rendered, no aio"
    else bad "WORKERS=$w: $out"; fi
done
tconf "$work/t4.conf"
out=$(VAULT_NGINX_AIO=off VAULT_NGINX_WORKERS=4 sh "$HOOK" "$work/t4.conf" 2>&1)
printf '%s' "$out" | grep -q 'PER WORKER' && ok "WORKERS=4 logs the per-worker keepalive note" || bad "WORKERS=4: no per-worker note: $out"
# refusals
refuse_t() {
    local label="$1" want="$2"; shift 2
    tconf "$work/tr.conf"
    if out=$(env "$@" sh "$HOOK" "$work/tr.conf" 2>&1); then bad "$label: accepted"
    elif printf '%s' "$out" | grep -q "FATAL.*$want"; then ok "$label: refused naming $want"
    else bad "$label: refused without $want: $out"; fi
}
for v in on 1 true Threads thread yes OFF false no 0 none " "; do refuse_t "AIO='$v'" VAULT_NGINX_AIO VAULT_NGINX_AIO="$v"; done
for v in 0 17 100 -1 abc 1.5 02 AUTO Auto "2 3"; do refuse_t "WORKERS='$v'" VAULT_NGINX_WORKERS VAULT_NGINX_WORKERS="$v"; done
# AIO=threads and the default (unset) refuse when the sendfile line is missing or duplicated
printf 'worker_processes  1;\nhttp {\n    gzip off;\n}\n' > "$work/ns.conf"
if out=$(VAULT_NGINX_AIO=threads sh "$HOOK" "$work/ns.conf" 2>&1); then bad "AIO=threads without a sendfile line was accepted"
else case "$out" in *sendfile*) ok "AIO=threads refuses when the sendfile line is not found exactly once" ;; *) bad "refused without naming sendfile: $out" ;; esac; fi
if out=$(sh "$HOOK" "$work/ns.conf" 2>&1); then bad "AIO unset (default threads) without a sendfile line was accepted"
else case "$out" in *sendfile*VAULT_NGINX_AIO=off*) ok "AIO unset refuses a missing sendfile line and names the opt-out" ;; *) bad "unset refused without naming sendfile / the opt-out: $out" ;; esac; fi
VAULT_NGINX_AIO=off sh "$HOOK" "$work/ns.conf" >/dev/null 2>&1 && ok "AIO=off does not need a sendfile line" || bad "AIO=off refused a config without sendfile"
printf 'worker_processes  1;\nhttp {\n    aio threads;\n    sendfile      on;\n}\n' > "$work/da.conf"
if out=$(sh "$HOOK" "$work/da.conf" 2>&1); then bad "AIO default accepted a config that already has an aio line"
else ok "AIO default refuses a second aio directive"; fi
printf 'worker_processes  1;\nhttp {\n    sendfile      on;\n    sendfile      on;\n}\n' > "$work/ds.conf"
if out=$(VAULT_NGINX_AIO=threads sh "$HOOK" "$work/ds.conf" 2>&1); then bad "AIO=threads with two sendfile lines was accepted"
else ok "AIO=threads refuses a duplicated sendfile line"; fi
# the shipped template has exactly one worker_processes line and one http block, one aio is added
tmplt="$(cd "$script_dir/.." && pwd)/docker/nginx.conf.template"
cp "$tmplt" "$work/real-tune.conf"
VAULT_NGINX_AIO=threads VAULT_NGINX_WORKERS=auto VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$work/real-tune.conf" >/dev/null 2>&1 \
    && [ "$(grep -c '^worker_processes  auto;$' "$work/real-tune.conf")" = 1 ] && [ "$(grep -cE '^[[:space:]]*aio threads;$' "$work/real-tune.conf")" = 1 ] \
    && [ "$(grep -c '^    sendfile      off;$' "$work/real-tune.conf")" = 1 ] && ! grep -q '^    sendfile      on;$' "$work/real-tune.conf" \
    && [ "$(diff "$tmplt" "$work/real-tune.conf" | grep -c '^[<>]' || true)" -ge 4 ] \
    && ok "real template: aio threads + sendfile off + worker_processes auto render" || bad "real template tuning render failed"


# --- the REAL template: OFF must leave nothing behind ---------------------------
tmpl="$(cd "$script_dir/.." && pwd)/docker/nginx.conf.template"
cp "$tmpl" "$work/real-off.conf"
VAULT_NGINX_AIO=off VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$work/real-off.conf" >/dev/null 2>&1 \
    && ok "real template, off: hook succeeds" || bad "real template, off: hook failed"
if [ "$(grep -cE 'vault_tp|throughput|VAULT_DEBUG' "$work/real-off.conf" || true)" = 0 ]; then
    ok "real template, off: no vault_tp / throughput / VAULT_DEBUG text is left"
else bad "real template, off: leftovers: $(grep -nE 'vault_tp|throughput|VAULT_DEBUG' "$work/real-off.conf" | head -3)"; fi
# AIO-DEFAULT-1b: the opt-out is the pre-rc21 default render, i.e. the
# template with only the marker lines deleted (git-independent check) ...
sed "/# VAULT_DEBUG_TP_LINE\$/d" "$tmpl" > "$work/real-premark.conf"
cmp -s "$work/real-premark.conf" "$work/real-off.conf" && ok "real template, AIO=off: template minus the marker lines, nothing else (pre-rc21 render)" \
    || bad "real template, AIO=off: more than the marker lines changed: $(diff "$work/real-premark.conf" "$work/real-off.conf" | head -5)"
# ... and the default render is that plus exactly the two AIO edits.
cp "$tmpl" "$work/real-default.conf"
VAULT_DEBUG_THROUGHPUT=off sh "$HOOK" "$work/real-default.conf" >/dev/null 2>&1 || bad "real template, default: hook failed"
awk '{ if ($0 == "    sendfile      on;") print "    sendfile      off;"; else print } /^http \{$/ { print "    aio threads;" }' "$work/real-off.conf" > "$work/real-default.expect"
cmp -s "$work/real-default.expect" "$work/real-default.conf" && ok "real template, AIO unset: opt-out render + 'aio threads;' + 'sendfile off;', nothing else" \
    || bad "real template, AIO unset: unexpected render: $(diff "$work/real-default.expect" "$work/real-default.conf" | head -5)"
# Byte identity with main: render main's template through main's hook with the
# flag off and VAULT_NGINX_AIO=off (before rc21 `off` and unset rendered the
# same; since rc21 `off` is the opt-out), and compare with this tree's
# template + hook, same values. main's own render is the reference, so the
# check stays meaningful after this lands on main.
repo_root="$(cd "$script_dir/../.." && pwd)"
if git -C "$repo_root" show main:core/docker/nginx.conf.template > "$work/main-template.conf" 2>/dev/null \
    && git -C "$repo_root" show main:core/docker/31-vault-debug-throughput.sh > "$work/main-hook.sh" 2>/dev/null; then
    VAULT_NGINX_AIO=off VAULT_DEBUG_THROUGHPUT=off sh "$work/main-hook.sh" "$work/main-template.conf" >/dev/null 2>&1 \
        || bad "main's hook failed on main's template"
    if cmp -s "$work/main-template.conf" "$work/real-off.conf"; then
        ok "real template, debug off, AIO=off, workers unset: byte-identical to main's off render"
    else bad "real template, off: differs from main's off render: $(diff "$work/main-template.conf" "$work/real-off.conf" | head -5)"; fi
else
    echo "skip: no git checkout with a main branch; byte-identity vs main not checked"
fi
# ON: markers gone, log_format and both directives present
cp "$tmpl" "$work/real-on.conf"
VAULT_DEBUG_THROUGHPUT=on sh "$HOOK" "$work/real-on.conf" >/dev/null 2>&1 \
    && [ "$(grep -c 'log_format vault_tp escape' "$work/real-on.conf")" = 1 ] \
    && [ "$(grep -c 'VAULT_DEBUG_TP_LINE' "$work/real-on.conf" || true)" = 0 ] \
    && [ "$(grep -cE '^[[:space:]]*access_log .*vault_tp' "$work/real-on.conf")" = 3 ] \
    && [ "$(grep -c 'log_format vault_tp_hb' "$work/real-on.conf")" = 1 ] \
    && [ "$(awk '/^ *location = \/lancache-heartbeat \{/,/return 200;/' "$work/real-on.conf" | grep -cE '^[[:space:]]*access_log ')" = 2 ] \
    && ok "real template, on: 2 log_formats + 3 directives (heartbeat location keeps the vault log), no marker" || bad "real template, on: unexpected render"

echo "debug-throughput hook: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
