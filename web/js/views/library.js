/**
 * Library view (WP 4a.3).
 *
 * Grid 2/3/list, search + filter chips (ANDed), the capsule pill
 * (icon + size, actionable), multi-select with bulk download split
 * semantics and bulk delete with the set-aware multiPlan arithmetic, and
 * in-place progress patching per the round-7 mockup rule. Ports
 * docs/design/vault-app-mockup.html's Library screen; see the module
 * headers in js/lib/game-status.js, js/lib/bulk-plan.js and
 * js/lib/multiplan.js for the specific, documented divergences from the
 * mockup's fake data model (no "stale" status yet, no live progress
 * percentage, multiPlan assembled from real per-call data instead of one
 * in-memory array).
 *
 * Data flows exclusively through the WP 4a.2 store (store-singleton.js) —
 * no parallel poll loop is created here. Detail-sheet / single-game delete
 * (mockup's `openDetail`) is WP 4a.4's scope: `onOpen` below opens
 * `components/game-detail-sheet.js`'s sheet, this file's one and only
 * reach into that module (this WP finally owns library.js — see WP 4a.4's
 * brief).
 *
 * DOM is rebuilt from scratch on every real state change (search, chip,
 * layout, selection, a `GET /v1/games` diff, or a job actually
 * transitioning status) and left COMPLETELY untouched on a no-op poll tick
 * (round-7 rule: never rebuild — and thereby restart the CSS status-icon
 * animation of — a card whose displayed state hasn't changed). See the
 * `store.subscribe("games", ...)` / `store.subscribe("jobs", ...)` calls
 * near the bottom of this file for exactly what counts as a real change.
 *
 * **"Check & update all cached games" (Phase 4c, WP 4c-web) — the
 * `doRefresh()` divergence, resolved.** `docs/design/vault-app-mockup-
 * NOTES.md`'s `doRefresh()` (this app's `store.refreshNow()`, wired to the
 * `visibilitychange` nudge in store.js and called after every mutating
 * action below) only re-polls vault-api for what it already knows — it
 * makes zero outbound requests to Steam and can never start a download.
 * `docs/PROJECT_PLAN.md` §7 Phase 4c calls a refresh gesture that CAN start
 * downloads a trap, and this WP keeps that boundary: the header button
 * below is a SEPARATE control that calls the new `POST /v1/prefill/cached`
 * (`api.prefillCached`), never folded into `store.refreshNow()`/pull-to-
 * refresh. `onCheckAndUpdate()` calls `store.refreshNow()` itself only
 * AFTER the real request already queued whatever it queued — same as every
 * other action in this file, to pull the fresh job rows onto screen sooner,
 * not as a substitute for the Steam-contacting call. Decision recorded here
 * per the plan's request; `docs/PROJECT_PLAN.md` ticks the corresponding
 * item, and `docs/WORKPACKAGES.md`'s Phase 4a divergence register (review
 * round 1, S3) carries the same two facts — a mockup-absent full-width
 * header row, and the `doRefresh()` resolution — for the user's veto and
 * the Android port's benefit.
 *
 * The mixed-outcome partition/wording (including the forced-run heads-up,
 * scoped and gated inside that module after review round 1's blocker —
 * see its header) and the in-flight guard are pulled into
 * `js/lib/cached-prefill-outcome.js` (DOM-free, unit-tested) — this file
 * only wires a button click to it and a real API call, same posture as
 * every other decision-logic extraction in this codebase (job-partition.js,
 * gc-flow.js, ...).
 *
 * **WP 4e.2 (desktop layout, D-3/D-4):** the DOM this file builds does not
 * change — the six `.lib-head`/`.lib-checkrow`/`.search`/`.chips`/`.grid`/
 * `.hint` children (`.bulk` is a seventh, out-of-flow sibling) keep the
 * exact same classes and append order they always had; `css/app.css`'s
 * BP-L block now assigns each of them a named grid area BY CLASS, and
 * turns `.grid` into a `repeat(auto-fill, minmax(var(--tile-min),1fr))`
 * density grid instead of the fixed 2/3-column switch. What DOES change
 * here is purely wording: `LAYOUT_LABEL` and the segmented control's own
 * button titles/aria-labels, relabeled from a literal column count
 * ("Two columns"/"Three columns") to an honest density description
 * ("Comfortable"/"Compact") now that the actual on-screen column count is
 * derived from viewport width, not fixed by this control — see the D-3
 * comment at `LAYOUT_LABEL`'s definition and `docs/WORKPACKAGES.md`'s
 * Phase 4a divergence register.
 *
 * **WP WEB-FEAT-1 (owned Steam games).** The grid shows the vault's games
 * PLUS every game the stored library SteamID64 owns
 * (`lib/owned-library.js`: merge, header wording, error text, loader). The
 * owned list is fetched on library open and on the notice line's "Reload
 * Steam library" button only, never on a poll tick (the relay hits Steam).
 * Owned games the vault does not know show as "Not cached" with no card
 * quick action and are never a bulk-download target; their download lives
 * in the detail sheet (its existing "not tracked" branch). A relay failure
 * leaves the vault games on screen and says why in the notice line.
 */

import { store } from "../store-singleton.js";
import { api } from "../api.js";
import { showToast } from "../components/toast.js";
import { buildCard, cardStructuralKey, patchCardVolatile } from "../components/game-card.js";
import { indexLiveJobsByAppid, isJobStateTransition } from "../lib/game-status.js";
import { chipCounts, normalizeQuery, visibleGames } from "../lib/library-filters.js";
import {
  classifyBulkSelection,
  buildBulkDownloadPlan,
  classifyBulkDeleteEligibility,
} from "../lib/bulk-plan.js";
import { buildMultiPlan } from "../lib/multiplan.js";
import {
  summarizeCachedPrefillOutcome,
  describeCachedPrefillError,
  createCheckAndUpdateAction,
} from "../lib/cached-prefill-outcome.js";
import { planGamesUpdate } from "../lib/render-plan.js";
import { formatBytesGB } from "../lib/format.js";
import { onViewChange, navigateTo } from "../router.js";
import { openDetail } from "../components/game-detail-sheet.js";
import {
  librarySubtitle,
  mergeOwnedLibrary,
  ownedNotice,
  NOTICE_ACTION,
  OWNED_STATUS,
} from "../lib/owned-library.js";
import { pushModal, popModal } from "../lib/modal-stack.js";
import { ownedLibrary } from "../owned-singleton.js";

