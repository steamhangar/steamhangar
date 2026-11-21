# ADR-0028: Web UI kit, scale and visual regression

Status: Accepted

Date: 2026-10-17

Related: `docs/design/rc20/kit.html` (binding visual reference),
`web/css/theme.css`, `web/css/kit.css`, `web/js/components/pill.js`,
`docs/PROJECT_PLAN.md` rc20 block (UI-KIT-1, WEB-GOLDENS-1).

## Context

The rc20 redesign shipped the mockups' structure but not their anatomy:
every page re-styled the old primitives by hand, copying CSS out of the
mockups. Measured on `web/css/app.css` before this ADR (comments
stripped): 24 distinct font sizes from 8.5px to 30px in 172 raw
`font-size:Npx` declarations, and 1048 px literals in total. `--dim-2`
(#63798A) reached 3.8:1 on `--surface`, below
WCAG AA for the 11px captions it is used for. Nothing caught a visual
regression: the web suite tests DOM wiring and CSS text, never pixels, so a
page could drift from its mockup without any test turning red.

## Decision

1. **One kit.** `web/css/kit.css` ports the primitives of `kit.html`
   (page head, section label, buttons, status pill, segmented control,
   tiles with meter, field card / field grid / field, filter and game
   chips, badge, attention row, empty state). Its class names and values
   are the spec; tokens come from `theme.css`. Pages compose from the kit
   instead of styling their own copies.
2. **A scale.** `theme.css` gains `--fs-1..7` (11/12/13/15/18/24/32px) and
   `--sp-1..8` (4px grid). New CSS uses the tokens only;
   `web/tests/css-kit-scale.test.js` rejects raw px font sizes and hex
   colours in `kit.css` and pins the count of legacy `font-size:Npx`
   literals in `app.css` so it can only go down. `--dim-2` becomes
   #7A91A0 (5.0:1).
3. **Opt-in scope.** Every kit rule is scoped under `.kit`, because
   `.btn`, `.pill`, `.chip`, `.sec`, `.field`, `.fg`, `.hint`, `.empty`,
   `.badge` and `.grip` already exist in `app.css` with other geometry.
   A page opts in by putting `kit` on its root; pages that have not
   migrated render exactly as before. `kit.css` loads before `app.css`
   so at equal specificity the old `[hidden]` and `:disabled` rules win.
4. **Container queries, not breakpoints.** Primitives are intrinsically
   responsive (auto-fit grids, flex-wrap); page compositions size
   themselves with `@container` on their own root.
5. **The pill wraps the status icon.** `components/pill.js`'s
   `createPill({kind, text, tone})` keeps the shape-first status system
   (`status-icon.js`) and adds the word and a tinted background. Disc
   16px, glyph box 60% of the diameter; motion rules stay in `theme.css`.
6. **A hidden `/kit` page** (`views/kit.js`, route in `router.js` and
   `webui._SPA_ROUTES`, no nav button) renders every primitive with demo
   data at full width and inside a 400px container, from the real
   stylesheets.
7. **Goldens.** Screenshot goldens of library/clients/downloads/settings/
   kit at 1400/1000/400px are package WEB-GOLDENS-1 (`tools/web-shot.mjs`,
   CI job `web / goldens`); this ADR records the decision, that package
   ships it.

## Consequences

- Later rc20 page packages (CLIENTS-4, DOWNLOADS-2, SETTINGS-2) build from
  `kit.css` and `createPill` only; a new primitive is added to the kit and
  the `/kit` page first, never to one page's CSS.
- Until a page migrates, two visual languages coexist; the migration is
  per page and the `.kit` scope keeps them from interfering.
- `app.css`'s 172 raw font sizes are a shrinking allow-list, not a target
  to fix in one sweep.
- The Android twin's `Color.kt` still pins the old `--dim-2`; it follows in
  its own package.

## Addendum: tokens contract (TOKENS-SYNC-1)

`docs/design/tokens.json` and `docs/design/status-vocabulary.json` pin colours, scale, radii and the status vocabulary for web and Android; tests on both sides are the generator.
`Color.kt`'s `Dim2` now matches the lifted #7A91A0, which settles the last consequence above.
Android `Type.kt` is a Material ramp, not a mirror of `--fs-*`, and stays outside the contract until the rc21 Compose follow-up.

## Addendum: type floor (FONT-FLOOR-1, 2026-11-21)

Audit D-3 found 21 legacy `app.css` rules at 8.5-10.5px, below `--fs-1` (11px), the floor `theme.css` names "never below this".
That debt is paid: all 21 now read `var(--fs-1)`: `.nav-btn .nav-lb`, `.nav-pip`, `.bulk .count .hid`, `.cflabel`, `.cflist .mk`, `h4.sec`, `.field label`, `.tag`, `.stepnum`, `.npip`, `.notif .nw`, `.notif .nwhen`, `.about-table td[data-label]::before`, `.depot .tag`, `.olabel`, `.orow .os`, `.ohint`, `.pkg-row .pkgstate`, `.detail-log`, `.about-table thead th` and `.rail-version`.
No rule needed `--fs-2`, because the kit's own uppercase section label is `--fs-1` too.
Decision 2's pin drops from 95 to 74, the real count, and `css-kit-scale.test.js` gains a floor lint over `theme.css`, `kit.css` and `app.css`: no px size below 11px (`.9px` and the `font:` shorthand included), no `calc()` that resolves below it, and no em/rem font size at all.
The bottom nav still fits "Downloads" and the other labels unclipped at 320, 360 and 400px.
The same package made `--nav-h` the bar's real height: the bar rendered 68-70px (its height depended on the font) against a 64px token, so the bulk bar, toast and save bar sat over its top edge.
Below BP-L the icon box and the label line are now fixed (21px, 16px), the bar is 69px with any font and `--nav-h` is 69px; landscape stays at the decided 48px (its button padding went from 8px to 9px).
`css-layout-foundation.test.js` adds the boxes up and pins them to the token.
Android needs no change: `tokens.json`'s `fs` group is unchanged, and the app's smallest text size is already 11sp (`Type.kt` `labelSmall`).
