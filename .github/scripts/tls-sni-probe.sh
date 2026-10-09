#!/bin/sh
# WP CORE-FIX-3 (ADR-0020): offline probe of vault-core's HTTPS passthrough
# SNI allowlist, run INSIDE the pinned nginx image by
# .github/scripts/verify-core-nginx.sh, against the rendered config already
# started on 127.0.0.1:443 in a container with NO network.
#
# Usage: sh tls-sni-probe.sh <access log> <error log>
#   The access log receives nginx's stdout (the stream block logs one
#   `tls client=... sni="..." target="..." upstream=...` line per
#   connection there); the error log receives its stderr.
#
# Why raw ClientHellos instead of curl/openssl: the cases that matter most
# are the ones a well-behaved client never sends -- a trailing dot, a
# newline or NUL inside the name, no server_name extension at all, plain
# HTTP on 443. curl normalises or refuses most of those, and the image has
# no openssl binary. So this script builds a minimal TLS 1.2-shaped
# ClientHello itself, byte by byte, with exactly the SNI given.
#
# What counts as the verdict, per connection (one new access-log line):
#   refused: target="" AND no resolver or connect attempt in the error log
#            (with no network, any attempt shows up there as an error);
#   allowed: target="<sni>:443" AND the error log shows nginx trying to
#            RESOLVE that name -- i.e. it would have dialled Valve.
# A status code alone cannot tell the two apart (both end in an error
# without a network), hence the log checks.
#
# Exit 0 = every case behaved as expected.

set -u

ACCESS=${1:?access log path}
ERROR=${2:?error log path}
status=0

# b <decimal> -> one raw byte. busybox and dash printf both take \ooo.
b() { printf "\\$(printf '%03o' "$1")"; }
# u16 <decimal> -> two bytes, big-endian.
u16() { b $(( ($1 >> 8) & 255 )); b $(( $1 & 255 )); }
# u24 <decimal> -> three bytes, big-endian.
u24() { b $(( ($1 >> 16) & 255 )); u16 $(( $1 & 65535 )); }

# hello <sni as a printf format, or the literal NONE for no SNI extension>
# Writes one TLS record containing a ClientHello: legacy version 3.3, a
# fixed 32-byte random, empty session id, one cipher suite
# (TLS_AES_128_GCM_SHA256), null compression, and -- unless NONE -- a single
# server_name extension of type host_name carrying the given bytes.
hello() {
    if [ "$1" = "NONE" ]; then
        body=41
    else
        # shellcheck disable=SC2059 # the name IS a printf format on purpose
        n=$(printf "$1" | wc -c)
        body=$((n + 52))
    fi
    b 22; u16 769; u16 $((body + 4))          # record: handshake, TLS 1.0 record version, length
    b 1; u24 "$body"                           # handshake: client_hello, length
    u16 771                                    # client_version 3.3
    i=0; while [ $i -lt 32 ]; do b $((i + 1)); i=$((i + 1)); done   # random
    b 0                                        # session_id length
    u16 2; u16 4865                            # cipher_suites: 0x1301
    b 1; b 0                                   # compression: null
    if [ "$1" != "NONE" ]; then
        u16 $((n + 9))                         # extensions length
        u16 0; u16 $((n + 5))                  # server_name extension, length
        u16 $((n + 3))                         # server_name_list length
        b 0; u16 "$n"                          # host_name, length
        # shellcheck disable=SC2059
        printf "$1"
    fi
}

# send <hold seconds> <payload command...>: open one connection to
# 127.0.0.1:443, write the payload, and keep the client side open for
# <hold> seconds (a pipeline lasts as long as its left side). A refused
# name is closed by nginx at once; an allowed one is held open while the
# resolver tries and fails (resolver_timeout 5s, no network), so allowed
# probes hold 7s.
send() {
    hold=$1; shift
    { "$@"; sleep "$hold"; } | timeout $((hold + 2)) nc 127.0.0.1 443 >/dev/null 2>&1 || true
}

# new_tls_line <access-log line count before>: wait (bounded, ~8s) for one
# new stream log line and print it.
new_tls_line() {
    i=0
    while [ $i -lt 40 ]; do
        l=$(tail -n +$(($1 + 1)) "$ACCESS" | grep ' tls client=' | tail -n 1)
        [ -n "$l" ] && { printf '%s\n' "$l"; return 0; }
        sleep 0.2; i=$((i + 1))
    done
    return 0
}

# probe <expect: refuse|allow> <label> <sni format or NONE> [expected target]
probe() {
    want=$1 label=$2 sni=$3 target=${4:-}
    a0=$(wc -l < "$ACCESS"); e0=$(wc -l < "$ERROR")
    if [ "$want" = "allow" ]; then send 7 hello "$sni"; else send 1 hello "$sni"; fi
    line=$(new_tls_line "$a0")
    errs=$(tail -n +$((e0 + 1)) "$ERROR")
    why=""
    [ -n "$line" ] || why="$why no tls access-log line;"
    case "$want" in
        refuse)
            case "$line" in *' target="" '*) : ;; *) why="$why target is not empty;" ;; esac
            if printf '%s\n' "$errs" | grep -q -i -E 'resolv|connect\(\)'; then
                why="$why the error log shows a resolve/connect attempt;"
            fi ;;
        allow)
            case "$line" in *" target=\"$target\" "*) : ;; *) why="$why target is not \"$target\";" ;; esac
            if ! printf '%s\n' "$errs" | grep -q -i 'resolv'; then
                why="$why no resolve attempt in the error log;"
            fi ;;
    esac
    if [ -z "$why" ]; then
        echo "tls guard OK ($want): $label -- $(printf '%s' "$line" | sed 's/^.* tls //')"
    else
        echo "FAIL: tls guard ($want): $label ->$why"
        printf '%s\n' "$line" | sed 's/^/    access: /'
        printf '%s\n' "$errs" | sed '/^$/d; s/^/    error:  /'
        status=1
    fi
}

