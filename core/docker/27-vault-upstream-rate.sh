#!/bin/sh
# SteamHangar vault-core container hook -- aggregate upstream rate cap
# (WP TH-1a; measurements in poc/throttle/RESULTS-THROTTLE-20260929.md,
# RESULTS-THROTTLE-DYNAMIC-20260930.md and RESULTS-TH1-20261001.md).
#
# Renders /etc/nginx/vault-upstream-rate.conf, which nginx.conf pulls in with
# `include vault-upstream-rate.conf;` (http level) and whose result variable
# $vault_upstream_rate feeds `proxy_limit_rate` in location @miss -- i.e. the
# Steam -> vault (upstream) read of a cache MISS. HITs never reach @miss.
#
# Inputs (env only, read once here at container start -- see core/README.md
# "Upstream rate cap"):
#   VAULT_UPSTREAM_RATE         aggregate bytes per second, nginx size syntax:
#                               digits with an optional k/K (x1024) or m/M
#                               (x1048576) suffix, e.g. 800k = 819200 B/s.
#                               Empty/unset = no cap.
#   VAULT_UPSTREAM_RATE_WINDOW  HH:MM-HH:MM, the SAME grammar as
#                               VAULT_SCHEDULE_WINDOW (api/vault_api/
#                               schedule_window.py): full speed INSIDE the
#                               window, capped outside it. Empty/unset = the
#                               cap applies around the clock.
#
# Why a generated include and not envsubst: nginx has no arithmetic, and the
# aggregate must be divided by the live number of in-flight requests. The
# division is precomputed here into a bucket map keyed on nginx's own
# connection counter (Weg A of TH-0b, user decision 2026-09-30):
#     map $connections_writing $vault_upstream_rate_share {
#         1 <total>; 2 <total/2>; ... 1024 <total/1024>; default <total/1024>;
#     }
# Every value is an integer floor, so N requests sharing bucket N never sum
# to more than the total. The divisor variable is $connections_writing, not
# $connections_active: the latter counts idle keep-alive connections too
# (TH-0b measured 0.49x of the target with 8 idle), see RESULTS-TH1.
#
# FAIL-CLOSED. nginx treats a `proxy_limit_rate` value it cannot parse as 0,
# i.e. UNLIMITED (TH-0b section 2.0, measured), and so is an empty one. So:
#   - an invalid VAULT_UPSTREAM_RATE / VAULT_UPSTREAM_RATE_WINDOW stops the
#     boot here (docker-entrypoint.sh runs hooks under `set -e`);
#   - every map carries a `default`, every capped value is >= 1;
#   - the rendered file is re-read and asserted before nginx ever sees it;
#   - 40-vault-preflight.sh takes a second look: the include and the
#     proxy_limit_rate line are wired in, and with a cap set the file has a
#     connection-count map with a positive default. It is not a full
#     independent re-derivation of the render.
# With no cap configured the file still defines $vault_upstream_rate (= 0,
# unlimited), so the directive in nginx.conf is always present and the
# native core/nginx/nginx.conf can carry the identical lines (its static
# core/nginx/vault-upstream-rate.conf is byte-for-byte this script's cap-off
# output -- check-config-drift.sh asserts that).
#
# Usage: 27-vault-upstream-rate.sh [output-file]
#   (no argument when run by docker-entrypoint.sh; the drift check passes a
#   temp path to compare the cap-off render against the native file)
#
# POSIX sh (busybox ash in the image, dash in CI); no bashisms.

set -eu

ME="27-vault-upstream-rate.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

