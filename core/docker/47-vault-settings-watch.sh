#!/bin/sh
# SteamHangar vault-core container hook -- applies core settings from
# vault-api at runtime, without a restart (D16c, docs/handover/
# d16-settings-audit.md 4.3; core/README.md "Settings from vault-api").
#
# 1. Writes the boot result of 15-vault-settings.envsh to
#    /vault/logs/settings-applied.json (the status file vault-api reads for
#    GET /v1/settings). Written here, not in 15-, because 21- makes /vault/logs
#    root-only only after 15- ran (SEC-FIX-5).
# 2. Forks a watcher loop -- the same pattern as 45-vault-eventlog-rotate.sh:
#    forked before the stock entrypoint execs nginx, re-parented to the nginx
#    master, never started for an `nginx -t`/`-T`/`-s`/`-v` run. Every
#    VS_POLL seconds it reads the settings file; when its content changed:
#      a. parse it (vs_parse). Not exactly what vault-api writes: "rejected".
#      b. resolve env > file > default (env pins recorded at boot by 15-),
#         render 20 -> 25 -> 26 -> 27 -> 28 into a staging directory and run
#         40's value checks there (vs_render).
#      c. GATE: `nginx -t -p /vault -c <staging>/nginx.conf`. `nginx -s reload`
#         is never the gate -- it returns 0 even when the master then refuses
#         the new config. `-p /vault` like the image CMD, so the root master
#         resolves the relative temp/log paths inside /vault (SEC-FIX-5).
#      d. back up the live files, move the staged ones into /etc/nginx, send
#         SIGHUP to the master (a reload: same master pid, 45- unaffected).
#      e. poll http://127.0.0.1/health until X-Vault-Settings-Apply shows this
#         attempt's unique id (X-Vault-Settings-Generation carries the
#         generation for vault-api; a generation can repeat after a lost
#         settings volume, the id cannot). Timeout: restore the backup,
#         SIGHUP again, "rejected: reload failed".
#      f. "applied" with the generation, the effective value and source of
#         every key.
#    On any failure the last good config stays live; it never fails open.
#
# Unchanged content: nothing to do. A changed content is applied whatever its
# GENERATION (not only a higher one: vault-api restarts at 1 when the
# settings volume is lost). A missing file (settings volume not mounted, or
# vault-api not started yet) changes nothing.

set -eu

ME="47-vault-settings-watch.sh"
VS_LIB="${VS_LIB:-/opt/vault/vault-settings-lib.sh}"
# shellcheck source=core/docker/vault-settings-lib.sh
. "$VS_LIB"

log()  { echo "$ME: $*"; }
warn() { echo "$ME: WARNING: $*" >&2; }

VS_POLL="${VS_POLL:-5}"
PID_FILE="${VS_PID_FILE:-/var/run/nginx.pid}"
# Seconds to wait for the reloaded workers to serve the new apply id.
RELOAD_WAIT="${VS_RELOAD_WAIT:-30}"
HEALTH_URL="${VS_HEALTH_URL:-http://127.0.0.1/health}"
BACKUP_DIR="$VS_CONF_DIR/.vault-settings-last-good"

master_pid() {
    [ -f "$PID_FILE" ] || return 1
    _p=$(head -c 16 "$PID_FILE" 2>/dev/null | tr -d '\n')
    case "$_p" in ''|*[!0-9]*) return 1 ;; esac
    _c=$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null) || return 1
    case "$_c" in "nginx: master process"*) echo "$_p" ;; *) return 1 ;; esac
}

# live_apply_id: the X-Vault-Settings-Apply id the workers serve now.
live_apply_id() {
    wget -q -S -O /dev/null "$HEALTH_URL" 2>&1 \
        | sed -n 's/^[[:space:]]*[Xx]-[Vv]ault-[Ss]ettings-[Aa]pply:[[:space:]]*\([A-Za-z0-9.-]*\).*$/\1/p' \
        | head -n 1
}

# wait_apply_id <id>: the new workers serve the config with this apply id.
wait_apply_id() {
    _i=0
    while [ "$_i" -lt "$RELOAD_WAIT" ]; do
        [ "$(live_apply_id)" = "$1" ] && return 0
        sleep 1
        _i=$((_i + 1))
    done
    return 1
}

# The apply id of the live config ("boot" until the first apply), and the
# live generation (set from the boot result below).
LIVE_ID=boot
APPLY_N=0
APPLIED_GEN=${VS_BOOT_GEN:-0}

