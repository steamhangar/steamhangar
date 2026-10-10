/**
 * Game detail sheet (WP 4a.4).
 *
 * Cover/name/status, sizes, the honest last-download/confirmed-current
 * wording, per-depot sharing with the four-state wording (recorded WP 4b.6
 * divergence, adopted here per this WP's brief), download/pause/resume/
 * cancel, delete with a per-depot freed/kept preview, and the
 * dry-run -> confirm -> execute GC flow. Opened from a Library card
 * (`views/library.js`'s `onOpen`, the hook that WP reserved for this one)
 * and from the notifications bell's `update_ready` rows (the WP 4a.7 TODO
 * this WP closes — `lib/notification-log.js`'s `NAVIGATION_KIND`).
 *
 * Built on the WP 4a.7 `sheet-dialog.js` scaffold (reused, not
 * re-implemented) for the same a11y reasons `components/clients-sheet.js`
 * and `components/notifications.js` already document: `role="dialog"` +
 * `aria-modal` + focus-on-open + Escape-to-close + backdrop-tap-to-close.
 * The delete confirm and the GC execute confirm are separate, persistent
 * `.dialog-backdrop`/`.dialog` overlays reusing `views/library.js`'s own
 * bulk-delete-confirm markup/CSS classes (`.cflist`/`.cflabel`/`.cftotal`/
 * `.cfsolo`) verbatim, so single-game and bulk delete never describe the
 * same arithmetic two different ways — same reasoning the Android sibling's
 * `DetailDeleteConfirmDialog` kdoc gives for reusing `library_delete_*`.
 *
 * **Depot sharing is computed, never stored, live** (mockup-notes.md round
 * 3, mirrored from the Android sibling's `DetailController` kdoc). `open()`
 * fetches `detail`/`mapping` ONCE; the actual sharing arithmetic
 * (`buildMultiPlan`/`buildDepotPresentation`) is re-derived on every games/
 * jobs poll tick from those fields PLUS the live store snapshots, so a
 * co-owner's cache state changing on the next poll while the sheet stays
 * open updates the sharing wording without a second `GET /v1/games/{appid}`
 * round trip.
 *
 * **Round-7 patch-in-place** (WP brief: "the sheet must not rebuild
 * animated nodes on poll ticks — patch volatile values, reuse the
 * render-plan pattern"). `lib/detail-render-plan.js`'s
 * `buildDetailStructuralKey` decides, per games/jobs tick, whether the
 * sheet's SHAPE changed (header status icon, job-control buttons, a
 * depot's sharing tag) — full `render()` only on a genuine change,
 * `patchVolatile()` (sizes, timestamps, co-owner cached/not-cached text)
 * on everything else, so a running download's status-icon animation
 * (`css/theme.css`'s `.sic.k-running`) is never restarted by an unrelated
 * byte-progress tick.
 *
 * GC job polling is its OWN loop, job-id bound (`generation` guards against
 * a stale loop outliving a `close()`/`reset()`/new dry run — the closest
 * plain-JS equivalent to the Android sibling's coroutine-`Job`
 * cancellation), completely independent of the shared `store-singleton.js`
 * poll loops: `GET /v1/jobs/{id}` (for `log_excerpt`) is never in that
 * store's vocabulary, same reasoning `views/downloads.js`'s lazy history-row
 * fetch documents.
 *
 * **Focus trap + background inert (WP 4a.8).** The sheet itself gets this
 * for free from `sheet-dialog.js`'s own WP 4a.8 wiring. The delete-confirm
 * and GC-execute-confirm overlays below are bespoke (not `createSheetDialog`
 * instances — they reuse `views/library.js`'s delete-dialog markup, see the
 * header above) and had NO keyboard support at all before this WP: no
 * Escape, no focus-on-open, no trap. They now push/pop onto the same
 * `lib/modal-stack.js` stack the sheet uses, which — because they open ON
 * TOP of the already-open sheet — also correctly makes the SHEET inert
 * while one of them is up (see that module's header, "why a stack").
 *
 * DOM-building component, not unit-tested directly (same posture as
 * `sheet-dialog.js`/`clients-sheet.js`/`notifications.js` — see
 * `sheet-dialog.js`'s header); every piece of DECISION logic it leans on
 * (`lib/depot-presentation.js`, `lib/detail-job.js`, `lib/detail-wording.js`,
 * `lib/gc-flow.js`, `lib/gc-log-summary.js`, `lib/detail-render-plan.js`,
 * plus the already-covered `lib/multiplan.js`/`lib/game-status.js`) is pure
 * and covered headlessly in web/tests/.
 *
 * **Header/hero art (WP 4h.3).** `components/header-art.js`'s
 * `buildHeaderArt(appid)` is appended once, first, in the loaded-detail
 * success branch of `render()` only (not the loading/error/not-tracked
 * states — nothing to show a hero for yet). That module owns the graceful-
 * absence and no-layout-shift discipline; this file only decides WHEN to
 * build one (every full `render()`, same treatment `buildHeader()`'s mini
 * cover already gets — never touched by `patchVolatile()`, so a poll tick
 * that doesn't change the structural key never re-fetches it).
 */

import { store } from "../store-singleton.js";
import { api } from "../api.js";
import { showToast } from "./toast.js";
import { createStatusIcon, STATUS_LABEL } from "./status-icon.js";
import { createSheetDialog } from "./sheet-dialog.js";
import { buildHeaderArt } from "./header-art.js";
import { onViewChange } from "../router.js";
import { formatBytesGB, formatTimestamp } from "../lib/format.js";
import { coverArtUrl, fallbackHues } from "../lib/cover-art.js";
import {
  dispKind,
  findLiveJob,
  statusAction,
  hasVisibleCacheContent,
  hasProtectedCacheContent,
  installedBadgeState,
  installedSectionPresence,
  INSTALLED_BADGE,
  isToolApp,
  statusIconKind,
  statusWordFor,
  TOOL_APP_NOTE,
} from "../lib/game-status.js";
import { buildMultiPlan } from "../lib/multiplan.js";
import { buildDepotPresentation, DEPOT_TAG } from "../lib/depot-presentation.js";
import { findTrackedJob, detailJobActions, DETAIL_JOB_ACTION } from "../lib/detail-job.js";
import { confirmedCurrentWording, CONFIRMED_CURRENT_WORDING } from "../lib/detail-wording.js";
import { GC_STATE, GC_EVENT, idleGcState, reduceGcFlow } from "../lib/gc-flow.js";
import { buildDetailStructuralKey } from "../lib/detail-render-plan.js";
import { pushModal, popModal } from "../lib/modal-stack.js";
import { appTitle, fillMissingNames, ownedNamesByAppid } from "../lib/owned-library.js";
import { ownedLibrary } from "../owned-singleton.js";

