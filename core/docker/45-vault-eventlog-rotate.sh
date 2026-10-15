#!/bin/sh
# SteamHangar vault-core container hook -- size-based rotation of the
# cache-event log (WP D15-ROT, ADR-0008 addendum 2026-10-13).
#
# The event log (VAULT_EVENT_LOG, 25-vault-eventlog.sh) used to be rotated
# only by vault-api's sweeper, by truncating it in place -- which a
# deployment can deny (the file then grows without bound; measured on a
# production instance at 41 MB and climbing, 2026-10-13). User decision
# 2026-10-13: vault-core rotates the log itself, at 64 MiB, and keeps ONE
# old file -- at most about 2 x 64 MiB on disk.
#
# How (rename rotation, never copytruncate):
#   1. find the running nginx master (pid file, verified via /proc);
#   2. mv event.log -> event.log.1 (one rename(2) in the same directory;
#      a previous event.log.1 is removed first). nginx keeps writing into
#      the renamed file through its open fd until step 4, so nothing is lost;
#   3. create a fresh event.log owned by the worker user (101:101 0644),
#      the same file 25-vault-eventlog.sh creates at start (vault-api's
#      sweeper must be able to open it; the master would otherwise create it
#      root:root on reopen);
#   4. SIGUSR1 to the master (`nginx -s reopen`): it flushes the buffered
#      lines into the old file, reopens the name and tells the workers to do
#      the same. From then on lines land in the new event.log.
# A reader that follows the file by inode (vault-api's sweeper, WP D15b)
# finishes the tail of event.log.1 and then switches. Nothing in this hook
# truncates or rewrites a byte of either file.
#
# Why a background loop started from a hook: the image's PID 1 is the nginx
# master itself (the stock /docker-entrypoint.sh execs it), there is no init
# and no cron in the image, and an extra process manager would be a second
# PID 1. The loop is forked here, before the exec, so it is re-parented to
# the nginx master, which reaps any child it does not know (no zombie). It
# dies with the container. It never signals anything but a process whose
# /proc cmdline says "nginx: master process", and it holds no lock nginx
# waits on; an `nginx -s reload` in between changes nothing (same master pid,
# the reload reopens the same name).
#
# Inputs (read at start; since D16 the size is editable in Settings: compose
# forwards VAULT_EVENT_LOG_ROTATE_BYTES as an env pin, else vault-api's core
# settings file supplies it and the loop re-reads it, see "D16c" below):
#   VAULT_EVENT_LOG                  -- empty: the log is off, so is this.
#   VAULT_EVENT_LOG_ROTATE_BYTES     -- rotate at or above this size.
#                                       Empty/unset = 67108864 (64 MiB),
#                                       0 = rotation off, otherwise
#                                       1048576 (1 MiB) .. 999999999999999.
#   VAULT_EVENT_LOG_ROTATE_INTERVAL  -- seconds between size checks,
#                                       empty/unset = 60, 1..3600.
# An invalid value stops the boot (same fail-closed policy as 26-..28-),
# also for an `nginx -t` run.
#
# When the loop is NOT started (with a log line saying why): event log off,
# rotation off, or the container command is not the server itself (an
# `nginx -t`, `-T`, `-s`, `-v`, `-V`, `-h` run, or no `nginx` command at all).
# The last check reads the entrypoint's argument list from
# /proc/$PPID/cmdline -- a hook gets no arguments from the stock entrypoint.
# Without it, a background loop forked by a one-shot `nginx -t` would keep
# the caller's stdout pipe open and hang anyone reading it to the end.
#
# Runs after 40-vault-preflight.sh on purpose: the loop starts only once
# every boot check has passed.

set -eu

ME="45-vault-eventlog-rotate.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

DEFAULT_BYTES=67108864
MIN_BYTES=1048576
DEFAULT_INTERVAL=60
PID_FILE=/var/run/nginx.pid
WORKER_USER=nginx

# digits <value> <max digits>: true iff 1..<max> ASCII digits, nothing else.
digits() {
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    [ "${#1}" -le "$2" ]
}

# strip0 <digits>: the same number without leading zeros ("064" -> "64",
# "000" -> "0"), so neither test(1) nor $(( )) can read it as octal.
strip0() {
    _s=$(printf '%s' "$1" | sed 's/^0*//')
    echo "${_s:-0}"
}

LOG_PATH="${VAULT_EVENT_LOG:-}"
BYTES_RAW="${VAULT_EVENT_LOG_ROTATE_BYTES:-}"
INTERVAL_RAW="${VAULT_EVENT_LOG_ROTATE_INTERVAL:-}"

