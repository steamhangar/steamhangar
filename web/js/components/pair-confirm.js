/**
 * "Replace this browser's API key?" confirm for a browser pairing link
 * (WP PAIR-1; flow in lib/pair-intake.js).
 *
 * Shown only when a `#pair=` link arrives in a browser that already stores a
 * DIFFERENT key: the link may belong to another hangar, so the stored key is
 * never overwritten silently. Same alertdialog markup and modal-stack
 * wiring as the PCs sheet's Remove confirm (components/clients-sheet.js):
 * focus starts on the non-destructive "Keep current key", Escape and Keep
 * resolve `false`, only "Replace" resolves `true`. The dialog names no key.
 *
 * The DOM is built on first use, so a page load without a pairing link
 * creates nothing.
 */

import { pushModal, popModal } from "../lib/modal-stack.js";

export const PAIR_CONFIRM_TITLE = "Replace this browser's API key?";
export const PAIR_CONFIRM_TEXT =
  "This browser is already connected with a different key. The link's key may belong to another hangar. If you replace it, this browser uses the link's key from now on (after a check against the server).";

let els = null;
let pending = null; // {resolve, invokerEl}

function build() {
  const backdrop = document.createElement("div");
  backdrop.className = "dialog-backdrop";
  const dialog = document.createElement("div");
  dialog.className = "dialog";
  dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "pair-confirm-title");
  dialog.setAttribute("aria-describedby", "pair-confirm-text");
  dialog.dataset.role = "pair-confirm";
  const title = document.createElement("h3");
  title.id = "pair-confirm-title";
  title.textContent = PAIR_CONFIRM_TITLE;
  const text = document.createElement("p");
  text.id = "pair-confirm-text";
  text.textContent = PAIR_CONFIRM_TEXT;
  const row = document.createElement("div");
  row.className = "row";
  const keep = document.createElement("button");
  keep.type = "button";
  keep.className = "btn ghost sm";
  keep.textContent = "Keep current key";
  keep.dataset.role = "pair-keep";
  const replace = document.createElement("button");
  replace.type = "button";
  replace.className = "btn danger sm";
  replace.textContent = "Replace";
  replace.dataset.role = "pair-replace";
  row.append(keep, replace);
  dialog.append(title, text, row);
  backdrop.appendChild(dialog);
  document.body.appendChild(backdrop);
  keep.addEventListener("click", () => settle(false));
  replace.addEventListener("click", () => settle(true));
  return { backdrop, keep };
}

function settle(answer) {
  if (!pending || !els) return;
  const { resolve, invokerEl } = pending;
  pending = null;
  els.backdrop.classList.remove("on");
  popModal(els.backdrop);
  if (invokerEl && typeof invokerEl.focus === "function") invokerEl.focus();
  resolve(answer);
}

/** Ask; resolves true only for "Replace". A second call while one is open
 * gets the same answer. */
export function confirmPairReplace() {
  if (!els) els = build();
  if (pending) return pending.promise;
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  pending = { resolve, promise, invokerEl: document.activeElement };
  els.backdrop.classList.add("on");
  pushModal(els.backdrop, () => settle(false));
  els.keep.focus();
  return promise;
}
