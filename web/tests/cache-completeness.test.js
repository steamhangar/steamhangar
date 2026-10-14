/**
 * WP CACHE-COMPLETE-1c — the pure decision and wording logic of
 * `web/js/lib/cache-completeness.js` (card status precedence, the detail
 * sheet's line and Verify targets, the Downloads "Incomplete" selection,
 * the schedule note and the verify toast). The DOM wiring is pinned in
 * game-card-completeness.test.js, game-detail-sheet-completeness.test.js,
 * library-completeness-wiring.test.js and downloads-incomplete-wiring.test.js.
 *
 * Guarantees pinned by name (MUTATION TARGET):
 *  C1 only a known 100 gets the green check ("Current", k-cached);
 *  C2 below 100 -> "Partially cached N%" / "Partial N%", own kind `partial`;
 *  C3 unknown (null, absent, malformed) with bytes on disk -> NOT the green
 *     check: "Cached · not verified", kind `unverified`;
 *  C4 "Update available" wins over every completeness state;
 *  C5 non-cached kinds (running, error, none, tool, ...) are untouched;
 *  C6 a group row reads `group_cache_complete_pct`, a plain row its own;
 *  C7 the Incomplete list: known and < 100, no tool app, no active job, no
 *     update flag;
 *  C8 "Complete now" only with a missing chunk file (`cache_missing_chunks`
 *     > 0); damaged-only rows have none and Verify skips them;
 *  C9 a verify that dedupes onto a RUNNING job says "verifies on the next
 *     run", never that it was queued;
 *  C10 the schedule note: window -> automatic, no window -> manual only,
 *     unknown -> no claim.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  COMPLETENESS,
  knownPct,
  completenessPctOf,
  completenessState,
  cardDisplay,
  isDamagedOnly,
  isCompletable,
  completenessLine,
  groupCompletenessLine,
  showsCompletenessBlock,
  verifyAppids,
  pendingIncomplete,
  incompleteHeadingText,
  completeAllAriaLabel,
  completeNowAriaLabel,
  incompletePctText,
  incompleteScheduleNote,
  summarizeVerifyStart,
  describeVerifyStartError,
  incompleteListKey,
  UNVERIFIED_WORD,
  UNKNOWN_LINE,
  DAMAGED_NOTE,
} from "../js/lib/cache-completeness.js";
import { STATUS_LABEL } from "../js/components/status-icon.js";
import { KIND } from "../js/lib/game-status.js";
import { groupGames } from "../js/lib/content-groups.js";
import { ERROR_KINDS } from "../js/errors.js";

const GB = 1_073_741_824;
function row(over = {}) {
  return {
    appid: 10,
    name: "Aurora",
    status: "done",
    last_prefill_at: "2026-10-01T00:00:00Z",
    size_bytes: 3 * GB,
    needs_force: false,
    update_available: false,
    cache_complete_pct: 100,
    cache_expected_bytes: 3 * GB,
    cache_present_bytes: 3 * GB,
    cache_complete_checked_at: "2026-10-14T08:00:00Z",
    cache_missing_chunks: 0,
    group_cache_complete_pct: 100,
    ...over,
  };
}
const partial = (pct, missing = 12) =>
  row({ cache_complete_pct: pct, cache_present_bytes: Math.floor((3 * GB * pct) / 100), cache_missing_chunks: missing });
const unknown = (over = {}) =>
  row({
    cache_complete_pct: null,
    cache_expected_bytes: null,
    cache_present_bytes: null,
    cache_complete_checked_at: null,
    cache_missing_chunks: null,
    group_cache_complete_pct: null,
    ...over,
  });

test("knownPct: integers 0..100 only", () => {
  for (const v of [0, 1, 99, 100]) assert.equal(knownPct(v), v);
  for (const v of [null, undefined, -1, 101, 42.5, "42", NaN, true]) assert.equal(knownPct(v), null, String(v));
});

test("MUTATION TARGET C1 -- only a known 100 is the green check, 'Current'", () => {
  const d = cardDisplay(row(), KIND.CACHED, STATUS_LABEL);
  assert.deepEqual([d.icon, d.text, d.word, d.compact], ["cached", "cached", "Current", "Current"]);
  for (const r of [partial(99), unknown(), row({ cache_complete_pct: undefined })]) {
    const x = cardDisplay(r, KIND.CACHED, STATUS_LABEL);
    assert.notEqual(x.icon, "cached", `pct ${r.cache_complete_pct} must not draw the green check`);
    assert.notEqual(x.word, "Current");
  }
});

test("MUTATION TARGET C2 -- below 100: 'Partially cached N%', compact 'Partial N%', kind partial, key carries N", () => {
  const d = cardDisplay(partial(42), KIND.CACHED, STATUS_LABEL);
  assert.equal(d.icon, "partial");
  assert.equal(d.text, "partial");
  assert.equal(d.word, "Partially cached 42%");
  assert.equal(d.compact, "Partial 42%");
  assert.equal(d.key, "partial:42");
  assert.equal(cardDisplay(partial(0), KIND.CACHED, STATUS_LABEL).word, "Partially cached 0%");
  assert.notEqual(cardDisplay(partial(43), KIND.CACHED, STATUS_LABEL).key, d.key, "a new figure changes the key");
});

test("MUTATION TARGET C3 -- unknown with bytes on disk: never the green check, 'Cached · not verified'", () => {
  for (const r of [unknown(), row({ cache_complete_pct: undefined }), row({ cache_complete_pct: 140 })]) {
    const d = cardDisplay(r, KIND.CACHED, STATUS_LABEL);
    assert.equal(d.icon, "unverified");
    assert.equal(d.text, "unverified");
    assert.equal(d.word, UNVERIFIED_WORD);
    assert.equal(d.word, "Cached · not verified");
    assert.equal(d.compact, "Not verified");
  }
  assert.equal(completenessState(unknown()), COMPLETENESS.UNVERIFIED);
});

test("MUTATION TARGET C4 -- 'Update available' wins over partial, unknown and complete", () => {
  for (const r of [partial(42), unknown(), row()]) {
    const d = cardDisplay({ ...r, update_available: true }, KIND.CACHED, STATUS_LABEL);
    assert.deepEqual([d.icon, d.text, d.word, d.compact], ["stale", "stale", "Update available", "Update available"]);
  }
});

test("MUTATION TARGET C5 -- non-cached kinds keep their own status, whatever the figure", () => {
  const cases = [
    [KIND.RUNNING, "running", "Downloading"],
    [KIND.UPDATING, "updating", "Updating"],
    [KIND.VERIFY, "verify", "Verifying"],
    [KIND.PAUSED, "paused", "Paused"],
    [KIND.ERROR, "error", "Failed"],
    [KIND.NONE, "none", "Not cached"],
    [KIND.TOOL, "notinuse", "Steam tool package"],
  ];
  for (const [kind, icon, word] of cases) {
    for (const r of [partial(42), unknown()]) {
      const d = cardDisplay(r, kind, STATUS_LABEL);
      assert.equal(d.icon, icon, kind);
      assert.equal(d.word, word, kind);
      assert.equal(d.compact, word, kind);
    }
  }
});

test("MUTATION TARGET C6 -- a group row reads group_cache_complete_pct, a plain row its own figure", () => {
  const parent = row({ appid: 500, name: "Parent", cache_complete_pct: 100, group_cache_complete_pct: 61 });
  const pkg = row({ appid: 501, name: "Parent - Pack", parent_appid: 500, cache_complete_pct: 20, group_cache_complete_pct: null });
  const [group] = groupGames([parent, pkg]).rows;
  assert.equal(completenessPctOf(group), 61);
  assert.equal(cardDisplay(group, KIND.CACHED, STATUS_LABEL).word, "Partially cached 61%");
  // The same parent row ungrouped (its package not in the list): own figure.
  assert.equal(completenessPctOf(parent), 100);
  assert.equal(cardDisplay(parent, KIND.CACHED, STATUS_LABEL).word, "Current");
  // A group whose group figure is unknown is not the green check either.
  const [g2] = groupGames([{ ...parent, group_cache_complete_pct: null }, pkg]).rows;
  assert.equal(cardDisplay(g2, KIND.CACHED, STATUS_LABEL).icon, "unverified");
});

test("MUTATION TARGET C8 -- completable vs damaged-only", () => {
  assert.equal(isCompletable(partial(42, 3)), true);
  assert.equal(isDamagedOnly(partial(42, 3)), false);
  assert.equal(isCompletable(partial(99, 0)), false);
  assert.equal(isDamagedOnly(partial(99, 0)), true);
  for (const r of [row(), unknown()]) {
    assert.equal(isCompletable(r), false);
    assert.equal(isDamagedOnly(r), false);
  }
});

test("detail line: own figure with bytes and time; unknown says so; 100 omits 'of'", () => {
  const p = row({ cache_complete_pct: 80, cache_expected_bytes: 4 * GB, cache_present_bytes: 3.2 * GB, cache_missing_chunks: 9 });
  assert.match(completenessLine(p), /^80% complete — 3\.2 GB of 4\.0 GB on disk · checked .+$/);
  assert.match(completenessLine(row()), /^100% complete — 3\.0 GB on disk · checked /);
  assert.equal(completenessLine(row({ cache_complete_checked_at: null })), "100% complete — 3.0 GB on disk");
  assert.equal(completenessLine(unknown()), UNKNOWN_LINE);
  assert.equal(UNKNOWN_LINE, "Completeness unknown — not measured for the current build.");
});

test("detail group line: only for a group row with a known group figure", () => {
  const parent = row({ appid: 500, group_cache_complete_pct: 75 });
  const pkg = row({ appid: 501, parent_appid: 500, group_cache_complete_pct: null });
  const [group] = groupGames([parent, pkg]).rows;
  assert.equal(groupCompletenessLine(group), "With content packages: 75% complete");
  assert.equal(groupCompletenessLine(parent), null, "a plain row");
  assert.equal(groupCompletenessLine(null), null);
  const [g2] = groupGames([{ ...parent, group_cache_complete_pct: null }, pkg]).rows;
  assert.equal(groupCompletenessLine(g2), null);
});

test("detail block: shown for a non-tool game with bytes (own or a package's), never for a tool app", () => {
  assert.equal(showsCompletenessBlock(row(), null), true);
  assert.equal(showsCompletenessBlock(row({ size_bytes: null }), null), false);
  assert.equal(showsCompletenessBlock(row({ tool_app: true }), null), false);
  const parent = row({ appid: 500, size_bytes: null });
  const pkg = row({ appid: 501, parent_appid: 500 });
  const [group] = groupGames([parent, pkg]).rows;
  assert.equal(showsCompletenessBlock(parent, group), true);
});

test("MUTATION TARGET C8 -- Verify sends members with bytes, skips tool apps and damaged-only members, appid order", () => {
  const members = [
    row({ appid: 30 }),
    partial(50, 4),
    { ...partial(99, 0), appid: 40 },
    row({ appid: 20, size_bytes: null }),
    row({ appid: 50, tool_app: true }),
    unknown({ appid: 5 }),
  ];
  assert.deepEqual(verifyAppids(members), [5, 10, 30]);
  assert.deepEqual(verifyAppids([]), []);
});

test("MUTATION TARGET C7 -- Incomplete list: known < 100, no tool app, no active job, no update flag; sorted by name", () => {
  const games = [
    { ...partial(42), appid: 1, name: "Zeta" },
    { ...partial(10), appid: 2, name: "alpha" },
    { ...row(), appid: 3, name: "Complete" },
    { ...unknown(), appid: 4, name: "Unknown" },
    { ...partial(30), appid: 5, name: "Tool", tool_app: true },
    { ...partial(30), appid: 6, name: "Running" },
    { ...partial(30), appid: 7, name: "Queued" },
    { ...partial(30), appid: 8, name: "Paused" },
    { ...partial(30), appid: 9, name: "Flagged", update_available: true },
    { ...partial(99, 0), appid: 10, name: "Damaged" },
    { ...partial(30), appid: 11, name: "Old job done" },
  ];
  const jobs = [
    { id: 1, appid: 6, type: "prefill", status: "running" },
    { id: 2, appid: 7, type: "prefill", status: "queued" },
    { id: 3, appid: 8, type: "prefill", status: "paused" },
    { id: 4, appid: 11, type: "prefill", status: "done" },
    { id: 5, appid: 1, type: "gc", status: "running" },
  ];
  assert.deepEqual(
    pendingIncomplete(games, jobs).map((g) => g.appid),
    [2, 10, 11, 1],
    "alpha, Damaged, Old job done, Zeta",
  );
  assert.deepEqual(pendingIncomplete(null, null), []);
});

test("Incomplete texts: heading, aria labels, the row's figure", () => {
  assert.equal(incompleteHeadingText(3), "Incomplete (3)");
  assert.equal(completeAllAriaLabel(1), "Complete all 1 game");
  assert.equal(completeAllAriaLabel(2), "Complete all 2 games");
  assert.equal(completeNowAriaLabel("Aurora"), "Complete now — Aurora");
  assert.equal(incompletePctText(partial(42)), "42%");
  assert.equal(incompletePctText(unknown()), "—");
  assert.equal(DAMAGED_NOTE, "Damaged chunks — SteamHangar can't repair these yet.");
});

test("Incomplete list key: changes with the figure and the completable state, not with unrelated fields", () => {
  const titleOf = (g) => g.name;
  const a = incompleteListKey([partial(42, 3)], titleOf);
  assert.notEqual(incompleteListKey([partial(43, 3)], titleOf), a);
  assert.notEqual(incompleteListKey([partial(42, 0)], titleOf), a);
  assert.equal(incompleteListKey([{ ...partial(42, 3), last_prefill_at: "2026-10-14T09:00:00Z" }], titleOf), a);
});

test("MUTATION TARGET C10 -- schedule note: window -> automatic in it, none -> manual only, unknown -> no claim", () => {
  assert.equal(
    incompleteScheduleNote({ enabled: true, window: "22:00-06:00", sweep_include_cached: false }),
    "These complete automatically in the schedule window 22:00–06:00.",
    "sweep_include_cached plays no part (user decision 2026-10-14)",
  );
  assert.equal(incompleteScheduleNote({ enabled: false, window: "22:00-06:00" }), "Manual only — no schedule window is set.");
  assert.equal(incompleteScheduleNote({ enabled: true, window: "" }), "Manual only — no schedule window is set.");
  assert.equal(incompleteScheduleNote(null), null);
});

test("MUTATION TARGET C9 -- verify toast per outcome; a RUNNING dedupe 'verifies on the next run'", () => {
  const ref = (status, deduplicated) => ({ appid: 10, job_id: 1, status, deduplicated });
  assert.deepEqual(summarizeVerifyStart([ref("queued", false)]), { message: "Queued to verify.", warn: false });
  assert.deepEqual(summarizeVerifyStart([ref("queued", false)], "complete"), { message: "Queued to complete.", warn: false });
  assert.deepEqual(summarizeVerifyStart([ref("queued", true)]), { message: "Already queued — it runs as a verify.", warn: false });
  assert.deepEqual(summarizeVerifyStart([ref("running", true)]), {
    message: "Already downloading — verifies on the next run.",
    warn: false,
  });
  assert.deepEqual(summarizeVerifyStart([ref("paused", true)]), { message: "Paused — it verifies when you resume it.", warn: true });
  assert.deepEqual(summarizeVerifyStart([]), { message: "Nothing to verify.", warn: false });
  assert.deepEqual(summarizeVerifyStart([], "complete"), { message: "Nothing to complete.", warn: false });
  const mixed = summarizeVerifyStart(
    [ref("queued", false), ref("queued", false), ref("running", true), ref("paused", true), ref("queued", true)],
    "complete",
  );
  assert.equal(
    mixed.message,
    "2 queued to complete · 1 already queued (runs as a verify) · 1 already downloading (verifies on the next run) · 1 paused (verifies when resumed)",
  );
  assert.equal(mixed.warn, true);
});

test("verify errors: a 5xx asks for a re-read, anything else shows the server's detail", () => {
  const server = describeVerifyStartError({ kind: ERROR_KINDS.SERVER, message: "boom" });
  assert.equal(server.refresh, true);
  assert.match(server.message, /some games may already be queued/);
  const v = describeVerifyStartError({ kind: ERROR_KINDS.VALIDATION, detail: "tool app", message: "x" });
  assert.deepEqual(v, { message: "tool app", warn: true, refresh: false });
});
