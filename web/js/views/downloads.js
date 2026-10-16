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
 * `stop_request` (-> patch just that card's `.jobacts`/`.stopnote`, never
 * its `.badge .sic` status-icon subtree). See that module's header for why
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
 * Under the bulk bar: current, average ("while loading") and peak speed,
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
 * **Updates available (WP UPD-6).** A section between Paused and Queue
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
 * After a delete the history list, the statistics header and the store's
 * jobs are fetched again; a failure keeps the dialog open with the error.
 * Wording: `lib/history-delete.js`.
 *
 * Section order, top to bottom: heading, bulk bar, statistics header,
 * Active, Paused, Updates available, Incomplete, Queue, History.
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
import {
  DASH,
  FEED_TEXT,
  emptyHistoryText,
  feedNote,
  feedState,
  filterJobsByRange,
  formatSpeed,
  headerTiles,
  rateCapLine,
  isRange,
  isStatsRange,
  readStoredStatsRange,
  STATS_RANGES,
  LIVE_POLL_MS,
  LIVE_SUMMARY_MS,
  LIVE_SUMMARY_RANGE,
  liveSeries,
  mergeLive,
  trimSeries,
  writeStoredStatsRange,
  jobSpeedText,
  JOB_SPEED_NOTE,
  joinNotes,
  mergeJobLists,
  refreshFailedText,
  seriesGeometry,
  seriesLabel,
  seriesStartText,
} from "../lib/traffic-view.js";
import { showToast } from "../components/toast.js";
import { createStatusIcon } from "../components/status-icon.js";
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
import { selectExcerptDisplay, EXCERPT_STATE } from "../lib/log-excerpt.js";
import { formatTimestamp, formatBytesGB } from "../lib/format.js";
import {
  pendingUpdates,
  updatesHeadingText,
  updateAllAriaLabel,
  updateNowAriaLabel,
  scheduleNote,
  summarizeUpdateStart,
  describeUpdateStartError,
  updatesListKey,
  UPDATE_NOW_LABEL,
  UPDATE_ALL_LABEL,
  STARTING_LABEL,
} from "../lib/updates-available.js";
import {
  pendingIncomplete,
  incompleteHeadingText,
  completeAllAriaLabel,
  completeNowAriaLabel,
  incompletePctText,
  incompleteScheduleNote,
  incompleteListKey,
  isCompletable,
  summarizeVerifyStart,
  describeVerifyStartError,
  COMPLETE_NOW_LABEL,
  COMPLETE_ALL_LABEL,
  DAMAGED_NOTE,
} from "../lib/cache-completeness.js";
import { onViewChange } from "../router.js";
import { isConnectionLost, onConnectionChange } from "../connection-status.js";
import { OFFLINE_CONTROL_TITLE } from "../lib/connection-watch.js";
import { fillMissingNames, hasText, vaultRowTitle } from "../lib/owned-library.js";
import { isToolApp, TOOL_APP_NOTE } from "../lib/game-status.js";
import { ownedLibrary } from "../owned-singleton.js";
import { contentPackageFallbackTitle } from "../lib/content-groups.js";
import { jobFailureHint, HINTS, NEWER_JOB_LINE, isNewestJobForApp, offersRetryFor } from "../lib/job-failure.js";
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
const CHEVRON_SVG =
  '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m7.5 4.5 6 5.5-6 5.5"/></svg>';

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

/** WP D15c: the statistics header's last answer for `range`
 * (`{throughput, series}`), and the History filter's fetched rows. Module
 * state, so a re-mount keeps the chosen ranges. */
const traffic = { range: readStoredStatsRange(), throughput: null, series: null, error: null, unsupported: false, noFigures: false };
const history = { range: "all", jobs: [], gen: 0 };

