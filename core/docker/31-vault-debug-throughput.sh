#!/bin/sh
# SteamHangar vault-core container hook -- opt-in throughput debug log
# (WP THROUGHPUT-DEBUG-1).
#
# Why: a client downloading a fully cached game (all HIT) through vault-core
# reached ~300 Mbit/s on a 1 Gbit/s LAN. To tell client, disk, nginx settings,
# TCP and "too few connections" apart, vault-core can write one compact line
# per depot request to /vault/logs/throughput-debug/. core/tools/throughput-report.py
# turns that file into per-client numbers and a verdict list.
#
# Input (env only, read at start; changing it needs a container restart):
#   VAULT_DEBUG_THROUGHPUT           unset, empty, 0, false, off, no -> OFF (default):
#                                    this hook only deletes the marker lines
#                                    from the rendered nginx.conf; the config,
#                                    the logs and the disk are exactly what they
#                                    were before. Old debug files are neither
#                                    read, written nor deleted. 1, true, on,
#                                    yes (lowercase) -> ON. Anything else stops
#                                    the boot (fail closed, like 26-).
#   VAULT_DEBUG_THROUGHPUT_MAX_MB    ON only. Hard-ish ceiling for ALL debug
#                                    files together, 1..1024, default 64.
#   VAULT_DEBUG_THROUGHPUT_MAX_DAYS  ON only. Rotated files older than this are
#                                    deleted, 1..30, default 3.
#
# Same file, second job (WP TUNE-1a, default changed by WP AIO-DEFAULT-1b,
# ADR-0030): two nginx throughput switches that are independent of the debug
# flag (they also work with it off). TUNE-1a shipped both as opt-in to test
# whether cold-read throughput (HDD, ~300 Mbit/s) is limited by the single
# worker blocking on disk reads. The cold-game test of 2026-10-18 proved it
# for AIO (ADR-0027 "Measurement 2026-10-18"), so since rc21 `aio threads` is
# the DEFAULT; VAULT_NGINX_WORKERS stays opt-in.
#   VAULT_NGINX_AIO       unset, empty, threads -> adds `aio threads;` to the
#                         http block AND switches `sendfile on;` to
#                         `sendfile off;` (the default since rc21). Both are
#                         needed: with sendfile on, nginx uses aio only for
#                         files >= directio (default off), so aio alone would
#                         change nothing. With sendfile off, file reads go
#                         through the thread pool and the page cache (no
#                         directio), at the price of one extra copy.
#                         `off` -> the opt-out: nothing is added and sendfile
#                         stays on, so the render is byte-identical to the
#                         pre-rc21 default render. Anything else stops the
#                         boot (fail closed); so does a missing/duplicated
#                         sendfile line or an existing aio line.
#   VAULT_NGINX_WORKERS   unset, empty -> `worker_processes 1;` as in the
#                         template. `auto` or a whole number 1..16 -> that
#                         value. Anything else stops the boot. Rate cap and
#                         limit_conn stay aggregate with more workers (their
#                         counters and zones are shared memory), but the
#                         upstream keepalive pool is PER WORKER, see
#                         core/README.md "Throughput tuning switches".
# The debug startup line shows the effective aio / worker_processes values.
#
# Everything lives in ONE directory, /vault/logs/throughput-debug/ (current
# file throughput.log, rotated files throughput.<epoch>.log). Removing that
# directory removes every trace; there is no database row and no schema.
#
# What it does when ON:
#   1. keeps the log_formats and the three `access_log ... vault_tp[_hb] ...`
#      lines in the rendered nginx.conf (location /depot/, location @miss and,
#      with the normal `vault` log restated beside it because a location's own
#      access_log replaces the inherited one, location = /lancache-heartbeat,
#      format vault_tp_hb, tag hb1). EVERY
#      line this feature adds to the template (comments, the log_format, the
#      access_log lines) ends in the marker comment `# VAULT_DEBUG_TP_LINE`,
#      so the OFF render is byte-identical to the template without the
#      feature; the ON render strips the marker. Same mechanism as
#      25-vault-eventlog.sh and check-config-drift.sh delta 8);
#   2. creates the directory (a plain mkdir; `nginx -t` opens the log too);
#   3. on a real server start only: appends ONE `# start ...` line to the log
#      with the rendered settings that matter for throughput (worker_processes,
#      worker_connections, sendfile, sendfile_max_chunk, aio, directio,
#      output_buffers, tcp_nopush, tcp_nodelay, limit_rate, proxy_limit_rate,
#      limit_conn, slice, keepalive_timeout; "unset" = nginx default);
#   4. on a real server start only: forks a housekeeping loop (every 30 s):
#      rotate the current file by rename + SIGUSR1 when it reaches MAX_MB/4
#      (at least 1 MiB) or is a day old, delete rotated files older than
#      MAX_DAYS, then delete the oldest rotated files until the directory is
#      below MAX_MB. That runs for as long as the flag stays on, so the data
#      cannot outgrow either limit. Same rename technique as
#      45-vault-eventlog-rotate.sh, which only knows the event log.
#
# Usage: the stock entrypoint runs it with no argument (after 29-, before
# 40-vault-preflight.sh). With ONE argument (a path to a rendered nginx.conf)
# it only does the config step -- that is how vault-settings-lib.sh's vs_render
# calls it for the settings watcher's staging directory. `--prune <dir>` runs
# one deletion pass (age, then size) on <dir> and exits; the tests use it with
# VAULT_DEBUG_NOW (epoch seconds) to fake the clock.
#
# Cost when ON: about 190 bytes per request. At ~40 requests/s (a 1 Gbit/s
# Steam download in 1 MiB slices) that is ~7.5 KB/s, 27 MB/hour; the buffer
# (64k, flushed at least every 5 s) keeps it off the request path.
#
# Limits are approximate: the CURRENT file is never deleted by the pruning, only
# rotated (or, when nginx cannot be signalled, truncated), and it is checked
# every 30 s, so the directory can exceed MAX_MB by up to one segment
# (MAX_MB/4, at least 1 MiB) plus 30 s of writes. With MAX_MB=1 the 1 MiB
# minimum segment alone is the whole budget.
#
# Cleanup by hand (debug files only):  rm -rf /vault/logs/throughput-debug
# Rollback: VAULT_DEBUG_THROUGHPUT off, restart vault-core (re-renders the
# config without the log lines), then remove the directory as above.

