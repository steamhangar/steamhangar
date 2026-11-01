# Learnings: Deploy, containers and measurements

Scope: compose, images and entrypoints, verify-stack, production line (DS-Lite), ZFS, throughput and cold-game measurements. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## Production rollout (operator line, DS-Lite)
- A keepalive pool caps IDLE upstream connections, not in-flight ones. A
  client we cannot throttle (the Steam client) still opens a fresh
  connection per request above the idle count, and behind CGNAT every one
  is a port mapping: 1838x `113` on the first client MISS test even on a
  pooled edge (2026-10-04). Test behind the real line with a real client,
  not only with our own capped prefill (D7).
- A low upstream rate and the global cap multiply: with the 4m rate and cap
  16 on `:80` combined, vault-core rejected about 14k-21k requests per
  minute with 503 (likely because throttled transfers hold the slots
  longer), and the clients retried at once (measured 2026-10-10; the rate
  was raised to 8m). Lower the rate only together with a look at the 503
  count. Whether the loop still occurs at the current rate is open (roadmap
  CAP-503-LOOP-1).

## Containers
- A non-root container user needs a real, writable HOME — tools that
  write caches (SteamPrefill: $HOME/.cache) crash on /nonexistent before
  printing anything, so error-pattern matching never fires. Set both the
  passwd home-dir AND ENV HOME; compose run vs docker exec derive HOME
  differently (WP 1.9 Fable blocker).
- "Verified by inspection" is not verified: every binary that ships in an
  image needs at least a credential-free smoke RUN in the verify suite
  (WP 1.9).
- A `.env` beside `compose.yaml` only feeds `${...}` interpolation inside
  that file — it is NOT a pass-through into container environments. Any
  setting config.py reads is dead unless the service's `environment:`
  block forwards it by name; WP 5.4 found four such dead vars
  (VAULT_SCHEDULE_WINDOW, VAULT_SCHEDULE_INTERVAL_MINUTES,
  VAULT_SCHEDULE_CLIENT_STALE_DAYS, VAULT_GC_GRACE_DAYS) and documented a
  compose.override.yaml recipe instead of a silently-broken .env example.
