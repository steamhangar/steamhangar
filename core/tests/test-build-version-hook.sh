#!/usr/bin/env bash
# Docker-free test of core/docker/29-vault-build-version.sh (WP VER-2).
# Runs the real hook under `sh` (as the container does) with a temp output
# path and asserts the file it writes, the fail-closed rendering of bad
# values, the atomic write, the symlink handling, the one refusal (the
# cache-event log naming the same file) and, with PATH stubs, the SEC-FIX-5
# rule that root writes only into a root-only directory chain.
#
#     bash core/tests/test-build-version-hook.sh
#
# Also run by .github/scripts/verify-core-nginx.sh step 0c (dev.sh
# test-core, CI). The hook inside the REAL entrypoint chain, its order and
# the boot refusal are checked there with the pinned image. Exit 0 = all
# cases pass; one line per case.
# Info-level SC2016 (literal $ in grep patterns) and SC2030/SC2031 (env set
# inside subshells on purpose) are intended here.
# shellcheck disable=SC2016,SC2030,SC2031
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$(cd "$script_dir/.." && pwd)/docker/29-vault-build-version.sh"
[ -f "$HOOK" ] || { echo "missing $HOOK" >&2; exit 1; }

work="$(mktemp -d)"
trap 'chmod -R u+w "$work" 2>/dev/null; rm -rf "$work"' EXIT INT TERM

pass=0
fail=0
ok()  { echo "ok:   $1"; pass=$((pass + 1)); }
bad() { echo "FAIL: $1: $2" >&2; fail=$((fail + 1)); }

SHA=0123456789abcdef0123456789abcdef01234567
TS='[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z'

# run_hook <out> <version|UNSET> <commit|UNSET> [event-log] -> $rc, $log
run_hook() {
    local out="$1" v="$2" c="$3" ev="${4:-}"
    log="$work/last.log"
    rc=0
    (
        unset VAULT_BUILD_VERSION VAULT_BUILD_COMMIT VAULT_EVENT_LOG
        [ "$v" = UNSET ] || export VAULT_BUILD_VERSION="$v"
        [ "$c" = UNSET ] || export VAULT_BUILD_COMMIT="$c"
        [ -z "$ev" ] || export VAULT_EVENT_LOG="$ev"
        sh "$HOOK" "$out"
    ) > "$log" 2>&1 || rc=$?
}

# expect <case> <version-in> <commit-in> <version-out> <commit-out>
expect() {
    local name="$1" vin="$2" cin="$3" vout="$4" cout="$5"
    local out="$work/$name/logs/vault-core-version.json"
    mkdir -p "$work/$name"
    run_hook "$out" "$vin" "$cin"
    local vre
    vre=$(printf '%s' "$vout" | sed 's/[.+]/\\&/g')
    local want="^\{\"component\":\"vault-core\",\"version\":\"$vre\",\"commit\":\"$cout\",\"recorded_at\":\"$TS\"\}$"
    if [ "$rc" -ne 0 ]; then
        bad "$name" "exit $rc: $(head -n1 "$log")"
    elif [ ! -f "$out" ]; then
        bad "$name" "no file written"
    elif [ "$(wc -l < "$out")" != "1" ] || ! grep -Eq "$want" "$out"; then
        bad "$name" "content: $(head -c 300 "$out")"
    elif python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$out" 2>/dev/null; then
        ok "$name: version=$vout commit=$cout"
    else
        bad "$name" "not valid JSON: $(head -c 300 "$out")"
    fi
}

# --- 1. valid values pass through ------------------------------------------
expect "release"        "0.1.0-rc9"    "$SHA"     "0.1.0-rc9"    "$SHA"
expect "dev-default"    "dev"          "unknown"  "dev"          "unknown"
expect "short-sha"      "dev-1a2b3c4"  "1a2b3c4"  "dev-1a2b3c4"  "1a2b3c4"
expect "plus-and-under" "1.0.0+build_7" "$SHA"    "1.0.0+build_7" "$SHA"
expect "max-length"     "$(printf 'a%.0s' $(seq 64))" "$SHA" "$(printf 'a%.0s' $(seq 64))" "$SHA"

