/**
 * `web/js/lib/decision-support.js` after WP WEB-FEAT-SUGGEST-2: the Phase 4h
 * statements that moved out of the removed suggestions panel — the card's
 * "Never played" pill, the detail sheet's "Last update downloaded" line and
 * the "update check due" notification. The panel-era honesty pins carry
 * over (absent playtime is never zero, bad day counts say nothing, no
 * judgemental or inactivity wording, `rtime_last_played` never read).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isNeverPlayedCached,
  lastUpdateDownloadedText,
  lastUpdateCheckAt,
  staleUpdateChecks,
  updateCheckDueWording,
  daysSince,
  UPDATE_CHECK_DUE_DAYS,
  UPDATE_CHECK_HINT,
  NEVER_PLAYED_LABEL,
  NEVER_PLAYED_COMPACT_LABEL,
  NEVER_PLAYED_DESCRIPTION,
} from "../js/lib/decision-support.js";

const NOW = Date.parse("2026-10-13T12:00:00Z");
const daysAgo = (n) => new Date(NOW - n * 86_400_000).toISOString();

// ---------------------------------------------------------------------
// "Never played" (card pill)
// ---------------------------------------------------------------------

test("wording: the pill's three strings are literal", () => {
  assert.equal(NEVER_PLAYED_LABEL, "Never played");
  assert.equal(NEVER_PLAYED_COMPACT_LABEL, "Unplayed");
  assert.equal(NEVER_PLAYED_DESCRIPTION, "Fully cached, never played");
});

test("never played: a real, explicit zero playtime on a game with cached bytes", () => {
  assert.equal(isNeverPlayedCached({ size_bytes: 1e9, owned_playtime_forever: 0 }), true);
});

test("MUTATION PIN: an ABSENT playtime is unknown, never zero (the ADR-0010 default gate)", () => {
  assert.equal(isNeverPlayedCached({ size_bytes: 1e9 }), false);
  assert.equal(isNeverPlayedCached({ size_bytes: 1e9, owned_playtime_forever: null }), false);
});

test("negative, non-finite or non-number playtime is unknown, never zero", () => {
  for (const v of [-1, NaN, Infinity, "0", false]) {
    assert.equal(isNeverPlayedCached({ size_bytes: 1e9, owned_playtime_forever: v }), false, String(v));
  }
});

test("MUTATION PIN (privacy): any non-zero playtime is not 'never played'", () => {
  for (const v of [1, 26, 4312]) assert.equal(isNeverPlayedCached({ size_bytes: 1e9, owned_playtime_forever: v }), false);
});

test("no cached bytes, or a Steam tool app, is never 'never played'", () => {
  for (const size_bytes of [null, 0, undefined]) {
    assert.equal(isNeverPlayedCached({ size_bytes, owned_playtime_forever: 0 }), false);
  }
  assert.equal(isNeverPlayedCached({ size_bytes: 1e9, owned_playtime_forever: 0, tool_app: true }), false);
});

test("MUTATION PIN (privacy): rtime_last_played is never read", () => {
  assert.equal(isNeverPlayedCached({ size_bytes: 1e9, rtime_last_played: 0 }), false);
  assert.equal(isNeverPlayedCached({ size_bytes: 1e9, owned_playtime_forever: 0, rtime_last_played: 1_700_000_000 }), true);
});

// ---------------------------------------------------------------------
// "Last update downloaded …" (detail sheet)
// ---------------------------------------------------------------------

test("update-downloaded line: 'changed' + a real day count, past tense, never a cadence", () => {
  const g = (n) => ({ manifest_change_frequency: "changed", manifest_days_since_last_change: n });
  assert.equal(lastUpdateDownloadedText(g(0)), "Last update downloaded today");
  assert.equal(lastUpdateDownloadedText(g(1)), "Last update downloaded yesterday");
  assert.equal(lastUpdateDownloadedText(g(2)), "Last update downloaded 2 days ago");
  assert.doesNotMatch(lastUpdateDownloadedText(g(9)), /every|usually|often/i);
});

test("update-downloaded line: 'stable', 'insufficient_data' and null say nothing (the 'unchanged for N days' statement was dropped)", () => {
  for (const f of ["stable", "insufficient_data", null, undefined]) {
    assert.equal(lastUpdateDownloadedText({ manifest_change_frequency: f, manifest_days_since_last_change: 3, manifest_observation_days: 120 }), null, String(f));
  }
});

test("MUTATION PIN: bad day counts (NaN, negative, fraction, Infinity, oversized, missing) say nothing", () => {
  for (const n of [NaN, -5, 3.7, Infinity, 1e21, null, undefined, "4"]) {
    assert.equal(lastUpdateDownloadedText({ manifest_change_frequency: "changed", manifest_days_since_last_change: n }), null, String(n));
  }
});

// ---------------------------------------------------------------------
// "Update check due" (notification)
// ---------------------------------------------------------------------

test("daysSince floors, rejects unparsable and future timestamps", () => {
  assert.equal(daysSince(daysAgo(14.9), NOW), 14);
  assert.equal(daysSince("nope", NOW), null);
  assert.equal(daysSince(daysAgo(-1), NOW), null);
  assert.equal(daysSince(null, NOW), null);
});

test("last update check = the newer of update_checked_at and last_manifest_check", () => {
  assert.equal(lastUpdateCheckAt({ update_checked_at: daysAgo(3), last_manifest_check: daysAgo(30) }), daysAgo(3));
  assert.equal(lastUpdateCheckAt({ update_checked_at: daysAgo(30), last_manifest_check: daysAgo(3) }), daysAgo(3));
  assert.equal(lastUpdateCheckAt({ update_checked_at: null, last_manifest_check: daysAgo(3) }), daysAgo(3));
  assert.equal(lastUpdateCheckAt({}), null);
});

const cached = (appid, checkedDaysAgo, extra = {}) => ({
  appid,
  size_bytes: 1e9,
  update_checked_at: checkedDaysAgo === null ? null : daysAgo(checkedDaysAgo),
  last_manifest_check: null,
  ...extra,
});

test("MUTATION PIN: the threshold is 14 days (was 30 in the panel) — 13 days is not due, 14 is", () => {
  assert.equal(UPDATE_CHECK_DUE_DAYS, 14);
  assert.deepEqual(staleUpdateChecks([cached(1, 13.9)], { nowMs: NOW }), []);
  assert.deepEqual(staleUpdateChecks([cached(1, 14)], { nowMs: NOW }), [{ appid: 1, days: 14 }]);
});

test("only cached games count; never-checked, owned-only and tool apps are left out", () => {
  const games = [
    cached(1, 20),
    cached(2, 20, { size_bytes: 0 }),
    cached(3, null),
    cached(4, 20, { owned_only: true }),
    cached(5, 20, { tool_app: true }),
    null,
    { appid: "x" },
  ];
  assert.deepEqual(staleUpdateChecks(games, { nowMs: NOW }).map((i) => i.appid), [1]);
  assert.deepEqual(staleUpdateChecks(undefined, { nowMs: NOW }), []);
});

test("a recent confirmed-current run clears an old oracle check", () => {
  assert.deepEqual(staleUpdateChecks([cached(1, 40, { last_manifest_check: daysAgo(2) })], { nowMs: NOW }), []);
});

test("oldest first, ties by appid", () => {
  const out = staleUpdateChecks([cached(3, 20), cached(1, 30), cached(2, 20)], { nowMs: NOW });
  assert.deepEqual(out.map((i) => i.appid), [1, 2, 3]);
});

test("wording: one game, two, three, and more than three (names listed, the rest counted)", () => {
  const name = (appid) => ["", "Aurora", "Briar", "Cinder", "Dusk", "Ember"][appid];
  assert.deepEqual(updateCheckDueWording([{ appid: 1, days: 21 }], name), {
    title: "Aurora",
    text: "Last checked for updates 21 days ago.",
  });
  assert.deepEqual(updateCheckDueWording([{ appid: 1, days: 21 }, { appid: 2, days: 15 }], name), {
    title: "2 games",
    text: "Not checked for updates in 14 days or more: Aurora and Briar.",
  });
  assert.equal(
    updateCheckDueWording([1, 2, 3].map((appid) => ({ appid, days: 20 })), name).text,
    "Not checked for updates in 14 days or more: Aurora, Briar and Cinder.",
  );
  assert.deepEqual(updateCheckDueWording([1, 2, 3, 4, 5].map((appid) => ({ appid, days: 20 })), name), {
    title: "5 games",
    text: "Not checked for updates in 14 days or more: Aurora, Briar, Cinder and 2 more.",
  });
});

test("the hint: 'Check updates' is named only when the button exists; oracle off says so; unknown says nothing extra", () => {
  const one = [{ appid: 1, days: 21 }];
  const name = () => "Aurora";
  assert.equal(
    updateCheckDueWording(one, name, UPDATE_CHECK_HINT.AVAILABLE).text,
    'Last checked for updates 21 days ago. Use "Check updates" in the Library to check again.',
  );
  assert.equal(
    updateCheckDueWording(one, name, UPDATE_CHECK_HINT.DISABLED).text,
    "Last checked for updates 21 days ago. Update checks are turned off on this vault.",
  );
  assert.equal(updateCheckDueWording(one, name, UPDATE_CHECK_HINT.NONE).text, "Last checked for updates 21 days ago.");
  assert.equal(updateCheckDueWording(one, name).text, "Last checked for updates 21 days ago.", "default: no target named");
  for (const h of [UPDATE_CHECK_HINT.DISABLED, UPDATE_CHECK_HINT.NONE]) {
    assert.doesNotMatch(updateCheckDueWording([{ appid: 1, days: 20 }, { appid: 2, days: 20 }], name, h).text, /Check updates/);
  }
  assert.match(
    updateCheckDueWording([{ appid: 1, days: 20 }, { appid: 2, days: 20 }], name, UPDATE_CHECK_HINT.AVAILABLE).text,
    /^Not checked for updates in 14 days or more: Aurora and Aurora\. Use "Check updates" in the Library to check again\.$/,
  );
});

test("no wording, across all three facts, judges the player or names an inactivity span", () => {
  const texts = [
    lastUpdateDownloadedText({ manifest_change_frequency: "changed", manifest_days_since_last_change: 5 }),
    updateCheckDueWording([{ appid: 1, days: 30 }], () => "X").text,
    updateCheckDueWording([{ appid: 1, days: 30 }, { appid: 2, days: 30 }], () => "X").text,
    NEVER_PLAYED_LABEL,
    NEVER_PLAYED_COMPACT_LABEL,
    NEVER_PLAYED_DESCRIPTION,
  ];
  for (const t of texts) {
    assert.doesNotMatch(t, /should|haven't played|you never|recommend|wasted|forgot/i, t);
    assert.doesNotMatch(t, /played .* ago|last played/i, t);
  }
});
