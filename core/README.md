# vault-core (Phase 1, WP 1.1)

Production nginx config for the SteamHangar cache core. Derives from the
Phase-0 PoC (`poc/conf/nginx.conf`, frozen as evidence) plus the four
binding production requirements discovered there and recorded in
[`docs/adr/0001-proxy-store-feasibility.md`](../docs/adr/0001-proxy-store-feasibility.md).

This file started as the WP 1.1 config README; the config is still
runnable natively on Windows for development exactly like the PoC was. The
container image arrived with WP 1.9 -- see "The Docker image" below.

## Files

```
core/
├── nginx/
│   ├── nginx.conf                        # the config itself
│   └── vault-upstream-rate.conf          # static "no cap" include (see "Upstream rate cap")
├── tests/
│   ├── test-core.ps1                     # automated suite, runs against the real Steam CDN
│   ├── fixtures/
│   │   ├── retry-regression.conf         # local throwaway rig for the B1 retry regression
│   │   └── eventlog-off.conf             # cache-event log OFF rig (WP 3.10 test group)
│   └── mvp/                              # MVP run script + recorded results (evidence)
├── Dockerfile, docker/                   # the container image -- see "The Docker image"
└── README.md                              # this file
```

`core/cache/`, `core/logs/`, `core/tmp/`, `core/_testcore_tmp/` are created
at runtime (by `test-core.ps1`, idempotently) and gitignored -- see the
repo root `.gitignore`.

## ADR-0001 requirement -> config mapping

| # | Requirement | Where in `core/nginx/nginx.conf` |
|---|---|---|
| 1 | LanCache heartbeat contract | `location = /lancache-heartbeat` (bottom of the `server` block) |
| 2 | Strip client `Range`/`Accept-Encoding`/`If-Range` upstream + store only 200 (incl. retry lists) | `proxy_set_header Range/Accept-Encoding/If-Range ""` in `@miss`; `map $upstream_status $vault_store_path` (`200` or `"~, 200$"` -> real path, else empty) feeding `proxy_store $vault_store_path` |
| 3 | `?nocache=1` bypass | `map $arg_nocache $vault_try_target` (forces `try_files` onto a guaranteed-missing path) used by `location /depot/` |
| 4 | Client-Host upstream, resolver, timeouts, retry, abuse guard | `resolver 1.1.1.1 ipv6=off valid=30s`; `map $host $vault_upstream_host` (full-match Steam hostnames, else empty) + `map $vault_upstream_host $vault_host_allowed`; `proxy_connect_timeout 3s`; `proxy_next_upstream error timeout http_502 http_503 http_504` with `proxy_next_upstream_tries 2` (at most one retry, none when the name resolves to a single address: nginx zeroes `tries` for a single-peer group; `error` dropped by WP CORE-FIX-2 and restored by WP CORE-FEAT-1b2 for the keepalive pool, ADR-0017 decision 6A, whose stale-pooled-connection case keeps its one free retry); the `if ($vault_host_allowed = 0) { return 403; }` guard in `@miss` |

(`Accept-Encoding`/`If-Range` stripping and the `"~, 200$"` retry-list
match were added in a review-fix pass after the initial WP 1.1 submission
-- see "Store guard" and the Accept-Encoding note below for why.)

Every requirement also has an inline comment at its implementation site in
`nginx.conf` explaining the *why*, not just the *what* -- this file is the
map/index, not a duplicate of that reasoning.

## Running it natively (development)

Same prefix trick as the PoC, and the **same nginx.exe binary** -- no
separate download/setup script exists under `core/` on purpose, to avoid
shipping a second copy of a 2.7 MB zip for a config that reuses the exact
same nginx build:

```powershell
poc\nginx\nginx.exe -p <repo>\core -c nginx\nginx.conf
```

All relative paths in `nginx.conf` (`root cache`, `logs/access.log`, the
temp paths) resolve against the `-p` prefix, so this creates/uses
`core/cache/`, `core/logs/` -- entirely separate from `poc/cache/`,
`poc/logs/`. `core/tests/test-core.ps1` does exactly this (see below).

**Port 443 (WP CORE-FIX-3):** the native config also runs the HTTPS
passthrough (`stream {}` block, see "HTTPS passthrough on port 443"), so
natively port 443 must be free as well; nginx refuses to start otherwise.

**Port 80 contention:** only one nginx can listen on port 80 at a time.
The PoC's own nginx may already be running (a live Steam client can be
using it as its cache right now). `test-core.ps1` handles this automatically:
stops whatever is running, runs vault-core's config for the test window,
then stops it again and restarts the PoC's nginx via `poc/start.ps1` --
the machine is left exactly as it was found. `poc/` itself is never
modified.

## Running the test suite

```powershell
core\tests\test-core.ps1
```

Runs against the real Steam CDN (known-good test object: depot `70403`,
chunk `773d10050d99b2544665873ec2125b3bf273e8b2`, the same one used
throughout `poc/`). Exit code 0 = pass, 1 = fail. As of the review-fix pass,
the suite has 15 test groups: health, heartbeat, smoke MISS/HIT,
Range-strip guard, nocache bypass, Host allowlist reject + pass-through,
three regressions added for the WP 1.1 review findings -- B1 retry-list
storage (own local fixture, `core/tests/fixtures/retry-regression.conf`),
S2 Accept-Encoding/gzip MISS+HIT byte-identity, and S3 `/tmp/proxy/...`
returning 404 -- plus five cache-event-log groups (WP 3.10: MISS/HIT
lines, nocache BYPASS, no heartbeat line, hostile URI escaping, feature OFF
via `core/tests/fixtures/eventlog-off.conf`). CI only parse-checks this
suite (it needs the real CDN); the pre-freeze request guards below are
exercised live, offline, by `.github/scripts/verify-core-nginx.sh`.

**Every `/depot/` request in the suite sends an explicit `Host` header**
naming a real Steam CDN hostname. This isn't incidental: it mirrors how
this server is actually used in production (a DNS- or hosts-file-redirected
Steam client always connects with a genuine `*.steamcontent.com` /
`*.steamserver.net` Host). `curl`/`Invoke-WebRequest`'s own default Host
(`127.0.0.1`) is exactly the kind of Host the allowlist guard (requirement
4) is designed to reject -- so it cannot be used for the suite's
positive-path tests, by design, not by oversight.

## Store guard: what nginx does on its own, and what the map actually adds

**Corrected in review (S1):** an earlier revision of this section presented
a 200/404/206/502 table as proof that the `$vault_store_path` map was
"guarding" storage. It wasn't testing the map's own contribution --
`proxy_store on;` with **no map at all** already skips storing 404/206/502
responses on this nginx build (confirmed by re-running that exact
comparison, unguarded, during the review-fix pass: with plain
`proxy_store on;` and a local throwaway upstream cycling through
200/301/302/404/206/502, only the 200 response landed on disk; the other
five were all correctly returned to the client but never written). That
table was validating **nginx's own built-in behavior**, not this config's
guard.

So what does the map in `nginx.conf` genuinely add, that bare
`proxy_store on;` does not give you for free?

```nginx
map $upstream_status $vault_store_path {
    200          "$document_root$uri";
    "~, 200$"    "$document_root$uri";
    default      "";
}
...
proxy_store $vault_store_path;
```

