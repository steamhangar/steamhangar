# Cold-game test plan (ADR-0027)

Planned 2026-10-18. Decides the `VAULT_NGINX_AIO` / `VAULT_NGINX_WORKERS`
defaults (rc20.x patch or rc21) and VERIFY-COLD-1. Protocol source:
ADR-0027 "How to test". Operator decisions of 2026-10-18 are marked (D).

## What "cold" means here

Cold = the game is fully in the lancache cache on disk, but NOT in the ZFS
ARC: every request is a HIT served from the HDD pool. It is not a cache miss.
A miss would measure the upstream rate cap (`VAULT_UPSTREAM_RATE`), not
the disk path.

The game (Satisfactory, about 24.8 GB) fits into the ARC of the production
host completely. After one download it is warm, so every run needs its own
eviction.

## Decisions (operator, 2026-10-18)

- (D) **Eviction:** the operator drops the host page cache and ARC as root
  before each run (`echo 3 > /proc/sys/vm/drop_caches`). ARC size from
  `arcstats` is read before and after. Fallback if the ARC does not shrink:
  rotate to a different, long-unread game of similar size, and say so in the
  result.
- (D) **Switches on production:** one test Antrag describes the whole step
  sequence. The operator sets each step, and the devbox measures in between.
  At the end the switches return to unset. No dev.sh helper for this test.
- (D) **Run scheme A-B-C-A**, four cold runs:
  1. baseline (switches unset)
  2. `VAULT_NGINX_AIO=threads`
  3. `VAULT_NGINX_WORKERS=2` (AIO unset again)
  4. baseline again (shows drift from load or time of day)

## Preconditions

- `VAULT_DEBUG_THROUGHPUT` on (on in production since rc19.2; checked
  2026-10-18 in the vault-core start log).
- The game is fully cached (Library shows it as cached and current). No
  prefill job is running or queued, and no other client downloads during
  the test.
- Same client PC, wired, same Steam settings, nothing else downloading.
- No rollout during the test window. A rollout recreates vault-core and
  mixes runs.
- Optional before run 1: LAN baseline with iperf3 (server on the client
  PC). It separates the network limit from the disk limit and gives HIT-PATH-1
  its missing LAN figure.

## Per run

1. Set the switch for this run (or unset it), then recreate vault-core.
   The recreate writes a new `# start` line to the debug log and restarts
   the `$connection` numbers. One run equals one start segment, so runs never
   mix.
2. Evict, then read the ARC size. It must have shrunk clearly.
3. On the client, uninstall the game and install it again.
4. While it downloads (read-only):
   - disk reads on the pool, short repeated samples (busybox `iostat`: only
     the partition lines are usable, the whole-disk counters wrap around).
     Reads > 0 during the run prove "cold".
   - nginx CPU and the number of client connections.
   - heartbeat latency every 5 s from a second container:
     `curl -w %{time_total} http://<vault-core>/lancache-heartbeat`
     (idle 2026-10-18: 200, 1.1 ms).
5. Afterwards, feed this run's start segment of the debug log to
   `core/tools/throughput-report.py --link-mbit 1000`. Record the following:
   - per-request p50 and p95
   - aggregate Mbit/s
   - heartbeat max
   - HIT share (must be about 100 %)
   - download duration

## Decision rule (fixed before the test)

A switch becomes a default candidate (new ADR with the numbers, rc20.x)
only if all of these hold:

- per-request p50 at least +30 % against BOTH baselines,
- heartbeat maximum below 100 ms (2026-10-16 cold baseline: 1.0-1.8 s),
- HIT share about 100 % and no failed run.

If both switches qualify, a fifth run with both switches set may follow
(operator's yes). Otherwise nothing changes, and the item moves to rc21.
If the two baselines differ by more than 30 %, the test is inconclusive and
gets repeated. Warm behaviour of a winning switch (`sendfile off` costs CPU
on warm hits) is checked separately before any default change.

## Result

- An inbox note with the table of the four runs.
- ADR-0027 gets the numbers, or a new ADR is written if a default changes.
- The rc19.3 box in `docs/PROJECT_PLAN.md` gets ticked.
- VERIFY-COLD-1 is decided: verify window on with the chosen worker
  setting, or it stays off.
