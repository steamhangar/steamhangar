#!/bin/sh
# Race rig for the cache volume's ownership (WP SEC-FIX-5): the start hooks
# 21-vault-volume-ownership.sh and 25-vault-eventlog.sh, and the root nginx
# master opening the cache-event log. Runs INSIDE the pinned nginx image as
# root, never on a host:
#
#   sh volume-ownership-race-rig.sh <dir holding the hooks>
#
# The directory must hold 25-vault-eventlog.sh; 21-vault-volume-ownership.sh
# is run when present. (Pointing it at the hooks from before SEC-FIX-5, which
# had no 21-, is how the pre-fix code is shown to fail here.)
#
# The threat (VER-2 review, 2026-10-03): /vault and /vault/logs belonged to
# uid 101 -- the nginx workers and vault-api -- mode 0755, no sticky bit.
#   (a) 25- runs as root and did `mkdir -p` and `[ -e ] || : >` on names in
#       a directory uid 101 can write: a symlink swapped in between check and
#       use makes root create or truncate any file;
#   (b) the root nginx master opens the event log O_APPEND|O_CREAT at every
#       start and follows symlinks: uid 101 can swap event.log for a link at
#       any time and root then creates/appends to any file (CVE-2016-1247
#       class). No hook-level re-check can close (b).
#
# The attacker is REAL uid 101: every attack runs under `su nginx`, so it
# succeeds exactly when the layout lets uid 101 do it. PATH wrappers put the
# attacks into the hooks' windows deterministically and record every attempt
# ("<attack> ok" / "<attack> denied"), so a pass never rests on an attack that
# did not run:
#   - `chown` wrapper (window (a), the create): before every chown the hooks
#     run, uid 101 tries to replace the event log with a DANGLING symlink to
#     /etc/rig-created. The pre-fix 25- chowned the log directory right
#     before `[ -e ] || : >`, which then created the link target as root.
#   - `mkdir` wrapper (window (a), mkdir -p): before every mkdir of a path
#     under /vault, uid 101 tries to plant the first missing directory of
#     that path as a symlink to /etc/rig-dir. The pre-fix `mkdir -p` then
#     created a directory inside /etc/rig-dir as root.
#   - window (b): after the hooks, uid 101 tries to replace the event log
#     with a dangling symlink to /etc/rig-master, and tmp/client_body with a
#     symlink to /etc/rig-dir, then root runs `nginx -t` on a config logging
#     there and using that temp path (nginx -t runs the master's start-up:
#     it opens every access_log O_APPEND|O_CREAT and, in ngx_create_paths,
#     mkdirs, stats, chowns and chmods every temp path BY NAME).
#
# Scenarios (layout "new" = what core/Dockerfile ships; "old" = a volume from
# before SEC-FIX-5, everything owned by 101):
#   S1 new layout, default path: every attack denied, boot OK.
#   S2 old layout, default path, event.log present (the upgrade): no attack
#      runs before 21- has migrated... except the chown wrapper's, which fires
#      before 21-'s FIRST chown, while /vault/logs is still 101's -- uid 101
#      wins that one, and the boot must then be REFUSED by 25- (a symlink on
#      the log name), never followed. (b) is then attempted on the migrated
#      volume and denied.
#   S3 new layout, nested custom path /vault/ev/sub/event.log: both
#      directories created root:root 0755 by 25-, the plants denied.
#   S4 old layout, nested custom path: same, after migration.
#   S5 a custom path under cache/depot (101-owned): refused by 25-.
#   S6 old layout, event log OFF: boots; only the temp-path attack of (b)
#      applies (the pre-fix code hands /etc/rig-dir to nginx here).
#   S7 old layout with cache/, tmp/, logs/ at 0750 (so 21- must chmod
#      them), and the CHILD-SWAP attack on (SEC-FIX-5 review): before EVERY
#      chown and chmod the hooks run, uid 101 tries to replace /vault/cache,
#      /vault/tmp and /vault/logs with links to /etc/rig-victim (root, 0700).
#      This pins 21-'s order: /vault first, then the children. Done right,
#      the swap can only land before /vault is root's, and 21- then refuses
#      the links; done children-first, root's chmod 0755 of a child follows
#      the freshly planted link and /etc/rig-victim becomes 0755.
# Damage checks in every scenario: /etc/rig-created and /etc/rig-master do
# not exist, /etc/rig-dir is still root:root 0755 and empty.
set -eu

