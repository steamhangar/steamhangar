# SteamHangar design rules

How the web UI (`web/`) and the Android app (`app/`) look and move: roles
and rules; the values live in the token files and are checked by tests.
Built in DESIGN-MD-1 (rc23) from the target values of the 2026-11-20
design audit, stated as the code is after the audit packages and the
operator's decisions of 2026-11-21.

**Precedence:** an operator decision > this file > the history documents
([mockup NOTES](docs/design/vault-app-mockup-NOTES.md),
[rc20 kit README](docs/design/rc20/README.md),
[rc21 README](docs/design/rc21/README.md) and the mockups). For a value,
[`docs/design/tokens.json`](docs/design/tokens.json) wins: if this file and
the JSON disagree, the JSON is right and this file gets fixed.

## Sources

| What | Where |
| --- | --- |
| Every token value (colour, type, spacing, radius, size, shadow) | [`docs/design/tokens.json`](docs/design/tokens.json) |
| Status kinds: word, glyph, tone, motion | [`docs/design/status-vocabulary.json`](docs/design/status-vocabulary.json) |
| Web tokens and status discs | [`web/css/theme.css`](web/css/theme.css) |
| Web primitives (opt-in `.kit` scope) | [`web/css/kit.css`](web/css/kit.css), ADR-0028 |
| Android colours | `app/app/src/main/java/dev/steamvault/app/ui/theme/Color.kt` |
| Visual spec of the primitives | [`docs/design/rc20/kit.html`](docs/design/rc20/kit.html), live at the app's hidden `/kit` page |
| Phone and touch rules | [`docs/design/rc21/mobile.html`](docs/design/rc21/mobile.html) |
| Why the palette, icons and motion are what they are | [mockup NOTES](docs/design/vault-app-mockup-NOTES.md) |

To change a value: `tokens.json` first, then `theme.css` and `Color.kt`.
There is no generator; `web/tests/design-tokens-contract.test.js` and the
Android `DesignTokensContractTest` / `StatusVocabularyContractTest` fail
when one side differs.

## Colour

The app is dark only, on purpose (NOTES "Committed to dark"). Every colour
is a token or a `color-mix()` of tokens; the one literal is sic-ink
(`color_literal` in `tokens.json`). `web/tests/design-nits.test.js` rejects
any other hex, `rgba()` or `hsl()` in `app.css`, and
`web/tests/css-kit-scale.test.js` any hex or `rgb()` in `kit.css`.

### Neutrals

One ramp in the accent's hue family: OKLCH hue ~218, chroma 0.014 (`--bg`)
up to 0.026 (mid tones), lightness kept from the previous ramp
(NEUTRAL-RAMP-1, audit D-7, Weg B; it was a steel blue at hue 230-249).

| Token | Role |
| --- | --- |
| `--bg` | page ground, input fill |
| `--surface` | cards, list rows |
| `--raised`, `--raised-2` | buttons, menus, popovers, the toast, the neutral pill |
| `--line` | separators, card and button borders |
| `--line-soft` | the border of a page card, quiet dividers |
| `--line-strong` | the border of a control identified by its border (below) |
| `--text` | primary text |
| `--dim` | secondary text, labels, the neutral status word |
| `--dim-2` | tertiary text and grips, the neutral status disc, control borders on `--raised-2`, the selected-segment ring |

### Accent

`--accent` (vault aqua) keeps the roles the rc20 kit gives it
(ACCENT-ROLES-1, audit D-2, Weg A: the kit stays, the old NOTES rule
"only primary actions, active nav, focus and progress" was corrected):

- **Action:** the primary button (`.btn.pri`, `--accent-ink` on accent),
  links.
- **Focus:** the 2 px `:focus-visible` outline everywhere.
- **Selection:** current nav item, pressed chips and segments, the current
  tab or Settings section, a checked switch, picked cards; tinted with
  `--accent-glow` or a 10-14 % mix into `--surface`.
- **Progress:** progress bars and the onboarding track.
- **Live download data:** the "From the internet" graph series (line,
  area, legend dot), live speeds, the "Loading now" rows and the Clients
  "now" box (accent 8 % fill, 30 % border), the device icon on a Clients
  card.
- **Small markers:** the unread dot, the toast key line, Add-a-device step
  numbers, the changed-field chip.

Never: a status tone. No status disc and no pill uses the accent
(PILL-TONE-1 removed the last one, the update state); a status hue is never
used as the accent. `--accent-deep` is the Android Material `secondary`
only.

### Data

Graph series and live values reuse existing tokens; there is no separate
data palette. "Served from cache" is `--ok`, "From the internet" the accent,
a live speed the accent, an idle or unattributed row `--dim` with a dashed
border.

