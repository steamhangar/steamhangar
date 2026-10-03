#!/usr/bin/env bash
# Docker-free test of core/docker/28-vault-upstream-pool.sh (WP CORE-FEAT-1b,
# docs/adr/0017-upstream-keepalive-pool.md). Renders VAULT_UPSTREAM_POOL_HOSTS
# lists through the real hook (run with `sh`, the way the container runs it)
# into a temp dir and asserts the shape of the output and every refusal rule.
#
# Runs standalone from anywhere with no arguments:
#     bash core/tests/test-upstream-pool-hook.sh
# and from .github/scripts/verify-core-nginx.sh's docker-free step 0, so
# `dev.sh test-core` and the CI gate run it. Exit 0 = all cases pass, 1 = at
# least one failed; one line per case.
#
# What this does NOT prove: that nginx accepts the rendered groups. `nginx -t`
# on a rendered pool include inside the pinned image is WP CORE-FEAT-1b2's job.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
core_dir="$(cd "$script_dir/.." && pwd)"
HOOK="$core_dir/docker/28-vault-upstream-pool.sh"
NATIVE="$core_dir/nginx/vault-upstream-pool.conf"

[ -f "$HOOK" ]   || { echo "missing $HOOK" >&2; exit 1; }
[ -f "$NATIVE" ] || { echo "missing $NATIVE" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

pass=0
fail=0
ok()   { echo "ok:   $1"; pass=$((pass + 1)); }
bad()  { echo "FAIL: $1: $2" >&2; fail=$((fail + 1)); }

# render <case-name> <hosts-value|UNSET> <output-path> -> stdout+stderr in
# $work/<case>.log, exit code in $rc
render() {
    local name="$1" hosts="$2" out="$3"
    rc=0
    if [ "$hosts" = "UNSET" ]; then
        (unset VAULT_UPSTREAM_POOL_HOSTS; sh "$HOOK" "$out") > "$work/$name.log" 2>&1 || rc=$?
    else
        VAULT_UPSTREAM_POOL_HOSTS="$hosts" sh "$HOOK" "$out" > "$work/$name.log" 2>&1 || rc=$?
    fi
}

count() { grep -c -- "$1" "$2" || true; }

# expect_refused <case-name> <hosts-value> <stderr-fragment>
# The hook must exit non-zero with a FATAL line naming the hook and the
# fragment, and must leave no output file (and no temp file) behind.
expect_refused() {
    local name="$1" hosts="$2" fragment="$3" out="$work/$1.conf"
    render "$name" "$hosts" "$out"
    if [ "$rc" -eq 0 ]; then
        bad "$name" "accepted '$hosts' (exit 0)"; return
    fi
    if ! grep -q '^28-vault-upstream-pool.sh: FATAL: ' "$work/$name.log"; then
        bad "$name" "exit $rc but no '28-vault-upstream-pool.sh: FATAL:' line: $(head -n1 "$work/$name.log")"; return
    fi
    if ! grep -qF -- "$fragment" "$work/$name.log"; then
        bad "$name" "FATAL message does not mention '$fragment': $(head -n1 "$work/$name.log")"; return
    fi
    if [ -e "$out" ]; then
        bad "$name" "refused but wrote $out"; return
    fi
    if ls "$work/$name.conf.tmp."* >/dev/null 2>&1; then
        bad "$name" "refused but left a temp file behind"; return
    fi
    ok "$name: refused ($(grep -m1 'FATAL' "$work/$name.log" | cut -c1-96)...)"
}

# assert_shape <case-name> <file> <host-count> <host...>
# The rendered file has exactly N blocks of the ADR-0017 shape, in list order,
# one sized zone, no resolver, nothing else but comments.
assert_shape() {
    local name="$1" f="$2" n="$3"; shift 3
    local problems=()
    [ "$(count '^upstream [a-z0-9.-]* {$' "$f")" = "$n" ] || problems+=("upstream blocks != $n")
    [ "$(count '^}$' "$f")" = "$n" ] || problems+=("closing braces != $n")
    [ "$(count '^    server [a-z0-9.-]* resolve max_fails=0;$' "$f")" = "$n" ] || problems+=("server ... resolve max_fails=0 lines != $n")
    [ "$(count '^    keepalive 8;$' "$f")" = "$n" ] || problems+=("'keepalive 8;' lines != $n")
    [ "$(count '^    keepalive_timeout 50s;$' "$f")" = "$n" ] || problems+=("'keepalive_timeout 50s;' lines != $n")
    [ "$(count '^    zone vault_edges 256k;$' "$f")" = "1" ] || problems+=("sized zone line != 1")
    [ "$(count '^    zone vault_edges;$' "$f")" = "$((n - 1))" ] || problems+=("size-less zone lines != $((n - 1))")
    grep -qw 'resolver' "$f" && problems+=("a 'resolver' token is present")
    grep -q 'Pooled edges: '"$n"' ' "$f" || problems+=("header does not say 'Pooled edges: $n'")
    if grep -v '^#' "$f" | grep -qvE '^(upstream [a-z0-9.-]+ \{|    (zone|server|keepalive|keepalive_timeout) [^;]+;|\})$'; then
        problems+=("an unexpected non-comment line")
    fi
    # Order: block i names host i, and the sized zone sits in block 1.
    local i=0 h
    for h in "$@"; do
        i=$((i + 1))
        [ "$(grep '^upstream ' "$f" | sed -n "${i}p")" = "upstream $h {" ] || problems+=("block $i is not '$h'")
        grep -qx "    server $h resolve max_fails=0;" "$f" || problems+=("no server line for $h")
    done
    [ "$(grep -A1 '^upstream ' "$f" | sed -n '2p')" = "    zone vault_edges 256k;" ] || problems+=("the sized zone is not in the first block")
    if [ "${#problems[@]}" -ne 0 ]; then
        bad "$name" "$(IFS='; '; echo "${problems[*]}")"
        return 1
    fi
    return 0
}

# --- 1. unset -> header-only file, byte-identical to the static native file --
render unset UNSET "$work/unset.conf"
if [ "$rc" -ne 0 ]; then
    bad "unset var" "hook failed: $(head -n1 "$work/unset.log")"
elif ! cmp -s "$work/unset.conf" "$NATIVE"; then
    bad "unset var" "render differs from core/nginx/vault-upstream-pool.conf"
elif grep -qv '^#' "$work/unset.conf"; then
    bad "unset var" "a non-comment line in the empty render"
elif ! grep -q 'Pooled edges: 0 ' "$work/unset.conf"; then
    bad "unset var" "header does not say 'Pooled edges: 0'"
else
    ok "unset var: header-only render, byte-identical to the static native file"
fi

# --- 2. set but empty -> the same -------------------------------------------
render empty "" "$work/empty.conf"
if [ "$rc" -ne 0 ] || ! cmp -s "$work/empty.conf" "$NATIVE"; then
    bad "empty var" "render differs from the static native file or hook failed"
else
    ok "empty var: same header-only render"
fi

# --- 3. one host --------------------------------------------------------------
render one "cache1-fra2.steamcontent.com" "$work/one.conf"
if [ "$rc" -ne 0 ]; then
    bad "one host" "hook failed: $(head -n1 "$work/one.log")"
elif assert_shape "one host" "$work/one.conf" 1 cache1-fra2.steamcontent.com; then
    ok "one host: 1 block, sized zone, resolve max_fails=0, keepalive 8, keepalive_timeout 50s, no resolver"
fi

# --- 4. two hosts: the exact golden render (the format is the contract) ------
render two "cache1-fra2.steamcontent.com dist-fra1.discovery.steamserver.net" "$work/two.conf"
cat > "$work/two.golden" <<'EOF'
# SteamHangar vault-core -- upstream keepalive pool include (ADR-0017).
# Rendered at container start by /docker-entrypoint.d/28-vault-upstream-pool.sh from
# VAULT_UPSTREAM_POOL_HOSTS -- do not edit; see core/README.md "Upstream
# keepalive pool". Natively (core/nginx/vault-upstream-pool.conf) this file is
# the empty render, byte for byte (check-config-drift.sh asserts it).
# Pooled edges: 2 (keepalive 8 idle per group, ceiling 32 idle = at most 4 groups).
# The shared zone's size is given once, on the first group (nginx docs, zone:
# "Several groups may share the same zone. In this case, it is enough to
# specify the size only once."). No DNS directive inside a group: each one
# inherits the http-level setting, ipv6=off valid=30s included (ADR-0017 (c)).
upstream cache1-fra2.steamcontent.com {
    zone vault_edges 256k;
    server cache1-fra2.steamcontent.com resolve max_fails=0;
    keepalive 8;
    keepalive_timeout 50s;
}
upstream dist-fra1.discovery.steamserver.net {
    zone vault_edges;
    server dist-fra1.discovery.steamserver.net resolve max_fails=0;
    keepalive 8;
    keepalive_timeout 50s;
}
EOF
if [ "$rc" -ne 0 ]; then
    bad "two hosts" "hook failed: $(head -n1 "$work/two.log")"
elif ! cmp -s "$work/two.conf" "$work/two.golden"; then
    bad "two hosts" "render differs from the golden file:"
    diff -u "$work/two.golden" "$work/two.conf" >&2 || true
else
    ok "two hosts (one per family): byte-identical to the golden render"
fi

# --- 5. four hosts = the ceiling -------------------------------------------
four_hosts=(cache1-fra2.steamcontent.com cache2-fra2.steamcontent.com cache3-fra2.steamcontent.com dist-fra1.discovery.steamserver.net)
four="${four_hosts[*]}"
render four "$four" "$work/four.conf"
if [ "$rc" -ne 0 ]; then
    bad "four hosts" "hook failed: $(head -n1 "$work/four.log")"
elif assert_shape "four hosts" "$work/four.conf" 4 "${four_hosts[@]}"; then
    if grep -q 'upstream keepalive pool ON: 4 edge group(s)' "$work/four.log"; then
        ok "four hosts (the ceiling): 4 blocks in list order, 4x max_fails=0 / keepalive 8 / 50s, one sized zone, no resolver"
    else
        bad "four hosts" "no 'pool ON: 4 edge group(s)' log line"
    fi
fi

# --- 6. refusals -------------------------------------------------------------
expect_refused "five hosts" "$four cache4-fra2.steamcontent.com" "is edge number 5, above the ceiling"
# The message wraps, so the ceiling value, the ADR and the per-group number
# are checked on the whole log rather than on the first line.
if tr '\n' ' ' < "$work/five hosts.log" | grep -q 'ceiling of *4 edges (keepalive 8 idle connections per group, at most *32 idle in total -- ADR-0017 decision 3A'; then
    ok "five hosts: the refusal names the ceiling (4 edges, 8 per group, 32 idle) and ADR-0017 decision 3A"
else
    bad "five hosts" "the refusal does not name the ceiling values and ADR-0017: $(tr '\n' ' ' < "$work/five hosts.log")"
fi
expect_refused "uppercase"      "Cache1-fra2.steamcontent.com"                      "contains uppercase"
expect_refused "bad suffix"     "cache1.example.com"                                "outside the Host allowlist families"
expect_refused "suffix as prefix" "steamcontent.com.evil.example"                   "outside the Host allowlist families"
expect_refused "bare family"    "steamcontent.com"                                  "is a bare family name"
expect_refused "bare family 2"  "steamserver.net"                                   "is a bare family name"
expect_refused "port"           "cache1-fra2.steamcontent.com:80"                   "carries a scheme or port"
expect_refused "duplicate"      "cache1-fra2.steamcontent.com cache1-fra2.steamcontent.com" "is listed twice"
expect_refused "marker"         "lancache.steamcontent.com"                         "discovery marker"
expect_refused "trailing dot"   "cache1-fra2.steamcontent.com."                     "has an empty label"
expect_refused "double dot"     "cache1..steamcontent.com"                          "has an empty label"
expect_refused "leading dot"    ".steamcontent.com"                                 "has an empty label"
expect_refused "charset"        "cache_1.steamcontent.com"                          "is not a host name"
expect_refused "scheme"         "http://cache1.steamcontent.com"                    "carries a scheme or port"
expect_refused "comma list"     "a.steamcontent.com,b.steamcontent.com"             "is not a host name"
# A CR (Windows-edited .env) fails the charset rule and the message must
# point at invisible characters.
expect_refused "CR in token"    "$(printf 'cache1.steamcontent.com\r')"             "invisible characters"
# RFC 1035 lengths (review finding: nginx accepts an over-long name and then
# logs 'could not be resolved' every 5 s for the container's lifetime).
l63="$(printf 'a%.0s' $(seq 63))"
l64="${l63}a"
expect_refused "64-char label"  "${l64}.steamcontent.com"                           "a DNS label may have at most 63"
expect_refused "64-char inner label" "cache1.${l64}.steamcontent.com"               "a DNS label may have at most 63"
# 63.63.63.63 + .steamcontent.com = 272 characters, every label legal.
expect_refused "272-char name"  "${l63}.${l63}.${l63}.${l63}.steamcontent.com"      "is 272 characters long; a host name may have"
# Boundaries that must pass: a 63-char label, and a 253-char name
# (63 + 63 + 63 + 44 + 3 dots + ".steamcontent.com" (17) = 253).
render label63 "${l63}.steamcontent.com" "$work/label63.conf"
if [ "$rc" -ne 0 ]; then
    bad "63-char label" "refused: $(head -n1 "$work/label63.log")"
elif assert_shape "63-char label" "$work/label63.conf" 1 "${l63}.steamcontent.com"; then
    ok "63-char label: accepted"
fi
name253="${l63}.${l63}.${l63}.$(printf 'b%.0s' $(seq 44)).steamcontent.com"
if [ "${#name253}" -ne 253 ]; then
    bad "253-char name" "test bug: built a ${#name253}-char name"
else
    render name253 "$name253" "$work/name253.conf"
    if [ "$rc" -ne 0 ]; then
        bad "253-char name" "refused: $(head -n1 "$work/name253.log")"
    elif assert_shape "253-char name" "$work/name253.conf" 1 "$name253"; then
        ok "253-char name: accepted (the boundary)"
    fi
fi
# The second host of an otherwise valid list is the bad one: the hook must
# still refuse (every token is validated, not just the first).
expect_refused "bad second host" "cache1-fra2.steamcontent.com cache2.example.org" "outside the Host allowlist families"

# --- 7. a glob token must not expand against the working directory ----------
# `set -f` in the hook: with a file named x.steamcontent.com in the cwd, an
# unquoted `*.steamcontent.com` would otherwise expand to that file name and
# pass validation.
mkdir -p "$work/globdir" && : > "$work/globdir/x.steamcontent.com"
rc=0
(cd "$work/globdir" && VAULT_UPSTREAM_POOL_HOSTS='*.steamcontent.com' sh "$HOOK" "$work/glob.conf") > "$work/glob.log" 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then
    bad "glob token" "'*.steamcontent.com' was accepted (globbing expanded it?)"
elif ! grep -qF "'*.steamcontent.com' is not a host name" "$work/glob.log"; then
    bad "glob token" "refused, but not as a literal '*': $(head -n1 "$work/glob.log")"
else
    ok "glob token: '*.steamcontent.com' refused as a literal (no globbing)"
fi

# --- 8. whitespace: runs collapse, no empty token can arise -----------------
ws="$(printf '  cache1-fra2.steamcontent.com \t\t dist-fra1.discovery.steamserver.net\n  ')"
render whitespace "$ws" "$work/ws.conf"
if [ "$rc" -ne 0 ]; then
    bad "whitespace" "hook failed on leading/trailing/double whitespace: $(head -n1 "$work/whitespace.log")"
elif ! cmp -s "$work/ws.conf" "$work/two.golden"; then
    bad "whitespace" "render differs from the two-host golden (tokens must split on whitespace runs)"
else
    ok "whitespace: leading/trailing/double spaces, tabs and newlines tolerated -- same render as the two-host list (no empty tokens)"
fi

# --- 9. the output-path argument is honoured --------------------------------
mkdir -p "$work/elsewhere"
render outpath "cache1-fra2.steamcontent.com" "$work/elsewhere/custom-name.conf"
if [ "$rc" -ne 0 ]; then
    bad "output path" "hook failed: $(head -n1 "$work/outpath.log")"
elif [ ! -f "$work/elsewhere/custom-name.conf" ]; then
    bad "output path" "nothing written at the given path"
elif ! grep -qF "rendered $work/elsewhere/custom-name.conf" "$work/outpath.log"; then
    bad "output path" "the log line does not name the given path"
elif ls "$work/elsewhere/"*.tmp.* >/dev/null 2>&1; then
    bad "output path" "a temp file was left next to the output"
else
    ok "output path: \$1 honoured, no temp file left behind"
fi

# --- 10. a render that cannot be written fails closed (no partial file) ------
rc=0
VAULT_UPSTREAM_POOL_HOSTS="cache1-fra2.steamcontent.com" sh "$HOOK" "$work/no-such-dir/pool.conf" > "$work/nodir.log" 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then
    bad "unwritable path" "exit 0 although the output directory does not exist"
elif [ -e "$work/no-such-dir" ]; then
    bad "unwritable path" "the hook created the directory"
else
    ok "unwritable path: non-zero exit, nothing created"
fi

echo
echo "test-upstream-pool-hook: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
