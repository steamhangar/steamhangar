# rc19 run plan (Saturday 2026-10-17)

Decided with Jan on 2026-10-15. Source of every item: `post-0.1.0-roadmap.md`
("rc19 Downloads block" and the rc17/rc18 follow-ups). The run is unattended;
stop only for a plan deviation not covered here.

## Decisions (no further asking)

- **Release chain allowed:** after green branch CI, green `dev.sh verify` and
  green main CI the run may tag `v0.1.0-rc19`, push the tag and write the
  deploy Antrag (every new .env/compose value listed). Jan rolls it out.
- **ID-AUDIT names:** only with the relay key set (ADR-0011 unchanged). No
  anonymous Valve lookup without the key. Without a key the UI keeps a
  neutral fallback and never shows a bare id where a name could exist.
- **Android:** D16 Android UI AND the Library sort go into the app too
  (Android jobs run only in GitHub CI; do not rebuild them locally).
- **Diagnosis session:** only the read-only parts run here (`dev.sh diag`:
  iperf3 LAN baseline, nginx connections/caps, logs, pool read figures).
  Result as an inbox note. The hit-path test (needs a game load on Demon) and
  every `dev.sh prod rate` step wait for Jan. Never `diag down` (other sessions measure in parallel).
- **Not in this run:** Clients / Add-a-device mockups and redesign, the full
  UI pass after 0.1.0, CACHE-REPAIR-1.
- **GRAPH-ZOOM = Weg B:** besides the web changes (1 h / 6 h ranges, trimmed
  X axis, filled area) the API gets a 1-minute traffic tier (schema bump,
  prune for the new tier). The Antrag must say that rollback to rc18.1 needs
  the SQLite dump from update.sh (newer schema is refused by older code).
  Test the migration on a copy of a populated dev DB.
- **GRAPH-LIVE (Jan, 2026-10-15, "Steam feeling"):** the graph must feel
  live: 5-second points for the first minute, then gradually coarser.
  Finding: the source is vault-core's access_log, written when a request
  ends (chunks are small, so 5 s buckets are accurate) and read by
  `event_sweep` only on the scheduler cadence, which is the real limit
  today. Design to implement (the run may refine, not drop the goal):
  (a) a LIVE tier held in memory only (ring buffer, lost on restart, never
  persisted): 5 s buckets for the last minute, 15 s up to 5 min, then the
  persisted 1-minute tier (Weg B) takes over; one stitched series from a
  new endpoint, e.g. `GET /v1/throughput/live`; (b) the log is tailed every
  ~2 s while at least one client polled the live endpoint in the last
  ~30 s ("watcher active"), otherwise the normal cadence, so idle load
  stays as today; (c) the web graph polls every 2-5 s only while the
  Downloads page is visible (Page Visibility API) and the default range is
  "Live" feeling inside the chosen range (1 h / 6 h / 24 h keep persisted
  tiers); (d) "Current speed" tile reads the same live tier. Measure the
  tail cost (CPU, SQLite writes) on a dev run with the runner active and
  state it in the Antrag. Android gets the same series after the web part
  passes.
- **Downloads range default:** the range the user chose last, remembered per
  browser (fallback 6 h when nothing is stored).
- **MAP-DRIFT-1 cleanup:** once at startup like the UPD-FIX-1 manifest
  repair: only orphan rows without a mapping and without bytes on disk,
  counters in the log, a non-lossy filter with a partial-update regression
  test.
- LIB-SORT: "last played" only while `relay_expose_last_played` is on;
  "newly bought" is dropped.
- **D16 neutral naming (D17 prep):** the upstream rate cap, its window and
  the connection caps are labelled as the vault's upstream (WAN) budget in
  the web UI and the Android UI, never as "Steam"; help texts likewise.
  Stored settings keys keep their names (they are persisted since rc18.1);
  only if a key itself is Steam-named, report it in the handover instead of
  renaming it. Steam-only settings stay in the Steam section.
- NOT-OWNED-1: Weg B (`not_owned` outcome) plus 250820 as seed.

## Card block (prepared 2026-10-15, scope to confirm by Jan before Saturday)

Source: `post-0.1.0-roadmap.md` D19 (with 1c), D20, D21, D22. Defaults the
run takes unless Jan says otherwise:

- **Recent reviews = option A:** only the overall rating (Steam's summary does
  not expose a recent figure, measured 2026-10-15). No review sampling.