const ACTIVE_JOB_STATUSES = ["queued", "running", "paused"];
const GC_POLL_INTERVAL_MS = 1200;

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed";
}
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
function activeJobAppidsFrom(jobs) {
  return new Set(jobs.filter((j) => ACTIVE_JOB_STATUSES.includes(j.status)).map((j) => j.appid));
}
function namesFor(appids, gamesByAppid) {
  return appids.map((id) => appTitle(id, gamesByAppid.get(id)?.name)).join(", ");
}
/** WP WEB-FIX-4: vault rows by appid with names filled from the owned
 * list (adds no rows, see lib/owned-library.js's fillMissingNames). */
function namedGamesByAppid() {
  return new Map(fillMissingNames(state.games, ownedLibrary.current().games).map((g) => [g.appid, g]));
}

// ---------------------------------------------------------------------
// Module-level state — one sheet instance for the whole app, same posture
// as clients-sheet.js/notifications.js (views are re-created on navigation
// with no unmount hook; this component is not view-scoped at all).
// ---------------------------------------------------------------------
const state = {
  appid: null,
  name: null, // fallback display name captured at open() time
  detail: null,
  mapping: [],
  notTracked: false,
  loadError: null,
  games: store.snapshot("games") || [],
  jobs: store.snapshot("jobs") || [],
  gcState: idleGcState(),
};

/** appid -> "reachable" token. Bumped on every open()/close()/GC restart so
 * an in-flight fetch or poll loop from a superseded call can recognize it
 * no longer owns the sheet and drop its result — the plain-JS analogue of
 * cancelling the Android sibling's coroutine `Job`. */
let generation = 0;

/** depotid -> expanded (co-owner panel open), persists across re-renders
 * for the currently-open game (cleared on open() for a different appid). */
const depotExpanded = new Map();

/** The structural signature `render()` last painted, for the round-7
 * patch-vs-rebuild decision on the next games/jobs poll tick — see
 * lib/detail-render-plan.js. `null` while nothing (or a non-loaded state)
 * is shown. */
let lastStructuralKey = null;

// ---------------------------------------------------------------------
// Sheet scaffold (WP 4a.7's createSheetDialog, reused verbatim)
// ---------------------------------------------------------------------

// WP 4e.3: "center" — the operator's decision puts the detail sheet at eye
// level as a centred card from BP-L up (docs/PROJECT_PLAN.md's Phase 4e
// section); below BP-L it stays the mockup's bottom sheet, unchanged. See
// sheet-dialog.js's header for why this is a plain, static modifier class
// and not a second state model.
const dialog = createSheetDialog({ ariaLabel: "Game detail", variant: "center" });

const contentEl = document.createElement("div");
const gcSectionEl = document.createElement("div");
const closeBtn = document.createElement("button");
closeBtn.type = "button";
closeBtn.className = "btn wide ghost";
closeBtn.textContent = "Close";
closeBtn.addEventListener("click", () => closeDetail());
dialog.body.append(contentEl, gcSectionEl, closeBtn);

// ---------------------------------------------------------------------
// Delete confirm — a second persistent overlay, reusing views/library.js's
// own bulk-delete-confirm markup/CSS 1:1 (`.dialog-backdrop`/`.dialog`/
// `.cflist`/`.cflabel`/`.cftotal`/`.cfsolo`) so single-game and bulk delete
// never describe the same arithmetic two different ways.
// ---------------------------------------------------------------------

const deleteBackdrop = document.createElement("div");
deleteBackdrop.className = "dialog-backdrop";
const deleteDialogEl = document.createElement("div");
deleteDialogEl.className = "dialog";
deleteDialogEl.setAttribute("role", "alertdialog");
deleteDialogEl.setAttribute("aria-modal", "true");
deleteDialogEl.setAttribute("aria-label", "Confirm deletion");
const dTitle = document.createElement("h3");
dTitle.textContent = "Delete from cache?";
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
deleteDialogEl.append(dTitle, dText, dNote, dRow);
deleteBackdrop.appendChild(deleteDialogEl);
document.body.appendChild(deleteBackdrop);

dNo.addEventListener("click", closeDeleteConfirm);
dYes.addEventListener("click", confirmDelete);

/** Element to return focus to on close, captured fresh on every open() —
 * same pattern as `sheet-dialog.js`'s `invokerEl` (WP 4a.8: this dialog had
 * no focus management at all before this WP). */
let deleteInvokerEl = null;

function openDeleteConfirm() {
  dText.textContent = "Calculating what this would free…";
  dNote.replaceChildren();
  dYes.disabled = true;
  deleteInvokerEl = document.activeElement;
  deleteBackdrop.classList.add("on");
  // WP 4a.8: also makes the sheet BEHIND this dialog inert; Escape routes
  // through lib/modal-stack.js's single dispatcher (see its header) rather
  // than a listener bound here directly — an independent one raced with the
  // sheet's own and closed BOTH on one Escape press, found live.
  pushModal(deleteBackdrop, closeDeleteConfirm);
  // "Keep" (the non-destructive default) rather than "Delete" — the usual
  // alertdialog convention of not defaulting focus onto the dangerous action.
  dNo.focus();

  const gamesByAppid = namedGamesByAppid();
  const activeJobAppids = activeJobAppidsFrom(state.jobs);
  const plan = buildMultiPlan([state.appid], {
    details: [state.detail],
    mapping: state.mapping,
    gamesByAppid,
    activeJobAppids,
  });
  renderDeletePlan(plan, gamesByAppid);
  dYes.disabled = false;
}

function closeDeleteConfirm() {
  deleteBackdrop.classList.remove("on");
  popModal(deleteBackdrop);
  if (deleteInvokerEl && typeof deleteInvokerEl.focus === "function") deleteInvokerEl.focus();
  deleteInvokerEl = null;
}

function renderDeletePlan(plan, gamesByAppid) {
  const freedText = formatBytesGB(plan.freedBytes) || "0 GB";
  const occupiedText = formatBytesGB(plan.occupiedBytes) || "0 GB";
  const name = state.detail?.name || state.name || `App ${state.appid}`;
  dText.textContent = `Deleting ${name} frees about ${freedText} of the ${occupiedText} it occupies.`;

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
  const b1 = document.createElement("b");
  b1.textContent = freedText;
  const b2 = document.createElement("b");
  b2.textContent = formatBytesGB(plan.keptBytes) || "0 GB";
  total.append(b1, " freed · ", b2, " stays on disk");
  dNote.appendChild(total);
}

async function confirmDelete() {
  const appid = state.appid;
  dYes.disabled = true;
  dNo.disabled = true;
  try {
    // Reports what the SERVER actually freed (`CacheDeletionOut.total_bytes_freed`),
    // never this dialog's preview plan — the server re-checks every depot's
    // co-owner state immediately before removing it and is the authority
    // (same posture views/library.js's own post-delete toast documents).
    const result = await api.deleteCache(appid);
    closeDeleteConfirm();
    showToast(`Freed ${formatBytesGB(result.total_bytes_freed) || "0 GB"}`);
    store.refreshNow();
    closeDetail();
  } catch (err) {
    closeDeleteConfirm();
    showToast(errorText(err), { warn: true });
  } finally {
    dYes.disabled = false;
    dNo.disabled = false;
  }
}