const LAYOUT_STORAGE_KEY = "steamvault.libraryLayout";
const LAYOUT_CLASS = { grid2: "", grid3: "cols3", list: "list" };
// D-3 (docs/WORKPACKAGES.md, user-approved 2026-08-18): this control ships
// literal column-count wording ("Two columns"/"Three columns") since the
// WP 4a.3 mockup port, which was true only as long as the grid itself was a
// fixed 2-or-3-column switch. WP 4e.2 makes the desktop column count derive
// from viewport width via `auto-fill` (css/app.css's BP-L block) — "two
// columns" stops being a true description of what pressing this button
// does the moment a wide screen renders seven of them. Rather than ship an
// aria-label that quietly lies at desktop widths (worse than a wording that
// simply differs from Android's, which nothing pins as parity — see the
// register entry), the control is honestly relabeled as what it actually
// is below BP-L too, not just above it: a DENSITY choice (fewer, larger
// tiles vs. more, smaller ones), not a literal count. "List" is unchanged —
// it never claimed a column count.
const LAYOUT_LABEL = { grid2: "Comfortable", grid3: "Compact", list: "List" };
const ACTIVE_JOB_STATUSES = ["queued", "running", "paused"];

// "Check & update", never "Check" (docs/PROJECT_PLAN.md §7 Phase 4c) —
// pressing this can consume real bandwidth; a label implying a read-only
// check would misrepresent what happens. The busy label carries the SAME
// rule (review round 1, S2): it is also this button's accessible name while
// it is focused mid-action — the one string a screen-reader user hears
// during the exact window the honesty mandate is about — so "Checking…"
// alone (missing the "& update" half) is not good enough here either.
const CHECK_UPDATE_LABEL = "Check & update all cached games";
const CHECK_UPDATE_BUSY_LABEL = "Checking & updating…";
// The paused-dedupe outcome is the one result that needs the user to go DO
// something (resume or cancel) — toast.js's default 2.6s is tuned for
// acknowledge-and-move-on messages, so this gets a longer, but still
// auto-dismissing, window (toast.js has no sticky/manual-dismiss mode —
// see the review-round-1 N3 note at this constant's one call site).
const CHECK_UPDATE_WARN_TOAST_MS = 6000;

function readStoredLayout() {
  try {
    const v = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    return v && Object.prototype.hasOwnProperty.call(LAYOUT_CLASS, v) ? v : "grid2";
  } catch {
    return "grid2";
  }
}
function writeStoredLayout(v) {
  try {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, v);
  } catch {
    // Same posture as api.js's localStorage helpers: a storage failure must
    // never break the layout switch itself, just its persistence.
  }
}

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed";
}

function namesFor(appids, gamesByAppid) {
  return appids.map((id) => gamesByAppid.get(id)?.name || `App ${id}`).join(", ");
}

// ---------------------------------------------------------------------
// Module-level state. Persists across re-mounts (navigating away and back
// to Library within the same session) — matches the mockup's single global
// STATE object; only `selecting`/`picked` reset on navigating away (see
// the onViewChange listener at the bottom), same as the mockup's
// nav-dismisses-transient-surfaces rule.
// ---------------------------------------------------------------------
const state = {
  // `vaultGames` is the raw `GET /v1/games` list; `games` is what the grid
  // shows: vault rows plus the owned-only rows (WP WEB-FEAT-1,
  // lib/owned-library.js's mergeOwnedLibrary). Every reader below uses
  // `games`; only the merge itself reads `vaultGames`.
  vaultGames: store.snapshot("games") || [],
  games: store.snapshot("games") || [],
  jobs: store.snapshot("jobs") || [],
  query: "",
  filterKey: "all",
  layout: readStoredLayout(),
  selecting: false,
  picked: new Set(),
  visibleAppids: new Set(),
};

let liveJobsByAppid = indexLiveJobsByAppid(state.jobs);

// ---------------------------------------------------------------------
// Owned Steam games (WP WEB-FEAT-1). The loader has no timer: `load()` runs
// on library open (renderLibrary) and on the notice line's "Reload" button,
// never from a store subscription — the relay hits Steam, the poll interval
// must not. Module-level like `state`, so the last owned list survives a
// navigate-away-and-back until the reload that the re-open triggers lands.
// ---------------------------------------------------------------------
// After a SteamID change (Settings), the previous owned list stays on screen
// until the new load lands — accepted: it is replaced, not merged, then.
// WP WEB-FIX-4: the loader itself lives in `owned-singleton.js` so other
// views can use its names; this view is still the one that loads it.
let mergedOwnedGames = null; // the owned list `state.games` was last merged with
ownedLibrary.subscribe((owned) => {
  if (owned.games === mergedOwnedGames) {
    // Only the status changed (a reload started): header + notice, no
    // grid rebuild.
    if (mounted()) {
      updateSubtitle();
      renderOwnedNotice();
    }
    return;
  }
  remerge();
  fullRender();
});

function remerge() {
  mergedOwnedGames = ownedLibrary.current().games;
  state.games = mergeOwnedLibrary(state.vaultGames, mergedOwnedGames);
}

// Module-level, not per-mount: an in-flight "Check & update" call must stay
// locked across a navigate-away-and-back (buildSection() reads
// checkAndUpdateAction.isInFlight() below to paint a freshly-built button's
// initial state correctly), same posture as `state` above.
const checkAndUpdateAction = createCheckAndUpdateAction({ fetcher: () => api.prefillCached() });

/** The currently-mounted <section>, or null. Store-subscription callbacks
 * check this before touching the DOM so a background tick while a
 * DIFFERENT view is showing (or after this one was navigated away from) is
 * a cheap no-op — the `onViewChange` listener near the bottom of this file
 * is what nulls `sectionEl` out the instant the user leaves Library, which
 * is the ONLY staleness signal this needs.
 *
 * Deliberately NOT also checking `sectionEl.isConnected` (fixed here —
 * WP 4a.4, tracked fix from WP 4a.5's twin bug in downloads.js, never
 * landed for this file): `renderLibrary()` builds the section, assigns it
 * to `sectionEl`, and calls `fullRender()` synchronously, all BEFORE
 * `app.js`'s `viewRoot.replaceChildren(render())` has attached it to the
 * document — so at that exact moment `isConnected` is still `false` even
 * though this genuinely is the current, about-to-be-shown section. Gating
 * on `isConnected` made the very first paint of every navigation to this
 * view silently no-op, and — since the WP 4a.3 per-card planner turned
 * store ticks into patch/rebuild decisions gated on `mounted()` — the bug
 * stopped self-healing: re-navigating to Library on an idle vault (no games
 * or jobs tick ever arriving to trigger a fallback full render) left the
 * grid empty until something else forced a `fullRender()`. See
 * `views/downloads.js`'s identical fix/rationale (WP 4a.5) for the sibling
 * bug found and corrected there but never ported here. */
let sectionEl = null;
function mounted() {
  return sectionEl !== null;
}

/** DOM refs for the currently-built section, reassigned by buildSection().
 * Bulk-bar/dialog target ids captured alongside for the click handlers
 * that are wired ONCE per mount (buildSection runs once per render). */