set -eu

ME="31-vault-debug-throughput.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

CONF="${1:-/etc/nginx/nginx.conf}"
RENDER_ONLY=no
[ "$#" -eq 0 ] || RENDER_ONLY=yes
MARKER="# VAULT_DEBUG_TP_LINE"
LOG_DIR=/vault/logs/throughput-debug
LOG_PATH="$LOG_DIR/throughput.log"
LOOP_INTERVAL=30
PID_FILE=/var/run/nginx.pid
DEFAULT_MAX_MB=64
DEFAULT_MAX_DAYS=3

# bounded <value> <default> <min> <max> <name>: echoes the number or dies.
bounded() {
    _b=${1:-$2}
    case "$_b" in ''|*[!0-9]*|0[0-9]*) die "$5='$(printf '%s' "$_b" | tr -c 'A-Za-z0-9._-' '?')' is not a whole number $3..$4." ;; esac
    [ "${#_b}" -le 4 ] && [ "$_b" -ge "$3" ] && [ "$_b" -le "$4" ] || die "$5=$_b is outside $3..$4."
    echo "$_b"
}

# prune_dir <dir>: one deletion pass. Only names this hook creates
# (throughput.<digits>.log, regular files) are ever removed.
prune_dir() {
    _now=${VAULT_DEBUG_NOW:-$(date +%s)}
    _maxage=$((MAX_DAYS * 86400))
    _maxbytes=$((MAX_MB * 1048576))
    for _p in "$1"/throughput.[0-9]*.log; do
        _f=${_p##*/}
        _e=${_f#throughput.}; _e=${_e%.log}
        case "$_e" in ''|*[!0-9]*) continue ;; esac
        [ -f "$1/$_f" ] && [ ! -L "$1/$_f" ] || continue
        if [ $((_now - _e)) -gt "$_maxage" ]; then
            rm -f "$1/$_f" && echo "$ME: deleted $_f (older than $MAX_DAYS day(s))"
        fi
    done
    while :; do
        _total=0
        for _p in "$1"/throughput.log "$1"/throughput.[0-9]*.log; do
            _f=${_p##*/}
            [ -f "$1/$_f" ] && [ ! -L "$1/$_f" ] || continue
            _total=$((_total + $(stat -c %s "$1/$_f" 2>/dev/null || echo 0)))
        done
        [ "$_total" -gt "$_maxbytes" ] || break
        _oldest=""
        for _p in "$1"/throughput.[0-9]*.log; do
            _f=${_p##*/}
            [ -f "$1/$_f" ] && [ ! -L "$1/$_f" ] || continue
            _oldest=$_f; break
        done
        [ -n "$_oldest" ] || break
        rm -f "$1/$_oldest" && echo "$ME: deleted $_oldest (directory $_total bytes > $MAX_MB MiB)"
    done
}

if [ "${1:-}" = "--prune" ]; then
    MAX_MB=$(bounded "${VAULT_DEBUG_THROUGHPUT_MAX_MB:-}" $DEFAULT_MAX_MB 1 1024 VAULT_DEBUG_THROUGHPUT_MAX_MB)
    MAX_DAYS=$(bounded "${VAULT_DEBUG_THROUGHPUT_MAX_DAYS:-}" $DEFAULT_MAX_DAYS 1 30 VAULT_DEBUG_THROUGHPUT_MAX_DAYS)
    [ -d "${2:-}" ] || die "--prune needs a directory"
    prune_dir "$2"
    exit 0
fi

[ -f "$CONF" ] || die "$CONF does not exist -- expected to run after envsubst rendering."

# --- nginx tuning (WP TUNE-1a; AIO default since rc21, ADR-0030) -------------
# Independent of the debug flag. Unset/empty VAULT_NGINX_AIO means the default
# `threads`; `off` is the explicit opt-out (pre-rc21 render, sendfile on).
aio_raw="${VAULT_NGINX_AIO:-}"
case "$aio_raw" in
    "") AIO=threads; AIO_SRC="unset (default)" ;;
    threads) AIO=threads; AIO_SRC="threads" ;;
    off) AIO=off; AIO_SRC="off" ;;
    *) die "VAULT_NGINX_AIO='$(printf '%s' "$aio_raw" | tr -c 'A-Za-z0-9._-' '?')' is not one of
  threads or empty (the default: adds 'aio threads;' and switches sendfile off) or
  off (opt-out: nothing added, sendfile stays on). Lowercase only. Refusing to start." ;;
