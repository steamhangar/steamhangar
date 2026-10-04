/**
 * WP WEB-FEAT-4: removing a PC — the pure and transport halves.
 *
 *   - `api.deleteClient` sends `DELETE /v1/clients/<one encoded segment>`
 *     for ids with special characters, resolves `null` on 204 and rejects
 *     a 404 with kind `not_found` (the sheet reads that as "already gone");
 *   - `isRemovableClientId` refuses exactly the ids the server's route
 *     cannot address ("/", measured against the real router), and the
 *     route grammar it depends on is pinned against routers/clients.py;
 *   - the confirm wording names what the endpoint deletes, pinned against
 *     the DELETE statements in agent_reports.delete_client (twin pin: a
 *     third table deleted on the server fails here, naming the edit);
 *   - demo mode's DELETE mirrors the server: row gone, 204, 404 after.
 *
 * DOM wiring (confirm, cancel, refresh, inline error) lives in
 * clients-remove-wiring.test.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const storage = new Map([["steamvault.apiKey", "test-key"]]);
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test" },
};

const sent = [];
let nextStatus = 204;
globalThis.fetch = async (url, init = {}) => {
  sent.push({ url: new URL(String(url)), method: init.method });
  const status = nextStatus;
  const text = status === 204 ? "" : JSON.stringify({ detail: status === 404 ? "Unknown client_id 'x'" : "boom" });
  return { ok: status >= 200 && status < 300, status, text: async () => text, json: async () => JSON.parse(text) };
};

const { api, ERROR_KINDS } = await import("../js/api.js");
const { demoRequest, resetDemoData } = await import("../js/demo-data.js");
const {
  isRemovableClientId,
  UNREMOVABLE_SLASH_NOTE,
  removeConfirmTitle,
  REMOVE_WHAT_TEXT,
  REMOVE_REREGISTER_TEXT,
  removedToastText,
  removeErrorText,
} = await import("../js/lib/clients-view.js");

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.join(__dirname, "..", "..", "api", "vault_api");
const clientsRouterSrc = readFileSync(path.join(apiDir, "routers", "clients.py"), "utf8");
const agentReportsSrc = readFileSync(path.join(apiDir, "agent_reports.py"), "utf8");
const agentRouterSrc = readFileSync(path.join(apiDir, "routers", "agent.py"), "utf8");

beforeEach(() => {
  sent.length = 0;
  nextStatus = 204;
  storage.set("steamvault.demoMode", "0");
  resetDemoData();
});

// ---------------------------------------------------------------------
// api.deleteClient
// ---------------------------------------------------------------------

test("MUTATION TARGET: deleteClient encodes the id as ONE path segment (space ? # % + & non-ASCII)", async () => {
  const cases = [
    ["gaming-pc", "/v1/clients/gaming-pc"],
    ["loft laptop", "/v1/clients/loft%20laptop"],
    ["a?b#c", "/v1/clients/a%3Fb%23c"],
    ["100%", "/v1/clients/100%25"],
    ["x+y&z=1", "/v1/clients/x%2By%26z%3D1"],
    ["Büro-PC", "/v1/clients/B%C3%BCro-PC"],
  ];
  for (const [id, expectedPath] of cases) {
    sent.length = 0;
    const result = await api.deleteClient(id);
    assert.equal(result, null, `204 resolves null (${id})`);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].method, "DELETE");
    assert.equal(sent[0].url.pathname, expectedPath, `path for ${JSON.stringify(id)}`);
    assert.equal(sent[0].url.search, "", `no query string leaks out of ${JSON.stringify(id)}`);
    assert.equal(sent[0].url.hash, "", `no fragment leaks out of ${JSON.stringify(id)}`);
    assert.equal(sent[0].url.origin, "http://vault.test");
  }
});

test("deleteClient: a 404 rejects with kind not_found, a 500 with kind server (the caller decides)", async () => {
  nextStatus = 404;
  await assert.rejects(api.deleteClient("gone"), (err) => err.kind === ERROR_KINDS.NOT_FOUND && err.status === 404);
  nextStatus = 500;
  await assert.rejects(api.deleteClient("pc"), (err) => err.kind === ERROR_KINDS.SERVER && err.detail === "boom");
});

test("deleteClient in demo mode makes no network request", async () => {
  storage.set("steamvault.demoMode", "1");
  assert.equal(await api.deleteClient("workshop-pc"), null);
  assert.equal(sent.length, 0);
});

// ---------------------------------------------------------------------
// isRemovableClientId + its server-side premise
// ---------------------------------------------------------------------

test("MUTATION TARGET: an id containing '/' is not removable; every other printable id is", () => {
  assert.equal(isRemovableClientId("lab/pc-1"), false);
  assert.equal(isRemovableClientId("/"), false);
  for (const id of ["gaming-pc", "loft laptop", "a?b#c", "100%", "Büro-PC", "..x", "a\\b"]) {
    assert.equal(isRemovableClientId(id), true, id);
  }
  assert.equal(isRemovableClientId(""), false);
  assert.equal(isRemovableClientId(null), false);
  assert.match(UNREMOVABLE_SLASH_NOTE, /"\/"/);
});

test("server premise: the DELETE route's {client_id} is a plain segment (no :path converter)", () => {
  assert.match(
    clientsRouterSrc,
    /@router\.delete\(\s*"\/v1\/clients\/\{client_id\}"/,
    "VALUE drift: the route no longer reads as a plain {client_id} segment. If it became {client_id:path}, ids with '/' " +
      "are addressable now: drop the '/' rule in isRemovableClientId and UNREMOVABLE_SLASH_NOTE. If only the spelling " +
      "changed, widen this regex (GRAMMAR drift).",
  );
});

// ---------------------------------------------------------------------
// Confirm wording vs. what the server deletes
// ---------------------------------------------------------------------

function deletedTables() {
  const m = /def delete_client\([\s\S]*?\n(?=def )/.exec(agentReportsSrc);
  assert.ok(m, "agent_reports.delete_client not found — VALUE drift (moved/renamed): re-point this pin");
  const tables = [...m[0].matchAll(/DELETE FROM (\w+)/g)].map((x) => x[1]).sort();
  assert.ok(tables.length > 0, "no DELETE statements read — GRAMMAR drift: widen the regex, do not touch the wording");
  return tables;
}

test("TWIN PIN: the confirm text names exactly the tables delete_client deletes from", () => {
  const tables = deletedTables();
  assert.deepEqual(
    tables,
    ["agent_reports", "client_bypass_state"],
    `VALUE drift: delete_client now deletes from ${tables.join(", ")}. Update REMOVE_WHAT_TEXT in ` +
      "web/js/lib/clients-view.js (and the Android twin), then this list.",
  );
  assert.match(REMOVE_WHAT_TEXT, /reports this PC's agent sent/, "agent_reports");
  assert.match(REMOVE_WHAT_TEXT, /bypass status/, "client_bypass_state");
  assert.match(REMOVE_WHAT_TEXT, /Cached games, downloads and cache statistics stay\./);
});

test("TWIN PIN: 'a running agent adds the PC back' holds — the report route stores for any valid id", () => {
  assert.match(clientsRouterSrc, /\*\*Not a ban\.\*\*/, "VALUE drift: DELETE /v1/clients is documented as more than 'not a ban' now");
  assert.match(agentRouterSrc, /agent_reports\.store_report\(/, "VALUE drift: the report route no longer calls store_report");
  assert.equal(
    /client_exists|unknown client|is_banned|deleted_clients/i.test(agentRouterSrc),
    false,
    "VALUE drift: the report route seems to check the client first; REMOVE_REREGISTER_TEXT may be false now",
  );
  assert.match(REMOVE_REREGISTER_TEXT, /^If vault-agent still runs on this PC, its next report adds the PC back/);
});

