# rc20 visual check (operator checklist)

The devbox has no browser, so every rc20 package was verified through DOM-level
tests only. This list is the browser check that closes rc20 (release gate in
`docs/handover/post-0.1.0-roadmap.md`). Reference: the mockups in
`docs/design/rc20/` (structure and behaviour are binding, copy is not).

## Setup

1. Dev instance up from `main` (`dev.sh up`; rebuild with `dev.sh build` first
   if the images are older than the last merge).
2. Open the web UI on the dev instance. Without an API key the first-run
   screen offers demo mode; demo mode shows every page with fixture data
   (no real vault needed). With the dev key the real, empty dev data shows.
3. Browser dev tools: responsive mode with widths **1400, 1000 and 400 px**.
   Also run each page once with "prefers-reduced-motion: reduce" (dev tools
   rendering tab) and once with normal motion.
4. Dev stays on `VAULT_SCHEDULE_WINDOW=off` with no downloads. Do not turn
   either on for this check.

## Every page, every width (1400 / 1000 / 400)

- [ ] No horizontal page scroll.
- [ ] Wide layout on Clients, Downloads, Settings, Library (LAYOUT-1).
- [ ] Nothing overlaps, clipped text, or unreachable control at 400 px.
- [ ] Tab key reaches every control in a sensible order; focus ring visible.

## Sheets, drawers and dialogs (SHEET-MOTION-1)

- [ ] Drawers slide in from their edge, bottom sheets slide up, the centre
      card and the scrim fade.
- [ ] Closing waits for the animation; clicking behind the closing scrim
      works at once.
- [ ] Escape closes the top-most overlay only; focus returns to the opener.
- [ ] Reduced motion: no animation, overlays still open and close.
- [ ] Known gap (nit): confirm dialogs animate in only.

## Clients (CLIENTS-1..4, ADD-DEVICE-1/2)

- [ ] One card per row, three zones (identity / activity / recent games);
      zones stack below about 700 px; tiles 2x2 at 400 px.
- [ ] Add a device: tab icons, numbered steps, one notice, Show + Copy,
      pairing QR blurred until Show, footer with Close.

- [ ] One card per device; a PC that is client and agent shows once.
- [ ] No "PCs running vault-agent" list, no "Manage PCs".
- [ ] Card grid fills the width (about 440 px minimum per card), single
      column at 400 px.
- [ ] Summary tiles show real values (Connected, Loaded, Agents, Outdated
      agents when agents exist); none shows NaN or an empty value.
- [ ] Outdated agent: warning on the card only when versions differ; the
      "Update command" button opens Add a device with no tab selected.
- [ ] Remove PC: confirm, card disappears and does not flicker back.
- [ ] Header button "Add a device" opens the drawer.
- [ ] Add a device: four tabs (Phone / Browser / Windows / Linux-SteamOS),
      arrow keys move between tabs, one security notice, secrets hidden until
      "Show", copy works, switching tabs hides them again.

## Downloads (LIVE-DELAY-1, DOWNLOADS-1/2)

- [ ] Hero: big number + pill + limit left, graph middle, four totals 2x2
      right; three columns at 1400, two at 1000, one at 400.

- [ ] Default range is live; a client download shows within about 10 s.
- [ ] Header cards decluttered; two columns (Active + Queue left, History
      right) at 1400, one column below 1000.
- [ ] Per-game Update and Verify buttons work.
- [ ] Queue reorders by drag and drop; ArrowUp/ArrowDown still moves a
      focused row.
- [ ] While Verifying the speed line reads "Verifying files, no download
      speed".
- [ ] History differs idle vs active; rows expand to failure hint, Retry,
      log excerpt, raw output.

## Settings (SETTINGS-1/2)

- [ ] Flat nav with the state on the right; one field card per group;
      hints behind (i); savebar inside the right column only when dirty.

- [ ] Sticky section nav on the left (Vault & connection, Download window,
      Disk & cleanup, Notifications, Steam, About); collapses sensibly
      below 1000 px.
- [ ] Expert switch reveals "Expert settings".
- [ ] Chips (Default / Changed here / From env) match the real state.
- [ ] Savebar appears on change only; risky keys still ask for
      confirmation; GC warning shows when Auto-GC is not Execute.
- [ ] Steam keeps inline Save / Preview / Reset.
- [ ] No "PCs and devices / Open Clients" row.

## Library (LIBRARY-1/2, LIBRARY-DETAIL-1)

- [ ] Chips wrap, Filters always visible, one status line; cards with one
      status line and a dot; detail sheet 920 px with hero band, tabs and a
      two-column Overview.

- [ ] One toolbar row: search, chips with counts, sort select plus an
      icon-only direction button (label and tooltip present), layout control,
      Filters popover, Select button.
- [ ] Split "Check updates" button with the menu entry "Check & update all
      cached games".
- [ ] Long-press and right-click selection still work.
- [ ] Game detail sheet: tabs Overview / Depots / Maintenance; red "Delete
      from cache" zone; both delete entry points use the same confirm.

- [ ] Game detail Overview shows the cache status line (and a damaged note
      when applicable); Verify stays in Maintenance.

## rc21 web additions already on main (look while you are there)

- [ ] Downloads: a "Client downloading" row above "Active" appears only
      while a machine has cache traffic (CLIENT-ROW-1).
- [ ] A game with depots mapped to no app reads "... not counted: N unmapped
      depot(s)" in the detail completeness line (UNMAPPED-FLAG-1).
- [ ] The incomplete-game action button reads "Verify" (was "Complete now");
      while Verifying, the status icon shows animated circular arrows, still
      under prefers-reduced-motion (VERIFY-LABEL-1).
- [ ] Unnamed content/DLC package rows read "Content package" instead of
      "Unknown app"; real unknown apps keep "Unknown app" (APP-NAMES-1).

## UI kit page

- [ ] Open `/kit` (hidden route): every primitive full width and inside a
      400 px container; compare with `docs/design/rc20/kit.html`.
- [ ] Web goldens: CI job `web / goldens` green on main.

## Result

Note per page: pass / issue (with width and a short description). Findings go
into the roadmap as IDs (roadmap rule in `CLAUDE.md`).
