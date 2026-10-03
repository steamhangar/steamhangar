#!/bin/sh
# Race rig for core/docker/29-vault-build-version.sh (WP VER-2 review M1,
# reworked in WP SEC-FIX-5). Runs INSIDE the pinned nginx image as root,
# never on a host:
#
#   sh build-version-race-rig.sh <hook>             # deterministic (CI gate)
#   sh build-version-race-rig.sh <hook> race [runs] # stochastic, manual only
#
# The threat: the hook runs as root and writes into /vault/logs on the volume
# vault-api and the nginx workers (uid 101) share. If uid 101 can rename names
# in that directory, it can swap the hook's temp file (or logs/ itself) for a
# symlink between root's create and root's write, and root writes through it.
#
# The rule since SEC-FIX-5: root writes only into a directory chain that no
# other uid can change (21-vault-volume-ownership.sh makes /vault and
# /vault/logs root:root 0755). The attacker here is REAL uid 101: every
# attack runs under `su nginx`, so it succeeds exactly when the layout lets
# uid 101 do it. (The VER-2 version of this rig did the swap as root, which
# modelled a 101-owned directory; with a root-owned one that would model an
# attacker who is already root.)
#
# DETERMINISTIC mode (the CI gate, .github/scripts/verify-core-nginx.sh).
# PATH wrappers put the attack exactly into each window, every time:
#   - `mktemp`: right after the real mktemp creates the temp file, uid 101
#     tries to swap that name for a symlink to a root-only 0600 victim;
#   - `mkdir`: right before the real mkdir creates /vault/logs, uid 101 tries
#     to plant /vault/logs as a symlink to a root directory.
# Each wrapper records every attempt and whether it succeeded, so a green
# result can never come from an attack that did not run. Scenarios:
#   0. control, root-owned layout: the file is written, root:root 0644;
#   1. root-owned layout, 20 runs: 20 temp-file swaps attempted, all denied,
#      the victim untouched, the file written every time;
#   2. a 101-owned layout (a volume 21- did not migrate): the hook must not
#      write at all -- mktemp never runs as root, the victim untouched. With
#      the chain check removed, uid 101 wins the swap and root overwrites the
#      victim (the mutation this scenario kills);
#   3. logs/ missing under a root-owned /vault: the plant is attempted and
#      denied, logs/ is created root:root 0755, the file written;
#   4. logs/ missing under a 101-owned /vault: the hook must not create it;
#   5. logs/ pre-planted as a symlink by uid 101 (old layout): nothing
#      written through it.
#
# RACE mode (manual): a loop as nginx that swaps temp files while the hook
# runs <runs> times on the 101-owned layout. Kept for humans; NOT in the CI
# gate, because how many runs it disturbs depends on scheduling.
set -eu

HOOK=${1:?usage: build-version-race-rig.sh <hook> [race [runs]]}
MODE=${2:-deterministic}
RUNS=${3:-400}
status=0
OUTF=/vault/logs/vault-core-version.json

cp "$HOOK" /tmp/hook-under-test.sh
chmod 0644 /tmp/hook-under-test.sh

# layout root|old: what 21-vault-volume-ownership.sh leaves (root) or what
# images before SEC-FIX-5 shipped (old: everything 101).
layout() {
    rm -rf /vault/logs
    mkdir -p /vault/logs
    # chown root first (it may be 101's from the previous scenario), then
    # chmod as the owner: with vault-core's compose capability set root has
    # no CAP_FOWNER and may chmod only what it owns.
    chown root:root /vault /vault/logs
    chmod 0755 /vault /vault/logs
    if [ "$1" != root ]; then
        chown nginx:nginx /vault /vault/logs
    fi
}

printf 'root-only secret\n' > /etc/vault-victim
chmod 0600 /etc/vault-victim
victim_ok() {
    [ "$(stat -c '%u:%g %a' /etc/vault-victim) $(cat /etc/vault-victim)" = "0:0 600 root-only secret" ]
}

run_hook() {
    VAULT_BUILD_VERSION=0.1.0-rc9 VAULT_BUILD_COMMIT=unknown "$@" sh /tmp/hook-under-test.sh "$OUTF" 2>&1 || true
}

if [ "$MODE" = "race" ]; then
    layout old
    cat > /tmp/racer.sh <<'RACER'
