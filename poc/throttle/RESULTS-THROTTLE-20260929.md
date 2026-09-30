# WP TH-0 — upstream throttling spike (measure, no production code)

Generated: 2026-09-29 (22:40-22:45 CEST = 20:40-20:45 UTC)
Instance: the test stack on the test host (vault-core at
`http://<vault-core>:8081`, image `steamhangar/vault-core:dev` built from
`core/Dockerfile` = `nginx:1.29.8-alpine3.23@sha256:5616878…`), driven only
through the test host's `dev.sh` wrapper. Repo state: `main` at 80f750f, clean.
Every change under `core/` was an experiment and is reverted; this file is
the only artefact.

Question (docs/PROJECT_PLAN.md §11 roadmap item 10): can the Steam → vault
(WAN) bandwidth be capped nginx-natively in vault-core's `@miss` path, with
the cap depending on the time of day, so a daytime manual prefill runs
capped while the 03:00-07:00 schedule window runs at full speed?

**Verdict: GO for the nginx-native path** (`proxy_limit_rate $var` +
`map $time_iso8601 $var`), with the limits in section 4 stated for TH-1.

---

## 1. Part A — how many concurrent chunk requests does SteamPrefill open?

Source read at the tag the runner image pins (`api/Dockerfile:38`:
`ARG STEAMPREFILL_VERSION=3.7.1`); the `master` branch was checked too and
is identical in every quoted line.

### 1.1 Default is 30, hardcoded in a model class

<https://github.com/tpill90/steam-lancache-prefill/blob/v3.7.1/SteamPrefill/Models/DownloadArguments.cs>

```csharp
private int _maxConcurrentRequests = 30;
...
/// Limits the maximum number of requests that can be in flight at any one time.  Does not guarantee there will always be 30,
/// it is just an upper limit.
///
/// The default of 30 was found to be a good middle ground for maximum throughput. It also minimizes the potential for SteamPrefill to
/// choke out any other downloads on the network, without having to require users setup QoS themselves.
public int MaxConcurrentRequests
{
    get
    {
        //TODO I don't like how this is using a static variable like this, because nothing else in this class is like this. Consider removing this later once its no longer used for debugging.
        if (AppConfig.MaxConcurrencyOverride != null)
        {
            return AppConfig.MaxConcurrencyOverride.Value;
        }
        return _maxConcurrentRequests;
    }
    set => _maxConcurrentRequests = value;
}
```

`PrefillCommand.cs` (v3.7.1, lines 64-69) constructs `DownloadArguments`
with only `Force`, `TransferSpeedUnit`, `OperatingSystems` — it never sets
`MaxConcurrentRequests`, and no `[CommandOption]` in that file mentions
concurrency. Not a config-file setting either
(`SteamPrefill/Settings/AppConfig.cs` has only the static
`public static int? MaxConcurrencyOverride { get; set; }`, line 95).

### 1.2 There IS a hidden CLI override: `--max-threads N`

Not a CliFx option, so it is absent from `--help` and from the upstream
docs; it is pre-parsed and stripped from `args` in `Program.cs` before
CliFx runs:

<https://github.com/tpill90/steam-lancache-prefill/blob/v3.7.1/SteamPrefill/Program.cs> (lines 113-122)

```csharp
if (args.Any(e => e.Contains("--max-threads")))
{
    var flagIndex = args.IndexOf("--max-threads");
    var count = args[flagIndex + 1];
    AppConfig.MaxConcurrencyOverride = int.Parse(count);

    AnsiConsole.Console.LogMarkupLine($"Using {LightYellow("--max-threads")} flag.  Will download using at most {Magenta(count)} threads");
    args.Remove("--max-threads");
    args.Remove(count);
}
```

Our runner builds its command in `api/vault_api/prefill.py:286-289` as
`[executable, "prefill", ("--force",) "--no-ansi"]`, so `--max-threads`
could be appended there if TH-1 wants to bound the multiplier from the
runner side. It is a debugging flag (the upstream TODO says so); treat it
as unstable, never as the primary mechanism.