/** WP D15c: the history fetch's page size (the API's maximum; no paging). */
const HISTORY_LIMIT = 200;

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
  const row = document.createElement("div");
  row.className = "urow";
  row.dataset.updateAppid = String(game.appid);
  const title = nameFor(game.appid, gamesByAppid);
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.dataset.appid = String(game.appid); // patchNames keeps it current
  nm.textContent = title;
  const size = document.createElement("span");
  size.className = "usize";
  size.textContent = sizeOfRow(game) || "—";
  const btn = updateButton(UPDATE_NOW_LABEL, "primary", updateNowAriaLabel(title), () =>
    startUpdates([game.appid], game.appid),
  );
  btn.dataset.role = "update-now";
  // Not `data-appid`: patchNames rewrites the text of every such node.
  btn.dataset.updateAppid = String(game.appid);
  row.append(nm, size, btn);
  return row;
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
    const which = updateButtonId(btn);
    const idle = role === "update-all" ? UPDATE_ALL_LABEL : UPDATE_NOW_LABEL;
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

  const { updates, updatesHeading, updatesNote, updatesActs, updatesBody } = els;
  updates.hidden = rows.length === 0;
  updatesHeading.textContent = updatesHeadingText(rows.length);
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
  const row = document.createElement("div");
  row.className = "urow";
  row.dataset.incompleteAppid = String(game.appid);
  const title = nameFor(game.appid, gamesByAppid);
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.dataset.appid = String(game.appid); // patchNames keeps it current
  nm.textContent = title;
  const pct = document.createElement("span");
  pct.className = "usize";
  pct.textContent = incompletePctText(game);
  row.append(nm, pct);
  if (isCompletable(game)) {
    const btn = completionButton(COMPLETE_NOW_LABEL, "primary", completeNowAriaLabel(title), () =>
      startCompletion([game.appid], game.appid),
    );
    btn.dataset.role = "complete-now";
    // Not `data-appid`: patchNames rewrites the text of every such node.
    btn.dataset.incompleteAppid = String(game.appid);
    row.appendChild(btn);
  } else {
    // Short only of wrong-size chunks: nothing a forced run fixes.
    const note = document.createElement("span");
    note.className = "unote";
    note.dataset.role = "damaged-note";
    note.textContent = DAMAGED_NOTE;
    row.appendChild(note);
  }
  return row;
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
    const which = completionButtonId(btn);
    const idle = btn.dataset.role === "complete-all" ? COMPLETE_ALL_LABEL : COMPLETE_NOW_LABEL;
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

  const { incomplete, incompleteHeading, incompleteNote, incompleteActs, incompleteBody } = els;
  incomplete.hidden = rows.length === 0;
  incompleteHeading.textContent = incompleteHeadingText(rows.length);
  incompleteNote.hidden = !note;
  incompleteNote.textContent = note ?? "";
  incompleteActs.replaceChildren();
  incompleteBody.replaceChildren();
  const completable = rows.filter(isCompletable).map((g) => g.appid);
  if (completable.length) {
    const all = completionButton(COMPLETE_ALL_LABEL, "", completeAllAriaLabel(completable.length), () =>
      startCompletion(completable, "all"),
    );
    all.dataset.role = "complete-all";
    incompleteActs.appendChild(all);
  }
  for (const g of rows) incompleteBody.appendChild(buildIncompleteRow(g, gamesByAppid));
  paintIncompleteBusy();

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
// Job card (Active / Paused sections)
// ---------------------------------------------------------------------

/** Rebuild ONLY `.jobacts` and `.stopnote` from `job`'s current control
 * fields — never touches `.jobtop`/`.badge`/its status-icon subtree, so
 * this is safe to call from the patch path (downloads-render-plan.js's
 * `patchStopRequest`) as well as from the initial build. */
