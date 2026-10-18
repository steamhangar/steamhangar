/**
 * Cache completeness in the web UI (WP CACHE-COMPLETE-1c). Pure decision and
 * wording logic, no DOM, no fetch: `components/game-card.js`,
 * `components/game-detail-sheet.js` and `views/downloads.js` are the glue,
 * web/tests/cache-completeness.test.js the pins.
 *
 * **The API (api/README.md "Cache completeness", "Verify and complete").**
 * `GET /v1/games` rows carry `cache_complete_pct` (0-100, floor; 100 only
 * when every byte of the CURRENT build is on disk, or `null` = not known),
 * `cache_expected_bytes`/`cache_present_bytes`/`cache_complete_checked_at`
 * (null exactly when the pct is), `cache_missing_chunks` (chunk files with
 * no file on disk; null exactly when the pct is; `0` on an incomplete row
 * means it is short only of chunk files with the WRONG size) and, on a
 * top-level row, `group_cache_complete_pct` over the D9 group.
 *
 * **Card status (user decision 2026-10-14, "only 100 % gets the green
 * check").** Completeness is a DISPLAY overlay on a CACHED card, exactly
 * like UPD-6's "Update available": `dispKind`, `data-dk`, `statusAction`
 * and the library filters stay on `cached` (the bytes on disk are real), so
 * every other module keeps working unchanged. What the card shows for a
 * CACHED kind, first match wins ({@link cardDisplay}):
 *   1. `update_available` -> "Update available" (UPD-6, orange refresh).
 *      It wins over completeness because the figure is measured against
 *      the NEW build once the oracle announced it (a game with an update is
 *      almost always "partial" or unknown), and the update run requests the
 *      full chunk list of that build, so it completes the game as well
 *      (api/README.md "Verify and complete": "the same list a non-forced run
 *      uses once an update exists"). Saying "Partially cached" there would
 *      state the same fact less usefully.
 *   2. pct 100 -> "Current", green check (the old cached state).
 *   3. pct 0-99 -> "Partially cached N%" (compact "Partial N%"), orange
 *      half-disc glyph `partial`.
 *   4. pct unknown with bytes on disk -> "Cached · not verified" (compact
 *      "Not verified"), neutral question glyph `unverified`. The README's
 *      UI contract: null "means 'not known', never 'complete'". Wording
 *      choice: the existing vocabulary describes the cache ("Not cached",
 *      "Installed but not cached"), so the word keeps "Cached" (the bytes
 *      are real) and adds the one thing that is not known. It is state-only
 *      copy: null has several causes (never measured, oracle off, a new
 *      build nobody fetched yet, the build changed since the check), and
 *      "not verified" is true for every one of them without claiming which
 *      (docs/LEARNINGS.md, "a UI sentence built from ONE API field").
 * A group card (D9) reads `group_cache_complete_pct`, a plain card its own
 * `cache_complete_pct` ({@link completenessPctOf}).
 *
 * **Incomplete list (Downloads).** {@link pendingIncomplete}: the raw rows
 * whose own pct is known and below 100 (the server's
 * `cache_completeness.is_incomplete`, which `GET /v1/games?incomplete=true`
 * and "Complete all" use), Steam tool apps left out (the server leaves them
 * out too), without an active prefill job (once a job exists the game is in
 * the queue), and without `update_available` (rule 1 above: it is listed
 * under "Updates available" and "Update now" completes it). Derived from the
 * polled games list rather than a second request: the predicate is the
 * server's own two-line rule on the same fields.
 *
 * **Actions.** "Verify" (detail sheet) and "Complete now" (one row) send
 * `POST /v1/prefill/verify {appids}`; "Complete all" sends the same route
 * with exactly the listed rows "Complete now" is offered for (like UPD-6's
 * "Update all"), never `POST /v1/prefill/incomplete`: the server's own
 * selection ignores jobs and update flags, so it could force games this
 * list does not show. A row short only of wrong-size chunks
 * (`cache_missing_chunks === 0`) gets no "Complete now": a forced run cannot
 * replace such a chunk (README "Damaged chunks"), so the row says so
 * instead ({@link DAMAGED_NOTE}). A verify that dedupes onto a RUNNING job
 * only forces the game's NEXT run (README "UI note") — {@link
 * summarizeVerifyStart} says so instead of presenting the running job as
 * the verify.
 */

