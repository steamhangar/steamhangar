# Plan archive: Phase 4e web desktop layout

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

#### Phase 4e — Web UI desktop layout (track: web)

The web UI (Phase 4a) has no responsive layer at all: the frozen round-7
mockup is a single 390×844 phone frame ported into a `max-width:960px`
column, so a 173×260 cover tile renders at a fixed 458×687 from 1280 to
2560px, the bottom nav's three items stretch to 631–844px around a 21px
glyph, and roughly half of a 1920px viewport carries nothing. **User
decision (2026-08-18): approved the full desktop-layout proposal, all
divergences included** ("setze alles so um") — implemented as designed,
not narrowed. See `docs/WORKPACKAGES.md`'s divergence register (D-1, D-3,
D-4, D-11, D-12) for what each package diverges from the mockup and why.

- [x] Spatial tokens, breakpoints (BP-M ≥720px / BP-L ≥1024px / BP-XL
      ≥1800px, width-keyed; pointer affordances in a separate
      `(hover:hover) and (pointer:fine)` query), and the bottom-nav-to-
      left-rail shell conversion; the large-library demo fixture the rest
      of the phase measures against
      *(WP 4e.1 done 2026-08-18: `--gutter`/`--nav-h`/`--rail-w`/
      `--w-text`/`--w-wall`/`--tile-min`/`--w-sheet` tokens in theme.css
      (no colour/font/radius value touched — Android's VaultColors literal-
      pins stay valid); `.view-root`/`.onb`/`.banner-wrap`'s four
      hand-copied 960/928/78 numbers replaced with token references/`calc()`
      derivations (correction, Opus review nitpick N5: this is four numbers
      that were COPIES of each other, not every dimensional literal in
      app.css — `.view-root`'s own `20px 16px 32px` padding and `.bulk`'s
      `left/right:12px` insets are untouched, independent literals, not
      claimed otherwise); `.banner-wrap`'s shrink-to-fit bug fixed (`width:100%`,
      no `display` declaration added — would have been a fourth `[hidden]`-
      vs-author-`display` instance); `<nav>` moved before `<main>` in
      index.html (D-11) with a compensating `.nav{ order:1 }` so VISUAL
      order below BP-L is unchanged (`order` is paint-order only — see S2
      below for the READING/focus-order consequence this claim does not
      cover); the shell/rail/breakpoint machinery verified to live only
      inside `min-width` blocks via a structural headless pin, plus a live
      six-width measurement (390/768/1280/1440/1920/2560) — reported in the
      coder's response (superseded by the corrected, extended table below —
      see blocker B3).
      Two general-purpose CSS lints added per the brief's request: (1) every
      class with an author `display` rule that is ALSO hidden-toggled
      somewhere in `web/js/` must have a matching `[hidden]` guard rule —
      a real cross-reference derived from the CSS/JS source each run, not a
      hand-typed list of the three previously-found offenders (`.btn`,
      `h4.sec`, `.onbnav`), mutation-verified by removing a guard and
      watching the named test die; (2) no `!important` outside the
      `prefers-reduced-motion` block, mutation-verified by adding one to a
      new breakpoint. `web/js/demo-data.js` gained
      `generateSyntheticGames`/a `localStorage["steamvault.demoLibrarySize"]`
      toggle (400 selectable, default unchanged at 6) reachable from both a
      headless test and the running app; documented in its own header what
      it can measure (DOM/poll-diff cost at scale) and cannot (no real cover
      art, so it does not exercise the CSP image-allowance half of a real
      400-game library).

      **Sequencing fix (orchestrator review, same day):** a first version of
      this package widened `--w-wall` to 1440px at BP-L and 1720px at BP-XL,
      on the reasoning that the rail frees up horizontal room the library
      grid should get to use. Measuring it live instead of assuming it
      exposed the opposite: because the Library grid is STILL the mockup's
      fixed 2/3/list column switch, not an `auto-fill` density grid,
      widening `.view-root`'s cap only grew the ALREADY-oversized cover tile
      further — 458×687 (the pre-WP baseline, unchanged from 1280–2560px)
      became ~815×1222 at 1920px and ~838×1257 at 2560px. Shipping that
      would have made the phase's own motivating problem measurably worse
      the moment this "foundation" package landed on its own, which is not
      independently shippable. **Fixed by sequencing, not scope creep:**
      `--w-wall` stays equal to the pre-WP effective cap (960px, `theme.css`)
      in EVERY breakpoint block (`:root` base, BP-L, BP-XL all say 960px
      explicitly — restated rather than omitted, so a later reader sees a
      deliberate decision, not a forgotten override) until a later package's
      auto-fill grid can actually use the room; that package changes this
      ONE value. `--w-text` (Settings/Downloads/onboarding/banner — text-
      first views sharing the same `.view-root`) still narrows to 760px at
      BP-M as designed; only the library-grid-specific widening at BP-L/XL
      was deferred. Two headless pins added
      (`web/tests/css-layout-foundation.test.js`): every `--w-wall:`
      assignment anywhere in theme.css/app.css must equal `960px`, and
      `.view-root`'s BP-L override must resolve to the SAME 960px as its
      base/BP-M cap — both mutation-verified (temporarily restoring
      1440px/1720px kills each by name). Re-measured live after the fix (see
      the corrected six-width table below): the tile is now IDENTICAL to the
      pre-WP baseline at every width from 1280–2560px, not merely "no
      worse". **Honest status, not silently narrowed:** the tile-oversize
      problem itself remains genuinely unfixed — that is explicitly D-4's
      scope, landing with the auto-fill grid below — this package now
      simply does not regress it in the meantime.

      **Opus review round 1: FAIL — three blockers, five should-fixes, six
      nitpicks, all fixed same day.** The reviewer's framing: the shell
      change (bottom nav -> left rail, `#app` flex -> grid) re-parents every
      `position:fixed` surface, and the first live-verification pass only
      measured three selectors (the tile, `.view-root`, the nav) — it should
      have swept every fixed/overlay surface. Two real bugs existed that a
      3-selector check could not see:
      - **B1** — `--nav-h:0` (BP-L) is UNITLESS; `.bulk`'s
        `bottom:calc(var(--nav-h) + 14px)` mixes it with a `14px` length,
        which is a calc() type mismatch, invalid at computed-value time,
        silently falling back to `bottom:auto`. Measured: the bulk action
        bar's `bottom` computed to -143782px (off screen) at every width
        >=1024px — multi-select (enter select mode, pick games, Download or
        Delete) was **unusable on desktop**. Fix: `--nav-h:0px`, plus a pin
        requiring every `--nav-h:` assignment anywhere to match `/^\d+px$/`
        (the CLASS of bug, not just the instance) — mutation-verified.
      - **B2** — `index.html` puts `.nav-pip` FIRST inside the Downloads
        button (harmless while `position:absolute`); BP-L's
        `position:static; margin-left:auto` override then rendered it BEFORE
        the icon in plain DOM/flex order, pushing icon+label right instead —
        measured live with a job running: the whole Downloads row sat ~88px
        out of line with Library/Settings. Fix: `order:1` on the BP-L
        `.nav-pip` rule (no DOM move — `aria-current`/click/keyboard/the
        `.on` toggle are all untouched), pinned and mutation-verified;
        re-measured at 1024/1280/1920px with an active job — Downloads'
        icon (x=22) and label (x=55) now match Library/Settings exactly.
      - **B3** — the plan and the test-file header both claimed "base
        (<720px) is byte-identical", which is false from ~430px up:
        `.banner-wrap{width:100%}` (the shrink-to-fit fix) is a deliberate
        TOP-LEVEL rule, not gated by any media query, so it changes real
        rendering wherever the bug it fixes existed. Measured against a
        `git archive HEAD` baseline: 430px viewport, 398.3px-wide banner @
        x=8.3 -> 415px @ x=0; 719px viewport, 398.3px @ x=152.8 -> 704px @
        x=0. **Fixed the CLAIM, not the behaviour** (the behaviour is the
        correct fix and was already disclosed elsewhere): only the mockup's
        own 390px is genuinely byte-identical end to end; "base is
        untouched" now means specifically "the shell/rail/breakpoint
        machinery is gated correctly", stated exactly that way in both this
        section and the test file's header, with `.banner-wrap{width:100%}`
        pinned as the one rule that must never silently revert.

      Should-fixes, all closed: **S1** `generateSyntheticGames` now sets
      `needsForce: !cached` (an idle/never-filled synthetic row was
      `needs_force=false`, a shape the real API can never produce per
      api/README.md's lifecycle — a demo fixture is a shipped 1:1 surface).
      **S2** `docs/WORKPACKAGES.md`'s D-11 entry now records the phone-side
      consequence honestly: `order` is visual-only, so below BP-L a
      keyboard/screen-reader user now tabs the nav BEFORE the content on
      every view — a real change to the exact surface WP 4a.8's a11y pass
      signed off on, defensible but not previously stated. **S3**
      `web/tests/README.md` gained a WP 4e.1 section (three new test files)
      and an honest "what this pass did not catch on its own" note naming
      B1/B2's category (any shell-layout-changing package must re-check
      every fixed/overlay surface, not just the ones its diff touches — no
      headless equivalent exists for this, same posture as WP 4a.8's list).
      **S4** `.banner-wrap` now picks up `--w-wall` (not `--w-text`) at
      BP-L, matching `.view-root` exactly — they share one visual column and
      a mismatched cap was a measured 100px seam (left edges 688.5 vs
      588.5 at 1920px); tying both to one token also keeps a later
      `--w-wall` raise aligned for free. **S5 — the process fix.** Widened
      this package's own regression pass to the surfaces B1/B2 fell into:
      `.bulk`, `.sheet-backdrop` (notifications panel, verified directly),
      `.dialog-backdrop` (delete confirm, verified directly) and `#toast`
      (verified directly) all confirmed on screen at 1024/1280/1920px in
      multi-select with an active job. The 1023–1279px transition band
      (below) was added to the measurement table per this same finding — the
      rail visibly taking 232px out of the available column is the widest-
      changing band in the whole spec and the first draft skipped it.

      Nitpicks, all closed: **N1** `css-hygiene.test.js`'s display/hidden
      lint now also recognizes `setAttribute("hidden", ...)`/
      `removeAttribute("hidden")` as toggle sites (not just the `.hidden =`
      property form — the reviewer's own probe of this hole survived the
      first version and now dies by name), and its header states the
      multi-class/descendant-selector limitation explicitly. **N2**
      `sectionHeadingFactory`'s hardcoded `["sec"]` mapping is now asserted
      against the real `sectionHeading()` source, so drift fails loudly.
      **N3** the six-width table below now says which scrollbar mode
      produced which row: viewports <768px trigger this Browser pane's
      mobile/touch emulation (overlay scrollbar, 0px reserved — confirmed:
      `innerWidth === clientWidth` at 390px), 768px and up use a classic
      scrollbar (15px reserved whenever content actually overflows, which
      the 400-game fixture guarantees) — not an inconsistency, a real
      platform difference the numbers now name. **N4** a dedicated pin now
      checks the exact shape of `last_prefill_at` (real ISO string for
      "done", exactly `null` for "idle") separately from the determinism
      check that strips it. **N5** the "four hand-copied numbers" claim
      above is corrected to name what it does NOT cover (`.view-root`'s own
      `20px 16px 32px` padding, `.bulk`'s `left/right:12px`, both still
      independent literals). **N6** `--tile-min`'s existence is now asserted
      inside the SAME test as the `--w-wall` anti-widening pin, so an
      automated "remove this, nothing references it" sweep cannot silently
      delete a token a later WP needs.

      Suite 450 green (36 new relative to the pre-WP-4e.1 baseline of 414:
      30 from the first pass, plus 6 new named tests in this fix round — one
      each for the B1 `--nav-h` unit pin, the B2 `.nav-pip order:1` pin, the
      S4 `.banner-wrap`/`--w-wall` alignment pin, the S1 `needs_force`
      inverse-of-cached pin, the N2 `sectionHeadingFactory` mapping pin, and
      the N4 `last_prefill_at` shape pin. B3's fix and N5/N6 extended
      EXISTING tests/comments in place rather than adding new ones; N1's
      `setAttribute`/`removeAttribute` recognition extends the existing
      lint's own scan, covered by the existing display/hidden-guard test
      re-passing plus the mutation check below, not a new test file entry).

      **Six-width table, final (re-measured after every fix above, live
      against a running vault-api + the 400-game demo fixture; scrollbar
      mode noted per N3):**

      | width | cover tile | `.view-root` | nav | scrollbar |
      |---|---|---|---|---|
      | 390 | 173.0×259.5 (byte-identical to pre-WP) | — (base) | bottom, 3-col | overlay (mobile emulation) |
      | 768 | 354.5×531.8 | 760px cap (BP-M) | bottom, 3-col, chips wrap | classic, 15px |
      | 1023 | 358.0×537.0 | 760px cap (still BP-M) | bottom, 3-col | classic, 15px |
      | 1024 | 366.5×549.8 | 777px (rail takes 232px out of the column) | rail, 232px | classic, 15px |
      | 1200 | 454.5×681.8 | 953px (approaching the 960px cap) | rail | classic, 15px |
      | 1279 | **458.0×687.0** (cap reached) | 960px cap | rail | classic, 15px |
      | 1280 | **458.0×687.0** (pre-WP baseline, exact) | 960px cap | rail, 232px | classic, 15px |
      | 1440 | **458.0×687.0** | 960px cap | rail | classic, 15px |
      | 1920 | **458.0×687.0** | 960px cap | rail | classic, 15px |
      | 2560 | **458.0×687.0** | 960px cap | rail | classic, 15px |

      The 1023→1279 band (added per should-fix S5) is the widest-changing
      one in the whole table: crossing BP-L subtracts the rail's 232px from
      the available column before `.view-root` can reach its 960px cap, so
      the tile visibly SHRINKS relative to where it would have landed
      without a rail, then grows back to the steady 458×687 by ~1279px —
      correct, expected behaviour, not a bug, but worth seeing in the table
      rather than jumping straight from 1023 to 1280.

      Re-verified separately, live, at 1024/1280/1920px in multi-select
      with an active synthetic job (the exact conditions B1/B2 needed to
      surface): `.bulk` on screen at all three (`bottom` computed to the
      intended `14px`, `rect.bottom` inside the viewport with the .22s
      slide-up transition settled); the Downloads nav-pip `on`, showing
      "1", at the row's trailing edge with icon/label x matching Library/
      Settings exactly; the notifications sheet-backdrop, the bulk-delete
      `.dialog-backdrop`, and `#toast` all independently confirmed on
      screen at 1920px.

      **DOM node count vs. column count — a framing correction (orchestrator
      review), pinned in a code comment so it is not re-litigated in a later
      package.** `web/js/views/library.js`'s `renderGrid` builds one card
      node per VISIBLE game regardless of `state.layout` (2/3/list all run
      the identical loop; only `els.grid.className` — a CSS concern —
      changes). A 400-game library already builds 400 card nodes TODAY, in
      every layout, on every viewport width; there is no virtualization
      either before or after this package. The auto-fill grid Phase 4e
      eventually ships changes how those 400 nodes are ARRANGED (more
      columns at wider viewports), never how many exist. The measured
      ~29–33ms cost of a full 400-card rebuild (see WP 4e.1's coder report)
      is therefore the number that actually matters for scaling, not a
      column-count framing — the gate this fixture exists to clear is
      already cleared)*
- [x] Auto-fill library grid using `--tile-min` (`repeat(auto-fill,
      minmax(var(--tile-min),1fr))`), replacing the fixed 2/3/list switch at
      BP-L/BP-XL, and raising `--w-wall` past its currently-deferred 960px
      alongside it (in the SAME package — see the sequencing note above,
      widening one without the other is exactly the regression already
      caught and fixed once) — the D-4 fix
      *(WP 4e.2, 2026-08-18, Opus round 1: FAIL — one blocker, five
      should-fixes — fix round verified, re-verified live: `web/css/app.css`'s
      BP-L block wires `.grid`/`.grid.cols3` to `repeat(auto-fill,minmax(
      var(--tile-min),1fr))`, `--tile-min` split into a 176px "Comfortable"
      default and a 150px "Compact" override (`.grid.cols3{--tile-min:150px}`,
      operator-chosen values, see below) — the D-3 density relabeling in
      `web/js/views/library.js` (`LAYOUT_LABEL`: "Two columns"/"Three columns"
      -> "Comfortable"/"Compact", `docs/WORKPACKAGES.md`'s register, now in
      proper bullet form). "Fix the tile, multiply the columns": `.grid.
      cols3`'s mobile-only compact card typography (tuned for a ~113px
      3-per-row phone tile) is reset back to the base card's own values at
      BP-L, seven reset rules pinned by literal name (round 1's blocker-class
      finding, S3: deleting the base `.grid`/`.grid.cols3` rules OR all of
      the BP-L reset rules survived 456/456 — both now die by name, verified
      by re-applying each mutation and watching the named test fail);
      `.grid.list` needed no override at all (kept via CSS specificity,
      pinned structurally). `--w-wall` raised to 1600px at BP-L (round 1
      should-fix S2: this value is UNREACHABLE within BP-L's own range —
      BP-L's widest viewport, 1799px, leaves a 1567px main column, 33px short
      — so it functions purely as a guard against a future `--rail-w`/
      breakpoint change, not as an active cap; the original comment's "lands
      at ~186px @1920, capped" claim was wrong, since 1920px is BP-XL's range
      where a SEPARATE 2000px value applies, fixed in both the code comment
      and the test's assertion message) and 2000px at BP-XL (bounded, not
      fully uncapped — argued against the brief's "topbar/reading measures
      don't improve past ~1600" note: true of `--w-text` content, not of a
      tile WALL, which keeps getting more columns from more room). `.bulk`
      re-derived from `--w-wall` with `--rail-w + --gutter` / `--gutter`
      insets — proven algebraically exact in both the capped and uncapped
      regimes, confirmed live at ten widths total across both review rounds
      with Δleft=Δwidth=0 throughout, including inside BP-XL. Search+chips
      moved into a `.view-library` BP-L grid, search capped at the new
      `--search-w:420px` token — round 1 should-fix S4: the cap and the
      area-map row order were both asserted by NAME (declared) but not by
      VALUE, so removing the cap (`1fr 1fr`) and swapping "search chips" to
      "chips search" both survived; both mutations now die by name.
      **Blocker B1 (round 1): `.empty` (the "no results" fallback, appended
      as a direct child of `.grid`) had no `grid-column` rule, unlike
      `.noresult` — under auto-fill it collapsed into a single track (an
      8.5%-of-row-wide block hard left at 2560px), reachable one click away
      via any zero-count filter chip (`renderChips` renders zero-count chips
      too). Fixed with `grid-column:1/-1`, pinned, and confirmed safe for
      `downloads.js`'s two other `.empty` uses (plain block containers, the
      property is ignored there) — re-verified live via the exact
      reproduction (the real "Downloading 0" chip on the 400-game fixture):
      `.empty` now measures the full grid width, not a single track.**
      **Should-fix S1 (round 1): operator-chosen `--tile-min` values,
      176px Comfortable / 150px Compact**, replacing the coder's
      first-shipped 210px/168px — measured live, 210px produced
      225.6-246.0px tiles (1.30x-1.42x the 173px mockup tile), falsifying
      the "no card here ever sees a size it wasn't designed for" claim.
      150px restores Compact's original WP 4e.1 value.

      **B2 (round 2, blocker): round 1's OWN S1 fix was still wrong.** It
      asserted 176px keeps the range at "~176-205px (mockup ±18%)" — wrong
      on both counts (a 4px-resolution sweep over 396 widths, 1024-2600px
      plus 3440px, both densities, measured the real range: 177-222px,
      1.02x-1.29x; 220/173 is +27%, not ±18%), and self-contradicted by the
      SAME comment's own sawtooth example ten lines below (220.0px) sitting
      under the 205px ceiling it had just asserted. Corrected in `theme.css`
      (and in `docs/WORKPACKAGES.md`/`web/tests/README.md`, which repeated
      the same wrong figure), WITH the load-bearing clause a bare
      number-fix would have omitted: a <=205px ceiling at every width is
      unachievable with `minmax(F,1fr)` at all — it would need F<=158px,
      already below Compact's own 150px floor — so this was never a value
      the token failed to find. The overshoot mechanism itself
      (`max = F + (F+gap)/n`, worst at the fewest columns, tightening as
      columns are added) is documented in theme.css's `--tile-min` comment,
      along with the operator's sawtooth-not-smoothed decision (re-measured
      example: 220.0px at a 1195px viewport drops to 177.6px at 1215px, a
      ~19% shrink from a 20px WIDER window).

      **S6 (operator decision, round 2):** narrowing the two floors this
      close (176px/150px, a 1.17x ratio) to keep S1's corrected range
      honest made Comfortable and Compact render IDENTICALLY — same column
      count, same tile width within 0.2px — across several sub-1400px
      bands (1024-1076, 1208-1238, ~1396px up). Accepted and documented
      (`theme.css`, this entry) rather than narrowing Compact further: a
      ~135px floor to force visible separation would sit 22% below the
      mockup's design size, with no measurement behind it.

      **S7 (round 2):** the round-1 B1 pin (`.empty{grid-column:1/-1}`) was
      class-specific — generalised into a static-analysis test scanning
      `library.js` for every class appended as a direct child of `.grid`
      (excluding `buildCard`'s "card" output, the grid's actual content)
      and requiring `grid-column` on each; mutation-verified against a
      synthetic new `libnotice` class the same way the reviewer's own
      reproduction worked. Also (round 2): B1's fix is a CORRECTION, not a
      new divergence — the frozen mockup already applies
      `style="grid-column:1/-1"` inline to this exact element
      (`docs/design/vault-app-mockup.html:1826`); the WP 4a.3 port had
      dropped it. And the fix's own call-site inventory was corrected: ONE
      `emptyMessage()` caller in `downloads.js` (not two), plus
      `settings.js:553`'s loading state, previously omitted entirely.

      Nitpicks closed: the hidden-toggle lint's three attribute regexes
      gained the `i` flag (case-insensitive, round 1) AND now also
      recognise `el.hidden ||=`/`??=` and `el.toggleAttribute?.(`
      (optional chaining, round 2 — both one character from an idiom
      already covered, both probed and found surviving), with
      `Object.assign(el, {hidden:true})` documented as a genuine, unclosed
      gap rather than implied covered; `LAYOUT_LABEL[key]` is now the
      single source `segButton` reads its title/aria-label from; the
      redundant `.grid.cols3 .meta .size{font-size:11px}` reset line was
      removed; the BP-XL comment's "1656px is 1920's natural column width"
      corrected to name it as the CONTENT width (the column box itself is
      1688px). Suite 462 green (451 WP 4e.1 baseline + 5 round-1 first pass
      + 4 round-1 fix-round pins + 1 round-2 S7 test + 1 round-2
      compound-operator/optional-chaining test). Rebased cleanly onto
      `da7ceae` (WP 4h.1, api/-only) mid-review with no conflicts in `web/`.
      Re-verified live against the demo 400-game fixture with the 176/150
      values: six-width table (390/768/1280/1440/1920/2560) for both
      densities (e.g. 1920px: 8 cols/194.6px Comfortable, 10 cols/153.3px
      Compact; 2560px: 10 cols/186px Comfortable, 12 cols/153px Compact),
      `.bulk` exact-pixel alignment reconfirmed, BP-M (768px) and base
      (390px) still byte-identical to the pre-4e.2/pre-4e.1 baselines. 400-card
      full grid rebuild measured 21-27ms (WP 4e.1 baseline: ~29-33ms — no
      regression).)*