let els = null;
let currentBulkPrimaryTargets = [];
let currentBulkSecondaryTargets = [];
let currentBulkDeleteIds = [];
let pendingDeleteIds = null;

// ---------------------------------------------------------------------
// Bulk-delete confirm dialog — built ONCE at module load, appended
// directly to `document.body` (a sibling of `#app`), NOT to the per-mount
// `<section>` `buildSection()` rebuilds on every navigation.
//
// **WP 4a.8 bug found live, fixed here.** This dialog originally WAS built
// inside `buildSection()` and appended as a child of the Library section —
// itself inside `#app`. The first version of this WP's `pushModal`/`inert`
// wiring (matching `components/game-detail-sheet.js`'s confirm dialogs,
// which genuinely ARE `document.body`-level siblings of `#app`) marked
// `#app` `inert` on open — which, for a dialog living INSIDE `#app`, made
// the dialog itself (and its Keep/Delete buttons) unfocusable and
// unclickable too (the confirm was completely unusable). Reproduced live in
// the Browser pane: `document.activeElement` was `<body>`, not the "Keep"
// button, and the dialog's own buttons were inert along with everything
// else in `#app`. Moving construction here — matching
// `game-detail-sheet.js`'s pattern exactly — is the fix: the dialog is now
// a true `document.body` sibling of `#app`, so marking `#app` inert no
// longer touches it.
// ---------------------------------------------------------------------
const dialogBackdrop = document.createElement("div");
dialogBackdrop.className = "dialog-backdrop";
const dialogEl = document.createElement("div");
dialogEl.className = "dialog";
dialogEl.setAttribute("role", "alertdialog");
dialogEl.setAttribute("aria-modal", "true");
dialogEl.setAttribute("aria-label", "Confirm deletion");
const dTitle = document.createElement("h3");
const dText = document.createElement("p");
const dNote = document.createElement("div");
const dRow = document.createElement("div");
dRow.className = "row";
const dNo = document.createElement("button");
dNo.type = "button";
dNo.className = "btn ghost sm";
dNo.textContent = "Keep";
const dYes = document.createElement("button");
dYes.type = "button";
dYes.className = "btn danger sm";
dYes.textContent = "Delete";
dRow.append(dNo, dYes);
dialogEl.append(dTitle, dText, dNote, dRow);
dialogBackdrop.appendChild(dialogEl);
document.body.appendChild(dialogBackdrop);

dNo.addEventListener("click", closeDeleteConfirm);
dYes.addEventListener("click", confirmDelete);

// ---------------------------------------------------------------------
// Selection mode
// ---------------------------------------------------------------------

function enterSelect(appid) {
  state.selecting = true;
  document.body.classList.add("selecting");
  if (appid != null) state.picked.add(appid);
  fullRender();
}

function exitSelect() {
  state.selecting = false;
  state.picked.clear();
  document.body.classList.remove("selecting");
  fullRender();
}

function onToggle(appid) {
  if (state.picked.has(appid)) state.picked.delete(appid);
  else state.picked.add(appid);
  if (state.picked.size === 0) {
    exitSelect();
    return;
  }
  fullRender();
}

function onOpen(appid) {
  const game = state.games.find((g) => g.appid === appid);
  openDetail(appid, game?.name);
}

// ---------------------------------------------------------------------
// Per-card actions (capsule pill / list-row icon)
// ---------------------------------------------------------------------

async function onAction(appid, actionType) {
  try {
    if (actionType === "download") {
      await api.prefill([appid]);
      showToast("Queued for download");
    } else if (actionType === "pause") {
      const job = liveJobsByAppid.get(appid);
      if (!job) return;
      await api.pauseJob(job.id);
      showToast("Pause requested", { warn: true });
    } else if (actionType === "resume") {
      const job = liveJobsByAppid.get(appid);
      if (!job) return;
      await api.resumeJob(job.id);
      showToast("Resuming — back in the queue");
    }
    store.refreshNow();
  } catch (err) {
    showToast(errorText(err), { warn: true });
  }
}

// ---------------------------------------------------------------------
// "Check & update all cached games" (Phase 4c, WP 4c-web) — see this file's
// module header for the doRefresh() divergence this deliberately does NOT
// fold into. The mixed-outcome wording, error classification and the
// in-flight guard itself all live in js/lib/cached-prefill-outcome.js
// (DOM-free, unit-tested); this function is the DOM-side glue: paint the
// busy state, call the guarded action, paint the result.
// ---------------------------------------------------------------------

function paintCheckUpdateButton(busy) {
  if (!mounted()) return;
  els.checkUpdateBtn.disabled = busy;
  els.checkUpdateBtn.textContent = busy ? CHECK_UPDATE_BUSY_LABEL : CHECK_UPDATE_LABEL;
}

async function onCheckAndUpdate() {
  // Belt: the real button is already `disabled` the instant this handler
  // starts (see the click listener in buildSection()), so a second click
  // cannot even reach here while one is in flight. Suspenders: the guard
  // itself lives in cached-prefill-outcome.js's createCheckAndUpdateAction,
  // independent of any button state — `run()` below no-ops if it is
  // already busy regardless of how it was invoked.
  paintCheckUpdateButton(true);
  const result = await checkAndUpdateAction.run();
  paintCheckUpdateButton(false);

  if (result.skipped) return; // already running — this press changed nothing

  if (result.ok) {
    // The forced-run note (if any) is composed INSIDE summarizeCachedPrefillOutcome
    // now, gated on the response's OWN `queued` bucket and scoped to only
    // those appids — review round 1 blocker: this used to be computed here
    // from `state.games` regardless of what the server actually queued,
    // which could (and did, live-reproduced) claim forced work was starting
    // on an empty or all-deduplicated outcome. `state.games` is passed
    // through only as the best-effort `needs_force` lookup table; the
    // module itself decides whether it applies at all.
    const summary = summarizeCachedPrefillOutcome(result.refs, state.games);
    // N3 (review round 1 nitpick): `summary.warn` means "the user needs to
    // go DO something" (a paused dedupe — resume or cancel it) — the one
    // outcome of this whole action that requires a follow-up action, not
    // just a status update. toast.js's default 2.6 s auto-dismiss is tuned
    // for "acknowledge and move on" messages; this one gets the longer
    // duration the component already supports rather than a new surface.
    showToast(summary.message, {
      warn: summary.warn,
      duration: summary.warn ? CHECK_UPDATE_WARN_TOAST_MS : undefined,
    });
    store.refreshNow();
  } else {
    const desc = describeCachedPrefillError(result.err);
    showToast(desc.message, { warn: true });
    // README's mid-loop-5xx honesty rule: re-read GET /v1/jobs rather than
    // implying the press did nothing — describeCachedPrefillError sets
    // `refresh` only for that one error kind.
    if (desc.refresh) store.refreshNow();
  }
}

