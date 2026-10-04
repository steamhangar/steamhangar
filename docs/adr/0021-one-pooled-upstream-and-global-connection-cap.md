# ADR-0021: One pooled upstream for every MISS, with a global connection cap (CORE-FIX-4, roadmap D7)

Date: 2026-10-04
Status: Accepted 2026-10-04 (user decisions below, "Decisions"; proof
steps P1-P3 run by the operator the same day, "Proof (measured)"). Code
follows in WPs CORE-FIX-4a..4c, plus the separate, droppable CORE-FIX-4d.
It replaces ADR-0017's routing model (each MISS goes to the edge the
client named) and adds a global cap. ADR-0017's
mechanism (a resolving, pooled `upstream` group matched by name) is
reused, not reverted. Needs a freeze exception note in ADR-0016 (draft
text in "Implementation plan", step 12).

**Why a new ADR and not an ADR-0017 addendum.** ADR-0017 lists "the
`proxy_pass` line" and "`proxy_set_header Host $vault_upstream_host`"
under "What stays unchanged", and its decisions 1A and 3A size the pool
per listed edge. This ADR changes exactly those two lines and the sizing
model, and adds a new mechanism (`limit_conn`). An addendum would hide
that reversal under an accepted, shipped decision. ADR-0017 gets a
one-paragraph pointer to this file.

Evidence labels: **(repo)** read in this repository; **(nginx docs)**
`ngx_http_upstream_module` and `ngx_http_limit_conn_module` pages, read
2026-10-04; **(nginx src)** the 1.29.8 release tarball (the version
`core/Dockerfile` pins), read 2026-10-04; **(measured)** numbers from the
operator's production line as given in the work package brief, or from
the repo's PoC results where named; **(RFC)** quoted from memory, not
re-read for this ADR; **(inference)** reasoned, not measured.

## Context

Measured on the production DS-Lite/CGNAT line on 2026-10-04 with
`v0.1.0-rc9` (ADR-0017 pool on, `--max-threads 8`):

- **Steam client MISS test.** 1838x `connect() failed (113: Host is
  unreachable)` in about 16 s across 14 edge host names; 898 of them to
  `cache9-ams1`, which is in the pool list. Steam retried and finished
  (measured; `docs/handover/post-0.1.0-roadmap.md` D7).
- **Prefill (SteamPrefill, `--max-threads 8`, forced) of app 275850.** In
  10 minutes: 11287 upstream 502, 2705 upstream 200, 11368x `113`. All
  upstream traffic went to one address, 155.133.248.6, whose host name
  is not in `VAULT_UPSTREAM_POOL_HOSTS`. The host's conntrack table held
  8247 outbound connections to port 80. GitHub and ghcr were unreachable
  from that host during the run. Interim mitigation on production:
  `VAULT_UPSTREAM_RATE=4m`.

Why both happen (nginx src, repo; ADR-0017 "Context"):

1. A host without its own `upstream` group takes the resolver path of the
   variable `proxy_pass`. That path has no keepalive cache, so every
   chunk opens a new TCP connection. The prefill above is this case.
2. A pooled group keeps at most `keepalive 8` *idle* connections. In-flight
   requests above that still open new connections, and when they finish
   the surplus is closed: the cache is full, so
   `ngx_http_upstream_free_keepalive_peer` closes the least recently used
   connection. A client that we cannot throttle therefore churns
   connections even on a pooled edge. The client test is this case
   (LEARNINGS "Production rollout").
3. Behind a CGN every new connection takes a port mapping, and the mapping
   outlives the connection. RFC 5382 REQ-5 lets a NAT drop a closing
   ("transitory") TCP session only after at least 4 minutes (RFC). The
   quota is per subscriber, so the whole household runs out.

The model that ties the numbers together (inference): mappings held
≈ new connections per second × time a mapping lingers (*r × L*).

- Prefill: 2705 + 11287 ≈ 14.0k upstream results in 600 s ≈ 23/s.
  8 threads and no reuse give r = 8 / d. With d ≈ 0.35 s per cold chunk
  (PoC median cold MISS 0.30-0.32 s, `poc/MISS-HANDLING-FINDINGS.md`)
  that is about 23/s, the same as measured. 8247 conntrack entries / 23
  per second ≈ 6 minutes, which fits an L of a few minutes.
- The only safe point measured on this line: 50 parallel new connections
  work, 200 fail about half the time (ADR-0017). To keep r × L under 50
  with L ≥ 240 s, r must stay below about **0.2 new connections/s**.
  Without reuse that means 0.2 chunks/s. No cap on concurrency or bytes
  gets anywhere near that.