### 1.2b No bandwidth/rate option either (PROJECT_PLAN §11 item 10, question a)

The complete `[CommandOption]` set of `PrefillCommand.cs` at v3.7.1 is
`all`, `recent`, `recently-purchased`, `top`, `force`, `os`, `verbose`,
`unit`, `no-ansi`. `unit` only selects the DISPLAY unit
(`TransferSpeedUnit`, bits vs. bytes) and does not throttle. No option,
config key or hidden pre-parsed flag in `Program.cs` limits bytes per
second — the only knob is the concurrency override above. Throttling has
to live outside SteamPrefill.

### 1.3 The shared HttpClient pins NO connection limit

<https://github.com/tpill90/steam-lancache-prefill/blob/v3.7.1/SteamPrefill/Handlers/DownloadHandler.cs>

```csharp
_client = new HttpClient();
// Lancache requires this user agent in order to correctly identify and cache Valve's content servers
_client.DefaultRequestHeaders.Add("User-Agent", "Valve/Steam HTTP Client 1.0");
...
await Parallel.ForEachAsync(requestsToDownload, new ParallelOptions { MaxDegreeOfParallelism = downloadArgs.MaxConcurrentRequests }, body: async (request, _) =>
```

No handler is configured, so .NET's default applies —
<https://learn.microsoft.com/en-us/dotnet/api/system.net.http.httpclienthandler.maxconnectionsperserver>:
"The default value for this property is `int.MaxValue`." Consequence: up
to 30 requests in flight → up to 30 keep-alive connections to vault-core →
on a cold game up to 30 simultaneous `@miss` upstream fetches, each capped
individually.

**Part A conclusion:** the effective WAN ceiling under a per-request
nginx cap is `rate × min(30, chunks still missing)` for a SteamPrefill run.
A 200 KB/s per-request cap means ≈ 6 MB/s ≈ 48 Mbit/s aggregate on a cold
game. TH-1 must dimension the operator-facing number as an aggregate and
divide by 30 (or pass `--max-threads`), not expose the raw per-request
value.

---

## 2. Part B — does `proxy_limit_rate $var` cap the proxy_store MISS path?

> Erratum (2026-09-30, WP TH-0b): nginx parses `200k` as 200 × 1024 =
> 204,800 B/s, so the percentages in 2.2 are 96.4-97.6 % of the cap (not
> 98.7-99.9 % of 200,000) and the 2.5 ratio is 3.88× (not 3.97×); the
> conclusions stand — see `poc/throttle/RESULTS-THROTTLE-DYNAMIC-20260930.md`.

### 2.0 Setup facts

- Test object: `/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2`
  with `Host: lancache.steamcontent.com` (the WP 0.2 object, still live:
  999,232 bytes, SHA256 `c78fb9f8a88318dd61f318bb95f0b59911c9bbbf8678f6ef2d2724cdbc56a66c`,
  identical to poc/RANGE-FINDINGS.md ground truth). The `$host` map at
  `core/docker/nginx.conf.template:177` rewrites that Host to
  `dist-fra1.discovery.steamserver.net`.
- Forced MISS: `?nocache=1` → `map $arg_nocache $vault_try_target` (template
  line ~220) points `try_files` at `/__steamhangar_force_miss__$uri`, which
  never exists, so every such request falls through to `@miss` and refetches
  from upstream (and re-stores). Every measured request below is confirmed
  `cache=MISS` in `dev.sh logs vault-core`.
- Container clock: **UTC**. `deploy/compose.yaml` forwards `TZ: ${TZ:-UTC}`
  to vault-api (line 253) and vault-runner (line 691), not to vault-core
  (its `environment:` block, lines 127-146, has `VAULT_RESOLVER` and
  `VAULT_EVENT_LOG` only). Evidence: every vault-core log line carries
  `+0000` (`29/Sep/2026:20:40:31 +0000`) while the test host is CEST. So a
  `$time_iso8601` regex in vault-core matches UTC hours until TH-1 forwards
  `TZ` (the schedule window in vault-api is evaluated in ITS `TZ`).
  Forwarding `TZ` presupposes the image resolves named zones: the upstream
  `nginx/docker-nginx` `stable/alpine-slim/Dockerfile` installs tzdata with
  the comment "Bring in tzdata so users could set the timezones through the
  environment variables" (`apk add --no-cache tzdata`,
  <https://github.com/nginx/docker-nginx/blob/master/stable/alpine-slim/Dockerfile>),
  so a named `TZ` should take effect — but this is coupled to the image
  pin and was NOT measured here; TH-1 verifies the log offset live after
  forwarding (`dev.sh logs vault-core` must show `+02:00`-style offsets,
  not `+0000`).