### Status

Shape first, word second, colour third: the glyph tells the state even
without colour (CVD-safe; the hues also differ in lightness). Words, glyphs
and tones come only from `status-vocabulary.json`.

| Tone | Kinds (word) |
| --- | --- |
| `--ok` | Current |
| `--run` | Downloading, Updating, Verifying (animated); Paused (still) |
| `--stale` | Update available, Warning, Partially cached |
| `--danger` | Failed |
| `--none` | Not cached, Not verified |
| neutral (`null`) | Cancelled, Check, Not in use |

- **One tone per state** (PILL-TONE-1, audit D-1): the disc
  (`.sic.k-<kind>`, Android `backgroundFor`) and the pill word use the same
  token. The one split is the neutral tone: word in `--dim` (text contrast),
  disc in `--dim-2`.
- A toned pill sits on its tone mixed 14 % into `--surface`; the neutral
  pill is `--dim` on `--raised-2`.
- **Card word** (operator 2026-11-21, Weg A): under a Library card the word
  takes the tone for alert and grey states (Update available, Partially
  cached, Failed, Not cached, Cached · not verified); Current and the
  active states keep a `--dim` word and let the dot carry the tone.
- **sic-ink** `#08120F` draws every glyph on every disc, the grey ones
  included (CONTRAST-1 / GLYPH-CONTRAST-1). It is also the ink of the
  counter pips on `--run` / `--stale`. No `.sic.k-*` rule overrides it.

### Contrast floors

Checked in `web/tests/css-contrast.test.js` (and the Android
`StatusIconLogicTest`: disc tones, no accent, pill grounds and their
contrast):

- Text 4.5:1 on its ground. `--dim-2` is the dimmest text tone allowed
  (5.26:1 on `--surface`).
- A control identified by its border (text input, select, the fused sort
  box, the icon segmented switch) takes `--line-strong`: 3.83:1 on `--bg`,
  3.45:1 on `--surface`, 3.12:1 on `--raised` (WCAG 1.4.11, CONTRAST-1 /
  LINE-CONTRAST-1). `--line` stays for separators and cards; it is 1.52:1
  on `--bg` and never the only edge of a control. Control borders on the
  `--raised-2` popover and the selected-segment ring use `--dim-2`.
- A glyph that carries meaning 3:1 on its disc (sic-ink on `--dim-2` is
  5.82:1).

## Type

**Fonts** (DESKTOP-FONT-1, audit D-14, Weg A: keep the stack). No webfont
and no bundled font files. `--ui` starts with Roboto (the Android system
face) and falls back to the platform UI face: Segoe UI Variable / Segoe UI
on Windows, `system-ui` / `-apple-system` on macOS, the distribution's UI
font on Linux unless Roboto is installed. That is the deliberate desktop
choice: the app looks native on each platform and ships no font bytes.
Text metrics therefore differ per platform, so no layout may depend on an
exact text width or height (size boxes from tokens and line heights, see
the `--nav-h` entry in `docs/learnings/web.md`). `--mono` (Roboto Mono,
Cascadia Mono, `ui-monospace` ...) sets every number, id, path and
endpoint, with `tabular-nums` where numbers line up. `--cond` (Roboto
Condensed ...) is only for cover art titles. Screenshot goldens render
with the pinned `tools/web-shot-fonts.conf`. Android uses
`FontFamily.Default` (Roboto) and `FontFamily.Monospace` (`Type.kt`).

**Scale** (`--fs-1..7`, ADR-0028; roles from `theme.css`):

| Token | Role |
| --- | --- |
| `--fs-1` | captions, mono meta, chips, key labels over a value |
| `--fs-2` | hints, pills, secondary lines, section labels |
| `--fs-3` | body text, labels, buttons |
| `--fs-4` | card titles, row names |
| `--fs-5` | section titles, card stats |
| `--fs-6` | page title, tile values |
| `--fs-7` | hero number |

- `--fs-1` (11 px) is a hard floor in `theme.css`, `kit.css` and `app.css`,
  in px, `calc()` and em/rem alike, the bottom nav and badges included
  (FONT-FLOOR-1, audit D-3).
- `kit.css` uses only the tokens. `app.css` still has legacy px sizes; their
  count is pinned and may only go down.
- **Weights:** 400 body, 500 labels, buttons, values and section heads, 700
  the primary button and page or sheet titles. 600 survives in legacy
  `app.css` rules and two kit rules; new rules do not add it (audit target,
  not migrated).
- **Case:** labels are sentence case with no tracking (DESIGN-NITS-1,
  D-12/D-13), banner actions too. Status tags (`.tag`, `.mk`, `.os`, `.nw`)
  are badges and keep their uppercase.