// ---------------------------------------------------------------------
// GC execute confirm — a third persistent overlay, same reasoning: opens
// ONLY from lib/gc-flow.js's CONFIRM_EXECUTE state, never directly.
// ---------------------------------------------------------------------

const gcConfirmBackdrop = document.createElement("div");
gcConfirmBackdrop.className = "dialog-backdrop";
const gcConfirmDialogEl = document.createElement("div");
gcConfirmDialogEl.className = "dialog";
gcConfirmDialogEl.setAttribute("role", "alertdialog");
gcConfirmDialogEl.setAttribute("aria-modal", "true");
gcConfirmDialogEl.setAttribute("aria-label", "Confirm garbage collection");
const gcTitle = document.createElement("h3");
gcTitle.textContent = "Delete orphaned chunks?";
const gcText = document.createElement("p");
const gcRow = document.createElement("div");
gcRow.className = "row";
const gcNo = document.createElement("button");
gcNo.type = "button";
gcNo.className = "btn ghost sm";
gcNo.textContent = "Cancel";
const gcYes = document.createElement("button");
gcYes.type = "button";
gcYes.className = "btn danger sm";
gcYes.textContent = "Delete";
gcRow.append(gcNo, gcYes);
gcConfirmDialogEl.append(gcTitle, gcText, gcRow);
gcConfirmBackdrop.appendChild(gcConfirmDialogEl);
document.body.appendChild(gcConfirmBackdrop);

gcNo.addEventListener("click", dismissGcExecuteConfirm);
gcYes.addEventListener("click", confirmGcExecute);

/** Edge-detected open/close state for the modal-stack push/pop and the
 * invoker-focus capture in `renderGcSection()` below (WP 4a.8) — needed
 * because `renderGcSection()` is called on every GC state change, not only
 * the transitions into/out of `CONFIRM_EXECUTE`, so a naive "push on every
 * call while showing" would keep stealing focus back to "Cancel" on an
 * unrelated re-render (e.g. the main sheet's own structural re-render
 * calling this while the confirm happens to still be open). */
let gcConfirmWasOpen = false;
let gcConfirmInvokerEl = null;

// ---------------------------------------------------------------------
// open / close
// ---------------------------------------------------------------------

/**
 * Open the sheet for `appid`, fetching `GET /v1/games/{appid}` +
 * `GET /v1/mapping` once (WP brief: opened from a Library card — `name` is
 * the card's already-known display name, shown while the fetch is in
 * flight and for the not-tracked empty state where the fetch never returns
 * one at all).
 * @param {number} appid
 * @param {string} [name]
 */
export function openDetail(appid, name) {
  generation++;
  state.appid = appid;
  // WP WEB-FIX-4: an opener without a name (a notification for a job whose
  // vault row has none) still gets the owned list's title.
  state.name = name || ownedNamesByAppid(ownedLibrary.current().games).get(appid) || null;
  state.detail = null;
  state.mapping = [];
  state.notTracked = false;
  state.loadError = null;
  state.gcState = idleGcState();
  depotExpanded.clear();
  lastStructuralKey = null;

  render();
  dialog.open();

  const myGeneration = generation;
  const myAppid = appid;
  (async () => {
    try {
      const [detail, mapping] = await Promise.all([api.game(appid), api.mapping()]);
      if (generation !== myGeneration || state.appid !== myAppid) return; // superseded
      state.detail = detail;
      state.mapping = mapping;
    } catch (err) {
      if (generation !== myGeneration || state.appid !== myAppid) return;
      if (err && err.kind === "not_found") {
        state.notTracked = true;
      } else {
        state.loadError = errorText(err);
      }
    }
    if (generation === myGeneration && state.appid === myAppid) render();
  })();
}

function closeDetail() {
  generation++;
  state.appid = null;
  // WP 4a.8: close any confirm overlay stacked on top of the sheet too —
  // both are no-ops when not open (closeDeleteConfirm unconditionally, and
  // dismissGcExecuteConfirm via reduceGcFlow's own DISMISS_CONFIRM ->
  // no-op-outside-CONFIRM_EXECUTE guard — see lib/gc-flow.js). Without this,
  // closing the sheet while one of them was open left it permanently
  // pushed on lib/modal-stack.js's stack, i.e. #app stuck `inert` forever.
  closeDeleteConfirm();
  dismissGcExecuteConfirm();
  dialog.close();
}

// Navigation dismisses transient surfaces (mockup rule) — closeDetail(),
// not a bare dialog.close(), so a confirm overlay left open when the user
// navigates away doesn't strand #app inert (see closeDetail()'s comment).
onViewChange(() => closeDetail());

// ---------------------------------------------------------------------
// Live game/job lookups
// ---------------------------------------------------------------------

/** `GET /v1/games` row for the open appid if the store already has one
 * (kept current by the ambient poll — no extra fetch), falling back to the
 * one-shot `detail` fetched at open() otherwise. Both shapes carry the same
 * status/last_prefill_at/last_manifest_check/size_bytes/needs_force fields
 * (api/vault_api/routers/games.py's GameSummary/GameDetail), so every
 * lib/game-status.js helper works unchanged against either. */
function currentGameLike() {
  if (!state.appid) return null;
  return state.games.find((g) => g.appid === state.appid) || state.detail;
}

function currentDepotPresentations() {
  if (!state.detail || !state.detail.depots.length) return [];
  const gamesByAppid = namedGamesByAppid();
  const activeJobAppids = activeJobAppidsFrom(state.jobs);
  const plan = buildMultiPlan([state.appid], {
    details: [state.detail],
    mapping: state.mapping,
    gamesByAppid,
    activeJobAppids,
  });
  const gameLike = currentGameLike();
  const thisAppIsHolder = gameLike ? hasProtectedCacheContent(gameLike, activeJobAppids.has(state.appid)) : false;
  return plan.rows.map((row) => buildDepotPresentation(row, gamesByAppid, thisAppIsHolder));
}

function computeStructuralKey() {
  const gameLike = currentGameLike();
  if (!gameLike) return null;
  const liveJob = findLiveJob(state.jobs, state.appid);
  const trackedJob = findTrackedJob(state.jobs, state.appid);
  const presentations = currentDepotPresentations();
  return buildDetailStructuralKey({
    dispKind: dispKind(gameLike, liveJob),
    trackedJobStatus: trackedJob ? trackedJob.status : null,
    depotTags: presentations.map((p) => p.tag),
    // WP AG-2 (review S4 fix): ONLY whether the "Installed on" section
    // exists at all (none <-> present) is structural — CACHED vs
    // NOT_CACHED within an already-existing section is deliberately NOT,
    // per `installedSectionPresence`'s own header (a live download can
    // cross that sub-state while dispKind stays "running" the whole time;
    // feeding the raw 3-state value here forced an unwanted full re-render
    // every such tick). `patchInstalledSection` keeps the note in sync for
    // that sub-state on every patch tick instead.
    installedBadge: installedSectionPresence(gameLike),
  });
}

// ---------------------------------------------------------------------
// Rendering — full rebuild
// ---------------------------------------------------------------------

