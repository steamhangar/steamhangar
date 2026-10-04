/**
 * Library card component (WP 4a.3).
 *
 * Builds one card for any of the three layouts (grid2/grid3/list — CSS
 * alone decides what's visible, see css/app.css "library layouts"; this
 * module emits ONE DOM shape for all three, exactly like the mockup's "one
 * render path... only the container class changes"). DOM-only: no fetch, no
 * store access, no polling — library.js owns state and passes plain data +
 * callbacks in. Not unit-tested the same way as the lib/ pure modules
 * (mirrors the existing split in this codebase: status-icon.js's DOM
 * builder isn't unit-tested either, only the glyph/label tables feeding
 * it) — this WP's headless coverage targets the decision logic
 * (game-status.js, library-filters.js, bulk-plan.js, multiplan.js,
 * cover-art.js, format.js).
 *
 * Round-7 rule (docs/design/vault-app-mockup-NOTES.md): `data-dk` on the
 * card is the STRUCTURAL signature. `views/library.js` reads it before
 * touching a card on a jobs poll tick and only rebuilds (calls buildCard
 * again) when it no longer matches the freshly computed `dispKind` — never
 * on an unrelated field change (e.g. a running job's `log_excerpt`
 * growing). This component has nothing to patch in place beyond that
 * signature check today (see game-status.js's module header, Divergence 2:
 * the real API has no live progress number the mockup's `patchGridProgress`
 * used to patch) — `cardStructuralKey` is exported so library.js's tick
 * handler and this file can never disagree about what "structural" means.
 *
 * **Capsule pill ground (WP WEB-FIX-9).** The pill's dark ground is drawn
 * only when it carries a number (cached game); icon-only pills get the
 * `bare` class (see `pillClassName`) and show just the status disc. The
 * status word lives in the meta row under the cover, never in the pill.
 *
 * **Nested-interactive-widget a11y (WP 4a.3 review nit, closed WP 4a.8).**
 * The card is `role="button"` yet contains two REAL nested `<button>`s (the
 * capsule pill and the meta-row icon, both only present when `statusAction`
 * returns something) — an ARIA authoring-practices grey area (a "button"
 * widget is conventionally a leaf). The fix shipped here is NOT removing
 * the nesting (the pill/icon must stay independently focusable and
 * operable — a screen-reader user needs to reach "download this one game"
 * without opening the detail sheet first) but making the OUTER card's
 * accessible NAME explicit (`aria-label`, set below) instead of
 * name-from-content: without it, focusing the card would concatenate the
 * cover's empty alt, the pill's own aria-label (when a button), `.name`,
 * the list-layout `.rowname` DUPLICATE of the same text, the meta icon's
 * sr-only word, the visible state word and the size — a garbled, doubled
 * announcement having nothing to do with nested buttons specifically. An
 * explicit `aria-label` makes accname computation skip content entirely, so
 * the card reads as one clean "Name — Status, Size" and the nested buttons
 * remain separately reachable/announced on their own terms when tabbed to
 * directly (activating them already `stopPropagation()`s so they never also
 * fire the card's `onOpen` — see the interaction wiring below). Redundant
 * icon sr-only labels next to an already-visible word are hidden via
 * `aria-hidden` for the same "avoid double announcement" reason
 * `components/clients-sheet.js`/`components/notifications.js` already
 * apply to their own status icons. Verified live with a screen reader
 * (see the WP 4a.8 coder's report); not unit-tested (DOM-building
 * component, see this file's own long-standing header note above).
 */

import { createStatusIcon, STATUS_LABEL } from "./status-icon.js";
import {
  KIND,
  dispKind,
  statusAction,
  installedBadgeState,
  installedOnSummary,
  installedBadgeText,
  installedBadgeCompactText,
  INSTALLED_BADGE,
  isToolApp,
  statusIconKind,
  statusWordFor,
  gameDisplayName,
} from "../lib/game-status.js";
import { formatBytesGB } from "../lib/format.js";
import { coverArtUrl, fallbackHues, fallbackPattern } from "../lib/cover-art.js";

/** The exact string library.js's `GET /v1/games` tick handler
 * (js/lib/render-plan.js's `planGamesUpdate`) compares against a live
 * card's `data-dk` to decide whether that appid can be PATCHED in place or
 * needs a full rebuild (round-7 rule — see render-plan.js's header). */
export function cardStructuralKey(game, liveJob) {
  return dispKind(game, liveJob);
}

