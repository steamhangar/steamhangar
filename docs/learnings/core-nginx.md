# Learnings: vault-core (nginx) and vault-dns

Scope: nginx config, proxy_store, upstream pool, rate caps, log formats, dnsmasq. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## nginx / vault-core
- `$upstream_status` becomes a comma list ("502, 200") when
  `proxy_next_upstream` retries — any map/guard keyed on it must handle the
  list form (WP 1.1 blocker).
- Unguarded `proxy_store` stores ONLY status-200 bodies on this nginx line —
  404/206/301/302 are not stored; don't claim otherwise, and keep explicit
  guards anyway for retry-list handling (WP 1.1, reviewer claim corrected by
  empirical rig).
- `proxy_pass` with a variable resolves at request time via the configured
  `resolver`; without a variable it resolves once at startup via the OS
  (hosts-file-poisonable) path (WP 0.1).
- Strip `Range`, `Accept-Encoding`, `If-Range` upstream on the store path —
  gzip bodies get stored raw and served corrupt otherwise (WP 1.1).
- nginx official image envsubst: always set `NGINX_ENVSUBST_FILTER` — but know
  why. It is a no-op today (the entrypoint's allowlist is built from env vars
  that EXIST, none named like an nginx variable, so filtered and unfiltered
  renders are byte-identical). It guards a future lowercase env var named
  `host`/`uri`, which envsubst would substitute INTO the config as its value —
  not blank it — leaving `nginx -t` green and the cache quietly wrong (WP 1.9).
- Temp paths must live OUTSIDE the document root but on the SAME filesystem
  as the cache (atomic rename) (WP 1.1/1.9).
- `access_log` inheritance is REPLACE, not merge: declaring a second log in
  a location silently kills the inherited one — restate every log per
  location (WP 3.10).
- `$upstream_cache_status` is literally `-` under proxy_store on both HIT
  and MISS (measured) — cache-status truth must come from structural
  location markers (`set` in the serving location), never from
  proxy_cache-only or `$upstream_status` variables (WP 3.10).
- PCRE `.` stops at newlines — regexes bounding DECODED URIs need `(?s)`
  or an embedded %0A silently truncates the match (WP 3.10).
- A guard grepping a log_format NAME also matches the `log_format`
  declaration and every `map` named after it — anchor config guards to the
  DIRECTIVE (`^\s*access_log\s.*name`). WP 3.10 shipped a fail-closed hook
  that failed 100% of the time in the default (event-log-off) deployment;
  container hooks without a container-level test stayed unexecuted until
  WP 5.1's first CI gate exposed it (WP 5.1 blocker).

## dnsmasq / vault-dns
- `address=/zone/ip` alone FORWARDS AAAA upstream on modern dnsmasq —
  pair with `local=/zone/` or IPv6 silently bypasses the cache (WP 0.6).
- Pi-hole v6 ignores /etc/dnsmasq.d by default — instructions must target
  `misc.dnsmasq_lines` (WP 1.8).

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- nginx facts measured in the throttling spike: `load_module` relative
  paths resolve against `-p`, not the config file; a `js_set` error fails
  OPEN on `proxy_limit_rate` (no cap, no error); `$connections_active`
  counts idle keep-alive connections too; nginx's `k` suffix is 1024, not
  1000 (WP TH-0b).