- [x] The rail narrower, and earning its width: `--rail-w` 232px -> 180px
      (operator verdict at ~2000px, live: "232px feels unnecessarily large
      for the three things in it"; asked whether to narrow or add content,
      the operator chose both), plus vault name (rail head) and a cache
      used/free summary (rail foot) so the rail carries more than the three
      nav items — inserted ahead of WP 4e.3/4e.4/4e.5 in the numbering, at
      the operator's request
      *(WP 4e.6 done 2026-08-18: `--rail-w` narrowed to a live-measured
      180px (theme.css) — a `.nav-btn`'s own content box is 135px
      (x=22-157; corrected from a first-pass 136px/169px that omitted
      `.nav`'s `border-right:1px`, Opus review nitpick), the longest label
      ("Downloads") spanning x=55-120.5 (65.5px); since `.nav-pip` always
      sits flush against the 157px edge, the figure that matters is the
      label-to-pip gap — 21.5px for a one-digit pip, 17.4px for the worst
      reachable case (a two-digit "20"; three digits is impossible,
      `jobs(limit=20)`) — no wrap, no overlap either way; the freed 52px
      flows into `--w-wall` through the EXISTING WP 4e.2 formulas with no
      change needed to them (`.bulk`'s
      Δleft/Δwidth measured exactly 0/0 at 1024/1920/2560px in multi-select,
      confirming they are genuinely parametric on `--rail-w`). Vault name
      (`#rail-vault-name`) via a one-time, gated `GET /v1/settings` fetch
      (`components/rail-panel.js`, mirrors `onboarding.js`'s reconnect-path
      gating); cache used/free (`#rail-cache`) via a FOURTH `store.js`
      resource loop added by this WP — the brief's own claim that
      `GET /v1/cache/summary` was "already polled" was false as of `git
      log` (zero call sites for `api.cacheSummary()` anywhere in `web/js/`
      before this WP), corrected in `store.js`'s header rather than left
      wrong; the new loop has no `keyFn`/diff (a single snapshot, not a
      list — no notification event either, since the Downloads nav pip
      already carries queue state). Both figures distinguish "unknown"
      from a GENUINE zero (`formatBytesGBOrZero`, a new sibling of the
      tile-badge's `formatBytesGB` with the OPPOSITE zero-handling rule —
      "0 B free" must render, not hide as "not known yet"), and a failed
      poll leaves the last successful render on screen rather than
      blanking it (same convention as `bypass-banner.js`'s "clients"
      subscription; live-verified end to end by monkey-patching
      `api.cacheSummary` to reject and confirming byte-identical text
      before/after). **Coordinator addition mid-WP:** a third rail-foot
      line reads `GET /v1/settings`'s confirmed top-level `server_version`
      string (a parallel `api/` package, WP 4e.7, landed the field while
      this WP was in flight) — absent/non-string/empty are all "render
      nothing", rendered as `v<value>` when present, clamped to 24 chars
      before the prefix; `demo-data.js`'s settings fixture gained the same
      field (`"0.1.0"`, matching `vault_api/__init__.py`'s real
      `__version__`) for 1:1 demo parity. Both new rail elements are
      `display:none` below BP-L unconditionally (load-bearing, not
      cosmetic — `.nav` is a 3-column CSS grid there, so two more in-flow
      children would auto-place into a second implicit row without the
      override); base (<720px) confirmed byte-unaffected at 375px/719px.
      All new content is plain, non-interactive text — no new focusable
      control, so "nothing reachable only by hover" holds trivially, and
      `aria-current`/keyboard reachability on the three nav buttons are
      untouched. First pass: suite 498 green (462 baseline + 36).

      **Opus review: PASS, no blockers — ten mutations died by name incl.
      three the reviewer added itself; four should-fixes, all addressed,
      suite 515 green.** **S1:** WP 4e.2's `--w-wall:1600px` comment claimed
      1600px was unreachable at BP-L — true at 232px, false at 180px (the
      cap now genuinely binds at BP-L's own top end, measured: `.view-root`
      1600px capped at 1799px vs. 1599px uncapped at 1794px, ~4px visual
      cost) — exactly the "future `--rail-w` change" scenario that comment's
      own guard warned about, which this WP triggered without anyone
      updating the sentence; fixed in the comment plus a new structural pin
      computing the same breakeven from the live tokens. **S2:** the cache
      loop's cadence (`intervals.gamesMs`) was completely unpinned —
      mutating it to `jobsFastMs` (7.5x more frequent against the one
      endpoint that can trigger a cold depot walk) survived all 498 prior
      tests; fixed with a live-timing pin (developing it surfaced a REAL
      hang — a failing assertion skipping `store.stop()` left a 50s timer
      alive past a 120s harness timeout — fixed with `try`/`finally`).
      **S3:** `rail-panel.js` had zero test coverage; deleting the
      poll-failure guard (blanks the rail instead of keeping the last real
      number) and the `freeText` guard (renders a literal `"Free null"`)
      both passed the full suite. Fixed by refactoring `rail-panel.js` into
      a dependency-injected `createRailPanel()` — the same `store.js`/
      `store-singleton.js` split, applied to a component for the first time
      (`app.js` now makes the real wiring call) — tested headlessly in new
      `rail-panel-wiring.test.js` (15 tests) via `fake-dom.js`. **S4:**
      "render nothing, never a placeholder" only covered the TEXT, not the
      `.rail-head`/`.rail-foot` containers — an empty box with a bare
      divider showed on a default install. Fixed: both ARE now
      `[hidden]`-toggled (guarded in app.css's BP-L block against the
      `display:block` override, same fix class as `.btn[hidden]`), with
      `.rail-foot` hidden only when BOTH the cache summary and the version
      line have nothing to show — pinned as its own AND-not-OR test, and
      live-verified in a real browser (`display:none`, zero height, when
      force-hidden). Nitpicks: the rail-geometry numbers above corrected
      (135px/157px/21.5px, not 136px/169px/12px); the `--tile-min` overshoot
      band's low end restated as exactly 176px (the token's own `minmax()`
      floor, not a third swept approximation); a stale "paints before
      subscribing" claim corrected to the actual order. Divergence recorded
      as D-12 (`docs/WORKPACKAGES.md`) — content beyond what D-1 already
      covers for the rail's mere existence, since the frozen mockup has no
      rail at all.)*
