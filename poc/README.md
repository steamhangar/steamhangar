# SteamVault — Phase 0 PoC (Work Package 0.1)

> **Status (banner added 2026-09-30).** Phase 0 is complete and this folder
> is frozen as evidence; the text below is the WP 0.1 write-up as it stood
> then, and its "awaiting" / "pending" notes are historical. Every planned
> run happened: WP 0.3 (real Steam client,
> `steam-client-test/RESULTS-20260804-*.md`) and WP 0.6 (Linux client under
> WSL2, `linux-client-test/RESULTS-20260805-*.md` — the current Linux
> client DOES perform the `lancache.steamcontent.com` lookup, ADR-0001 /
> ADR-0002 addenda). The outcome is recorded in ADR-0001 and
> `docs/PROJECT_PLAN.md` §7 Phase 0. The one exception to the freeze is
> `throttle/`: later measurement spikes (WP TH-0, TH-0b) land there.

Throwaway feasibility code for `docs/PROJECT_PLAN.md` section 7 ("Phase 0 —
Feasibility PoC"). **Nothing under `poc/` is production code.** It exists to
answer one question before any real implementation starts:

> Can nginx's `proxy_store` cache Steam CDN depot chunks **path-faithfully**
> on disk (so a game = a folder, and deleting a game = `rm -rf` that folder),
> and serve repeat requests straight from disk without hitting the upstream
> again?

This package (0.1) answers that question for plain (non-Range) GET requests,
running nginx natively on Windows — no Docker (deliberate: containerization
happens at the end of Phase 1, per the project's working agreement).

Range-request behavior is explicitly **out of scope here** — see WP 0.2.
Testing against the real Steam client is explicitly **out of scope here** —
see WP 0.3. SteamPrefill is explicitly **out of scope here** — see WP 0.4
([`steamprefill/PROTOCOL.md`](steamprefill/PROTOCOL.md)).

## What's in this folder

| Path | Purpose |
|---|---|
| `setup.ps1` | Downloads + extracts official nginx-for-Windows into `nginx/`. Idempotent. |
| `start.ps1` / `stop.ps1` | Start/stop nginx. `start.ps1` accepts `-Config <name>` (default `nginx.conf`). |
| `conf/nginx.conf` | The purpose-built PoC config, WP 0.1 baseline: synchronous store-on-miss (`proxy_store`). Was frozen through WP 0.1/0.2/0.3/0.5 (whose measurements are complete and committed); the freeze was lifted for WP 0.4 to add the `/lancache-heartbeat` endpoint (see below) — otherwise unchanged. |
| `conf/nginx-passthrough.conf` | WP 0.5: transparent-passthrough variant — same HIT path (`try_files`, so a prefill-filled cache still serves as a HIT), but a MISS is proxied straight through with **no `proxy_store`**, no disk write. |
| `test-smoke.ps1` | Automated proof: MISS then HIT against a real Steam CDN chunk (store config). |
| `test-range.ps1` | WP 0.2: Range-request test suite (cold/warm cache, suffix/mid/multi-range, concurrent downloads). See `RANGE-FINDINGS.md`. |
| `RANGE-FINDINGS.md` | WP 0.2 evidence write-up answering the Phase 0 critical question on `proxy_store` + Range requests. |
| `test-misshandling.ps1` | WP 0.5: correctness + latency/throughput measurement suite comparing `nginx.conf` (store) vs. `nginx-passthrough.conf` (passthrough) on cache miss. See `MISS-HANDLING-FINDINGS.md`. |
| `MISS-HANDLING-FINDINGS.md` | WP 0.5 evidence write-up for the "Miss-handling decision" checkbox (`docs/PROJECT_PLAN.md` §7) — synchronous store vs. transparent passthrough, correctness + measured latency/throughput, preliminary Phase-0-gate recommendation. |
| `steam-client-test/` | WP 0.3: real-Steam-client test kit — `PROTOCOL.md` (step-by-step protocol for the human-run test), `analyze.ps1` (mines `logs/access.log` and answers the Phase-0 checkboxes automatically), `test-analyze.ps1` (proves the analysis script itself against a synthetic fixture log). See below. |
| `steamprefill/` | WP 0.4: SteamPrefill test kit — `setup.ps1` (downloads the latest `tpill90/steam-lancache-prefill` Windows x64 release into `steamprefill/bin/`, gitignored), `PROTOCOL.md` (protocol for the human-run login/select-apps/prefill test — **read section 0 first**, it explains the `/lancache-heartbeat` + `X-LanCache-Processed-By` contract SteamPrefill's auto-detection requires, now implemented in both nginx configs), `verify.ps1` (mines `logs/access.log` + `cache/depot/` and answers the Phase-0 checkboxes), `test-verify.ps1` (proves `verify.ps1` against a synthetic fixture log + fake cache dir). See `steamprefill/PROTOCOL.md`. |
| `linux-client-test/` | WP 0.6: Linux-Steam-client (WSL2) test kit — run for real on 2026-08-05 (`RESULTS-20260805-*.md`; the "awaiting WSL2 setup" note below is historical). |
| `throttle/` | Post-Phase-0 measurement spikes for download throttling, run against the containerized test stack: WP TH-0 (`RESULTS-THROTTLE-20260929.md` — nginx-native `proxy_limit_rate` caps the miss path) and WP TH-0b (`RESULTS-THROTTLE-DYNAMIC-20260930.md` — one aggregate cap divided by the live connection count). Evidence only; the implementation is WP TH-1. |
| `nginx/` | Extracted nginx binary (gitignored, recreated by `setup.ps1`). |
| `cache/` | The cache store, `cache/depot/<id>/...` (gitignored). |
| `logs/` | nginx access/error log + pid (gitignored). |

## Setup

```powershell
cd poc
.\setup.ps1
```

Downloads nginx 1.30.4 (stable) from nginx.org (~1.7 MB) into `poc/nginx/`
and creates `poc/cache/` and `poc/logs/` (plus the temp-path subdirectories
nginx needs at startup). Safe to re-run — skips the download if
`poc/nginx/nginx.exe` already exists.

## Start / stop

```powershell
.\start.ps1                                    # store config (nginx.conf), unchanged default
.\start.ps1 -Config nginx-passthrough.conf     # WP 0.5 passthrough variant
.\stop.ps1                                     # graceful `nginx -s stop` (works for either config)
```

`start.ps1` launches `nginx.exe -p <poc> -c conf/<Config>`; all relative
paths inside `nginx.conf` (root, logs, temp dirs) resolve against that `-p`
prefix, so the config doesn't hardcode this machine's checkout path.

Manual check:

```powershell
curl.exe -i http://127.0.0.1/health
```

## Test

```powershell
.\test-smoke.ps1
```

For Range-request behavior (WP 0.2 — the §7/§9 critical question), see:

```powershell
.\test-range.ps1
```

This covers cold-cache Range requests, warm-cache Range requests (simple,
suffix, mid-file, multi-range), and concurrent cold-cache downloads,
verifying stored-file integrity (size + SHA256) at every step. Same
`-DepotId`/`-ChunkHash`/`-BaseUrl` overrides as `test-smoke.ps1`. Full
results and the Plan A/B recommendation are written up in
[`RANGE-FINDINGS.md`](RANGE-FINDINGS.md) — **read that file for the actual
Phase 0 verdict**, the console PASS/FAIL exit code alone does not capture
it (see the file's own header for why).

`test-smoke.ps1` starts nginx if needed, clears any previously cached copy
of the test object, fetches a real Steam CDN depot chunk twice through the
proxy, and asserts:

1. First request → HTTP 200, and the file appears under
   `poc/cache/depot/<depotid>/chunk/<hash>`.
2. Second request → served without contacting upstream, verified via the
   access log's `cache=HIT` / `upstream_status=-` marker (vs. `cache=MISS`
   / `upstream_status=200` on the first request).
3. Both response bodies are byte-identical (SHA256 compared).

Exit code `0` = PASS, `1` = FAIL, with a itemized `[ OK ]` / `[FAIL]` list
either way.

The default test target is depot `70403`, chunk
`773d10050d99b2544665873ec2125b3bf273e8b2` (a 999,232-byte chunk, confirmed
reachable and served correctly by the real Steam CDN on 2026-08-04 while
building this PoC). If that chunk ever gets garbage-collected upstream,
override it:

```powershell
.\test-smoke.ps1 -DepotId <id> -ChunkHash <sha1>
```

For the miss-handling design decision — synchronous store (`nginx.conf`) vs.
transparent passthrough (`nginx-passthrough.conf`), the `docs/PROJECT_PLAN.md`
§7 "Miss-handling decision" checkbox — see:

```powershell
.\test-misshandling.ps1
```

This starts/stops nginx itself, switching between both configs, and runs:
correctness checks (passthrough stores nothing on a cold miss, including on a
Range request; a pre-seeded/prefill-filled object still serves as a HIT under
passthrough; the store config's WP 0.1/0.2 MISS→store→HIT behavior is
unchanged), plus a latency/throughput measurement (N=5 iterations, cold miss
and warm HIT, against both configs). Exit code reflects only the correctness
checks — the measurement is written up, with the full numbers and a
preliminary Phase-0-gate recommendation, in
[`MISS-HANDLING-FINDINGS.md`](MISS-HANDLING-FINDINGS.md).

For the real Steam client (WP 0.3 — a Windows hosts-file redirect + an
actual game download, not synthetic curl requests), see
[`steam-client-test/PROTOCOL.md`](steam-client-test/PROTOCOL.md) for the
step-by-step protocol and
[`steam-client-test/analyze.ps1`](steam-client-test/analyze.ps1) for the
script that mines `poc/logs/access.log` afterwards and answers the Phase-0
checkboxes (URI-scheme conformance, real-client Range usage, hit/miss
split + throughput, per-depot counts) automatically, writing a
`RESULTS-<timestamp>.md` alongside it. The analysis script has its own
test (`steam-client-test/test-analyze.ps1`, a synthetic fixture log) that
passes independently of the real-client run.

For the Linux desktop Steam client (WP 0.6 — verifying the known upstream
quirk that it does **not** perform the `lancache.steamcontent.com` lookup,
and validating the `vault-dns` wildcard-rewrite approach as the Linux/Steam
Deck-compatible alternative), see
[`linux-client-test/PROTOCOL.md`](linux-client-test/PROTOCOL.md). **Status:
pre-built, awaiting WSL2 setup** — WSL2 is not available on this machine yet
(SVM must first be enabled in BIOS, then Ubuntu installed — see that
document's section 0), so the WSL-side scripts
(`linux-client-test/wsl-setup.sh`, `scenario-a.sh`, `scenario-b.sh`) have
not been run for real yet. What *is* testable today —
`linux-client-test/analyze-windows.ps1` (a thin wrapper around this
folder's own `analyze.ps1`, see above) against a synthetic fixture, plus a
bash syntax check of the three shell scripts — is proven by
`linux-client-test/test-kit.ps1`, which passes independently of WSL2
existing.

## Result: the central assumption holds (for non-Range GET)

- `proxy_store on;` combined with `root cache;` stores the response at
  exactly the same path a static file at that URI would have (nginx derives
  the store path from `root`/`alias` + URI) — **no manual path-rewriting
  needed**, and nginx **auto-creates the full nested directory tree**
  (`cache/depot/70403/chunk/`) on first write. This was verified empirically,
  not just from documentation (see "Windows path notes" below).
- A `try_files $uri @miss;` + named-location fallback makes hit vs. miss
  trivially observable and cheap: a HIT is a plain static-file response (no
  proxy involved at all), a MISS goes through `proxy_pass` + `proxy_store`.
- Confirmed via manual curl runs and `test-smoke.ps1`: first request ~0.29s
  (real network fetch, 999,232 bytes), second request ~0.003s (disk read),
  identical bytes.

## Upstream choice and the loop-risk it avoids

**The pitfall:** WP 0.3 (real Steam client test) will add a Windows hosts
entry `lancache.steamcontent.com -> 127.0.0.1` so the Steam client discovers
this machine as its cache. But nginx, running on the *same* machine, would
also be subject to that hosts entry if it ever tried to resolve
`lancache.steamcontent.com` (or reused that name anywhere) — creating a
resolution loop where the cache tries to fetch from itself.

**The fix, in `conf/nginx.conf`:**

1. The upstream in `proxy_pass` is **`dist-fra1.discovery.steamserver.net`**,
   not `lancache.steamcontent.com` or anything in the `*.steamcontent.com`
   family. This hostname is a real Valve CDN edge — confirmed during this
   PoC's build via `nslookup lancache.steamcontent.com`, which currently
   resolves via CNAME chain through
   `origin-tier2.steampipe.steamcontent.com` →
   `steampipe-origin-tier2.steamcontent.com` →
   `cache-origin.steampipe.steamcontent.akadns.net` →
   **`dist-fra1.discovery.steamserver.net`** (A/AAAA:
   `162.254.197.9`, `162.254.197.25`, ...). Fetching directly from that final
   hostname returned identical content (verified byte-for-byte against the
   `lancache.steamcontent.com` name resolved via `--resolve` to the same IP).
   It is outside the `steamcontent.com` zone, so nothing WP 0.3's hosts-file
   change touches will ever shadow it.
2. An explicit **`resolver 1.1.1.1 ipv6=off valid=300s;`** directive makes
   nginx resolve that upstream itself, over the network, instead of
   depending on the OS resolver (which is what consults the hosts file) at
   all. This is defense in depth: even if a future work package added a
   hosts entry for this exact upstream name, nginx's own resolver would
   still bypass it, because `proxy_pass` here uses a variable
   (`http://dist-fra1.discovery.steamserver.net$request_uri`) — variables in
   `proxy_pass` force runtime resolution via the configured `resolver`
   instead of the resolution nginx does once at config-load time for
   literal hostnames.

If `dist-fra1.discovery.steamserver.net` ever stops resolving/serving
(Valve's CDN topology can change), re-run
`nslookup lancache.steamcontent.com` to find the current
`*.discovery.steamserver.net` target and swap it in — just keep it outside
the `steamcontent.com` zone.

## Windows path notes (verified, not assumed)

- Forward slashes work fine in `nginx.conf` on Windows for `root`,
  `access_log`, `proxy_temp_path`, etc. — confirmed by running `nginx -t`
  and the actual server.
- Relative paths (e.g. `root cache;`, `access_log logs/access.log;`) resolve
  against the `-p` prefix passed on the command line, **not** against the
  directory containing `nginx.conf`. That's why `start.ps1`/`stop.ps1` both
  pass `-p <poc>` explicitly.
- nginx initializes several temp-path directories at startup regardless of
  whether the corresponding module is used in the config
  (`client_body_temp_path`, `proxy_temp_path`, `fastcgi_temp_path`,
  `uwsgi_temp_path`, `scgi_temp_path`) — if any of their target directories
  don't exist, `nginx -t` fails with `CreateDirectory() ... failed (3: The
  system cannot find the path specified)`. All five are pointed at
  subdirectories under `cache/tmp/` and pre-created by `setup.ps1`. This was
  discovered empirically while bringing this PoC up (not documented clearly
  by nginx itself) — worth remembering for the Phase 1 production config.
- `proxy_store`'s directory auto-creation (see above) meant no equivalent
  pre-creation was needed for `cache/depot/...` itself — nginx creates that
  tree on demand, per depot ID, as new chunks are stored.

## Access log format

```
$time_local uri="$request_uri" status=$status range="$http_range"
upstream_status=$vault_upstream_status bytes_sent=$bytes_sent
request_time=$request_time cache=$vault_cache_status
```

`$vault_cache_status` is `HIT` or `MISS` (set directly in the relevant
`location` blocks — not inferred after the fact). `$vault_upstream_status`
is `$upstream_status` mapped to `-` when empty (i.e., on local hits, where
no upstream was ever contacted). Logged to `poc/logs/access.log`.

## What this PoC does NOT cover

- **Range requests** (`Range:` header handling, partial-content behavior,
  whether `proxy_store` can even represent a partial response correctly) —
  this package (0.1) only proves the happy path (full-body GET). Covered by
  WP 0.2 (`test-range.ps1` / `RANGE-FINDINGS.md`), with a preliminary
  (not yet final — see that file) answer to the `docs/PROJECT_PLAN.md` §9
  risk.
- **The real Steam client** — this PoC (0.1) was tested with `curl.exe`/
  `Invoke-WebRequest` directly against depot chunk URLs, not by running
  Steam with a hosts-file redirect and downloading a real game. WP 0.3
  (`steam-client-test/`) provides the protocol and the automated log
  analysis for that test (also where the loop-risk mitigation above gets
  exercised for real) — but running it still requires a human to actually
  execute the protocol (admin rights for the hosts file, a real download);
  it had not been run as of this writing.
- **SteamPrefill** — WP 0.4 (`steamprefill/`) builds the test kit and confirms
  (via the binary's own source, `LancacheIpResolver.cs`, and an empirical
  `curl` check against the running PoC) that SteamPrefill's cache
  auto-detection requires a `/lancache-heartbeat` endpoint returning an
  `X-LanCache-Processed-By` header — a real LanCache-project contract.
  Both `conf/nginx.conf` and `conf/nginx-passthrough.conf` now implement
  it (a minimal `add_header` + `return 200` location, confirmed via a live
  `curl` check and asserted automatically by `test-smoke.ps1`). The
  interactive login/select-apps/prefill run itself (see
  `steamprefill/PROTOCOL.md`) is still the user's part to execute.
- **Non-200 upstream responses** (404/403/etc.) — `proxy_store` behavior on
  error responses was not exercised or asserted here.
- **Miss-handling performance (store vs. passthrough)** — this package (0.1)
  only proves store-mode correctness, not whether synchronous store-on-miss
  is fast enough vs. the plan's prefill-first alternative. Covered by WP 0.5
  (`test-misshandling.ps1` / `MISS-HANDLING-FINDINGS.md`), also preliminary
  pending WP 0.3's real-client evidence.
- **Docker/production hardening** — no log rotation, no healthcheck
  container, no multi-worker tuning. Deliberately out of scope per the
  project's Phase 0 charter ("no production code before Phase 0").
