#!/bin/sh
# SteamHangar vault-core container hook -- optional cache-event log toggle
# (WP 3.10, ADR-0008).
#
# Runs from the official nginx image's /docker-entrypoint.d/ hook directory,
# AFTER 20-envsubst-on-templates.sh has rendered nginx.conf.template ->
# nginx.conf (envsubst already substituted ${VAULT_EVENT_LOG} with whatever
# the environment holds, INCLUDING an empty string if the variable is unset
# or set to "" -- Compose/`docker run -e` always EXPORT the key, so envsubst
# treats it as "defined but empty", never as "missing" -- see
# core/docker/nginx.conf.template's header), and BEFORE
# 40-vault-preflight.sh's own checks and before nginx itself is exec'd (this
# script is named to sort between the two: 20- then 25- then 40-).
# docker-entrypoint.sh runs with `set -e`, so a non-zero exit here aborts
# container start, same as every other hook in this directory.
#
# --- Why this can't be handled by envsubst alone ----------------------------
# An empty ${VAULT_EVENT_LOG} substitution leaves:
#     access_log  vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE
# which nginx parses as "access_log vault_event buffer=64k flush=5s;" -- the
# FORMAT NAME ("vault_event") lands in the PATH argument slot instead, and
# nginx refuses to start with a confusing error, not a clean "logging
# disabled". envsubst has no conditional substitution, so turning "empty
# means off" into an actually-clean no-op requires a second, explicit pass
# over the rendered file -- this script is that pass.
#
# The two identical access_log lines in nginx.conf.template (one in
# location /depot/, one in location @miss) both carry a stable trailing
# comment marker, "# VAULT_EVENT_LOG_LINE", specifically so this script can
# find and remove (or clean up) them by that marker -- never by trying to
# re-parse or guess at the rendered path.

set -eu

ME="25-vault-eventlog.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

CONF="/etc/nginx/nginx.conf"
MARKER="# VAULT_EVENT_LOG_LINE"
VALUE="${VAULT_EVENT_LOG:-}"
WORKER_USER="nginx"

[ -f "$CONF" ] || die "$CONF does not exist -- expected to run after envsubst rendering
  (after /docker-entrypoint.d/20-envsubst-on-templates.sh)."

if [ -z "$VALUE" ]; then
    # --- Feature OFF (ADR-0008: "optional at runtime") ----------------------
    # Remove both marked lines entirely: no access_log directive at all for
    # the event log, so vault-core behaves exactly as if this work package
    # never shipped -- no file is ever created or written to, and the
    # request-processing cost is zero (not even a format-string build).
    sed -i "/${MARKER}\$/d" "$CONF"

    remaining=$(grep -c "$MARKER" "$CONF" 2>/dev/null || true)
    if [ "${remaining:-0}" != "0" ]; then
        die "failed to remove all cache-event-log lines while VAULT_EVENT_LOG is
  unset/empty ($remaining marker(s) remain in $CONF). Refusing to start with a
  half-disabled event log rather than guessing which line survived."
    fi

    # REVIEW FINDING N1: the marker-count check above only proves no MARKER
    # survived -- it says nothing about whether a live "vault_event"
    # access_log directive is still in the file. A line that somehow lost
    # its trailing "# VAULT_EVENT_LOG_LINE" comment (a future edit to the
    # template that drops the marker but keeps the directive, a corrupted
    # render, a hand-edited nginx.conf.template) would sail through the
    # check above with remaining=0 while leaving a real, live event-log
    # access_log behind -- exactly the "half-disabled" state this script
    # exists to prevent. Check for the DIRECTIVE itself too, independent of
    # the marker. Keep the Dockerfile's marker-count build-time assertion as
    # the FIRST line of defense (it catches a template edit before the
    # image even ships); this is the second, runtime one.
    #
    # BLOCKER FIX (WP 5.1 CI review): a bare `grep -c "vault_event"` also
    # matches the `log_format vault_event escape=default ...` declaration
    # itself and the three `$vault_event_*` map names above it -- none of
    # which are access_log directives and all of which are ALWAYS present
    # in the rendered config regardless of VAULT_EVENT_LOG. That made this
    # branch unconditionally die with survivors>0 on every OFF render,
    # i.e. vault-core could never start with its own documented default
    # (empty VAULT_EVENT_LOG, deploy/compose.yaml sets only VAULT_RESOLVER)
    # -- reproduced and confirmed by WP 5.1's CI job, which runs this exact
    # OFF render. Anchored to the DIRECTIVE line itself (`access_log ...
    # vault_event ...`) instead, matching what the marker-based deletion
    # above actually targets.
    survivors=$(grep -cE '^[[:space:]]*access_log[[:space:]].*vault_event' "$CONF" 2>/dev/null || true)
    if [ "${survivors:-0}" != "0" ]; then
        die "VAULT_EVENT_LOG is unset/empty but $survivors access_log
  directive(s) referencing 'vault_event' still remain in $CONF after
  marker-based removal -- a live event-log access_log directive may have
  survived without its marker comment. Refusing to start half-disabled
  rather than guessing which line is safe to ignore."
    fi

    log "VAULT_EVENT_LOG unset/empty -- cache-event log disabled (ADR-0008 optional-at-runtime), no access_log directive rendered"
    exit 0
