# tools/

Developer tools that are neither shipped nor part of a component. Plain
Node (22+), no npm dependencies; the repo has no `package.json`, so the
commands below are the interface.

## Web screenshot goldens (WP WEB-GOLDENS-1)

Three scripts render the web UI headlessly and compare the result with the
checked-in goldens in `web/tests/goldens/<page>-<width>.png`:

| script | role |
|---|---|
| `web-serve.mjs` | serves `web/` like vault-api does (index.html for `/` and the router's views, files as files, 404 for everything else incl. `/v1/*`). Reads `VIEWS` from `web/js/router.js`, so an unshipped route is a 404 here too. |
| `web-shot.mjs` | drives a Chromium binary over the DevTools protocol (Node's built-in WebSocket + fetch, no playwright package) and writes one full-page PNG per page and width. |
| `png-diff.mjs` | compares two PNG directories pixel by pixel (own PNG codec on `node:zlib`), writes a diff PNG per failing pair, prints a table, exit 1 on failure. |
| `web-shot-fonts.conf` | self-contained fontconfig: every family in `web/css/theme.css`'s font stacks maps to Liberation Sans / Mono, no other installed font is admitted, glyphs render unhinted with grey antialiasing; it deliberately does not include the host's fontconfig. |

### What makes the pixels reproducible

- Demo mode (`localStorage["steamvault.demoMode"] = "1"`), a fixed Downloads
  range (`steamvault.downloadsRange = "6h"`) and the dismissed desktop-site
  hint are written by a script that runs before the app's modules on every
  navigation, so no API, no onboarding overlay, no live polling.
- Frozen clock (`Date` shim at `--clock`, default `2026-10-17T12:00:00Z`),
  timezone `UTC`, locale `en-US` -- demo timestamps and "x min ago" labels
  never move.
- `prefers-reduced-motion: reduce`, hidden scrollbars, device scale factor 1,
  sRGB colour profile, LCD text off.
- Every request that leaves the served origin fails (`Fetch` domain), so
  cover art always shows its fallback tile instead of a CDN download.
- Same Chromium build everywhere: CI installs it with
  `npx --yes playwright-core@1.63.0 install --with-deps chromium`
  (build 1243 = Chrome for Testing 153.0.8010.12). Locally the tool finds
  the newest `~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome`;
  `--chrome <path>` or `CHROME_PATH` override it. Bump the pin here and in
  both `ci.yml` steps together (a pytest pins that they agree).
- Same fonts everywhere: CI installs `fonts-liberation`; the tool points
  Chromium at `tools/web-shot-fonts.conf` (`FONTCONFIG_FILE`), so the
  host's own fontconfig (the devbox maps `sans-serif` to Liberation Mono)
  cannot leak in. The conf admits only Liberation Sans, Serif and Mono
  (`<selectfont>`): a runner has DejaVu, Noto and more, the devbox only
  Liberation, and Chromium falls back per glyph -- CI drew the kit grip
  (U+22EE) from a runner font, the devbox drew a missing-glyph box. For
  the same reason Liberation Sans Narrow (in Debian 12's fonts-liberation
  1.07, not on the runner) is rejected and the condensed stack maps to
  Liberation Sans. Hinting is off (`hintnone`): it depends on the font
  files' glyph programs and the FreeType build, which differ between the
  devbox (Debian 12) and CI (Ubuntu 24.04). After editing the conf run
  `FONTCONFIG_FILE=$PWD/tools/web-shot-fonts.conf fc-match 'sans\-serif'`
  (absolute path: fontconfig looks a relative one up under /etc/fonts) and
  expect Liberation Sans, and `fc-list : family` must print exactly the
  three families -- fontconfig silently ignores a malformed file
  (XML forbids `--` inside a comment), and `fc-match` needs the hyphen
  escaped (it reads `a-b` as family `a`, size `b`; Chromium passes the CSS
  names verbatim).
- No `--font-render-hinting`: build 1243's `chrome` binary does not have
  the switch (only `chrome-headless-shell` does); with it, 0 pixels changed.

Measured on the devbox (2026-10-17): two consecutive runs of the full set
differ by 0 pixels.

### Comparing a workstation's shots with CI's goldens (`--mode aa-tolerant`)

Same Chromium build and same fonts are still not the same pixels on another
OS: text rasterises with slightly different glyph-edge coverage. Measured
2026-10-17 (devbox Debian 12 vs CI goldens, the conf before GOLDENS-LOCAL-1):
all 15 pages failed strict with 0.30-0.84 % differing pixels, every one on
a glyph edge, layout identical. `png-diff.mjs --mode aa-tolerant` excuses a
differing pixel when it is rasterisation noise (details in the file
header): its value is a blend of the other image's 3x3 neighbourhood in both
directions, or both neighbourhoods are edges with the same local "ink"
(mean within 24 per channel). Everything else still counts against
`--threshold`. Excused pixels are yellow in the diff PNG, counted ones red.

Measured with it (same day, against the then-current goldens):

| local shots | strict | aa-tolerant |
|---|---|---|
| old conf (hinted, Narrow, fallback fonts) | 15 fail, 0.30-0.84 % | 15 pass, 0.02-0.11 % (rest: brand in Narrow, grip glyph) |
| this conf (unhinted, Liberation only) | 15 fail, 0.74-2.41 % (goldens still hinted) | 15 pass, 0.001-0.06 % (rest: grip glyph) |
| kit tiles shifted 4 px (downloads) | fail | fail, 0.47 / 0.60 % |

Against an identical local baseline the tolerant mode keeps most of what
strict sees: a 4-px tile shift 61-66 % of strict's pixels, a tile
background change 90-92 %, a hidden element 100 %. Its blind spot is a
whole element moved by exactly 1 px (indistinguishable from an edge drawn
with other coverage); like strict, it also cannot see a change smaller than
`--threshold` (one changed letter, a hidden 16-px wordmark at 1000 px width).

CI keeps `strict` (CI shots vs CI goldens are bit-identical). Use
`aa-tolerant` only where the shots come from another machine, i.e. the
operator's local check:

```
node tools/png-diff.mjs --expected web/tests/goldens --actual <out> --diff <out>-diff --missing warn --mode aa-tolerant
```

### Run it locally

```
node tools/web-serve.mjs --port 8787 &
node tools/web-shot.mjs --base http://127.0.0.1:8787 --out /tmp/web-shots
node tools/png-diff.mjs --expected web/tests/goldens --actual /tmp/web-shots --diff /tmp/web-shots-diff --mode aa-tolerant
```

(`--mode aa-tolerant` because the goldens come from CI, see below.)

`web-shot.mjs` flags (all optional): `--base`, `--out`, `--widths 1400,1000,400`,
`--pages library,clients,downloads,settings,kit`, `--chrome`, `--clock`,
`--timezone`, `--locale`, `--wait-selector "#view-root .view"`, `--timeout`
(ms), `--settle` (ms), `--max-height`, `--fonts-conf`, and the booleans
`--[no-]reduced-motion`, `--[no-]hide-scrollbars`, `--[no-]block-offsite`.
A page the server answers with 404 is skipped with a warning (the `kit`
page exists only once UI-KIT-1 has landed); any other failure exits 1.

`png-diff.mjs` flags: `--threshold 0.002` (fraction of differing pixels a
pair may have), `--tolerance 8` (per-channel delta below which a pixel is
"the same"), `--missing fail|warn` (a screenshot without a golden),
`--mode strict|aa-tolerant` (above; default `strict`),
`--github` (workflow-command annotations + step summary); `warn` covers
wholly new pages only, a missing width of a known page is `partial` and
fails. A golden without
a screenshot always fails (stale). Different sizes always fail.

Against the running dev instance instead of the static server, point
`--base` at it; demo mode is set by the tool, the instance's data is not
touched. Those pixels are for eyeballing only -- goldens come from CI.

### Goldens: recorded by CI, never committed from a workstation

Only CI's pixels are canonical (same reason as the Android goldens in
`app/README.md`): one Chromium build, one font set, one fontconfig. The
flow, mirrored from `android-goldens-record` / `android-goldens-push`:

1. Push the work to a branch named `wp/<name>-goldens`.
2. `web-goldens-record` deletes `web/tests/goldens/*.png`, re-records every
   page the server has, renders a second time and compares (must match),
   and uploads the PNGs as the `web-screenshot-goldens` artifact.
3. `web-goldens-push` (the only web job with a write token; runs no node,
   no npx, no repository script) validates the artifact -- names must be
   `<page>-<width>.png` for the `DEFAULT_PAGES` / `DEFAULT_WIDTHS` lines in
   `tools/web-shot.mjs`, every width of a recorded page present, real PNGs
   -- commits them as github-actions[bot] and pushes to the same branch.
   It runs after `android-goldens-push` and rebases over that job's commit
   if Android goldens are all that moved; any other movement of the branch
   aborts (the newer run takes over).
4. `git fetch` and review the bot commit's images, then merge as usual. A
   `-goldens` branch accepts **every** visual change its push renders; the
   guard is that review plus the normal CI run on the merge.

The verify job (`web / goldens`) never records: it only compares. On it a
screenshot of a wholly new page without any golden is a notice (the first
run after a page is added); a missing width of a page that has goldens at
other widths (`partial`), a golden without a screenshot, a size change or
more than 0.2 % differing pixels is a failure. Consequently `kit` (UI-KIT-1,
now on main) is verified only after a `wp/<name>-goldens` record run that
follows the UI-KIT-1 merge; until then each of its shots is a notice;
`web-screenshots` (all PNGs) and `web-screenshots-diff` (failing pairs,
differing pixels red, size overflow magenta) are the artifacts.

### Tests

```
node --test web/tests/png-diff.test.js web/tests/web-serve.test.js web/tests/web-shot-config.test.js
```

The CI contract (jobs, pins, the executed validate / commit / push steps) is
pinned by `api/tests/test_web_goldens_1_ci.py`.