- Measurement command (same for every run; `speed_download` is bytes/s):

  ```
  curl -s -o /dev/null -w '%{http_code} %{size_download} %{speed_download} %{time_total}\n' \
    -H 'Host: lancache.steamcontent.com' \
    'http://<vault-core>:8081/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2?nocache=1'
  ```
- Total pulled from Steam across the whole spike: 17 MISS fetches × 999,232 B
  ≈ 17 MB (budget was 50 MB).
- Config validity on each experiment build: `dev.sh up vault-core` rebuilt
  the image and the container came up healthy and served — nginx refuses to
  start on a bad config, so each variant passed the real `nginx -t`.

### 2.1 Baseline (pristine image, no cap)

| run | status | bytes | speed (B/s) | time_total (s) |
|---|---|---|---|---|
| 1 | 200 | 999232 | 2,681,602 | 0.373 |
| 2 | 200 | 999232 | 2,467,239 | 0.405 |
| 3 | 200 | 999232 | 2,642,597 | 0.378 |

Log (`dev.sh logs vault-core | grep 773d1005`):

```
29/Sep/2026:20:40:31 +0000 uri="/depot/70403/chunk/773d…?nocache=1" status=200 range="-" upstream_status=200 bytes_sent=999703 request_time=0.372 cache=MISS
29/Sep/2026:20:40:32 +0000 uri="…?nocache=1" status=200 … request_time=0.404 cache=MISS
29/Sep/2026:20:40:32 +0000 uri="…?nocache=1" status=200 … request_time=0.377 cache=MISS
```

Baseline ≈ 2.5-2.7 MB/s (≈ 21 Mbit/s) for a single request from
dist-fra1 to the test host at this hour — this is the WAN/CDN single-stream rate,
not a cache limit (a HIT of the same object served in 0.001 s, see 2.4).

### 2.2 Experiment build: fixed 200k cap

Diff applied to `core/docker/nginx.conf.template` (http block, after the
`$vault_try_target` map; and inside `location @miss` after
`proxy_store_access`):

```nginx
    map $time_iso8601 $vault_upstream_rate {
        default 200k;
    }
...
            proxy_limit_rate   $vault_upstream_rate; # TH-0 EXPERIMENT
```

Build: `dev.sh up vault-core` (= `compose up -d --build vault-core`), then
`dev.sh health` polled until `core /health -> ok`.

| run | status | bytes | speed (B/s) | time_total (s) |
|---|---|---|---|---|
| 1 | 200 | 999232 | 197,334 | 5.064 |
| 2 | 200 | 999232 | 198,521 | 5.033 |
| 3 | 200 | 999232 | 199,833 | 5.000 |

```
29/Sep/2026:20:42:00 +0000 uri="…?nocache=1" status=200 range="-" upstream_status=200 bytes_sent=999703 request_time=5.063 cache=MISS
29/Sep/2026:20:42:05 +0000 … request_time=5.032 cache=MISS
29/Sep/2026:20:42:10 +0000 … request_time=4.999 cache=MISS
```

**Delta vs. baseline: 2.6 MB/s → 0.198 MB/s, i.e. 13× slower; measured rate
is 98.7-99.9 % of the configured 200,000 B/s.** The cap is exact and
stable across runs. (`proxy_buffering` is on by default and proxy_store
requires it anyway, so nothing else had to change.)

### 2.3 Time dependence: the map is evaluated per request against the live clock

