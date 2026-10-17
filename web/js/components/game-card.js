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
 * **Card anatomy (WP LIBRARY-2, docs/design/rc20/library.html).** The cover
 * carries only the title and a size badge (`.sz`, a cached game's size;
 * superseding the WEB-FIX-9 status pill and the "Never played" cover pill).
 * Under the cover, `.meta` holds the status line `.stt` (a tone dot and the
 * sentence-case status word, plus the card's quick action as a small
 * status-icon button `.icnact` when `statusAction` offers one) and the
 * sub-lines (`.pkgline`, `.updbadge`, `.instbadge`, `.playbadge`); the
 * Compact layout hides the sub-lines, the List layout lays `.meta` out as
 * columns (css/kit.css, Library block).
 *
 * **Nested-interactive-widget a11y (WP 4a.3 review nit, closed WP 4a.8).**
 * The card is `role="button"` yet contains a REAL nested `<button>` (the
 * status line's action icon, only present when `statusAction` returns
 * something; LIBRARY-2 dropped the cover pill that used to be a second
 * one) — an ARIA authoring-practices grey area (a "button"
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
 *
 * **"Update available" state (WP UPD-6, supersedes the UPD-2 badge on
 * cached cards).** `game.update_available === true` (WP UPD-1: a stale
 * oracle verdict on a game with cache content; a group row ORs it over its
 * members) turns a CACHED card's status into "Update available": the status
 * dot turns orange instead of green, and the status word reads "Update available"
 * in `--stale` (lib/game-status.js `showsUpdateState`, `cardIconKind`,
 * `cardStatusWord`). User feedback on rc15: cards said both "Current" and
 * "Update". It is a DISPLAY overlay, not a status kind: `dispKind`/`data-dk`
 * stay `cached`, so `views/library.js` full-renders when the flag flips
 * (`updateFlagsChanged`); `patchCardVolatile` never redraws the icon.
 * The UPD-2 `.updbadge` line remains only for a card whose status word
 * does NOT already say it (a failed or running app that still carries the
 * flag), so nothing on the card is said twice.
 *
 * **Cache completeness (WP CACHE-COMPLETE-1c).** A CACHED card without an
 * update shows the green check only at `cache_complete_pct` 100 (a group
 * card: `group_cache_complete_pct`); below 100 it reads "Partially cached
 * N%" with an orange dot, unknown reads "Cached · not verified" with a
 * neutral one. Same DISPLAY-overlay shape as UPD-6 (dispKind
 * stays `cached`); "Update available" wins. Precedence and wording:
 * lib/cache-completeness.js `cardDisplay`. The overlay is part of the card's
 * `data-ck`, which views/library.js compares like `data-dk`, so a changed
 * figure rebuilds that one card; `patchCardVolatile` never redraws the icon.
 * The two new words have a compact twin (`.stfull`/`.stcompact` inside
 * `.state`, CSS picks one per layout); every other word stays plain text.
 *
 * **"Never played" line (WP WEB-FEAT-SUGGEST-2, moved off the cover by WP
 * LIBRARY-2).** The Phase 4h panel's "fully cached, never played"
 * statement as a `.playbadge` sub-line in `.meta`. Shown only for a CACHED card whose
 * owned-list playtime is a real zero (`lib/decision-support.js`
 * `isNeverPlayedCached`; playtime reaches the app only with the operator's
 * ADR-0010 gate on). Plain text, no icon, `aria-hidden` — the card's
 * accessible name carries "Fully cached, never played" instead.
 *
 * **Group cards (WP TOOL-APPS-2c, ADR-0022 §3).** A row built by
 * `lib/content-groups.js`'s `groupGames` (a parent game with its content
 * packages) is one card: the parent's status, the group's size (the union of
 * the members' depots, `displaySizeBytes`), and a `.pkgline` in the meta row
 * — "+N content packages", followed by a warning "· N failed" when a package
 * failed (user decision Q3 B variant). The package list itself lives in the
 * detail sheet (Q4 A). `data-gk` carries the group's structural part
 * (package count and failed count, `ctx.failedPackageCount`): a change
 * rebuilds the card, never a patch.
 */

import { createStatusIcon, STATUS_LABEL } from "./status-icon.js";
import {
  KIND,
  dispKind,
  statusAction,
  installedBadgeState,
  installedOnSummary,
  installedOnNames,
  installedBadgeText,
  installedBadgeCompactText,
  INSTALLED_BADGE,
  isToolApp,
  gameDisplayName,
  showsUpdateState,
  UPDATE_AVAILABLE_WORD,
} from "../lib/game-status.js";
import { cardDisplay, completenessState, COMPLETENESS } from "../lib/cache-completeness.js";
import { formatBytesGB } from "../lib/format.js";
import {
  displaySizeBytes,
  failedPackagesText,
  isGroup,
  packagesAriaText,
  packagesLineText,
} from "../lib/content-groups.js";
import { coverCandidates, fallbackHues, fallbackPattern, wireCoverFallback } from "../lib/cover-art.js";
import {
  isNeverPlayedCached,
  NEVER_PLAYED_LABEL,
  NEVER_PLAYED_COMPACT_LABEL,
  NEVER_PLAYED_DESCRIPTION,
} from "../lib/decision-support.js";

/** The exact string library.js's `GET /v1/games` tick handler
 * (js/lib/render-plan.js's `planGamesUpdate`) compares against a live
 * card's `data-dk` to decide whether that appid can be PATCHED in place or
 * needs a full rebuild (round-7 rule — see render-plan.js's header). */
export function cardStructuralKey(game, liveJob) {
  return dispKind(game, liveJob);
}

/** WP CACHE-COMPLETE-1c: the card's completeness-overlay part (`data-ck`):
 * what the status icon and word show beyond `dispKind`. views/library.js
 * rebuilds a card whose painted `data-ck` no longer matches. */
export function cardCompletenessKey(game, liveJob) {
  return cardDisplay(game, dispKind(game, liveJob), STATUS_LABEL).key;
}

/** Fill a card's `.state` element: plain text, or the full/compact twin
 * spans when the overlay has a shorter form (module header). */
export function fillStateWord(stateEl, display) {
  stateEl.className = "state tx-" + display.text;
  if (display.compact === display.word) {
    stateEl.textContent = display.word;
    return;
  }
  const full = document.createElement("span");
  full.className = "stfull";
  full.textContent = display.word;
  const compact = document.createElement("span");
  compact.className = "stcompact";
  compact.textContent = display.compact;
  stateEl.replaceChildren(full, compact);
}

function pillNumberText(game, kind) {
  // Honest per game-status.js's Divergence 2: no fabricated live percentage
  // for running/paused — only a genuinely cached game prints a number.
  if (kind !== "cached") return null;
  return formatBytesGB(displaySizeBytes(game)); // WP TOOL-APPS-2c: a group's union
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
  // WP LIB-LIST-1: the list column truncates (ellipsis), so the badge's
  // tooltip names EVERY client, not the "first +N" summary.
  const title = installedBadgeText(state, installedOnNames(game.installed_on));
  if (existing) {
    existing.className = installedTagClass(state);
    existing.title = title;
    existing.replaceChildren(full, compact);
  } else {
    const tag = document.createElement("span");
    tag.className = installedTagClass(state);
    tag.title = title;
    tag.append(full, compact);
    metaEl.appendChild(tag);
  }
}

export const UPDATE_BADGE_TEXT = UPDATE_AVAILABLE_WORD;
export const UPDATE_BADGE_COMPACT_TEXT = "Update";

/** Whether the card needs the separate `.updbadge` line: the flag is set
 * and the status word does not already say "Update available" (WP UPD-6). */
function showsUpdateBadge(game, kind) {
  return game.update_available === true && !showsUpdateState(game, kind);
}

/**
 * Create or remove the `.updbadge` child of a card's `.meta` row so it
 * matches `showsUpdateBadge` (WP UPD-2, narrowed by WP UPD-6). Plain text,
 * no icon, no animation — the same documented exception to
 * `patchCardVolatile`'s "no node created or removed" rule as
 * `syncInstalledTag`. Two children like `.instbadge`: CSS shows the compact
 * word in cols3 and list.
 * @param {HTMLElement} metaEl
 * @param {object} game
 * @param {string} kind the card's dispKind
 */
function syncUpdateTag(metaEl, game, kind) {
  const existing = metaEl.querySelector(".updbadge");
  if (!showsUpdateBadge(game, kind)) {
    if (existing) existing.remove();
    return;
  }
  if (existing) return;
  const tag = document.createElement("span");
  tag.className = "updbadge";
  const full = document.createElement("span");
  full.className = "ubfull";
  full.textContent = UPDATE_BADGE_TEXT;
  const compact = document.createElement("span");
  compact.className = "ubcompact";
  compact.textContent = UPDATE_BADGE_COMPACT_TEXT;
  tag.append(full, compact);
  metaEl.appendChild(tag);
}

/** Whether this card shows the "Never played" pill (module header). WP
 * CACHE-COMPLETE-1c: not on a partially cached card — its description says
 * "Fully cached", which a known figure below 100 contradicts. An unknown
 * figure keeps the pill (its rule was always bytes-based; dropping it there
 * would remove it on every vault running without the oracle). */
function showsNeverPlayed(game, kind) {
  return kind === KIND.CACHED && isNeverPlayedCached(game) && completenessState(game) !== COMPLETENESS.PARTIAL;
}

function neverPlayedNode(className, fullClass, compactClass) {
  const tag = document.createElement("span");
  tag.className = className;
  tag.title = NEVER_PLAYED_DESCRIPTION;
  tag.setAttribute("aria-hidden", "true");
  const full = document.createElement("span");
  full.className = fullClass;
  full.textContent = NEVER_PLAYED_LABEL;
  const compact = document.createElement("span");
  compact.className = compactClass;
  compact.textContent = NEVER_PLAYED_COMPACT_LABEL;
  tag.append(full, compact);
  return tag;
}

/**
 * Create or remove the "Never played" sub-line (`.playbadge` in `.meta`;
 * WP LIBRARY-2 dropped its cover pill twin). Plain text nodes only — the
 * same documented exception to `patchCardVolatile`'s "no node created or
 * removed" rule as `syncUpdateTag`; the status line is never touched.
 * @param {HTMLElement} cardEl
 * @param {object} game
 * @param {string} kind
 */
function syncNeverPlayed(cardEl, game, kind) {
  const want = showsNeverPlayed(game, kind);
  const metaEl = cardEl.querySelector(".meta");
  const twin = metaEl ? metaEl.querySelector(".playbadge") : null;
  if (!want) {
    if (twin) twin.remove();
    return;
  }
  if (metaEl && !twin) metaEl.appendChild(neverPlayedNode("playbadge", "pbfull", "pbcompact"));
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
  // WP LIBRARY-2: the cover's size badge follows the number (a plain text
  // span, never the status line's icon).
  const cap = cardEl.querySelector(".cap");
  if (cap) syncSizeBadge(cap, pillNumberText(game, kind));
  const sizeEl = cardEl.querySelector(".meta .size");
  if (sizeEl) sizeEl.textContent = formatBytesGB(displaySizeBytes(game)) || "—";
  const metaEl = cardEl.querySelector(".meta");
  if (metaEl) {
    syncInstalledTag(metaEl, game);
    syncUpdateTag(metaEl, game, kind);
  }
  syncNeverPlayed(cardEl, game, kind); // WP WEB-FEAT-SUGGEST-2
  // Keep the explicit accessible name (module header) in sync too — `kind`
  // is unchanged by definition on the patch path (round-7 rule), but the
  // SIZE this same tick just wrote above is part of the announced name and
  // would otherwise go stale until the next structural rebuild.
  cardEl.setAttribute("aria-label", cardAccessibleLabel(game, kind, failedCountFromKey(cardEl.dataset.gk)));
}

/** The failed-package count a card was built with (its `data-gk`). */
function failedCountFromKey(gk) {
  const m = typeof gk === "string" ? gk.match(/^\d+:(\d+)$/) : null;
  return m ? Number(m[1]) : 0;
}

/** `data-gk`: a group card's structural part (lib/content-groups.js
 * `groupCardKey` computes the same string from the live jobs). */
function groupKeyFor(game, failedCount) {
  return isGroup(game) ? `${game.packages.length}:${failedCount}` : "";
}

/**
 * The "+N content packages" line of a group card (WP TOOL-APPS-2c), with a
 * "· N failed" warning when a package failed. Plain text, `aria-hidden`
 * (the card's accessible name carries the same facts).
 * @param {object} game a group row
 * @param {number} failedCount
 * @returns {HTMLElement}
 */
function buildPackagesLine(game, failedCount) {
  const line = document.createElement("span");
  line.className = "pkgline";
  line.setAttribute("aria-hidden", "true");
  const n = document.createElement("span");
  n.className = "pkgn";
  n.textContent = packagesLineText(game.packages.length);
  line.appendChild(n);
  if (failedCount > 0) {
    // Review fix: the separator is its own element (a leading space in a
    // flex item's text is trimmed), and the warning is one unit that wraps
    // onto its own line on a narrow card instead of being clipped
    // (css/app.css `.pkgline`).
    const warn = document.createElement("span");
    warn.className = "pkgfail";
    const sep = document.createElement("span");
    sep.className = "pkgsep";
    sep.textContent = "·";
    const text = document.createElement("span");
    text.className = "pkgfailtext";
    text.textContent = failedPackagesText(failedCount);
    warn.append(sep, text);
    line.appendChild(warn);
  }
  return line;
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
  // WP D10: vault-api's stored copy first, then the server-resolved CDN
  // cover (WP API-FIX-5), then the legacy path; removed only after all fail.
  wireCoverFallback(img, coverCandidates(game.appid, game.cover_url, game.cover_cached_url), () =>
    img.remove(),
  );
  cap.appendChild(img);

  const scrim = document.createElement("div");
  scrim.className = "scrim";
  cap.appendChild(scrim);

  return cap;
}

/**
 * Create, update or remove the cover's size badge `.sz` (WP LIBRARY-2: the
 * cover carries the size only, kit library.html `.sz`). The number is
 * `pillNumberText`'s: a cached game's size, never a fabricated live figure
 * (game-status.js Divergence 2).
 * @param {HTMLElement} cap the card's `.cap`
 * @param {string|null} num
 */
function syncSizeBadge(cap, num) {
  let sz = cap.querySelector(".sz");
  if (!num) {
    if (sz) sz.remove();
    return;
  }
  if (!sz) {
    sz = document.createElement("span");
    sz.className = "sz";
    sz.setAttribute("aria-hidden", "true");
    cap.appendChild(sz);
  }
  sz.textContent = num;
}

/**
 * The status line `.stt`: a tone dot, the status word and, when the card
 * offers a quick action (download, retry, pause, resume, check again), a
 * small button carrying the status icon. The dot and the icon are
 * `aria-hidden` — the visible word right next to them already says it.
 * @param {{icon: string, text: string, word: string, compact: string}} display
 * @param {{type: string, title: string, muted?: boolean} | null} action
 * @param {string} gameName
 * @returns {HTMLElement}
 */
function buildStatusLine(display, action, gameName) {
  const line = document.createElement("div");
  line.className = "stt";
  const dot = document.createElement("i");
  dot.className = "dot tx-" + display.text;
  dot.setAttribute("aria-hidden", "true");
  line.appendChild(dot);
  const state = document.createElement("span");
  fillStateWord(state, display);
  line.appendChild(state);
  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "icnact" + (action.muted ? " muted" : "");
    btn.title = action.title;
    btn.setAttribute("aria-label", `${action.title} — ${gameName}`);
    const icon = createStatusIcon(display.icon);
    icon.setAttribute("aria-hidden", "true");
    btn.appendChild(icon);
    line.appendChild(btn);
  }
  return line;
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
  // WP LIB-LIST-1: every client by name (the visible column may truncate).
  const names = installedOnNames(game.installed_on);
  if (state === INSTALLED_BADGE.NOT_CACHED && kind === KIND.NONE) {
    return `Installed on ${names}`;
  }
  return installedBadgeText(state, names);
}

