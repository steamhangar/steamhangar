#!/bin/sh
# SteamHangar vault-core container hook -- record the build version for
# vault-api's GET /v1/about (WP VER-2).
#
# Writes ONE small JSON file into the shared cache volume at every start:
#
#   /vault/logs/vault-core-version.json
#   {"component":"vault-core","version":"0.1.0-rc9","commit":"<sha>","recorded_at":"2026-10-03T10:00:00Z"}
#
# vault-api mounts the same volume and reads the file. Why a file and not an
# HTTP endpoint: vault-api has no network path to vault-core, by design (the
# egress lock, docs/adr/0011-egress-lock.md: "every cross-container fact it
# needs already comes through a shared volume"). User decision "Weg A",
# 2026-10-03. Consequence, stated in /v1/about: the file says which version
# STARTED last, not whether vault-core runs now.
#
# Placement: /vault/logs/ is outside the document root (`root cache;` =
# /vault/cache), so nginx never serves the file, and it is on the volume
# vault-api mounts. Deliberately no `location = /vault-version`: the version
# would then be readable by every LAN client without a key, while this
# server sends a bare `Server: nginx` on purpose; the authenticated
# GET /v1/about is the one place that shows it.
#
# Input: VAULT_BUILD_VERSION / VAULT_BUILD_COMMIT (baked by core/Dockerfile,
# WP VER-1; `docker run -e` can override them, so they are checked HERE, where
# they are written). Same grammar as vault_api.build_info():
#   version: 1-64 chars of [0-9A-Za-z._+-], first char a letter or digit
#   commit:  7-40 chars of [0-9a-f], or the literal Dockerfile default "unknown"
# A value outside the grammar (including blank or unset) is written as the
# word "invalid" -- fail closed, never the raw value. The values are written
# into the JSON verbatim only AFTER that check, and the grammar contains no
# character JSON would need to escape.
#
# Failure policy: the version is informational, so an I/O problem (read-only
# volume, a directory squatting on the name) does NOT stop the cache. It
# logs a WARNING and removes the old file if it can, so vault-api reports
# "no version recorded" instead of a stale one. The ONE refusal is a
# configuration error: VAULT_EVENT_LOG naming the same file. Writing would
# replace the cache-event log with this JSON at every start (and vault-api's
# sweeper would read it), so the hook stops the boot instead.
#
# Who writes what (WP SEC-FIX-5; supersedes VER-2 review M1's split). The
# hook writes as whoever runs it. As root it writes ONLY into a directory
# chain no other uid can change: the output directory and every ancestor up
# to / must be a real directory, owned by uid 0, writable by neither group nor
# others (vault_root_only_dir). 21-vault-volume-ownership.sh makes /vault and
# /vault/logs so before this hook runs. Then nobody can swap a name in the
# directory between root's mktemp, write, chmod and rename, so root may do all
# of them, and the file is root:root 0644 (vault-api only reads it).
#
#   - Chain not root-only (a volume 21- did not fix, a test path): WARNING,
#     nothing written, nothing removed. Not even a stale file is removed:
#     `rm` on a name in a directory another uid controls is the same class of
#     mistake.
#   - Output directory missing: created with plain `mkdir` (never -p) and
#     chmod 0755, only if its parent chain is root-only.
#   - Not root (tests, a native run): no chain check. A link followed by a
#     non-root caller reaches only what that caller may write anyway.
#
# VER-2's design (root only creates logs/, an `su nginx` writer does the rest)
# fit a 101-owned logs/; with a root-owned logs/ the nginx user can no longer
# create the temp file, and root writing into a root-only directory is the
# simpler safe form.
#
# Atomicity is unchanged: mktemp in the same directory, chmod 0644, `mv -f`
# over the target, so a reader sees either the old or the new file. A symlink
# on the target name is removed first (`mv` onto a symlink to a directory
# would move the file into that directory).
#
# Usage: the stock entrypoint runs it with no argument (sorted after the
# envsubst hook and 21..28, before 40-vault-preflight.sh). Tests pass the
# output file as $1 (core/tests/test-build-version-hook.sh,
# core/tests/build-version-race-rig.sh).

set -eu
LC_ALL=C
export LC_ALL

ME="29-vault-build-version.sh"

log()  { echo "$ME: $*"; }
warn() { echo "$ME: WARNING: $*" >&2; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

valid_version() {
    v=$1
    [ -n "$v" ] || return 1
    [ "${#v}" -le 64 ] || return 1
    case "$v" in
        [!0-9A-Za-z]*) return 1 ;;
        *[!0-9A-Za-z._+-]*) return 1 ;;
    esac
    return 0
}

valid_commit() {
    c=$1
    [ "${#c}" -ge 7 ] && [ "${#c}" -le 40 ] || return 1
    case "$c" in
        *[!0-9a-f]*) return 1 ;;
    esac
    return 0
}

