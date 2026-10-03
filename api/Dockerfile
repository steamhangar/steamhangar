# SteamHangar vault-api -- FastAPI + SQLite brain, and the SteamPrefill host
# (Phase 1, WP 1.9).
#
# Build from the REPO ROOT, not api/ (packaging work package,
# docs/PROJECT_PLAN.md §7 Phase 5, 2026-08-17): the web UI (web/, Phase 4a) is
# baked into this image now (see the `COPY web /app/web` below), and web/ is a
# SIBLING of api/, not a child of it -- an api/-only build context cannot see
# it. deploy/compose.yaml's vault-api service sets `context: ..` /
# `dockerfile: api/Dockerfile` accordingly; every COPY source path below is
# repo-root-relative as a result. vault-core and vault-dns are UNCHANGED --
# they keep their own directories as build context.
#     docker build -t steamhangar/vault-api:<version> -f api/Dockerfile .
# (<version> is a free-text tag for your own build. The version the running
# server reports comes from the VAULT_VERSION/VAULT_COMMIT build args, see
# "Build identity" at the end of this file, WP VER-1.)
# or, normally, via deploy/compose.yaml. The root `.dockerignore` keeps this
# wider context from also sending app/ (the Android tree), poc/ (Phase-0
# evidence) and every other component's own directory to the daemon on
# every build -- measured 9.734 MB -> 1.54 MB (84% smaller) on this
# checkout (see the .dockerignore's own header for how that was measured).
#
# --- Base image pin ----------------------------------------------------------
#   python:3.13.14-slim-trixie
#   digest resolved 2026-08-05 via `docker pull` + `docker inspect --format
#   '{{index .RepoDigests 0}}'`.
# Pinned by exact tag AND digest (docs/PROJECT_PLAN.md §9 / CLAUDE.md: pinned
# tags only). Debian-based rather than Alpine on purpose: the SteamPrefill
# release below is a self-contained .NET binary built against glibc, which
# musl/Alpine cannot run.
#
# --- Stage 1: fetch SteamPrefill ---------------------------------------------
# Kept in a throwaway stage so curl/unzip never exist in the shipped image.
#
# Pinned to the exact upstream release Phase 0 validated (ADR-0001: SteamPrefill
# v3.7.1 auto-detected the cache via the lancache-heartbeat contract and
# prefilled path-faithfully). Declared BEFORE the first FROM (a global build
# arg) so both stages read the one value: stage 1 downloads that release, the
# final stage bakes it as ENV STEAMPREFILL_VERSION for GET /v1/about (WP VER-2).
# Bump it together with STEAMPREFILL_ARCHIVE/URL/SHA256 below.
ARG STEAMPREFILL_VERSION=3.7.1
FROM python:3.13.14-slim-trixie@sha256:bf503bb2243c5aad0aa951544dd60d165f992646441d35dea90893703fc26251 AS steamprefill