# --- Grammar (checked even when the event log is off: a typo surfaces
# before someone switches the log on) ----------------------------------------
if [ -z "$BYTES_RAW" ]; then
    BYTES=$DEFAULT_BYTES
elif digits "$BYTES_RAW" 15; then
    BYTES=$(strip0 "$BYTES_RAW")
    if [ "$BYTES" -ne 0 ] && [ "$BYTES" -lt "$MIN_BYTES" ]; then
        die "VAULT_EVENT_LOG_ROTATE_BYTES='$BYTES_RAW' is below the minimum $MIN_BYTES (1 MiB).
  Use 0 to switch rotation off, leave it empty for the default $DEFAULT_BYTES (64 MiB)."
    fi
else
    die "VAULT_EVENT_LOG_ROTATE_BYTES='$BYTES_RAW' is not a whole number of bytes
  (digits only, at most 15). Empty = $DEFAULT_BYTES (64 MiB), 0 = rotation off."
fi

if [ -z "$INTERVAL_RAW" ]; then
    INTERVAL=$DEFAULT_INTERVAL
elif digits "$INTERVAL_RAW" 4 && [ "$(strip0 "$INTERVAL_RAW")" -ge 1 ] \
        && [ "$(strip0 "$INTERVAL_RAW")" -le 3600 ]; then
    INTERVAL=$(strip0 "$INTERVAL_RAW")
else
    die "VAULT_EVENT_LOG_ROTATE_INTERVAL='$INTERVAL_RAW' must be 1..3600 seconds
  (empty = $DEFAULT_INTERVAL)."
fi

# `--grammar` (the shared fixture table, core/tests/test-settings-grammar.sh):
# the normalised rotation size, nothing else.
if [ "${1:-}" = --grammar ]; then
    echo "$BYTES"
    exit 0
fi

# --- D16c: the size may change at runtime ---------------------------------------
# 15-vault-settings.envsh writes the effective core settings, and the settings
# watcher (47-) replaces the file on every applied generation. When the size
# does NOT come from an env pin, the loop re-reads it before every check, so
# a new value from Settings applies within one interval. An unreadable or
# invalid line keeps the previous size (the watcher only installs values
# this hook's grammar accepted).
EFFECTIVE_FILE="${VS_EFFECTIVE_FILE:-/etc/nginx/vault-settings.effective}"
TAB=$(printf '\t')
SETTABLE=no
if [ -f "$EFFECTIVE_FILE" ]; then
    _src=$(grep -m 1 '^VAULT_EVENT_LOG_ROTATE_BYTES=' "$EFFECTIVE_FILE" 2>/dev/null || true)
    case "$_src" in
        "") : ;;
        *"${TAB}env") : ;;
        *) SETTABLE=yes ;;
    esac
fi

# runtime_bytes: the size in the effective settings file, or return 1.
runtime_bytes() {
    _l=$(grep -m 1 '^VAULT_EVENT_LOG_ROTATE_BYTES=' "$EFFECTIVE_FILE" 2>/dev/null) || return 1
    _v=${_l#*=}
    _v=${_v%%"$TAB"*}
    digits "$_v" 15 || return 1
    _v=$(strip0 "$_v")
    if [ "$_v" -ne 0 ] && [ "$_v" -lt "$MIN_BYTES" ]; then
        return 1
    fi
    echo "$_v"
}

if [ -z "$LOG_PATH" ]; then
    log "VAULT_EVENT_LOG unset/empty -- no cache-event log, no rotation"
    exit 0
fi
if [ "$BYTES" -eq 0 ] && [ "$SETTABLE" = no ]; then
    log "VAULT_EVENT_LOG_ROTATE_BYTES=0 -- rotation of $LOG_PATH is OFF (the file grows until something else rotates it)"
    exit 0
fi

# 25-vault-eventlog.sh already refused anything else and runs first; this is
# the same rule restated, because this hook renames and creates names there.
case "$LOG_PATH" in
    /vault/*) : ;;
    *) die "VAULT_EVENT_LOG='$LOG_PATH' is not under /vault/ (25-vault-eventlog.sh should have refused it)." ;;
esac
case "$LOG_PATH" in
    *[!A-Za-z0-9/_.-]*|*..*|*/./*|*/.|*/)
        die "VAULT_EVENT_LOG='$LOG_PATH' is not a plain path (25-vault-eventlog.sh should have refused it)." ;;
esac
OLD_PATH="$LOG_PATH.1"

