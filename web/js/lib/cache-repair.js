/**
 * "Repair" for a game short only of wrong-size chunk files (WP
 * CACHE-REPAIR-UI-1, web part). Pure decision, wording and flow logic, no
 * DOM, no fetch: `components/repair-flow.js` is the dialog and the request
 * glue, `views/downloads.js` (the "Incomplete" rows) and
 * `components/game-detail-sheet.js` (Maintenance tab and header) place the
 * button; web/tests/cache-repair.test.js pins this module.
 *
 * **The API (api/README.md, `POST /v1/cache/{appid}/repair`, WP
 * CACHE-REPAIR-1).** Dry run by default (no body); `{"execute": true}`
 * deletes the wrong-size chunk files of the app's target manifests and
 * queues a forced prefill that fetches fresh copies. `200` for a dry run or
 * a no-op, `202` once files were removed and the job queued; `404` unknown
 * app / no mappings; `409` an active job of the app or of a co-owner of a
 * damaged depot, or the app's last prefill ended `not_owned` (both also on
 * the dry run); `422` more than `max_files` damaged on execute (the dry run
 * reports `exceeds_cap: true`); `500` cache-root guard.
 *
 * **Who gets the button ({@link showsRepair}).** A row whose OWN figure is
 * known, below 100 and short of no missing chunk file
 * (`cache_missing_chunks === 0`, `isDamagedOnly`), not a Steam tool app, not
 * known as not owned (the server answers 409 for that, so the row says why
 * instead: {@link REPAIR_NOT_OWNED_NOTE}) and without a queued, running or
 * paused prefill of its own (the server answers 409 for that too; the job
 * itself is the thing to wait for).
 *
 * **The flow ({@link reduceRepairFlow}).** Same posture as `gc-flow.js`:
 * EXECUTE is never sent without an explicit confirm after a dry run. The
 * only way into {@link REPAIR_STATE}.EXECUTING is a `confirm` event fired
 * from CONFIRM, and CONFIRM is reached only from a dry-run answer that
 * found damaged files within the cap. Every unrecognised (state, event)
 * pair returns the state unchanged, so a double click can never skip the
 * confirm.
 *
 * Every user-visible string is an exported constant or a small template
 * function here, so the Android twin can copy them 1:1 (the same pattern as
 * `lib/cache-completeness.js` and its `CacheCompletenessWebTwinTest.kt`).
 */

import { isDamagedOnly } from "./cache-completeness.js";
import { isToolApp, isNotOwnedApp } from "./game-status.js";
import { formatBytesGB } from "./format.js";
import { ERROR_KINDS } from "../errors.js";

// ---------------------------------------------------------------------
// Wording
// ---------------------------------------------------------------------

export const REPAIR_LABEL = "Repair";
export const REPAIR_CHECKING_LABEL = "Checking…";
export const REPAIR_HINT =
  "Deletes the chunk files that have the wrong size, then downloads fresh copies. You confirm before anything is deleted.";
/** The row note next to the button: why this row offers Repair, not Verify. */
export const REPAIR_NOTE = "Damaged chunks — Verify can't fix these, Repair can.";
/** A damaged-only row of a game whose last download ended not_owned. */
export const REPAIR_NOT_OWNED_NOTE = "Damaged chunks — can't be repaired: this Steam account doesn't own the game.";

export const REPAIR_CONFIRM_LABEL = "Delete and repair";
export const REPAIR_EXECUTING_LABEL = "Repairing…";
export const REPAIR_CANCEL_LABEL = "Cancel";
export const REPAIR_CLOSE_LABEL = "Close";
export const REPAIR_SHOW_JOB_LABEL = "Show job";
export const REPAIR_DONE_TITLE = "Repair started";
export const REPAIR_NOOP_TITLE = "Nothing was deleted";
export const REPAIR_NOTHING_TEXT = "Nothing to repair — no chunk file with the wrong size was found.";
export const REPAIR_NOOP_TEXT =
  "The damaged files had changed or were gone when the repair ran, so nothing was deleted and no download was queued.";
export const REPAIR_BUSY_TEXT =
  "A download that uses these files is queued, running or paused. Repair once it has finished — nothing was deleted.";
export const REPAIR_NOT_OWNED_TEXT =
  "The last download of this game ended 'not owned', so fresh copies could not be fetched — nothing was deleted.";
export const REPAIR_NETWORK_TEXT = "Could not reach vault-api — nothing was changed.";
export const REPAIR_EXECUTE_UNKNOWN_TEXT =
  "The repair did not answer cleanly — some files may already be deleted. Re-checking Downloads…";
export const REPAIR_FAILED_TEXT = "Could not start the repair.";

/** "Repair — Emberreach": the button's accessible name. */
export function repairAriaLabel(title) {
  return `${REPAIR_LABEL} — ${title}`;
}

/** "Repair Emberreach?" — the confirm dialog's title. */
export function repairConfirmTitle(title) {
  return `${REPAIR_LABEL} ${title}?`;
}

