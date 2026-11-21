/**
 * Client priority in the Downloads view (WP CAP-503-FIX-1b; the API side is
 * CAP-503-FIX-1a, api/README.md "Client priority over the prefill runner").
 *
 * vault-api holds the prefill runner while a device loads through the cache
 * and lets it carry on one idle gap (120 s) after the last sighting.
 * `GET /v1/client-priority` says whether it holds right now, for which
 * devices (`clients`, names from the Clients page) and until when
 * (`resume_at`); the job rows say why a job is paused (`pause_reason`:
 * `"client"` or `"manual"`, null = not paused) and whether the operator
 * resumed it during a hold (`client_override`, operator decision 2026-11-21
 * Weg B: it runs alongside the device).
 *
 * The texts here, one per state:
 * - a job paused for a device (`pause_reason: "client"`): which device(s)
 *   and the expected resume time ({@link clientHoldNote});
 * - every queued job while the runner is held (queued GC jobs wait too,
 *   operator decision 2026-11-21 Weg A): one note over the queue
 *   ({@link queueHoldNote});
 * - a job the operator resumed during the hold: "Runs alongside <device> by
 *   your choice." ({@link clientOverrideNote});
 * - `restored: true` (vault-api restarted with jobs still paused for a
 *   device, no device seen since): `clients` is empty and `last_client_at`
 *   is the restart, so the texts never name a device or claim a sighting;
 * - a manual pause keeps the view's existing note (these return `null`).
 *
 * Pure: no DOM, no fetch. The Android app mirrors every literal of
 * {@link WORDING} (`ui/downloads/logic/ClientPriority.kt`, pinned by
 * `ClientPriorityWebTwinTest`): change one, change both.
 */
import { formatClockHHMM } from "./connection-watch.js";

/** The hold poll's cadence while a job is queued, running or paused; the
 * controller looks every 5 s, so a faster poll would show nothing new. */
export const CLIENT_PRIORITY_POLL_MS = 5000;

export const WORDING = Object.freeze({
  heldSeen:
    "Paused to give {devices} the full speed through the cache. Resumes by itself at about {time} unless a device keeps loading. Resume runs it now, alongside {devices}.",
  heldRestored:
    "Paused for device priority before vault-api restarted. No device has been seen loading since; resumes by itself at about {time} unless one starts.",
  heldPending: "Paused for device priority. Resumes by itself once no device is loading.",
  pausingClient:
    "Pausing so a device loading through the cache gets the full speed. It resumes by itself, from the cache, not from zero.",
  overrideSeen: "Runs alongside {devices} by your choice.",
  overrideAny: "Runs alongside any loading device by your choice.",
  queueSeen:
    "Queued downloads wait to give {devices} the full speed through the cache. They carry on at about {time} unless a device keeps loading.",
  queueRestored:
    "Queued downloads wait: device priority was on before vault-api restarted, and no device has been seen loading since. They carry on at about {time} unless one starts.",
  queuePending: "Queued downloads wait for device priority.",
});

/** Replace each `{key}` in `template` with `values[key]`. */
function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (whole, key) => (key in values ? String(values[key]) : whole));
}

/**
 * Device names as one phrase: "A", "A and B", "A, B and C".
 * @param {string[]} names
 * @returns {string}
 */
export function joinDevices(names) {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** `true` only when the answer says the runner is held right now. */
export function isHolding(cp) {
  return !!cp && cp.holding === true;
}

/**
 * The devices of the last sighting (the Clients page name, else the
 * address), or `null` when there is none to name: a restored hold, or no
 * device in the answer.
 * @param {object | null | undefined} cp a `GET /v1/client-priority` answer
 * @returns {string | null}
 */
export function holdDevices(cp) {
  if (!isHolding(cp) || cp.restored === true || !Array.isArray(cp.clients)) return null;
  const names = cp.clients
    .map((c) => (c && typeof c.name === "string" && c.name.trim() ? c.name.trim() : c && typeof c.addr === "string" ? c.addr : ""))
    .filter((n) => n);
  return names.length ? joinDevices(names) : null;
}

/**
 * The expected resume time as local "HH:MM" (`resume_at`, the last sighting
 * plus the idle gap), or `null` when the answer has none.
 * @param {object | null | undefined} cp
 * @returns {string | null}
 */
export function holdResumeClock(cp) {
  if (!isHolding(cp) || typeof cp.resume_at !== "string") return null;
  const ms = Date.parse(cp.resume_at);
  return Number.isNaN(ms) ? null : formatClockHHMM(ms);
}

/**
 * The note of a job paused for a device, or `null` for any other job (a
 * manual pause keeps the view's own note). Without a current hold (the
 * answer not in yet, or the hold just ended and the job is about to be
 * resumed) the note names no device and no time.
 * @param {object} job a `GET /v1/jobs` row
 * @param {object | null | undefined} cp
 * @returns {string | null}
 */
export function clientHoldNote(job, cp) {
  if (!job || job.status !== "paused" || job.pause_reason !== "client") return null;
  const time = holdResumeClock(cp);
  if (time === null) return WORDING.heldPending;
  if (cp.restored === true) return fill(WORDING.heldRestored, { time });
  const devices = holdDevices(cp);
  return devices ? fill(WORDING.heldSeen, { devices, time }) : WORDING.heldPending;
}

/**
 * The stop note of a running job vault-api is pausing for a device
 * (`stop_request: "pause"` with `pause_reason: "client"`), else `null`.
 * @param {object} job
 * @returns {string | null}
 */
export function clientPausingNote(job) {
  if (!job || job.status !== "running" || job.stop_request !== "pause" || job.pause_reason !== "client") return null;
  return WORDING.pausingClient;
}

/**
 * The note of a job the operator resumed during a hold (`client_override`),
 * while the runner is held; `null` otherwise (no hold: nothing runs
 * alongside anything).
 * @param {object} job
 * @param {object | null | undefined} cp
 * @returns {string | null}
 */
export function clientOverrideNote(job, cp) {
  if (!job || job.client_override !== true || !isHolding(cp)) return null;
  if (job.status !== "running" && job.status !== "queued") return null;
  const devices = holdDevices(cp);
  return devices ? fill(WORDING.overrideSeen, { devices }) : WORDING.overrideAny;
}

/**
 * The note over the queue while the runner is held and at least one queued
 * job waits (a job with `client_override` does not wait), else `null`.
 * @param {object[]} queued the queued jobs
 * @param {object | null | undefined} cp
 * @returns {string | null}
 */
export function queueHoldNote(queued, cp) {
  if (!isHolding(cp) || !Array.isArray(queued)) return null;
  if (!queued.some((j) => j && j.client_override !== true)) return null;
  const time = holdResumeClock(cp);
  if (time === null) return WORDING.queuePending;
  if (cp.restored === true) return fill(WORDING.queueRestored, { time });
  const devices = holdDevices(cp);
  return devices ? fill(WORDING.queueSeen, { devices, time }) : WORDING.queuePending;
}
