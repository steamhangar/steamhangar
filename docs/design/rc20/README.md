# rc20 web redesign: approved mockups

Static HTML mockups approved on 2026-10-16. They are the visual reference for
the rc20 packages in `docs/PROJECT_PLAN.md` ("rc20 web redesign"). Open them
in a browser; they need no server.

| File | Page | Packages |
|---|---|---|
| `kit.html` | **UI kit (primary reference since 2026-10-17):** scale, contrast, status vocabulary, primitives, the three-zone Clients card and the width-aware Downloads layout, each at 1400 and 400 px | UI-KIT-1, CLIENTS-4, DOWNLOADS-2 |
| `clients.html` | Clients, device cards, Add a device drawer | CLIENTS-1..3, ADD-DEVICE-1 |
| `downloads.html` | Downloads: idle and busy states, History collapsed and expanded | DOWNLOADS-1 |
| `settings.html` | Settings: section nav, field grids, savebar | SETTINGS-1 |
| `library.html` | Library toolbar, cards, game detail sheet | LIBRARY-1, LIBRARY-DETAIL-1 |

## Rules for using them

- **Structure, spacing, hierarchy and behaviour are binding; copy is not.**
  Some placeholder lines in the mockups are German or invented. The shipped
  UI keeps the existing English strings from the code (`settings-layout.js`,
  `settings-presentation.js`, `about-view.js`, ...). New strings are English.
- Tokens come from `web/css/theme.css`, never from the mockup CSS. The
  mockups use the same values, but production code uses the variables.
- Sample data (device "Desk PC", game list, job numbers) is illustrative.
- Width: the wide layout is `--w-page` (about 1400px, package LAYOUT-1); the
  mockups are drawn at 1480px viewport with side gutters.
- Motion: sheets and drawers slide or fade (package SHEET-MOTION-1) and
  respect `prefers-reduced-motion`.
- No logic is implemented in the mockups; the DOM structure is not a
  prescription for the vanilla JS views.

## Precedence (2026-10-17)

`kit.html` wins over the page mockups where they differ: Clients cards are
one per row in three zones (not a two-column grid), the Downloads hero puts
the four totals beside the graph and the page has three columns, pills wrap
the `status-icon.js` disc instead of a dot, `--dim-2` is the lifted #7A91A0
(#7C9298 since NEUTRAL-RAMP-1). The page
mockups remain the reference for everything the kit does not show
(drawer, Settings panels, Library toolbar and detail sheet).

## Token and status contract (TOKENS-SYNC-1)

`docs/design/tokens.json` (colours `color`, `color_alpha`, `color_literal`,
type scale `fs`, spacing `sp`, radii `r`, and the web-only groups `size`
(touch targets) and `shadow` (elevation)) and `docs/design/status-vocabulary.json` (kind, word, glyph, pill tone,
animated) are the one source both frontends are checked against. There is no
generator: `web/tests/design-tokens-contract.test.js` checks `theme.css`,
`status-icon.js` and `pill.js`, and the Android `DesignTokensContractTest` and
`StatusVocabularyContractTest` check `Color.kt`, `StatusKind`, `strings.xml`
and the status-icon logic, so a value changed on one side only fails CI.
Change a value in the JSON first, then on both sides.

One tone per state (PILL-TONE-1, audit D-1): a kind's pill tone is also its
disc colour (`.sic.k-<kind>` in `theme.css`, `backgroundFor` on Android),
as in the kit, where the disc is `currentColor`. The neutral tone (`null`)
writes its word in `--dim` and draws its disc in `--dim-2`. Both contract
tests check the disc colour against the tone. The update state reads
"Update available" in orange on every surface.

Card word rule (operator decision 2026-11-21, Weg A): the status word under
a Library card takes its state's tone wherever the tone is an alert or a
grey state: orange for Update available and Partially cached, red for
Failed, `--none` for Not cached and Cached · not verified, so word, pill and
dot agree for those states. Current and the active states (Downloading,
Updating, Verifying, Paused) keep the `--dim` word; their dot carries the
tone. Every coloured card word is 4.5:1 or more on the list row's
`--surface` and on `--bg` (`web/tests/css-contrast.test.js`).

DESIGN-NITS-1 rules (audit 2026-11-20, D-5..D-16; pinned in
`web/tests/design-nits.test.js`):
- Labels are sentence case: section labels (`.sec`, card titles, "Loading
  now") at `--fs-2`, key labels over a value at `--fs-1`, no uppercase
  transform and no tracking. Status tags (`.tag`, `.mk`, `.pkgstate`, `.os`,
  `.nw`) are badges, not labels, and keep their uppercase.
- One separator per level: a card's border is the only frame; a block
  inside a card is a row of it, set off by spacing or one hairline, never a
  filled or bordered box (Downloads hero tiles, the Settings connection
  row, the Clients agent line).
- Elevation: `--sh-1` for what floats over the page (menus, popovers, the
  bulk and save bars, the toast, a dragged queue row), `--sh-2` for overlays
  above a scrim (dialogs and sheets; no offset, so it serves every sheet
  variant). Both are inked from `--bg`; rings drawn with box-shadow are
  borders, not elevation.
- Press: `.btn`, `.fchip`, `.seg button` and `.card` shrink to
  `scale(.97)` over 100 ms while pressed (not when disabled or
  `aria-disabled`, a card not while its own status action is pressed), and
  stay still under reduced motion.
- Radii come from `--r-*` (controls `--r-s`, containers `--r-m`); spacing
  from `--sp-*`; colours from the tokens (sic-ink is the counter pips' ink).
- The rc20 page mockups are superseded where they differ on D-12 (cards in
  cards, uppercase labels) and D-15 (the Library toolbar: search and tools
  on the first row, the chips in their own row; DOM and tab order search,
  tools, chips, on a phone too).

## Status (2026-10-17)

All page packages of the rework are on main. The live reference for the
primitives is the hidden `/kit` page of the app (rendered from the real
stylesheets); `kit.html` stays the design spec and must use the same
values as `web/css/kit.css`.
