/**
 * Downloads view (WP 4a.5).
 *
 * Active job(s), the FIFO queue, and history with lazily-fetched log
 * excerpts. Ports docs/design/vault-app-mockup.html's Downloads screen
 * onto the REAL WP 3.12 job-control semantics — see js/lib/job-partition.js
 * for the one deliberate, DOCUMENTED divergence from the mockup: pause
 * releases the worker slot (api/README.md "The worker slot"), so a paused
 * job gets its own "Paused" section instead of pretending it still
 * occupies the mockup's single active slot.
 *
 * Data flows exclusively through the WP 4a.2 store (store-singleton.js) —
 * no parallel poll loop is created here, and `store.refreshNow()` only
 * re-polls vault-api (never a download trigger — Phase 4c guard,
 * docs/PROJECT_PLAN.md). Job control (pause/resume/cancel) is optimistic-UI
 * OFF: a click only calls the endpoint and nudges an immediate re-poll: the
 * next `GET /v1/jobs` tick is what actually updates what's on screen,
 * matching the mockup's own "server confirms" pattern (docs/design/
 * vault-app-mockup-NOTES.md "Endpoint mapping").
 *
 * Round-7 patch-in-place rule, ported: `js/lib/downloads-render-plan.js`'s
 * `planJobsUpdate` decides, per `GET /v1/jobs` tick, whether anything
 * structural changed (any `status` transition anywhere -> full section
 * rebuild) or whether the only thing that moved is a running job's
 * `stop_request` (-> patch just that card's action row and stop note, never
 * its status pill's icon subtree). See that module's header for why
 * `stop_request` is the ONLY volatile field the real API has here — no
 * live byte progress exists to patch, unlike the mockup.
 *
 * **Titles (WP WEB-FIX-4).** A job carries only an appid, and vault-api
 * has no name for an app it never resolved (an owned-only game queued from
 * its detail sheet gets an `apps` row with `name = NULL`). Every title here
 * is `appTitle`'s order: vault name, then the owned-games list's name
 * (`owned-singleton.js`), then "App <id>". This view never polls the relay:
 * it uses the list the Library already loaded, and only when a job on
 * screen has no vault name AND no load was ever started in this page's
 * life (`/downloads` opened directly) does it start that one load
 * (`ownedLibrary.loadIfNeverLoaded`).
 *
 * **Known failures (WP WEB-FIX-4).** A failed prefill whose log ends in
 * vault-api's `reason=not_logged_in` line, or an `exit_code` failure whose
 * output carries SteamPrefill's public-IP cache-detection error
 * (`lib/job-failure.js`), shows a short how-to and a Retry button at the
 * top of its expanded history row; the raw SteamPrefill output moves into a
 * closed `<details>`. Every other failure shows its output as before:
 * there the output IS the diagnosis, and no summary exists that could
 * replace it.
 *
 * **Pause all / Resume all (WP WEB-FEAT-5).** A bar under the heading
 * pauses every queued prefill job and then the running one, or resumes
 * every paused job (decisions and wording: `lib/bulk-jobs.js`). Same
 * non-optimistic posture as the per-job buttons: the calls go out (at most
 * `BULK_CONCURRENCY` at once), one aggregate toast reports what the server
 * did, then `store.refreshNow()` repaints. Pause all asks first, naming the
 * count; Resume all does not. The bar is built once per mount and patched
 * (`paintBulkBar`), so a poll tick does not take focus from its buttons.
 *
 * **Statistics header, per-job speed, history filter (WP D15c, D13/D15).**
 * In the hero (since DOWNLOADS-2, see Layout below): current, average ("while loading") and peak speed,
 * GB loaded, hit rate, GB saved by the cache and active clients from
 * `GET /v1/throughput`, plus a small inline-SVG graph of
 * `GET /v1/throughput/series`, both for a 24 h / 7 days / 30 days / All
 * range (`lib/traffic-view.js` has the wording and the honest-state rules:
 * feed disabled -> a note and no numbers, stale/never-swept -> a dash for
 * the current values). They are polled every 30 s only while this view is
 * open (`createViewPoll`); the store's jobs poll is untouched. A running
 * job's card gets a speed line from the same answer's `jobs[]`, patched in
 * place (never the badge's status icon). The History list has its own range
 * filter (default All): `GET /v1/jobs?range=&limit=200`, fetched when the
 * view opens and when the filter changes, merged with the store's newest 20
 * (the fresher copy wins) and filtered client-side by the same rule, so a
 * job that finishes while the view is open appears at once.
 *
 * **Updates available (WP UPD-6).** A section (middle column since DOWNLOADS-2)
 * lists the games whose `update_available` is set and that have no active
 * job (`lib/updates-available.js` `pendingUpdates`), each with "Update now",
 * plus "Update all" and a note on whether they download by themselves
 * (`scheduleNote`, from `GET /v1/schedule`, read once per mount). Both
 * buttons send `POST /v1/prefill` with the listed app ids — the enqueue
 * "Check & update" uses, never a forced run of its own. The game then sits
 * in the Queue; it leaves this list as soon as a jobs poll shows its job.
 * The section is rebuilt only when its content changes
 * (`updatesListKey`), so a poll tick does not take a button's focus.
 *
 * **Incomplete (WP CACHE-COMPLETE-1c).** Right after "Updates available",
 * same pattern: the games whose measured `cache_complete_pct` is below 100,
 * with no active job and no update flag (lib/cache-completeness.js
 * `pendingIncomplete`), each with its figure and "Complete now" (`POST
 * /v1/prefill/verify`, a forced run), plus "Complete all" (the same
 * route with exactly the listed completable rows, like "Update all") and a
 * schedule note (`incompleteScheduleNote`:
 * completed automatically in the window, or manual only). A game short only
 * of damaged (wrong-size) chunks has no "Complete now" — a forced run
 * cannot fix it — and says so in its row; "Complete all" is offered only
 * while at least one row can be completed. Like every Downloads section
 * since UPD-6 (user rule 2026-10-14) it is rendered only with at least one
 * entry, and rebuilt only when its content changes.
 *
 * **Delete history (WP D15-NAMES).** Under the History filter, a "Delete
 * history…" button opens a confirm dialog (alertdialog, focus on "Keep")
 * with two choices, "Older than 30 days" (preselected) and "Everything",
 * and `DELETE /v1/history?scope=` behind the confirm button. It lives here
 * and not on the Clients page because both halves of what it deletes
 * (finished jobs and the traffic statistics, `api/vault_api/history.py`)
 * are shown on this page, next to the History time filter they are
 * measured against; the Clients page only reads part of the statistics.
 * After a delete the history list, the hero statistics and the store's
 * jobs are fetched again; a failure keeps the dialog open with the error.
 * Wording: `lib/history-delete.js`.
 *
 * **Layout (WP DOWNLOADS-2, docs/design/rc20/kit.html "Downloads,
 * width-aware").** The view root carries `kit` (css/kit.css, ADR-0028) and
 * is its own inline-size container. Top to bottom: the page head (title,
 * subline, Pause all / Resume all on the right), the hero
 * (`components/downloads-hero.js`: speed, pill, limit + devices caption,
 * graph, mini line, four tiles), then three equal columns: Active + Queue |
 * Updates available + Incomplete | History. Container queries in kit.css
 * fold them to two columns (History full width) below 1100px and one column
 * at 760px; the middle column is left out while both of its lists are
 * empty. Paused jobs fold into Active (their card carries Resume). Rows are
 * built by `components/job-row.js` and `components/history-list.js`; this
 * module keeps the data flow, polling and handlers.
 *
 *
 * `highlightJob(jobId)` (WP 4a.7) is this module's one export beyond
 * `renderDownloads` — the notification bell's "job events -> Downloads
 * with the job highlighted" navigation target lands here without any
 * other module reaching into this view's internals.
 */

import { store } from "../store-singleton.js";
import { createViewPoll, VIEW_POLL_MS } from "../store.js";
import { api, getStoredApiKey, isDemoMode } from "../api.js";
import { ERROR_KINDS } from "../errors.js";
import { createRangeSegs } from "../components/range-segs.js";
import { createDownloadsHero } from "../components/downloads-hero.js";
import {
  createJobCard,
  createAttentionRow,
  createQueueRow,
  ROW_UPDATE_LABEL,
  ROW_VERIFY_LABEL,
  ROW_REMOVE_LABEL,
  UPDATE_ALL_LABEL,
  VERIFY_ALL_LABEL,
  verifyAllAriaLabel,
} from "../components/job-row.js";
import { createHistoryItem, setHistoryItemExpanded, paintHistoryPanel } from "../components/history-list.js";
import {
  emptyHistoryText,
  feedState,
  filterJobsByRange,
  headerTiles,
  rateCapLine,
  isRange,
  isStatsRange,
  readStoredStatsRange,
  LIVE_POLL_MS,
  LIVE_SUMMARY_MS,
  LIVE_SUMMARY_RANGE,
  liveSeries,
  mergeLive,
  writeStoredStatsRange,
  jobSpeedText,
  JOB_SPEED_NOTE,
  mergeJobLists,
} from "../lib/traffic-view.js";
import { showToast } from "../components/toast.js";
import {
  partitionJobs,
  countPending,
  queuePosition,
  jobIconKind,
  jobStatusWord,
  activeJobKind,
  activeJobWord,
} from "../lib/job-partition.js";
import { planJobsUpdate } from "../lib/downloads-render-plan.js";
import { reorderIds, keyTargetIndex, dropIndex, withQueuePositions } from "../lib/queue-reorder.js";
import { selectExcerptDisplay } from "../lib/log-excerpt.js";
import { formatTimestamp, formatBytesGB } from "../lib/format.js";
import {
  pendingUpdates,
  updateAllAriaLabel,
  updateNowAriaLabel,
  scheduleNote,
  summarizeUpdateStart,
  describeUpdateStartError,
  updatesListKey,
  UPDATES_HEADING,
  STARTING_LABEL,
} from "../lib/updates-available.js";
import {
  pendingIncomplete,
  completeNowAriaLabel,
  incompletePctText,
  incompleteScheduleNote,
  incompleteListKey,
  isCompletable,
  summarizeVerifyStart,
  describeVerifyStartError,
  INCOMPLETE_HEADING,
  DAMAGED_NOTE,
} from "../lib/cache-completeness.js";
import { onViewChange } from "../router.js";
import { isConnectionLost, onConnectionChange } from "../connection-status.js";
import { OFFLINE_CONTROL_TITLE } from "../lib/connection-watch.js";
import { fillMissingNames, hasText, vaultRowTitle } from "../lib/owned-library.js";
import { isToolApp, KIND } from "../lib/game-status.js";
import { ownedLibrary } from "../owned-singleton.js";
import { contentPackageFallbackTitle } from "../lib/content-groups.js";
import { jobFailureHint, isFailedPrefill, retryOffer } from "../lib/job-failure.js";
import { pushModal, popModal } from "../lib/modal-stack.js";
import {
  HISTORY_DELETE_KEEPS,
  HISTORY_DELETE_OPTIONS,
  HISTORY_DELETE_TITLE,
  HISTORY_DELETE_WHAT,
  SCOPE_OLDER,
  historyDeleteButtonText,
  historyDeleteErrorText,
  historyDeletedText,
  isHistoryScope,
} from "../lib/history-delete.js";
import {
  HISTORY_LIMIT,
  HISTORY_FIRST_PAGE,
  HISTORY_CAPPED_TEXT,
  historyPage,
  idleSubtitle,
  isHistoryCapped,
  nextShown,
  showMoreLabel,
  showMoreText,
} from "../lib/history-paging.js";
import {
  WORDING as BULK,
  bulkBarState,
  bulkPauseTargets,
  bulkResumeTargets,
  runBulkPause,
  runBulkResume,
  bulkSummary,
  pauseAriaLabel,
  resumeAriaLabel,
  confirmTitle,
} from "../lib/bulk-jobs.js";

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed";
}