**1. The retry-list case (the real BLOCKER this map exists to fix).**
`$upstream_status` is not always a bare status code: when
`proxy_next_upstream` retries a failed attempt against a second backend,
nginx sets it to a **comma-separated list of every attempt**, e.g.
`"502, 200"` for a failed-then-succeeded retry. This is not a lab
curiosity -- real Phase-0 traffic produced exactly this
(`poc/logs/access.log` lines 3391-3392: `upstream_status=502, 200`,
21-36 second requests during a transient upstream outage). A map keyed on
the bare literal `200` does **not** match `"502, 200"` -- it falls to
`default ""`, silently discarding a completely valid, fully-received body
that the client already got a correct 200 for. This was reproduced in an
isolated two-backend rig (one always-502, one always-200, `upstream {}` +
`proxy_next_upstream`) during the review-fix pass: the buggy map (bare
`200` only) left the object uncached after a successful retry; the fixed
map (bare `200` plus `"~, 200$"`, matching "last attempt in the list was
200") stored it correctly. `core/tests/test-core.ps1` test 7 encodes this
exact regression against a checked-in fixture
(`core/tests/fixtures/retry-regression.conf`), not just the real CDN
(which cannot be made to fail on demand).

**2. Redirect bodies (301/302).** Own follow-up finding, not yet
independently reproduced against a real Steam CDN redirect (none was
observed in Phase 0 or this WP): a bare `proxy_store on;`, per the same
local test, does **not** store 301/302 bodies either on this nginx build --
so on *this specific build*, the map's 301/302 handling is currently
redundant with nginx's own behavior, same as the 404/206/502 case. This is
kept anyway (see point 3) rather than removed, since relying on an
unstated, unversioned nginx internal behavior for a security/correctness
property is fragile across nginx versions/platforms in a way an explicit
map in our own config is not.

**3. Explicitness and auditability regardless of platform behavior.**
Even where the map's `default ""` branch is redundant with nginx's own
unguarded behavior on this specific build, keeping it explicit means the
"only 200 (or a retry that ended in 200) gets stored" invariant is stated
in *our* config, not left to depend on nginx-internal behavior that could
plausibly differ across versions/platforms and was never in nginx's public
documentation as a status-code contract in the first place. The map is the
one part of this guarantee that's actually ours to keep correct -- which is
exactly what the B1 bug (point 1) was: an *our-own-code* bug, not an nginx
behavior surprise.

`core/tests/test-core.ps1`'s Range-strip guard test (cold object + a real
`Range: bytes=0-1023` header against the real Steam CDN) still asserts the
stored file is the complete object, but as noted before, the real CDN
(like nginx's own unguarded behavior) never actually answers with a 206
once Range is stripped -- so that test alone proves the *Range-strip*, not
the *store-path map*, is doing the work for that particular scenario. The
retry-rig regression (point 1) is what actually exercises the map's own
behavior end-to-end.

**What the client receives for a Range request on a cold object:** with
Range stripped upstream, the upstream always answers 200 (full body) --
and since nginx forwards a MISS response to the client exactly as received
from upstream (no local range-serving logic on the miss path), the client
*also* gets a 200 with the full body, not a 206, even though it asked for
`bytes=0-1023`. This is identical to what the PoC already observed (the
tested CDN edge ignores Range on a miss regardless), so no client-visible
regression -- but production no longer *relies on* that upstream behavior
being true forever; it's now enforced. Once an object is warm, nginx's
static-file serving handles Range (including partial/suffix/multi-range)
correctly and natively, unchanged from the PoC (`poc/RANGE-FINDINGS.md`
scenarios C/D1-D3).

## `?nocache=1` bypass: chosen semantics

`try_files` resolves against `$uri`, which has the query string already
stripped -- so left alone, a `?nocache=1` request for an already-cached
object would still HIT. `$vault_try_target` forces `try_files` onto a
path that can never exist on disk whenever `?nocache=1` is present, so it
always falls through to `@miss` (a real upstream fetch), regardless of
what's currently cached.

The fresh response is then written back to the object's normal canonical
path via the same 200-only guard above -- i.e. **a `nocache=1` request
refreshes the stored copy rather than deleting it**. This is what
`proxy_store` naturally does when writing to the same path again, and is
explicitly allowed ("acceptable") by ADR req 3. It also means a `nocache=1`
request against a URI whose upstream now returns a non-200 leaves the
previously-stored copy untouched (the store-path guard still applies) --
a bypass never destroys a known-good cached copy with a failed fetch.

## Accept-Encoding / If-Range stripped upstream too (S2 fix)

**Found in review:** without stripping `Accept-Encoding`, upstream can
answer a MISS with a gzip-compressed body (`Content-Encoding: gzip`).
`proxy_store` writes exactly the bytes it received -- compressed -- to
disk under the object's canonical path. A later HIT serves that same file
straight from disk via `try_files`, which has no idea the bytes were ever
compressed and does **not** re-add a `Content-Encoding` header. The client
then receives a raw gzip body with nothing in the HTTP response signaling
that it's compressed: silent corruption indistinguishable from a healthy
response until something tries to actually use the depot chunk.

**Fix:** `proxy_set_header Accept-Encoding "";` in `@miss` forces upstream
to always answer identity (uncompressed), so the stored bytes and the
served bytes always agree. `If-Range` is stripped alongside it for the
same reason `Range` already was: a conditional-range header only makes
sense paired with a `Range` request, and `Range` is already gone.
`core/tests/test-core.ps1` test 8 sends `Accept-Encoding: gzip` on both a
cold and a warm request against the real Steam CDN and asserts both
bodies are byte-identical to ground truth.

## Temp files must never be web-reachable (S3 fix)

**Found in review:** an earlier revision placed nginx's temp paths
(`client_body_temp_path`, `proxy_temp_path`, etc.) under `cache/tmp/...` --
inside `root cache`. Since nothing excluded `/tmp/...` as a request path,
`GET /tmp/proxy/<tempname>` resolved onto the same filesystem tree nginx
serves `/depot/...` from, and could serve an in-flight (or leftover)
`proxy_store` temp file's raw bytes to any client that guessed or observed
a temp filename. Reviewer reproduced this directly.

**Fix:** temp paths now live under `tmp/`, a *sibling* of `cache/` (both
directly under the `-p` prefix), so no request URI this server accepts
maps onto them at all -- moving the problem out of existence rather than
trying to block it after the fact. A second, independent layer
(`location ^~ /tmp/ { return 404; }`) is kept anyway as defense-in-depth,
in case a future edit ever reintroduces a temp dir under `cache/` again.
`core/tests/test-core.ps1` test 9 asserts `GET /tmp/proxy/whatever` returns
404.

**Same-filesystem requirement (binding for WP 1.9):** `tmp/` and `cache/`
must stay on the *same* filesystem/volume. `proxy_store` finishes a
download by `rename()`-ing the completed temp file into its final path
under `cache/depot/...` -- atomic and effectively instant only when both
paths share one filesystem. Across filesystems, `rename()` fails and nginx
falls back to a full copy (slower, and briefly doubles disk usage for
large depot chunks). In dev, both are plain relative paths under the same
`-p` prefix, so this holds automatically. **In the Docker image, `tmp/`
and `cache/` must be part of the same mounted volume** (the same bind
mount or the same named volume) -- never split across two separate volume
mounts, and never one of them left on the container's own (possibly
different-filesystem, e.g. overlay/tmpfs) root.

## Known gap: `analyze.ps1` cannot parse comma-list `upstream_status` lines (S4)

`poc/steam-client-test/analyze.ps1`'s log-line regex captures
`upstream_status` with `\S+` (`upstream_status=(?<ustatus>\S+)`) --
a single non-whitespace token. A retried request's log line reads
`upstream_status=502, 200 bytes_sent=...` (note the space after the
comma): `\S+` only matches `502,`, leaving a stray `` 200`` immediately
before `bytes_sent=`, which the anchored (`^...$`) pattern for the *entire*
line requires to appear right where `\S+` stopped. The whole line fails to
match and is **silently skipped** by the analyzer -- not misparsed with a
wrong value, just dropped as if it never happened.

**Wider since WP CORE-FEAT-1b2 (ADR-0017).** The `vault` log format now
carries two fields appended after `cache=...` (`upstream_addr="..."` and
`upstream_connect_time=...`, see "Upstream keepalive pool" below). The
existing fields keep their names and order, but two PoC analyzers anchor
their pattern at end-of-line (`cache=(?<cache>\S+)$`):
`poc/steam-client-test/analyze.ps1` and `poc/steamprefill/verify.ps1`.
Against a log written by the current format each matches **no line at
all**, retried or not. The PoC fixtures in `poc/` still use the 7-field
lines and keep passing; only a run of those analyzers against a current
vault-core log is affected.

This is a known, currently-unfixed gap, not something this work package
resolves: `analyze.ps1` belongs to `poc/` (frozen as Phase-0 evidence) and
is out of scope here. It matters going forward because **this gap gets
worse, not better, the more `proxy_next_upstream` actually helps** --
every request that needed a retry to succeed is exactly the kind of
request this analyzer will now silently drop from any future log-based
analysis (hit/miss ratios, bypass detection in Phase 3, etc.). Flagging
this explicitly so a future work package (whichever one next touches log
analysis -- likely bypass detection, requirement A12, Phase 3) fixes the
regex to tolerate a comma-separated list rather than rediscovering the gap
the hard way.

## Host-header allowlist: design and a real-world DNS surprise

