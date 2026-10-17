/**
 * `components/client-card.js` (WP CLIENTS-4): the three-zone Clients card of
 * docs/design/rc20/kit.html, rendered from `lib/clients-page.js`'s
 * `clientCardModel`.
 *
 *   - three zones + footer, in order;
 *   - the pills by state (Online / Offline / Downloading / No current data,
 *     "Agent outdated" only when the CLIENTS-3 comparison says so);
 *   - the outdated box only when outdated; the agent health row only with
 *     agent data;
 *   - the solid "now" box while loading, the dashed idle box otherwise;
 *   - the "+N more" chip;
 *   - Rename / Remove agent / Show update command call their handlers.
 *
 * Harness: fake-dom.js. Assertions compare text and classes, never nodes.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;

const { createClientCard } = await import("../js/components/client-card.js");
const { clientCardModel } = await import("../js/lib/clients-page.js");

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;
const NOW = Date.parse("2026-10-17T12:00:00Z");
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

function agentClient(over = {}) {
  return {
    key: "agent:desk-pc",
    kind: "agent",
    client_id: "desk-pc",
    addresses: ["192.168.1.42"],
    connected: true,
    agent_presence: "online",
    last_active: iso(8 * 60_000),
    installed_app_count: 24,
    stats: { bytes_served: 38.2 * GIB, hits: 98, misses: 2, hit_rate: 0.98 },
    loading_now: { loading: false, bytes_per_second: 0, apps: [] },
    recent_apps: [
      { appid: 1, name: "Satisfactory", bytes_served: 24.8 * GIB },
      { appid: 2, name: "The Riftbreaker", bytes_served: 10.3 * GIB },
      { appid: 3, name: "Dyson Sphere Program", bytes_served: 1.7 * GIB },
      { appid: 4, name: "Four", bytes_served: GIB },
      { appid: 5, name: "Five", bytes_served: GIB },
    ],
    recent_unattributed_bytes: 1.4 * GIB,
    name: null,
    name_addr: "192.168.1.42",
    display_name: "desk-pc",
    agent: { client_id: "desk-pc", agent_version: "0.1.0-rc18.1", last_reported_at: iso(5 * 60_000), bypass_suspected: false },
    ...over,
  };
}
function runnerClient(over = {}) {
  return {
    key: "runner",
    kind: "runner",
    client_id: null,
    addresses: ["172.16.12.1"],
    connected: true,
    agent_presence: null,
    last_active: iso(9 * 60_000),
    installed_app_count: null,
    stats: { bytes_served: 491 * GIB, hits: 77, misses: 23, hit_rate: 0.77 },
    loading_now: { loading: true, bytes_per_second: 28.8 * MIB, apps: [{ appid: 9, name: "HELLDIVERS 2" }] },
    recent_apps: [{ appid: 9, name: "HELLDIVERS 2", bytes_served: 50 * GIB }],
    recent_unattributed_bytes: 17 * GIB,
    name: null,
    name_addr: null,
    display_name: "Prefill runner",
    agent: null,
    ...over,
  };
}

const render = (client, ctx = {}, handlers = {}) =>
  createClientCard(clientCardModel(client, { range: "7d", nowMs: NOW, ...ctx }), handlers);
const q = (root, role) => root.querySelector(`[data-role="${role}"]`);
const text = (root, sel) => {
  const n = root.querySelector(sel);
  return n ? n.textContent : null;
};
const click = (el) => el.dispatchEvent({ type: "click" });

test("MUTATION TARGET: the card has the three zones and the footer, in kit.html's order", () => {
  const c = render(agentClient());
  assert.match(c.className, /^ccard clcard\b/);
  assert.equal(c.getAttribute("data-client-key"), "agent:desk-pc");
  assert.equal(c.getAttribute("data-kind"), "agent");
  assert.deepEqual(
    c.children.map((z) => z.getAttribute("data-zone") || z.className),
    ["identity", "activity", "recent", "cf"],
  );
  assert.equal(text(c, ".ch .nm"), "desk-pc");
  assert.equal(text(c, ".ch .meta"), "vault-agent · 192.168.1.42");
  assert.ok(c.querySelector(".ch .ico"), "icon tile");
  assert.equal(text(c, ".sec"), "Recently loaded");
});

test("MUTATION TARGET: pills by state", () => {
  const pill = (client) => q(render(client), "connection");
  const on = pill(agentClient());
  assert.equal(on.textContent, "Online");
  assert.match(on.className, /\bpill ok\b/);
  const off = pill(agentClient({ agent_presence: "offline", connected: true }));
  assert.equal(off.textContent, "Offline", "an agent's pill follows the server's presence verdict");
  const runner = pill(runnerClient());
  assert.equal(runner.textContent, "Downloading");
  assert.match(runner.className, /\bpill run\b/);
  assert.equal(pill(runnerClient({ loading_now: { loading: false, apps: [] } })).textContent, "Online");
  assert.equal(pill(runnerClient({ connected: false, loading_now: { loading: false, apps: [] } })).textContent, "Offline");
  assert.equal(pill(runnerClient({ connected: null, loading_now: null })).textContent, "No current data");
});

test("MUTATION TARGET: the outdated pill and box appear only when the versions differ", () => {
  const outdated = render(agentClient(), { serverVersion: "0.1.0-rc19.3" });
  assert.equal(q(outdated, "outdated-pill").textContent, "Agent outdated");
  assert.match(q(outdated, "outdated-pill").className, /\bpill stale\b/);
  const box = q(outdated, "agent-outdated");
  assert.equal(box.className, "upd");
  assert.equal(text(box, "b"), "Agent version differs from the server");
  assert.match(text(box, "span"), /^Agent 0\.1\.0-rc18\.1 · server 0\.1\.0-rc19\.3\./);
  assert.equal(q(box, "update-command").textContent, "Show update command");
  assert.match(outdated.className, /\bwarn\b/);

  for (const server of ["0.1.0-rc18.1", null, "dev"]) {
    const calm = render(agentClient(), { serverVersion: server });
    assert.equal(q(calm, "outdated-pill"), null, `no pill for server ${server}`);
    assert.equal(q(calm, "agent-outdated"), null, `no box for server ${server}`);
    assert.doesNotMatch(calm.className, /\bwarn\b/);
  }
});

test("the agent health row exists only with agent data; bypass replaces healthy", () => {
  const healthy = render(agentClient());
  const row = q(healthy, "agent-health");
  assert.equal(row.className, "agent");
  assert.equal(text(row, "b"), "Healthy");
  assert.ok(row.querySelector("b .ok-ico"), "the check is an icon, not a font glyph");
  assert.equal(q(healthy, "agent-presence").textContent, "last seen 5 min ago · agent 0.1.0-rc18.1");
  assert.equal(q(render(runnerClient()), "agent-health"), null);
  assert.equal(q(render(agentClient({ agent: null })), "agent-health"), null, "an agent row without agent data");
  const bypass = render(agentClient({ agent: { client_id: "desk-pc", bypass_suspected: true, app_count: 2 } }));
  assert.equal(q(bypass, "agent-health").className, "agent bad");
  assert.equal(text(q(bypass, "agent-health"), "b"), "Bypassing the cache");
  assert.ok(q(bypass, "bypass-hint"));
  assert.equal(q(bypass, "agent-presence"), null);
});

test("MUTATION TARGET: idle vs loading box", () => {
  const idle = q(render(agentClient()), "loading-now");
  assert.equal(idle.className, "now idle");
  assert.equal(idle.textContent, "Loading nothing right now");
  const unknown = q(render(agentClient({ loading_now: null })), "loading-now");
  assert.equal(unknown.className, "now idle");
  assert.equal(unknown.textContent, "Loading now: —", "no current data: the dash, not 'nothing'");
  const busy = q(render(runnerClient()), "loading-now");
  assert.equal(busy.className, "now");
  assert.equal(text(busy, "b"), "HELLDIVERS 2");
  assert.equal(text(busy, "span"), "Loading into the cache");
  assert.equal(text(busy, ".spd"), "28.8 MB/s");
  const unlinked = q(render(runnerClient({ loading_now: { loading: true, bytes_per_second: MIB, apps: [] } })), "loading-now");
  assert.equal(text(unlinked, "b"), "Content not linked to one game");
});

test("stats: agent shows Installed / Loaded / Hit rate with a meter; runner and address Loaded / Hit rate / Unlinked", () => {
  const cells = (c) =>
    q(c, "stats")
      .children.map((d) => `${d.getAttribute("data-stat")}:${text(d, ".k")}:${d.querySelector(".v").textContent}:${d.querySelector(".meter") ? "meter" : "-"}`);
  assert.deepEqual(cells(render(agentClient())), ["installed:Installed:24games:-", "loaded:Loaded:38.2GB:-", "hitrate:Hit rate:98%:meter"]);
  assert.deepEqual(cells(render(runnerClient())), ["loaded:Loaded:491GB:-", "hitrate:Hit rate:77%:meter", "unlinked:Unlinked:17.0GB:-"]);
  assert.deepEqual(
    cells(render(agentClient({ stats: null, installed_app_count: null }))),
    ["installed:Installed:—:-", "loaded:Loaded:—:-", "hitrate:Hit rate:—:-"],
    "nothing reported: dashes, never zeros",
  );
});

test("MUTATION TARGET: recent games as chips with +N more", () => {
  const c = render(agentClient());
  const chips = q(c, "recent").children;
  assert.deepEqual(chips.map((x) => x.className), ["gchip", "gchip", "gchip", "gchip more"]);
  assert.equal(chips[0].textContent, "Satisfactory24.8 GB");
  assert.equal(text(chips[0], "b"), "24.8 GB");
  assert.equal(chips[3].textContent, "+2 more");
  const few = q(render(runnerClient()), "recent").children;
  assert.equal(few.length, 1, "no +N chip when everything fits");
  assert.equal(q(render(agentClient({ recent_apps: [] })), "recent-empty").textContent, "Nothing loaded in the last 7 days");
  assert.equal(q(render(agentClient({ recent_apps: null })), "recent-none").textContent, "No traffic statistics");
});

test("footer: last active, the unlinked note on agent cards, and the remove error line", () => {
  const c = render(agentClient());
  assert.equal(q(c, "last-active").textContent, "Last active 8 min ago");
  assert.equal(q(c, "unlinked").textContent, "1.4 GB not linked to a game");
  assert.equal(q(render(runnerClient()), "unlinked"), null, "the runner shows it as a figure instead");
  const err = q(c, "remove-error");
  assert.equal(err.hidden, true);
  assert.equal(err.getAttribute("role"), "alert");
  const failed = q(render(agentClient(), { removeError: "Could not remove desk-pc: boom" }), "remove-error");
  assert.equal(failed.hidden, false);
  assert.equal(failed.textContent, "Could not remove desk-pc: boom");
  assert.equal(q(render(runnerClient()), "remove-error"), null, "no agent, no remove error line");
});

test("MUTATION TARGET: actions call their handlers", () => {
  const calls = [];
  const c = render(agentClient(), { serverVersion: "0.1.0-rc19.3" }, {
    onRename: () => calls.push("rename"),
    onRemove: () => calls.push("remove"),
    onUpdateCommand: () => calls.push("update"),
  });
  const rename = q(c, "rename-client");
  const remove = q(c, "remove-pc");
  assert.equal(rename.textContent, "Rename");
  assert.equal(remove.textContent, "Remove agent");
  assert.equal(remove.getAttribute("aria-label"), "Remove agent desk-pc");
  assert.equal(q(c, "update-command").getAttribute("aria-label"), "Show update command for desk-pc");
  click(rename);
  click(remove);
  click(q(c, "update-command"));
  assert.deepEqual(calls, ["rename", "remove", "update"]);
  const runner = render(runnerClient());
  assert.equal(runner.querySelector(".cf .acts"), null, "no name_addr, no agent: no actions");
  const slash = render(agentClient({ client_id: "lab/pc", key: "agent:lab/pc", agent: { client_id: "lab/pc" } }));
  assert.equal(q(slash, "remove-pc"), null);
  assert.ok(q(slash, "remove-unavailable"));
});
