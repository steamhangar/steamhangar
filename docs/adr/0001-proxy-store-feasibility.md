# ADR-0001: Plan B confirmed — nginx proxy_store with path-faithful depot storage

Date: 2026-08-05
Status: Accepted (Phase 0 gate decision by the project owner)

## Context

Phase 0 existed to answer one question before any product code: is a
purpose-built nginx cache with `proxy_store` (path-faithful storage under
`/cache/depot/<id>/...`) viable for real Steam traffic — or do we fall back
to Plan A (unmodified LanCache plus a manager layer)? The known HIGH risk
was `proxy_store` only storing complete responses vs. clients using Range
requests (plan §9).

## Evidence (WP 0.1–0.6, all committed under poc/)

- **URI scheme:** 100% conformance to `/depot/<id>/{chunk,manifest,patch}/…`
  across three real clients (Windows Steam, SteamPrefill 3.7.1, Linux Steam
  on Ubuntu 26.04) and ~17k logged requests. Depot-structured storage works
  as designed.
- **Range risk defused:** real clients sent ZERO Range headers across all
  runs; the tested CDN edges ignore Range on cache miss and always return
  full 200 bodies. Warm-cache range requests are served correctly by nginx
  static file handling.
- **Cache behavior:** 100% HIT on warm re-downloads, disk-limited delivery,
  cross-client sharing confirmed (chunks cached by the Windows client
  served as HITs to the Linux client).
- **SteamPrefill:** auto-detects the cache and prefills path-faithfully
  (filesystem layout cross-check PASS).
- **vault-dns approach validated:** wildcard DNS rewrite captures the Linux
  client; AAAA/IPv6 leak identified and closed (see below).
- **Store-vs-passthrough measurement:** client-perceived miss-path
  difference within run-to-run noise; HIT ~120x faster than MISS.

## Decision

1. **Plan B is confirmed** for vault-core: purpose-built nginx with
   `proxy_store`, path-faithful depot storage. The Plan A fallback is
   retired (stays documented here as the road not taken).
2. **Miss handling: HYBRID.** vault-core stores on miss (synchronous
   `proxy_store`, immediate benefit for the next client) AND a cache miss
   raises a signal to vault-api that triggers a prefill job completing the
   affected app. Implementation is staged: store-on-miss ships in Phase 1
   (it is the PoC-proven base); the miss→prefill trigger lands in Phase 3
   together with the scheduler/job infrastructure.

## Production requirements discovered in Phase 0 (binding for Phase 1+)

1. **LanCache heartbeat contract:** `GET /lancache-heartbeat` must answer
   with an `X-LanCache-Processed-By` header — SteamPrefill refuses to
   prefill without it.
2. **Store guards:** strip the client `Range` header upstream and store
   only status-200 responses, closing the partial-response corruption
   window for CDN edges not yet observed.
3. **`?nocache=1` bypass:** honor SteamPrefill's cache-busting speed probes
   by bypassing the stored file (LanCache contract), instead of serving
   the cached object.
4. **Upstream design:** do not hardcode a single CDN edge. Honor the client
   Host header (request-time resolution via an explicit resolver), use
   short connect timeouts and retry — dead upstream IPs caused real 42s
   stalls and 502 waves in Phase 0.
5. **Manifests do not dedupe by URL:** manifest requests carry per-request
   codes in the path; identical manifests get stored repeatedly. Manifest
   storage/GC needs its own strategy (small files; revisit in Phase 3 GC).
6. **vault-dns must pair `address=` with `local=`:** modern dnsmasq (2.9x)
   forwards AAAA queries upstream otherwise, silently re-opening the IPv6
   bypass the container exists to close.
7. **Bypass detection (A12) must account for Steam Local Network Game
   Transfers:** clients can legitimately receive content P2P from LAN
   peers; low cache traffic alone is not evidence of a DNS/IPv6 bypass.

## Consequences

- Phase 1 (vault-core + vault-api MVP) may start; the production nginx
  config derives from `poc/conf/nginx.conf` plus requirements 1–4 above.
- One revision to the plan's §5 narrative: the miss path is store-first
  (hybrid), not transparent-passthrough-first as originally leaned.
- The Linux-client quirk documented upstream is outdated (current clients
  perform lancache discovery); hosts-file mode remains Windows-only in
  docs, DNS mode stays the recommended path for multi-device LANs.

## Addendum (2026-09-30): hosts-file mode is not Windows-only

The last consequence above was written before its own evidence was drawn
through: the same WP 0.6 finding that retired the Linux-client quirk also
removes the reason hosts-file mode was scoped to Windows. The current
Linux/SteamOS Steam client performs the `lancache.steamcontent.com`
lookup, so a hosts entry works there too, and vault-agent's hosts mode
shipped platform-neutral in Phase 2 (`agent/README.md`, "The Linux-client
finding"). Read "hosts-file mode remains Windows-only in docs" as
superseded: the mode is for a single gaming PC, Windows or Linux/SteamOS;
DNS mode stays the recommended path for multi-device LANs.