test("titles and messages name the PC", () => {
  assert.equal(removeConfirmTitle("retired-pc"), "Remove retired-pc?");
  assert.equal(removedToastText("retired-pc"), "retired-pc removed from the list.");
  assert.equal(removeErrorText("retired-pc", "boom"), "Could not remove retired-pc: boom");
});

// ---------------------------------------------------------------------
// Phone layout (no browser here: pins the declarations, top level)
// ---------------------------------------------------------------------

const appCss = readFileSync(path.join(__dirname, "..", "css", "app.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** Declarations of a TOP-LEVEL rule (brace depth 0, i.e. no @media). */
function topLevelRule(selector) {
  const re = new RegExp(`(^|[}\\s])${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`, "g");
  for (const m of appCss.matchAll(re)) {
    const before = appCss.slice(0, m.index);
    const depth = (before.match(/\{/g) || []).length - (before.match(/\}/g) || []).length;
    if (depth === 0) return m[2];
  }
  return null;
}

test("phone width: the PC name wraps inside the confirm title, the row's actions wrap, errors wrap", () => {
  assert.match(topLevelRule(".pcs-remove-dialog h3") || "", /overflow-wrap:\s*anywhere/, "long PC name breaks inside the dialog");
  assert.match(topLevelRule(".jobacts") || "", /flex-wrap:\s*wrap/, "the Remove button row wraps");
  assert.match(topLevelRule(".dialog") || "", /width:\s*100%/, "dialog never wider than the viewport padding box");
  const wrapList = /(^|\})\s*([^{}]*\.errline[^{}]*)\{\s*overflow-wrap:\s*anywhere;?\s*\}/.exec(appCss);
  assert.ok(wrapList, ".errline is in the WEB-FIX-3 overflow-wrap:anywhere list (the inline error may echo a long id)");
});

// ---------------------------------------------------------------------
// Demo mode
// ---------------------------------------------------------------------

test("MUTATION TARGET: demo DELETE removes only that PC, answers null (204), then 404 like the server", async () => {
  const before = (await demoRequest("GET", "/v1/clients")).map((c) => c.client_id);
  assert.deepEqual(before, ["workshop-pc", "loft-laptop"], "seed precondition");
  assert.equal(await demoRequest("DELETE", "/v1/clients/loft-laptop"), null);
  assert.deepEqual((await demoRequest("GET", "/v1/clients")).map((c) => c.client_id), ["workshop-pc"]);
  await assert.rejects(
    demoRequest("DELETE", "/v1/clients/loft-laptop"),
    (err) => err.kind === ERROR_KINDS.NOT_FOUND && err.status === 404 && err.detail === "Unknown client_id 'loft-laptop'",
  );
});

test("demo DELETE decodes the encoded segment (the path api.js sends)", async () => {
  storage.set("steamvault.demoMode", "1");
  await api.deleteClient("workshop-pc");
  assert.deepEqual((await demoRequest("GET", "/v1/clients")).map((c) => c.client_id), ["loft-laptop"]);
  await assert.rejects(demoRequest("DELETE", "/v1/clients/no%20such%20pc"), (err) => err.detail === "Unknown client_id 'no such pc'");
  await assert.rejects(demoRequest("DELETE", "/v1/clients/a/b"), (err) => err.kind === ERROR_KINDS.NOT_FOUND);
});
