#!/bin/sh
# Race rig for core/docker/29-vault-build-version.sh (WP VER-2, review M1).
# Runs INSIDE the pinned nginx image as root, never on a host:
#
#   sh build-version-race-rig.sh <hook>             # deterministic (CI gate)
#   sh build-version-race-rig.sh <hook> race [runs] # stochastic, manual only
#
# The threat: /vault and /vault/logs belong to uid 101 (nginx here, vault-api
# in the other container), mode 0755, no sticky bit, so uid 101 can swap any
# name in them for a symlink at any moment. Before the fix, root's `printf >`
# and `chmod` on the hook's temp file followed such a swapped link, and a
# root `mkdir -p` + `chown` (no -h) followed a planted logs/ symlink.
#
# DETERMINISTIC mode (the CI gate, .github/scripts/verify-core-nginx.sh).
# The attacker wins every window by construction instead of by timing:
#   - a `mktemp` wrapper first in PATH creates the temp file with the real
#     mktemp, then at once swaps that name for a symlink to a root-only
#     0600 victim before handing the name back: the window between "temp
#     file created" and "temp file written" is always lost;
#   - a `mkdir` wrapper plants /vault/logs as a symlink to a root directory
#     right before the real mkdir: the window between the hook's symlink
#     check and its mkdir is always lost.
# Doing the swap as root (the wrapper runs as whoever calls mktemp/mkdir)
# models uid 101 exactly: uid 101 owns the directory, so it can make the same
# rename. Every run must have been attacked (the wrappers count), so a green
# result cannot come from a race that never happened. Before the fix this
# mode overwrote the victim and handed the root directory to nginx; after it,
# the writer (nginx) gets EACCES on the victim and root never runs a
# following mkdir/chown.
#
# RACE mode (manual): the original stochastic racer, a loop as nginx that
# swaps temp files while the hook runs <runs> times. Kept for humans because
# it attacks without any wrapper; NOT in the CI gate, since how many runs it
# disturbs depends on CPU scheduling (5 of 400 on the devbox, possibly 0 on a
# CI runner, and a 0 proves nothing).
set -eu

HOOK=${1:?usage: build-version-race-rig.sh <hook> [race [runs]]}
MODE=${2:-deterministic}
RUNS=${3:-400}
status=0

cp "$HOOK" /tmp/hook-under-test.sh
chmod 0644 /tmp/hook-under-test.sh

reset_volume() {
    rm -rf /vault/logs
    mkdir -p /vault/logs
    chown nginx:nginx /vault /vault/logs
    chmod 0755 /vault /vault/logs
}

printf 'root-only secret\n' > /etc/vault-victim
chmod 0600 /etc/vault-victim
victim_ok() {
    [ "$(stat -c '%u:%g %a' /etc/vault-victim) $(cat /etc/vault-victim)" = "0:0 600 root-only secret" ]
}

run_hook() {
    VAULT_BUILD_VERSION=0.1.0-rc9 VAULT_BUILD_COMMIT=unknown "$@" sh /tmp/hook-under-test.sh 2>&1 || true
}

if [ "$MODE" = "race" ]; then
    reset_volume
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
    disturbed=0
    i=0
    while [ "$i" -lt "$RUNS" ]; do
        case "$(run_hook env)" in *WARNING*) disturbed=$((disturbed + 1)) ;; esac
        i=$((i + 1))
    done
    touch /tmp/racer.stop
    wait "$racer" 2>/dev/null || true
    if victim_ok; then
        echo "race (manual): victim untouched after $RUNS runs, $disturbed disturbed (0 disturbed proves nothing)"
    else
        echo "FAIL (M1, race): the victim changed: $(stat -c '%u:%g %a' /etc/vault-victim) $(cat /etc/vault-victim)"
        status=1
    fi
    exit "$status"
fi

