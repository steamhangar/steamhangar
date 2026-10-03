/**
 * Shape-drift guards for demo mode's `GET /v1/clients` and `GET /v1/about`
 * (WP WEB-FEAT-3, closing AGENT-FEAT-1 review finding S1: the demo clients
 * silently lacked the four presence fields while demo-data.js's header
 * claimed "field names match exactly").
 *
 * Twin pin, same style as demo-data-installed-on.test.js: the server's
 * Pydantic models are read as plain text (no Python here) and the demo's
 * KEYS must EQUAL the model's fields — a field added on the server with no
 * demo counterpart fails here, and so does a demo-only extra. Every message
 * names which edit applies: VALUE drift (the server model changed: update
 * demo-data.js) or GRAMMAR drift (same model, new spelling the field regex
 * does not read: widen `modelFields` here, do not touch demo-data.js).
 *
 * The presence rule the demo mirrors (`demoPresence`) is pinned against the
 * server constants too, and checked row by row: `offline_after` must be
 * `last_reported_at + 2 x interval + 5 min` (interval 1800 s when null).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  demoRequest,
  resetDemoData,
  demoPresence,
  DEMO_ASSUMED_REPORT_INTERVAL_SECONDS,
  DEMO_PRESENCE_GRACE_SECONDS,
} from "../js/demo-data.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.join(__dirname, "..", "..", "api", "vault_api");
const clientsRouterSrc = readFileSync(path.join(apiDir, "routers", "clients.py"), "utf8");
const aboutRouterSrc = readFileSync(path.join(apiDir, "routers", "about.py"), "utf8");
const agentReportsSrc = readFileSync(path.join(apiDir, "agent_reports.py"), "utf8");

/**
 * Field names of `class <name>(BaseModel)` in `src`: every line indented by
 * exactly four spaces that reads `ident: <annotation>` (comments, the
 * `model_config` assignment and docstrings do not match).
 */
function modelFields(src, name) {
  const m = new RegExp(`class ${name}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\S)`).exec(src);
  assert.ok(m, `class ${name}(BaseModel) not found — VALUE drift (it moved or was renamed): re-point this guard`);
  const fields = [...m[1].matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:\s*\S/gm)].map((x) => x[1]);
  assert.ok(
    fields.length > 0,
    `no fields read from ${name} — GRAMMAR drift (the field syntax changed): widen modelFields, do not touch demo-data.js`,
  );
  return fields;
}

