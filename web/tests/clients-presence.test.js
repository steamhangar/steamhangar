/**
 * Headless tests for the WP WEB-FEAT-3 presence helpers in
 * web/js/lib/clients-view.js and `formatAgo` in web/js/lib/format.js.
 *
 * The headline guarantee: presence is the SERVER's `presence` field (WP
 * AGENT-FEAT-1, `agent_reports.presence`), read verbatim — never recomputed
 * from `last_reported_at`/`offline_after` against this browser's clock
 * (docs/LEARNINGS.md: two call sites computing the same predicate diverge).
 * The fixtures below deliberately CONTRADICT the timestamps (online with a
 * 3-day-old report, offline with a report from a second ago), so any
 * client-side recomputation fails them.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  presenceOf,
  presenceWord,
  lastSeenText,
  agentVersionText,
  presenceLine,
  agentsSummary,
  agentsSummaryText,
} from "../js/lib/clients-view.js";
import { formatAgo } from "../js/lib/format.js";

const NOW = Date.parse("2026-10-03T12:00:00Z");
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

const onlineButOld = {
  client_id: "a",
  last_reported_at: iso(3 * 86_400_000),
  offline_after: iso(3 * 86_400_000 - 25 * 60_000),
  presence: "online",
};
const offlineButFresh = {
  client_id: "b",
  last_reported_at: iso(1000),
  offline_after: iso(-25 * 60_000),
  presence: "offline",
};

test("MUTATION TARGET: presence comes from the server field even when the timestamps say otherwise", () => {
  assert.equal(presenceOf(onlineButOld), "online");
  assert.equal(presenceWord(onlineButOld), "Online");
  assert.equal(presenceOf(offlineButFresh), "offline");
  assert.equal(presenceWord(offlineButFresh), "Offline");
});

test("MUTATION TARGET: the agents summary counts by the server field, not by timestamps", () => {
  assert.deepEqual(agentsSummary([onlineButOld, offlineButFresh, onlineButOld]), {
    online: 2,
    offline: 1,
    unknown: 0,
    total: 3,
  });
  assert.equal(agentsSummaryText([onlineButOld, offlineButFresh]), "Agents: 1 online, 1 offline");
});

test("a server older than AGENT-FEAT-1 (no presence field) reads 'Not reported', never a guess", () => {
  const legacy = { client_id: "c", last_reported_at: iso(1000) };
  assert.equal(presenceOf(legacy), null);
  assert.equal(presenceWord(legacy), "Not reported");
  assert.equal(presenceOf({ presence: "ONLINE" }), null, "only the two documented words count");
  assert.equal(
    agentsSummaryText([legacy, onlineButOld]),
    "Agents: 1 online, 0 offline, 1 without presence (server older than this web UI)",
  );
});

test("agents summary: null before the first answer, a plain sentence for an empty list", () => {
  assert.equal(agentsSummary(undefined), null);
  assert.equal(agentsSummaryText(null), null);
  assert.equal(agentsSummaryText([]), "Agents: none have reported yet");
});

test("MUTATION TARGET: agent_version null reads 'version not reported'", () => {
  assert.equal(agentVersionText({ agent_version: null }), "version not reported");
  assert.equal(agentVersionText({}), "version not reported");
  assert.equal(agentVersionText({ agent_version: "  " }), "version not reported");
  assert.equal(agentVersionText({ agent_version: "0.1.0" }), "agent 0.1.0");
});

test("last seen and the presence line use last_reported_at for words only", () => {
  assert.equal(lastSeenText({ last_reported_at: iso(4 * 60_000) }, NOW), "last seen 4 min ago");
  assert.equal(lastSeenText({ last_reported_at: null }, NOW), "last seen: not reported");
  assert.equal(
    presenceLine({ last_reported_at: iso(2 * 3_600_000), agent_version: null }, NOW),
    "last seen 2 h ago · version not reported",
  );
});

test("relative time: formatAgo boundaries", () => {
  assert.equal(formatAgo(iso(0), NOW), "just now");
  assert.equal(formatAgo(iso(59_000), NOW), "just now");
  assert.equal(formatAgo(iso(60_000), NOW), "1 min ago");
  assert.equal(formatAgo(iso(59 * 60_000), NOW), "59 min ago");
  assert.equal(formatAgo(iso(60 * 60_000), NOW), "1 h ago");
  assert.equal(formatAgo(iso(23 * 3_600_000 + 59 * 60_000), NOW), "23 h ago");
  assert.equal(formatAgo(iso(86_400_000), NOW), "1 day ago");
  assert.equal(formatAgo(iso(3 * 86_400_000), NOW), "3 days ago");
});

test("relative time: a future timestamp never yields a negative age; bad input is null", () => {
  assert.equal(formatAgo(iso(-30_000), NOW), "just now");
  assert.equal(formatAgo(iso(-5 * 60_000), NOW), "in the future (clocks differ)");
  assert.equal(formatAgo(null, NOW), null);
  assert.equal(formatAgo("", NOW), null);
  assert.equal(formatAgo("yesterday-ish", NOW), null);
  assert.equal(formatAgo(42, NOW), null);
});