function pillNumberText(game, kind) {
  // Honest per game-status.js's Divergence 2: no fabricated live percentage
  // for running/paused — only a genuinely cached game prints a number.
  if (kind !== "cached") return null;
  return formatBytesGB(game.size_bytes);
}

/** Installed-badge class for the `.instbadge` span (WP AG-2, css/app.css) —
 * `.warn` is the "installed but not cached" case this whole feature exists
 * for; the plain class is purely informational. */
function installedTagClass(state) {
  return state === INSTALLED_BADGE.NOT_CACHED ? "instbadge warn" : "instbadge";
}

/**
 * Build the `.instbadge`'s two children (review S3 fix): a `.ibfull` span
 * with {@link installedBadgeText}'s full wording and an `.ibcompact` span
 * with {@link installedBadgeCompactText}'s shorter wording — CSS (not this
 * module, per its own "CSS alone decides what's visible" header rule)
 * toggles which one is actually shown, `.ibcompact` only in `.grid.cols3`.
 * Review measurement: the full string never fit the cols3 card at all
 * (149.8px against the space available), so the one fact the badge exists
 * to show — WHICH client — never rendered; the compact form exists
 * specifically so cols3 keeps that fact instead of an ellipsis.
 * @param {string} state one of INSTALLED_BADGE's values (never NONE — the
 *   caller never builds this for the empty case).
 * @param {string} summary from installedOnSummary
 * @returns {HTMLElement[]}
 */
function buildInstalledTagChildren(state, summary) {
  const full = document.createElement("span");
  full.className = "ibfull";
  full.textContent = installedBadgeText(state, summary);
  const compact = document.createElement("span");
  compact.className = "ibcompact";
  compact.textContent = installedBadgeCompactText(state, summary);
  return [full, compact];
}

/**
 * Create/update/remove the `.instbadge` child of a card's `.meta` row so it
 * always matches `game.installed_on`'s current state — called from BOTH
 * `buildCard` (first paint) and `patchCardVolatile` (a poll tick that only
 * this field changed, e.g. an agent's report going stale or fresh, without
 * `dispKind` itself changing). This is the ONE documented exception to
 * `patchCardVolatile`'s own "no node is created, removed or replaced" rule
 * (see that function's docstring) — a plain text badge with no icon and no
 * CSS animation, so creating/removing/replacing it here on a patch tick
 * cannot touch the status-icon `<svg>` subtree that rule exists to protect.
 * @param {HTMLElement} metaEl the card's `.meta` row (mutated).
 * @param {object} game GameSummary
 */
function syncInstalledTag(metaEl, game) {
  const state = installedBadgeState(game);
  const existing = metaEl.querySelector(".instbadge");
  if (state === INSTALLED_BADGE.NONE) {
    if (existing) existing.remove();
    return;
  }
  const summary = installedOnSummary(game.installed_on);
  const [full, compact] = buildInstalledTagChildren(state, summary);
  if (existing) {
    existing.className = installedTagClass(state);
    existing.replaceChildren(full, compact);
  } else {
    const tag = document.createElement("span");
    tag.className = installedTagClass(state);
    tag.append(full, compact);
    metaEl.appendChild(tag);
  }
}

/**
 * Patch the VOLATILE text on an already-built card in place. The
 * status-icon `<svg>` subtree (and any CSS animation running on it) is
 * left completely untouched — that is the invariant this function exists
 * to protect, and the reason it exists at all. The ONE documented
 * exception (WP AG-2 review): `syncInstalledTag` below MAY create/remove/
 * replace the `.instbadge` span itself, since that is a plain text node
 * with no icon and no animation — see its own docstring for why that
 * cannot touch the protected subtree. This is the round-7 counterpart to
 * `buildCard`: called only when `render-plan.js`'s `planGamesUpdate` has
 * already established the game's STRUCTURAL key (`cardStructuralKey`) has
 * NOT changed — the caller (views/library.js) must never call this for a
 * structural transition, only for a same-kind update (e.g. `size_bytes`
 * drifting while a download runs, per the games-poll cadence).
 *
 * @param {HTMLElement} cardEl the existing `.card` element (mutated).
 * @param {object} game the game's freshest data.
 * @param {string} kind this card's (unchanged) structural key — passed in
 *   rather than recomputed so the caller's own structural-key check is the
 *   single source of truth for "did anything shape-relevant move".
 */
