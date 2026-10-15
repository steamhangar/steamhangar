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
  every `dev.sh prod rate` step wait for Jan. Always `diag down` at the end.
- **Not in this run:** Clients / Add-a-device mockups and redesign, the full
  UI pass after 0.1.0, CACHE-REPAIR-1.
- **GRAPH-ZOOM = Weg B:** besides the web changes (1 h / 6 h ranges, trimmed
  X axis, filled area) the API gets a 1-minute traffic tier (schema bump,
  prune for the new tier). The Antrag must say that rollback to rc18.1 needs
  the SQLite dump from update.sh (newer schema is refused by older code).
  Test the migration on a copy of a populated dev DB.
- **Downloads range default:** the range the user chose last, remembered per
  browser (fallback 6 h when nothing is stored).
- **MAP-DRIFT-1 cleanup:** once at startup like the UPD-FIX-1 manifest
  repair: only orphan rows without a mapping and without bytes on disk,
  counters in the log, a non-lossy filter with a partial-update regression
  test.
- LIB-SORT: "last played" only while `relay_expose_last_played` is on;
  "newly bought" is dropped.
- NOT-OWNED-1: Weg B (`not_owned` outcome) plus 250820 as seed.

## Waves (max 3 agents at once, resume instead of respawn)

1. **Fixes and trust:** NOT-OWNED-1, METRIC-AUDIT, TILE-INTERNET, GRAPH-ZOOM.
2. **Data and handling:** ID-AUDIT, LIB-SORT (web + app), MAP-DRIFT-1.
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