# --- Is this the server start? ------------------------------------------------
server_start=no
if [ -r "/proc/$PPID/cmdline" ]; then
    seen_nginx=no
    info_flag=no
    # One word per argument (spaces inside an argument, as in
    # "daemon off;", become \001 first); no globbing of the words. An
    # argument holding a tab or newline only splits into more words, which
    # can add a flag match ("not started") but never remove one.
    set -f
    for arg in $(tr '\0 ' '\n\001' < "/proc/$PPID/cmdline"); do
        if [ "$seen_nginx" = no ]; then
            case "$arg" in nginx|nginx-debug|*/nginx|*/nginx-debug) seen_nginx=yes ;; esac
            continue
        fi
        case "$arg" in
            -*[tTsvVh?]*) info_flag=yes ;;
        esac
    done
    set +f
    if [ "$seen_nginx" = yes ] && [ "$info_flag" = no ]; then
        server_start=yes
    fi
fi
if [ "$server_start" != yes ]; then
    log "not a server start (an nginx -t/-T/-s/-v run or no nginx command) -- rotation loop not started"
    exit 0
fi

# --- The loop -----------------------------------------------------------------
# master_pid: the running nginx master's pid on stdout, or nothing.
master_pid() {
    [ -f "$PID_FILE" ] || return 1
    _p=$(head -c 16 "$PID_FILE" 2>/dev/null | tr -d '\n')
    digits "$_p" 10 || return 1
    # A pid file can outlive its master across a container restart (the
    # writable layer survives); only a live master process counts.
    _c=$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null) || return 1
    case "$_c" in "nginx: master process"*) echo "$_p" ;; *) return 1 ;; esac
}

rotate_once() {
    if [ "$SETTABLE" = yes ]; then
        _nb=$(runtime_bytes) || _nb=$BYTES
        if [ "$_nb" != "$BYTES" ]; then
            log "rotation size changed by the core settings: $BYTES -> $_nb bytes (0 = off)"
            BYTES=$_nb
        fi
    fi
    [ "$BYTES" -ne 0 ] || return 0
    [ -f "$LOG_PATH" ] && [ ! -L "$LOG_PATH" ] || return 0
    size=$(stat -c %s "$LOG_PATH" 2>/dev/null) || return 0
    digits "$size" 19 || return 0
    [ "$size" -ge "$BYTES" ] || return 0

    # The master first: renaming without a reopen would leave nginx writing
    # into event.log.1 forever while event.log stays empty.
    pid=$(master_pid) || { log "WARNING: $LOG_PATH is $size bytes but no running nginx master was found ($PID_FILE) -- not rotating this time"; return 0; }

    if [ -d "$OLD_PATH" ] && [ ! -L "$OLD_PATH" ]; then
        log "WARNING: $OLD_PATH is a directory -- not rotating $LOG_PATH ($size bytes); remove it"
        return 0
    fi
    # Removed first, so the mv below always renames onto a free name (busybox
    # mv would move INTO a directory, or one a symlink points at). The
    # directory is root-only (25-vault-eventlog.sh), so nobody else can put
    # a name back in between.
    rm -f "$OLD_PATH" || { log "WARNING: could not remove $OLD_PATH -- not rotating"; return 0; }
    mv "$LOG_PATH" "$OLD_PATH" || { log "WARNING: mv $LOG_PATH $OLD_PATH failed -- not rotating"; return 0; }
    # Append-create, never truncate: if a reload opened the name in between,
    # the lines it already wrote stay.
    if ! { : >> "$LOG_PATH" && chown -h "$WORKER_USER:$WORKER_USER" "$LOG_PATH"; }; then
        log "WARNING: could not create $LOG_PATH as $WORKER_USER:$WORKER_USER -- nginx creates it root-owned on reopen; vault-api may then only read it"
    fi
    if kill -USR1 "$pid"; then
        log "rotated $LOG_PATH ($size bytes) -> $OLD_PATH, nginx master $pid reopened its logs"
    else
        log "WARNING: rotated $LOG_PATH ($size bytes) -> $OLD_PATH, but signalling nginx master $pid failed -- lines keep going to $OLD_PATH until the next reopen/restart"
    fi
}

rotate_loop() {
    # The hook runs under set -e; one failing command must not end the loop.
    set +e
    while :; do
        sleep "$INTERVAL"
        rotate_once
    done
}

rotate_loop < /dev/null &
log "rotating $LOG_PATH at $BYTES bytes into $OLD_PATH (one old file kept), checked every ${INTERVAL}s (pid $!)"
if [ "$SETTABLE" = yes ]; then
    log "the rotation size follows the core settings (re-read before every check; 0 = off for now)"
fi
exit 0