export function patchCardVolatile(cardEl, game, kind) {
  const pill = cardEl.querySelector(".cappill");
  const newPillNum = pillNumberText(game, kind);
  if (pill) {
    let pv = pill.querySelector(".pv");
    if (newPillNum) {
      if (!pv) {
        pv = document.createElement("span");
        pv.className = "pv";
        pill.appendChild(pv);
      }
      pv.textContent = newPillNum;
    } else if (pv) {
      pv.remove();
    }
    // WP WEB-FIX-9: the ground follows the number (see pillClassName).
    pill.classList.toggle("bare", !newPillNum);
  }
  const sizeEl = cardEl.querySelector(".meta .size");
  if (sizeEl) sizeEl.textContent = formatBytesGB(game.size_bytes) || "—";
  const metaEl = cardEl.querySelector(".meta");
  if (metaEl) syncInstalledTag(metaEl, game);
  // Keep the explicit accessible name (module header) in sync too — `kind`
  // is unchanged by definition on the patch path (round-7 rule), but the
  // SIZE this same tick just wrote above is part of the announced name and
  // would otherwise go stale until the next structural rebuild.
  cardEl.setAttribute("aria-label", cardAccessibleLabel(game, kind));
}

function buildCover(game) {
  const { h1, h2 } = fallbackHues(game.appid);
  const pattern = fallbackPattern(game.appid);
  const cap = document.createElement("div");
  cap.className = `cap p${pattern}`;
  cap.style.setProperty("--h1", String(h1));
  cap.style.setProperty("--h2", String(h2));

  const art = document.createElement("div");
  art.className = "art";
  cap.appendChild(art);

  // Real Steam CDN artwork, layered over the procedural fallback above. On
  // any load failure (offline LAN, unknown appid, blocked host) it is
  // simply removed, leaving the styled fallback tile + game name visible —
  // never a broken-image icon or a blank rectangle.
  const img = document.createElement("img");
  img.className = "cover";
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.addEventListener("error", () => img.remove(), { once: true });
  img.src = coverArtUrl(game.appid);
  cap.appendChild(img);

  const scrim = document.createElement("div");
  scrim.className = "scrim";
  cap.appendChild(scrim);

  return cap;
}

function buildIcon(kind, { action, gameName }) {
  const icon = createStatusIcon(statusIconKind(kind));
  if (!action) {
    // No action -> a plain, non-focusable span (see createStatusIcon). The
    // meta row always shows the SAME status word right next to this icon
    // (the `.state` span in buildCard below) — mark the icon aria-hidden so
    // its own built-in sr-only label is not announced a second time (same
    // "avoid double announcement" posture as clients-sheet.js/
    // notifications.js's status icons).
    icon.setAttribute("aria-hidden", "true");
    return icon;
  }
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "icnact";
  btn.title = action.title;
  btn.setAttribute("aria-label", `${action.title} — ${gameName}`);
  btn.appendChild(icon);
  return btn;
}

/**
 * The pill's class list (WP WEB-FIX-9). The dark translucent ground exists
 * to carry the NUMBER legibly over any cover art (mockup round 6: "status
 * icon + the number ... over a dark translucent ground"). With no number
 * to print (every kind but a cached game, Divergence 2) that ground
 * collapsed to a small grey lozenge around the icon with an empty right
 * end, read live as "a grey ring with no status word" (user feedback on
 * rc9, 2026-10-04). Icon-only pills are therefore `bare`: the status disc
 * stands on the cover by itself (css/app.css `.cappill.bare`). The status
 * WORD stays in the meta row under the cover, not in the pill: the round-6
 * rule is that nothing on the card is said twice.
 * @param {boolean} wantsButton
 * @param {string|null} num pillNumberText's result
 * @returns {string}
 */
export function pillClassName(wantsButton, num) {
  return "cappill" + (wantsButton ? " act" : "") + (num ? "" : " bare");
}

function buildPill(game, kind, action) {
  const wantsButton = !!action;
  const pill = document.createElement(wantsButton ? "button" : "span");
  pill.className = pillClassName(wantsButton, pillNumberText(game, kind));
  if (wantsButton) {
    pill.type = "button";
    pill.title = action.title;
    pill.setAttribute("aria-label", `${action.title} — ${displayName(game)}`);
  }
  const icon = createStatusIcon(statusIconKind(kind));
  // The pill's icon is always redundant with the meta row's visible status
  // word on the SAME card (round 6 gave every layout that word back) — hide
  // it from assistive tech whether or not the pill itself is a button: a
  // button's own aria-label already wins for ITS name, but the icon's
  // sr-only text is still a distinct node a linear/browse-mode read would
  // otherwise announce a second (or third) time.
  icon.setAttribute("aria-hidden", "true");
  pill.appendChild(icon);
  const num = pillNumberText(game, kind);
  if (num) {
    const pv = document.createElement("span");
    pv.className = "pv";
    pv.textContent = num;
    pill.appendChild(pv);
  }
  return pill;
}

