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
  demoSettingSpecs,
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
  // Docstrings are dropped first: a wrapped docstring line can read like
  // `ident: text` (CoreApplyOut's "greater: the generation ...", D16c).
  const body = m[1].replace(/"""[\s\S]*?"""/g, "");
  const fields = [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:\s*\S/gm)].map((x) => x[1]);
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

// ---- WP DIST-1b: GET /v1/dist (DistOut / ArtifactOut, WP DIST-1a) ----------

const distRouterSrc = readFileSync(path.join(apiDir, "routers", "dist.py"), "utf8");
const distSrc = readFileSync(path.join(apiDir, "dist.py"), "utf8");

test("MUTATION TARGET: demo GET /v1/dist keys equal DistOut / ArtifactOut, source is one of the Literal's members", async () => {
  const index = await demoRequest("GET", "/v1/dist");
  const outer = modelFields(distRouterSrc, "DistOut");
  assert.deepEqual(sortedKeys(index), [...outer].sort(), driftMessage("demo /v1/dist", "DistOut", outer, "update handleGetDist in demo-data.js"));
  const inner = modelFields(distRouterSrc, "ArtifactOut");
  const sources = literalMembers(distRouterSrc, "ArtifactOut", "source");
  assert.ok(index.artifacts.length > 0);
  for (const a of index.artifacts) {
    assert.deepEqual(sortedKeys(a), [...inner].sort(), driftMessage(`demo /v1/dist artifact ${a.id}`, "ArtifactOut", inner, "update handleGetDist in demo-data.js"));
    assert.ok(sources.includes(a.source), `${a.source} not in ArtifactOut.source`);
    assert.equal(a.url, a.local_url, "a baked file is served locally");
    assert.match(a.sha256, /^[0-9a-f]{64}$/);
  }
});

test("demo GET /v1/dist mirrors a release vault: the about version, the server's ten ids and names, the alias", async () => {
  const index = await demoRequest("GET", "/v1/dist");
  const about = (await demoRequest("GET", "/v1/about")).components.find((c) => c.name === "vault-api");
  assert.equal(index.version, about.version, "same version as demo /v1/about");
  assert.equal(index.release, true);
  // The ids in the server's order, read from vault_api/dist.py's specs.
  const ids = [...distSrc.matchAll(/ArtifactSpec\(\s*"([a-z0-9-]+)"/g)].map((m) => m[1]);
  assert.ok(ids.length >= 10, `ids read from dist.py: [${ids.join(", ")}] — GRAMMAR drift if fewer than ten: widen this regex`);
  assert.deepEqual(index.artifacts.map((a) => a.id), ids, "VALUE drift: the server's file list changed, update DEMO_DIST_FILES");
  assert.equal(index.apk_alias_url, "/dist/steamhangar-app.apk");
  assert.match(distSrc, /APK_ALIAS = "steamhangar-app\.apk"/);
  assert.equal(index.artifacts[0].name, `steamhangar-app-v${index.version}.apk`);
});

// ---- WP D16-WEB: GET /v1/settings (SettingsOut / SettingInfoOut, D16b) ------

const settingsRouterSrc = readFileSync(path.join(apiDir, "routers", "settings.py"), "utf8");
const settingsStoreSrc = readFileSync(path.join(apiDir, "settings_store.py"), "utf8");

/** OVERRIDABLE_SPECS read from settings_store.py: key, env name, applies
 * and the target/risky/expert keywords of each `SettingSpec(...)` block. */
function overridableSpecs() {
  const start = settingsStoreSrc.indexOf("OVERRIDABLE_SPECS: dict[str, SettingSpec] = {");
  const end = settingsStoreSrc.indexOf("\n}\n", start);
  assert.ok(start !== -1 && end !== -1, "OVERRIDABLE_SPECS not found — VALUE drift (moved/renamed): re-point this guard");
  const blocks = settingsStoreSrc.slice(start, end).split("SettingSpec(").slice(1);
  return blocks.map((b) => {
    const m = /^\s*"([a-z_0-9]+)",\s*"(VAULT_[A-Z_0-9]+)",\s*"([a-z_-]+)"/.exec(b);
    assert.ok(m, `a SettingSpec( block does not start with "key", "ENV", "applies" — GRAMMAR drift: widen this reader. Block: ${b.slice(0, 80)}`);
    const target = /target=TARGET_CORE/.test(b) ? "core" : /target=TARGET_BOTH/.test(b) ? "api+core" : "api";
    return { key: m[1], env_var: m[2], applies: m[3], target, risky: /risky=True/.test(b), expert: /expert=True/.test(b) };
  });
}

test("sanity: the spec reader sees 30 editable keys incl. the risky core keys (proves it is not vacuous)", () => {
  const specs = overridableSpecs();
  assert.equal(specs.length, 30, `read ${specs.length} specs — GRAMMAR drift if the API still has 30: widen overridableSpecs`);
  assert.ok(specs.find((s) => s.key === "upstream_max_conns" && s.risky && s.expert && s.target === "core"));
  assert.ok(specs.find((s) => s.key === "log_level" && !s.risky && s.expert));
});

test("MUTATION TARGET: demo /v1/settings has EXACTLY SettingsOut's keys, and every row EXACTLY SettingInfoOut's", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  const outer = modelFields(settingsRouterSrc, "SettingsOut");
  assert.deepEqual(sortedKeys(out), [...outer].sort(), driftMessage("demo /v1/settings", "SettingsOut", outer, "update handleGetSettings in demo-data.js"));
  const inner = modelFields(settingsRouterSrc, "SettingInfoOut");
  assert.ok(inner.includes("editable") && inner.includes("shadowed"), "GRAMMAR drift: the D16b fields were not read");
  for (const row of out.settings) {
    assert.deepEqual(sortedKeys(row), [...inner].sort(), driftMessage(`demo settings row ${row.key}`, "SettingInfoOut", inner, "update describeDemoSettings in demo-data.js"));
  }
});