function confirmedCurrentText(lastPrefillAt, lastManifestCheck) {
  const wording = confirmedCurrentWording(lastPrefillAt, lastManifestCheck);
  if (wording === CONFIRMED_CURRENT_WORDING.NEVER_CONFIRMED) return "Not yet confirmed current.";
  const when = formatTimestamp(lastManifestCheck);
  if (wording === CONFIRMED_CURRENT_WORDING.CONFIRMED_BEFORE_CACHE_CLEARED) {
    return `Confirmed current at ${when} (before the cache was cleared).`;
  }
  return `Confirmed current at ${when}.`;
}

function buildMiniCover(appid, coverUrl) {
  const { h1, h2 } = fallbackHues(appid);
  const cap = document.createElement("div");
  cap.className = "cap";
  cap.style.setProperty("--h1", String(h1));
  cap.style.setProperty("--h2", String(h2));
  const img = document.createElement("img");
  img.className = "cover";
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.addEventListener("error", () => img.remove(), { once: true });
  img.src = coverArtUrl(appid, coverUrl);
  cap.appendChild(img);
  return cap;
}

function buildHeader(gameLike, liveJob) {
  const dhead = document.createElement("div");
  dhead.className = "dhead";
  // WP API-FIX-5: the detail's server-resolved cover, else the list row's.
  dhead.appendChild(buildMiniCover(state.appid, state.detail?.cover_url ?? gameLike?.cover_url));

  const info = document.createElement("div");
  const h2 = document.createElement("h2");
  // WP API-FIX-4: a tool app's name comes from the server's tool list.
  h2.textContent = state.detail?.name?.trim() || toolAppName(gameLike) || state.name || `App ${state.appid}`;
  const appidLine = document.createElement("div");
  appidLine.className = "appid";
  appidLine.textContent = `App ${state.appid}`;
  info.append(h2, appidLine);

  const kind = dispKind(gameLike, liveJob);
  const statusRow = document.createElement("div");
  statusRow.className = "detail-statusrow";
  const statusIcon = createStatusIcon(statusIconKind(kind), { size: "sm" });
  // WP 4a.8 icon audit: the word right after this icon already says the
  // same thing visibly — hide the icon's own sr-only label so it is not
  // announced twice (same "avoid double announcement" posture as
  // clients-sheet.js/notifications.js's status icons).
  statusIcon.setAttribute("aria-hidden", "true");
  statusRow.appendChild(statusIcon);
  const word = document.createElement("span");
  word.className = "tx-" + kind;
  word.textContent = statusWordFor(kind, STATUS_LABEL);
  statusRow.appendChild(word);
  info.appendChild(statusRow);

  dhead.appendChild(info);
  return dhead;
}

/** WP API-FIX-4: the tool app's server-supplied name, or null. */
function toolAppName(gameLike) {
  if (!isToolApp(gameLike)) return null;
  const name = gameLike.tool_app_name;
  return typeof name === "string" && name.trim() ? name.trim() : null;
}

/**
 * WP API-FIX-4: the neutral note a Steam tool app's sheet shows where a
 * game would offer Download/Retry (lib/game-status.js `TOOL_APP_NOTE`).
 * Exported for web/tests/tool-app.test.js, same named-exception posture as
 * `buildInstalledSection` below.
 * @param {object} gameLike
 * @returns {HTMLElement | null} `null` for every ordinary app.
 */
export function buildToolAppNote(gameLike) {
  if (!isToolApp(gameLike)) return null;
  const note = document.createElement("p");
  note.className = "hint detail-block";
  note.dataset.role = "tool-note";
  note.textContent = TOOL_APP_NOTE;
  return note;
}

function buildFactLines(gameLike) {
  const facts = document.createElement("div");
  facts.className = "facts detail-block";

  const sizeLine = document.createElement("div");
  sizeLine.dataset.role = "size";
  sizeLine.textContent = formatBytesGB(gameLike.size_bytes) || "Size unknown";
  facts.appendChild(sizeLine);

  const lastDlLine = document.createElement("div");
  lastDlLine.dataset.role = "lastdl";
  lastDlLine.textContent = gameLike.last_prefill_at
    ? `Last downloaded ${formatTimestamp(gameLike.last_prefill_at)}`
    : "Never downloaded";
  facts.appendChild(lastDlLine);

  const confirmedLine = document.createElement("div");
  confirmedLine.dataset.role = "confirmed";
  confirmedLine.textContent = confirmedCurrentText(gameLike.last_prefill_at, gameLike.last_manifest_check);
  facts.appendChild(confirmedLine);

  // WP API-FIX-4: no "next download" for a tool app, so no --force line.
  if (gameLike.needs_force && !isToolApp(gameLike)) {
    const needsForceLine = document.createElement("div");
    needsForceLine.className = "tx-error";
    needsForceLine.textContent = "The next download re-verifies from scratch (--force).";
    facts.appendChild(needsForceLine);
  }

  return facts;
}

/**
 * WP AG-2: one `{client_id, reported_at}` row of the "Installed on" section
 * below. `reported_at` is rendered with `formatTimestamp` (the project's
 * standing timestamp formatting), never the raw ISO string the API sends.
 * `data-role="iwhen"` and the row's own `dataset.clientid` are what
 * `patchInstalledSection` below targets on a poll tick that leaves the
 * section's PRESENCE unchanged (see that function's header). Exported for
 * `web/tests/game-detail-sheet-installed.test.js` (WP AG-2 review round 1 —
 * a DOM-building component gets the same "named exception" posture
 * `header-art.test.js`'s header documents for its own sibling).
 */
export function buildInstalledRow(entry) {
  const row = document.createElement("div");
  row.className = "installed-row";
  row.dataset.clientid = entry.client_id;
  const name = document.createElement("span");
  name.className = "iname";
  name.textContent = entry.client_id;
  const when = document.createElement("span");
  when.className = "iwhen";
  when.dataset.role = "iwhen";
  when.textContent = formatTimestamp(entry.reported_at);
  row.append(name, when);
  return row;
}

/**
 * The NOT_CACHED note (WP AG-2 review nitpick): bytes-only wording. The
 * earlier draft said the vault has "nothing... protecting this game", which
 * collides with `hasProtectedCacheContent`'s STATUS-based vocabulary
 * (`lib/game-status.js`'s module header) — in the "last cached remnant"
 * case that predicate is TRUE (the mapping still protects a shared depot)
 * at exactly the moment this note is showing (`hasVisibleCacheContent` is
 * what gates it, per `installedBadgeState`), so the same word would assert
 * two contradictory things about the same game in two places. Stated as a
 * plain fact about bytes instead, which is genuinely true in both cases.
 */
function buildInstalledNote() {
  const note = document.createElement("p");
  note.className = "hint tx-stale";
  note.dataset.role = "installed-note";
  note.textContent = "Installed but not cached — no cached bytes for this game right now.";
  return note;
}

