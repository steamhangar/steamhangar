# SteamHangar vault-core -- the path-faithful Steam depot cache (Phase 1, WP 1.9).
#
# Build from the repo's core/ directory:
#     docker build -t steamhangar/vault-core:0.1.0 core/
# or, normally, via deploy/compose.yaml.
#
# --- Base image pin ----------------------------------------------------------
# Pinned by BOTH an exact version tag and its digest (docs/PROJECT_PLAN.md §9 /
# CLAUDE.md: pinned tags only, never latest/release). The tag documents WHAT this
# is for a human; the digest is what actually makes the build reproducible, since
# a tag can be re-pointed upstream at any time.
#
#   nginx:1.29.8-alpine3.23
#   digest resolved 2026-08-05 via `docker pull` + `docker inspect --format
#   '{{index .RepoDigests 0}}'` (26.0 MB base image).
#
# Alpine (not the Debian variant) because vault-core is a single-purpose static
# file server + reverse proxy: nothing here needs glibc, and the smaller base is
# less to keep patched. Bump both the tag and the digest together.
FROM nginx:1.29.8-alpine3.23@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de

LABEL org.opencontainers.image.title="SteamHangar vault-core" \
      org.opencontainers.image.description="nginx proxy_store cache with path-faithful Steam depot storage" \
      org.opencontainers.image.source="https://github.com/steamhangar/steamhangar" \
      org.opencontainers.image.licenses="Apache-2.0"

# --- Config -------------------------------------------------------------------
# The config is baked into the image, not mounted: it is reviewed, test-covered
# code (core/tests/test-core.ps1), not a knob. The single deployment-specific
# value -- the upstream DNS resolver (ADR-0001 req 4) -- is an env placeholder
# rendered at start; see core/docker/nginx.conf.template's header for the
# mechanism and core/docker/check-config-drift.sh for the contract that keeps
# this template in sync with core/nginx/nginx.conf.
COPY docker/nginx.conf.template /etc/nginx/templates/nginx.conf.template
# WP SEC-FIX-5: makes /vault, cache/, tmp/ and logs/ root:root 0755 at every
# start (migrating a volume from an earlier image) and refuses to start when
# it cannot. Runs before every other hook that touches /vault.
COPY docker/21-vault-volume-ownership.sh /docker-entrypoint.d/21-vault-volume-ownership.sh
COPY docker/25-vault-eventlog.sh /docker-entrypoint.d/25-vault-eventlog.sh
COPY docker/26-vault-tls-passthrough.sh /docker-entrypoint.d/26-vault-tls-passthrough.sh
COPY docker/27-vault-upstream-rate.sh /docker-entrypoint.d/27-vault-upstream-rate.sh
COPY docker/28-vault-upstream-pool.sh /docker-entrypoint.d/28-vault-upstream-pool.sh
# WP VER-2: records VAULT_BUILD_VERSION/COMMIT (validated) in
# /vault/logs/vault-core-version.json at every start for vault-api's
# GET /v1/about. Touches no nginx config.
COPY docker/29-vault-build-version.sh /docker-entrypoint.d/29-vault-build-version.sh
COPY docker/40-vault-preflight.sh /docker-entrypoint.d/40-vault-preflight.sh

# Shipped for auditability: the running image can prove its own config matches
# the reviewed native one (`docker run --rm --entrypoint sh <image> -c
# 'sh /opt/vault/check-config-drift.sh'` needs core/nginx/nginx.conf mounted, so
# this is primarily a repo-side tool -- it is included so the check travels with
# the artifact rather than only existing in a checkout).
COPY docker/check-config-drift.sh /opt/vault/check-config-drift.sh
# WP CORE-FEAT-1b (ADR-0017): the native (empty) pool include travels with the
# image for the same reason -- the build-time check below proves the hook's
# empty render is byte-identical to it, and a running image can re-prove it.
# nginx never reads this copy; the hook renders /etc/nginx/vault-upstream-pool.conf.
COPY nginx/vault-upstream-pool.conf /opt/vault/vault-upstream-pool.native.conf

