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
 * `buildHeaderArt(appid)` is appended into the hero band in the loaded-detail
 * success branch of `render()` only (the loading/error/not-tracked states
 * show the band's gradient alone). That module owns the graceful-
 * absence and no-layout-shift discipline; this file only decides WHEN to
 * build one (every full `render()`, same treatment `buildHeader()`'s mini
 * cover already gets — never touched by `patchVolatile()`, so a poll tick
 * that doesn't change the structural key never re-fetches it).
 *
 * **Cache completeness (WP CACHE-COMPLETE-1c).** The header's status uses
 * the Library card's overlay (lib/cache-completeness.js `cardDisplay`: only
 * 100 % is the green check). Under the facts a completeness block
 * (`buildCompletenessSection`) shows the game's own figure ("80% complete —
 * 3.2 of 4.0 GB on disk · checked …", or "Completeness unknown …"), the
 * group's figure for a game with content packages, the damaged-chunks note
 * for a game short only of wrong-size chunks, and "Verify": `POST
 * /v1/prefill/verify` for the game (and its packages) — a forced prefill
 * that fetches whatever is missing. A verify that lands on a RUNNING job
 * only forces the next run, and the toast says "verifies on the next run"
 * (`summarizeVerifyStart`). The block's shape is part of the structural
 * key; its line texts are patched.
 *
 * **Content packages (WP TOOL-APPS-2c, ADR-0022 §3).** For a game with
 * content packages (the same grouping the Library uses, lib/content-groups.js
 * `groupGames` over the store's rows), the sheet lists them in a "Content
 * packages" section (components/package-list.js) with the total size of the
 * group (the server's `group_size_bytes`) and a "Retry download"
 * button per failed package. The package rows' structure is part of the
 * structural key; their sizes are patched.
 *
 * **Anatomy (WP LIBRARY-2, docs/design/rc20/library.html + kit).** The sheet
 * carries `kit game-detail` and is ~920px wide (`--w-sheet-l`). A permanent
 * hero band (`.gd-hero`: the fallback-hue gradient, the header art from WP
 * 4h.3 now layered over it instead of growing above the header) runs full
 * bleed; an X button (`.gd-x`) closes the sheet from its top-right corner
 * (the bottom "Close" button is gone). The header row `.gd-head` holds the
 * mini cover overlapping the hero, the title, "App N" with the status pill
 * (components/pill.js) and the actions `.gd-acts` (Verify, then the primary
 * action). Overview is two columns: a 2x2 facts grid (Size / Last
 * downloaded / Confirmed current / Cache), "Installed on" and About on the
 * left, Content packages on the right. Depots and Maintenance are unchanged.
 */

import { store } from "../store-singleton.js";
import { api } from "../api.js";
import { UNKNOWN_APP_NAME } from "../lib/app-names.js";
import { showToast } from "./toast.js";
import { STATUS_LABEL } from "./status-icon.js";
import { createPill } from "./pill.js";
import { createSheetDialog } from "./sheet-dialog.js";
import { buildHeaderArt } from "./header-art.js";
import { onViewChange } from "../router.js";
import { formatBytesGB, formatTimestamp } from "../lib/format.js";
import { coverCandidates, fallbackHues, wireCoverFallback } from "../lib/cover-art.js";
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
  TOOL_APP_NOTE,
  NOT_OWNED_LIBRARY_NOTE,
  showsNotOwnedNote,
  showsUpdateState,
} from "../lib/game-status.js";
import {
  cardDisplay,
  completenessLine,
  groupCompletenessLine,
  isDamagedOnly,
  showsCompletenessBlock,
  verifyAppids,
  summarizeVerifyStart,
  describeVerifyStartError,
  DAMAGED_NOTE,
  VERIFY_HINT,
  VERIFY_LABEL,
  verifyAriaLabel,
} from "../lib/cache-completeness.js";
import {
  sheetUpdateAppids,
  summarizeUpdateStart,
  describeUpdateStartError,
  UPDATE_NOW_LABEL,
} from "../lib/updates-available.js";
import {
  PREVIEW_STATE,
  PENDING_RETRY_MS,
  isReady,
  expectedSizeText,
  expectedDepotRows,
  expectedDlcDepotRows,
  wantsPreview,
} from "../lib/depot-preview.js";
import {
  STORE_RETRY_MS,
  STORE_STATE,
  byLine,
  descriptionText,
  genreList,
  hasStoreContent,
  isReady as isStoreReady,
  modeChips,
  ratingText,
} from "../lib/store-details.js";
import { buildMultiPlan } from "../lib/multiplan.js";
import { buildDepotPresentation, DEPOT_TAG } from "../lib/depot-presentation.js";
import { findTrackedJob, detailJobActions, DETAIL_JOB_ACTION } from "../lib/detail-job.js";
import { confirmedCurrentWording, CONFIRMED_CURRENT_WORDING } from "../lib/detail-wording.js";
import { lastUpdateDownloadedText } from "../lib/decision-support.js";
import { GC_STATE, GC_EVENT, idleGcState, reduceGcFlow } from "../lib/gc-flow.js";
import { buildDetailStructuralKey } from "../lib/detail-render-plan.js";
import { pushModal, popModal } from "../lib/modal-stack.js";
import { appTitle, fillMissingNames, ownedNamesByAppid } from "../lib/owned-library.js";
import { ownedLibrary } from "../owned-singleton.js";
import { groupGames, groupHasCacheContent, isGroup } from "../lib/content-groups.js";
import { isMembersMismatch, isPlanBlocked, planMemberIds, MEMBERS_CHANGED_NOTICE } from "../lib/group-delete.js";
import { buildGroupDeletePlan, buildNotice, groupDeleteIntro } from "./group-delete-plan.js";
import { buildIntroNodes, buildPlanNodes, createDeleteConfirm } from "./delete-confirm.js";
import { createTabs } from "./tabs.js";
import { buildPackagesSection, packagesStructuralKey, patchPackagesSection } from "./package-list.js";

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
  /** WP D19-1b: `GET /v1/oracle/{appid}/preview` payload, or null. Only ever
   * set for a game with no observed depots; the server gates it behind the
   * oracle and depot-preview switches (state "off" when either is off). */
  preview: null,
  /** WP D19-1c: `GET /v1/store/{appid}/details` payload, or null (state "off"
   * unless VAULT_STORE_DETAILS is on). Plain text only, via textContent. */
  store: null,
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
// WP LIBRARY-2: the sheet composes from css/kit.css (ADR-0028) and is
// docs/design/rc20/library.html's detail sheet: ~920px wide
// (`--w-sheet-l`), a permanent hero band, an X close button top-right
// instead of a bottom "Close" button.
dialog.sheet.classList.add("kit", "game-detail");

const contentEl = document.createElement("div");
contentEl.className = "gd";
const gcSectionEl = document.createElement("div");
const closeBtn = document.createElement("button");
closeBtn.type = "button";
closeBtn.className = "gd-x";
closeBtn.setAttribute("aria-label", "Close");
closeBtn.title = "Close";
closeBtn.innerHTML =
  '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m5.5 5.5 9 9M14.5 5.5l-9 9"/></svg>';
closeBtn.addEventListener("click", () => closeDetail());
// `gcSectionEl` lives inside the Maintenance tab (render() places it); it is
// a persistent element because the GC flow repaints it on its own.
gcSectionEl.className = "detail-box";
dialog.body.append(contentEl);
// Persistent and outside the scrolling body, so it stays reachable while
// the sheet scrolls and in every state (loading, error, not tracked).
dialog.sheet.appendChild(closeBtn);

/** Selected tab of the open sheet; survives the structural re-renders a poll
 * tick can cause, reset to Overview on every open(). */
let activeTab = "overview";

// ---------------------------------------------------------------------
// Delete confirm — the shared components/delete-confirm.js (WP
// LIBRARY-DETAIL-1; views/library.js uses the same component for the bulk
// delete), a second persistent overlay on `document.body`. Single-game and
// bulk delete describe the same arithmetic the same way because they render
// it with the same builders.
// ---------------------------------------------------------------------

const deleteConfirm = createDeleteConfirm({ onConfirm: confirmDelete, onClose: onDeleteConfirmClosed });

/** WP TOOL-APPS-2c: the grouped-delete plan the dialog currently shows
 * (null for a plain delete), and a token so a superseded plan fetch drops
 * its answer. */
let pendingGroupPlan = null;
let groupPlanGeneration = 0;

/**
 * WP TOOL-APPS-2c (ADR-0022 §4): delete a game with its content packages.
 * Fetches the server's plan, lists every app it removes, the bytes freed and
 * the depots kept; Delete stays disabled while a member has a job in flight.
 * Also the re-confirm step after a 409 "members differ" (`notice`).
 * @param {string} [notice]
 */
async function openGroupDeleteConfirm(notice) {
  const myGen = ++groupPlanGeneration;
  const appid = state.appid;
  pendingGroupPlan = null;
  deleteConfirm.open();
  try {
    const plan = await api.deletePlan(appid);
    if (myGen !== groupPlanGeneration || state.appid !== appid) return;
    const gamesByAppid = namedGamesByAppid();
    const nameFor = (id, serverName) => appTitle(id, gamesByAppid.get(id)?.name || serverName);
    const block = buildGroupDeletePlan(plan, nameFor);
    deleteConfirm.setIntro(groupDeleteIntro(block.subject, block.freedBytes));
    const noticeEl = notice ? buildNotice(notice) : null;
    deleteConfirm.setNote(noticeEl ? [noticeEl, block.node] : [block.node]);
    pendingGroupPlan = plan;
    deleteConfirm.setConfirmEnabled(!isPlanBlocked(plan));
    // Review fix (a11y): the re-confirm notice takes focus and is announced.
    if (noticeEl) noticeEl.focus();
  } catch (err) {
    if (myGen !== groupPlanGeneration) return;
    deleteConfirm.showError(errorText(err));
  }
}

function openDeleteConfirm() {
  // WP TOOL-APPS-2c: a game with content packages is deleted with them.
  if (currentGroup()) {
    openGroupDeleteConfirm();
    return;
  }
  pendingGroupPlan = null;
  deleteConfirm.open();

  const gamesByAppid = namedGamesByAppid();
  const activeJobAppids = activeJobAppidsFrom(state.jobs);
  const plan = buildMultiPlan([state.appid], {
    details: [state.detail],
    mapping: state.mapping,
    gamesByAppid,
    activeJobAppids,
  });
  const name = state.detail?.name || state.name || UNKNOWN_APP_NAME;
  deleteConfirm.setIntro(buildIntroNodes(name, plan));
  deleteConfirm.setNote(buildPlanNodes(plan, gamesByAppid, namesFor));
  deleteConfirm.setConfirmEnabled(true);
}

/** Runs on every close of the shared dialog (Keep, Escape, after a delete). */
function onDeleteConfirmClosed() {
  groupPlanGeneration++;
  pendingGroupPlan = null;
}

function closeDeleteConfirm() {
  deleteConfirm.close();
}

async function confirmDelete() {
  const appid = state.appid;
  if (pendingGroupPlan) {
    await confirmGroupDelete(appid, pendingGroupPlan);
    return;
  }
  deleteConfirm.setBusy(true);
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
    deleteConfirm.setBusy(false);
  }
}

/** WP TOOL-APPS-2c: send the grouped delete with exactly the confirmed
 * members; on a 409 "members differ" fetch the plan again and ask again. */
async function confirmGroupDelete(appid, plan) {
  deleteConfirm.setBusy(true);
  try {
    const result = await api.deleteCacheWithPackages(appid, planMemberIds(plan));
    deleteConfirm.setBusy(false);
    closeDeleteConfirm();
    showToast(`Freed ${formatBytesGB(result.total_bytes_freed) || "0 GB"}`);
    store.refreshNow();
    closeDetail();
  } catch (err) {
    deleteConfirm.setBusy(false);
    if (isMembersMismatch(err)) {
      // Keep focus inside the dialog while the plan is fetched again.
      deleteConfirm.focusKeep();
      store.refreshNow();
      await openGroupDeleteConfirm(MEMBERS_CHANGED_NOTICE);
      return;
    }
    closeDeleteConfirm();
    showToast(errorText(err), { warn: true });
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
  state.preview = null;
  state.store = null;
  depotExpanded.clear();
  lastStructuralKey = null;
  activeTab = "overview";

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
    if (
      generation === myGeneration &&
      state.appid === myAppid &&
      !state.loadError &&
      wantsPreview(state.detail)
    ) {
      loadPreview(myGeneration, myAppid, 0);
    }
    if (generation === myGeneration && state.appid === myAppid && !state.loadError) {
      loadStore(myGeneration, myAppid, 0);
    }
  })();
}

