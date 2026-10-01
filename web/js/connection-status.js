/**
 * App-wide "connection lost" flag (WP WEB-FIX-2).
 *
 * Written by `components/connection-banner.js` (the one place that decides
 * it, via `lib/connection-watch.js`), read by views that must not offer
 * live-looking controls while the vault is unreachable (`views/downloads.js`
 * disables its job-control buttons). Listeners fire only on a real change.
 */

let lost = false;
const listeners = new Set();

export function isConnectionLost() {
  return lost;
}

/** @param {boolean} value */
export function setConnectionLost(value) {
  const next = !!value;
  if (next === lost) return;
  lost = next;
  for (const cb of listeners) cb(lost);
}

/** @param {(lost: boolean) => void} cb @returns {() => void} unsubscribe */
export function onConnectionChange(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
