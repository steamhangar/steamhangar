#!/usr/bin/env bash
# vault-core CI gate (WP 5.1): `nginx -t` against the RENDERED container
# config, inside the exact pinned upstream nginx image -- never against
# core/nginx/nginx.conf directly, since that native-dev file is not what the
# container actually runs (see core/README.md "The Docker image").
#
# Explicitly OUT of scope here (per the WP 5.1 brief): no `docker build` of
# any SteamHangar image (that would start crossing into image-publishing
# territory, WP 5.5's job, and it is unnecessary anyway -- rendering the
# template needs only the stock upstream image plus core/docker/*.sh, none
# of which requires a build step). This script only ever `docker pull`s a
# public, by-digest-pinned base image and `docker run`s it.
#
# --- Review round 2 (S1): the REAL render path, not a hand-rolled one ------
# Round 1 of this script called `envsubst` itself instead of the base
# image's actual /docker-entrypoint.d/20-envsubst-on-templates.sh hook, to
# avoid depending on that hook's exact file name (an upstream implementation
# detail). The reviewer correctly pushed back: that also means round 1 never
# exercised the REAL render wiring (NGINX_ENVSUBST_FILTER and friends) at
# all -- it only ever tested this script's own idea of what that wiring
# does. This version mounts the template + our two owned hook scripts into
# their REAL container paths, sets the exact ENV vars core/Dockerfile sets,
# and lets the image's OWN stock /docker-entrypoint.sh run every hook in
# /docker-entrypoint.d/ (the two stock ones this repo doesn't own, plus our
# 25- and 40-) in the same sorted order the real container uses, before
# `nginx -t` runs. The only deviation from "just run the image normally" is
# mechanical: --entrypoint is overridden to `sh` for one setup step (copying
# the two hook scripts into a real, non-bind-mounted, chmod-able location --
# a read-only bind mount cannot be chmod'd, and the stock entrypoint only
# executes hooks it can see are +x), and to let this script capture `nginx
# -t`'s exit code and still run the invariant assertions below afterward
# (calling /docker-entrypoint.sh as a plain foreground command, not via our
# own `exec`, returns control here once it's done). Functionally identical
# to `docker run ... "$IMAGE" nginx -t -p /vault -c /etc/nginx/nginx.conf`
# with no --entrypoint override at all, which is what actually ships.
#
# Runs on ubuntu-latest, which ships Docker preinstalled
# (actions/runner-images). No local Docker is required to develop this
# script -- it was written and read-reviewed without Docker (none available
# on the dev machine, a standing constraint noted in docs/LEARNINGS.md /
# memory) and is therefore CI-only verified beyond the drift check below;
# see the WP 5.1 coder report for what *was* verified locally.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
core_dir="$repo_root/core"
dockerfile="$core_dir/Dockerfile"

for f in "$dockerfile" \
         "$core_dir/docker/nginx.conf.template" \
         "$core_dir/docker/25-vault-eventlog.sh" \
         "$core_dir/docker/27-vault-upstream-rate.sh" \
         "$core_dir/nginx/vault-upstream-rate.conf" \
         "$core_dir/docker/28-vault-upstream-pool.sh" \
         "$core_dir/nginx/vault-upstream-pool.conf" \
         "$core_dir/tests/test-upstream-pool-hook.sh" \
         "$core_dir/docker/40-vault-preflight.sh" \
         "$core_dir/docker/check-config-drift.sh"; do
    [ -f "$f" ] || { echo "missing expected file: $f" >&2; exit 1; }
done

# --- 0. drift check first (S3): pure POSIX, no Docker, ~1s -----------------
# Fails fast and cheaply if the container template has silently diverged
# from the reviewed, real-CDN-tested native config (core/README.md "The
# Docker image") -- no point spending a docker pull/run cycle validating a
# rendered config that already failed this much narrower, much faster check.
echo "--- core/docker/check-config-drift.sh ---"
sh "$core_dir/docker/check-config-drift.sh"

# --- 0b. the upstream keepalive pool hook, docker-free (WP CORE-FEAT-1b) ---
# ADR-0017: renders VAULT_UPSTREAM_POOL_HOSTS lists through the real
# 28-vault-upstream-pool.sh (under `sh`, as the container runs it) into a
# temp dir and asserts the group shape and every refusal rule. Still no
# Docker; `nginx -t` on a rendered pool include in the pinned image is WP
# CORE-FEAT-1b2's job in the docker-based steps below.
echo "--- core/tests/test-upstream-pool-hook.sh ---"
bash "$core_dir/tests/test-upstream-pool-hook.sh"

# --- S2: derive the pinned image ref from core/Dockerfile itself -----------
# Round 1 duplicated the tag+digest as a literal in this script -- a bump to
# core/Dockerfile's FROM line would silently leave this check validating a
# stale image. Read it from the one place that is actually allowed to change
# it.
IMAGE=$(sed -n 's/^FROM[[:space:]]\{1,\}//p' "$dockerfile" | head -n1)
case "$IMAGE" in
    *@sha256:*) : ;;
    *)
        echo "core/Dockerfile's FROM line does not pin a digest (@sha256:...): '$IMAGE'" >&2
        echo "Refusing to test against an unpinned/mutable image reference." >&2
        exit 1
        ;;
esac
echo "Pinned base image (from core/Dockerfile): $IMAGE"

# --- S-B: the NGINX_ENVSUBST_* values passed to `docker run -e` below are
# hand-duplicated from core/Dockerfile's ENV block (there is no equivalent
# of S2's "read the FROM line" trick for values spread across several ENV
# continuation lines). Assert they still match before relying on them, so a
# future edit to the Dockerfile's ENV block can't silently leave this script
# validating wiring the real image no longer uses.
for expected in \
    'NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates' \
    'NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx' \
    'NGINX_ENVSUBST_FILTER=^VAULT_'; do
    grep -qF -- "$expected" "$dockerfile" || {
        echo "core/Dockerfile no longer sets '$expected' -- this script's" >&2
        echo "hand-duplicated docker run -e flags are stale. Update both." >&2
        exit 1
    }
done

echo "docker pull $IMAGE"
docker pull "$IMAGE"

