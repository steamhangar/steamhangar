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
 * `highlightJob(jobId)` (WP 4a.7) is this module's one export beyond
 * `renderDownloads` — the notification bell's "job events -> Downloads
 * with the job highlighted" navigation target lands here without any
 * other module reaching into this view's internals.
 */

import { store } from "../store-singleton.js";
import { api } from "../api.js";
import { showToast } from "../components/toast.js";
import { createStatusIcon } from "../components/status-icon.js";
import {
  partitionJobs,
  countPending,
  queuePosition,
  jobIconKind,
  jobStatusWord,
} from "../lib/job-partition.js";
import { planJobsUpdate } from "../lib/downloads-render-plan.js";
import { selectExcerptDisplay, EXCERPT_STATE } from "../lib/log-excerpt.js";
import { formatTimestamp } from "../lib/format.js";
import { onViewChange } from "../router.js";
import { isConnectionLost, onConnectionChange } from "../connection-status.js";
import { OFFLINE_CONTROL_TITLE } from "../lib/connection-watch.js";

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
};

/** jobId -> {expanded, loading, error, excerpt}. Persists across re-mounts
 * AND across a full section rebuild triggered by an unrelated job
 * transition, so expanding a history row survives a poll tick that
 * structurally changes some OTHER job. `excerpt` is `undefined` until the
 * lazy `GET /v1/jobs/{id}` fetch has completed at least once — distinct
 * from a job that genuinely has no log output (`""`/`null`). */
const excerptState = new Map();
function getExcerptState(jobId) {
  if (!excerptState.has(jobId)) {
    excerptState.set(jobId, { expanded: false, loading: false, error: null, excerpt: undefined });
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

function gamesByAppidMap() {
  return new Map(state.games.map((g) => [g.appid, g]));
}
function nameFor(appid, gamesByAppid) {
  return gamesByAppid.get(appid)?.name || `App ${appid}`;
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
  card.dataset.dk = job.status;

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

  const kind = jobIconKind(job);
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
  word.textContent = jobStatusWord(job);
  badge.appendChild(word);

  top.append(info, badge);
  card.appendChild(top);

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

function buildQueueRow(job, position, gamesByAppid) {
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

  const pos = document.createElement("span");
  pos.className = "pos";
  pos.textContent = `#${position}`;

  const removeBtn = actionButton("Remove", "danger", () => onCancel(job.id));

  row.append(gripWrap, nm, pos, removeBtn);
  return row;
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
  if (display.truncated) {
    const note = document.createElement("p");
    note.className = "truncnote";
    note.textContent = "Truncated — showing the last portion of the output.";
    logEl.appendChild(note);
  }
  const body = document.createElement("div");
  body.textContent = display.lines.join("\n");
  logEl.appendChild(body);
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
  els.queueBody.replaceChildren();
  if (!p.queued.length) {
    els.queueBody.appendChild(hintMessage("Nothing waiting."));
    els.queueHint.textContent = "";
  } else {
    for (const job of p.queued) {
      els.queueBody.appendChild(buildQueueRow(job, queuePosition(p.queued, job.id), gamesByAppid));
    }
    els.queueHint.textContent =
      "vault-api runs one job at a time, oldest first. Drag-to-reorder is not built yet — the queue is FIFO." +
      (hasPaused
        ? " A paused job does not hold this queue back — it keeps draining oldest-first."
        : "");
  }

  els.historyBody.replaceChildren();
  if (!p.history.length) {
    els.historyBody.appendChild(hintMessage("Nothing finished yet."));
  } else {
    for (const job of p.history) els.historyBody.appendChild(buildHistoryRow(job, gamesByAppid));
  }

  applyPendingHighlight();
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

  const activeHeading = sectionHeading("Active");
  const activeBody = document.createElement("div");

  const pausedHeading = sectionHeading("Paused");
  const pausedBody = document.createElement("div");

  const queueHeading = document.createElement("h4");
  queueHeading.className = "sec";
  queueHeading.append("Queue ");
  const queueCount = document.createElement("span");
  queueCount.className = "n";
  queueHeading.appendChild(queueCount);
  const queueBody = document.createElement("div");
  const queueHint = hintMessage("");

  const historyHeading = sectionHeading("History");
  const historyBody = document.createElement("div");

  section.append(
    head,
    activeHeading,
    activeBody,
    pausedHeading,
    pausedBody,
    queueHeading,
    queueBody,
    queueHint,
    historyHeading,
    historyBody,
  );

  els = {
    section,
    sub,
    activeBody,
    pausedHeading,
    pausedBody,
    queueCount,
    queueBody,
    queueHint,
    historyBody,
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
  if (plan.full) {
    fullRender();
  } else if (plan.patchStopRequest.length) {
    patchStopRequests(plan.patchStopRequest);
  }
});

store.subscribe("games", ({ items }) => {
  if (!Array.isArray(items)) return;
  state.games = items;
  patchNames();
});

// WP WEB-FIX-2: repaint the job controls on a connection transition. A full
// rebuild, not a patch: transitions are rare, and every button's disabled
// state (including Pausing…/Cancelling…) is re-derived from scratch.
onConnectionChange(() => {
  fullRender();
});

onViewChange((view) => {
  if (view === "downloads") return;
  sectionEl = null;
});

// Paint the pip immediately from whatever snapshot already exists (e.g.
// the Library view already polled jobs before the user ever opened
// Downloads) rather than waiting for this module's first live tick.
updateNavPip(state.jobs);

export function renderDownloads() {
  const section = buildSection();
  sectionEl = section;
  fullRender();
  return section;
}