OUT="${1:-/etc/nginx/vault-upstream-rate.conf}"
# Chosen by measurement (RESULTS-TH1-20261001.md section 2).
DIVISOR_VAR='$connections_writing'
# One bucket per possible connection: nginx.conf pins worker_processes 1 and
# worker_connections 1024, so $connections_writing can never exceed 1024 and
# every live count has its own exact bucket -- no count can fall through to
# the default and overshoot the total. check-config-drift.sh asserts
# BUCKETS >= worker_processes x worker_connections, so raising either in the
# template without raising this fails CI. The default only covers a count of
# 0 (e.g. a variable evaluated in the log phase).
BUCKETS=1024
# Smallest accepted total: total/BUCKETS must stay >= 1, because a bucket
# that rounds down to 0 would be "unlimited" to nginx.
MIN_TOTAL=$BUCKETS
# The time pattern prefix for $time_iso8601 ("2026-10-01T03:15:00+02:00").
# The full date anchor is the form TH-0 measured flipping with the live clock.
TIME_PREFIX='^[0-9]{4}-[0-9]{2}-[0-9]{2}T'

RATE_RAW="${VAULT_UPSTREAM_RATE:-}"
WINDOW_RAW="${VAULT_UPSTREAM_RATE_WINDOW:-}"

# --- VAULT_UPSTREAM_RATE ------------------------------------------------------
# Strict: no whitespace, no leading zero (busybox/dash arithmetic would read
# "0800" as octal), 1-9 digits, optional k/K/m/M. "0" is refused rather than
# taken as "unlimited": leave the variable empty for that.
parse_rate() {
    _v=$1
    case "$_v" in
        *[!0123456789kKmM]*|"")
            die "VAULT_UPSTREAM_RATE='$_v' is not a byte rate. Use digits with an optional
  k (x1024) or m (x1048576) suffix, e.g. 800k = 819200 bytes/s aggregate; leave
  it empty for no cap. Refusing to start: nginx would treat an unparseable
  rate as UNLIMITED." ;;
    esac
    _unit=""
    case "$_v" in
        *[kKmM]) _unit=${_v#"${_v%?}"}; _num=${_v%?} ;;
        *)       _num=$_v ;;
    esac
    case "$_num" in
        ""|*[!0123456789]*)
            die "VAULT_UPSTREAM_RATE='$_v': the unit suffix must come last and only once
  (e.g. 800k or 2m). Refusing to start." ;;
        0*)
            die "VAULT_UPSTREAM_RATE='$_v' starts with 0. Leave the variable empty for no
  cap; a leading zero is refused instead of guessed at. Refusing to start." ;;
    esac
    if [ "${#_num}" -gt 9 ]; then
        die "VAULT_UPSTREAM_RATE='$_v' has more than 9 digits. Use a k or m suffix.
  Refusing to start."
    fi
    case "$_unit" in
        k|K) _mult=1024 ;;
        m|M) _mult=1048576 ;;
        *)   _mult=1 ;;
    esac
    TOTAL=$((_num * _mult))
    if [ "$TOTAL" -lt "$MIN_TOTAL" ]; then
        die "VAULT_UPSTREAM_RATE='$_v' = $TOTAL bytes/s is below the minimum of $MIN_TOTAL:
  the cap is divided into up to $BUCKETS shares and a share of 0 would mean
  UNLIMITED to nginx. Refusing to start."
    fi
}

# --- VAULT_UPSTREAM_RATE_WINDOW ----------------------------------------------
# Mirrors api/vault_api/schedule_window.py parse_window() exactly:
#   strip, exactly one '-', each side stripped, zero-padded HH:MM with ASCII
#   digits, minute 00-59, hour 00-23, '24:00' accepted as END only,
#   start == end refused. Start inclusive, end exclusive, whole minutes,
#   end < start = overnight (wraps midnight).
# One deliberate, fail-closed divergence: Python's str.strip() also removes
# non-ASCII whitespace (U+00A0, U+2000...); this shell strip removes the
# ASCII whitespace set str.isspace() accepts (space, \t \n \v \f \r and
# \x1c-\x1f) only, so a value padded with non-ASCII whitespace that vault-api
# would accept stops vault-core's boot instead of being reinterpreted.
WS=$(printf ' \t\n\v\f\r\034\035\036\037X')
WS=${WS%X}

strip() {
    _s=$1
    while :; do
        case "$_s" in
            [$WS]*) _s=${_s#?} ;;
            *) break ;;
        esac
    done
    while :; do
        case "$_s" in
            *[$WS]) _s=${_s%?} ;;
            *) break ;;
        esac
    done
    printf '%s' "$_s"
}