function paintJobActions(card, job) {
  const acts = card.querySelector(".jobacts");
  const stopNote = card.querySelector(".stopnote");
  if (!acts || !stopNote) return;

  const cancelling = job.status === "running" && job.stop_request === "cancel";
  const pausing = job.status === "running" && job.stop_request === "pause";

  acts.replaceChildren();
  if (job.status === "paused") {
    acts.append(
      actionButton("Resume", "primary", () => onResume(job.id)),
      actionButton("Cancel", "danger", () => onCancel(job.id)),
    );
  } else if (job.status === "running") {
    if (job.type === "prefill") {
      const pauseBtn = actionButton(pausing ? "Pausing…" : "Pause", "", () => onPause(job.id));
      pauseBtn.disabled = pauseBtn.disabled || pausing || cancelling;
      acts.appendChild(pauseBtn);
    }
    const cancelBtn = actionButton(cancelling ? "Cancelling…" : "Cancel", "danger", () =>
      onCancel(job.id),
    );
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
 * @param {"active"|"held"} mode
 * @param {Map<number, object>} gamesByAppid
 */
function buildJobCard(job, mode, gamesByAppid) {
  const card = document.createElement("div");
  card.className = "jobcard " + mode;
  card.dataset.jid = String(job.id);
  // WP WEB-FEAT-6: the badge's kind (running/updating/verify/paused), read
  // back by `jobCardKindsStale` when a games poll lands.
  const kind = activeJobKind(job, gamesByAppid.get(job.appid));
  card.dataset.dk = kind;

  const top = document.createElement("div");
  top.className = "jobtop";

  const info = document.createElement("div");
  const nm = document.createElement("div");
  nm.className = "nm";
  nm.dataset.appid = String(job.appid);
  nm.textContent = nameFor(job.appid, gamesByAppid);
  const sm = document.createElement("div");
  sm.className = "sm";
  sm.textContent = `job #${job.id} · appid ${job.appid}`;
  info.append(nm, sm);

  const badge = document.createElement("span");
  badge.className = "badge tx-" + kind;
  const badgeIcon = createStatusIcon(kind, { size: "sm" });
  // WP 4a.8 icon audit: the word right after this icon already says the
  // same thing visibly — hide the icon's own sr-only label so it is not
  // announced twice (same posture as this file's own `buildHistoryRow`
  // icon, and clients-sheet.js/notifications.js's status icons).
  badgeIcon.setAttribute("aria-hidden", "true");
  badge.appendChild(badgeIcon);
  const word = document.createElement("span");
  word.textContent = activeJobWord(job, gamesByAppid.get(job.appid));
  badge.appendChild(word);

  top.append(info, badge);
  card.appendChild(top);

  // WP D15c (D13 SPEED-1): the job's speed at the cache, painted by
  // paintJobSpeeds() from the statistics poll; hidden until there is one.
  if (mode === "active") {
    const speed = document.createElement("p");
    speed.className = "jobspeed";
    speed.dataset.role = "job-speed";
    speed.title = "Measured at the cache over the last few minutes, on this game's own depots.";
    speed.hidden = true;
    card.appendChild(speed);
  }

  if (mode === "held") {
    // The slot-release divergence, stated on the card itself — see
    // js/lib/job-partition.js's module header for the full "why".
    const note = document.createElement("p");
    note.className = "holdnote";
    note.textContent =
      "Paused — this does not hold the worker slot. vault-api released it immediately, so another queued job may already be running. Resume puts this job back at the front of the queue.";
    card.appendChild(note);
  }

  const stopNote = document.createElement("p");
  stopNote.className = "stopnote";
  stopNote.hidden = true;
  card.appendChild(stopNote);

  const acts = document.createElement("div");
  acts.className = "jobacts";
  card.appendChild(acts);

  paintJobActions(card, job);
  return card;
}

// ---------------------------------------------------------------------
// Queue row
// ---------------------------------------------------------------------

function buildQueueRow(job, position, gamesByAppid, total = 1) {
  const row = document.createElement("div");
  row.className = "qrow";
  row.dataset.jid = String(job.id);

  const grip = staticIcon(GRIP_SVG);
  const gripWrap = document.createElement("span");
  gripWrap.className = "grip";
  gripWrap.appendChild(grip);

  const nm = document.createElement("span");
  nm.className = "nm";
  nm.dataset.appid = String(job.appid);
  nm.textContent = nameFor(job.appid, gamesByAppid);

  // WP D20: the grip is the reorder handle (pointer drag, or focus + arrow
  // keys). With one queued job, or while the connection is lost, there is
  // nothing to move: it stays a plain icon and is not focusable.
  if (total > 1 && !isConnectionLost()) {
    gripWrap.className = "grip grip-live";
    gripWrap.setAttribute("role", "button");
    gripWrap.setAttribute("tabindex", "0");
    gripWrap.setAttribute(
      "aria-label",
      `Reorder ${nm.textContent}, position ${position} of ${total}. Use the up and down arrow keys, Home or End.`,
    );
    gripWrap.addEventListener("keydown", (e) => {
      const target = keyTargetIndex(e.key, position - 1, total);
      if (target === null) return;
      e.preventDefault();
      void moveQueued(job.id, target, { refocus: true });
    });
    gripWrap.addEventListener("pointerdown", (e) => startQueueDrag(e, row, job.id));
  }

  const pos = document.createElement("span");
  pos.className = "pos";
  pos.textContent = `#${position}`;

  const removeBtn = actionButton("Remove", "danger", () => onCancel(job.id));

  row.append(gripWrap, nm, pos, removeBtn);
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
  const row = active.parentNode;
  return row && row.dataset && row.dataset.jid ? Number(row.dataset.jid) : null;
}

function focusGrip(jobId) {
  if (!mounted()) return;
  const grip = els.queueBody.querySelector(`.qrow[data-jid="${jobId}"] .grip`);
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

function paintExcerpt(rowEl, jobId) {
  const logEl = rowEl.querySelector(".log");
  if (!logEl) return;
  const st = getExcerptState(jobId);
  const display = selectExcerptDisplay(st);
  logEl.replaceChildren();

  if (display.state === EXCERPT_STATE.COLLAPSED) return;
  if (display.state === EXCERPT_STATE.LOADING) {
    const p = document.createElement("p");
    p.className = "loading";
    p.textContent = "Loading log…";
    logEl.appendChild(p);
    return;
  }
  if (display.state === EXCERPT_STATE.ERROR) {
    const p = document.createElement("p");
    p.className = "errmsg";
    p.textContent = `Could not load the log: ${display.message}`;
    logEl.appendChild(p);
    return;
  }
  if (display.state === EXCERPT_STATE.EMPTY) {
    const p = document.createElement("p");
    p.className = "emptymsg";
    p.textContent = "No log output for this job.";
    logEl.appendChild(p);
    return;
  }
  const job = findJobById(jobId);
  const hint = jobFailureHint(job, st.excerpt);
  const outputParent = hint ? appendFailureHint(logEl, hint, job, st) : logEl;
  if (display.truncated) {
    const note = document.createElement("p");
    note.className = "truncnote";
    note.textContent = "Truncated — showing the last portion of the output.";
    outputParent.appendChild(note);
  }
  const body = document.createElement("div");
  body.textContent = display.lines.join("\n");
  outputParent.appendChild(body);
}

/** WP WEB-FIX-4: a known failure's hint block ("Steam login missing",
 * "cannot find the cache"; text in lib/job-failure.js's HINTS), then a
 * closed `<details>` for the raw output. Returns the `<details>` the caller
 * puts the output into. Text only (textContent), no markup from the log.
 * Retry only on the newest prefill job for the app: an older failed row
 * would queue a second run of a game that already has a newer job. */
function appendFailureHint(logEl, hintKind, job, st) {
  const text = HINTS[hintKind];
  const box = document.createElement("div");
  box.className = "failhint";
  box.dataset.hint = hintKind;
  const para = (value, className) => {
    const p = document.createElement("p");
    if (className) p.className = className;
    p.textContent = value;
    return p;
  };
  const code = document.createElement("code");
  code.className = "cmd";
  code.textContent = text.code;
  box.append(para(text.title, "failhint-title"), para(text.body), para(text.codeIntro), code, para(text.after));
  if (!offersRetryFor(gamesByAppidMap().get(job.appid))) {
    // WP API-FIX-4: a tool app's job is never retried (POST /v1/prefill
    // answers 422); say why instead of offering the button.
    box.appendChild(para(TOOL_APP_NOTE));
  } else if (isNewestJobForApp(job, state.jobs)) {
    box.appendChild(para(text.retry));
    const acts = document.createElement("div");
    acts.className = "jobacts";
    const retry = actionButton("Retry", "primary", () => onRetry(job.appid));
    retry.dataset.retryAppid = String(job.appid); // patchNames keeps the label current
    retry.setAttribute("aria-label", `Retry ${nameFor(job.appid, gamesByAppidMap())}`);
    acts.appendChild(retry);
    box.appendChild(acts);
  } else {
    box.appendChild(para(NEWER_JOB_LINE));
  }

  const raw = document.createElement("details");
  raw.className = "rawlog";
  raw.open = !!st.rawOpen;
  raw.addEventListener("toggle", () => {
    st.rawOpen = raw.open;
  });
  const summary = document.createElement("summary");
  summary.textContent = text.outputSummary;
  raw.appendChild(summary);

  logEl.append(box, raw);
  return raw;
}

function historyRowNow(jobId) {
  return mounted() ? els.historyBody.querySelector(`.hrow[data-jid="${jobId}"]`) : null;
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
    row.classList.toggle("open", st.expanded);
    row.querySelector("button").setAttribute("aria-expanded", String(st.expanded));
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
 * against css/app.css: no `overflow:hidden` above `.hrow`), so the mockup's
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
  applyPendingHighlight();
}

function applyPendingHighlight() {
  if (pendingHighlightJobId == null) return;
  const jobId = pendingHighlightJobId;
  const row = historyRowNow(jobId);
  if (!row) return; // job not in the History section on this render yet — stays queued
  pendingHighlightJobId = null;
  row.classList.add("open");
  const toggle = row.querySelector("button");
  if (toggle) toggle.setAttribute("aria-expanded", "true");
  paintExcerpt(row, jobId);
  ensureExcerptLoaded(jobId, row);
  row.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "center" });
  if (toggle) toggle.focus();
}