RUN set -eux; \
    chmod 0755 /docker-entrypoint.d/21-vault-volume-ownership.sh /docker-entrypoint.d/25-vault-eventlog.sh /docker-entrypoint.d/26-vault-tls-passthrough.sh /docker-entrypoint.d/27-vault-upstream-rate.sh /docker-entrypoint.d/28-vault-upstream-pool.sh /docker-entrypoint.d/29-vault-build-version.sh /docker-entrypoint.d/40-vault-preflight.sh /opt/vault/check-config-drift.sh; \
    # The stock image's demo vhost would otherwise be rendered pointless anyway
    # (our nginx.conf does not include conf.d/*), but removing it keeps the
    # image honest about what it serves.
    rm -f /etc/nginx/conf.d/default.conf; \
    # --- The one volume ------------------------------------------------------
    # cache/, tmp/ and logs/ are created HERE, inside one directory, so that a
    # fresh named volume mounted at /vault is seeded with all three -- with the
    # right ownership -- and therefore automatically satisfies the binding
    # same-filesystem requirement (core/README.md): proxy_store completes each
    # object by rename()-ing from tmp/ into cache/depot/..., which is atomic only
    # within one filesystem.
    #   cache/depot/ is pre-created because vault-api's deletion guard
    #   (DELETE /v1/cache/{appid}) refuses a cache root that has no depot/ dir,
    #   and vault-api mounts this same volume.
    #   logs/ is pre-created (WP 3.10, ADR-0008) so that turning on
    #   VAULT_EVENT_LOG=/vault/logs/event.log needs no host-side mkdir/chown --
    #   same "named volumes get this right automatically" property cache/ and
    #   tmp/ already have. Harmless when the feature stays off (empty dir).
    mkdir -p /vault/cache/depot /vault/tmp /vault/logs; \
    # uid/gid 101 = the stock nginx image's `nginx` user, which the workers run
    # as. vault-api's container user is created with the SAME numeric uid/gid so
    # both services can write this shared volume -- see api/Dockerfile.
    # WP SEC-FIX-5: uid 101 owns only what it writes (cache/depot here; the
    # nginx temp dirs and the event log are created at start). /vault, cache/,
    # tmp/ and logs/ stay root:root 0755, because root (the start hooks, the
    # nginx master) opens names in them and must never follow a name uid 101
    # could swap. A fresh named volume copies this ownership up, the mount
    # point's included; 21-vault-volume-ownership.sh enforces it at every start.
    chown root:root /vault /vault/cache /vault/tmp /vault/logs; \
    chown nginx:nginx /vault/cache/depot; \
    chmod 0755 /vault /vault/cache /vault/cache/depot /vault/tmp /vault/logs; \
    [ "$(stat -c '%u:%g %a' /vault /vault/cache /vault/tmp /vault/logs | sort -u)" = "0:0 755" ]; \
    [ "$(stat -c '%u:%g %a' /vault/cache/depot)" = "101:101 755" ]; \
    # Fail the BUILD, not the first deployment, if the template ever loses its
    # placeholder or gains an unexpected one.
    grep -q '\${VAULT_RESOLVER}' /etc/nginx/templates/nginx.conf.template; \
    # Same for the WP 3.10 event-log placeholder -- it must appear exactly
    # twice (location /depot/ and location @miss, see
    # core/docker/check-config-drift.sh "delta 6"), each carrying the
    # trailing marker comment 25-vault-eventlog.sh depends on to find it.
    grep -q '\${VAULT_EVENT_LOG}' /etc/nginx/templates/nginx.conf.template; \
    [ "$(grep -c '# VAULT_EVENT_LOG_LINE' /etc/nginx/templates/nginx.conf.template)" = "2" ]; \
    # WP TH-1a: the upstream rate cap is wired in exactly once (include at
    # http level, proxy_limit_rate in @miss), and the hook renders its cap-off
    # form in this image's own shell.
    [ "$(grep -c '^[[:space:]]*include vault-upstream-rate\.conf;' /etc/nginx/templates/nginx.conf.template)" = "1" ]; \
    [ "$(grep -c '^[[:space:]]*proxy_limit_rate[[:space:]]*\$vault_upstream_rate;' /etc/nginx/templates/nginx.conf.template)" = "1" ]; \
    VAULT_UPSTREAM_RATE= VAULT_UPSTREAM_RATE_WINDOW= sh /docker-entrypoint.d/27-vault-upstream-rate.sh /tmp/vault-upstream-rate.build-check.conf; \
    rm -f /tmp/vault-upstream-rate.build-check.conf; \
    # WP CORE-FIX-3: the HTTPS passthrough block is marked exactly once for
    # 26-vault-tls-passthrough.sh, and this nginx build has the stream and
    # ssl_preread modules compiled in statically (no load_module needed).
    [ "$(grep -c -x '# VAULT_TLS_PASSTHROUGH_BEGIN' /etc/nginx/templates/nginx.conf.template)" = "1" ]; \
    [ "$(grep -c -x '# VAULT_TLS_PASSTHROUGH_END' /etc/nginx/templates/nginx.conf.template)" = "1" ]; \
    # (Separate commands, no `&&`/`!`: `set -e` ignores a failure inside an
    # AND list and after `!`, which would let the build pass.)
    nginx -V 2>&1 | grep -q -e '--with-stream '; \
    nginx -V 2>&1 | grep -q -e '--with-stream_ssl_preread_module'; \
    if nginx -V 2>&1 | grep -q -e '--with-stream=dynamic' -e '--with-stream_ssl_preread_module=dynamic'; then exit 1; fi; \
    # WP CORE-FEAT-1b (ADR-0017): the keepalive pool include is wired in
    # exactly once; the hook's empty render equals the shipped native file
    # byte for byte; a full list (the ceiling, 4 edges) renders 4 groups with
    # no resolver of their own; a 5th edge and a name outside the allowlist
    # families are refused -- all in this image's own shell, so an image
    # cannot be built with a broken hook.
    [ "$(grep -c '^[[:space:]]*include vault-upstream-pool\.conf;' /etc/nginx/templates/nginx.conf.template)" = "1" ]; \
    pool_check=/tmp/vault-upstream-pool.build-check.conf; \
    VAULT_UPSTREAM_POOL_HOSTS= sh /docker-entrypoint.d/28-vault-upstream-pool.sh "$pool_check"; \
    cmp "$pool_check" /opt/vault/vault-upstream-pool.native.conf; \
    VAULT_UPSTREAM_POOL_HOSTS='cache1-fra2.steamcontent.com cache2-fra2.steamcontent.com cache3-fra2.steamcontent.com dist-fra1.discovery.steamserver.net' \
        sh /docker-entrypoint.d/28-vault-upstream-pool.sh "$pool_check"; \
    [ "$(grep -c '^upstream ' "$pool_check")" = "4" ]; \
    # (an explicit if: `! grep` would not trip set -e, POSIX exempts `!` pipelines)
    if grep -qw resolver "$pool_check"; then \
        echo "BUILD CHECK FAILED: a resolver directive inside the pool include (ADR-0017 (c))" >&2; exit 1; \
    fi; \
    rm -f "$pool_check"; \
    if VAULT_UPSTREAM_POOL_HOSTS='c1.steamcontent.com c2.steamcontent.com c3.steamcontent.com c4.steamcontent.com c5.steamcontent.com' \
        sh /docker-entrypoint.d/28-vault-upstream-pool.sh "$pool_check" 2>/dev/null; then \
        echo "BUILD CHECK FAILED: 28-vault-upstream-pool.sh accepted 5 edges (ceiling is 4, ADR-0017 decision 3A)" >&2; exit 1; \
    fi; \
    if VAULT_UPSTREAM_POOL_HOSTS='cache1.example.com' \
        sh /docker-entrypoint.d/28-vault-upstream-pool.sh "$pool_check" 2>/dev/null; then \
        echo "BUILD CHECK FAILED: 28-vault-upstream-pool.sh accepted a host outside the allowlist families" >&2; exit 1; \
    fi; \
    [ ! -e "$pool_check" ]; \
    # WP VER-2: the build-version hook writes exactly the reviewed JSON for a
    # valid pair and fails closed ("invalid") for a bad value, before the
    # image ships. A scratch path, never /vault (that is the volume), and not
    # under /tmp: as root the hook writes only into a root-only directory
    # chain (SEC-FIX-5), and /tmp is world-writable.
    ver_check=/opt/vault/ver-check/vault-core-version.json; \
    VAULT_BUILD_VERSION=0.1.0-rc9 VAULT_BUILD_COMMIT=0123456789abcdef0123456789abcdef01234567 \
        sh /docker-entrypoint.d/29-vault-build-version.sh "$ver_check"; \
    grep -qx '{"component":"vault-core","version":"0.1.0-rc9","commit":"0123456789abcdef0123456789abcdef01234567","recorded_at":"[0-9]\{4\}-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z"}' "$ver_check"; \
    VAULT_BUILD_VERSION='1.0 beta' VAULT_BUILD_COMMIT=XYZ \
        sh /docker-entrypoint.d/29-vault-build-version.sh "$ver_check" 2>/dev/null; \
    grep -q '"version":"invalid","commit":"invalid"' "$ver_check"; \
    [ "$(stat -c '%u:%g %a' "$ver_check")" = "0:0 644" ]; \
    rm -rf /opt/vault/ver-check

