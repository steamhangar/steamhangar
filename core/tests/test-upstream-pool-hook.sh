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
# CORE-FIX-4a (ADR-0021) adds the VAULT_UPSTREAM_EDGE single-pool mode and the
# VAULT_UPSTREAM_MAX_CONNS cap; the legacy (edge empty) cases below keep the
# ADR-0017 contract, the edge/cap cases are in sections 11-14.
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

# The image's ENV defaults (edge ON) must not leak into the legacy cases.
unset VAULT_UPSTREAM_EDGE VAULT_UPSTREAM_MAX_CONNS VAULT_PREFILL_MAX_THREADS VAULT_UPSTREAM_POOL_HOSTS

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
    [ "$(count '^}$' "$f")" = "$((n + 1))" ] || problems+=("closing braces != $((n + 1)) (n groups + the target map)")
    [ "$(count '^map \$vault_upstream_host \$vault_upstream_target {$' "$f")" = "1" ] || problems+=("target map != 1")
    [ "$(count '^    default \$vault_upstream_host;$' "$f")" = "1" ] || problems+=("legacy map default is not \$vault_upstream_host")
    [ "$(count '^    server [a-z0-9.-]* resolve max_fails=0;$' "$f")" = "$n" ] || problems+=("server ... resolve max_fails=0 lines != $n")
    [ "$(count '^    keepalive 8;$' "$f")" = "$n" ] || problems+=("'keepalive 8;' lines != $n")
    [ "$(count '^    keepalive_timeout 50s;$' "$f")" = "$n" ] || problems+=("'keepalive_timeout 50s;' lines != $n")
    [ "$(count '^    zone vault_edges 256k;$' "$f")" = "1" ] || problems+=("sized zone line != 1")
    [ "$(count '^    zone vault_edges;$' "$f")" = "$((n - 1))" ] || problems+=("size-less zone lines != $((n - 1))")
    grep -qw 'resolver' "$f" && problems+=("a 'resolver' token is present")
    grep -q 'Pooled edges: '"$n"' ' "$f" || problems+=("header does not say 'Pooled edges: $n'")
    if grep -v '^#' "$f" | grep -qvE '^(upstream [a-z0-9.-]+ \{|map \$vault_upstream_host \$vault_upstream_target \{|    (zone|server|keepalive|keepalive_timeout|default) [^;]+;|\})$'; then
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
elif [ "$(grep -v '^#' "$work/unset.conf")" != 'map $vault_upstream_host $vault_upstream_target {
    default $vault_upstream_host;
}' ]; then
    bad "unset var" "the empty render holds more than the legacy target map"
elif ! grep -q 'Pooled edges: 0 ' "$work/unset.conf"; then
    bad "unset var" "header does not say 'Pooled edges: 0'"
