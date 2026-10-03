# SteamHangar vault-dns -- optional bundled DNS redirector (Phase 1, WP 1.9).
#
# Only needed by deployments that do NOT already run a local DNS server
# (docs/PROJECT_PLAN.md §10 mode 2). Enabled behind a Compose profile:
#     docker compose --profile dns up -d
#
# Build from the repo's dns/ directory:
#     docker build -t steamhangar/vault-dns:0.1.0 dns/
#
# *** EXPOSURE WARNING -- READ deploy/README.md BEFORE PUBLISHING PORT 53 ***
# This is a forwarding resolver with no source-address ACL (dnsmasq has no such
# concept, and the container cannot add one -- see dns/README.md "Exposure").
# Reachable from the internet it becomes an open resolver, i.e. a DNS
# amplification/reflection weapon pointed at third parties from this homelab's
# IP. Port 53 must be published on a specific LAN host IP
# ("192.168.1.50:53:53/udp"), NEVER a bare "53:53/udp" (= 0.0.0.0 = every
# interface). deploy/compose.yaml defaults this to 127.0.0.1 for exactly that
# reason: a misconfigured deployment must fail closed (nothing can reach it),
# never open.
#
# --- Base image pin ----------------------------------------------------------
#   alpine:3.23.5
#   digest resolved 2026-08-05 via `docker pull` + `docker inspect --format
#   '{{index .RepoDigests 0}}'`.
FROM alpine:3.23.5@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40

LABEL org.opencontainers.image.title="SteamHangar vault-dns" \
      org.opencontainers.image.description="Optional dnsmasq that points *.steamcontent.com at vault-core and closes the AAAA bypass" \
      org.opencontainers.image.source="https://github.com/steamhangar/steamhangar" \
      org.opencontainers.image.licenses="Apache-2.0"

# Package versions are pinned exactly, matching the pinned base image, so the
# built artifact is reproducible and an upstream change is a visible diff here
# rather than a silent difference between two builds of the "same" Dockerfile.
# Consequence, stated plainly: when Alpine ships a security update these exact
# versions disappear from the repository index and this build FAILS until the
# pins are bumped. That is the intended trade-off (a loud, reviewable bump beats
# an invisible drift) -- bump the base image digest and these versions together.
#   dnsmasq          2.91-r1        (alpine v3.23 main)
#   gettext-envsubst 0.24.1-r1      (alpine v3.23 main; provides /usr/bin/envsubst
#                                    without pulling in all of gettext)
RUN set -eux; \
    apk add --no-cache \
        dnsmasq=2.91-r1 \
        gettext-envsubst=0.24.1-r1; \
    dnsmasq --version | head -1

# The template is WP 1.8's reviewed artifact and is copied in verbatim -- this
# image renders it, it does not reinterpret it.
COPY dnsmasq.conf.template /etc/vault-dns/dnsmasq.conf.template
COPY docker-entrypoint.sh /usr/local/bin/vault-dns-entrypoint.sh

RUN set -eux; \
    chmod 0755 /usr/local/bin/vault-dns-entrypoint.sh; \
    # Alpine's dnsmasq package ships an /etc/dnsmasq.conf; the entrypoint renders
    # its own config to /run/vault-dns/dnsmasq.conf and passes --conf-file
    # explicitly, so remove the stock one rather than leave two plausible
    # candidates in the image.
    rm -f /etc/dnsmasq.conf; \
    mkdir -p /run/vault-dns

EXPOSE 53/udp 53/tcp

# Asks the running dnsmasq itself for a name inside the redirected zone and
# requires the answer to be the configured cache IP. This proves the thing that
# actually matters (the redirect is live), not merely that a process is running.
# The synthetic subdomain exists only for this probe -- the wildcard covers it.
# Exact-line, fixed-string match (review N4): a bare `grep -q "$CACHE_IP"`
# treated the dots as regex wildcards and matched any longer address with the
# same prefix (192.168.1.5 inside 192.168.1.50), so a wrong answer could read
# as healthy. busybox nslookup prints each A answer as "Address: <ip>".
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD nslookup -type=a healthcheck.steamcontent.com 127.0.0.1 2>/dev/null | grep -qxF -- "Address: $CACHE_IP" || exit 1

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
# (docs: deploy/README.md "Build version").
ARG VAULT_VERSION=dev
ARG VAULT_COMMIT=unknown
ENV VAULT_BUILD_VERSION="${VAULT_VERSION}" \
    VAULT_BUILD_COMMIT="${VAULT_COMMIT}"
LABEL org.opencontainers.image.version="${VAULT_VERSION}" \
      org.opencontainers.image.revision="${VAULT_COMMIT}"

ENTRYPOINT ["/usr/local/bin/vault-dns-entrypoint.sh"]

# --keep-in-foreground: no daemonising, so the container's lifetime is dnsmasq's.
# --log-facility=-    : log to stderr instead of syslog (which goes nowhere in a
#                       container) -- this is what makes `docker logs` and the
#                       json-file driver's rotation limits work. NOTE the config
#                       itself keeps log-queries OFF (dns/README.md "Privacy
#                       note"): this flag chooses the destination, not the
#                       verbosity.
# --user/--group      : dnsmasq binds :53 as root and immediately drops to this
#                       unprivileged account (created by the Alpine package).
CMD ["dnsmasq", \
     "--keep-in-foreground", \
     "--log-facility=-", \
     "--user=dnsmasq", \
     "--group=dnsmasq", \
     "--conf-file=/run/vault-dns/dnsmasq.conf"]