# --- 2. bad values fail closed to "invalid" (never the raw value) ---------
expect "version-unset"    UNSET            "$SHA"     "invalid" "$SHA"
expect "version-blank"    ""               "$SHA"     "invalid" "$SHA"
expect "version-space"    "1.0 beta"       "$SHA"     "invalid" "$SHA"
expect "version-quote"    '1.0"x'          "$SHA"     "invalid" "$SHA"
expect "version-slash"    "1.0/x"          "$SHA"     "invalid" "$SHA"
expect "version-lead-dot" ".1.0"           "$SHA"     "invalid" "$SHA"
expect "version-lead-dash" "-x"            "$SHA"     "invalid" "$SHA"
expect "version-65-chars" "$(printf 'a%.0s' $(seq 65))" "$SHA" "invalid" "$SHA"
expect "version-newline"  $'1.0\n2'        "$SHA"     "invalid" "$SHA"
expect "version-non-ascii" "1.0é"          "$SHA"     "invalid" "$SHA"
expect "version-backslash" '1\0'           "$SHA"     "invalid" "$SHA"
expect "commit-unset"     "1.0"            UNSET      "1.0"     "unknown"
expect "commit-upper"     "1.0"            "ABCDEF1"  "1.0"     "invalid"
expect "commit-6-chars"   "1.0"            "abcdef"   "1.0"     "invalid"
expect "commit-41-chars"  "1.0"            "${SHA}a"  "1.0"     "invalid"
expect "commit-blank"     "1.0"            ""         "1.0"     "invalid"
expect "commit-newline"   "1.0"            $'abcdef1\n' "1.0"   "invalid"

# --- 3. atomic replace: the old file is replaced, no temp file remains ------
out="$work/atomic/logs/vault-core-version.json"
mkdir -p "$(dirname "$out")"
printf 'old\n' > "$out"
run_hook "$out" "2.0" "$SHA"
if [ "$rc" -eq 0 ] && grep -q '"version":"2.0"' "$out" && [ -z "$(find "$(dirname "$out")" -name '.vault-core-version.*')" ]; then
    ok "atomic: old file replaced, no temp file left behind"
else
    bad "atomic" "rc=$rc, content=$(head -c 200 "$out"), dir=$(find "$(dirname "$out")" -mindepth 1 | tr '\n' ' ')"
fi
if [ "$(stat -c %a "$out")" = "644" ]; then
    ok "atomic: the file is 0644 (vault-api, another uid, reads it)"
else
    bad "atomic" "mode $(stat -c %a "$out"), expected 644"
fi

# --- 4. a symlink squatting on the name is replaced, its target untouched ---
out="$work/symlink/logs/vault-core-version.json"
mkdir -p "$(dirname "$out")" "$work/symlink/elsewhere"
printf 'precious\n' > "$work/symlink/elsewhere/target"
ln -s "$work/symlink/elsewhere/target" "$out"
run_hook "$out" "3.0" "$SHA"
if [ "$rc" -eq 0 ] && [ ! -L "$out" ] && grep -q '"version":"3.0"' "$out" \
   && [ "$(cat "$work/symlink/elsewhere/target")" = "precious" ]; then
    ok "symlink file: replaced by a regular file, the link target is untouched"
else
    bad "symlink file" "rc=$rc link=$([ -L "$out" ] && echo yes || echo no) target=$(cat "$work/symlink/elsewhere/target")"
fi

# A symlink to a DIRECTORY on the name: mv must not move the file into it.
out="$work/symlinkdir/logs/vault-core-version.json"
mkdir -p "$(dirname "$out")" "$work/symlinkdir/elsewhere"
ln -s "$work/symlinkdir/elsewhere" "$out"
run_hook "$out" "3.1" "$SHA"
if [ "$rc" -eq 0 ] && [ -f "$out" ] && [ ! -L "$out" ] && [ -z "$(ls -A "$work/symlinkdir/elsewhere")" ]; then
    ok "symlink to a directory: replaced, nothing written into the directory"
else
    bad "symlink to a directory" "rc=$rc, elsewhere holds: $(find "$work/symlinkdir/elsewhere" -mindepth 1 | tr '\n' ' ')"
