/**
 * The "Repair" flow (WP CACHE-REPAIR-UI-1): dry run -> confirm dialog with
 * the dry run's counts -> execute -> the resulting job. Shared by the
 * Downloads "Incomplete" rows (`views/downloads.js`) and the game detail
 * sheet (`components/game-detail-sheet.js`); the decisions and every string
 * live in `lib/cache-repair.js`, this module is the DOM and the requests.
 *
 * `startRepair({appid, title, onShowJob})` sends the DRY RUN (`POST
 * /v1/cache/{appid}/repair`, no body) and resolves once that answer is
 * handled, so the caller can show its button busy meanwhile:
 * - nothing damaged: a toast, the store re-reads (the figure was stale);
 * - more than the cap: the dialog explains it, with "Close" only;
 * - otherwise the confirm: counts per depot, "Cancel" (initial focus) and
 *   "Delete and repair";
 * - a failed dry run: a toast with the honest reason, no dialog.
 * Only "Delete and repair" sends `{"execute": true}` (lib/cache-repair.js
 * `reduceRepairFlow`, THE pin). A failed execute keeps the dialog open with
 * the error (`role="alert"`). A successful one turns the dialog into the
 * result: what was deleted, the job, "Show job" (initial focus; calls
 * `onShowJob(jobId)` after the dialog has closed) and "Close".
 *
 * Markup and focus rules are those of the other confirms (Delete history,
 * Delete from cache): one `.dialog-backdrop` built on first use as a
 * `document.body` child, `role="alertdialog"` + `aria-modal`, Escape and the
 * inert background through `lib/modal-stack.js`, focus back to the invoker
 * on close (or the caller's `focusFallback` when that button is gone after a
 * successful repair). Every request answer carries the token of the flow
 * that sent it, so a late answer of a closed flow (sheet closed, another
 * game's repair started) is dropped instead of landing in the newer dialog.
 * Busy buttons use `aria-disabled` plus a guard, never `disabled`
 * (docs/LEARNINGS.md, WP WEB-FEAT-1). Text via `textContent` only.
 */

import { api } from "../api.js";
import { store } from "../store-singleton.js";
import { showToast } from "./toast.js";
import { pushModal, popModal } from "../lib/modal-stack.js";
import {
  REPAIR_STATE,
  REPAIR_EVENT,
  idleRepairState,
  reduceRepairFlow,
  describeRepairError,
  repairConfirmTitle,
  repairBlockedTitle,
  repairPlanText,
  repairDepotLine,
  unreadableDepotsText,
  repairBlockedText,
  repairDoneText,
  REPAIR_CONFIRM_LABEL,
  REPAIR_EXECUTING_LABEL,
  REPAIR_CANCEL_LABEL,
  REPAIR_CLOSE_LABEL,
  REPAIR_SHOW_JOB_LABEL,
  REPAIR_DONE_TITLE,
  REPAIR_NOOP_TITLE,
  REPAIR_NOTHING_TEXT,
  REPAIR_NOOP_TEXT,
} from "../lib/cache-repair.js";

let els = null;
let flow = idleRepairState();
let invokerEl = null;
let onShowJobCb = null;
let modalOpen = false;
let focusFallbackCb = null;
/** Serial of the flows started; each request answer carries its flow's
 * token, so a late answer of a closed flow never reaches a newer one. */
let serial = 0;

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

function build() {
  const backdrop = el("div", "dialog-backdrop");
  const dialog = el("div", "dialog repair-dialog");
  dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "repair-title");
  dialog.setAttribute("aria-describedby", "repair-text");
  dialog.dataset.role = "repair-dialog";
  const title = el("h3");
  title.id = "repair-title";
  const text = el("p");
  text.id = "repair-text";
  text.dataset.role = "repair-text";
  const list = el("ul", "cflist");
  list.dataset.role = "repair-depots";
  const notice = el("p", "cfnotice");
  notice.dataset.role = "repair-unreadable";
  const error = el("p", "errline");
  error.dataset.role = "repair-error";
  error.setAttribute("role", "alert");
  const row = el("div", "row");
  const secondary = el("button", "btn ghost sm");
  secondary.type = "button";
  secondary.dataset.role = "repair-cancel";
  const primary = el("button", "btn sm");
  primary.type = "button";
  primary.dataset.role = "repair-ok";
  row.append(secondary, primary);
  dialog.append(title, text, list, notice, error, row);
  backdrop.appendChild(dialog);
  document.body.appendChild(backdrop);
  secondary.addEventListener("click", () => {
    if (secondary.getAttribute("aria-disabled") === "true") return;
    dispatch({ type: REPAIR_EVENT.CLOSE });
  });
  primary.addEventListener("click", () => {
    if (primary.getAttribute("aria-disabled") === "true") return;
    if (flow.kind === REPAIR_STATE.CONFIRM) void confirmRepair();
    else if (flow.kind === REPAIR_STATE.DONE) showJob();
  });
  return { backdrop, dialog, title, text, list, notice, error, secondary, primary };
}