import { KIND, hasVisibleCacheContent, isToolApp, isNotOwnedApp, showsUpdateState, cardIconKind, cardTextKind, cardStatusWord } from "./game-status.js";
import { isGroup } from "./content-groups.js";
import { partitionCachedPrefillOutcome } from "./cached-prefill-outcome.js";
import { formatBytesGB, formatTimestamp } from "./format.js";
import { formatWindow, MANUAL_ONLY_NOTE } from "./updates-available.js";
import { ERROR_KINDS } from "../errors.js";

/** The three completeness states of a CACHED card. */
export const COMPLETENESS = Object.freeze({
  COMPLETE: "complete",
  PARTIAL: "partial",
  UNVERIFIED: "unverified",
});

/** Status-icon kinds (components/status-icon.js STATUS_LABEL keys) the
 * overlay draws. */
export const PARTIAL_ICON_KIND = "partial";
export const UNVERIFIED_ICON_KIND = "unverified";

export const UNVERIFIED_WORD = "Cached · not verified";
export const UNVERIFIED_COMPACT_WORD = "Not verified";

/** "Partially cached 42%". */
export function partialWord(pct) {
  return `Partially cached ${pct}%`;
}

/** "Partial 42%" — the list layout and the compact tiles. */
export function partialCompactWord(pct) {
  return `Partial ${pct}%`;
}

/** A completeness figure from the API: an integer 0..100, else null. */
export function knownPct(value) {
  return Number.isInteger(value) && value >= 0 && value <= 100 ? value : null;
}

/** The figure a row's card shows: the group's for a group row (D9,
 * lib/content-groups.js `groupGames`), else the row's own. */
export function completenessPctOf(row) {
  if (!row) return null;
  return knownPct(isGroup(row) ? row.group_cache_complete_pct : row.cache_complete_pct);
}

/** {@link COMPLETENESS} state of a row (meaningful for a CACHED card). */
export function completenessState(row) {
  const pct = completenessPctOf(row);
  if (pct === null) return COMPLETENESS.UNVERIFIED;
  return pct === 100 ? COMPLETENESS.COMPLETE : COMPLETENESS.PARTIAL;
}

/**
 * Everything a card (or the detail header) shows for its status, with the
 * precedence of the module header.
 * @param {object} row the card's row (a group row for a group card)
 * @param {string} kind the card's dispKind
 * @param {Record<string, string>} labels components/status-icon.js STATUS_LABEL
 * @returns {{icon: string, text: string, word: string, compact: string, key: string}}
 *   `icon` the status-icon kind, `text` the `tx-<text>` colour suffix,
 *   `word`/`compact` the full and compact status words (equal unless the
 *   overlay has a shorter form), `key` what changes when any of them does.
 */
export function cardDisplay(row, kind, labels) {
  const base = {
    icon: cardIconKind(row, kind),
    text: cardTextKind(row, kind),
    word: cardStatusWord(row, kind, labels),
  };
  if (kind !== KIND.CACHED || showsUpdateState(row, kind)) {
    return { ...base, compact: base.word, key: base.text };
  }
  const state = completenessState(row);
  if (state === COMPLETENESS.PARTIAL) {
    const pct = completenessPctOf(row);
    return {
      icon: PARTIAL_ICON_KIND,
      text: PARTIAL_ICON_KIND,
      word: partialWord(pct),
      compact: partialCompactWord(pct),
      key: `${PARTIAL_ICON_KIND}:${pct}`,
    };
  }
  if (state === COMPLETENESS.UNVERIFIED) {
    return {
      icon: UNVERIFIED_ICON_KIND,
      text: UNVERIFIED_ICON_KIND,
      word: UNVERIFIED_WORD,
      compact: UNVERIFIED_COMPACT_WORD,
      key: UNVERIFIED_ICON_KIND,
    };
  }
  return { ...base, compact: base.word, key: base.text };
}