// ---------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------

function renderChips() {
  const counts = chipCounts(state.games, { query: state.query, liveJobsByAppid });
  els.chips.replaceChildren();
  for (const c of counts) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.dataset.f = c.key;
    btn.setAttribute("aria-pressed", String(state.filterKey === c.key));
    btn.appendChild(document.createTextNode(c.label + " "));
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = String(c.count);
    btn.appendChild(n);
    els.chips.appendChild(btn);
  }
}

function renderEmptyState() {
  if (state.query) {
    const wrap = document.createElement("div");
    wrap.className = "noresult";
    const p = document.createElement("p");
    p.appendChild(document.createTextNode("No game in your library matches "));
    const b = document.createElement("b");
    b.textContent = `"${state.query}"`;
    p.appendChild(b);
    p.appendChild(
      document.createTextNode(state.filterKey !== "all" ? " with this filter." : "."),
    );
    wrap.appendChild(p);
    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "btn sm";
    clearBtn.textContent = "Clear search";
    clearBtn.addEventListener("click", clearSearch);
    wrap.appendChild(clearBtn);
    return wrap;
  }
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = "Nothing here with this filter.";
  return p;
}

// **DOM node count is independent of column count (WP 4e.1 finding, worth
// pinning here in a comment rather than re-discovering it in Phase 4e's next
// package).** `renderGrid` below builds one `buildCard(...)` node per VISIBLE
// game regardless of `state.layout` — 2 columns, 3 columns and list all run
// the exact same loop over the exact same `list`; only `els.grid.className`
// changes, which is a CSS `grid-template-columns` concern, not a DOM-size
// one. Concretely: a library of 400 already-visible games builds 400 card
// nodes TODAY, in every layout, on every desktop-viewport width — the
// desktop-layout phase (docs/PROJECT_PLAN.md §7 Phase 4e) that eventually
// raises the on-screen COLUMN count via an `auto-fill` grid (`--tile-min`,
// css/theme.css) does not change this function's node count at all, only
// how those existing 400 nodes are arranged. No virtualization/windowing
// exists here regardless of column count — see the coder's WP 4e.1 report
// for the measured cost of a full rebuild at N=400 (~30ms synchronous).
function renderGrid() {
  const list = visibleGames(state.games, {
    query: state.query,
    filterKey: state.filterKey,
    liveJobsByAppid,
  });
  state.visibleAppids = new Set(list.map((g) => g.appid));
  els.grid.className = ("grid " + (LAYOUT_CLASS[state.layout] || "")).trim();
  els.grid.replaceChildren();

  if (list.length === 0) {
    els.grid.appendChild(renderEmptyState());
    return;
  }

  for (const game of list) {
    els.grid.appendChild(
      buildCard(game, {
        liveJob: liveJobsByAppid.get(game.appid),
        picked: state.picked.has(game.appid),
        selecting: state.selecting,
        onOpen,
        onLongPress: enterSelect,
        onToggle,
        onAction,
      }),
    );
  }
}

function setButtonEnabled(btn, enabled, label) {
  btn.disabled = !enabled;
  btn.classList.toggle("primary", enabled);
  btn.textContent = label;
}

function syncBulk() {
  els.bulkBar.classList.toggle("up", state.selecting);
  els.selectBtn.classList.toggle("on", state.selecting);
  els.bulkCount.textContent = String(state.picked.size);
  const hiddenCount = [...state.picked].filter((id) => !state.visibleAppids.has(id)).length;
  els.bulkHidden.textContent = hiddenCount ? ` · ${hiddenCount} out of view` : "";

  if (!state.selecting) {
    currentBulkPrimaryTargets = [];
    currentBulkSecondaryTargets = [];
    currentBulkDeleteIds = [];
    return;
  }

  const gamesByAppid = new Map(state.games.map((g) => [g.appid, g]));
  const pickedGames = [...state.picked].map((id) => gamesByAppid.get(id)).filter(Boolean);

  const classification = classifyBulkSelection(pickedGames, state.jobs);
  const plan = buildBulkDownloadPlan(classification, pickedGames.length);

  setButtonEnabled(els.bulkPrimary, plan.primaryEnabled, plan.primaryLabel);
  currentBulkPrimaryTargets = plan.primaryTargets;
  els.bulkNote.textContent = plan.note;

  if (plan.secondaryLabel) {
    els.bulkSecondary.style.display = "";
    els.bulkSecondary.textContent = plan.secondaryLabel;
    currentBulkSecondaryTargets = plan.secondaryTargets;
  } else {
    els.bulkSecondary.style.display = "none";
    currentBulkSecondaryTargets = [];
  }

  // Bulk delete eligibility lives in bulk-plan.js (js/lib/bulk-plan.js's
  // classifyBulkDeleteEligibility) — has-cache-content, not "status is not
  // none" (an `error` app with zero visible bytes has no depot mappings
  // left and would just 404; see that function's docstring).
  const deletable = classifyBulkDeleteEligibility(pickedGames, state.jobs);
  if (deletable.length) {
    els.bulkDelete.style.display = "";
    els.bulkDelete.textContent =
      deletable.length < pickedGames.length
        ? `Delete ${deletable.length} of ${pickedGames.length}`
        : `Delete ${deletable.length === 1 ? "1 game" : deletable.length + " games"}`;
    currentBulkDeleteIds = deletable.map((g) => g.appid);
  } else {
    els.bulkDelete.style.display = "none";
    currentBulkDeleteIds = [];
  }
}

function updateSubtitle() {
  // WP WEB-FEAT-1: "owned" only when the owned list really loaded — see
  // lib/owned-library.js's header for both wordings and the count choice.
  els.sub.textContent = librarySubtitle({
    games: state.games,
    liveJobsByAppid,
    owned: ownedLibrary.current(),
  });
}

const NOTICE_BUTTON_LABEL = {
  [NOTICE_ACTION.SETTINGS]: "Open Settings",
  [NOTICE_ACTION.RELOAD]: "Reload Steam library",
};

