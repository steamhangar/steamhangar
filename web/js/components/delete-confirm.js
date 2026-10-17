/**
 * The one "Delete from cache?" confirmation (WP LIBRARY-DETAIL-1). Before
 * this package `views/library.js` (bulk delete) and
 * `components/game-detail-sheet.js` (single game) each built the same
 * `.dialog-backdrop` / alertdialog with its own focus, modal-stack and
 * busy-state wiring. Both now create an instance with `createDeleteConfirm`;
 * what differs between them (where the plan comes from, what Delete sends)
 * stays with the caller, passed in as `onConfirm` / `onClose`.
 *
 * Behaviour carried over unchanged:
 * - built ONCE at module load by the caller and appended to `document.body`
 *   (a sibling of `#app`, never a child: `pushModal` marks `#app` inert and
 *   a dialog inside it would be inert too, found live in WP 4a.8);
 * - `role="alertdialog"` + `aria-modal`, "Keep" (the non-destructive button)
 *   takes focus on open, focus returns to the invoker on close;
 * - Escape and the inert background go through `lib/modal-stack.js`
 *   (`pushModal`/`popModal`), never a listener of its own;
 * - the plan arithmetic markup (`.cflist`/`.cflabel`/`.cftotal`/`.cfsolo`)
 *   is the same for one game and for a selection (`buildPlanNodes`).
 *
 * Plain text via `textContent` only; no innerHTML.
 */

import { formatBytesGB } from "../lib/format.js";
import { pushModal, popModal } from "../lib/modal-stack.js";

const CALCULATING_TEXT = "Calculating what this would free…";

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

function strong(text, extraClass) {
  return el("b", extraClass || "", text);
}

/**
 * The intro sentence as nodes: "Deleting <b>X</b> frees about <b>1 GB</b> of
 * the <b>2 GB</b> it occupies." `many` switches "it occupies" to "they occupy".
 * @param {string} subject game name, or "N games"
 * @param {{freedBytes: number, occupiedBytes: number}} plan
 * @param {{many?: boolean}} [opts]
 * @returns {(string | HTMLElement)[]}
 */
export function buildIntroNodes(subject, plan, { many = false } = {}) {
  return [
    "Deleting ",
    strong(subject),
    " frees about ",
    strong(formatBytesGB(plan.freedBytes) || "0 GB", "num"),
    " of the ",
    strong(formatBytesGB(plan.occupiedBytes) || "0 GB", "num"),
    many ? " they occupy." : " it occupies.",
  ];
}

/**
 * The multiPlan preview under the intro: a "no shared depots" line, or the
 * shared-depot list with a freed/kept total.
 * @param {object} plan `buildMultiPlan` result
 * @param {Map<number, object>} gamesByAppid
 * @param {(appids: number[], gamesByAppid: Map<number, object>) => string} namesFor
 * @returns {HTMLElement[]}
 */
export function buildPlanNodes(plan, gamesByAppid, namesFor) {
  const freedText = formatBytesGB(plan.freedBytes) || "0 GB";
  const occupiedText = formatBytesGB(plan.occupiedBytes) || "0 GB";
  if (plan.sharedRows.length === 0) {
    return [el("p", "cfsolo", `No shared depots — the full ${occupiedText} is freed.`)];
  }

  const out = [
    el("p", "cflabel", `${plan.sharedRows.length} shared depot${plan.sharedRows.length > 1 ? "s" : ""}`),
  ];
  const ul = el("ul", "cflist");
  for (const row of plan.sharedRows) {
    const li = el("li", row.free ? "free" : "keep");
    let why;
    if (row.free) {
      why = row.others.length
        ? `no cached co-owner — ${namesFor(row.others, gamesByAppid)} ${row.others.length > 1 ? "are" : "is"} not cached`
        : "every game mapping this depot is in this selection";
    } else {
      why = `${namesFor(row.holderAppids, gamesByAppid)} still cached`;
    }
    li.append(
      el("span", "did", String(row.depotid)),
      el("span", "mk", row.free ? "freed" : "kept"),
      el("span", "dsz", formatBytesGB(row.sizeBytes) || "—"),
      el("span", "why", why),
    );
    ul.appendChild(li);
  }
  out.push(ul);

  const total = el("p", "cftotal");
  total.append(strong(freedText), " freed · ", strong(formatBytesGB(plan.keptBytes) || "0 GB"), " stays on disk");
  out.push(total);
  return out;
}