The allowlist (`$vault_host_allowed`, requirement 4's abuse guard) is only
enforced inside `@miss` -- **not** on the whole `/depot/` location. An
already-cached object is served as a HIT regardless of the request's Host
header. This is deliberate: serving bytes that are already legitimately on
disk carries no open-proxy risk (the attacker learns nothing they couldn't
get by requesting the exact same path with a valid Host), whereas
`@miss` is the only place this server can be made to dial an
attacker-chosen destination -- that's where the guard has to live.

**Full match, one map (SEC-FIX-1).** The allowed host is a FULL match of a
strict hostname charset, anchored at both ends:

    ~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamcontent\.com$
    ~*^[a-z0-9-]+(\.[a-z0-9-]+)*\.steamserver\.net$

The same map produces the upstream host: the matched `$host`, the fallback
edge for `lancache.steamcontent.com` (an exact entry, which beats every
regex in an nginx map), or empty. `$vault_host_allowed` is derived from it
(empty = 0), so the guard and the name `proxy_pass` dials can never
disagree. Before SEC-FIX-1 the patterns were suffix-only
(`~*\.steamcontent\.com$`), and the upstream map passed every `$host`
through. A Host such as `127.0.0.1?x.steamcontent.com` passed that
allowlist, and `proxy_pass` splits its URL at the `?`. Measured on nginx
1.29.8, with the old config: nginx itself already answers `400` for a Host
containing `?`, `@`, `#`, a backslash, a space or `/`, so no relay
happened. `%` and `_` got through and were accepted, which led to a DNS
lookup inside Valve's zone. Real clients are unaffected: nginx lowercases
`$host`, strips `:port` and drops one trailing dot before the map runs, so
`CACHE2-AMS1.SteamContent.COM:80` and `cache2-ams1.steamcontent.com.` both
match. The CI gate (`.github/scripts/verify-core-nginx.sh`) probes the
delimiter classes live, with no network, and asserts both a refusal and
the absence of any resolver or upstream attempt. It also evaluates both
maps for normalised and for raw delimiter hosts.

**Discrepancy found while writing this work package's tests:** ADR-0001
req 4 describes `lancache.steamcontent.com` as "unusable, e.g. ... which
has no public A record", requiring the fallback-edge mapping for it. Direct
`nslookup` against both the local resolver and `1.1.1.1` during this WP
(2026-08-05) shows it now resolves via a real CNAME chain
(`lancache.steamcontent.com` -> ... -> `dist-fra1.discovery.steamserver.net`,
the same edge this config falls back to). This appears to be another
"community-documented assumption superseded by fresh evidence" case, the
same pattern WP 0.6 already found for the Linux-client quirk. Practically,
this means the explicit fallback mapping in `nginx.conf` is currently
*redundant* for reachability (nginx's resolver would follow the CNAME to
the same result even without it) -- but it is kept as designed, because:
it is harmless, it matches the ADR's documented intent, and there is no
guarantee Valve keeps that CNAME indefinitely (historically it was
documented as absent). If it disappears again, this config already handles
it without a change.

## Request guards in `location /depot/` (pre-freeze review)

Three `if { return; }` blocks (the only `if` form the nginx docs call
safe) run in the rewrite phase, before `try_files`, so a rejected request
never touches the disk or `@miss`:

- **Self-proxy loop breaker (S1).** `@miss` stamps every outbound request
  with `X-SteamHangar-Hop: 1`; a request *arriving* with that header can
  only be our own proxied request coming back -- e.g. a router that DNATs
  all port-53 traffic to a Pi-hole/AdGuard rewriting `*.steamcontent.com`
  to this server, so `resolver` no longer reaches a truthful upstream. It
  is answered `508 Loop Detected` at once. 508 is not in
  `proxy_next_upstream`'s retry list and the store guard only writes a
  200, so the loop ends after one hop with nothing stored. Belt to these
  braces: `40-vault-preflight.sh` asks the configured resolver for
  `cache2-ams1.steamcontent.com` at boot and refuses to start on a
  private/loopback/link-local/CGNAT answer (an unreachable resolver only
  logs a note -- it never fails the boot). Cost of the header: it goes
  upstream, in plain HTTP, on **every** miss, so Valve and any on-path
  observer can identify the cache as SteamHangar. A real Steam CDN edge is
  expected to ignore it (unmeasured against the live CDN).
- **GET only (P3).** Primary reason: `@miss` is a `proxy_store` path, and a
  relayed HEAD miss could store an empty object (a HEAD response has no
  body) under the chunk's name; any other method would be relayed to Valve
  for nothing. Any non-GET is `405` locally; HEAD is included. That depot
  traffic is GET-only is reasoned, not measured (the PoC `log_format` never
  recorded the method): no in-repo client issues anything but GET.
- **No directory probing (S2).** A URI ending in `/` is `404` locally.
  Before, `try_files` resolved it against the directory on disk: `403` once
  that depot had ever been cached, an upstream `404` otherwise -- an
  unauthenticated "is this depot cached here" oracle.

Two `http`-level hardening directives ship with them: `resolver_timeout
5s` (P1; nginx's default 30s would hang every MISS that long when the
resolver is down) and `server_tokens off` (N5; no version in `Server:` or
on error pages).

## `proxy_next_upstream` caveat (honesty note)

`nginx.conf` sets `proxy_connect_timeout 3s` and
`proxy_next_upstream error timeout http_502 http_503 http_504` with a
bounded `proxy_next_upstream_tries 2` / `proxy_next_upstream_timeout 6s`.
This directly addresses the ~42s stall Phase 0 observed from a dead
upstream IP (`poc/linux-client-test` findings) for the *connect* phase.

**One retry, and why `error` left and came back (WP CORE-FIX-2, then WP
CORE-FEAT-1b2, ADR-0017 decision 6A).** Before 2026-10-02 the list began
with `error` and allowed 3 attempts (up to 2 retries). A production rollout
behind a DS-Lite line showed why that hurt: vault-core opened a new
upstream TCP connection for every miss (variable `proxy_pass`, no keepalive
pool yet), the carrier-grade NAT ran out of port mappings and answered
every new SYN with ICMP host-unreachable at once (RFC 6888 REQ-11), and
nginx logged 57845 `connect() failed (113: Host is unreachable)` 502s
against 4029 200s in about two minutes. Each of those is an `error`, and
each retry was one more SYN against a NAT that was already full;
SteamPrefill re-requests failed chunks itself on top. Stage 1 (WP
CORE-FIX-2) therefore cut the list to `timeout http_502 http_503 http_504`
with `tries 2`, and capped SteamPrefill on the vault-api side
(`--max-threads`, `VAULT_PREFILL_MAX_THREADS`, default 8, `api/README.md`).

Stage 2 is the upstream keepalive pool (section "Upstream keepalive pool"
below), and it changes what an `error` means. A pooled connection the edge
closed while it sat idle surfaces as an `error` on its next use, and since
nginx 1.9.13 (`CHANGES`) a failure on a *cached* connection is retried only
if `error` is in `proxy_next_upstream`. That retry does not consume a try:
`ngx_http_upstream_next` does `tries++` for the cached case before it
applies the retry mask, so a stale pooled connection keeps its one free
retry, transparently, while a real connect failure -- a SYN the CGN
refuses -- gets at most one retry under `tries 2`, and none when the name
resolves to a single address: nginx zeroes `tries` for a single-peer group
(`ngx_http_upstream_round_robin.c`, the `single` branch of
`free_round_robin_peer`), so `tries 2` only bites with two or more A
records; the stale-cached path still gets its `tries++` (0 -> 1). So WP
CORE-FEAT-1b2 put `error` back next to stage 1's list and kept
`tries 2`; without it, the first request after an idle edge-side close
would be a 502 per pooled group. The pool's `keepalive_timeout 50s` (below
the edges' measured >= 60 s) makes that case rare in the first place.

What did not change: a connect *timeout* still gets one retry, and so does
an upstream 502/503/504. On Linux, with `proxy_connect_timeout 3s`, a
black-holed IP is a `timeout` (3s per attempt, so it cannot spin). The
Phase 0 dead-IP stalls on Windows were logged as 502 `error`s instead,
because the OS connect timeout of about 21s fired before nginx's; that does
not apply in the container. Pinned by `core/docker/check-config-drift.sh`
step 2c as the exact line, in `@miss`, in both config files, with no second
`proxy_next_upstream*` line anywhere else.

What this work package did **not** independently re-verify: whether
`proxy_next_upstream` actually retries against a *second* IP address when
`resolver` returns multiple A records for the same hostname and
`proxy_pass` targets it via a variable (as here), rather than through a
real `upstream {}` server-group block. This is documented, longstanding
nginx behavior for resolver-backed dynamic `proxy_pass`, but doing a live
test would require a hostname with a genuinely-dead first IP and a working
second IP, which wasn't available to construct safely against the real
Steam CDN in this WP's timebox. The `proxy_connect_timeout 3s` bound alone
already prevents the specific 42s-stall failure mode observed in Phase 0
regardless of whether the multi-IP-retry path fires -- worst case without
it, a request still fails fast (bounded by `proxy_next_upstream_timeout`)
instead of hanging.

## Cache-event log (WP 3.10, ADR-0008)

A second, dedicated, machine-readable `access_log` alongside the
human-oriented `vault` log above. See
[`docs/adr/0008-cache-event-feed.md`](../docs/adr/0008-cache-event-feed.md)
for WHY it exists (feeding WP 3.11's miss-triggered prefill completion and
per-client bypass detection without making vault-core's serving path depend
on vault-api being alive) -- this section documents the format and the
decisions pinned by evidence gathered for this work package. The reasoning
also lives as comments at the implementation site in `core/nginx/nginx.conf`
/ `core/docker/nginx.conf.template` (search for "WP 3.10").

### Format

Tab-separated (`\t`), one line per logged request, `escape=default`,
version-prefixed:

**Finalized before its first consumer.** The `$status` field (#9) was added
by review finding N3 after the rest of this format was already implemented
and tested, but *before* anything ever consumed it -- WP 3.11 (the
sweeper) does not exist yet, so there was no shipped reader to break. The
version field therefore stayed `v1`: this IS v1's definition, changed in
place while it was still pre-release. The version field's job is to catch
*future* format changes made after a consumer exists, not this one -- see
field 1 below.

| # | Field | Source | Notes |
|---|---|---|---|
| 1 | format version | literal `v1` | WP 3.11's sweeper MUST reject any line whose first field isn't exactly `v1` instead of guessing at a changed layout |
| 2 | time | `$time_iso8601` | e.g. `2026-08-09T14:03:11+02:00` |
| 3 | client address | `$remote_addr` | direct TCP peer; no `X-Forwarded-For` trust (vault-core is never behind another proxy) |
| 4 | cache status | `$vault_event_status` | `HIT` / `MISS` / `BYPASS` -- see "Cache-status truth" below |
| 5 | depot id | `$vault_event_depot` | digits captured from `/depot/<id>/...`, else `-` |
| 6 | URI path | `$vault_event_uri` | `$uri` (decoded, query-string-free), bounded to 300 characters |
| 7 | bytes sent | `$bytes_sent` | same variable/semantics as the `vault` log's `bytes_sent` field |
| 8 | host | `$host` | lowercased, port-stripped |
| 9 | HTTP status | `$status` | the response status code. Without it, hit statistics would count a 403 (Host-allowlist rejection), a 404, or a 502 the same as genuinely served traffic -- `cache_status` (field 4) says HIT/MISS/BYPASS but nothing about whether the response actually succeeded, and `bytes_sent` (field 7) includes error-body bytes too. WP 3.11's sweeper needs to filter to 2xx/206 before treating a line as served traffic or as evidence a depot is now cached |

Example line (tabs shown as `→` for readability):

```
v1→2026-08-09T14:03:11+02:00→192.168.1.42→MISS→70403→/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2→999232→lancache.steamcontent.com→200
```

### Escaping: `escape=default`, not `escape=json`

This is a TSV, not JSON-lines: `escape=json`'s dialect (`\u00XX`, JSON
quoting rules) buys a byte-oriented `line.split("\t")` parser nothing and
makes the field-count guarantee below harder to reason about.
`escape=default` does exactly what a tab-separated format needs -- it
escapes control characters (`0x00`-`0x1F`, `0x7F`) as a printable `\xXX`
sequence and escapes a literal `"` and `\` the same way. Concretely this
means a hostile request path containing a percent-encoded tab or newline
(`%09`, `%0A`) -- which nginx decodes into `$uri` as a literal control byte
before this log format ever sees it -- comes out as `\x09`/`\x0A` in the
log line, **not** as a real tab or newline. The only real tab bytes on the
line are the ones the `log_format` string itself inserts between fields, so
a naive tab-split in WP 3.11's sweeper always gets exactly 9 fields.

**Pinned empirically** (`core/tests/test-core.ps1`, WP 3.10 test group):
a request whose path contains `%09%22%0A%C3%A9` (encoded tab, quote,
newline, and a non-ASCII UTF-8 character) still produces an event-log line
that splits into exactly 9 tab-separated fields with field 1 == `v1`.

### Cache-status truth: not `$upstream_status`, not `$upstream_cache_status`

`$upstream_status` becomes a comma-separated retry list on a
`proxy_next_upstream` retry (LEARNINGS, e.g. `"502, 200"`) -- a status field
fed from it directly would need the same `"~, 200$"`-style last-attempt
parsing the store-guard map already does, and would still only describe the
upstream HTTP status code, not "was this object served from local disk or
fetched". `$upstream_cache_status` does not help either: it belongs to the
`proxy_cache` module, which this config never enables (it uses `proxy_store`
exclusively). **Measured, not assumed**, for this work package: with real
requests through the local test rig, `$upstream_cache_status` read back as
the literal string `-` on BOTH the `try_files`-HIT path (no upstream module
ever invoked for that request) and the `proxy_store` MISS path -- it carries
no HIT/MISS signal at all in this config, confirming the ADR's explicit
warning that it "does NOT apply to proxy_store setups".

`$vault_cache_status` -- the marker this config already sets via `set` in
`location /depot/` (`HIT`) and `location @miss` (`MISS`), used by the
existing human-readable `vault` log too -- has neither problem: it reflects
**which location block actually served the request**, a structural fact
fixed before any upstream I/O happens, not a parsed status code. A request
that retries `502` then `200` inside `@miss` is still, and was always going
to be, an `@miss` request -- MISS -- regardless of how many upstream
attempts that took. This is the "FINAL attempt's meaning" the comma-list
problem asks for, pinned by construction rather than by parsing.

`$vault_event_status` layers exactly one more distinction on top:
`?nocache=1` requests (ADR req 3) are BYPASS-intent, not a plain miss --
SteamPrefill's speed probes should be identifiable as such without WP 3.11
re-deriving `$arg_nocache` itself. Since `$vault_try_target` already forces
every `nocache=1` request through `@miss` (it can never resolve as a HIT),
only the `MISS:1 -> BYPASS` branch is reachable today; `HIT:1 -> BYPASS` is
mapped anyway, defensively, matching this file's existing style of keeping
branches that "can't currently happen" explicit (see the `/tmp/` location
and the store-path guard's 301/302 branch above).

### Scope: what produces a line, what doesn't

The `access_log ... vault_event ...` directive is declared **per-location**
(inside `location /depot/` and `location @miss`), not at `server`/`http`
level. `/health`, `/lancache-heartbeat` and `location ^~ /tmp/` never
declare it at all, so they produce **zero** event-log lines -- not a typed
"heartbeat" line the sweeper has to recognize and skip, but no line at all,
which is the cheapest possible thing to skip. A request that `try_files`
resolves locally never reaches `@miss` (nginx switches the request's
"current location" on an internal redirect, including for the log phase),
so exactly one of the two directives fires per request -- never both, never
neither, for anything reaching `/depot/`.

### Performance: `buffer=64k flush=5s`

Keeps the write off the request's hot path: nginx appends into an
in-memory buffer and only issues a `write()` syscall when the buffer fills
or 5 seconds have elapsed since the last flush, whichever comes first.
**Implication for WP 3.11's sweeper:** a line can sit unflushed in memory
for up to 5 seconds after the request that produced it. The sweeper's
cadence (WP 3.5, coarser than seconds) already tolerates this trivially --
but a same-second "did the sweeper see this MISS yet" test or expectation
would be flaky against a live server; don't write one. (`test-core.ps1`'s
event-log tests read the file directly after the request completes and are
therefore unaffected -- flush latency only matters for a process reading
the file concurrently with traffic, which is exactly WP 3.11's situation.)

### Rotation and the USR1 contract

Per ADR-0008, **the sweeper (WP 3.11, `api/`) owns the cursor and
truncation** -- nothing in `core/` rotates this file. Two consequences worth
being explicit about:

- nginx's stock `USR1`-reopen behavior (`kill -USR1 <master pid>`, or
  `nginx -s reopen`) already covers every open `access_log` file handle,
  including this one, for free -- no code in this work package changes
  that. If a future rotation strategy ever renames the file instead of
  truncating it in place, the sweeper (or whatever triggers rotation) MUST
  signal vault-core's master process afterwards, or nginx keeps writing to
  the renamed (now effectively deleted-on-most-filesystems) inode. This is
  a boundary note, not something WP 3.10 needed to implement: the ADR's
  chosen design is cursor+truncate, not rename.
- Truncate-in-place is actually safe against nginx's own writer without any
  reopen at all, and it's worth recording why: nginx opens `access_log`
  files with `O_APPEND`, under which every `write()` targets the file's
  *current* end-of-file as tracked by the kernel, not an offset cached by
  the writing process. Truncating the file to zero bytes while nginx holds
  it open changes that current end-of-file too, so the next line nginx
  writes lands at the new offset 0 -- no gap, no sparse hole, no reopen
  needed. This is *why* ADR-0008 could choose "sweep, then truncate" as the
  whole rotation story instead of a logrotate-style rename dance.

### Docker: `VAULT_EVENT_LOG` (optional, default OFF)

`core/docker/nginx.conf.template` renders the two `access_log` lines'
path from `${VAULT_EVENT_LOG}` (envsubst, `NGINX_ENVSUBST_FILTER=^VAULT_`,
same mechanism as `VAULT_RESOLVER`). Default in `core/Dockerfile` (the
IMAGE's own baked-in default, unchanged by anything below): **empty
(feature off)**, deliberately -- ADR-0008 frames the feed as "optional at
runtime", and WP 3.10 itself shipped no consumer for the file, only the
groundwork for one.

**That consumer has since shipped, and the deployment-level default has
changed as a result (kept here for historical accuracy about WP 3.10's own
scope; see `deploy/README.md` "Cache-event log" for what is actually true
of a fresh `docker compose up` today).** WP 3.11 added the sweeper that
reads this file (miss-triggered prefill completion, per-client hit
statistics, bypass detection -- requirement A12), and the 2026-08-17
packaging work package wired `VAULT_EVENT_LOG` through
`deploy/compose.yaml`/`deploy/.env.example` (the wiring WP 3.10 explicitly
left out of its own scope, "What this work package does NOT cover" below)
and made the pair **default ON** in `deploy/.env.example` now that turning
it on actually does something. The image-level default above is still
empty/off; a fresh `deploy/.env` from `.env.example` is what turns it on --
that value is not `VAULT_EVENT_LOG` alone, it also needs vault-api's
matching `VAULT_EVENT_LOG_PATH` (`deploy/README.md` has the full pairing
requirement):

```
VAULT_EVENT_LOG=/vault/logs/event.log
```

`/vault/logs/` is pre-created (owned by the `nginx` user) by
`core/Dockerfile`, the same "named volumes get this right automatically"
treatment `cache/` and `tmp/` already get -- turning the feature on needs
no host-side `mkdir`/`chown` even on a fresh volume.

Because plain envsubst can't express "empty means: delete this directive
entirely" (an empty substitution leaves a syntactically broken
`access_log`, not a clean no-op -- see the comments in
`core/docker/nginx.conf.template` and `core/docker/25-vault-eventlog.sh`),
a small additional entrypoint hook,
**`core/docker/25-vault-eventlog.sh`** (runs after `20-envsubst-...`, before
`40-vault-preflight.sh`), does the rest:

- `VAULT_EVENT_LOG` empty/unset: deletes both `access_log ... vault_event
  ...` lines from the rendered config entirely (found via a stable trailing
  marker comment, `# VAULT_EVENT_LOG_LINE`, not by re-parsing the
  substituted path) -- verified no marker line survives, **and** (review
  finding N1, hardening added after initial review) verified no line
  referencing `vault_event` survives EITHER, independent of the marker: a
  marker-only check would pass with `rc=0` even if some future edit left a
  live event-log `access_log` directive that had simply lost its trailing
  comment. Either check failing refuses to start rather than boot
  half-disabled. The Dockerfile's build-time marker-count assertion (`core/
  Dockerfile`, "Same for the WP 3.10 event-log placeholder") is the FIRST
  line of defense, catching a template edit before the image even ships;
  this pair of runtime checks is the second.
- `VAULT_EVENT_LOG` non-empty: validated against a strict character
  allowlist (letters, digits, `/`, `_`, `-`, `.`, must start with `/`) --
  same class of guard `40-vault-preflight.sh` already applies to
  `VAULT_RESOLVER`, because this value is also substituted verbatim into
  `nginx.conf` and a `;`/`{`/`}` in it would be config injection. **Also
  required (review finding N2): the path must be under `/vault/`.** The
  character allowlist alone accepts any syntactically clean absolute path,
  and this script `mkdir -p`s and `chown`s the value's PARENT directory to
  the `nginx` worker user -- unconstrained, `VAULT_EVENT_LOG=/etc/nginx/x.log`
  would hand `/etc/nginx` itself to that user. The feature only ever needs
  to write somewhere on the `/vault` volume (alongside `cache/` and `tmp/`),
  so anything outside it is refused with a clear message rather than acted
  on. The marker comment is stripped (cosmetic only) once both checks pass,
  and the target directory is `mkdir -p`'d and `chown`'d to the `nginx`
  user. The hook runs as root and `/vault` is shared with vault-api, so a
  symlink anywhere on the path below `/vault/` (directory or file) is
  refused instead of followed, and the `chown`s use `-h`.

`core/docker/check-config-drift.sh` was extended with a 6th recognized
delta for the two event-log lines (native: hardcoded ON at
`logs/event.log`, since native dev mode has no runtime env-var mechanism
and is never the deployed target anyway; container: the `${VAULT_EVENT_LOG}`
placeholder) -- everything else about the two lines must stay
byte-identical, same contract as the other five deltas.

**Since closed:** `.github/scripts/verify-core-nginx.sh` (CI, and locally
via the dev wrapper) now renders the real image for both states, asserts
the event-log file is created and owned 101:101 before nginx's root master
opens it (pre-freeze review S3: a root-owned file made vault-api's
truncating sweeper fail with EPERM), and asserts four bad values (relative
path, injection character, outside `/vault`, a `..` escape -- review S4)
plus a planted symlinked log directory and a symlinked log file are
refused by `25-vault-eventlog.sh`, and that `40-vault-preflight.sh` refuses
the image's stock `nginx.conf`. Historical note from WP 3.10: that
work package's environment had no Docker and could not run the actual
image end-to-end. The `25-vault-eventlog.sh` sed/validation logic was
verified directly against synthetic rendered-config fixtures under
`sh` (both the empty and non-empty paths, plus rejecting a relative path
and an injection attempt), and `check-config-drift.sh` was verified to both
pass on the real files and fail on an injected mismatch -- but the full
`envsubst` render -> `25-vault-eventlog.sh` -> `40-vault-preflight.sh` ->
`nginx -t` chain inside the real Alpine image was not independently
re-run here, matching the same documented constraint prior work packages
in this repo have flagged when Docker wasn't available.

## Log rotation

`access_log logs/access.log vault;` and `error_log logs/error.log warn;`
are both plain files nginx keeps open for the lifetime of the worker
process -- nothing here rotates them automatically, same as any nginx
install.

**In the container (WP 1.9): RESOLVED, and not with logrotate.** An earlier
revision of this section sketched a `/etc/logrotate.d/steamhangar-core` file
plus `nginx -s reopen`. That was superseded during WP 1.9 by a simpler
answer with strictly fewer moving parts: the container writes
`access_log /dev/stdout` and `error_log /dev/stderr`, and **rotation is the
Docker json-file driver's job** -- `max-size: 10m`, `max-file: 5` on every
service in `deploy/compose.yaml`, enforced by the daemon.

No logrotate binary in the image, no cron, no `SIGUSR1` dance, no log
volume, no risk of an unrotated file filling a container filesystem -- and
`docker logs` becomes the one place all three services' logs appear. The
limits are tunable per deployment (`VAULT_LOG_MAX_SIZE` /
`VAULT_LOG_MAX_FILE` in `deploy/.env`); see `deploy/README.md`
("Logs and rotation"). Verified applied to the running containers in
`deploy/VERIFICATION-*.md` (step 5h).

**Windows-native (dev):** no logrotate equivalent is set up for local
development -- logs simply accumulate under `core/logs/`. Since this mode
is for development/testing only (never the deployed target), delete
`core/logs/access.log`/`error.log` manually if they grow inconveniently
large; they are gitignored and carry no state that needs to survive a
restart.

## The Docker image (WP 1.9 -- implemented)

```
core/
├── Dockerfile                       # nginx:1.29.8-alpine3.23, pinned by digest
└── docker/
    ├── nginx.conf.template          # what actually runs in the container
    ├── 25-vault-eventlog.sh         # VAULT_EVENT_LOG on/off + validation
    ├── 26-vault-tls-passthrough.sh  # VAULT_TLS_PASSTHROUGH on/off (port 443)
    ├── 27-vault-upstream-rate.sh    # VAULT_UPSTREAM_RATE(_WINDOW) -> rate include
    ├── 40-vault-preflight.sh        # boot-time guards (see below)
    └── check-config-drift.sh        # keeps the template honest