/**
 * A NAME FALLBACK BUG found live against a real vault-api during this WP's
 * e2e pass (WP 4a.8): `GameSummary.name` is genuinely `null` for an app
 * vault-api has never resolved a display name for (no Steam identity linked
 * — api/vault_api/routers/games.py; the demo-mode fixtures always seed a
 * name, so this state was never exercised until testing against a real,
 * un-Steam-linked server). Before this fix, a card for such a game rendered
 * with a completely BLANK title (`.textContent = null` coerces to `""`) and
 * — once `cardAccessibleLabel` below started building an explicit
 * `aria-label` — an aria-label reading a bare `" — Failed"` with no
 * identifying text at all. `components/game-detail-sheet.js` and
 * `views/downloads.js`'s `nameFor` already fall back to `App {appid}` for
 * exactly this case; this was the one remaining place in the app that did
 * not. Verified live: reloading against the running instance now shows
 * "App 440" instead of a blank title/name row.
 *
 * Exported (review fix, WP 4a.8 cycle 2): the live e2e pass exercised this
 * through the running app, but nothing pinned it as a regression test —
 * `web/tests/game-card.test.js` covers it directly against the fake DOM.
 */
export function displayName(game) {
  // WP API-FIX-4: lib/game-status.js's gameDisplayName adds the tool app's
  // server-supplied name ("Steamworks Common Redistributables") between the
  // vault name and the `App {appid}` fallback.
  return gameDisplayName(game);
}

/**
 * The installed-badge fragment for the card's accessible name (WP AG-2
 * review nitpick): {@link installedBadgeText}'s NOT_CACHED wording restates
 * "not cached", which duplicates `STATUS_LABEL.none` ("Not cached") when
 * `dispKind` is itself `"none"` — the only `dispKind` the NOT_CACHED badge
 * can co-occur with that ALSO says "not cached" on its own (`dispKind`'s
 * other possible co-occurrences — running/paused/error — each say something
 * that does not mention cache state at all, so no restatement risk there).
 * In that one case this fragment drops the "but not cached" half, since the
 * status word right before it in the joined label already said it.
 * @param {object} game
 * @param {string} kind this card's dispKind
 * @returns {string | null}
 */
function installedAriaFragment(game, kind) {
  const state = installedBadgeState(game);
  if (state === INSTALLED_BADGE.NONE) return null;
  const summary = installedOnSummary(game.installed_on);
  if (state === INSTALLED_BADGE.NOT_CACHED && kind === KIND.NONE) {
    return `Installed on ${summary}`;
  }
  return installedBadgeText(state, summary);
}

/** The card's explicit accessible name (module header, "Nested-interactive-
 * widget a11y") — game name, status word, and size when there is one to
 * report (mirrors the visible `.meta` row's own size fallback: "—" is a
 * visual placeholder, not spoken). Recomputed by `patchCardVolatile` too,
 * since a size-only tick (no structural change) would otherwise leave a
 * stale byte count in the announced name. */
function cardAccessibleLabel(game, kind) {
  const parts = [displayName(game), statusWordFor(kind, STATUS_LABEL)];
  const sizeText = formatBytesGB(game.size_bytes);
  if (sizeText) parts.push(sizeText);
  const installedText = installedAriaFragment(game, kind);
  if (installedText) parts.push(installedText);
  return parts.join(" — ");
}

/**
 * @param {object} game GameSummary
 * @param {{
 *   liveJob?: object,
 *   picked: boolean,
 *   selecting: boolean,
 *   onOpen: (appid: number) => void,
 *   onLongPress: (appid: number) => void,
 *   onToggle: (appid: number) => void,
 *   onAction: (appid: number, actionType: string) => void,
 * }} ctx
 * @returns {HTMLElement}
 */