# VS_QUIET=1 while the loop retries the same content after a transient failure
# (apply_file returned 2). reject stays silent only for a reason IDENTICAL to
# the last one it reported; a different failure in the streak is reported.
VS_QUIET=0
VS_LAST_REASON=""

reject() {
    # reject <generation> <reason>
    if [ "$VS_QUIET" = 1 ] && [ "$2" = "$VS_LAST_REASON" ]; then return 0; fi
    VS_LAST_REASON=$2
    warn "settings generation $1 rejected: $2 -- generation $APPLIED_GEN stays live"
    vs_write_status rejected "$1" "$APPLIED_GEN" "$2" "$VS_CONF_DIR/vault-settings.effective" \
        || warn "could not write $VS_STATUS_FILE"
}

restore_backup() {
    for _f in $VS_RENDERED; do
        if [ -f "$BACKUP_DIR/$_f" ]; then
            cp "$BACKUP_DIR/$_f" "$VS_CONF_DIR/.$_f.restore" && mv -f "$VS_CONF_DIR/.$_f.restore" "$VS_CONF_DIR/$_f"
        fi
    done
}

# apply_file <content>: one apply attempt (the steps a-f above). Returns 0
# when the content was applied or refused for a reason the content itself
# causes (parse, render, nginx -t, a reload that did not take); 2 for a
# transient local failure (no staging dir, no master, backup or swap
# failed), so the watcher tries the same content again (review D16c S5).
apply_file() {
    if ! vs_parse "$1"; then
        reject "${VS_GEN:-0}" "settings file rejected: $VS_ERR"
        return 0
    fi
    # Any change of the file content is applied, also with the same or a
    # LOWER generation: vault-api restarts at 1 when the settings volume is
    # lost (the watcher only gets here when the content changed).
    _gen=$VS_GEN
    APPLY_N=$((APPLY_N + 1))
    _id="a$$-$APPLY_N-$(date +%s)"
    [ "$VS_QUIET" = 1 ] || log "settings generation $_gen found (live: $APPLIED_GEN) -- rendering"
    _stage=$(mktemp -d) || { reject "$_gen" "could not create a staging directory (will retry)"; return 2; }
    vs_resolve yes
    if ! (vs_export; vs_render "$_stage" "$_gen" "$_id"); then
        _why=$(grep -m 1 'FATAL' "$_stage/.render.log" 2>/dev/null | cut -c 1-300 || true)
        sed 's/^/    /' "$_stage/.render.log" >&2 || true
        rm -rf "$_stage"
        reject "$_gen" "render failed${_why:+: $_why}"
        return 0
    fi
    if ! _t=$(nginx -t -p /vault -c "$_stage/nginx.conf" 2>&1); then
        printf '%s\n' "$_t" | sed 's/^/    /' >&2
        rm -rf "$_stage"
        reject "$_gen" "nginx -t failed: $(printf '%s\n' "$_t" | grep -m 1 'emerg' | cut -c 1-300)"
        return 0
    fi
    _pid=$(master_pid) || { rm -rf "$_stage"; reject "$_gen" "no running nginx master found (will retry)"; return 2; }

    # Swap: back up the live files, move the staged ones in (rename per file).
    rm -rf "$BACKUP_DIR"
    if ! mkdir -m 0755 "$BACKUP_DIR"; then
        rm -rf "$_stage"
        reject "$_gen" "could not create $BACKUP_DIR (will retry)"
        return 2
    fi
    for _f in $VS_RENDERED; do
        if [ -f "$VS_CONF_DIR/$_f" ] && ! cp -p "$VS_CONF_DIR/$_f" "$BACKUP_DIR/$_f"; then
            rm -rf "$_stage"
            reject "$_gen" "could not back up $VS_CONF_DIR/$_f (will retry)"
            return 2
        fi
    done
    _swap_ok=yes
    for _f in $VS_RENDERED; do
        if ! { cp "$_stage/$_f" "$VS_CONF_DIR/.$_f.new" && chmod 0644 "$VS_CONF_DIR/.$_f.new" \
                && mv -f "$VS_CONF_DIR/.$_f.new" "$VS_CONF_DIR/$_f"; }; then
            _swap_ok=no
            break
        fi
    done
    rm -rf "$_stage"
    if [ "$_swap_ok" != yes ]; then
        # Nothing was reloaded yet: putting the old files back is enough.
        restore_backup
        reject "$_gen" "could not move the staged files into $VS_CONF_DIR; the old files are back (will retry)"
        return 2
    fi
    kill -HUP "$_pid"
    if wait_apply_id "$_id"; then
        APPLIED_GEN=$_gen
        LIVE_ID=$_id
        vs_write_status applied "$_gen" "$_gen" "" "$VS_CONF_DIR/vault-settings.effective" \
            || warn "could not write $VS_STATUS_FILE"
        while IFS= read -r _line; do
            log "  $(vs_shown "$_line")"
        done < "$VS_CONF_DIR/vault-settings.effective"
        log "settings generation $_gen applied (nginx reloaded, master $_pid)"
        return 0
    fi
    restore_backup
    kill -HUP "$_pid" || true
    if wait_apply_id "$LIVE_ID"; then
        reject "$_gen" "reload failed: generation $_gen was not live after ${RELOAD_WAIT}s; restored generation $APPLIED_GEN (see the container log)"
    else
        reject "$_gen" "reload failed and generation $APPLIED_GEN is not confirmed live either after the restore (see the container log)"
    fi
    return 0
}