Two builds, current container hour = 20 UTC (`date -u` on the host at
build time: `20:43:08` and `20:43:22`; container is UTC per 2.0).

**Build A — regex matches the CURRENT hour → 0 (unlimited):**

```nginx
    map $time_iso8601 $vault_upstream_rate {
        default 200k;
        "~^\d{4}-\d{2}-\d{2}T20:" 0;
    }
```

| run | status | bytes | speed (B/s) | time_total (s) |
|---|---|---|---|---|
| 1 | 200 | 999232 | 1,869,627 | 0.534 |
| 2 | 200 | 999232 | 2,331,625 | 0.429 |
| 3 | 200 | 999232 | 2,183,235 | 0.458 |

```
29/Sep/2026:20:43:12 +0000 … request_time=0.534 cache=MISS
29/Sep/2026:20:43:12 +0000 … request_time=0.428 cache=MISS
29/Sep/2026:20:43:13 +0000 … request_time=0.457 cache=MISS
```

→ uncapped (baseline speed) although `default` is 200k.

**Build B — regex matches a NON-current hour (23) → 0; default 200k:**

```nginx
    map $time_iso8601 $vault_upstream_rate {
        default 200k;
        "~^\d{4}-\d{2}-\d{2}T23:" 0;
    }
```

| run | status | bytes | speed (B/s) | time_total (s) |
|---|---|---|---|---|
| 1 | 200 | 999232 | 199,343 | 5.013 |
| 2 | 200 | 999232 | 199,573 | 5.007 |
| 3 | 200 | 999232 | 199,986 | 4.996 |

```
29/Sep/2026:20:43:31 +0000 … request_time=5.012 cache=MISS
29/Sep/2026:20:43:36 +0000 … request_time=5.005 cache=MISS
29/Sep/2026:20:43:41 +0000 … request_time=4.995 cache=MISS
```

→ capped. Same directive, same map shape, only the hour literal differs,
and the outcome flips — so `$time_iso8601` is evaluated per request
against the live clock and `proxy_limit_rate` honours the map result.
Both builds used the full `^\d{4}-\d{2}-\d{2}T<hh>:` anchor; a shorter
hour-bucket pattern for the 03:00-07:00 window is a derivation, not a
measurement (see 4.8).

Doc check: nginx says variables in `proxy_limit_rate` arrived in **1.27.0**
(<https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_limit_rate>:
"Parameter value can contain variables (1.27.0)."), not 1.17 as the brief
assumed. The pinned image is 1.29.8, so this is fine; TH-1's
documentation must state ≥ 1.27.0 as the floor. The native rig satisfies
it too: `poc/README.md:48` / `poc/setup.ps1` (`$NginxVersion = "1.30.4"`)
fetch nginx 1.30.4 for Windows.

### 2.4 Integrity: a capped MISS still lands complete and serves as a HIT

Right after the three capped MISS runs of 2.2 (same build), the same URL
without `?nocache=1`:

```
curl -s -o /dev/null -w '%{http_code} %{size_download} %{speed_download} %{time_total}\n' -H 'Host: lancache.steamcontent.com' 'http://<vault-core>:8081/depot/70403/chunk/773d…'
200 999232 920951152 0.001085
curl -s -H 'Host: lancache.steamcontent.com' 'http://<vault-core>:8081/depot/70403/chunk/773d…' | sha256sum
c78fb9f8a88318dd61f318bb95f0b59911c9bbbf8678f6ef2d2724cdbc56a66c  -
```

```
29/Sep/2026:20:42:32 +0000 uri="/depot/70403/chunk/773d…" status=200 range="-" upstream_status=- bytes_sent=999490 request_time=0.000 cache=HIT
29/Sep/2026:20:42:32 +0000 … upstream_status=- bytes_sent=999490 request_time=0.000 cache=HIT
```

Same 999,232 bytes, same SHA256 as the WP 0.2 ground truth, served as HIT
in 1 ms (≈ 920 MB/s — LAN serving is untouched by the cap, as required).

### 2.5 Concurrency: the cap is per request, so 4 requests ≈ 4 × cap