/** Whether the OS/browser has `prefers-reduced-motion: reduce` set. Used
 * only by `applyPendingHighlight()`'s `scrollIntoView()` call below — see
 * that function's header for why an explicit `behavior` option needs its
 * own reduced-motion check rather than relying on css/theme.css's
 * `scroll-behavior` override. */
function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

// Static, non-interpolated decorative markup only (no user data ever flows
// through this helper) — same trust level as library.js's segButton, the
// documented pattern for CSP-clean literal SVG.
function staticIcon(svgMarkup) {
  const span = document.createElement("span");
  span.innerHTML = svgMarkup;
  return span.firstElementChild;
}
const GRIP_SVG =
  '<svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><circle cx="7" cy="4" r="1.5"/><circle cx="13" cy="4" r="1.5"/><circle cx="7" cy="10" r="1.5"/><circle cx="13" cy="10" r="1.5"/><circle cx="7" cy="16" r="1.5"/><circle cx="13" cy="16" r="1.5"/></svg>';

// ---------------------------------------------------------------------
// Module-level state. Persists across re-mounts (navigating away and back
// within the same session), same posture as library.js's `state`.
// ---------------------------------------------------------------------
const state = {
  jobs: store.snapshot("jobs") || [],
  games: store.snapshot("games") || [],
  // WP WEB-FIX-4: false until a real `GET /v1/games` answer is in. Before
  // that every job "lacks" a vault name, and that must not start the
  // owned-list load.
  gamesKnown: Array.isArray(store.snapshot("games")),
  // WP UPD-6: `GET /v1/schedule`, read once per mount; null = not known.
  schedule: null,
  // WP RATE-TILE: `GET /v1/settings`, read once per mount; null = not known.
  settings: null,
};

/** WP D15c: the hero statistics's last answer for `range`
 * (`{throughput, series}`), and the History filter's fetched rows. Module
 * state, so a re-mount keeps the chosen ranges. */
const traffic = { range: readStoredStatsRange(), throughput: null, series: null, error: null, unsupported: false, noFigures: false };
/** WP DOWNLOADS-3: `shown` = rows on screen (first page, then +20 per
 * "Show more"); `loaded` = the range fetch has answered at least once, so
 * the subline may count the list. */
const history = { range: "all", jobs: [], gen: 0, shown: HISTORY_FIRST_PAGE, loaded: false };

/** A job by id from the store's list or the history fetch. */
function findJobById(jobId) {
  return state.jobs.find((j) => j.id === jobId) || history.jobs.find((j) => j.id === jobId);
}

/** jobId -> {expanded, loading, error, excerpt}. Persists across re-mounts
 * AND across a full section rebuild triggered by an unrelated job
 * transition, so expanding a history row survives a poll tick that
 * structurally changes some OTHER job. `excerpt` is `undefined` until the
 * lazy `GET /v1/jobs/{id}` fetch has completed at least once — distinct
 * from a job that genuinely has no log output (`""`/`null`). */
const excerptState = new Map();
function getExcerptState(jobId) {
  if (!excerptState.has(jobId)) {
    // `rawOpen`: the not_logged_in row's raw-output <details>, kept here so a
    // full section rebuild does not snap it shut again.
    excerptState.set(jobId, { expanded: false, loading: false, error: null, excerpt: undefined, rawOpen: false });
  }
  return excerptState.get(jobId);
}

/** The currently-mounted <section>, or null. Store-subscription callbacks
 * check this before touching the DOM so a background tick while a
 * DIFFERENT view is showing (or after this one was navigated away from) is
 * a cheap no-op — `onViewChange` below is what nulls `sectionEl` out the
 * instant the user leaves this view, which is the ONLY staleness signal
 * this needs.
 *
 * Deliberately NOT also checking `sectionEl.isConnected` (library.js's
 * variant does): `renderDownloads()` builds the section, assigns it to
 * `sectionEl`, and calls `fullRender()` synchronously, all BEFORE
 * `app.js`'s `viewRoot.replaceChildren(render())` has attached it to the
 * document — so at that exact moment `isConnected` is still `false` even
 * though this genuinely is the current, about-to-be-shown section. Gating
 * on `isConnected` made the very first paint of every navigation to this
 * view silently no-op, leaving an empty shell until whatever poll tick
 * happened to land next attached the section in the meantime (found while
 * manually verifying this WP — a real, observable empty-Downloads-screen
 * bug, not a hidden-tab timing artifact of the verification harness). */
let sectionEl = null;
function mounted() {
  return sectionEl !== null;
}

let els = null;

/** Vault rows by appid, names filled from the owned list (WP WEB-FIX-4). */
function gamesByAppidMap() {
  return new Map(fillMissingNames(state.games, ownedLibrary.current().games).map((g) => [g.appid, g]));
}
function nameFor(appid, gamesByAppid) {
  const game = gamesByAppid.get(appid);
  // WP TOOL-APPS-2c (ADR-0022 §2): a content package without a name of its
  // own reads "<parent name> · content <appid>", not "App <appid>".
  if (game && !hasText(game.name)) {
    const grouped = contentPackageFallbackTitle(game, (p) =>
      gamesByAppid.has(p) ? vaultRowTitle(p, gamesByAppid.get(p)) : null,
    );
    if (grouped) return grouped;
  }
  // WP API-FIX-4: vaultRowTitle adds the Steam tool app's server-sent name.
  return vaultRowTitle(appid, gamesByAppid.get(appid));
}

/** WP WEB-FIX-4: start the one owned-list load this view may start (see
 * the module header) when a job on screen has no vault name. */
function maybeLoadOwnedNames() {
  if (!state.gamesKnown) return;
  const named = new Set(
    state.games.filter((g) => hasText(g.name) || (isToolApp(g) && hasText(g.tool_app_name))).map((g) => g.appid),
  );
  if (state.jobs.some((j) => !named.has(j.appid))) ownedLibrary.loadIfNeverLoaded();
}

// ---------------------------------------------------------------------
// Nav pip (this view owns the jobs data the count is computed from — see
// index.html's WP 4a.1 scaffold comment). Updated unconditionally, whether
// or not this view is the one currently mounted (mirrors the mockup's
// always-on `syncPip`, called from every job-affecting action).
// ---------------------------------------------------------------------
function updateNavPip(jobs) {
  const pip = document.getElementById("nav-pip");
  const btn = pip ? pip.closest(".nav-btn") : null;
  if (!pip) return;
  const count = countPending(jobs);
  pip.textContent = String(count);
  pip.classList.toggle("on", count > 0);
  if (btn) {
    if (count > 0) {
      btn.setAttribute("aria-label", `Downloads — ${count} pending`);
    } else {
      btn.removeAttribute("aria-label");
    }
  }
}

// ---------------------------------------------------------------------
// Job control (optimistic-UI OFF — see module header)
// ---------------------------------------------------------------------

async function withButtonBusy(btn, fn) {
  btn.disabled = true;
  try {
    await fn();
  } catch (err) {
    showToast(errorText(err), { warn: true });
  } finally {
    btn.disabled = false;
  }
}

async function onPause(jobId) {
  await api.pauseJob(jobId);
  showToast("Pause requested — bytes already fetched stay on the cache");
  store.refreshNow();
}
async function onResume(jobId) {
  await api.resumeJob(jobId);
  showToast("Resuming — back at the front of the queue");
  store.refreshNow();
}
async function onCancel(jobId) {
  await api.cancelJob(jobId);
  showToast("Cancel requested");
  store.refreshNow();
}
/** WP WEB-FIX-4: the not_logged_in block's Retry — a new prefill for the
 * same app (`POST /v1/prefill`; vault-api dedupes an in-flight one). */
async function onRetry(appid) {
  await api.prefill([appid]);
  showToast("Queued for download");
  store.refreshNow();
}

function actionButton(label, variant, handler) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn sm" + (variant ? " " + variant : "");
  btn.textContent = label;
  btn.addEventListener("click", () => withButtonBusy(btn, handler));
  gateOffline(btn);
  return btn;
}

/** WP WEB-FIX-2: while the connection banner is shown, every job-control
 * button (Pause/Resume/Cancel/Remove — all built by actionButton) is
 * disabled with an explanatory title instead of looking live and failing
 * on click. Callers that compute their own `disabled` OR into it. */
function gateOffline(btn) {
  if (!isConnectionLost()) return;
  btn.disabled = true;
  btn.title = OFFLINE_CONTROL_TITLE;
}

// ---------------------------------------------------------------------
// Pause all / Resume all (WP WEB-FEAT-5)
// ---------------------------------------------------------------------

/** `null`, or the bulk run in flight (`"pause"`/`"resume"`). Module-level,
 * so a re-mount mid-run still shows the busy state. */
let bulkBusy = null;

/** Toast duration for the aggregate result — longer than the default,
 * because it may carry a server reason. */
const BULK_TOAST_MS = 7000;

