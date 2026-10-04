/**
 * Headless tests for web/js/lib/clients-view.js (WP 4a.7 DoD).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  partitionClients,
  hitRatePercent,
  addressesText,
  describeHealthyClient,
  describeBypassClient,
  bypassBannerText,
} from "../js/lib/clients-view.js";

function client(overrides) {
  return {
    client_id: "workshop-pc",
    first_seen: "2026-08-01T00:00:00Z",
    last_reported_at: "2026-08-10T00:00:00Z",
    app_count: 10,
    source_addrs: ["10.10.0.21"],
    cache_hits: 5,
    cache_misses: 1,
    bytes_served: 1234,
    last_seen_in_cache_log: "2026-08-10T00:00:00Z",
    bypass_suspected: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------
// partitionClients
// ---------------------------------------------------------------------

test("partitionClients: splits by bypass_suspected, preserving order within each bucket", () => {
  const a = client({ client_id: "a", bypass_suspected: true });
  const b = client({ client_id: "b", bypass_suspected: false });
  const c = client({ client_id: "c", bypass_suspected: true });
  const { bypassing, healthy } = partitionClients([a, b, c]);
  assert.deepEqual(bypassing.map((x) => x.client_id), ["a", "c"]);
  assert.deepEqual(healthy.map((x) => x.client_id), ["b"]);
});

test("partitionClients: empty/null/undefined input -> both buckets empty", () => {
  for (const input of [[], null, undefined]) {
    const { bypassing, healthy } = partitionClients(input);
    assert.deepEqual(bypassing, []);
    assert.deepEqual(healthy, []);
  }
});

// ---------------------------------------------------------------------
// hitRatePercent
// ---------------------------------------------------------------------

test("hitRatePercent: rounds hits/(hits+misses) to a whole-number percentage", () => {
  assert.equal(hitRatePercent(client({ cache_hits: 96, cache_misses: 4 })), 96);
  assert.equal(hitRatePercent(client({ cache_hits: 1, cache_misses: 2 })), 33); // 33.33.. -> 33
  assert.equal(hitRatePercent(client({ cache_hits: 2, cache_misses: 1 })), 67); // 66.67 -> 67
});

test("hitRatePercent: null (never fabricated 0%) when there are zero total requests", () => {
  assert.equal(hitRatePercent(client({ cache_hits: 0, cache_misses: 0 })), null);
});

test("hitRatePercent: non-finite/negative/missing counters treated as 0, not NaN/crash", () => {
  // NaN/negative hits count as 0 -> 0 hits out of 5 total (the misses) = 0%.
  assert.equal(hitRatePercent(client({ cache_hits: NaN, cache_misses: 5 })), 0);
  assert.equal(hitRatePercent(client({ cache_hits: -3, cache_misses: 5 })), 0);
  assert.equal(hitRatePercent({}), null);
  assert.equal(hitRatePercent(null), null);
});

// ---------------------------------------------------------------------
// addressesText
// ---------------------------------------------------------------------

test("addressesText: joins multiple addresses", () => {
  assert.equal(
    addressesText(client({ source_addrs: ["10.10.0.21", "10.10.0.22"] })),
    "10.10.0.21, 10.10.0.22",
  );
});

test("addressesText: honest 'no known address' for an empty/missing list (pre-schema-v9 reports)", () => {
  assert.equal(addressesText(client({ source_addrs: [] })), "no known address");
  assert.equal(addressesText(client({ source_addrs: undefined })), "no known address");
  assert.equal(addressesText(null), "no known address");
});

// ---------------------------------------------------------------------
// describeHealthyClient / describeBypassClient
// ---------------------------------------------------------------------

test("describeHealthyClient: full stats line", () => {
  const line = describeHealthyClient(
    client({ app_count: 61, bytes_served: 41.8 * 1_073_741_824, cache_hits: 96, cache_misses: 4 }),
  );
  assert.equal(line, "61 games reported · 41.8 GB served · 96% hit");
});

test("describeHealthyClient: singular 'game' for app_count === 1", () => {
  const line = describeHealthyClient(client({ app_count: 1, cache_hits: 1, cache_misses: 0 }));
  assert.match(line, /^1 game reported/);
});

test("describeHealthyClient: honest fallbacks when nothing has happened yet", () => {
  const line = describeHealthyClient(
    client({ app_count: null, bytes_served: 0, cache_hits: 0, cache_misses: 0 }),
  );
  assert.equal(line, "game count not reported · nothing served yet · no cache requests yet");
});

test("describeBypassClient: states the observation, not a verdict", () => {
  const line = describeBypassClient(client({ app_count: 38 }));
  assert.equal(line, "38 games reported · none of its downloads have reached the cache recently");
  assert.doesNotMatch(line, /DNS/i); // the cause-agnostic explanation lives in BYPASS_EXPLANATION, not here
});

// ---------------------------------------------------------------------
// bypassBannerText
// ---------------------------------------------------------------------

test("bypassBannerText: empty string when nobody is suspected", () => {
  assert.equal(bypassBannerText([client(), client({ client_id: "b" })]), "");
  assert.equal(bypassBannerText([]), "");
  assert.equal(bypassBannerText(null), "");
});

test("bypassBannerText: singular wording, names the one client", () => {
  assert.equal(
    bypassBannerText([client({ client_id: "DEMON", bypass_suspected: true })]),
    "Client DEMON is bypassing the cache — check its DNS.",
  );
});

test("bypassBannerText: plural wording for two or more, does not name them", () => {
  const clients = [
    client({ client_id: "a", bypass_suspected: true }),
    client({ client_id: "b", bypass_suspected: true }),
  ];
  assert.equal(bypassBannerText(clients), "2 clients are bypassing the cache — check their DNS.");
});