// ---------------------------------------------------------------------
// Detail sheet
// ---------------------------------------------------------------------

export const VERIFY_LABEL = "Verify";
export const VERIFY_HINT =
  "Re-checks every chunk of the current build against the cache and downloads what is missing.";
/** A row short only of wrong-size chunks (see module header). */
export const DAMAGED_NOTE = "Damaged chunks — SteamHangar can't repair these yet.";
export const UNKNOWN_LINE = "Completeness unknown — not measured for the current build.";

/**
 * WP UNMAPPED-FLAG-1: the honesty suffix for a known figure when the server
 * reports `cache_unmapped_depots` > 0 (depots the game uses that have no
 * mapping and are NOT in the percentage). `""` for 0, absent or malformed.
 */
export function unmappedNote(row) {
  const n = row?.cache_unmapped_depots;
  if (!Number.isInteger(n) || n <= 0) return "";
  return ` · not counted: ${n} unmapped ${n === 1 ? "depot" : "depots"}`;
}

/** True for a row whose own figure is known, below 100 and short of no
 * missing chunk file: only wrong-size chunks, nothing a forced run fixes. */
export function isDamagedOnly(row) {
  const pct = knownPct(row?.cache_complete_pct);
  return pct !== null && pct < 100 && row.cache_missing_chunks === 0;
}

/** True for a row "Complete now" can help: known, below 100, at least one
 * chunk file missing (the server's `cache_completeness.is_completable`). */
export function isCompletable(row) {
  const pct = knownPct(row?.cache_complete_pct);
  return pct !== null && pct < 100 && Number.isInteger(row.cache_missing_chunks) && row.cache_missing_chunks > 0;
}

/**
 * The detail sheet's completeness line for a row's OWN figure: "80% complete
 * — 3.2 of 4.0 GB on disk · checked <time>", or {@link UNKNOWN_LINE}.
 * @param {object} row
 * @returns {string}
 */
export function completenessLine(row) {
  const pct = knownPct(row?.cache_complete_pct);
  if (pct === null) return UNKNOWN_LINE;
  const present = formatBytesGB(row.cache_present_bytes) || "0.0 GB";
  const expected = formatBytesGB(row.cache_expected_bytes);
  // Always "X of Y" when the expected size is known, also at 100%: the figure
  // only covers the depots the server maps to the game, so the size it is
  // measured against must stay visible (WP MAP-FROM-MANIFEST-1).
  const amount = !expected ? `${present} on disk` : `${present} of ${expected} on disk`;
  const checked = row.cache_complete_checked_at ? ` · checked ${formatTimestamp(row.cache_complete_checked_at)}` : "";
  // The template stays byte-identical: the Android twin pins it.
  return `${pct}% complete — ${amount}${checked}` + unmappedNote(row);
}

/**
 * WP GROUP-VERIFIED-1: the honesty suffix for a known group figure when the
 * server reports `group_cache_unmeasured_depots` > 0 — content-package depots
 * with no usable measurement (typically an unowned package), left OUT of the
 * group percentage and never counted as complete. Their bytes stay visible.
 * `""` for 0, absent or malformed.
 */
export function unmeasuredNote(row) {
  const n = row?.group_cache_unmeasured_depots;
  if (!Number.isInteger(n) || n <= 0) return "";
  const bytes = formatBytesGB(row.group_cache_unmeasured_bytes);
  const size = bytes ? ` (${bytes})` : "";
  return ` · not measured: ${n} package ${n === 1 ? "depot" : "depots"}${size}`;
}

/** The group line under it for a group row with a known group figure, else
 * null: "With content packages: 75% complete". */
export function groupCompletenessLine(row) {
  if (!isGroup(row)) return null;
  const pct = knownPct(row.group_cache_complete_pct);
  // The template stays byte-identical (Android twin pin); the note is appended.
  return pct === null ? null : `With content packages: ${pct}% complete` + unmeasuredNote(row);
}

