/**
 * Pure presentation logic for `GET /v1/schedule` (WP 4d-web — closing the
 * "Phase 4a UI switch remains open" gap docs/PROJECT_PLAN.md's Phase 4d
 * entry names).
 *
 * `sweep_cached_gc_risk` is already computed server-side, from the
 * EFFECTIVE (override-resolved) `sweep_include_cached`/`auto_gc` settings —
 * `api/vault_api/routers/schedule.py`'s own docstring says a UI can render
 * it "without re-deriving the two settings' interaction itself". This
 * module takes that at face value: neither function below recomputes the
 * condition from the two settings again. Two call sites computing the same
 * domain predicate WILL diverge (docs/LEARNINGS.md, WP 4c-api/WP 4d) — the
 * fix here is not "get the client copy right", it is "have only one copy".
 *
 * **Review round 1 (Opus) FAIL, both blockers the same lesson: a field's
 * VALUE is not a description of the world.** `sweep_cached_gc_risk === true`
 * describes a CONFIGURATION (`sweep_include_cached` on, `auto_gc` not
 * `execute`) — it says nothing about whether the scheduler is even enabled,
 * so wording it as present-tense activity ("cached games ARE BEING
 * refreshed") over-claims whenever there is no schedule window (the shipped
 * default). `last_sweep_targets === null` describes an ABSENCE of a
 * recorded result, which has two different causes
 * (`api/vault_api/scheduler.py::claim_sweep` stamps `last_sweep_at` and
 * NULLs both counters in the SAME statement; `finish_sweep` fills them in
 * only once the sweep actually finishes) — `last_sweep_at` being present or
 * absent is what tells the two apart, and the fix round below is what makes
 * this module read that field instead of assuming "null counters" only ever
 * means "never ran".
 *
 * `last_sweep_targets` now carries THREE states that must never share
 * wording: `last_sweep_at` also null means the scheduler has never claimed
 * a sweep at all; `last_sweep_at` stamped but the counters still null means
 * a sweep STARTED and has not recorded a result — genuinely either still
 * running or the process died mid-sweep, per
 * `api/vault_api/routers/schedule.py`'s own field docstring ("Both null
 * while a sweep is in flight, or if the process died during one") — this
 * module states both remaining possibilities and picks neither; `0` means
 * it HAS run and found nothing to check. Neither the zero-targets branch
 * nor any other branch below names a CAUSE for an empty target set — at the
 * time this module was written, that could mean "no PC agent has ever
 * reported installed games" alone, but a sibling package changes the
 * sweep's default target set to also include cached-but-unreported games,
 * which makes any single hardcoded cause wrong the moment it lands. Only
 * observable possibilities are offered, never a diagnosis, and no default
 * value for any setting is ever stated here — the caller must pass the
 * server's own effective values (already true for `sweep_include_cached`,
 * surfaced by the Settings view's own toggle) rather than this module
 * guessing at what is "normally" on or off.
 */

import { formatTimestamp } from "./format.js";

/**
 * The "did the last scheduled sweep actually do anything" line.
 *
 * @param {{last_sweep_targets?: unknown, last_sweep_enqueued?: unknown, last_sweep_at?: unknown} | null | undefined} schedule
 *   The raw `GET /v1/schedule` response, or `null`/`undefined` before the
 *   first successful fetch / after a failed one.
 * @returns {string | null} `null` when there is nothing honest to print
 *   (no schedule snapshot yet, or a malformed one — `last_sweep_targets` is
 *   documented as always `number | null`, never any other shape).
 */
export function sweepTargetsMessage(schedule) {
  if (!schedule || typeof schedule !== "object") return null;
  const targets = schedule.last_sweep_targets;
  const hasTimestamp = typeof schedule.last_sweep_at === "string" && schedule.last_sweep_at.length > 0;

  if (targets === null || targets === undefined) {
    if (!hasTimestamp) {
      return "The scheduled sweep has not run yet.";
    }
    // claim_sweep stamps last_sweep_at and NULLs both counters in one
    // statement; finish_sweep fills them in only once the sweep completes.
    // A stamped timestamp with no counters therefore means a sweep STARTED
    // and has not (yet, or ever) recorded a result — state both remaining
    // possibilities (still running / the process stopped before finishing)
    // rather than choosing one, per api/vault_api/routers/schedule.py's own
    // field docstring.
    const whenText = formatTimestamp(schedule.last_sweep_at);
    return (
      `A sweep started (${whenText}) but has not recorded a result yet — it may still be ` +
      "running, or it may have stopped before finishing."
    );
  }
  if (typeof targets !== "number" || !Number.isFinite(targets)) return null;

  const whenText = formatTimestamp(hasTimestamp ? schedule.last_sweep_at : null);

  if (targets === 0) {
    return (
      `The last run (${whenText}) found no games to check. If that is unexpected, check whether any ` +
      "PC agent has reported installed games, and whether the “Include cached games” setting " +
      "covers what you expect it to."
    );
  }

  const enqueued =
    typeof schedule.last_sweep_enqueued === "number" && Number.isFinite(schedule.last_sweep_enqueued)
      ? schedule.last_sweep_enqueued
      : 0;
  const gameWord = targets === 1 ? "game" : "games";
  const jobWord = enqueued === 1 ? "job" : "jobs";
  return `The last run (${whenText}) checked ${targets} ${gameWord} and started ${enqueued} new ${jobWord}.`;
}

/**
 * The "keeping the cache current without collecting" warning (WP brief:
 * never a block, never an auto-fix — the operator decides).
 *
 * Worded as a CONFIGURATION statement, never an activity claim — the same
 * choice `api/vault_api/scheduler.py::warn_once_if_cached_sweep_without_gc`
 * makes in its own log line ("VAULT_SWEEP_INCLUDE_CACHED is on while
 * VAULT_AUTO_GC is %r, not 'execute'"): `sweep_cached_gc_risk` is a pure
 * function of two settings and says nothing about whether the scheduler
 * even has a window configured. With no schedule window (the shipped
 * default), nothing is being refreshed and disk usage is not growing from
 * this — a sentence that asserts refreshing/growing IN PROGRESS would be
 * false in exactly that case, which is why every clause below is phrased as
 * "is set to"/"would" rather than "is"/"will".
 *
 * @param {{sweep_cached_gc_risk?: unknown} | null | undefined} schedule
 * @returns {string | null} the warning text, or `null` — including when
 *   `schedule` is missing entirely, and when `sweep_cached_gc_risk` is
 *   anything other than the literal boolean `true` (a strict check, so a
 *   stray truthy non-boolean from a future API change cannot silently start
 *   showing this).
 */
/**
 * The one visible sentence of that warning (WP D16-WEB, audit 1.2: state
 * stays visible, the explanation above goes behind (i)). Same rule: a
 * configuration statement, never an activity claim.
 */
export const CACHED_SWEEP_GC_RISK_SHORT =
  "Include cached games is on while Auto-GC is not set to Execute: a refreshed game would leave its old chunks on disk.";

export function cachedSweepGcRiskWarning(schedule) {
  if (!schedule || schedule.sweep_cached_gc_risk !== true) return null;
  return (
    "The sweep is set to include cached games while garbage collection is not set to execute. " +
    "Any game this configuration refreshes would leave its previous chunks on disk instead of " +
    "freeing them — if the sweep runs, disk usage would grow over time. The sweep is never " +
    "refused and GC is never turned on automatically — turn off “Include cached games”, or set " +
    "Auto-GC to Execute, if you want to avoid this."
  );
}
