#!/usr/bin/env bash
# Docker-free test of core/docker/45-vault-eventlog-rotate.sh's start
# decisions (WP D15-ROT). Runs the real hook under `sh` and asserts:
#   - every invalid VAULT_EVENT_LOG_ROTATE_BYTES / _INTERVAL is refused with
#     the hook's FATAL line naming the variable, also with the event log off;
#   - event log off, rotation off (0, 000) and a non-server parent command
#     (nginx -t, -T, -tq, -s reopen, -V, no nginx at all) exit 0 WITHOUT
#     starting the loop, each with its own log line.
# The parent command is modelled the way the stock entrypoint calls a hook:
# the hook's parent process carries the container command in its argv.
#
# Runs standalone (bash core/tests/test-eventlog-rotate-hook.sh) and from
# .github/scripts/verify-core-nginx.sh's docker-free steps. Exit 0 = pass.
#
# What this does NOT prove: an actual rotation, the reopen, the new file's
# owner -- verify-core-nginx.sh's live rotation check runs the real
# entrypoint and a real nginx for that. The "server start" branch is not
# run here, because it forks a loop.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$(cd "$script_dir/.." && pwd)/docker/45-vault-eventlog-rotate.sh"
[ -f "$HOOK" ] || { echo "missing $HOOK" >&2; exit 1; }

unset VAULT_EVENT_LOG VAULT_EVENT_LOG_ROTATE_BYTES VAULT_EVENT_LOG_ROTATE_INTERVAL

pass=0
fail=0
ok()   { echo "ok:   $1"; pass=$((pass + 1)); }
bad()  { echo "FAIL: $1"; fail=$((fail + 1)); }

export HOOK
# The child scripts below expand $HOOK themselves, hence single quotes.
# shellcheck disable=SC2016
PARENT='sh "$HOOK"; exit $?'

# refuse <label> <expected variable> <env assignments...>
refuse() {
    local label="$1" var="$2" out rc=0
    shift 2
    out=$(env "$@" bash -c "$PARENT" nginx -t 2>&1) || rc=$?
    if [ "$rc" != 0 ] && grep -qF "45-vault-eventlog-rotate.sh: FATAL: $var=" <<<"$out"; then
        ok "refused: $label"
    else
        bad "$label: rc=$rc, output: $out"
    fi
}

# skip <label> <expected log text> <env assignments...> -- <parent argv...>
skip() {
    local label="$1" want="$2" out rc=0
    shift 2
    local -a envs=()
    while [ "$1" != "--" ]; do envs+=("$1"); shift; done
    shift
    out=$(env "${envs[@]}" bash -c "$PARENT" "$@" 2>&1) || rc=$?
    if [ "$rc" = 0 ] && grep -qF "$want" <<<"$out" && ! grep -q 'rotating .* checked every' <<<"$out"; then
        ok "not started: $label"
    else
        bad "$label: rc=$rc, output: $out"
    fi
}

ON=VAULT_EVENT_LOG=/vault/logs/event.log

refuse "bytes not a number"         VAULT_EVENT_LOG_ROTATE_BYTES    "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=abc
refuse "bytes with a unit"          VAULT_EVENT_LOG_ROTATE_BYTES    "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=64m
refuse "bytes negative"             VAULT_EVENT_LOG_ROTATE_BYTES    "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=-1
refuse "bytes with a blank"         VAULT_EVENT_LOG_ROTATE_BYTES    "$ON" "VAULT_EVENT_LOG_ROTATE_BYTES=1 048 576"
refuse "bytes below 1 MiB"          VAULT_EVENT_LOG_ROTATE_BYTES    "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=1048575
refuse "bytes 16 digits"            VAULT_EVENT_LOG_ROTATE_BYTES    "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=1000000000000000
refuse "bytes non-ASCII digit"      VAULT_EVENT_LOG_ROTATE_BYTES    "$ON" "VAULT_EVENT_LOG_ROTATE_BYTES=2097152$(printf '\xd9\xa3')"
refuse "interval 0"                 VAULT_EVENT_LOG_ROTATE_INTERVAL "$ON" VAULT_EVENT_LOG_ROTATE_INTERVAL=0
refuse "interval 000"               VAULT_EVENT_LOG_ROTATE_INTERVAL "$ON" VAULT_EVENT_LOG_ROTATE_INTERVAL=000
refuse "interval 3601"              VAULT_EVENT_LOG_ROTATE_INTERVAL "$ON" VAULT_EVENT_LOG_ROTATE_INTERVAL=3601
refuse "interval with a unit"       VAULT_EVENT_LOG_ROTATE_INTERVAL "$ON" VAULT_EVENT_LOG_ROTATE_INTERVAL=1m
refuse "bad bytes, event log off"   VAULT_EVENT_LOG_ROTATE_BYTES    VAULT_EVENT_LOG= VAULT_EVENT_LOG_ROTATE_BYTES=abc

skip "event log off"            "no cache-event log, no rotation"   VAULT_EVENT_LOG= -- nginx -g "daemon off;"
skip "rotation off (0)"         "rotation of /vault/logs/event.log is OFF" "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=0 -- nginx -g "daemon off;"
skip "rotation off (000)"       "rotation of /vault/logs/event.log is OFF" "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=000 -- nginx -g "daemon off;"
skip "nginx -t"                 "not a server start" "$ON" -- nginx -t -p /vault -c /etc/nginx/nginx.conf
skip "nginx -T"                 "not a server start" "$ON" -- nginx -T
skip "nginx -tq"                "not a server start" "$ON" -- nginx -tq
skip "nginx -s reopen"          "not a server start" "$ON" -- nginx -s reopen
skip "nginx -V"                 "not a server start" "$ON" -- /usr/sbin/nginx -V
skip "nginx-debug -t"           "not a server start" "$ON" -- nginx-debug -g "daemon off;" -t
skip "no nginx command"         "not a server start" "$ON" -- sh -c true
skip "valid values, nginx -t"   "not a server start" "$ON" VAULT_EVENT_LOG_ROTATE_BYTES=0067108864 VAULT_EVENT_LOG_ROTATE_INTERVAL=0005 -- nginx -t

# A path outside /vault (25- refuses it first in the container; restated here).
refuse "event log outside /vault" VAULT_EVENT_LOG VAULT_EVENT_LOG=/etc/event.log

echo "test-eventlog-rotate-hook: $pass passed, $fail failed"
[ "$fail" = 0 ]
