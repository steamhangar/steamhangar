/**
 * Bulk-download split semantics (WP 4a.3).
 *
 * Ports the mockup's `syncBulk` primary/secondary decision (docs/design/
 * vault-app-mockup-NOTES.md round 5, "Bulk actions never silently
 * re-download a cached game"): multi-select classifies the picked set by
 * REAL cache state and targets only what needs bytes — the button spells
 * out the skip count, re-download is always an explicit secondary, never
 * folded into the primary. Pure — no DOM, no fetch — the classification
 * and the resulting button/note text are both unit-testable
 * (web/tests/bulk-plan.test.js).
 *
 * Narrower than the mockup by exactly the same divergence documented in
 * game-status.js: there is no "stale" status, so the mockup's three-way
 * split (free / stale-update / cached) collapses to two buckets here —
 * `needsDownload` (not cached OR errored — see the docstring on
 * `classifyBulkSelection` for why `error` joins `none`) and `current`
 * (cached). `busy` (already has a job in flight) is excluded from both, as
 * in the mockup.
 *
 * `classifyBulkDeleteEligibility` (WP 4a.3 review fix, should-fix 1) lives
 * here too, next to the download split, rather than inlined in
 * `views/library.js` where the review round found it untested: the
 * mockup's own delete-eligibility rule is has-cache-content
 * (`vault-app-mockup.html`'s `dels = rest.filter(g=>g.st!=="none")`), which
 * for the real API means `hasVisibleCacheContent`, NOT "not none" —
 * `dispKind(g) !== "none"` also matches `error`, and an errored app with
 * ZERO bytes has no depot mappings left to delete
 * (`DELETE /v1/cache/{appid}` 404s: "appid has no depot_app_map rows").
 */

import { dispKind, findLiveJob, hasVisibleCacheContent, KIND } from "./game-status.js";

/** Appids with a prefill job that is queued, running or paused right now —
 * shared by both classifiers below (queued jobs count as busy here, unlike
 * `findLiveJob` — dedupe protection needs the queued case too; mockup
 * parity: `isBusy` checks `LIVE.includes(...) || st==="queued"`). */
function busyAppidsFromJobs(jobs) {
  return new Set(
    (Array.isArray(jobs) ? jobs : [])
      .filter((j) => j.type === "prefill" && ["queued", "running", "paused"].includes(j.status))
      .map((j) => j.appid),
  );
}

/**
 * @param {object[]} games the SELECTED games (already resolved from the
 *   picked appid set — callers own that lookup).
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` snapshot.
 * @returns {{
 *   busy: object[], needsDownload: object[], current: object[], notOnVault: object[],
 * }}
 */
export function classifyBulkSelection(games, jobs) {
  const busyAppids = busyAppidsFromJobs(jobs);
  const busy = [];
  const rest = [];
  // WP WEB-FEAT-1 (user decision): an owned game the vault does not know
  // (`owned_only`, lib/owned-library.js) is downloadable from its detail
  // sheet only, never in bulk — its own bucket, never `needsDownload`.
  const notOnVault = [];
  for (const g of games) {
    if (g && g.owned_only === true) notOnVault.push(g);
    else (busyAppids.has(g.appid) ? busy : rest).push(g);
  }

  const needsDownload = [];
  const current = [];
  for (const g of rest) {
    // A live job can't be true here (busyAppids already excludes it), so
    // dispKind's cache-only branch is exactly what we want: `none` and
    // `error` both mean "not successfully cached" — an errored app gets
    // the same "needs a (re)download" treatment as a never-downloaded one,
    // matching statusAction's retry decision in game-status.js.
    const kind = dispKind(g, undefined);
    (kind === KIND.CACHED ? current : needsDownload).push(g);
  }
  return { busy, needsDownload, current, notOnVault };
}