// Confirm dialog — built ONCE at module load as a `document.body` sibling
// of `#app`, the same placement library.js's delete confirm documents (a
// dialog inside `#app` would go inert with it).
const bulkBackdrop = document.createElement("div");
bulkBackdrop.className = "dialog-backdrop";
const bulkDialog = document.createElement("div");
bulkDialog.className = "dialog";
bulkDialog.setAttribute("role", "alertdialog");
bulkDialog.setAttribute("aria-modal", "true");
bulkDialog.dataset.role = "bulk-pause-confirm";
const bulkTitle = document.createElement("h3");
bulkTitle.id = "bulk-pause-title";
bulkDialog.setAttribute("aria-labelledby", "bulk-pause-title");
const bulkText = document.createElement("p");
const bulkGcNote = document.createElement("p");
bulkGcNote.textContent = BULK.confirmGcNote;
const bulkRow = document.createElement("div");
bulkRow.className = "row";
const bulkNo = document.createElement("button");
bulkNo.type = "button";
bulkNo.className = "btn ghost sm";
bulkNo.textContent = BULK.confirmNo;
bulkNo.dataset.role = "bulk-pause-no";
const bulkYes = document.createElement("button");
bulkYes.type = "button";
bulkYes.className = "btn primary sm";
bulkYes.textContent = BULK.confirmYes;
bulkYes.dataset.role = "bulk-pause-yes";
bulkRow.append(bulkNo, bulkYes);
bulkDialog.append(bulkTitle, bulkText, bulkGcNote, bulkRow);
bulkBackdrop.appendChild(bulkDialog);
document.body.appendChild(bulkBackdrop);

let bulkConfirmOpen = false;
let bulkInvokerEl = null;

function openPauseConfirm() {
  const bar = bulkBarState(state.jobs);
  if (!bar.pauseVisible || bulkBusy) return;
  bulkTitle.textContent = confirmTitle(bar.pauseCount);
  bulkText.textContent = BULK.confirmBody;
  bulkGcNote.hidden = !bar.gcActive;
  bulkYes.disabled = isConnectionLost();
  bulkInvokerEl = document.activeElement;
  bulkConfirmOpen = true;
  bulkBackdrop.classList.add("on");
  pushModal(bulkBackdrop, closePauseConfirm);
  bulkNo.focus(); // the non-destructive default gets initial focus
}

function closePauseConfirm() {
  if (!bulkConfirmOpen) return;
  bulkConfirmOpen = false;
  bulkBackdrop.classList.remove("on");
  popModal(bulkBackdrop);
  if (bulkInvokerEl && typeof bulkInvokerEl.focus === "function" && bulkInvokerEl.isConnected !== false) {
    bulkInvokerEl.focus();
  }
  bulkInvokerEl = null;
}

bulkNo.addEventListener("click", closePauseConfirm);
bulkYes.addEventListener("click", () => {
  closePauseConfirm();
  // The targets are taken from the list as it is NOW, not as it was when
  // the dialog opened: anything that finished meanwhile is simply not sent.
  runBulk("pause");
});

/**
 * Run Pause all / Resume all against the current jobs snapshot. Reached
 * only from the bar's buttons and the confirm dialog's "Pause all".
 * @param {"pause"|"resume"} kind
 */
async function runBulk(kind) {
  if (bulkBusy) return;
  const targets = kind === "pause" ? bulkPauseTargets(state.jobs) : bulkResumeTargets(state.jobs);
  const total = kind === "pause" ? targets.count : targets.length;
  if (!total) return;
  bulkBusy = kind;
  paintBulkBar();
  let results = [];
  try {
    results =
      kind === "pause"
        ? await runBulkPause(targets, (id) => api.pauseJob(id))
        : await runBulkResume(targets, (id) => api.resumeJob(id));
  } finally {
    bulkBusy = null;
    paintBulkBar();
  }
  const summary = bulkSummary(kind, results);
  showToast(summary.text, { warn: summary.warn, duration: BULK_TOAST_MS });
  store.refreshNow();
}

function bulkButton(label, variant, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn sm" + (variant ? " " + variant : "");
  btn.textContent = label;
  // While a run is busy the buttons are aria-disabled, not `disabled`, so
  // the focused one keeps focus (docs/LEARNINGS.md, WP WEB-FEAT-1); the
  // click guard for that state is `bulkBusy` in openPauseConfirm/runBulk.
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    onClick();
  });
  return btn;
}

/** Sync the bulk bar with `state.jobs`, `bulkBusy` and the connection. */
function paintBulkBar() {
  if (!mounted() || !els.bulkBar) return;
  const bar = bulkBarState(state.jobs);
  const offline = isConnectionLost();
  const { bulkBar, bulkPause, bulkResume } = els;

  // Keep a focused button's focus on a sibling when it disappears.
  const pauseHadFocus = document.activeElement === bulkPause;
  const resumeHadFocus = document.activeElement === bulkResume;

  bulkBar.hidden = !bar.visible && !bulkBusy;
  bulkPause.hidden = !bar.pauseVisible && bulkBusy !== "pause";
  bulkResume.hidden = !bar.resumeVisible && bulkBusy !== "resume";

  bulkPause.textContent = bulkBusy === "pause" ? BULK.pausing : BULK.pauseAll;
  bulkResume.textContent = bulkBusy === "resume" ? BULK.resuming : BULK.resumeAll;
  bulkPause.setAttribute("aria-label", bulkBusy === "pause" ? BULK.pausing : pauseAriaLabel(bar.pauseCount));
  bulkResume.setAttribute("aria-label", bulkBusy === "resume" ? BULK.resuming : resumeAriaLabel(bar.resumeCount));

  for (const btn of [bulkPause, bulkResume]) {
    if (bulkBusy) btn.setAttribute("aria-disabled", "true");
    else btn.removeAttribute("aria-disabled");
    btn.disabled = offline;
    if (offline) btn.title = OFFLINE_CONTROL_TITLE;
    else btn.removeAttribute("title");
  }

  if (pauseHadFocus && bulkPause.hidden && !bulkResume.hidden) bulkResume.focus();
  if (resumeHadFocus && bulkResume.hidden && !bulkPause.hidden) bulkPause.focus();
}

// ---------------------------------------------------------------------
// Updates available (WP UPD-6)
// ---------------------------------------------------------------------

/** `null`, `"all"`, or the appid whose "Update now" request is in flight.
 * One request at a time; every update button is aria-disabled meanwhile. */
let updatesInFlight = null;
/** Bumped per mount so a late `GET /v1/schedule` answer for an old mount
 * is dropped. */
let scheduleGen = 0;
let settingsGen = 0;

const sizeOfRow = (g) => formatBytesGB(g.size_bytes);

/** WP DOWNLOADS-2: the middle column (Updates available + Incomplete) is
 * left out while both lists are empty (no empty headings, user rule
 * 2026-10-14); the columns grid then has two columns (kit.css
 * `.cols3.no-attn`). */
function syncAttentionColumn() {
  if (!mounted() || !els.attnCol) return;
  const empty = els.updates.hidden && els.incomplete.hidden;
  els.attnCol.hidden = empty;
  els.cols.classList.toggle("no-attn", empty);
}

async function loadSchedule() {
  const my = ++scheduleGen;
  try {
    const sched = await api.schedule();
    if (my !== scheduleGen) return;
    state.schedule = sched;
  } catch {
    if (my !== scheduleGen) return;
    state.schedule = null; // unknown: no note rather than a guess
  }
  paintUpdates();
  paintIncomplete(); // WP CACHE-COMPLETE-1c: its schedule note too
  paintStats(); // WP RATE-TILE: the cap line reads the window
}

/** WP RATE-TILE: the upstream rate cap for the "Current speed" tile. */
async function loadRateCap() {
  const my = ++settingsGen;
  try {
    const settings = await api.getSettings();
    if (my !== settingsGen) return;
    state.settings = settings;
  } catch {
    if (my !== settingsGen) return;
    state.settings = null; // unknown: no line rather than a guess
  }
  paintStats();
}

/**
 * Queue updates for `appids` (`POST /v1/prefill`, see module header).
 * @param {number[]} appids
 * @param {"all"|number} which what the busy label goes on
 */
async function startUpdates(appids, which) {
  if (updatesInFlight !== null || !appids.length) return;
  updatesInFlight = which;
  paintUpdates();
  try {
    const refs = await api.prefill(appids);
    const outcome = summarizeUpdateStart(refs, state.games);
    showToast(outcome.message, { warn: outcome.warn });
    store.refreshNow();
  } catch (err) {
    const outcome = describeUpdateStartError(err);
    showToast(outcome.message, { warn: true });
    if (outcome.refresh) store.refreshNow();
  } finally {
    updatesInFlight = null;
    paintUpdates();
  }
}

function updateButton(label, variant, ariaLabel, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn sm" + (variant ? " " + variant : "");
  btn.textContent = label;
  btn.setAttribute("aria-label", ariaLabel);
  // Busy (paintUpdateBusy): aria-disabled plus this guard, not `disabled`
  // (docs/LEARNINGS.md, WP WEB-FEAT-1), so a focused button keeps its focus.
  btn.addEventListener("click", () => {
    if (btn.disabled || updatesInFlight !== null) return;
    onClick();
  });
  gateOffline(btn);
  return btn;
}

function buildUpdateRow(game, gamesByAppid) {
  const title = nameFor(game.appid, gamesByAppid);
  const btn = updateButton(ROW_UPDATE_LABEL, "w72", updateNowAriaLabel(title), () => startUpdates([game.appid], game.appid));
  btn.dataset.role = "update-now";
  // Not `data-appid`: patchNames rewrites the text of every such node.
  btn.dataset.updateAppid = String(game.appid);
  return createAttentionRow({ appid: game.appid, title, meta: sizeOfRow(game) || "—", button: btn, dataKey: "updateAppid" });
}

/** Which update button `btn` is: `"all"`, its row's appid, or null. Reads
 * the button's own data attributes (no `closest`, see buildUpdateRow). */
function updateButtonId(btn) {
  if (!btn || !btn.dataset) return null;
  if (btn.dataset.role === "update-all") return "all";
  if (btn.dataset.role === "update-now" && btn.dataset.updateAppid) return Number(btn.dataset.updateAppid);
  return null;
}

/** Which update button of this section has focus right now, or null. */
function focusedUpdateButton() {
  const el = document.activeElement;
  for (const btn of els.updates.querySelectorAll("button")) if (btn === el) return updateButtonId(btn);
  return null;
}

/** Label and busy state of the update buttons, patched in place (review
 * fix): an in-flight change alone must not rebuild the buttons, or the one
 * just pressed loses keyboard focus. */