- nginx 1.29.8 rejects `?@#\` and space in Host with 400 before any map
  runs, but `%` and `_` pass. A status-only Host test cannot tell refusal
  from relay, so check the logs too (WP SEC-FIX-1).
- A DNS rewrite of `*.steamcontent.com` also catches HTTPS: SteamPrefill
  fetches depot manifests over TLS from the CDN host, so a cache that
  serves only port 80 breaks prefill in DNS mode. lancache answers with
  an SNI proxy; vault-core now passes TLS through on 443 with nginx
  `stream` + `ssl_preread` behind a strict suffix allowlist (WP
  CORE-FIX-3).
- Pin a security-relevant config block as an exact directive list, not
  as a set of required lines: an edit applied to both config copies
  (`set` overriding the target, a block-level `resolver`,
  `proxy_protocol on`) passed the required-line drift pins. stream's
  `proxy_next_upstream` defaults to on; `limit_conn` in stream counts
  sessions still in preread (WP CORE-FIX-3).
- See also [deploy.md](deploy.md), this section: "On a DS-Lite line (IPv4 behind the provider's CGNAT) a burst of ..."

## 2026-10-02/03 — upstream keepalive pool (ADR-0017, WP CORE-FEAT-1a..1d)
- nginx pools upstream connections only for explicit `upstream` groups
  (those with a server-level config); the resolver path a variable
  `proxy_pass` takes for an unknown host builds a throw-away peer per
  request and never sees the keepalive cache, even after 1.29.7 made
  `keepalive 32 local` the default. A variable `proxy_pass` matches a
  defined group by host name first (port 0 when the URL has none), so one
  group per edge name, `server <name> resolve`, pools without touching
  the `proxy_pass` line (WP CORE-FEAT-1b, verified in the 1.29.8 source).
- Since nginx 1.9.13 an error on a cached (pooled) connection is retried
  only if `error` is in `proxy_next_upstream`; the cached case gets its
  try back (`tries++`), so the retry is free. Dropping `error` to stop a
  connect storm (CORE-FIX-2) and adding a pool later turns every stale
  pooled connection into a client-visible 502 — the two decisions
  interact, re-check one when changing the other (WP CORE-FEAT-1b2).
- A resolved group with one A record is a "single" peer group: nginx
  zeroes `tries` after the first failure, so `proxy_next_upstream_tries 2`
  gives no retry at all for single-address names. "One retry" is an upper
  bound, never a guarantee (WP CORE-FEAT-1b2 review).
- With several resolved peers, the defaults `max_fails=1 fail_timeout=10s`
  turn one CGNAT host-unreachable into 10 s of `no live upstreams` for
  every request to that edge; `max_fails=0` keeps the per-request fast
  failure instead (ADR-0017 (b)).
- Several `upstream` groups may share one `zone`; the size must appear on
  exactly one of them, in any order, or `nginx -t` fails with `zero size
  shared memory zone`. Render the size on the first group and pin
  "exactly one sized line" (WP CORE-FEAT-1b).
- Appending key=value fields to a log format is only "append-compatible"
  for substring parsers; a regex anchored with `$` on the old last field
  (`poc/*/analyze.ps1`, `verify.ps1`) matches nothing afterwards. Grep the
  repo for `$`-anchored consumers before touching a log format (WP
  CORE-FEAT-1b2).
- Measuring HTTP keepalive from the shell: two curl processes never share
  a connection, and `curl --keepalive` only enables TCP keepalive probes.
  Reuse across a pause needs one process holding one socket
  (`python3 http.client`), and the edge name must be pinned to its public
  IP first, or a LAN DNS rewrite makes the cache itself answer and the
  test proves the wrong thing (ADR-0017 "Prove before building").
- The nginx master (root) opens the access/event log O_APPEND|O_CREAT
  and `ngx_create_paths` mkdirs/chowns/chmods temp paths by name, both
  following symlinks, at every start and `nginx -t`: every directory
  whose names root touches must be root-owned and not renamable by the
  worker uid (CVE-2016-1247 class). Compose's cap set has no
  CAP_FOWNER, so chmod must precede chown; run hook tests with the exact
  compose cap set. A probe that "cannot create a name" must use a random
  name and accept only EACCES/EPERM as "closed" (WP SEC-FIX-5).

## 2026-10-13/14 — rc15/rc16 (D9, D10, UPD-5, UPD-6, D15a-c)
- See also [deploy.md](deploy.md), this section: "Behind Docker published ports (userland proxy / NAT) vault-api sees ..."

## 2026-10-14/15 — rc18 night run (UPD-FIX-1, D16a-c, DIST-1a/b)
- `nginx -s reload` returns 0 even when the new config is refused; gate on
  `nginx -t -p <prefix> -c <staged>` and confirm the new config is live
  (unique header) before reporting "applied" (D16c).

## 2026-10-16 - rc19.3 throughput tuning (TUNE-1a/1b)
- The heartbeat request has no access log on main. It is only visible in the
  opt-in debug log (`VAULT_DEBUG_THROUGHPUT`), so enable the flag before a
  test, not after.
- `aio threads` is a no-op while `sendfile on` is used without `directio`;
  the `VAULT_NGINX_AIO=threads` switch therefore also sets `sendfile off`.

## 2026-10-18 - cold-game test (ADR-0027 measurement)
- A heartbeat that waits for a blocked nginx worker shows `request_time`
  0 ms in nginx's own log: the wait happens before the request is read.
  Measure heartbeat latency from outside, e.g. curl from a second
  container.
