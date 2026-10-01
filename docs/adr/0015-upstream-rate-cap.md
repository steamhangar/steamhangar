# ADR-0015: Upstream rate cap: one aggregate limit divided by live request count

Date: 2026-10-01
Status: Accepted (user decision 2026-09-30; implemented in WP TH-1a, core,
and WP TH-1b, deploy and docs)

## Context

Operator request 2026-08-30 (`docs/PROJECT_PLAN.md` §11 item 10): cap the
upstream (Steam CDN -> vault) bandwidth, time-dependent, so a daytime
manual prefill does not saturate the WAN while people use it. LAN serving
(cache HITs) stays uncapped. The shipped schedule window (ADR-0014,
`03:00-07:00`) only confines *when* the bulk downloads run, not *how fast*.

Evidence, in order:

- **WP TH-0** (`poc/throttle/RESULTS-THROTTLE-20260929.md`): SteamPrefill
  3.7.1 has no rate option, only a hidden `--max-threads` concurrency
  override (default 30 in flight). nginx-native `proxy_limit_rate $var`
  fed by a `map $time_iso8601` caps the `proxy_store` miss path exactly,
  flips per request with the live clock, stores the full object and leaves
  HITs at LAN speed. GO, no new module. Its main limit: the cap is PER
  REQUEST (4 requests moved 3.88x the cap).
- **User decision 2026-09-30:** the operator sets ONE aggregate limit; at
  download time it is divided by the number of parallel downloads actually
  running, not by a fixed 30.
- **WP TH-0b** (`poc/throttle/RESULTS-THROTTLE-DYNAMIC-20260930.md`)
  measured two shapes of that division against 819,200 B/s (`800k`):
  - **Option A:** a `map $connections_active` bucket map generated at container
    start. 0.98-0.99x of the target at 8 and 16 parallel, no transient for
    a burst inside ~20 ms, no module, no state, native-rig compatible.
    Weakness: idle keep-alive connections count, so 8 idle ones drop the
    aggregate to 0.49x (errs toward under-using the WAN).
  - **Option B:** njs with a shared-dict in-flight counter. Also ~0.98x
    steady state, but overshoots in the first chunk round of a burst
    (3.25x at 16 parallel), leaks its counter on a client abort (permanent
    until restart) and needs a module the native Windows rig lacks.
  - Option A was chosen; `$connections_writing` was named as the first
    refinement to measure.
- **WP TH-1a** (`poc/throttle/RESULTS-TH1-20261001.md`): the divisor
  switched to `$connections_writing`. 8 parallel MISSes: 0.980x with
  `_writing`, 0.967x with `_active`; with 8 idle keep-alives also open:
  `_writing` 0.981x, `_active` 0.496x. Neither exceeded the target.

## Decision

- **One variable, `VAULT_UPSTREAM_RATE`** (bytes/s, nginx size syntax,
  `k` = 1024, `m` = 1048576). Empty = no cap, the default.
- **Division by `$connections_writing`.**
  `core/docker/27-vault-upstream-rate.sh` precomputes, at container start,
  a map from the live count to a per-request share: buckets 1..1024
  (bucket N = total/N), 1024 being `worker_processes 1` x
  `worker_connections 1024`, so no possible count reaches the `default`.
  `check-config-drift.sh` pins that bound. `proxy_limit_rate` reads the
  share once per request.
- **Optional window, `VAULT_UPSTREAM_RATE_WINDOW`** (the exact
  `VAULT_SCHEDULE_WINDOW` grammar): full speed inside, capped outside, in
  vault-core's local time. `deploy/compose.yaml` forwards it with
  `${VAULT_UPSTREAM_RATE_WINDOW-${VAULT_SCHEDULE_WINDOW-03:00-07:00}}`:
  unset follows the scheduler's env window (same default), explicitly blank
  caps around the clock. `TZ` is now forwarded to vault-core (`${TZ:-UTC}`,
  same as vault-api) so the window means the operator's clock.
- **Env-only.** Neither value is a vault-api setting (ADR-0009 does not
  apply); a change needs a vault-core recreate. A window stored via
  `PATCH /v1/settings` is not followed.
- **Fail-closed.** nginx reads an empty or unparseable `proxy_limit_rate`
  value as unlimited, so an invalid rate or window stops vault-core's boot,
  the script re-reads its own output, and `40-vault-preflight.sh`
  independently refuses to boot when the include is missing or uncapped
  while a rate is set.

## Consequences, the honest limits

- The cap is per request, divided by the live count. HITs, `/health` and
  other in-flight requests count in N but use no WAN bandwidth, so the WAN
  is under-used, never over the cap from that. A request's share is fixed
  when its upstream headers arrive; it does not speed up when others end.
- Slow-ramp overshoot is bounded (at most one chunk per request already in
  flight) and unmeasured; only a burst inside ~20 ms was measured.
- The LAN client that triggers a MISS is slowed too; there is no
  nginx-native way to slow the upstream read alone.
- The window edge stops nothing: a job running past it continues, capped,
  chunk by chunk. Stopping is the scheduler's call.
- Not measured: a real SteamPrefill run, HIT/MISS mixes, 30 parallel,
  the window live against a non-UTC `TZ`, `X-Accel-Buffering` from upstream.
- This is a bandwidth cap, not a request limit and not a DoS control.
- Per-device QoS on the router remains the zero-code alternative.

Full mechanism and limits: `core/README.md` "Upstream rate cap"; operator
view: `deploy/README.md` "Upstream rate cap".