/**
 * Which of the SELECTED games can actually be sent to
 * `DELETE /v1/cache/{appid}` without a guaranteed 404 — has real bytes on
 * the cache right now, AND is not busy (deleting under an in-flight prefill
 * is a 409 anyway; excluding it here keeps the button's own count honest
 * about what it will actually attempt). Deliberately
 * `hasVisibleCacheContent`, not `dispKind(...) !== "none"`: an `error`
 * status with zero visible bytes (e.g. a first-ever prefill that failed
 * before writing anything) has no depot mappings to delete — offering it
 * for bulk delete would just 404. An `error` status WITH bytes (a half-
 * deleted or partially-failed run, api/README.md "Per-game deletion":
 * "a failed depot is typically half deleted") genuinely has content to
 * clean up and stays eligible.
 *
 * @param {object[]} games the SELECTED games.
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` snapshot.
 * @returns {object[]} the subset of `games` eligible for bulk delete.
 */
export function classifyBulkDeleteEligibility(games, jobs) {
  const busyAppids = busyAppidsFromJobs(jobs);
  return games.filter((g) => !busyAppids.has(g.appid) && hasVisibleCacheContent(g));
}

const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/**
 * Build the bulk-download bar's button/note text from a classification.
 * Mirrors the mockup's three visible outcomes (mockup's `stale` branch is
 * gone — see module header):
 *   1. Something needs downloading -> primary targets exactly that,
 *      skip count spelled out.
 *   2. Nothing needs downloading, nothing is `current` either (every pick
 *      is `busy`) -> primary disabled, "Already downloading".
 *   3. Nothing needs downloading, everything is `current` -> primary
 *      disabled ("All cached"), secondary offers an explicit re-download.
 *
 * @param {{busy: object[], needsDownload: object[], current: object[], notOnVault?: object[]}} classification
 *   `notOnVault` (WP WEB-FEAT-1): picked owned-only games, never a target.
 * @param {number} totalPicked
 */
export function buildBulkDownloadPlan(classification, totalPicked) {
  const { busy, needsDownload, current } = classification;
  const notOnVault = classification.notOnVault || [];
  // WP WEB-FEAT-1: appended to whichever note applies, so a picked owned-only
  // game is never silently counted as "already cached".
  const notOnVaultNote = notOnVault.length
    ? `${plural(notOnVault.length, "game")} not on the vault yet — open ${notOnVault.length === 1 ? "it" : "each one"} to download.`
    : "";
  const withNotOnVault = (note) => [note, notOnVaultNote].filter(Boolean).join(" ");

  if (needsDownload.length) {
    const skipped = totalPicked - needsDownload.length - notOnVault.length;
    return {
      primaryEnabled: true,
      primaryLabel:
        needsDownload.length < totalPicked
          ? `Download ${needsDownload.length} of ${totalPicked}`
          : `Download ${plural(needsDownload.length, "game")}`,
      primaryTargets: needsDownload.map((g) => g.appid),
      note: withNotOnVault(
        skipped
          ? `${skipped} already cached — not re-downloaded.`
          : needsDownload.length > 1 && !notOnVault.length
            ? `All ${needsDownload.length} are new to the cache.`
            : "",
      ),
      secondaryLabel: null,
      secondaryTargets: [],
    };
  }

  if (current.length) {
    return {
      primaryEnabled: false,
      primaryLabel: notOnVault.length ? "Nothing to download here" : "All cached — nothing to download",
      primaryTargets: [],
      note: withNotOnVault(
        notOnVault.length
          ? `${current.length} cached — re-download only if you need to refetch from Steam.`
          : "Every selected game is current. Re-download only if you need to refetch from Steam.",
      ),
      secondaryLabel: `Re-download ${current.length}`,
      secondaryTargets: current.map((g) => g.appid),
    };
  }

  if (notOnVault.length) {
    return {
      primaryEnabled: false,
      primaryLabel: "Nothing to download here",
      primaryTargets: [],
      note: withNotOnVault(busy.length ? `${busy.length} already downloading.` : ""),
      secondaryLabel: null,
      secondaryTargets: [],
    };
  }

  return {
    primaryEnabled: false,
    primaryLabel: busy.length === totalPicked && totalPicked > 0 ? "Already downloading" : "Nothing to download",
    primaryTargets: [],
    note:
      busy.length === totalPicked && totalPicked > 0
        ? "Every selected game already has a job in flight."
        : "",
    secondaryLabel: null,
    secondaryTargets: [],
  };
}

// findLiveJob is re-exported for callers that already have this module open
// and want the same "live job" notion classifyBulkSelection is built on top
// of (game-status.js remains the canonical source).
export { findLiveJob };