# watch_loop <content applied or rejected at boot>
watch_loop() {
    # One failing command must not end the loop.
    set +e
    _last=$1
    _streak_content=""
    while :; do
        sleep "$VS_POLL"
        _rc=0
        _content=$(vs_read_file) || _rc=$?
        case "$_rc" in
            0) : ;;
            1) continue ;;
            *) _content="__unreadable__" ;;
        esac
        [ "$_content" != "$_last" ] || continue
        if [ "$_rc" != 0 ]; then
            _last=$_content
            reject 0 "settings file $VS_SETTINGS_FILE is not a readable regular file of at most $VS_MAX_BYTES bytes"
            continue
        fi
        # _last moves only when this content got a final answer; a transient
        # failure (return 2) leaves it, so the next tick retries.
        # A retry of the content that already failed transiently stays quiet
        # (one log line and status per failure streak, not one per poll).
        VS_QUIET=0
        [ "$_content" != "$_streak_content" ] || VS_QUIET=1
        _arc=0
        apply_file "$_content" || _arc=$?
        if [ "$_arc" = 2 ]; then
            _streak_content=$_content
        else
            _streak_content=""
            _last=$_content
            VS_QUIET=0
            VS_LAST_REASON=""
        fi
    done
}

# core/tests/test-settings-watch.sh sources this file for its functions only
# (a deterministic rollback test with stubbed nginx, wget and master).
if [ "${VS_WATCH_FUNCTIONS_ONLY:-}" = 1 ]; then
    log "VS_WATCH_FUNCTIONS_ONLY=1: functions loaded, watcher not started (test mode)"
    # shellcheck disable=SC2317 # exit only when run, not sourced
    return 0 2>/dev/null || exit 0
fi

# --- Is this the server start? (same test as 45-) ---------------------------
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
    log "not a server start (an nginx -t/-T/-s/-v run or no nginx command) -- settings watcher not started"
    exit 0
fi

if [ -z "${VS_ENV_CAPTURED:-}" ]; then
    warn "15-vault-settings.envsh did not run (no env capture) -- settings watcher not started"
    exit 0
fi

# --- boot status ----------------------------------------------------------------
if vs_write_status "${VS_BOOT_STATE:-none}" "${VS_BOOT_SEEN:-$APPLIED_GEN}" "$APPLIED_GEN" "${VS_BOOT_REASON:-}" "$VS_CONF_DIR/vault-settings.effective"; then
    log "boot status written to $VS_STATUS_FILE (${VS_BOOT_STATE:-none}, file generation ${VS_BOOT_SEEN:-$APPLIED_GEN}, live generation $APPLIED_GEN)"
else
    warn "could not write $VS_STATUS_FILE; vault-api cannot show the core settings state"
fi

# The boot already applied the file's current content (or rejected it), so
# the loop starts from it: no reload for an unchanged file.
_boot_rc=0
_boot_content=$(vs_read_file) || _boot_rc=$?
[ "$_boot_rc" = 0 ] || _boot_content=""
watch_loop "$_boot_content" < /dev/null &
log "settings watcher started (pid $!): $VS_SETTINGS_FILE checked every ${VS_POLL}s, live generation $APPLIED_GEN"
exit 0
