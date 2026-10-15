# rc19 run state

Started 2026-10-15 ~21:00 Berlin (Jan moved the start up from Saturday).
Plan: `rc19-run-plan.md`. Fallback restart trigger: trig_01SKKTRgdhvuEpYJkEv9cnNP
(fires 2026-10-16T00:03Z; replace after each wave).

## Wave 1 (fixes and trust)
- [x] NOT-OWNED-1 (reviewed PASS, incl. Android parity) merged to main
- [x] TILE-INTERNET + METRIC-AUDIT (reviewed PASS, incl. Android parity) merged to main
- [ ] GRAPH-ZOOM (coder afdc6c2266fa5fc3a running, branch wp/graph-zoom based on wp/rc19-w1-goldens)
- main CI after merge: check; `dev.sh verify` still owed (run once after GRAPH-ZOOM, before tag)
- Learned: web label changes break Android *WebTwinTest (CI only) and screenshot goldens; goldens refresh = push a `wp/*-goldens` branch, then an empty commit to re-run CI.

Fallback restart trigger: trig_01LbKjgPvpJSQEcLhqp7ni7E (23:55 Berlin).

## Open questions
none

## Wave 2 status (2026-10-15 ~20:45Z)

Passed (reviewed), merged on wp/rc19-w2-goldens: GRAPH-ZOOM (wp/graph-zoom, schema 28->29, 1m tier), LIB-SORT web+app (wp/lib-sort), MAP-DRIFT-1 (wp/map-drift-1; nit: fail-safe branches unreadable-dir and DeletionGuardError untested), ID-AUDIT (wp/id-audit; nit: stale "App <id>" comments in owned-singleton.js, downloads.js, owned-library.js, demo-data.js).
Merged tree: test-api 3875 pass, test-web 1722 pass. Next: push wp/rc19-w2-goldens, bot refreshes goldens, empty commit re-runs CI, ff-merge to main.
Open for Jan (ask in one AskUserQuestion batch): anonymous cover refresher (ADR-0016 addendum) vs "names only with relay key": A keep + document, B gate on key, C defer to D22.
Not started: D23 (api then web), wave 3 (D19, D20, D16 Android UI, rc18 follow-ups, GRAPH-LIVE, D16 neutral naming), wave 4 close.
Restart triggers pending: trig_01LbKjgPvpJSQEcLhqp7ni7E (2026-10-15T21:55Z) and trig_01HTJMhrGFUuhdEc7Rv788DM (2026-10-16T00:10Z, Jan's request, weekly reset). Keep the 00:10Z one.

## Wave 2 part 2 (2026-10-15 late)

Passed and merged on wp/rc19-w2-goldens (test-api 3922, test-web 1747): D23 api (schema 30, VAULT_LIBRARY_META default off, bail-out pause fix), D23 web (client-side filter; genre label map is client-side; should-fix: genre select vs multi-genre stored filter), D20 api+web (schema 31, jobs.order_key, POST /v1/jobs/{id}/move; nit: no claim-vs-move race test, no aria-live).
Android parity still owed: D23 (chips/sort), D20 (reorder UI; strings.xml still says "not built yet"), D19, D16 UI.
Open for Jan: cover refresher anonymous vs "names only with relay key" (A keep+document, B gate on key, C defer to D22).
Next: CI green on this branch -> ff-merge to main; then wave 3 (D19-1a/1b/1c, D16 Android UI + neutral naming, rc18 follow-ups, GRAPH-LIVE, Android for D19/D20/D23), wave 4 close.

## Wave 3 part 1 (2026-10-16 ~00:00 Berlin... see git log)

main = e77e357: wave 2 fully merged (GRAPH-ZOOM, LIB-SORT, MAP-DRIFT-1, ID-AUDIT, D23 api+web, D20 api+web). CI-only api failure was SQLite 3.45 vs DROP COLUMN in test_d20 fixture (fixed). Tip for diagnosing CI test failures without log access: temp branch with `::error::` annotations from pytest FAILED lines (wp/ci-debug, delete later).
Passed: D19-1a/1b (wp/d19-1ab, schema 32, ADR-0024), merged on wp/rc19-w3-goldens (integration branch, in /mnt/ssd_pool/app_data/devbox/work/w2-integ).
Running: Android D23/D20 comparator fix on wp/android-d23-d20 (then merge into wp/rc19-w3-goldens, CI cycle, goldens).
Next: D19-1c (store details), Android D19, D16 Android UI + neutral naming, rc18 follow-ups, GRAPH-LIVE, wave 4 close.
Restart triggers pending: trig_01HTJMhrGFUuhdEc7Rv788DM (00:10Z), trig_01M7qtqVfFQZVXax5UJCfuSj (03:00Z).