HOOKS=${1:?usage: volume-ownership-race-rig.sh <dir with 25-vault-eventlog.sh [and 21-vault-volume-ownership.sh]>}
[ -f "$HOOKS/25-vault-eventlog.sh" ] || { echo "no 25-vault-eventlog.sh in $HOOKS" >&2; exit 2; }
HAVE21=no
[ -f "$HOOKS/21-vault-volume-ownership.sh" ] && HAVE21=yes
status=0

real_chown=$(command -v chown)
real_chmod=$(command -v chmod)
real_mkdir=$(command -v mkdir)
mkdir -p /tmp/rigbin
: > /tmp/rig.attacks
chmod 0666 /tmp/rig.attacks

# S7's child-swap, run by the chown and chmod wrappers while
# /tmp/rig.childswap exists.
cat > /tmp/rigbin/childswap <<'SWAP'
#!/bin/sh
[ -e /tmp/rig.childswap ] || exit 0
for c in cache tmp logs; do
    [ -L "/vault/$c" ] && continue
    if su -s /bin/sh -c "mv /vault/$c /vault/.rig-old-$c-$$ && ln -s /etc/rig-victim /vault/$c" nginx 2>/dev/null; then
        echo "child-swap ok" >> /tmp/rig.attacks
    else
        echo "child-swap denied" >> /tmp/rig.attacks
    fi
done
SWAP
cat > /tmp/rigbin/chmod <<EOF
#!/bin/sh
sh /tmp/rigbin/childswap
exec "$real_chmod" "\$@"
EOF

# The event log path the wrappers attack is read from a file, per scenario.
cat > /tmp/rigbin/chown <<EOF
#!/bin/sh
sh /tmp/rigbin/childswap
v=\$(cat /tmp/rig.value)
if [ -n "\$v" ] && [ ! -L "\$v" ]; then
    d=\$(dirname "\$v")
    if su -s /bin/sh -c "ln -s /etc/rig-created '\$d/.rig-swap' && mv -f '\$d/.rig-swap' '\$v'" nginx 2>/dev/null; then
        echo "create-swap ok" >> /tmp/rig.attacks
    else
        rm -f "\$d/.rig-swap" 2>/dev/null
        echo "create-swap denied" >> /tmp/rig.attacks
    fi