/**
 * Whether the sheet shows the completeness block at all: a non-tool game
 * with bytes on disk (its own, or a group member's).
 * @param {object} gameLike the game's own row
 * @param {object | null} group its group row, or null
 */
export function showsCompletenessBlock(gameLike, group) {
  if (!gameLike || isToolApp(gameLike)) return false;
  if (hasVisibleCacheContent(gameLike)) return true;
  return !!group && group.packages.some(hasVisibleCacheContent);
}

/**
 * The app ids the sheet's "Verify" sends: the game and, for a group, its
 * packages — each with bytes on disk, not a tool app and not short only of
 * wrong-size chunks (a forced run cannot help those). In appid order.
 * @param {object[]} members the game's own row, plus its packages for a group
 * @returns {number[]}
 */
export function verifyAppids(members) {
  return (Array.isArray(members) ? members : [])
    // WP NOT-OWNED-2: a known-not-owned package would only end not_owned again.
    .filter(
      (m) =>
        m && hasVisibleCacheContent(m) && !isToolApp(m) && !isDamagedOnly(m) && !isNotOwnedApp(m),
    )
    .map((m) => m.appid)
    .sort((a, b) => a - b);
}

// ---------------------------------------------------------------------
// Downloads: "Incomplete (N)"
// ---------------------------------------------------------------------

export const INCOMPLETE_HEADING = "Incomplete";
/** WP AUTO-VERIFY-1: the note while automatic completion is switched off. */
export const AUTO_COMPLETE_OFF_NOTE = "Manual only — automatic completion is off in Settings.";
export const COMPLETE_NOW_LABEL = "Verify";
export const COMPLETE_ALL_LABEL = "Complete all";

const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "paused"]);

/**
 * The incomplete games the Downloads section lists (module header), sorted
 * by display name (case-insensitive), then appid.
 * @param {object[] | null | undefined} games `GET /v1/games` rows
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` rows
 * @param {(game: object) => string} [titleOf]
 * @returns {object[]}
 */
export function pendingIncomplete(games, jobs, titleOf = (g) => String(g.name ?? "")) {
  const busy = new Set(
    (Array.isArray(jobs) ? jobs : [])
      .filter((j) => j && j.type === "prefill" && ACTIVE_JOB_STATUSES.has(j.status))
      .map((j) => j.appid),
  );
  const rows = (Array.isArray(games) ? games : []).filter((g) => {
    if (!g || isToolApp(g) || busy.has(g.appid) || g.update_available === true) return false;
    const pct = knownPct(g.cache_complete_pct);
    return pct !== null && pct < 100;
  });
  return rows
    .map((g) => ({ g, t: titleOf(g).toLowerCase() }))
    .sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : a.g.appid - b.g.appid))
    .map(({ g }) => g);
}

/** "Incomplete (N)". */
export function incompleteHeadingText(count) {
  return `${INCOMPLETE_HEADING} (${count})`;
}

/** "Complete all 2 games" — the button's accessible name; `count` is the
 * number of rows "Complete now" is offered for. */
export function completeAllAriaLabel(count) {
  return `${COMPLETE_ALL_LABEL} ${count} ${count === 1 ? "game" : "games"}`;
}

/** "Complete now — Emberreach". */
export function completeNowAriaLabel(title) {
  return `${COMPLETE_NOW_LABEL} — ${title}`;
}

/** "Verify — Emberreach". */
export function verifyAriaLabel(title) {
  return `${VERIFY_LABEL} — ${title}`;
}

/** The row's figure: "42%". */
export function incompletePctText(row) {
  const pct = knownPct(row?.cache_complete_pct);
  return pct === null ? "—" : `${pct}%`;
}