/** "Can't repair Emberreach" — the title when the plan exceeds the cap. */
export function repairBlockedTitle(title) {
  return `Can't repair ${title}`;
}

/** "1 file" / "12 files". */
export function fileCount(n) {
  return `${n} ${n === 1 ? "file" : "files"}`;
}

/**
 * A damaged amount: below 1 GiB in MB (a handful of wrong-size chunks is
 * typically a few MB, which "0.0 GB" would hide), else the app's GB form.
 * @param {unknown} bytes
 * @returns {string}
 */
export function repairSizeText(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return formatBytesGB(bytes);
}

/** The confirm's main sentence from a dry-run report. */
export function repairPlanText(report) {
  const n = report.damaged_chunks;
  return (
    `${fileCount(n)} (${repairSizeText(report.damaged_bytes)}) in the cache have the wrong size. ` +
    "Repair deletes them, then queues a forced download that fetches fresh copies."
  );
}

/** One depot line of the confirm: "Depot 2010081 · 3 files · 4.2 MB". */
export function repairDepotLine(depot) {
  return `Depot ${depot.depotid} · ${fileCount(depot.damaged_chunks)} · ${repairSizeText(depot.damaged_bytes)}`;
}

/** The note for depots whose target manifest could not be read, or "". */
export function unreadableDepotsText(report) {
  const ids = Array.isArray(report?.unreadable_depots) ? report.unreadable_depots : [];
  if (!ids.length) return "";
  const which = ids.join(", ");
  return ids.length === 1
    ? `Depot ${which} could not be read and is left out.`
    : `Depots ${which} could not be read and are left out.`;
}

/** Why a dry run that exceeds the cap is refused (execute would answer 422). */
export function repairBlockedText(report) {
  return (
    `${fileCount(report.damaged_chunks)} have the wrong size — more than the repair limit of ${report.max_files}. ` +
    "That many mismatches point at a wrong target manifest or a broken cache. Nothing was deleted."
  );
}

/** The result sentence after an execute that deleted files. */
export function repairDoneText(report) {
  const removed = `Deleted ${fileCount(report.removed_chunks)} (${repairSizeText(report.removed_bytes)}).`;
  const skipped = skippedTotal(report);
  const skippedText = skipped ? ` ${fileCount(skipped)} changed meanwhile and stayed.` : "";
  if (!Number.isInteger(report.job_id)) return removed + skippedText;
  const job = report.job_deduplicated
    ? ` The already queued job #${report.job_id} fetches fresh copies.`
    : ` Job #${report.job_id} fetches fresh copies.`;
  return removed + skippedText + job;
}

function skippedTotal(report) {
  const s = report && typeof report.skipped === "object" && report.skipped ? report.skipped : {};
  return Object.values(s).reduce((sum, v) => sum + (Number.isInteger(v) && v > 0 ? v : 0), 0);
}

// ---------------------------------------------------------------------
// Visibility
// ---------------------------------------------------------------------

const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "paused"]);

/** True while `jobs` holds a queued, running or paused prefill of `appid`. */
function hasActivePrefill(appid, jobs) {
  return (Array.isArray(jobs) ? jobs : []).some(
    (j) => j && j.type === "prefill" && j.appid === appid && ACTIVE_JOB_STATUSES.has(j.status),
  );
}

/**
 * Whether a row offers "Repair" (module header).
 * @param {object | null | undefined} row a `GET /v1/games` row (the game's own)
 * @param {object[] | null | undefined} [jobs] `GET /v1/jobs` rows
 * @returns {boolean}
 */
export function showsRepair(row, jobs) {
  if (!row || isToolApp(row) || isNotOwnedApp(row)) return false;
  if (!isDamagedOnly(row)) return false;
  return !hasActivePrefill(row.appid, jobs);
}

/** The note a damaged-only row shows: {@link REPAIR_NOTE} next to the
 * button, {@link REPAIR_NOT_OWNED_NOTE} when the game is known as not
 * owned, "" for every other row. */
export function repairNoteFor(row) {
  if (!row || isToolApp(row) || !isDamagedOnly(row)) return "";
  return isNotOwnedApp(row) ? REPAIR_NOT_OWNED_NOTE : REPAIR_NOTE;
}

// ---------------------------------------------------------------------
// Flow
// ---------------------------------------------------------------------

export const REPAIR_STATE = Object.freeze({
  IDLE: "idle",
  CHECKING: "checking",
  CONFIRM: "confirm",
  BLOCKED: "blocked",
  EXECUTING: "executing",
  DONE: "done",
});

export const REPAIR_EVENT = Object.freeze({
  START: "start",
  DRY_RUN_OK: "dry_run_ok",
  DRY_RUN_FAILED: "dry_run_failed",
  CONFIRM: "confirm",
  EXECUTE_OK: "execute_ok",
  EXECUTE_FAILED: "execute_failed",
  CLOSE: "close",
  RESET: "reset",
});

export function idleRepairState() {
  return { kind: REPAIR_STATE.IDLE };
}