Four parallel capped MISS curls (`&` + `wait`, 200k build):

```
1 200 999232 198490 5.034
2 200 999232 198517 5.033
3 200 999232 198427 5.036
4 200 999232 199226 5.016
```

```
29/Sep/2026:20:42:37 +0000 … request_time=5.014 cache=MISS
29/Sep/2026:20:42:37 +0000 … request_time=5.031 cache=MISS
29/Sep/2026:20:42:37 +0000 … request_time=5.032 cache=MISS
29/Sep/2026:20:42:37 +0000 … request_time=5.034 cache=MISS
```

All four finished in the same second, each at its own full 200 KB/s.
Aggregate = 4 × 999,232 B / 5.036 s = **793,671 B/s = 3.97 × the cap**.
The divisor is the slowest request's `time_total`, not a wall clock: the
wall clock was not captured (`bc` is absent on the test host and the
capture line failed). Using the slowest `time_total` overstates the
aggregate by at most the launch stagger between the four curls, which is
below 20 ms per the four `time_total` values (5.016-5.036 s) and the four
same-second log lines; the ratio is therefore 3.9-4.0×, not exactly 3.97.
This confirms the nginx doc sentence verbatim: "The limit is set
per a request, and so if nginx simultaneously opens two connections to
the proxied server, the overall rate will be twice as much as the
specified limit." Combined with Part A: SteamPrefill's 30 in-flight
requests scale this to ≈ 30 × cap.

### 2.6 Drift guard and test-core with the experiment in place

`sh core/docker/check-config-drift.sh` → exit 1:

```
check-config-drift: FAIL -- the container template diverges from core/nginx/nginx.conf
@@ -31,6 +31,9 @@
+map $time_iso8601 $vault_upstream_rate {
+default 200k;
+}
@@ -87,6 +90,7 @@
+proxy_limit_rate $vault_upstream_rate; # TH-0 EXPERIMENT
```

`dev.sh test-core` → exit 1 with the identical diff: the CI gate
(`.github/scripts/verify-core-nginx.sh`, line 63-64) runs the drift check
FIRST under `set -e`, so the `nginx -t` half never ran with the experiment
in place (validity is established by the running container instead, see
2.0). Meaning for TH-1: either (a) put the same map + directive into the
native `core/nginx/nginx.conf` so both files stay identical after
normalisation (the guard's design intent — but the native Windows test
rig then needs nginx ≥ 1.27.0 and the map's window literals become a
build-time constant in the native file), or (b) template the rate/window
through envsubst placeholders like `${VAULT_RESOLVER}` and register them
as a seventh allowed delta (`expect_once` + a `sed` un-apply line) in
`check-config-drift.sh`. Given that the window/rate must be operator
configuration, (b) is the shape that matches the existing deltas.

After `git checkout -- core/` + pristine rebuild the guard is green again:
`check-config-drift: OK -- 102 normalised directive lines identical`.

### 2.7 Revert and pristine state

```
git checkout -- core/
dev.sh build vault-core && dev.sh up vault-core
dev.sh health     -> core /health -> ok, X-LanCache-Processed-By: steamhangar, api {"status":"ok"}
```

Pristine sanity MISS: `200 999232 3015948 0.331` (uncapped, log
`20:44:00 +0000 … request_time=0.330 cache=MISS`). `dev.sh ps`: all four
services up, vault-core recreated. `git status --short`: only this file.

---

## 3. Go / No-Go

**GO.** `proxy_limit_rate $vault_upstream_rate` inside `location @miss`,
fed by `map $time_iso8601`, caps the proxy_store MISS path exactly
(198-200 KB/s against a 200k setting, 13× below the 2.6 MB/s baseline),
flips per request with the live clock, stores the full object, leaves HIT
serving at LAN speed, and the measured mechanism needs no new nginx
module. The FEATURE does need a compose change (forward `TZ` to
vault-core, 4.3) and optionally a runner change (`--max-threads`, 4.1),
plus packaging: envsubst plumbing, the drift-guard delta, docs.

## 4. Open limits — state these in TH-1's docs, do not paper over them