fi

# --- 5. a symlinked logs/ directory is refused, without failing the boot ----
mkdir -p "$work/linkeddir/real" "$work/linkeddir/vault"
ln -s "$work/linkeddir/real" "$work/linkeddir/vault/logs"
out="$work/linkeddir/vault/logs/vault-core-version.json"
run_hook "$out" "4.0" "$SHA"
if [ "$rc" -eq 0 ] && [ -z "$(ls -A "$work/linkeddir/real")" ] && grep -q 'is a symlink' "$log"; then
    ok "symlinked directory: nothing written through it, boot continues (exit 0, WARNING)"
else
    bad "symlinked directory" "rc=$rc, real holds: $(find "$work/linkeddir/real" -mindepth 1 | tr '\n' ' '), log: $(head -n2 "$log")"
fi

# --- 6. an unwritable directory: warning, exit 0, the stale file is removed --
if [ "$(id -u)" != "0" ]; then
    out="$work/ro/logs/vault-core-version.json"
    mkdir -p "$(dirname "$out")"
    printf '{"stale":true}\n' > "$out"
    chmod 0555 "$(dirname "$out")"
    run_hook "$out" "5.0" "$SHA"
    chmod 0755 "$(dirname "$out")"
    if [ "$rc" -eq 0 ] && grep -q 'WARNING: could not write' "$log" && grep -q stale "$out"; then
        # The directory was read-only, so the stale file could not be removed
        # either; the hook must say so instead of claiming success.
        if grep -q 'could not remove the old' "$log"; then
            ok "read-only directory: exit 0, both failures reported"
        else
            bad "read-only directory" "stale file kept without saying so: $(cat "$log")"
        fi
    else
        bad "read-only directory" "rc=$rc log: $(cat "$log")"
    fi
    # Writable directory, but the name is a directory: warn, exit 0.
    out="$work/isdir/logs/vault-core-version.json"
    mkdir -p "$out"
    run_hook "$out" "5.1" "$SHA"
    if [ "$rc" -eq 0 ] && [ -d "$out" ] && grep -q 'is a directory' "$log"; then
        ok "name taken by a directory: exit 0, WARNING, directory untouched"
    else
        bad "name taken by a directory" "rc=$rc log: $(cat "$log")"
    fi
else
    echo "skip: read-only directory cases (running as root)"
fi

# --- 7. the one refusal: VAULT_EVENT_LOG names this file -------------------
out="$work/collide/logs/vault-core-version.json"
mkdir -p "$(dirname "$out")"
printf 'event-line\n' > "$out"
for ev in "$out" "$(printf '%s' "$out" | sed 's#/logs/#//logs/#')"; do
    run_hook "$out" "6.0" "$SHA" "$ev"
    if [ "$rc" -ne 0 ] && grep -q '^29-vault-build-version.sh: FATAL: ' "$log" && [ "$(cat "$out")" = "event-line" ]; then
        ok "collision: VAULT_EVENT_LOG=$ev refused, the event log untouched"
    else
        bad "collision" "VAULT_EVENT_LOG=$ev: rc=$rc, file=$(head -c 100 "$out"), log: $(head -n1 "$log")"
    fi
done
# A different event log is fine.
run_hook "$out" "6.1" "$SHA" "$work/collide/logs/event.log"
if [ "$rc" -eq 0 ] && grep -q '"version":"6.1"' "$out"; then
    ok "no collision: a different VAULT_EVENT_LOG does not stop the hook"
else
    bad "no collision" "rc=$rc log: $(head -n1 "$log")"
fi

# --- 8. a missing logs/ directory is created --------------------------------
out="$work/fresh/vault/logs/vault-core-version.json"
mkdir -p "$work/fresh/vault"
run_hook "$out" "7.0" "$SHA"
if [ "$rc" -eq 0 ] && grep -q '"version":"7.0"' "$out"; then
    ok "missing logs/ directory: created and written (bind-mounted /vault)"
else
    bad "missing logs/ directory" "rc=$rc log: $(head -n1 "$log")"
fi