function dispatch(event) {
  const next = reduceRepairFlow(flow, event);
  if (next === flow) return;
  flow = next;
  paint();
}

function setBusy(btn, busy) {
  if (busy) btn.setAttribute("aria-disabled", "true");
  else btn.removeAttribute("aria-disabled");
}

/** Sync the dialog with `flow`. */
function paint() {
  const open =
    flow.kind === REPAIR_STATE.CONFIRM ||
    flow.kind === REPAIR_STATE.EXECUTING ||
    flow.kind === REPAIR_STATE.BLOCKED ||
    flow.kind === REPAIR_STATE.DONE;
  if (!open) {
    closeDialog();
    return;
  }
  if (!els) els = build();
  const { title, text, list, notice, error, secondary, primary } = els;
  const report = flow.report;
  list.replaceChildren();
  list.hidden = true;
  notice.textContent = "";
  notice.hidden = true;
  error.textContent = "";
  error.hidden = true;
  primary.hidden = false;
  setBusy(primary, false);
  setBusy(secondary, false);
  let focus = null;

  if (flow.kind === REPAIR_STATE.CONFIRM || flow.kind === REPAIR_STATE.EXECUTING) {
    title.textContent = repairConfirmTitle(flow.title);
    text.textContent = repairPlanText(report);
    for (const depot of Array.isArray(report.depots) ? report.depots : []) {
      const li = el("li", "free");
      li.appendChild(el("span", "did", repairDepotLine(depot)));
      list.appendChild(li);
    }
    list.hidden = list.childNodes.length === 0;
    const unreadable = unreadableDepotsText(report);
    notice.textContent = unreadable;
    notice.hidden = !unreadable;
    if (flow.kind === REPAIR_STATE.CONFIRM && flow.error) {
      error.textContent = flow.error;
      error.hidden = false;
    }
    secondary.textContent = REPAIR_CANCEL_LABEL;
    primary.className = "btn danger sm";
    const executing = flow.kind === REPAIR_STATE.EXECUTING;
    primary.textContent = executing ? REPAIR_EXECUTING_LABEL : REPAIR_CONFIRM_LABEL;
    setBusy(primary, executing);
    setBusy(secondary, executing);
    focus = secondary; // the non-destructive default
  } else if (flow.kind === REPAIR_STATE.BLOCKED) {
    title.textContent = repairBlockedTitle(flow.title);
    text.textContent = repairBlockedText(report);
    secondary.textContent = REPAIR_CLOSE_LABEL;
    primary.hidden = true;
    focus = secondary;
  } else {
    const removed = report && report.removed_chunks > 0;
    title.textContent = removed ? REPAIR_DONE_TITLE : REPAIR_NOOP_TITLE;
    text.textContent = removed ? repairDoneText(report) : REPAIR_NOOP_TEXT;
    secondary.textContent = REPAIR_CLOSE_LABEL;
    const hasJob = Number.isInteger(report?.job_id);
    // `.btn.primary` (app.css): the dialog is a body child outside `.kit`,
    // where kit.css's `.btn.pri` does not apply.
    primary.className = "btn primary sm";
    primary.textContent = REPAIR_SHOW_JOB_LABEL;
    primary.hidden = !hasJob;
    focus = hasJob ? primary : secondary;
  }

  if (!modalOpen) {
    modalOpen = true;
    els.backdrop.classList.add("on");
    pushModal(els.backdrop, () => dispatch({ type: REPAIR_EVENT.CLOSE }));
    focus.focus();
  } else if (flow.kind === REPAIR_STATE.DONE) {
    // Confirm -> result: the focused button changed its meaning.
    focus.focus();
  }
  // Executing and a failed execute keep focus where it is; the error line
  // is announced through role="alert".
}