# render_and_test <label> <VAULT_EVENT_LOG value> <expected access_log/vault_event directive count> [probe-guards] [VAULT_UPSTREAM_RATE] [VAULT_UPSTREAM_RATE_WINDOW] [rate mode: off|cap|window]
#
# A non-empty 4th argument additionally STARTS the rendered nginx and probes
# the location /depot/ request guards over loopback (once is enough; the
# guards do not depend on the event-log state).
#
# Renders and validates once per VAULT_EVENT_LOG state -- the ADR-0008
# feature has two materially different code paths in
# core/docker/25-vault-eventlog.sh (strip the two access_log lines entirely
# vs. keep-and-validate them), and core/Dockerfile's own default is the OFF
# state, so both need their own `nginx -t` pass rather than trusting one to
# imply the other.
#
# Args 5-7 (WP TH-1a): the upstream rate cap env and which render shape the
# include must have -- "off" (no cap: one map, default 0), "cap" (800k
# around the clock: the 1024-bucket share map IS $vault_upstream_rate) or
# "window" (800k outside 22:30-06:15, a window that wraps midnight and has
# minute boundaries on both edges). The bucket/window assertions below are
# written for exactly those two inputs.
render_and_test() {
    local label="$1" event_log="$2" expected_directives="$3"
    local rate="${5:-}" window="${6:-}" rate_mode="${7:-off}"
    # SEC-FIX-1: the guard-probe run gets no network at all. Its Host-allowlist
    # probes count any resolver or upstream attempt as a failure, and without
    # a network such an attempt can only show up locally (error log, access
    # log), never as a real DNS query or connection from the CI runner.
    local -a net_args=()
    [ -n "${4:-}" ] && net_args=(--network none)
    echo "--- nginx -t: $label (VAULT_EVENT_LOG='$event_log', VAULT_UPSTREAM_RATE='$rate', VAULT_UPSTREAM_RATE_WINDOW='$window') ---"
    docker run --rm "${net_args[@]}" \
        -v "$core_dir/docker:/workspace/core-docker:ro" \
        -e NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates \
        -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
        -e NGINX_ENVSUBST_FILTER='^VAULT_' \
        -e VAULT_RESOLVER="1.1.1.1" \
        -e VAULT_EVENT_LOG="$event_log" \
        -e EXPECTED_DIRECTIVES="$expected_directives" \
        -e PROBE_GUARDS="${4:-}" \
        -e VAULT_UPSTREAM_RATE="$rate" \
        -e VAULT_UPSTREAM_RATE_WINDOW="$window" \
        -e RATE_MODE="$rate_mode" \
        --entrypoint sh \
        "$IMAGE" -c '
            set -eu

            # Same layout core/Dockerfile creates for the real /vault volume.
            mkdir -p /etc/nginx/templates /vault/cache/depot /vault/tmp
            chown -R nginx:nginx /vault

            # Place the template + our two owned hooks at their REAL
            # container paths. Copied (not bind-mounted) specifically so
            # they land as regular files in this container'"'"'s own
            # writable layer -- a read-only bind mount cannot be chmod'"'"'d,
            # and the two hooks need +x for the stock entrypoint to run
            # them at all (matching core/Dockerfile'"'"'s own
            # `chmod 0755 /docker-entrypoint.d/25-... /docker-entrypoint.d/40-...`
            # RUN step, reproduced here instead of via a build).
            cp /workspace/core-docker/nginx.conf.template /etc/nginx/templates/nginx.conf.template
            cp /workspace/core-docker/25-vault-eventlog.sh /docker-entrypoint.d/25-vault-eventlog.sh
            cp /workspace/core-docker/27-vault-upstream-rate.sh /docker-entrypoint.d/27-vault-upstream-rate.sh
            cp /workspace/core-docker/28-vault-upstream-pool.sh /docker-entrypoint.d/28-vault-upstream-pool.sh
            cp /workspace/core-docker/40-vault-preflight.sh /docker-entrypoint.d/40-vault-preflight.sh
            chmod 0755 /docker-entrypoint.d/25-vault-eventlog.sh /docker-entrypoint.d/27-vault-upstream-rate.sh /docker-entrypoint.d/28-vault-upstream-pool.sh /docker-entrypoint.d/40-vault-preflight.sh

            # The REAL stock entrypoint: runs every /docker-entrypoint.d/*.sh
            # hook in sorted order (stock 10-/15-/20-envsubst, our 25-, stock
            # 30-, our 40-), using the real NGINX_ENVSUBST_FILTER mechanism,
            # then execs its argument list ("nginx -t ..."). Called as a
            # plain foreground command (no `exec` here) so control returns
            # to THIS shell afterward for the invariant assertions below --
            # everything up to and including nginx -t is otherwise exactly
            # what a real `docker run ... "$IMAGE" nginx -t -p /vault -c
            # /etc/nginx/nginx.conf` (no entrypoint override) would do.
            nginx_t_status=0
            /docker-entrypoint.sh nginx -t -p /vault -c /etc/nginx/nginx.conf || nginx_t_status=$?

            conf=/etc/nginx/nginx.conf
            [ -f "$conf" ] || { echo "FATAL: $conf was never rendered"; exit 1; }

            # --- S-A (WP 5.1 review round 3, latent false green) ----------
            # The stock 20-envsubst-on-templates.sh hook SOFT-FAILS: if the
            # template dir is missing, the output dir is unwritable, or a
            # future base image drops the hook entirely, it (or the
            # entrypoint around it) can return 0 without ever rendering our
            # template -- and the stock image already ships its OWN
            # /etc/nginx/nginx.conf at that exact path. In the OFF scenario
            # that stock config would ALSO show directive_count=0, matching
            # EXPECTED_DIRECTIVES and passing this check for entirely the
            # wrong reason. Guard against validating the wrong file: only
            # the SteamHangar template declares the vault_event log format,
            # so its absence means rendering never happened.
            grep -q "log_format vault_event" "$conf" || {
                echo "FATAL: $conf is not the SteamHangar config -- the template was never rendered"
                exit 1
            }

            # --- B1 pinned invariant (WP 5.1 review, blocker) -------------
            # Independent of nginx -t (a syntax check -- it has no opinion
            # on WHICH access_log directives are present): assert the
            # cache-event-log directive count matches this scenario exactly,
            # and that no half-rendered marker survives either way.
            directive_count=$(grep -cE "^[[:space:]]*access_log[[:space:]].*vault_event" "$conf" || true)
            marker_count=$(grep -c "# VAULT_EVENT_LOG_LINE" "$conf" || true)
            echo "directive_count=$directive_count (expected $EXPECTED_DIRECTIVES), marker_count=$marker_count (expected 0)"

            status=0
            if [ "$nginx_t_status" != "0" ]; then
                # N-a: this is the exit status of the WHOLE entrypoint
                # chain, not necessarily nginx -t itself -- a hook (e.g. our
                # own 40-vault-preflight.sh) aborting before nginx -t ever
                # runs lands here too, and "nginx -t exited N" would misname
                # it.
                echo "FAIL: entrypoint/nginx -t exited $nginx_t_status"
                status=1
            fi
            if [ "$directive_count" != "$EXPECTED_DIRECTIVES" ]; then
                echo "FAIL: expected $EXPECTED_DIRECTIVES vault_event access_log directive(s), found $directive_count"
                status=1
            fi
            if [ "${marker_count:-0}" != "0" ]; then
                echo "FAIL: $marker_count VAULT_EVENT_LOG_LINE marker(s) survived -- half-rendered config"
                status=1
            fi

            # --- Pre-freeze review S3/P6: event-log file ownership ---------
            # nginx -t (like a real start) opens every access_log from the
            # root master process. 25-vault-eventlog.sh must have created
            # the file first and owned it to uid/gid 101 -- the numeric
            # identity vault-api runs as (api/Dockerfile), whose sweeper
            # truncates this file. A root:root file here is the regression.
            if [ -n "$VAULT_EVENT_LOG" ]; then
                nginx_ids="$(id -u nginx):$(id -g nginx)"
                if [ "$nginx_ids" != "101:101" ]; then
                    echo "FAIL: nginx user is $nginx_ids in this image, expected 101:101 (api/Dockerfile pins vault-api to 101)"
                    status=1
                fi
                owner=$(stat -c %u:%g "$VAULT_EVENT_LOG" 2>/dev/null || echo missing)
                echo "event log owner=$owner (expected 101:101)"
                if [ "$owner" != "101:101" ]; then
                    echo "FAIL: $VAULT_EVENT_LOG owner is $owner, expected 101:101 -- vault-api could not truncate it"
                    status=1
                fi
            fi

            # --- WP TH-1a: the upstream rate cap, structurally ----------
            # proxy_limit_rate only acts on buffered responses; pin the
            # wiring in the RENDERED config (not just the template), then
            # the include 27-vault-upstream-rate.sh rendered for this
            # scenario env.
            for want in "include vault-upstream-rate.conf;" "proxy_buffering on;" \
                        "proxy_ignore_headers X-Accel-Buffering;" "proxy_limit_rate \$vault_upstream_rate;"; do
                n=$(grep -v "^[[:space:]]*#" "$conf" | sed -e "s/^[[:space:]]*//" -e "s/[[:space:]][[:space:]]*/ /g" | grep -c -x -F "$want" || true)
                if [ "$n" != "1" ]; then
                    echo "FAIL: expected exactly 1 \"$want\" in the rendered $conf, found $n"
                    status=1
                fi
            done
            # ... and all three INSIDE location @miss: a named location
            # inherits nothing from /depot/, so the counts above would still
            # be 1 with the lines moved one block up and the cap gone.
            miss=$(grep -v "^[[:space:]]*#" "$conf" | sed -e "s/^[[:space:]]*//" -e "s/[[:space:]][[:space:]]*/ /g" \
                | awk "/^location @miss [{]\$/ { f = 1 } f { print; d += gsub(/[{]/, \"&\") - gsub(/[}]/, \"&\"); if (d <= 0) exit }")
            [ -n "$miss" ] || { echo "FAIL: no location @miss block in the rendered $conf"; status=1; }
            for want in "proxy_buffering on;" "proxy_ignore_headers X-Accel-Buffering;" "proxy_limit_rate \$vault_upstream_rate;"; do
                n=$(printf "%s\n" "$miss" | grep -c -x -F "$want" || true)
                if [ "$n" != "1" ]; then
                    echo "FAIL: \"$want\" is not inside location @miss in the rendered $conf (found $n there)"
                    status=1
                fi
            done
            if grep -q "^[[:space:]]*proxy_buffering[[:space:]][[:space:]]*off" "$conf"; then
                echo "FAIL: proxy_buffering off in $conf -- the upstream cap would not apply"
                status=1
            fi
            rc=/etc/nginx/vault-upstream-rate.conf
            rate_ok() {
                if grep -qx -F -- "$1" "$rc"; then :; else echo "FAIL ($RATE_MODE): \"$1\" missing from $rc"; status=1; fi
            }
            rate_absent() {
                if grep -q -F -- "$1" "$rc"; then echo "FAIL ($RATE_MODE): \"$1\" must not be in $rc"; status=1; fi
            }
            if [ ! -f "$rc" ]; then
                echo "FAIL: $rc was never rendered"
                status=1
            else
                buckets=$(grep -cE "^    [0-9]+ [1-9][0-9]*;$" "$rc" || true)
                patterns=$(grep -c "^    \"~" "$rc" || true)
                case "$RATE_MODE" in
                    off)
                        rate_ok "map \$time_iso8601 \$vault_upstream_rate {"
                        rate_ok "    default 0;"
                        rate_absent "connections_"
                        [ "$buckets" = "0" ] || { echo "FAIL (off): $buckets bucket lines rendered"; status=1; } ;;
                    cap)
                        rate_ok "map \$connections_writing \$vault_upstream_rate {"
                        rate_ok "    1 819200;"
                        rate_ok "    8 102400;"
                        rate_ok "    1024 800;"
                        rate_ok "    default 800;"
                        rate_absent "time_iso8601"
                        [ "$buckets" = "1024" ] || { echo "FAIL (cap): expected 1024 bucket lines, found $buckets"; status=1; } ;;
                    window)
                        rate_ok "map \$connections_writing \$vault_upstream_rate_share {"
                        rate_ok "    1 819200;"
                        rate_ok "    8 102400;"
                        rate_ok "    1024 800;"
                        rate_ok "    default 800;"
                        rate_ok "map \$time_iso8601 \$vault_upstream_in_window {"
                        rate_ok "    default 0;"
                        # 22:30-06:15: minute edge at 22:30 (inclusive) and
                        # 06:15 (exclusive), whole hours 23 and 00-05 between,
                        # across midnight.
                        P="^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                        rate_ok "    \"~${P}22:(?:3[0-9]|4[0-9]|5[0-9])\" 1;"
                        for hh in 23 00 01 02 03 04 05; do rate_ok "    \"~${P}${hh}:\" 1;"; done
                        rate_ok "    \"~${P}06:(?:0[0-9]|1[0-4])\" 1;"
                        [ "$patterns" = "9" ] || { echo "FAIL (window): expected 9 time patterns, found $patterns"; status=1; }
                        rate_ok "map \$vault_upstream_in_window \$vault_upstream_rate {"
                        rate_ok "    1 0;"
                        rate_ok "    default \$vault_upstream_rate_share;"
                        [ "$buckets" = "1024" ] || { echo "FAIL (window): expected 1024 bucket lines, found $buckets"; status=1; } ;;
                    *) echo "FAIL: unknown RATE_MODE $RATE_MODE"; status=1 ;;
                esac
                echo "upstream rate include ($RATE_MODE): $buckets bucket line(s), $patterns time pattern(s)"
            fi

            # --- Pre-freeze review S1/S2/P3/N5: request guards, LIVE -------
            # nginx -t proves the directives parse, not that they answer.
            # Start the rendered config for real and probe the guards in
            # location /depot/ over loopback. Every probe below is answered
            # locally by a `return` in the rewrite phase, so none of them
            # needs DNS or the Steam CDN -- this runs offline.
            if [ -n "${PROBE_GUARDS:-}" ] && [ "$nginx_t_status" = "0" ]; then
                command -v curl >/dev/null 2>&1 || { echo "FAIL: curl missing in the base image, cannot probe guards"; exit 1; }
                # The config logs to /dev/stdout and /dev/stderr. nginx opens
                # those paths at start, so redirecting them here sends the
                # access log and error log to two files the SEC-FIX-1 Host
                # probes below can read back.
                : > /tmp/probe-access.log
                : > /tmp/probe-error.log
                nginx -p /vault -c "$conf" >> /tmp/probe-access.log 2>> /tmp/probe-error.log
                obj=http://127.0.0.1/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2
                probe() {
                    want=$1; what=$2; shift 2
                    got=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "$@" || echo curl-error)
                    if [ "$got" = "$want" ]; then
                        echo "guard OK: $what -> $got"
                    else
                        echo "FAIL: $what -> $got, expected $want"
                        status=1
                    fi
                }
                probe 508 "S1 hop header (self-proxy loop)" -H "X-SteamHangar-Hop: 1" "$obj"
                probe 405 "P3 POST"                          -X POST "$obj"
                probe 405 "P3 HEAD"                          -I "$obj"
                probe 404 "S2 trailing slash (cached-depot oracle)" http://127.0.0.1/depot/70403/chunk/
                probe 404 "S2 trailing slash, depot root"   http://127.0.0.1/depot/70403/
                probe 200 "/health still answers"           http://127.0.0.1/health
                server_hdr=$(curl -s -m 5 -o /dev/null -D - http://127.0.0.1/health | tr -d "\r" | sed -n "s/^[Ss]erver:[[:space:]]*//p")
                if [ "$server_hdr" = "nginx" ]; then
                    echo "guard OK: N5 Server header carries no version ($server_hdr)"
                else
                    echo "FAIL: N5 Server header is '"'"'$server_hdr'"'"', expected bare '"'"'nginx'"'"'"
                    status=1
                fi

                # --- SEC-FIX-1 (WP 5.3 review U-1): Host allowlist, LIVE -----
                # Threat: a Host such as "127.0.0.1?x.steamcontent.com" passes
                # a suffix-only allowlist, and proxy_pass splits the URL at
                # "?" and dials 127.0.0.1. Each crafted Host below must be
                # refused locally, and the status code alone does not prove
                # that: a request relayed to 127.0.0.1 also comes back 403,
                # because there is no index for "/". So every probe also needs
                # exactly ONE new access-log line (a request relayed to this
                # server would add a second one), upstream_status "-" on it,
                # and no new error-log line. This container has no network, so
                # a resolver or connect attempt always logs an error. One
                # error-log line is expected and ignored: the "uninitialized
                # vault_cache_status" warning nginx writes when it rejects a
                # request before the server-level `set` runs.
                #
                # "400 403" = either refusal is fine. Measured on 1.29.8
                # (SEC-FIX-1): nginx rejects a Host containing "?", "@", "#",
                # a backslash, a space or a slash with 400 before any map is
                # evaluated; "%" and "_" get through to the allowlist. The
                # allowlist itself is pinned against every one of those
                # characters by the raw map probe further down.
                host_probe() {
                    want=$1; what=$2; shift 2
                    a0=$(wc -l < /tmp/probe-access.log); e0=$(wc -l < /tmp/probe-error.log)
                    got=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$@" || echo curl-error)
                    i=0
                    while [ "$(wc -l < /tmp/probe-access.log)" -le "$a0" ] && [ $i -lt 30 ]; do sleep 0.1; i=$((i + 1)); done
                    sleep 0.3
                    new=$(tail -n +$((a0 + 1)) /tmp/probe-access.log)
                    nnew=$(printf "%s\n" "$new" | grep -c . || true)
                    errs=$(tail -n +$((e0 + 1)) /tmp/probe-error.log | grep -v -F "using uninitialized \"vault_cache_status\" variable while logging request" || true)
                    why=""
                    case " $want " in *" $got "*) : ;; *) why="$why status $got, expected one of: $want;" ;; esac
                    [ "$nnew" = "1" ] || why="$why $nnew access-log lines, expected 1;"
                    case "$new" in *" status=$got "*" upstream_status=- "*) : ;; *) why="$why access line is not status=$got with upstream_status=-;" ;; esac
                    [ -z "$errs" ] || why="$why error log grew;"
                    if [ -z "$why" ]; then
                        echo "host guard OK: $what -> $got, no upstream attempt"
                    else
                        echo "FAIL: host guard: $what ->$why"
                        printf "%s\n" "$new" | sed "s/^/    access: /"
                        [ -z "$errs" ] || printf "%s\n" "$errs" | sed "s/^/    error:  /"
                        status=1
                    fi
                }
                obj0=http://127.0.0.1/depot/1/chunk/0000000000000000000000000000000000000000
                host_probe 403       "control: off-list Host"               -H "Host: evil.example.com" "$obj0"
                host_probe 403       "control: Steam name as a prefix"      -H "Host: x.steamcontent.com.evil.example.com" "$obj0"
                host_probe 403       "control: bare apex"                   -H "Host: steamcontent.com" "$obj0"
                host_probe "400 403" "? (query) delimiter"                  -H "Host: 127.0.0.1?x.steamcontent.com" "$obj0"
                host_probe "400 403" "? delimiter, steamserver.net"         -H "Host: 127.0.0.1?x.steamserver.net" "$obj0"
                host_probe "400 403" "@ (userinfo) delimiter"               -H "Host: evil.example.com@x.steamcontent.com" "$obj0"
                host_probe "400 403" "# (fragment) delimiter"               -H "Host: 127.0.0.1#x.steamcontent.com" "$obj0"
                host_probe 403       "% (percent-encoded ?)"                -H "Host: 127.0.0.1%3fx.steamcontent.com" "$obj0"
                host_probe 403       "_ (not a hostname character)"         -H "Host: x_y.steamcontent.com" "$obj0"
                host_probe "400 403" ": port, then ?"                       -H "Host: 127.0.0.1:80?x.steamcontent.com" "$obj0"
                host_probe "400 403" "backslash"                            -H "Host: 127.0.0.1\\x.steamcontent.com" "$obj0"
                host_probe "400 403" "space"                                -H "Host: 127.0.0.1 x.steamcontent.com" "$obj0"
                host_probe "400 403" "slash"                                -H "Host: 127.0.0.1/x.steamcontent.com" "$obj0"
                host_probe "400 403" "trailing dot after a ? delimiter"     -H "Host: 127.0.0.1?x.steamcontent.com." "$obj0"
                host_probe 403       "trailing dot, off-list"               -H "Host: evil.example.com." "$obj0"
                host_probe 403       "request-line host beats a Steam Host" -H "Host: cache2-ams1.steamcontent.com" \
                    --request-target "http://evil.example.com/depot/1/chunk/0000000000000000000000000000000000000000" http://127.0.0.1/
                host_probe "400 403" "@ in the request-line host"           \
                    --request-target "http://evil.example.com@x.steamcontent.com/depot/1/chunk/0000000000000000000000000000000000000000" http://127.0.0.1/

                # --- SEC-FIX-1: what the two Host maps evaluate to -----------
                # A legitimate Host would need the real CDN, and nginx rejects
                # most delimiter Hosts before any map runs. So a throwaway
                # server includes the two Host maps exactly as rendered and
                # returns "$vault_host_allowed|$vault_upstream_host".
                #   - maps-host.conf: unchanged, keyed on $host, for what nginx
                #     normalises (case, ":port", one trailing dot).
                #   - maps-raw.conf: the same maps with $host replaced by an
                #     X-Probe-Host request header, so the regexes see the raw
                #     delimiter strings nginx would otherwise reject first.
                # A refused Host must map to an EMPTY upstream host, so
                # proxy_pass can never dial a name the allowlist did not match.
                mkdir -p /tmp/hostprobe/logs
                awk "/^[[:space:]]*map[[:space:]].*[\$]vault_(host_allowed|upstream_host)[[:space:]]*[{]/ { f = 1 } f { print; d += gsub(/[{]/, \"&\") - gsub(/[}]/, \"&\"); if (d <= 0) { f = 0; d = 0 } }" \
                    "$conf" > /tmp/hostprobe/maps-host.conf
                nmaps=$(grep -c "^[[:space:]]*map[[:space:]]" /tmp/hostprobe/maps-host.conf || true)
                [ "$nmaps" = "2" ] || { echo "FAIL: expected the 2 Host maps in $conf, extracted $nmaps"; status=1; }
                sed "s/[\$]host\([^a-z_]\)/\$http_x_probe_host\1/g" /tmp/hostprobe/maps-host.conf > /tmp/hostprobe/maps-raw.conf
                grep -q "[\$]host[^a-z_]" /tmp/hostprobe/maps-raw.conf && { echo "FAIL: a \$host survived in maps-raw.conf"; status=1; }
                mapprobe_start() {
                    printf "%s\n" \
                        "worker_processes 1;" \
                        "pid /tmp/hostprobe/nginx.pid;" \
                        "error_log /dev/stderr warn;" \
                        "events { worker_connections 16; }" \
                        "http {" \
                        "    access_log off;" \
                        "    map_hash_bucket_size 128;" \
                        "    include $1;" \
                        "    server {" \
                        "        listen 127.0.0.1:8099;" \
                        "        location / { return 200 \"\$vault_host_allowed|\$vault_upstream_host\"; }" \
                        "    }" \
                        "}" > /tmp/hostprobe/nginx.conf
                    nginx -p /tmp/hostprobe -c /tmp/hostprobe/nginx.conf
                }
                mapprobe_stop() {
                    nginx -p /tmp/hostprobe -c /tmp/hostprobe/nginx.conf -s quit || true
                    i=0; while [ -f /tmp/hostprobe/nginx.pid ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
                }
                map_probe() {
                    hdr=$1; h=$2; want=$3
                    got=$(curl -s -m 5 -H "$hdr: $h" http://127.0.0.1:8099/ | head -c 200 || echo curl-error)
                    if [ "$got" = "$want" ]; then
                        echo "host map OK ($hdr): \"$h\" -> \"$got\""
                    else
                        echo "FAIL: host map ($hdr): \"$h\" -> \"$got\", expected \"$want\""
                        status=1
                    fi
                }
                mapprobe_start /tmp/hostprobe/maps-host.conf
                map_probe Host "cache2-ams1.steamcontent.com"        "1|cache2-ams1.steamcontent.com"
                map_probe Host "CACHE2-AMS1.SteamContent.COM:80"     "1|cache2-ams1.steamcontent.com"
                map_probe Host "cache2-ams1.steamcontent.com."       "1|cache2-ams1.steamcontent.com"
                map_probe Host "lancache.steamcontent.com"           "1|dist-fra1.discovery.steamserver.net"
                map_probe Host "127.0.0.1%3fx.steamcontent.com"      "0|"
                map_probe Host "evil.example.com"                    "0|"
                mapprobe_stop
                mapprobe_start /tmp/hostprobe/maps-raw.conf
                map_probe X-Probe-Host "cache2-ams1.steamcontent.com"         "1|cache2-ams1.steamcontent.com"
                map_probe X-Probe-Host "dist-fra1.discovery.steamserver.net"  "1|dist-fra1.discovery.steamserver.net"
                map_probe X-Probe-Host "lancache.steamcontent.com"            "1|dist-fra1.discovery.steamserver.net"
                map_probe X-Probe-Host "127.0.0.1?x.steamcontent.com"         "0|"
                map_probe X-Probe-Host "127.0.0.1?x.steamserver.net"          "0|"
                map_probe X-Probe-Host "evil.example.com@x.steamcontent.com"  "0|"
                map_probe X-Probe-Host "127.0.0.1#x.steamcontent.com"         "0|"
                map_probe X-Probe-Host "127.0.0.1%3fx.steamcontent.com"       "0|"
                map_probe X-Probe-Host "127.0.0.1:80?x.steamcontent.com"      "0|"
                map_probe X-Probe-Host "127.0.0.1\\x.steamcontent.com"        "0|"
                map_probe X-Probe-Host "127.0.0.1 x.steamcontent.com"         "0|"
                map_probe X-Probe-Host "127.0.0.1/x.steamcontent.com"         "0|"
                map_probe X-Probe-Host "x_y.steamcontent.com"                 "0|"
                map_probe X-Probe-Host "x..steamcontent.com"                  "0|"
                map_probe X-Probe-Host ".steamcontent.com"                    "0|"
                map_probe X-Probe-Host "steamcontent.com"                     "0|"
                map_probe X-Probe-Host "x.steamcontent.com.evil.example.com"  "0|"
                mapprobe_stop

                nginx -p /vault -c "$conf" -s quit || true
            fi
            exit $status
        '
}

# render_must_fail <label> <VAULT_EVENT_LOG value>
#
# Pre-freeze review P6: the VAULT_EVENT_LOG validation in
# core/docker/25-vault-eventlog.sh guards config injection and a chown of
# arbitrary directories, but only the two happy paths were ever rendered.
# Each bad value below must abort the entrypoint chain (non-zero) AND the
# abort must come from 25-vault-eventlog.sh itself -- a failure anywhere
# else (pull, a later hook, nginx -t) would otherwise pass for the wrong
# reason.
#
# Optional 3rd/4th args (pre-freeze review S5): plant a symlink at <link>
# pointing to <target> before the entrypoint runs, to prove the hook refuses
# to create/chown through it instead of following it as root.
render_must_fail() {
    local label="$1" event_log="$2" link="${3:-}" target="${4:-}" out rc=0
    echo "--- must refuse: $label (VAULT_EVENT_LOG='$event_log') ---"
    out=$(docker run --rm \
        -v "$core_dir/docker:/workspace/core-docker:ro" \
        -e NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates \
        -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
        -e NGINX_ENVSUBST_FILTER='^VAULT_' \
        -e VAULT_RESOLVER="1.1.1.1" \
        -e VAULT_EVENT_LOG="$event_log" \
        -e SYMLINK_AT="$link" \
        -e SYMLINK_TO="$target" \
        --entrypoint sh \
        "$IMAGE" -c '
            set -eu
            mkdir -p /etc/nginx/templates /vault/cache/depot /vault/tmp
            chown -R nginx:nginx /vault
            if [ -n "$SYMLINK_AT" ]; then
                mkdir -p "$(dirname "$SYMLINK_AT")"
                ln -s "$SYMLINK_TO" "$SYMLINK_AT"
            fi
            cp /workspace/core-docker/nginx.conf.template /etc/nginx/templates/nginx.conf.template
            cp /workspace/core-docker/25-vault-eventlog.sh /docker-entrypoint.d/25-vault-eventlog.sh
            cp /workspace/core-docker/27-vault-upstream-rate.sh /docker-entrypoint.d/27-vault-upstream-rate.sh
            cp /workspace/core-docker/28-vault-upstream-pool.sh /docker-entrypoint.d/28-vault-upstream-pool.sh
            cp /workspace/core-docker/40-vault-preflight.sh /docker-entrypoint.d/40-vault-preflight.sh
            chmod 0755 /docker-entrypoint.d/25-vault-eventlog.sh /docker-entrypoint.d/27-vault-upstream-rate.sh /docker-entrypoint.d/28-vault-upstream-pool.sh /docker-entrypoint.d/40-vault-preflight.sh
            /docker-entrypoint.sh nginx -t -p /vault -c /etc/nginx/nginx.conf
        ' 2>&1) || rc=$?
    if [ "$rc" = "0" ]; then
        printf '%s\n' "$out"
        echo "FAIL: VAULT_EVENT_LOG='$event_log' was accepted (exit 0), expected a refusal" >&2
        return 1
    fi
    if ! printf '%s\n' "$out" | grep -qF "25-vault-eventlog.sh: FATAL"; then
        printf '%s\n' "$out"
        echo "FAIL: VAULT_EVENT_LOG='$event_log' failed (exit $rc), but not in 25-vault-eventlog.sh's validation" >&2
        return 1
    fi
    echo "refused as expected (exit $rc): $(printf '%s\n' "$out" | grep -F '25-vault-eventlog.sh: FATAL' | head -n1)"
}

render_and_test "cache-event log OFF (core/Dockerfile default)" "" 0 probe-guards
render_and_test "cache-event log ON" "/vault/logs/event.log" 2

# WP TH-1a: the three upstream-cap render shapes (cap off is the two runs
# above), each through the real entrypoint chain and nginx -t.
render_and_test "upstream cap ON, no window" "" 0 "" "800k" "" cap
render_and_test "upstream cap ON, window wrapping midnight" "" 0 "" "800k" "22:30-06:15" window

render_must_fail "relative path"            "logs/event.log"
render_must_fail "injection character"      "/vault/logs/e;vent.log"
render_must_fail "outside /vault"           "/etc/nginx/event.log"
render_must_fail "'..' escape out of /vault" "/vault/../etc/event.log"
render_must_fail "symlinked log directory"  "/vault/logs/event.log" /vault/logs /etc/nginx
render_must_fail "symlinked log file"       "/vault/logs/event.log" /vault/logs/event.log /etc/passwd

# Pre-freeze review S5: 40-vault-preflight.sh must refuse the base image's
# STOCK /etc/nginx/nginx.conf (what is left at that path when the envsubst
# hook soft-fails and never renders the template). Run the hook directly
# against the untouched image -- no template copied, no envsubst -- and
# require its specific FATAL, so an abort for any other reason fails here.
echo "--- must refuse: 40-vault-preflight.sh against the stock nginx.conf ---"
stock_rc=0
stock_out=$(docker run --rm \
    -v "$core_dir/docker:/workspace/core-docker:ro" \
    -e VAULT_RESOLVER="1.1.1.1" \
    --entrypoint sh \
    "$IMAGE" -c 'sh /workspace/core-docker/40-vault-preflight.sh' 2>&1) || stock_rc=$?
if [ "$stock_rc" = "0" ] || ! printf '%s\n' "$stock_out" | grep -qF "40-vault-preflight.sh: FATAL: /etc/nginx/nginx.conf is NOT the SteamHangar config"; then
    printf '%s\n' "$stock_out"
    echo "FAIL: 40-vault-preflight.sh did not refuse the stock nginx.conf (exit $stock_rc)" >&2
    exit 1
fi
echo "refused as expected (exit $stock_rc): $(printf '%s\n' "$stock_out" | grep -F 'FATAL' | head -n1)"

# --- WP TH-1a: invalid VAULT_UPSTREAM_RATE / _WINDOW must stop the boot ------
# nginx reads an unparseable proxy_limit_rate as 0 = UNLIMITED (TH-0b,
# measured), so every bad value must abort the REAL entrypoint chain, and
# the abort must come from 27-vault-upstream-rate.sh itself. One container,
# one entrypoint run per case. Cases are "rate|window".
echo "--- must refuse: invalid VAULT_UPSTREAM_RATE / VAULT_UPSTREAM_RATE_WINDOW ---"
rate_cases='0|
0800k|
800kb|
8.5m|
 800k|
63|
1023|
1234567890|
2g|
abc|
800k|25:00-03:00
800k|03:00-03:00
800k|24:00-03:00
800k|3:00-07:00
800k|03:00-07:00-08:00
800k|03:60-07:00
800k|03:00-24:01
800k|03:00
|25:00-03:00'
docker run --rm \
    -v "$core_dir/docker:/workspace/core-docker:ro" \
    -e NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates \
    -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
    -e NGINX_ENVSUBST_FILTER='^VAULT_' \
    -e VAULT_RESOLVER="1.1.1.1" \
    -e VAULT_EVENT_LOG="" \
    -e RATE_CASES="$rate_cases" \
    --entrypoint sh \
    "$IMAGE" -c '
        set -eu
        mkdir -p /etc/nginx/templates /vault/cache/depot /vault/tmp
        chown -R nginx:nginx /vault
        cp /workspace/core-docker/nginx.conf.template /etc/nginx/templates/nginx.conf.template
        for h in 25-vault-eventlog.sh 27-vault-upstream-rate.sh 28-vault-upstream-pool.sh 40-vault-preflight.sh; do
            cp "/workspace/core-docker/$h" "/docker-entrypoint.d/$h"
            chmod 0755 "/docker-entrypoint.d/$h"
        done
        status=0
        n=0
        printf "%s\n" "$RATE_CASES" > /tmp/cases
        while IFS="|" read -r r w; do
            n=$((n + 1))
            rc=0
            out=$(VAULT_UPSTREAM_RATE="$r" VAULT_UPSTREAM_RATE_WINDOW="$w" \
                  /docker-entrypoint.sh nginx -t -p /vault -c /etc/nginx/nginx.conf 2>&1) || rc=$?
            if [ "$rc" = "0" ]; then
                echo "FAIL: rate=\"$r\" window=\"$w\" was accepted (exit 0)"
                status=1
            elif ! printf "%s\n" "$out" | grep -qF "27-vault-upstream-rate.sh: FATAL"; then
                printf "%s\n" "$out"
                echo "FAIL: rate=\"$r\" window=\"$w\" failed (exit $rc), but not in 27-vault-upstream-rate.sh"
                status=1
            else
                echo "refused (exit $rc): rate=\"$r\" window=\"$w\": $(printf "%s\n" "$out" | grep -F "27-vault-upstream-rate.sh: FATAL" | head -n1 | cut -c1-110)"
            fi
        done < /tmp/cases
        [ "$n" = "19" ] || { echo "FAIL: ran $n rate/window cases, expected 19"; status=1; }
        exit $status
    '

# --- WP TH-1a: 40-vault-preflight.sh re-checks the include independently -----
# A cap configured but not rendered (hook missing/out of order) must stop the
# boot. Render with NO cap through the real chain, then run the preflight
# with VAULT_UPSTREAM_RATE set (stale cap-off include) and with the include
# deleted; both must hit the preflight's own FATAL.
echo "--- must refuse: 40-vault-preflight.sh against a missing / cap-off include ---"
docker run --rm \
    -v "$core_dir/docker:/workspace/core-docker:ro" \
    -e NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates \
    -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
    -e NGINX_ENVSUBST_FILTER='^VAULT_' \
    -e VAULT_RESOLVER="1.1.1.1" \
    -e VAULT_EVENT_LOG="" \
    -e VAULT_UPSTREAM_RATE="" \
    --entrypoint sh \
    "$IMAGE" -c '
        set -eu
        mkdir -p /etc/nginx/templates /vault/cache/depot /vault/tmp
        chown -R nginx:nginx /vault
        cp /workspace/core-docker/nginx.conf.template /etc/nginx/templates/nginx.conf.template
        for h in 25-vault-eventlog.sh 27-vault-upstream-rate.sh 28-vault-upstream-pool.sh 40-vault-preflight.sh; do
            cp "/workspace/core-docker/$h" "/docker-entrypoint.d/$h"
            chmod 0755 "/docker-entrypoint.d/$h"
        done
        /docker-entrypoint.sh nginx -t -p /vault -c /etc/nginx/nginx.conf >/dev/null 2>&1
        status=0
        expect_preflight_fatal() {
            what=$1; shift
            rc=0
            out=$("$@" sh /docker-entrypoint.d/40-vault-preflight.sh 2>&1) || rc=$?
            if [ "$rc" != "0" ] && printf "%s\n" "$out" | grep -qF "40-vault-preflight.sh: FATAL"; then
                echo "refused as expected ($what): $(printf "%s\n" "$out" | grep -F FATAL | head -n1 | cut -c1-110)"
            else
                printf "%s\n" "$out"
                echo "FAIL: 40-vault-preflight.sh did not refuse ($what), exit $rc"
                status=1
            fi
        }
        expect_preflight_fatal "cap set, include rendered without it" env VAULT_UPSTREAM_RATE=800k
        rm /etc/nginx/vault-upstream-rate.conf
        expect_preflight_fatal "include missing" env VAULT_UPSTREAM_RATE=
        exit $status
    '

# --- WP TH-1a: the rendered value, LIVE (offline) -----------------------------
# Map contents prove the shape; this proves what nginx actually evaluates.
# A throwaway server includes the rendered file and returns
# $vault_upstream_rate for one loopback request ($connections_writing = 1,
# i.e. bucket 1 = the total). Window cases are built around the container's
# CURRENT local minute, so both "inside" (expect 0 = full speed) and
# "outside" (expect the cap) run against the real $time_iso8601. Needs no
# DNS and no CDN. The probe config is written with printf (no here-doc) so
# no line of it can ever terminate an enclosing here-doc.
echo "--- upstream cap: evaluated value of \$vault_upstream_rate (live, loopback) ---"
docker run --rm \
    -v "$core_dir/docker:/workspace/core-docker:ro" \
    --entrypoint sh \
    "$IMAGE" -c '
        set -eu
        command -v curl >/dev/null 2>&1 || { echo "FAIL: curl missing in the base image"; exit 1; }
        mkdir -p /tmp/probe/logs
        printf "%s\n" \
            "worker_processes 1;" \
            "pid /tmp/probe/nginx.pid;" \
            "error_log /dev/stderr warn;" \
            "events { worker_connections 16; }" \
            "http {" \
            "    access_log off;" \
            "    include /tmp/probe/vault-upstream-rate.conf;" \
            "    server {" \
            "        listen 127.0.0.1:8099;" \
            "        location / { return 200 \"\$vault_upstream_rate\"; }" \
            "    }" \
            "}" > /tmp/probe/nginx.conf
        hhmm() { printf "%02d:%02d" $(( ($1 % 1440) / 60 )) $(( ($1 % 1440) % 60 )); }
        now_h=$(date +%H); now_m=$(date +%M)
        now_h=${now_h#0}; now_m=${now_m#0}
        now=$(( ${now_h:-0} * 60 + ${now_m:-0} ))
        inside="$(hhmm $((now + 1440 - 2)))-$(hhmm $((now + 3)))"
        outside="$(hhmm $((now + 3)))-$(hhmm $((now + 6)))"
        echo "container local time $(date +%H:%M) (TZ=${TZ:-unset}); inside window $inside, outside window $outside"
        status=0
        probe_value() {
            label=$1 rate=$2 window=$3 want=$4
            VAULT_UPSTREAM_RATE="$rate" VAULT_UPSTREAM_RATE_WINDOW="$window" \
                sh /workspace/core-docker/27-vault-upstream-rate.sh /tmp/probe/vault-upstream-rate.conf >/dev/null
            nginx -p /tmp/probe -c /tmp/probe/nginx.conf
            got=$(curl -s -m 5 http://127.0.0.1:8099/ || echo curl-error)
            nginx -p /tmp/probe -c /tmp/probe/nginx.conf -s quit || true
            i=0; while [ -f /tmp/probe/nginx.pid ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
            if [ "$got" = "$want" ]; then
                echo "rate OK: $label -> $got"
            else
                echo "FAIL: $label -> \"$got\", expected \"$want\""
                status=1
            fi
        }
        probe_value "no cap"                       ""   ""         0
        probe_value "800k, no window, 1 request"   800k ""         819200
        probe_value "800k, inside the window"      800k "$inside"  0
        probe_value "800k, outside the window"     800k "$outside" 819200
        # 24:00 as the END value: [now+3min, end of day) excludes now --
        # unless now+3 already crossed midnight, when that window is empty
        # territory for this probe; skip it then rather than build a wrong
        # expectation.
        if [ $((now + 3)) -lt 1440 ]; then
            probe_value "2m, outside a window ending 24:00" 2m "$(hhmm $((now + 3)))-24:00" 2097152
        fi
        exit $status
    '

echo "OK: rendered core/docker/nginx.conf.template passes 'nginx -t' and the" \
     "access_log/vault_event invariant for both VAULT_EVENT_LOG states;" \
     "the event log is owned 101:101; the /depot/ request guards answer live;" \
     "crafted Hosts are refused with no upstream attempt and the Host maps" \
     "full-match only Steam hostnames;" \
     "four bad VAULT_EVENT_LOG values and two planted symlinks are refused;" \
     "the preflight refuses the stock nginx.conf; the upstream cap renders in" \
     "all three shapes, refuses invalid rates/windows, is re-checked by the" \
     "preflight and evaluates to the expected value live."
