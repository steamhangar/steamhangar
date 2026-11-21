/**
 * Headless tests for web/js/lib/game-status.js (WP 4a.3).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  KIND,
  INSTALLED_BADGE,
  findLiveJob,
  indexLiveJobsByAppid,
  hasVisibleCacheContent,
  hasProtectedCacheContent,
  dispKind,
  statusAction,
  isJobStateTransition,
  installedBadgeState,
  installedOnSummary,
  installedBadgeText,
  installedBadgeCompactText,
  installedSectionPresence,
} from "../js/lib/game-status.js";

const game = (over) => ({
  appid: 1,
  name: "Aurora Cascade",
  status: "idle",
  last_prefill_at: null,
  depot_count: 0,
  size_bytes: null,
  needs_force: false,
  ...over,
});

const job = (over) => ({
  id: 1,
  appid: 1,
  type: "prefill",
  status: "running",
  ...over,
});

// ---------------------------------------------------------------------
// findLiveJob / indexLiveJobsByAppid
// ---------------------------------------------------------------------

test("findLiveJob matches only prefill jobs in running/paused", () => {
  const jobs = [
    job({ id: 1, appid: 10, status: "running" }),
    job({ id: 2, appid: 20, status: "paused" }),
    job({ id: 3, appid: 30, status: "queued" }),
    job({ id: 4, appid: 40, status: "done" }),
    job({ id: 5, appid: 50, status: "running", type: "gc" }),
  ];
  assert.equal(findLiveJob(jobs, 10).id, 1);
  assert.equal(findLiveJob(jobs, 20).id, 2);
  assert.equal(findLiveJob(jobs, 30), undefined, "queued is not live (matches mockup jobFor)");
  assert.equal(findLiveJob(jobs, 40), undefined);
  assert.equal(findLiveJob(jobs, 50), undefined, "a GC job must never drive the download pill");
});

test("indexLiveJobsByAppid is a Map keyed by appid, same filtering as findLiveJob", () => {
  const jobs = [job({ id: 1, appid: 10, status: "running" }), job({ id: 2, appid: 20, status: "queued" })];
  const map = indexLiveJobsByAppid(jobs);
  assert.equal(map.size, 1);
  assert.equal(map.get(10).id, 1);
  assert.equal(map.get(20), undefined);
});

test("indexLiveJobsByAppid tolerates a null/undefined jobs snapshot", () => {
  assert.equal(indexLiveJobsByAppid(undefined).size, 0);
  assert.equal(indexLiveJobsByAppid(null).size, 0);
});

// ---------------------------------------------------------------------
// hasVisibleCacheContent / hasProtectedCacheContent
// ---------------------------------------------------------------------

test("hasVisibleCacheContent requires a positive numeric size_bytes", () => {
  assert.equal(hasVisibleCacheContent(game({ size_bytes: 100 })), true);
  assert.equal(hasVisibleCacheContent(game({ size_bytes: 0 })), false);
  assert.equal(hasVisibleCacheContent(game({ size_bytes: null })), false);
  assert.equal(hasVisibleCacheContent(game({ size_bytes: undefined })), false);
});

test("hasProtectedCacheContent mirrors the server predicate: idle + never-prefilled + no job => false", () => {
  assert.equal(
    hasProtectedCacheContent(game({ status: "idle", last_prefill_at: null }), false),
    false,
  );
});

test("hasProtectedCacheContent: any of status!=idle / last_prefill_at set / active job protects", () => {
  assert.equal(hasProtectedCacheContent(game({ status: "done" }), false), true);
  assert.equal(hasProtectedCacheContent(game({ status: "error" }), false), true);
  assert.equal(
    hasProtectedCacheContent(game({ status: "idle", last_prefill_at: "2026-08-01T00:00:00Z" }), false),
    true,
  );
  assert.equal(hasProtectedCacheContent(game({ status: "idle", last_prefill_at: null }), true), true);
});

test("hasVisibleCacheContent and hasProtectedCacheContent can disagree (the 'last cached remnant' case)", () => {
  // status 'done' but the depot behind it was just reclaimed as an
  // orphaned remnant by an UNRELATED delete — api/README.md "Last cached
  // remnants". The grid must show this as Not cached; the deletion-side
  // predicate must still treat it as protecting a depot until it is
  // re-prefilled (fail-closed).
  const remnant = game({ status: "done", size_bytes: null, last_prefill_at: "2026-08-01T00:00:00Z" });
  assert.equal(hasVisibleCacheContent(remnant), false);
  assert.equal(hasProtectedCacheContent(remnant, false), true);
});

// ---------------------------------------------------------------------
// dispKind
// ---------------------------------------------------------------------

test("dispKind: a live running job overrides cache state", () => {
  assert.equal(dispKind(game({ status: "done", size_bytes: 100 }), job({ status: "running" })), KIND.RUNNING);
});
test("dispKind: a live paused job overrides cache state", () => {
  assert.equal(dispKind(game({ status: "idle" }), job({ status: "paused" })), KIND.PAUSED);
});
test("dispKind: no live job, status error => ERROR regardless of bytes", () => {
  assert.equal(dispKind(game({ status: "error", size_bytes: 500 }), undefined), KIND.ERROR);
  assert.equal(dispKind(game({ status: "error", size_bytes: null }), undefined), KIND.ERROR);
});
test("dispKind: no live job, done + visible bytes => CACHED", () => {
  assert.equal(dispKind(game({ status: "done", size_bytes: 500 }), undefined), KIND.CACHED);
});
test("dispKind: no live job, done but zero/no bytes => NONE (invariant, mockup round 5 finding 6)", () => {
  assert.equal(dispKind(game({ status: "done", size_bytes: null }), undefined), KIND.NONE);
  assert.equal(dispKind(game({ status: "done", size_bytes: 0 }), undefined), KIND.NONE);
});
test("dispKind: idle, no bytes => NONE", () => {
  assert.equal(dispKind(game({ status: "idle", size_bytes: null }), undefined), KIND.NONE);
});

// ---------------------------------------------------------------------
// statusAction
// ---------------------------------------------------------------------

test("statusAction: null while multi-select is active, regardless of state", () => {
  assert.equal(statusAction(game({ status: "idle" }), undefined, true), null);
  assert.equal(statusAction(game({ status: "done", size_bytes: 5 }), job({ status: "running" }), true), null);
});
test("statusAction: running job => pause", () => {
  const a = statusAction(game(), job({ status: "running" }), false);
  assert.equal(a.type, "pause");
});
test("statusAction: paused job => resume", () => {
  const a = statusAction(game(), job({ status: "paused" }), false);
  assert.equal(a.type, "resume");
});
test("statusAction: not-cached game => download", () => {
  const a = statusAction(game({ status: "idle", size_bytes: null }), undefined, false);
  assert.equal(a.type, "download");
  assert.equal(a.title, "Download to cache");
});
test("statusAction: errored game => download, titled as a retry", () => {
  const a = statusAction(game({ status: "error" }), undefined, false);
  assert.equal(a.type, "download");
  assert.equal(a.title, "Retry download");
});
test("statusAction: a cached game is inert (never a silent re-download)", () => {
  assert.equal(statusAction(game({ status: "done", size_bytes: 5 }), undefined, false), null);
});

// ---------------------------------------------------------------------
// isJobStateTransition — the round-7 "don't rebuild on a no-op tick" guard
// ---------------------------------------------------------------------

test("isJobStateTransition: same status (even with a grown log_excerpt) is NOT a transition", () => {
  const a = job({ status: "running", log_excerpt: "line 1" });
  const b = job({ status: "running", log_excerpt: "line 1\nline 2\nline 3" });
  assert.equal(isJobStateTransition(a, b), false);
});
test("isJobStateTransition: a status change IS a transition", () => {
  assert.equal(isJobStateTransition(job({ status: "running" }), job({ status: "paused" })), true);
});
test("isJobStateTransition: a brand-new job (no prev) is always a transition", () => {
  assert.equal(isJobStateTransition(undefined, job({ status: "running" })), true);
});
test("isJobStateTransition: a job disappearing (no curr) is always a transition", () => {
  assert.equal(isJobStateTransition(job({ status: "running" }), undefined), true);
});

// ---------------------------------------------------------------------
// installedBadgeState / installedOnSummary / installedBadgeText (WP AG-2)
// ---------------------------------------------------------------------

const installedOn = (n, over = {}) =>
  Array.from({ length: n }, (_, i) => ({
    client_id: `client-${i}`,
    reported_at: "2026-08-22T09:15:03Z",
    ...over,
  }));

test("MUTATION TARGET -- installedBadgeState: empty installed_on is NONE regardless of cache state", () => {
  assert.equal(installedBadgeState(game({ installed_on: [], size_bytes: 500 })), INSTALLED_BADGE.NONE);
  assert.equal(installedBadgeState(game({ installed_on: [], size_bytes: null })), INSTALLED_BADGE.NONE);
});

test("installedBadgeState: missing installed_on field (older fixture, pre-AG-1) is also NONE, not a throw", () => {
  assert.equal(installedBadgeState(game({})), INSTALLED_BADGE.NONE);
});

test("MUTATION TARGET -- installedBadgeState: non-empty installed_on + visible bytes is CACHED", () => {
  assert.equal(installedBadgeState(game({ installed_on: installedOn(1), size_bytes: 500 })), INSTALLED_BADGE.CACHED);
});

test("MUTATION TARGET -- installedBadgeState: non-empty installed_on + no visible bytes is NOT_CACHED (both directions pinned)", () => {
  assert.equal(installedBadgeState(game({ installed_on: installedOn(1), size_bytes: null })), INSTALLED_BADGE.NOT_CACHED);
  assert.equal(installedBadgeState(game({ installed_on: installedOn(1), size_bytes: 0 })), INSTALLED_BADGE.NOT_CACHED);
});

test("installedBadgeState: the 'last cached remnant' case (status done, no visible bytes) is still NOT_CACHED when installed", () => {
  // Mirrors the hasVisibleCacheContent/hasProtectedCacheContent divergence
  // test above -- the badge must follow the BYTES predicate, same as the
  // grid's own dispKind, not the status-based one.
  const remnant = game({
    status: "done",
    size_bytes: null,
    last_prefill_at: "2026-08-01T00:00:00Z",
    installed_on: installedOn(1),
  });
  assert.equal(installedBadgeState(remnant), INSTALLED_BADGE.NOT_CACHED);
});

test("installedOnSummary: empty/missing list is null (nothing honest to print)", () => {
  assert.equal(installedOnSummary([]), null);
  assert.equal(installedOnSummary(null), null);
  assert.equal(installedOnSummary(undefined), null);
});

test("installedOnSummary: one entry is just its client_id", () => {
  assert.equal(installedOnSummary(installedOn(1)), "client-0");
});

test("installedOnSummary: more than one entry appends a '+N' count of the REST", () => {
  assert.equal(installedOnSummary(installedOn(3)), "client-0 +2");
});

test("MUTATION TARGET -- installedBadgeText: NONE never produces a sentence (no 'not installed' claim)", () => {
  assert.equal(installedBadgeText(INSTALLED_BADGE.NONE, null), null);
});

test("installedBadgeText: CACHED names the client(s), no 'not cached' wording", () => {
  const text = installedBadgeText(INSTALLED_BADGE.CACHED, "client-0");
  assert.match(text, /^Installed on client-0$/);
  assert.doesNotMatch(text, /not cached/i);
});

test("MUTATION TARGET -- installedBadgeText: NOT_CACHED states both facts -- installed, and not cached", () => {
  const text = installedBadgeText(INSTALLED_BADGE.NOT_CACHED, "client-0");
  assert.match(text, /installed/i);
  assert.match(text, /not cached/i);
});

test("installedBadgeText: an unrecognised state is also null, not a thrown error", () => {
  assert.equal(installedBadgeText("bogus", "client-0"), null);
});

test("installedBadgeCompactText: NONE never produces a sentence (no 'not installed' claim)", () => {
  assert.equal(installedBadgeCompactText(INSTALLED_BADGE.NONE, null), null);
});

test("MUTATION TARGET -- installedBadgeCompactText: CACHED is just the summary, no lead-in", () => {
  assert.equal(installedBadgeCompactText(INSTALLED_BADGE.CACHED, "client-0"), "client-0");
});

test("MUTATION TARGET -- installedBadgeCompactText: NOT_CACHED keeps 'not cached', drops the 'Installed,' lead-in", () => {
  const text = installedBadgeCompactText(INSTALLED_BADGE.NOT_CACHED, "client-0");
  assert.match(text, /not cached/i);
  assert.doesNotMatch(text, /^Installed/, "the compact form must not restate the 'Installed' lead-in the full form uses");
  assert.match(text, /client-0/);
});

test("installedBadgeCompactText is always no longer than installedBadgeText for the same state/summary (it exists to be shorter)", () => {
  for (const state of [INSTALLED_BADGE.CACHED, INSTALLED_BADGE.NOT_CACHED]) {
    const full = installedBadgeText(state, "workshop-pc +2");
    const compact = installedBadgeCompactText(state, "workshop-pc +2");
    assert.ok(compact.length < full.length, `compact form for ${state} must be strictly shorter than the full form`);
  }
});

test("installedBadgeCompactText: an unrecognised state is also null, not a thrown error", () => {
  assert.equal(installedBadgeCompactText("bogus", "client-0"), null);
});

// ---------------------------------------------------------------------
// installedSectionPresence (WP AG-2 review S4 -- the detail sheet's
// structural-key input, narrower than installedBadgeState on purpose)
// ---------------------------------------------------------------------

test("MUTATION TARGET -- installedSectionPresence: NONE collapses to 'none'", () => {
  assert.equal(installedSectionPresence(game({ installed_on: [] })), "none");
});

test("MUTATION TARGET -- installedSectionPresence: CACHED and NOT_CACHED BOTH collapse to 'present' -- the whole S4 fix", () => {
  // If this collapsed cached/not_cached into DIFFERENT values again, a live
  // download crossing size_bytes from 0 to positive (dispKind staying
  // "running" throughout, per game-status.js's own dispKind rule) would
  // force a full detail-sheet re-render every time, for every installed
  // game -- the exact regression S4 describes.
  const cached = installedSectionPresence(game({ installed_on: installedOn(1), size_bytes: 500 }));
  const notCached = installedSectionPresence(game({ installed_on: installedOn(1), size_bytes: null }));
  assert.equal(cached, "present");
  assert.equal(notCached, "present");
  assert.equal(cached, notCached);
});

test("installedSectionPresence: missing installed_on field is also 'none', not a throw", () => {
  assert.equal(installedSectionPresence(game({})), "none");
});