/**
 * The note under the heading, from `GET /v1/schedule`. Completion is a
 * download, so it follows the update rules (README "Automatic completion"):
 * nothing outside the window, manual only without one. Unlike updates, it
 * covers every incomplete cached game whether or not a PC reports it
 * (user decision 2026-10-14), so `sweep_include_cached` plays no part.
 * Rows short only of damaged chunks are never completed automatically; their
 * own row note says so. `null` while the schedule is not known.
 * WP AUTO-VERIFY-1: with `auto_complete_cached: false` (Settings "Complete
 * cached games automatically" off) nothing completes on its own:
 * {@link AUTO_COMPLETE_OFF_NOTE}.
 * @param {{enabled?: unknown, window?: unknown, auto_complete_cached?: unknown} | null | undefined} schedule
 * @returns {string | null}
 */
export function incompleteScheduleNote(schedule) {
  if (!schedule || typeof schedule !== "object") return null;
  const hasWindow = schedule.enabled === true && typeof schedule.window === "string" && schedule.window.trim();
  if (!hasWindow) return MANUAL_ONLY_NOTE;
  // WP AUTO-VERIFY-1: only an explicit false (an older server omits the
  // field and always completed in the window).
  if (schedule.auto_complete_cached === false) return AUTO_COMPLETE_OFF_NOTE;
  return `These complete automatically in the schedule window ${formatWindow(schedule.window)}.`;
}

/**
 * Toast for a successful `POST /v1/prefill/verify` ("Verify", "Complete
 * now", "Complete all").
 *
 * A deduplicated entry is worded by its job's status (README "UI note"): a
 * QUEUED job runs forced when the worker claims it; a PAUSED one runs
 * forced once resumed; a RUNNING one keeps its mode and only the game's
 * NEXT run is forced — "verifies on the next run", never presented as the
 * verify itself.
 * @param {object[] | null | undefined} refs `PrefillJobRef[]`
 * @param {"verify"|"complete"} [mode] the verb for newly queued jobs
 * @returns {{message: string, warn: boolean}}
 */
export function summarizeVerifyStart(refs, mode = "verify") {
  const p = partitionCachedPrefillOutcome(refs);
  if (p.total === 0) {
    return { message: mode === "complete" ? "Nothing to complete." : "Nothing to verify.", warn: false };
  }
  if (p.total === 1) {
    if (p.queued.length) return { message: mode === "complete" ? "Queued to complete." : "Queued to verify.", warn: false };
    if (p.alreadyQueued.length) return { message: "Already queued — it runs as a verify.", warn: false };
    if (p.alreadyPaused.length) return { message: "Paused — it verifies when you resume it.", warn: true };
    return { message: "Already downloading — verifies on the next run.", warn: false };
  }
  const parts = [];
  if (p.queued.length) parts.push(`${p.queued.length} queued to ${mode === "complete" ? "complete" : "verify"}`);
  if (p.alreadyQueued.length) parts.push(`${p.alreadyQueued.length} already queued (runs as a verify)`);
  if (p.alreadyRunning.length) parts.push(`${p.alreadyRunning.length} already downloading (verifies on the next run)`);
  if (p.alreadyPaused.length) parts.push(`${p.alreadyPaused.length} paused (verifies when resumed)`);
  const message = parts.join(" · ");
  return { message: message.charAt(0).toUpperCase() + message.slice(1), warn: p.alreadyPaused.length > 0 };
}

/**
 * How to report a failed verify/complete request. A 5xx can leave the first
 * apps already queued (one transaction per app), so it asks for a re-read
 * instead of claiming nothing happened.
 * @returns {{message: string, warn: true, refresh: boolean}}
 */
export function describeVerifyStartError(err) {
  if (err && err.kind === ERROR_KINDS.SERVER) {
    return {
      message: "The server had trouble partway through — some games may already be queued. Re-checking Downloads…",
      warn: true,
      refresh: true,
    };
  }
  const detail = err && typeof err.detail === "string" && err.detail ? err.detail : null;
  return { message: detail || (err && err.message) || "Could not start the verify.", warn: true, refresh: false };
}

/** A string that changes whenever the Incomplete section's rendered content
 * would (the view rebuilds it only then, so a poll tick keeps focus). */
export function incompleteListKey(rows, titleOf) {
  return rows
    .map((g) => `${g.appid}|${titleOf(g)}|${incompletePctText(g)}|${isCompletable(g) ? 1 : 0}`)
    .join("\n");
}
