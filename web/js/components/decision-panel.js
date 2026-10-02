/**
 * The Phase 4h suggestions panel's DOM wiring (WP 4h.2).
 *
 * **Plan vs. brief, resolved (coordinator, 2026-08-19): `docs/PROJECT_PLAN.md`
 * :1867-1869 is the operator-ratified definition — a right-hand column at
 * BP-XL (>=1800px) AND a collapsible card below that width, ONE component
 * with two CSS presentations, never two independently-built surfaces.** This
 * file renders exactly one `<aside id="decision-panel">` (index.html); which
 * of the two presentations it LOOKS like is decided entirely by CSS — the
 * BP-L block (`min-width:1024px`, an explicit fourth grid row/area, Opus
 * review blocker B1 fix round) and the BP-XL block
 * (`min-width:1800px`, gated on `.app.has-decision-panel`) — see
 * `web/tests/decision-panel-layout.test.js` for the structural pins on that
 * split. This module never branches on viewport width itself, matching the
 * codebase's standing rule that layout is a CSS concern, not a JS one
 * (css/app.css's own header: "Layout is keyed on WIDTH only... a view must
 * still get the width-appropriate layout") — it DOES decide the ONE signal
 * (`.app`'s `has-decision-panel` class) that tells the CSS which
 * presentation applies once the viewport is wide enough to choose (see
 * "Visibility" below).
 *
 * **Testable via dependency injection**, same posture as
 * `components/rail-panel.js` (see that module's header for why: a factory
 * taking every real dependency as one options object lets
 * `web/tests/decision-panel-wiring.test.js` drive it with `fake-dom.js`
 * elements, a trivial fake store, fake `onViewChange`/`getCurrentView`, and a
 * fake `storage` object — no real `document`, no real `window.localStorage`,
 * no real router). Zero import-time side effects — `app.js` performs the one
 * real, side-effecting call.
 *
 * **Data source: only the existing "games" poll (store.js) — no new poll
 * loop.** WP 4e.6's own lesson (cited in this WP's brief) is "measure before
 * adding a fourth loop"; `GET /v1/games` already carries every
 * `manifest_change_frequency`/`manifest_observation_days`/
 * `manifest_days_since_last_change` field the frequency-only statement
 * families need, at the SAME cadence the Library view's own grid already
 * repaints from. Playtime (`playtimeByAppid`, `lib/decision-support.js`'s
 * `buildSuggestions`) has no wiring here at all. Since WP WEB-FEAT-1 a
 * persisted Steam identity DOES exist — the vault's `steam_library_steamid`
 * setting, which `views/library.js` uses to fetch `GET /v1/steam/owned-games`
 * on library open — but this panel's playtime path is NOT wired to it (that
 * list is never shared with this panel, and the relay only carries playtime
 * when the ADR-0010 gate is on); doing so is a separate, unscoped feature.
 * The panel therefore always operates in the "frequency" or "insufficient_data" tier in the real
 * product today; the "full" (playtime-inclusive) tier is real, tested code
 * in `lib/decision-support.js`, reachable today only from a caller that
 * already has a `playtimeByAppid` map (a future WP's job) — see the coder's
 * report for this stated as a limitation, not silently implied to be wired.
 *
 * **Visibility.** Library-view-only (the statements are about games; showing
 * this on Downloads/Settings has no mockup analogue and nothing to say).
 * Hidden whenever: not on the Library view, OR the user dismissed it
 * (persisted), OR there are currently zero games at all (nothing to react
 * to yet — distinct from "insufficient_data", which DOES render, with an
 * honest empty-state message, once there is at least one game to have an
 * opinion about).
 *
 * **Two separate signals, not one (Opus review should-fix S3, WP 4h.2 fix
 * round): the `<aside>`'s own `hidden` attribute vs. `#app`'s
 * `has-decision-panel` class.** `rootEl.hidden` is exactly the visibility
 * rule above — it governs whether the card/row renders AT ALL. `
 * has-decision-panel` is narrower: it governs only whether the BP-XL block
 * may reserve its real-cost, explicit-width right-hand COLUMN, keyed on
 * `buildSuggestions()`'s own `tier !== "insufficient_data"` (an empty
 * result never earns the column — see css/app.css's BP-XL comment for the
 * measured column-count cost this avoids). An empty result on a wide
 * viewport therefore still shows the honest "not enough history yet"
 * message — just as the BP-L block's plain, no-reservation ROW, never as a
 * dedicated sidebar for one static sentence.
 *
 * **Dismiss vs. collapse — two independent, separately-keyed localStorage
 * flags (brief's explicit instruction), same idiom `views/library.js` already
 * uses for its own layout preference (`steamvault.libraryLayout`).** Dismiss
 * is the stronger act (plan's binding privacy stance: "dismissible... at any
 * time") — it hides the panel in BOTH presentations and stays hidden across
 * reloads and poll ticks until localStorage is cleared; there is no re-enable
 * control in this package (a Settings toggle to bring it back is a natural,
 * separate follow-up, not silently promised here). Collapse only affects the
 * sub-BP-XL card presentation (BP-XL's right-hand column ignores it entirely,
 * per app.css) and defaults to COLLAPSED on a first visit, per the brief.
 *
 * **The "dismissed stays dismissed across a poll tick" pin** (precedent:
 * web/tests/bypass-banner.test.js) means something slightly different here
 * than for that in-memory, auto-clearing banner: this module's `dismissed`
 * flag is read from `storage` ONCE at construction and never re-read from a
 * "games" tick — a poll tick calls `render()`, and `render()` must respect
 * the in-memory `state.dismissed` it already has rather than re-deriving
 * anything from the tick that could accidentally un-dismiss it.
 */