/**
 * The "Installed on" section (WP AG-2) — one row per fresh
 * `installed_on` entry (api/README.md "Installed state per app": already
 * pre-filtered to fresh reports, so a NON-empty list here is the only
 * honest signal; this sheet never renders anything for the empty case, no
 * "not installed" claim — see lib/game-status.js's module header). The
 * `installed but not cached` note is the whole reason this feature exists:
 * an app a client claims to have installed, with no cached bytes for it on
 * this vault's disk (bytes-only wording, matching `buildInstalledNote`'s
 * own — see that function's header for why "protecting" is the one word
 * to avoid here).
 * @param {object} gameLike GameSummary/GameDetail-shaped (both carry
 *   `installed_on` — see currentGameLike()'s own comment on field parity).
 * @returns {HTMLElement | null} `null` when there is nothing to show.
 */
export function buildInstalledSection(gameLike) {
  const installedOn = Array.isArray(gameLike.installed_on) ? gameLike.installed_on : [];
  if (installedOn.length === 0) return null;

  const wrap = document.createElement("div");
  wrap.className = "detail-block";
  const heading = document.createElement("h4");
  heading.className = "sec";
  heading.textContent = "Installed on";
  wrap.appendChild(heading);

  const list = document.createElement("div");
  list.className = "installed-list";
  for (const entry of installedOn) list.appendChild(buildInstalledRow(entry));
  wrap.appendChild(list);

  if (installedBadgeState(gameLike) === INSTALLED_BADGE.NOT_CACHED) {
    wrap.appendChild(buildInstalledNote());
  }

  return wrap;
}

/**
 * Round-7 patch-in-place for the installed-on section (called from
 * `patchVolatile()` — this section has no icon/animated node, so unlike the
 * header status icon it is safe to add/remove/reorder ROWS and the NOTE
 * here, not just patch text). Only ever reaches a live `.installed-list`
 * when the section was already painted by `render()` — whether the section
 * EXISTS AT ALL (`installedSectionPresence`: none <-> present) is part of
 * `computeStructuralKey()` below and goes through a full `render()` instead.
 *
 * **Handles the CACHED <-> NOT_CACHED sub-state too (WP AG-2 review S4).**
 * An earlier draft fed the raw 3-state `installedBadgeState` into the
 * structural key, so cached<->not_cached also went through a full rebuild —
 * measured live consequence: a running download's `size_bytes` crossing
 * zero (the server's size cache updates continuously during a download,
 * `game-status.js`'s own module header) flips this exact transition while
 * `dispKind` stays `"running"` throughout (the live-job override ignores
 * bytes entirely), forcing an unwanted full sheet re-render — animated
 * header icon recreated, scroll reset — once per download, for every
 * installed game. `installedSectionPresence` (the structural key input) no
 * longer distinguishes the two, so THIS function is what keeps the note in
 * sync on every such tick instead.
 *
 * @param {HTMLElement} container the element to search within (the real
 *   call site passes `contentEl`; parameterised — rather than reading the
 *   module-private `contentEl` directly — so this function is independently
 *   testable against a hand-built fake DOM tree with no sheet/store
 *   apparatus at all, same reasoning `buildInstalledRow`/`buildInstalledSection`
 *   above are already exported for).
 * @param {object} gameLike
 */
export function patchInstalledSection(container, gameLike) {
  const list = container.querySelector(".installed-list");
  if (!list) return; // nothing painted for this game right now
  const installedOn = Array.isArray(gameLike.installed_on) ? gameLike.installed_on : [];
  const rowsByClient = new Map([...list.querySelectorAll(".installed-row")].map((r) => [r.dataset.clientid, r]));
  const seen = new Set();
  for (const entry of installedOn) {
    seen.add(entry.client_id);
    let row = rowsByClient.get(entry.client_id);
    if (!row) {
      row = buildInstalledRow(entry);
    } else {
      const when = row.querySelector('[data-role="iwhen"]');
      if (when) when.textContent = formatTimestamp(entry.reported_at);
    }
    // Re-append every row IN SERVER ORDER on every tick (a no-op move for a
    // row already in the right place — `appendChild` on an existing child
    // relocates it, per spec — WP AG-2 review nitpick: a naive "only append
    // NEW rows" left a client that re-sorted in the server's response stuck
    // at its old position until the next full rebuild).
    list.appendChild(row);
  }
  for (const [clientId, row] of rowsByClient) {
    if (!seen.has(clientId)) row.remove();
  }

  // The note is the ONE part of this section installedSectionPresence no
  // longer forces a rebuild for (S4, this function's own header) — synced
  // here on every patch tick instead, added/removed/left alone as the live
  // badge state requires.
  const wantsNote = installedBadgeState(gameLike) === INSTALLED_BADGE.NOT_CACHED;
  const existingNote = container.querySelector('[data-role="installed-note"]');
  if (wantsNote && !existingNote) {
    list.parentNode.appendChild(buildInstalledNote());
  } else if (!wantsNote && existingNote) {
    existingNote.remove();
  }
}

async function withButtonBusy(btn, fn) {
  btn.disabled = true;
  try {
    await fn();
  } finally {
    btn.disabled = false;
  }
}

function actionButton(label, variant, handler) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn sm" + (variant ? " " + variant : "");
  btn.textContent = label;
  btn.addEventListener("click", () => withButtonBusy(btn, handler));
  return btn;
}

async function runJobControl(action, successMessage) {
  try {
    await action();
    showToast(successMessage);
    store.refreshNow();
  } catch (err) {
    showToast(errorText(err), { warn: true });
  }
}

function buildJobControlRow(job) {
  const actions = detailJobActions(job);
  const row = document.createElement("div");
  row.className = "jobacts detail-block";
  if (actions.has(DETAIL_JOB_ACTION.RESUME)) {
    row.appendChild(
      actionButton("Resume", "primary", () => runJobControl(() => api.resumeJob(job.id), "Resuming — back in the queue")),
    );
  }
  if (actions.has(DETAIL_JOB_ACTION.PAUSE)) {
    row.appendChild(actionButton("Pause", "", () => runJobControl(() => api.pauseJob(job.id), "Pause requested")));
  }
  if (actions.has(DETAIL_JOB_ACTION.CANCEL)) {
    row.appendChild(actionButton("Cancel", "danger", () => runJobControl(() => api.cancelJob(job.id), "Cancel requested")));
  }
  return row;
}

function buildDownloadButton(action) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn primary sm detail-block";
  btn.textContent = action.title;
  btn.addEventListener("click", () =>
    withButtonBusy(btn, async () => {
      try {
        await api.prefill([state.appid]);
        showToast("Queued for download");
        store.refreshNow();
      } catch (err) {
        showToast(errorText(err), { warn: true });
      }
    }),
  );
  return btn;
}

function depotTagLabel(tag) {
  switch (tag) {
    case DEPOT_TAG.SOLE_HOLDER:
      return "shared · sole holder";
    case DEPOT_TAG.PROTECTED:
      return "shared";
    case DEPOT_TAG.ORPHANED:
      return "shared · orphaned";
    default:
      return null;
  }
}
function depotNoteLabel(tag) {
  switch (tag) {
    case DEPOT_TAG.SOLE_HOLDER:
      return "You are the only cached game using this depot — deleting frees it.";
    case DEPOT_TAG.PROTECTED:
      return "This depot is only removed once no cached game needs it.";
    case DEPOT_TAG.ORPHANED:
      return "No cached game maps this depot anymore — deleting this game frees it too.";
    default:
      return null;
  }
}