# parse_time <text> <end?> -> sets MINUTE (0-1440)
parse_time() {
    _t=$1
    case "$_t" in
        [0123456789][0123456789]:[0123456789][0123456789]) : ;;
        *) die "VAULT_UPSTREAM_RATE_WINDOW: '$_t' is not a valid time of day; expected
  zero-padded HH:MM (24-hour), e.g. '09:00'. Refusing to start." ;;
    esac
    _h=${_t%%:*}; _m=${_t#*:}
    _h=${_h#0}; _m=${_m#0}
    _h=${_h:-0}; _m=${_m:-0}
    if [ "$_m" -gt 59 ]; then
        die "VAULT_UPSTREAM_RATE_WINDOW: '$_t' has an invalid minute ($_m); must be 00-59.
  Refusing to start."
    fi
    if [ "$_h" -eq 24 ]; then
        if [ "$2" != "end" ]; then
            die "VAULT_UPSTREAM_RATE_WINDOW: '24:00' is only accepted as the END of a
  window (meaning end of day); a window cannot start at 24:00. Refusing to start."
        fi
        if [ "$_m" -ne 0 ]; then
            die "VAULT_UPSTREAM_RATE_WINDOW: '$_t' is invalid; the only accepted 24-hour
  spelling is exactly '24:00'. Refusing to start."
        fi
        MINUTE=1440
        return 0
    fi
    if [ "$_h" -gt 23 ]; then
        die "VAULT_UPSTREAM_RATE_WINDOW: '$_t' has an invalid hour ($_h); must be 00-23
  (or exactly '24:00' as the window's end). Refusing to start."
    fi
    MINUTE=$((_h * 60 + _m))
}

parse_window() {
    _w=$(strip "$1")
    case "$_w" in
        *-*-*|"") die "VAULT_UPSTREAM_RATE_WINDOW='$1' must contain exactly one '-' separating
  start and end, e.g. '03:00-07:00'. Refusing to start." ;;
        *-*) : ;;
        *) die "VAULT_UPSTREAM_RATE_WINDOW='$1' must contain exactly one '-' separating
  start and end, e.g. '03:00-07:00'. Refusing to start." ;;
    esac
    parse_time "$(strip "${_w%%-*}")" start; W_START=$MINUTE
    parse_time "$(strip "${_w#*-}")" end;    W_END=$MINUTE
    if [ "$W_START" -eq "$W_END" ]; then
        die "VAULT_UPSTREAM_RATE_WINDOW='$1' starts and ends at the same time, which is
  ambiguous. Use '00:00-24:00' for a window that is always open. Refusing to start."
    fi
}

fmt_hhmm() { printf '%02d:%02d' $(($1 / 60)) $(($1 % 60)); }

# minute_regex <lo> <hi>  (0 <= lo <= hi <= 59, inclusive) -> alternation of
# two-digit minute patterns, grouped by tens digit, e.g. 30..59 -> 3[0-9]|4[0-9]|5[0-9]
minute_regex() {
    _lo=$1; _hi=$2; _out=""
    _d=$((_lo / 10))
    while [ "$_d" -le $((_hi / 10)) ]; do
        _ulo=0; _uhi=9
        [ "$_d" -eq $((_lo / 10)) ] && _ulo=$((_lo % 10))
        [ "$_d" -eq $((_hi / 10)) ] && _uhi=$((_hi % 10))
        if [ "$_ulo" -eq "$_uhi" ]; then _alt="$_d$_ulo"; else _alt="${_d}[$_ulo-$_uhi]"; fi
        _out="${_out:+$_out|}$_alt"
        _d=$((_d + 1))
    done
    printf '%s' "$_out"
}

# in_window <minute-of-day> -> 0 if inside [W_START, W_END) (wrapping)
in_window() {
    if [ "$W_END" -lt "$W_START" ]; then
        [ "$1" -ge "$W_START" ] || [ "$1" -lt "$W_END" ]
    else
        [ "$1" -ge "$W_START" ] && [ "$1" -lt "$W_END" ]
    fi
}