function paintUpdateBusy() {
  const busy = updatesInFlight !== null;
  for (const btn of els.updates.querySelectorAll("button")) {
    const role = btn.dataset.role;
    if (role !== "update-all" && role !== "update-now") continue;
    const which = updateButtonId(btn);
    const idle = role === "update-all" ? UPDATE_ALL_LABEL : ROW_UPDATE_LABEL;
    const label = updatesInFlight === which ? STARTING_LABEL : idle;
    if (btn.textContent !== label) btn.textContent = label;
    if (busy) btn.setAttribute("aria-disabled", "true");
    else btn.removeAttribute("aria-disabled");
  }
}

/**
 * Sync the "Updates available" section with the games, jobs, schedule and
 * connection state. Rebuilds only when its content changes; the in-flight
 * state is patched in place (`paintUpdateBusy`). On a rebuild a focused
 * update button keeps focus on its replacement; when its row is gone focus
 * moves to "Update all", and when the whole section is gone to the Queue
 * heading (the started game is there now).
 */
function paintUpdates() {
  if (!mounted() || !els.updates) return;
  const gamesByAppid = gamesByAppidMap();
  const titleOf = (g) => nameFor(g.appid, gamesByAppid);
  const rows = state.gamesKnown ? pendingUpdates(state.games, state.jobs, titleOf) : [];
  const note = scheduleNote(state.schedule);
  const key = [updatesListKey(rows, titleOf, sizeOfRow), note ?? "", String(isConnectionLost())].join("\u0000");
  if (els.updatesKey === key) {
    paintUpdateBusy();
    return;
  }
  els.updatesKey = key;
  const hadFocus = focusedUpdateButton();

  const { updates, updatesCount, updatesNote, updatesActs, updatesBody } = els;
  updates.hidden = rows.length === 0;
  setBadge(updatesCount, rows.length);
  updatesNote.hidden = !note;
  updatesNote.textContent = note ?? "";
  updatesActs.replaceChildren();
  updatesBody.replaceChildren();
  if (rows.length) {
    const all = updateButton(UPDATE_ALL_LABEL, "", updateAllAriaLabel(rows.length), () =>
      startUpdates(
        rows.map((g) => g.appid),
        "all",
      ),
    );
    all.dataset.role = "update-all";
    updatesActs.appendChild(all);
    for (const g of rows) updatesBody.appendChild(buildUpdateRow(g, gamesByAppid));
  }
  paintUpdateBusy();
  syncAttentionColumn();

  if (hadFocus === null) return;
  const same =
    hadFocus === "all"
      ? updatesActs.querySelector('[data-role="update-all"]')
      : Array.from(updatesBody.querySelectorAll('[data-role="update-now"]')).find((b) => updateButtonId(b) === hadFocus);
  const target = same || updatesActs.querySelector('[data-role="update-all"]') || els.queueHeading;
  target.focus();
}

// ---------------------------------------------------------------------
// Incomplete (WP CACHE-COMPLETE-1c)
// ---------------------------------------------------------------------

/** `null`, `"all"`, or the appid whose "Complete now" request is in flight.
 * One request at a time; every button of the section is aria-disabled
 * meanwhile. */
let incompleteInFlight = null;

/**
 * Queue the completion: `POST /v1/prefill/verify` with one game ("Complete
 * now") or exactly the listed completable rows ("Complete all").
 * @param {number[]} appids
 * @param {"all"|number} which what the busy label goes on
 */
async function startCompletion(appids, which) {
  if (incompleteInFlight !== null || !appids.length) return;
  incompleteInFlight = which;
  paintIncomplete();
  try {
    const refs = await api.prefillVerify(appids);
    const outcome = summarizeVerifyStart(refs, "complete");
    showToast(outcome.message, { warn: outcome.warn });
    store.refreshNow();
  } catch (err) {
    const outcome = describeVerifyStartError(err);
    showToast(outcome.message, { warn: true });
    if (outcome.refresh) store.refreshNow();
  } finally {
    incompleteInFlight = null;
    paintIncomplete();
  }
}

function completionButton(label, variant, ariaLabel, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn sm" + (variant ? " " + variant : "");
  btn.textContent = label;
  btn.setAttribute("aria-label", ariaLabel);
  // Busy: aria-disabled plus this guard, not `disabled` (see updateButton).
  btn.addEventListener("click", () => {
    if (btn.disabled || incompleteInFlight !== null) return;
    onClick();
  });
  gateOffline(btn);
  return btn;
}

function buildIncompleteRow(game, gamesByAppid) {
  const title = nameFor(game.appid, gamesByAppid);
  let btn = null;
  if (isCompletable(game)) {
    btn = completionButton(ROW_VERIFY_LABEL, "w72", completeNowAriaLabel(title), () => startCompletion([game.appid], game.appid));
    btn.dataset.role = "complete-now";
    // Not `data-appid`: patchNames rewrites the text of every such node.
    btn.dataset.incompleteAppid = String(game.appid);
  }
  // Short only of wrong-size chunks: nothing a forced run fixes, so no
  // button and the row says why.
  return createAttentionRow({
    appid: game.appid,
    title,
    meta: `${incompletePctText(game)} on disk`,
    button: btn,
    note: btn ? "" : DAMAGED_NOTE,
    dataKey: "incompleteAppid",
  });
}

/** Which completion button `btn` is: `"all"`, its row's appid, or null. */
function completionButtonId(btn) {
  if (!btn || !btn.dataset) return null;
  if (btn.dataset.role === "complete-all") return "all";
  if (btn.dataset.role === "complete-now" && btn.dataset.incompleteAppid) return Number(btn.dataset.incompleteAppid);
  return null;
}

function focusedCompletionButton() {
  const el = document.activeElement;
  for (const btn of els.incomplete.querySelectorAll("button")) if (btn === el) return completionButtonId(btn);
  return null;
}

/** Label and busy state of the completion buttons, patched in place. */
function paintIncompleteBusy() {
  const busy = incompleteInFlight !== null;
  for (const btn of els.incomplete.querySelectorAll("button")) {
    if (btn.dataset.role !== "complete-all" && btn.dataset.role !== "complete-now") continue;
    const which = completionButtonId(btn);
    const idle = btn.dataset.role === "complete-all" ? VERIFY_ALL_LABEL : ROW_VERIFY_LABEL;
    const label = incompleteInFlight === which ? STARTING_LABEL : idle;
    if (btn.textContent !== label) btn.textContent = label;
    if (busy) btn.setAttribute("aria-disabled", "true");
    else btn.removeAttribute("aria-disabled");
  }
}

/**
 * Sync the "Incomplete" section with the games, jobs, schedule and
 * connection state — the same rebuild/focus rules as `paintUpdates`.
 */
function paintIncomplete() {
  if (!mounted() || !els.incomplete) return;
  const gamesByAppid = gamesByAppidMap();
  const titleOf = (g) => nameFor(g.appid, gamesByAppid);
  const rows = state.gamesKnown ? pendingIncomplete(state.games, state.jobs, titleOf) : [];
  const note = incompleteScheduleNote(state.schedule);
  const key = [incompleteListKey(rows, titleOf), note ?? "", String(isConnectionLost())].join("\u0000");
  if (els.incompleteKey === key) {
    paintIncompleteBusy();
    return;
  }
  els.incompleteKey = key;
  const hadFocus = focusedCompletionButton();

  const { incomplete, incompleteCount, incompleteNote, incompleteActs, incompleteBody } = els;
  incomplete.hidden = rows.length === 0;
  setBadge(incompleteCount, rows.length);
  incompleteNote.hidden = !note;
  incompleteNote.textContent = note ?? "";
  incompleteActs.replaceChildren();
  incompleteBody.replaceChildren();
  const completable = rows.filter(isCompletable).map((g) => g.appid);
  if (completable.length) {
    const all = completionButton(VERIFY_ALL_LABEL, "", verifyAllAriaLabel(completable.length), () =>
      startCompletion(completable, "all"),
    );
    all.dataset.role = "complete-all";
    incompleteActs.appendChild(all);
  }
  for (const g of rows) incompleteBody.appendChild(buildIncompleteRow(g, gamesByAppid));
  paintIncompleteBusy();
  syncAttentionColumn();

  if (hadFocus === null) return;
  const same =
    hadFocus === "all"
      ? incompleteActs.querySelector('[data-role="complete-all"]')
      : Array.from(incompleteBody.querySelectorAll('[data-role="complete-now"]')).find(
          (b) => completionButtonId(b) === hadFocus,
        );
  const target = same || incompleteActs.querySelector('[data-role="complete-all"]') || els.queueHeading;
  target.focus();
}

// ---------------------------------------------------------------------
// Job card (Active column; paused jobs fold in, WP DOWNLOADS-2)
// ---------------------------------------------------------------------

/** The slot-release divergence, stated on a paused card itself — see
 * js/lib/job-partition.js's module header for the full "why". */
const HOLD_NOTE =
  "Paused — this does not hold the worker slot. vault-api released it immediately, so another queued job may already be running. Resume puts this job back at the front of the queue.";

/** Rebuild ONLY the card's action row and stop note from `job`'s current
 * control fields — never touches the name or the status pill's icon, so
 * this is safe to call from the patch path (downloads-render-plan.js's
 * `patchStopRequest`) as well as from the initial build. */
function paintJobActions(card, job) {
  const acts = card.querySelector('[data-role="job-acts"]');
  const stopNote = card.querySelector('[data-role="stop-note"]');
  if (!acts || !stopNote) return;

  const cancelling = job.status === "running" && job.stop_request === "cancel";
  const pausing = job.status === "running" && job.stop_request === "pause";

  acts.replaceChildren();
  if (job.status === "paused") {
    acts.append(
      actionButton("Resume", "pri", () => onResume(job.id)),
      actionButton("Cancel", "ghost danger", () => onCancel(job.id)),
    );
  } else if (job.status === "running") {
    if (job.type === "prefill") {
      const pauseBtn = actionButton(pausing ? "Pausing…" : "Pause", "", () => onPause(job.id));
      pauseBtn.disabled = pauseBtn.disabled || pausing || cancelling;
      acts.appendChild(pauseBtn);
    }
    const cancelBtn = actionButton(cancelling ? "Cancelling…" : "Cancel", "ghost danger", () => onCancel(job.id));
    cancelBtn.disabled = cancelBtn.disabled || cancelling;
    acts.appendChild(cancelBtn);
  }

  if (cancelling) {
    stopNote.hidden = false;
    stopNote.textContent =
      "Cancel requested — the download is stopping. Bytes already fetched stay on the cache.";
  } else if (pausing) {
    stopNote.hidden = false;
    stopNote.textContent = "Pause requested — stopping. Resume re-runs from the cache, not from zero.";
  } else {
    stopNote.hidden = true;
    stopNote.textContent = "";
  }
}

