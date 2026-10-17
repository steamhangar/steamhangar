/**
 * Unit tests for `web/js/lib/clients-page.js` (WP D15c): the Clients page's
 * per-client wording over `GET /v1/clients/overview` (WP D15b).
 *
 * Pins: an address client is named by its address and never called a PC;
 * the prefill runner (WP D15-RUNNER) is its own kind; "installed"
 * only for agent clients; a `null` from the server is a dash, a real zero
 * prints; the feed-disabled shape (stats/recent null) prints no stats line.
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
  clientName,
  clientsSummaryText,
  connectionChip,
  installedText,
  isAddressClient,
  isAgentClient,
  isRunnerClient,
  lastActiveText,
  loadingNowText,
  recentAppsText,
  statsText,
  isAgentOutdated,
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
  addresses: ["192.168.1.42"],
  connected: true,
  agent_presence: "online",
  cache_active: true,
  last_active: "2026-10-13T20:06:00Z",
  installed_appids: [440, 570],
  installed_app_count: 2,
  stats: { bytes_served: GIB, hit_bytes: 0.75 * GIB, miss_bytes: 0.25 * GIB, requests: 2048, hits: 1536, misses: 512, hit_rate: 0.75 },
  loading_now: { loading: true, bytes_per_second: 30 * MIB, apps: [{ appid: 440, name: "Some Game", bytes_per_second: 30 * MIB }], unattributed_bytes_per_second: 0 },
  recent_apps: [{ appid: 440, name: "Some Game", bytes_served: GIB, last_seen: "2026-10-13T20:06:00Z" }],
  recent_unattributed_bytes: 0,
  ...extra,
});
const address = (extra = {}) =>
  agent({
    key: "addr:192.168.1.77",
    kind: "address",
    client_id: null,
    addresses: ["192.168.1.77"],
    agent_presence: null,
    installed_appids: null,
    installed_app_count: null,
    ...extra,
  });

test("names: an agent by its client_id, an address client by its address (no invented name)", () => {
  assert.equal(clientName(agent()), "gaming-pc");
  assert.equal(clientName(address()), "192.168.1.77");
  assert.equal(clientName(address({ addresses: [] })), "192.168.1.77", "falls back to the key");
});

test("MUTATION TARGET: an address client is 'No agent · seen in the cache log' and never called a PC", () => {
  const text = clientKindText(address());
  assert.equal(text, "No agent · seen in the cache log");
  assert.doesNotMatch(text, /\bPC\b|computer|console|phone|runner/i);
  assert.equal(clientKindText(agent()), "vault-agent · 192.168.1.42");
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
  assert.equal(clientKindText(runner), "This server's prefill downloads · 172.18.0.1");
  assert.equal(clientKindText({ ...runner, addresses: [] }), "This server's prefill downloads");
  assert.equal(installedText(runner), null);
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

test("connection chip: true/false/null map to three words and the .pchip classes", () => {
  assert.deepEqual(connectionChip(agent()), { word: "Agent online", cls: "pchip pchip-on" });
  assert.deepEqual(connectionChip(agent({ connected: false })), { word: "Agent offline", cls: "pchip pchip-off" });
  assert.deepEqual(connectionChip(address({ connected: false })), { word: "No agent", cls: "pchip pchip-off" });
  assert.deepEqual(connectionChip(address({ connected: true })), { word: "Loading", cls: "pchip pchip-on" });
  assert.deepEqual(connectionChip(address({ connected: null })), { word: "No current data", cls: "pchip pchip-unknown" });
});

test("MUTATION TARGET: installed games only for agent clients; an unreadable snapshot says so", () => {
  assert.equal(installedText(address()), null);
  assert.equal(installedText(agent()), "2 games installed");
  assert.equal(installedText(agent({ installed_app_count: 1 })), "1 game installed");
  assert.equal(installedText(agent({ installed_app_count: 0 })), "0 games installed", "a real zero prints");
  assert.equal(installedText(agent({ installed_app_count: null })), "Installed games: not reported");
});

test("MUTATION TARGET: loading now — null (feed not ok) is a dash, idle says nothing, loading names the speed and games", () => {
  assert.equal(loadingNowText(agent({ loading_now: null })), "Loading now: —");
  assert.equal(loadingNowText(agent({ loading_now: { loading: false, bytes_per_second: 0, apps: [], unattributed_bytes_per_second: 0 } })), "Loading now: nothing");
  assert.equal(loadingNowText(agent()), "Loading now: 30.0 MB/s · Some Game");
  assert.equal(
    loadingNowText(address({ loading_now: { loading: true, bytes_per_second: 4 * MIB, apps: [], unattributed_bytes_per_second: 4 * MIB } })),
    "Loading now: 4.0 MB/s · content not linked to one game",
  );
  assert.equal(appLabel({ appid: 7, name: null }), "Unknown app");
});

test("stats over the range; none with the feed disabled (stats null)", () => {
  assert.equal(statsText(agent(), "7d"), "1.0 GB loaded in the last 7 days · hit rate 75%");
  assert.equal(statsText(agent({ stats: { ...agent().stats, bytes_served: 0, hit_rate: null } }), "24h"), "0 GB loaded in the last 24 hours");
  assert.equal(statsText(agent({ stats: null }), "7d"), null);
});

test("recently loaded: top three with sizes, '+N more', content without one game", () => {
  const apps = [1, 2, 3, 4, 5].map((i) => ({ appid: i, name: `Game ${i}`, bytes_served: i * GIB, last_seen: "2026-10-13T20:00:00Z" }));
  assert.equal(
    recentAppsText(agent({ recent_apps: apps, recent_unattributed_bytes: 300 * MIB })),
    "Loaded recently: Game 1 (1.0 GB), Game 2 (2.0 GB), Game 3 (3.0 GB), +2 more, 300 MB not linked to one game",
  );
  assert.equal(recentAppsText(agent({ recent_apps: [] })), null);
  assert.equal(recentAppsText(agent({ recent_apps: null })), null);
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
      agent({ connected: true, loading_now: { loading: true }, stats: { bytes_served: 2 * GIB, hits: 3, misses: 1, hit_rate: 0.75 } }),
      address({ connected: false, loading_now: { loading: false }, stats: { bytes_served: GIB, hits: 1, misses: 3, hit_rate: 0.25 } }),
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
  const tiles = summaryTiles([address({ connected: true, loading_now: { loading: false }, stats: null })], "7d");
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

test("isAgentOutdated: differs only; unknown or non-release versions never warn (CLIENTS-3)", () => {
  assert.equal(isAgentOutdated("0.1.0-rc19.3", "0.1.0-rc19.3"), false, "equal");
  assert.equal(isAgentOutdated("0.1.0-rc18.1", "0.1.0-rc19.2"), true, "differs");
  assert.equal(isAgentOutdated("0.1.0", "0.1.0-rc19.2"), true, "release vs rc differs");
  assert.equal(isAgentOutdated("0.2.0", "0.1.0"), true, "newer agent differs too: the rule is 'differs'");
  assert.equal(isAgentOutdated(" 0.1.0 ", "0.1.0"), false, "whitespace ignored");
  for (const unknown of [null, undefined, "", "  ", 7, "dev", "v0.1.0", "dev-1a2b3c4"]) {
    assert.equal(isAgentOutdated(unknown, "0.1.0"), false, `agent ${String(unknown)}`);
    assert.equal(isAgentOutdated("0.1.0", unknown), false, `server ${String(unknown)}`);
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

