# rc20 web redesign: approved mockups

Static HTML mockups approved on 2026-10-16. They are the visual reference for
the rc20 packages in `docs/PROJECT_PLAN.md` ("rc20 web redesign"). Open them
in a browser; they need no server.

| File | Page | Packages |
|---|---|---|
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
