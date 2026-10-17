# rc20 rework run state

Updated after every passed package. Plan: `docs/PROJECT_PLAN.md`, rc20 block,
"Rework after the operator's visual check (2026-10-17)". Reference:
`docs/design/rc20/kit.html` (primary), page mockups in `docs/design/rc20/`.

## Done (on main)

- UI-KIT-1 (07fde55, 1e82f57; merge 82f4849)
- WEB-GOLDENS-1 (50b198b, 31d5eff); kit root fix 353b4f9; first web goldens record pending (wp/rc20-base-goldens failed on /kit before the fix)
- TOKENS-SYNC-1 (061b4ce, c1a3eaf, Android golden 58eb2ee)
- CLIENTS-4 (3751e12, 3a89818)
- ADD-DEVICE-2 (0a26cba, b6e01ea; branch wp/rc20-add-device-2b)
- DOWNLOADS-2 (f72735f, d021c2a; branch wp/rc20-downloads-2b); twin hotfix aa34299
- SETTINGS-2 (ee90a27, a05ca6a; branch wp/rc20-settings-2b)

## Running (2026-10-17, resumed 13:40 after a session-limit stop)

- LIBRARY-2: coder on `wp/rc20-library-2`.

## Next

2. LIBRARY-2
3. MOBILE-1 (proposed, operator to confirm rc20 vs rc21): phone nav, sheets
   as bottom sheets, 44px touch targets, QR-pairing flow end to end; phone
   mockup from the kit first.
4. rc20 docs, close: test-web, verify, goldens green, tag only with the
   operator's yes.

## Rules for this run

- Max 3 agents; resume a stopped agent with SendMessage, never respawn.
- Every visual package proves itself with rendered screenshots
  (1400/400) before review; the reviewer looks at them.
- Session limit window is 5 h; a fallback restart is scheduled
  (send_later) and replaced after each wave.

## Open questions for the operator

- MOBILE-1 in rc20 or first in rc21?
- Follow-ups after rc20 (agreed order): large demo fixture + empty/error
  states into kit and goldens; migration CI from every published schema
  (rc21); hit-path test (with the operator on prod); docs consolidation.

## Notes

- main CI red since the DOWNLOADS-2 merge (b7f23c1): Android TrafficWebTwinTest
  reads literals from views/downloads.js that moved to downloads-hero.js, and
  the axis label changed to "peak". Fixed by aa34299 (merged).
  Rule for the rest of the run: every web package that moves or rewords
  strings greps app/app/src/test for twin assertions on its files.

- GitHub API polling without a token is limited to 60 requests per hour;
  poll every 2-3 minutes, not every 30 s.
- `wp/rc20-baseline-goldens` was pushed by mistake from a stale main
  (9c10b33); harmless, left for the operator to delete.
