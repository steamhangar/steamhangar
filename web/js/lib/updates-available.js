/**
 * "Updates available" in the Downloads view (WP UPD-6, user feedback on
 * prod rc15: "Checked 14 games · 7 updates available", but the 7 were hard
 * to find). Pure decision and wording logic, no DOM, no fetch —
 * `views/downloads.js` is the glue, web/tests/updates-available.test.js the
 * pins.
 *
 * **Which games.** Every `GET /v1/games` row with `update_available ===
 * true` (WP UPD-1: a stale oracle verdict on an app with cache content)
 * that has no prefill job queued, running or paused right now — once a job
 * exists the game is in the normal queue above and leaves this list; once
 * the job finishes the server clears the flag. Raw rows, not Library
 * groups: a flagged content package is its own prefill target, so it is
 * its own row (named "<parent> · content <appid>" by the view when it has
 * no name). Steam tool apps never appear (`POST /v1/prefill` answers 422
 * for them, so there is nothing honest to offer).
 *
 * **Update now / Update all** send `POST /v1/prefill` with the listed app
 * ids — the same enqueue `POST /v1/prefill/cached` ("Check & update")
 * uses (api/vault_api/routers/jobs.py: one `enqueue_prefill`, same dedupe).
 * There is no force flag on that route: whether a run passes `--force` is
 * decided by the app's own `needs_force` at claim time, so an update is a
 * non-forced, changed-chunks-only run unless a deletion or GC touched the
 * app's depots — the toast says so, as "Check & update" does.
 *
 * **Schedule note.** From `GET /v1/schedule` (read once per view mount):
 * with a window (`enabled`) the sweep downloads automatically in it — for
 * every cached game when `sweep_include_cached` is on, otherwise only for
 * games installed on a PC that reports to the vault; without a window
 * nothing downloads on its own. The window is the server's local time, as
 * configured (`VAULT_SCHEDULE_WINDOW`, "HH:MM-HH:MM").
 */

import { isToolApp } from "./game-status.js";
import { partitionCachedPrefillOutcome, countForcedCachedGames } from "./cached-prefill-outcome.js";
import { ERROR_KINDS } from "../errors.js";

export const UPDATES_HEADING = "Updates available";
export const UPDATE_NOW_LABEL = "Update now";
export const UPDATE_ALL_LABEL = "Update all";
export const UPDATING_ALL_LABEL = "Starting…";
export const MANUAL_ONLY_NOTE = "Manual only — no schedule window is set.";

/** Job statuses that put an app into the normal queue (Active / Paused /
 * Queue sections) and therefore out of this list. */
const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "paused"]);

/**
 * The flagged games without an active prefill job, sorted by display name
 * (case-insensitive), then appid.
 * @param {object[] | null | undefined} games `GET /v1/games` rows
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` rows
 * @param {(game: object) => string} [titleOf] display name, for sorting
 * @returns {object[]}
 */
export function pendingUpdates(games, jobs, titleOf = (g) => String(g.name ?? "")) {
  const busy = new Set(
    (Array.isArray(jobs) ? jobs : [])
      .filter((j) => j && j.type === "prefill" && ACTIVE_JOB_STATUSES.has(j.status))
      .map((j) => j.appid),
  );
  const rows = (Array.isArray(games) ? games : []).filter(
    (g) => g && g.update_available === true && !isToolApp(g) && !busy.has(g.appid),
  );
  return rows
    .map((g) => ({ g, t: titleOf(g).toLowerCase() }))
    .sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : a.g.appid - b.g.appid))
    .map(({ g }) => g);
}

/** "Updates available (N)". */
export function updatesHeadingText(count) {
  return `${UPDATES_HEADING} (${count})`;
}

/** "Update all 7 games" — the button's accessible name. */
export function updateAllAriaLabel(count) {
  return `${UPDATE_ALL_LABEL} ${count} ${count === 1 ? "game" : "games"}`;
}

/** "Update now — Emberreach". */
export function updateNowAriaLabel(title) {
  return `${UPDATE_NOW_LABEL} — ${title}`;
}

/**
 * "22:00-06:00" -> "22:00–06:00" (en dash). Anything that is not two
 * HH:MM times is returned trimmed as configured, never reinterpreted.
 * @param {string} raw
 * @returns {string}
 */
export function formatWindow(raw) {
  const text = String(raw).trim();
  const m = /^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$/.exec(text);
  return m ? `${m[1]}–${m[2]}` : text;
}

/**
 * The note under the heading. `null` while the schedule is not known (not
 * read yet, or the read failed): no claim either way.
 * @param {{enabled?: unknown, window?: unknown, sweep_include_cached?: unknown} | null | undefined} schedule
 * @returns {string | null}
 */
export function scheduleNote(schedule) {
  if (!schedule || typeof schedule !== "object") return null;
  const hasWindow = schedule.enabled === true && typeof schedule.window === "string" && schedule.window.trim();
  if (!hasWindow) return MANUAL_ONLY_NOTE;
  const window = formatWindow(schedule.window);
  if (schedule.sweep_include_cached === false) {
    return (
      `Games installed on a PC download automatically in the schedule window ${window}; ` +
      "the others only when you start them here."
    );
  }
  return `Downloads automatically in the schedule window ${window}.`;
}

/**
 * Toast for a successful `POST /v1/prefill` from this section.
 * @param {object[] | null | undefined} refs `PrefillJobRef[]`
 * @param {object[] | null | undefined} games `GET /v1/games` snapshot (forced-run note)
 * @returns {{message: string, warn: boolean}}
 */
export function summarizeUpdateStart(refs, games) {
  const p = partitionCachedPrefillOutcome(refs);
  if (p.total === 0) return { message: "Nothing to update.", warn: false };
  const parts = [];
  if (p.queued.length) parts.push(`${p.queued.length} queued for update`);
  if (p.alreadyQueued.length) parts.push(`${p.alreadyQueued.length} already queued`);
  if (p.alreadyRunning.length) parts.push(`${p.alreadyRunning.length} already in progress`);
  if (p.alreadyPaused.length) {
    parts.push(`${p.alreadyPaused.length} paused — resume or cancel ${p.alreadyPaused.length > 1 ? "them" : "it"} first`);
  }
  let message = parts.join(" · ");
  if (p.queued.length > 0) {
    const forced = countForcedCachedGames(p.queued, games);
    if (forced > 0) message += ` (${forced} forced — those run full, disk-speed re-checks and may take longer)`;
  }
  return { message: message.charAt(0).toUpperCase() + message.slice(1), warn: p.alreadyPaused.length > 0 };
}

/**
 * How to report a failed `POST /v1/prefill` from this section. A 5xx can
 * leave the first apps already queued (one transaction per app), so it
 * asks for a re-read instead of claiming nothing happened.
 * @returns {{message: string, warn: true, refresh: boolean}}
 */
export function describeUpdateStartError(err) {
  if (err && err.kind === ERROR_KINDS.SERVER) {
    return {
      message: "The server had trouble partway through — some updates may already be queued. Re-checking Downloads…",
      warn: true,
      refresh: true,
    };
  }
  const detail = err && typeof err.detail === "string" && err.detail ? err.detail : null;
  return { message: detail || (err && err.message) || "Could not start the update.", warn: true, refresh: false };
}

/** A string that changes whenever the list's rendered content would: the
 * view rebuilds the section only then (a focused button keeps its focus
 * across unrelated poll ticks). */
export function updatesListKey(rows, titleOf, sizeOf) {
  return rows.map((g) => `${g.appid}|${titleOf(g)}|${sizeOf(g) ?? ""}`).join("\n");
}