else
    ok "unset var: header + legacy target map only, byte-identical to the static native file"
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
# SteamHangar vault-core -- upstream keepalive pool include (ADR-0017, ADR-0021).
# Rendered at container start by /docker-entrypoint.d/28-vault-upstream-pool.sh from
# VAULT_UPSTREAM_EDGE and VAULT_UPSTREAM_POOL_HOSTS -- do not edit; see core/README.md
# "Upstream keepalive pool". Natively (core/nginx/vault-upstream-pool.conf) this file
# is the empty legacy render, byte for byte (check-config-drift.sh asserts it).
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
# $vault_upstream_target: the name @miss dials and sends as Host (ADR-0021).
map $vault_upstream_host $vault_upstream_target {
    default $vault_upstream_host;
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
    if grep -q 'upstream keepalive pool ON (legacy per-name mode): 4 edge group(s)' "$work/four.log"; then
        ok "four hosts (the ceiling): 4 blocks in list order, 4x max_fails=0 / keepalive 8 / 50s, one sized zone, no resolver"
    else
        bad "four hosts" "no 'pool ON (legacy per-name mode): 4 edge group(s)' log line"
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

# =============================================================================
# CORE-FIX-4a (ADR-0021): edge mode and the global cap
# =============================================================================
EDGE_DEFAULT=dist-fra1.discovery.steamserver.net

# render_env <case> <out> VAR=value... : run the hook with exactly these
# variables (everything else unset), exit code in $rc, log in $work/<case>.log
render_env() {
    local name="$1" out="$2"; shift 2
    rc=0
    env -i "$@" sh "$HOOK" "$out" > "$work/$name.log" 2>&1 || rc=$?
}
# refused_env <case> <fragment> VAR=value... : non-zero, FATAL naming the
# fragment, neither output file written.
refused_env() {
    local name="$1" fragment="$2"; shift 2
    local out="$work/$name.conf"
    rm -f "$work/vault-upstream-cap.conf"
    render_env "$name" "$out" "$@"
    if [ "$rc" -eq 0 ]; then bad "$name" "accepted $* (exit 0)"; return; fi
    if ! grep -q '^28-vault-upstream-pool.sh: FATAL: ' "$work/$name.log"; then
        bad "$name" "no FATAL line: $(head -n1 "$work/$name.log")"; return
    fi
    if ! tr '\n' ' ' < "$work/$name.log" | grep -qF -- "$fragment"; then
        bad "$name" "FATAL does not mention '$fragment': $(tr '\n' ' ' < "$work/$name.log")"; return
    fi
    if [ -e "$out" ] || [ -e "$work/vault-upstream-cap.conf" ]; then
        bad "$name" "refused but wrote an include"; return
    fi
    ok "$name: refused"
}

# --- 11. edge mode: one group, map default = the edge, cap include default ---
rm -f "$work/vault-upstream-cap.conf"
render_env edge "$work/edge.conf" VAULT_UPSTREAM_EDGE=$EDGE_DEFAULT
cat > "$work/edge.golden" <<EOF
# SteamHangar vault-core -- upstream keepalive pool include (ADR-0017, ADR-0021).
# Rendered at container start by /docker-entrypoint.d/28-vault-upstream-pool.sh from
# VAULT_UPSTREAM_EDGE and VAULT_UPSTREAM_POOL_HOSTS -- do not edit; see core/README.md
# "Upstream keepalive pool". Natively (core/nginx/vault-upstream-pool.conf) this file
# is the empty legacy render, byte for byte (check-config-drift.sh asserts it).
# Edge mode (ADR-0021): every allowed MISS goes to $EDGE_DEFAULT, pooled with
# keepalive 16 (= the connection cap). The Host header the client sent only
# has to pass the allowlist; it never selects the upstream.
# The group's zone is required by 'server ... resolve'. No DNS directive inside
# the group: it inherits the http-level setting, ipv6=off valid=30s included.
upstream $EDGE_DEFAULT {
    zone vault_edges 256k;
    server $EDGE_DEFAULT resolve max_fails=0;
    keepalive 16;
    keepalive_timeout 50s;
}
# \$vault_upstream_target: the name @miss dials and sends as Host (ADR-0021).
map \$vault_upstream_host \$vault_upstream_target {
    default $EDGE_DEFAULT;
}
EOF
if [ "$rc" -ne 0 ]; then
    bad "edge mode" "hook failed: $(head -n1 "$work/edge.log")"
elif ! cmp -s "$work/edge.conf" "$work/edge.golden"; then
    bad "edge mode" "render differs from the golden:"; diff -u "$work/edge.golden" "$work/edge.conf" >&2 || true
elif ! cmp -s "$work/vault-upstream-cap.conf" "$core_dir/nginx/vault-upstream-cap.conf"; then
    bad "edge mode" "default cap include differs from core/nginx/vault-upstream-cap.conf"
elif ! grep -q "upstream edge mode ON: every MISS goes to $EDGE_DEFAULT .*connection cap 16" "$work/edge.log"; then
    bad "edge mode" "log line does not name the edge and the cap: $(head -n1 "$work/edge.log")"
else
    ok "edge mode: golden render (one group named like the edge, keepalive = cap), default cap include = native file, log names edge and cap"
fi

# --- 12. the pool list is ignored in edge mode -------------------------------
render_env edge_ignores "$work/edge2.conf" VAULT_UPSTREAM_EDGE=$EDGE_DEFAULT VAULT_UPSTREAM_POOL_HOSTS="cache1.example.com"
if [ "$rc" -ne 0 ] || ! cmp -s "$work/edge2.conf" "$work/edge.conf"; then
    bad "edge ignores pool list" "render differs or hook failed: $(head -n1 "$work/edge_ignores.log")"
else
    ok "edge mode: VAULT_UPSTREAM_POOL_HOSTS is ignored (even an invalid one), same render"
fi

# --- 13. edge refusals -------------------------------------------------------
refused_env "edge outside families" "outside the Host allowlist families" VAULT_UPSTREAM_EDGE=cache1.example.com
refused_env "edge marker"           "discovery marker"                    VAULT_UPSTREAM_EDGE=lancache.steamcontent.com
refused_env "edge bare family"      "is a bare family name"               VAULT_UPSTREAM_EDGE=steamserver.net
refused_env "edge with port"        "carries a scheme or port"            VAULT_UPSTREAM_EDGE=dist-fra1.discovery.steamserver.net:80
refused_env "edge uppercase"        "contains uppercase"                  VAULT_UPSTREAM_EDGE=Dist-fra1.discovery.steamserver.net
refused_env "two edges"             "takes exactly one"                   "VAULT_UPSTREAM_EDGE=a.steamcontent.com b.steamcontent.com"

# --- 14. the cap C ----------------------------------------------------------
for c in 8 64; do
    rm -f "$work/vault-upstream-cap.conf"
    render_env "cap $c" "$work/cap$c.conf" VAULT_UPSTREAM_EDGE=$EDGE_DEFAULT VAULT_UPSTREAM_MAX_CONNS=$c
    if [ "$rc" -ne 0 ]; then
        bad "cap $c" "refused: $(head -n1 "$work/cap $c.log")"
    elif ! grep -qx "limit_conn vault_upstream_total $c;" "$work/vault-upstream-cap.conf" \
         || ! grep -qx "    keepalive $c;" "$work/cap$c.conf"; then
        bad "cap $c" "cap include or edge keepalive do not carry $c"
    else
        ok "cap $c: limit_conn vault_upstream_total $c; and keepalive $c;"
    fi
done
# prefill floor: empty counts as 8 (compose forwards the :-8 default)
refused_env "cap 7 vs empty prefill" "below the prefill thread count 8" VAULT_UPSTREAM_MAX_CONNS=7 VAULT_PREFILL_MAX_THREADS=
refused_env "cap 7 vs unset prefill" "below the prefill thread count 8" VAULT_UPSTREAM_MAX_CONNS=7
refused_env "cap 1 vs prefill 2"     "below the prefill thread count 2" VAULT_UPSTREAM_MAX_CONNS=1 VAULT_PREFILL_MAX_THREADS=2
rm -f "$work/vault-upstream-cap.conf"
render_env "cap 4 prefill 4" "$work/cap4.conf" VAULT_UPSTREAM_EDGE=$EDGE_DEFAULT VAULT_UPSTREAM_MAX_CONNS=4 VAULT_PREFILL_MAX_THREADS=4
if [ "$rc" -eq 0 ] && grep -qx 'limit_conn vault_upstream_total 4;' "$work/vault-upstream-cap.conf"; then
    ok "cap 4 with prefill 4: accepted (cap >= prefill threads)"
else
    bad "cap 4 prefill 4" "refused or wrong render: $(head -n1 "$work/cap 4 prefill 4.log")"
fi
refused_env "cap 0"        "0 and leading zeros are not accepted" VAULT_UPSTREAM_MAX_CONNS=0
refused_env "cap 65"       "outside 1..64"                         VAULT_UPSTREAM_MAX_CONNS=65
refused_env "cap 100"      "outside 1..64"                         VAULT_UPSTREAM_MAX_CONNS=100
refused_env "cap abc"      "not a whole number"                    VAULT_UPSTREAM_MAX_CONNS=abc
refused_env "cap negative" "not a whole number"                    VAULT_UPSTREAM_MAX_CONNS=-1
refused_env "cap leading zero" "leading zeros"                     VAULT_UPSTREAM_MAX_CONNS=016
refused_env "cap with space" "not a whole number"                  "VAULT_UPSTREAM_MAX_CONNS=1 6"

echo
echo "test-upstream-pool-hook: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