# vault_root_only_dir <dir>: true iff <dir> is a real directory (not a
# symlink), owned by uid 0 and writable by neither group nor others, i.e. only
# root can create, rename or remove names in it. Byte-identical copies live in
# 21-vault-volume-ownership.sh and 25-vault-eventlog.sh (pinned by
# core/tests/test-root-only-dir.sh).
vault_root_only_dir() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    _vrod=$(stat -c '%u %a' "$1") || return 1
    [ "${_vrod%% *}" = "0" ] || return 1
    [ $(( 0${_vrod#* } & 022 )) -eq 0 ] || return 1
    return 0
}

# root_chain_bad <dir>: prints the first component of <dir>'s chain (itself,
# then each ancestor up to /) that is NOT root-only; prints nothing and
# returns 1 when the whole chain is root-only.
root_chain_bad() {
    _c=$1
    while :; do
        if ! vault_root_only_dir "$_c"; then
            printf '%s' "$_c"
            return 0
        fi
        [ "$_c" = / ] && return 1
        _c=$(dirname -- "$_c")
    done
}

OUT="${1:-/vault/logs/vault-core-version.json}"
OUT_DIR=$(dirname -- "$OUT")

# --- collision with the cache-event log (config error, refuse) --------------
# Compare with repeated slashes collapsed: 25-vault-eventlog.sh already
# refuses '.', '..' and a trailing '/', but accepts '//'.
squash() { printf '%s' "$1" | tr -s '/'; }
if [ -n "${VAULT_EVENT_LOG:-}" ] && [ "$(squash "$VAULT_EVENT_LOG")" = "$(squash "$OUT")" ]; then
    die "VAULT_EVENT_LOG='$VAULT_EVENT_LOG' names the build-version file this hook
  writes at every start ($OUT). That would overwrite the cache-event log. Point
  VAULT_EVENT_LOG at another file, e.g. /vault/logs/event.log."
fi

# --- the values ---------------------------------------------------------------
version=invalid
if [ -n "${VAULT_BUILD_VERSION+x}" ] && valid_version "$VAULT_BUILD_VERSION"; then
    version=$VAULT_BUILD_VERSION
else
    warn "VAULT_BUILD_VERSION is unset or not a valid version; recording 'invalid'."
fi
commit=unknown
if [ -n "${VAULT_BUILD_COMMIT+x}" ] && [ "$VAULT_BUILD_COMMIT" != "unknown" ]; then
    if valid_commit "$VAULT_BUILD_COMMIT"; then
        commit=$VAULT_BUILD_COMMIT
    else
        commit=invalid
        warn "VAULT_BUILD_COMMIT is not a commit id; recording 'invalid'."
    fi
fi
recorded_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# --- where root may write (SEC-FIX-5) -----------------------------------------
# Returns 1 (with a WARNING) when root must not touch the directory at all.
root_may_write() {
    [ "$(id -u)" = "0" ] || return 0
    case "$OUT_DIR" in
        /*) : ;;
        *) warn "$OUT is not an absolute path; as root, not writing."; return 1 ;;
    esac
    if [ -L "$OUT_DIR" ]; then
        warn "$OUT_DIR is a symlink; refusing to write through it (replace it with a real directory)."
        return 1
    fi
    if [ ! -e "$OUT_DIR" ]; then
        bad=$(root_chain_bad "$(dirname -- "$OUT_DIR")") || bad=""
        if [ -n "$bad" ]; then
            warn "$bad is not a root-only directory ($(stat -c '%u:%g %a' "$bad" 2>&1)); as root, not creating $OUT_DIR in it (SEC-FIX-5)."
            return 1
        fi
        # Plain mkdir in a root-only parent: nobody else can create the name.
        mkdir "$OUT_DIR" || return 1
        chmod 0755 "$OUT_DIR" || return 1
    fi
    bad=$(root_chain_bad "$OUT_DIR") || bad=""
    if [ -n "$bad" ]; then
        warn "$bad is not a root-only directory ($(stat -c '%u:%g %a' "$bad" 2>&1)); another uid could swap names in it, so root writes nothing there (SEC-FIX-5). vault-api may show the previous version."
        return 1
    fi
    return 0
}

# --- the write -------------------------------------------------------------------
# mktemp, write, chmod 0644, rename over the target. Every name here lives in
# OUT_DIR, which is root-only when this runs as root (root_may_write).
write_file() {
    if [ -L "$OUT_DIR" ]; then
        warn "$OUT_DIR is a symlink; refusing to write through it (replace it with a real directory)."
        return 1
    fi
    if [ ! -d "$OUT_DIR" ]; then
        mkdir "$OUT_DIR" || return 1
    fi
    if [ -d "$OUT" ] && [ ! -L "$OUT" ]; then
        warn "$OUT is a directory; not writing the build version."
        return 1
    fi
    tmp=$(mktemp "$OUT_DIR/.vault-core-version.XXXXXX") || return 1
    if ! printf '{"component":"vault-core","version":"%s","commit":"%s","recorded_at":"%s"}\n' \
            "$version" "$commit" "$recorded_at" > "$tmp" \
       || ! chmod 0644 "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if [ -L "$OUT" ]; then
        rm -f "$OUT"
    fi
    if ! mv -f "$tmp" "$OUT"; then
        rm -f "$tmp"
        return 1
    fi
    return 0
}

discard_old() {
    if [ -L "$OUT" ] || [ -f "$OUT" ]; then
        rm -f "$OUT" 2>/dev/null || warn "could not remove the old $OUT either; vault-api may show the previous version."
    fi
}

if ! root_may_write; then
    warn "could not write $OUT; vault-api will report no or an old vault-core version. The cache starts anyway."
    exit 0
fi
if write_file; then
    log "build version recorded for vault-api's GET /v1/about: version $version, commit $commit ($OUT, owner $(stat -c %u:%g "$OUT"))"
else
    discard_old
    warn "could not write $OUT; vault-api will report no vault-core version. The cache starts anyway."
fi
exit 0
