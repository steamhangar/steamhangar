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

# render_and_test <label> <VAULT_EVENT_LOG value> <expected access_log/vault_event directive count> [probe-guards]
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
render_and_test() {
    local label="$1" event_log="$2" expected_directives="$3"
    echo "--- nginx -t: $label (VAULT_EVENT_LOG='$event_log') ---"
    docker run --rm \
        -v "$core_dir/docker:/workspace/core-docker:ro" \
        -e NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates \
        -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
        -e NGINX_ENVSUBST_FILTER='^VAULT_' \
        -e VAULT_RESOLVER="1.1.1.1" \
        -e VAULT_EVENT_LOG="$event_log" \
        -e EXPECTED_DIRECTIVES="$expected_directives" \
        -e PROBE_GUARDS="${4:-}" \
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
            cp /workspace/core-docker/40-vault-preflight.sh /docker-entrypoint.d/40-vault-preflight.sh
            chmod 0755 /docker-entrypoint.d/25-vault-eventlog.sh /docker-entrypoint.d/40-vault-preflight.sh

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

            # --- Pre-freeze review S1/S2/P3/N5: request guards, LIVE -------
            # nginx -t proves the directives parse, not that they answer.
            # Start the rendered config for real and probe the guards in
            # location /depot/ over loopback. Every probe below is answered
            # locally by a `return` in the rewrite phase, so none of them
            # needs DNS or the Steam CDN -- this runs offline.
            if [ -n "${PROBE_GUARDS:-}" ] && [ "$nginx_t_status" = "0" ]; then
                command -v curl >/dev/null 2>&1 || { echo "FAIL: curl missing in the base image, cannot probe guards"; exit 1; }
                nginx -p /vault -c "$conf"
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
            cp /workspace/core-docker/40-vault-preflight.sh /docker-entrypoint.d/40-vault-preflight.sh
            chmod 0755 /docker-entrypoint.d/25-vault-eventlog.sh /docker-entrypoint.d/40-vault-preflight.sh
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

echo "OK: rendered core/docker/nginx.conf.template passes 'nginx -t' and the" \
     "access_log/vault_event invariant for both VAULT_EVENT_LOG states;" \
     "the event log is owned 101:101; the /depot/ request guards answer live;" \
     "four bad VAULT_EVENT_LOG values and two planted symlinks are refused;" \
     "the preflight refuses the stock nginx.conf."
