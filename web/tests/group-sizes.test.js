/**
 * Headless tests for web/js/lib/group-sizes.js (WP TOOL-APPS-2c): depot
 * lists for exact group sizes are read once per row signature, re-read when
 * the row changes, and a failed read is not retried at the same signature.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createGroupSizeResolver } from "../js/lib/group-sizes.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

function rig({ fail = new Set() } = {}) {
  const calls = [];
  let changes = 0;
  const resolver = createGroupSizeResolver({
    fetchDetail: async (appid) => {
      calls.push(appid);
      if (fail.has(appid)) throw new Error("boom");
      return { depots: [{ depotid: appid * 10, size_bytes: 7, shared: false }] };
    },
    onChange: () => {
      changes += 1;
    },
  });
  return { resolver, calls, changes: () => changes };
}

const g = (appid, size = 100, count = 1) => ({ appid, size_bytes: size, depot_count: count });

test("reads each row once per signature and serves it from the cache", async () => {
  const { resolver, calls, changes } = rig();
  assert.equal(resolver.depotsFor(g(1)), undefined);
  resolver.request([g(1), g(2)]);
  resolver.request([g(1), g(2)]); // in flight: no second read
  await flush();
  await flush();
  assert.deepEqual(calls, [1, 2]);
  assert.deepEqual(resolver.depotsFor(g(1)), [{ depotid: 10, size_bytes: 7 }]);
  assert.ok(changes() >= 1, "listeners told");
  resolver.request([g(1), g(2)]);
  await flush();
  assert.deepEqual(calls, [1, 2], "MUTATION PIN: a cached signature is never read again");
});

test("a changed size or depot count makes the cached list stale and reads it again", async () => {
  const { resolver, calls } = rig();
  resolver.request([g(1)]);
  await flush();
  await flush();
  assert.equal(resolver.depotsFor(g(1, 200)), undefined, "stale for the new signature");
  resolver.request([g(1, 200)]);
  await flush();
  await flush();
  assert.deepEqual(calls, [1, 1]);
  assert.ok(resolver.depotsFor(g(1, 200)));
});

test("a failed read is remembered for that signature (no hammering), and the row stays unknown", async () => {
  const { resolver, calls, changes } = rig({ fail: new Set([3]) });
  resolver.request([g(3)]);
  await flush();
  await flush();
  resolver.request([g(3)]);
  await flush();
  assert.deepEqual(calls, [3]);
  assert.equal(resolver.depotsFor(g(3)), undefined);
  assert.equal(changes(), 0);
});