/**
 * WP D19-1b: ask for the expected depots of a never-cached game. Best effort:
 * any failure leaves the card exactly as it was. While the server says
 * `pending` (background oracle query running) it asks again a few times.
 */
async function loadPreview(myGeneration, myAppid, attempt) {
  let preview;
  try {
    preview = await api.depotPreview(myAppid);
  } catch {
    return;
  }
  if (generation !== myGeneration || state.appid !== myAppid) return;
  if (!wantsPreview(state.detail)) return; // observed depots arrived meanwhile
  state.preview = preview;
  if (isReady(preview)) render();
  if (preview && preview.state === PREVIEW_STATE.PENDING && attempt < PENDING_RETRY_MS.length) {
    setTimeout(() => loadPreview(myGeneration, myAppid, attempt + 1), PENDING_RETRY_MS[attempt]);
  }
}

/**
 * WP D19-1c: ask for the store details of the open game. Best effort: any
 * failure, or the server saying "off", leaves the card exactly as it was.
 * Never asked before the card's own data has loaded.
 */
async function loadStore(myGeneration, myAppid, attempt) {
  let payload;
  try {
    payload = await api.storeDetails(myAppid);
  } catch {
    return;
  }
  if (generation !== myGeneration || state.appid !== myAppid) return;
  state.store = payload;
  if (isStoreReady(payload)) render();
  if (payload && payload.state === STORE_STATE.PENDING && attempt < STORE_RETRY_MS.length) {
    setTimeout(() => loadStore(myGeneration, myAppid, attempt + 1), STORE_RETRY_MS[attempt]);
  }
}