```

**The container does NOT run `core/nginx/nginx.conf`.** Seven kinds of
directive line cannot be shared with the native dev config -- the log
destinations (HTTP and, since WP CORE-FIX-3, the stream log), the pid path,
an explicit worker user, the resolver becoming an env placeholder (in
`http {}` and in `stream {}`), and the two cache-event-log lines becoming a
`${VAULT_EVENT_LOG}` placeholder -- so `core/docker/nginx.conf.template` is
a near-verbatim copy carrying exactly those deltas (the authoritative list,
with expected counts, is in `check-config-drift.sh`). Everything else (every map, the store
guard, the Host allowlist, the Range/Accept-Encoding stripping, the nocache
bypass, the log format) is byte-identical, and that is **machine-checked**
by `core/docker/check-config-drift.sh`: it normalises both files, un-applies
the enumerated deltas, and diffs. 146 normalised directive lines (as of
CORE-FIX-3), verified identical; it also asserts that the
`vault_event` log_format line keeps all 8 of its LITERAL tabs in both files
(review P7) -- the normaliser would otherwise hide a tab -> space edit that
breaks vault-api's tab-split parser -- and verified to actually catch an injected difference (a
negative test in `deploy/tests/verify-stack.sh`, step 1b). Run it after
touching either file.

- `-p /vault` is the prefix, so `root cache` -> `/vault/cache` and
  `proxy_temp_path tmp/proxy` -> `/vault/tmp/proxy`, path-faithful layout
  unchanged as predicted.
- **Capabilities (SEC-FIX-1).** The entrypoint, its hooks and the nginx
  master run as root; only the workers run as uid 101. `deploy/compose.yaml`
  drops ALL capabilities for vault-core and adds back NET_BIND_SERVICE,
  SETUID, SETGID, CHOWN and DAC_OVERRIDE. The reason for each is in the
  comment there. `deploy/tests/verify-stack.sh` step 4c reads the master's
  and a worker's CapEff from `/proc`. A plain `docker run` of the image
  (no compose) still gets Docker's default set.
- **The same-filesystem requirement is now enforced, not just documented:**
  `cache/` and `tmp/` live under ONE volume mounted at `/vault`, and
  `40-vault-preflight.sh` compares their `st_dev` at every start. A split
  mount is a loud boot failure instead of a silent fallback from `rename()`
  to a full copy.
- **The resolver (ADR req 4) is configurable** via `VAULT_RESOLVER`
  (default `1.1.1.1`), rendered by the official image's
  `/etc/nginx/templates` envsubst mechanism with
  `NGINX_ENVSUBST_FILTER=^VAULT_`. Measured caveat for anyone tempted to drop
  that filter: today it changes nothing (filtered and unfiltered renders are
  byte-identical, because no existing env var is named like an nginx runtime
  variable). It guards a *future* lowercase env var colliding with `$host`,
  `$uri` and friends -- unfiltered, envsubst would replace the nginx variable
  with that env var's **value**, `nginx -t` would still pass, and the cache
  would silently misbehave. See the comment in `core/Dockerfile`.
- Other preflight guards, exercised in `deploy/VERIFICATION-*.md`
  (step 8): an unrendered `${VAULT_...}` placeholder, an empty resolver, a
  resolver value containing nginx-config-injection characters, and a cache
  directory the worker user (uid 101) cannot write. Added in the pre-freeze
  review and NOT part of any recorded `deploy/VERIFICATION-*.md` run: a
  stock (never rendered) `nginx.conf` is refused (S5; pinned by a negative
  in `.github/scripts/verify-core-nginx.sh`); the resolver allowlist admits
  `[` `]` so a bracketed IPv6 `[addr]:port` value actually works (N3); and
  the resolver loop probe described under "Request guards" above (N3 is
  not pinned by any automated test; the loop probe is, since WP
  CORE-FIX-3: `deploy/tests/verify-stack.sh` step 7i boots vault-core with
  vault-dns as `VAULT_RESOLVER` and expects the FATAL).
- Deployment, volumes, ports and the port-80/dedicated-IP guidance:
  `deploy/README.md`.

## Build version (WP VER-1)

`core/Dockerfile` takes the build args `VAULT_VERSION` (default `dev`) and
`VAULT_COMMIT` (default `unknown`) and bakes them as the runtime env
`VAULT_BUILD_VERSION` / `VAULT_BUILD_COMMIT` and the OCI
`org.opencontainers.image.version` / `.revision` labels. `publish.yml`
passes the release tag without its `v` and the commit SHA; a local build
keeps the defaults. The block sits at the end of the Dockerfile, after the
last `RUN`, so a new version reuses every cached layer
(`deploy/tests/verify-stack.sh` step 2.ver checks that).

## Build version file for `GET /v1/about` (WP VER-2)

The hook `docker/29-vault-build-version.sh` runs at every start (after the
envsubst render and hooks 25-28, before `40-vault-preflight.sh`) and writes
one line of JSON into the cache volume:

```
/vault/logs/vault-core-version.json
{"component":"vault-core","version":"0.1.0-rc9","commit":"<sha>","recorded_at":"2026-10-03T10:00:00Z"}
```

vault-api mounts the same volume and serves it as the `vault-core` entry of
`GET /v1/about` (api/README.md "Component versions"). A file, not an HTTP
endpoint, because vault-api has no network path to vault-core by design
(ADR-0011); user decision "Weg A", 2026-10-03. vault-api therefore reports
vault-core's status as `unknown` with the recording time: the file says which
version started last, not that nginx runs now.

- **Validated where it is written.** `VAULT_BUILD_VERSION` must match the
  VER-1 grammar (1-64 of `[0-9A-Za-z._+-]`, first a letter or digit),
  `VAULT_BUILD_COMMIT` 7-40 lowercase hex or `unknown`. Anything else,
  including blank or unset, is written as `invalid`, never as the raw value.
  The ENV is never rendered into nginx config, so the `^VAULT_` envsubst
  filter question does not arise: the template has no placeholder for it.
- **Not served.** `/vault/logs/` is outside `root cache;`, and there is no
  `location = /vault-version`: every LAN client could read it without a key,
  while this server deliberately sends a bare `Server: nginx`. Because the
  config is unchanged, `check-config-drift.sh` and the preflight's exact
  pins need nothing new; `verify-core-nginx.sh` asserts the rendered config
  never mentions the file and probes `/vault-version` and
  `/logs/vault-core-version.json` live (404).
- **Root writes nothing (review M1).** `/vault` and `/vault/logs` belong to
  uid 101 (nginx here, vault-api next door), mode 0755 without a sticky
  bit, so uid 101 can swap any name in them for a symlink at any time; a
  root `printf >` or `chmod` on a name there can be redirected anywhere
  (reproduced in review: a root-only 0600 file overwritten and made 0644).
  So root only creates a missing `logs/` (plain `mkdir`, never `-p`;
  `chown -h nginx:nginx`; a re-check that it is not a symlink). Everything
  else (mktemp, write, chmod, the rename, removing a stale file) runs as
  the nginx user: the hook re-invokes itself with `--writer` under busybox
  `su`, which refuses to run as root. A link followed by uid 101 reaches
  only what uid 101 may write anyway. Pinned structurally and with stubs in
  `core/tests/test-build-version-hook.sh`, and live in the pinned image by
  `core/tests/build-version-race-rig.sh` (a racer as nginx swaps every temp
  file for a symlink to a root-only file; the pre-fix hook overwrote it,
  the fixed one leaves it untouched).
- **Atomic.** `mktemp` in the same directory, `chmod 0644`, `mv -f`; a
  symlink on the file name is removed before the rename.
- **Never stops the cache, with one exception.** A write failure (read-only
  volume, a directory on the name) logs a WARNING, removes the old file if
  it can (so vault-api says "no version recorded yet" instead of a stale
  one) and boot continues. The exception: `VAULT_EVENT_LOG` naming this same
  file stops the boot with `29-vault-build-version.sh: FATAL`, because the
  hook would replace the cache-event log at every start.
- **Bind mounts.** A bind-mounted `/vault` without `logs/` gets it created
  (owner `nginx`). `/vault` itself must exist (it is the mount point), and
  an existing `logs/` must be writable by uid 101, or the hook only warns
  and vault-api reports "no version recorded yet".

Tests: `core/tests/test-build-version-hook.sh` (docker-free, under `sh`),
the build-time check in `core/Dockerfile`, `verify-core-nginx.sh` (hook
order, the file and its nginx owner in the real entrypoint chain, the
collision refusal, the 404 probes, the race rig), and
`deploy/tests/verify-stack.sh` steps 6w and 9d.

## Upstream rate cap (WP TH-1a)

Caps the **upstream** read of a cache MISS (Steam CDN -> vault-core). HITs
never reach `@miss` and are served at LAN speed. Off by default.

| Variable | Meaning | Empty / unset |
|---|---|---|
| `VAULT_UPSTREAM_RATE` | ONE aggregate limit in bytes per second, nginx size syntax: digits with an optional `k` (x1024) or `m` (x1048576) suffix. `800k` = 819,200 B/s, not 800,000. | no cap |
| `VAULT_UPSTREAM_RATE_WINDOW` | `HH:MM-HH:MM`, the exact grammar of `VAULT_SCHEDULE_WINDOW` (`api/vault_api/schedule_window.py`): start inclusive, end exclusive, whole minutes, `22:00-06:00` wraps midnight, `24:00` only as the end. **Full speed inside the window, capped outside it.** | the cap applies around the clock |

**Mechanism.** nginx has no arithmetic, so
`/docker-entrypoint.d/27-vault-upstream-rate.sh` precomputes the division
at container start into `/etc/nginx/vault-upstream-rate.conf`, which
`nginx.conf` includes:

- a map from `$connections_writing` to a per-request share: bucket 1 = the
  total, bucket N = total/N (integer floor) up to N = 1024, `default` =
  total/1024 (only reached by a count of 0, e.g. in the log phase);
- with a window, a map from `$time_iso8601` (one line per hour touched by
  the window, minute ranges at the edges) and a third map choosing `0`
  (unlimited) inside the window, the share outside it;
- with no cap, a single `default 0;` map, so `proxy_limit_rate
  $vault_upstream_rate;` is always present.

`proxy_limit_rate` takes that value once per request, when the upstream
response headers arrive (TH-0b section 1.1). It only acts on buffered
responses, so `proxy_buffering on;` is explicit in `@miss` and the upstream
cannot switch it off with `X-Accel-Buffering: no` (`proxy_ignore_headers`;
a reasoned guard, not measured against an upstream that sends the header).
Both lines and `proxy_limit_rate` are pinned by `check-config-drift.sh`
and the CI gate, by count AND by position inside the `location @miss`
block (a named location inherits nothing from `/depot/`).

**Why `$connections_writing`, not `$connections_active`** (measured,
`poc/throttle/RESULTS-TH1-20261001.md`, 800k target): 8 parallel MISSes
gave 0.981x of the target with `_writing` and 0.967x with `_active`; with 8
idle keep-alive connections open as well, `_writing` stayed at 0.981x
while `_active` fell to 0.496x, because `_active` counts idle keep-alives.

**What the cap is, stated honestly:**

- **It is per request, divided by the live count.** Each MISS gets
  total/N, N being the number of requests nginx is processing at that
  moment. The aggregate equals the total only while those N are all MISSes.
- **HIT, `/health` and other in-flight requests dilute the share.** They
  count in N but take no WAN bandwidth, so the WAN is under-used (never
  over the cap from this). Idle keep-alive connections do NOT count
  (measured above). A request's share is fixed when it starts: when other
  requests finish, the remaining ones do not speed up.
- **Slow-ramp overshoot is bounded and unmeasured.** A client that opens
  its connections more slowly than one upstream round-trip leaves the
  first MISSes at the larger share they saw (worst case: the total) until
  their chunk ends -- at most one chunk per request already in flight.
  Measured only for a burst inside ~20 ms (no overshoot, TH-0b).
- **One bucket per possible connection.** nginx.conf pins
  `worker_processes 1` and `worker_connections 1024`, so
  `$connections_writing` can never exceed 1024 and every live count has
  its own exact bucket; no count falls through to the default.
  `check-config-drift.sh` asserts BUCKETS >= worker_processes x
  worker_connections, so raising either without raising BUCKETS in
  `27-vault-upstream-rate.sh` fails CI.
- **The LAN client that triggers a MISS is capped too.** The chunk streams
  through to it at the capped pace; there is no nginx-native way to slow
  the upstream read without slowing the one client waiting on it.
- **The window edge stops nothing.** The value is chosen per request, so a
  sweep or prefill running past the end of the window continues, capped,
  chunk by chunk. Stopping at the edge would be the scheduler's decision.
- **Container local time.** `$time_iso8601` is nginx's local time, so the
  window means the operator's clock only if `TZ` reaches vault-core
  (tzdata is in the image, measured). Since WP TH-1b `deploy/compose.yaml`
  forwards `TZ` to vault-core (default `UTC`), so the window is evaluated
  in vault-core's `TZ`, the same zone the scheduler in vault-api uses.
- **Env-only, baked at container start.** Neither value is a vault-api
  setting; a change needs a container restart (recreate). Since WP TH-1b
  `deploy/compose.yaml` forwards both variables to vault-core (see
  `deploy/README.md` "Upstream rate cap").

**Fail-closed.** nginx reads an empty or unparseable `proxy_limit_rate`
value as 0 = **unlimited** (TH-0b, measured), so: an invalid
`VAULT_UPSTREAM_RATE` (anything but 1-9 digits with an optional suffix, a
leading zero, `0`, below 1024 B/s so a share would round to 0) or an invalid
`VAULT_UPSTREAM_RATE_WINDOW` stops the boot with
`27-vault-upstream-rate.sh: FATAL`; the script re-reads its own output
and refuses to start on a map that could evaluate to empty or 0; and
`40-vault-preflight.sh` takes a second look and refuses to boot if the
include is missing or not wired in, or if `VAULT_UPSTREAM_RATE` is set but
the file holds no connection-count map with a positive default. That is a
presence check, not a full independent re-derivation of the render. A
window with no rate is validated and then has nothing to lift. One
deliberate divergence from vault-api's parser: Python's `strip()` also
removes non-ASCII whitespace, this script only ASCII whitespace -- such a
value stops vault-core's boot instead of being reinterpreted.

**Native rig.** `core/nginx/nginx.conf` carries the identical `include`
and `@miss` lines; its `core/nginx/vault-upstream-rate.conf` is the static
cap-off form (`check-config-drift.sh` asserts it equals the script's
cap-off render). The divisor map needs the stub_status module
(`--with-http_stub_status_module` -- confirmed in the container image's
`nginx -V`) and nginx >= 1.27.0; for the native Windows binary both are
unverified here and checked by `test-core.ps1` test 15 when it runs.

**Tests.** `.github/scripts/verify-core-nginx.sh` renders cap off, cap on
without a window and cap on with a midnight-wrapping window (22:30-06:15)
through the real entrypoint, runs `nginx -t` on each and asserts the map
contents; refuses 18 invalid rate/window values; checks the preflight's
cross-check; and starts a throwaway server that returns the evaluated
`$vault_upstream_rate` inside and outside a window built around the
current minute.

## HTTPS passthrough on port 443 (WP CORE-FIX-3, ADR-0020)

**Why.** In DNS mode every connection to a `*.steamcontent.com` name lands
on vault-core, HTTPS included. SteamPrefill (SteamKit2) fetches depot
manifests over HTTPS from those names; with only port 80 open it failed
with `HttpRequestException ... while downloading manifests` (production,
2026-10-02). lancache answers the same problem with an SNI proxy; this is
that, as a `stream {}` block at the end of both config files.

**What it does.** `ssl_preread on` reads the TLS ClientHello without
decrypting anything and exposes the SNI as `$ssl_preread_server_name`. One
map turns an allowlisted name into `"<name>:443"` and everything else into
an empty string; `proxy_pass $vault_tls_upstream` then opens ONE TCP
connection to that name's real address (resolved through `VAULT_RESOLVER`,
same as the HTTP cache) and copies bytes both ways. vault-core holds no
certificate and no key; the client verifies Valve's certificate itself.
Nothing is cached, nothing is rate-capped (`VAULT_UPSTREAM_RATE` is HTTP
only; stream sessions are not in `$connections_writing` either).

**The allowlist** -- `"~*^(?=.{1,253}\z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+steamcontent\.com\z"`:

| SNI | Result |
|---|---|
| any subdomain of `steamcontent.com`, any case | passed, forwarded as sent |
| `steamcontent.com` (apex) | closed (not used for downloads; the HTTP allowlist refuses it too) |
| trailing dot | closed (RFC 6066 forbids it in SNI) |
| `evilsteamcontent.com`, `steamcontent.com.evil.example` | closed (label-boundary full match) |
| empty label, `_`, `:`, NUL, newline, a 64-character label | closed (1-63 `[a-z0-9-]` per label, no leading/trailing `-`, at most 253 in total; `\z`, not `$`, so a trailing newline cannot match) |
| `*.steamserver.net` | closed (the LAN rewrite covers `*.steamcontent.com` only) |
| no SNI, plain HTTP on 443 | closed |

A refused connection maps to an empty target: `proxy_pass` fails with `no
host in upstream` before any DNS query or connect, and nginx closes it
(stream status 500).

**One upstream connection per client connection.** `proxy_next_upstream
off` (the stream default is ON and would try the next A record on a
connect error -- each retry another SYN against a full carrier-grade NAT,
WP CORE-FIX-2). `proxy_connect_timeout 3s` as on the HTTP path,
`preread_timeout 5s`, `proxy_timeout 5m` idle.

**Loop bound, not a destination filter.** nginx's stream proxy has no hook
between "name resolved" and "connect", so it cannot refuse a private
answer at request time. The boot preflight refuses a `VAULT_RESOLVER` that
answers a Steam CDN name with a private/loopback/link-local/CGNAT address
or one of the container's own addresses. If a rewrite appears after boot,
`limit_conn` bounds the damage: 64 sessions per client address (a loop
arrives from one address) and 256 in total (each session holds two of the
1024 `worker_connections`, so the HTTP cache keeps half; that half also
holds the upstream keepalive pool's at most 32 idle connections, ADR-0017,
leaving at least 480 for live HTTP requests. `check-config-drift.sh` step 2f
pins `worker_processes 1`, `worker_connections 1024` and this arithmetic).

**Log.** One line per connection, in the container on stdout next to the
HTTP log (`docker compose logs vault-core`), natively in `logs/tls.log`:

```
02/Oct/2026:18:45:01 +0000 tls client=192.168.1.20 sni="cache2-ams1.steamcontent.com" target="cache2-ams1.steamcontent.com:443" upstream=155.133.248.13:443 status=200 bytes_sent=5321 bytes_received=812 session_time=0.412
02/Oct/2026:18:45:07 +0000 tls client=192.168.1.20 sni="example.com" target="" upstream=- status=500 bytes_sent=0 bytes_received=0 session_time=0.051
```

**Container switch.** `VAULT_TLS_PASSTHROUGH` (image default `1`):
`1/true/on/yes` keeps the block, `0/false/off/no` makes
`26-vault-tls-passthrough.sh` delete it (it is wrapped in
`# VAULT_TLS_PASSTHROUGH_BEGIN/END` marker comments in the template),
anything else stops the boot. `40-vault-preflight.sh` then checks the
result in both directions: off leaves no `stream`/`ssl_preread`/`listen
443`; on has exactly one listener, `ssl_preread on`, `proxy_next_upstream
off`, `proxy_pass $vault_tls_upstream`, and the allowlist map equal to the
reviewed two entries; and nowhere an `ssl_certificate`, `proxy_ssl*` or
`listen ... ssl`. Whether 443 is reachable from the LAN is
`deploy/compose.yaml`'s `VAULT_TLS_BIND` (`deploy/README.md` "Port 443").