1. **Per-request, not aggregate.** Measured 3.97× with 4 requests.
   SteamPrefill defaults to 30 in flight (Part A) with no connection
   limit, so WAN ≈ 30 × cap on a cold game. The operator-facing setting
   must be an aggregate that TH-1 divides by the runner's parallelism
   (30, or a `--max-threads` value the runner passes — an undocumented
   debugging flag). Real Steam clients on a MISS are also per-connection
   and their connection count is not pinned by anything we control.
2. **The LAN client that triggers a MISS is capped too.** The chunk is
   streamed to the requester at the capped rate (5.0 s for 1 MB at 200k
   in every capped run). Only HITs are unaffected. A daytime manual
   prefill is exactly the intended victim; a real Steam client
   downloading an uncached game by day is collateral, and the docs must
   say so. There is no nginx-native way to cap the upstream read without
   also slowing the one client waiting on it (proxy_store streams the
   response through).
3. **Clock and timezone.** vault-core runs in UTC because compose does not
   forward `TZ` to it; the schedule window in vault-api is interpreted in
   vault-api's `TZ`. TH-1 must forward the same `TZ` to vault-core (or
   express the map in UTC) or the "full speed 03:00-07:00" promise is off
   by the operator's UTC offset.
4. **Window edges are per request, not per transfer — and the cap stops
   nothing.** (PROJECT_PLAN §11 item 10, question c.) The map value is
   resolved when the request enters `@miss`; a 1 MB Steam chunk takes at
   most seconds, so edge behaviour is bounded by one chunk duration, but
   this was NOT measured across an hour boundary. A prefill job that runs
   past the window's end is neither stopped nor paused by this mechanism:
   its next chunk requests simply get the capped rate, so the job
   continues to completion, slower. Stopping at the edge would be the
   scheduler's decision, not nginx's.
5. **Configuration is baked at container start.** The map is static nginx
   config rendered by envsubst; a window or rate changed via vault-api's
   settings (db-stored, ADR-0014 style) cannot reach nginx without a
   re-render + reload. TH-1 either scopes the setting as env-only
   (documented, like `VAULT_RESOLVER`) or builds a reload path — the
   former is the honest first cut.
6. **Version floor is nginx 1.27.0** for variables in `proxy_limit_rate`
   (brief assumed 1.17). Image is 1.29.8; the native Windows rig fetches
   1.30.4 (`poc/README.md:48`, `poc/setup.ps1`) — both satisfy it.
7. **Drift guard fails by design** until TH-1 registers the new lines
   (2.6). `dev.sh test-core` is red for the same reason and stops before
   `nginx -t`.
8. **Not tested:** the short window pattern (`"~T0[3-6]:" 0;` for
   03:00-07:00 — only the full `^\d{4}-\d{2}-\d{2}T<hh>:` anchor was
   built), minute-granular boundaries via alternation, and a window that
   wraps midnight; `proxy_next_upstream` retry interaction with the cap
   (a retried request keeps the per-request limit; untested), `limit_rate`
   granularity below ~100k (nginx paces per event-loop write; very small
   values may be coarse), and the runner's own speed probe
   (`?nocache=1` + `Range bytes=0-0`) — under a cap it will simply report
   the capped rate, which SteamPrefill only displays.

## 5. Commands run against dev.sh (complete list, in order)

```
dev.sh ps
dev.sh health
dev.sh logs vault-core            # (grep 773d1005; after every measurement set)
dev.sh up vault-core              # experiment build 1: default 200k
dev.sh test-core                  # with the experiment in place -> rc=1 at the drift check
dev.sh up vault-core              # build A: "~^\d{4}-\d{2}-\d{2}T20:" 0
dev.sh up vault-core              # build B: "~^\d{4}-\d{2}-\d{2}T23:" 0
dev.sh build vault-core && dev.sh up vault-core   # pristine rebuild after git checkout -- core/
dev.sh health                     # green
dev.sh ps
```

No `dev.sh down`, no `dev.sh reset`, no direct `docker` calls, no
SteamPrefill run, `dev.env` never read.
