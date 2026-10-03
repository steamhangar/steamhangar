#!/bin/sh
# SteamHangar vault-core container hook -- who may rename names on the
# cache volume (WP SEC-FIX-5).
#
# Runs from the official nginx image's /docker-entrypoint.d/ as root, after
# 20-envsubst-on-templates.sh and BEFORE every hook that touches /vault
# (25-vault-eventlog.sh, 29-vault-build-version.sh, 40-vault-preflight.sh) and
# before the nginx master starts. docker-entrypoint.sh runs with `set -e`, so a
# non-zero exit here stops the container.
#
# --- The problem -------------------------------------------------------------
# Root acts on names inside /vault: these hooks create and chown files there,
# and the nginx master (root) opens the event log with O_APPEND|O_CREAT and
# creates/chowns/chmods its temp directories under tmp/ BY NAME at every start,
# following symlinks. vault-api and the nginx workers both run as uid 101 and
# share this volume. Up to SEC-FIX-5 the image made /vault, /vault/logs,
# /vault/cache and /vault/tmp belong to uid 101 (mode 0755, no sticky bit), so
# uid 101 could swap any of those names for a symlink at any moment and make
# root create, truncate, append to, chown or chmod any file in this container
# (the CVE-2016-1247 class; review finding of WP VER-2). No re-check in a
# shell hook closes that: the master opens the log after every hook has run.
#
# --- The fix: ownership -------------------------------------------------------
# Every directory whose ENTRIES root resolves belongs to root, mode 0755:
#
#   /vault                 root:root 0755
#   /vault/cache           root:root 0755
#   /vault/cache/depot     101:101   (the workers' proxy_store tree, vault-api GC)
#   /vault/tmp             root:root 0755
#   /vault/tmp/<nginx temp dirs>  101:101 0700 (created here when missing)
#   /vault/logs            root:root 0755
#   /vault/logs/event.log  101:101   (25-vault-eventlog.sh; vault-api truncates it)
#   /vault/logs/vault-core-version.json  root:root 0644 (29-vault-build-version.sh)
#
# uid 101 keeps write access to exactly what it writes (the depot tree, the
# temp dirs, the event log's CONTENT) and loses the ability to rename any name
# root resolves. That closes both the hook windows and the master's open.
#
# --- Upgrade (a volume or bind mount from before SEC-FIX-5) ------------------
# This hook migrates at every start: `chown -h 0:0` and `chmod 0755` on
# /vault, then on each of cache/, tmp/, logs/. Order matters. /vault is the
# mount point (its name lives in the container's root-owned /), so it cannot
# be swapped; once it belongs to root and is 0755, uid 101 can no longer
# rename cache/, tmp/ or logs/, and only then are they checked (not a
# symlink, a directory) and fixed. Each level is PROBED before the next one
# is touched: as uid 101 the hook tries to create a symlink in the directory
# it just fixed. If that works anyway (an ACL, a filesystem that ignores Unix
# permissions, a chmod that silently did nothing), the names below are not
# stable and the hook refuses before acting on them. The probe tries both a
# symlink and a directory under a random name, and counts only "Permission
# denied" / "Operation not permitted" as closed: any other failure (a name
# that already exists, a read-only filesystem, a missing tool) is
# inconclusive and refuses too, and so does a probe name that already exists.
#
# Refusal, not a warning: whenever this cannot be made true (chown not
# permitted -- no CAP_CHOWN, a root-squashing NFS export, files owned by a
# uid outside a user-namespaced daemon's mapping --, a mode that does not
# stick, a planted symlink, an open or inconclusive probe), the hook stops the
# container with host-side commands. Starting anyway would mean running a
# root nginx master on a volume uid 101 can rewire; a stopped cache is the
# safer failure, and the fix is two commands. User decision 2026-10-03
# ("Ist okay"), recorded in the ADR-0016 addendum of that date. A symlink
# found on one of these names is never removed
# automatically: on a volume where it could only have been planted (nothing
# in SteamHangar creates one), the operator should look before it goes.
#
# Missing cache/ or tmp/ are left to 40-vault-preflight.sh, which refuses with
# "mount the cache volume" (a bind of the wrong, empty host directory should
# fail loudly, not become a fresh cache). A missing logs/ is created here.
#
# Usage: the stock entrypoint runs it with no argument. Tests:
# core/tests/volume-ownership-race-rig.sh (in the pinned image) and
# .github/scripts/verify-core-nginx.sh (real entrypoint chain, refusals).

set -eu
LC_ALL=C
export LC_ALL

ME="21-vault-volume-ownership.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

