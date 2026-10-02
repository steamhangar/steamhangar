# ADR-0017: Upstream keepalive pool per Steam CDN edge (CORE-FEAT-1, roadmap D1)

Date: 2026-10-02
Status: Proposed (draft; awaits user decisions on the open questions; code
only after tag v0.1.0, ADR-0016)

Evidence labels used below: **(repo)** read in this repository, **(nginx
docs)** `ngx_http_upstream_module` documentation and `CHANGES` as read on
2026-10-02, **(nginx src)** the 1.29.8 source read on 2026-10-02 (the version
`core/Dockerfile` pins), **(handover)** `docs/handover/post-0.1.0-roadmap.md`,
not re-verified here, **(inference)** reasoned, not measured.

## Context

The first production rollout (2026-10-01/02) ran on a DS-Lite line: IPv4
leaves through the provider's CGNAT, which answers ICMP host-unreachable
once the per-subscriber port quota is spent (RFC 6888 REQ-11, per
handover); nginx logs `connect() failed (113: Host is unreachable)`. One
prefill sent about 62k chunk requests in two minutes and 94% failed; single
requests always worked; from a container on the same host 50 parallel new
connections worked while 200 failed about half the time (handover
"Context"; `docs/PROJECT_PLAN.md` §11 item 13 A, CORE-FIX-2).

