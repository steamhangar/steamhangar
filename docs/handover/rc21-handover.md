# rc21 handover (2026-10-18)

Written at the end of the rc20 session for the session that builds rc21.
Read this, then the rc21 block in `docs/PROJECT_PLAN.md`, then
`docs/LEARNINGS.md` (section "2026-10-17 — rc20 rework").

## Where things stand

- Production runs **v0.1.0-rc20.2** (3f78b1b), rolled out 2026-10-18.
  Schema 33. `VAULT_DEBUG_THROUGHPUT=1`; `VAULT_NGINX_AIO` /
  `VAULT_NGINX_WORKERS` blank again after the cold-game test.
- Cold-game test done (ADR-0027 "Measurement 2026-10-18"): `AIO=threads`
  about 5-7x faster on cold reads, link-limited; `WORKERS=2` no gain.
  **AIO-DEFAULT-1 decided: Weg B** — production `.env` gets
  `VAULT_NGINX_AIO=threads` via Antrag A-20261018-02; rc21 ships the code
  default (new ADR, tests, verify's flag-off check reworked) and the `.env`
  line is removed after the rc21 rollout. VERIFY-COLD-1 follows once AIO is
  live.
- Tags: rc20 (648bc6d), rc20.1 (fff08b9), rc20.2 (3f78b1b). main CI green.
- The web UI is built on the UI kit (ADR-0028): `web/css/kit.css`, scale
  tokens in `theme.css`, the hidden `/kit` page, every kit rule scoped under
  `.kit`. Design spec: `docs/design/rc20/kit.html` (must match kit.css),
  page mockups next to it. Design tokens and the status vocabulary are a
  contract checked by web and Android tests (`docs/design/tokens.json`,
  `docs/design/status-vocabulary.json`).
- Web screenshot goldens run in CI (`web / goldens`, strict). Re-record by
  pushing the branch as `wp/<name>-goldens`; the bot commits the PNGs; merge
  that branch. Locally: `dev.sh test-web-shots [out]` (tolerant mode, all 15
  pass on main as of 2026-10-18).

## How to work (proven in rc20)

- Coder then reviewer per package; max 3 agents; resume agents with
  SendMessage instead of respawning; branch names `wp/rc21-<id>`. No
  force-push: a rebased branch gets a new name (`-b`).
- Every visual package proves itself with rendered screenshots at 1400 /
  1000 / 400 (tools/web-shot.mjs with tools/web-shot-fonts.conf); reviewers
  look at them. DOM tests alone shipped the wrong look once.
- Any web change that moves or rewords strings: grep `app/app/src/test` for
  twin pins on the touched files first (DOWNLOADS-2 turned main red).
- kit.css is appended to by many packages: merge in a fixed order, rebase
  the next branch after each merge, keep all blocks.
- Poll GitHub's API at most every 2-3 minutes (60 requests/hour per ZEUS IP,
  unauthenticated).
- The orchestrator writes no production code; docs, plan ticks, merges,
  tags (with the operator's yes) and Antraege are the orchestrator's.

## rc21: what is next (plan block "rc21 — Android parity and cache health")

1. **MOBILE-1** first: build a phone mockup from the kit (touch targets via
   `@media (pointer:coarse)`, sheets as bottom sheets, QR pairing flow,
   safe-area insets, visible overflow on scrolling segs) and get the
   operator's approval before coding.
2. Web/API packages: CACHE-REPAIR-UI-1, CLIENTS-LIVE-1, RUNNER-SPLIT-1,
   CSS-HIDDEN-LINT-1 (rest), the rc20 review nits.
3. From the operator's cold-start test (roadmap, 2026-10-18):
   AIO code default (AIO-DEFAULT-1 Weg B, second half), UI-COLD-CACHED-1 (check whether rc20.2's loading states cover it; the
   API side, the depot walk in `api/vault_api/sizes.py` `SizeCache`, blocks
   every request on a cold dentry cache — serve the stale snapshot while
   recomputing), VERIFY-COLD-1, TP-REPORT-2. Read those roadmap entries; the
   cold-start session owns their measurements.
4. Android: parity with the rc20 redesign (the `/kit` page and kit.html are
   the spec), the history-count twin of DOWNLOADS-3, LIVE-DELAY-1 follow-up,
   ANDROID-GAPS-1, APP-NIGHT-GOLDEN-1. Android builds only in GitHub CI.
5. Close: release gate, tag `v0.1.0-rc21` only with the operator's yes.

## Open items outside the repo

- Antrag numbering: two files carry A-20261018-01 (cold-game test and the
  rc20.2 rollout). Before numbering a new Antrag, list the day's files in
  `/vault-inbox/antraege/` and `erledigt/`.

- Stray branch `wp/rc20-baseline-goldens` (stale, harmless) for the operator
  to delete; the devbox may not delete remote branches.
- `.env.bak_pre-rc20` on ZEUS kept as the rollback for rc20; the operator
  decides when to delete it.
