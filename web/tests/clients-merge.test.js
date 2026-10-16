/**
 * Unit tests for `mergeAgentData` in `web/js/lib/clients-page.js` (WP
 * CLIENTS-1): one card per device.
 *
 * Pins the matching rule (equal `client_id` between an overview agent row and
 * a `GET /v1/clients` row; an address row never joins an agent card), the
 * three cases (client only, agent only, both) and the edge cases.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mergeAgentData } from "../js/lib/clients-page.js";

const overviewAgent = (id, extra = {}) => ({
  key: `agent:${id}`,
  kind: "agent",
  client_id: id,
  addresses: ["192.168.1.42"],
  connected: true,
  agent_presence: "online",
  stats: { bytes_served: 5 },
  display_name: id,
  ...extra,
});
const overviewAddr = (addr, extra = {}) => ({
  key: `addr:${addr}`,
  kind: "address",
  client_id: null,
  addresses: [addr],
  connected: false,
  agent_presence: null,
  stats: { bytes_served: 7 },
  display_name: addr,
  ...extra,
});
const agentRow = (id, extra = {}) => ({
  client_id: id,
  last_reported_at: "2026-10-16T10:00:00Z",
  app_count: 3,
  source_addrs: ["192.168.1.42"],
  presence: "online",
  agent_version: "0.1.0",
  bypass_suspected: false,
  name: null,
  name_addr: null,
  display_name: id,
  ...extra,
});

test("both: the agent row rides on the overview card, which keeps its traffic figures", () => {
  const a = agentRow("desk-pc");
  const out = mergeAgentData([overviewAgent("desk-pc")], [a]);
  assert.equal(out.length, 1);
  assert.equal(out[0].key, "agent:desk-pc");
  assert.equal(out[0].agent, a);
  assert.deepEqual(out[0].stats, { bytes_served: 5 });
});

test("client only: addresses and the runner pass through with agent null", () => {
  const out = mergeAgentData([overviewAddr("192.168.1.77"), { key: "runner", kind: "runner", client_id: null, addresses: [] }], [agentRow("desk-pc")]);
  assert.equal(out.filter((c) => c.kind !== "agent").every((c) => c.agent === null), true);
  assert.equal(out.length, 3, "the agent PC without an overview row is added once");
});

test("client only: an overview agent row without agent data stays and has agent null", () => {
  const out = mergeAgentData([overviewAgent("desk-pc")], []);
  assert.equal(out.length, 1);
  assert.equal(out[0].agent, null);
});

test("no agent answer yet (null): every card as the overview sent it", () => {
  const out = mergeAgentData([overviewAgent("desk-pc"), overviewAddr("10.0.0.5")], null);
  assert.deepEqual(out.map((c) => [c.key, c.agent]), [["agent:desk-pc", null], ["addr:10.0.0.5", null]]);
});

test("agent only: a card from the agent row alone, no traffic figures, never zeros", () => {
  const a = agentRow("laptop", { presence: "offline", source_addrs: [], app_count: 0 });
  const out = mergeAgentData([], [a]);
  assert.equal(out.length, 1);
  const c = out[0];
  assert.equal(c.key, "agent:laptop");
  assert.equal(c.kind, "agent");
  assert.equal(c.connected, false);
  assert.equal(c.agent_presence, "offline");
  assert.equal(c.installed_app_count, 0, "a real zero stays a number");
  assert.equal(c.stats, null);
  assert.equal(c.loading_now, null);
  assert.equal(c.recent_apps, null);
  assert.equal(c.last_active, "2026-10-16T10:00:00Z");
  assert.equal(c.agent, a);
});

test("agent only: a presence the server did not send is 'unknown' (connected null), not offline", () => {
  const out = mergeAgentData([], [agentRow("old-server", { presence: undefined })]);
  assert.equal(out[0].connected, null);
  assert.equal(out[0].agent_presence, null);
});

test("MUTATION TARGET: an address row never joins an agent card, even on the agent's own address", () => {
  const out = mergeAgentData(
    [overviewAgent("desk-pc", { addresses: [] }), overviewAddr("192.168.1.42")],
    [agentRow("desk-pc", { source_addrs: ["192.168.1.42"] })],
  );
  assert.deepEqual(out.map((c) => c.key), ["agent:desk-pc", "addr:192.168.1.42"]);
  assert.equal(out[1].agent, null);
});

test("matching is exact on client_id: case and whitespace differ, so no match", () => {
  const out = mergeAgentData([overviewAgent("Desk-PC")], [agentRow("desk-pc")]);
  assert.equal(out[0].agent, null);
  assert.equal(out.length, 2, "the unmatched agent row is its own card");
});

test("a repeated client_id in the agent list counts once, first row wins", () => {
  const first = agentRow("desk-pc", { agent_version: "1" });
  const out = mergeAgentData([overviewAgent("desk-pc")], [first, agentRow("desk-pc", { agent_version: "2" })]);
  assert.equal(out.length, 1);
  assert.equal(out[0].agent, first);
});

test("rows without a usable client_id are ignored, not matched to each other", () => {
  const out = mergeAgentData(
    [overviewAgent(null, { key: "agent:?" })],
    [agentRow(""), { presence: "online" }, null],
  );
  assert.equal(out.length, 1);
  assert.equal(out[0].agent, null);
});

test("inputs are not mutated, and non-array inputs give an empty list", () => {
  const ov = [overviewAgent("desk-pc")];
  const copy = JSON.stringify(ov);
  mergeAgentData(ov, [agentRow("desk-pc")]);
  assert.equal(JSON.stringify(ov), copy);
  assert.deepEqual(mergeAgentData(undefined, undefined), []);
});