test("MUTATION TARGET: the demo's editable keys equal OVERRIDABLE_SPECS in order, with env name, applies, target, risky and expert", async () => {
  const specs = overridableSpecs();
  assert.deepEqual(demoSettingSpecs(), specs, "VALUE drift: settings_store.OVERRIDABLE_SPECS changed — update DEMO_SETTING_SPECS in demo-data.js");
  const out = await demoRequest("GET", "/v1/settings");
  const editable = out.settings.filter((r) => !r.env_only);
  assert.deepEqual(editable.map((r) => r.key), specs.map((s) => s.key));
  for (const r of editable) {
    const s = specs.find((x) => x.key === r.key);
    assert.equal(r.env_visible, s.target !== "core", `${r.key}: env_visible is false exactly for core-only keys`);
    assert.equal(r.editable, r.source !== "env", `${r.key}: editable is false exactly while env pins it`);
    assert.ok(r.grammar.length > 0, `${r.key}: grammar hint`);
  }
});

test("the demo's env-only rows equal ENV_ONLY_INFO_KEYS (webhook_url included, masked)", async () => {
  const m = /ENV_ONLY_INFO_KEYS[^=]*=\s*\(([\s\S]*?)\n\)/.exec(settingsStoreSrc);
  assert.ok(m, "ENV_ONLY_INFO_KEYS not found — VALUE drift: re-point this guard");
  const keys = [...m[1].matchAll(/EnvOnlySpec\("([a-z_]+)",\s*"(VAULT_[A-Z_]+)"/g)].map((x) => [x[1], x[2]]);
  assert.ok(keys.length >= 10, `read ${keys.length} env-only keys — GRAMMAR drift: widen this regex`);
  const out = await demoRequest("GET", "/v1/settings");
  const rows = out.settings.filter((r) => r.env_only).map((r) => [r.key, r.env_var]);
  assert.deepEqual(rows, keys, "VALUE drift: update ENV_ONLY_DEMO in demo-data.js");
  const url = out.settings.find((r) => r.key === "webhook_url");
  assert.match(url.effective, /^https:\/\/[^/@]+\/\*\*\*$/, "masked like settings_store.mask_url");
  assert.equal(url.editable, false);
});

test("MUTATION TARGET: demo core_apply has EXACTLY CoreApplyOut's keys, applied and not pending (D16c)", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  const fields = modelFields(settingsRouterSrc, "CoreApplyOut");
  assert.deepEqual(sortedKeys(out.core_apply), [...fields].sort(), driftMessage("demo core_apply", "CoreApplyOut", fields, "update demoCoreApply in demo-data.js"));
  assert.equal(out.core_apply.state, "applied");
  assert.equal(out.core_apply.pending, false);
  assert.match(settingsStoreSrc + readFileSync(path.join(apiDir, "core_settings.py"), "utf8"), /STATUS_STATES = frozenset\(\{[^}]*"applied"/, "VALUE drift: 'applied' is no longer a core status state");
  const before = out.core_apply.generation;
  const apiOnly = await demoRequest("PATCH", "/v1/settings", { body: { bypass_window_days: "4" } });
  assert.equal(apiOnly.core_apply.generation, before, "an api-only write does not bump the core generation");
  const after = await demoRequest("PATCH", "/v1/settings", { body: { event_log_rotate_bytes: "2097152" } });
  assert.equal(after.core_apply.generation, before + 1, "a core key write is a new generation");
  assert.equal(after.core_apply.applied_generation, after.core_apply.generation);
});
