# SteamHangar vault-proxy -- the egress-lock allowlist proxy (WP EG-1, ADR-0011).
#
# This is the ONE way vault-api reaches anything beyond the LAN once the lock
# is in effect (deploy/compose.yaml's `vault-lan` network drops vault-api's
# default route; `vault-egress`, an `internal: true` network shared ONLY with
# this container, is its one remaining path out). tinyproxy forwards a
# request only if the destination host matches the filter file
# docker-entrypoint.sh renders at container start from VAULT_EGRESS_ALLOW
# (plus one baked-in host -- see that script's own comment for why).
#
# Never runs as root, at any point: tinyproxy's listen port (8888) is
# unprivileged, so unlike vault-core's nginx (which needs to bind :80 as root
# and then drop to uid 101) this container has no privilege-drop dance to do
# at all -- USER below applies for the ENTIRE container lifetime, build steps
# included past that line, which is also why cap_drop: ALL (deploy/compose.yaml)
# costs this service nothing: it never had a capability to lose.
#
# --- Base image pin ----------------------------------------------------------
# Deliberately the SAME digest dns/Dockerfile already pins (ADR-0011: reusing
# an already-vetted, already-pinned base beats adding a second one to track).
#   alpine:3.23.5
#   digest resolved 2026-08-05 via `docker pull` + `docker inspect --format
#   '{{index .RepoDigests 0}}'` (dns/Dockerfile's own record of this pin).
FROM alpine:3.23.5@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40

LABEL org.opencontainers.image.title="SteamHangar vault-proxy" \
      org.opencontainers.image.description="Allowlist-only forward proxy: the sole egress path for the egress-locked vault-api container" \
      org.opencontainers.image.source="https://github.com/steamhangar/steamhangar" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.1.0"

# Exact version pin, matching the pinned base image (same reasoning as
# dns/Dockerfile: an upstream Alpine security bump removes this exact version
# from the repository index and this build FAILS loudly rather than silently
# picking up different bytes under the same apk command).
#   tinyproxy   1.11.3-r0   (alpine v3.23 main; resolved via `apk policy
#                            tinyproxy` against this exact base digest)
RUN set -eux; \
    apk add --no-cache \
        tinyproxy=1.11.3-r0; \
    tinyproxy -v

# Shipped as a TEMPLATE (WP DEPLOY-FIX-2): the entrypoint renders
# /run/tinyproxy/tinyproxy.conf from it at every start, replacing the static
# default client `Allow <cidr>` line with VAULT_EGRESS_SUBNET. The template
# lives in /etc/tinyproxy/, which stays root:root 0755 with the template
# root:root 0444 (below): the running proxy, as the unprivileged `tinyproxy`
# account, can change its own RUNNING config in /run/tinyproxy/, but never
# the source that config is rebuilt from at each start.
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf.template
COPY docker-entrypoint.sh /usr/local/bin/vault-proxy-entrypoint.sh
# Sourced by the entrypoint above (round-2 review S2/N4) via
# `. "$(dirname "$0")/validate-hostname.sh"` -- MUST land in the same
# directory as the entrypoint script itself for that relative lookup to
# resolve, which is why this is a second COPY into the same target
# directory rather than a subdirectory.
COPY validate-hostname.sh /usr/local/bin/validate-hostname.sh
# Same rule for the subnet check (WP DEPLOY-FIX-2).
COPY validate-subnet.sh /usr/local/bin/validate-subnet.sh

# The apk package ships its own stock tinyproxy.conf (root, 0644). It is
# DELETED here (WP DEPLOY-FIX-2): the entrypoint renders the real config from
# our template at every start, and if that render is ever bypassed, tinyproxy
# must fail to start for lack of a config rather than run the stock one (fail
# closed). Only /run/tinyproxy/ is chowned to the `tinyproxy` account the
# package also creates (uid 100, gid 101): the entrypoint renders the config
# and the filter file there, and the pid file lives there too. That process
# runs as that uid for its entire life -- there is no root step at container
# start to chown anything for a first write the way core/Dockerfile's
# preflight does for vault-core's bind mount. /etc/tinyproxy/ is deliberately
# NOT chowned: root:root 0755, template root:root 0444, so nothing running in
# this container can replace the reviewed source.
RUN set -eux; \
    rm -f /etc/tinyproxy/tinyproxy.conf; \
    chmod 0755 /usr/local/bin/vault-proxy-entrypoint.sh /usr/local/bin/validate-hostname.sh /usr/local/bin/validate-subnet.sh; \
    mkdir -p /run/tinyproxy; \
    chown -R tinyproxy:tinyproxy /run/tinyproxy; \
    chown -R root:root /etc/tinyproxy; \
    chmod 0755 /etc/tinyproxy; \
    chmod 0444 /etc/tinyproxy/tinyproxy.conf.template

EXPOSE 8888

# No inbound host: this proxies FROM vault-api TO the internet, never the
# other way. A TCP connect to the port it listens on is a real liveness
# proof (tinyproxy accepted the bind and is answering), not a placeholder --
# busybox nc's -z (zero-I/O / scan) mode makes the connect-and-close without
# needing to speak HTTP or CONNECT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD nc -z 127.0.0.1 8888 || exit 1

# Applies for the rest of this file AND at container run time -- see the
# header comment above for why there is no root phase to drop out of here,
# unlike vault-core/vault-dns.
USER tinyproxy:tinyproxy

ENTRYPOINT ["/usr/local/bin/vault-proxy-entrypoint.sh"]
# -d: stay in the foreground (see core/Dockerfile-style containers -- a
# daemonizing default would make the container's lifetime detached from
# tinyproxy's own, and `docker stop` would have nothing left to signal).
CMD ["tinyproxy", "-d", "-c", "/run/tinyproxy/tinyproxy.conf"]
