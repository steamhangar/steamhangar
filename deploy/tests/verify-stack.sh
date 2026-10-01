#!/bin/sh
# SteamHangar WP 1.9 -- container verification suite.
#
# Proves that the five images (four builds -- vault-runner reuses vault-api's)
# and deploy/compose.yaml actually deliver what the Phase-0 PoC and WP 1.1-1.8
# established, INSIDE Linux containers: the cache
# stores and serves real Steam CDN bytes, the API answers and authenticates, the
# DNS container redirects A and NODATAs AAAA, and every fail-fast guard fails.
#
# Run it on a Linux host with Docker (this project develops on Windows; the
# canonical place to run this is WSL2):
#
#     sudo sh deploy/tests/verify-stack.sh
#
# It is self-contained and side-effect-free by design:
#   * uses its own Compose project name (steamhangar-verify), so it can never
#     touch a real deployment's containers or volumes
#   * publishes every port on 127.0.0.1 and on non-default port numbers, so it
#     cannot collide with a host nginx on :80 or a host resolver on :53
#   * tears its containers and volumes down at the end (images are kept -- they
#     are the artifact under test)
#
# Requires outbound internet: two of the checks talk to the real Steam CDN and
# to a public resolver on purpose. There is no mock -- the whole point of Phase 0
# was that only real traffic settles these questions.
#
# Exit code 0 = every check passed.

set -u
# Compose gives the caller's shell environment precedence over --env-file;
# steps 3e/3e-bis/3e-ter/6i-core assert compose DEFAULTS, so a TZ or window
# exported in the calling shell must not leak in.
# VAULT_EGRESS_SUBNET (WP DEPLOY-FIX-2) for the same reason: this run's own
# subnet must come from the generated env file, never from the caller.
unset TZ VAULT_SCHEDULE_WINDOW VAULT_UPSTREAM_RATE VAULT_UPSTREAM_RATE_WINDOW VAULT_EGRESS_SUBNET

# --- where things are --------------------------------------------------------
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/../.." && pwd)
compose_file="$repo_root/deploy/compose.yaml"

PROJECT=steamhangar-verify
TAG=${VAULT_IMAGE_TAG:-0.1.0}

# Non-default, loopback-only host ports (see header).
CORE_PORT=8180
API_PORT=8181
DNS_PORT=15353

# The known-good Phase-0 test object: depot 70403, used from poc/ through
# core/tests/test-core.ps1. Small, stable, and already proven to be a real
# cacheable chunk.
DEPOT=70403
CHUNK=773d10050d99b2544665873ec2125b3bf273e8b2
CDN_HOST=cache2-ams1.steamcontent.com

CORE_URL="http://127.0.0.1:$CORE_PORT"
API_URL="http://127.0.0.1:$API_PORT"
DEPOT_URI="/depot/$DEPOT/chunk/$CHUNK"

TEST_API_KEY="verify-only-not-a-real-key-$$"
TEST_CACHE_IP=192.168.222.50

# WP DEPLOY-FIX-2: the project name scopes container, volume and network
# NAMES, but not the vault-egress SUBNET -- with compose's default
# (172.30.238.0/24) this run's `up -d` fails with "Pool overlaps" on any host
# that already runs a test or production stack. Its own /24 lets it run beside
# them. Must not overlap any other Docker network on the host.
VERIFY_EGRESS_SUBNET=172.30.239.0/24
DEFAULT_EGRESS_SUBNET=172.30.238.0/24

work=$(mktemp -d)
env_file="$work/verify.env"

pass=0
fail=0

# --- output helpers ----------------------------------------------------------
section() { printf '\n\n## %s\n\n' "$*"; }
step()    { printf '\n### %s\n\n' "$*"; }
say()     { printf '%s\n' "$*"; }
run()     { printf '$ %s\n' "$*"; sh -c "$*" 2>&1 | sed 's/^/    /'; }

ok()   { pass=$((pass + 1)); printf 'PASS  %s\n' "$*"; }
bad()  { fail=$((fail + 1)); printf 'FAIL  %s\n' "$*"; }

# fatal <reason>: WP DEPLOY-FIX-2. A failed build or `up -d` leaves nothing
# meaningful to check -- every later step would only cascade into FAILs that
# hide the one real cause. Stop here, non-zero; the EXIT trap still cleans up.
fatal() {
    fail=$((fail + 1))
    printf '\nFATAL %s\n' "$*"
    say "checks passed so far: $pass, failed: $fail -- aborting, no further checks run."
    exit 2
}

# compose_up_or_die <env-file> <compose args...>: runs `docker compose ...`
# for an `up -d` with this run's project, prints its output like run(), and
# aborts via fatal() if it fails. run() pipes through sed and loses the exit
# status, which is how a failed `up -d` used to cascade.
compose_up_or_die() {
    up_env=$1
    shift
    printf '$ docker compose --env-file %s -f %s -p %s %s\n' "$up_env" "$compose_file" "$PROJECT" "$*"
    if docker compose --env-file "$up_env" -f "$compose_file" -p "$PROJECT" "$@" > "$work/compose-up.log" 2>&1; then
        sed 's/^/    /' "$work/compose-up.log"
    else
        up_rc=$?
        sed 's/^/    /' "$work/compose-up.log"
        fatal "docker compose $* failed (exit $up_rc) -- see the output above"
    fi
}

# assert_eq <expected> <actual> <description>
assert_eq() {
    if [ "$1" = "$2" ]; then ok "$3 (= $2)"; else bad "$3 -- expected '$1', got '$2'"; fi
}
# assert_contains <haystack> <needle> <description>
assert_contains() {
    case "$1" in
        *"$2"*) ok "$3" ;;
        *)      bad "$3 -- '$2' not found in: $(printf '%s' "$1" | head -c 300)" ;;
    esac
}
# assert_not_contains <haystack> <needle> <description>
assert_not_contains() {
    case "$1" in
        *"$2"*) bad "$3 -- '$2' unexpectedly present" ;;
        *)      ok "$3" ;;
    esac
}

dc() {
    docker compose --env-file "$env_file" -f "$compose_file" -p "$PROJECT" "$@"
}

cleanup() {
    section "Cleanup"
    say 'Test containers and TEST volumes are removed; the images are kept (they are the artifact).'
    # Pre-freeze review N3: the step-6l host-side listener used to be killed
    # only on the straight-line path, so an abort (INT/TERM, or a `set -e`-
    # style early exit added later) between its start and that kill left a
    # `python3 -m http.server` bound to 0.0.0.0:18089 on the HOST. Killed
    # here too, BEFORE $work is removed (the pid file lives there); a pid
    # file that was never written, or a listener already gone, is a no-op.
    if [ -f "$work/b2-listener.pid" ]; then
        kill "$(cat "$work/b2-listener.pid")" >/dev/null 2>&1 || true
    fi
    run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' --profile dns down -v --remove-orphans"
    docker volume rm -f "$PROJECT-split-cache" "$PROJECT-scratch" >/dev/null 2>&1
    # WP DEPLOY-FIX-3: section 9's bind dir holds files owned by uid 101
    # (nginx wrote them); hand them back to the caller so rm -rf works
    # without root. The containers are gone by now.
    if [ -n "${bind_dir:-}" ] && [ -d "$bind_dir" ]; then
        docker run --rm --user 0:0 --entrypoint sh -v "$bind_dir:/vault" \
            "ghcr.io/steamhangar/vault-core:$TAG" -c "chown -R $(id -u):$(id -g) /vault" >/dev/null 2>&1
    fi
    rm -rf "$work"
}
# INT/TERM: clean up once (drop the EXIT trap first so it does not run a
# second time) and exit non-zero, instead of resuming the script.
trap cleanup EXIT
trap 'trap - EXIT; cleanup; exit 130' INT TERM

printf '# SteamHangar WP 1.9 -- container verification transcript\n\n'
say "date:            $(date -u '+%Y-%m-%dT%H:%M:%SZ') (UTC)"
say "host:            $(uname -srm)"
say "distro:          $(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"
say "docker:          $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
say "docker compose:  $(docker compose version --short 2>/dev/null)"
say "repo:            $repo_root"
say "compose project: $PROJECT (isolated from any real deployment)"

# =============================================================================
section "1. Config drift: the container nginx.conf is the reviewed one"
# =============================================================================
say 'core/docker/nginx.conf.template must stay identical to the reviewed, CDN-tested'
say 'core/nginx/nginx.conf apart from five container-plumbing lines.'
step "1a. Positive: the checked-in pair is in sync"
run "sh '$repo_root/core/docker/check-config-drift.sh'"
if sh "$repo_root/core/docker/check-config-drift.sh" >/dev/null 2>&1; then
    ok "check-config-drift.sh reports the two configs in sync"
else
    bad "check-config-drift.sh reports drift"
fi

step "1b. Negative: an injected difference is actually caught"
say 'Without this, a green drift check would prove nothing. A copy of the pair is'
say 'mutated (proxy_connect_timeout 3s -> 30s in the container template only) and'
say 'the same script is re-run against the copy.'
mkdir -p "$work/drift/nginx" "$work/drift/docker"
cp "$repo_root/core/nginx/nginx.conf" "$work/drift/nginx/nginx.conf"
cp "$repo_root/core/docker/nginx.conf.template" "$work/drift/docker/nginx.conf.template"
cp "$repo_root/core/docker/check-config-drift.sh" "$work/drift/docker/check-config-drift.sh"
# TH-1a: the drift script also reads these two (resolved from its own dir/..).
cp "$repo_root/core/docker/27-vault-upstream-rate.sh" "$work/drift/docker/27-vault-upstream-rate.sh"
cp "$repo_root/core/nginx/vault-upstream-rate.conf" "$work/drift/nginx/vault-upstream-rate.conf"
# The unmutated copy must pass first, else a later FAIL could stem from an
# incomplete copy rather than from the injected difference.
if sh "$work/drift/docker/check-config-drift.sh" >/dev/null 2>&1; then
    ok "unmutated drift copy passes (the copy is complete)"
else
    bad "unmutated drift copy already fails (copy incomplete?)"
fi
sed -i 's/proxy_connect_timeout      3s;/proxy_connect_timeout      30s;/' "$work/drift/docker/nginx.conf.template"
run "sh '$work/drift/docker/check-config-drift.sh' 2>&1 | tail -12"
if drift_out=$(sh "$work/drift/docker/check-config-drift.sh" 2>&1); then
    bad "drift check did NOT catch an injected difference"
elif printf '%s\n' "$drift_out" | grep -q 'proxy_connect_timeout'; then
    ok "drift check catches the injected difference (exit non-zero, names proxy_connect_timeout)"
else
    bad "drift check failed but did not name proxy_connect_timeout (wrong failure reason)"
fi

# =============================================================================
section "2. Image builds"
# =============================================================================
for svc in core api proxy dns; do
    step "2.$svc  docker build $svc/"
    build_failed=0
    if [ "$svc" = "api" ]; then
        # vault-api's build context moved to the REPO ROOT in the packaging
        # work package (docs/PROJECT_PLAN.md §7 Phase 5): api/Dockerfile now
        # COPYs web/ in too (`COPY web /app/web`), and web/ is a repo-root
        # SIBLING of api/, not a child of it -- an api/-only context can no
        # longer reach it. core and dns are UNCHANGED, still built from their
        # own directories below.
        docker build -t "ghcr.io/steamhangar/vault-api:$TAG" -f "$repo_root/api/Dockerfile" "$repo_root" \
            > "$work/build-$svc.log" 2>&1 || build_failed=1
    elif [ "$svc" = "proxy" ]; then
        # WP EG-1 (ADR-0011), round-2 review B4: this WAS missing entirely --
        # deploy/README.md claimed this script "builds every image including
        # vault-proxy" while this loop only ever built core/api/dns, so a real
        # Dockerfile break here (e.g. Alpine dropping the pinned tinyproxy
        # version) was never caught here, and step 6l further down would have
        # gone on to test whatever STALE `ghcr.io/steamhangar/vault-proxy:$TAG` image
        # happened to already exist locally, silently. deploy/proxy/ is its
        # own build context (not $repo_root/proxy, which does not exist --
        # this service's Dockerfile lives under deploy/, unlike the three
        # components above which each own a repo-root-level directory).
        docker build -t "ghcr.io/steamhangar/vault-proxy:$TAG" "$repo_root/deploy/proxy" \
            > "$work/build-$svc.log" 2>&1 || build_failed=1
    else
        docker build -t "ghcr.io/steamhangar/vault-$svc:$TAG" "$repo_root/$svc" \
            > "$work/build-$svc.log" 2>&1 || build_failed=1
    fi
    if [ "$build_failed" -eq 0 ]; then
        ok "vault-$svc image built"
    else
        tail -30 "$work/build-$svc.log" | sed 's/^/    /'
        # fatal() counts this failure once; no separate bad() here.
        fatal "vault-$svc image build failed -- every later check would test a missing or stale image"
    fi
done

step "2.sizes  Built image sizes"
run "docker images --filter reference='steamhangar/*' --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.ID}}'"

step "2.pins  Base image pins actually used (tag + digest)"
run "grep -h '^FROM' '$repo_root/core/Dockerfile' '$repo_root/api/Dockerfile' '$repo_root/deploy/proxy/Dockerfile' '$repo_root/dns/Dockerfile'"

step "2.sp  SteamPrefill binary in the vault-api image"
say 'Checked by inspection only. This work package deliberately does NOT execute'
say 'SteamPrefill in a container: it has no Steam session, and creating one is the'
say "operator's one-time interactive step (deploy/README.md 'First run')."
run "docker run --rm --entrypoint sh ghcr.io/steamhangar/vault-api:$TAG -c 'ls -l /opt/steamprefill/SteamPrefill; sha256sum /opt/steamprefill/SteamPrefill; head -c 4 /opt/steamprefill/SteamPrefill | od -c | head -1'"
sp_deps=$(docker run --rm --entrypoint sh "ghcr.io/steamhangar/vault-api:$TAG" -c 'ldd /opt/steamprefill/SteamPrefill 2>&1' )
say ''
say 'Dynamic libraries the binary needs, resolved inside the image (no "not found"):'
printf '%s\n' "$sp_deps" | sed 's/^/    /'
assert_not_contains "$sp_deps" "not found" "every shared library SteamPrefill needs resolves in the image"

step "2.web  Web UI is baked into the vault-api image (packaging WP)"
say 'docs/PROJECT_PLAN.md §7 Phase 5: the web UI moved from mount-only (a'
say 'documented gap since WP 4a.1) into the image itself -- api/Dockerfile now'
say 'COPYs web/ in at /app/web and sets VAULT_WEB_DIR=/app/web explicitly.'
say 'Checked here at the IMAGE layer (docker run, no compose stack needed yet),'
say 'so a broken COPY path is caught even before section 6 exercises it over HTTP.'
web_ls=$(docker run --rm --entrypoint sh "ghcr.io/steamhangar/vault-api:$TAG" -c 'ls -la /app/web /app/web/css /app/web/js 2>&1')
printf '%s\n' "$web_ls" | sed 's/^/    /'
assert_contains "$web_ls" "index.html" "/app/web/index.html exists in the image"
assert_contains "$web_ls" "app.css" "/app/web/css/app.css (an app-shell asset) exists in the image"
assert_contains "$web_ls" "app.js" "/app/web/js/app.js (an app-shell asset) exists in the image"
webdir_env=$(docker run --rm --entrypoint sh "ghcr.io/steamhangar/vault-api:$TAG" -c 'printf %s "$VAULT_WEB_DIR"')
assert_eq "/app/web" "$webdir_env" "VAULT_WEB_DIR is baked into the image and points at the actual COPY target"