- `VAULT_UPSTREAM_RATE=4m` limits bytes, not connections. With the PoC's
  prefill average of about 184 KiB per chunk (179.95 MiB / 1003 MISS,
  `poc/steamprefill/RESULTS-STEAMPREFILL-20260804-195348.md`), 4 MiB/s
  still allows about 22 chunks/s, which is about 22 new connections/s
  (inference; whether the interim mitigation helped is not measured).

Requirements from the operator's documentation session (2026-10-04),
numbered as used below: (1) log the upstream host name so edges can be
attributed; (2) keepalive not bound to a fixed host list; (3) a global
hard cap on concurrent AND new upstream connections, so neither a
prefill nor a Steam client can drain the household's CGN quota; (4) a
verify case with an edge outside the pool list.

**Conclusion (inference): a hard cap on concurrency is necessary, but it
is not enough.** The cap bounds in-flight connections. Only reusing
connections bounds new ones. So every MISS must go through a pool whose
idle capacity is at least as large as the concurrency cap. Then
connections are only opened at warm-up, after `keepalive_timeout` or
`keepalive_requests`, or when the edge closes one.

## What nginx OSS 1.29.8 can do (research question a)

| Feature | Behaviour | Label |
|---|---|---|
| `server <name> resolve` in an `upstream` with `zone` | One peer per A record, re-resolved every `valid` (30 s) through the http-level `resolver`. Already in use (ADR-0017). | nginx src, repo |
| `keepalive N` (+ `keepalive_timeout`, `keepalive_time 1h`, `keepalive_requests 1000`) | At most N idle connections per group per worker. When the cache is full, a released connection evicts the oldest idle one. Reuse is matched by peer address. | nginx src (`ngx_http_upstream_keepalive_module.c`) |
| `max_conns=N` on `server` | In OSS since 1.11.5 (`CHANGES`). Counts *active* connections per **peer**, so per resolved A record, not per group; idle pooled connections do not count. When every peer is at the limit, `get_round_robin_peer` returns `NGX_BUSY`, nginx logs `no live upstreams` and calls `ngx_http_upstream_next(..., FT_NOLIVE)`. `proxy_next_upstream` cannot select `FT_NOLIVE`, so there is **no retry: immediate 502**, and no connect is attempted. Works only in explicit groups; the resolver path has no peers to limit. | nginx docs, nginx src (`ngx_http_upstream_round_robin.c` ~733, `ngx_http_upstream.c` ~1617, ~4667) |
| `queue` (wait for a free `max_conns` slot) | "available as part of our commercial subscription"; not in the OSS source. | nginx docs, nginx src |
| `limit_conn_zone` + `limit_conn` | Counts requests being processed, per key. A constant key makes it one global counter. Over the limit, nginx answers at once with `limit_conn_status` (default **503**, settable 400-599) and logs `limiting connections by zone "..."`. An empty key is not counted. `$limit_conn_status` (PASSED/REJECTED) can be logged. `limit_conn_dry_run` exists. | nginx docs, nginx src (`ngx_http_limit_conn_module.c`) |
| `limit_conn` inside `@miss` | `try_files` jumps to a named location at the `location_rewrite` phase, so rewrite (the 403 `if`) runs first and preaccess (`limit_conn`) runs after it. `location /depot/` has no limit, which leaves `r->main->limit_conn_status` unset, so `@miss` still counts. Forged hosts (403) are never counted. The count is released when the request ends, which is after the upstream connection was released. So it is an upper bound on upstream in-flight. | nginx src (`ngx_http_named_location`, `ngx_http_limit_conn_handler`) |
| `limit_req` | Rate (r/s), not concurrency. With `burst` and no `nodelay` it *delays* excess requests, which is the only queue OSS has. It runs before `limit_conn` in preaccess, so a delayed request does not hold a `limit_conn` slot. | nginx src (`auto/modules` order, `ngx_http_init_phase_handlers` reverse loop) |
| `proxy_next_upstream` | A failed connect (`error`) is retried on the *next peer*, up to `min(peers, proxy_next_upstream_tries 2)`. A single-address group gets no retry (LEARNINGS). Each retry is another connect. | nginx src, repo |

How the two clients react to an immediate 5xx (repo, measured): the Steam
client got hundreds of 502s in the 2026-10-04 test (each `113` ends as a
502 to the client), retried, and finished. SteamPrefill 3.7.1 retries
failed chunks for up to two more rounds, at once and with `?nocache=1`
(handover "Context"). A cap smaller than `VAULT_PREFILL_MAX_THREADS`
would therefore fail chunks of every prefill. A 503 from the cap
specifically, and 429, are not measured for either client (inference: the
Steam client treats a 503 like the 502s it already survives).

## Can one edge serve any chunk? (research question b)