function renderOwnedNotice() {
  const owned = ownedLibrary.current();
  const notice = ownedNotice(owned);
  const { ownedNote, ownedText, ownedSettingsBtn, ownedReloadBtn } = els;
  // The nodes are built once per mount (buildSection) and only UPDATED
  // here — never replaced — so a focused "Reload" button keeps focus across
  // its own reload cycle. The live region is the text span alone, and its
  // text is written only when it changes, so a re-render (search keystroke,
  // chip click, loading flip) announces nothing new.
  const text = notice ? notice.text : "";
  // The span itself is NEVER hidden (an empty span renders nothing): a
  // screen reader may ignore a live region that becomes visible in the same
  // render as its new text (e.g. loaded OK -> reload fails). Only the outer
  // <p> is hidden.
  if (ownedText.textContent !== text) ownedText.textContent = text;
  ownedNote.classList.toggle("warn", !!(notice && notice.warn));

  const actions = notice ? notice.actions : owned.status === OWNED_STATUS.READY ? [NOTICE_ACTION.RELOAD] : [];
  ownedSettingsBtn.hidden = !actions.includes(NOTICE_ACTION.SETTINGS);
  ownedReloadBtn.hidden = !actions.includes(NOTICE_ACTION.RELOAD);
  // aria-disabled, not `disabled`: a disabled button drops focus to <body>.
  // The click handler checks the loader state itself.
  ownedReloadBtn.setAttribute("aria-disabled", String(!!owned.loading));
  ownedNote.hidden = !text && ownedSettingsBtn.hidden && ownedReloadBtn.hidden;
}

function noticeButton(action) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "linkbtn";
  btn.dataset.action = action;
  btn.textContent = NOTICE_BUTTON_LABEL[action];
  btn.hidden = true;
  if (action === NOTICE_ACTION.RELOAD) {
    btn.setAttribute("aria-disabled", "false");
    btn.addEventListener("click", () => {
      if (ownedLibrary.current().loading) return; // a reload is already in flight
      ownedLibrary.load();
    });
  } else {
    btn.addEventListener("click", () => navigateTo("settings"));
  }
  return btn;
}

function fullRender() {
  if (!mounted()) return;
  updateSubtitle();
  renderOwnedNotice();
  renderChips();
  renderGrid();
  syncBulk();
}

// ---------------------------------------------------------------------
// Search / chips / layout
// ---------------------------------------------------------------------

function applySearch(rawValue) {
  state.query = normalizeQuery(rawValue);
  els.searchWrap.classList.toggle("filled", els.searchInput.value.length > 0);
  fullRender();
}
function clearSearch() {
  els.searchInput.value = "";
  applySearch("");
  els.searchInput.focus();
}

function setLayout(value, announce) {
  if (!Object.prototype.hasOwnProperty.call(LAYOUT_CLASS, value)) return;
  state.layout = value;
  writeStoredLayout(value);
  for (const btn of els.layoutSegs.querySelectorAll("button[data-layout]")) {
    btn.setAttribute("aria-pressed", String(btn.dataset.layout === value));
  }
  fullRender();
  if (announce) showToast(`${LAYOUT_LABEL[value]} layout`);
}

// ---------------------------------------------------------------------
// Bulk-delete confirm dialog (multiPlan)
// ---------------------------------------------------------------------

function renderDeletePlan(plan, ids, gamesByAppid) {
  const freedText = formatBytesGB(plan.freedBytes) || "0 GB";
  const occupiedText = formatBytesGB(plan.occupiedBytes) || "0 GB";

  dText.replaceChildren();
  if (ids.length > 1) {
    dText.append(
      "Deleting ",
      strong(`${ids.length} games`),
      " frees about ",
      strong(freedText, "num"),
      " of the ",
      strong(occupiedText, "num"),
      " they occupy.",
    );
  } else {
    const name = gamesByAppid.get(ids[0])?.name || `App ${ids[0]}`;
    dText.append(
      "Deleting ",
      strong(name),
      " frees about ",
      strong(freedText, "num"),
      " of the ",
      strong(occupiedText, "num"),
      " it occupies.",
    );
  }

  dNote.replaceChildren();
  if (plan.sharedRows.length === 0) {
    const p = document.createElement("p");
    p.className = "cfsolo";
    p.textContent = `No shared depots — the full ${occupiedText} is freed.`;
    dNote.appendChild(p);
    return;
  }

  const label = document.createElement("p");
  label.className = "cflabel";
  label.textContent = `${plan.sharedRows.length} shared depot${plan.sharedRows.length > 1 ? "s" : ""}`;
  dNote.appendChild(label);

  const ul = document.createElement("ul");
  ul.className = "cflist";
  for (const row of plan.sharedRows) {
    const li = document.createElement("li");
    li.className = row.free ? "free" : "keep";
    const did = document.createElement("span");
    did.className = "did";
    did.textContent = String(row.depotid);
    const mk = document.createElement("span");
    mk.className = "mk";
    mk.textContent = row.free ? "freed" : "kept";
    const dsz = document.createElement("span");
    dsz.className = "dsz";
    dsz.textContent = formatBytesGB(row.sizeBytes) || "—";
    const why = document.createElement("span");
    why.className = "why";
    if (row.free) {
      why.textContent = row.others.length
        ? `no cached co-owner — ${namesFor(row.others, gamesByAppid)} ${row.others.length > 1 ? "are" : "is"} not cached`
        : "every game mapping this depot is in this selection";
    } else {
      why.textContent = `${namesFor(row.holderAppids, gamesByAppid)} still cached`;
    }
    li.append(did, mk, dsz, why);
    ul.appendChild(li);
  }
  dNote.appendChild(ul);

  const total = document.createElement("p");
  total.className = "cftotal";
  total.append(strong(freedText), " freed · ", strong(formatBytesGB(plan.keptBytes) || "0 GB"), " stays on disk");
  dNote.appendChild(total);
}

function strong(text, extraClass) {
  const b = document.createElement("b");
  if (extraClass) b.className = extraClass;
  b.textContent = text;
  return b;
}

/** Element to return focus to when the delete-confirm dialog closes,
 * captured fresh on every open() — same pattern as `sheet-dialog.js`'s
 * `invokerEl` (WP 4a.8: this dialog had no focus management at all before
 * this WP: no Escape, no focus-on-open, no trap). */
let deleteConfirmInvokerEl = null;

async function openDeleteConfirm(ids) {
  pendingDeleteIds = ids;
  const gamesByAppid = new Map(state.games.map((g) => [g.appid, g]));
  dTitle.textContent =
    ids.length > 1 ? `Delete ${ids.length} games from cache?` : "Delete from cache?";
  dText.textContent = "Calculating what this would free…";
  dNote.replaceChildren();
  dYes.disabled = true;
  deleteConfirmInvokerEl = document.activeElement;
  dialogBackdrop.classList.add("on");
  // WP 4a.8: #app goes inert while this is up; Escape routes through
  // lib/modal-stack.js's single dispatcher (see its header) rather than a
  // listener bound here directly.
  pushModal(dialogBackdrop, closeDeleteConfirm);
  dNo.focus(); // "Keep" — the non-destructive default gets initial focus

  try {
    const [details, mapping] = await Promise.all([
      Promise.all(ids.map((id) => api.game(id))),
      api.mapping(),
    ]);
    const activeJobAppids = new Set(
      state.jobs.filter((j) => ACTIVE_JOB_STATUSES.includes(j.status)).map((j) => j.appid),
    );
    const plan = buildMultiPlan(ids, { details, mapping, gamesByAppid, activeJobAppids });
    renderDeletePlan(plan, ids, gamesByAppid);
    dYes.disabled = false;
  } catch (err) {
    dText.textContent = `Could not calculate the delete plan: ${errorText(err)}`;
    dYes.disabled = true;
  }
}