import { buildSuggestions } from "../lib/decision-support.js";
import { fillMissingNames } from "../lib/owned-library.js";

const STORAGE_KEYS = Object.freeze({
  dismissed: "steamvault.decisionPanelDismissed",
  collapsed: "steamvault.decisionPanelCollapsed",
});

function readFlag(storage, key, fallback) {
  try {
    const v = storage.getItem(key);
    return v === null || v === undefined ? fallback : v === "1";
  } catch {
    return fallback; // same posture as api.js's localStorage helpers
  }
}

function writeFlag(storage, key, value) {
  try {
    storage.setItem(key, value ? "1" : "0");
  } catch {
    // Storage failure must never break the toggle itself, just its persistence.
  }
}

const INSUFFICIENT_DATA_TEXT =
  "Not enough history yet to suggest anything here — check back in a couple of weeks.";

/**
 * @param {{
 *   elements: {
 *     rootEl: object, bodyEl: object, collapseBtn: object, dismissBtn: object,
 *     appEl: object, createElement: (tag: string) => object,
 *   },
 *   store: { subscribe: Function, snapshot: Function },
 *   onViewChange: (handler: (view: string) => void) => (() => void),
 *   getCurrentView: () => string,
 *   storage: { getItem: Function, setItem: Function },
 *   suggestionsLimit?: number,
 * }} deps `createElement`/`storage` are both required (no `document`/
 *   `window` fallback inside this module — same DI posture as
 *   `components/rail-panel.js`'s `elements.createElement`, so this file has
 *   zero references to real globals and `decision-panel-wiring.test.js` can
 *   drive it with `fake-dom.js` plus a trivial in-memory storage fake).
 * @returns {{ render: Function }} `render` is exposed only for
 *   `decision-panel-wiring.test.js`; production code never calls it directly.
 */
