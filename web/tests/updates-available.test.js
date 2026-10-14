/**
 * Pure logic of the Downloads view's "Updates available" section (WP UPD-6,
 * `web/js/lib/updates-available.js`), plus the demo data it renders from:
 * the demo's flagged game appears, the demo's schedule window yields the
 * automatic-download note, and "Update now" (POST /v1/prefill) moves the
 * game into the queue and out of the list.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  pendingUpdates,
  updatesHeadingText,
  updateAllAriaLabel,
  updateNowAriaLabel,
  formatWindow,
  scheduleNote,
  summarizeUpdateStart,
  describeUpdateStartError,
  updatesListKey,
  sheetUpdateAppids,
  MANUAL_ONLY_NOTE,
  STARTING_LABEL,
} from "../js/lib/updates-available.js";
import { ERROR_KINDS } from "../js/errors.js";

const g = (appid, name, over = {}) => ({
  appid,
  name,
  status: "done",
  size_bytes: 2e9,
  needs_force: false,
  update_available: true,
  ...over,
});
const job = (appid, status, type = "prefill") => ({ id: appid * 10, appid, status, type });

test("MUTATION TARGET -- pendingUpdates: only update_available === true, sorted by name", () => {
  const rows = pendingUpdates([
    g(3, "zeta"),
    g(1, "Alpha"),
    g(2, "beta", { update_available: false }),
    g(4, "Gamma", { update_available: "true" }),
    g(5, "Delta", { update_available: undefined }),
  ]);
  assert.deepEqual(rows.map((r) => r.appid), [1, 3]);
});

test("MUTATION TARGET -- pendingUpdates: a queued, running or paused PREFILL job takes the game out; finished jobs and GC jobs do not", () => {
  const games = [g(1, "A"), g(2, "B"), g(3, "C"), g(4, "D"), g(5, "E"), g(6, "F")];
  const jobs = [job(1, "queued"), job(2, "running"), job(3, "paused"), job(4, "done"), job(5, "error"), job(6, "running", "gc")];
  assert.deepEqual(pendingUpdates(games, jobs).map((r) => r.appid), [4, 5, 6]);
});

test("pendingUpdates: Steam tool apps never appear (POST /v1/prefill refuses them); null inputs are empty", () => {
  assert.deepEqual(pendingUpdates([g(228980, "Redist", { tool_app: true })], []), []);
  assert.deepEqual(pendingUpdates(null, null), []);
  assert.deepEqual(pendingUpdates(undefined, [job(1, "queued")]), []);
});

test("pendingUpdates: sorts by the caller's title (unnamed apps), ties by appid", () => {
  const titleOf = (r) => r.name ?? `App ${r.appid}`;
  const rows = pendingUpdates([g(9, null), g(8, null), g(7, "Aa")], [], titleOf);
  assert.deepEqual(rows.map((r) => r.appid), [7, 8, 9]);
});

test("MUTATION TARGET -- sheetUpdateAppids: flagged members without an active job, in appid order", () => {
  const members = [g(500, "Parent", { update_available: false }), g(502, "Pack B"), g(501, "Pack A"), g(503, "Pack C")];
  assert.deepEqual(sheetUpdateAppids(members, [job(503, "queued")]), [501, 502]);
  assert.deepEqual(sheetUpdateAppids([g(1, "A", { update_available: false })], []), []);
});

test("wording: heading, aria-labels", () => {
  assert.equal(STARTING_LABEL, "Starting…");
  assert.equal(updatesHeadingText(7), "Updates available (7)");
  assert.equal(updateAllAriaLabel(1), "Update all 1 game");
  assert.equal(updateAllAriaLabel(7), "Update all 7 games");
  assert.equal(updateNowAriaLabel("Emberreach"), "Update now — Emberreach");
});

test("formatWindow: HH:MM-HH:MM gets an en dash; anything else is shown as configured", () => {
  assert.equal(formatWindow("22:00-06:00"), "22:00–06:00");
  assert.equal(formatWindow(" 1:30 - 5:00 "), "1:30–5:00");
  assert.equal(formatWindow("weird"), "weird");
});

test("MUTATION TARGET -- scheduleNote: a window -> automatic; none -> manual only; unknown -> no claim", () => {
  assert.equal(
    scheduleNote({ enabled: true, window: "22:00-06:00", sweep_include_cached: true }),
    "These download automatically in the schedule window 22:00–06:00.",
  );
  assert.equal(scheduleNote({ enabled: false, window: null, sweep_include_cached: true }), MANUAL_ONLY_NOTE);
  assert.equal(MANUAL_ONLY_NOTE, "Manual only — no schedule window is set.");
  // `enabled` is what the scheduler acts on; a window string alone is not enough.
  assert.equal(scheduleNote({ enabled: false, window: "22:00-06:00" }), MANUAL_ONLY_NOTE);
  assert.equal(scheduleNote(null), null);
  assert.equal(scheduleNote(undefined), null);
});

test("MUTATION TARGET -- scheduleNote: with 'Include cached games' off the sweep only covers installed games — the note says so", () => {
  const note = scheduleNote({ enabled: true, window: "01:00-05:00", sweep_include_cached: false });
  assert.equal(
    note,
    "Those installed on a PC download automatically in the schedule window 01:00–05:00; the others only when you start them here.",
  );
});

test("summarizeUpdateStart: every PrefillJobRef outcome reads honestly", () => {
  assert.deepEqual(summarizeUpdateStart([], []), { message: "Nothing to update.", warn: false });
  assert.deepEqual(summarizeUpdateStart([{ appid: 1, job_id: 9, status: "queued", deduplicated: false }], []), {
    message: "1 queued for update",
    warn: false,
  });
  const mixed = summarizeUpdateStart(
    [
      { appid: 1, job_id: 9, status: "queued", deduplicated: false },
      { appid: 2, job_id: 8, status: "queued", deduplicated: true },
      { appid: 3, job_id: 7, status: "running", deduplicated: true },
      { appid: 4, job_id: 6, status: "paused", deduplicated: true },
    ],
    [],
  );
  assert.equal(mixed.message, "1 queued for update · 1 already queued · 1 already in progress · 1 paused — resume or cancel it first");
  assert.equal(mixed.warn, true);
});

test("MUTATION TARGET -- summarizeUpdateStart: the forced-run note counts only freshly queued apps with needs_force", () => {
  const games = [g(1, "A", { needs_force: true }), g(2, "B", { needs_force: true })];
  const out = summarizeUpdateStart(
    [
      { appid: 1, job_id: 9, status: "queued", deduplicated: false },
      { appid: 2, job_id: 8, status: "running", deduplicated: true },
    ],
    games,
  );
  assert.equal(out.message, "1 queued for update · 1 already in progress (1 forced — those run full, disk-speed re-checks and may take longer)");
});

test("describeUpdateStartError: a 5xx asks for a re-read (some may be queued); others report the reason", () => {
  const server = describeUpdateStartError({ kind: ERROR_KINDS.SERVER, message: "500" });
  assert.equal(server.refresh, true);
  assert.match(server.message, /some updates may already be queued/);
  const val = describeUpdateStartError({ kind: ERROR_KINDS.VALIDATION, detail: "app 228980 is a Steam tool app" });
  assert.deepEqual(val, { message: "app 228980 is a Steam tool app", warn: true, refresh: false });
  assert.equal(describeUpdateStartError(undefined).message, "Could not start the update.");
});

test("updatesListKey changes with appid, title and size, not otherwise", () => {
  const t = (r) => r.name;
  const s = (r) => String(r.size_bytes);
  const a = updatesListKey([g(1, "A")], t, s);
  assert.equal(updatesListKey([g(1, "A")], t, s), a);
  assert.notEqual(updatesListKey([g(1, "B")], t, s), a);
  assert.notEqual(updatesListKey([g(1, "A", { size_bytes: 3e9 })], t, s), a);
  assert.notEqual(updatesListKey([g(2, "A")], t, s), a);
});

// ---- demo mode ------------------------------------------------------------

test("demo: the flagged game is listed, the demo window gives the automatic note, and 'Update now' moves it into the queue", async () => {
  const { demoRequest, resetDemoData } = await import("../js/demo-data.js");
  resetDemoData();
  try {
    const games = await demoRequest("GET", "/v1/games");
    const jobs = await demoRequest("GET", "/v1/jobs");
    const rows = pendingUpdates(games, jobs);
    assert.ok(rows.length >= 1, "at least one flagged demo game");
    assert.ok(rows.some((r) => r.name === "Emberreach"), "the demo's flagged game");
    const schedule = await demoRequest("GET", "/v1/schedule");
    assert.equal(scheduleNote(schedule), "These download automatically in the schedule window 22:00–06:00.");

    const refs = await demoRequest("POST", "/v1/prefill", { body: { appids: rows.map((r) => r.appid) } });
    assert.match(summarizeUpdateStart(refs, games).message, /queued for update/);
    const after = pendingUpdates(await demoRequest("GET", "/v1/games"), await demoRequest("GET", "/v1/jobs"));
    assert.deepEqual(after, [], "every listed game now has a queued job");
  } finally {
    resetDemoData();
  }
});