- [x] Overlay geometry at BP-L (detail sheet / notifications / clients sheet
      sizing and placement once the shell is no longer phone-width) — WP 4e.3,
      2026-08-18: centred card (680px via --w-sheet-l, operator-approved range
      680-760) + right drawer at BP-L, mobile byte-unchanged; Opus review
      round 1 FAIL (confirm dialogs -90px off-axis, found by headless-Chrome
      measurement), round 2 PASS (offset 0.0px at three widths); D-13 in
      docs/WORKPACKAGES.md
- [x] Downloads/Settings-specific desktop layout — WP 4e.5, 2026-08-19:
      both views become a capped, centred 760px reading column at BP-L
      (existing --w-text token, no new magic number); multi-column and a
      control/help hybrid considered and rejected with measured reasons
      (D-15 in docs/WORKPACKAGES.md — the 178-char holdnote used to render
      as ONE line across 1931px at 2560). Opus review PASS, centring
      measured 0.0px off the content axis at four widths. PHASE 4e
      COMPLETE with this package.
- [x] Pointer/keyboard interaction model pass (hover states, focus order)
      — WP 4e.4, 2026-08-19: 11 hover rules gated in place behind
      (hover:hover) and (pointer:fine), clipped focus rings fixed via
      outline-offset, desktop scrollbar affordances, bulk bar removed from
      the hidden tab order. Opus round 1 FAIL (relocating the hover rules
      inverted the cascade for five equal-specificity state rules, found by
      live measurement), round 2 PASS (all five re-measured as holding).
      Roving tabindex deferred with honest costs — D-14 in
      docs/WORKPACKAGES.md
      beyond the one rail hover affordance WP 4e.1 added
