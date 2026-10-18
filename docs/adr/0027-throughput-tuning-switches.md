# ADR-0027: Throughput tuning switches and heartbeat diagnostics

Status: Accepted

Date: 2026-10-16

Related: ADR-0017 (upstream keepalive pool), ADR-0025 (live throughput tier),
`core/README.md`, `core/tools/throughput-report.py`, `docs/LEARNINGS.md`.

## Context

Measurement on 2026-10-16, same game (Satisfactory depot), same client PC,
32 connections:

- Cold run (content not in ARC): per-request p50 3.3 MB/s (about 250-300
  Mbit/s). The heartbeat request showed 1.0-1.8 s latency.
- Warm run: per-request p50 12.6 MB/s, up to about 880 Mbit/s, 0 disk reads,
  nginx at 2-5% CPU, heartbeat latency 0.7 ms.
- A SteamPrefill job failed with "Unable to detect Lancache server!" during
  the cold phase. The root cause is unproven.
- Runner and client PC contention were ruled out.

Hypothesis (not proven): one nginx worker with blocking `sendfile` on a
4-HDD ZFS pool serializes cold reads and delays small requests such as the
heartbeat; ARC makes warm reads fast.

## Decision

Ship two opt-in switches, both off by default, plus heartbeat lines in the
opt-in debug log:

- `VAULT_NGINX_AIO=threads` renders `aio threads;` and sets `sendfile off`
  (`aio threads` does nothing while `sendfile on` is used without directio).
- `VAULT_NGINX_WORKERS` (`auto` or 1..16, default 1 (unset)) sets the worker count.
- The heartbeat has no access log on main, so it is only visible with
  `VAULT_DEBUG_THROUGHPUT`; the report has a Heartbeat section.

Not default-on, because:

- The effect is unmeasured; the hypothesis may be wrong.
- The upstream keepalive pool (ADR-0017) is per worker, so more workers mean
  more idle upstream connections and a lower reuse rate.
- `proxy_limit_rate` (the rate cap) is per connection; with more workers the
  aggregate reasoning about the total cap must be re-checked before a default
  changes.
- `sendfile off` costs CPU on warm hits, which are fast today.

## How to test

Sunday cold-game protocol, same game, same PC, same connection count, game
evicted from cache before each run, debug log on:

1. Baseline cold run (no switches).
2. Cold run with `VAULT_NGINX_AIO=threads`.
3. Cold run with `VAULT_NGINX_WORKERS=2` (alone, from baseline).

Compare per-request p50 and the heartbeat maximum from the report. Always
label runs cold or warm; one run proves nothing.

## Rollback

Unset the variable and recreate vault-core. No data or schema is touched.

## Consequences

- Default behaviour is byte-identical to rc19.2 with the switches unset.
- A default change needs a new ADR with the measured numbers.

## Measurement 2026-10-18 (Sunday cold-game test)

Protocol: `docs/handover/cold-game-test-plan.md`. Same game (Satisfactory,
29,854 requests, 13.17 GB, 100 % HIT), same wired client PC, 32 connections,
1 Gbit/s link. "Cold" means the host page cache and ARC were dropped right
before the run (ARC down to 1-2 GB), proven by disk reads during the run.
Duration runs from the first to the last depot request in the debug log.
The heartbeat was probed every 5 s from a second container on the bridge
network.

| Run | Switches | State | Duration | Avg Mbit/s | p95 Mbit/s (10 s) | Per-request p50 | Heartbeat p50 / max |
|---|---|---|---|---|---|---|---|
| 1 | none | cold | 16 min 09 s | 109 | 208 | 0.9 MB/s | 1.95 s / 7.58 s |
| 2 | AIO=threads | cold | 2 min 23 s | 736 | 939 | 9.0 MB/s | 0.8 ms / 295 ms |
| 3 | WORKERS=2 | cold | 11 min 15 s | 156 | 242 | 1.3 MB/s | 1.10 s / 2.98 s |
| 4 | none | cold | 11 min 23 s | 154 | 351 | 1.3 MB/s | 1.34 s / 4.99 s |
| 5 | AIO=threads | cold | 2 min 25 s | 726 | 923 | 8.3 MB/s | 0.8 ms / 41 ms |
| 6 | AIO=threads | warm | 2 min 22 s | 741 | 941 | 14.4 MB/s | 0.8 ms / 3.4 ms |

Findings:

- The hypothesis holds. Blocking `sendfile` on one worker serializes cold
  reads, and with `aio threads` the pool is read in parallel. Per Pool disk,
  iostat showed about 5k blocks/s without AIO and about 81k-84k with AIO.
- AIO met every criterion fixed before the test: more than +30 % against
  both baselines, heartbeat max below 100 ms (run 5; run 2 had one 295 ms
  spike), 100 % HIT. Cold and warm alike, the 1 Gbit/s link is the limit.
  The warm run showed no measurable host CPU increase (total about 30 %, the
  same as idle; iowait below 2 %). The nginx process CPU alone was not
  measured.
- WORKERS=2 brought nothing over the faster baseline, and the heartbeat
  stayed at seconds, because both workers block. No default.
- The heartbeat lines in the debug log showed 0.0 ms throughout. The wait
  happens before nginx accepts the request, so only an outside probe sees
  it.
- The two baselines differed by 42 %, against the planned 30 %. The verdict
  does not depend on it, since AIO is 4.7 times faster than the faster
  baseline. Cause unknown (BASELINE-SPREAD-1 in the roadmap).

Decision status: whether and how `VAULT_NGINX_AIO=threads` becomes the
default is the open decision AIO-DEFAULT-1 in
`docs/handover/post-0.1.0-roadmap.md`. A default change still needs a new
ADR. Production runs with both switches unset again; the test was rolled
back on 2026-10-18.
