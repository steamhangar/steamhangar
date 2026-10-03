#!/usr/bin/env bash
# Docker-free test of vault_root_only_dir (WP SEC-FIX-5), the one predicate
# three vault-core start hooks use to decide whether root may act on names in
# a directory:
#
#   core/docker/21-vault-volume-ownership.sh
#   core/docker/25-vault-eventlog.sh
#   core/docker/29-vault-build-version.sh
#
# 1. The three copies are byte-identical (a fix in one must reach all).
# 2. The function itself, extracted from 21-, under `sh`: a `stat` stub
#    supplies "<uid> <mode>" so the uid-0 cases can run without root; real
#    files supply the symlink / not-a-directory cases.
#
#     bash core/tests/test-root-only-dir.sh
#
# Run by .github/scripts/verify-core-nginx.sh (dev.sh test-core, CI). The
# real-uid behaviour is exercised in the pinned image by
# core/tests/volume-ownership-race-rig.sh.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker_dir="$(cd "$script_dir/../docker" && pwd)"
hooks=(21-vault-volume-ownership.sh 25-vault-eventlog.sh 29-vault-build-version.sh)

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

pass=0
fail=0
ok()  { echo "ok:   $1"; pass=$((pass + 1)); }
bad() { echo "FAIL: $1: $2" >&2; fail=$((fail + 1)); }

extract() {
    awk '/^vault_root_only_dir\(\) \{$/ { f = 1 } f { print } f && /^}$/ { exit }' "$1"
}

# --- 1. identical copies --------------------------------------------------------
ref="$(extract "$docker_dir/${hooks[0]}")"
if [ -z "$ref" ]; then
    bad "extract" "no vault_root_only_dir in ${hooks[0]}"
fi
for h in "${hooks[@]}"; do
    got="$(extract "$docker_dir/$h")"
    if [ -z "$got" ]; then
        bad "copy in $h" "vault_root_only_dir not found"
    elif [ "$got" = "$ref" ]; then
        ok "copy in $h is byte-identical to ${hooks[0]}'s"
    else
        bad "copy in $h" "differs from ${hooks[0]}'s: $(diff <(printf '%s\n' "$ref") <(printf '%s\n' "$got") | tr '\n' ' ')"
    fi
done

# --- 2. behaviour -------------------------------------------------------------------
printf '%s\n' "$ref" > "$work/fn.sh"
mkdir -p "$work/stubs" "$work/dir"
cat > "$work/stubs/stat" <<'STUB'
#!/bin/sh
# stat -c '%u %a' <path> -> $STUB_STAT; anything else is the real stat.
if [ "$1" = "-c" ] && [ "$2" = "%u %a" ] && [ -n "${STUB_STAT:-}" ]; then
    echo "$STUB_STAT"; exit 0
fi
PATH=$STUB_REAL_PATH exec stat "$@"
STUB
chmod 0755 "$work/stubs/stat"
: > "$work/file"
ln -s "$work/dir" "$work/link"

# check <name> <want: yes|no> <path> [stub "uid mode"]
check() {
    local name="$1" want="$2" path="$3" stub="${4:-}" got
    # The stub gets the caller's PATH on purpose (SC2097/SC2098 intended).
    # shellcheck disable=SC2097,SC2098
    if STUB_STAT="$stub" STUB_REAL_PATH="$PATH" PATH="$work/stubs:$PATH" \
        sh -c '. "$1"; vault_root_only_dir "$2"' sh "$work/fn.sh" "$path"; then
        got=yes
    else
        got=no
    fi
    if [ "$got" = "$want" ]; then ok "$name -> $got"; else bad "$name" "got $got, want $want"; fi
}

check "root 0755"                      yes "$work/dir" "0 755"
check "root 0700"                      yes "$work/dir" "0 700"
check "root 0711"                      yes "$work/dir" "0 711"
check "root 0555"                      yes "$work/dir" "0 555"
check "root 2755 (setgid, no write)"   yes "$work/dir" "0 2755"
check "root 0775 (group write)"        no  "$work/dir" "0 775"
check "root 0757 (other write)"        no  "$work/dir" "0 757"
check "root 0777"                      no  "$work/dir" "0 777"
check "root 1777 (sticky /tmp)"        no  "$work/dir" "0 1777"
check "root 0735 (group -wx)"          no  "$work/dir" "0 735"
check "root 0752 (other -w-)"          no  "$work/dir" "0 752"
check "root 0720"                      no  "$work/dir" "0 720"
check "uid 101 0755"                   no  "$work/dir" "101 755"
check "uid 1000 0755"                  no  "$work/dir" "1000 755"
check "uid 10 0755 (not a 0 prefix)"   no  "$work/dir" "10 755"
check "symlink to a dir, stat says root" no "$work/link" "0 755"
check "regular file, stat says root"   no  "$work/file" "0 755"
check "missing path, stat says root"   no  "$work/missing" "0 755"
check "real dir of the test user (unstubbed)" "$([ "$(id -u)" = 0 ] && echo yes || echo no)" "$work/dir"

echo
echo "test-root-only-dir: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