- [x] **4e.7 (api)** — the server's own version, reported once (`GET
      /v1/settings`) and pinned against drift, so the new left rail can show
      what is actually running rather than a frontend-side constant that
      would only state what the UI believes, not what is running
      (`VAULT_WEB_DIR` can point at a different `web/` than the image
      shipped, so the two really can diverge)
      *(WP 4e.7 done: `vault_api.__version__` (`api/vault_api/__init__.py`)
      is the one hand-maintained constant — no env var, no git describe (no
      `.git` in the image) — now also used for `FastAPI(version=...)` in
      `main.py` instead of a second hardcoded `"0.1.0"` literal;
      `GET /v1/settings` gained a top-level `server_version` field (string),
      a sibling of `readonly`, deliberately NOT a `settings` row (not
      settable, no source/applies semantics) — `PATCH` rejects it exactly
      like any unrecognised key (`422`, generic detail, not the env-only
      one) since it lives in neither `OVERRIDABLE_SPECS` nor
      `ENV_ONLY_KEYS`; deliberately NOT mirrored onto `GET /v1/health`
      (the one unauthenticated route, fixed body by design — a version
      there is free fingerprinting), pinned with a byte-for-byte response-
      text assertion so a future addition trips an assertion, not a review
      comment. Anti-drift pin (`api/tests/test_version_pin.py`) derives
      `vault_api.__version__` and `deploy/compose.yaml`'s three
      `${VAULT_IMAGE_TAG:-0.1.0}` image-tag defaults independently and
      compares them, no Docker/network required; mutation-verified in both
      directions plus a narrower internal-consistency pin catching a
      partial hand-edit across the three `image:` lines. `api/README.md`
      documents the field and states plainly that the number is
      hand-maintained and means "the code in this image", not a published
      release (no release tags exist yet; the publish PATH is now built —
      WP CI-3, 2026-08-22 — but has never executed, so "nothing published"
      remains true until the first tag).
      **Opus review round 1: FAIL — one blocker (B1), two should-fixes,
      three nitpicks, all fixed same day.** B1: the first version's pin
      covered only `deploy/compose.yaml`, while THREE more hand-maintained
      copies of the same number existed unpinned — `api/Dockerfile`'s
      `org.opencontainers.image.version` LABEL (a build-time literal that
      cannot read a Python module, so it can never be collapsed into
      `__version__` itself), `deploy/tests/verify-stack.sh`'s
      `TAG=${VAULT_IMAGE_TAG:-...}`, and `deploy/.env.example`'s example
      line — plus `api/Dockerfile`'s own free-text `docker build` comment,
      fixed by removing the literal instead of pinning a comment. All
      three real sites now have their own named, mutation-verified (both
      directions) pin; two more copies (`core/Dockerfile`,
      `dns/Dockerfile`) stay explicitly OUT of this pin's reach per this
      WP's `api/`+`deploy/`-only footprint and are named as a real,
      documented gap rather than omitted — see `test_version_pin.py`'s
      module docstring for the full table. **S1:** `main.py`'s
      `version=VAULT_API_VERSION` wiring had a comment-only guarantee —
      `version="9.9.9"` survived the full suite (bounded blast radius,
      confirmed empirically: `openapi_url=None` makes `GET /openapi.json`
      404, so the value is unobservable over HTTP either way) — closed
      with a direct `app.version == __version__` assertion through a real
      `create_app()` call, mutation-verified. **S2:** the `/v1/health`
      justification in both `routers/settings.py` and this README
      misattributed §10's "never expose vault-core/port 80" rule to
      vault-api itself; corrected to the real, STRONGER reason — §10's
      public-domain profile fronts vault-api (not vault-core) and can
      legitimately put `/v1/health` on the open internet, which is exactly
      why its body must stay minimal. Nitpicks: the compose precondition
      test now asserts the key SET directly instead of a self-referential
      dict comparison; every by-name lookup uses `.get(...)` so a mutated-
      away key reports a clear value mismatch instead of a bare
      `KeyError`; and this README no longer calls `VAULT_IMAGE_TAG` an
      "already-published" tag (nothing is published yet — it is a locally
      built image's tag). Suite 1617 passed, 1 skipped (baseline 1603/1)
      — 14 new tests.)*