function closeDeleteConfirm() {
  dialogBackdrop.classList.remove("on");
  popModal(dialogBackdrop);
  if (deleteConfirmInvokerEl && typeof deleteConfirmInvokerEl.focus === "function") deleteConfirmInvokerEl.focus();
  deleteConfirmInvokerEl = null;
  pendingDeleteIds = null;
}

async function confirmDelete() {
  const ids = pendingDeleteIds;
  if (!ids) return;
  dYes.disabled = true;
  dNo.disabled = true;
  const results = await Promise.allSettled(ids.map((id) => api.deleteCache(id)));
  dYes.disabled = false;
  dNo.disabled = false;
  closeDeleteConfirm();
  exitSelect();
  store.refreshNow();

  // Report what the SERVER actually did, not this dialog's preview — the
  // server re-checks every depot's co-owner state immediately before
  // removing it (api/README.md "Two-stage decision"), so it is the
  // authority, not multiplan.js's prediction (see that module's header).
  let freedTotal = 0;
  let failedCount = 0;
  for (const r of results) {
    if (r.status === "fulfilled") freedTotal += r.value?.total_bytes_freed || 0;
    else failedCount += 1;
  }
  const freedText = formatBytesGB(freedTotal) || "0 GB";
  showToast(
    failedCount
      ? `Freed ${freedText} · ${failedCount} delete${failedCount > 1 ? "s" : ""} failed`
      : `Freed ${freedText}`,
    { warn: !!failedCount },
  );
}

// ---------------------------------------------------------------------
// Static DOM construction (rebuilt fresh on every mount — see mounted())
// ---------------------------------------------------------------------

// Takes the label from `LAYOUT_LABEL[layoutKey]` itself (Opus review
// nitpick, WP 4e.2 fix round) rather than a separately-passed `title`
// string: the three call sites used to spell "Comfortable"/"Compact"/"List"
// out a second time by hand, with nothing pinning the two copies equal — a
// wording change to `LAYOUT_LABEL` (D-3's whole point) could drift from the
// segmented control's own title/aria-label with no test catching it. One
// source of truth removes the drift surface entirely.
function segButton(layoutKey, svgMarkup) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.dataset.layout = layoutKey;
  const title = LAYOUT_LABEL[layoutKey];
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.setAttribute("aria-pressed", String(state.layout === layoutKey));
  // Static, non-interpolated decorative markup only (no user data ever
  // flows through this helper) — same trust level as the literal SVGs
  // already inline in index.html's nav.
  btn.innerHTML = svgMarkup;
  return btn;
}

