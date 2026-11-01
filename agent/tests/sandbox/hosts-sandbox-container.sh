#!/bin/sh
# WP 2.3 sandbox verification - runs INSIDE a throwaway container, as root.
#
# This is the end-to-end proof that `vault-agent hosts` does the right thing
# against a REAL system hosts file (the container's own /etc/hosts), not just
# against synthetic fixtures:
#
#   apply -> the hostname actually resolves to the cache IP (getent)
#   remove -> resolution is gone AND the file is byte-identical to before
#   non-root -> a clean permission-denied message with the sudo hint, and the
#               file is untouched
#
# It is deliberately re-runnable and self-contained. NEVER run it outside a
# throwaway container: it modifies /etc/hosts.
#
# Driver: agent/tests/sandbox/run-hosts-sandbox.sh (host side).

set -u

AGENT=/vault-agent
HOSTS=/etc/hosts
BACKUP=/etc/hosts.steamhangar.bak
CACHE_IP=10.0.0.50
OTHER_IP=10.44.44.44
CACHE_HOST=lancache.steamcontent.com
# busybox su resolves a user NAME, not a numeric uid ("su: unknown user
# 65534"), and alpine's `nobody` has /sbin/nologin as its shell - hence the
# name plus an explicit -s /bin/sh on every su below.
NOBODY=nobody

fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }

# ok LABEL ACTUAL EXPECTED
ok() {
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 -- got [$2], want [$3]"; fi
}

# has LABEL FILE NEEDLE
has() {
    if grep -qF -- "$3" "$2"; then pass "$1"; else
        fail "$1 -- [$3] not found in:"
        sed 's/^/        /' "$2"
    fi
}

sha() { sha256sum "$HOSTS" | cut -d' ' -f1; }
meta() { stat -c '%a %U %G' "$HOSTS"; }
inode() { stat -c '%i' "$HOSTS"; }

# resolves -> prints the address getent found, or "none"
resolves() {
    out=$(getent hosts "$CACHE_HOST" 2>/dev/null | head -1 | awk '{print $1}')
    [ -n "$out" ] && echo "$out" || echo "none"
}

section() { echo; echo "=== $* ==="; }

TMP=/tmp/out.txt

echo "############################################################"
echo "# vault-agent hosts - container sandbox (WP 2.3)"
echo "# image:  $(cat /etc/alpine-release 2>/dev/null || echo unknown)"
echo "# binary: $($AGENT hosts 2>&1 | head -1)"
echo "############################################################"

section "0. baseline"
SHA0=$(sha)
META0=$(meta)
INODE0=$(inode)
echo "sha256 : $SHA0"
echo "mode   : $META0"
echo "inode  : $INODE0"
echo "--- /etc/hosts (docker-managed, a BIND MOUNT - see note in the driver) ---"
cat "$HOSTS"
echo "--- end ---"
ok "the cache hostname does not resolve yet" "$(resolves)" "none"

section "1. status before apply"
$AGENT hosts status --cache-ip "$CACHE_IP" >"$TMP" 2>&1
ok "status exits 0" "$?" "0"
cat "$TMP"
has "status reports absent" "$TMP" "state:      absent"
ok "status did not modify the file" "$(sha)" "$SHA0"

section "2. apply"
$AGENT hosts apply --cache-ip "$CACHE_IP" >"$TMP" 2>&1
rc=$?
cat "$TMP"
ok "apply exits 0" "$rc" "0"
has "apply reports the state transition" "$TMP" "absent -> present-correct"
has "apply names the backup" "$TMP" "$BACKUP"
has "apply shows the block it wrote" "$TMP" "$CACHE_IP $CACHE_HOST"
has "apply tells the user to restart Steam" "$TMP" "restart Steam"

echo "--- /etc/hosts after apply ---"
cat "$HOSTS"
echo "--- end ---"

section "3. THE point of the whole feature: does it actually resolve?"
ok "getent resolves the cache hostname to the cache IP" "$(resolves)" "$CACHE_IP"

section "4. status after apply"
$AGENT hosts status --cache-ip "$CACHE_IP" >"$TMP" 2>&1
cat "$TMP"
has "status reports present-correct" "$TMP" "state:      present-correct"
has "status reports the managed IP" "$TMP" "managed IP: $CACHE_IP"
has "status resolver check agrees" "$TMP" "$CACHE_HOST -> $CACHE_IP"