/**
 * @param {object} job JobSummary (status is "running" or "paused" here)
 * @param {Map<number, object>} gamesByAppid
 */
function buildJobCard(job, gamesByAppid) {
  const game = gamesByAppid.get(job.appid);
  const held = job.status === "paused";
  const card = createJobCard({
    job,
    title: nameFor(job.appid, gamesByAppid),
    kind: activeJobKind(job, game),
    word: activeJobWord(job, game),
    held,
    holdNote: held ? HOLD_NOTE : "",
  });
  paintJobActions(card, job);
  return card;
}

// ---------------------------------------------------------------------
// Queue row
// ---------------------------------------------------------------------

function buildQueueRow(job, position, gamesByAppid, total = 1) {
  const title = nameFor(job.appid, gamesByAppid);
  const gripWrap = document.createElement("span");
  gripWrap.className = "grip";
  gripWrap.dataset.jid = String(job.id);
  gripWrap.appendChild(staticIcon(GRIP_SVG));

  const removeBtn = actionButton(ROW_REMOVE_LABEL, "ghost w72", () => onCancel(job.id));
  removeBtn.setAttribute("aria-label", `Remove ${title} from the queue`);
  removeBtn.dataset.role = "queue-remove";
  const row = createQueueRow({
    jobId: job.id,
    appid: job.appid,
    title,
    size: formatBytesGB(gamesByAppid.get(job.appid)?.size_bytes) || "",
    grip: gripWrap,
    removeButton: removeBtn,
  });

  // WP D20: the grip is the reorder handle (pointer drag, or focus + arrow
  // keys, QUEUE-KBD-KEEP). With one queued job, or while the connection is
  // lost, there is nothing to move: it stays a plain icon and is not
  // focusable.
  if (total > 1 && !isConnectionLost()) {
    gripWrap.className = "grip grip-live";
    gripWrap.setAttribute("role", "button");
    gripWrap.setAttribute("tabindex", "0");
    gripWrap.setAttribute(
      "aria-label",
      `Reorder ${title}, position ${position} of ${total}. Use the up and down arrow keys, Home or End.`,
    );
    gripWrap.addEventListener("keydown", (e) => {
      const target = keyTargetIndex(e.key, position - 1, total);
      if (target === null) return;
      e.preventDefault();
      void moveQueued(job.id, target, { refocus: true });
    });
    gripWrap.addEventListener("pointerdown", (e) => startQueueDrag(e, row, job.id));
  }
  return row;
}

// ---------------------------------------------------------------------
// Queue reorder (WP D20)
// ---------------------------------------------------------------------

/** While a drag or a move request is in flight a jobs poll must not repaint
 * the queue (it would snap the row back or drop the optimistic order).
 * `holds` counts those; a poll that arrives meanwhile sets `dirty` and the
 * repaint happens on release. */
const reorder = { holds: 0, dirty: false };

/** The job id whose reorder handle has keyboard focus, else null. */
function focusedGripJid() {
  const active = document.activeElement;
  if (!active || !active.classList || !active.classList.contains("grip-live")) return null;
  return active.dataset && active.dataset.jid ? Number(active.dataset.jid) : null;
}

function focusGrip(jobId) {
  if (!mounted()) return;
  const grip = els.queueBody.querySelector(`.grip[data-jid="${jobId}"]`);
  if (grip && typeof grip.focus === "function") grip.focus();
}

/**
 * Move a queued job to `toIndex` (0-based among the queued jobs). The list
 * is reordered at once (optimistic) and `POST /v1/jobs/{id}/move` is sent;
 * on failure the previous order is restored and the error shown. The
 * server's answer is authoritative, so a refresh follows either way.
 * @returns {Promise<boolean>} true when a request was sent and accepted
 */
async function moveQueued(jobId, toIndex, { refocus = false } = {}) {
  if (reorder.holds > 0) return false; // one move at a time
  const ids = partitionJobs(state.jobs).queued.map((j) => j.id);
  const next = reorderIds(ids, jobId, toIndex);
  if (next.every((id, i) => id === ids[i])) return false;

  const before = state.jobs;
  state.jobs = withQueuePositions(before, next);
  fullRender();
  if (refocus) focusGrip(jobId);

  reorder.holds += 1;
  let ok = false;
  try {
    await api.moveJob(jobId, next.indexOf(jobId));
    ok = true;
  } catch (err) {
    state.jobs = store.snapshot("jobs") || before;
    showToast(errorText(err), { warn: true });
  }
  reorder.holds -= 1;
  reorder.dirty = false;
  if (!ok) {
    fullRender();
    if (refocus) focusGrip(jobId);
  }
  store.refreshNow();
  return ok;
}

/** Pointer drag of a queue row by its grip. The row follows the pointer in
 * the DOM (moved between its siblings); on release the final index is sent
 * through {@link moveQueued}. Escape or a cancelled pointer puts it back. */
function startQueueDrag(e, row, jobId) {
  if (e.button !== undefined && e.button !== 0) return;
  if (reorder.holds > 0) return;
  e.preventDefault();
  reorder.holds += 1;
  row.classList.add("dragging");
  let index = Array.from(els.queueBody.querySelectorAll(".qrow")).indexOf(row);

  const others = () => Array.from(els.queueBody.querySelectorAll(".qrow")).filter((r) => r !== row);

  const onMove = (ev) => {
    const rows = others();
    const mids = rows.map((r) => {
      const box = r.getBoundingClientRect();
      return box.top + box.height / 2;
    });
    index = dropIndex(mids, ev.clientY);
    const anchor = rows[index] || null;
    if (anchor) els.queueBody.insertBefore(row, anchor);
    else els.queueBody.appendChild(row);
  };
  const finish = (commit) => {
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    document.removeEventListener("pointercancel", onCancelDrag);
    document.removeEventListener("keydown", onKey);
    row.classList.remove("dragging");
    reorder.holds -= 1;
    reorder.dirty = false;
    if (commit) {
      void moveQueued(jobId, index).then((sent) => {
        if (!sent) fullRender();
      });
    } else {
      fullRender();
    }
  };
  const onUp = () => finish(true);
  const onCancelDrag = () => finish(false);
  const onKey = (ev) => {
    if (ev.key === "Escape") finish(false);
  };
  document.addEventListener("pointermove", onMove);
  document.addEventListener("pointerup", onUp);
  document.addEventListener("pointercancel", onCancelDrag);
  document.addEventListener("keydown", onKey);
}

// ---------------------------------------------------------------------
// History row (lazy log-excerpt fetch on expand)
// ---------------------------------------------------------------------

/** Fill an item's hanging panel from its excerpt state
 * (components/history-list.js `paintHistoryPanel`). A failed prefill gets
 * its known-cause hint (`lib/job-failure.js`, WP WEB-FIX-4) or the generic
 * failure block (WP DOWNLOADS-1), both with the Retry offer: the button on
 * the newest job of an app only (an older failed row would queue a second
 * run of a game that already has a newer job), never for a Steam tool app
 * (`POST /v1/prefill` answers 422, WP API-FIX-4). */
function paintExcerpt(itemEl, jobId) {
  const panel = itemEl.querySelector(".hpanel");
  if (!panel) return;
  const st = getExcerptState(jobId);
  const display = selectExcerptDisplay(st);
  const job = findJobById(jobId);
  const ready = st.excerpt !== undefined;
  paintHistoryPanel(panel, {
    display,
    hint: ready && job ? jobFailureHint(job, st.excerpt) : null,
    failed: isFailedPrefill(job),
    retry: job
      ? { offer: retryOffer(job, state.jobs, gamesByAppidMap().get(job.appid)), make: () => retryButton(job) }
      : { offer: "newer", make: null },
    rawOpen: st.rawOpen,
    onRawToggle: (open) => {
      st.rawOpen = open;
    },
  });
}

/** The failure block's Retry button (WP WEB-FIX-4). */
function retryButton(job) {
  const retry = actionButton("Retry", "pri", () => onRetry(job.appid));
  retry.dataset.retryAppid = String(job.appid); // patchNames keeps the label current
  retry.setAttribute("aria-label", `Retry ${nameFor(job.appid, gamesByAppidMap())}`);
  return retry;
}

function historyRowNow(jobId) {
  return mounted() ? els.historyBody.querySelector(`.hitem[data-jid="${jobId}"]`) : null;
}

/** Lazily fetch `GET /v1/jobs/{id}` for its `log_excerpt`, exactly once per
 * job (guarded by `st.excerpt === undefined` — "never fetched", distinct
 * from a job that genuinely produced no output). Shared by
 * `toggleHistoryRow` (an operator expanding a row by hand) and
 * `highlightJob` below (a notification jumping here with the row
 * pre-expanded) so the two paths cannot drift on the fetch/error/re-paint
 * bookkeeping. */
async function ensureExcerptLoaded(jobId, row) {
  const st = getExcerptState(jobId);
  if (st.excerpt !== undefined || st.loading) return;
  st.loading = true;
  if (row) paintExcerpt(row, jobId);
  try {
    const detail = await api.job(jobId);
    st.excerpt = detail && typeof detail.log_excerpt === "string" ? detail.log_excerpt : "";
    st.error = null;
  } catch (err) {
    st.error = errorText(err);
  } finally {
    st.loading = false;
    // The row may have been rebuilt (a full jobs-tick rebuild) while this
    // fetch was in flight — re-look-up the live element rather than
    // trusting the captured reference.
    const liveRow = historyRowNow(jobId);
    if (liveRow) paintExcerpt(liveRow, jobId);
  }
}

async function toggleHistoryRow(jobId) {
  const st = getExcerptState(jobId);
  st.expanded = !st.expanded;
  const row = historyRowNow(jobId);
  if (row) {
    setHistoryItemExpanded(row, st.expanded);
    paintExcerpt(row, jobId);
  }
  if (st.expanded) await ensureExcerptLoaded(jobId, row);
}

// ---------------------------------------------------------------------
// Cross-view navigation target (WP 4a.7). The bell panel
// (components/notifications.js) asks this view to land on and expand one
// job's history row without reaching into this module beyond this one
// exported function — the router/app-shell-level hook the WP 4a.7 brief
// asks for, instead of a library.js-style internal reach-in.
// ---------------------------------------------------------------------

/** Job id queued for highlighting before this view was (re-)mounted (the
 * caller navigates here first — app.js's router — and this view may not
 * have built its section yet at the moment `highlightJob` is called).
 * Applied by the next `fullRender()`, then cleared — one-shot. */