function literalMembers(src, cls, field) {
  const m = new RegExp(`class ${cls}\\(BaseModel\\):[\\s\\S]*?\\n\\s+${field}:\\s*Literal\\[([^\\]]*)\\]`).exec(src);
  assert.ok(m, `${cls}.${field} is not a Literal[...] any more — VALUE drift if its meaning changed, GRAMMAR drift otherwise`);
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

const sortedKeys = (o) => Object.keys(o).sort();

/** Failure text for a key mismatch: names the field list the regex READ, so
 * a reader can tell a real server change from a misread model. */
function driftMessage(what, model, read, fix) {
  return (
    `${what}: keys differ from ${model}. ${model} fields as read from the Python source: [${read.join(", ")}]. ` +
    `If that list matches the model: VALUE drift — ${fix}. ` +
    `If that list is wrong (a field missing or extra compared with the model): GRAMMAR drift — widen modelFields, do not touch demo-data.js.`
  );
}

beforeEach(() => resetDemoData());

test("sanity: the field reader sees the four AGENT-FEAT-1 fields in ClientOut (proves the regex is not vacuous)", () => {
  const fields = modelFields(clientsRouterSrc, "ClientOut");
  for (const f of ["client_id", "agent_version", "report_interval_seconds", "presence", "offline_after"]) {
    assert.ok(fields.includes(f), `${f} not read from ClientOut — GRAMMAR drift: widen modelFields`);
  }
});

test("sanity: the field reader sees ComponentOut's six fields and AboutOut's one, exactly", () => {
  assert.deepEqual(
    modelFields(aboutRouterSrc, "ComponentOut"),
    ["name", "version", "commit", "status", "checked_at", "detail"],
    "ComponentOut as read differs from its known fields — GRAMMAR drift if the model is unchanged (widen modelFields), VALUE drift otherwise (update this sanity list and demo-data.js)",
  );
  assert.deepEqual(
    modelFields(aboutRouterSrc, "AboutOut"),
    ["components"],
    "AboutOut as read differs from {components} — GRAMMAR drift if the model is unchanged (widen modelFields), VALUE drift otherwise",
  );
});

test("MUTATION TARGET: every demo /v1/clients row has EXACTLY ClientOut's keys", async () => {
  const expected = modelFields(clientsRouterSrc, "ClientOut").sort();
  const rows = await demoRequest("GET", "/v1/clients");
  assert.ok(rows.length >= 2);
  for (const row of rows) {
    assert.deepEqual(
      sortedKeys(row),
      expected,
      driftMessage(`demo client ${row.client_id}`, "ClientOut", expected, "update demo-data.js buildClients()/handleGetClients()"),
    );
  }
});

test("MUTATION TARGET: demo /v1/about has EXACTLY AboutOut's keys, and every component EXACTLY ComponentOut's", async () => {
  const about = await demoRequest("GET", "/v1/about");
  const aboutFields = modelFields(aboutRouterSrc, "AboutOut").sort();
  assert.deepEqual(sortedKeys(about), aboutFields, driftMessage("demo /v1/about", "AboutOut", aboutFields, "update handleGetAbout()"));
  const expected = modelFields(aboutRouterSrc, "ComponentOut").sort();
  for (const c of about.components) {
    assert.deepEqual(
      sortedKeys(c),
      expected,
      driftMessage(`demo component ${c.name}`, "ComponentOut", expected, "update handleGetAbout()"),
    );
  }
});

test("demo /v1/about: the server's six names in the server's order, statuses from the server's set", async () => {
  const about = await demoRequest("GET", "/v1/about");
  assert.deepEqual(
    about.components.map((c) => c.name),
    literalMembers(aboutRouterSrc, "ComponentOut", "name"),
    "VALUE drift: ComponentOut's name list/order changed — update handleGetAbout()",
  );
  const statuses = literalMembers(aboutRouterSrc, "ComponentOut", "status");
  for (const c of about.components) {
    assert.ok(statuses.includes(c.status), `${c.name}: status ${c.status} is not a server status word`);
    assert.match(c.checked_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/, "server timestamp format, whole seconds");
  }
});

test("demo /v1/about is plausible: vault-core unknown with a 'Recorded at ... last start' detail, vault-dns unknown", async () => {
  const about = await demoRequest("GET", "/v1/about");
  const by = Object.fromEntries(about.components.map((c) => [c.name, c]));
  assert.equal(by["vault-core"].status, "unknown");
  assert.match(by["vault-core"].detail, /^Recorded at vault-core's last start, \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\. /);
  assert.equal(by["vault-dns"].status, "unknown");
  assert.equal(by["vault-dns"].version, null);
  assert.equal(by["vault-api"].status, "ok");
});

test("drift guard: the demo presence constants equal agent_reports.py's", () => {
  const assumed = /^ASSUMED_REPORT_INTERVAL_SECONDS\s*=\s*(\d+)\s*\*\s*(\d+)\s*$/m.exec(agentReportsSrc);
  const grace = /^PRESENCE_GRACE_SECONDS\s*=\s*(\d+)\s*\*\s*(\d+)\s*$/m.exec(agentReportsSrc);
  assert.ok(assumed && grace, "GRAMMAR drift: the constants are no longer written as `N * M` — widen these regexes");
  assert.equal(Number(assumed[1]) * Number(assumed[2]), DEMO_ASSUMED_REPORT_INTERVAL_SECONDS, "VALUE drift: update demo-data.js");
  assert.equal(Number(grace[1]) * Number(grace[2]), DEMO_PRESENCE_GRACE_SECONDS, "VALUE drift: update demo-data.js");
});

test("demo presence rule: online at exactly the deadline, offline one second later, unreadable -> offline/null", () => {
  const last = "2026-10-03T12:00:00Z";
  const deadline = Date.parse(last) + (2 * 600 + 300) * 1000;
  assert.deepEqual(demoPresence(last, 600, deadline), ["online", "2026-10-03T12:25:00Z"]);
  assert.equal(demoPresence(last, 600, deadline + 1000)[0], "offline");
  assert.deepEqual(demoPresence(last, null, Date.parse(last))[1], "2026-10-03T13:05:00Z", "null interval -> 30 min assumed");
  assert.deepEqual(demoPresence("garbage", 600, deadline), ["offline", null]);
});

test("MUTATION TARGET: demo rows — one current agent at 10 min and online, one legacy row with nulls and offline", async () => {
  const rows = await demoRequest("GET", "/v1/clients");
  const current = rows.find((r) => r.report_interval_seconds === 600);
  const legacy = rows.find((r) => r.agent_version === null && r.report_interval_seconds === null);
  assert.ok(current, "a 10-minute agent row");
  assert.equal(typeof current.agent_version, "string");
  assert.equal(current.presence, "online");
  assert.ok(legacy, "a legacy row with both new fields null");
  assert.equal(legacy.presence, "offline", "the legacy row is offline (and still listed)");
});

test("MUTATION TARGET: every demo row's offline_after is last_reported_at + 2 x interval + 5 min, by the server rule", async () => {
  const rows = await demoRequest("GET", "/v1/clients");
  for (const r of rows) {
    const interval = r.report_interval_seconds ?? 1800;
    const want = new Date(Date.parse(r.last_reported_at) + (2 * interval + 300) * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
    assert.equal(r.offline_after, want, `${r.client_id}: offline_after`);
    const expectedPresence = Date.now() <= Date.parse(r.offline_after) ? "online" : "offline";
    assert.equal(r.presence, expectedPresence, `${r.client_id}: presence agrees with offline_after`);
  }
});