esac
workers_raw="${VAULT_NGINX_WORKERS:-}"
case "$workers_raw" in
    "") WORKERS="" ;;
    auto) WORKERS=auto ;;
    *) WORKERS=$(bounded "$workers_raw" 1 1 16 VAULT_NGINX_WORKERS) ;;
esac
if [ "$AIO" = threads ]; then
    [ "$(grep -cE '^[[:space:]]*aio[[:space:]]' "$CONF" || true)" = 0 ] || die "$CONF already has an aio directive; refusing to add a second one."
    [ "$(grep -c '^http {$' "$CONF" || true)" = 1 ] || die "expected exactly one 'http {' line in $CONF to add 'aio threads;' after."
    [ "$(grep -c '^    sendfile      on;$' "$CONF" || true)" = 1 ] || die "expected exactly one '    sendfile      on;' line in $CONF to switch off for aio threads (VAULT_NGINX_AIO=$AIO_SRC; VAULT_NGINX_AIO=off opts out)."
    awk '{ if ($0 == "    sendfile      on;") print "    sendfile      off;"; else print } /^http \{$/ { print "    aio threads;" }' "$CONF" > "$CONF.tune.$$" \
        && cat "$CONF.tune.$$" > "$CONF" && rm -f "$CONF.tune.$$" || die "could not add aio threads to $CONF."
    [ "$(grep -c '^    sendfile      off;$' "$CONF" || true)" = 1 ] || die "could not switch sendfile off in $CONF."
    log "VAULT_NGINX_AIO=$AIO_SRC: 'aio threads;' added to http and 'sendfile on' switched to 'sendfile off' (reads go through the thread pool and page cache; one extra copy). Opt out with VAULT_NGINX_AIO=off."
else
    log "VAULT_NGINX_AIO=off: no aio, 'sendfile on' kept (the pre-rc21 render)."
fi
if [ -n "$WORKERS" ]; then
    [ "$(grep -c '^worker_processes  1;$' "$CONF" || true)" = 1 ] || die "expected exactly one 'worker_processes  1;' line in $CONF."
    sed -i "s/^worker_processes  1;\$/worker_processes  $WORKERS;/" "$CONF"
    [ "$(grep -c "^worker_processes  $WORKERS;\$" "$CONF" || true)" = 1 ] || die "could not set worker_processes $WORKERS in $CONF."
    if [ "$WORKERS" != 1 ]; then
        log "VAULT_NGINX_WORKERS=$WORKERS: worker_processes $WORKERS. Upstream rate cap and limit_conn stay aggregate (shared memory); the upstream keepalive pool is PER WORKER (idle upstream connections up to 8 x workers per edge)."
    fi
fi