section "5. file metadata and backup"
ok "permissions/owner unchanged" "$(meta)" "$META0"
ok "backup holds the pre-apply bytes" "$(sha256sum $BACKUP | cut -d' ' -f1)" "$SHA0"
if [ "$(inode)" = "$INODE0" ]; then
    echo "INFO  inode unchanged ($INODE0) -> the in-place fallback was used"
else
    echo "INFO  inode changed ($INODE0 -> $(inode)) -> the atomic rename was used"
fi

section "6. re-apply is a no-op"
SHA_APPLIED=$(sha)
$AGENT hosts apply --cache-ip "$CACHE_IP" >"$TMP" 2>&1
ok "re-apply exits 0" "$?" "0"
has "re-apply writes nothing" "$TMP" "nothing was written"
ok "re-apply left the file byte-identical" "$(sha)" "$SHA_APPLIED"

section "7. changing the IP replaces the block in place"
$AGENT hosts apply --cache-ip "$OTHER_IP" >"$TMP" 2>&1
ok "apply with a new IP exits 0" "$?" "0"
has "it reports the different-ip transition" "$TMP" "present-different-ip -> present-correct"
ok "getent now resolves to the new IP" "$(resolves)" "$OTHER_IP"
$AGENT hosts apply --cache-ip "$CACHE_IP" >/dev/null 2>&1
ok "and back again" "$(resolves)" "$CACHE_IP"

section "8. remove"
$AGENT hosts remove >"$TMP" 2>&1
rc=$?
cat "$TMP"
ok "remove exits 0" "$rc" "0"
has "remove reports the transition" "$TMP" "-> absent"
ok "resolution is gone" "$(resolves)" "none"
ok "THE FILE IS BYTE-IDENTICAL TO THE PRE-APPLY STATE" "$(sha)" "$SHA0"
ok "permissions/owner still unchanged" "$(meta)" "$META0"

section "9. remove is idempotent"
$AGENT hosts remove >"$TMP" 2>&1
ok "second remove exits 0" "$?" "0"
has "second remove says there is nothing to do" "$TMP" "nothing to do"
ok "file still byte-identical" "$(sha)" "$SHA0"

section "10. non-root: apply must fail cleanly and change nothing"
rm -f "$BACKUP"
SHA_CLEAN=$(sha)
su -s /bin/sh -c "$AGENT hosts apply --cache-ip $CACHE_IP" "$NOBODY" >"$TMP" 2>&1
rc=$?
cat "$TMP"
ok "non-root apply exits 1" "$rc" "1"
has "it says permission denied" "$TMP" "permission denied"
has "it prints the sudo hint" "$TMP" "sudo"
has "the sudo hint repeats the exact command" "$TMP" "hosts apply --cache-ip $CACHE_IP"
has "it explains no self-elevation" "$TMP" "never elevates itself"
ok "the hosts file is untouched" "$(sha)" "$SHA_CLEAN"
if [ -e "$BACKUP" ]; then fail "non-root apply created a backup file"; else pass "no backup was created"; fi

section "11. non-root: status still works (read-only)"
su -s /bin/sh -c "$AGENT hosts status --cache-ip $CACHE_IP" "$NOBODY" >"$TMP" 2>&1
ok "non-root status exits 0" "$?" "0"
has "non-root status reports absent" "$TMP" "state:      absent"

section "12. non-root: remove of an EXISTING block fails cleanly"
$AGENT hosts apply --cache-ip "$CACHE_IP" >/dev/null 2>&1
SHA_WITH_BLOCK=$(sha)
rm -f "$BACKUP"
su -s /bin/sh -c "$AGENT hosts remove" "$NOBODY" >"$TMP" 2>&1
rc=$?
cat "$TMP"
ok "non-root remove exits 1" "$rc" "1"
has "it prints the sudo hint for remove" "$TMP" "sudo"
has "the hint repeats the remove command" "$TMP" "hosts remove"
ok "the block is still there, file unchanged" "$(sha)" "$SHA_WITH_BLOCK"

section "13. cleanup: back to the pristine file"
$AGENT hosts remove >/dev/null 2>&1
rm -f "$BACKUP"
ok "final file is byte-identical to the baseline" "$(sha)" "$SHA0"

echo
echo "############################################################"
if [ "$fails" -eq 0 ]; then
    echo "# ALL CHECKS PASSED"
else
    echo "# $fails CHECK(S) FAILED"
fi
echo "############################################################"
exit "$fails"