# Declared so that a plain `docker run` without -v cannot silently fill the
# container's writable layer with depot chunks, and so that the default is one
# mount covering both cache/ and tmp/. A deployment that still splits them is
# caught at boot by 40-vault-preflight.sh.
VOLUME ["/vault"]

ENV NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates \
    NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
    # Restricts envsubst to ${VAULT_*} placeholders.
    #
    # MEASURED, so nobody "simplifies" this away on the strength of a green test:
    # with this image's stock environment the filter changes NOTHING today --
    # rendering with and without it produces a byte-identical nginx.conf. The
    # official entrypoint builds its envsubst allowlist from environment
    # variables that actually EXIST, and none of them is named like an nginx
    # runtime variable. envsubst is case-sensitive and nginx's variables are
    # lowercase ($host, $uri, $document_root, $upstream_status, ...), while
    # environment variables are conventionally uppercase -- which is exactly why
    # there is no collision at the moment.
    #
    # The filter is a guard against a FUTURE collision: a base-image update, an
    # orchestrator, or an operator introducing a lowercase env var named e.g.
    # `host` or `uri` (legal, just unusual). Unfiltered, envsubst would then
    # replace that nginx variable with the env var's VALUE -- not blank it -- so
    # the config would still be syntactically valid, `nginx -t` would still pass,
    # and the server would quietly proxy/store against the wrong value. Nothing
    # else in this stack catches that; the filter does, by never substituting
    # anything that is not a ${VAULT_*} placeholder in the first place.
    NGINX_ENVSUBST_FILTER=^VAULT_ \
    # ADR-0001 req 4. Overridable per deployment (deploy/.env). Must be a real
    # upstream resolver -- never vault-dns, which would answer *.steamcontent.com
    # with vault-core's own address and loop every miss back into this server.
    VAULT_RESOLVER=1.1.1.1 \
    # WP 3.10 (ADR-0008): path for the optional cache-event log. DEFAULT EMPTY
    # -- feature OFF -- deliberately, not "on by default with a sane path":
    # the ADR explicitly frames this feed as "optional at runtime", and
    # nothing in this image rotates or truncates the file once it exists
    # (that is WP 3.11's sweeper, api/, not yet shipped as of this work
    # package) -- defaulting it on here would grow an unbounded file on every
    # vault-core deployment that hasn't also deployed the sweeper. Set it to
    # an absolute path under the /vault volume (e.g.
    # /vault/logs/event.log -- logs/ is pre-created below) in deploy/.env to
    # enable it once a consumer exists. /docker-entrypoint.d/25-vault-eventlog.sh
    # validates the value and either strips the feature entirely (empty) or
    # prepares the target directory (non-empty) before nginx starts.
    VAULT_EVENT_LOG= \
    # WP TH-1a: aggregate upstream (Steam -> vault) cap in bytes/s, nginx size
    # syntax (k = 1024, m = 1048576), e.g. 800k. DEFAULT EMPTY = no cap.
    # /docker-entrypoint.d/27-vault-upstream-rate.sh divides it by the live
    # number of in-flight requests and refuses to boot on an invalid value.
    # Env-only, read once at container start (core/README.md "Upstream rate
    # cap").
    VAULT_UPSTREAM_RATE= \
    # HH:MM-HH:MM (VAULT_SCHEDULE_WINDOW's grammar): full speed inside, the
    # cap outside, in the container's local time (TZ). Empty = cap always.
    VAULT_UPSTREAM_RATE_WINDOW= \
    # WP CORE-FEAT-1b (ADR-0017): space-separated Steam CDN edge host names
    # that get a keepalive pool (at most 4). DEFAULT EMPTY = no pool; the
    # hook /docker-entrypoint.d/28-vault-upstream-pool.sh treats unset and
    # empty alike (core/README.md "Upstream keepalive pool").
    VAULT_UPSTREAM_POOL_HOSTS= \
    # WP CORE-FIX-3 (ADR-0020): HTTPS passthrough on port 443 for
    # *.steamcontent.com SNI names, TLS not terminated. DEFAULT ON (1);
    # 0/false/off/no removes the stream block at start
    # (/docker-entrypoint.d/26-vault-tls-passthrough.sh). Whether the port
    # is reachable from the LAN is decided by the publish line in
    # deploy/compose.yaml (VAULT_TLS_BIND), not here.
    VAULT_TLS_PASSTHROUGH=1