- **Measured, repo (Phase 0, 2026-08-04/05).** The PoC (`poc/conf/
  nginx.conf`, WP 0.1-0.4) sent **every** MISS to one fixed edge with a
  fixed Host header, no matter which host the client asked for:
  `proxy_set_header Host dist-fra1.discovery.steamserver.net;
  proxy_pass http://dist-fra1.discovery.steamserver.net$request_uri;`.
  The first real Steam client download (WP 0.3) and the SteamPrefill run
  (WP 0.4: 1003 MISS, 179.95 MiB, several depots) were served through it.
  `poc/README.md` also records byte-identical content for
  `Host: lancache.steamcontent.com` and `Host: dist-fra1...` on the same
  IP.
- **Repo, 2026-08-05.** `lancache.steamcontent.com`, the name Valve
  publishes for LAN caches, resolves through a CNAME chain
  (`origin-tier2.steampipe.steamcontent.com` -> ... ->
  `dist-fra1.discovery.steamserver.net`) (`core/README.md` "Host-header
  allowlist"). Valve's own LAN-cache name therefore points at this tier.
- **Measured, 2026-10-04.** The prefill put all of its traffic on one
  address, and the client spread one app over 14 names. Chunks are
  content-addressed by `/depot/<id>/chunk/<sha>`, so any edge can serve
  any chunk.
- **Tokens.** The WP 0.3 client log had 0 non-conforming URIs: no query
  string, so no CDN auth token on Valve-edge chunk URLs (repo, one client,
  2026-08). Re-checked on production on 2026-10-04: no token anywhere in
  the vault-core log (P3, "Proof (measured)").
- **Host header.** The PoC evidence holds for the shape "Host = the name
  we dial". Sending the *client's* Host to a *different* edge is only
  supported by the byte-identical observation above, which is one IP and
  two names (inference beyond that).
- **Geo.** `dist-fra1` is Frankfurt; the 2026-10-04 traffic went to Valve
  addresses in Amsterdam (155.133.248.x, the `-ams1` names). An operator
  elsewhere picks a nearer `dist-*` name. The name at the end of the
  public CNAME chain of `lancache.steamcontent.com` is the one Valve's DNS
  hands out for the operator's location (inference: the `akadns` hop is
  a geo-DNS hop).