#!/bin/sh
n=0
while [ ! -e /tmp/racer.stop ]; do
    for f in /vault/logs/.vault-core-version.*; do
        if [ ! -f "$f" ] || [ -L "$f" ]; then continue; fi
        n=$((n + 1))
        ln -s /etc/vault-victim "/vault/logs/.swap$n" 2>/dev/null || continue
        mv -f "/vault/logs/.swap$n" "$f" 2>/dev/null || rm -f "/vault/logs/.swap$n"
    done
done
RACER
    rm -f /tmp/racer.stop
    su -s /bin/sh -c 'exec sh /tmp/racer.sh' nginx &
    racer=$!
    i=0
    while [ "$i" -lt "$RUNS" ]; do
        run_hook env > /dev/null
        i=$((i + 1))
    done
    touch /tmp/racer.stop
    wait "$racer" 2>/dev/null || true
    if victim_ok; then
        echo "race (manual): victim untouched after $RUNS runs"
    else
        echo "FAIL (race): the victim changed: $(stat -c '%u:%g %a' /etc/vault-victim) $(cat /etc/vault-victim)"
        status=1
    fi
    exit "$status"
fi

# --- deterministic mode ---------------------------------------------------------
real_mktemp=$(command -v mktemp)
real_mkdir=$(command -v mkdir)
mkdir -p /tmp/rigbin
# One line per attempt: "<window> ok" (uid 101 got through) or "<window> denied".
: > /tmp/rig.attacks
: > /tmp/rig.mktemp-calls
chmod 0666 /tmp/rig.attacks /tmp/rig.mktemp-calls
cat > /tmp/rigbin/mktemp <<EOF
#!/bin/sh
echo "\$(id -un) \$*" >> /tmp/rig.mktemp-calls
t=\$("$real_mktemp" "\$@") || exit \$?
case "\$t" in
    /vault/logs/.vault-core-version.*)
        if su -s /bin/sh -c "ln -s /etc/vault-victim '\$t.swap' && mv -f '\$t.swap' '\$t'" nginx 2>/dev/null; then
            echo "swap ok" >> /tmp/rig.attacks
        else
            echo "swap denied" >> /tmp/rig.attacks
        fi ;;
esac
printf '%s\n' "\$t"
EOF
cat > /tmp/rigbin/mkdir <<EOF
#!/bin/sh
for a in "\$@"; do
    if [ "\$a" = /vault/logs ] && [ ! -e /vault/logs ] && [ ! -L /vault/logs ]; then
        if su -s /bin/sh -c 'ln -s /etc/vault-target /vault/logs' nginx 2>/dev/null; then
            echo "plant ok" >> /tmp/rig.attacks
        else
            echo "plant denied" >> /tmp/rig.attacks
        fi
    fi
done
exec "$real_mkdir" "\$@"
EOF
chmod 0755 /tmp/rigbin/mktemp /tmp/rigbin/mkdir

attacks() { grep -c "^$1 $2\$" /tmp/rig.attacks || true; }
target_clean() {
    [ "$(stat -c '%U:%G %a' /etc/vault-target) $(find /etc/vault-target -mindepth 1 | wc -l)" = "root:root 755 0" ]
}
file_ok() {
    [ -f "$OUTF" ] && [ ! -L "$OUTF" ] && [ "$(stat -c '%u:%g %a' "$OUTF")" = "0:0 644" ] \
        && grep -q '"version":"0.1.0-rc9"' "$OUTF"
}
reset_target() { rm -rf /etc/vault-target; mkdir -p /etc/vault-target; chmod 0755 /etc/vault-target; }

# 0. Control: root-owned layout, no wrappers: the hook writes the file, as root.
layout root
run_hook env > /tmp/rig.out
if file_ok; then
    echo "rig control OK: root-owned layout, the hook writes $OUTF root:root 0644"
else
    echo "FAIL (rig control): $(stat -c '%u:%g %a' "$OUTF" 2>&1); hook said: $(cat /tmp/rig.out)"
    status=1
fi

# 1. The temp-file window on the root-owned layout, 20 runs.
layout root
: > /tmp/rig.attacks
i=0
while [ "$i" -lt 20 ]; do
    run_hook env PATH="/tmp/rigbin:$PATH" >> /tmp/rig.out
    i=$((i + 1))
done
tried=$(grep -c '^swap ' /tmp/rig.attacks || true)
if [ "$tried" != "20" ]; then
    echo "FAIL (rig): expected 20 temp-file swap attempts, got $tried -- the attack did not run, this proves nothing"
    status=1
