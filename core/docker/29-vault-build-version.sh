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
# Who writes what (review M1, WP VER-2 round 2). /vault and /vault/logs
# belong to uid 101, the nginx user here AND vault-api's user, mode 0755 with
# no sticky bit: whoever runs as uid 101 can rename any name in them at any
# moment. No check-then-act a root process does on a name in that directory
# is safe, mktemp included: mktemp creates its file with O_EXCL, but the
# NAME it returns can be swapped for a symlink before the next command opens
# it, and a root `printf >` / `chmod` would then follow the link anywhere
# (reproduced in review: a 0600 root file overwritten and made 0644). So:
#
#   - root does exactly one thing on that volume: create a MISSING logs/
#     directory, with plain `mkdir` (never -p; it fails rather than follows
#     when the name is already a symlink), `chown -h` (never dereferences)
#     to nginx, then a re-check that the name is still not a symlink;
#   - every other file operation (mktemp, write, chmod, symlink removal, the
#     rename, removing a stale file) runs as the nginx user, by re-invoking
#     this script with `--writer` under busybox `su`, the way
#     40-vault-preflight.sh already probes as that user. A link followed by
#     uid 101 reaches only what uid 101 may write anyway.
#   - The writer refuses to run as root, and as root without an nginx user
#     the hook writes nothing (warning), instead of falling back to root.
#
# Atomicity is unchanged: mktemp in the same directory, chmod 0644, `mv -f`
# over the target, so a reader sees either the old or the new file. A symlink
# on the target name is removed first (`mv` onto a symlink to a directory
# would move the file into that directory).
#
# Usage: the stock entrypoint runs it with no argument (sorted after the
# envsubst hook and 25..28, before 40-vault-preflight.sh). Tests pass the
# output file as $1 (core/tests/test-build-version-hook.sh). `--writer OUT`
# is the internal unprivileged half described above.

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

# --- the unprivileged writer (--writer OUT) -----------------------------------
# Every file operation on a name inside OUT_DIR lives here and nowhere else
# (pinned by core/tests/test-build-version-hook.sh). Never runs as root.
if [ "${1:-}" = "--writer" ]; then
    OUT=${2:?--writer needs the output path}
    OUT_DIR=$(dirname -- "$OUT")
    if [ "$(id -u)" = "0" ]; then
        die "the writer half must not run as root (review M1)."
    fi
    # Values come from the root half; re-checked so this half alone can
    # never write anything outside the grammar.
    w_version=${VBV_VERSION:-invalid}
    valid_version "$w_version" || w_version=invalid
    w_commit=${VBV_COMMIT:-unknown}
    case "$w_commit" in
        unknown|invalid) : ;;
        *) valid_commit "$w_commit" || w_commit=invalid ;;
    esac
    w_at=${VBV_RECORDED_AT:-}
    case "$w_at" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) : ;;
        *) w_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) ;;
    esac
    discard_old() {
        if [ -L "$OUT" ] || [ -f "$OUT" ]; then
            rm -f "$OUT" 2>/dev/null || warn "could not remove the old $OUT either; vault-api may show the previous version."
        fi
    }
    write_file() {
        if [ -L "$OUT_DIR" ]; then
            warn "$OUT_DIR is a symlink; refusing to write through it (replace it with a real directory)."
            return 1
        fi
        if [ -d "$OUT" ] && [ ! -L "$OUT" ]; then
            warn "$OUT is a directory; not writing the build version."
            return 1
        fi
        tmp=$(mktemp "$OUT_DIR/.vault-core-version.XXXXXX") || return 1
        if ! printf '{"component":"vault-core","version":"%s","commit":"%s","recorded_at":"%s"}\n' \
                "$w_version" "$w_commit" "$w_at" > "$tmp" \
           || ! chmod 0644 "$tmp"; then
            rm -f "$tmp"
            return 1
        fi
        [ -L "$OUT" ] && rm -f "$OUT"
        if ! mv -f "$tmp" "$OUT"; then
            rm -f "$tmp"
            return 1
        fi
        return 0
    }
    if write_file; then
        exit 0
    fi
    discard_old
    exit 1
fi

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

# --- write it ----------------------------------------------------------------
WRITER_USER=nginx

# Root half: only a missing logs/ directory (see the header, review M1).
prepare_dir() {
    if [ -L "$OUT_DIR" ]; then
        warn "$OUT_DIR is a symlink; refusing to write through it (replace it with a real directory)."
        return 1
    fi
    [ -d "$OUT_DIR" ] && return 0
    # Plain mkdir: if the name became a symlink since the check above, this
    # fails (EEXIST) instead of creating anything behind it.
    mkdir "$OUT_DIR" || return 1
    if [ "$(id -u)" = "0" ] && id "$WRITER_USER" >/dev/null 2>&1; then
        # -h: if the new directory was already swapped for a symlink, this
        # changes the link's owner, never its target's.
        chown -h "$WRITER_USER:$WRITER_USER" "$OUT_DIR" || return 1
    fi
    if [ -L "$OUT_DIR" ] || [ ! -d "$OUT_DIR" ]; then
        warn "$OUT_DIR changed while it was being created; not writing."
        return 1
    fi
    return 0
}

# Run the writer half as the nginx user (or as the caller when not root).
run_writer() {
    VBV_VERSION=$version
    VBV_COMMIT=$commit
    VBV_RECORDED_AT=$recorded_at
    export VBV_VERSION VBV_COMMIT VBV_RECORDED_AT
    if [ "$(id -u)" != "0" ]; then
        sh "$0" --writer "$OUT"
        return
    fi
    if ! id "$WRITER_USER" >/dev/null 2>&1; then
        warn "running as root and there is no '$WRITER_USER' user to write as; not writing (never as root)."
        return 1
    fi
    case "$0$OUT" in
        *\'*) warn "a quote in the script or output path; not writing."; return 1 ;;
    esac
    # busybox su without -l keeps the environment, so the VBV_* values
    # reach the writer; the test-core run checks the written content.
    su -s /bin/sh -c "exec sh '$0' --writer '$OUT'" "$WRITER_USER"
}

if prepare_dir && run_writer; then
    log "build version recorded for vault-api's GET /v1/about: version $version, commit $commit ($OUT)"
else
    warn "could not write $OUT; vault-api will report no vault-core version. The cache starts anyway."
fi
exit 0