**Limit of the proof, and the check that closes it.** Edge mode sends the
edge's own name as `Host` for every client name (`proxy_set_header Host
$vault_upstream_target`). The byte-identical evidence in P2 covers one IP
and two names (`dist-fra1` and `cache9-ams1`, one chunk). That every other
client name, every depot and every chunk behaves the same is an inference
beyond it, and P3 (no token tied to an edge) is an observation of one log,
not a guarantee. The check after rollout is therefore explicit: with
edge mode on, a client update of an app that is not cached must finish
without a hash mismatch, and the `upstream_status` field of the vault-core
log must show no 4xx/5xx for `host=` names other than the edge beyond the
pre-change level; one such status that only appears in edge mode means
B1 does not hold for that name and the operator sets `VAULT_UPSTREAM_EDGE=` empty.

## Options

### A: keep one group per name, add caps

ADR-0017 unchanged, plus a global `limit_conn` in `@miss`, plus
`max_conns`/`keepalive` raised to the cap per listed group, plus a
`limit_req` rate limit on unlisted names (key empty for listed names).

- Pro: smallest change; the client's choice of edge is kept.
- Con: unlisted names (13 of the 14 in the client test; the prefill's
  edge) still open one connection per chunk. Keeping them under 0.2/s
  makes them unusably slow, and a looser limit drains the quota again
  (Context). It does not meet requirement (2). The list goes stale.

### B: send every MISS to one pooled upstream, plus a global cap (recommended)

All allowed MISSes go to one configured edge name, `VAULT_UPSTREAM_EDGE`,
default `dist-fra1.discovery.steamserver.net`. The edge is an ADR-0017
style group (`resolve`, `zone`, `max_fails=0`), so failover across its
A records comes for free. The Host header becomes that name (**B1**, the
PoC shape). The group holds `keepalive C`. One `limit_conn` in `@miss`
with a constant key caps all MISSes at **C** in flight.

- Open sockets ≤ C in flight + C idle = **2C**. One peer: ≤ C, because a
  new connection is only opened when no idle one exists. New connections
  happen only at warm-up, after `keepalive_timeout 50s` idle, every
  `keepalive_requests 1000` per connection, or when the edge closes one.
  With C = 16 and d = 0.35 s that is about 16 / (1000 × 0.35) ≈ 0.05/s
  steady state, plus at most 2C per burst after an idle pause. So
  r × L ≤ 2C + 0.05 × 240 ≈ 44 at C = 16 (inference), against the 50
  measured as safe. It is independent of how many names a client uses.
  The 50 is the household's whole budget, so it must also hold the HTTPS
  passthrough on port 443 (ADR-0020): up to 256 concurrent sessions at the
  current stream cap, each a new mapping, and the cap only meets the
  budget once CORE-FIX-4d lowers it to 32 total. Until 4d lands, the
  44 above covers the HTTP path alone and the 443 sessions come on top.
- **ASSUMPTIONS behind the 2C bound (not measured, to check after the
  rollout):** (a) the edge accepts about `keepalive_requests 1000`
  requests per connection before it closes it; a lower limit raises the
  steady churn above the 0.05/s used here; (b) warm-up bursts of up to 2C
  new connections happen at most once per linger window (after an idle
  pause longer than `keepalive_timeout`), not several times within it;
  (c) the pool's idle connections spread unevenly over the C in-flight
  slots (nginx takes the most recently used idle one, which favours a
  small hot set, but a burst can still open up to C new ones while idle
  ones exist for other slots), so `keepalive 2C` is the cautious option if
  the post-rollout connect-time check shows new connections where reuse
  was expected. A cheap pre-rollout measurement: one `curl -v` loop of
  about 1100 sequential requests to the edge and a count of TCP connects
  (`ss`/conntrack on the test box) tells (a); the post-rollout connect-time
  histogram ("After rollout") tells (b) and (c).
- Pro: meets requirements (2) and (3) structurally; one name to resolve
  instead of a list; no stale list. Retries on a connect error move to
  the next A record, which single-address groups never did.
- Con: the Steam client's choice of edge (load balancing, geo) is
  overridden, and the household's misses all go to one Valve tier.
  Throughput depends on one tier: the PoC measured about 3 MB/s per
  stream to `dist-fra1` from another network (repo), so 16 streams ≈
  48 MB/s (inference). If the edge is down or NXDOMAIN, every MISS
  fails until it is back (502 `no live upstreams`); there is no fallback
  to the client's own name by design.
- **B2 variant:** several names in the group and the client's Host
  passed through. More throughput and diversity, but it relies on Valve
  edges ignoring a foreign Host, which only one observation supports.
  Not recommended without proof step P2b.

### C: a generated per-edge list (`cacheN-<pop>` × N)

- nginx OSS cannot create groups at run time (no API; `upstream` is
  config-only; nginx docs: dynamic configuration is commercial). The list
  would have to be every plausible name, e.g. 20 POPs × 20 numbers = 400
  groups: 400 DNS queries per 30 s, mostly NXDOMAIN (an NXDOMAIN group
  has no peers and answers `no live upstreams`). Idle capacity adds
  up per group (400 × K), so the socket bound needs the global cap anyway.
  Names outside the pattern stay unpooled.
- Con dominates: high DNS load, a list that still goes stale, and an
  unbounded idle total. Rejected.

### D: a local forward proxy with a connection pool (sidecar)

For example HAProxy: per-request DNS (`do-resolve` + `set-dst`), `http-reuse
always`, and a real request **queue** at `maxconn`. That is what nginx OSS
lacks.

- Pro: queues instead of rejecting; can preserve the client's edge.
- Con: a new image (pin, digest, CVE tracking), a new container, a second
  config grammar, its own fail-closed validation and test rig, and a new
  hop on the upstream path. vault-core's upstream is not under the egress
  lock (ADR-0011 covers vault-api; repo, verify-stack section 10 note), so
  this is not blocked, but a new egress-capable container widens the
  surface that ADR-0011 keeps narrow. It is a large freeze exception just
  before `v0.1.0`. Keep it as the fallback if B's throughput or the 503
  behaviour fails in production.

## Recommendation

**B1 + global cap**, all behind env with fail-closed validation:

1. Every allowed MISS goes to `VAULT_UPSTREAM_EDGE` (one name, both Host
   and dial target), pooled with `keepalive C`. An empty value keeps
   today's per-name path (ADR-0017 groups for listed names), as a
   rollback switch.
2. `limit_conn` with one constant key in `@miss`, C =
   `VAULT_UPSTREAM_MAX_CONNS` (default 16, 1..64, never off), status 503.
   It applies in both modes. In the per-name mode it bounds concurrency
   only, not churn (documented).
3. The access log gains `host="$host"` and `limit_conn=$limit_conn_status`
   (requirement 1). `upstream_addr` and `upstream_connect_time` are
   already there (ADR-0017 1b2), so reuse can be counted: a connect time
   of `0.000` means a reused connection (ADR-0017 "Prove before
   building" item 4, inference).
4. Verify cases: an edge outside any list, the cap enforced, failover
   between two A records, the legacy path (requirement 4).

`max_conns` is not used: `limit_conn` is exact, global, and works on both
paths. `max_conns` would add a second 502 path that counts per A record.

## Decisions (user, 2026-10-04)

All six answered with the recommended option:

1. **Option: B1 + global cap.** Every allowed MISS goes to one pooled
   edge name (dial target and Host header); one `limit_conn` in `@miss`.
   Option A (caps on the per-name model) and D (HAProxy sidecar) are not
   built; D stays the fallback if production shows B1's throughput or
   its 503 behaviour to be unacceptable.
2. **`VAULT_UPSTREAM_EDGE` is on out of the box** through the compose
   default `dist-fra1.discovery.steamserver.net`. Explicitly empty =
   rollback to the ADR-0017 per-name path (`VAULT_UPSTREAM_POOL_HOSTS`).
3. **Cap C = `VAULT_UPSTREAM_MAX_CONNS`:** default 16, range 1..64, no off
   switch. vault-core refuses to boot when C < `VAULT_PREFILL_MAX_THREADS`
   (forwarded to vault-core for that check; compose forwards
   `${VAULT_PREFILL_MAX_THREADS:-8}`, and the hook treats an empty or unset
   value as 8, vault-api's own default, so a lowered cap cannot slip
   through with the variable left empty).
4. **Status over the cap: 503** (nginx's default, set explicitly).
5. **443 passthrough budget:** lower `limit_conn vault_tls_total` from 256
   to 32 and `vault_tls_client` from 64 to 16, as the separate, droppable
   package CORE-FIX-4d (amends ADR-0020).
6. **Proof first:** done by the operator before any code; results below.
7. **Timing: before `v0.1.0`** (user, 2026-10-04). This supersedes the
   earlier "known limitation in the release notes, fix after the tag"
   decision for D7. The freeze exception is recorded in ADR-0016 (step 12)
   and lands together with the first code package, CORE-FIX-4a.

## Proof (measured, operator, production line, 2026-10-04)

- **P1 A records.** `dig +short A dist-fra1.discovery.steamserver.net
  @1.1.1.1` -> `162.254.197.9`, `162.254.197.25`: two peers, so a failed
  connect gets one retry on the other address
  (`proxy_next_upstream_tries 2`). `dig +short lancache.steamcontent.com
  @1.1.1.1` -> `origin-tier2.steampipe.steamcontent.com` ->
  `steampipe-origin-tier2.steamcontent.com` ->
  `cache-origin.steampipe.steamcontent.akadns.net` ->
  `dist-fra1.discovery.steamserver.net` -> the same two addresses. From
  the operator's line, Valve's own LAN-cache name ends at this edge,
  which confirms the default (the 2026-08-05 repo observation still
  holds).
- **P2 One edge serves a chunk another edge was asked for.** The same
  chunk `/depot/4358691/chunk/f1ea53af2e11db025852ba989c108bcadbfb1113`,
  fetched via public IP from `dist-fra1.discovery.steamserver.net`
  (Host = that name) and from `cache9-ams1.steamcontent.com` (Host = that
  name): both `200`, 11632 bytes, sha256
  `2f3667c293bd09ac51d69034fdf136955e1ca97cdbab70adf533fe2b32638f63` for
  both. This proves the B1 shape (Host = the name we dial). P2b, a foreign
  Host on an edge, was not run; B2 stays unproven and is not built.
- **P3 Tokens.** `grep -c 'uri="[^"]*token='` over the whole vault-core log
  -> `0`. No logged MISS URI carries a token, so no URL is tied to the
  edge it was issued for.

With two peers, option B's socket bound stays ≤ 2C (C in flight + C
idle) and ≤ C per peer (inference, nginx src).

## Proof recipe (as run; for re-checking or another operator's edge)

Pin public IPs as in ADR-0017 "Prove before building" step 0; never test
through the LAN rewrite.

- **P1 A records.** `dig +short A dist-fra1.discovery.steamserver.net
  @1.1.1.1` gives the number of peers. Also
  `dig +short lancache.steamcontent.com @1.1.1.1`: the last name of the
  chain is the geo-correct default.
- **P2 One edge serves the client's chunks.** Take 3 chunk URIs with a
  `host=` that is NOT the edge (event log field 6 = URI, field 8 = host).
  For each: `curl -s --resolve <edge>:80:$ip -H 'Host: <edge>' -o a.bin
  -w '%{http_code}\n' http://<edge><uri>` and `curl -s --resolve
  <orig>:80:$ip2 -o b.bin http://<orig><uri>`. Proof: both 200 and
  `sha256sum a.bin b.bin` identical. Against: a 403/404 from the edge.
  - **P2b (only for B2):** the same request to the edge's IP with
    `Host: <orig>`.