function buildSection() {
  document.body.classList.toggle("selecting", state.selecting);

  const section = document.createElement("section");
  section.className = "view view-library";

  const head = document.createElement("div");
  head.className = "lib-head";
  const titles = document.createElement("div");
  const h1 = document.createElement("h1");
  h1.textContent = "Library";
  const sub = document.createElement("span");
  sub.className = "lib-sub";
  titles.append(h1, sub);

  const tools = document.createElement("div");
  tools.className = "lib-tools";

  const layoutSegs = document.createElement("div");
  layoutSegs.className = "segs";
  layoutSegs.setAttribute("role", "group");
  layoutSegs.setAttribute("aria-label", "Library layout");
  layoutSegs.append(
    segButton(
      "grid2",
      '<svg width="16" height="16" viewBox="0 0 18 18" fill="currentColor"><rect x="2" y="3" width="6" height="12" rx="1.5"/><rect x="10" y="3" width="6" height="12" rx="1.5"/></svg>',
    ),
    segButton(
      "grid3",
      '<svg width="16" height="16" viewBox="0 0 18 18" fill="currentColor"><rect x="2" y="3" width="3.6" height="12" rx="1.2"/><rect x="7.2" y="3" width="3.6" height="12" rx="1.2"/><rect x="12.4" y="3" width="3.6" height="12" rx="1.2"/></svg>',
    ),
    segButton(
      "list",
      '<svg width="16" height="16" viewBox="0 0 18 18" fill="currentColor"><rect x="2" y="3.4" width="14" height="2.6" rx="1.3"/><rect x="2" y="7.7" width="14" height="2.6" rx="1.3"/><rect x="2" y="12" width="14" height="2.6" rx="1.3"/></svg>',
    ),
  );

  const selectBtn = document.createElement("button");
  selectBtn.type = "button";
  selectBtn.className = "iconbtn";
  selectBtn.title = "Select games";
  selectBtn.setAttribute("aria-label", "Select games");
  selectBtn.innerHTML =
    '<svg width="19" height="19" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="2.5" width="15" height="15" rx="3.5"/><path d="m6.4 10.2 2.4 2.5 4.8-5"/></svg>';

  tools.append(layoutSegs, selectBtn);
  head.append(titles, tools);

  // "Check & update all cached games" (Phase 4c, WP 4c-web) — a plain
  // labeled button, not an icon (module header: the visible wording IS the
  // honesty guarantee — "Check & update", never "Check" — so this cannot be
  // reduced to a glyph + aria-label the way the icon buttons above are).
  // Its own full-width row rather than squeezed into `.lib-tools`'
  // icon-button row: that row is `flex-wrap:nowrap` and already tight with
  // the layout segments + select button, no room for this button's full
  // honest label at phone widths.
  const checkRow = document.createElement("div");
  checkRow.className = "lib-checkrow";
  const checkUpdateBtn = document.createElement("button");
  checkUpdateBtn.type = "button";
  checkUpdateBtn.className = "btn ghost sm";
  const startsBusy = checkAndUpdateAction.isInFlight();
  checkUpdateBtn.disabled = startsBusy;
  checkUpdateBtn.textContent = startsBusy ? CHECK_UPDATE_BUSY_LABEL : CHECK_UPDATE_LABEL;
  checkRow.appendChild(checkUpdateBtn);
  // WP WEB-FEAT-1: the owned-list status line (and its Reload / Open
  // Settings buttons) lives INSIDE `.lib-checkrow`, not as a new child of
  // the section: `.view-library`'s BP-L grid assigns its six in-flow
  // children an area each, by class (css/app.css), and a seventh would land
  // in an implicit row.
  const ownedNote = document.createElement("p");
  ownedNote.className = "lib-owned";
  ownedNote.hidden = true;
  const ownedText = document.createElement("span");
  ownedText.className = "lib-owned-text";
  ownedText.setAttribute("role", "status");
  const ownedSettingsBtn = noticeButton(NOTICE_ACTION.SETTINGS);
  const ownedReloadBtn = noticeButton(NOTICE_ACTION.RELOAD);
  ownedNote.append(ownedText, ownedSettingsBtn, ownedReloadBtn);
  checkRow.appendChild(ownedNote);

  const searchWrap = document.createElement("div");
  searchWrap.className = "search";
  const mag = document.createElement("span");
  mag.className = "mag";
  mag.setAttribute("aria-hidden", "true");
  mag.innerHTML =
    '<svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="8.8" cy="8.8" r="5.6"/><path d="m13 13 4 4"/></svg>';
  const searchInput = document.createElement("input");
  searchInput.type = "text";
  searchInput.id = "lib-q";
  searchInput.inputMode = "search";
  searchInput.placeholder = "Search your library";
  searchInput.autocomplete = "off";
  searchInput.spellcheck = false;
  searchInput.setAttribute("aria-label", "Search library by title");
  searchInput.value = state.query;
  const qClear = document.createElement("button");
  qClear.type = "button";
  qClear.className = "qx";
  qClear.setAttribute("aria-label", "Clear search");
  qClear.innerHTML =
    '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="m5.5 5.5 9 9M14.5 5.5l-9 9"/></svg>';
  searchWrap.append(mag, searchInput, qClear);
  searchWrap.classList.toggle("filled", state.query.length > 0);

  const chips = document.createElement("div");
  chips.className = "chips";
  chips.setAttribute("role", "group");
  chips.setAttribute("aria-label", "Filter library");

  const grid = document.createElement("div");
  grid.className = "grid";

  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "Long-press (or right-click) a game to select several.";

  // ---- bulk bar ----
  const bulkBar = document.createElement("div");
  bulkBar.className = "bulk";
  const bulkHead = document.createElement("div");
  bulkHead.className = "bulkhead";
  const count = document.createElement("span");
  count.className = "count";
  const bulkCount = document.createElement("b");
  bulkCount.textContent = "0";
  const bulkHidden = document.createElement("span");
  bulkHidden.className = "hid";
  count.append(bulkCount, " selected", bulkHidden);
  const bulkCancel = document.createElement("button");
  bulkCancel.type = "button";
  bulkCancel.className = "btn ghost sm";
  bulkCancel.textContent = "Cancel";
  bulkHead.append(count, bulkCancel);
  const bulkNote = document.createElement("p");
  bulkNote.className = "bulknote";
  const bulkBtns = document.createElement("div");
  bulkBtns.className = "bulkbtns";
  const bulkDelete = document.createElement("button");
  bulkDelete.type = "button";
  bulkDelete.className = "btn danger sm";
  bulkDelete.style.display = "none";
  const bulkSecondary = document.createElement("button");
  bulkSecondary.type = "button";
  bulkSecondary.className = "btn ghost sm";
  bulkSecondary.style.display = "none";
  const bulkPrimary = document.createElement("button");
  bulkPrimary.type = "button";
  bulkPrimary.className = "btn primary sm";
  bulkPrimary.textContent = "Download";
  bulkBtns.append(bulkDelete, bulkSecondary, bulkPrimary);
  bulkBar.append(bulkHead, bulkNote, bulkBtns);

  // The delete-confirm dialog is NOT built here — see the module-level
  // block near the top of this file (WP 4a.8: it must be a `document.body`
  // sibling of `#app`, not a child of this per-mount `<section>`, or
  // marking `#app` inert would also make the dialog itself inert).
  section.append(head, checkRow, searchWrap, chips, grid, hint, bulkBar);

  els = {
    sub,
    ownedNote,
    ownedText,
    ownedSettingsBtn,
    ownedReloadBtn,
    layoutSegs,
    selectBtn,
    checkUpdateBtn,
    searchWrap,
    searchInput,
    qClear,
    chips,
    grid,
    bulkBar,
    bulkCount,
    bulkHidden,
    bulkCancel,
    bulkNote,
    bulkDelete,
    bulkSecondary,
    bulkPrimary,
  };

  // ---- static listeners (wired once per mount — see mounted()) ----
  searchInput.addEventListener("input", (e) => applySearch(e.target.value));
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      clearSearch();
    }
  });
  qClear.addEventListener("click", clearSearch);

  chips.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    state.filterKey = btn.dataset.f;
    fullRender();
  });

  layoutSegs.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-layout]");
    if (!btn) return;
    setLayout(btn.dataset.layout, true);
  });

  selectBtn.addEventListener("click", () => {
    if (state.selecting) exitSelect();
    else enterSelect(null);
  });

  checkUpdateBtn.addEventListener("click", onCheckAndUpdate);

  bulkCancel.addEventListener("click", exitSelect);

  bulkPrimary.addEventListener("click", async () => {
    if (!currentBulkPrimaryTargets.length) return;
    const targets = currentBulkPrimaryTargets;
    const skipped = state.picked.size - targets.length;
    try {
      await api.prefill(targets);
      // "skipped", not "already cached" (WP WEB-FEAT-1): the skipped picks
      // can be busy or owned-only games too — the bulk note above already
      // said which.
      showToast(
        `${targets.length} job${targets.length > 1 ? "s" : ""} queued` +
          (skipped ? ` · ${skipped} skipped` : ""),
      );
      exitSelect();
      store.refreshNow();
    } catch (err) {
      showToast(errorText(err), { warn: true });
    }
  });

  bulkSecondary.addEventListener("click", async () => {
    if (!currentBulkSecondaryTargets.length) return;
    const targets = currentBulkSecondaryTargets;
    try {
      await api.prefill(targets);
      showToast(`${targets.length} re-download${targets.length > 1 ? "s" : ""} queued`);
      exitSelect();
      store.refreshNow();
    } catch (err) {
      showToast(errorText(err), { warn: true });
    }
  });

  bulkDelete.addEventListener("click", () => {
    if (currentBulkDeleteIds.length) openDeleteConfirm(currentBulkDeleteIds);
  });

  // dNo/dYes are wired ONCE at module load — see the module-level dialog
  // construction block near the top of this file.

  return section;
}

// ---------------------------------------------------------------------
// Games-tick patch/rebuild (WP 4a.3 review fix, blocker B1)
//
// The round-7 mockup rule ("rebuild DOM only when a card's STATE changes...
// patch the volatile values in place... touch no animated node") is
// binding for the GAMES poll too, not just the jobs one — the 15 s games
// poll drifts `size_bytes` upward for a running download independently of
// the jobs poll, and a naive "any updated row -> rebuild" would recreate
// the animated status-icon `<svg>` subtree (restarting its CSS animation)
// on every such tick. `js/lib/render-plan.js`'s `planGamesUpdate` is the
// pure decision (structural key changed -> rebuild; unchanged -> patch);
// everything below is the DOM-side execution of that decision.
// ---------------------------------------------------------------------

