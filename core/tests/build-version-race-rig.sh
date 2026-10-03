#!/bin/sh
# Race rig for core/docker/29-vault-build-version.sh (WP VER-2, review M1).
# Runs INSIDE the pinned nginx image as root (.github/scripts/
# verify-core-nginx.sh mounts it), never on a host.
#
#   sh build-version-race-rig.sh <hook> [runs]
#
# The threat: /vault and /vault/logs belong to uid 101 (nginx here, vault-api
# in the other container), mode 0755, no sticky bit, so uid 101 can swap any
# name in them for a symlink at any moment. A racer running AS nginx watches
# for the hook's temp files and replaces each with a symlink to a root-only
# 0600 victim; the hook runs <runs> times as root. Before the fix, root's
# `printf >` / `chmod` followed the swapped link (review reproduced it).
#
# Pass conditions:
#   1. the victim keeps its content, mode 0600 and owner root;
#   2. every version file present is owned by nginx (the writer half);
#   3. a planted logs/ symlink to a root directory is neither chowned nor
#      written through.
# The rig also prints how many runs the racer actually disturbed (hook
# WARNING lines), so a green result can be told apart from a race that never
# happened; it fails when that number is 0, because then it proved nothing.
set -eu

HOOK=${1:?usage: build-version-race-rig.sh <hook> [runs]}
RUNS=${2:-400}
status=0

cp "$HOOK" /tmp/hook-under-test.sh
chmod 0644 /tmp/hook-under-test.sh

mkdir -p /vault/logs
chown nginx:nginx /vault /vault/logs
chmod 0755 /vault /vault/logs

printf 'root-only secret\n' > /etc/vault-victim
chmod 0600 /etc/vault-victim

cat > /tmp/racer.sh <<'RACER'
#!/bin/sh
# Runs as nginx: swap every temp file the hook creates for a symlink.
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
chmod 0755 /tmp/racer.sh
rm -f /tmp/racer.stop
su -s /bin/sh -c 'exec sh /tmp/racer.sh' nginx &
racer=$!

disturbed=0
i=0
while [ "$i" -lt "$RUNS" ]; do
    out=$(VAULT_BUILD_VERSION=0.1.0-rc9 VAULT_BUILD_COMMIT=unknown sh /tmp/hook-under-test.sh 2>&1) || true
    case "$out" in *WARNING*) disturbed=$((disturbed + 1)) ;; esac
    i=$((i + 1))
done
touch /tmp/racer.stop
wait "$racer" 2>/dev/null || true

victim_state="$(stat -c '%u:%g %a' /etc/vault-victim) $(cat /etc/vault-victim)"
if [ "$victim_state" = "0:0 600 root-only secret" ]; then
    echo "race OK: the root-only victim is untouched after $RUNS runs ($disturbed run(s) disturbed by the racer)"
else
    echo "FAIL (M1): the victim changed: $victim_state"
    status=1
fi
if [ "$disturbed" -eq 0 ]; then
    echo "FAIL (M1 rig): the racer never disturbed a run; the rig proved nothing"
    status=1
fi
for f in /vault/logs/vault-core-version.json /vault/logs/.vault-core-version.*; do
    if [ ! -e "$f" ] || [ -L "$f" ]; then continue; fi
    owner=$(stat -c %U "$f")
    if [ "$owner" != "nginx" ]; then
        echo "FAIL (M1): $f is owned by $owner, expected nginx (the writer half)"
        status=1
    fi
done
[ "$status" -ne 0 ] || echo "race OK: every version/temp file left is owned by nginx"

# Second primitive: logs/ planted as a symlink to a root directory.
rm -rf /vault/logs
mkdir -p /etc/vault-target
su -s /bin/sh -c 'ln -s /etc/vault-target /vault/logs' nginx
out=$(VAULT_BUILD_VERSION=0.1.0-rc9 sh /tmp/hook-under-test.sh 2>&1) || true
target_state="$(stat -c '%U:%G' /etc/vault-target) $(find /etc/vault-target -mindepth 1 | wc -l)"
if [ "$target_state" = "root:root 0" ] && [ -L /vault/logs ]; then
    echo "planted-symlink OK: /etc/vault-target stays root:root and empty"
else
    echo "FAIL (M1): planted logs/ symlink: target is now '$target_state'; hook said: $out"
    status=1
fi
exit "$status"