/**
 * Build the dialog (appended to `document.body`, hidden) and return its
 * controller.
 * @param {{onConfirm: () => (void | Promise<void>), onClose?: () => void}} handlers
 *   `onConfirm`: the Delete button. `onClose`: runs on every close() (also a
 *   close from the modal stack's Escape), so the caller can drop its pending
 *   plan and invalidate in-flight plan fetches.
 */
export function createDeleteConfirm({ onConfirm, onClose }) {
  const backdrop = el("div", "dialog-backdrop");
  const dialogEl = el("div", "dialog");
  dialogEl.setAttribute("role", "alertdialog");
  dialogEl.setAttribute("aria-modal", "true");
  dialogEl.setAttribute("aria-label", "Confirm deletion");
  const titleEl = el("h3", "", "Delete from cache?");
  const textEl = el("p");
  const noteEl = el("div");
  const row = el("div", "row");
  const keepBtn = el("button", "btn ghost sm", "Keep");
  keepBtn.type = "button";
  const deleteBtn = el("button", "btn danger sm", "Delete");
  deleteBtn.type = "button";
  row.append(keepBtn, deleteBtn);
  dialogEl.append(titleEl, textEl, noteEl, row);
  backdrop.appendChild(dialogEl);
  document.body.appendChild(backdrop);

  /** Element to return focus to on close, captured fresh on every open. */
  let invokerEl = null;

  function isOpen() {
    return backdrop.classList.contains("on");
  }

  /**
   * Show the dialog in its "calculating" state. Calling it while already
   * open (the re-confirm after a 409) only resets the content: focus and
   * the modal stack are left alone.
   * @param {string} [title]
   */
  function open(title) {
    titleEl.textContent = title || "Delete from cache?";
    textEl.textContent = CALCULATING_TEXT;
    noteEl.replaceChildren();
    deleteBtn.disabled = true;
    if (isOpen()) return;
    invokerEl = document.activeElement;
    backdrop.classList.add("on");
    // Makes #app (and the sheet behind) inert; Escape routes through the
    // modal stack's single dispatcher.
    pushModal(backdrop, close);
    keepBtn.focus(); // the non-destructive default gets initial focus
  }

  function close() {
    backdrop.classList.remove("on");
    popModal(backdrop);
    if (invokerEl && typeof invokerEl.focus === "function") invokerEl.focus();
    invokerEl = null;
    if (onClose) onClose();
  }

  /** Replace the intro line: a string, or nodes from `buildIntroNodes`. */
  function setIntro(intro) {
    if (typeof intro === "string") textEl.textContent = intro;
    else {
      textEl.replaceChildren();
      textEl.append(...intro);
    }
  }

  /** Replace the plan area. */
  function setNote(nodes) {
    noteEl.replaceChildren(...nodes);
  }

  /** The plan could not be computed: say so, Delete stays disabled. */
  function showError(message) {
    textEl.textContent = `Could not calculate the delete plan: ${message}`;
    deleteBtn.disabled = true;
  }

  /** Enable or disable Delete once a plan is shown. */
  function setConfirmEnabled(enabled) {
    deleteBtn.disabled = !enabled;
  }

  /** Disable both buttons while a delete is in flight, re-enable after. */
  function setBusy(busy) {
    deleteBtn.disabled = busy;
    keepBtn.disabled = busy;
  }

  /** Keep focus inside the dialog while a plan is fetched again. */
  function focusKeep() {
    keepBtn.focus();
  }

  keepBtn.addEventListener("click", close);
  deleteBtn.addEventListener("click", () => onConfirm());

  return {
    backdrop,
    isOpen,
    open,
    close,
    setIntro,
    setNote,
    showError,
    setConfirmEnabled,
    setBusy,
    focusKeep,
  };
}