/** appid -> the `data-dk` currently painted on that appid's card, for
 * every card ON SCREEN right now. Read fresh from the live grid each time
 * (cheap — a handful of cards) rather than kept as separate bookkeeping
 * state that could drift from what is actually painted. */
function currentCardKeys() {
  const map = new Map();
  if (!els) return map;
  for (const card of els.grid.querySelectorAll(".card[data-appid]")) {
    map.set(Number(card.dataset.appid), card.dataset.dk);
  }
  return map;
}

function computeCardKey(game) {
  return cardStructuralKey(game, liveJobsByAppid.get(game.appid));
}

/** Would `game` still be part of the currently visible (search + chip
 * filtered) list? Used to bail a per-card rebuild out to a full render
 * when a structural transition would also change GRID MEMBERSHIP (e.g.
 * the active chip is "Failed" and this game just stopped being an error) —
 * reuses the same tested predicate the initial render is built from
 * (library-filters.js), so there is only one definition of "visible" in
 * this file. */
function stillMatchesCurrentView(game) {
  return (
    visibleGames([game], {
      query: state.query,
      filterKey: state.filterKey,
      liveJobsByAppid,
    }).length === 1
  );
}

/** Rebuild exactly one card (a genuine structural transition) — every
 * OTHER card on screen, including one that is actively animating, is left
 * completely untouched. */
function rebuildCard(appid) {
  const game = state.games.find((g) => g.appid === appid);
  const cardEl = els.grid.querySelector(`.card[data-appid="${appid}"]`);
  if (!game || !cardEl) return;
  const newCard = buildCard(game, {
    liveJob: liveJobsByAppid.get(appid),
    picked: state.picked.has(appid),
    selecting: state.selecting,
    onOpen,
    onLongPress: enterSelect,
    onToggle,
    onAction,
  });
  cardEl.replaceWith(newCard);
}

/** Patch exactly one card's volatile text (no node created/removed/
 * replaced — the status-icon subtree, and any animation running on it, is
 * never touched). Only ever called for an appid `render-plan.js` has
 * already established has an UNCHANGED structural key. */
function patchCard(appid) {
  const game = state.games.find((g) => g.appid === appid);
  const cardEl = els.grid.querySelector(`.card[data-appid="${appid}"]`);
  if (!game || !cardEl) return;
  patchCardVolatile(cardEl, game, cardEl.dataset.dk);
}

/** One `GET /v1/games` tick: patch/rebuild exactly the cards that need it,
 * or fall back to a full render when the plan says so or when a
 * structural transition would also change which cards the current
 * filter/search shows. */
function applyGamesTick(diff) {
  const plan = planGamesUpdate(diff, currentCardKeys(), computeCardKey);
  if (plan.full) {
    fullRender();
    return;
  }
  for (const appid of plan.rebuild) {
    const game = state.games.find((g) => g.appid === appid);
    if (!game || !stillMatchesCurrentView(game)) {
      // A structural change that also moves this card in or out of the
      // active filter/chip view — grid MEMBERSHIP, not just this card's
      // own content. Bail to a full render for the whole tick, same as
      // the mockup's own structural-mismatch fallback
      // (`patchGridProgress`'s `if (card.dataset.dk !== dispKind(...))
      // renderGrid()`).
      fullRender();
      return;
    }
    rebuildCard(appid);
  }
  for (const appid of plan.patch) patchCard(appid);

  if (plan.rebuild.length) {
    // Only a REBUILT card can have changed `dispKind` (a patch-only update
    // is, by definition, the same structural key), so the chip counts and
    // "N on the cache" subtitle only need refreshing when something was
    // rebuilt — cheap either way (chips carry no animation to protect) but
    // no reason to touch them on a pure size-drift tick.
    renderChips();
    updateSubtitle();
    syncBulk();
  }
}

// ---------------------------------------------------------------------
// Store subscriptions — set up ONCE at module load, never per mount (see
// store-singleton.js's header for why: views are re-created on every
// navigation with no unmount hook, so subscribing per-render would stack
// duplicate listeners). Every callback is a safe no-op while a different
// view is mounted.
// ---------------------------------------------------------------------

store.subscribe("games", ({ items, diff }) => {
  if (!Array.isArray(items)) return; // {error} payload — nothing to render
  state.vaultGames = items;
  // Re-merge with the owned list the loader already holds — a games tick
  // never re-fetches it (WP WEB-FEAT-1, no relay polling).
  remerge();
  if (!mounted()) return;
  applyGamesTick(diff);
});

store.subscribe("jobs", ({ items, diff }) => {
  if (!Array.isArray(items)) return;
  const prevLive = liveJobsByAppid;
  const newLive = indexLiveJobsByAppid(items);
  state.jobs = items;

  let structuralChange = !diff || diff.isFirst;
  if (!structuralChange) {
    const appids = new Set([...prevLive.keys(), ...newLive.keys()]);
    for (const appid of appids) {
      if (isJobStateTransition(prevLive.get(appid), newLive.get(appid))) {
        structuralChange = true;
        break;
      }
    }
  }
  liveJobsByAppid = newLive;
  // The bulk bar's busy/queued classification also depends on non-live
  // (queued) jobs, so it is always kept in sync even when nothing
  // structural happened to a card (cheap: only touches the bar, no cards).
  if (mounted()) syncBulk();
  if (structuralChange) fullRender();
});

onViewChange((view) => {
  if (view === "library") return;
  if (state.selecting) {
    state.selecting = false;
    state.picked.clear();
    document.body.classList.remove("selecting");
  }
  // Navigation dismisses transient surfaces (mockup rule) — the delete
  // dialog is now a `document.body`-level sibling of `#app` (module-level
  // construction block, WP 4a.8 fix) so it survives this view's own DOM
  // being torn down, and closing it here is purely the UX rule, not a
  // leak-prevention measure the way it would have been before that fix
  // (when the dialog was destroyed WITH the section, silently leaving
  // `lib/modal-stack.js`'s stack referencing a detached element and `#app`
  // stuck `inert` forever). Safe to call unconditionally — a no-op when the
  // dialog was not open (see closeDeleteConfirm()).
  closeDeleteConfirm();
  // Release the detached DOM tree — app.js's replaceChildren() already
  // detached it, but this module held its own reference in `sectionEl`
  // (mounted()'s isConnected check would already report false, this just
  // stops the tree from being reachable, and therefore keepable by the
  // GC, through this module for no reason once we've navigated away).
  sectionEl = null;
});

export function renderLibrary() {
  const section = buildSection();
  sectionEl = section;
  fullRender();
  // WP WEB-FEAT-1: the one "view open" fetch of the stored SteamID64 and its
  // owned list. The other caller of load() is the Reload button.
  ownedLibrary.load();
  return section;
}