fi
exec "$real_chown" "\$@"
EOF
cat > /tmp/rigbin/mkdir <<EOF
#!/bin/sh
for a in "\$@"; do
    case "\$a" in
        /vault/*)
            # the first missing component of the path, below /vault
            p=/vault; rest=\${a#/vault/}
            while [ -n "\$rest" ]; do
                c=\${rest%%/*}
                case "\$rest" in */*) rest=\${rest#*/} ;; *) rest= ;; esac
                p="\$p/\$c"
                if [ ! -e "\$p" ] && [ ! -L "\$p" ]; then
                    if su -s /bin/sh -c "ln -s /etc/rig-dir '\$p'" nginx 2>/dev/null; then
                        echo "mkdir-plant ok" >> /tmp/rig.attacks
                    else
                        echo "mkdir-plant denied" >> /tmp/rig.attacks
                    fi
                    break
                fi
            done ;;
    esac
done
exec "$real_mkdir" "\$@"
EOF
chmod 0755 /tmp/rigbin/chown /tmp/rigbin/mkdir /tmp/rigbin/chmod /tmp/rigbin/childswap

layout() {
    rm -rf /vault/cache /vault/tmp /vault/logs /vault/ev
    "$real_mkdir" -p /vault/cache/depot /vault/tmp /vault/logs
    # chown root, then chmod, then hand over: run with vault-core's compose
    # capability set, root has no CAP_FOWNER and may chmod only what it owns
    # (/vault may still be 101's from the previous scenario).
    "$real_chown" root:root /vault
    chmod 0755 /vault /vault/cache /vault/cache/depot /vault/tmp /vault/logs
    if [ "$1" = new ]; then
        "$real_chown" root:root /vault /vault/cache /vault/tmp /vault/logs
        "$real_chown" nginx:nginx /vault/cache/depot
    else
        "$real_chown" -R nginx:nginx /vault
        su -s /bin/sh -c ': > /vault/logs/event.log' nginx
        if [ "$1" = old0750 ]; then
            su -s /bin/sh -c 'chmod 0750 /vault/cache /vault/tmp /vault/logs' nginx
        fi
    fi
}

reset_victims() {
    rm -rf /etc/rig-created /etc/rig-master /etc/rig-dir /etc/rig-victim
    "$real_mkdir" /etc/rig-dir /etc/rig-victim
    chmod 0755 /etc/rig-dir
    chmod 0700 /etc/rig-victim
}
damage() {
    d=""
    [ -e /etc/rig-created ] && d="$d /etc/rig-created created;"
    [ -e /etc/rig-master ] && d="$d /etc/rig-master created;"
    [ "$(stat -c '%U:%G %a' /etc/rig-victim) $(find /etc/rig-victim -mindepth 1 | wc -l)" = "root:root 700 0" ] \
        || d="$d /etc/rig-victim now '$(stat -c '%U:%G %a' /etc/rig-victim) $(find /etc/rig-victim -mindepth 1 | tr '\n' ' ')';"
    [ "$(stat -c '%U:%G %a' /etc/rig-dir) $(find /etc/rig-dir -mindepth 1 | wc -l)" = "root:root 755 0" ] \
        || d="$d /etc/rig-dir now '$(stat -c '%U:%G %a' /etc/rig-dir) $(find /etc/rig-dir -mindepth 1 | tr '\n' ' ')';"
    printf '%s' "$d"
}

# conf_for <value>: the two marked access_log lines 25- edits, as rendered.
conf_for() {
    cat > /etc/nginx/nginx.conf <<CONF
user nginx;
pid /tmp/rig-nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 16; }
http {
    log_format vault_event '\$remote_addr';
    client_body_temp_path tmp/client_body;
    server {
        listen 127.0.0.1:8099;
        location /a/ {
            access_log $1 vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE
            return 204;
        }
        location /b/ {
            access_log $1 vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE
            return 204;
        }
    }
}
CONF
}