raw="${VAULT_DEBUG_THROUGHPUT:-}"
case "$raw" in
    ""|0|false|off|no) MODE=off ;;
    1|true|on|yes)     MODE=on ;;
    *) die "VAULT_DEBUG_THROUGHPUT='$(printf '%s' "$raw" | tr -c 'A-Za-z0-9._-' '?')' is not one of
  1/true/on/yes (switch the throughput debug log on) or 0/false/off/no/empty (off, the default).
  Lowercase only. Refusing to start." ;;
esac

if [ "$MODE" = off ]; then
    sed -i "/${MARKER}\$/d" "$CONF"
    left=$(grep -c "$MARKER" "$CONF" 2>/dev/null || true)
    [ "${left:-0}" = 0 ] || die "$left marker line(s) remain in $CONF while the debug log is off."
    survivors=$(grep -cE 'vault_tp|throughput-debug' "$CONF" 2>/dev/null || true)
    [ "${survivors:-0}" = 0 ] || die "VAULT_DEBUG_THROUGHPUT is off but $survivors line(s) naming vault_tp or throughput-debug remain in $CONF.
  Refusing to start half-disabled."
    log "VAULT_DEBUG_THROUGHPUT off -- no throughput debug log rendered"
    exit 0
fi

sed -i "s/[[:space:]]*${MARKER}\$//" "$CONF"
left=$(grep -c "$MARKER" "$CONF" 2>/dev/null || true)
[ "${left:-0}" = 0 ] || die "failed to strip the marker comment from $CONF ($left remain)."
n=$(grep -cE '^[[:space:]]*access_log[[:space:]].*vault_tp([[:space:]]|;|$)' "$CONF" 2>/dev/null || true)
[ "${n:-0}" = 2 ] || die "expected 2 access_log directives naming vault_tp in $CONF (location /depot/ and @miss), found ${n:-0}."
nh=$(grep -cE '^[[:space:]]*access_log[[:space:]].*vault_tp_hb([[:space:]]|;|$)' "$CONF" 2>/dev/null || true)
[ "${nh:-0}" = 1 ] || die "expected 1 access_log directive naming vault_tp_hb in $CONF (location = /lancache-heartbeat), found ${nh:-0}."

MAX_MB=$(bounded "${VAULT_DEBUG_THROUGHPUT_MAX_MB:-}" $DEFAULT_MAX_MB 1 1024 VAULT_DEBUG_THROUGHPUT_MAX_MB)
MAX_DAYS=$(bounded "${VAULT_DEBUG_THROUGHPUT_MAX_DAYS:-}" $DEFAULT_MAX_DAYS 1 30 VAULT_DEBUG_THROUGHPUT_MAX_DAYS)

if [ "$RENDER_ONLY" = yes ]; then
    log "throughput debug log ENABLED in $CONF ($LOG_PATH, at most $MAX_MB MiB / $MAX_DAYS day(s)); render only"
    exit 0
fi

# nginx -t opens the log file too, so the directory must exist for every
# non-render run, not only a real start. Plain mkdir, never -p (the parent,
# /vault/logs, is made by 21-vault-volume-ownership.sh).
if [ -L "$LOG_DIR" ]; then
    die "$LOG_DIR is a symlink; refusing to write through it."
fi
if [ ! -d "$LOG_DIR" ]; then
    mkdir "$LOG_DIR" || die "could not create $LOG_DIR (is /vault/logs there and writable?)."
    chmod 0755 "$LOG_DIR" 2>/dev/null || true
fi

# --- a real server start? (same detection as 45-vault-eventlog-rotate.sh) ---
# An `nginx -t`/-T/-s/-v run must not append to the log nor fork a loop that
# would hold the caller's stdout pipe open.
server_start=no
if [ -r "/proc/$PPID/cmdline" ]; then
    seen_nginx=no
    info_flag=no
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
    log "throughput debug log ENABLED (not a server start -- no startup line, no housekeeping loop)"
    exit 0
fi

if [ -L "$LOG_PATH" ] || { [ -e "$LOG_PATH" ] && [ ! -f "$LOG_PATH" ]; }; then
    die "$LOG_PATH is a symlink or not a regular file; refusing to write through it."
fi