/**
 * The store block (description, mode chips, genres, developer/date, overall
 * rating), or null. Every string goes in through `textContent`; the markup is
 * ours.
 */
function buildStoreBlock() {
  if (!hasStoreContent(state.store)) return null;
  const wrap = document.createElement("div");
  wrap.className = "store-details detail-block";
  const description = descriptionText(state.store);
  if (description) {
    const p = document.createElement("p");
    p.className = "store-desc";
    p.textContent = description;
    p.title = "Click to expand";
    p.addEventListener("click", () => p.classList.toggle("expanded"));
    wrap.appendChild(p);
  }
  // WP CARD-LAYOUT-1: the facts (mode chips, genres, developer/date, rating)
  // are a second column beside the description on wide screens and stack
  // under it on narrow ones (CSS `.store-details` / `.store-facts`).
  const facts = document.createElement("div");
  facts.className = "store-facts";
  const chips = modeChips(state.store);
  if (chips.length) {
    const row = document.createElement("div");
    row.className = "store-chips";
    for (const label of chips) {
      const chip = document.createElement("span");
      chip.className = "store-chip";
      chip.textContent = label;
      row.appendChild(chip);
    }
    facts.appendChild(row);
  }
  const lines = [
    ["genres", genreList(state.store).join(", ")],
    ["by", byLine(state.store)],
    ["rating", ratingText(state.store)],
  ];
  for (const [role, line] of lines) {
    if (!line) continue;
    const p = document.createElement("p");
    p.className = "u2 store-line";
    p.dataset.role = role;
    p.textContent = line;
    facts.appendChild(p);
  }
  if (facts.childNodes.length) wrap.appendChild(facts);
  if (!description) wrap.classList.add("store-nodesc");
  return wrap;
}