**Why vault-core opens one TCP connection per chunk (repo + nginx src).**
`core/nginx/nginx.conf::@miss` proxies with
`proxy_pass http://$vault_upstream_host$request_uri;` and the http-level
`resolver 1.1.1.1 ipv6=off valid=30s;`. With a variable `proxy_pass`,
`ngx_http_upstream.c::ngx_http_upstream_init_request` first searches the
configured `upstream` groups for the evaluated host; if none matches it
starts a resolver lookup and builds a throw-away peer set for that one
request (`ngx_http_upstream_create_round_robin_peer`). The keepalive cache
lives in `ngx_http_upstream_keepalive_module.c` and is attached only to
groups that have a server-level config (`init_main_conf` skips entries
whose `srv_conf == NULL`, the "implicit upstreams"), so the resolver path
never sees it. nginx 1.29.7 enabled keepalive by default for `upstream`
groups (`CHANGES` 1.29.7); the value `32 local` is in the docs ("Since
1.29.7, keepalive connections are enabled by default, with a default limit
of 32") and in `ngx_http_upstream_keepalive_module.c::
ngx_http_upstream_keepalive_init_main_conf`. That default does not reach
the resolver path either. Every MISS therefore costs one TCP
handshake and, behind a CGNAT, one port mapping.

**Stage 1 and stage 2.** WP CORE-FIX-2 (stage 1, in flight on another
branch; facts per the orchestrator's brief and the ADR-0016 addendum of
2026-10-02, which is not in this worktree) caps SteamPrefill's concurrency
with `--max-threads` (`VAULT_PREFILL_MAX_THREADS`, default 8) and changes
`@miss` to `proxy_next_upstream timeout http_502 http_503 http_504;
proxy_next_upstream_tries 2;`. That bounds the burst; it does not change the
one-connection-per-chunk cost. This ADR is stage 2: reuse upstream
connections so a download opens about as many connections as it has
workers. It helps Steam clients too: their concurrency is not ours to cap,
and they download through the same `@miss` (handover, D1).

## Mechanism (proposed)

One `upstream` group per known edge host, rendered at container start from a
list (source: open question 1), included at http level the way
`core/nginx/vault-upstream-rate.conf` is today (repo):

```nginx
upstream cache9-ams1.steamcontent.com {
    zone vault_edges 256k;                     # size: open, see "Consequences and honest limits"
    server cache9-ams1.steamcontent.com resolve max_fails=0;
    keepalive 8;                               # open question 3
}
```

**How the lookup matches (nginx src).** `ngx_http_proxy_module.c::
ngx_http_proxy_eval` parses the evaluated URL with `default_port 80` and
records `resolved->no_port = url.no_port`. `ngx_http_upstream_init_request`
then accepts a group when the host length and case-insensitive name match
and `(uscf->port == 0 && resolved->no_port) || uscf->port == resolved->port`.
An `upstream` block is registered with `no_port = 1` and port 0
(`ngx_http_upstream.c::ngx_http_upstream` sets `u.no_port = 1`), and nginx
strips `:port` from `$host` before the map runs (`core/README.md`
"Host-header allowlist"), so `$vault_upstream_host` never carries a port and
the group named exactly like the edge matches. Any host without a group
takes today's resolver path unchanged. This agrees with the handover's
"no port = 0" note.

**What stays unchanged (repo).** The `proxy_pass` line; the Host allowlist
map and its 403; `proxy_set_header Host $vault_upstream_host`;
`X-SteamHangar-Hop "1"` and the 508 guard in `location /depot/`; the
Range/Accept-Encoding/If-Range stripping; the store guard and `proxy_store
$vault_store_path`; `proxy_limit_rate $vault_upstream_rate`. The marker
`lancache.steamcontent.com` maps to `dist-fra1.discovery.steamserver.net`,
so the list must name that edge, not the marker, to pool it.

**(a) HTTP/1.1 and the Connection header.** `@miss` already sets
`proxy_http_version 1.1;` and `proxy_set_header Connection "";` (repo,
`core/nginx/nginx.conf::@miss`), which is exactly what the keepalive
directive requires for HTTP (nginx docs). Nothing to add.

**(b) `max_fails` / `fail_timeout` once a group has several peers.**
`server <name> resolve` creates one peer per A record. nginx docs: with a
single server in a group, `max_fails` and `fail_timeout` are ignored and the
server is never unavailable; `ngx_http_upstream_round_robin.c::
ngx_http_upstream_free_round_robin_peer` confirms it (the `single` branch
resets `fails`). With two or more peers the defaults `max_fails=1
fail_timeout=10s` apply: one failed connect (CGNAT host-unreachable counts,
it is `NGX_PEER_FAILED`) disables that peer for 10 s, and when every peer of
the group is disabled `ngx_http_upstream_connect` logs `no live upstreams`
and the request ends in 502 before any connect is attempted, including the
single requests that always worked (nginx src). A stale pooled connection
that the edge closed is also freed with `NGX_PEER_FAILED`
(`ngx_http_upstream.c::ngx_http_upstream_next`), so it too would count.
**Decision (engineering, not a user question): `max_fails=0`** (docs:
"disables the accounting of attempts"). Each request still fails fast on
`proxy_connect_timeout 3s`, and a bad IP leaves with the next re-resolve,
not via a failure counter; the default would turn one CGNAT burst into
10 s of `no live upstreams` for every request to that edge. Whether a
Valve edge name returns more than one A record is unverified here.

**(c) Which resolver the group uses.** `resolve` needs a `resolver` "in the
http block or in the corresponding upstream block" (nginx docs; the
upstream-level directive exists since 1.27.3, `CHANGES`). In
`ngx_http_upstream_round_robin.c::ngx_http_upstream_init_round_robin` a
group without its own `resolver` takes the http-level one, including its
`ipv6=off valid=30s`, and merges `resolver_timeout` the same way (5 s
here). No second resolver line is needed; the rendered groups must NOT
carry one, so `${VAULT_RESOLVER}` stays the single place that names a DNS
server (drift guard delta 4, `core/docker/check-config-drift.sh`).

**(d) Loop safety still holds.** `ngx_http_upstream.c::
ngx_http_upstream_server` sets `u.no_resolve = 1` when `resolve` is given,
so the name is not looked up through the OS resolver at config-parse time,
and the run-time lookups go through nginx's own resolver
(`ngx_http_upstream_zone_module.c::ngx_http_upstream_zone_resolve_timer`,
`ngx_resolve_start(uscf->resolver, ...)`). The hosts file is never
consulted, which is the property `core/nginx/nginx.conf` "Loop-safety
reasoning" item 1 relies on. The 508 guard does not depend on how the peer
was chosen: `@miss` still stamps `X-SteamHangar-Hop` and a request arriving
with it is answered 508 before `@miss` (repo). The boot probe in
`core/docker/40-vault-preflight.sh` is unchanged. A rewrite appearing
after boot reaches a pooled group at its next re-resolve (every `valid`,
30 s) and then loops once into 508 per MISS, the same outcome as today.

**(e) Keepalive timers (nginx docs, 1.29.8 defaults confirmed in
`ngx_http_upstream_keepalive_module.c::init_main_conf`).**
`keepalive_timeout 60s` (idle connection kept this long), `keepalive_time
1h` (connection closed after this age), `keepalive_requests 1000`. A
connection goes back to the pool only if the response completed cleanly
and the upstream did not ask to close (`u->keepalive`,
`free_keepalive_peer`). Proposal: keep the three defaults until the
production measurement says what idle timeout the Valve edges use; if they
close idle connections sooner than 60 s, set `keepalive_timeout` just below
that so nginx closes first and the stale-connection path (open question 6)
is rarely taken. `keepalive_requests 1000` is one reconnect per 1000 chunks.

**(f) Effect on `$connections_writing` (ADR-0015's divisor).** `ngx_stat_
writing` is changed only in `ngx_http_request.c` (client request state);
`ngx_stat_active` is incremented in `ngx_event_accept.c` and decremented
in `ngx_http_request.c::ngx_http_close_connection`, both client side;
`ngx_event_connect.c` touches no counter (nginx src). Idle or busy upstream connections are
therefore invisible to the divisor and the cap's share math should not
change. Label: **to measure** (a re-run of the TH-1a 8-parallel figure with
pooling on, `poc/throttle/RESULTS-TH1-20261001.md` as the baseline).

**Stale pooled connections and `proxy_next_upstream` (nginx src).** Since
nginx 1.9.13 an error on a cached connection is retried only if `error` is
in `proxy_next_upstream` (`CHANGES` 1.9.13; `ngx_http_upstream_next` does
`tries++` for the cached case, then applies the `next_upstream & ft_type`
check like any other failure). CORE-FIX-2 stage 1 removes `error`, so with
a pool the first request on a connection the edge has meanwhile closed
would get a 502 instead of a transparent retry. See open question 6.

## Open questions for the user

1. **Source of the edge list.**
   Weg A: a static list in env (`VAULT_UPSTREAM_POOL_HOSTS`, space
   separated, validated against the two allowlist families
   `*.steamcontent.com` / `*.steamserver.net` of the `$vault_upstream_host`
   map, since a foreign name could never be dialled but would be
   re-resolved every 30 s for nothing; rendered by an entrypoint hook like
   `27-vault-upstream-rate.sh`, fail-closed).
   Weg B: learned from the `$host` column of the cache-event log
   (`vault_event` field 8; `VAULT_EVENT_LOG` is ON in the shipped
   `deploy/.env.example`, OFF in the image default) plus a reload.
   Weg C: union, A as seed, B on top.
   Recommendation: A first: deterministic, testable offline, env-only like
   ADR-0015. B needs a writer, a reload path and a staleness rule, while
   the edge set per region is small and stable (handover; inference).
2. **What the shipped list contains (with 1 Weg A).**
   Weg A: `VAULT_UPSTREAM_POOL_HOSTS` empty by default: opt-in, zero
   benefit out of the box, nothing in the repo that can go stale.
   Weg B: `deploy/.env.example` ships a seed: at least
   `dist-fra1.discovery.steamserver.net` (the edge the marker maps to)
   plus the edges the operator's event log showed during the rollout; the
   operator extends it.
   Recommendation: B. An unused entry costs one DNS query per 30 s and
   nothing else; a stale entry costs "no gain", never an error (limits).
   The operator one-liner for finding edges (event log field 8:
   `cut -f8 /vault/logs/event.log | sort | uniq -c | sort -rn`) is
   CORE-FEAT-1d scope.
3. **Pool size per edge and the total ceiling.** In flight is bounded by
   the client: at most `VAULT_PREFILL_MAX_THREADS` (8) for a capped
   prefill, 30 for stock SteamPrefill, unknown for Steam clients.
   `keepalive N` caps idle connections per group per worker
   (`worker_processes 1`), not the total (nginx docs). The only measured
   safe point is 50 parallel new connections (200 failed half the time,
   handover), and the handover asks for a total "well below" the quota.
   Open connections at any moment = in flight + idle, idle at most the
   ceiling.
   Weg A: `keepalive 8` per edge, render-time ceiling 32 idle connections
   in total (4 edges x 8), refused above it. Worst case 32 idle + 8 in
   flight = 40 for a capped prefill; 32 + 30 = 62 for stock SteamPrefill,
   above the measured point.
   Weg B: `keepalive 4` per edge, ceiling 48 (12 edges x 4): 48 + 8 = 56.
   Weg C: nginx's default 32 per edge, no ceiling: 8 edges could hold 256
   idle, far above the measured point.
   Recommendation: A. It matches the stage-1 worker count and keeps the
   capped-prefill worst case under 50; the stock-SteamPrefill case is the
   one stage 1 already removed; a too-long list fails at boot instead of
   at the CGN. Not covered by any number here: port mappings that closed
   connections still hold at the CGN for a while (inference, unmeasured).
4. **Reload strategy when the list changes.**
   Weg A: `nginx -s reload` inside the container after re-rendering
   (needs an exec or sidecar; the entrypoint hooks run only at start).
   Weg B: recreate the container, env-only, like `VAULT_UPSTREAM_RATE`.
   Recommendation: B with Weg A of question 1; reload only becomes
   necessary if a learner (1 B/C) lands.
5. **Interaction with `VAULT_UPSTREAM_RATE`.**
   Weg A: ship with the cap untouched, plus one assertion in the CI gate
   that `proxy_limit_rate $vault_upstream_rate;` is still in `@miss`, and
   re-measure TH-1a's 8-parallel number once with pooling on.
   Weg B: block D1 on a full TH-0b style re-measurement first.
   Recommendation: A; the source reading in (f) predicts no change, so one
   confirming measurement is proportionate.
6. **`error` in `proxy_next_upstream`.**
   Weg A: D1 restores `error` next to CORE-FIX-2's `timeout http_502
   http_503 http_504` and keeps `proxy_next_upstream_tries 2`; a stale
   pooled connection then retries once on a fresh connection and does not
   consume a try (`tries++`, nginx src); a real connect failure still
   stops at 2 tries.
   Weg B: keep stage 1's list and accept a 502 per stale pooled
   connection (SteamPrefill retries with `?nocache=1`, handover; Steam
   clients: unknown).
   Recommendation: A, with `keepalive_timeout` tuned per (e) so the case
   is rare in the first place.

## Prove before building

On the operator's production host; the test instance has no internet by
design. Nothing here changes vault-core. Two chunk URLs of the same edge
are needed (take them from a recent `vault-core` access log line:
`uri="..."` plus the client's Host).

0. **Pin the public edge IP first.** On the operator's host
   `*.steamcontent.com` may resolve to vault-core itself (vault-dns, a
   Pi-hole/AdGuard rewrite, or `extra_hosts`; `deploy/README.md` "DNS:
   pick one of three modes"; `docs/PROJECT_PLAN.md` §11 item 13 B
   "re-measure with the DNS rewrite"). A run that hits vault-core shows
   reuse for the wrong reason. So: `ip=$(dig +short A <edge> @1.1.1.1 |
   head -1)`, use `--resolve <edge>:80:$ip` (curl) or `$ip` directly
   (python below), and filter `ss`/`tcpdump` on that IP. If `dig` returns
   a private address, or curl prints `Connected to <edge> (<private or LAN
   address>)`, the run is invalid.
1. **Reuse across two requests, one process.** `curl -sv --resolve
   <edge>:80:$ip -o /dev/null -o /dev/null http://<edge>/depot/<id>/
   chunk/<a> http://<edge>/depot/<id>/chunk/<b>`. Proof of keepalive: the
   second request logs `Re-using existing connection` (one `Connected to
   <edge> ($ip)` line in total). Proof against: a second `Connected to`
   line, or `Connection: close` in the first response's headers.
2. **Handshake count.** In a second terminal: `ss -tn state established
   "( dst $ip )"` before and between the two requests (same local port =
   reused), or `tcpdump -ni <wan-if> "tcp[tcpflags] & tcp-syn != 0 and
   dst host $ip and dst port 80"` during the run: exactly one SYN for two
   requests proves reuse.
3. **Idle timeout of the edge.** Two requests on ONE open connection with
   a pause between them, from a single process; two `curl` processes never
   share a connection, and curl's `--keepalive` only enables TCP keepalive
   probes, so a SYN count from two invocations is always 2. Use
   `python3 -c 'import http.client,sys,time; c=http.client.HTTPConnection(
   sys.argv[1],80,timeout=30); h={"Host":sys.argv[2]}; c.request("GET",
   sys.argv[3],headers=h); c.getresponse().read(); time.sleep(int(
   sys.argv[5])); c.request("GET",sys.argv[4],headers=h); print(
   c.getresponse().status)' $ip <edge> /depot/<id>/chunk/<a>
   /depot/<id>/chunk/<b> 15` for pauses of 15, 30 and 60 s. A printed
   `200` means the connection survived the pause; `RemoteDisconnected`,
   `ConnectionResetError` or `BadStatusLine` on the second request means
   the edge closed it. The largest pause that still answers is the value
   for (e); step 2 running alongside must show one SYN per run.
4. **What vault-core cannot show today.** The `vault` log_format in
   `core/nginx/nginx.conf` has neither `$upstream_connect_time` nor
   `$upstream_addr` (repo); measuring reuse inside vault-core needs the
   log fields added under 1b2. A reused connection then logs a connect time
   near 0 (`ngx_http_upstream_send_request` stamps now minus the upstream
   start, and a pooled connect returns `NGX_DONE` in the same loop turn;
   nginx src, printed value is inference).

## Tests (planned, not written)

- `nginx -t` on the rendered config in the pinned image for 0, 1 and the
  ceiling's worth of hosts, and refusal of invalid values (a host outside
  the two allowlist families, a duplicate, a port, more hosts than the
  ceiling), in `.github/scripts/verify-core-nginx.sh` (no network).
