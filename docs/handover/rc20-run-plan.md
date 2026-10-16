# rc20 run plan (web redesign)

Decided with the operator on 2026-10-16. Source: the approved mockups in
`docs/design/rc20/` and the "rc20 web redesign" block in
`docs/PROJECT_PLAN.md`. Show the package list to the operator before
delegating (repo rule); after that the run follows the rules below.

## Decisions

- **Scope:** the redesign is web only (vanilla JS views, CSS, tests): no
  schema bump, no new env value. Android is not part of rc20. Added by the
  user on 2026-10-16, outside the redesign: LIVE-DELAY-1 (analysis + fix,
  before DOWNLOADS-1), BOOT-RATE-1 (core, roadmap D24) and
  LIB-META-WINDOW-1 (api, roadmap D23 note); see the PROJECT_PLAN rc20
  block.
- **Outdated agent = Weg A** (frontend version comparison). Weg B (agent
  reports its OS, API serves a minimum version) is v0.2.0 with the installer.
- **Clients:** one card per device; no separate agent list, no "Manage PCs".
- **Downloads:** per-game Update and Verify; queue sortable by drag and drop
  only; History differs idle vs active and expands per row.
- **Library:** sort is one group, the direction is an icon-only button.
- **Wide layout** for Clients, Downloads, Settings, Library (LAYOUT-1).
- **Slide/fade animation** for all sheets and drawers (SHEET-MOTION-1).
- Mockup copy is placeholder; shipped strings stay English and come from the
  existing code. See `docs/design/rc20/README.md`.

## Waves (max 3 agents at once, resume instead of respawn)

1. **Base:** LAYOUT-1, SHEET-MOTION-1 (light review, CSS-heavy).
2. **Clients:** CLIENTS-1 -> CLIENTS-2 -> CLIENTS-3, ADD-DEVICE-1 in parallel
   (touches `add-device-sheet.js` only; CLIENTS-3 depends on it for the
   no-tab open, so land ADD-DEVICE-1 first if both are ready).
3. **Downloads and Settings:** LIVE-DELAY-1 (analysis, then fix) first,
   then DOWNLOADS-1 on top of it; SETTINGS-1 independent. DOWNLOADS-1 keeps
   the existing keyboard reorder path (`web/js/lib/queue-reorder.js`,
   ArrowUp/ArrowDown) and settles the "Speed not measured yet" wording
   while Verifying (VERIFY-SPEED-TEXT-1).
   BOOT-RATE-1 (core) and LIB-META-WINDOW-1 (api) touch no web file and can
   run in any wave next to the web packages (still max 3 agents).
4. **Library:** LIBRARY-1, then LIBRARY-DETAIL-1 (both touch `library.js`;
   run sequentially).
5. **Close:** `dev.sh test-web`, `dev.sh verify`, visual check, docs.

## Acceptance (every package)

- Matches the mockup's structure and behaviour at 1400, 1000 and 400px; no
  horizontal page scroll; both reduced-motion and normal motion work.
- Tokens from `theme.css`; no new hard-coded colours or widths except via
  tokens. Existing wording, keys, accessibility names and keyboard behaviour
  kept; icon-only buttons carry `aria-label` and `title`.
- Existing tests adapted, new tests for new behaviour (version comparison,
  drag reorder, split button menu, tab switching, shared delete dialog).
- No browser exists on the devbox: coder and reviewer verify through
  `dev.sh test-web` and DOM-level tests; the operator checks the rendered
  pages on the dev instance. Say so explicitly in the status line.
- One Conventional Commit per passed package; update the plan checkboxes
  and `docs/LEARNINGS.md`.

## Rules that apply

coder then reviewer; findings that need no decision are fixed without asking.
Light review for CSS/web-only, full review where logic moves (CLIENTS-1,
DOWNLOADS-1, LIBRARY-DETAIL-1). Push only `wp/*`, `main`, `v*` tags; merge to
main after green branch CI. No ZEUS specifics in the repo. Dev instance
stays on `VAULT_SCHEDULE_WINDOW=off`; no downloads in dev. Tag
`v0.1.0-rc20` needs the operator's yes.

## Open follow-ups (not in rc20)

- ~~Keyboard alternative for the queue drag and drop.~~ Corrected
  2026-10-16: not a follow-up. The keyboard path already exists
  (`queue-reorder.js`, ArrowUp/ArrowDown) and DOWNLOADS-1 must keep it.
- v0.2.0 Weg B items (plan block "v0.2.0, Weg B for agents").