let pendingHighlightJobId = null;

/**
 * Expand (never collapse) job `jobId`'s history row and scroll it into
 * view — the "job events -> Downloads with the job highlighted"
 * destination for both a finished and a failed job's notification (see
 * `lib/notification-log.js`'s `navigationTargetFor`). Safe to call
 * regardless of whether Downloads is currently mounted or whether the job
 * has reached the history bucket in `state.jobs` yet.
 *
 * Unlike the mockup's `openNote()` (docs/design/vault-app-mockup-NOTES.md
 * round 6), this uses `scrollIntoView()` directly rather than manually
 * walking a scroll container: the mockup's own note warns that
 * `scrollIntoView()` also scrolls an `overflow:hidden` app shell and shifts
 * every absolutely positioned surface with it — but this app's shell has no
 * such ancestor (`view-root`/`.app` are plain document flow, verified
 * against css/app.css and css/kit.css: no `overflow:hidden` above `.hitem`), so the mockup's
 * specific failure mode does not apply here.
 *
 * **`behavior` is resolved per `prefers-reduced-motion` explicitly (WP
 * 4a.8 review fix), not left to css/theme.css's `scroll-behavior:auto
 * !important` block.** Per the CSSOM-View spec, an explicit `behavior`
 * passed to `scrollIntoView()`/`scrollTo()` OVERRIDES the element's CSS
 * `scroll-behavior` — the reduced-motion block only governs scrolls that
 * fall back to CSS (a bare `scrollIntoView()`/anchor-jump with no options
 * object), so a literal `"smooth"` here escapes that block entirely and
 * animates regardless of the user's preference. There is no `matchMedia`
 * call anywhere else in `web/js/`; this is the first.
 */
export function highlightJob(jobId) {
  if (jobId == null) return;
  getExcerptState(jobId).expanded = true;
  pendingHighlightJobId = jobId;
  // WP DOWNLOADS-3: a row beyond the first History page is paged in by
  // renderHistory (which ends in applyPendingHighlight).
  if (mounted()) renderHistory();
  else applyPendingHighlight();
}

function applyPendingHighlight() {
  if (pendingHighlightJobId == null) return;
  const jobId = pendingHighlightJobId;
  const row = historyRowNow(jobId);
  if (!row) return; // job not in the History section on this render yet — stays queued
  pendingHighlightJobId = null;
  setHistoryItemExpanded(row, true);
  const toggle = row.querySelector(".hrow");
  paintExcerpt(row, jobId);
  ensureExcerptLoaded(jobId, row);
  row.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "center" });
  if (toggle) toggle.focus();
}

function buildHistoryRow(job, gamesByAppid) {
  const st = getExcerptState(job.id);
  const item = createHistoryItem({
    job,
    title: nameFor(job.appid, gamesByAppid),
    kind: jobIconKind(job), // "cached" | "error" | "cancelled" | "notinuse"
    word: jobStatusWord(job),
    meta: `job #${job.id} · ${jobStatusWord(job)} · ${formatTimestamp(job.finished_at)}`,
    expanded: st.expanded,
    onToggle: () => toggleHistoryRow(job.id),
  });
  paintExcerpt(item, job.id);
  return item;
}

// ---------------------------------------------------------------------
// Section rendering
// ---------------------------------------------------------------------

/** The kit's dashed empty box (kit.css `.empty`). */
function emptyMessage(text) {
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = text;
  return p;
}
function hintMessage(text) {
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = text;
  return p;
}

/** WP DOWNLOADS-3: the idle count is the History list's own set for the
 * selected range (`historyRows()`), not the store's 20-job poll. */
function subtitleText(p) {
  const bits = [];
  if (p.running.length) bits.push(`${p.running.length} running`);
  if (p.paused.length) bits.push(`${p.paused.length} paused`);
  if (p.queued.length) bits.push(`${p.queued.length} queued`);
  if (!bits.length) {
    const capped = isHistoryCapped(history.jobs.length);
    const count = historyRows().length;
    return idleSubtitle({ loaded: history.loaded, count: capped ? Math.min(count, HISTORY_LIMIT) : count, capped });
  }
  return bits.join(" · ");
}

function paintSubtitle() {
  if (mounted()) els.sub.textContent = subtitleText(partitionJobs(state.jobs));
}

/** A section-head count badge; hidden at zero. */
function setBadge(badge, n) {
  badge.textContent = String(n);
  badge.hidden = n === 0;
}

function fullRender() {
  if (!mounted()) return;
  const p = partitionJobs(state.jobs);
  const gamesByAppid = gamesByAppidMap();

  els.sub.textContent = subtitleText(p);
  paintBulkBar();

  // WP DOWNLOADS-2: paused jobs fold into Active, after the running one(s).
  const active = [...p.running, ...p.paused];
  setBadge(els.activeCount, active.length);
  els.activeBody.replaceChildren();
  if (!active.length) {
    els.activeBody.appendChild(emptyMessage("No download running. Start one from the Library."));
  } else {
    for (const job of active) els.activeBody.appendChild(buildJobCard(job, gamesByAppid));
  }

  setBadge(els.queueCount, p.queued.length);
  // WP D20: a rebuild must not drop keyboard focus from a reorder handle.
  const focusedJid = focusedGripJid();
  els.queueBody.replaceChildren();
  if (!p.queued.length) {
    els.queueBody.appendChild(emptyMessage("Nothing waiting."));
    els.queueHint.textContent = "";
    els.queueHint.hidden = true;
  } else {
    for (const job of p.queued) {
      els.queueBody.appendChild(buildQueueRow(job, queuePosition(p.queued, job.id), gamesByAppid, p.queued.length));
    }
    els.queueHint.hidden = false;
    els.queueHint.textContent =
      "vault-api runs one job at a time, top of the list first. Drag a row by its handle, or focus the handle and use the arrow keys, to change the order." +
      (p.paused.length ? " A paused job does not hold this queue back — the queue keeps draining." : "");
  }

  if (focusedJid !== null) focusGrip(focusedJid);

  maybeLoadOwnedNames();

  renderHistory(gamesByAppid);
  paintJobSpeeds();
}

/** WP D15c: the History list for the chosen range — the store's newest jobs
 * merged with the `?range=` fetch, filtered by the server's own rule. */
function historyRows() {
  return partitionJobs(filterJobsByRange(mergeJobLists(state.jobs, history.jobs), history.range)).history;
}

/** Identity of the rows on screen; a history fetch that changes nothing
 * visible does not rebuild them. */
function historyKey(rows) {
  return rows.map((j) => `${j.id}:${j.status}`).join(",");
}

/** WP DOWNLOADS-3: the "Show N more" button, or the capped note, after the
 * rows on screen; `null` when every row is shown and nothing is cut off. */
function historyFooter(page) {
  if (page.next > 0) {
    const btn = el("button", "btn sm ghost hmore", showMoreText(page.next));
    btn.type = "button";
    btn.dataset.role = "history-more";
    btn.setAttribute("aria-label", showMoreLabel(page.next, page.remaining));
    btn.addEventListener("click", () => showMoreHistory());
    return btn;
  }
  if (page.capped) {
    const note = hintMessage(HISTORY_CAPPED_TEXT);
    note.dataset.role = "history-capped";
    return note;
  }
  return null;
}

function renderHistory(gamesByAppid = gamesByAppidMap()) {
  if (!mounted()) return;
  const finished = historyRows();
  els.historyKey = historyKey(finished);
  els.historyBody.replaceChildren();
  if (!finished.length) {
    els.historyBody.appendChild(emptyMessage(emptyHistoryText(history.range)));
  } else {
    // WP DOWNLOADS-3: the first page, then 20 more per click. An expanded
    // row keeps its state across the rebuild (`getExcerptState`). A row a
    // deep link asked to open (`pendingHighlightJobId`) is paged in first.
    const wanted = pendingHighlightJobId == null ? -1 : finished.findIndex((j) => j.id === pendingHighlightJobId);
    while (wanted >= history.shown) history.shown = nextShown(history.shown);
    const page = historyPage(finished.length, history.shown, { capped: isHistoryCapped(history.jobs.length) });
    for (const job of finished.slice(0, page.visible)) els.historyBody.appendChild(buildHistoryRow(job, gamesByAppid));
    const footer = historyFooter(page);
    if (footer) els.historyBody.appendChild(footer);
  }
  paintSubtitle();
  applyPendingHighlight();
}

/** Append the next 20 rows; focus moves to the first new row, so keyboard
 * users continue where the list grew (the button is rebuilt). */
function showMoreHistory() {
  if (!mounted()) return;
  const before = history.shown;
  history.shown = nextShown(history.shown);
  renderHistory();
  const rows = els.historyBody.querySelectorAll(".hitem .hrow");
  const first = rows[before];
  if (first) first.focus();
}

async function loadHistory() {
  const gen = ++history.gen;
  const range = history.range;
  let items = [];
  try {
    const answer = await api.jobs(HISTORY_LIMIT, { range });
    items = Array.isArray(answer) ? answer : [];
  } catch {
    // The store's newest jobs still show; the filter applies to them.
    items = [];
  }
  if (gen !== history.gen) return; // a newer range was chosen meanwhile
  history.jobs = items;
  const firstAnswer = !history.loaded;
  history.loaded = true;
  if (!mounted()) return;
  if (historyKey(historyRows()) !== els.historyKey) renderHistory();
  else if (firstAnswer) paintSubtitle();
}

function setHistoryRange(range) {
  if (!isRange(range) || range === history.range) return;
  history.range = range;
  history.jobs = [];
  history.shown = HISTORY_FIRST_PAGE;
  history.loaded = false;
  renderHistory();
  loadHistory();
}

// ---------------------------------------------------------------------
// Delete history (WP D15-NAMES) — built once at module load as a
// `document.body` sibling of `#app`, like the Pause all confirm above.
// ---------------------------------------------------------------------

