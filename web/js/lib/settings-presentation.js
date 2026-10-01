/**
 * Pure presentation mapping for one `GET /v1/settings` entry (WP 4a.6;
 * ADR-0009). Kept separate from settings.js so the "what does this
 * `applies`/`source` value actually mean to a human" mapping is testable
 * without building any DOM, and so the wording is defined in exactly one
 * place rather than re-typed at every call site.
 */

const APPLIES_TEXT = Object.freeze({
  immediately: "Takes effect immediately.",
  next_sweep: "Takes effect at the next scheduled sweep.",
  "restart-required": "Takes effect after vault-api restarts.",
});

/** @param {string} applies one of "immediately"|"next_sweep"|"restart-required" */
export function appliesText(applies) {
  return APPLIES_TEXT[applies] || "Takes effect at an unspecified time.";
}

const SOURCE_TEXT = Object.freeze({
  db: "Overridden here",
  env: "From the environment",
  default: "Default value",
});

/** @param {string} source one of "db"|"env"|"default" */
export function sourceLabel(source) {
  return SOURCE_TEXT[source] || source;
}

/**
 * Whether a "revert to default/env" action is meaningful for this entry —
 * ADR-0009 decision 2: only a `db`-sourced value has an override row to
 * clear at all. Env-only rows never offer this (there is no override
 * concept for them).
 * @param {{source: string, env_only: boolean}} entry
 */
export function canReset(entry) {
  return !entry.env_only && entry.source === "db";
}

/**
 * A settings entry's `effective` value as the string a plain text/number
 * `<input>` should be pre-filled with. `webhook_events` (the one list-typed
 * value `GET` returns) becomes a comma-joined string; `null` (the blank
 * "disabled" state `schedule_window`/`webhook_url` share, ADR-0009) becomes
 * an empty string, matching what typing nothing and submitting would mean.
 * @param {{key: string, effective: unknown}} entry
 * @returns {string}
 */
export function effectiveAsInputValue(entry) {
  if (entry.effective === null || entry.effective === undefined) return "";
  if (Array.isArray(entry.effective)) return entry.effective.join(",");
  return String(entry.effective);
}

/**
 * The eight `GET /v1/settings` keys `views/settings.js` builds a control
 * for (WP WEB-FIX-1, P2). Before this guard existed, a response missing any
 * one of them (an older/newer vault-api, a proxy rewriting the body) made
 * `entryByKey()` return `undefined`, and the first `entry.key` dereference
 * threw AFTER `loadSettings()`'s try/catch had already finished — an
 * uncaught error inside `fullRender()` that left "Loading settings…" on
 * screen forever with nothing in the UI saying why. Pinned as a literal
 * list, not derived from the view (a derived list would drift with it).
 */
export const REQUIRED_SETTING_KEYS = Object.freeze([
  "vault_name",
  "schedule_window",
  "schedule_interval_minutes",
  "schedule_client_stale_days",
  "sweep_include_cached",
  "auto_gc",
  "webhook_url",
  "webhook_events",
]);

/**
 * Which of `required` keys a `GET /v1/settings` response lacks — a
 * malformed response (no `settings` array at all) is reported as missing
 * EVERY key rather than throwing, so the caller has one path to its error
 * state.
 * @param {{settings?: Array<{key: string}>} | null | undefined} response
 * @param {readonly string[]} [required]
 * @returns {string[]} in `required` order; empty when the response is complete
 */
export function missingSettingKeys(response, required = REQUIRED_SETTING_KEYS) {
  const list = response && Array.isArray(response.settings) ? response.settings : [];
  const present = new Set(list.map((e) => e && e.key));
  return required.filter((key) => !present.has(key));
}