- **P3 Tokens.** `docker compose logs --no-log-prefix vault-core 2>&1 | grep -c 'uri="[^"]*token='`
  must be 0. A non-zero count means some URLs carry tokens bound to a
  host, and B needs a rethink.

## Implementation plan (follow-up code WPs, per the decisions)

Split for the ≤ 2 h rule, in this order:

- **CORE-FIX-4a** (core/, about 2 h): steps 1-6.
- **CORE-FIX-4b** (CI and verify-stack, about 2 h): steps 7-8.
- **CORE-FIX-4c** (deploy and docs, about 1 h): steps 9-12.
- **CORE-FIX-4d** (core/ stream block, about 1 h, separate and droppable,
  decision 5): step 13. 4a-4c must not depend on it; it can ship in the
  same rc or be dropped without touching them.

1. **`core/docker/28-vault-upstream-pool.sh`** (extend, keep one hook).
   It gets two new inputs, `VAULT_UPSTREAM_EDGE` and
   `VAULT_UPSTREAM_MAX_CONNS`, and a second output path argument (usage
   `[pool-out] [cap-out]`).
   - `VAULT_UPSTREAM_EDGE`: exactly one token. It goes through the
     existing host validation unchanged: lowercase, charset, label and
     name lengths, family suffix, not the marker. More than one token is
     refused.
   - `VAULT_UPSTREAM_MAX_CONNS`: digits only, no leading zero, 1..64.
     Empty means the default 16; `0`, `off` and garbage are refused (no
     off switch, decision 3). If `VAULT_PREFILL_MAX_THREADS` is set and
     valid, C below it is refused with a message naming both (decision 3).
     Garbage in that variable is ignored here, because vault-api validates
     it.
   - Edge mode render (`vault-upstream-pool.conf`):
     `upstream <edge> { zone vault_edges 256k; server <edge> resolve
     max_fails=0; keepalive <C>; keepalive_timeout 50s; }` plus
     `map $vault_upstream_host $vault_upstream_target { default <edge>; }`.
     If `VAULT_UPSTREAM_POOL_HOSTS` is also set, log one line saying it is
     ignored, and render no per-name group (an edge equal to a listed
     name would otherwise be a duplicate upstream).
   - Legacy render (edge empty): today's per-name groups, unchanged
     (ceiling 32 idle), plus `map $vault_upstream_host
     $vault_upstream_target { default $vault_upstream_host; }`.
   - Cap render (`vault-upstream-cap.conf`, both modes):
     `limit_conn vault_upstream_total <C>;`.
   - The self-check is extended: exactly one map with a non-empty
     default; in edge mode exactly one group, whose name equals the map
     default, with `keepalive <C>`; one `limit_conn` line with C ≥ 1; no
     DNS directive anywhere.