fi

# --- Feature ON: validate the path -------------------------------------------
# This value is embedded verbatim into nginx.conf by envsubst, so anything
# that could end/open a directive (';', '{', '}') or a quote would be config
# injection -- same class of risk 40-vault-preflight.sh already guards for
# VAULT_RESOLVER, same fix: a strict character allowlist rather than trying
# to enumerate everything that's dangerous. Absolute path only (nginx would
# otherwise resolve a relative one against its prefix, /vault, which works
# but hides the actual location from anyone reading the env var alone).
case "$VALUE" in
    /*) : ;;
    *) die "VAULT_EVENT_LOG='$VALUE' must be an absolute path (start with '/'), or
  empty/unset to disable the cache-event log entirely." ;;
esac
case "$VALUE" in
    *[!A-Za-z0-9/_.-]*)
        die "VAULT_EVENT_LOG='$VALUE' contains characters outside the allowed set
  (letters, digits, '/', '_', '-', '.'). This value is written verbatim into
  nginx.conf; refusing rather than risking config injection." ;;
esac

# REVIEW FINDING N2: the checks above accept ANY absolute path, and this
# script used to `mkdir -p` and `chown` the value's PARENT DIRECTORY (since
# SEC-FIX-5 it creates missing directories root-owned and chowns only the
# file) -- unconstrained, VAULT_EVENT_LOG=/etc/nginx/x.log would hand
# /etc/nginx itself over to the nginx worker user (uid 101), and worse paths
# (/etc, /) are just as syntactically "valid absolute paths". This value
# only ever needs to point somewhere on the /vault volume (core/README.md
# "Docker: VAULT_EVENT_LOG" -- the log lives alongside cache/ and tmp/ on
# the one volume vault-api also mounts), so require that explicitly instead
# of trusting an operator-supplied path to be well-intentioned.
case "$VALUE" in
    /vault/*) : ;;
    *) die "VAULT_EVENT_LOG='$VALUE' must be a path under /vault/ (the shared cache
  volume, e.g. /vault/logs/event.log). Refusing to mkdir/chown a directory
  outside /vault/ for the nginx worker user -- that could hand over an
  unrelated system directory (e.g. /etc/nginx) depending on the value." ;;
esac

# PRE-FREEZE REVIEW S4: the containment check above is a PREFIX glob, and the
# character allowlist admits '.', so '/vault/../etc/x.log' passed both and the
# chown below would have handed /etc to the worker user. Reject any '..'
# component (and '.' components, a trailing '/.' and a trailing '/', none of
# which name a plain file) instead of trying to canonicalise the path --
# there is no legitimate reason for the operator-supplied value to contain
# them.
case "$VALUE" in
    *..*|*/./*|*/.|*/)
        die "VAULT_EVENT_LOG='$VALUE' contains a '..' or '.' path component or ends
  in '/'. The path must name a plain file under /vault/ directly, e.g.
  /vault/logs/event.log -- refusing rather than resolving it." ;;
esac

# Strip only the trailing marker comment -- it has done its job identifying
# the line for this script; nginx would silently ignore it either way (it's
# a valid trailing comment), but removing it keeps the deployed config free
# of a marker that only ever mattered pre-boot.
sed -i "s/[[:space:]]*${MARKER}\$//" "$CONF"

still_marked=$(grep -c "$MARKER" "$CONF" 2>/dev/null || true)
if [ "${still_marked:-0}" != "0" ]; then
    die "failed to strip the cache-event-log marker comment from $CONF while enabling it ($still_marked remain)."
fi