# --- allowed: a real CDN name, any case, several labels --------------------
probe allow  "a Steam CDN edge"                    "cache2-ams1.steamcontent.com"   "cache2-ams1.steamcontent.com:443"
probe allow  "mixed case (DNS is case-insensitive)" "CACHE2-AMS1.SteamContent.COM"   "CACHE2-AMS1.SteamContent.COM:443"
probe allow  "several labels"                      "a.b-c.steamcontent.com"         "a.b-c.steamcontent.com:443"
probe allow  "63-character label"                  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.steamcontent.com" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.steamcontent.com:443"

# --- refused: everything else ----------------------------------------------
probe refuse "no SNI extension"                    NONE
probe refuse "off-list name"                       "example.com"
probe refuse "bare apex"                           "steamcontent.com"
probe refuse "suffix without a dot"                "evilsteamcontent.com"
probe refuse "Steam name as a prefix"              "x.steamcontent.com.evil.example"
probe refuse "trailing dot (RFC 6066 forbids it)"  "x.steamcontent.com."
probe refuse "empty label"                         "x..steamcontent.com"
probe refuse "leading dot"                         ".steamcontent.com"
probe refuse "underscore"                          "x_y.steamcontent.com"
probe refuse "IP literal"                          "127.0.0.1"
probe refuse "port in the name"                    "x.steamcontent.com:443"
probe refuse "trailing newline (the \\z anchor)"    'x.steamcontent.com\n'
probe refuse "embedded newline"                    'x.steamcontent.com\nx.steamcontent.com'
probe refuse "NUL byte"                            'x.steamcontent.com\000'
probe refuse "steamserver.net (HTTP-only family)"  "x.steamserver.net"
probe refuse "leading hyphen in a label"           "-x.steamcontent.com"
probe refuse "trailing hyphen in a label"          "x-.steamcontent.com"
probe refuse "name longer than 253 characters"     "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.steamcontent.com"
probe refuse "64-character label"                  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.steamcontent.com"

# --- refused: not TLS at all ---------------------------------------------------
a0=$(wc -l < "$ACCESS"); e0=$(wc -l < "$ERROR")
send 1 printf 'GET / HTTP/1.1\r\nHost: cache2-ams1.steamcontent.com\r\n\r\n'
line=$(new_tls_line "$a0")
errs=$(tail -n +$((e0 + 1)) "$ERROR")
case "$line" in
    *' target="" '*)
        if printf '%s\n' "$errs" | grep -q -i -E 'resolv|connect\(\)'; then
            echo "FAIL: tls guard (refuse): plain HTTP on 443 caused a resolve/connect attempt"; status=1
        else
            echo "tls guard OK (refuse): plain HTTP on 443 -- $(printf '%s' "$line" | sed 's/^.* tls //')"
        fi ;;
    *) echo "FAIL: tls guard (refuse): plain HTTP on 443 -> '$line'"; status=1 ;;
esac

# --- the loop bound: per-client connection cap -------------------------------
# cap + 6 idle connections from one address (127.0.0.1), each holding its
# slot until preread_timeout (5s) closes it. The per-client cap is the
# configured VAULT_TLS_CLIENT_MAX_CONNS (empty/unset = 64, the default,
# WP CORE-FIX-4d), so at least the excess must be refused by limit_conn
# (stream status 503) at once -- the same thing that stops a resolver loop
# from eating every worker connection. The caller (verify-core-nginx.sh)
# passes the same env the hook rendered, so a hook that ignored the value
# fails here: at a tuned 32 a default 64 would refuse none of 38.
cap="${VAULT_TLS_CLIENT_MAX_CONNS:-64}"
case "$cap" in
    ""|*[!0-9]*) echo "FAIL: per-client cap: VAULT_TLS_CLIENT_MAX_CONNS='$cap' is not a number"; exit 1 ;;
esac
n_conn=$((cap + 6))
a0=$(wc -l < "$ACCESS")
i=0
while [ $i -lt $n_conn ]; do
    ( sleep 7 | timeout 8 nc 127.0.0.1 443 >/dev/null 2>&1 ) &
    i=$((i + 1))
done
wait
capped=$(tail -n +$((a0 + 1)) "$ACCESS" | grep ' tls client=' | grep -c ' status=503 ' || true)
total=$(tail -n +$((a0 + 1)) "$ACCESS" | grep -c ' tls client=' || true)
if [ "$capped" -ge 6 ] && [ "$total" = "$n_conn" ]; then
    echo "tls guard OK: per-client cap $cap -- $capped of $n_conn parallel connections refused with 503"
else
    echo "FAIL: per-client cap $cap: $capped of $total logged connections refused with 503, expected >= 6 of $n_conn"
    status=1
fi

exit $status