**Modules.** The pinned image compiles `ngx_stream_module` and
`ngx_stream_ssl_preread_module` in statically (`nginx -V`), so there is no
`load_module` line; `core/Dockerfile` and the CI gate fail if that changes.

**Tests.**
- `check-config-drift.sh` step 2e: the stream pins in both files, the whole
  stream block equal to an exact directive list (the same list
  `40-vault-preflight.sh` checks the rendered config against at boot; the
  drift check keeps the two copies identical), the map
  has exactly its two entries, `http {}` listens on 80 only, no TLS
  termination anywhere, and the template's markers enclose exactly the
  stream block (deleting them leaves the http block byte-identical).
- `.github/scripts/verify-core-nginx.sh`: module check; on/off renders
  through the real entrypoint with `nginx -t`; 16 switch values (9 accepted
  spellings, 7 refused); 10 preflight tamper cases
  (`.github/scripts/tls-preflight-tamper.cases`, including an added `set`,
  a server-level `resolver` and `proxy_protocol on`, caught by the exact
  stream-block pin); the loop probe's own-address branch with stubbed
  `nslookup`/`ip` (a public answer equal to the container's own address is
  refused, a different one passes); and, offline against the
  live listener, raw ClientHellos for every allowlist row above
  (`.github/scripts/tls-sni-probe.sh`: refused = empty target and no
  resolve/connect attempt in the error log; allowed = the name as target
  and a resolve attempt), plain HTTP on 443, and the per-client cap (70
  parallel idle connections, 6 refused with 503).