WORKDIR /vault

# Steam CDN traffic is plain HTTP (docs/PROJECT_PLAN.md §10); no TLS is ever
# terminated here. 443 is the HTTPS passthrough (WP CORE-FIX-3, ADR-0020): TLS
# bytes for *.steamcontent.com are copied to Valve unchanged, no certificate.
EXPOSE 80 443

# /health is a local, non-proxying location (see the config): it needs no Host
# allowlist entry and never touches the network, so it is a true liveness probe
# for "nginx is up and serving", not for "the internet works".
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/health || exit 1

# --- Build identity (WP VER-1) -----------------------------------------------
# The release version and commit this image was built from, set by
# .github/workflows/publish.yml (`VAULT_VERSION` = the tag without its `v`,
# e.g. 0.1.0-rc8; `VAULT_COMMIT` = the full commit SHA). A local build
# (`docker build`, deploy/compose.yaml, deploy/tests/verify-stack.sh) gets the
# defaults `dev` / `unknown`, so a hand-built image never claims a release.
# Baked as runtime ENV so the running container can report it; nothing reads
# a LABEL at runtime. Declared LAST, after every RUN and COPY: a changed
# build-arg value invalidates the cache of every later RUN, so the expensive
# layers above stay cached across releases. Never validated here: ENV can be
# overridden at `docker run` time, so every consumer validates what it reads
# (docs: core/README.md "Build version").
ARG VAULT_VERSION=dev
ARG VAULT_COMMIT=unknown
ENV VAULT_BUILD_VERSION="${VAULT_VERSION}" \
    VAULT_BUILD_COMMIT="${VAULT_COMMIT}"
LABEL org.opencontainers.image.version="${VAULT_VERSION}" \
      org.opencontainers.image.revision="${VAULT_COMMIT}"

# ENTRYPOINT stays the base image's /docker-entrypoint.sh, which renders the
# template and runs /docker-entrypoint.d/*.sh (including our preflight) before
# exec'ing this command. `-p /vault` is what makes the config's relative paths
# ("root cache", "tmp/proxy") resolve inside the volume.
CMD ["nginx", "-g", "daemon off;", "-p", "/vault", "-c", "/etc/nginx/nginx.conf"]