- Drift guard: the include line pinned in both `core/nginx/nginx.conf` and
  `core/docker/nginx.conf.template`; the native static include is the
  empty render (same contract as `vault-upstream-rate.conf`); no
  `resolver` inside any rendered group.
- verify-stack: a fake edge container counting accepted TCP connections,
  reachable under an allowlisted name (`fake1.steamcontent.com`) through a
  test resolver, so `VAULT_RESOLVER` stays an IP and the boot probe's name
  NXDOMAINs harmlessly. N chunk MISSes through a pooled group must cost
  fewer than N connections; N against an unlisted host cost exactly N.
- The 508 loop guard still fires when the test resolver points a pooled
  name at vault-core itself; the Host allowlist still answers 403.
- The rate cap still applies with pooling on (question 5, Weg A).

## Consequences and honest limits

- Unknown hosts keep today's behaviour: one connection per chunk, no
  error, only no gain. A stale list degrades silently.
- The list can go stale: Valve's edge names vary by region and over time
  (`cacheN-<pop>`), and ISP-hosted "CDN" hosts may answer 403 for some
  depots (handover). Pooling does not change what an edge answers.
- NXDOMAIN on re-resolve empties the group's peers
  (`ngx_http_upstream_zone_resolve_handler`, nginx src); the next request
  to that name then fails as `no live upstreams` until a later resolve
  succeeds. Today the resolver path fails the same request with 502.
