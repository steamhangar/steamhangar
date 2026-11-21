/**
 * Toast component (WP 4a.1 scaffold).
 *
 * Ports the mockup's toast: a small message strip near the bottom of the
 * viewport, auto-dismissing after a fixed delay
 * (docs/design/vault-app-mockup-NOTES.md round 7). This work package only
 * wires the DOM node and the show/hide mechanics against the `#toast`
 * markup in index.html; later work packages (4a.3+) call `showToast()`
 * for real events (download queued, delete confirmed, ...).
 */

let toastEl = null;
let textEl = null;
let hideTimer = null;

/** Bind to the `#toast` / `#toast-text` nodes. Call once at startup. */
export function initToast() {
  toastEl = document.getElementById("toast");
  textEl = document.getElementById("toast-text");
}

/**
 * Show a toast message.
 * @param {string} message
 * @param {{warn?: boolean, duration?: number}} [options]
 */
export function showToast(message, { warn = false, duration = 2600 } = {}) {
  if (!toastEl || !textEl) return;
  textEl.textContent = message;
  toastEl.classList.toggle("warn", !!warn);
  toastEl.classList.add("on");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => toastEl.classList.remove("on"), duration);
}
