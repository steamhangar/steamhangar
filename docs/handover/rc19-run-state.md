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
