# ADR-0030: `aio threads` is the default of vault-core's nginx

Status: Accepted

Date: 2026-10-18

Related: ADR-0027 (throughput tuning switches, "Measurement 2026-10-18"),
ADR-0017 (upstream keepalive pool), `core/README.md` "Throughput tuning
switches", `docs/handover/cold-game-test-plan.md`, roadmap item
AIO-DEFAULT-1 (`docs/handover/post-0.1.0-roadmap.md`), WP AIO-DEFAULT-1b.

Supersedes in part: ADR-0027, for the default of `VAULT_NGINX_AIO` only.
`VAULT_NGINX_WORKERS` keeps its ADR-0027 default.

## Context

ADR-0027 shipped `VAULT_NGINX_AIO=threads` as an opt-in switch and asked for
a new ADR with measured numbers before any default change. The cold-game test
of 2026-10-18 (same game, Satisfactory, 29,854 requests, 13.17 GB, 100 % HIT;
same wired client PC; 32 connections; 1 Gbit/s link; host page cache and ARC
dropped before every cold run) measured:

| Run | Switches | State | Duration | Avg Mbit/s | p95 Mbit/s (10 s) | Per-request p50 | Heartbeat p50 / max |
|---|---|---|---|---|---|---|---|
| 1 | none | cold | 16 min 09 s | 109 | 208 | 0.9 MB/s | 1.95 s / 7.58 s |
| 2 | AIO=threads | cold | 2 min 23 s | 736 | 939 | 9.0 MB/s | 0.8 ms / 295 ms |
| 3 | WORKERS=2 | cold | 11 min 15 s | 156 | 242 | 1.3 MB/s | 1.10 s / 2.98 s |
| 4 | none | cold | 11 min 23 s | 154 | 351 | 1.3 MB/s | 1.34 s / 4.99 s |
| 5 | AIO=threads | cold | 2 min 25 s | 726 | 923 | 8.3 MB/s | 0.8 ms / 41 ms |
| 6 | AIO=threads | warm | 2 min 22 s | 741 | 941 | 14.4 MB/s | 0.8 ms / 3.4 ms |

In short: cold reads with AIO were 4.7 to 6.8 times faster than the two
baselines and limited by the 1 Gbit/s link; the heartbeat maximum fell from
3-7.6 s to 41-295 ms; per Pool disk, reads went from about 5k blocks/s to
81k-84k. The warm run showed no measurable host CPU increase (total about
30 %, as idle; iowait below 2 %). `WORKERS=2` brought nothing over the faster
baseline, and its heartbeat stayed at seconds. Full findings: ADR-0027
"Measurement 2026-10-18".

The operator decided on 2026-10-18 (AIO-DEFAULT-1, Weg B): production gets
`VAULT_NGINX_AIO=threads` through its `.env` at once (Antrag A-20261018-02),
and rc21 ships the code default so that line can go.

## Decision

1. Unset or blank `VAULT_NGINX_AIO` renders `aio threads;` in the `http`
   block and switches `sendfile on;` to `sendfile off;`, exactly what
   `VAULT_NGINX_AIO=threads` rendered before. `threads` stays valid and
   renders the same config.
2. `VAULT_NGINX_AIO=off` is the opt-out. It renders no `aio` and keeps the
   shipped `sendfile on;`, byte-identical to the pre-rc21 default render.
3. Any other value still stops the boot in
   `core/docker/31-vault-debug-throughput.sh` (fail closed, unchanged). The
   hook also still stops the boot when it cannot find exactly one
   `sendfile on;` line or finds an `aio` line already, now for the default
   too.
4. `VAULT_NGINX_WORKERS` keeps its default (unset = `worker_processes 1`).
5. The hook logs the AIO choice on every start
   (`VAULT_NGINX_AIO=unset (default): ...` or `VAULT_NGINX_AIO=off: ...`).

Tests: `core/tests/test-debug-throughput-hook.sh` (default, `threads` and
blank render identically; the default is the opt-out render plus the two AIO
lines; `off` equals the template with only the marker lines deleted and
main's own `off` render), `.github/scripts/verify-core-nginx.sh` (every
scenario checks the default through the real entrypoint, plus an `off`
scenario), `deploy/tests/verify-stack.sh` 5a2 (the running stack with the
shipped `.env`) and 8g (default, `threads`, `off`, refusal through the built
image).

## Consequences

- Every cache read takes one extra copy (kernel to nginx buffer to socket)
  instead of `sendfile()`. The host CPU did not rise measurably on the warm
  run, but the nginx process CPU alone was not measured; a host with a slow
  CPU and a fast pool may see a cost. `VAULT_NGINX_AIO=off` is the way back.
- The heartbeat no longer waits behind blocking disk reads during cold
  downloads; whether the runner still needs a retry on "Unable to detect
  Lancache server!" is decided separately (roadmap, after AIO-DEFAULT-1).
- One measured game, one client, one host (4-HDD ZFS pool). Two baselines
  differed by 42 % (BASELINE-SPREAD-1); the verdict does not depend on it.
- Upgrades: an existing `.env` with `VAULT_NGINX_AIO=threads` renders the
  same config as before. A blank value now means `threads`, not off; an
  operator who wants the old behaviour sets `off`.

## Follow-up

- After the rc21 rollout, remove the `VAULT_NGINX_AIO=threads` line from the
  production `.env` (it then equals the default). This is an operator step on
  the production host, not a repo change.

## Rollback

Set `VAULT_NGINX_AIO=off` and recreate vault-core. No data or schema is
touched. Reverting the code default needs a new ADR.