function buildHistoryRow(job, gamesByAppid) {
  const row = document.createElement("div");
  row.className = "hrow";
  row.dataset.jid = String(job.id);
  const st = getExcerptState(job.id);
  if (st.expanded) row.classList.add("open");

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(st.expanded));

  // jobIconKind reuses the "cached" glyph (a checkmark) for a DONE job —
  // sharing the shape is right (both mean "succeeded"), but that kind's
  // built-in screen-reader word from status-icon.js is "Current" (correct
  // for a library card, misleading read out loud for a finished job here).
  // The icon is made purely decorative and `.when` below (which already
  // states "Done"/"Failed"/"Cancelled" as visible text) carries the real
  // accessible word instead, so nothing is announced twice OR wrong.
  const kind = jobIconKind(job); // "cached" | "error" | "cancelled"
  const iconWrap = document.createElement("span");
  iconWrap.setAttribute("aria-hidden", "true");
  iconWrap.appendChild(createStatusIcon(kind, { size: "sm" }));
  toggle.appendChild(iconWrap);

  const info = document.createElement("span");
  info.className = "hrow-info";
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.dataset.appid = String(job.appid);
  nm.textContent = nameFor(job.appid, gamesByAppid);
  const when = document.createElement("div");
  when.className = "when";
  when.textContent = `job #${job.id} · ${jobStatusWord(job)} · ${formatTimestamp(job.finished_at)}`;
  info.append(nm, when);

  const arrow = document.createElement("span");
  arrow.className = "arrow";
  arrow.appendChild(staticIcon(CHEVRON_SVG));

  toggle.append(info, arrow);
  toggle.addEventListener("click", () => toggleHistoryRow(job.id));

  const log = document.createElement("div");
  log.className = "log";

  row.append(toggle, log);
  paintExcerpt(row, job.id);
  return row;
}