const histBackdrop = document.createElement("div");
histBackdrop.className = "dialog-backdrop";
const histDialog = document.createElement("div");
histDialog.className = "dialog hist-delete-dialog";
histDialog.setAttribute("role", "alertdialog");
histDialog.setAttribute("aria-modal", "true");
histDialog.setAttribute("aria-labelledby", "hist-delete-title");
histDialog.setAttribute("aria-describedby", "hist-delete-what hist-delete-keeps");
histDialog.dataset.role = "history-delete-confirm";
const histTitle = document.createElement("h3");
histTitle.id = "hist-delete-title";
histTitle.textContent = HISTORY_DELETE_TITLE;
const histWhat = document.createElement("p");
histWhat.id = "hist-delete-what";
histWhat.textContent = HISTORY_DELETE_WHAT;
const histScope = document.createElement("fieldset");
histScope.className = "hist-scope";
const histLegend = document.createElement("legend");
histLegend.textContent = "What to delete";
histScope.appendChild(histLegend);
const histRadios = HISTORY_DELETE_OPTIONS.map((option) => {
  const row = document.createElement("label");
  row.className = "srow";
  const radio = document.createElement("input");
  radio.type = "radio";
  radio.name = "history-delete-scope";
  radio.value = option.value;
  radio.id = `hist-scope-${option.value}`;
  radio.dataset.role = "history-scope";
  radio.addEventListener("change", () => paintHistDeleteButton());
  const grow = document.createElement("span");
  grow.className = "grow";
  const ttl = document.createElement("span");
  ttl.className = "ttl";
  ttl.textContent = option.label;
  const desc = document.createElement("span");
  desc.className = "desc";
  desc.textContent = option.desc;
  grow.append(ttl, desc);
  row.append(radio, grow);
  histScope.appendChild(row);
  return radio;
});
const histKeeps = document.createElement("p");
histKeeps.id = "hist-delete-keeps";
histKeeps.textContent = HISTORY_DELETE_KEEPS;
const histError = document.createElement("p");
histError.className = "errline";
histError.dataset.role = "history-delete-error";
histError.setAttribute("role", "alert");
histError.hidden = true;
const histRow = document.createElement("div");
histRow.className = "row";
const histNo = document.createElement("button");
histNo.type = "button";
histNo.className = "btn ghost sm";
histNo.textContent = "Keep";
histNo.dataset.role = "history-delete-no";
const histYes = document.createElement("button");
histYes.type = "button";
histYes.className = "btn danger sm";
histYes.dataset.role = "history-delete-yes";
histRow.append(histNo, histYes);
histDialog.append(histTitle, histWhat, histScope, histKeeps, histError, histRow);
histBackdrop.appendChild(histDialog);
document.body.appendChild(histBackdrop);

const histFlow = { open: false, busy: false, invokerEl: null };

function chosenHistoryScope() {
  const checked = histRadios.find((r) => r.checked);
  return checked && isHistoryScope(checked.value) ? checked.value : SCOPE_OLDER;
}

function paintHistDeleteButton() {
  histYes.textContent = histFlow.busy ? "Deleting…" : historyDeleteButtonText(chosenHistoryScope());
}

function setHistBusy(busy) {
  histFlow.busy = busy;
  // aria-disabled + click guards, not `disabled` (LEARNINGS, WP WEB-FEAT-1).
  for (const b of [histNo, histYes]) {
    if (busy) b.setAttribute("aria-disabled", "true");
    else b.removeAttribute("aria-disabled");
  }
  paintHistDeleteButton();
}

function setHistError(message) {
  histError.textContent = message || "";
  histError.hidden = !message;
}

function openHistoryDelete() {
  if (histFlow.open || histFlow.busy) return;
  for (const radio of histRadios) radio.checked = radio.value === SCOPE_OLDER;
  setHistError(null);
  setHistBusy(false);
  histFlow.invokerEl = document.activeElement;
  histFlow.open = true;
  histBackdrop.classList.add("on");
  pushModal(histBackdrop, () => {
    if (!histFlow.busy) closeHistoryDelete();
  });
  histNo.focus(); // the non-destructive default gets initial focus
}

function closeHistoryDelete({ restoreFocus = true } = {}) {
  if (!histFlow.open) return;
  histFlow.open = false;
  histBackdrop.classList.remove("on");
  popModal(histBackdrop);
  const invoker = histFlow.invokerEl;
  histFlow.invokerEl = null;
  if (!restoreFocus) return;
  const live = mounted() ? els.historyDeleteBtn : null;
  const target = live || invoker;
  if (target && typeof target.focus === "function" && target.isConnected !== false) target.focus();
}

async function confirmHistoryDelete() {
  if (histFlow.busy || !histFlow.open) return;
  const scope = chosenHistoryScope();
  setHistError(null);
  setHistBusy(true);
  let result;
  try {
    result = await api.deleteHistory(scope);
  } catch (err) {
    setHistBusy(false);
    setHistError(historyDeleteErrorText(err));
    return;
  }
  setHistBusy(false);
  closeHistoryDelete();
  showToast(historyDeletedText(result));
  history.jobs = [];
  history.loaded = false; // no subline count from the store's 20-job poll meanwhile
  if (mounted()) renderHistory();
  loadHistory();
  trafficPoll.refresh();
  store.refreshNow();
}

histNo.addEventListener("click", () => {
  if (!histFlow.busy) closeHistoryDelete();
});
histYes.addEventListener("click", () => confirmHistoryDelete());

// ---------------------------------------------------------------------
// Statistics: the hero (WP D15c, GRAPH-LIVE, LIVE-DELAY-1; DOWNLOADS-2)
// ---------------------------------------------------------------------

/** Cached persisted 1 h summary of the live view (refreshed every 30 s). */
const liveSummary = { at: 0, value: null };

/** One live tick: the live series every time, the persisted summary rarely. */
async function fetchLive() {
  const now = Date.now();
  const stale = !liveSummary.value || now - liveSummary.at >= LIVE_SUMMARY_MS;
  const [live, summary] = await Promise.all([api.throughputLive(), stale ? api.throughput(LIVE_SUMMARY_RANGE) : null]);
  if (summary) {
    liveSummary.value = summary;
    liveSummary.at = now;
  }
  return { range: "live", throughput: mergeLive(liveSummary.value, live), series: liveSeries(live) };
}

const trafficPoll = createViewPoll({
  fetcher: async () => {
    const range = traffic.range;
    if (range === "live") return fetchLive();
    const [throughput, series] = await Promise.all([api.throughput(range), api.throughputSeries(range)]);
    return { range, throughput, series };
  },
  // WP GRAPH-LIVE: fast only in the live view; the loop already parks
  // while the tab is hidden.
  intervalMs: () => (traffic.range === "live" ? LIVE_POLL_MS : VIEW_POLL_MS),
  onTick: (answer) => {
    // An answer for a range the user has since left: dropped, the refresh
    // the range change asked for is queued behind it.
    if (!answer || answer.range !== traffic.range) return;
    traffic.throughput = answer.throughput;
    traffic.series = answer.series;
    traffic.error = null;
    traffic.unsupported = false;
    traffic.noFigures = headerTiles(answer.throughput) === null;
    paintStats();
    paintJobSpeeds();
  },
  onError: (err) => {
    if (err && err.kind === ERROR_KINDS.NOT_FOUND) {
      // A server older than D15b: polling again cannot change that answer
      // (an upgrade restarts vault-api; the next visit to this view asks
      // again), so the poll stops here.
      traffic.unsupported = true;
      traffic.noFigures = true;
      trafficPoll.stop();
    } else {
      traffic.error = errorText(err);
    }
    paintStats();
  },
});

function setStatsRange(range) {
  if (!isStatsRange(range) || range === traffic.range) return;
  traffic.range = range;
  writeStoredStatsRange(range);
  traffic.throughput = null;
  traffic.series = null;
  traffic.error = null;
  liveSummary.value = null;
  paintStats();
  paintJobSpeeds();
  trafficPoll.refresh();
}

/** Repaint the hero from `traffic` (components/downloads-hero.js). Nothing
 * in it but the range buttons is focusable, so a repaint takes no focus. */
function paintStats() {
  if (!mounted() || !els.hero) return;
  const cap = state.settings ? rateCapLine(state.settings, state.schedule, Date.now()) : null;
  els.hero.paint({
    throughput: traffic.throughput,
    series: traffic.series,
    range: traffic.range,
    rateCap: cap,
    unsupported: traffic.unsupported,
    error: traffic.error,
    noFigures: traffic.noFigures,
  });
}

/** Patch every running job card's speed line (text only). */
function paintJobSpeeds() {
  if (!mounted()) return;
  const t = traffic.throughput;
  const state = feedState(t);
  const entries = new Map(((t && t.jobs) || []).map((j) => [j.job_id, j]));
  for (const card of els.activeBody.querySelectorAll(".job")) {
    const line = card.querySelector('[data-role="job-speed"]');
    if (!line) continue;
    // WP DOWNLOADS-1: a Verifying card says so, with or without statistics.
    const fresh = t && t.range === traffic.range;
    const text = jobSpeedText(fresh ? entries.get(Number(card.dataset.jid)) : undefined, fresh ? state : "disabled", {
      verifying: card.dataset.dk === KIND.VERIFY,
    });
    line.textContent = text || "";
    line.hidden = !text;
    line.title = text ? JOB_SPEED_NOTE : "";
  }
}

/** Patch just the named jobs' action row and stop note (their
 * `stop_request` changed, their `status` did not — downloads-render-plan.js's
 * verdict). Never touches a pill's status-icon node. */
function patchStopRequests(jobIds) {
  for (const jobId of jobIds) {
    const job = state.jobs.find((j) => j.id === jobId);
    const card = els.activeBody.querySelector(`.job[data-jid="${jobId}"]`);
    if (job && card) paintJobActions(card, job);
  }
  // A stop_request change moves a running job in or out of Pause all's set.
  paintBulkBar();
}

/** Update just the name text on every visible row for this appid, without
 * touching any status-icon subtree — used for the `GET /v1/games` poll
 * (15s cadence, independent of the jobs poll), which must never force a
 * Downloads section rebuild of its own. */
function patchNames() {
  if (!mounted()) return;
  const gamesByAppid = gamesByAppidMap();
  for (const nm of els.section.querySelectorAll("[data-appid]")) {
    const appid = Number(nm.dataset.appid);
    const fresh = nameFor(appid, gamesByAppid);
    if (nm.textContent !== fresh) nm.textContent = fresh;
  }
  // WP DOWNLOADS-2: queue rows show the game's size, which arrives with
  // the games poll (often after the first jobs paint).
  for (const sz of els.section.querySelectorAll("[data-size-appid]")) {
    const fresh = formatBytesGB(gamesByAppid.get(Number(sz.dataset.sizeAppid))?.size_bytes) || "";
    if (sz.textContent !== fresh) sz.textContent = fresh;
  }
  // The failure hints' Retry buttons name the game too (aria-label).
  for (const btn of els.section.querySelectorAll("button[data-retry-appid]")) {
    const label = `Retry ${nameFor(Number(btn.dataset.retryAppid), gamesByAppid)}`;
    if (btn.getAttribute("aria-label") !== label) btn.setAttribute("aria-label", label);
  }
}