export function buildCard(game, ctx) {
  const { liveJob, picked, selecting, onOpen, onLongPress, onToggle, onAction } = ctx;
  const kind = dispKind(game, liveJob);
  const action = statusAction(game, liveJob, selecting);

  const card = document.createElement("div");
  // WP API-FIX-4: a Steam tool app is shown muted (css/app.css `.card.tool`),
  // not hidden — it is real and installed, just never downloaded on its own.
  card.className = "card" + (picked ? " picked" : "") + (isToolApp(game) ? " tool" : "");
  card.dataset.appid = String(game.appid);
  card.dataset.dk = kind;
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  card.setAttribute("aria-label", cardAccessibleLabel(game, kind));
  // In multi-select mode the card's default action is "toggle selection",
  // not "open" — aria-pressed makes that a stated TOGGLE state instead of a
  // silent visual-only "picked" class (mirrors the `.pick` checkmark, which
  // stays aria-hidden since this is the accessible equivalent of it).
  if (selecting) card.setAttribute("aria-pressed", String(picked));

  card.appendChild(buildCover(game));
  const cap = card.firstChild;
  cap.appendChild(buildPill(game, kind, action)); // z-index:2 in CSS, so DOM order doesn't matter

  const name = document.createElement("div");
  name.className = "name";
  name.textContent = displayName(game);
  cap.appendChild(name);

  const pick = document.createElement("span");
  pick.className = "pick";
  pick.setAttribute("aria-hidden", "true");
  const pickSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  pickSvg.setAttribute("width", "12");
  pickSvg.setAttribute("height", "12");
  pickSvg.setAttribute("viewBox", "0 0 20 20");
  pickSvg.setAttribute("fill", "none");
  pickSvg.setAttribute("stroke", "currentColor");
  pickSvg.setAttribute("stroke-width", "3");
  const pickPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  pickPath.setAttribute("d", "m4 10.6 4 4L16.4 5.6");
  pickSvg.appendChild(pickPath);
  pick.appendChild(pickSvg);
  card.appendChild(pick);

  const rowname = document.createElement("span");
  rowname.className = "rowname";
  rowname.textContent = displayName(game);
  card.appendChild(rowname);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.appendChild(buildIcon(kind, { action, gameName: displayName(game) }));
  const state = document.createElement("span");
  state.className = "state tx-" + kind;
  state.textContent = statusWordFor(kind, STATUS_LABEL);
  meta.appendChild(state);
  // Unlike the pill (which omits the number entirely rather than fabricate
  // one — see pillNumberText), the list/meta size column always shows
  // SOMETHING, using an explicit "—" for "nothing honest to print" (mockup
  // parity: `gb(null)` returns "—" here, whereas `pillHTML`'s own
  // `num?...:""` check is what actually hides it on the cover).
  const size = document.createElement("span");
  size.className = "size";
  size.textContent = formatBytesGB(game.size_bytes) || "—";
  meta.appendChild(size);
  syncInstalledTag(meta, game); // WP AG-2: appends `.instbadge` iff installed_on is non-empty
  card.appendChild(meta);

  // ---- interaction wiring (mockup parity: long-press / right-click /
  // header select icon -> multi-select; a tap toggles selection while
  // selecting, otherwise it's a plain open — WP 4a.4 supplies onOpen's
  // real behaviour, this WP wires the callback but library.js's onOpen is
  // a no-op for now, see views/library.js). ----
  let pressTimer = null;
  let longFired = false;
  const clearPress = () => {
    if (pressTimer !== null) clearTimeout(pressTimer);
    pressTimer = null;
  };
  const startPress = () => {
    longFired = false;
    clearPress();
    pressTimer = setTimeout(() => {
      longFired = true;
      onLongPress(game.appid);
    }, 420);
  };
  card.addEventListener("mousedown", startPress);
  card.addEventListener("touchstart", startPress, { passive: true });
  for (const ev of ["mouseup", "mouseleave", "touchend", "touchmove", "touchcancel"]) {
    card.addEventListener(ev, clearPress);
  }
  card.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    onLongPress(game.appid);
  });
  card.addEventListener("click", () => {
    if (longFired) {
      longFired = false;
      return;
    }
    if (selecting) onToggle(game.appid);
    else onOpen(game.appid);
  });
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (selecting) onToggle(game.appid);
      else onOpen(game.appid);
    }
  });

  if (action) {
    // The pill/meta-icon action buttons must never let their click reach
    // the card (which would open the detail view / toggle selection on
    // top of firing the action) — mockup parity
    // (`onclick="event.stopPropagation();..."`).
    for (const el of card.querySelectorAll("button.cappill, button.icnact")) {
      el.addEventListener("mousedown", (e) => e.stopPropagation());
      el.addEventListener("touchstart", (e) => e.stopPropagation(), { passive: true });
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        onAction(game.appid, action.type);
      });
    }
  }

  return card;
}