# --- the startup line: rendered settings that matter for throughput ----------
CONF_DIR=$(dirname -- "$CONF")
# directive <name>: every value of the directive in nginx.conf and the rendered
# includes, `,`-joined; "unset" when the config never sets it.
directive() {
    _v=$(cat "$CONF" "$CONF_DIR"/vault-upstream-*.conf 2>/dev/null \
        | sed -n "s/^[[:space:]]*$1[[:space:]][[:space:]]*\\([^#;]*\\);.*/\\1/p" \
        | tr -s ' \t' ' ' | sed 's/ *$//' | tr '\n' ',' | sed 's/,$//')
    printf '%s' "${_v:-unset}"
}
rate_note=$(grep -m 1 -E '^# (Cap|No cap)' "$CONF_DIR/vault-upstream-rate.conf" 2>/dev/null | cut -c 3-80 || true)
{
    printf '# start %s %s: ' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ME"
    for d in worker_processes worker_connections sendfile sendfile_max_chunk aio directio \
             output_buffers tcp_nopush tcp_nodelay limit_rate proxy_limit_rate limit_conn \
             slice keepalive_timeout; do
        printf '%s=%s ' "$d" "$(directive "$d" | tr ' ' '_')"
    done
    printf 'upstream_rate_cap="%s" ' "${rate_note:-unknown}"
    case "$(directive worker_processes)" in
        1|unset) ;;
        *) printf 'workers_note="rate cap and limit_conn are aggregate (shared memory); upstream keepalive pool is per worker" ' ;;
    esac
    printf 'hb_fields=hb1,msec,client,status,request_time,connection,connection_requests,tcp_rtt_us '
    printf 'fields=tp1,msec,client,depot,method,status,range,bytes_sent,request_time,upstream_response_time,cache,connection,connection_requests,request_length,tcp_rtt_us,tcp_rttvar_us,tcp_snd_cwnd,tcp_rcv_space\n'
} >> "$LOG_PATH"
chmod 0644 "$LOG_PATH" 2>/dev/null || true

# --- housekeeping loop ---------------------------------------------------------
master_pid() {
    [ -f "$PID_FILE" ] || return 1
    _p=$(head -c 16 "$PID_FILE" 2>/dev/null | tr -d '\n')
    case "$_p" in ''|*[!0-9]*) return 1 ;; esac
    _c=$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null) || return 1
    case "$_c" in "nginx: master process"*) echo "$_p" ;; *) return 1 ;; esac
}

SEG_BYTES=$((MAX_MB * 1048576 / 4))
[ "$SEG_BYTES" -ge 1048576 ] || SEG_BYTES=1048576
LAST_ROT=$(date +%s)
NO_MASTER=0

rotate_once() {
    [ -f "$LOG_PATH" ] && [ ! -L "$LOG_PATH" ] || return 0
    size=$(stat -c %s "$LOG_PATH" 2>/dev/null) || return 0
    now=$(date +%s)
    if [ "$size" -lt "$SEG_BYTES" ] && { [ $((now - LAST_ROT)) -lt 86400 ] || [ "$size" -eq 0 ]; }; then
        return 0
    fi
    if ! pid=$(master_pid); then
        # Cannot signal nginx, so no rename (it would keep writing into the
        # renamed file). The size limit must still hold: cut the current file
        # in place (nginx writes with O_APPEND, so this is safe) once it is
        # over the segment size, and say so every ~5 minutes.
        NO_MASTER=$((NO_MASTER + 1))
        if [ "$size" -ge "$SEG_BYTES" ]; then
            : > "$LOG_PATH" && log "WARNING: nginx master not found; truncated $LOG_PATH ($size bytes) to keep the $MAX_MB MiB limit"
        elif [ $((NO_MASTER % 10)) -eq 1 ]; then
            log "WARNING: nginx master not found (pid file $PID_FILE); cannot rotate $LOG_PATH"
        fi
        return 0
    fi
    NO_MASTER=0
    new="$LOG_DIR/throughput.$now.log"
    [ ! -e "$new" ] || return 0
    mv "$LOG_PATH" "$new" || return 0
    { : >> "$LOG_PATH" && chmod 0644 "$LOG_PATH"; } || true
    LAST_ROT=$now
    if kill -USR1 "$pid"; then
        log "rotated $LOG_PATH ($size bytes) -> $new, nginx master $pid reopened its logs"
    else
        log "WARNING: rotated $LOG_PATH but signalling nginx master $pid failed"
    fi
}

housekeeping_loop() {
    set +e
    while :; do
        sleep "$LOOP_INTERVAL"
        rotate_once
        prune_dir "$LOG_DIR"
    done
}

housekeeping_loop < /dev/null &
log "throughput debug log ON: $LOG_PATH, files rotated at $SEG_BYTES bytes or daily, kept at most $MAX_DAYS day(s) and $MAX_MB MiB in total, checked every ${LOOP_INTERVAL}s (pid $!)"
exit 0