- `deploy/tests/verify-stack.sh` step 3q (where 443 is published, per
  `.env` case), 5j (one real handshake through the cache to a Valve edge
  with certificate verification; off-list SNI and no SNI refused) and 7i
  (vault-core refuses to boot with vault-dns as `VAULT_RESOLVER`).

## Upstream keepalive pool (WP CORE-FEAT-1b, ADR-0017)

Reuses **upstream** connections (vault-core -> Steam CDN edge) across cache
MISSes instead of opening a new TCP connection per chunk. Off by default
(empty list). Why it exists: the first production rollout ran behind a
DS-Lite carrier-grade NAT that ran out of port mappings during a prefill
(`connect() failed (113: Host is unreachable)`, 94% of 62k chunk requests
failed; ADR-0017 "Context"). Stage 1 (WP CORE-FIX-2) bounded the burst;
this is stage 2, which removes the per-chunk connection for the edges you
name.

| Variable | Meaning | Empty / unset |
|---|---|---|
| `VAULT_UPSTREAM_POOL_HOSTS` | Space-separated Steam CDN edge host names, e.g. `cache1-fra2.steamcontent.com dist-fra1.discovery.steamserver.net`. Lowercase, no port, each ending in `.steamcontent.com` or `.steamserver.net` (the two families of the Host allowlist map), **at most 4**. | no pool; every MISS opens its own connection, exactly as before |