- Not addressed: the Steam client's own concurrency and edge selection,
  CM/WebSocket traffic, DNS (`vault-dns`), IPv6 egress (roadmap D4).
- Memory: one shared zone for all groups ("several groups may share the
  same zone", nginx docs); the size per resolved peer is undocumented and
  must be sized by starting with the largest list. `keepalive N` cache
  slots per group are negligible.
- DNS load: one query per listed edge every `valid` (30 s) while vault-core
  runs, download or not (nginx src). A pooled connection holds a CGNAT
  mapping for up to `keepalive_timeout` idle; question 3's ceiling bounds
  that.

## Work package split (all code after tag v0.1.0; needs an ADR-0016 exit note)

- **CORE-FEAT-1a** (docs, about 1 h): the measurement recipe above as an
  operator page, with both proof statements; the operator's recorded
  result is appended to this ADR.
- **CORE-FEAT-1b** (core, about 1.5 h): `core/docker/28-vault-upstream-
  pool.sh` rendering `vault-upstream-pool.conf` (groups with `resolve
  max_fails=0` and `keepalive N`) from `VAULT_UPSTREAM_POOL_HOSTS` with
  validation, ceiling and fail-closed self-check; the include line in both
  configs with the empty native file; drift-guard pins.
- **CORE-FEAT-1b2** (core, about 1.5 h): `verify-core-nginx.sh` renders and
  refusals; `error` back in `proxy_next_upstream`; `upstream_addr="..."`
  and `upstream_connect_time=...` appended to `log_format vault` as
  key=value fields, which keeps the PoC-era field order the config comment
  calls "deliberately unchanged" (append-compatible for analyzers; the
  `-match` assertions in `core/tests/test-core.ps1` may need touching).
- **CORE-FEAT-1c** (deploy tests, about 2 h): verify-stack fake edge with
  connection counting, test resolver, fallback, 508 and rate-cap checks.
- **CORE-FEAT-1d** (deploy and docs, about 1 h): compose forwarding,
  `.env.example` stanza with the seed (question 2) and the edge-finding
  one-liner, `core/README.md` and `deploy/README.md` sections, PROJECT_PLAN
  §11 item 13 D1 ticked.
