/**
 * "Connection lost / data may be stale" decision logic (WP WEB-FIX-2).
 *
 * Pure. Every polling-store subscriber drops `{error}` payloads on purpose
 * (a transient failure must not blank a working screen), so before this
 * module a vault-api restart or a dropped network left the UI showing a
 * frozen snapshot with live-looking controls and no word about it. This
 * reducer turns the four resource streams (`jobs`/`games`/`clients`/
 * `cache`) into one boolean: is the connection lost right now.
 *
 * Rule (the threshold choice, documented here and in web/tests/README.md):
 *   - Only NETWORK and SERVER errors count. AUTH is the reconnect dialog's
 *     job (`components/auth-recovery.js`) and must not be double-signalled;
 *     NOT_FOUND/VALIDATION/UNKNOWN mean the server answered. All of those
 *     are ignored entirely: they neither start, extend nor end a streak.
 *   - A streak starts at the first counting failure after a success. The
 *     indicator shows on a LATER counting failure that arrives at least
 *     `LOST_AFTER_MS` (20 s) after the streak started, with no successful
 *     poll of ANY resource in between. So it needs at least two failures,
 *     and they must span 20 s. A pure count would not do: backoff.js
 *     retries after ~1 s, so "two consecutive failures" is one blip. 20 s
 *     sits above a fast vault-api restart and below the 30 s backoff cap,
 *     so a real outage shows within about 20-37 s of the first failed poll
 *     (one 16 s backoff step past the threshold, +/-20 % jitter; measured
 *     20.0-35.5 s in a 200k-run simulation of backoff.js).
 *   - Any successful poll of any resource ends the streak and clears the
 *     indicator: the vault answered, so the next ticks bring fresh data.
 *     Consequence: one endpoint failing alone while the others succeed
 *     never shows it (that is not a lost connection).
 *
 * `lastSuccessMs` is the time of the last successful poll seen here; the
 * store keeps no such stamp, so the reducer records it from the same
 * payloads. `null` until the first success (a page opened while the vault
 * was already down has no "last update" to name).
 *
 * Covered in web/tests/connection-banner.test.js.
 */

import { ERROR_KINDS } from "../errors.js";

export const LOST_AFTER_MS = 20000;

export const CONNECTION_RESTORED_TEXT = "Connection to the vault restored.";

export const OFFLINE_CONTROL_TITLE = "Not available while the connection to the vault is lost.";

/** @returns {{lost: boolean, streakStartMs: number | null, lastSuccessMs: number | null}} */
export function initialConnectionState() {
  return { lost: false, streakStartMs: null, lastSuccessMs: null };
}

/** @param {any} err a store `{error}` payload's error */
export function isConnectionFailure(err) {
  return !!err && (err.kind === ERROR_KINDS.NETWORK || err.kind === ERROR_KINDS.SERVER);
}

/**
 * @param {ReturnType<typeof initialConnectionState>} state
 * @param {any} payload a store resource payload (`{items}`, `{item}` or `{error}`)
 * @param {number} nowMs
 */
export function nextConnectionState(state, payload, nowMs) {
  if (!payload) return state;
  if (payload.error) {
    if (!isConnectionFailure(payload.error)) return state;
    if (state.streakStartMs === null) return { ...state, streakStartMs: nowMs };
    if (state.lost || nowMs - state.streakStartMs < LOST_AFTER_MS) return state;
    return { ...state, lost: true };
  }
  return { lost: false, streakStartMs: null, lastSuccessMs: nowMs };
}

/** Local wall-clock "HH:MM" (24 h, zero-padded) — locale-independent. */
export function formatClockHHMM(ms) {
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** @param {number | null} lastSuccessMs */
export function connectionLostText(lastSuccessMs) {
  const when = typeof lastSuccessMs === "number" ? ` (last update ${formatClockHHMM(lastSuccessMs)})` : "";
  return `Lost connection to the vault — showing the last data received${when}. Retrying…`;
}
