# rc21 handover (updated 2026-10-18 evening)

For the next rc21 session. Read this, then the rc21 block in
`docs/PROJECT_PLAN.md` (every open box and nit is there), `docs/LEARNINGS.md`
(sections 2026-10-17 and 2026-10-18) and the roadmap's "Version rules"
(ADR-0029). The operator is on vacation after 2026-10-18; ZEUS may be off.

## Where things stand

- Production runs **v0.1.0-rc20.2** (3f78b1b), schema 33, with two `.env`
  overrides set by Antrag:
  - `VAULT_NGINX_AIO=threads` (A-20261018-02, live since 12:40Z): remove
    after the rc21 rollout, rc21 ships it as the code default (ADR-0030).
  - `VAULT_AUTO_GC=dry-run` (A-20261018-03, until rc21): GC hole fixed in
    GROUP-VERIFIED-1; the rc21 rollout Antrag sets it back to `execute`.
  - Until rc21 is live: no "Delete" of single HELLDIVERS 2 content
    packages (DELETE-CLAIMANT-1 is not deployed yet).
- rc21 packages on main (each coder → reviewer PASS → branch CI → merge):
  MOBILE-1 (1a mockup + 1b phone fine-tuning, goldens), AIO-DEFAULT-1b,
  UI-COLD-CACHED-1, GROUP-VERIFIED-1 (GC claimant fix, mapping keeps
  manifest-named depots, group figure, "Confirmed current" after every
  successful run), AUTO-VERIFY-1, TP-REPORT-2, CLIENTS-LIVE-1,
  DELETE-CLAIMANT-1, RUNNER-RETRY-1. No rc21 tag yet.
- Not started in code: **GRAPH-RECENT-1 + LOADING-NOW-1**. A coder ran
  from 20:23 but was stopped by the environment without writing anything;
  the worktree `/mnt/ssd_pool/app_data/devbox/work/wt-graph-recent-1`
  (branch `wp/rc21-graph-recent-1`, at d8dd3bd) is clean. Start a fresh
  coder there (rebase onto main first); the brief is the GRAPH-RECENT-1 /
  LOADING-NOW-1 boxes in the plan. Operator evidence: the Downloads page
  showed "0 MB/s Idle" + "No traffic in this range" while a verify job was
  running (2026-10-18 20:40).
- VERIFY-COLD-1: a manual verify run of 5 large games on prod was measured
  on 2026-10-18 evening (result in the roadmap entry VERIFY-COLD-1 /
  PROJECT_PLAN box). The planned full observation (Weg A, manual) is after
  the operator's vacation.

## Next (rc21 order)

1. GRAPH-RECENT-1 + LOADING-NOW-1 (fresh start), then RUNNER-SPLIT-1 (same files:
   `api/vault_api/routers/traffic.py`, `live_tier.py`, Downloads hero).
2. CACHE-REPAIR-UI-1 (web part), CSS-HIDDEN-LINT-1 plus the nit bundle of
   the rc21 block (FLAKY-DEMO-24H-1, flaky clients-merged-card wiring test,
   review nits of every package above).
3. Android: the operator's rc20 visual check PASSED (2026-10-18), so the
   Android packages of the rc21 block are unblocked (parity with the rc20
   redesign incl. the MOBILE-1b phone behaviour, DOWNLOADS-3 history twin,
   LIVE-DELAY-1 follow-up, ANDROID-GAPS-1, APP-NIGHT-GOLDEN-1 and the
   follow-ups collected today). Android builds only in GitHub CI.
4. Close: release gate, `dev.sh verify`, tag `v0.1.0-rc21` only with the
   operator's yes; rollout Antrag incl. removing `VAULT_NGINX_AIO`, setting
   `VAULT_AUTO_GC=execute`, then one verify of HELLDIVERS 2 (553850): the
   log must show `removed=[]` and 0 unmapped depots; with that, put the
   open question AUTO-VERIFY-FULL-1 to the operator.

## Decided today (details in the plan / roadmap)

- RELEASE-SPLIT-1 (rc22): one product version (ADR-0029); each component
  carries the version in which it last changed, unchanged ones are not
  rebuilt.
- AUTO-VERIFY-1 Weg A (done); AUTO-VERIFY-FULL-1 deferred to after the
  GROUP-VERIFIED-1 prod check.
- RUNNER-RETRY-1 built (done). FAULT-SIM-1 before 1.0.0.
- 0.4.0: AGENT-PLATFORMS-1 (Linux, macOS, Steam Deck, Steam Frame;
  signing costs noted), CLEANUP-SUGGEST-1, CACHE-MAX-1 (Weg B, updates
  keep running).
- rc22: VERIFY-FAST-1, CARD-CORNERS-1 (not reproduced with a real cover;
  needs a screenshot of an affected card).

## How to work (unchanged, plus today's lessons)

- Max 3 agents. Resume agents with SendMessage; if the harness says an
  agent was "stopped by the user", it was not the operator (three times
  today): check the worktree, tell him, continue with a fresh agent.
- Rebase an unpushed branch onto main before its review when main moved
  (several packages touched `cache-completeness.js`, `demo-data.js`,
  `api/README.md`).
- Bot golden commits do not trigger CI; the run on the branch head before
  the bot commit is the gate. Merge the `-goldens` branch via a named ref
  (`git fetch origin <b>:refs/remotes/origin/<b>`), `git pull` overwrites
  FETCH_HEAD.
- GitHub API polling every 2.5-3 min (scratchpad `ci.sh` pattern).
