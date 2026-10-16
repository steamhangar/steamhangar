# rc19.3 handover (2026-10-16)

Entry point for parallel sessions. Canonical plan: `docs/PROJECT_PLAN.md`;
rules: `CLAUDE.md` (incl. the roadmap rule). Language with the user: German.

## State

- `v0.1.0-rc19` (b29379b) and `v0.1.0-rc19.2` (1a22561) are tagged and never
  move. rc19.2 is rolled out on the test and prod stacks per the user.
- rc19.3 content:
  - TUNE-1a: heartbeat in the opt-in debug log, `VAULT_NGINX_AIO=threads`
    (= `aio threads` + `sendfile off`), `VAULT_NGINX_WORKERS`, report
    Heartbeat section. All off by default, byte-identical render when unset.
  - TUNE-1b: ADR-0027, LEARNINGS, plan block, index `jobs (status,
    finished_at)`.
  - FIX-SHOT-1: the stats graph caption used the real clock, so two
    Roborazzi goldens (`downloads_stats_*_night`) went stale every hour.
- rc19.3 is on `main` (d409f30, CI green) and tagged `v0.1.0-rc19.3`.
  Rollout Antrag: A-20261016-03 (switches stay off until the Sunday test).
- Open measurements: Sunday cold-game test per ADR-0027; LIVE-DELAY-1
  (Downloads page shows client downloads >1 min late, cause unknown);
  root cause of the HELLDIVERS 2 "Unable to detect Lancache server!" failure
  is unproven (hypothesis: heartbeat answered late while the single worker is
  busy with cold HDD reads).

## Session B: complete the roadmap

Goal: make `docs/handover/post-0.1.0-roadmap.md` (D1-D23) and the version
blocks in `docs/PROJECT_PLAN.md` complete and consistent.
- Merge the "rc20 candidates" block of the plan into the roadmap: every
  candidate gets an ID, one-paragraph scope, dependencies, size estimate
  (packages of at most 1-2 h), and a state (open / designed / blocked).
- Cross-check against `docs/adr/` (latest 0027) and `docs/LEARNINGS.md`:
  items already decided must cite their ADR, superseded ones are marked.
- Do not change code. Docs-only branch `wp/docs-roadmap-*`, ask the user
  before anything that changes scope or order.

## Session C: plan rc20

Goal: a work-package list for rc20 that the user can approve (show it BEFORE
delegating, per `CLAUDE.md`).
- Input: the "rc20 candidates" block, the roadmap, the open items above.
- Decisions that need the user (offer as Weg A / Weg B with a
  recommendation): scope and order; whether the optional "Client downloading"
  row is in; whether a runner retry on "Unable to detect" is worth it
  before the Sunday data exists; Clients / Add-a-device redesign depends on
  the mockups from the Clients-page mockup session.
- The Sunday test result decides whether AIO/WORKERS become defaults; do not
  plan that before the measurement.

## Rules that bite

Docker only via the devbox `dev.sh`; no ZEUS-specific paths, IPs or hashes in
the repo; one commit per passed work package; coder then reviewer; pushes
only on `wp/*`, `main` and `v*` tags after green tests and CI.