elif [ "$(attacks swap ok)" != "0" ]; then
    echo "FAIL (SEC-FIX-5): uid 101 swapped $(attacks swap ok) of 20 temp files in a root-owned logs/"
    status=1
elif victim_ok && file_ok; then
    echo "temp-file swap OK: 20/20 attempts by uid 101 denied, the victim untouched, the file written"
else
    echo "FAIL (SEC-FIX-5): victim $(stat -c '%u:%g %a' /etc/vault-victim) '$(cat /etc/vault-victim)', file $(stat -c '%u:%g %a' "$OUTF" 2>&1)"
    status=1
fi

# 2. A 101-owned layout: uid 101 COULD swap, so root must not write at all.
layout old
: > /tmp/rig.attacks
: > /tmp/rig.mktemp-calls
run_hook env PATH="/tmp/rigbin:$PATH" > /tmp/rig.out
if ! victim_ok; then
    echo "FAIL (SEC-FIX-5): 101-owned logs/: root wrote through a swapped temp file: victim $(stat -c '%u:%g %a' /etc/vault-victim) '$(cat /etc/vault-victim)'"
    status=1
elif [ -s /tmp/rig.mktemp-calls ] || [ -e "$OUTF" ]; then
    echo "FAIL (SEC-FIX-5): 101-owned logs/: the hook still acted as root (mktemp: $(cat /tmp/rig.mktemp-calls), file: $([ -e "$OUTF" ] && echo present || echo absent))"
    status=1
elif grep -q 'not a root-only directory' /tmp/rig.out; then
    echo "101-owned logs/ OK: root wrote nothing (no mktemp), the victim untouched, WARNING given"
else
    echo "FAIL (SEC-FIX-5): 101-owned logs/: no warning; hook said: $(cat /tmp/rig.out)"
    status=1
fi

# 3. logs/ missing under a root-owned /vault: the plant is denied.
layout root
rm -rf /vault/logs
reset_target
: > /tmp/rig.attacks
run_hook env PATH="/tmp/rigbin:$PATH" > /tmp/rig.out
if [ "$(grep -c '^plant ' /tmp/rig.attacks || true)" != "1" ]; then
    echo "FAIL (rig): expected 1 logs/ plant attempt, got $(grep -c '^plant ' /tmp/rig.attacks || true) -- the attack did not run"
    status=1
elif [ "$(attacks plant ok)" = "0" ] && target_clean && [ ! -L /vault/logs ] \
     && [ "$(stat -c '%u:%g %a' /vault/logs)" = "0:0 755" ] && file_ok; then
    echo "planted-before-mkdir OK: denied for uid 101, logs/ created root:root 0755, file written"
else
    echo "FAIL (SEC-FIX-5): plant $(cat /tmp/rig.attacks); target '$(stat -c '%U:%G %a' /etc/vault-target) $(find /etc/vault-target -mindepth 1 | wc -l)'; logs $(stat -c '%u:%g %a %F' /vault/logs 2>&1); hook said: $(cat /tmp/rig.out)"
    status=1
fi

# 4. logs/ missing under a 101-owned /vault: root must not create it.
layout old
rm -rf /vault/logs
reset_target
run_hook env > /tmp/rig.out
if [ ! -e /vault/logs ] && [ ! -L /vault/logs ] && target_clean && grep -q 'not creating' /tmp/rig.out; then
    echo "101-owned /vault OK: logs/ not created by root"
else
    echo "FAIL (SEC-FIX-5): 101-owned /vault: logs $(stat -c '%u:%g %a %F' /vault/logs 2>&1); hook said: $(cat /tmp/rig.out)"
    status=1
fi

# 5. logs/ pre-planted by uid 101 on the old layout (the static case).
layout old
rm -rf /vault/logs
reset_target
su -s /bin/sh -c 'ln -s /etc/vault-target /vault/logs' nginx
run_hook env > /tmp/rig.out
if target_clean && [ -L /vault/logs ]; then
    echo "planted-before-run OK: /etc/vault-target stays root:root 0755 and empty"
else
    echo "FAIL (SEC-FIX-5): pre-planted logs/ symlink: target now '$(stat -c '%U:%G %a' /etc/vault-target) $(find /etc/vault-target -mindepth 1 | wc -l)'; hook said: $(cat /tmp/rig.out)"
    status=1
fi
exit "$status"