**Mechanism.** `/docker-entrypoint.d/28-vault-upstream-pool.sh` renders one
`upstream` group per listed edge into `/etc/nginx/vault-upstream-pool.conf`,
which `nginx.conf` includes at http level directly after the rate include:

```nginx
upstream cache1-fra2.steamcontent.com {
    zone vault_edges 256k;                                  # size on the first group only
    server cache1-fra2.steamcontent.com resolve max_fails=0;
    keepalive 8;
    keepalive_timeout 50s;
}
```

`@miss` keeps its variable `proxy_pass http://$vault_upstream_host$request_uri;`.
nginx looks the evaluated host up among the configured groups first: a
listed edge matches its group by name (nginx strips `:port` from `$host`
before the allowlist map runs, so the names compare equal) and reuses one of
up to 8 idle pooled connections; **a host that is not listed takes today's
per-request resolver path, unchanged** -- no error, just no gain. `@miss`
already speaks HTTP/1.1 with an empty `Connection` header, which is what
pooling requires. Each group re-resolves its name through the http-level
`resolver` (`ipv6=off valid=30s`), one peer per A record; `max_fails=0`
keeps one CGNAT connect failure from parking a peer for 10 s of `no live
upstreams`. The groups carry **no `resolver` of their own**, so
`VAULT_RESOLVER` stays the single DNS address in the image; the hook's
self-check and `check-config-drift.sh` step 2d both refuse a render that
contains one. `keepalive_timeout 50s` sits below the >= 60 s idle timeout
measured on the edges on 2026-10-02, so nginx closes idle connections
first.