2. **`core/nginx/nginx.conf` and `core/docker/nginx.conf.template`**
   (identical edits):
   - http: `limit_conn_zone $server_port zone=vault_upstream_total:1m;`
     (the constant-key form the stream block already uses).
   - `@miss`, after the 403 `if`: `include vault-upstream-cap.conf;` and
     `limit_conn_status 503;` (decision 4).
   - `proxy_set_header Host $vault_upstream_target;` and
     `proxy_pass http://$vault_upstream_target$request_uri;`.
   - `log_format vault`: append ` host="$host"
     limit_conn=$limit_conn_status`.
   - The comments "Loop-safety reasoning" and "What stays unchanged" are
     updated.
   - Native static files: `core/nginx/vault-upstream-pool.conf` becomes
     the legacy empty render, which now includes the identity map, and
     the new `core/nginx/vault-upstream-cap.conf` is the default render
     (C = 16).
3. **`core/docker/40-vault-preflight.sh`.** Both includes are wired in; the
   cap file holds a positive `limit_conn`; in edge mode the map default
   equals `VAULT_UPSTREAM_EDGE`. The loop probe also resolves
   `VAULT_UPSTREAM_EDGE` and dies if it answers with vault-core's own
   address, because a loop on that name would turn every MISS into a 508.
4. **`core/docker/check-config-drift.sh`.**
   - New pins in both files: the zone line; the cap include and
     `limit_conn_status 503;` inside `@miss`; the new Host and
     `proxy_pass` lines (step 2c's retry pins unchanged); the two new log
     fields.
   - `cmp` of both native files against the hook's default renders.
   - Step 2f budget: replace the `MAX_IDLE_TOTAL` term with
     `2 × CAP_MAX` (CAP_MAX=64 read from the hook): 512 − 128 = 384 ≥ 256.
     The legacy ceiling of 32 is kept as its own pin.
   - Grep `$`-anchored consumers of `log_format vault` first (LEARNINGS:
     `poc/*/analyze.ps1`, `verify.ps1`, `core/tests/test-core.ps1`).
5. **`core/Dockerfile`.** COPY the new native file; the build-time
   self-check renders both modes; ENV defaults `VAULT_UPSTREAM_EDGE=""`
   and `VAULT_UPSTREAM_MAX_CONNS=16`. The compose default (step 9) turns
   edge mode on, per decision 2.
6. **`core/tests/test-upstream-pool-hook.sh`.** Edge mode, legacy mode,
   both set (ignored, logged), and the C bounds 1, 16 and 64. Refused:
   0, 65, 08, `off`, two edge tokens, a foreign family, the marker, and
   C < prefill threads.
7. **`.github/scripts/verify-core-nginx.sh`.** `nginx -t` on renders
   {edge, legacy} × C {1, 16, 64} and the refusals in the real image; the
   rate-cap pin stays; assert `limit_conn` appears only in `@miss`.
8. **`deploy/tests/verify-stack.sh` section 10** (the fake edge, the fake
   resolver and fixed IPs already exist):
   - 10c/10d become the **legacy** control (`VAULT_UPSTREAM_EDGE=` empty).
   - **New 10f, edge mode,** with `VAULT_UPSTREAM_EDGE=fake1.steamcontent.com`:
     N sequential MISSes with `Host: fake2.steamcontent.com` (outside every
     list) and N with `Host: fake3...` cost at most 2 connections in
     total. The fake edge logs the received Host: it must be `fake1`.
     All 2N chunks are stored.
   - **New 10g, cap,** with `VAULT_UPSTREAM_MAX_CONNS=4` and a slow fake
     path (the handler sleeps 3 s for one depot id): 12 parallel MISSes.
     The fake edge's maximum concurrent in-flight count is ≤ 4; exactly 8
     answers are 503 within 1 s; nothing is stored for them; the access
     log has 8 `limit_conn=REJECTED` lines, and every line has `host="`.
   - **New 10h, failover:** the fake resolver gives `fake1` two A
     records (the real edge has two, P1), one of them a closed port (ECONNREFUSED, the same
     `FT_ERROR` path as `113`). All requests answer 200; there is no
     `no live upstreams`.
   - `113` itself: an `iptables ... -j REJECT --reject-with
     icmp-host-unreachable` rule on the fake edge, if the test image has
     iptables and the run allows `NET_ADMIN`; otherwise only 111. The CGN
     quota and mapping linger cannot be simulated locally.
   - 508 loop: the resolver points the edge name at vault-core and
     expects 508 for every MISS.
   - Rate cap with edge mode on (ADR-0017 decision 5A carried over).
9. **`deploy/compose.yaml` and `deploy/.env.example`.** Forward
   `VAULT_UPSTREAM_EDGE` (`${VAULT_UPSTREAM_EDGE-dist-fra1.discovery.steamserver.net}`,
   decision 2: on out of the box, explicitly empty = rollback),
   `VAULT_UPSTREAM_MAX_CONNS` (`${VAULT_UPSTREAM_MAX_CONNS-}`) and
   `VAULT_PREFILL_MAX_THREADS` to vault-core. The `.env.example` stanza
   covers the dig one-liner for the geo-correct name, the cap, and "empty
   = legacy per-name path". Mark `VAULT_UPSTREAM_POOL_HOSTS` as
   legacy-mode only.
10. **Docs.** `core/README.md` new section "Upstream edge and connection
    cap" (mechanism, socket bound 2C, limits). `deploy/README.md`
    operator section plus troubleshooting rows (`503` storm = cap too
    low; `no live upstreams` = edge NXDOMAIN or loop). The measurement
    commands of "After rollout".
11. **Plan and learnings.** `docs/PROJECT_PLAN.md` §11 item 13: tick D7
    and rewrite the release-notes item (the known limitation becomes "fixed
    in the next rc; legacy mode keeps it"). Update the ADR-0017 pointer to the
    shipped state. `docs/LEARNINGS.md`:
    the r × L model, `max_conns` being per A record with an immediate 502,
    and `limit_conn` counting in a named location.
12. **ADR-0016 freeze note, draft text:**
    "Addendum 2026-10-0X — freeze exception: one pooled upstream and a
    global connection cap (WP CORE-FIX-4, roadmap D7). User decision
    2026-10-04: fix before `v0.1.0`. ADR-0021 accepted with the answers
    1 B1 + global cap, 2 edge on out of the box
    (`dist-fra1.discovery.steamserver.net`, empty = rollback), 3 C default
    16 in 1..64 with no off switch and boot refused below
    `VAULT_PREFILL_MAX_THREADS` (empty counts as 8), 4 status 503, 5 passthrough lowered to
    32/16 as CORE-FIX-4d, 6 proof run by the operator (P1-P3 green).
    Scope: core/ — `28-vault-upstream-pool.sh` (`VAULT_UPSTREAM_EDGE`,
    `VAULT_UPSTREAM_MAX_CONNS`, cap include), both nginx configs (zone,
    cap include, `limit_conn_status 503`, `$vault_upstream_target` in
    Host and `proxy_pass`, two log fields), the native static includes,
    `40-vault-preflight.sh`, `check-config-drift.sh`, `core/Dockerfile`,
    the hook test, `verify-core-nginx.sh`. deploy/ and docs (not
    frozen): compose forwarding, `.env.example`, verify-stack section 10,
    READMEs. Not in this exception: api/ (no change; the event sweep
    already counts only 2xx as success, `api/vault_api/event_sweep.py`),
    the stream block, except CORE-FIX-4d on its own line
    (`vault_tls_total` 256 -> 32, `vault_tls_client` 64 -> 16, droppable).
    Every other frozen-path change still needs its own decision."
13. **CORE-FIX-4d (decision 5, separate and droppable).**
    `limit_conn vault_tls_total` 256 → 32 and `limit_conn vault_tls_client`
    64 → 16 in both configs and wherever the pinned stream lists repeat
    them (grep `vault_tls_total`/`vault_tls_client` in `core/docker/`,
    `.github/scripts/` and `deploy/tests/`); `TLS_TOTAL` in drift step
    2f; an ADR-0020 addendum with the new numbers; the verify-stack TLS
    limit checks; the passthrough numbers in `core/README.md` and
    `deploy/README.md`. Freeze note: its own line in the step 12
    addendum.

## After rollout: what the operator measures (production line)

Repeat both 2026-10-04 scenarios: a client update of an app that is not
cached, and the forced prefill of app 275850.

- `docker compose logs --no-log-prefix --since 15m vault-core 2>&1 | grep -c 'Host is unreachable'`
  should be 0 (before: 1838 and 11368).
- `docker compose logs --no-log-prefix --since 15m vault-core 2>&1 | grep -c 'limiting connections by zone "vault_upstream_total"'`
  shows how often the cap bit.
- `docker compose logs --no-log-prefix --since 15m vault-core 2>&1 | grep -o 'upstream_connect_time=[0-9.]*' | sort | uniq -c | sort -rn | head`
  should be dominated by `0.000` (reused connections).
- `docker compose logs --no-log-prefix --since 15m vault-core 2>&1 | grep -o 'host="[^"]*"' | sort | uniq -c | sort -rn`
  attributes the requests by edge name (requirement 1).
- `docker compose exec vault-core netstat -tn | awk '$5 ~ /:80$/ && $6 == "ESTABLISHED"' | wc -l`
  during the download should be ≤ 2C.
- On the host: the conntrack count of vault-core's outbound :80
  connections (before: 8247) should be ≤ 2C plus a few closing ones.
- During the run, from another device in the house, a new HTTPS
  connection (e.g. `curl -sI https://github.com`) must succeed.
- `VAULT_UPSTREAM_RATE` can be removed only after this is green.

## Consequences and honest limits

- If B holds, the CGN load becomes a function of C, not of the client's
  behaviour or the edge list. Requirement (3) holds for new connections
  only to the degree reuse holds. An edge that answers
  `Connection: close` on every response would bring churn back, at most
  C/d per second. The 2026-10-02 measurement showed keep-alive (ADR-0017),
  and the after-rollout connect-time histogram shows it.
- Clients above C get 503s. The Steam client's handling of a sustained 503
  share is not measured. SteamPrefill is safe while C ≥ its threads and
  only one job runs.
- **The concurrency limit that bites: client + prefill together.** C is
  global, not per source. A prefill at its default 8 threads leaves
  C - 8 = 8 slots at C = 16; a Steam client updating several apps in
  parallel on top of it gets 503s for everything above that, and a
  prefill that finds the slots taken by a client can get 503s on its own
  chunks as well. The boot check (C ≥ prefill threads) only guarantees a
  prefill alone fits. Raise C (to at most 64, at the price of a larger 2C
  socket bound) or lower `VAULT_PREFILL_MAX_THREADS` if both are expected
  at the same time; running them one after the other needs no change.
- One edge name is a single point of dependency (DNS, Valve's tier).
  Rollback: set `VAULT_UPSTREAM_EDGE=` empty and recreate.
- Not addressed: IPv6 egress (D4), DNS traffic, the CM/WebSocket traffic
  of clients, and other household devices' own use of the quota.