- Third occurrence of the above, so treat it as a rule, not a war story:
  **a doc sentence telling an operator to set a variable IS a claim that
  the shipped stack forwards it, and must be verified in the same diff.**
  The P1 packaging review caught a privacy mitigation ("point
  VAULT_MANIFEST_ORACLE_URL at your own mirror instead") whose key
  compose never forwarded and which is not DB-overridable either — an
  operator following our own advice still shipped their cached app IDs to
  api.steamcmd.net, with no error. The cheap systematic guard: when a
  package touches env plumbing, produce the FULL table of variables
  config.py reads against what compose forwards, and decide each one
  explicitly (forward it, or say in the docs that it needs a
  compose.override.yaml). Key-presence preconditions in the verify suite
  are what actually catch this — a value-only assertion passes happily on
  the empty string an unforwarded key renders as. (Scope note, WP 4f: that
  is true of a SHELL assertion, where `printenv` prints nothing for absent
  and for empty alike. In Python it is not: `os.environ.get(key, default)`
  supplies `default` only for an ABSENT key, so an unforwarded key gets the
  default fine and only a forwarded-but-empty one arrives as `""`. Do not
  carry the shell framing into config code — see the entry below.)
- `os.environ.get(key, default)` supplies `default` only when the key is
  **absent**; a key that is present-but-blank sails through as `""`. The
  realistic source is a *forwarded* key with empty interpolation
  (`KEY: ${KEY}` with nothing in `.env`) or a bare `KEY:` / `ENV KEY=` in a
  derived image — **not** an unforwarded key, which is simply absent. Any
  path-ish setting with a usable default therefore needs an explicit
  `not value.strip()` refusal at boot, or a blank value defers the failure
  into a background thread that catches everything and retries forever
  (WP 4f: a blank `VAULT_CACHE_ROOT` plus the cached-sweep mode silently
  killed the installed half of every sweep, once per interval, forever).
- Two call sites computing the same domain predicate WILL diverge, and
  behavioural fixtures will not notice. WP 4c-api's route and WP 4d's sweep
  disagreed on one real post-delete state (`[440, 730]` vs `[730]`), which
  let a button re-queue a game the sweep correctly treated as deleted. The
  durable fix is a **structural** pin: monkeypatch the shared function to a
  sentinel-returning fake and assert every caller returns *exactly* its
  result — not a superset or subset, which also catches a caller layering an
  extra filter on top. Measured after unification: reverting either caller
  to its own predicate leaves 1540 behavioural tests green and kills only
  the structural test (WP 4f).

## 2026-08-22 — the defaults-flip wave (SWEEP-1, APP-DEMO, 4d-web, AG-0, CI-3/AGENT-BIN)
- compose `${VAR:-default}` substitutes on unset AND on explicitly-blank,
  so "leave it empty to disable" documentation around a `:-` forwarding
  is false — there is NO .env expression of disabled, and under
  VAULT_SETTINGS_READONLY=1 no PATCH escape either (WP SWEEP-1 R2-B1,
  measured). The no-colon form `${VAR-default}` distinguishes them:
  unset -> default, blank -> "". Choose per variable and record why
  (TZ deliberately stays `:-` because blank==UTC there, measured). Pin
  the substitution FORM structurally and the blank case live.

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- Two services mounting the same fresh named volume, both from images
  that populate that path, race on Docker's copy-up ("mkdir … file
  exists"). A green first run proves nothing; the race needs a fresh
  volume to show. Fix: `:nocopy` on every consumer but the one seeder
  (WP TH-1-FIX). Named-volume mode only: a bind mount must carry no
  volume options, and Docker 28 refuses `:nocopy` on a bind outright. A
  green verify on named volumes said nothing about bind mode; the first
  real rollout with VAULT_CACHE_PATH broke on it. Every mode a doc
  promises needs its own live run (WP DEPLOY-FIX-3).
- On a DS-Lite line (IPv4 behind the provider's CGNAT) a burst of new
  upstream connections ends in instant `connect() failed (113: Host is
  unreachable)`: the CGN answers ICMP host-unreachable once the
  subscriber's port quota is spent (RFC 6888 REQ-11). One connection
  per chunk (variable `proxy_pass`, no keepalive) plus 30 instantly
  retrying SteamPrefill workers spun at ~500 req/s. Cap the workers and
  stop nginx retrying `error`; the root fix is connection reuse (WP
  CORE-FIX-2).
- Compose `${VAR:+...}` with an empty host port publishes on a random
  host port; `set -e` ignores failures inside `&&` lists and after `!`,
  so build-time checks must be separate commands (WP CORE-FIX-3).

## 2026-10-02/03 — upstream keepalive pool (ADR-0017, WP CORE-FEAT-1a..1d)
- Never edit a shell script while a run is reading it: `sh` reads the
  script as it executes and aborts with a bogus syntax error at an
  unchanged line. A local docker without buildx uses the legacy builder,
  whose output format differs from BuildKit's; parse both (WP VER-1).
- A root entrypoint hook must not create, write or chmod a name inside a
  directory another uid owns: that uid can swap the name for a symlink
  between root's check and use, and mktemp's O_EXCL only guards the
  create. Do the file work as the owning user (busybox `su` keeps the
  env), keep root to plain `mkdir` + `chown -h`, and prove it with a
  real-uid race rig. tinyproxy filters before its StatHost check, so a
  status page cannot be added without widening the egress allowlist
  (WP VER-2).

## 2026-10-09/10 — rc13 night run (CORE-FIX-4d, WEB-FEAT-6, DEPS-1, HYG-1, WEB-FIX-10, AGENT-FIX-3)
- verify-stack step 10h (global cap, live) still flakes under load
  despite 93542a1 (1 of 2 slow requests held after 15 s); a rerun was
  green. Open follow-up.

## 2026-10-13/14 — rc15/rc16 (D9, D10, UPD-5, UPD-6, D15a-c)
- Behind Docker published ports (userland proxy / NAT) vault-api sees the
  bridge gateway as the peer, never the client's LAN address. Anything that
  attributes traffic to a client (D15, "client bypasses the cache") must
  take the address from a source that sees the real peer (proxy access
  log, or the agent reporting its own LAN address — AGENT-ADDR-1).

## 2026-10-14/15 — rc18 night run (UPD-FIX-1, D16a-c, DIST-1a/b)
- "Blank vs unset" cannot be told apart once compose forwards `${VAR-}`.
  When a blank value means something (scheduler off, legacy edge), give the
  grammar an explicit keyword (`off`) and teach EVERY parser of that key
  the keyword in the same package — api twins AND the core shell hooks.
  A keyword the docs recommend but core refuses crashes vault-core
  (D16b-1 review B1).

## 2026-10-16 - rc19.3 throughput tuning (TUNE-1a/1b)
- Cold and warm reads differ by a large factor (ZFS ARC): the same game on
  the same PC measured p50 3.3 MB/s cold and 12.6 MB/s warm. A single run
  proves nothing; always label a measurement cold or warm and compare like
  with like.
- busybox `iostat` has no `-x`, and Glances rates are averaged since the last
  query: neither shows peaks. Use short repeated samples for peak load.

## 2026-10-18 - cold-game test (ADR-0027 measurement)
- `echo 3 > /proc/sys/vm/drop_caches` on the host empties the ZFS ARC
  (OpenZFS 2.3, `zfs_arc_shrinker_limit=0`): 20-30 GB down to 1-2 GB. That
  makes repeatable cold runs of the same game possible. Prove "cold" with
  disk reads during the run, not with the ARC size alone.
- One baseline is not enough: two identical cold baselines differed by 42 %,
  while two AIO runs differed by 1.4 %. Plan at least two baselines (A-B-C-A).
- Copy the throughput debug log only after the download has finished and
  into a new folder per run. An early copy holds only the `# start` line, and
  `cp -r` into an existing folder nests it. Rotation keeps at most 64 MiB, so
  copy after every run.
- Exact download duration = first to last `tp1` line of the client in the
  run's `# start` segment (msec field). A recreate per run gives each run its
  own segment.
- busybox `iostat` (diag container): the whole-disk counters wrap around;
  only the partition lines are usable.

## 2026-11-01 — rc21.1 (prod findings after the rc21 rollout)
- **`zfs list` "G" is GiB**: server log lines compared with zfs use 2^30.