step "2.home  HOME for uid 101 exists, is owned by it, and both definitions agree"
say 'Regression guard for the WP 1.9 review blocker: with HOME unwritable,'
say "SteamPrefill's AppConfig static constructor throws before parsing any"
say 'argument, so the documented login and every prefill job die identically.'
run "docker run --rm --entrypoint sh ghcr.io/steamhangar/vault-api:$TAG -c 'getent passwd 101; echo \"ENV HOME=\$HOME\"; stat -c \"%n %u:%g %a\" \$HOME'"
home_passwd=$(docker run --rm --entrypoint sh "ghcr.io/steamhangar/vault-api:$TAG" -c 'getent passwd 101 | cut -d: -f6')
home_env=$(docker run --rm --entrypoint sh "ghcr.io/steamhangar/vault-api:$TAG" -c 'printf %s "$HOME"')
home_own=$(docker run --rm --entrypoint sh "ghcr.io/steamhangar/vault-api:$TAG" -c 'stat -c "%u:%g" /opt/steamprefill/home')
assert_eq "/opt/steamprefill/home" "$home_passwd" "passwd entry for uid 101 has a real home"
assert_eq "$home_passwd" "$home_env"             "ENV HOME agrees with the passwd entry"
assert_eq "101:101" "$home_own"                  "HOME is owned by the container user"

step "2.smoke  Credential-free SteamPrefill smoke check"
say 'Runs the real binary with stdin closed and NO credentials. Expected sane'
say 'outcome: it starts, reports that a Steam account is required, prompts for a'
say 'username, and then exits because stdin is at EOF. What must NOT appear is a'
say 'TypeInitializationException -- that is the blocker signature, and it fires'
say 'before any prompt, so "reached the username prompt" is the proof it is gone.'
say ''
say 'NO CREDENTIALS ARE ENTERED HERE, EVER. Logging in is the operator step.'
# ANSI is stripped: SteamPrefill colourises mid-sentence (e.g. "A <esc>[38;5;80m
# Steam<esc>[0m account is required"), so raw substring matching is unreliable.
strip_ansi() { sed -e 's/\x1B\[[0-9;]*[A-Za-z]//g'; }
sp_smoke=$(docker run --rm --entrypoint /opt/steamprefill/SteamPrefill \
             "ghcr.io/steamhangar/vault-api:$TAG" select-apps < /dev/null 2>&1 | strip_ansi | head -12)
printf '%s\n' "$sp_smoke" | sed 's/^/    /'
assert_not_contains "$sp_smoke" "TypeInitializationException" "no TypeInitializationException (the blocker signature)"
assert_not_contains "$sp_smoke" "UnauthorizedAccessException" "no UnauthorizedAccessException reaching for HOME"
assert_contains     "$sp_smoke" "account is required in order to prefill apps" "SteamPrefill starts and reaches its login logic"
assert_contains     "$sp_smoke" "Steam account name" "...and gets as far as prompting for a username"

# =============================================================================
section "3. compose.yaml review surface"
# =============================================================================
cat > "$env_file" <<EOF
# generated by deploy/tests/verify-stack.sh -- test values only
VAULT_API_KEY=$TEST_API_KEY
VAULT_IMAGE_TAG=$TAG
VAULT_CORE_BIND=127.0.0.1
VAULT_CORE_PORT=$CORE_PORT
VAULT_API_BIND=127.0.0.1
VAULT_API_PORT=$API_PORT
VAULT_DNS_BIND=127.0.0.1
VAULT_DNS_PORT=$DNS_PORT
CACHE_IP=$TEST_CACHE_IP
VAULT_EGRESS_SUBNET=$VERIFY_EGRESS_SUBNET
EOF
say 'Test .env used for this run (ports moved off 80/8080/53 because this WSL host'
say 'already has services there; bind kept on loopback so nothing is LAN-visible):'
say ''
sed 's/^/    /' "$env_file"

step "3a. Rendered configuration (docker compose config)"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' --profile dns config"

step "3b. No secret is baked into compose.yaml"
if grep -nE '(VAULT_API_KEY[[:space:]]*[:=][[:space:]]*[A-Za-z0-9])' "$compose_file" | grep -v '\${' > "$work/secret.txt"; then
    bad "compose.yaml appears to contain a literal API key"
    sed 's/^/    /' "$work/secret.txt"
else
    ok "compose.yaml contains no literal secret (VAULT_API_KEY only as a required \${...} reference)"
fi

step "3c. Port 53 is never published on 0.0.0.0"
rendered=$(dc --profile dns config 2>/dev/null)
assert_not_contains "$rendered" "0.0.0.0:53" "rendered config does not publish :53 on 0.0.0.0"
bare53=$(grep -nE '^[[:space:]]*-[[:space:]]*"?53:53' "$compose_file" || true)
assert_eq "" "$bare53" "compose.yaml has no bare 53:53 mapping"

step "3d. VAULT_API_KEY is required, not defaulted"
noKey=$(docker compose --env-file /dev/null -f "$compose_file" -p "$PROJECT" config 2>&1 >/dev/null)
say "$noKey" | sed 's/^/    /'
assert_contains "$noKey" "VAULT_API_KEY" "compose refuses to render without VAULT_API_KEY"

