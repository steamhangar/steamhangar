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