/** The "Expected ..." block (size line, depot rows, DLC depots), or null. */
function buildExpectedBlock() {
  const sizeText = expectedSizeText(state.preview);
  if (!sizeText) return null;
  const wrap = document.createElement("div");
  wrap.className = "expected-depots";
  const size = document.createElement("p");
  size.className = "u1 expected-size";
  size.textContent = sizeText;
  wrap.appendChild(size);
  const list = document.createElement("div");
  list.className = "depots";
  for (const r of expectedDepotRows(state.preview)) {
    const row = document.createElement("div");
    row.className = "depot expected-depot";
    row.textContent = `Depot ${r.depotid} — ${r.sizeText || "—"}`;
    list.appendChild(row);
  }
  wrap.appendChild(list);
  const dlc = expectedDlcDepotRows(state.preview);
  if (dlc.length) {
    const note = document.createElement("p");
    note.className = "u2 expected-dlc";
    note.textContent =
      "Not included in the size (DLC depots): " +
      dlc.map((d) => (d.dlcappid ? `${d.depotid} (DLC ${d.dlcappid})` : String(d.depotid))).join(", ");
    wrap.appendChild(note);
  }
  const hint = document.createElement("p");
  hint.className = "u2";
  hint.textContent = "An expectation from the public Steam branch, not a measurement.";
  wrap.appendChild(hint);
  return wrap;
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

/** WP TOOL-APPS-2c: the open game's group row (parent + content packages)
 * from the store's rows, or null when it has no packages. */
function currentGroup() {
  if (!state.appid) return null;
  const row = groupGames(state.games).rows.find((g) => g.appid === state.appid);
  return isGroup(row) ? row : null;
}

/** WP UPD-6 review fix: the row whose `update_available` the header shows
 * — the group row (OR over its members, like the Library card) or the
 * game's own row. */
function currentUpdateRow() {
  return currentGroup() || currentGameLike();
}

/** WP UPD-6 review fix: the app ids the sheet's "Update now" sends — the
 * game and, for a group, its packages, each while flagged and without an
 * active job (lib/updates-available.js `sheetUpdateAppids`). */
function currentUpdateAppids() {
  const gameLike = currentGameLike();
  if (!gameLike) return [];
  const group = currentGroup();
  return sheetUpdateAppids(group ? [gameLike, ...group.packages] : [gameLike], state.jobs);
}

/** WP CACHE-COMPLETE-1c: the app ids the sheet's "Verify" sends — the game
 * and, for a group, its packages (lib/cache-completeness.js
 * `verifyAppids`). */
function currentVerifyAppids() {
  const gameLike = currentGameLike();
  if (!gameLike) return [];
  const group = currentGroup();
  return verifyAppids(group ? [gameLike, ...group.packages] : [gameLike]);
}

/** WP CACHE-COMPLETE-1c: the structural-key part for the completeness
 * block — whether it shows, the header overlay, the Verify targets, the
 * damaged note and the group line. "" when the block does not show. */
function completenessStructuralPart(kind) {
  const gameLike = currentGameLike();
  const group = currentGroup();
  if (!showsCompletenessBlock(gameLike, group)) return "";
  const header = cardDisplay(currentUpdateRow() || gameLike, kind, STATUS_LABEL).key;
  const damaged = isDamagedOnly(gameLike) ? 1 : 0;
  const groupLine = groupCompletenessLine(group) ? 1 : 0;
  return `${header}|${currentVerifyAppids().join(",")}|${damaged}|${groupLine}`;
}

/** The structural-key part for the update state: "" when nothing shows. */
function updatesStructuralPart(kind) {
  const header = showsUpdateState(currentUpdateRow(), kind);
  const ids = currentUpdateAppids();
  return header || ids.length ? `${header ? 1 : 0}:${ids.join(",")}` : "";
}

function packageJobLookups() {
  return {
    liveJobFor: (appid) => findLiveJob(state.jobs, appid),
    trackedJobFor: (appid) => findTrackedJob(state.jobs, appid),
  };
}

async function retryPackage(pkg, btn) {
  await withButtonBusy(btn, async () => {
    try {
      await api.prefill([pkg.appid]);
      showToast("Queued for download");
      store.refreshNow();
    } catch (err) {
      showToast(errorText(err), { warn: true });
    }
  });
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
  const group = currentGroup();
  return buildDetailStructuralKey({
    // WP TOOL-APPS-2c: which packages, their status and Retry offer.
    packages: group ? packagesStructuralKey(group.packages, packageJobLookups()) : null,
    dispKind: dispKind(gameLike, liveJob),
    // WP UPD-6 review fix: header state and the "Update now" targets.
    updates: updatesStructuralPart(dispKind(gameLike, liveJob)),
    // WP CACHE-COMPLETE-1c: header overlay and the completeness block.
    completeness: completenessStructuralPart(dispKind(gameLike, liveJob)),
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

/** The "Confirmed current" fact's value (WP LIBRARY-2: the 2x2 facts grid
 * carries the label, so the value is the time alone). Honest per
 * lib/detail-wording.js: never confirmed reads "Not yet". */
function confirmedCurrentValue(lastPrefillAt, lastManifestCheck) {
  const wording = confirmedCurrentWording(lastPrefillAt, lastManifestCheck);
  if (wording === CONFIRMED_CURRENT_WORDING.NEVER_CONFIRMED) return "Not yet";
  const when = formatTimestamp(lastManifestCheck);
  if (wording === CONFIRMED_CURRENT_WORDING.CONFIRMED_BEFORE_CACHE_CLEARED) {
    return `${when} (before the cache was cleared)`;
  }
  return when;
}

/** The "Last downloaded" fact's value. */
function lastDownloadedValue(lastPrefillAt) {
  return lastPrefillAt ? formatTimestamp(lastPrefillAt) : "Never";
}

/** The "Size" fact's value. */
function sizeValue(gameLike) {
  return formatBytesGB(gameLike.size_bytes) || "Unknown";
}

/**
 * The permanent hero band (WP LIBRARY-2): full bleed, a gradient from the
 * game's fallback hues (the same pair its cover tile uses). render() layers
 * the Steam header art over it once the game has loaded
 * (components/header-art.js removes its own wrapper on failure, leaving the
 * gradient).
 */
function buildHero() {
  const { h1, h2 } = fallbackHues(state.appid);
  const hero = document.createElement("div");
  hero.className = "gd-hero";
  hero.style.setProperty("--h1", String(h1));
  hero.style.setProperty("--h2", String(h2));
  return hero;
}

function buildMiniCover(appid, coverUrl, cachedUrl) {
  const { h1, h2 } = fallbackHues(appid);
  const cap = document.createElement("div");
  cap.className = "cap gd-mini";
  cap.style.setProperty("--h1", String(h1));
  cap.style.setProperty("--h2", String(h2));
  const img = document.createElement("img");
  img.className = "cover";
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  // WP D10: stored copy, then server CDN cover, then legacy (cover-art.js).
  wireCoverFallback(img, coverCandidates(appid, coverUrl, cachedUrl), () => img.remove());
  cap.appendChild(img);
  return cap;
}

/**
 * The header row under the hero (WP LIBRARY-2): the mini cover overlapping
 * the hero, the title with "App N" and the status pill. render() appends
 * the action group (Verify + the primary action) as its last child.
 * @param {object} gameLike
 * @param {object|undefined} liveJob
 */
function buildHeader(gameLike, liveJob) {
  const dhead = document.createElement("div");
  dhead.className = "dhead gd-head";
  // WP API-FIX-5: the detail's server-resolved cover, else the list row's.
  // WP D10: plus vault-api's stored copy, same precedence.
  dhead.appendChild(
    buildMiniCover(
      state.appid,
      state.detail?.cover_url ?? gameLike?.cover_url,
      state.detail?.cover_cached_url ?? gameLike?.cover_cached_url,
    ),
  );

  const info = document.createElement("div");
  info.className = "gd-id";
  const h2 = document.createElement("h2");
  // WP API-FIX-4: a tool app's name comes from the server's tool list.
  h2.textContent = sheetTitle(gameLike);
  const sub = document.createElement("div");
  sub.className = "gd-sub";
  const appidLine = document.createElement("span");
  appidLine.className = "appid";
  appidLine.textContent = `App ${state.appid}`;
  sub.append(appidLine, buildDetailStatusRow(currentUpdateRow() || gameLike, dispKind(gameLike, liveJob)));
  info.append(h2, sub);

  dhead.appendChild(info);
  return dhead;
}

/**
 * The header's status pill (kit `.pill` via components/pill.js: the
 * status-icon disc plus the word). WP UPD-6 review fix: the same "Update
 * available" overlay as the Library card for a cached game whose row — the
 * group row for a game with packages — carries `update_available: true`.
 * Exported for web/tests/game-detail-sheet-update.test.js.
 * @param {object} row the game's row (or its group row)
 * @param {string} kind the game's dispKind
 * @returns {HTMLElement}
 */
export function buildDetailStatusRow(row, kind) {
  // WP CACHE-COMPLETE-1c: the card's overlay (completeness after UPD-6).
  const display = cardDisplay(row, kind, STATUS_LABEL);
  const pill = createPill({ kind: display.icon, text: display.word });
  pill.classList.add("detail-statusrow");
  pill.dataset.text = display.text;
  // WP 4a.8 icon audit: the word right after the disc already says the
  // same thing visibly — hide the disc from assistive tech.
  const icon = pill.querySelector(".sic");
  if (icon) icon.setAttribute("aria-hidden", "true");
  return pill;
}

/**
 * WP UPD-6 review fix: "Update now" for a flagged cached game. `onStart`
 * runs on click with the button busy (`disabled`, like the sheet's other
 * action buttons). Exported for web/tests/game-detail-sheet-update.test.js.
 * @param {number[]} appids what the press sends (`data-appids`)
 * @param {() => Promise<void>} onStart
 * @returns {HTMLButtonElement}
 */
export function buildUpdateNowButton(appids, onStart) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn pri";
  btn.dataset.role = "update-now";
  btn.textContent = UPDATE_NOW_LABEL;
  btn.dataset.appids = appids.join(",");
  btn.addEventListener("click", () => withButtonBusy(btn, onStart));
  return btn;
}

/** `POST /v1/prefill` for the sheet's "Update now" — the same non-forced
 * enqueue as Downloads' "Update now" and "Check & update". */
async function startSheetUpdate(appids) {
  try {
    const refs = await api.prefill(appids);
    const outcome = summarizeUpdateStart(refs, state.games);
    showToast(outcome.message, { warn: outcome.warn });
    store.refreshNow();
  } catch (err) {
    const outcome = describeUpdateStartError(err);
    showToast(outcome.message, { warn: true });
    if (outcome.refresh) store.refreshNow();
  }
}

/**
 * WP CACHE-COMPLETE-1c: the completeness block (module header). `null` when
 * the game shows none (a tool app, or nothing on disk). Exported for
 * web/tests/game-detail-sheet-completeness.test.js.
 * @param {object} gameLike the game's own row
 * @param {object | null} group its group row, or null
 * @param {number[]} appids what "Verify" sends; no button when empty
 * @param {() => Promise<void>} onVerify
 * @param {string} title the game's name, for the button's accessible name
 * @returns {HTMLElement | null}
 */
export function buildCompletenessSection(gameLike, group, appids, onVerify, title) {
  if (!showsCompletenessBlock(gameLike, group)) return null;
  const wrap = document.createElement("div");
  wrap.className = "facts detail-block";
  wrap.dataset.role = "completeness";
  const line = document.createElement("div");
  line.dataset.role = "complete-line";
  line.textContent = completenessLine(gameLike);
  wrap.appendChild(line);
  const groupText = groupCompletenessLine(group);
  if (groupText) {
    const groupLine = document.createElement("div");
    groupLine.dataset.role = "complete-group";
    groupLine.textContent = groupText;
    wrap.appendChild(groupLine);
  }
  if (isDamagedOnly(gameLike)) {
    const note = document.createElement("div");
    note.className = "tx-partial";
    note.dataset.role = "damaged-note";
    note.textContent = DAMAGED_NOTE;
    wrap.appendChild(note);
  }
  if (appids.length) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn sm detail-block";
    btn.dataset.role = "verify";
    btn.dataset.appids = appids.join(",");
    btn.textContent = VERIFY_LABEL;
    btn.setAttribute("aria-label", verifyAriaLabel(title));
    // The hint is visible text under the button and its description.
    const hint = document.createElement("p");
    hint.className = "hint detail-verify-hint";
    hint.id = "detail-verify-hint";
    hint.dataset.role = "verify-hint";
    hint.textContent = VERIFY_HINT;
    btn.setAttribute("aria-describedby", hint.id);
    btn.addEventListener("click", () => withButtonBusy(btn, onVerify));
    wrap.append(btn, hint);
  }
  return wrap;
}

/** `POST /v1/prefill/verify` for the sheet's "Verify" (WP
 * CACHE-COMPLETE-1c): a forced prefill per app id. */
async function startSheetVerify(appids) {
  try {
    const refs = await api.prefillVerify(appids);
    const outcome = summarizeVerifyStart(refs, "verify");
    showToast(outcome.message, { warn: outcome.warn });
    store.refreshNow();
  } catch (err) {
    const outcome = describeVerifyStartError(err);
    showToast(outcome.message, { warn: true });
    if (outcome.refresh) store.refreshNow();
  }
}

/** The sheet's game title (header and the Verify button's name). WP
 * API-FIX-4: a tool app's name comes from the server's tool list. */
function sheetTitle(gameLike) {
  return state.detail?.name?.trim() || toolAppName(gameLike) || state.name || UNKNOWN_APP_NAME;
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

/**
 * WP NOT-OWNED-2: the small neutral note under the header of an app that
 * stays Cached although its last prefill ended `not_owned`.
 * @param {object} gameLike
 * @returns {HTMLElement | null}
 */
export function buildNotOwnedNote(gameLike) {
  if (!showsNotOwnedNote(gameLike)) return null;
  const note = document.createElement("p");
  note.className = "hint detail-block";
  note.dataset.role = "not-owned-note";
  note.textContent = NOT_OWNED_LIBRARY_NOTE;
  return note;
}

/** Fill (or hide) the "Last update downloaded …" fact line. */
function syncUpdateDownloadedLine(el, gameLike) {
  const text = lastUpdateDownloadedText(gameLike);
  el.textContent = text || "";
  el.hidden = !text;
}

/** One cell of the Overview's facts grid: an uppercase label and a value
 * whose `data-role` patchVolatile writes. */
function factCell(label, role, value) {
  const cell = document.createElement("div");
  cell.className = "gd-fact";
  const k = document.createElement("div");
  k.className = "k";
  k.textContent = label;
  const v = document.createElement("div");
  v.className = "v";
  v.dataset.role = role;
  v.textContent = value;
  cell.append(k, v);
  return { cell, v };
}

/**
 * The Overview's facts (WP LIBRARY-2): a 2x2 grid — Size, Last downloaded,
 * Confirmed current, Cache — then the notes that only sometimes apply
 * (damaged chunks, last update downloaded, the --force line) under it.
 */
function buildFactLines(gameLike) {
  const facts = document.createElement("div");
  facts.className = "facts detail-block";
  const grid = document.createElement("div");
  grid.className = "gd-facts";
  facts.appendChild(grid);

  grid.appendChild(factCell("Size", "size", sizeValue(gameLike)).cell);
  grid.appendChild(factCell("Last downloaded", "lastdl", lastDownloadedValue(gameLike.last_prefill_at)).cell);
  grid.appendChild(
    factCell("Confirmed current", "confirmed", confirmedCurrentValue(gameLike.last_prefill_at, gameLike.last_manifest_check))
      .cell,
  );

  // LIBRARY-DETAIL-1: the cache status stays visible on Overview (the Verify
  // button lives in Maintenance and the header). Distinct data-role from the
  // Verify box's "complete-line" so neither copy shadows the other.
  if (showsCompletenessBlock(gameLike, currentGroup())) {
    const { cell, v: cacheLine } = factCell("Cache", "cache-status-cell", "");
    cacheLine.dataset.role = "cache-status";
    cacheLine.textContent = completenessLine(gameLike);
    cacheLine.classList.add("long");
    grid.appendChild(cell);
    if (isDamagedOnly(gameLike)) {
      const damaged = document.createElement("div");
      damaged.className = "tx-partial";
      damaged.dataset.role = "cache-damaged";
      damaged.textContent = DAMAGED_NOTE;
      facts.appendChild(damaged);
    }
  }

  // WP WEB-FEAT-SUGGEST-2: the Phase 4h panel's "last changed N days ago",
  // moved here. Hidden (not absent) when there is no real observation, so
  // patchVolatile can fill it in place on a later tick.
  const updateDownloadedLine = document.createElement("div");
  updateDownloadedLine.dataset.role = "updatedl";
  syncUpdateDownloadedLine(updateDownloadedLine, gameLike);
  facts.appendChild(updateDownloadedLine);

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
  btn.className = "btn" + (variant ? " " + variant : "");
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
  row.className = "jobacts";
  if (actions.has(DETAIL_JOB_ACTION.RESUME)) {
    row.appendChild(
      actionButton("Resume", "pri", () => runJobControl(() => api.resumeJob(job.id), "Resuming — back in the queue")),
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
  btn.className = "btn pri";
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
    const expected = buildExpectedBlock();
    if (expected) {
      wrap.appendChild(expected);
      return wrap;
    }
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
  h3.textContent = state.name || UNKNOWN_APP_NAME;
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
  const expected = buildExpectedBlock();
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
  // WP D19-1c: the store block (when the switch is on and the store knew the
  // app) sits between the note and the expected depots.
  wrap.append(...[h3, p, buildStoreBlock(), expected, btn].filter((el) => el !== null));
  return wrap;
}

/** The Download / Resume / Pause / Update-now row under the header, or null. */
function buildPrimaryAction(gameLike, trackedJob) {
  if (trackedJob) return buildJobControlRow(trackedJob);
  const action = statusAction(gameLike, undefined, false);
  if (action) return buildDownloadButton(action);
  // WP UPD-6 review fix: a flagged cached game (or group) offers its update.
  const updateAppids = currentUpdateAppids();
  if (updateAppids.length) return buildUpdateNowButton(updateAppids, () => startSheetUpdate(updateAppids));
  return null;
}

/** A titled box of the Maintenance tab. */
function buildMaintenanceBox(title, children, { danger = false } = {}) {
  const box = document.createElement("div");
  box.className = danger ? "detail-box detail-box-danger" : "detail-box";
  const h = document.createElement("h4");
  h.className = "detail-box-title";
  h.textContent = title;
  box.append(h, ...children);
  return box;
}

/**
 * The Maintenance tab: Verify (the completeness block with its button),
 * garbage collection, and the red "Delete from cache" zone. Boxes that do
 * not apply to this game are left out; the GC box is the persistent
 * `gcSectionEl` (empty and hidden when the game has no depots).
 */
function buildMaintenancePanel(gameLike, group) {
  const panel = document.createElement("div");
  panel.className = "detail-panel";
  panel.dataset.role = "maintenance";

  const verifyIds = currentVerifyAppids();
  const completeness = buildCompletenessSection(
    gameLike,
    group,
    verifyIds,
    () => startSheetVerify(verifyIds),
    sheetTitle(gameLike),
  );
  if (completeness) panel.append(buildMaintenanceBox("Verify", [completeness]));

  panel.append(gcSectionEl);

  // WP TOOL-APPS-2c: a game whose packages hold bytes can be deleted too.
  const canDelete = hasVisibleCacheContent(gameLike) || (group && groupHasCacheContent(group));
  if (canDelete) {
    const text = document.createElement("p");
    text.className = "hint";
    text.textContent = "Removes this game's chunks from the vault. Depots another cached game still uses stay.";
    panel.append(buildMaintenanceBox("Delete from cache", [text, buildDeleteButton()], { danger: true }));
  }

  if (!completeness && !canDelete && !(state.detail && state.detail.depots.length)) {
    const none = document.createElement("p");
    none.className = "hint";
    none.dataset.role = "maintenance-empty";
    none.textContent = "Nothing to maintain until this game has cached content.";
    panel.append(none);
  }
  return panel;
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

  // WP LIBRARY-2: the hero band is permanent (gradient while loading, for
  // an error and for an untracked app; header art over it once loaded).
  if (state.notTracked) {
    contentEl.append(buildHero(), bodyWrap(renderNotTracked()));
    gcSectionEl.replaceChildren();
    lastStructuralKey = null;
    return;
  }
  if (state.loadError) {
    contentEl.append(buildHero(), bodyWrap(renderLoadError()));
    gcSectionEl.replaceChildren();
    lastStructuralKey = null;
    return;
  }
  if (!state.detail) {
    contentEl.append(buildHero(), bodyWrap(renderLoading()));
    gcSectionEl.replaceChildren();
    lastStructuralKey = null;
    return;
  }

  const gameLike = currentGameLike();
  const liveJob = findLiveJob(state.jobs, state.appid);
  const trackedJob = findTrackedJob(state.jobs, state.appid);

  const hero = buildHero();
  hero.append(buildHeaderArt(state.appid));
  contentEl.append(hero);
  const head = buildHeader(gameLike, liveJob);
  contentEl.append(head);

  // The header row's actions (WP LIBRARY-2): Verify, then the primary
  // action (job controls, download or update). They are about the game,
  // not about one tab.
  const acts = document.createElement("div");
  acts.className = "gd-acts";
  const headVerify = buildHeadVerifyButton(gameLike);
  if (headVerify) acts.append(headVerify);
  const actions = buildPrimaryAction(gameLike, trackedJob);
  if (actions) acts.append(actions);
  if (acts.childNodes.length) head.append(acts);

  // Overview, two columns (WP LIBRARY-2, library.html): left the facts
  // grid, "Installed on" and About (store details, only when
  // VAULT_STORE_DETAILS is on and the store knew the app); right the
  // content packages. One column when there are none, or in a narrow sheet.
  const overview = document.createElement("div");
  overview.className = "detail-panel gd-ov";
  const left = document.createElement("div");
  left.className = "gd-col";
  const right = document.createElement("div");
  right.className = "gd-col";
  const storeBlock = buildStoreBlock();
  const toolNote = buildToolAppNote(gameLike);
  if (toolNote) left.append(toolNote);
  const notOwnedNote = buildNotOwnedNote(gameLike);
  if (notOwnedNote) left.append(notOwnedNote);
  left.append(buildFactLines(gameLike));

  const installedSection = buildInstalledSection(gameLike);
  if (installedSection) left.append(installedSection);
  if (storeBlock) left.append(buildAboutSection(storeBlock));

  // WP TOOL-APPS-2c: the game's content packages.
  const group = currentGroup();
  if (group) {
    right.append(
      buildPackagesSection(group, {
        groupSizeBytes: group.group_size_bytes,
        ...packageJobLookups(),
        onRetry: retryPackage,
      }),
    );
  }
  overview.append(left);
  if (right.childNodes.length) overview.append(right);
  else overview.classList.add("one");

  const depotsPanel = document.createElement("div");
  depotsPanel.className = "detail-panel";
  depotsPanel.append(buildDepotsSection());

  const maintenance = buildMaintenancePanel(gameLike, group);

  const tabs = createTabs({
    idPrefix: "detail",
    label: "Game detail sections",
    tabs: [
      { id: "overview", label: "Overview", panel: overview },
      { id: "depots", label: "Depots", panel: depotsPanel },
      { id: "maintenance", label: "Maintenance", panel: maintenance },
    ],
    selected: activeTab,
    onSelect: (id) => {
      activeTab = id;
    },
  });
  const body = document.createElement("div");
  body.className = "gd-body";
  body.append(overview, depotsPanel, maintenance);
  contentEl.append(tabs.tablist, body);

  renderGcSection();

  lastStructuralKey = computeStructuralKey();
}

/** The padded body under the hero for the loading / error / not-tracked
 * states. */
function bodyWrap(child) {
  const body = document.createElement("div");
  body.className = "gd-body gd-state";
  body.append(child);
  return body;
}

/** The "About" block of the Overview's left column: the store details
 * under a section label. */
function buildAboutSection(storeBlock) {
  const wrap = document.createElement("div");
  wrap.className = "detail-block gd-about";
  const heading = document.createElement("h4");
  heading.className = "sec";
  heading.textContent = "About";
  wrap.append(heading, storeBlock);
  return wrap;
}

/**
 * WP LIBRARY-2: the header's "Verify" — the same forced prefill as the
 * Maintenance tab's Verify box (`POST /v1/prefill/verify`), offered when
 * that box offers it.
 * @param {object} gameLike
 * @returns {HTMLButtonElement | null}
 */
function buildHeadVerifyButton(gameLike) {
  const ids = currentVerifyAppids();
  if (!ids.length || !showsCompletenessBlock(gameLike, currentGroup())) return null;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn";
  btn.dataset.role = "head-verify";
  btn.dataset.appids = ids.join(",");
  btn.textContent = VERIFY_LABEL;
  btn.setAttribute("aria-label", verifyAriaLabel(sheetTitle(gameLike)));
  btn.title = VERIFY_HINT;
  btn.addEventListener("click", () => withButtonBusy(btn, () => startSheetVerify(ids)));
  return btn;
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
  if (sizeEl) sizeEl.textContent = sizeValue(gameLike);

  const lastDlEl = contentEl.querySelector('[data-role="lastdl"]');
  if (lastDlEl) lastDlEl.textContent = lastDownloadedValue(gameLike.last_prefill_at);

  const confirmedEl = contentEl.querySelector('[data-role="confirmed"]');
  if (confirmedEl) confirmedEl.textContent = confirmedCurrentValue(gameLike.last_prefill_at, gameLike.last_manifest_check);

  const updateDownloadedEl = contentEl.querySelector('[data-role="updatedl"]');
  if (updateDownloadedEl) syncUpdateDownloadedLine(updateDownloadedEl, gameLike);

  patchInstalledSection(contentEl, gameLike);

  // WP CACHE-COMPLETE-1c: the figures move without a structural change.
  const completeLineEl = contentEl.querySelector('[data-role="complete-line"]');
  if (completeLineEl) completeLineEl.textContent = completenessLine(gameLike);
  const cacheStatusEl = contentEl.querySelector('[data-role="cache-status"]');
  if (cacheStatusEl) cacheStatusEl.textContent = completenessLine(gameLike);
  const completeGroupEl = contentEl.querySelector('[data-role="complete-group"]');
  if (completeGroupEl) completeGroupEl.textContent = groupCompletenessLine(currentGroup()) || "";

  const group = currentGroup();
  if (group) patchPackagesSection(contentEl, group.packages, group.group_size_bytes);

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
  // as the delete-confirm dialog's "Keep" focus.
  if (openingGcConfirm) gcNo.focus();

  if (!state.detail || !state.detail.depots.length) return; // nothing to collect

  const heading = document.createElement("h4");
  heading.className = "detail-box-title";
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