// ---------------------------------------------------------------------
// Static DOM construction
// ---------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** A kit section head: the uppercase label (an h2 for the outline) on the
 * left, `right` (count badge, buttons) on the right. */
function sectionHead(label, ...right) {
  const head = el("div", "sechd");
  const h2 = el("h2", "sec", label);
  const acts = el("div", "hacts");
  acts.append(...right);
  head.append(h2, acts);
  return { head, h2, acts };
}

function countBadge(role) {
  const badge = el("span", "badge");
  badge.dataset.role = role;
  badge.hidden = true;
  return badge;
}

function block(className, ...children) {
  const blk = el("div", className ? `blk ${className}` : "blk");
  blk.append(...children);
  return blk;
}

function buildSection() {
  const section = el("section", "view view-downloads kit dl");

  // Page head: title + subline left, Pause all / Resume all right.
  const head = el("div", "page-head");
  const headText = el("div");
  const h1 = el("h1", null, "Downloads");
  const sub = el("div", "sub");
  sub.dataset.role = "dl-sub";
  headText.append(h1, sub);

  // WP WEB-FEAT-5: Pause all / Resume all + the scheduler note.
  const bulkBar = el("div", "dl-bulk");
  bulkBar.setAttribute("role", "group");
  bulkBar.setAttribute("aria-label", BULK.groupLabel);
  bulkBar.hidden = true;
  const bulkActs = el("div", "dl-bulk-acts");
  const bulkPause = bulkButton(BULK.pauseAll, "", openPauseConfirm);
  bulkPause.dataset.role = "bulk-pause";
  const bulkResume = bulkButton(BULK.resumeAll, "pri", () => runBulk("resume"));
  bulkResume.dataset.role = "bulk-resume";
  bulkActs.append(bulkPause, bulkResume);
  const bulkNote = el("p", "hint dl-bulk-note", BULK.schedulerNote);
  bulkBar.append(bulkActs, bulkNote);
  head.append(headText, bulkBar);

  // The hero (WP D15c statistics, DOWNLOADS-2 anatomy).
  const hero = createDownloadsHero({ range: traffic.range, onRangeChange: setStatsRange });

  // Column 1: Active (running + paused) and Queue.
  const activeCount = countBadge("active-count");
  const activeHead = sectionHead("Active", activeCount);
  const activeBody = el("div", "rowlist");
  activeBody.dataset.role = "active-list";

  const queueCount = countBadge("queue-count");
  const queueHead = sectionHead("Queue", queueCount);
  queueHead.h2.tabIndex = -1; // WP UPD-6: focus anchor when the updates section empties
  const queueBody = el("div", "rowlist");
  queueBody.dataset.role = "queue-list";
  const queueHint = hintMessage("");
  queueHint.hidden = true;
  const col1 = el("div", "col");
  col1.append(block("dl-active", activeHead.head, activeBody), block("dl-queue", queueHead.head, queueBody, queueHint));

  // Column 2: Updates available and Incomplete (WP UPD-6, CACHE-COMPLETE-1c),
  // each hidden until it has a game; the column is left out when both are.
  const updatesCount = countBadge("updates-count");
  const updatesActs = el("div", "dl-updates-acts");
  const updatesHead = sectionHead(UPDATES_HEADING, updatesCount, updatesActs);
  updatesHead.h2.id = "dl-updates-heading";
  const updatesBody = el("div", "rowlist dl-updates-list");
  updatesBody.id = "dl-updates-list";
  const updatesNote = hintMessage("");
  updatesNote.classList.add("dl-updates-note");
  const updates = block("dl-updates", updatesHead.head, updatesBody, updatesNote);
  updates.hidden = true;
  updates.setAttribute("role", "group");
  updates.setAttribute("aria-labelledby", "dl-updates-heading");

  const incompleteCount = countBadge("incomplete-count");
  const incompleteActs = el("div", "dl-updates-acts");
  const incompleteHead = sectionHead(INCOMPLETE_HEADING, incompleteCount, incompleteActs);
  incompleteHead.h2.id = "dl-incomplete-heading";
  const incompleteBody = el("div", "rowlist dl-updates-list");
  incompleteBody.id = "dl-incomplete-list";
  const incompleteNote = hintMessage("");
  incompleteNote.classList.add("dl-updates-note");
  const incomplete = block("dl-incomplete", incompleteHead.head, incompleteBody, incompleteNote);
  incomplete.hidden = true;
  incomplete.setAttribute("role", "group");
  incomplete.setAttribute("aria-labelledby", "dl-incomplete-heading");

  const attnCol = el("div", "col dl-col-attn");
  attnCol.hidden = true;
  attnCol.append(updates, incomplete);

  // Column 3: History with its own range filter (WP D15c, default All) and
  // "Delete…" (WP D15-NAMES) in one head row.
  const historyRange = createRangeSegs({
    label: "History range",
    value: history.range,
    onChange: setHistoryRange,
    role: "history-range",
    className: "seg rangesegs",
  });
  const historyDeleteBtn = el("button", "btn sm ghost", "Delete…");
  historyDeleteBtn.type = "button";
  historyDeleteBtn.dataset.role = "history-delete";
  historyDeleteBtn.setAttribute("aria-label", "Delete history…");
  historyDeleteBtn.addEventListener("click", () => openHistoryDelete());
  const historyHead = sectionHead("History", historyRange.el, historyDeleteBtn);
  const historyBody = el("div", "rowlist");
  historyBody.dataset.role = "history-list";
  const col3 = el("div", "col dl-col-history");
  col3.append(block("dl-history", historyHead.head, historyBody));

  const cols = el("div", "cols3 no-attn");
  cols.append(col1, attnCol, col3);

  section.append(head, hero.el, cols);

  els = {
    section,
    sub,
    bulkBar,
    bulkPause,
    bulkResume,
    hero,
    cols,
    activeCount,
    activeBody,
    queueCount,
    queueBody,
    queueHint,
    historyBody,
    historyDeleteBtn,
    attnCol,
    updates,
    updatesCount,
    updatesNote,
    updatesActs,
    updatesBody,
    updatesKey: null,
    incomplete,
    incompleteCount,
    incompleteNote,
    incompleteActs,
    incompleteBody,
    incompleteKey: null,
    queueHeading: queueHead.h2,
  };
  return section;
}

// ---------------------------------------------------------------------
// Store subscriptions — set up ONCE at module load (views are re-created
// on every navigation with no unmount hook; see library.js's identical
// reasoning), never per mount.
// ---------------------------------------------------------------------

store.subscribe("jobs", ({ items, diff }) => {
  if (!Array.isArray(items)) return; // {error} payload — nothing to render
  state.jobs = items;
  updateNavPip(items); // unconditional: the pip lives in the nav, not this view

  if (!mounted()) return;
  const plan = planJobsUpdate(diff);
  if (plan.full && reorder.holds > 0) {
    // WP D20: a drag or a move request holds the queue; the release repaints.
    reorder.dirty = true;
  } else if (plan.full) {
    fullRender();
  } else if (plan.patchStopRequest.length) {
    patchStopRequests(plan.patchStopRequest);
  }
  // WP UPD-6: a new job takes its game out of the list, without waiting for
  // a games poll (pinned in downloads-updates-wiring.test.js).
  paintUpdates();
  paintIncomplete(); // WP CACHE-COMPLETE-1c: same rule
});

/** WP WEB-FEAT-6: true when a job card's badge kind no longer matches its
 * games row — the first games answer arriving after the first jobs paint
 * (Downloading -> Updating), or `needs_force` changing mid-run. The badge
 * kind is structural (its icon), so the caller rebuilds; a games poll that
 * changes nothing here stays a name-only patch. */
function jobCardKindsStale() {
  if (!mounted()) return false;
  const gamesByAppid = new Map(state.games.map((g) => [g.appid, g]));
  for (const card of els.section.querySelectorAll(".job")) {
    const job = state.jobs.find((j) => j.id === Number(card.dataset.jid));
    if (job && card.dataset.dk !== activeJobKind(job, gamesByAppid.get(job.appid))) return true;
  }
  return false;
}

store.subscribe("games", ({ items }) => {
  if (!Array.isArray(items)) return;
  state.games = items;
  state.gamesKnown = true;
  if (mounted()) maybeLoadOwnedNames();
  if (jobCardKindsStale()) fullRender();
  else patchNames();
  paintUpdates(); // WP UPD-6: a flag set or cleared by this poll
  paintIncomplete(); // WP CACHE-COMPLETE-1c: a figure measured by this poll
});

// WP WEB-FIX-4: an owned list that lands (from the Library or from this
// view's one load) only changes names: patch them, no rebuild.
ownedLibrary.subscribe(() => {
  patchNames();
  paintUpdates(); // WP UPD-6: names feed the sort order and aria-labels
  paintIncomplete(); // WP CACHE-COMPLETE-1c: same
});

// WP WEB-FIX-2: repaint the job controls on a connection transition. A full
// rebuild, not a patch: transitions are rare, and every button's disabled
// state (including Pausing…/Cancelling…) is re-derived from scratch.
onConnectionChange(() => {
  fullRender();
  paintUpdates(); // WP UPD-6: the offline gate
  paintIncomplete(); // WP CACHE-COMPLETE-1c: same
});

onViewChange((view) => {
  if (view === "downloads") return;
  // The history confirm belongs to this page; an in-flight delete still
  // finishes and refreshes the store.
  closeHistoryDelete({ restoreFocus: false });
  sectionEl = null;
  trafficPoll.stop();
});

// Paint the pip immediately from whatever snapshot already exists (e.g.
// the Library view already polled jobs before the user ever opened
// Downloads) rather than waiting for this module's first live tick.
updateNavPip(state.jobs);

export function renderDownloads() {
  const section = buildSection();
  sectionEl = section;
  // WP DOWNLOADS-3: every visit starts at the first History page.
  history.shown = HISTORY_FIRST_PAGE;
  fullRender();
  paintUpdates(); // WP UPD-6
  paintIncomplete(); // WP CACHE-COMPLETE-1c
  loadSchedule(); // WP UPD-6: the section's schedule note
  loadRateCap(); // WP RATE-TILE
  paintStats();
  // Same gate as the store's loops (store-singleton.js): no key and no
  // demo mode means the onboarding overlay is up.
  if (getStoredApiKey() || isDemoMode()) {
    trafficPoll.start();
    trafficPoll.refresh();
    loadHistory();
  }
  return section;
}