ARG STEAMPREFILL_VERSION
ARG STEAMPREFILL_ARCHIVE=SteamPrefill-3.7.1-linux-x64.zip
ARG STEAMPREFILL_URL=https://github.com/tpill90/steam-lancache-prefill/releases/download/v3.7.1/SteamPrefill-3.7.1-linux-x64.zip
# HOW THIS CHECKSUM WAS OBTAINED (documented because it matters):
# upstream publishes NO checksum file and no signature alongside its release
# assets -- verified against the GitHub releases API for tag v3.7.1 on
# 2026-08-05, whose asset list contains only the four platform .zip files
# (linux-arm64, linux-x64, osx-x64, win-x64) and nothing else. So this is a
# trust-on-first-use pin, established as follows:
#   1. downloaded https://github.com/tpill90/steam-lancache-prefill/releases/
#      download/v3.7.1/SteamPrefill-3.7.1-linux-x64.zip over HTTPS (GitHub's
#      own release CDN, TLS-verified) on 2026-08-05
#   2. checked the byte count against the size the GitHub releases API reports
#      for that asset: 12095541 bytes -- matched
#   3. sha256sum -> 678765365fd3dacd9394912526a086e15f4f5b27dc6db24ea6ef8dc99e2d3748
#   4. repeated the download independently and re-hashed: same digest
# From here on the pin is what protects the build: any change to the bytes at
# that URL -- upstream re-cutting the tag, a compromised release, a truncated
# download -- fails the build instead of silently shipping. Bump version, URL
# and hash together, never one of them alone.
ARG STEAMPREFILL_SHA256=678765365fd3dacd9394912526a086e15f4f5b27dc6db24ea6ef8dc99e2d3748

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip; \
    rm -rf /var/lib/apt/lists/*; \
    curl --fail --silent --show-error --location --retry 3 \
         --output /tmp/steamprefill.zip "$STEAMPREFILL_URL"; \
    echo "${STEAMPREFILL_SHA256}  /tmp/steamprefill.zip" | sha256sum --check --strict -; \
    unzip -q /tmp/steamprefill.zip -d /tmp/steamprefill; \
    mkdir -p /opt/steamprefill; \
    cp "/tmp/steamprefill/SteamPrefill-${STEAMPREFILL_VERSION}-linux-x64/SteamPrefill" \
       /opt/steamprefill/SteamPrefill; \
    chmod 0755 /opt/steamprefill/SteamPrefill; \
    rm -rf /tmp/steamprefill /tmp/steamprefill.zip

# --- Build probe: the hidden --max-threads flag (WP CORE-FIX-2) -------------
# vault-api passes `--max-threads N` on every prefill (VAULT_PREFILL_MAX_THREADS)
# so SteamPrefill cannot exhaust a carrier-grade NAT's port mappings. The flag
# is HIDDEN: not in --help, pre-parsed and stripped in Program.cs
# (ParseHiddenFlags) before CliFx runs. If a future SteamPrefill drops it,
# CliFx answers "Unrecognized option(s): --max-threads" and EVERY prefill job
# fails -- so this probe fails the image build instead.
# Why the log line and not the exit code: `--help` ignores unknown options and
# exits 0 either way (measured on 3.7.1: `prefill --bogus-flag 1 --help` -> 0),
# so only the flag's own "Will download using at most N threads" line proves
# the parser recognised it. A help run makes no network request and needs no
# Steam login. Invariant globalization because this throwaway stage has no
# libicu; HOME and the working directory are scratch because SteamPrefill's
# AppConfig creates directories there before parsing anything. ANSI is
# stripped: the count is colourised mid-sentence.
# api/tests/test_core_fix_2_max_threads.py pins this probe's presence and the
# list of SteamPrefill versions verified to carry the flag.
RUN set -eux; \
    mkdir -p /tmp/sp-probe/home; \
    probe_out=$(cd /tmp/sp-probe && HOME=/tmp/sp-probe/home DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
        /opt/steamprefill/SteamPrefill prefill --max-threads 3 --help </dev/null 2>&1 \
        | sed 's/\x1b\[[0-9;]*m//g'); \
    printf '%s\n' "$probe_out" | head -n 2; \
    printf '%s\n' "$probe_out" | grep -F "Will download using at most 3 threads" \
        || { echo "SteamPrefill ${STEAMPREFILL_VERSION} did not print the --max-threads line (flag dropped, or SteamPrefill failed to start; see output above). vault-api passes it on every prefill." >&2; exit 1; }; \
    rm -rf /tmp/sp-probe /opt/steamprefill/Config

# --- Stage 2: the runtime image ----------------------------------------------
FROM python:3.13.14-slim-trixie@sha256:bf503bb2243c5aad0aa951544dd60d165f992646441d35dea90893703fc26251

# org.opencontainers.image.version/.revision are set from the build args at
# the end of this file ("Build identity", WP VER-1), not here.
LABEL org.opencontainers.image.title="SteamHangar vault-api" \
      org.opencontainers.image.description="FastAPI + SQLite control plane: prefill orchestration, per-game size and deletion" \
      org.opencontainers.image.source="https://github.com/steamhangar/steamhangar" \
      org.opencontainers.image.licenses="Apache-2.0"

# libicu is the one native dependency a self-contained .NET application needs
# beyond what python:slim already ships (globalization data). ca-certificates is
# already present in this base image, and SteamPrefill needs it to talk to Steam.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libicu76; \
    rm -rf /var/lib/apt/lists/*

# uid/gid 101 is NOT arbitrary: it is the numeric identity of the stock nginx
# image's `nginx` user, which vault-core's workers run as. vault-core and
# vault-api share the depot cache volume -- vault-core writes it (proxy_store),
# vault-api reads it (size calculation) and deletes from it
# (DELETE /v1/cache/{appid}) -- so the two containers must agree on ownership.
# Anything else means either a chmod 0777 cache or a service that cannot do its
# job. Named `vault` here for readability; only the number is load-bearing.
#
# HOME MUST BE A REAL, WRITABLE, PERSISTENT DIRECTORY (WP 1.9 review, blocker).
# The first revision used the usual service-account idiom, --home-dir
# /nonexistent, and that broke SteamPrefill completely. Measured, on all three
# invocation paths (docker run, docker compose run, docker compose exec):
#
#   System.TypeInitializationException: The type initializer for
#   'SteamPrefill.Settings.AppConfig' threw an exception.
#     System.UnauthorizedAccessException: Access to the path '/nonexistent' is denied.
#       at DirectoryInfo System.IO.Directory.CreateDirectory(string)
#       at SteamPrefill.Settings.AppConfig..cctor()
#       at bool SteamPrefill.Settings.AppConfig.get_DebugLogs()
#       at List<string> SteamPrefill.Program.ParseHiddenFlags()
#
# It is a STATIC CONSTRUCTOR reached from Program.Main before any argument is
# parsed and before any prompt, so it is not a login problem that only shows up
# once someone tries to log in -- the documented interactive login and every
# prefill job die identically, with a stack trace instead of a usable message.
#
# Two places have to agree, because different invocations derive HOME
# differently (measured: `docker compose run` inherits it from the passwd entry,
# `docker exec` fell back to /root -- unwritable for uid 101 either way):
#   1. the passwd entry            --home-dir below
#   2. the image environment       ENV HOME further down
RUN set -eux; \
    groupadd --system --gid 101 vault; \
    useradd --system --uid 101 --gid 101 --no-create-home \
            --home-dir /opt/steamprefill/home --shell /usr/sbin/nologin vault

# Runtime dependencies only -- requirements-dev.txt (pytest, httpx) is
# deliberately not installed: the test suite runs in CI/dev, not in production.
# --no-cache-dir keeps the wheel cache out of the layer.
# Repo-root-relative source path (see the header note on the build context).
COPY api/requirements.txt /tmp/requirements.txt
RUN set -eux; \
    pip install --no-cache-dir -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt

COPY --from=steamprefill /opt/steamprefill/SteamPrefill /opt/steamprefill/SteamPrefill
# WP VER-2: which SteamPrefill this image carries, for GET /v1/about (subprocess
# mode reads it here, vault-runner reports it from here in queue mode). The
# global build arg above, so it cannot drift from the release stage 1 fetched.
ARG STEAMPREFILL_VERSION
ENV STEAMPREFILL_VERSION="${STEAMPREFILL_VERSION}"
# Repo-root-relative (see header note): this used to be plain `vault_api`
# when the build context was api/ itself.
COPY api/vault_api /app/vault_api
# The built web UI (Phase 4a, WP 4a.1-4a.8), baked into the image rather than
# left mount-only: mount-only cannot survive a `docker pull` of a published
# image (WP 5.5, ghcr.io) -- there is no repo checkout on the pulling host to
# mount web/ from, so packaging it INTO the image is the only option that
# still works after publish. web/ is a repo-root sibling of api/, hence the
# build-context move documented at the top of this file. The chown -R
# .../app below (RUN block) already covers this path -- no extra chown needed
# here.
COPY web /app/web

# Mount points, all owned by the service user:
#   /data                     -> SQLite database (own named volume)
#   /opt/steamprefill/Config  -> SteamPrefill's session + selection state (own
#                                named volume; this is where the Steam login
#                                lives after the one interactive
#                                `docker compose run` -- see deploy/README.md).
#                                SteamPrefill resolves Config/ relative to its
#                                own executable, hence this exact path.
#   /opt/steamprefill/home    -> HOME for uid 101 (own named volume). See the
#                                blocker note above for why this must exist and
#                                be writable at all. It gets its own volume
#                                rather than living inside Config/ because what
#                                lands here is a regenerable manifest/depot
#                                cache that grows, while Config/ holds a Steam
#                                session that is small, secret and worth backing
#                                up -- deliberately different lifecycles, so
#                                "back this up" and "you may delete this" stay
#                                separable advice (deploy/README.md).
#   /vault                    -> the shared depot cache volume (same volume
#                                vault-core mounts). Only vault-core seeds an
#                                empty named volume: vault-api's mount is
#                                nocopy in named-volume mode only; a
#                                VAULT_CACHE_PATH bind carries no options
#                                (deploy/compose.yaml). cache/depot is
#                                still pre-created here, same layout as
#                                vault-core's image, for bind-mount/standalone
#                                use, and so vault-api's deletion guard (which refuses a
#                                cache root with no depot/ dir) is satisfied on a
#                                fresh install.
RUN set -eux; \
    mkdir -p /data /opt/steamprefill/Config /opt/steamprefill/home /vault/cache/depot /vault/tmp; \
    chown -R 101:101 /data /opt/steamprefill /vault /app; \
    # Assert the two halves of the HOME fix agree with each other and with the
    # directory that actually exists -- a build-time guard, because getting this
    # wrong is silent until someone tries to prefill.
    test "$(getent passwd 101 | cut -d: -f6)" = "/opt/steamprefill/home"; \
    test "$(stat -c '%u:%g' /opt/steamprefill/home)" = "101:101"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Must match the passwd entry above; see the blocker note there for why
    # both are needed. Never point this at /nonexistent, /root, or any path the
    # image does not own as 101:101.
    HOME=/opt/steamprefill/home \
    VAULT_DB_PATH=/data/vault.db \
    VAULT_CACHE_ROOT=/vault/cache \
    VAULT_STEAMPREFILL_PATH=/opt/steamprefill/SteamPrefill \
    # Web UI static directory (packaging WP). config._default_web_dir()'s
    # relative computation climbs three dirname() calls from THIS file's own
    # location (api/vault_api/config.py -> api/vault_api -> api/ -> repo
    # root) to find web/ as a repo-root sibling of api/ -- correct for a
    # native checkout, but wrong in here: vault_api/ lives at /app/vault_api
    # in this image (see the COPY above), so that same climb lands on `/`
    # and looks for `/web`, which does not exist. Setting VAULT_WEB_DIR
    # explicitly to the actual COPY target sidesteps the wrong computation
    # entirely rather than trying to make the relative math agree with the
    # image's layout.
    VAULT_WEB_DIR=/app/web \
    DOTNET_CLI_TELEMETRY_OPTOUT=1

# VAULT_API_KEY is deliberately NOT given a default here. vault-api raises
# RuntimeError at startup without it (api/vault_api/config.py), which is the
# intended behaviour: a shipped default key is a shipped vulnerability.
# deploy/compose.yaml makes it a required .env value.

USER 101:101
WORKDIR /app
EXPOSE 8080

# /v1/health is the one unauthenticated route by design (api/README.md "Auth"):
# fixed body, no data, explicitly meant for exactly this kind of probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=3).status == 200 else 1)"]

# --- Build identity (WP VER-1) -----------------------------------------------
# The release version and commit this image was built from, set by
# .github/workflows/publish.yml (`VAULT_VERSION` = the tag without its `v`,
# e.g. 0.1.0-rc8; `VAULT_COMMIT` = the full commit SHA). A local build
# (`docker build`, deploy/compose.yaml, deploy/tests/verify-stack.sh) gets the
# defaults `dev` / `unknown`, so a hand-built image never claims a release.
# vault-api reads VAULT_BUILD_VERSION at startup and serves it as
# `server_version` (vault_api.build_info(); api/README.md "Build version");
# vault-runner runs this same image. Declared LAST, in the final stage, after
# every RUN and COPY: a changed build-arg value invalidates the cache of every
# later RUN, so pip, apt and the SteamPrefill fetch above stay cached across
# releases. Not validated here: ENV can be overridden at `docker run` time,
# so vault_api validates what it reads and falls back when it is unusable.
ARG VAULT_VERSION=dev
ARG VAULT_COMMIT=unknown
ENV VAULT_BUILD_VERSION="${VAULT_VERSION}" \
    VAULT_BUILD_COMMIT="${VAULT_COMMIT}"
LABEL org.opencontainers.image.version="${VAULT_VERSION}" \
      org.opencontainers.image.revision="${VAULT_COMMIT}"

# EXACTLY ONE uvicorn worker, on purpose and permanently. vault-api runs a single
# background job-queue thread per process and repairs orphaned 'running' jobs at
# startup; a second process would fail the first one's genuinely running job and
# run two prefill workers against one SQLite file (api/README.md "Worker
# lifecycle"). Do not add --workers.
# --no-proxy-headers (WP API-FIX-2, N4): uvicorn defaults to trusting
# X-Forwarded-For from 127.0.0.1 and rewriting request.client from it.
# routers/agent.py records request.client as the agent's identity address and
# promises it is NEVER header-derived; this flag is what makes that true.
CMD ["python", "-m", "uvicorn", "vault_api.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