/** The card's explicit accessible name (module header, "Nested-interactive-
 * widget a11y") — game name, status word, and size when there is one to
 * report (mirrors the visible `.meta` row's own size fallback: "—" is a
 * visual placeholder, not spoken). Recomputed by `patchCardVolatile` too,
 * since a size-only tick (no structural change) would otherwise leave a
 * stale byte count in the announced name. */
function cardAccessibleLabel(game, kind, failedCount = 0) {
  const parts = [displayName(game), cardDisplay(game, kind, STATUS_LABEL).word];
  const sizeText = formatBytesGB(displaySizeBytes(game));
  if (sizeText) parts.push(sizeText);
  if (isGroup(game)) parts.push(packagesAriaText(game.packages.length, failedCount));
  if (showsUpdateBadge(game, kind)) parts.push(UPDATE_BADGE_TEXT);
  if (showsNeverPlayed(game, kind)) parts.push(NEVER_PLAYED_DESCRIPTION);
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
 *   failedPackageCount?: number,
 * }} ctx `failedPackageCount` (WP TOOL-APPS-2c): how many of a group
 *   card's packages failed (lib/content-groups.js `failedPackages`).
 * @returns {HTMLElement}
 */
export function buildCard(game, ctx) {
  const { liveJob, picked, selecting, onOpen, onLongPress, onToggle, onAction } = ctx;
  const failedCount = isGroup(game) ? ctx.failedPackageCount || 0 : 0;
  const kind = dispKind(game, liveJob);
  const action = statusAction(game, liveJob, selecting);

  const card = document.createElement("div");
  // WP API-FIX-4: a Steam tool app is shown muted (css/app.css `.card.tool`),
  // not hidden — it is real and installed, just never downloaded on its own.
  card.className = "card" + (picked ? " picked" : "") + (isToolApp(game) ? " tool" : "");
  card.dataset.appid = String(game.appid);
  card.dataset.dk = kind;
  const display = cardDisplay(game, kind, STATUS_LABEL); // WP UPD-6 + CACHE-COMPLETE-1c
  card.dataset.ck = display.key;
  card.dataset.gk = groupKeyFor(game, failedCount);
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  card.setAttribute("aria-label", cardAccessibleLabel(game, kind, failedCount));
  // In multi-select mode the card's default action is "toggle selection",
  // not "open" — aria-pressed makes that a stated TOGGLE state instead of a
  // silent visual-only "picked" class (mirrors the `.pick` checkmark, which
  // stays aria-hidden since this is the accessible equivalent of it).
  if (selecting) card.setAttribute("aria-pressed", String(picked));

  card.appendChild(buildCover(game));
  const cap = card.firstChild;
  // WP LIBRARY-2: the cover carries the title and the size badge only.
  syncSizeBadge(cap, pillNumberText(game, kind));

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
  meta.appendChild(buildStatusLine(display, action, displayName(game)));
  // Unlike the cover badge (which omits the number entirely rather than
  // fabricate one — see pillNumberText), the list layout's size column
  // always shows SOMETHING, using an explicit "—" for "nothing honest to
  // print". The grid layouts hide this cell (the badge is on the cover).
  const size = document.createElement("span");
  size.className = "size";
  size.textContent = formatBytesGB(displaySizeBytes(game)) || "—";
  meta.appendChild(size);
  // WP TOOL-APPS-2c: the group's own line, before the badges.
  if (isGroup(game)) meta.appendChild(buildPackagesLine(game, failedCount));
  syncUpdateTag(meta, game, kind); // WP UPD-2/UPD-6: `.updbadge` iff flagged and not said by the status word
  syncInstalledTag(meta, game); // WP AG-2: appends `.instbadge` iff installed_on is non-empty
  card.appendChild(meta);
  syncNeverPlayed(card, game, kind); // WP WEB-FEAT-SUGGEST-2: needs `.meta` in place

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
    // Only the card itself: Enter/Space on the nested quick-action button
    // bubbles here and must run that button's own action, not open the sheet.
    if (e.target !== card) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (selecting) onToggle(game.appid);
      else onOpen(game.appid);
    }
  });

  if (action) {
    // The status line's action button must never let its click reach the
    // card (which would open the detail view / toggle selection on top of
    // firing the action) — mockup parity
    // (`onclick="event.stopPropagation();..."`).
    for (const el of card.querySelectorAll("button.icnact")) {
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
