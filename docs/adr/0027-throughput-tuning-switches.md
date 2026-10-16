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