PREFIX=/vault
WORKER_USER=nginx

# vault_root_only_dir <dir>: true iff <dir> is a real directory (not a
# symlink), owned by uid 0 and writable by neither group nor others, i.e. only
# root can create, rename or remove names in it. Byte-identical copies live in
# 25-vault-eventlog.sh and 29-vault-build-version.sh (pinned by
# core/tests/test-root-only-dir.sh).
vault_root_only_dir() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    _vrod=$(stat -c '%u %a' "$1") || return 1
    [ "${_vrod%% *}" = "0" ] || return 1
    [ $(( 0${_vrod#* } & 022 )) -eq 0 ] || return 1
    return 0
}

if [ "$(id -u)" != "0" ]; then
    log "not running as root: no root process will act on $PREFIX, nothing to enforce"
    exit 0
fi
id "$WORKER_USER" >/dev/null 2>&1 || die "no '$WORKER_USER' user in this image"
WORKER_IDS="$(id -u "$WORKER_USER"):$(id -g "$WORKER_USER")"

host_fix() {
    cat <<EOF
  Fix it on the Docker host, then start again (<dir> is VAULT_CACHE_PATH, or
  the named volume's directory from 'docker volume inspect'):
      chown root:root <dir> <dir>/cache <dir>/tmp <dir>/logs
      chmod 0755 <dir> <dir>/cache <dir>/tmp <dir>/logs
  These owners assume a plain Docker daemon. With userns-remap, use the
  host uids the container's root and uid $WORKER_IDS map to (the subordinate
  id base from /etc/subuid, and base+101) instead of root and 101. On a
  root-squashing NFS export, run the commands on the NFS server.
  (deploy/README.md "Using a dedicated cache mount"). vault-core refuses to
  start rather than run its root nginx master on a volume uid $WORKER_IDS can
  rewire (SEC-FIX-5).
EOF
}

# shown <text>: a path or link target as it may appear in a message -- only
# [A-Za-z0-9/._+ -], everything else (newlines, escapes, quotes) as '?'.
shown() { printf '%s' "$1" | tr -c 'A-Za-z0-9/._+ -' '?'; }

# ensure_root_dir <dir>: make an existing, verified-real directory root:root
# 0755. Callers have checked that <dir> is not a symlink, and its parent is
# root-only, so the name cannot change between that check and these calls
# (chmod has no -h; it would follow a link). chown first: without CAP_FOWNER
# root may chmod only a directory it owns.
ensure_root_dir() {
    _d=$1
    _before=$(stat -c '%u:%g %a' "$_d")
    if [ "${_before%% *}" != "0:0" ]; then
        if ! _err=$(chown -h 0:0 "$_d" 2>&1); then
            die "cannot make $_d owned by root ($_err).
$(host_fix)"
        fi
    fi
    if [ "${_before#* }" != "755" ]; then
        if ! _err=$(chmod 0755 "$_d" 2>&1); then
            die "cannot set $_d to mode 0755 ($_err).
$(host_fix)"
        fi
    fi
    _after=$(stat -c '%u:%g %a' "$_d")
    if [ "$_after" != "0:0 755" ]; then
        die "$_d is still '$_after' after chown/chmod, expected '0:0 755'.
$(host_fix)"
    fi
    if [ "$_before" != "$_after" ]; then
        log "$_d: $_before -> $_after (migrated)"
    fi
}

# probe_closed <dir>: uid 101 must not be able to create a name in <dir>,
# neither a symlink nor a directory. Only EACCES/EPERM count as "closed"
# (review of SEC-FIX-5: with a fixed name, an EEXIST from a name planted
# earlier read as closed). The name is random, and one that exists anyway is
# refused before the probe runs.
probe_closed() {
    _rand=$(od -An -N8 -tx1 /dev/urandom | tr -dc '0-9a-f')
    [ -n "$_rand" ] || die "could not read /dev/urandom for the ownership probe of $1."
    _p="$1/.vault-owner-probe.$_rand"
    if [ -e "$_p" ] || [ -L "$_p" ]; then
        die "the probe name $(shown "$_p") already exists in $1. Nothing in SteamHangar
  creates it; inspect $1 and remove it, then start again.
$(host_fix)"
    fi
    for _how in "ln -s /nonexistent" "mkdir"; do
        if _perr=$(su -s /bin/sh -c "$_how '$_p'" "$WORKER_USER" 2>&1); then
            su -s /bin/sh -c "rm -rf '$_p'" "$WORKER_USER" 2>/dev/null || rm -rf "$_p"
            die "$1 is root:root 0755, yet uid $WORKER_IDS could still create a name in
  it ($_how). An ACL, or a filesystem that ignores Unix permissions: every
  name in it could be swapped for a symlink that vault-core's root processes
  would follow. Remove the ACL or move the cache to a filesystem with Unix
  permissions.
$(host_fix)"
        fi
        case "$_perr" in
            *"Permission denied"*|*"Operation not permitted"*) : ;;
            *) die "the ownership probe in $1 is inconclusive: uid $WORKER_IDS '$_how' failed
  with '$(shown "$_perr")', not with 'Permission denied'. Refusing rather than
  reading an unexpected failure as 'closed'.
$(host_fix)" ;;
        esac
    done
}

# --- /vault itself -----------------------------------------------------------
[ -e "$PREFIX" ] || [ -L "$PREFIX" ] || die "$PREFIX does not exist. Mount the SteamHangar cache volume at $PREFIX."
[ ! -L "$PREFIX" ] || die "$PREFIX is a symlink. Mount the cache volume at $PREFIX directly."
[ -d "$PREFIX" ] || die "$PREFIX is not a directory. Mount the cache volume at $PREFIX."
ensure_root_dir "$PREFIX"
probe_closed "$PREFIX"

# --- cache/, tmp/, logs/ -------------------------------------------------------
for name in cache tmp logs; do
    p="$PREFIX/$name"
    if [ -L "$p" ]; then
        die "$p is a symlink (to '$(shown "$(readlink "$p")")'). Nothing in SteamHangar creates one;
  it may have been planted while $PREFIX still belonged to uid $WORKER_IDS.
  Inspect it, replace it with a real directory, then start again. vault-core's
  root processes would otherwise follow it."
    fi
    if [ ! -e "$p" ]; then
        if [ "$name" = logs ]; then
            # Plain mkdir in a root-only parent: nobody else can create the
            # name first. Created root:root by construction.
            mkdir "$p"
            chmod 0755 "$p"
            log "created $p (root:root 0755)"
        fi
        continue
    fi
    [ -d "$p" ] || die "$p exists but is not a directory. Replace it with a directory."
    ensure_root_dir "$p"
    probe_closed "$p"
done

# ensure_worker_dir <dir> <mode>: a directory the workers write into, owned by
# uid 101. Created when missing (its parent is root-only and probed, so the
# mkdir/chown -h/chmod act on the name just created). An existing one is left
# alone: its ownership is the operator's (cache/depot) or nginx's (temp dirs),
# and 40-vault-preflight.sh probes that the workers can write it.
ensure_worker_dir() {
    _w=$1
    if [ -L "$_w" ]; then
        die "$_w is a symlink (to '$(shown "$(readlink "$_w")")'). vault-core's root nginx master
  would follow it. Replace it with a real directory, then start again."
    fi
    if [ ! -e "$_w" ]; then
        # chmod BEFORE chown: vault-core has no CAP_FOWNER (deploy/compose.yaml
        # drops ALL and adds CHOWN, not FOWNER), so root may chmod only what
        # it owns. In the other order every first start failed here, once
        # per missing directory, until restarts had created them all
        # (measured in verify-stack, WP SEC-FIX-5).
        mkdir "$_w"
        chmod "$2" "$_w"
        chown -h "$WORKER_USER:$WORKER_USER" "$_w"
        log "created $_w ($WORKER_IDS $2)"
        return 0
    fi
    [ -d "$_w" ] || die "$_w exists but is not a directory. Replace it with a directory."
}

if [ -d "$PREFIX/cache" ]; then
    ensure_worker_dir "$PREFIX/cache/depot" 0755
fi
if [ -d "$PREFIX/tmp" ]; then
    # Every temp path core/docker/nginx.conf.template sets. The nginx master
    # creates them too, and then chowns/chmods them by name: safe only because
    # tmp/ is root-only now. Pre-created here so the preflight can probe
    # tmp/proxy as the worker user before nginx exists.
    for t in proxy client_body fastcgi uwsgi scgi; do
        ensure_worker_dir "$PREFIX/tmp/$t" 0700
    done
fi

for d in "$PREFIX" "$PREFIX/cache" "$PREFIX/tmp" "$PREFIX/logs"; do
    [ -d "$d" ] || continue
    vault_root_only_dir "$d" || die "$d is not root-only after this hook ran ($(stat -c '%u:%g %a' "$d"))."
done
log "volume ownership OK: $PREFIX, cache/, tmp/, logs/ are root:root 0755; uid $WORKER_IDS cannot rename names root opens"