# --- WP D1's gap CLOSED by the packaging WP's real run (2026-08-17) --------
# Steps 3e and 5i (this one and the one in section 5 below) were written in
# WP D1 with no Docker available and had never run against a real host --
# only statically validated (YAML parse, `sh -n`, the awk/sed extraction
# logic replayed against synthetic fixtures). The packaging work package
# (build-context move to the repo root, the twelve env-forwarding additions
# across this step and the B1 audit block below it, and 2.web/6h/6i/6j in
# the sections that follow -- 109 checks total AT THAT TIME, up from WP
# D1's 73) was written the same way, then actually run THREE times against
# a real Docker host across two review rounds (WSL2, Engine 29.1.3/Compose
# 2.40.3): 105/109 passed on the final run. Every packaging-WP check passed
# on every run. (The suite has grown since with later work packages -- WP
# RN-1's run of this script counted 185 checks total; deploy/README.md
# carries that measured figure.) The 4 failures were ALL in step 5i below,
# and were a genuine PRE-EXISTING bug unrelated to this package: nginx's
# cache-event `access_log` uses `buffer=64k flush=5s` (core/nginx/nginx.conf),
# and step 5i's grep ran immediately after the triggering request with no
# wait for that flush -- an isolated repro (fresh `docker run` of vault-core alone,
# one real MISS, checking the file at increasing delays) showed the correct
# 9-field line reliably once you wait past 5 s, and reliably empty before
# that. Reproducible on every run so far, but NOT deterministic in the
# strict sense -- the pass/fail line depended on real wall-clock timing
# against a host whose speed varies, so a green 5i on a slower host would
# not by itself have meant the underlying race was fixed (see
# docs/LEARNINGS.md "Testing discipline" for the standing entry this bug
# produced). FIXED in WP 4g with a bounded wait-for-line loop -- see the
# comment above step 5i in section 5 below for the mechanism and why a flat
# `sleep` would not have been an honest fix.
step "3e. Phase-3 knobs (VAULT_EVENT_LOG / VAULT_GC_GRACE_DAYS / VAULT_AUTO_GC) plus VAULT_SWEEP_INCLUDE_CACHED (WP SWEEP-1): defaults pass through unchanged"
say 'The test .env above (section 3) sets none of these -- deploy/README.md'
say '"Phase-3 knobs" -- so the rendered config must show vault-core with an empty'
say 'event-log path and vault-api with the same defaults api/README.md documents'
say '(grace window 14 days, auto-GC execute, sweep_include_cached true -- WP SWEEP-1'
say '/ ADR-0014 flipped the latter two together, 2026-08-22). Quoting style varies'
say 'across Compose versions, so values are compared with surrounding quotes stripped.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' --profile dns config"
rendered_default=$(dc --profile dns config 2>/dev/null)
# Extract just one service's block: from its "  <name>:" key line up to (but
# not including) the next line at the same two-space service-key indent --
# robust regardless of which services precede/follow or whether the dns
# profile happens to be included in this render.
# Stop at either a sibling service key (two-space indent) or the next
# top-level key (zero indent, e.g. "volumes:" following the last service) --
# whichever comes first, so the block never runs past this one service's
# lines even if it happens to be the last one rendered.
core_block=$(printf '%s\n' "$rendered_default" | awk '/^  vault-core:/{f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
api_block=$(printf '%s\n' "$rendered_default" | awk '/^  vault-api:/{f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
# WP S-2 (ADR-0012): the runner's own block, same isolation logic.
runner_block=$(printf '%s\n' "$rendered_default" | awk '/^  vault-runner:/{f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
# WP EG-1 (ADR-0011): the proxy's own block, same isolation logic.
proxy_block=$(printf '%s\n' "$rendered_default" | awk '/^  vault-proxy:/{f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
# WP EG-1: the top-level `networks:` key itself (0-indent -- a DIFFERENT
# thing from any service's own 4-indent `networks:` sub-key inside its own
# block above; ends at the next 0-indent key, e.g. `volumes:`).
networks_block=$(printf '%s\n' "$rendered_default" | awk '/^networks:/{f=1;next} f && /^[A-Za-z]/{exit} f')

# Precondition, checked BEFORE the emptiness check below means anything:
# `assert_eq "" "$event_log_val"` also reads as "pass" if VAULT_EVENT_LOG is
# entirely ABSENT from the rendered block (grep finds nothing -> the variable
# is empty too) -- exactly the regression this step exists to catch if
# someone ever deletes the compose.yaml passthrough line. Gate on the key
# actually being present exactly once first (LEARNINGS "Testing discipline":
# fail-closed assertions need a precondition, not just an outcome check).
event_log_key_count=$(printf '%s\n' "$core_block" | grep -c 'VAULT_EVENT_LOG:')
assert_eq "1" "$event_log_key_count" "vault-core: VAULT_EVENT_LOG key is present exactly once in the rendered block (precondition for the emptiness check below)"

event_log_val=$(printf '%s\n' "$core_block" | grep 'VAULT_EVENT_LOG:' | head -1 | sed -e 's/^[[:space:]]*VAULT_EVENT_LOG:[[:space:]]*//' -e 's/"//g')
grace_val=$(printf '%s\n' "$api_block" | grep 'VAULT_GC_GRACE_DAYS:' | head -1 | sed -e 's/^[[:space:]]*VAULT_GC_GRACE_DAYS:[[:space:]]*//' -e 's/"//g')
autogc_val=$(printf '%s\n' "$api_block" | grep 'VAULT_AUTO_GC:' | head -1 | sed -e 's/^[[:space:]]*VAULT_AUTO_GC:[[:space:]]*//' -e 's/"//g')
assert_eq "" "$event_log_val"    "vault-core: VAULT_EVENT_LOG renders empty (feature off) by default"
assert_eq "14" "$grace_val"      "vault-api: VAULT_GC_GRACE_DAYS default (14) passes through"
# WP SWEEP-1 (ADR-0014, operator decision 2026-08-22): VAULT_AUTO_GC's own
# default flipped from "off" to "execute", PAIRED with
# VAULT_SWEEP_INCLUDE_CACHED below also flipping to "true" -- see that ADR
# for the pairing argument and docs/PROJECT_PLAN.md/README.md for the
# upgrade-behaviour-change this represents on an existing deployment.
assert_eq "execute" "$autogc_val" "vault-api: VAULT_AUTO_GC default (execute, WP SWEEP-1) passes through"

# WP SWEEP-1 (ADR-0014). Newly forwarded here as of this package -- see
# deploy/compose.yaml's own comment on this key for why (the
# VAULT_SETTINGS_READONLY trap this closes: without this line, a
# `VAULT_SETTINGS_READONLY=1` deployment would have had no way to turn the
# now-default-on cached sweep back off at all). Same precondition-then-value
# pattern as VAULT_EVENT_LOG_PATH below: presence is checked BEFORE the value,
# or a deleted passthrough line would read as an unnoticed pass.
sweep_include_cached_key_count=$(printf '%s\n' "$api_block" | grep -c 'VAULT_SWEEP_INCLUDE_CACHED:')
assert_eq "1" "$sweep_include_cached_key_count" "vault-api: VAULT_SWEEP_INCLUDE_CACHED key is present exactly once in the rendered block (precondition; WP SWEEP-1 forwarding)"
sweep_include_cached_val=$(printf '%s\n' "$api_block" | grep 'VAULT_SWEEP_INCLUDE_CACHED:' | head -1 | sed -e 's/^[[:space:]]*VAULT_SWEEP_INCLUDE_CACHED:[[:space:]]*//' -e 's/"//g')
assert_eq "true" "$sweep_include_cached_val" "vault-api: VAULT_SWEEP_INCLUDE_CACHED default (true, WP SWEEP-1) passes through"

# WP SWEEP-1 follow-up (ADR-0014 "Shipping an enabled nightly schedule", S3
# review finding + operator decision): VAULT_SCHEDULE_WINDOW and TZ are what
# make the sweep half of the pairing above actually run out of the box --
# without a window, VAULT_SWEEP_INCLUDE_CACHED=true has no sweep to apply
# itself to. Same precondition-then-value pattern as the keys above.
schedule_window_key_count=$(printf '%s\n' "$api_block" | grep -c 'VAULT_SCHEDULE_WINDOW:')
assert_eq "1" "$schedule_window_key_count" "vault-api: VAULT_SCHEDULE_WINDOW key is present exactly once in the rendered block (precondition; WP SWEEP-1 follow-up forwarding)"
schedule_window_val=$(printf '%s\n' "$api_block" | grep 'VAULT_SCHEDULE_WINDOW:' | head -1 | sed -e 's/^[[:space:]]*VAULT_SCHEDULE_WINDOW:[[:space:]]*//' -e 's/"//g')
assert_eq "03:00-07:00" "$schedule_window_val" "vault-api: VAULT_SCHEDULE_WINDOW default (03:00-07:00, WP SWEEP-1 follow-up) passes through"

tz_key_count=$(printf '%s\n' "$api_block" | grep -c '^[[:space:]]*TZ:')
assert_eq "1" "$tz_key_count" "vault-api: TZ key is present exactly once in the rendered block (precondition; WP SWEEP-1 follow-up forwarding)"
tz_val=$(printf '%s\n' "$api_block" | grep '^[[:space:]]*TZ:' | head -1 | sed -e 's/^[[:space:]]*TZ:[[:space:]]*//' -e 's/"//g')
assert_eq "UTC" "$tz_val" "vault-api: TZ default (UTC, WP SWEEP-1 follow-up) passes through -- deliberately NOT a guessed populated zone, see deploy/compose.yaml's own comment on this key"

step "3e-bis. An EXPLICITLY BLANK VAULT_SCHEDULE_WINDOW really disables the scheduler (review round 2, blocker R2-B1)"
say 'Measured directly during review round 2: with the colon form'
say '(${VAULT_SCHEDULE_WINDOW:-03:00-07:00}), a .env line reading'
say 'VAULT_SCHEDULE_WINDOW= (present, explicitly blank) still rendered the'
say 'default window -- there was no way to express "disabled" in .env at all,'
say 'while four shipped places claimed there was. Fixed by switching to the'
say 'no-colon form (${VAULT_SCHEDULE_WINDOW-03:00-07:00}), which only'
say 'substitutes for an UNSET variable. This step is the live proof: a second'
say '.env, identical to section 3'"'"'s except one explicit blank line, must'
say 'render an EMPTY value here, not the default -- the static pin in'
say 'api/tests/test_p1_compose_env_defaults.py (test_schedule_window_uses_the_'
say 'no_colon_substitution_form) checks the SYNTAX; this checks the actual'
say 'Compose substitution BEHAVIOUR it is supposed to produce.'
blank_window_env_file="$work/verify-blank-window.env"
cp "$env_file" "$blank_window_env_file"
printf 'VAULT_SCHEDULE_WINDOW=\n' >> "$blank_window_env_file"
run "docker compose --env-file '$blank_window_env_file' -f '$compose_file' -p '$PROJECT' --profile dns config"
rendered_blank_window=$(docker compose --env-file "$blank_window_env_file" -f "$compose_file" -p "$PROJECT" --profile dns config 2>/dev/null)
api_block_blank_window=$(printf '%s\n' "$rendered_blank_window" | awk '/^  vault-api:/{f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
blank_window_key_count=$(printf '%s\n' "$api_block_blank_window" | grep -c 'VAULT_SCHEDULE_WINDOW:')
assert_eq "1" "$blank_window_key_count" "vault-api: VAULT_SCHEDULE_WINDOW key is present exactly once in the rendered block against the blank-window .env (precondition)"
blank_window_val=$(printf '%s\n' "$api_block_blank_window" | grep 'VAULT_SCHEDULE_WINDOW:' | head -1 | sed -e 's/^[[:space:]]*VAULT_SCHEDULE_WINDOW:[[:space:]]*//' -e 's/"//g')
assert_eq "" "$blank_window_val" "vault-api: an explicitly blank VAULT_SCHEDULE_WINDOW= renders EMPTY, not the 03:00-07:00 default -- the scheduler-disable path documented in deploy/README.md and deploy/.env.example actually works"

step "3e-ter. vault-core's VAULT_UPSTREAM_RATE_WINDOW nested default resolves per case (WP TH-1b, ADR-0015)"
say 'The line is ${VAULT_UPSTREAM_RATE_WINDOW-${VAULT_SCHEDULE_WINDOW-03:00-07:00}}:'
say 'unset follows the schedule window (and its default), explicitly blank'
say 'stays blank (= cap around the clock). The static pins in'
say 'api/tests/test_p1_compose_env_defaults.py check the SYNTAX; this step'
say 'renders four .env variants (section 3'"'"'s .env plus the lines named) and'
say 'checks what Compose actually resolves. Same mechanics as 3e-bis. The'
say 'image itself sets VAULT_UPSTREAM_RATE_WINDOW= (core/Dockerfile), so only'
say 'a rendered value proves the compose line, not presence in the container.'
# Each case: <label>|<extra .env lines, \n-separated, may be empty>|<expected>
for rate_window_case in \
    'nothing set||03:00-07:00' \
    'VAULT_SCHEDULE_WINDOW= (blank)|VAULT_SCHEDULE_WINDOW=\n|' \
    'VAULT_SCHEDULE_WINDOW=01:00-02:00|VAULT_SCHEDULE_WINDOW=01:00-02:00\n|01:00-02:00' \
    'VAULT_UPSTREAM_RATE_WINDOW= (blank) with VAULT_SCHEDULE_WINDOW=01:00-02:00|VAULT_SCHEDULE_WINDOW=01:00-02:00\nVAULT_UPSTREAM_RATE_WINDOW=\n|'
do
    rw_label=${rate_window_case%%|*}
    rw_rest=${rate_window_case#*|}
    rw_lines=${rw_rest%%|*}
    rw_expected=${rw_rest#*|}
    rate_window_env_file="$work/verify-rate-window.env"
    cp "$env_file" "$rate_window_env_file"
    # shellcheck disable=SC2059 # the case table's \n escapes are the format
    printf "$rw_lines" >> "$rate_window_env_file"
    say "case: $rw_label"
    run "docker compose --env-file '$rate_window_env_file' -f '$compose_file' -p '$PROJECT' --profile dns config"
    rendered_rate_window=$(docker compose --env-file "$rate_window_env_file" -f "$compose_file" -p "$PROJECT" --profile dns config 2>/dev/null)
    core_block_rate_window=$(printf '%s\n' "$rendered_rate_window" | awk '/^  vault-core:/{f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
    rate_window_key_count=$(printf '%s\n' "$core_block_rate_window" | grep -c 'VAULT_UPSTREAM_RATE_WINDOW:')
    assert_eq "1" "$rate_window_key_count" "vault-core: VAULT_UPSTREAM_RATE_WINDOW key is present exactly once in the rendered block [$rw_label] (precondition)"
    rate_window_val=$(printf '%s\n' "$core_block_rate_window" | grep 'VAULT_UPSTREAM_RATE_WINDOW:' | head -1 | sed -e 's/^[[:space:]]*VAULT_UPSTREAM_RATE_WINDOW:[[:space:]]*//' -e 's/"//g')
    assert_eq "$rw_expected" "$rate_window_val" "vault-core: VAULT_UPSTREAM_RATE_WINDOW renders '$rw_expected' [$rw_label]"
done

# Packaging WP regression guard (docs/PROJECT_PLAN.md §7 Phase 5): these two
# keys existed in config.py well before they were ever forwarded in
# deploy/compose.yaml's vault-api `environment:` block -- LEARNINGS.md
# "Containers" names exactly this class of bug (a setting config.py reads is
# dead unless the service block forwards it by name). Same precondition-then-
# value pattern as VAULT_EVENT_LOG above: presence is checked BEFORE
# emptiness, or a deleted passthrough line would read as an unnoticed pass.
event_log_path_key_count=$(printf '%s\n' "$api_block" | grep -c 'VAULT_EVENT_LOG_PATH:')
manifest_oracle_key_count=$(printf '%s\n' "$api_block" | grep -c 'VAULT_MANIFEST_ORACLE:')
assert_eq "1" "$event_log_path_key_count" "vault-api: VAULT_EVENT_LOG_PATH key is present exactly once in the rendered block (precondition)"
assert_eq "1" "$manifest_oracle_key_count" "vault-api: VAULT_MANIFEST_ORACLE key is present exactly once in the rendered block (precondition)"

event_log_path_val=$(printf '%s\n' "$api_block" | grep 'VAULT_EVENT_LOG_PATH:' | head -1 | sed -e 's/^[[:space:]]*VAULT_EVENT_LOG_PATH:[[:space:]]*//' -e 's/"//g')
manifest_oracle_val=$(printf '%s\n' "$api_block" | grep 'VAULT_MANIFEST_ORACLE:' | head -1 | sed -e 's/^[[:space:]]*VAULT_MANIFEST_ORACLE:[[:space:]]*//' -e 's/"//g')
assert_eq "" "$event_log_path_val"    "vault-api: VAULT_EVENT_LOG_PATH renders empty (feature off) with the test .env's defaults"
assert_eq "" "$manifest_oracle_val"   "vault-api: VAULT_MANIFEST_ORACLE renders empty (oracle off) by default"

# B1 audit (Opus review, packaging-WP fix round): a complete pass over every
# env var api/vault_api/config.py reads found TEN MORE forwarded nowhere in
# deploy/compose.yaml -- VAULT_MANIFEST_ORACLE_URL/VAULT_MANIFEST_ORACLE_TIMEOUT
# are the two the reviewer named explicitly (.env.example told operators to
# set the URL for a privacy mitigation that could not work while unforwarded
# -- exactly the LEARNINGS.md "Containers" bug class this whole package
# exists to close, re-introduced by the package itself). The rest are the
# same gap found systematically. Same precondition-then-value pattern as
# above, looped rather than repeated ten times by hand -- `${pair%%:*}` /
# `${pair#*:}` split on the FIRST colon only (shortest match from the front,
# longest match from the back), which matters because the oracle URL's own
# value contains colons ("https://...") and must not be mis-split by them.
for pair in \
    "VAULT_MANIFEST_KEEP:3" \
    "VAULT_EVENT_SWEEP_INTERVAL_MINUTES:5" \
    "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES:60" \
    "VAULT_MISS_TRIGGER_MAX_PER_SWEEP:5" \
    "VAULT_BYPASS_WINDOW_DAYS:3" \
    "VAULT_CLIENT_STATS_KEEP:48" \
    "VAULT_EVENT_LOG_MAX_BYTES:67108864" \
    "VAULT_MANIFEST_ORACLE_URL:https://api.steamcmd.net/v1/info" \
    "VAULT_MANIFEST_ORACLE_TIMEOUT:10.0" \
    "VAULT_WEBHOOK_TIMEOUT_SECONDS:5.0" \
    "VAULT_SETTINGS_READONLY:false"
do
    key=${pair%%:*}
    expected=${pair#*:}
    count=$(printf '%s\n' "$api_block" | grep -c "${key}:")
    assert_eq "1" "$count" "vault-api: $key key is present exactly once in the rendered block (precondition for the value check below)"
    val=$(printf '%s\n' "$api_block" | grep "${key}:" | head -1 | sed -e "s/^[[:space:]]*${key}:[[:space:]]*//" -e 's/"//g')
    assert_eq "$expected" "$val" "vault-api: $key default ($expected) passes through"
done

# WP S-2 (ADR-0012): the runner split's own env-forwarding audit. Same
# precondition-then-value pattern as the B1 loop above, but now checking
# TWO service blocks per variable where the variable appears on both sides
# (api/tests/test_p1_compose_env_defaults.py's own extension, mirrored here
# for the Docker-dependent cross-check this script exists to provide).
# VAULT_PREFILL_MODE's "queue" expected value is a DELIBERATE divergence
# from vault_api/config.py's own built-in default ('subprocess') -- see
# deploy/compose.yaml's comment on that key and
# test_config_py_own_prefill_mode_default_is_still_subprocess in the Python
# test file for the other half of that divergence's pin.
step "3f. WP S-2 (ADR-0012): vault-runner service exists in the rendered config, on both required sides"
say 'A missing vault-runner: block fails every check in this step at the'
say 'precondition stage, before any value comparison -- this is also the named'
say 'check that dies if the whole service is ever dropped from compose.yaml.'
runner_block_nonempty=$([ -n "$runner_block" ] && echo yes || echo no)
assert_eq "yes" "$runner_block_nonempty" "vault-runner: service block is present in the rendered config"

for pair in \
    "VAULT_PREFILL_MODE:queue" \
    "VAULT_RUNNER_LEASE_TIMEOUT_SECONDS:30.0"
do
    key=${pair%%:*}
    expected=${pair#*:}
    count=$(printf '%s\n' "$api_block" | grep -c "${key}:")
    assert_eq "1" "$count" "vault-api: $key key is present exactly once in the rendered block (precondition)"
    val=$(printf '%s\n' "$api_block" | grep "${key}:" | head -1 | sed -e "s/^[[:space:]]*${key}:[[:space:]]*//" -e 's/"//g')
    assert_eq "$expected" "$val" "vault-api: $key default ($expected) passes through"
done

for pair in \
    "VAULT_LOG_LEVEL:INFO" \
    "VAULT_PREFILL_MODE:queue" \
    "VAULT_PREFILL_TIMEOUT_SECONDS:14400" \
    "VAULT_RUNNER_HEARTBEAT_SECONDS:5.0" \
    "VAULT_RUNNER_POLL_SECONDS:1.0"
do
    key=${pair%%:*}
    expected=${pair#*:}
    count=$(printf '%s\n' "$runner_block" | grep -c "${key}:")
    assert_eq "1" "$count" "vault-runner: $key key is present exactly once in the rendered block (precondition)"
    val=$(printf '%s\n' "$runner_block" | grep "${key}:" | head -1 | sed -e "s/^[[:space:]]*${key}:[[:space:]]*//" -e 's/"//g')
    assert_eq "$expected" "$val" "vault-runner: $key default ($expected) passes through"
done

# The other direction of the "both sides must agree" mutation bar: a
# hand-off with no runner listening is caught by vault-api's own
# VAULT_PREFILL_MODE presence check above; a runner with no hand-offs is
# caught by vault-runner's copy just above. Neither one alone would catch
# the OTHER service's line being dropped -- both loops are required.

step "3g. WP S-2: VAULT_API_KEY is never forwarded to vault-runner"
say 'ADR-0012 S2 / api/README.md "Queue mode": this process never serves HTTP'
say 'and never authenticates a request, so it has no legitimate use for the'
say 'LAN control-plane secret -- Settings.from_env(require_api_key=False) is'
say 'the code-side half of this; this checks the compose-side half, against'
say "the runner's own rendered block (which already includes everything"
say "between its service header and the next service/top-level key, not just"
say 'its environment: section -- the same awk extraction used for api_block/'
say 'core_block above).'
runner_apikey_key_count=$(printf '%s\n' "$runner_block" | grep -c 'VAULT_API_KEY:' || true)
assert_eq "0" "${runner_apikey_key_count:-0}" "vault-runner's rendered block does not set VAULT_API_KEY"

step "3h. WP S-2: the Config/ (Steam session) volume mounts on exactly one service, and it is vault-runner"
say 'ADR-0012 S5: the credential-bearing volume moved from vault-api to'
say 'vault-runner. First checked against the RAW file, anchored to an actual'
say 'mount-target SHAPE (a sequence item ending ":/opt/steamprefill/Config"),'
say 'not a bare substring match -- the same anchoring style as step 6h below,'
say 'for the same reason (a comment merely MENTIONING the path must not'
say 'satisfy this check) -- then cross-checked against the RENDERED config'
say '(long-form "target: ..." syntax) attributed to each service by name, so'
say 'a mount on the wrong service is caught even if the raw-file count alone'
say 'stays at 1 (e.g. moved to vault-core instead of vault-runner by mistake).'
config_mount_lines=$(grep -nE '^[[:space:]]*-[[:space:]]*[^[:space:]]*:/opt/steamprefill/Config(:|[[:space:]]*$)' "$compose_file" || true)
config_mount_count=$(printf '%s\n' "$config_mount_lines" | grep -c . || true)
assert_eq "1" "${config_mount_count:-0}" "exactly one service in compose.yaml mounts /opt/steamprefill/Config"
config_target_in_api=$(printf '%s\n' "$api_block" | grep -c 'target: /opt/steamprefill/Config' || true)
config_target_in_runner=$(printf '%s\n' "$runner_block" | grep -c 'target: /opt/steamprefill/Config' || true)
assert_eq "0" "${config_target_in_api:-0}" "vault-api's rendered block does NOT mount /opt/steamprefill/Config (moved out in WP S-2)"
assert_eq "1" "${config_target_in_runner:-0}" "vault-runner's rendered block mounts /opt/steamprefill/Config"

step "3i. WP S-2: the HOME (/opt/steamprefill/home) volume is now shared by BOTH vault-api and vault-runner"
say 'Not a leftover -- new reasoning for WP S-2 (see compose.yaml comment on'
say "this mount): SteamPrefill's manifest temp-cache lives under this"
say 'directory, SteamPrefill now writes it from vault-runner, and manifest'
say 'ingestion (still vault-api-side, ADR-0012 §1) can only read those files'
say 'back if both containers share the SAME volume at the SAME path. Dropping'
say 'this from either service silently breaks manifest ingestion for every'
say 'queue-mode prefill from then on -- see 6k below for the live,'
say 'cross-container proof (a file written from one side, read from the'
say 'other), not just this static config check.'
home_target_in_api=$(printf '%s\n' "$api_block" | grep -c 'target: /opt/steamprefill/home' || true)
home_target_in_runner=$(printf '%s\n' "$runner_block" | grep -c 'target: /opt/steamprefill/home' || true)
assert_eq "1" "${home_target_in_api:-0}" "vault-api's rendered block mounts /opt/steamprefill/home"
assert_eq "1" "${home_target_in_runner:-0}" "vault-runner's rendered block mounts /opt/steamprefill/home"

step "3i2. WP S-2 (review round 2, should-fix S3): vault-runner does NOT mount the depot cache volume"
say 'Deliberately absent -- neither prefill_runner.py nor prefill.py reads'
say 'VAULT_CACHE_ROOT (evidence in the compose.yaml comment on vault-api'
say 'above), and mounting it anyway would be a read-write hole onto the'
say 'ENTIRE served cache inside the one deliberately broad-egress container'
say '(ADR-0012) once EG-1 locks it down -- blast radius, not ownership, is'
say 'the argument. vault-api and vault-core keep this mount unchanged.'
cache_target_in_runner=$(printf '%s\n' "$runner_block" | grep -c 'target: /vault$' || true)
assert_eq "0" "${cache_target_in_runner:-0}" "vault-runner's rendered block does NOT mount /vault (the depot cache)"

step "3j. WP S-2: a stable container_name renders for vault-runner, scoped to THIS run's isolated project"
say 'Confirms the ${COMPOSE_PROJECT_NAME}-vault-runner expression (compose.yaml'
say "comment on this key) resolves to THIS script's own isolated project name"
say "($PROJECT), not a bare literal that would collide with a real deployment"
say 'sharing this host -- exactly the isolation this script promises in its own'
say 'header comment.'
assert_contains "$rendered_default" "container_name: $PROJECT-vault-runner" "rendered config's vault-runner container_name is project-scoped to $PROJECT"

step "3k. WP EG-1 (ADR-0011): the egress-lock networks render, with the configured subnet and masquerade disabled"
say 'The top-level networks: block (distinct from any SERVICE'"'"'s own 4-indent'
say 'networks: sub-key -- see this section'"'"'s own extraction comment) must'
say 'show vault-lan with masquerade OFF and vault-egress as internal:true with'
say 'the exact subnet deploy/proxy/tinyproxy.conf'"'"'s own Allow directive'
say 'names (api/tests/test_eg1_egress_lock.py has the static, no-Docker-needed'
say 'cross-file pin for that pairing; this is the Docker-rendered counterpart).'
assert_contains "$networks_block" 'com.docker.network.bridge.enable_ip_masquerade: "false"' "rendered networks: vault-lan disables IP masquerade"
assert_contains "$networks_block" "internal: true" "rendered networks: vault-egress is internal: true"
say ''
say 'WP DEPLOY-FIX-2: the subnet is ${VAULT_EGRESS_SUBNET:-172.30.238.0/24}. This'
say "run's env file sets $VERIFY_EGRESS_SUBNET, which must reach BOTH the network and"
say "vault-proxy's environment; a BLANK value must render the default (colon form)."
assert_contains "$networks_block" "subnet: $VERIFY_EGRESS_SUBNET" "rendered networks: vault-egress uses this run's VAULT_EGRESS_SUBNET"
assert_contains "$proxy_block" "VAULT_EGRESS_SUBNET: $VERIFY_EGRESS_SUBNET" "rendered vault-proxy environment carries the same VAULT_EGRESS_SUBNET"
blank_subnet_env_file="$work/verify-blank-subnet.env"
sed '/^VAULT_EGRESS_SUBNET=/d' "$env_file" > "$blank_subnet_env_file"
printf 'VAULT_EGRESS_SUBNET=\n' >> "$blank_subnet_env_file"
rendered_blank_subnet=$(docker compose --env-file "$blank_subnet_env_file" -f "$compose_file" -p "$PROJECT" config 2>/dev/null)
networks_block_blank_subnet=$(printf '%s\n' "$rendered_blank_subnet" | awk '/^networks:/{f=1;next} f && /^[A-Za-z]/{exit} f')
assert_contains "$networks_block_blank_subnet" "subnet: $DEFAULT_EGRESS_SUBNET" "a BLANK VAULT_EGRESS_SUBNET= renders the default $DEFAULT_EGRESS_SUBNET (colon form), not an empty subnet"

step "3l. WP EG-1: vault-api is attached to vault-lan + vault-egress ONLY -- never default"
say 'The mutation this package'"'"'s own bar names by name: reattaching vault-api'
say 'to a default-routed network must be caught, here, not just in'
say 'api/tests/test_eg1_egress_lock.py (which parses the raw file, never'
say 'through Docker -- this is the Docker-rendered cross-check of the same fact).'
api_networks=$(printf '%s\n' "$api_block" | awk '/^    networks:/{f=1;next} f && !/^      /{exit} f' | sed -e 's/^      //' -e 's/:.*$//' | sort | tr '\n' ' ' | sed 's/ $//')
say "    vault-api's rendered networks: $api_networks"
assert_eq "vault-egress vault-lan" "$api_networks" "vault-api is attached to exactly {vault-egress, vault-lan} -- not default"

step "3m. WP EG-1: vault-proxy exists, is dual-attached (vault-egress + default), and has no published ports"
proxy_block_nonempty=$([ -n "$proxy_block" ] && echo yes || echo no)
assert_eq "yes" "$proxy_block_nonempty" "vault-proxy: service block is present in the rendered config"
proxy_networks=$(printf '%s\n' "$proxy_block" | awk '/^    networks:/{f=1;next} f && !/^      /{exit} f' | sed -e 's/^      //' -e 's/:.*$//' | sort | tr '\n' ' ' | sed 's/ $//')
say "    vault-proxy's rendered networks: $proxy_networks"
assert_eq "default vault-egress" "$proxy_networks" "vault-proxy is attached to exactly {default, vault-egress}"
proxy_ports_count=$(printf '%s\n' "$proxy_block" | grep -c '^    ports:' || true)
assert_eq "0" "${proxy_ports_count:-0}" "vault-proxy publishes no ports at all"

step "3n. WP EG-1: vault-api's HTTP_PROXY/HTTPS_PROXY/NO_PROXY and VAULT_EGRESS_ALLOW render correctly"
say 'Unlike every ${VAR:-default} key elsewhere in this script'"'"'s section 3,'
say 'HTTP_PROXY/HTTPS_PROXY/NO_PROXY are deliberate LITERALS, not operator-'
say 'configurable -- checked as exact values, not defaults-that-could-be-'
say 'overridden.'
for pair in \
    "HTTP_PROXY:http://vault-proxy:8888" \
    "HTTPS_PROXY:http://vault-proxy:8888" \
    "NO_PROXY:127.0.0.1,localhost"
do
    key=${pair%%:*}
    expected=${pair#*:}
    count=$(printf '%s\n' "$api_block" | grep -c "^      ${key}:" || true)
    assert_eq "1" "${count:-0}" "vault-api: $key key is present exactly once in the rendered block (precondition)"
    val=$(printf '%s\n' "$api_block" | grep "^      ${key}:" | head -1 | sed -e "s/^[[:space:]]*${key}:[[:space:]]*//" -e 's/"//g')
    assert_eq "$expected" "$val" "vault-api: $key renders as the expected literal"
done
egress_allow_key_count=$(printf '%s\n' "$api_block" | grep -c 'VAULT_EGRESS_ALLOW:')
proxy_egress_allow_key_count=$(printf '%s\n' "$proxy_block" | grep -c 'VAULT_EGRESS_ALLOW:')
assert_eq "1" "$egress_allow_key_count" "vault-api: VAULT_EGRESS_ALLOW key is present exactly once in the rendered block (precondition)"
assert_eq "1" "$proxy_egress_allow_key_count" "vault-proxy: VAULT_EGRESS_ALLOW key is present exactly once in the rendered block (precondition)"
egress_allow_val=$(printf '%s\n' "$api_block" | grep 'VAULT_EGRESS_ALLOW:' | head -1 | sed -e 's/^[[:space:]]*VAULT_EGRESS_ALLOW:[[:space:]]*//' -e 's/"//g')
proxy_egress_allow_val=$(printf '%s\n' "$proxy_block" | grep 'VAULT_EGRESS_ALLOW:' | head -1 | sed -e 's/^[[:space:]]*VAULT_EGRESS_ALLOW:[[:space:]]*//' -e 's/"//g')
assert_eq "" "$egress_allow_val" "vault-api: VAULT_EGRESS_ALLOW defaults empty (the test .env above sets none)"
assert_eq "" "$proxy_egress_allow_val" "vault-proxy: VAULT_EGRESS_ALLOW defaults empty"

step "3o. WP EG-1: vault-core, vault-runner and vault-dns are UNCHANGED -- still on the plain default network"
say 'ADR-0011 states this explicitly as out of scope: vault-runner stays broad'
say '(its egress is Steam'"'"'s CM/CDN fleet, not enumerable) and vault-core keeps'
say 'its own, separate, already-narrower Host allowlist (ADR-0001 req 4).'
dns_block=$(printf '%s\n' "$rendered_default" | awk '/^  vault-dns:/{f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
for pair in "vault-core:$core_block" "vault-runner:$runner_block" "vault-dns:$dns_block"; do
    svc_name=${pair%%:*}
    svc_block=${pair#*:}
    nets=$(printf '%s\n' "$svc_block" | awk '/^    networks:/{f=1;next} f && !/^      /{exit} f' | sed -e 's/^      //' -e 's/:.*$//' | tr -d ' ')
    assert_eq "default" "$nets" "$svc_name is attached to exactly {default} -- unaffected by the egress lock"
done

step "3p. WP DEPLOY-FIX-3: the /vault mount renders as nocopy volume (VAULT_CACHE_PATH unset) or plain bind (set)"
say 'vault-api'"'"'s /vault mount carries nocopy so vault-core stays the only seeder'
say 'of a fresh named volume (docs/LEARNINGS.md, copy-up race). A bind mount'
say 'must NOT carry it: Docker 28 / Compose 2.38 pass the short-syntax string'
say 'to the daemon, which refuses "/path:/vault:rw,nocopy" with "field'
say 'VolumeOptions must not be specified". Checked on the Docker-rendered'
say 'config, in both modes, for both services that mount /vault.'
# vault_mount_entry <service block>: the one rendered long-syntax volume entry
# whose target is /vault (from its "      - type:" line to the next entry or
# the end of the service's volumes: list).
vault_mount_entry() {
    printf '%s\n' "$1" | awk '
        function flush() { if (hit) print e; e = ""; hit = 0 }
        /^    volumes:/ { inv = 1; next }
        inv && !/^      / { flush(); inv = 0 }
        inv && /^      - / { flush(); e = $0; next }
        inv { e = e "\n" $0; if ($0 ~ /^        target: \/vault$/) hit = 1 }
        END { flush() }'
}
# The bind dir section 9 really mounts; for this render it need not exist yet.
bind_dir="$work/cache-bind"
bind_env_file="$work/verify-bind.env"
cp "$env_file" "$bind_env_file"
printf 'VAULT_CACHE_PATH=%s\n' "$bind_dir" >> "$bind_env_file"
blank_cache_env_file="$work/verify-blank-cache-path.env"
cp "$env_file" "$blank_cache_env_file"
printf 'VAULT_CACHE_PATH=\n' >> "$blank_cache_env_file"
rendered_bind=$(docker compose --env-file "$bind_env_file" -f "$compose_file" -p "$PROJECT" --profile dns config 2>/dev/null)
rendered_blank_cache=$(docker compose --env-file "$blank_cache_env_file" -f "$compose_file" -p "$PROJECT" --profile dns config 2>/dev/null)
for mode in named blank bind; do
    case "$mode" in
        named) mode_render=$rendered_default ;;
        blank) mode_render=$rendered_blank_cache ;;
        bind)  mode_render=$rendered_bind ;;
    esac
    for svc_name in vault-core vault-api; do
        svc_block=$(printf '%s\n' "$mode_render" | awk -v s="  $svc_name:" '$0 == s {f=1;next} f && (/^  [A-Za-z0-9_-]+:/ || /^[A-Za-z]/){exit} f')
        entry=$(vault_mount_entry "$svc_block")
        say ''
        say "    $svc_name /vault mount, $mode mode:"
        printf '%s\n' "${entry:-<none>}" | sed 's/^/      /'
        if [ "$mode" = bind ]; then
            assert_contains "$entry" "type: bind" "$svc_name ($mode): /vault is a bind mount"
            assert_contains "$entry" "source: $bind_dir" "$svc_name ($mode): /vault binds VAULT_CACHE_PATH"
            assert_not_contains "$entry" "nocopy" "$svc_name ($mode): the bind mount carries no nocopy"
            assert_not_contains "$entry" "volume:" "$svc_name ($mode): the bind mount carries no volume options at all"
        else
            assert_contains "$entry" "type: volume" "$svc_name ($mode): /vault is a named volume"
            assert_contains "$entry" "source: vault-cache" "$svc_name ($mode): /vault is the vault-cache volume"
            if [ "$svc_name" = vault-api ]; then
                assert_contains "$entry" "nocopy: true" "$svc_name ($mode): nocopy is kept (vault-core stays the only seeder)"
            else
                assert_not_contains "$entry" "nocopy" "$svc_name ($mode): no nocopy (it IS the seeder)"
            fi
        fi
    done
done
say ''
say 'Rendered config, unset vs. VAULT_CACHE_PATH set (diff -u):'
printf '%s\n' "$rendered_default" > "$work/render-named.yaml"
printf '%s\n' "$rendered_bind" > "$work/render-bind.yaml"
diff -u "$work/render-named.yaml" "$work/render-bind.yaml" | sed 's/^/    /'
if [ "$rendered_default" = "$rendered_blank_cache" ]; then
    ok "a BLANK VAULT_CACHE_PATH= renders byte-identically to an unset one"
else
    bad "a BLANK VAULT_CACHE_PATH= renders differently from an unset one"
fi

# =============================================================================
section "4. Stack up (vault-core + vault-api + vault-proxy + vault-runner)"
# =============================================================================
compose_up_or_die "$env_file" up -d

say ''
say 'Waiting for both healthchecks to report healthy...'
i=0
while [ "$i" -lt 60 ]; do
    core_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-core)" 2>/dev/null || echo starting)
    api_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-api)" 2>/dev/null || echo starting)
    [ "$core_h" = "healthy" ] && [ "$api_h" = "healthy" ] && break
    i=$((i + 1))
    sleep 2
done
say "vault-core health: $core_h    vault-api health: $api_h"
assert_eq "healthy" "$core_h" "vault-core container healthcheck"
assert_eq "healthy" "$api_h"  "vault-api container healthcheck"

step "3.5-runner. WP S-2: vault-runner comes up clean once vault-api is healthy (the depends_on ordering fix)"
say 'vault-runner has NO container HEALTHCHECK (deploy/compose.yaml explains'
say 'why: it never serves HTTP, so the .State.Health field this script uses'
say 'for the other two services does not exist here at all) -- readiness is'
say '.State.Status == running PLUS zero restarts, not a health status string.'
say ''
say 'This is also where an EMPIRICAL finding from writing this package lives:'
say 'on a genuinely fresh vault-db volume, without depends_on, vault-runner'
say "would race vault-api's schema creation (init_db, called from create_app()"
say 'before uvicorn ever binds its port) and crash on its very first poll tick'
say '("no such table: jobs") -- self-healing via restart: unless-stopped, but'
say 'a needless crash-and-traceback on every first-ever start. The'
say "depends_on: vault-api: condition: service_healthy fix removes the race"
say 'entirely (vault-api cannot report healthy before init_db has already run,'
say 'by construction) -- this step is what would catch a regression of that'
say 'fix (RestartCount would climb above 0 again).'
i=0
while [ "$i" -lt 30 ]; do
    runner_status=$(docker inspect --format '{{.State.Status}}' "$(dc ps -q vault-runner)" 2>/dev/null || echo missing)
    [ "$runner_status" = "running" ] && break
    i=$((i + 1))
    sleep 2
done
say "vault-runner status: $runner_status"
assert_eq "running" "$runner_status" "vault-runner container is running"
runner_restarts=$(docker inspect --format '{{.RestartCount}}' "$(dc ps -q vault-runner)" 2>/dev/null || echo -1)
say "vault-runner restart count: $runner_restarts"
assert_eq "0" "$runner_restarts" "vault-runner started clean with zero restarts (no schema-creation race)"

step "4a. vault-core boot log (preflight output)"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' logs vault-core"

step "4b. Container users and the shared cache volume"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-core sh -c 'ps -o user,args | head -4; echo; stat -c \"%n %U:%G %a\" /vault /vault/cache /vault/cache/depot /vault/tmp'"
say 'vault-api no longer mounts /opt/steamprefill/Config as of WP S-2 (moved to'
say 'vault-runner, ADR-0012 §5) -- stat-ing it here would just prove a negative'
say "removal, so this now checks vault-api's remaining mounts (/vault/cache,"
say '/data, and the now-SHARED /opt/steamprefill/home) plus vault-runner'
say "OWN mounts, Config/ included, in the SAME step so the split is visible"
say 'side by side. vault-runner is deliberately NOT stat-ed on /vault/cache'
say '(review round 2, should-fix S3): that mount was removed from this'
say 'service on purpose -- see deploy/compose.yaml'"'"'s comment on its'
say 'volumes: for the blast-radius argument (a read-write mount of the'
say 'entire served cache inside the one deliberately broad-egress container'
say 'is a strictly worse posture than the diagnostic convenience it bought).'
say 'Step 6k below asserts the ABSENCE explicitly; this step only exercises'
say "what vault-runner actually has."
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'id; stat -c \"%n %u:%g\" /vault/cache /data /opt/steamprefill/home'"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-runner sh -c 'id; stat -c \"%n %u:%g\" /data /opt/steamprefill/Config /opt/steamprefill/home'"

step "4c. SEC-FIX-1 (S-1): vault-core holds exactly its documented capability set"
say 'compose.yaml drops ALL capabilities for vault-core and adds back'
say 'NET_BIND_SERVICE, SETUID, SETGID, CHOWN and DAC_OVERRIDE (reasons in the'
say 'comment there). Measured from the kernel, not from the compose file:'
say 'CapEff/CapBnd of PID 1 (the nginx master, root) must be exactly that set'
say '(bits 10, 7, 6, 0, 1 = 0x4c3), and a worker (uid 101) must hold nothing.'
say 'The stack reaching healthy in step 4 already shows the set is enough for'
say 'a default boot; step 5i repeats the boot with the event log ON.'
run "docker inspect --format 'CapDrop={{.HostConfig.CapDrop}} CapAdd={{.HostConfig.CapAdd}}' \$(docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' ps -q vault-core)"
core_pid1=$(dc exec -T vault-core sh -c 'tr "\0" " " < /proc/1/cmdline; echo; grep -E "^Cap(Eff|Bnd):" /proc/1/status' 2>&1)
printf '%s\n' "$core_pid1" | sed 's/^/    /'
assert_contains "$core_pid1" "nginx: master" "vault-core PID 1 is the nginx master"
core_capeff=$(printf '%s\n' "$core_pid1" | awk '$1 == "CapEff:" { print $2 }')
core_capbnd=$(printf '%s\n' "$core_pid1" | awk '$1 == "CapBnd:" { print $2 }')
assert_eq "00000000000004c3" "$core_capeff" "vault-core master CapEff = NET_BIND_SERVICE+SETUID+SETGID+CHOWN+DAC_OVERRIDE"
assert_eq "00000000000004c3" "$core_capbnd" "vault-core master CapBnd = the same set (cap_drop ALL took effect)"
core_worker=$(dc exec -T vault-core sh -c 'for d in /proc/[0-9]*; do if tr "\0" " " < "$d/cmdline" 2>/dev/null | grep -q "^nginx: worker"; then awk "\$1 == \"Uid:\" || \$1 == \"CapEff:\"" "$d/status"; break; fi; done' 2>&1)
printf '%s\n' "$core_worker" | sed 's/^/    /'
assert_eq "0000000000000000" "$(printf '%s\n' "$core_worker" | awk '$1 == "CapEff:" { print $2 }')" "vault-core worker CapEff is empty"
assert_eq "101" "$(printf '%s\n' "$core_worker" | awk '$1 == "Uid:" { print $3 }')" "vault-core worker runs as uid 101"

# =============================================================================
section "5. vault-core behaviour"
# =============================================================================
step "5a. /health"
health=$(curl -s --max-time 10 "$CORE_URL/health")
say "    $health"
assert_eq "ok" "$(printf '%s' "$health" | tr -d '\n')" "GET /health returns ok"

step "5b. LanCache heartbeat (ADR-0001 req 1 -- SteamPrefill refuses to prefill without it)"
hb=$(curl -s -D - -o /dev/null --max-time 10 "$CORE_URL/lancache-heartbeat")
# printf '%s\n', not '%s': command substitution strips the trailing newline, and
# without restoring it the next PASS line gets appended to the last header line
# instead of starting its own -- which makes `grep -c '^PASS'` under-count the
# results by one against the summary. (Found reviewing the first recorded run.)
printf '%s\n' "$hb" | sed 's/^/    /'
assert_contains "$hb" "X-LanCache-Processed-By: steamhangar" "heartbeat carries X-LanCache-Processed-By"

step "5c. Temp paths are not web-reachable (WP 1.1 S3 fix)"
tmp_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$CORE_URL/tmp/proxy/anything")
assert_eq "404" "$tmp_code" "GET /tmp/proxy/... returns 404"

step "5d. Host allowlist (ADR-0001 req 4 -- no open proxy)"
forged=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: evil.example.com' "$CORE_URL$DEPOT_URI")
assert_eq "403" "$forged" "a forged non-Steam Host is refused on the miss path"
# SEC-FIX-1 (U-1): the allowlist is a full match now, not a suffix match.
# nginx itself answers 400 for "?" in a Host; "%" reaches the allowlist.
# The full delimiter matrix, with the no-upstream-attempt check, runs in
# .github/scripts/verify-core-nginx.sh.
forged_q=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: 127.0.0.1?x.steamcontent.com' "$CORE_URL$DEPOT_URI")
case "$forged_q" in
    400|403) ok "a Host carrying '?' before a Steam suffix is refused locally (= $forged_q)" ;;
    *)       bad "a Host carrying '?' before a Steam suffix -- expected 400 or 403, got '$forged_q'" ;;
esac
forged_pct=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: 127.0.0.1%3fx.steamcontent.com' "$CORE_URL$DEPOT_URI")
assert_eq "403" "$forged_pct" "a Host carrying '%' before a Steam suffix is refused by the allowlist"

step "5e. REAL Steam CDN cache test: MISS -> stored in the volume -> HIT"
say "object: $DEPOT_URI  (Host: $CDN_HOST)"
say ''
miss=$(curl -s -o "$work/miss.bin" -w 'http=%{http_code} bytes=%{size_download} seconds=%{time_total}' \
        --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI")
say "    MISS  $miss"
miss_code=$(printf '%s' "$miss" | sed -n 's/.*http=\([0-9]*\).*/\1/p')
assert_eq "200" "$miss_code" "cold request returns 200 from the real Steam CDN"

sleep 1
stored=$(dc exec -T vault-core sh -c "ls -l /vault/cache/depot/$DEPOT/chunk/$CHUNK 2>&1")
say "    stored: $stored"
assert_not_contains "$stored" "No such file" "the response was proxy_store'd into the volume at the path-faithful location"

hit=$(curl -s -o "$work/hit.bin" -w 'http=%{http_code} bytes=%{size_download} seconds=%{time_total}' \
        --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI")
say "    HIT   $hit"
hit_code=$(printf '%s' "$hit" | sed -n 's/.*http=\([0-9]*\).*/\1/p')
assert_eq "200" "$hit_code" "warm request returns 200"

miss_sha=$(sha256sum "$work/miss.bin" | cut -d' ' -f1)
hit_sha=$(sha256sum "$work/hit.bin" | cut -d' ' -f1)
disk_sha=$(dc exec -T vault-core sh -c "sha256sum /vault/cache/depot/$DEPOT/chunk/$CHUNK" | cut -d' ' -f1)
miss_size=$(stat -c %s "$work/miss.bin")
say ''
say "    sha256 MISS body : $miss_sha  ($miss_size bytes)"
say "    sha256 HIT  body : $hit_sha"
say "    sha256 on disk   : $disk_sha"
assert_eq "$miss_sha" "$hit_sha"  "MISS and HIT bodies are byte-identical"
assert_eq "$miss_sha" "$disk_sha" "the stored file is byte-identical to what the client received"

step "5f. ?nocache=1 bypass (ADR-0001 req 3 -- SteamPrefill's speed probe)"
nc_code=$(curl -s -o "$work/nocache.bin" -w '%{http_code}' --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI?nocache=1")
nc_sha=$(sha256sum "$work/nocache.bin" | cut -d' ' -f1)
assert_eq "200" "$nc_code" "?nocache=1 request succeeds"
assert_eq "$miss_sha" "$nc_sha" "?nocache=1 returns the same bytes (refreshed, not corrupted)"

step "5g. Access log: the vault log format reaches docker logs unchanged"
say 'Log rotation is the json-file driver (max-size/max-file in compose.yaml) --'
say 'this is what makes that possible: nginx writes to stdout, not to a file.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' logs --no-log-prefix vault-core | grep depot"
logline=$(dc logs --no-log-prefix vault-core 2>/dev/null | grep "$CHUNK" | head -1)
assert_contains "$logline" "cache=" "access log lines carry the vault format's cache= field"

step "5h. json-file log limits are actually applied to the container"
run "docker inspect --format '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}' \$(docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' ps -q vault-core vault-api)"
logcfg=$(docker inspect --format '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}' "$(dc ps -q vault-core)")
assert_contains "$logcfg" "json-file" "vault-core uses the json-file driver"
assert_contains "$logcfg" "max-size" "vault-core has a max-size limit"
assert_contains "$logcfg" "max-file" "vault-core has a max-file limit"

# =============================================================================
# 5i. Cache-event log (VAULT_EVENT_LOG), enabled ONLY for this check.
#
# Sections 1-5h deliberately ran with VAULT_EVENT_LOG unset (the shipped
# default, section 3e above), so they exercise what a plain `docker compose
# up` actually gets. This step turns the feature on for vault-core alone,
# removes the depot object's already-cached copy from 5e so the next request
# is a REAL cache miss (not a warm HIT replaying from disk), and checks the
# resulting line against core/README.md "Cache-event log"'s 9-field v1
# format -- then reverts vault-core to the feature-off default before
# section 8's fail-fast guards run.
#
# History (WP D1 -> packaging WP -> WP 4g): this step was written without a
# Docker host available, so for a while its container behaviour really was
# unverified. That gap is CLOSED. The packaging WP ran it against real
# containers three times (which is how the timing bug below was found, since
# it failed deterministically), and WP 4g's review re-ran the whole suite
# green: 109/109, exit 0, on Engine 29.1.3 / Compose 2.40.3, with the event
# line arriving after ~4s. The real MISS reaching event.log, the
# `docker compose up -d vault-core` recreate picking up the new env var and
# the healthcheck timing are all confirmed by real runs now -- do not read
# the paragraph below as an open caveat.
#
# WP 4g fix -- the timing bug (docs/LEARNINGS.md "Testing discipline",
# flush=5s entry): nginx buffers this access_log with `buffer=64k
# flush=5s` (core/nginx/nginx.conf), and grepping the file immediately
# after the triggering request is never raceless -- measured 0 lines at
# t~0s and t~2s, the correct 9-field line at t~7s, on an untouched
# baseline. Below, the grep is wrapped in a BOUNDED wait-for-line loop
# (poll once per second, up to 10s = the 5s flush plus scheduling slack)
# instead of a bare grep (raced 105/109 in the packaging-WP run) OR a flat
# `sleep 6`/`sleep 10`. A fixed sleep is not an acceptable fix even though
# it would make this step pass: on a slow or loaded host, `docker compose
# exec` alone can already exceed 5s, which is exactly why the un-fixed
# step could pass "by luck" today -- a sleep long enough to always clear
# that variance is not a bound anyone could justify, and a shorter one
# would silently keep the race, just with better odds. The loop instead
# turns "wait, then check" into a real assertion: if the line never shows
# up before the deadline, the four checks below fail with a message that
# says so explicitly (nothing arrived), which is a distinct failure class
# from "a line arrived but a field didn't match" (a parser/format
# regression) -- collapsing those two into the same blank-field failure,
# as the original code did, is exactly what made this bug take three
# review rounds to diagnose correctly. A green 5i now means the
# flush-and-read path worked within its documented budget, not that the
# host happened to be slow enough to win the race unassisted.
# =============================================================================
step "5i. Cache-event log: enable, force a fresh MISS, verify the v1 line, revert"
say 'VAULT_EVENT_LOG is off by default (core/README.md "Docker: VAULT_EVENT_LOG").'
say 'This step is the one place in this script that turns it on, to prove the'
say 'deploy/ wiring (compose.yaml passthrough + the shared /vault volume) actually'
say 'produces a usable line end to end, not just that the variable is plumbed.'

printf '\nVAULT_EVENT_LOG=/vault/logs/event.log\n' >> "$env_file"
compose_up_or_die "$env_file" up -d vault-core
i=0
while [ "$i" -lt 30 ]; do
    core_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-core)" 2>/dev/null || echo starting)
    [ "$core_h" = "healthy" ] && break
    i=$((i + 1)); sleep 2
done
assert_eq "healthy" "$core_h" "vault-core is healthy again after enabling VAULT_EVENT_LOG"

say ''
say 'Removing the object 5e already cached, so the next request is a genuine MISS'
say 'against the real Steam CDN, not a HIT replayed from local disk.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-core sh -c 'rm -f /vault/cache/depot/$DEPOT/chunk/$CHUNK'"

evlog_miss=$(curl -s -o /dev/null -w 'http=%{http_code}' --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI")
say "    MISS (event-log check)  $evlog_miss"
assert_contains "$evlog_miss" "http=200" "the forced fresh MISS still succeeds against the real Steam CDN"

say ''
say 'Waiting (bounded) for the flush: nginx writes this access_log with'
say 'buffer=64k flush=5s, so the line lands up to 5s after the request --'
say 'one short line will never fill 64k, so the 5s timer is what actually'
say 'governs this. Polling once per second for up to 10s (5s flush plus'
say 'scheduling slack), not a flat sleep -- see the comment above this step'
say 'for why a fixed sleep would hide rather than fix the race.'
evline=""
waited=0
max_wait=10
while [ "$waited" -lt "$max_wait" ]; do
    evline=$(dc exec -T vault-core sh -c "grep '$CHUNK' /vault/logs/event.log 2>/dev/null | tail -1" | tr -d '\r')
    [ -n "$evline" ] && break
    waited=$((waited + 1))
    sleep 1
done

if [ -z "$evline" ]; then
    # Say what was actually observed, not what it probably means. The wait
    # predicate is `grep $CHUNK`, so a format regression that dropped or
    # shortened field 6 (the URI) writes a line this grep cannot match and
    # lands right here -- calling that "the write path is broken" would
    # misattribute it, which is the exact confusion WP 4g exists to remove.
    # The line count and tail below let a reader tell the two apart at a
    # glance: 0 lines means nothing was written, N lines means something was
    # written that does not carry this chunk id.
    ev_lines=$(dc exec -T vault-core sh -c "wc -l < /vault/logs/event.log 2>/dev/null || echo '?'" | tr -d '\r')
    say "    event-log line: (no line matching the chunk after waiting ${max_wait}s; event.log has ${ev_lines} line(s))"
    if [ "${ev_lines:-0}" != "0" ]; then
        dc exec -T vault-core sh -c "tail -3 /vault/logs/event.log 2>/dev/null" | tr -d '\r' | sed 's/^/      last: /' || true
    fi
    never_arrived="no event-log line matching this MISS's chunk id arrived within ${max_wait}s; event.log has ${ev_lines} line(s). 0 lines ==> nothing reached VAULT_EVENT_LOG (the write path). Non-zero ==> something was written but does not carry the chunk id, i.e. suspect the log_format, not the write path. A malformed-but-present matching line takes the other branch and reports expected-vs-got per field."
    bad "the event-log line has exactly 9 tab-separated fields (core/README.md format) -- $never_arrived"
    bad "field 1 is the v1 format version -- $never_arrived"
    bad "field 4 records MISS for this forced fresh fetch -- $never_arrived"
    bad "field 9 (HTTP status) is 200 -- $never_arrived"
else
    say "    event-log line (arrived after ${waited}s): $evline"
    field_count=$(printf '%s' "$evline" | awk -F'\t' '{print NF}')
    field1=$(printf '%s' "$evline" | awk -F'\t' '{print $1}')
    field4=$(printf '%s' "$evline" | awk -F'\t' '{print $4}')
    field9=$(printf '%s' "$evline" | awk -F'\t' '{print $9}')
    assert_eq "9" "$field_count"  "the event-log line has exactly 9 tab-separated fields (core/README.md format)"
    assert_eq "v1" "$field1"      "field 1 is the v1 format version"
    assert_eq "MISS" "$field4"    "field 4 records MISS for this forced fresh fetch"
    assert_eq "200" "$field9"     "field 9 (HTTP status) is 200"
fi

say ''
say 'Reverting: strip VAULT_EVENT_LOG back out of the test .env and recreate'
say 'vault-core so section 8'"'"'s fail-fast guards below run against the shipped'
say 'feature-off default, not this check'"'"'s override.'
sed -i '/^VAULT_EVENT_LOG=/d' "$env_file"
compose_up_or_die "$env_file" up -d vault-core
i=0
while [ "$i" -lt 30 ]; do
    core_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-core)" 2>/dev/null || echo starting)
    [ "$core_h" = "healthy" ] && break
    i=$((i + 1)); sleep 2
done
assert_eq "healthy" "$core_h" "vault-core is healthy again after reverting VAULT_EVENT_LOG to the feature-off default"

# =============================================================================
section "6. vault-api behaviour"
# =============================================================================
step "6a. GET /v1/health (the one unauthenticated route, by design)"
apihealth=$(curl -s --max-time 10 "$API_URL/v1/health")
say "    $apihealth"
assert_contains "$apihealth" '"status":"ok"' "GET /v1/health returns status ok"

step "6b. Auth is enforced"
code_nokey=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API_URL/v1/games")
assert_eq "401" "$code_nokey" "GET /v1/games without X-Api-Key is 401"
code_wrong=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "X-Api-Key: wrong" "$API_URL/v1/games")
assert_eq "401" "$code_wrong" "GET /v1/games with a wrong key is 401"
code_docs=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API_URL/openapi.json")
assert_eq "404" "$code_docs" "/openapi.json is disabled"

step "6c. PUT/GET mapping round-trip through the container"
put=$(curl -s --max-time 10 -X PUT "$API_URL/v1/mapping/441" \
        -H "X-Api-Key: $TEST_API_KEY" -H 'Content-Type: application/json' \
        -d '{"appid": 440, "app_name": "Team Fortress 2"}')
say "    PUT  /v1/mapping/441 -> $put"
assert_contains "$put" '"depotid":441' "PUT /v1/mapping/441 accepted"

getmap=$(curl -s --max-time 10 -H "X-Api-Key: $TEST_API_KEY" "$API_URL/v1/mapping")
say "    GET  /v1/mapping     -> $getmap"
assert_contains "$getmap" '{"depotid":441,"appid":440}' "GET /v1/mapping returns the round-tripped pair"

games=$(curl -s --max-time 10 -H "X-Api-Key: $TEST_API_KEY" "$API_URL/v1/games")
say "    GET  /v1/games       -> $games"
assert_contains "$games" '"appid":440' "GET /v1/games shows the app created by the mapping"

step "6d. The API sees the SAME cache volume vault-core just wrote into"
say 'This is the shared-volume/uid contract: vault-core (uid 101) stored a real'
say 'depot chunk above; vault-api (also uid 101) must be able to size it.'
summary=$(curl -s --max-time 20 -H "X-Api-Key: $TEST_API_KEY" "$API_URL/v1/cache/summary")
say "    GET  /v1/cache/summary -> $summary"
unmapped=$(printf '%s' "$summary" | sed -n 's/.*"unmapped_depots":{"count":\([0-9]*\).*/\1/p')
assert_eq "1" "$unmapped" "vault-api sees exactly 1 unmapped depot on disk (the $DEPOT chunk vault-core cached)"
assert_not_contains "$summary" '"total_bytes":0' "vault-api reports non-zero bytes for the shared cache volume"

step "6e. The database landed on its own volume"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'ls -l /data'"

step "6f. SteamPrefill on the other two invocation paths (still credential-free)"
say 'The blocker was reproduced on three paths and they derive HOME differently,'
say 'so all three are guarded. Step 2.smoke covered plain `docker run`; these two'
say 'are the DOCUMENTED login command and the exec-into-a-running-container case.'
say ''
say 'Again: no credentials are entered. Reaching the username prompt IS the pass.'

sp_run=$(dc run --rm --no-deps -T vault-api \
           /opt/steamprefill/SteamPrefill select-apps < /dev/null 2>&1 | strip_ansi | head -8)
say ''
say '    $ docker compose run --rm --no-deps vault-api /opt/steamprefill/SteamPrefill select-apps'
printf '%s\n' "$sp_run" | sed 's/^/    /'
assert_not_contains "$sp_run" "TypeInitializationException" "compose run: no TypeInitializationException"
assert_contains     "$sp_run" "account is required in order to prefill apps" "compose run (the documented login flow) reaches SteamPrefill's login logic"

sp_exec=$(dc exec -T vault-api \
            /opt/steamprefill/SteamPrefill select-apps < /dev/null 2>&1 | strip_ansi | head -8)
say ''
say '    $ docker compose exec vault-api /opt/steamprefill/SteamPrefill select-apps'
printf '%s\n' "$sp_exec" | sed 's/^/    /'
assert_not_contains "$sp_exec" "TypeInitializationException" "compose exec: no TypeInitializationException"
assert_contains     "$sp_exec" "account is required in order to prefill apps" "compose exec reaches SteamPrefill's login logic"

step "6g. What SteamPrefill actually wrote under HOME (now persistent)"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'find /opt/steamprefill/home -maxdepth 3 | head -20; echo; stat -c \"%n %u:%g\" /opt/steamprefill/home'"

step "6h. Web UI served from the IMAGE, over the running container, no bind mount involved (packaging WP)"
say 'Section 2.web already proved the files exist inside the image; this proves'
say 'GET / actually serves them through a real running container -- and that no'
say 'bind mount is doing the work behind the scenes, which would make 2.web look'
say 'load-bearing when it was not: this Compose project (the checked-in'
say "deploy/compose.yaml, no override file) never mounts anything over /app/web."
# Anchored to an actual mount-target SHAPE (a sequence item ending
# ":/app/web"), not a bare substring match -- a plain 'grep /app/web' would
# also fire on a comment merely MENTIONING the path (this file has several,
# e.g. the say lines just above), which would make this check pass for the
# wrong reason forever (reviewer nitpick).
webmount=$(grep -nE '^\s*-\s*\S*:/app/web(:|[[:space:]]*$)' "$compose_file" || true)
assert_eq "" "$webmount" "compose.yaml has no bind mount over /app/web -- a 200 below can only come from the image's own COPY"
webroot_code=$(curl -s -o "$work/webroot.html" -w '%{http_code}' --max-time 10 "$API_URL/")
say "    GET / -> http $webroot_code"
assert_eq "200" "$webroot_code" "GET / returns 200"
webroot_body=$(cat "$work/webroot.html")
assert_contains "$webroot_body" "<title>SteamHangar</title>" "GET / body is the real app shell (title tag), not a generic 404 page"
assert_contains "$webroot_body" 'id="app"' "GET / body contains the app shell's root element"

step "6i. Env-forwarding regression guard: VAULT_EVENT_LOG_PATH, VAULT_MANIFEST_ORACLE and VAULT_SETTINGS_READONLY actually reach vault-api's process environment"
say 'Section 3e already proved these render into `docker compose config` output;'
say 'this is the stronger claim -- that the value genuinely lands in the RUNNING'
say "container's environment, not merely in the rendered YAML (a passthrough"
say 'line can exist in compose.yaml and still not reach the process if, say, the'
say 'key were misspelled on one side). `printenv NAME` exits 0 iff NAME is a'
say 'defined variable, even when its value is empty -- exactly what is being'
say 'proved here, since the test .env leaves all three unset.'
say 'VAULT_SETTINGS_READONLY (Fable-audit follow-up: a security lock that fails'
say 'open if the forwarding line is ever removed) is checked the same way --'
say 'api/tests/test_p1_compose_env_defaults.py already pins the rendered'
say "compose.yaml text; this proves the container's actual process environment,"
say 'which a misspelled key on either side could still defeat even with that'
say 'text-level pin green.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'printenv VAULT_EVENT_LOG_PATH; echo \"exit=\$?\"; printenv VAULT_MANIFEST_ORACLE; echo \"exit=\$?\"; printenv VAULT_SETTINGS_READONLY; echo \"exit=\$?\"'"
evpath_defined=$(dc exec -T vault-api sh -c 'printenv VAULT_EVENT_LOG_PATH >/dev/null 2>&1; echo "exit=$?"')
oracle_defined=$(dc exec -T vault-api sh -c 'printenv VAULT_MANIFEST_ORACLE >/dev/null 2>&1; echo "exit=$?"')
settings_readonly_defined=$(dc exec -T vault-api sh -c 'printenv VAULT_SETTINGS_READONLY >/dev/null 2>&1; echo "exit=$?"')
assert_contains "$evpath_defined" "exit=0" "VAULT_EVENT_LOG_PATH is a defined env var inside the running vault-api container"
assert_contains "$oracle_defined" "exit=0" "VAULT_MANIFEST_ORACLE is a defined env var inside the running vault-api container"
assert_contains "$settings_readonly_defined" "exit=0" "VAULT_SETTINGS_READONLY is a defined env var inside the running vault-api container"

step "6i-core. Env-forwarding guard (WP TH-1b): TZ and VAULT_UPSTREAM_RATE_WINDOW reach vault-core's process environment WITH their compose values"
say 'VALUES, not presence: core/Dockerfile sets VAULT_UPSTREAM_RATE= and'
say 'VAULT_UPSTREAM_RATE_WINDOW= as image ENV, so a printenv exit code would'
say 'pass even with the compose lines deleted. The test .env sets neither TZ'
say 'nor either window variable, so the compose defaults must arrive: TZ=UTC'
say '(the image sets no TZ) and the window 03:00-07:00 (the image default is'
say 'blank). VAULT_UPSTREAM_RATE is not checked here: its compose default and'
say 'the image ENV are both blank, so no value can tell them apart; 3e-ter and'
say 'the static pins cover its line.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-core sh -c 'printenv TZ; echo \"exit=\$?\"; printenv VAULT_UPSTREAM_RATE_WINDOW; echo \"exit=\$?\"'"
core_tz_val=$(dc exec -T vault-core printenv TZ 2>/dev/null | tr -d '\r')
core_rate_window_val=$(dc exec -T vault-core printenv VAULT_UPSTREAM_RATE_WINDOW 2>/dev/null | tr -d '\r')
assert_eq "UTC" "$core_tz_val" "vault-core: TZ inside the running container is the compose default"
assert_eq "03:00-07:00" "$core_rate_window_val" "vault-core: VAULT_UPSTREAM_RATE_WINDOW inside the running container is the compose default (follows VAULT_SCHEDULE_WINDOW's default), not the image's blank ENV"

step "6j. Regression guard: /v1/health and an authed route still behave after the build-context change"
say '6a/6b above already exercise these for auth-contract reasons; restated'
say 'explicitly here because the build-context move (api/ -> repo root,'
say 'packaging WP) is exactly the kind of change that could silently break'
say 'something else while still building and starting cleanly -- a wrong COPY'
say 'source path does not stop Python from importing vault_api, so the signal'
say 'has to come from these routes actually answering, not from `docker build`'
say 'exiting 0.'
regcheck_health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API_URL/v1/health")
assert_eq "200" "$regcheck_health" "GET /v1/health still returns 200 after the build-context change"
regcheck_games=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "X-Api-Key: $TEST_API_KEY" "$API_URL/v1/games")
assert_eq "200" "$regcheck_games" "GET /v1/games (authed) still returns 200 after the build-context change"

step "6k. vault-runner (WP S-2, ADR-0012): the empirical runner-split checks"
say 'Everything above this step in section 6 predates the runner split and'
say 'still exercises vault-api only, unchanged. This step is the new'
say "service's own behavioural evidence -- container identity, the secret it"
say "must NOT have, the env forwarding it DOES have, the poll-loop log line"
say 'that is its liveness proof (it has no HTTP healthcheck, see step'
say '3.5-runner above), and a REAL cross-container file write proving the'
say 'shared HOME volume (step 3i) actually does what its comment claims,'
say 'not just that the mount is declared.'

say ''
say '--- container identity: the stable, project-scoped container_name ---'
runner_cid=$(dc ps -q vault-runner)
runner_name=$(docker inspect --format '{{.Name}}' "$runner_cid" | sed 's#^/##')
say "    container_name (live): $runner_name"
assert_eq "$PROJECT-vault-runner" "$runner_name" "the running container's actual name matches the stable, project-scoped container_name"

say ''
say '--- S3 (review round 2): the depot cache volume is NOT mounted on the running container ---'
say 'Checked via `docker inspect`'"'"'s real Mounts list, not `stat` -- the'
say 'image itself pre-creates /vault/cache/depot at build time (api/Dockerfile,'
say 'for the OTHER service that DOES need it), so a bare `stat /vault/cache`'
say 'would succeed on vault-runner too even with no volume mounted there,'
say 'proving nothing. Mounts is the ground truth for whether a NAMED VOLUME'
say 'is actually attached at that path.'
runner_vault_mount=$(docker inspect --format '{{range .Mounts}}{{.Destination}} {{end}}' "$runner_cid" | tr ' ' '\n' | grep -c '^/vault$' || true)
say "    mount destinations: $(docker inspect --format '{{range .Mounts}}{{.Destination}} {{end}}' "$runner_cid")"
assert_eq "0" "${runner_vault_mount:-0}" "the running vault-runner container has no /vault mount (depot cache excluded, per S3's blast-radius argument)"

say ''
say '--- liveness proof: no HTTP surface to probe, so the poll-tick log line is the evidence ---'
runner_logs=$(dc logs --no-log-prefix vault-runner 2>/dev/null)
say "$runner_logs" | sed 's/^/    /'
assert_contains "$runner_logs" "starting (poll every" "vault-runner logged its startup/poll-loop line"
assert_contains "$runner_logs" "SteamPrefill path '/opt/steamprefill/SteamPrefill'" "the logged startup line names the real SteamPrefill path (not an empty/misconfigured one)"

say ''
say '--- env forwarding: VAULT_PREFILL_MODE reaches BOTH processes; VAULT_API_KEY reaches NEITHER runner ---'
say 'Same printenv-exit-code pattern as step 6i above (present-even-if-empty'
say 'is exit 0; absent is exit 1) -- proving the RUNNING container'
say "environment, not just the rendered YAML text steps 3f/3g already pinned."
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-runner sh -c 'printenv VAULT_PREFILL_MODE; echo \"exit=\$?\"; printenv VAULT_API_KEY >/dev/null 2>&1; echo \"exit=\$?\"'"
runner_prefillmode_defined=$(dc exec -T vault-runner sh -c 'printenv VAULT_PREFILL_MODE >/dev/null 2>&1; echo "exit=$?"')
runner_apikey_defined=$(dc exec -T vault-runner sh -c 'printenv VAULT_API_KEY >/dev/null 2>&1; echo "exit=$?"')
api_prefillmode_defined=$(dc exec -T vault-api sh -c 'printenv VAULT_PREFILL_MODE >/dev/null 2>&1; echo "exit=$?"')
assert_contains "$runner_prefillmode_defined" "exit=0" "VAULT_PREFILL_MODE is a defined env var inside the running vault-runner container"
assert_contains "$api_prefillmode_defined" "exit=0" "VAULT_PREFILL_MODE is a defined env var inside the running vault-api container (both sides, per compose.yaml's comment)"
assert_not_contains "$runner_apikey_defined" "exit=0" "VAULT_API_KEY is NOT a defined env var inside the running vault-runner container (require_api_key=False has nothing to read)"

say ''
say '--- the shared HOME volume, proven live: a file written from vault-runner is visible from vault-api ---'
say 'This is the step 3i config-level mount check turned into a real filesystem'
say 'proof -- the actual failure this package is guarding against'
say '(manifest_ingest.py silently finding nothing to ingest) would NOT be'
say 'caught by the mount merely being declared on both services if, say, one'
say 'side pointed at a DIFFERENT volume of the same name in a mistyped override.'
probe_name="wp-s2-verify-shared-home-$$"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-runner sh -c 'touch /opt/steamprefill/home/$probe_name'"
home_probe_seen=$(dc exec -T vault-api sh -c "ls /opt/steamprefill/home/ 2>/dev/null" | tr -d '\r')
assert_contains "$home_probe_seen" "$probe_name" "a file vault-runner wrote under /opt/steamprefill/home is visible from vault-api (the SAME volume, not two same-named ones)"
dc exec -T vault-runner sh -c "rm -f /opt/steamprefill/home/$probe_name" >/dev/null 2>&1 || true

say ''
say '--- the documented login path (deploy/README.md "First run") actually reaches SteamPrefill, credential-free ---'
say 'Same shape as step 6f above (which runs the binary directly via'
say '`compose run` regardless of VAULT_PREFILL_MODE, and still exercises the'
say "SteamPrefill-boots-cleanly regression it always did -- but 6f's Config/"
say 'directory is now just an ephemeral, non-persistent path inside'
say "vault-api's OWN writable layer, since that volume moved to vault-runner"
say 'in WP S-2; this step is the one that actually reaches a PERSISTENT'
say 'Config/ volume). Reaching the username prompt IS the pass here too; no'
say 'credentials are entered, here or ever in this script.'
sp_runner_exec=$(dc exec -T vault-runner \
            /opt/steamprefill/SteamPrefill select-apps < /dev/null 2>&1 | strip_ansi | head -8)
say ''
say '    $ docker exec <container_name> /opt/steamprefill/SteamPrefill select-apps'
printf '%s\n' "$sp_runner_exec" | sed 's/^/    /'
assert_not_contains "$sp_runner_exec" "TypeInitializationException" "vault-runner exec: no TypeInitializationException"
assert_not_contains "$sp_runner_exec" "UnauthorizedAccessException" "vault-runner exec: no UnauthorizedAccessException reaching for HOME"
assert_contains     "$sp_runner_exec" "account is required in order to prefill apps" "vault-runner exec reaches SteamPrefill's login logic -- this is the deploy/README.md-documented login container as of WP S-2"

say ''
say '--- stop_grace_period actually renders on the container, not only in compose.yaml text ---'
runner_stop_timeout=$(docker inspect --format '{{.Config.StopTimeout}}' "$runner_cid")
say "    StopTimeout: ${runner_stop_timeout}s"
assert_eq "20" "$runner_stop_timeout" "vault-runner's container has the 20s stop_grace_period applied (ADR-0012 §4 nitpick: teardown budget margin)"

step "6l. Egress lock (WP EG-1, ADR-0011): the empirical checks"
say 'Everything above proves the STATIC shape (section 3) or vault-runner'"'"'s'
say 'own split (6k). This step is the live behavioural evidence for the lock'
say 'itself: vault-proxy comes up healthy, an allowlisted request through it'
say 'succeeds, a non-allowlisted one is REFUSED BY THE PROXY (403 Filtered,'
say 'not a silent drop), a raw direct socket bypassing the proxy entirely'
say 'cannot reach an ARBITRARY address (the measurement behind the'
say 'NO_PROXY/LAN-webhook design decision, ADR-0011 §4 -- DNS resolution and'
say 'the Docker host'"'"'s own reachable addresses are a DIFFERENT, deliberately'
say 'open question, not tested here -- see docs/adr/0011-egress-lock.md'"'"'s'
say '"What this ADR does NOT claim to defend against"), and -- the mutation'
say 'bar this package was written against -- widening the allowlist and'
say 're-running the EXACT SAME denied-host probe flips the result, proving'
say 'the proxy checks in section 3 are not asserting a foregone conclusion.'
say ''
say 'The test .env from section 3 sets no VAULT_EGRESS_ALLOW at all (empty,'
say 'the shipped default) and no VAULT_MANIFEST_ORACLE either -- so every'
say 'check below that succeeds through the proxy without any allowlist entry'
say 'is exercising the ONE baked-in host, api.steampowered.com (the Steam Web'
say 'API relay), not an operator-configured one.'

say ''
say '--- vault-proxy healthcheck ---'
i=0
proxy_h=starting
while [ "$i" -lt 15 ]; do
    proxy_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-proxy)" 2>/dev/null || echo starting)
    [ "$proxy_h" = "healthy" ] && break
    i=$((i + 1))
    sleep 2
done
say "    vault-proxy health: $proxy_h"
assert_eq "healthy" "$proxy_h" "vault-proxy container healthcheck"

say ''
say '--- WP DEPLOY-FIX-2: the client allowlist follows VAULT_EGRESS_SUBNET, live ---'
say "This run uses $VERIFY_EGRESS_SUBNET, not the default. The real network must have"
say "that subnet, vault-proxy's rendered tinyproxy.conf must allow exactly it (plus"
say "loopback), and vault-api's vault-egress address must lie inside it."
egress_net="${PROJECT}_vault-egress"
lan_net="${PROJECT}_vault-lan"
# IPv4 entries only: a daemon that adds IPv6 by default would list a ULA /64
# here too (measured 2026-10-01). compose.yaml disables IPv6 on this network,
# which the IPv6 checks below assert separately.
egress_net_subnet=$(docker network inspect "$egress_net" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>&1 | tr ' ' '\n' | grep -v ':' | grep -v '^$' | tr '\n' ' ' | sed 's/ $//')
assert_eq "$VERIFY_EGRESS_SUBNET" "$egress_net_subnet" "network $egress_net has exactly this run's IPv4 subnet"
proxy_allow=$(dc exec -T vault-proxy sh -c "grep -Ei '^[[:space:]]*allow[[:space:]]' /run/tinyproxy/tinyproxy.conf" 2>&1 | tr -d '\r' | tr '\n' ';')
assert_eq "Allow 127.0.0.1;Allow $VERIFY_EGRESS_SUBNET;" "$proxy_allow" "vault-proxy's rendered tinyproxy.conf allows exactly loopback + VAULT_EGRESS_SUBNET"
api_egress_ip=$(docker inspect --format "{{with index .NetworkSettings.Networks \"$egress_net\"}}{{.IPAddress}}{{end}}" "$(dc ps -q vault-api)" 2>/dev/null)
api_ip_in_subnet=$(python3 -c 'import ipaddress, sys; print("inside" if ipaddress.ip_address(sys.argv[1]) in ipaddress.ip_network(sys.argv[2]) else "outside")' "$api_egress_ip" "$VERIFY_EGRESS_SUBNET" 2>&1)
say "    vault-api on $egress_net: ${api_egress_ip:-<none>}"
assert_eq "inside" "$api_ip_in_subnet" "vault-api's vault-egress address ($api_egress_ip) lies inside $VERIFY_EGRESS_SUBNET"

say ''
say '--- WP DEPLOY-FIX-2: no IPv6 on vault-lan / vault-egress ---'
say 'The IPv4 lock rests on masquerade being off on vault-lan; IPv6 has no NAT to'
say 'turn off. compose.yaml sets enable_ipv6: false on both lock networks, so'
say 'vault-api must have no IPv6 address on either, whatever the daemon default.'
for net in "$lan_net" "$egress_net"; do
    net_v6=$(docker network inspect "$net" --format '{{.EnableIPv6}}' 2>&1)
    assert_eq "false" "$net_v6" "network $net has IPv6 disabled"
    api_v6=$(docker inspect --format "{{with index .NetworkSettings.Networks \"$net\"}}v6=[{{.GlobalIPv6Address}}]{{end}}" "$(dc ps -q vault-api)" 2>&1)
    # "v6=[]" also proves vault-api IS attached there (empty output would not).
    assert_eq "v6=[]" "$api_v6" "vault-api is on $net with no IPv6 address"
done

say ''
say '--- env forwarding (round-2 review S4): HTTP_PROXY/HTTPS_PROXY/NO_PROXY actually reach the RUNNING vault-api process ---'
say 'Same printenv-exit-code pattern as step 6i above (present-even-if-empty'
say 'is exit 0; absent is exit 1) -- section 3n already proved these three'
say 'render into `docker compose config` output; this is the stronger claim'
say "that the value genuinely lands in the RUNNING container's environment,"
say 'not merely in the rendered YAML.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'printenv HTTP_PROXY; echo \"exit=\$?\"; printenv HTTPS_PROXY; echo \"exit=\$?\"; printenv NO_PROXY; echo \"exit=\$?\"'"
http_proxy_val=$(dc exec -T vault-api sh -c 'printenv HTTP_PROXY' | tr -d '\r')
https_proxy_val=$(dc exec -T vault-api sh -c 'printenv HTTPS_PROXY' | tr -d '\r')
no_proxy_val=$(dc exec -T vault-api sh -c 'printenv NO_PROXY' | tr -d '\r')
assert_eq "http://vault-proxy:8888" "$http_proxy_val" "HTTP_PROXY reaches the running vault-api process with the expected literal value"
assert_eq "http://vault-proxy:8888" "$https_proxy_val" "HTTPS_PROXY reaches the running vault-api process with the expected literal value"
assert_eq "127.0.0.1,localhost" "$no_proxy_val" "NO_PROXY reaches the running vault-api process with the expected literal value (loopback only)"

say ''
say '--- the baked-in host is reachable through the proxy with an EMPTY VAULT_EGRESS_ALLOW ---'
baked_probe=$(dc exec -T vault-api python3 <<'PYEOF'
import urllib.request
try:
    r = urllib.request.urlopen(
        "https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/", timeout=8
    )
    print("OK", r.status)
except Exception as exc:  # noqa: BLE001 - this IS the diagnostic
    print("FAIL", repr(exc))
PYEOF
)
say "    $baked_probe"
assert_contains "$baked_probe" "OK 200" "vault-api reaches api.steampowered.com through vault-proxy, no VAULT_EGRESS_ALLOW entry needed (the baked-in relay host)"

say ''
say '--- a NON-allowlisted host is refused BY THE PROXY (403 Filtered) -- the destination allowlist, actually enforced ---'
denied_probe=$(dc exec -T vault-api python3 <<'PYEOF'
import urllib.request
try:
    urllib.request.urlopen("http://example.com/", timeout=8)
    print("UNEXPECTED SUCCESS")
except OSError as exc:
    print("DENIED", repr(exc))
PYEOF
)
say "    $denied_probe"
assert_contains "$denied_probe" "DENIED" "a non-allowlisted host is refused, not silently allowed through"
assert_contains "$denied_probe" "403" "...specifically with a real 403 Filtered from tinyproxy itself, not a generic connection failure"

say ''
say '--- a raw direct socket, bypassing HTTP_PROXY entirely, reaches NO ARBITRARY DESTINATION -- the LAN-webhook design question, measured live ---'
say 'This is what settled ADR-0011 S4: vault-lan has no working direct route'
say 'to an ARBITRARY destination once masquerade is disabled, so there is no'
say '"skip the proxy for local traffic" case for a genuinely separate device'
say '-- a LAN target would fail exactly the same way a WAN one does here.'
say 'This does NOT test DNS resolution or the Docker host'"'"'s own address --'
say 'those are DIFFERENT, deliberately open channels, checked separately'
say 'right below rather than left untested and implicitly assumed closed.'
direct_probe=$(dc exec -T vault-api python3 <<'PYEOF'
import socket
import time

start = time.time()
try:
    socket.create_connection(("1.1.1.1", 443), timeout=6)
    print("UNEXPECTED: connected")
except OSError as exc:
    print("BLOCKED after %.1fs: %r" % (time.time() - start, exc))
PYEOF
)
say "    $direct_probe"
assert_contains "$direct_probe" "BLOCKED" "a raw direct socket from vault-api (no HTTP_PROXY involved at all) cannot reach an arbitrary real address"

say ''
say '--- round-2 review B1: DNS resolution from vault-api still reaches the real internet (expected, not a bug) ---'
say 'A wildcard-DNS host that encodes its own answer in the query name --'
say 'resolving it successfully is the exfiltration channel ADR-0011 names as'
say 'open and NOT closed by this lock (Docker'"'"'s embedded resolver answers'
say 'from the HOST'"'"'s own network namespace, not the container'"'"'s, so'
say 'vault-lan'"'"'s disabled masquerade is irrelevant to it).'
dns_probe=$(dc exec -T vault-api python3 <<'PYEOF'
import socket
try:
    ip = socket.gethostbyname("7-7-7-7.sslip.io")
    print("RESOLVED", ip)
except OSError as exc:
    print("FAILED", repr(exc))
PYEOF
)
say "    $dns_probe"
assert_contains "$dns_probe" "RESOLVED 7.7.7.7" "DNS resolution from inside vault-api reaches the real internet (the documented, open channel -- not a regression if this passes)"

say ''
say '--- round-2 review B2: the Docker host'"'"'s own address is directly reachable, bypassing HTTP_PROXY (expected, not a bug) ---'
say 'A reply from the host to a container needs no masquerade, so this is'
say 'unaffected by vault-lan'"'"'s disabled masquerade rule -- unlike the'
say 'arbitrary-destination case above. This script'"'"'s OWN test .env binds'
say 'every published port to 127.0.0.1 specifically (this file'"'"'s own header'
say 'comment: kept off the LAN on purpose), which is not the address a'
say 'container sees when it reaches for "the host" via its gateway -- so'
say 'this starts a throwaway listener bound to every interface on the HOST'
say '(not 127.0.0.1-scoped) to test the actual claim honestly, rather than'
say 'reusing a port this run deliberately narrowed. vault-core'"'"'s own'
say '0.0.0.0-by-default published port (deploy/compose.yaml'"'"'s own comment'
say 'on it) is the real, shipped instance of the same fact -- this listener'
say 'stands in for it only because THIS test run'"'"'s own isolation deliberately'
say 'rebound that one port to loopback.'
b2_port=18089
( python3 -m http.server "$b2_port" --bind 0.0.0.0 >/dev/null 2>&1 & echo $! > "$work/b2-listener.pid" )
sleep 1
# Round-2 investigation, measured directly (not assumed): vault-api's
# `eth0` is NOT reliably `vault-lan` -- Docker attached vault-egress as
# eth0 and vault-lan as eth1 in the run this was found in, the REVERSE of
# their order in compose.yaml's own `networks:` list under vault-api. A
# first version of this check hardcoded "eth0" and got vault-egress's own
# subnet line instead (no gateway field at all, `internal: true` -- Docker
# does not even program a default route for it), which decoded to
# "0.0.0.0" and produced a same-host-but-wrong-target
# `ConnectionRefusedError`, not the timeout a genuinely blocked route would
# give -- a false negative for this exact check, not a sign the underlying
# B2 claim was wrong. Finding the interface with an ACTUAL gateway-bearing
# default route (Destination all-zero AND Gateway non-zero) is robust
# against that reordering, because vault-egress (`internal: true`) never
# gets one, regardless of which `ethN` name Docker assigns it.
host_reach_probe=$(dc exec -T vault-api python3 <<PYEOF
import socket
import struct

gw_ip = None
with open("/proc/net/route") as f:
    for line in f.read().splitlines()[1:]:
        fields = line.split()
        if fields[1] == "00000000" and fields[2] != "00000000":
            gw_ip = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
            break
try:
    if gw_ip is None:
        raise OSError("no gateway-bearing default route found in /proc/net/route")
    s = socket.create_connection((gw_ip, $b2_port), timeout=6)
    print("REACHED", gw_ip, "via gateway")
    s.close()
except OSError as exc:
    print("FAILED", repr(exc))
PYEOF
)
kill "$(cat "$work/b2-listener.pid")" >/dev/null 2>&1 || true
rm -f "$work/b2-listener.pid"  # cleanup() must not kill a reused pid later
say "    $host_reach_probe"
assert_contains "$host_reach_probe" "REACHED" "the Docker host's own address is directly reachable from vault-api without HTTP_PROXY (the documented, open channel)"

say ''
say '--- mutation bar: widening VAULT_EGRESS_ALLOW actually changes what gets through ---'
say 'Recreates vault-proxy with example.com ADDED to the allowlist, then'
say 're-runs the EXACT SAME probe that was just denied above. If it still'
say 'failed, the "DENIED" result above would not have been driven by the'
say 'allowlist'"'"'s actual content -- this is what proves it was.'
widened_env="$work/widened.env"
cp "$env_file" "$widened_env"
echo "VAULT_EGRESS_ALLOW=example.com" >> "$widened_env"
compose_up_or_die "$widened_env" up -d --force-recreate vault-proxy
i=0
proxy_h2=starting
while [ "$i" -lt 15 ]; do
    proxy_h2=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-proxy)" 2>/dev/null || echo starting)
    [ "$proxy_h2" = "healthy" ] && break
    i=$((i + 1))
    sleep 2
done
widened_probe=$(dc exec -T vault-api python3 <<'PYEOF'
import urllib.request
try:
    r = urllib.request.urlopen("http://example.com/", timeout=8)
    print("OK", r.status)
except OSError as exc:
    print("STILL DENIED", repr(exc))
PYEOF
)
say "    $widened_probe"
assert_contains "$widened_probe" "OK" "the SAME host, previously denied, now succeeds once its host is in VAULT_EGRESS_ALLOW -- the allowlist is real, not decorative"

say ''
say '--- restoring the narrow allowlist (leaves the stack in the state every later step expects) ---'
compose_up_or_die "$env_file" up -d --force-recreate vault-proxy
i=0
proxy_h3=starting
while [ "$i" -lt 15 ]; do
    proxy_h3=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-proxy)" 2>/dev/null || echo starting)
    [ "$proxy_h3" = "healthy" ] && break
    i=$((i + 1))
    sleep 2
done
assert_eq "healthy" "$proxy_h3" "vault-proxy is healthy again after being restored to the narrow (empty) allowlist"
restored_probe=$(dc exec -T vault-api python3 <<'PYEOF'
import urllib.request
try:
    urllib.request.urlopen("http://example.com/", timeout=8)
    print("UNEXPECTED SUCCESS")
except OSError as exc:
    print("DENIED", repr(exc))
PYEOF
)
say "    $restored_probe"
assert_contains "$restored_probe" "DENIED" "example.com is denied again after restoring the original (empty) allowlist"

say ''
say '--- mutation bar: dropping vault-proxy entirely breaks every outbound call, immediately ---'
say 'The static case (the service block missing from compose.yaml) is'
say 'api/tests/test_eg1_egress_lock.py::test_vault_proxy_service_exists'"'"'s'
say 'job, run in CI with no Docker at all. This is the LIVE counterpart: with'
say 'the CONTAINER stopped (compose.yaml itself untouched), HTTP_PROXY still'
say 'names it by DNS, and that name now resolves to nothing.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' stop vault-proxy"
dropped_probe=$(dc exec -T vault-api python3 <<'PYEOF'
import urllib.request
try:
    urllib.request.urlopen("https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/", timeout=8)
    print("UNEXPECTED SUCCESS")
except OSError as exc:
    print("FAILS", repr(exc))
PYEOF
)
say "    $dropped_probe"
assert_contains "$dropped_probe" "FAILS" "with vault-proxy stopped, even the baked-in-host call fails immediately -- there is no fallback path"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' start vault-proxy"
i=0
proxy_h4=starting
while [ "$i" -lt 15 ]; do
    proxy_h4=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-proxy)" 2>/dev/null || echo starting)
    [ "$proxy_h4" = "healthy" ] && break
    i=$((i + 1))
    sleep 2
done
assert_eq "healthy" "$proxy_h4" "vault-proxy is healthy again after being restarted"

# =============================================================================
section "7. vault-dns (--profile dns)"
# =============================================================================
step "7a. Fail-fast: no CACHE_IP"
say 'dns/README.md makes CACHE_IP required with no default; the entrypoint must'
say 'refuse to start rather than emit address=/steamcontent.com/ with no address.'
nocacheip=$(docker run --rm "ghcr.io/steamhangar/vault-dns:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$nocacheip" | sed 's/^/    /'
assert_contains "$nocacheip" "FATAL" "vault-dns refuses to start without CACHE_IP"
assert_not_contains "$nocacheip" "exit=0" "...and exits non-zero"

step "7b. Fail-fast: CACHE_IP that is not a plain IPv4 address"
badip=$(docker run --rm -e 'CACHE_IP=1.2.3.4
log-queries' "ghcr.io/steamhangar/vault-dns:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$badip" | sed 's/^/    /'
assert_contains "$badip" "FATAL" "a CACHE_IP carrying an injected config line is refused"

step "7c. Start the dns profile"
compose_up_or_die "$env_file" --profile dns up -d vault-dns
sleep 3
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' logs vault-dns"

step "7d. Rendered dnsmasq.conf inside the container"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-dns sh -c 'grep -v \"^#\" /run/vault-dns/dnsmasq.conf | grep -v \"^\$\"'"

step "7e. A query for a Steam CDN name -> the cache IP"
a_ans=$(dig +short @127.0.0.1 -p "$DNS_PORT" A "$CDN_HOST" 2>&1)
say "    dig +short @127.0.0.1 -p $DNS_PORT A $CDN_HOST"
say "      -> $a_ans"
assert_eq "$TEST_CACHE_IP" "$(printf '%s' "$a_ans" | tr -d '\n')" "A query is answered with CACHE_IP"

wild_ans=$(dig +short @127.0.0.1 -p "$DNS_PORT" A anything.else.steamcontent.com 2>&1)
say "    wildcard subdomain -> $wild_ans"
assert_eq "$TEST_CACHE_IP" "$(printf '%s' "$wild_ans" | tr -d '\n')" "the wildcard covers arbitrary subdomains"

step "7f. AAAA -> NODATA (ADR-0001 req 6: the IPv6 bypass stays closed)"
aaaa=$(dig @127.0.0.1 -p "$DNS_PORT" AAAA "$CDN_HOST" 2>&1)
printf '%s\n' "$aaaa" | grep -E 'status:|ANSWER SECTION|^cache2|ANSWER:' | sed 's/^/    /'
assert_contains "$aaaa" "status: NOERROR" "AAAA answer status is NOERROR"
assert_contains "$aaaa" "ANSWER: 0" "AAAA answer contains zero records (NODATA, not Valve's real IPv6)"

step "7g. Everything else is still forwarded upstream"
fwd=$(dig +short @127.0.0.1 -p "$DNS_PORT" A example.com 2>&1)
say "    dig +short A example.com -> $(printf '%s' "$fwd" | tr '\n' ' ')"
if [ -n "$fwd" ]; then ok "non-steamcontent.com queries are forwarded and answered"; else bad "upstream forwarding returned nothing"; fi

step "7h. vault-dns healthcheck"
dns_h=""
i=0
while [ "$i" -lt 20 ]; do
    dns_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-dns)" 2>/dev/null || echo starting)
    [ "$dns_h" = "healthy" ] && break
    i=$((i + 1)); sleep 2
done
assert_eq "healthy" "$dns_h" "vault-dns container healthcheck"

# =============================================================================
section "8. vault-core fail-fast guards"
# =============================================================================
say 'Each of these is a deployment mistake that would otherwise be silent.'

step "8a. cache/ and tmp/ split across two filesystems"
say 'Simulated with a tmpfs over /vault/tmp (a different st_dev), which is exactly'
say 'what a second volume mount would look like to the preflight.'
docker volume create "$PROJECT-scratch" >/dev/null
split=$(docker run --rm -v "$PROJECT-scratch:/vault" --tmpfs /vault/tmp "ghcr.io/steamhangar/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$split" | grep -E 'FATAL|st_dev|exit=' | sed 's/^/    /'
assert_contains "$split" "DIFFERENT" "a split cache//tmp mount is refused at boot"
assert_not_contains "$split" "exit=0" "...and exits non-zero"

step "8b. An empty VAULT_RESOLVER"
emptyres=$(docker run --rm -v "$PROJECT-scratch:/vault" -e VAULT_RESOLVER= "ghcr.io/steamhangar/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$emptyres" | grep -E 'FATAL|exit=' | sed 's/^/    /'
assert_contains "$emptyres" "VAULT_RESOLVER is empty" "an empty resolver is refused"

step "8c. A VAULT_RESOLVER carrying an nginx-config injection"
inj=$(docker run --rm -v "$PROJECT-scratch:/vault" -e 'VAULT_RESOLVER=1.1.1.1; return 200 "pwned";' "ghcr.io/steamhangar/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$inj" | grep -E 'FATAL|exit=' | sed 's/^/    /'
assert_contains "$inj" "refusing" "a resolver value with config-injection characters is refused"

step "8d. A misconfigured envsubst filter leaves a placeholder unrendered"
unrendered=$(docker run --rm -v "$PROJECT-scratch:/vault" -e 'NGINX_ENVSUBST_FILTER=^NOTHING_' "ghcr.io/steamhangar/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$unrendered" | grep -E 'FATAL|unsubstituted|exit=' | sed 's/^/    /'
assert_contains "$unrendered" "unsubstituted" "an unrendered \${VAULT_...} placeholder is caught before nginx starts"

step "8e. A cache directory the worker user cannot write"
mkdir -p "$work/rootonly/cache/depot" "$work/rootonly/tmp"
chmod 0755 "$work/rootonly" "$work/rootonly/cache" "$work/rootonly/tmp"
chown -R 0:0 "$work/rootonly" 2>/dev/null
chmod 0555 "$work/rootonly/cache" "$work/rootonly/tmp"
ro=$(docker run --rm -v "$work/rootonly:/vault" "ghcr.io/steamhangar/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$ro" | grep -E 'FATAL|chown|exit=' | sed 's/^/    /'
assert_contains "$ro" "not writable" "a cache directory the nginx worker cannot write is refused"

# =============================================================================
section "9. Dedicated cache mount: VAULT_CACHE_PATH bind mode, live (WP DEPLOY-FIX-3)"
# =============================================================================
say 'Every section above ran on the default named volume. This one follows'
say 'deploy/README.md "Using a dedicated cache mount" to the letter: create'
say '<path>/cache/depot and <path>/tmp, owned 101:101, set VAULT_CACHE_PATH,'
say 'bring the stack up. A first rollout on Docker 28 failed exactly here'
say '("field VolumeOptions must not be specified") while this suite was green.'
say ''
say 'Ownership: this script may run as a non-root user (CI, a devbox), so the'
say 'chown 101:101 the README asks for runs in a throwaway root container of'
say 'the vault-core image -- the same effect as the operator'"'"'s own chown.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' --profile dns down"

step "9a. Host directory prepared as the README demands"
mkdir -p "$bind_dir/cache/depot" "$bind_dir/tmp"
docker run --rm --user 0:0 --entrypoint sh -v "$bind_dir:/vault" \
    "ghcr.io/steamhangar/vault-core:$TAG" -c 'chown -R 101:101 /vault' 2>&1 | sed 's/^/    /'
bind_own=$(stat -c '%u:%g' "$bind_dir" "$bind_dir/cache" "$bind_dir/cache/depot" "$bind_dir/tmp" | sort -u | tr '\n' ' ' | sed 's/ $//')
assert_eq "101:101" "$bind_own" "bind dir, cache/, cache/depot and tmp are owned 101:101 (precondition)"

step "9b. Stack up with VAULT_CACHE_PATH=$bind_dir"
say 'With the cache-event log ON, as deploy/.env.example ships it: vault-core'
say 'must create <path>/logs/ itself, nothing beyond cache/depot and tmp is'
say 'asked of the operator.'
bind_live_env_file="$work/verify-bind-live.env"
cp "$bind_env_file" "$bind_live_env_file"
printf 'VAULT_EVENT_LOG=/vault/logs/event.log\nVAULT_EVENT_LOG_PATH=/vault/logs/event.log\n' >> "$bind_live_env_file"
printf '$ docker compose --env-file %s -f %s -p %s up -d\n' "$bind_live_env_file" "$compose_file" "$PROJECT"
if docker compose --env-file "$bind_live_env_file" -f "$compose_file" -p "$PROJECT" up -d > "$work/compose-up-bind.log" 2>&1; then
    sed 's/^/    /' "$work/compose-up-bind.log"
    ok "docker compose up -d succeeds with VAULT_CACHE_PATH set to an absolute host path"
    bind_up=yes
else
    sed 's/^/    /' "$work/compose-up-bind.log"
    bad "docker compose up -d with VAULT_CACHE_PATH set failed -- see the output above"
    bind_up=no
fi
assert_not_contains "$(cat "$work/compose-up-bind.log")" "VolumeOptions" "the daemon did not reject the /vault bind for carrying volume options"

if [ "$bind_up" = yes ]; then
    i=0
    while [ "$i" -lt 60 ]; do
        core_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-core)" 2>/dev/null || echo starting)
        api_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-api)" 2>/dev/null || echo starting)
        [ "$core_h" = "healthy" ] && [ "$api_h" = "healthy" ] && break
        i=$((i + 1))
        sleep 2
    done
    say "vault-core health: $core_h    vault-api health: $api_h"
    assert_eq "healthy" "$core_h" "vault-core is healthy on the bind-mounted cache"
    assert_eq "healthy" "$api_h"  "vault-api is healthy on the bind-mounted cache"

    step "9c. Both services really mount the host directory, and it is the same one"
    for svc_name in vault-core vault-api; do
        m=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/vault"}}{{.Type}} {{.Source}}{{end}}{{end}}' "$(dc ps -q "$svc_name")" 2>/dev/null)
        assert_eq "bind $bind_dir" "$m" "$svc_name: /vault is a bind of VAULT_CACHE_PATH"
    done

    step "9d. A real MISS lands on the host directory (proxy_store rename tmp/ -> cache/ works on the bind)"
    bmiss=$(curl -s -o "$work/miss-bind.bin" -w '%{http_code}' --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI")
    assert_eq "200" "$bmiss" "cold request through the bind-mounted cache returns 200"
    sleep 1
    if [ -f "$bind_dir/cache/depot/$DEPOT/chunk/$CHUNK" ]; then
        ok "the chunk is stored at <VAULT_CACHE_PATH>/cache/depot/$DEPOT/chunk/$CHUNK on the host"
    else
        bad "the chunk is not at <VAULT_CACHE_PATH>/cache/depot/$DEPOT/chunk/$CHUNK on the host"
    fi
    api_sees=$(dc exec -T vault-api sh -c "test -f /vault/cache/depot/$DEPOT/chunk/$CHUNK && echo present || echo absent" 2>&1)
    assert_eq "present" "$api_sees" "vault-api sees the chunk vault-core stored (one shared bind)"
    if [ -d "$bind_dir/logs" ]; then
        ok "vault-core created <VAULT_CACHE_PATH>/logs/ for the event log itself"
    else
        bad "<VAULT_CACHE_PATH>/logs/ is missing although VAULT_EVENT_LOG is on"
    fi
fi

# =============================================================================
section "10. Result"
# =============================================================================
say "checks passed: $pass"
say "checks failed: $fail"
if [ "$fail" -eq 0 ]; then
    say ''
    say 'ALL CHECKS PASSED'
    exit_code=0
else
    say ''
    say 'THERE WERE FAILURES'
    exit_code=1
fi
exit "$exit_code"