function toggleDepot(depotid) {
  const open = !depotExpanded.get(depotid);
  depotExpanded.set(depotid, open);
  const wrap = contentEl.querySelector(`.depotwrap[data-depotid="${depotid}"]`);
  if (!wrap) return;
  wrap.classList.toggle("open", open);
  const row = wrap.querySelector(".depot");
  if (row) row.setAttribute("aria-expanded", String(open));
}

function buildDepotRow(p) {
  const wrapEl = document.createElement("div");
  const expanded = !!depotExpanded.get(p.depotid);
  wrapEl.className = "depotwrap" + (p.coOwners.length ? " sh" : "") + (expanded ? " open" : "");
  wrapEl.dataset.depotid = String(p.depotid);

  const row = document.createElement("div");
  row.className = "depot";
  const idSpan = document.createElement("span");
  idSpan.className = "id";
  idSpan.textContent = String(p.depotid);
  row.appendChild(idSpan);

  const tagText = depotTagLabel(p.tag);
  if (tagText) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = tagText;
    row.appendChild(tag);
  }

  const size = document.createElement("span");
  size.className = "b";
  size.dataset.role = "depsize";
  size.textContent = formatBytesGB(p.sizeBytes) || "—";
  row.appendChild(size);

  if (p.coOwners.length) {
    row.setAttribute("role", "button");
    row.tabIndex = 0;
    row.setAttribute("aria-expanded", String(expanded));
    const chev = document.createElement("span");
    chev.className = "chev";
    chev.setAttribute("aria-hidden", "true");
    chev.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="m7.5 4.5 6 5.5-6 5.5"/></svg>';
    row.appendChild(chev);
    row.addEventListener("click", () => toggleDepot(p.depotid));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleDepot(p.depotid);
      }
    });
  }
  wrapEl.appendChild(row);

  if (p.coOwners.length) {
    const owners = document.createElement("div");
    owners.className = "owners";
    const olabel = document.createElement("p");
    olabel.className = "olabel";
    olabel.textContent = "Also mapped to";
    owners.appendChild(olabel);
    for (const co of p.coOwners) {
      const orow = document.createElement("div");
      orow.className = "orow" + (co.cached ? "" : " off");
      orow.dataset.appid = String(co.appid);
      const on = document.createElement("span");
      on.className = "on";
      on.textContent = co.name;
      const os = document.createElement("span");
      os.className = "os";
      os.dataset.role = "coowner-status";
      os.textContent = co.cached ? "cached" : "not cached · mapping kept";
      orow.append(on, os);
      owners.appendChild(orow);
    }
    const note = depotNoteLabel(p.tag);
    if (note) {
      const noteEl = document.createElement("p");
      noteEl.className = "ohint";
      noteEl.textContent = note;
      owners.appendChild(noteEl);
    }
    wrapEl.appendChild(owners);
  }

  return wrapEl;
}

function buildDepotsSection() {
  const wrap = document.createElement("div");
  const heading = document.createElement("h4");
  heading.className = "sec";
  heading.textContent = "Depots";
  wrap.appendChild(heading);

  if (!state.detail.depots.length) {
    const unknown = document.createElement("div");
    unknown.className = "unknown";
    const u1 = document.createElement("p");
    u1.className = "u1";
    u1.textContent = "Depots unknown until the first download";
    const u2 = document.createElement("p");
    u2.className = "u2";
    u2.textContent =
      "vault-api learns which depots belong to a game by watching what the first download actually fetches.";
    unknown.append(u1, u2);
    wrap.appendChild(unknown);
    return wrap;
  }

  const list = document.createElement("div");
  list.className = "depots";
  for (const p of currentDepotPresentations()) list.appendChild(buildDepotRow(p));
  wrap.appendChild(list);
  return wrap;
}

function buildDeleteButton() {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn danger sm detail-block";
  btn.textContent = "Delete from cache";
  btn.addEventListener("click", () => openDeleteConfirm());
  return btn;
}

function renderLoading() {
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = "Loading…";
  return p;
}

function renderLoadError() {
  const p = document.createElement("p");
  p.className = "errline";
  p.textContent = `Could not load this game: ${state.loadError}`;
  return p;
}

function renderNotTracked() {
  const wrap = document.createElement("div");
  const h3 = document.createElement("h3");
  h3.textContent = state.name || `App ${state.appid}`;
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = "vault-api does not track this app yet — nothing has been downloaded or manually mapped.";
  // WP API-FIX-4: no download offer for a Steam tool app. Decided from the
  // store's games row only (no client-side tool list); an app with no row
  // at all carries no flag, and the server's 422 detail is the answer then.
  const storeRow = state.games.find((g) => g.appid === state.appid);
  if (isToolApp(storeRow)) {
    p.textContent = TOOL_APP_NOTE;
    wrap.append(h3, p);
    return wrap;
  }
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn primary sm";
  btn.textContent = "Download to cache";
  btn.addEventListener("click", () =>
    withButtonBusy(btn, async () => {
      try {
        await api.prefill([state.appid]);
        showToast("Queued for download");
        store.refreshNow();
        closeDetail();
      } catch (err) {
        showToast(errorText(err), { warn: true });
      }
    }),
  );
  wrap.append(h3, p, btn);
  return wrap;
}

/** Full rebuild of the sheet body from `state` — called on open(), once the
 * fetch settles, and whenever a games/jobs poll tick's structural key
 * changed (see the round-7 comparison in the store subscriptions below). */
function render() {
  contentEl.replaceChildren();

  if (!state.appid) {
    gcSectionEl.replaceChildren();
    return;
  }

  if (state.notTracked) {
    contentEl.append(renderNotTracked());
    gcSectionEl.replaceChildren();
    lastStructuralKey = null;
    return;
  }
  if (state.loadError) {
    contentEl.append(renderLoadError());
    gcSectionEl.replaceChildren();
    lastStructuralKey = null;
    return;
  }
  if (!state.detail) {
    contentEl.append(renderLoading());
    gcSectionEl.replaceChildren();
    lastStructuralKey = null;
    return;
  }

  const gameLike = currentGameLike();
  const liveJob = findLiveJob(state.jobs, state.appid);
  const trackedJob = findTrackedJob(state.jobs, state.appid);

  contentEl.append(buildHeaderArt(state.appid));
  contentEl.append(buildHeader(gameLike, liveJob));
  const toolNote = buildToolAppNote(gameLike);
  if (toolNote) contentEl.append(toolNote);
  contentEl.append(buildFactLines(gameLike));

  const installedSection = buildInstalledSection(gameLike);
  if (installedSection) contentEl.append(installedSection);

  if (trackedJob) {
    contentEl.append(buildJobControlRow(trackedJob));
  } else {
    const action = statusAction(gameLike, undefined, false);
    if (action) contentEl.append(buildDownloadButton(action));
  }

  contentEl.append(buildDepotsSection());

  if (hasVisibleCacheContent(gameLike)) {
    contentEl.append(buildDeleteButton());
  }

  renderGcSection();

  lastStructuralKey = computeStructuralKey();
}