/**
 * True when an answer event belongs to the flow in `state`: every request
 * answer carries the `token` of the flow that sent it (WP CACHE-REPAIR-UI-1
 * review fix). A late answer of a closed flow — the sheet was closed and
 * another game's repair started meanwhile — must never land in the newer
 * flow's dialog (wrong title or counts, or a cancel with the wrong toast).
 */
function ownsAnswer(state, event) {
  return state.token !== undefined && event.token === state.token;
}

/**
 * @param {object} state a REPAIR_STATE-tagged object
 * @param {object} event `{type, ...}`: START `{appid, title, token}`;
 *   DRY_RUN_OK `{token, report}`, DRY_RUN_FAILED `{token}`, EXECUTE_OK
 *   `{token, report}`, EXECUTE_FAILED `{token, message}` (ignored unless
 *   `token` is the current flow's); CONFIRM, CLOSE, RESET
 * @returns {object} the next state (the same reference for a no-op)
 */
export function reduceRepairFlow(state, event) {
  switch (event.type) {
    case REPAIR_EVENT.START:
      return state.kind === REPAIR_STATE.IDLE
        ? { kind: REPAIR_STATE.CHECKING, appid: event.appid, title: event.title, token: event.token }
        : state;

    case REPAIR_EVENT.DRY_RUN_OK: {
      if (state.kind !== REPAIR_STATE.CHECKING || !ownsAnswer(state, event)) return state;
      const report = event.report;
      // Nothing damaged (any more): no dialog, the caller says so.
      if (!report || !(report.damaged_chunks > 0)) return { kind: REPAIR_STATE.IDLE };
      const base = { appid: state.appid, title: state.title, token: state.token, report };
      if (report.exceeds_cap) return { kind: REPAIR_STATE.BLOCKED, ...base };
      return { kind: REPAIR_STATE.CONFIRM, ...base, error: null };
    }

    case REPAIR_EVENT.DRY_RUN_FAILED:
      return state.kind === REPAIR_STATE.CHECKING && ownsAnswer(state, event) ? { kind: REPAIR_STATE.IDLE } : state;

    // THE pin: the only way to EXECUTING, and only from CONFIRM.
    case REPAIR_EVENT.CONFIRM:
      return state.kind === REPAIR_STATE.CONFIRM
        ? { kind: REPAIR_STATE.EXECUTING, appid: state.appid, title: state.title, token: state.token, report: state.report }
        : state;

    case REPAIR_EVENT.EXECUTE_OK:
      return state.kind === REPAIR_STATE.EXECUTING && ownsAnswer(state, event)
        ? { kind: REPAIR_STATE.DONE, appid: state.appid, title: state.title, token: state.token, report: event.report }
        : state;

    case REPAIR_EVENT.EXECUTE_FAILED:
      return state.kind === REPAIR_STATE.EXECUTING && ownsAnswer(state, event)
        ? {
            kind: REPAIR_STATE.CONFIRM,
            appid: state.appid,
            title: state.title,
            token: state.token,
            report: state.report,
            error: event.message,
          }
        : state;

    // Cancel / Close / Escape: never while the execute request is in flight.
    case REPAIR_EVENT.CLOSE:
      return state.kind === REPAIR_STATE.CONFIRM || state.kind === REPAIR_STATE.BLOCKED || state.kind === REPAIR_STATE.DONE
        ? { kind: REPAIR_STATE.IDLE }
        : state;

    case REPAIR_EVENT.RESET:
      return { kind: REPAIR_STATE.IDLE };

    default:
      return state;
  }
}

// ---------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------

function detailOf(err) {
  return err && typeof err.detail === "string" && err.detail ? err.detail : null;
}

/**
 * How to report a failed repair request.
 *
 * - network: nothing reached the server on a dry run; on an execute the
 *   outcome is unknown, so it says so and asks for a re-read.
 * - 409: the server's two reasons (api `routers/cache.py`), told apart by
 *   the `not_owned` token its detail names; both mean nothing was deleted.
 * - 5xx on execute: the deletion may have started, so a re-read.
 * - everything else (404, 422, a 5xx dry run, 401): the server's own detail.
 * @param {unknown} err an ApiError (or anything thrown)
 * @param {{execute: boolean}} opts which request failed
 * @returns {{message: string, refresh: boolean}}
 */
export function describeRepairError(err, { execute }) {
  const kind = err && err.kind;
  if (kind === ERROR_KINDS.NETWORK) {
    return execute ? { message: REPAIR_EXECUTE_UNKNOWN_TEXT, refresh: true } : { message: REPAIR_NETWORK_TEXT, refresh: false };
  }
  if (err && err.status === 409) {
    const detail = detailOf(err) || "";
    return { message: /not_owned/.test(detail) ? REPAIR_NOT_OWNED_TEXT : REPAIR_BUSY_TEXT, refresh: true };
  }
  if (kind === ERROR_KINDS.SERVER && execute) return { message: REPAIR_EXECUTE_UNKNOWN_TEXT, refresh: true };
  return { message: detailOf(err) || (err && err.message) || REPAIR_FAILED_TEXT, refresh: false };
}