## Space, shape, elevation

- **Spacing:** `--sp-1..8` on a 4 px grid. A padding, margin or gap equal
  to a token value must use the token; the remaining px literals are
  pinned per file and may only go down (`design-nits.test.js`, D-9). 1-2 px
  hairline offsets are fine.
- **Radii** (D-8, no px radius literal left): `--r-s` controls (buttons,
  inputs, icon boxes); `--r-m` cards, rows, banners, the Library split
  menu and Filters popover; `--r-l` the legacy popover (`.pop`), dialogs,
  the bulk bar and the large page cards (Clients device card, Downloads
  hero); `--r-xl` sheet corners (top corners of a bottom
  sheet, all corners of the centred card); `999px` capsules (pills,
  chips); `50%` discs.
- **Elevation** (D-10): two web tokens inked from `--bg`, never black.
  `--sh-1` for what floats over the page (the Library menu and Filters
  popovers, the bulk and save bars, the toast, a dragged queue row; the
  generic `.pop` has none); `--sh-2` for overlays above a
  scrim (dialogs and sheets; no offset, so it serves every sheet variant).
  Rings drawn with `box-shadow` (`0 0 0 2px`, `inset 0 0 0 1px`) are
  borders, not elevation.
- **One separator per level** (D-12): a card's border is its only frame;
  a block inside a card is set off by spacing or one hairline, never by a
  filled or bordered box.
- **Touch** (MOBILE-1b): under `pointer:coarse` controls grow to `--ctl-h`
  (38 px) and an invisible `::after` extends the hit area to `--hit`
  (44 px).
- Layout widths and breakpoints are the spatial tokens in `theme.css`
  (`--w-page`, `--rail-w`, `--w-sheet` ...), redefined only in `app.css`'s
  breakpoint blocks.

## Motion

Motion means "something is happening now", never decoration.

- Animate `transform` and `opacity`; colour, border and ring fades on hover
  or focus are fine (140-150 ms). Never animate layout (`width`, `height`,
  position); the onboarding track scales with `scaleX` (D-16). The one
  exception today is the game detail sheet's sticky tab strip easing its
  `top` (`kit.css`).
- **Press** (D-11, kit scope: `.kit .btn`, `.kit .fchip`,
  `.kit .seg button`, `.kit .card`): these shrink to `scale(.97)` over
  100 ms while pressed; not when disabled or `aria-disabled`, and a card
  not while its own status action is pressed.
- **Overlays** (SHEET-MOTION-1): fades `--dur-fade` (180 ms), sheets and
  drawers `--dur-sheet` (280 ms) with `--ease-sheet`.
- **Status icons:** only the active kinds move, and only the glyph group:
  Downloading's arrow falls (1.6 s loop), Updating and Verifying turn
  (2.2 s loop), both linear. Paused is still.
- **Reduced motion:** a global block in `theme.css` cuts every animation
  and transition; the press rule drops its scale.

## Android twin

The app shares the colour groups of `tokens.json` (`color`, `color_alpha`,
`color_literal`) and the status vocabulary; both are contract-tested. Type,
spacing, radii, shadows, touch targets and motion above are web tokens:
Compose uses its own Material 3 type roles, dp values and tonal elevation,
and follows the same roles (accent, status tones, contrast floors). Shared
strings and logic are pinned by twin tests (see `docs/learnings/android.md`).

## Decisions behind these rules

Where the code differs from the audit proposal, the code is the rule:

| Audit item | Shipped |
| --- | --- |
| D-2 accent only for action, focus, selection; new data colour | Weg A: kit accent roles kept, no data palette (ACCENT-ROLES-1) |
| D-14 desktop font | Weg A: system stack kept, choice written down above (DESKTOP-FONT-1) |
| D-7 neutral ramp | Weg B: hue ~218, the audit's values (NEUTRAL-RAMP-1) |
| D-4 `--line-strong` #5F727E | turned with the ramp to #5F7379, same lightness |
| D-1 status tones | the audit's proposal; "Update available" in orange everywhere (UPDATE-TONE-1, UPDATE-WORD-1) |
| D-10 shadows `0 4px 14px` / `0 14px 30px`, toast and save bar on `--sh-2` | other values (see `tokens.json` "shadow"); `--sh-2` only above a scrim |
| Radii: `--r-l` for sheets and popovers | `--r-l` dialogs, the legacy popover, large page cards; kit popovers `--r-m`; sheets `--r-xl` |
| Weights 400/500/700 | target for new rules; 600 not yet migrated |

The findings and decisions are recorded in
`docs/handover/post-0.1.0-roadmap.md`, section "Audit 2026-11-20:
findings".