// ---------------------------------------------------------------------
// Section rendering
// ---------------------------------------------------------------------

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

function subtitleText(p) {
  const bits = [];
  if (p.running.length) bits.push(`${p.running.length} running`);
  if (p.paused.length) bits.push(`${p.paused.length} paused`);
  if (p.queued.length) bits.push(`${p.queued.length} queued`);
  if (!bits.length) return `Idle · ${p.history.length} in history`;
  return bits.join(" · ");
}

function fullRender() {
  if (!mounted()) return;
  const p = partitionJobs(state.jobs);
  const gamesByAppid = gamesByAppidMap();

  els.sub.textContent = subtitleText(p);
  paintBulkBar();

  els.activeBody.replaceChildren();
  if (!p.running.length) {
    els.activeBody.appendChild(emptyMessage("No download running. Start one from the Library."));
  } else {
    for (const job of p.running) els.activeBody.appendChild(buildJobCard(job, "active", gamesByAppid));
  }

  const hasPaused = p.paused.length > 0;
  els.pausedHeading.hidden = !hasPaused;
  els.pausedBody.hidden = !hasPaused;
  els.pausedBody.replaceChildren();
  for (const job of p.paused) els.pausedBody.appendChild(buildJobCard(job, "held", gamesByAppid));

  els.queueCount.textContent = String(p.queued.length);
  // WP D20: a rebuild must not drop keyboard focus from a reorder handle.
  const focusedJid = focusedGripJid();
  els.queueBody.replaceChildren();
  if (!p.queued.length) {
    els.queueBody.appendChild(hintMessage("Nothing waiting."));
    els.queueHint.textContent = "";
  } else {
    for (const job of p.queued) {
      els.queueBody.appendChild(buildQueueRow(job, queuePosition(p.queued, job.id), gamesByAppid, p.queued.length));
    }
    els.queueHint.textContent =
      "vault-api runs one job at a time, top of the list first. Drag a row by its handle, or focus the handle and use the arrow keys, to change the order." +
      (hasPaused
        ? " A paused job does not hold this queue back — the queue keeps draining."
        : "");
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

function renderHistory(gamesByAppid = gamesByAppidMap()) {
  if (!mounted()) return;
  const finished = historyRows();
  els.historyKey = historyKey(finished);
  els.historyBody.replaceChildren();
  if (!finished.length) {
    els.historyBody.appendChild(hintMessage(emptyHistoryText(history.range)));
  } else {
    for (const job of finished) els.historyBody.appendChild(buildHistoryRow(job, gamesByAppid));
  }
  applyPendingHighlight();
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
  if (mounted() && historyKey(historyRows()) !== els.historyKey) renderHistory();
}

function setHistoryRange(range) {
  if (!isRange(range) || range === history.range) return;
  history.range = range;
  history.jobs = [];
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
// Statistics header (WP D15c)
// ---------------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";
const GRAPH_W = 300;
const GRAPH_H = 60;

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

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

/** Repaint the note, the tiles and the graph from `traffic`. Nothing in
 * here is focusable, so rebuilding the tiles takes no focus. */
function paintStats() {
  if (!mounted() || !els.statsNote) return;
  const t = traffic.throughput;
  let note = null;
  if (traffic.unsupported) note = FEED_TEXT.unsupported;
  else if (traffic.error && !t) note = `Could not load the statistics: ${traffic.error}`;
  else if (t) note = joinNotes(traffic.error ? refreshFailedText(traffic.error) : null, feedNote(t));
  else note = "Loading statistics…";
  els.statsNote.textContent = note || "";
  els.statsNote.hidden = !note;
  // No figures (feed disabled, or a server without the endpoints): no
  // range to choose either, the note stands alone. While an answer is
  // pending the last known verdict holds, so a range click never hides
  // the control it came from.
  els.statsRange.hidden = traffic.noFigures;

  const cap = state.settings ? rateCapLine(state.settings, state.schedule, Date.now()) : null;
  const tiles = traffic.unsupported ? null : headerTiles(t, cap);
  els.statsGrid.replaceChildren();
  els.statsGrid.hidden = !tiles;
  for (const tile of tiles || []) {
    const box = document.createElement("div");
    box.className = "stat";
    box.dataset.stat = tile.key;
    box.title = tile.title;
    const dt = document.createElement("dt");
    dt.textContent = tile.label;
    const dd = document.createElement("dd");
    if (tile.value === DASH) {
      const dash = document.createElement("span");
      dash.setAttribute("aria-hidden", "true");
      dash.textContent = DASH;
      const sr = document.createElement("span");
      sr.className = "sr-only";
      sr.textContent = "Not available";
      dd.append(dash, sr);
    } else {
      dd.textContent = tile.value;
    }
    if (tile.detail) {
      const detail = document.createElement("span");
      detail.className = "stat-detail";
      detail.textContent = tile.detail;
      dd.appendChild(detail);
    }
    if (tile.limit) {
      const limit = document.createElement("span");
      limit.className = "stat-detail";
      limit.dataset.stat = "limit";
      limit.textContent = tile.limit;
      dd.appendChild(limit);
    }
    box.append(dt, dd);
    els.statsGrid.appendChild(box);
  }
  paintGraph();
}

function paintGraph() {
  const s = traffic.series;
  const show = !traffic.unsupported && s && feedState(s) !== "disabled" && s.range === traffic.range;
  // WP GRAPH-ZOOM: only the span that has data; the axis caption follows it.
  const shown = show ? { ...s, points: trimSeries(s.points) } : null;
  const geo = shown ? seriesGeometry(shown.points, { width: GRAPH_W, height: GRAPH_H }) : null;
  els.graph.replaceChildren();
  // An all-zero series is a flat line on the floor: nothing to show.
  els.graph.hidden = !geo || geo.maxBytesPerSecond === 0;
  if (els.graph.hidden) return;
  const svg = svgEl("svg", {
    viewBox: `0 0 ${GRAPH_W} ${GRAPH_H}`,
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": seriesLabel(s, geo.maxBytesPerSecond),
  });
  svg.dataset.role = "graph-svg";
  svg.append(svgEl("path", { class: "tg-area", d: geo.area }), svgEl("path", { class: "tg-line", d: geo.line }));
  const axis = document.createElement("div");
  axis.className = "tg-axis";
  axis.setAttribute("aria-hidden", "true");
  for (const text of [seriesStartText(shown), `highest ${formatSpeed(geo.maxBytesPerSecond) || "0 MB/s"}`, "now"]) {
    const span = document.createElement("span");
    span.textContent = text;
    axis.appendChild(span);
  }
  els.graph.append(svg, axis);
}

/** Patch every running job card's speed line (text only). */
function paintJobSpeeds() {
  if (!mounted()) return;
  const t = traffic.throughput;
  const state = feedState(t);
  const entries = new Map(((t && t.jobs) || []).map((j) => [j.job_id, j]));
  for (const card of els.activeBody.querySelectorAll(".jobcard")) {
    const line = card.querySelector('[data-role="job-speed"]');
    if (!line) continue;
    const text = t && t.range === traffic.range ? jobSpeedText(entries.get(Number(card.dataset.jid)), state) : null;
    line.textContent = text || "";
    line.hidden = !text;
    line.title = text ? JOB_SPEED_NOTE : "";
  }
}

/** Patch just the named jobs' `.jobacts`/`.stopnote` (their `stop_request`
 * changed, their `status` did not — downloads-render-plan.js's verdict).
 * Never touches a `.badge .sic` node. */
function patchStopRequests(jobIds) {
  for (const jobId of jobIds) {
    const job = state.jobs.find((j) => j.id === jobId);
    const card =
      els.activeBody.querySelector(`.jobcard[data-jid="${jobId}"]`) ||
      els.pausedBody.querySelector(`.jobcard[data-jid="${jobId}"]`);
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
  // The failure hints' Retry buttons name the game too (aria-label).
  for (const btn of els.section.querySelectorAll("button[data-retry-appid]")) {
    const label = `Retry ${nameFor(Number(btn.dataset.retryAppid), gamesByAppid)}`;
    if (btn.getAttribute("aria-label") !== label) btn.setAttribute("aria-label", label);
  }
}

// ---------------------------------------------------------------------
// Static DOM construction
// ---------------------------------------------------------------------

function sectionHeading(text) {
  const h4 = document.createElement("h4");
  h4.className = "sec";
  h4.textContent = text;
  return h4;
}

function buildSection() {
  const section = document.createElement("section");
  section.className = "view view-downloads";

  const head = document.createElement("div");
  head.className = "dl-head";
  const h1 = document.createElement("h1");
  h1.textContent = "Downloads";
  const sub = document.createElement("span");
  sub.className = "dl-sub";
  head.append(h1, sub);

  // WP WEB-FEAT-5: Pause all / Resume all + the scheduler note.
  const bulkBar = document.createElement("div");
  bulkBar.className = "dl-bulk";
  bulkBar.setAttribute("role", "group");
  bulkBar.setAttribute("aria-label", BULK.groupLabel);
  bulkBar.hidden = true;
  const bulkActs = document.createElement("div");
  bulkActs.className = "dl-bulk-acts";
  const bulkPause = bulkButton(BULK.pauseAll, "", openPauseConfirm);
  bulkPause.dataset.role = "bulk-pause";
  const bulkResume = bulkButton(BULK.resumeAll, "primary", () => runBulk("resume"));
  bulkResume.dataset.role = "bulk-resume";
  bulkActs.append(bulkPause, bulkResume);
  const bulkNote = document.createElement("p");
  bulkNote.className = "dl-bulk-note";
  bulkNote.textContent = BULK.schedulerNote;
  bulkBar.append(bulkActs, bulkNote);

  // WP D15c: the statistics header.
  const stats = document.createElement("div");
  stats.className = "dl-stats";
  stats.setAttribute("role", "region");
  stats.setAttribute("aria-label", "Download statistics");
  stats.dataset.role = "dl-stats";
  const statsRange = createRangeSegs({
    label: "Statistics range",
    value: traffic.range,
    onChange: setStatsRange,
    role: "stats-range",
    ranges: STATS_RANGES,
  });
  const statsNote = hintMessage("");
  statsNote.dataset.role = "stats-note";
  statsNote.hidden = true;
  const statsGrid = document.createElement("dl");
  statsGrid.className = "statgrid";
  statsGrid.dataset.role = "stats-grid";
  statsGrid.hidden = true;
  const graph = document.createElement("figure");
  graph.className = "tgraph";
  graph.dataset.role = "stats-graph";
  graph.hidden = true;
  statsRange.el.hidden = traffic.noFigures;
  stats.append(statsRange.el, statsNote, statsGrid, graph);

  const activeHeading = sectionHeading("Active");
  const activeBody = document.createElement("div");

  const pausedHeading = sectionHeading("Paused");
  const pausedBody = document.createElement("div");

  // WP UPD-6: hidden until paintUpdates finds a flagged game.
  const updates = document.createElement("div");
  updates.className = "dl-updates";
  updates.hidden = true;
  const updatesHeading = sectionHeading("");
  updatesHeading.id = "dl-updates-heading";
  updates.setAttribute("role", "group");
  updates.setAttribute("aria-labelledby", "dl-updates-heading");
  const updatesNote = hintMessage("");
  updatesNote.classList.add("dl-updates-note");
  const updatesActs = document.createElement("div");
  updatesActs.className = "dl-updates-acts";
  const updatesBody = document.createElement("div");
  updatesBody.className = "dl-updates-list";
  updates.append(updatesHeading, updatesNote, updatesActs, updatesBody);

  // WP CACHE-COMPLETE-1c: hidden until paintIncomplete finds a game. Styled
  // like the updates section (`.dl-updates`), found by `.dl-incomplete`.
  const incomplete = document.createElement("div");
  incomplete.className = "dl-updates dl-incomplete";
  incomplete.hidden = true;
  const incompleteHeading = sectionHeading("");
  incompleteHeading.id = "dl-incomplete-heading";
  incomplete.setAttribute("role", "group");
  incomplete.setAttribute("aria-labelledby", "dl-incomplete-heading");
  const incompleteNote = hintMessage("");
  incompleteNote.classList.add("dl-updates-note");
  const incompleteActs = document.createElement("div");
  incompleteActs.className = "dl-updates-acts";
  const incompleteBody = document.createElement("div");
  incompleteBody.className = "dl-updates-list";
  incomplete.append(incompleteHeading, incompleteNote, incompleteActs, incompleteBody);

  const queueHeading = document.createElement("h4");
  queueHeading.className = "sec";
  queueHeading.tabIndex = -1; // WP UPD-6: focus anchor when the updates section empties
  queueHeading.append("Queue ");
  const queueCount = document.createElement("span");
  queueCount.className = "n";
  queueHeading.appendChild(queueCount);
  const queueBody = document.createElement("div");
  const queueHint = hintMessage("");

  const historyHeading = sectionHeading("History");
  // WP D15c: the History filter (its own range, default All).
  const historyRange = createRangeSegs({
    label: "History range",
    value: history.range,
    onChange: setHistoryRange,
    role: "history-range",
  });
  // WP D15-NAMES: "Delete history…" under the filter.
  const historyActs = document.createElement("div");
  historyActs.className = "jobacts dl-history-acts";
  const historyDeleteBtn = document.createElement("button");
  historyDeleteBtn.type = "button";
  historyDeleteBtn.className = "btn ghost sm";
  historyDeleteBtn.textContent = "Delete history…";
  historyDeleteBtn.dataset.role = "history-delete";
  historyDeleteBtn.addEventListener("click", () => openHistoryDelete());
  historyActs.appendChild(historyDeleteBtn);
  const historyBody = document.createElement("div");

  section.append(
    head,
    bulkBar,
    stats,
    activeHeading,
    activeBody,
    pausedHeading,
    pausedBody,
    updates,
    incomplete,
    queueHeading,
    queueBody,
    queueHint,
    historyHeading,
    historyRange.el,
    historyActs,
    historyBody,
  );

  els = {
    section,
    sub,
    bulkBar,
    bulkPause,
    bulkResume,
    activeBody,
    pausedHeading,
    pausedBody,
    queueCount,
    queueBody,
    queueHint,
    historyBody,
    historyDeleteBtn,
    statsRange: statsRange.el,
    statsNote,
    statsGrid,
    graph,
    updates,
    updatesHeading,
    updatesNote,
    updatesActs,
    updatesBody,
    updatesKey: null,
    incomplete,
    incompleteHeading,
    incompleteNote,
    incompleteActs,
    incompleteBody,
    incompleteKey: null,
    queueHeading,
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
  for (const card of els.section.querySelectorAll(".jobcard")) {
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