function closeDialog({ restoreFocus = true } = {}) {
  if (!modalOpen || !els) return;
  modalOpen = false;
  els.backdrop.classList.remove("on");
  popModal(els.backdrop);
  const invoker = invokerEl;
  const fallback = focusFallbackCb;
  invokerEl = null;
  focusFallbackCb = null;
  if (!restoreFocus) return;
  if (invoker && typeof invoker.focus === "function" && invoker.isConnected !== false) {
    invoker.focus();
    return;
  }
  // The invoker is gone (a successful repair re-renders the row or sheet
  // without its button): the caller's fallback, never the bare body.
  const target = fallback ? fallback() : null;
  if (target && typeof target.focus === "function") target.focus();
}

async function confirmRepair() {
  if (flow.kind !== REPAIR_STATE.CONFIRM) return;
  const appid = flow.appid;
  const token = flow.token;
  dispatch({ type: REPAIR_EVENT.CONFIRM });
  if (flow.kind !== REPAIR_STATE.EXECUTING) return;
  let report;
  try {
    report = await api.repairCache(appid, true);
  } catch (err) {
    const outcome = describeRepairError(err, { execute: true });
    // A late answer of a closed flow is dropped by the reducer (token).
    dispatch({ type: REPAIR_EVENT.EXECUTE_FAILED, token, message: outcome.message });
    if (outcome.refresh) store.refreshNow();
    return;
  }
  dispatch({ type: REPAIR_EVENT.EXECUTE_OK, token, report });
  // Also for a late answer: files may have been deleted, re-read.
  store.refreshNow();
}

function showJob() {
  if (flow.kind !== REPAIR_STATE.DONE) return;
  const jobId = flow.report?.job_id;
  const cb = onShowJobCb;
  // The dialog closes first: navigating dismisses transient surfaces, and
  // the target view takes focus.
  invokerEl = null;
  focusFallbackCb = null;
  dispatch({ type: REPAIR_EVENT.CLOSE });
  if (cb && Number.isInteger(jobId)) cb(jobId);
}

/**
 * Start a repair: the dry run, then the dialog (module header). A call while
 * a flow is already running is a no-op.
 * @param {{appid: number, title: string, onShowJob?: (jobId: number) => void,
 *   focusFallback?: () => (HTMLElement | null)}} opts `focusFallback` names
 *   where focus goes on close when the invoking button is gone
 * @returns {Promise<void>} once the dry-run answer is handled
 */
export async function startRepair({ appid, title, onShowJob, focusFallback }) {
  if (flow.kind !== REPAIR_STATE.IDLE) return;
  invokerEl = document.activeElement;
  onShowJobCb = typeof onShowJob === "function" ? onShowJob : null;
  focusFallbackCb = typeof focusFallback === "function" ? focusFallback : null;
  const token = ++serial;
  dispatch({ type: REPAIR_EVENT.START, appid, title, token });
  let report;
  try {
    report = await api.repairCache(appid, false);
  } catch (err) {
    if (flow.token !== token) return; // a closed flow's answer: nothing to say
    dispatch({ type: REPAIR_EVENT.DRY_RUN_FAILED, token });
    const outcome = describeRepairError(err, { execute: false });
    showToast(outcome.message, { warn: true });
    if (outcome.refresh) store.refreshNow();
    return;
  }
  if (flow.token !== token || flow.kind !== REPAIR_STATE.CHECKING) return; // closed or superseded meanwhile
  dispatch({ type: REPAIR_EVENT.DRY_RUN_OK, token, report });
  if (flow.kind === REPAIR_STATE.IDLE) {
    showToast(REPAIR_NOTHING_TEXT);
    store.refreshNow();
  }
}

/** Close the dialog and forget the flow (sheet closed, view changed). An
 * execute already sent still finishes on the server. */
export function closeRepair({ restoreFocus = false } = {}) {
  flow = idleRepairState();
  closeDialog({ restoreFocus });
  invokerEl = null;
  focusFallbackCb = null;
}

/** Test seam: the current flow state kind. */
export function repairFlowState() {
  return flow.kind;
}