# --- 9. SEC-FIX-5: as root, only into a root-only directory chain -----------
# Structural pin: the main flow asks root_may_write before write_file, and
# root_may_write consults root_chain_bad on the output directory.
main_check=$(grep -n '^if ! root_may_write; then$' "$HOOK" | cut -d: -f1)
main_write=$(grep -n '^if write_file; then$' "$HOOK" | cut -d: -f1)
if [ -n "$main_check" ] && [ -n "$main_write" ] && [ "$main_check" -lt "$main_write" ]; then
    ok "SEC-FIX-5 structure: root_may_write gates write_file"
else
    bad "SEC-FIX-5 structure" "root_may_write ($main_check) does not precede write_file ($main_write)"
fi
rmw=$(awk '/^root_may_write\(\) \{$/ { f = 1 } f { print } f && /^}$/ { exit }' "$HOOK")
if printf '%s\n' "$rmw" | grep -q 'root_chain_bad "\$OUT_DIR"' \
   && printf '%s\n' "$rmw" | grep -q '\[ "\$(id -u)" = "0" \] || return 0'; then
    ok "SEC-FIX-5 structure: root_may_write checks the whole chain of OUT_DIR when root"
else
    bad "SEC-FIX-5 structure" "root_may_write no longer checks root_chain_bad \"\$OUT_DIR\" for root"
fi
if grep -qE '\bsu\b.*--writer|^if \[ "\$\{1:-\}" = "--writer" \]' "$HOOK"; then
    bad "SEC-FIX-5 structure" "the VER-2 --writer split is still present"
else
    ok "SEC-FIX-5 structure: no su writer left (root writes, in a root-only directory)"
fi

# Behaviour, with PATH stubs: `id -u` answers 0 (root). The work directory
# belongs to the test user, so the real chain is NOT root-only. A `stat`
# stub can make it look root-only ("0 755" for '%u %a'), except for one
# path named in STUB_BAD, to drive both branches.
stubs="$work/stubs"
mkdir -p "$stubs"
cat > "$stubs/id" <<'STUB'
#!/bin/sh
if [ "$1" = "-u" ]; then echo 0; exit 0; fi
PATH=$STUB_REAL_PATH exec id "$@"
STUB
cat > "$stubs/stat" <<'STUB'
#!/bin/sh
if [ -n "${STUB_ROOT_STAT:-}" ] && [ "$1" = "-c" ] && [ "$2" = "%u %a" ]; then
    if [ -n "${STUB_BAD:-}" ] && [ "$3" = "$STUB_BAD" ]; then echo "101 755"; else echo "0 755"; fi
    exit 0
fi
PATH=$STUB_REAL_PATH exec stat "$@"
STUB
for s in mkdir mktemp mv; do
    cat > "$stubs/$s" <<STUB