# emit_window_lines: one map line per hour that has any minute inside the
# window -- the bare hour when all 60 minutes are inside, else the hour plus
# its minute ranges (an hour holding both edges of an overnight window, e.g.
# 22:45-22:15, gets two ranges).
emit_window_lines() {
    _hr=0
    while [ "$_hr" -lt 24 ]; do
        _alts=""; _run_lo=-1; _mn=0; _full=1
        while [ "$_mn" -le 60 ]; do
            if [ "$_mn" -lt 60 ] && in_window $((_hr * 60 + _mn)); then
                [ "$_run_lo" -lt 0 ] && _run_lo=$_mn
            else
                [ "$_mn" -lt 60 ] && _full=0
                if [ "$_run_lo" -ge 0 ]; then
                    _r=$(minute_regex "$_run_lo" $((_mn - 1)))
                    _alts="${_alts:+$_alts|}$_r"
                    _run_lo=-1
                fi
            fi
            _mn=$((_mn + 1))
        done
        _hh=$(printf '%02d' "$_hr")
        if [ "$_full" -eq 1 ]; then
            printf '    "~%s%s:" 1;\n' "$TIME_PREFIX" "$_hh"
        elif [ -n "$_alts" ]; then
            printf '    "~%s%s:(?:%s)" 1;\n' "$TIME_PREFIX" "$_hh" "$_alts"
        fi
        _hr=$((_hr + 1))
    done
}

emit_share_map() {
    # $1 = name of the variable the map defines
    printf 'map %s %s {\n' "$DIVISOR_VAR" "$1"
    _n=1
    while [ "$_n" -le "$BUCKETS" ]; do
        printf '    %s %s;\n' "$_n" $((TOTAL / _n))
        _n=$((_n + 1))
    done
    printf '    default %s;\n' $((TOTAL / BUCKETS))
    printf '}\n'
}

# --- render ------------------------------------------------------------------
TOTAL=0
[ -z "$RATE_RAW" ] || parse_rate "$RATE_RAW"

W_START=""; W_END=""; WINDOW_NORM=""
if [ -n "$(strip "$WINDOW_RAW")" ]; then
    parse_window "$WINDOW_RAW"
    WINDOW_NORM="$(fmt_hhmm "$W_START")-$(fmt_hhmm "$W_END")"
fi

tmp="$OUT.tmp.$$"
trap 'rm -f "$tmp"' EXIT INT TERM
{
    echo "# Generated at container start by /docker-entrypoint.d/$ME -- do not edit."
    echo "# Source: VAULT_UPSTREAM_RATE / VAULT_UPSTREAM_RATE_WINDOW, see core/README.md"
    echo "# \"Upstream rate cap\". Defines \$vault_upstream_rate for proxy_limit_rate in @miss."
    if [ "$TOTAL" -eq 0 ]; then
        echo "# No cap configured: 0 = unlimited."
        echo 'map $time_iso8601 $vault_upstream_rate {'
        echo '    default 0;'
        echo '}'
    elif [ -z "$WINDOW_NORM" ]; then
        echo "# Cap $TOTAL bytes/s aggregate, around the clock, divided by $DIVISOR_VAR."
        emit_share_map '$vault_upstream_rate'
    else
        echo "# Cap $TOTAL bytes/s aggregate, divided by $DIVISOR_VAR, OUTSIDE the window"
        echo "# $WINDOW_NORM (container local time, start inclusive, end exclusive)."
        emit_share_map '$vault_upstream_rate_share'
        echo 'map $time_iso8601 $vault_upstream_in_window {'
        echo '    default 0;'
        emit_window_lines
        echo '}'
        echo 'map $vault_upstream_in_window $vault_upstream_rate {'
        echo '    1 0;'
        echo '    default $vault_upstream_rate_share;'
        echo '}'
    fi
} > "$tmp"