- **D19-1c switch:** own env `VAULT_STORE_DETAILS`, default off, until D22
  folds it into the modes. New host `store.steampowered.com` goes into the
  proxy allow list and the deploy Antrag. Plain-text rendering only.
- **Order inside the block:** D21 (read-only review, produces
  `docs/security/privacy-review.md` and the failure-mode analysis per store)
  -> D19-1a/1b (expected depots/size, oracle only) -> D19-1c (enrichment) in
  parallel with D20-1a/1b (queue order, independent). D22 is NOT built in
  rc19: only its ADR draft comes out of D21; the modes follow after rc19.
- Android for D19 and D20 follows in the app track after the web parts pass.
- Place in the waves: D21 at the start of wave 2, D19 and D20 in wave 3
  (after the Wave 1 api schema work for GRAPH-ZOOM has landed, so the two
  migrations do not collide: GRAPH-ZOOM first, then D20's `jobs` column).
  Two schema bumps in one release: the Antrag states both and the rollback
  note (SQLite dump) once.

## Jan's answers on the card block (2026-10-15, final)

- **D23 LIBRARY-META-1 is IN rc19, complete (1a-1c):** table, migration,
  nightly oracle job, filter/sort parameters, web chips and sort. Third
  schema bump in the release: the Antrag lists all three (GRAPH-ZOOM, D20,
  D23) and the rollback note once. Order of migrations: GRAPH-ZOOM, D20, D23.
  D23-1d (optional store night fill) stays out unless D19-1c has passed.
- **Shipped defaults all off:** `VAULT_STORE_DETAILS` default off, no new
  external traffic by default. The deploy Antrag proposes, for Jan's own
  prod instance, the values that enable store details, last played and
  names (his decision to apply; D21 findings are attached to the Antrag).
- **Recent reviews = option A** (overall rating only), confirmed.
- **All or nothing:** no tag `v0.1.0-rc19` before the card block, D23 and
  the Android parts have passed. If the limit runs out, stop cleanly after
  the last passed package, write the handover with the open list, and do
  NOT tag.

## Autonomy, questions and restarts (Jan, 2026-10-15)

- **Unattended first:** decide with the defaults in this plan; ask only when
  blocked by something not covered here.
- **Questions only as AskUserQuestion with options** (Jan gets a phone push).
  Always a recommended option first. If no answer comes, continue with other
  packages that do not depend on it; do not idle.
- **The session limit cannot be read.** When it is hit the session just
  stops. Therefore: keep `docs/handover/rc19-run-state.md` (gitignored not
  needed, commit it on the wp branch merge) updated after EVERY passed
  package: done list, running package, next package, open questions,
  branch names. At run start and after every wave, schedule a fallback
  restart with `send_later` (delay about 5 h, message: "continue the rc19
  run from rc19-run-state.md"). Before scheduling a new one, delete the
  previous trigger (`delete_trigger`) so only one is pending. When the run
  finishes (tag + Antrag + handover done), delete the pending trigger.
- **On a restart message:** read the state file, check `git status` and
  running worktrees first, resume the open package (re-run its reviewer if
  unsure), never redo passed packages.

## Waves (max 3 agents at once, resume instead of respawn)

1. **Fixes and trust:** NOT-OWNED-1, METRIC-AUDIT, TILE-INTERNET, GRAPH-ZOOM.
2. **Data and handling:** ID-AUDIT, LIB-SORT (web + app), MAP-DRIFT-1, D23 (api first, web after).
3. **Android and rest:** D16 Android UI, rc18 follow-ups (watcher retry test,
   log once per failure streak, VS_WATCH_FUNCTIONS_ONLY line, conftest
   `http_fetch` guard, webhook boot witness test, demo `core_apply`).
4. **Close:** diag read-only measurements + inbox note, docs, verify, tag,
   Antrag, handover.

## Rules that apply

One commit per passed package, Conventional Commits, English in the repo.
coder then reviewer, findings that need no decision are fixed without asking
([[autonomy-on-review-findings]]). Light review for docs/CSS, full review for
api/core changes. Push only `wp/*`, `main`, `v*` tags; merge to main after
green branch CI. No ZEUS specifics in the repo. Dev instance stays on
`VAULT_SCHEDULE_WINDOW=off`; no downloads from the internet in dev.
