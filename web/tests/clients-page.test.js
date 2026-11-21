/**
 * Unit tests for `web/js/lib/clients-page.js` (WP D15c): the Clients page's
 * per-client wording over `GET /v1/clients/overview` (WP D15b).
 *
 * Pins: an address client is named by its address and never called a PC;
 * the prefill runner (WP D15-RUNNER) is its own kind. The card's cells
 * ("installed" only for agent clients, a `null` is a dash, a real zero
 * prints, feed-disabled stats) are pinned in client-card.test.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AGENTLESS_NOTE,
  NO_CLIENTS_TEXT,
  appLabel,
  clientKindText,
  clientCardModel,
  clientName,
  clientTitle,
  clientsSummaryText,
  isAddressClient,
  isAgentClient,
  isRunnerClient,
  lastActiveText,
  agentVersionIssue,
  outdatedAgentInfo,
  summaryTiles,
  splitValueUnit,
  outdatedAgentsPillText,
} from "../js/lib/clients-page.js";

const MIB = 1024 * 1024;
const GIB = 1024 * MIB;
const NOW = Date.parse("2026-10-13T20:10:00Z");

const agent = (extra = {}) => ({
  key: "agent:gaming-pc",
  kind: "agent",
  client_id: "gaming-pc",
  display_name: "gaming-pc",
  addresses: ["10.0.0.42"],
  connected: true,
  agent_presence: "online",
  cache_active: true,
  last_active: "2026-10-13T20:06:00Z",
  installed_appids: [440, 570],
  installed_app_count: 2,
  stats: { bytes_served: GIB, hit_bytes: 0.75 * GIB, miss_bytes: 0.25 * GIB, requests: 2048, hits: 1536, misses: 512, hit_rate: 0.75 },
  live_now: { loading: true, bytes_per_second: 30 * MIB, apps: [{ appid: 440, name: "Some Game", bytes_per_second: 30 * MIB }], unattributed_bytes_per_second: 0 },
  recent_apps: [{ appid: 440, name: "Some Game", bytes_served: GIB, last_seen: "2026-10-13T20:06:00Z" }],
  recent_unattributed_bytes: 0,
  ...extra,
});
const address = (extra = {}) =>
  agent({
    key: "addr:10.0.0.77",
    kind: "address",
    client_id: null,
    display_name: "10.0.0.77",
    addresses: ["10.0.0.77"],
    agent_presence: null,
    installed_appids: null,
    installed_app_count: null,
    ...extra,
  });

test("names: the server's display_name (an agent by its client_id, an address by its address); no client-side rule (OLD-SERVER-FALLBACK-1)", () => {
  assert.equal(clientName(agent()), "gaming-pc");
  assert.equal(clientName(address()), "10.0.0.77");
  assert.equal(clientName(agent({ display_name: undefined })), "\u2014", "MUTATION PIN: no pre-D15-NAMES fallback to client_id");
  assert.equal(clientName(address({ display_name: "" })), "\u2014", "nor to the address");
});

test("MUTATION TARGET: an address client is 'No agent · seen in the cache log' and never called a PC", () => {
  const text = clientKindText(address());
  assert.equal(text, "No agent · seen in the cache log");
  assert.doesNotMatch(text, /\bPC\b|computer|console|phone|runner/i);
  assert.equal(clientKindText(agent()), "vault-agent · 10.0.0.42");
  assert.equal(clientKindText(agent({ addresses: [] })), "vault-agent · no address in the cache log");
  assert.doesNotMatch(AGENTLESS_NOTE, /runner|prefill/i);
});

test("MUTATION TARGET: D15-RUNNER — the runner is titled by the server and described as the server's prefill downloads", () => {
  const runner = agent({
    key: "runner",
    kind: "runner",
    client_id: null,
    addresses: ["172.18.0.1"],
    agent_presence: null,
    installed_appids: null,
    installed_app_count: null,
    name: null,
    name_addr: null,
    display_name: "Prefill runner",
  });
  assert.equal(clientName(runner), "Prefill runner");
  // WP RUNNER-NAME-1: the card shows the product's name; clientName keeps
  // the server's rule (twin-pinned), clientTitle overrides it for the runner only.
  assert.equal(clientTitle(runner), "SteamHangar");
  assert.equal(clientTitle({ ...runner, display_name: "Anything else" }), "SteamHangar");
  assert.equal(clientCardModel(runner, { range: "7d" }).name, "SteamHangar");
  const pc = agent();
  assert.equal(clientTitle(pc), clientName(pc), "every other client keeps clientName");
  assert.equal(clientKindText(runner), "This server's prefill downloads · 172.18.0.1");
  assert.equal(clientKindText({ ...runner, addresses: [] }), "This server's prefill downloads");
  assert.deepEqual(
    [isRunnerClient(runner), isAgentClient(runner), isAddressClient(runner)],
    [true, false, false],
  );
  assert.deepEqual([isAddressClient(address()), isAddressClient(agent())], [true, false]);
});

test("wording: no 'yet' that promises self-healing; the agentless note never tells a user to install an agent that may be installed", () => {
  for (const t of [AGENTLESS_NOTE, NO_CLIENTS_TEXT, clientKindText(agent({ addresses: [] }))]) assert.doesNotMatch(t, /\byet\b/i);
  assert.doesNotMatch(AGENTLESS_NOTE, /\binstall\b/i);
  assert.match(AGENTLESS_NOTE, /A PC with vault-agent can also show up here by its address until the server can match the two\./);
});

test("appLabel: a missing name is the neutral \"Unknown app\"", () => {
  assert.equal(appLabel({ appid: 7, name: null }), "Unknown app");
});

test("last active and the page summary", () => {
  assert.equal(lastActiveText(agent(), NOW), "Last active 4 min ago");
  assert.equal(lastActiveText(agent({ last_active: null }), NOW), "Last active: never");
  assert.equal(clientsSummaryText([agent(), address({ connected: false })]), "2 clients · 1 connected");
  assert.equal(clientsSummaryText([agent()]), "1 client · 1 connected");
});

test("summaryTiles: the four fixed tiles come from the cards only (CLIENTS-4)", () => {
  const tiles = summaryTiles(
    [
      agent({ connected: true, live_now: { loading: true }, stats: { bytes_served: 2 * GIB, hits: 3, misses: 1, hit_rate: 0.75 } }),
      address({ connected: false, live_now: { loading: false }, stats: { bytes_served: GIB, hits: 1, misses: 3, hit_rate: 0.25 } }),
    ],
    "7d",
  );
  assert.deepEqual(tiles.map((t) => t.key), ["connected", "loaded", "hitrate", "agents"]);
  assert.deepEqual([tiles[0].value, tiles[0].unit, tiles[0].detail], ["1", "of 2", "1 loading now"]);
  assert.deepEqual([tiles[1].value, tiles[1].unit, tiles[1].detail], ["3.0", "GB", "in the last 7 days"]);
  // Request-based over all requests (4 hits of 8), not the mean of the rates.
  assert.deepEqual([tiles[2].value, tiles[2].unit, tiles[2].meter], ["50", "%", 50]);
  assert.deepEqual([tiles[3].value, tiles[3].unit], ["1", "online"]);
});

test("summaryTiles: the Agents tile counts outdated agents only against a known server version (CLIENTS-3/4)", () => {
  const tile = (list, server) => summaryTiles(list, "7d", server).find((t) => t.key === "agents");
  const none = tile([address()], "0.1.0");
  assert.equal(none.detail, "No agent paired");
  assert.equal(none.hi, false);
  const old = agent({ agent: { agent_version: "0.0.9" } });
  const current = agent({ key: "agent:b", agent: { agent_version: "0.1.0" } });
  const warn = tile([old, current], "0.1.0");
  assert.equal(warn.outdated, 1);
  assert.equal(warn.hi, true);
  assert.equal(warn.detail, "2 paired");
  assert.equal(outdatedAgentsPillText(warn.outdated), "1 outdated");
  const calm = tile([current], "0.1.0");
  assert.equal(calm.outdated, 0);
  assert.equal(calm.hi, false);
  assert.equal(calm.detail, "1 paired · all match the server");
  const unknown = tile([old], null);
  assert.equal(unknown.outdated, null, "unknown server version: no count claimed");
  assert.equal(unknown.hi, false);
  assert.equal(unknown.detail, "1 paired · server version not known");
});

test("summaryTiles: no statistics shows dashes and says why, never zeros", () => {
  const tiles = summaryTiles([address({ connected: true, live_now: { loading: false }, stats: null })], "7d");
  assert.deepEqual(tiles.map((t) => t.key), ["connected", "loaded", "hitrate", "agents"]);
  assert.equal(tiles[0].detail, "Nothing loading now");
  assert.deepEqual([tiles[1].value, tiles[1].detail], ["\u2014", "No traffic statistics"]);
  assert.deepEqual([tiles[2].value, tiles[2].detail, tiles[2].meter], ["\u2014", "No traffic statistics", undefined]);
  const noReq = summaryTiles([address({ stats: { bytes_served: 0, hits: 0, misses: 0, hit_rate: null } })], "7d");
  assert.deepEqual([noReq[2].value, noReq[2].detail], ["\u2014", "No cache requests yet"]);
});

test("splitValueUnit: number and unit for the kit's value<small>unit</small>", () => {
  assert.deepEqual(splitValueUnit("38.2 GB"), { value: "38.2", unit: "GB" });
  assert.deepEqual(splitValueUnit("75%"), { value: "75", unit: "%" });
  assert.deepEqual(splitValueUnit("< 1 MB"), { value: "< 1", unit: "MB" });
  assert.deepEqual(splitValueUnit("30.0 MB/s"), { value: "30.0", unit: "MB/s" });
  assert.deepEqual(splitValueUnit(null), { value: "\u2014", unit: "" });
  assert.deepEqual(splitValueUnit("\u2014"), { value: "\u2014", unit: "" });
});

test("agentVersionIssue without a bundled agent: differs only; unknown or non-release versions never warn (CLIENTS-3)", () => {
  assert.equal(agentVersionIssue("0.1.0-rc19.3", "0.1.0-rc19.3"), null, "equal");
  assert.equal(agentVersionIssue("0.1.0-rc18.1", "0.1.0-rc19.2"), "server", "differs");
  assert.equal(agentVersionIssue("0.1.0", "0.1.0-rc19.2"), "server", "release vs rc differs");
  assert.equal(agentVersionIssue("0.2.0", "0.1.0"), "server", "newer agent differs too: the rule is 'differs'");
  assert.equal(agentVersionIssue(" 0.1.0 ", "0.1.0"), null, "whitespace ignored");
  for (const unknown of [null, undefined, "", "  ", 7, "dev", "v0.1.0", "dev-1a2b3c4"]) {
    assert.equal(agentVersionIssue(unknown, "0.1.0"), null, `agent ${String(unknown)}`);
    assert.equal(agentVersionIssue("0.1.0", unknown), null, `server ${String(unknown)}`);
  }
});

test("outdatedAgentInfo reads the agent row's version; agentless cards never warn", () => {
  const withVersion = (v) => agent({ agent: { agent_version: v } });
  assert.deepEqual(outdatedAgentInfo(withVersion("0.1.0-rc18.1"), "0.1.0-rc19.2"), { agent: "0.1.0-rc18.1", server: "0.1.0-rc19.2" });
  assert.equal(outdatedAgentInfo(withVersion(null), "0.1.0"), null);
  assert.equal(outdatedAgentInfo(withVersion("0.1.0"), null), null);
  assert.equal(outdatedAgentInfo(agent({ agent: null }), "0.1.0"), null);
  assert.equal(outdatedAgentInfo(address(), "0.1.0"), null);
});

