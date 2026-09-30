# WP TH-0b — throttling spike 2: an aggregate cap that divides itself by the live parallelism (measure, no production code)

Generated: 2026-09-30 (06:21-06:25 UTC for Shape A, 11:18-11:23 UTC for
Shape B — the session was cut in two by a rate limit; the container clock
is UTC as TH-0 established).
Instance: the test stack on the test host (vault-core at
`http://<vault-core>:8081`, image `steamhangar/vault-core:dev` built from
`core/Dockerfile` = `nginx:1.29.8-alpine3.23@sha256:5616878…`), driven only
through the test host's `dev.sh` wrapper. Repo state: `main` at 9865237.
Every change under `core/` was an experiment and is reverted; this file is
the only artefact. Predecessor: `poc/throttle/RESULTS-THROTTLE-20260929.md`
(TH-0), whose §4 limits 1 ("per-request, not aggregate") this spike
addresses.

Question (user decision 2026-09-30): the operator configures ONE aggregate
upstream limit; at download time the system must divide it by the number of
parallel upstream downloads that are actually running — not by a fixed 30.
`proxy_limit_rate` is per request and nginx has no arithmetic, so two shapes
were built and measured against a target of **819,200 B/s** (nginx `800k`;
note `k` = 1024 in nginx size parsing — TH-0's "200k = 200,000" percentages
were 2.4 % optimistic for the same reason: TH-0 §2.2's "98.7-99.9 % of
200,000 B/s" is 96.4-97.6 % of 204,800 B/s, and TH-0 §2.5's "3.97× the
cap" is 3.88× of 204,800; the mechanism conclusions are unchanged):

- **Shape A** — `map $connections_active $vault_upstream_rate` with 64
  generated buckets (`1 819200; 2 409600; … 64 12800; default 12800;`),
  no new module (stub_status is compiled in).
- **Shape B** — njs (`ngx_http_js_module.so`, ships in the image) with a
  `js_shared_dict_zone` in-flight counter: `js_set` increments and returns
  `total / n`, a `js_body_filter` decrements on the last buffer.

**Verdict in one line:** both shapes deliver the aggregate on the wall clock
(0.98-0.99× target at 8 and 16 parallel; 0.97× is B at 4), but they fail
in opposite directions — A under-uses the WAN whenever idle keep-alive
connections exist (measured 0.49× with 8 idle) and showed no transient for
a ≤ 20 ms burst (slower connection ramps can over-shoot by at most one
chunk per request already in flight — unmeasured, §1.1), B over-shoots in
the first chunk round of a burst (measured 3.25× at 16 parallel), leaks
its counter on a client abort (measured, permanent until restart) and
needs a module the Windows rig does not have. Recommendation in §6.

---

## 0. Setup facts (both shapes)

- Test object, Host header, `?nocache=1` forced-MISS mechanism, curl
  format: identical to TH-0 §2.0. `speed_download` is bytes/s and INCLUDES
  the time to first byte.
- New for TH-0b: wall clock per round captured with `date +%s%N` before
  launching N background curls and after `wait`; aggregate = Σ bytes /
  wall seconds. Per-request completion times were captured too. "Instantaneous
  aggregate at t" below = Σ `speed_download` of all requests whose
  `time_total` > t, i.e. what the WAN carried while all of them were still
  running (valid only for single-round runs, not for the pool run).
- Idle keep-alive connections were created from bash with
  `exec {fd}<>/dev/tcp/<vault-core>/8081`, one `GET /health HTTP/1.1` with
  `Connection: keep-alive`, response read, fd kept open for the duration of
  the round and closed afterwards. `keepalive_timeout` is nginx's default
  (75 s), longer than every round.
- Experiment log line (`vault` log_format extended for the experiment):
  Shape A appended `conn_at_miss=$vault_conn_at_miss` (a `set
  $vault_conn_at_miss $connections_active;` in `@miss`'s rewrite phase),
  `conn_at_log=$connections_active` (log phase) and `rate=$vault_upstream_rate`
  (the map result, which is cached per request, so the logged value IS the
  value `proxy_limit_rate` consumed). Shape B appended `conn_at_log`,
  `n_at_rate` (js_var written by the js_set handler) and `n_at_end` (js_var
  written by the body filter on the last buffer).
- Build facts, from an experiment-only entrypoint hook
  (`27-vault-evidence.sh`, printed into `dev.sh logs vault-core` at
  container start):

  ```
  nginx version: nginx/1.29.8
  configure arguments: … --with-http_stub_status_module … --with-compat …
  /etc/nginx/modules/ngx_http_js_module.so        (1,953,792 bytes)
  nginx-module-njs-1.29.8.0.9.6-r1 x86_64 {nginx-module-njs} [installed]
  ```

  So `$connections_active` (stub_status variables, nginx ≥ 1.3.14) is
  available in `map` without a `stub_status` location, and njs **0.9.6** is
  present (`js_shared_dict_zone` needs ≥ 0.8.0).
- Upstream budget: 145 MB of the 150 MB cap were pulled from Steam — the
  first working-but-fail-open Shape B build (§2.0) ran the full A-style
  series uncapped (1/4/8/16, 8 with idle keep-alives, 8×2 pool = 53 MB)
  before the log revealed the exception; only the 1/4/8/16 + idle rounds
  were repeated on the fixed build, the pool round was not (budget).
  Every measured request below is `cache=MISS` in the log.
- `worker_processes 1` (template line ~71): `$connections_active` and the
  njs shared dict are worker-wide by design (shared memory), but every
  number here was measured with a single worker.

---

## 1. Shape A — `$connections_active` bucket map

Diff applied to `core/docker/nginx.conf.template` (generated by a 10-line
Python snippet, total = 819200, buckets 1..64):

```nginx
    map $connections_active $vault_upstream_rate {
        1 819200;
        2 409600;
        3 273066;
        4 204800;
        …
        64 12800;
        default 12800;
    }
…
        set $vault_conn_at_miss "-";                       # server level
…
        location @miss {
            set $vault_conn_at_miss $connections_active;   # experiment log only
            …
            proxy_limit_rate   $vault_upstream_rate;
```

Build: `dev.sh up vault-core`, `dev.sh health` green, first log lines show
`conn_at_log=1 rate=819200`.

### 1.1 Rounds: 1 / 4 / 8 / 16 parallel

| round | wall (s) | bytes | aggregate B/s | ratio to 819,200 | per-request speed B/s | `rate=` seen by every request | `conn_at_miss` seen (rewrite phase) |
|---|---|---|---|---|---|---|---|
| 1 | 1.376 | 999,232 | 726,445 | 0.89× | 740,639 | 819200 | 1 |
| 4 | 6.276 | 3,996,928 | 636,900 | 0.78× (see note) | 160,205-166,853 | 204800 (all 4) | 2,3,3,4 |
| 8 | 9.967 | 7,993,856 | 802,059 | 0.98× | 100,726-101,146 | 102400 (all 8) | 1,2,3,5,5,6,7,8 |
| 16 | 19.745 | 15,987,712 | 809,694 | 0.99× | 50,791-50,869 | 51200 (all 16) | 2,4,4,4,5,6,7,8,9,10,12,13,14,14,15,16 |

Log excerpt (round of 8, `dev.sh logs vault-core | grep 773d1005`):

```
30/Sep/2026:06:22:32 +0000 uri="…773d?nocache=1" status=200 … request_time=9.880 cache=MISS conn_at_miss=3 conn_at_log=8 rate=102400
30/Sep/2026:06:22:32 +0000 … request_time=9.886 cache=MISS conn_at_miss=2 conn_at_log=8 rate=102400
30/Sep/2026:06:22:32 +0000 … request_time=9.887 cache=MISS conn_at_miss=1 conn_at_log=8 rate=102400
30/Sep/2026:06:22:32 +0000 … request_time=9.876 cache=MISS conn_at_miss=5 conn_at_log=8 rate=102400
30/Sep/2026:06:22:32 +0000 … request_time=9.874 cache=MISS conn_at_miss=8 conn_at_log=8 rate=102400
30/Sep/2026:06:22:32 +0000 … request_time=9.875 cache=MISS conn_at_miss=7 conn_at_log=8 rate=102400
30/Sep/2026:06:22:32 +0000 … request_time=9.875 cache=MISS conn_at_miss=6 conn_at_log=8 rate=102400
30/Sep/2026:06:22:32 +0000 … request_time=9.917 cache=MISS conn_at_miss=5 conn_at_log=1 rate=102400
```

Findings:

1. **The map sees the live value per request** (brief concern 3): every
   request of a round logged `rate = 819200 / N` and the per-request speeds
   match (101 KB/s at N=8, 50.8 KB/s at N=16). Aggregate 0.98-0.99× target
   at 8 and 16.
2. **No harmonic start-up transient in a burst** (brief concern 2, the
   H(N) ≈ 4× worry): `conn_at_miss` proves the requests ARRIVED staggered
   (values 1..N at the rewrite phase), yet every request got `total/N`.
   Reason: nginx evaluates `proxy_limit_rate` when the upstream RESPONSE
   HEADERS arrive (`time_starttransfer` 0.05-0.11 s in these rounds), and
   by then all N connections had been accepted. Source (nginx
   release-1.29.8, `src/http/ngx_http_upstream.c`): `p->limit_rate =
   ngx_http_complex_value_size(r, u->conf->limit_rate, 0)` at line 3475
   inside `ngx_http_upstream_send_response`, which is called only from
   `ngx_http_upstream_process_header` (line 2617) — i.e. once, after the
   upstream headers are in. The same function returns 0 (= unlimited) for
   a value it cannot parse, which is the measured fail-open of §2.0. The
   count therefore LEADS the limiter by one upstream round-trip. This holds
   only while the burst fits inside one upstream TTFB (here: 16 curls
   forked by bash within ~20 ms vs. 50-110 ms TTFB). A client that ramps
   its connections more slowly than that leaves each earlier MISS at the
   smaller count it saw (worst case `total/1`) until its chunk ends —
   ≤ 1.2 s per 1 MB chunk at 800k — the same lag class as Shape B's
   transient, bounded to at most one chunk per request already in flight.
   **Unmeasured**; "no transient" is a statement about a ≤ 20 ms burst.
3. The round of 4 is the outlier at 0.78×: all four requests had
   `time_starttransfer` = 1.08 s (upstream-side stall before the first
   byte, the other rounds had 0.05-0.11 s) and `speed_download` includes
   that idle second. Net of TTFB each request moved 999,232 B in
   5.0-5.2 s = 192-200 KB/s = 94-97 % of its 204,800 B/s share — the cap
   itself was exact; the aggregate number is depressed by the stall.
4. The single request reached 740,639 B/s = 90 % of 819,200 (TH-0 saw
   96-98 % at 200k); nginx paces per event-loop write and a 1 MB object at
   800k is only ~1.3 s, so the first-write granularity shows. Not
   investigated further.
5. A coinciding `/health` probe (Docker healthcheck, every 30 s) counts:
   the probe during the round of 8 logged `conn_at_log=9`. For a download
   this would mean `total/(N+1)` for the duration of that request — the
   safe direction (below target), and bounded by one health check per 30 s.

### 1.2 Count semantics: idle keep-alive connections are counted

Round of 8 with 8 idle keep-alive connections open (brief concern 1):

```
idle keep-alive connections open: 8
wall=19.748s bytes=7993856 aggregate=404,794 B/s target=819200 ratio=0.49x
req1..req8: 50,718-50,817 B/s each, time_total 19.66-19.70 s
```

Log: the eight `/health` requests that opened the idle connections show
`conn_at_log` climbing 1,2,3,…,8 as each one stayed open; the eight
downloads then logged `conn_at_miss=10..16 conn_at_log=16 rate=51200`,
i.e. `$connections_active` = 8 idle + 8 active = 16, and every download got
1/16 of the total. **Aggregate 0.49× target.**

`$connections_active` is, per the stub_status docs, "the current number of
active client connections including Waiting connections" — measured to be
exactly that. Expected consequence for the real client (inference,
unmeasured): TH-0 Part A proved 30-way parallelism and no connection cap
in SteamPrefill's `HttpClient`, so up to 30 keep-alive connections can
exist; whether they persist idle through a HIT-dominated run depends on
.NET's pool idle timeout and nginx's `keepalive_requests` (default 1000),
neither measured here. If they do persist, then on a partially cached
game (HITs served in 1-2 ms) every MISS gets `total/30` even when it is
the only MISS in flight: the cap is not exceeded, but the WAN is
under-used in proportion to the HIT ratio. A cold game (30 MISSes in
flight) gets the full aggregate.

### 1.3 Steady state: 8 keep-alive workers × 2 chunks each

Each of 8 background curls fetched the chunk twice over ONE connection
(curl reuses it), mimicking SteamPrefill's `HttpClient` + 30-way
`Parallel.ForEachAsync` in miniature:

```
wall=19.904s bytes=15987712 aggregate=803,232 B/s target=819200 ratio=0.98x
first chunks : 100,745-101,023 B/s, second chunks: 100,001-100,247 B/s
log: first round conn_at_miss=3..8 rate=102400; second round conn_at_miss=8 (all) rate=102400
```

Second-round requests saw the count at exactly 8 (connections reused, none
idle-but-extra), every chunk got `total/8`; aggregate 0.98×. No transient
between rounds.

### 1.4 Integrity

Right after the last capped round (same build):

```
curl … '<vault-core>/depot/70403/chunk/773d…'   -> 200 999232 473569668 0.002110
curl … | sha256sum                               -> c78fb9f8a88318dd61f318bb95f0b59911c9bbbf8678f6ef2d2724cdbc56a66c
log: … status=200 … bytes_sent=999490 request_time=0.000 cache=HIT conn_at_miss=- conn_at_log=1 rate=819200
```

Full object, TH-0/WP 0.2 SHA, served as HIT in 2 ms.

### 1.5 Drift guard and test-core with Shape A in place

`sh core/docker/check-config-drift.sh` → exit 1, diff = the 66-line map,
the two `set` lines, the log_format extension and `proxy_limit_rate`
(+70 lines). `dev.sh test-core` → exit 1 with the same diff; as in TH-0 the
CI script runs the drift check first under `set -e`, so `nginx -t` never
ran (validity established by the running container). For TH-1 the
production shape of A is an entrypoint-generated map file, so the honest
delta is one `include` line (or one marker-commented `map` block) registered
in `check-config-drift.sh` the way delta 6 is — not 66 literal lines in the
native file.

---

## 2. Shape B — njs in-flight MISS counter

### 2.0 Getting it to run (three findings before the first number)

1. **`load_module modules/ngx_http_js_module.so;` does NOT work in this
   image as deployed.** vault-core runs `nginx -p /vault`, so the relative
   path resolved to `/vault/modules/…`:

   ```
   [emerg] 1#1: dlopen() "/vault/modules/ngx_http_js_module.so" failed (Error loading shared library …: No such file or directory) in /etc/nginx/nginx.conf:71
   ```

   → container in a restart loop, `dev.sh health` curl exit 7. Absolute
   `load_module /etc/nginx/modules/ngx_http_js_module.so;` loads
   (`[notice] 1#1: js vm init njs`).
2. **`js_shared_dict_zone` must be declared `type=number` for `incr()`.**
   The default zone is string-typed; `d.incr('n', 1)` throws
   `TypeError: shared dict is not a number dict` at every call.
3. **A throwing `js_set` handler fails OPEN.** With the exception above the
   variable evaluated to `""`, nginx logged

   ```
   [error] 50#50: *91 invalid size "" while reading response header from upstream, client: …, request: "GET /depot/70403/chunk/773d…?nocache=1 HTTP/1.1", upstream: "http://<steam-cdn>:80/depot/…", host: "lancache.steamcontent.com"
   ```

   and then **served the MISS uncapped**: single request 3,386,125 B/s,
   4 parallel 8.3 MB/s aggregate (10.1× target), 8 parallel 9.1×, 16
   parallel 8.5×, all `status=200`, all stored (HIT afterwards). The cap
   silently disappears; only the error log knows. Any production variant
   of `proxy_limit_rate $variable` needs a guard that a malformed value
   cannot reach the directive — this applies to Shape A's entrypoint
   generator too (a map with a bad literal fails `nginx -t`, which is the
   better failure, but a missing `default` would leave `""` → same
   fail-open).

Final experiment (`core/docker/throttle.js`, copied to `/etc/nginx/njs/`):

```js
const TOTAL = 819200;
function rate(r) {                      // called once per request, when proxy_limit_rate is read
    const n = ngx.shared.vault_inflight.incr('n', 1);   // count includes this request
    r.variables.vault_inflight_at_rate = String(n);
    return String(Math.floor(TOTAL / n));
}
function leave(r, data, flags) {        // js_body_filter: pass through, decrement on the last buffer
    r.sendBuffer(data, flags);
    if (flags.last) {
        r.variables.vault_inflight_at_end = String(ngx.shared.vault_inflight.incr('n', -1));
    }
}
export default { rate, leave };
```

```nginx
load_module /etc/nginx/modules/ngx_http_js_module.so;      # main context
…
    js_path /etc/nginx/njs;                                  # http context
    js_import throttle from throttle.js;
    js_shared_dict_zone zone=vault_inflight:32k type=number;
    js_var $vault_inflight_at_rate "-";
    js_var $vault_inflight_at_end "-";
    js_set $vault_upstream_rate_js throttle.rate;
…
        location @miss {
            js_body_filter     throttle.leave buffer_type=buffer;   # buffer_type=buffer: binary bodies
            proxy_limit_rate   $vault_upstream_rate_js;
```

### 2.1 Rounds: 1 / 4 / 8 / 16 parallel

| round | wall (s) | aggregate B/s | ratio (wall) | per-request speeds B/s | `n_at_rate` seen | instantaneous aggregate while all ran | H(N) |
|---|---|---|---|---|---|---|---|
| 1 | 1.395 | 716,104 | 0.87× | 723,726 | 1 | 0.88× | 1.00 |
| 4 | 5.030 | 794,606 | 0.97× | 763,335 / 390,676 / 265,060 / 199,825 | 1,2,3,4 | 1,618,896 = **1.98×** | 2.08 |
| 8 | 9.905 | 807,064 | 0.99× | 766,069 … 101,207 (= total/1 … total/8) | 1..8 | 2,136,554 = **2.61×** | 2.72 |
| 16 | 19.741 | 809,885 | 0.99× | 755,517 … 50,856 (= total/1 … total/16) | 1..16 | 2,662,021 = **3.25×** | 3.38 |

Log excerpt (round of 8):

```
30/Sep/2026:11:20:52 +0000 … request_time=1.302 cache=MISS conn_at_log=8 n_at_rate=1 n_at_end=7
30/Sep/2026:11:20:53 +0000 … request_time=2.544 cache=MISS conn_at_log=7 n_at_rate=2 n_at_end=6
30/Sep/2026:11:20:54 +0000 … request_time=3.765 cache=MISS conn_at_log=6 n_at_rate=3 n_at_end=5
30/Sep/2026:11:20:55 +0000 … request_time=4.980 cache=MISS conn_at_log=5 n_at_rate=4 n_at_end=4
30/Sep/2026:11:20:57 +0000 … request_time=6.204 cache=MISS conn_at_log=4 n_at_rate=5 n_at_end=3
30/Sep/2026:11:20:58 +0000 … request_time=7.424 cache=MISS conn_at_log=3 n_at_rate=6 n_at_end=2
30/Sep/2026:11:20:59 +0000 … request_time=8.664 cache=MISS conn_at_log=2 n_at_rate=7 n_at_end=1
30/Sep/2026:11:21:00 +0000 … request_time=9.872 cache=MISS conn_at_log=1 n_at_rate=8 n_at_end=0
```

Findings:

1. **The counter is exact and returns to zero** (`n_at_end=0` after every
   round; `n_at_rate` = 1..N in evaluation order). Per-request rates are
   exactly `total/k` for the k-th request to reach the limiter.
2. **The harmonic burst transient is real for B** — the count LAGS: the
   k-th request to have its `proxy_limit_rate` evaluated sees only k
   in-flight. Measured instantaneous aggregate while the whole round was
   running: 1.98× / 2.61× / 3.25× target for N = 4 / 8 / 16, tracking
   H(N) − small (the wall-clock aggregate still lands at 0.97-0.99× because
   the slowest request, at total/N, defines the round's duration). It
   decays as the fast requests complete: request k finishes after
   k × (chunk/total) = k × 1.22 s here, so the excess is gone after N chunk
   times = the first "round" (4.9 s at N=4, 19.6 s at N=16). Extrapolated
   arithmetic, not measured: SteamPrefill's N=30 → H(30) = 3.99× for the
   first instants, with the whole first round of 30 chunks (~30 MB)
   front-loaded; with continuous replacement each replacement sees n ≈ N
   and gets total/N, so after the first round the aggregate is ≈ 1×.
3. Shape A did not show this because `$connections_active` leads the
   limiter (§1.1 finding 2) while B's counter is incremented at the
   limiter. The brief's original shape — increment at `@miss` ENTRY (a
   `js_set` variable forced in the rewrite phase via `set`), read at rate
   time — would give B the same lead of one upstream TTFB. Not built, not
   measured.

### 2.2 Count semantics: idle keep-alive connections are ignored

Round of 8 with 8 idle keep-alive connections open:

```
wall=9.943s bytes=7993856 aggregate=803,985 B/s target=819200 ratio=0.98x
log: conn_at_log=16..9 (the idle ones are there), n_at_rate=1..8, n_at_end=7..0
```

Same numbers as without idle connections; B counts MISS transfers only.
This is the property Shape A lacks (§1.2, 0.49×).

### 2.3 Client abort leaks the counter (measured)

```
curl --max-time 0.5 … '?nocache=1'     -> 200 319017 0.499   (curl rc=28, aborted mid-transfer)
log: … status=200 upstream_status=200 bytes_sent=344064 request_time=1.342 cache=MISS conn_at_log=1 n_at_rate=1 n_at_end=-
sleep 3; one plain forced MISS:
curl … '?nocache=1'                     -> 200 999232 391668 2.551
log: … request_time=2.549 cache=MISS conn_at_log=1 n_at_rate=2 n_at_end=1
```

The aborted request never delivered a last buffer to the body filter
(`n_at_end=-`), the shared-dict counter stayed at 1, and the next SOLO
download got `total/2` = 409,600 B/s (measured 391,668). The error is
permanent (shared dict lives until nginx restart) and cumulative: every
cancelled Steam download or closed SteamPrefill run leaves the vault
throttled harder. Note also that nginx had `proxy_ignore_client_abort off`
(default) and still logged `upstream_status=200 bytes_sent=344064`; what
became of the partial temp file and of the stored copy was NOT checked
(the follow-up request carried `?nocache=1`, which refetches regardless,
so it proves nothing about the store — §5).

Mitigations visible in the njs docs
(<https://nginx.org/en/docs/http/ngx_http_js_module.html>), none measured:
`js_shared_dict_zone … timeout=` ("the time after which key-value pairs
are removed from the zone") with one key per in-flight request and the
count taken as `dict.size()`, so a leaked entry expires after e.g. 60 s;
or a `js_periodic` (njs ≥ 0.8.1) reconciliation. Both bound the leak, both
add a second timer-driven mechanism to what was meant to be a counter.

### 2.4 Integrity

```
curl … '<vault-core>/depot/70403/chunk/773d…'   -> 200 999232 363093023 0.002752
sha256sum                                        -> c78fb9f8a88318dd61f318bb95f0b59911c9bbbf8678f6ef2d2724cdbc56a66c
log: … cache=HIT conn_at_log=1 n_at_rate=- n_at_end=-
```

Full object, same SHA, HIT in 3 ms — the body filter with
`buffer_type=buffer` did not disturb proxy_store. (Even the fail-open run
of §2.0 stored complete objects.)

### 2.5 Drift guard and test-core with Shape B in place

`sh core/docker/check-config-drift.sh` → exit 1; diff = `load_module` (main
context), 6 `js_*` lines (http), `js_body_filter` + `proxy_limit_rate`
(`@miss`), plus the experiment log fields. `dev.sh test-core` → exit 1 at
the drift check, `nginx -t` not reached. A production Shape B is a
9-directive, container-only delta plus a `.js` file the native
`core/nginx/nginx.conf` can never carry (nginx.org's Windows build has no
dynamic modules and no njs; `poc/setup.ps1` fetches nginx 1.30.4 for
Windows) — the drift guard would need a "container-only block" concept it
does not have today (delta 6 is a one-line placeholder, not a block).

---

## 3. Revert and pristine state

```
git checkout -- core/ ; rm core/docker/27-vault-evidence.sh core/docker/throttle.js
sh core/docker/check-config-drift.sh   -> check-config-drift: OK -- 102 normalised directive lines identical
dev.sh build vault-core && dev.sh up vault-core
dev.sh health                          -> core /health -> ok, X-LanCache-Processed-By: steamhangar, api {"status":"ok"}
HIT sanity: 200 999232 626085213 0.001596
log: 30/Sep/2026:11:23:03 +0000 uri="…773d" status=200 range="-" upstream_status=- bytes_sent=999490 request_time=0.000 cache=HIT   (pristine format, no experiment fields, no evidence hook)
git status --short core/               -> (empty)
```

No further upstream fetch was spent on the pristine sanity (budget).

---

## 4. Comparison A vs. B

| | Shape A — `$connections_active` map | Shape B — njs in-flight counter |
|---|---|---|
| Wall-clock aggregate vs. target | 0.98× (8), 0.99× (16), 0.98× (8×2 pool) | 0.97× (4), 0.99× (8), 0.99× (16) |
| Per-request share | exact `total/count`, all requests of a round equal | exact `total/k` for the k-th to reach the limiter |
| Start-up transient in a burst of N | **none measured for a ≤ 20 ms burst** (count leads the limiter by one upstream TTFB; all N saw N); slower ramps: bounded overshoot ≤ one chunk per request already in flight, unmeasured | **H(N)**: 1.98× / 2.61× / 3.25× instantaneous at N = 4 / 8 / 16; lasts one chunk round (N × chunk/total) |
| Count semantics | ALL accepted client connections incl. idle keep-alive, HIT-serving and health-check connections → **0.49× with 8 idle** (under-use) | MISS transfers only → 0.98× with 8 idle |
| Failure direction | over-count → under-throttle (the WAN-safe side); slow ramp → bounded overshoot (unmeasured); `default` bucket needed or fail-open | under-count in a burst → over-shoot; **leak on client abort** → cumulative under-throttle until restart; exception → fail-open (uncapped) |
| State | none (nginx's own counter) | shared dict, survives across requests, no reconciliation |
| Module dependency | none (stub_status is compiled into the image AND into nginx.org's Windows builds) | `ngx_http_js_module.so` (in the image, njs 0.9.6) + a `.js` file; `load_module` must be an absolute path under `-p /vault` |
| Windows native rig (`poc/setup.ps1`, nginx 1.30.4) | map works natively (static map in the native file, generated map in the container) | not runnable — container-only |
| Drift-guard impact | 1 generated map: one `include`/marker delta if generated at entrypoint, else 66 literal lines in both files | 9 directives across main/http/location + a file → needs a container-only block concept |
| Config plumbing | entrypoint script generates buckets from `VAULT_UPSTREAM_RATE` (like `25-vault-eventlog.sh`) | `TOTAL` rendered into the `.js` (envsubst or a `js_var` from `${VAULT_…}`) |
| Upstream spent measuring | 53 MB | 53 MB (fail-open run) + 38 MB |

## 5. Not tested

- Shape A with `$connections_writing` instead of `$connections_active`
  ("connections where nginx is writing the response back to the client" —
  excludes Waiting keep-alives per the stub_status docs). It would close
  the idle-keep-alive gap of §1.2 while still counting HIT-serving
  connections (ms each) and the LAN side of in-flight MISSes; the natural
  next measurement for A, one round set (~30 MB).
- Shape B with the increment at `@miss` entry (rewrite phase) instead of at
  limiter time — the brief's original wording, expected to remove the
  burst transient for the same reason A has none; unmeasured.
- Shape B leak mitigations (`timeout=` per-request keys, `js_periodic`),
  and njs CPU cost of a body filter on a 30-way prefill.
- A floor on the divisor (`max(count, m)`, e.g. `VAULT_UPSTREAM_MIN_PARALLEL`).
  Arithmetic only: it lowers B's burst peak from H(N) to 1 + H(N) − H(m)
  (N=30: m=4 → 2.9×, m=8 → 2.3×), so it is a partial remedy, not a fix.
- Bursts slower than one upstream TTFB (a client that ramps connections
  over seconds) — A's "count leads" property depends on the burst fitting
  inside the TTFB.
- 30 parallel (SteamPrefill's default), a real SteamPrefill run, HIT/MISS
  mixes, TZ/time windows (TH-0 §4.3), everything else in TH-0 §4.8.
- `proxy_store` handling of the aborted transfer's temp file (§2.3).

## 6. Recommendation (Weg A / Weg B)

**Weg A — `$connections_active` (or `$connections_writing`) bucket map,
generated at container start from one `VAULT_UPSTREAM_RATE`.** Delivers
the aggregate on the wall clock with no transient measured for a ≤ 20 ms
burst (slower ramps: bounded overshoot of at most one chunk per request
already in flight, unmeasured), no module, no state, no leak, native-rig
compatible, one-line drift delta; its measured weakness is under-use of
the WAN when idle keep-alive connections exist (0.49× with 8 idle), i.e.
it errs toward the side the operator asked for and costs prefill time,
not WAN peace. First refinement to measure: `$connections_writing`, and a
slow-ramp burst for the overshoot bound.

**Weg B — njs exact in-flight counter.** The only shape that gives each
MISS its true share regardless of idle connections (0.98× with 8 idle),
but measured to over-shoot the cap ~H(N)× in the first chunk round of a
burst (3.25× at 16; ~4× extrapolated for 30), to leak permanently on a
client abort (a normal Steam event), to fail open on any handler error,
and to be container-only with a 9-directive drift-guard delta. Choosing it
means also building entry-time increment, leak reconciliation and a
fail-closed guard — none measured here.

Trade-off in one sentence: A is honest about the cap and pessimistic about
throughput; B is honest about throughput and needs three more mechanisms
to be honest about the cap. Recommendation: **Weg A** for TH-1's first
cut, with `$connections_writing` measured before the bucket variable is
fixed. Jan decides.

## 7. Commands run against dev.sh (complete list, in order)

```
dev.sh ps ; dev.sh health                       # baseline, stack up, pristine image
dev.sh up vault-core                            # Shape A build (map + log fields + proxy_limit_rate + evidence hook)
dev.sh health ; dev.sh logs vault-core          # evidence: stub_status, njs module, apk versions
dev.sh logs vault-core                          # after each A round set (grep 773d1005)
dev.sh test-core                                # with Shape A in place -> rc=1 at the drift check
--- session cut by rate limit; resumed ---
dev.sh up vault-core                            # Shape B build 1: relative load_module -> restart loop (health curl rc=7)
dev.sh ps ; dev.sh logs vault-core              # dlopen /vault/modules/... evidence
dev.sh up vault-core                            # Shape B build 2: absolute load_module -> up, but string dict -> fail-open
dev.sh logs vault-core                          # TypeError + "invalid size" evidence
dev.sh up vault-core                            # Shape B build 3: type=number -> capped
dev.sh logs vault-core                          # after the B round set and the abort/leak probe
dev.sh test-core                                # with Shape B in place -> rc=1 at the drift check
dev.sh build vault-core && dev.sh up vault-core # pristine rebuild after git checkout -- core/
dev.sh health ; dev.sh ps ; dev.sh logs vault-core   # green, pristine log format
```

No `dev.sh down`, no `dev.sh reset`, no direct `docker` calls, no
SteamPrefill run, `dev.env` never read, nothing outside `core/` touched
(the parallel `agent/go/` modifications in the working tree are not mine).