# scenario <name> <layout> <VAULT_EVENT_LOG> <expect: boot|refuse> [refusing hook]
scenario() {
    name=$1; lay=$2; value=$3; expect=$4; refuser=${5:-25-vault-eventlog.sh}
    layout "$lay"
    reset_victims
    conf_for "$value"
    printf '%s\n' "$value" > /tmp/rig.value
    : > /tmp/rig.attacks
    rm -f /tmp/rig.childswap
    [ -z "${CHILDSWAP:-}" ] || : > /tmp/rig.childswap
    out=/tmp/rig.$name.out
    rc=0
    (
        export PATH="/tmp/rigbin:$PATH" VAULT_EVENT_LOG="$value"
        if [ "$HAVE21" = yes ]; then sh "$HOOKS/21-vault-volume-ownership.sh"; fi
        sh "$HOOKS/25-vault-eventlog.sh"
    ) > "$out" 2>&1 || rc=$?
    : > /tmp/rig.value          # no more attacks from the wrappers
    rm -f /tmp/rig.childswap

    # window (b): uid 101 swaps the log and a temp dir for links, root
    # runs the master's start-up.
    master=skipped
    if [ "$rc" = "0" ]; then
        if [ -n "$value" ]; then
            d=$(dirname "$value")
            if su -s /bin/sh -c "ln -s /etc/rig-master '$d/.rig-swap' && mv -f '$d/.rig-swap' '$value'" nginx 2>/dev/null; then
                echo "master-swap ok" >> /tmp/rig.attacks
            else
                rm -f "$d/.rig-swap" 2>/dev/null
                echo "master-swap denied" >> /tmp/rig.attacks
            fi
        fi
        if su -s /bin/sh -c "{ [ ! -e /vault/tmp/client_body ] || mv /vault/tmp/client_body /vault/tmp/.rig-old; } && ln -s /etc/rig-dir /vault/tmp/client_body" nginx 2>/dev/null; then
            echo "master-tmp-swap ok" >> /tmp/rig.attacks
        else
            echo "master-tmp-swap denied" >> /tmp/rig.attacks
        fi
        nginx -t -p /vault -c /etc/nginx/nginx.conf >> "$out" 2>&1 && master=ok || master=failed
    fi

    tried=$(grep -c . /tmp/rig.attacks || true)
    won=$(grep -c ' ok$' /tmp/rig.attacks || true)
    hurt=$(damage)
    why=""
    [ "$tried" -ge 1 ] || why="$why no attack ran (this proves nothing);"
    [ -z "$hurt" ] || why="$why DAMAGE:$hurt"
    case "$expect" in
        boot)
            [ "$rc" = "0" ] || why="$why hooks refused (exit $rc);"
            [ "$won" = "0" ] || why="$why uid 101 won $won attack(s): $(grep ' ok$' /tmp/rig.attacks | tr '\n' ' ');"
            [ "$master" = ok ] || why="$why nginx -t $master;"
            if [ -z "$why" ]; then
                for p in /vault /vault/cache /vault/tmp /vault/logs ${value:+"$(dirname "$value")"}; do
                    [ "$(stat -c '%u:%g %a' "$p")" = "0:0 755" ] || why="$why $p is $(stat -c '%u:%g %a' "$p");"
                done
                if [ -n "$value" ]; then
                    [ -f "$value" ] && [ ! -L "$value" ] && [ "$(stat -c '%u:%g' "$value")" = "101:101" ] \
                        || why="$why $value is not a regular 101:101 file ($(stat -c '%u:%g %F' "$value" 2>&1));"
                fi
            fi ;;
        refuse)
            [ "$rc" != "0" ] || why="$why the hooks accepted it (exit 0), expected a refusal;"
            grep -qF "$refuser: FATAL" "$out" || why="$why the refusal did not come from $refuser;" ;;
    esac
    if [ -z "$why" ]; then
        echo "$name OK ($expect): $tried attack(s) by uid 101, $won got through, no damage$( [ "$expect" = refuse ] && printf ', %s' "$(grep -F "$refuser: FATAL" "$out" | head -n1 | cut -c1-140)")"
    else
        echo "FAIL ($name):$why"
        echo "    attacks: $(tr '\n' ' ' < /tmp/rig.attacks)"
        sed 's/^/    hook: /' "$out" | head -n 20
        status=1
    fi
}

echo "hooks under test: $HOOKS (21-vault-volume-ownership.sh: $HAVE21)"
scenario S1-new-default    new /vault/logs/event.log       boot
scenario S2-upgrade        old /vault/logs/event.log       refuse 25-vault-eventlog.sh
scenario S3-new-nested     new /vault/ev/sub/event.log     boot
scenario S4-upgrade-nested old /vault/ev/sub/event.log     boot
scenario S5-under-depot    new /vault/cache/depot/x.log    refuse 25-vault-eventlog.sh
scenario S6-upgrade-log-off old ""                         boot
CHILDSWAP=1; scenario S7-upgrade-child-swap old0750 /vault/logs/event.log refuse 21-vault-volume-ownership.sh
CHILDSWAP=
exit "$status"
