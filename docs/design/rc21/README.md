# rc21 phone mockup (MOBILE-1a)

Static HTML mockup for the phone fine-tuning package MOBILE-1 in
`docs/PROJECT_PLAN.md` (rc21 block). Design only: it waits for the
operator's approval before any web code is written (MOBILE-1b). Open it in
a browser; it needs no server.

| File | Content |
|---|---|
| `mobile.html` | Phone frames (390 px, the pairing flow at 400 px, one landscape frame at 844×390): touch targets under `pointer:coarse`, overflow cue on scrolling segs, bottom sheets (detail, confirm, History row), the QR pairing flow, safe-area insets, 200 % text. Ends with the proposed CSS, the proposed new values and the open questions. |

## Rules for using it

- Builds on `docs/design/rc20/kit.html` and `web/css/kit.css`; class names
  and anatomy are the kit's, so the implementation is mostly CSS.
- Token values are copied from `web/css/theme.css`. Every value that does
  not exist yet is listed in the page's "Proposed new values" table
  (`--hit`, `--ctl-h`, `88dvh`, the landscape `--nav-h`, `6.5em`, `11em`,
  the 200 px phone QR). Nothing else is new.
- The mockup fakes `@media (pointer:coarse)` with a `.coarse` class and the
  `env(safe-area-inset-*)` values with fixed numbers (iPhone 15: 47 / 34 px
  portrait, 47 / 47 / 21 px landscape). The magenta dashed boxes, the
  hatched inset bands, the device bezel and the black island are
  annotation only and never ship.
- Copy comes from `web/js` (pairing, History delete, failure hints,
  `PAIR_TEXT`); sample data is illustrative. Mockup copy is never a reason
  to change shipped wording.
- Each frame's note names the CSS rule it implies; the "Proposed CSS"
  block collects them. It is the shape of the rules, not final code.

## Status (2026-10-18)

Draft for review. The open questions at the end of the page (Q1-Q7) need
the operator's decision before MOBILE-1b starts.