export function createDecisionPanel({
  elements,
  store,
  onViewChange,
  getCurrentView,
  storage,
  suggestionsLimit = 5,
  // WP WEB-FIX-4: optional `{current, subscribe}` (owned-singleton.js's
  // ownedLibrary). Only fills missing names; never adds a game.
  ownedNames = null,
}) {
  const { rootEl, bodyEl, collapseBtn, dismissBtn, appEl, createElement } = elements;

  const state = {
    games: store.snapshot("games") || [],
    view: getCurrentView(),
    dismissed: readFlag(storage, STORAGE_KEYS.dismissed, false),
    collapsed: readFlag(storage, STORAGE_KEYS.collapsed, true),
  };

  function render() {
    const onLibrary = state.view === "library";
    const visible = onLibrary && !state.dismissed && state.games.length > 0;
    rootEl.hidden = !visible;
    if (!visible) {
      appEl.classList.remove("has-decision-panel");
      return;
    }

    // Computed BEFORE the has-decision-panel decision below (Opus review
    // should-fix S3, WP 4h.2 fix round) — `tier` previously had no
    // production caller at all (LEARNINGS' zero-caller class): the BP-XL
    // right-hand COLUMN reservation (an explicit `--panel-w`-wide grid
    // track, real cost — see css/app.css's BP-XL comment) used to be keyed
    // on plain visibility, so a fresh vault with nothing to say yet
    // (`tier === "insufficient_data"`, WP 4h.1's own ~14-day honesty
    // window) still reserved it to show one static sentence.
    const games = ownedNames ? fillMissingNames(state.games, ownedNames.current().games) : state.games;
    const suggestions = buildSuggestions(games, { limit: suggestionsLimit });
    const { items, tier } = suggestions;

    // `#app`'s own class, not `.decision-panel`'s — css/app.css's BP-XL
    // block keys the third grid column/track off THIS class so an EMPTY
    // panel never reserves that column (S3): only a real, non-empty result
    // gets the column; an empty one still shows (the message below) but as
    // the BP-L block's plain, no-extra-cost row instead.
    appEl.classList.toggle("has-decision-panel", tier !== "insufficient_data");

    rootEl.classList.toggle("collapsed", state.collapsed);
    collapseBtn.setAttribute("aria-expanded", String(!state.collapsed));
    // aria-expanded alone is not enough — a screen-reader user needs the
    // ACTION the button performs, which flips with the state (index.html's
    // static markup only seeds the initial "Expand suggestions" — this is
    // what keeps it honest after the first toggle).
    collapseBtn.setAttribute("aria-label", state.collapsed ? "Expand suggestions" : "Collapse suggestions");

    bodyEl.replaceChildren();
    if (items.length === 0) {
      const p = createElement("p");
      p.className = "dp-empty";
      p.textContent = INSUFFICIENT_DATA_TEXT;
      bodyEl.appendChild(p);
      return;
    }
    for (const item of items) {
      const row = createElement("p");
      row.className = "dp-row";
      const name = createElement("b");
      name.textContent = item.name;
      // A dedicated element for the rest of the line (not a raw string
      // passed to `.append()`, even though a real DOM would auto-wrap it in
      // a text node fine) — `fake-dom.js`'s `FakeElement.append`/
      // `appendChild` assign `node.parentNode` unconditionally, which
      // throws under strict mode for a bare string; keeping every appended
      // child a real element keeps this file's `.append()` calls testable
      // against that shim without special-casing text nodes there.
      const rest = createElement("span");
      rest.textContent = ` — ${item.text}`;
      row.append(name, rest);
      bodyEl.appendChild(row);
    }
  }

  collapseBtn.addEventListener("click", () => {
    state.collapsed = !state.collapsed;
    writeFlag(storage, STORAGE_KEYS.collapsed, state.collapsed);
    render();
  });

  dismissBtn.addEventListener("click", () => {
    state.dismissed = true;
    writeFlag(storage, STORAGE_KEYS.dismissed, true);
    render();
  });

  // "games" is the ONLY resource this module subscribes to — see the module
  // header for why no new poll loop was added. A `{error}` payload is
  // ignored (leaves the last real render on screen), matching every other
  // component's convention for a transient poll failure.
  store.subscribe("games", ({ items }) => {
    if (!Array.isArray(items)) return;
    state.games = items;
    render();
  });

  onViewChange((view) => {
    state.view = view;
    render();
  });

  if (ownedNames) ownedNames.subscribe(() => render());

  render(); // paint immediately from whatever snapshot/view already exists
  return { render };
}
