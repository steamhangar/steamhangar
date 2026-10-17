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