# --- deterministic mode ---------------------------------------------------------
real_mktemp=$(command -v mktemp)
real_mkdir=$(command -v mkdir)
mkdir -p /tmp/rigbin
: > /tmp/rig.swaps
: > /tmp/rig.plants
chmod 0666 /tmp/rig.swaps /tmp/rig.plants
cat > /tmp/rigbin/mktemp <<EOF
#!/bin/sh
t=\$("$real_mktemp" "\$@") || exit \$?
case "\$t" in
    /vault/logs/.vault-core-version.*)
        ln -s /etc/vault-victim "\$t.swap" && mv -f "\$t.swap" "\$t" && echo "swapped \$(id -un)" >> /tmp/rig.swaps ;;
esac
printf '%s\n' "\$t"
EOF
cat > /tmp/rigbin/mkdir <<EOF
#!/bin/sh
for a in "\$@"; do
    if [ "\$a" = /vault/logs ] && [ ! -e /vault/logs ] && [ ! -L /vault/logs ]; then
        ln -s /etc/vault-target /vault/logs && echo "planted \$(id -un)" >> /tmp/rig.plants
    fi
done
exec "$real_mkdir" "\$@"
EOF
chmod 0755 /tmp/rigbin/mktemp /tmp/rigbin/mkdir

# 0. Control: without the wrappers the hook writes the file, as nginx.
reset_volume
run_hook env > /tmp/rig.out
if [ -f /vault/logs/vault-core-version.json ] && [ "$(stat -c %U /vault/logs/vault-core-version.json)" = "nginx" ] \
   && grep -q '"version":"0.1.0-rc9"' /vault/logs/vault-core-version.json; then
    echo "rig control OK: unattacked, the hook writes the file as nginx"
else
    echo "FAIL (rig control): unattacked run did not write the file as nginx: $(cat /tmp/rig.out)"
    status=1
fi

# 1. The temp-file window, lost on every one of 20 runs.
reset_volume
i=0
while [ "$i" -lt 20 ]; do
    run_hook env PATH="/tmp/rigbin:$PATH" >> /tmp/rig.out
    i=$((i + 1))
done
swaps=$(grep -c '^swapped ' /tmp/rig.swaps || true)
if [ "$swaps" != "20" ]; then
    echo "FAIL (rig): expected 20 temp-file swaps, got $swaps -- the attack did not run, this proves nothing"
    status=1
elif victim_ok; then
    echo "temp-file swap OK: 20/20 runs attacked, the root-only victim is untouched (writer ran as $(sort -u /tmp/rig.swaps | sed 's/^swapped //' | tr '\n' ' '))"
else
    echo "FAIL (M1): the victim changed through a swapped temp file: $(stat -c '%u:%g %a' /etc/vault-victim) $(cat /etc/vault-victim)"
    status=1
fi

# 2. The check-then-mkdir window: logs/ planted right before mkdir.
rm -rf /vault/logs /etc/vault-target
mkdir -p /etc/vault-target
run_hook env PATH="/tmp/rigbin:$PATH" > /tmp/rig.out
plants=$(grep -c '^planted ' /tmp/rig.plants || true)
target_state="$(stat -c '%U:%G' /etc/vault-target) $(find /etc/vault-target -mindepth 1 | wc -l)"
if [ "$plants" != "1" ]; then
    echo "FAIL (rig): expected 1 planted logs/ symlink, got $plants -- the attack did not run"
    status=1
elif [ "$target_state" = "root:root 0" ]; then
    echo "planted-before-mkdir OK: /etc/vault-target stays root:root and empty"
else
    echo "FAIL (M1): planted logs/ symlink: /etc/vault-target is now '$target_state'; hook said: $(cat /tmp/rig.out)"
    status=1
fi

# 3. logs/ planted before the run (the static check).
rm -rf /vault/logs /etc/vault-target
mkdir -p /etc/vault-target
su -s /bin/sh -c 'ln -s /etc/vault-target /vault/logs' nginx
run_hook env > /tmp/rig.out
target_state="$(stat -c '%U:%G' /etc/vault-target) $(find /etc/vault-target -mindepth 1 | wc -l)"
if [ "$target_state" = "root:root 0" ] && [ -L /vault/logs ]; then
    echo "planted-before-run OK: /etc/vault-target stays root:root and empty"
else
    echo "FAIL (M1): pre-planted logs/ symlink: target is now '$target_state'; hook said: $(cat /tmp/rig.out)"
    status=1
fi
exit "$status"