# --- The directory and the file (WP SEC-FIX-5) -------------------------------
# Everything below runs as root, and the nginx master (also root) opens this
# path with O_APPEND|O_CREAT at every start, following symlinks. That is safe
# only if no other uid can rename any name on the way: so every directory
# from /vault down to the log's own directory must be root-only (a real
# directory, uid 0, not group- or world-writable). 21-vault-volume-ownership.sh
# makes /vault and /vault/logs so; a custom VAULT_EVENT_LOG directory that is
# missing is created here, root:root 0755 (plain mkdir in a root-only
# parent); one that exists but belongs to someone else -- e.g. anything under
# cache/depot or tmp/, which uid 101 owns -- is refused, never chowned to root
# (it may hold uid 101's data).
#
# History: up to SEC-FIX-5 this section checked for symlinks and then acted
# (mkdir -p, `: >`, chown) on names inside a directory uid 101 owned, which
# left a window between check and use (pre-freeze review S5 / threat-model
# §9 P-2) and could never cover the master's own open (VER-2 review).
#
# vault_root_only_dir <dir>: true iff <dir> is a real directory (not a
# symlink), owned by uid 0 and writable by neither group nor others, i.e. only
# root can create, rename or remove names in it. Byte-identical copies live in
# 21-vault-volume-ownership.sh and 29-vault-build-version.sh (pinned by
# core/tests/test-root-only-dir.sh).
vault_root_only_dir() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    _vrod=$(stat -c '%u %a' "$1") || return 1
    [ "${_vrod%% *}" = "0" ] || return 1
    [ $(( 0${_vrod#* } & 022 )) -eq 0 ] || return 1
    return 0
}

refuse_dir() {
    die "VAULT_EVENT_LOG='$VALUE': '$1' is not a directory only root can change
  ($2). This hook and the nginx master open the log as root; uid 101 (the nginx
  workers, vault-api) must not be able to rename names on the way there.
  Use a path under /vault/logs/ (the default is /vault/logs/event.log).
  Upgrading with a custom log directory: vault-core before SEC-FIX-5 chowned
  that directory to 101:101 itself. If it holds only the event log, give it
  back to root on the Docker host (chown root:root <dir> && chmod 0755 <dir>,
  <dir> being this path inside VAULT_CACHE_PATH or the named volume) and
  start again; never do that for a directory under cache/depot or tmp/."
}

# shown <text>: a link target as it may appear in a message -- only
# [A-Za-z0-9/._+ -], everything else (newlines, escapes, quotes) as '?'.
shown() { printf '%s' "$1" | tr -c 'A-Za-z0-9/._+ -' '?'; }

event_dir=/vault
vault_root_only_dir "$event_dir" || refuse_dir "$event_dir" "$(stat -c '%u:%g %a' "$event_dir" 2>&1); 21-vault-volume-ownership.sh should have fixed it"
rest=${VALUE#/vault/}
while :; do
    case "$rest" in
        */*) comp=${rest%%/*}; rest=${rest#*/} ;;
        *) break ;;
    esac
    [ -n "$comp" ] || continue      # '//' in the value
    event_dir="$event_dir/$comp"
    if [ -L "$event_dir" ]; then
        refuse_dir "$event_dir" "a symlink to '$(shown "$(readlink "$event_dir")")'"
    fi
    if [ ! -e "$event_dir" ]; then
        mkdir "$event_dir"
        chmod 0755 "$event_dir"
        log "created $event_dir (root:root 0755) for the cache-event log"
    fi
    vault_root_only_dir "$event_dir" || refuse_dir "$event_dir" "$(stat -c '%u:%g %a' "$event_dir" 2>&1)"
done

# The file. Its directory is root-only now, so nothing can change the name
# between these checks and the create/chown below. What can still be there
# is something planted BEFORE this start, while the directory belonged to uid
# 101 (an upgraded volume): a symlink, a FIFO, a hard link. Each is refused,
# not repaired -- nothing in SteamHangar creates one.
if [ -L "$VALUE" ]; then
    die "VAULT_EVENT_LOG='$VALUE' is a symlink (to '$(shown "$(readlink "$VALUE")")'). The
  nginx master opens this path as root and would follow it. Nothing in
  SteamHangar creates one; it may have been planted while the directory still
  belonged to uid 101. Inspect it, remove it, then start again."
fi
if [ -d "$VALUE" ]; then
    die "VAULT_EVENT_LOG='$VALUE' is a directory; it must name a file."
fi
if [ -e "$VALUE" ] && [ ! -f "$VALUE" ]; then
    die "VAULT_EVENT_LOG='$VALUE' exists but is not a regular file ($(stat -c %F "$VALUE")).
  Remove it, then start again."
fi
# PRE-FREEZE REVIEW S3: nginx's master runs as root and opens every access_log
# at startup, so a missing file would be created root:root 0644 -- and
# vault-api's sweeper (api/vault_api/event_sweep.py, uid 101) truncates this
# file in place (ftruncate) after reading it, which then fails with EPERM and
# the file grows without bound. So: create it here if missing and chown it to
# the worker user (uid/gid 101 in this image -- the SAME numeric identity
# api/Dockerfile gives vault-api). nginx opens an EXISTING file O_APPEND
# without changing its owner. Re-chowning an existing file also migrates a
# root-owned log from an earlier image.
[ -e "$VALUE" ] || : > "$VALUE"
links=$(stat -c %h "$VALUE")
if [ "$links" != "1" ]; then
    die "VAULT_EVENT_LOG='$VALUE' has $links hard links; chowning it to the worker user
  would hand every other name of the same file over too. Remove it (a fresh
  log is created at the next start), then start again."
fi
chown -h "$WORKER_USER:$WORKER_USER" "$VALUE"
log "cache-event log ENABLED (ADR-0008): $VALUE (owner $(stat -c %u:%g "$VALUE") in root-only $event_dir, shared with vault-api)"