# --- assert the render before nginx sees it ----------------------------------
# Independent of how the file was produced: re-read it and check the shape.
assert_fail() { die "rendered $OUT failed its self-check: $*. Refusing to start
  with a rate map that could evaluate to empty or 0 (= UNLIMITED in nginx)."; }

defs=$(grep -c '^map [^ ]* \$vault_upstream_rate {$' "$tmp" || true)
[ "$defs" = "1" ] || assert_fail "expected exactly 1 map defining \$vault_upstream_rate, found $defs"
opens=$(grep -c '{$' "$tmp" || true)
closes=$(grep -c '^}$' "$tmp" || true)
[ "$opens" = "$closes" ] || assert_fail "unbalanced braces ($opens open, $closes close)"
# Every value line is "<key> <value>;" with a non-empty value -- no line may
# end in a bare key, and no value may be an empty string.
if grep -v '^#' "$tmp" | grep -v '{$' | grep -v '^}$' | grep -qvE '^    [^ ]+ [^ ;]+;$'; then
    assert_fail "a map line is not of the form '<key> <value>;'"
fi
grep -q '""' "$tmp" && assert_fail "an empty-string value is present"

if [ "$TOTAL" -eq 0 ]; then
    [ "$(grep -c '^    default 0;$' "$tmp")" = "1" ] || assert_fail "cap off but no 'default 0;'"
    grep -q 'connections_' "$tmp" && assert_fail "cap off but a connection map was rendered"
else
    # The share map alone: from its "map $connections_..." line to its "}".
    share=$(awk '/^map \$connections_/ { f = 1 } f { print } f && /^}$/ { exit }' "$tmp")
    [ "$(printf '%s\n' "$share" | grep -c '^map ')" = "1" ] || assert_fail "no connection-count share map"
    buckets=$(printf '%s\n' "$share" | grep -cE '^    [0-9]+ [1-9][0-9]*;$' || true)
    [ "$buckets" = "$BUCKETS" ] || assert_fail "expected $BUCKETS positive bucket lines in the share map, found $buckets"
    printf '%s\n' "$share" | grep -qx "    1 $TOTAL;" || assert_fail "bucket 1 is not the total ($TOTAL)"
    printf '%s\n' "$share" | grep -qx "    default $((TOTAL / BUCKETS));" || assert_fail "no positive default bucket"
    [ "$(printf '%s\n' "$share" | grep -c '^    ')" = "$((BUCKETS + 1))" ] \
        || assert_fail "the share map has lines other than $BUCKETS buckets and a default"
    if [ -n "$WINDOW_NORM" ]; then
        grep -qx '    default $vault_upstream_rate_share;' "$tmp" \
            || assert_fail "the outside-window default is not the capped share"
        wl=$(grep -c '^    "~' "$tmp" || true)
        [ "$wl" -ge 1 ] || assert_fail "the window map has no time pattern"
    fi
fi

mv "$tmp" "$OUT"
trap - EXIT INT TERM

if [ "$TOTAL" -eq 0 ]; then
    if [ -n "$WINDOW_NORM" ]; then
        log "VAULT_UPSTREAM_RATE unset/empty -- no upstream cap; VAULT_UPSTREAM_RATE_WINDOW=$WINDOW_NORM is valid but has nothing to lift"
    else
        log "VAULT_UPSTREAM_RATE unset/empty -- no upstream cap (proxy_limit_rate 0)"
    fi
elif [ -z "$WINDOW_NORM" ]; then
    log "upstream cap ON: $TOTAL bytes/s aggregate, around the clock, per-request share = total / $DIVISOR_VAR (buckets 1..$BUCKETS, floor $((TOTAL / BUCKETS)))"
else
    log "upstream cap ON: $TOTAL bytes/s aggregate OUTSIDE $WINDOW_NORM (full speed inside; container local time, TZ='${TZ:-<unset, UTC>}', now $(date '+%H:%M %Z')), per-request share = total / $DIVISOR_VAR"
    if [ "$W_START" -eq 0 ] && [ "$W_END" -eq 1440 ]; then
        log "note: the window 00:00-24:00 covers the whole day -- the cap never applies"
    fi
fi