#!/bin/sh
echo "$s \$*" >> "\$STUB_LOG"
PATH=\$STUB_REAL_PATH exec $s "\$@"
STUB
done
chmod 0755 "$stubs"/*

root_run() {
    local out="$1"
    log="$work/last.log"; rc=0
    : > "$work/stub.log"
    (
        export STUB_LOG="$work/stub.log" STUB_REAL_PATH="$PATH" PATH="$stubs:$PATH"
        export VAULT_BUILD_VERSION=8.0 VAULT_BUILD_COMMIT="$SHA"
        unset VAULT_EVENT_LOG
        sh "$HOOK" "$out"
    ) > "$log" 2>&1 || rc=$?
}

# 9a. Real ownership (the test user's): root writes nothing, removes nothing.
out="$work/asroot/vault/logs/vault-core-version.json"
mkdir -p "$(dirname "$out")"
printf 'stale\n' > "$out"
root_run "$out"
if [ "$rc" -eq 0 ] && [ "$(cat "$out")" = "stale" ] && grep -q 'not a root-only directory' "$log" \
   && ! grep -qE '^(mktemp|mv) ' "$work/stub.log"; then
    ok "SEC-FIX-5 as root, directory not root-only: nothing written or removed, no mktemp/mv, WARNING"
else
    bad "SEC-FIX-5 as root, not root-only" "rc=$rc file=$(cat "$out") stubs: $(tr '\n' ';' < "$work/stub.log") log: $(head -n2 "$log")"
fi

# 9b. Missing logs/ in a parent that is not root-only: not even created.
out="$work/asroot-missing/vault/logs/vault-core-version.json"
mkdir -p "$work/asroot-missing/vault"
root_run "$out"
if [ "$rc" -eq 0 ] && [ ! -e "$(dirname "$out")" ] && ! grep -q '^mkdir ' "$work/stub.log" \
   && grep -q "not creating" "$log"; then
    ok "SEC-FIX-5 as root, parent not root-only: logs/ is not created"
else
    bad "SEC-FIX-5 as root, missing logs/" "rc=$rc stubs: $(tr '\n' ';' < "$work/stub.log") log: $(head -n2 "$log")"
fi

# 9c. Chain reported root-only: root writes (mktemp + mv in the directory).
out="$work/rootonly/vault/logs/vault-core-version.json"
mkdir -p "$(dirname "$out")"
STUB_ROOT_STAT=1 root_run "$out"
if [ "$rc" -eq 0 ] && grep -q '"version":"8.0"' "$out" 2>/dev/null && grep -q '^mktemp ' "$work/stub.log"; then
    ok "SEC-FIX-5 as root, root-only chain: the file is written"
else
    bad "SEC-FIX-5 as root, root-only chain" "rc=$rc file=$(head -c 100 "$out" 2>&1) log: $(head -n2 "$log")"
fi

# 9d. One ANCESTOR not root-only (two levels up): refused, naming it.
out="$work/ancestor/vault/logs/vault-core-version.json"
mkdir -p "$(dirname "$out")"
STUB_ROOT_STAT=1 STUB_BAD="$work/ancestor" root_run "$out"
if [ "$rc" -eq 0 ] && [ ! -e "$out" ] && grep -qF "$work/ancestor is not a root-only directory" "$log" \
   && ! grep -q '^mktemp ' "$work/stub.log"; then
    ok "SEC-FIX-5 as root, an ancestor not root-only: refused, the ancestor named"
else
    bad "SEC-FIX-5 as root, ancestor" "rc=$rc exists=$([ -e "$out" ] && echo yes || echo no) log: $(head -n2 "$log")"
fi

# 9e. Missing logs/ under a root-only chain: created with a plain mkdir.
out="$work/rootonly-missing/vault/logs/vault-core-version.json"
mkdir -p "$work/rootonly-missing/vault"
STUB_ROOT_STAT=1 root_run "$out"
if [ "$rc" -eq 0 ] && grep -q '"version":"8.0"' "$out" 2>/dev/null \
   && grep -qx "mkdir $work/rootonly-missing/vault/logs" "$work/stub.log"; then
    ok "SEC-FIX-5 as root, root-only parent: logs/ created with a plain mkdir, file written"
else
    bad "SEC-FIX-5 as root, root-only parent" "rc=$rc stubs: $(tr '\n' ';' < "$work/stub.log") log: $(head -n2 "$log")"
fi

# 9f. A symlinked logs/ as root: refused before any chain walk.
mkdir -p "$work/plant/vault" "$work/plant/target"
ln -s "$work/plant/target" "$work/plant/vault/logs"
STUB_ROOT_STAT=1 root_run "$work/plant/vault/logs/vault-core-version.json"
if [ "$rc" -eq 0 ] && [ -z "$(find "$work/plant/target" -mindepth 1)" ] && grep -q 'is a symlink' "$log"; then
    ok "SEC-FIX-5 as root, planted logs/ symlink: nothing written, target untouched"
else
    bad "SEC-FIX-5 as root, planted logs/ symlink" "rc=$rc target: $(find "$work/plant/target" -mindepth 1) log: $(head -n2 "$log")"
fi

# 9g. Relative output path as root: refused (the chain walk needs /).
root_run "relative/logs/vault-core-version.json"
if [ "$rc" -eq 0 ] && grep -q 'not an absolute path' "$log" && [ ! -e relative ]; then
    ok "SEC-FIX-5 as root, relative path: refused"
else
    bad "SEC-FIX-5 as root, relative path" "rc=$rc log: $(head -n2 "$log")"
fi

echo
echo "test-build-version-hook: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