/** Patch only the volatile TEXT fields a games/jobs poll tick can change
 * without altering the sheet's structural key (sizes, timestamps, a
 * co-owner's cached/not-cached wording) — never touches the header
 * status-icon subtree or rebuilds any depot row (see module header, "Round-7
 * patch-in-place"). Only ever called when `computeStructuralKey()` matches
 * `lastStructuralKey`, i.e. render() has already built the DOM this queries. */
function patchVolatile() {
  const gameLike = currentGameLike();
  if (!gameLike) return;

  const sizeEl = contentEl.querySelector('[data-role="size"]');
  if (sizeEl) sizeEl.textContent = formatBytesGB(gameLike.size_bytes) || "Size unknown";

  const lastDlEl = contentEl.querySelector('[data-role="lastdl"]');
  if (lastDlEl) {
    lastDlEl.textContent = gameLike.last_prefill_at
      ? `Last downloaded ${formatTimestamp(gameLike.last_prefill_at)}`
      : "Never downloaded";
  }

  const confirmedEl = contentEl.querySelector('[data-role="confirmed"]');
  if (confirmedEl) confirmedEl.textContent = confirmedCurrentText(gameLike.last_prefill_at, gameLike.last_manifest_check);

  patchInstalledSection(contentEl, gameLike);

  for (const p of currentDepotPresentations()) {
    const wrap = contentEl.querySelector(`.depotwrap[data-depotid="${p.depotid}"]`);
    if (!wrap) continue;
    const sizeSpan = wrap.querySelector('[data-role="depsize"]');
    if (sizeSpan) sizeSpan.textContent = formatBytesGB(p.sizeBytes) || "—";
    for (const co of p.coOwners) {
      const orow = wrap.querySelector(`.orow[data-appid="${co.appid}"]`);
      if (!orow) continue;
      orow.classList.toggle("off", !co.cached);
      const statusSpan = orow.querySelector('[data-role="coowner-status"]');
      if (statusSpan) statusSpan.textContent = co.cached ? "cached" : "not cached · mapping kept";
    }
  }
}

// ---------------------------------------------------------------------
// GC flow
// ---------------------------------------------------------------------

function buildGcPlanBody(job, summary, dryRun) {
  const wrap = document.createElement("div");
  const headline = document.createElement("p");
  if (summary) {
    if (dryRun) {
      const count = summary.wouldDeleteCount ?? 0;
      headline.textContent =
        count === 0
          ? "No orphaned chunks found."
          : `Found ${count} orphaned chunk${count === 1 ? "" : "s"} — about ${formatBytesGB(summary.wouldDeleteBytes) || "0 GB"}.`;
    } else {
      const count = summary.chunksRemoved ?? 0;
      headline.textContent = `Removed ${count} chunk${count === 1 ? "" : "s"}, freeing about ${
        formatBytesGB(summary.totalBytesFreed ?? summary.bytesFreed) || "0 GB"
      }.`;
    }
  } else {
    headline.textContent = "The job finished, but its log did not have a totals line this app recognizes.";
  }
  wrap.appendChild(headline);

  if (summary?.heldBackCount) {
    const held = document.createElement("p");
    held.className = "hint";
    held.textContent = `${summary.heldBackCount} chunk${summary.heldBackCount === 1 ? "" : "s"} (${
      formatBytesGB(summary.heldBackBytes) || "0 GB"
    }) held back — inside the grace window.`;
    wrap.appendChild(held);
  }

  if (job?.log_excerpt) {
    const logHeading = document.createElement("p");
    logHeading.className = "hint";
    logHeading.textContent = `Job #${job.id} log:`;
    const log = document.createElement("div");
    log.className = "detail-log";
    log.textContent = job.log_excerpt;
    wrap.append(logHeading, log);
  }
  return wrap;
}

/** Rebuilds `gcSectionEl` from `state.gcState` — completely independent of
 * `render()`/`patchVolatile()`: no games/jobs poll tick ever drives this
 * section, only this module's own GC actions and its own poll loop, so it
 * has no round-7 concern of its own to guard against. */
function renderGcSection() {
  gcSectionEl.replaceChildren();

  // WP 4a.8: focus trap + background inert for the GC-execute confirm,
  // edge-detected against the LAST kind this function saw (see
  // `gcConfirmWasOpen`'s doc comment) rather than pushed/popped on every
  // call while the state happens to stay CONFIRM_EXECUTE.
  const showGcConfirm = state.gcState.kind === GC_STATE.CONFIRM_EXECUTE;
  const openingGcConfirm = showGcConfirm && !gcConfirmWasOpen;
  const closingGcConfirm = !showGcConfirm && gcConfirmWasOpen;
  if (openingGcConfirm) {
    gcConfirmInvokerEl = document.activeElement;
    // Also makes the sheet BEHIND this dialog inert; Escape routes through
    // lib/modal-stack.js's single dispatcher, not a listener bound here —
    // see that module's header for the live-found double-close bug an
    // independent one caused.
    pushModal(gcConfirmBackdrop, dismissGcExecuteConfirm);
  } else if (closingGcConfirm) {
    popModal(gcConfirmBackdrop);
    if (gcConfirmInvokerEl && typeof gcConfirmInvokerEl.focus === "function") gcConfirmInvokerEl.focus();
    gcConfirmInvokerEl = null;
  }
  gcConfirmBackdrop.classList.toggle("on", showGcConfirm);
  gcConfirmWasOpen = showGcConfirm;
  // "Cancel" (the non-destructive default), not "Delete" — same convention
  // as the delete-confirm dialog's `dNo.focus()`.
  if (openingGcConfirm) gcNo.focus();

  if (!state.detail || !state.detail.depots.length) return; // nothing to collect

  const heading = document.createElement("h4");
  heading.className = "sec";
  heading.textContent = "Garbage collection";
  gcSectionEl.appendChild(heading);

  const gs = state.gcState;
  switch (gs.kind) {
    case GC_STATE.IDLE: {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn sm";
      btn.textContent = "Check for orphaned chunks";
      btn.addEventListener("click", () => restartGcDryRun());
      gcSectionEl.appendChild(btn);
      break;
    }
    case GC_STATE.CANCELLED: {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Garbage collection was cancelled.";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn sm";
      btn.textContent = "Check again";
      btn.addEventListener("click", () => restartGcDryRun());
      gcSectionEl.append(p, btn);
      break;
    }
    case GC_STATE.REQUESTING_DRY_RUN:
    case GC_STATE.POLLING_DRY_RUN: {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Checking for orphaned chunks…";
      gcSectionEl.appendChild(p);
      break;
    }
    case GC_STATE.DRY_RUN_PLAN:
    case GC_STATE.CONFIRM_EXECUTE: {
      gcSectionEl.appendChild(buildGcPlanBody(gs.job, gs.summary, true));
      const btnRow = document.createElement("div");
      btnRow.className = "jobacts";
      const executeBtn = document.createElement("button");
      executeBtn.type = "button";
      executeBtn.className = "btn danger sm";
      executeBtn.textContent = "Execute";
      executeBtn.disabled = (gs.summary?.wouldDeleteCount ?? 1) === 0;
      executeBtn.addEventListener("click", () => requestGcExecute());
      const againBtn = document.createElement("button");
      againBtn.type = "button";
      againBtn.className = "btn ghost sm";
      againBtn.textContent = "Check again";
      againBtn.addEventListener("click", () => restartGcDryRun());
      btnRow.append(executeBtn, againBtn);
      gcSectionEl.appendChild(btnRow);

      const count = gs.summary?.wouldDeleteCount;
      const bytes = gs.summary?.wouldDeleteBytes;
      gcText.textContent =
        count != null && bytes != null
          ? `This deletes ${count} orphaned chunk${count === 1 ? "" : "s"}, freeing about ${formatBytesGB(bytes) || "0 GB"}.`
          : "This deletes the orphaned chunks found by the check.";
      break;
    }
    case GC_STATE.REQUESTING_EXECUTE:
    case GC_STATE.POLLING_EXECUTE: {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Deleting orphaned chunks…";
      gcSectionEl.appendChild(p);
      break;
    }
    case GC_STATE.EXECUTE_DONE: {
      gcSectionEl.appendChild(buildGcPlanBody(gs.job, gs.summary, false));
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn sm";
      btn.textContent = "Check again";
      btn.addEventListener("click", () => restartGcDryRun());
      gcSectionEl.appendChild(btn);
      break;
    }
    case GC_STATE.ERROR: {
      const p = document.createElement("p");
      p.className = "errline";
      p.textContent = gs.message;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn sm";
      btn.textContent = "Check again";
      btn.addEventListener("click", () => restartGcDryRun());
      gcSectionEl.append(p, btn);
      break;
    }
    default:
      break;
  }
}