**The ceiling, and what the hook refuses (fail-closed, boot stops with
`28-vault-upstream-pool.sh: FATAL: ...` naming the value):**

- **More than 4 edges.** `keepalive 8` idle connections per group, at most
  32 idle in total (ADR-0017 decision 3A: the only measured safe point
  behind the CGNAT was 50 parallel connections; 32 idle + 8 in flight for a
  capped prefill stays under it). The 5th name is refused with the ceiling
  and the ADR in the message.
- **Uppercase.** nginx would match it, but the list must equal what
  `$vault_upstream_host` yields, and that is lowercase; the hook keeps it
  strict instead of normalising.
- **A port** (`host:80`), **a scheme**, **a comma-separated list**,
  **anything outside `[a-z0-9-]` labels joined by single dots** (so no
  trailing FQDN dot, no empty label, no `*`; globbing is off in the hook so
  a `*` cannot expand against the working directory).
- **A name outside the two allowlist families**, including the bare
  `steamcontent.com` / `steamserver.net` and a family used as a prefix
  (`steamcontent.com.evil.example`): vault-core would never dial it but
  would re-resolve it every 30 s for nothing.
- **`lancache.steamcontent.com`**, the client-side discovery marker: the
  allowlist map rewrites that exact string to
  `dist-fra1.discovery.steamserver.net`, so a group of the marker's name can
  never match (and the name has no public A record). List the edge instead.
- **A label longer than 63 characters or a name longer than 253** (RFC
  1035). nginx would accept such a name at config time, but the group's
  resolve handler then sends malformed queries and logs `could not be
  resolved` every `resolver_timeout` for the container's lifetime.
- **Duplicates.**
- Whitespace is the only separator: runs of spaces, tabs or newlines
  collapse and leading/trailing whitespace is ignored, so an empty token
  cannot arise (POSIX word splitting); this is tolerated, not refused.
- The rendered file is re-read before nginx sees it: block count equals the
  host count, one `server <host> resolve max_fails=0;` per block, exactly
  one sized `zone` line, no `resolver` token, nothing but comments and
  those directives.

**Honest limits (ADR-0017 "Consequences").**

- **A stale list is silent.** Valve's edge names vary by region and over
  time (`cacheN-<pop>`); an entry nobody is routed to costs one DNS query
  per 30 s and gives nothing, and an edge missing from the list keeps the
  old one-connection-per-chunk behaviour without any warning. Pooling does
  not change what an edge answers (some ISP-hosted edges 403 certain
  depots).
- **NXDOMAIN empties a group.** If a listed name stops resolving, its peer
  list becomes empty and the next MISS to it fails as `no live upstreams`
  until a later re-resolve succeeds. Today the resolver path fails the same
  request with 502, so this is the same outcome with a different log line.
- **One DNS query per listed edge every 30 s** while vault-core runs,
  download or not. A pooled idle connection also holds a CGNAT mapping for
  up to `keepalive_timeout`; the ceiling bounds that.
- A connection the edge closed while idle in the pool surfaces as an
  `error` on its next use. `@miss` lists `error` in `proxy_next_upstream`
  (WP CORE-FEAT-1b2, ADR-0017 decision 6A), so nginx retries that request
  without consuming a try -- the retry may itself land on another idle
  cached connection of the same group (the balancer picks the peer, then
  the cache is searched), and each stale one costs one more free retry,
  bounded by the group's idle count (8); a real connect failure still gets
  at most one retry (none for a single-address name). Details and the
  nginx-source reasoning: "`proxy_next_upstream` caveat" above.
- Not addressed: the Steam client's own concurrency and edge selection,
  CM/WebSocket traffic, `vault-dns`, IPv6 egress.

**Env-only, baked at container start.** Not a vault-api setting; a change
to the list needs a vault-core recreate (ADR-0017 decision 4B, the same
contract as `VAULT_UPSTREAM_RATE`). The zone's `256k` is sized for the
4-group ceiling (the per-peer cost is undocumented upstream).

**Seeing reuse in the access log (WP CORE-FEAT-1b2).** The `vault` log
format (every line on `docker logs vault-core`) ends with two fields
appended for this feature, after `cache=...` and key=value like the rest:

```text
... request_time=0.329 cache=MISS upstream_addr="155.133.248.17:80" upstream_connect_time=0.000
```

- `upstream_addr`: the peer that answered (`ip:port`), `"-"` when no
  upstream was contacted (a HIT, a 403 from the Host allowlist, `/health`).
- `upstream_connect_time`: seconds spent establishing the upstream
  connection. A **reused** pooled connection logs `0.000` or close to it
  (nginx stamps the connect time in the same event-loop turn that returned
  the cached connection; ADR-0017 "Prove before building" item 4 -- a
  source reading, not yet measured: WP CORE-FEAT-1c's fake edge and the
  first production run with a pool are what confirm the printed value).
  A fresh connection logs its TCP handshake time instead. A pooled MISS
  therefore reads `cache=MISS ... upstream_connect_time=0.000` with the
  same `upstream_addr` as the MISS before it; an unlisted edge keeps
  logging a handshake time on every MISS.

Both fields become comma-separated lists on a `proxy_next_upstream` retry,
like `upstream_status` (`docs/LEARNINGS.md`). The existing fields keep
their names and order (append only); an analyzer that anchors at
end-of-line must learn the two fields ("Known gap" above).

**Native rig.** `core/nginx/nginx.conf` carries the identical
`include vault-upstream-pool.conf;` line; its
`core/nginx/vault-upstream-pool.conf` is the hook's empty render byte for
byte (`check-config-drift.sh` step 2d asserts it with `cmp`, and the
Dockerfile proves the same at build time, plus a 4-edge render and two
refusals, in the image's own shell).

**Tests.** `core/tests/test-upstream-pool-hook.sh` (bash, no Docker; run
by `verify-core-nginx.sh` step 0b) renders unset/empty/1/2/4-host lists
through the real hook, compares the two-host render against a golden file,
and asserts every refusal listed above (one case per rule, including the
RFC 1035 length limits with their 63/253 boundaries accepted) plus the
glob, whitespace, output-path and unwritable-path behaviour.
`.github/scripts/verify-core-nginx.sh` (WP CORE-FEAT-1b2) then runs
`nginx -t` in the pinned image through the real entrypoint chain on a
1-edge and a 4-edge (ceiling, with the rate cap on) render -- offline, with
`--network none`, because `server ... resolve` is parsed without a lookup
-- prints the rendered include, asserts its shape (N blocks, N
`resolve max_fails=0`, N `keepalive 8` / `keepalive_timeout 50s`, one sized
zone, no resolver) in every scenario including the empty render, pins
`proxy_limit_rate $vault_upstream_rate;` in `@miss` of both config files
by name (ADR-0017 decision 5A), and proves that five bad lists (5 hosts, a
foreign family, a port, uppercase, a duplicate) stop the real boot with the
hook's FATAL. The fake-edge connection count is WP CORE-FEAT-1c. The operator side -- the shipped seed list in
`deploy/.env.example`, the compose forwarding and how to find your edges in
the event log -- is documented in `deploy/README.md` (WP CORE-FEAT-1d).

## What this work package does NOT cover

- Docker/Dockerfile/Compose -- delivered later by WP 1.9, see
  "The Docker image" above
- vault-api or any API code (WP 1.2+)
- Miss-triggered prefill completion (Phase 3, hybrid decision in ADR-0001)
- Manifest-based garbage collection (Phase 3)
- Per-client bypass detection (Phase 3, requirement A12)
- Changes to `poc/` (frozen as Phase-0 evidence; its nginx is only
  stopped/started by `test-core.ps1`, never its config or code)

**WP 3.10 (cache-event log, ADR-0008) additionally does NOT cover**, by
explicit scope boundary (this work package touches `core/` only):

- The sweeper that reads this log, the byte-offset cursor, truncation, the
  miss-trigger rule, or any per-client statistics -- all WP 3.11, `api/`.
- Exposing `VAULT_EVENT_LOG` through `deploy/compose.yaml` /
  `deploy/.env.example` so an operator can actually set it via Compose --
  those files live under `deploy/`, out of this work package's scope. Today
  the Dockerfile's baked-in empty default is the only value a plain
  `docker compose up` gets; a deployment wanting the feature on needs a
  `deploy/` change first (expected to land alongside WP 3.11, which is the
  first thing that would actually consume the file).
- A real end-to-end Docker build/run of the container template + the new
  `25-vault-eventlog.sh` hook (no Docker available in this work package's
  environment at the time; since closed -- see "Since closed" above).
