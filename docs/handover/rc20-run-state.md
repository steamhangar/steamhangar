# rc20 rework run state

Updated after every passed package. Plan: `docs/PROJECT_PLAN.md`, rc20 block,
"Rework after the operator's visual check (2026-10-17)". Reference:
`docs/design/rc20/kit.html` (primary), page mockups in `docs/design/rc20/`.

## Done (on main)

- UI-KIT-1 (07fde55, 1e82f57; merge 82f4849)

## Running (2026-10-17, resumed 13:40 after a session-limit stop)

- WEB-GOLDENS-1: coded (`wp/rc20-web-goldens-1`, 50b198b, not pushed),
  full review running.
- TOKENS-SYNC-1: coder on `wp/rc20-tokens-sync-1`.
- CLIENTS-4: coder on `wp/rc20-clients-4` (screenshots mandatory).

## Next

1. ADD-DEVICE-2 (after CLIENTS-4; the card's update button opens it)
2. DOWNLOADS-2, SETTINGS-2 (parallel)
3. LIBRARY-2
4. MOBILE-1 (proposed, operator to confirm rc20 vs rc21): phone nav, sheets
   as bottom sheets, 44px touch targets, QR-pairing flow end to end; phone
   mockup from the kit first.
5. rc20 docs, close: test-web, verify, goldens green, tag only with the
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