async function pollGc(jobId, myGeneration) {
  while (true) {
    if (myGeneration !== generation || !dialog.isOpen()) return;
    let job;
    try {
      job = await api.job(jobId);
    } catch (err) {
      if (myGeneration !== generation) return;
      const executing = state.gcState.kind === GC_STATE.POLLING_EXECUTE;
      state.gcState = { kind: GC_STATE.ERROR, message: errorText(err), executeAttempted: executing };
      renderGcSection();
      return;
    }
    if (myGeneration !== generation) return;
    state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.POLL_RESULT, job });
    renderGcSection();
    if (state.gcState.kind === GC_STATE.POLLING_DRY_RUN || state.gcState.kind === GC_STATE.POLLING_EXECUTE) {
      await sleep(GC_POLL_INTERVAL_MS);
    } else {
      return; // reached a terminal state (plan shown, done, cancelled, error)
    }
  }
}

/** Starts a dry-run GC job (`POST .../gc` with the server's own default,
 * `execute: false`) and polls it to completion. See lib/gc-flow.js's header
 * for the state machine this drives and the guarantee it enforces. */
function startGcDryRun() {
  state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.START_DRY_RUN });
  renderGcSection();
  if (state.gcState.kind !== GC_STATE.REQUESTING_DRY_RUN) return; // stray call while already mid-flow

  const appid = state.appid;
  const myGeneration = ++generation;
  (async () => {
    try {
      const ref = await api.gc(appid, false);
      if (myGeneration !== generation) return;
      state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.DRY_RUN_QUEUED, jobId: ref.job_id });
      renderGcSection();
      await pollGc(ref.job_id, myGeneration);
    } catch (err) {
      if (myGeneration !== generation) return;
      state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.DRY_RUN_FAILED, message: errorText(err) });
      renderGcSection();
    }
  })();
}

/** The sheet's single "Check again" action, usable from every terminal or
 * plan-shown GC state — resets to Idle (a plain, synchronous state
 * assignment) then starts a fresh dry run on the very next line, mirroring
 * the Android sibling's `restartGcDryRun` fix: `lib/gc-flow.js`'s reducer
 * only accepts START_DRY_RUN from IDLE/EXECUTE_DONE/ERROR/CANCELLED, not
 * from DRY_RUN_PLAN itself, so a "Check again" wired directly to
 * `startGcDryRun()` from that state would silently do nothing. */
function restartGcDryRun() {
  generation++; // invalidate any in-flight poll loop from the previous run
  state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.RESET });
  startGcDryRun();
}

/** User tapped "Execute" after seeing the dry-run plan — opens the second
 * confirm dialog. Does NOT call the API yet (see confirmGcExecute). */
function requestGcExecute() {
  state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.REQUEST_EXECUTE });
  renderGcSection();
}

function dismissGcExecuteConfirm() {
  state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.DISMISS_CONFIRM });
  renderGcSection();
}

/**
 * The ONLY call site that can ever cause `POST .../gc {"execute":true}` to
 * fire. `lib/gc-flow.js`'s `reduceGcFlow` already refuses to produce
 * REQUESTING_EXECUTE from anywhere but CONFIRM_EXECUTE — the guard below is
 * defence-in-depth on top of that (docs/LEARNINGS.md "redundant defence
 * layers cannot be pinned by one test": pin each layer standalone), not a
 * bet that the reducer might be wrong.
 */
function confirmGcExecute() {
  state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.CONFIRM_EXECUTE });
  renderGcSection();
  if (state.gcState.kind !== GC_STATE.REQUESTING_EXECUTE) return;

  const appid = state.appid;
  const myGeneration = ++generation;
  (async () => {
    try {
      const ref = await api.gc(appid, true);
      if (myGeneration !== generation) return;
      state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.EXECUTE_QUEUED, jobId: ref.job_id });
      renderGcSection();
      await pollGc(ref.job_id, myGeneration);
    } catch (err) {
      if (myGeneration !== generation) return;
      state.gcState = reduceGcFlow(state.gcState, { type: GC_EVENT.EXECUTE_FAILED, message: errorText(err) });
      renderGcSection();
    }
  })();
}

// ---------------------------------------------------------------------
// Store subscriptions — set up ONCE at module load (this component is not
// view-scoped, same posture as clients-sheet.js/notifications.js).
// ---------------------------------------------------------------------

function applyLiveTick() {
  if (!dialog.isOpen() || !state.appid || !state.detail) return; // nothing shown yet, or a different surface
  const nextKey = computeStructuralKey();
  if (nextKey === lastStructuralKey) {
    patchVolatile();
  } else {
    render();
  }
}

store.subscribe("games", ({ items }) => {
  if (!Array.isArray(items)) return; // {error} payload — nothing to render
  state.games = items;
  applyLiveTick();
});

store.subscribe("jobs", ({ items }) => {
  if (!Array.isArray(items)) return;
  state.jobs = items;
  applyLiveTick();
});
