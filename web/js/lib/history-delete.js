/**
 * "Delete history" (WP D15-NAMES): the two variants and the dialog's
 * wording. Pure: no DOM, no fetch. Unit-tested in
 * web/tests/history-delete.test.js.
 *
 * What the server deletes is defined in api/vault_api/history.py: finished
 * jobs (done, failed, cancelled) and the traffic statistics behind the
 * Downloads header, its graph and the Clients page numbers. Cached games,
 * running/queued/paused jobs, PCs and client names are never touched. The
 * texts below say exactly that and nothing more.
 */

import { ERROR_KINDS } from "../errors.js";

export const SCOPE_OLDER = "older-than-30d";
export const SCOPE_ALL = "all";

/** Dialog options, safest first (and the default). */
export const HISTORY_DELETE_OPTIONS = Object.freeze([
  Object.freeze({
    value: SCOPE_OLDER,
    label: "Older than 30 days",
    desc: "Finished downloads and traffic statistics from before the last 30 days.",
  }),
  Object.freeze({
    value: SCOPE_ALL,
    label: "Everything",
    desc: "All finished downloads and all traffic statistics. Speed, graph and client figures start again from zero.",
  }),
]);

export const HISTORY_DELETE_TITLE = "Delete history?";

export const HISTORY_DELETE_WHAT =
  "Deletes finished downloads (done, failed, cancelled) with their logs, and the traffic statistics behind the speed figures, the graph and the numbers on the Clients page.";

export const HISTORY_DELETE_KEEPS =
  "Kept: cached games, running, queued and paused downloads, PCs and client names. This cannot be undone.";

/** @param {string} scope */
export function isHistoryScope(scope) {
  return scope === SCOPE_OLDER || scope === SCOPE_ALL;
}

/** Confirm button label for the chosen variant. */
export function historyDeleteButtonText(scope) {
  return scope === SCOPE_ALL ? "Delete everything" : "Delete older than 30 days";
}

/** Toast after a successful delete, from the server's answer. */
export function historyDeletedText(result) {
  const n = result && Number.isInteger(result.jobs_deleted) ? result.jobs_deleted : 0;
  const jobs = `${n} finished download${n === 1 ? "" : "s"}`;
  if (result && result.scope === SCOPE_ALL) return `History deleted: ${jobs} and all traffic statistics.`;
  return `History older than 30 days deleted: ${jobs} and the traffic statistics from that time.`;
}

/** Error line in the dialog. Only a 404 means "server too old" (LEARNINGS,
 * WP WEB-FEAT-3: a 401 is a key problem, never an outdated server). */
export function historyDeleteErrorText(err) {
  if (err && err.kind === ERROR_KINDS.NOT_FOUND) {
    return "This server cannot delete history yet. Update vault-api to use this.";
  }
  const detail = err && typeof err.detail === "string" && err.detail ? err.detail : (err && err.message) || "Request failed.";
  return `Could not delete history: ${detail}`;
}
