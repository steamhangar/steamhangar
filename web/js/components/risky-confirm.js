/**
 * "Save settings that can break downloads?" confirm (WP D16-WEB, D16b-2).
 *
 * Opened by the Settings save bar when the batch sets or clears a key the
 * API marks `risky` (or when the server answers 428 naming such keys). It
 * lists every risky key with its old and new value and what can break
 * (lib/settings-layout.js `riskyItems`). Same alertdialog markup and
 * modal-stack wiring as components/pair-confirm.js: focus starts on the
 * non-destructive "Cancel", Escape and Cancel resolve `false`, only
 * "Save anyway" resolves `true`. Only on `true` does the view send the
 * PATCH with `?confirm_risky=true`.
 *
 * The DOM is built on first use.
 */

import { pushModal, popModal } from "../lib/modal-stack.js";

export const RISKY_CONFIRM_TITLE = "Save settings that can break downloads?";
export const RISKY_CONFIRM_TEXT = "These changes affect how vault-core reaches Steam. A wrong value can stop downloads for every PC until it is fixed.";

let els = null;
let pending = null; // {resolve, promise, invokerEl}

function build() {
  const backdrop = document.createElement("div");
  backdrop.className = "dialog-backdrop";
  const dialog = document.createElement("div");
  dialog.className = "dialog";
  dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "risky-confirm-title");
  dialog.setAttribute("aria-describedby", "risky-confirm-text");
  dialog.dataset.role = "risky-confirm";
  const title = document.createElement("h3");
  title.id = "risky-confirm-title";
  title.textContent = RISKY_CONFIRM_TITLE;
  const text = document.createElement("p");
  text.id = "risky-confirm-text";
  text.textContent = RISKY_CONFIRM_TEXT;
  const list = document.createElement("ul");
  list.className = "bullets risky-list";
  list.dataset.role = "risky-list";
  const row = document.createElement("div");
  row.className = "row";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn ghost sm";
  cancel.textContent = "Cancel";
  cancel.dataset.role = "risky-cancel";
  const ok = document.createElement("button");
  ok.type = "button";
  ok.className = "btn danger sm";
  ok.textContent = "Save anyway";
  ok.dataset.role = "risky-ok";
  row.append(cancel, ok);
  dialog.append(title, text, list, row);
  backdrop.appendChild(dialog);
  document.body.appendChild(backdrop);
  cancel.addEventListener("click", () => settle(false));
  ok.addEventListener("click", () => settle(true));
  return { backdrop, cancel, list };
}

function settle(answer) {
  if (!pending || !els) return;
  const { resolve, invokerEl } = pending;
  pending = null;
  els.backdrop.classList.remove("on");
  popModal(els.backdrop);
  if (invokerEl && typeof invokerEl.focus === "function" && invokerEl.isConnected !== false) invokerEl.focus();
  resolve(answer);
}

/**
 * Ask; resolves true only for "Save anyway". A second call while one is
 * open gets the same answer.
 * @param {Array<{label: string, from: string, to: string, risk: string}>} items
 * @returns {Promise<boolean>}
 */
export function confirmRiskyChange(items) {
  if (!els) els = build();
  if (pending) return pending.promise;
  els.list.replaceChildren();
  for (const item of items) {
    const li = document.createElement("li");
    const head = document.createElement("b");
    head.textContent = `${item.label}: ${item.from} → ${item.to}.`;
    li.append(head, document.createTextNode(` ${item.risk}`));
    els.list.appendChild(li);
  }
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  pending = { resolve, promise, invokerEl: document.activeElement };
  els.backdrop.classList.add("on");
  pushModal(els.backdrop, () => settle(false));
  els.cancel.focus();
  return promise;
}

/** Test seam: whether the dialog is open. */
export function isRiskyConfirmOpen() {
  return pending !== null;
}
