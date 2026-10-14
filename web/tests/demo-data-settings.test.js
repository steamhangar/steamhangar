import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

beforeEach(() => {
  resetDemoData();
});

test("GET /v1/settings returns every overridable key plus env-only informational rows", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  assert.equal(out.readonly, false);
  const keys = out.settings.map((s) => s.key);
  for (const k of [
    "vault_name",
    "schedule_window",
    "schedule_interval_minutes",
    "schedule_client_stale_days",
    "sweep_include_cached",
    "auto_gc",
    "webhook_url",
    "webhook_events",
  ]) {
    assert.ok(keys.includes(k), `missing overridable key ${k}`);
  }
  assert.ok(keys.includes("db_path"), "missing env-only informational row");
  const dbPath = out.settings.find((s) => s.key === "db_path");
  assert.equal(dbPath.env_only, true);
});

// WP 4e.6 rail foot / WP 4e.7 (api/) — server_version is a SIBLING of
// readonly on the response object, never a row inside `settings`, matching
// the real endpoint's shape (it has no source precedence and PATCH rejects
// it as an unrecognised key — nothing in demo mode's coercePatchValue
// accepts it either, by omission).
test("GET /v1/settings carries server_version as a top-level string sibling of readonly, not a settings row", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  assert.equal(typeof out.server_version, "string");
  assert.ok(out.server_version.length > 0);
  assert.equal(out.settings.some((s) => s.key === "server_version"), false, "server_version must not appear as a settings row");
});

test("a freshly reset demo has no db-sourced overrides", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  for (const entry of out.settings.filter((s) => !s.env_only)) {
    assert.notEqual(entry.source, "db");
  }
});

test("PATCH sets an override, and GET reflects it with source 'db'", async () => {
  const patched = await demoRequest("PATCH", "/v1/settings", { body: { bypass_window_days: "9" } });
  const entry = patched.settings.find((s) => s.key === "bypass_window_days");
  assert.equal(entry.effective, 9);
  assert.equal(entry.source, "db");
  assert.equal(entry.value, 9, "D16b: `value` carries the stored Settings value");
});

test("PATCH with null clears an override back to env/default", async () => {
  await demoRequest("PATCH", "/v1/settings", { body: { auto_gc: "dry-run" } });
  const cleared = await demoRequest("PATCH", "/v1/settings", { body: { auto_gc: null } });
  const entry = cleared.settings.find((s) => s.key === "auto_gc");
  assert.notEqual(entry.source, "db");
  // SETTINGS_BASE.auto_gc.env — ADR-0014 (2026-08-22) flipped the real
  // api/vault_api/config.py default to "execute"; see
  // web/tests/demo-data-config-defaults.test.js for the pin against that
  // module's own source text, which is what actually keeps this literal
  // honest going forward, not this comment.
  assert.equal(entry.effective, "execute");
});

// WP 4d-web: sweep_include_cached now exists as a real overridable key
// (previously only sweep_include_cached-adjacent scheduler fields did).
test("sweep_include_cached is one of the overridable keys, and defaults to true (ADR-0014)", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  const entry = out.settings.find((s) => s.key === "sweep_include_cached");
  assert.ok(entry, "missing overridable key sweep_include_cached");
  assert.equal(entry.effective, true);
  assert.equal(entry.applies, "next_sweep");
});

test("PATCH sweep_include_cached accepts every real bool spelling, case-insensitively", async () => {
  for (const [raw, expected] of [
    ["true", true],
    ["FALSE", false],
    ["yes", true],
    ["no", false],
    ["1", true],
    ["0", false],
    ["on", true],
    ["off", false],
  ]) {
    const out = await demoRequest("PATCH", "/v1/settings", { body: { sweep_include_cached: raw } });
    const entry = out.settings.find((s) => s.key === "sweep_include_cached");
    assert.equal(entry.effective, expected, `raw=${raw}`);
  }
});

test("PATCH rejects a non-boolean-spelling string for sweep_include_cached", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { sweep_include_cached: "maybe" } }),
    (err) => err.status === 422,
  );
});

test("PATCH rejects a JSON boolean literal for sweep_include_cached (same lax-mode trap as auto_gc)", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { sweep_include_cached: true } }),
    (err) => err.status === 422,
  );
});

test("PATCH validates the WHOLE body before writing anything (all-or-nothing)", async () => {
  await assert.rejects(
    () =>
      demoRequest("PATCH", "/v1/settings", {
        body: { bypass_window_days: "11", auto_gc: "not-a-real-mode" },
      }),
    (err) => err.status === 422,
  );
  const after = await demoRequest("GET", "/v1/settings");
  const bypass = after.settings.find((s) => s.key === "bypass_window_days");
  assert.notEqual(bypass.effective, 11);
});

test("PATCH rejects an env-only key by name, distinct from 'unknown key'", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { db_path: "/somewhere/else.db" } }),
    (err) => err.status === 422 && /environment-only/.test(err.detail),
  );
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { not_a_real_setting: "x" } }),
    (err) => err.status === 422 && /not a recognised setting/.test(err.detail),
  );
});

test("PATCH rejects a boolean value explicitly (Pydantic lax-mode trap, LEARNINGS Parsers)", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { auto_gc: true } }),
    (err) => err.status === 422,
  );
});

test("webhook_events accepts a JSON array, joined with commas before validation", async () => {
  const out = await demoRequest("PATCH", "/v1/settings", {
    body: { webhook_events: ["job.done", "job.error"] },
  });
  const entry = out.settings.find((s) => s.key === "webhook_events");
  assert.deepEqual([...entry.effective].sort(), ["job.done", "job.error"]);
});

test("webhook_events rejects an unknown event name", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { webhook_events: "job.done,not.a.real.event" } }),
    (err) => err.status === 422,
  );
});

// ---------------------------------------------------------------------
// Steam Web API relay (/v1/steam/*)
// ---------------------------------------------------------------------

const VALID_KEY = "0123456789ABCDEF0123456789ABCDEF";
const VALID_STEAMID = "76561198042117903";

test("GET /v1/steam/key starts unconfigured", async () => {
  const out = await demoRequest("GET", "/v1/steam/key");
  assert.deepEqual(out, { configured: false, key_last4: null });
});

test("PUT /v1/steam/key configures it and returns only the last 4 characters", async () => {
  const out = await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  assert.equal(out.configured, true);
  assert.equal(out.key_last4, "CDEF");
  assert.equal(JSON.stringify(out).includes(VALID_KEY), false);
});

test("PUT /v1/steam/key rejects a malformed key", async () => {
  await assert.rejects(
    () => demoRequest("PUT", "/v1/steam/key", { body: { key: "too-short" } }),
    (err) => err.status === 422,
  );
});

test("DELETE /v1/steam/key clears configuration", async () => {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  const out = await demoRequest("DELETE", "/v1/steam/key");
  assert.equal(out, null);
  const status = await demoRequest("GET", "/v1/steam/key");
  assert.deepEqual(status, { configured: false, key_last4: null });
});

test("GET /v1/steam/owned-games answers 409 while unconfigured", async () => {
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
});

test("MUTATION PIN: 409-unconfigured is checked even with a syntactically valid steamid", async () => {
  // If the configured-check were ever dropped, this would 200 instead.
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
});

test("GET /v1/steam/owned-games answers 200 with a game list once configured", async () => {
  // WP 4h.2 fix (baseline-test edit, named per the brief): this used to
  // assert `"playtime_forever" in g` for every game — the shape of a
  // NON-default ADR-0010 gate state (both relay_expose_playtime/
  // relay_expose_last_played on), while the real DEFAULT (both off, WP
  // 4h.0) omits the key entirely (`response_model_exclude_unset=True`).
  // The demo fixture was wrong, not this test in isolation, but the
  // assertion baked the wrong shape in — per docs/LEARNINGS.md ("demo
  // fixtures are a shipped surface, shapes 1:1 with the real API"), the
  // fixture is corrected (web/js/demo-data.js's `DEMO_OWNED_GAMES` now
  // ships keys-absent by default) and this baseline test now asserts the
  // DEFAULT shape instead of the accidentally-endorsed non-default one; the
  // enabled-gate shape is covered separately in
  // web/tests/demo-data-relay-privacy.test.js.
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  const out = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  assert.equal(out.configured, true);
  assert.ok(out.game_count > 0);
  assert.ok(Array.isArray(out.games));
  for (const g of out.games) {
    assert.ok("appid" in g && "name" in g);
    assert.equal("playtime_forever" in g, false, "the DEFAULT gate (both ADR-0010 keys off) must omit playtime_forever entirely, never a fabricated 0");
    assert.equal("rtime_last_played" in g, false, "the DEFAULT gate must omit rtime_last_played entirely");
  }
});

test("GET /v1/steam/owned-games answers 422 for a syntactically invalid steamid, even when configured", async () => {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: "not-a-steamid" } }),
    (err) => err.status === 422,
  );
});

test("GET /v1/steam/player-summaries mirrors the same 409/422/200 shape", async () => {
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/player-summaries", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  const out = await demoRequest("GET", "/v1/steam/player-summaries", { params: { steamid: VALID_STEAMID } });
  assert.equal(out.configured, true);
  assert.equal(out.players.length, 1);
  assert.equal(out.players[0].steamid, VALID_STEAMID);
});

test("turning the relay off is immediate: DELETE then the very next call answers 409 again", async () => {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  await demoRequest("DELETE", "/v1/steam/key");
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
});

test("resetDemoData() clears settings overrides and the steam relay key between test cases", async () => {
  await demoRequest("PATCH", "/v1/settings", { body: { bypass_window_days: "12" } });
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  resetDemoData();
  const settings = await demoRequest("GET", "/v1/settings");
  assert.notEqual(settings.settings.find((s) => s.key === "bypass_window_days").effective, 12);
  const steamKey = await demoRequest("GET", "/v1/steam/key");
  assert.equal(steamKey.configured, false);
});

// ---------------------------------------------------------------------
// WP D16-WEB: the demo mirrors D16b (env wins, risky confirm, cross rules)
// ---------------------------------------------------------------------

test("D16b: an env-pinned key reports source env / editable false, and PATCH answers 409 with the server's wording", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  const name = out.settings.find((s) => s.key === "vault_name");
  assert.equal(name.source, "env");
  assert.equal(name.editable, false);
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { vault_name: "x" } }),
    (err) => err.status === 409 && err.detail === "'vault_name' is set via env (VAULT_NAME); env always wins. Remove the environment variable and restart to change it here.",
  );
  const timeout = out.settings.find((s) => s.key === "webhook_timeout_seconds");
  assert.equal(timeout.shadowed, true, "a stored value under an env pin is reported shadowed");
  assert.equal(timeout.effective, 10);
});

test("D16b: a risky key without confirm_risky answers 428 {message, risky_keys} and writes nothing; with it, it is stored", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { upstream_max_conns: "24", bypass_window_days: "4" } }),
    (err) => err.status === 428 && Array.isArray(err.detail.risky_keys) && err.detail.risky_keys.join() === "upstream_max_conns" && typeof err.detail.message === "string",
  );
  const before = await demoRequest("GET", "/v1/settings");
  assert.equal(before.settings.find((s) => s.key === "bypass_window_days").source, "default", "428 writes nothing");
  const out = await demoRequest("PATCH", "/v1/settings", { body: { upstream_max_conns: "24" }, params: { confirm_risky: "true" } });
  const row = out.settings.find((s) => s.key === "upstream_max_conns");
  assert.equal(row.effective, "24");
  assert.equal(row.source, "db");
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { upstream_max_conns: null } }),
    (err) => err.status === 428,
    "clearing a risky key needs the confirmation too",
  );
});

test("D16b: cross rules answer 422 (cap below threads, per-client TLS above total)", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { upstream_max_conns: "4" }, params: { confirm_risky: "true" } }),
    (err) => err.status === 422 && /PREFILL_MAX_THREADS/.test(err.detail),
  );
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { tls_client_max_conns: "300" }, params: { confirm_risky: "true" } }),
    (err) => err.status === 422,
  );
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { tls_max_conns: "32" }, params: { confirm_risky: "true" } }),
    (err) => err.status === 422 && /TLS_CLIENT_MAX_CONNS/.test(err.detail),
  );
});

test("D16b: the webhook URL is env-only (422), schedule_window spells no window 'off', /v1/schedule follows", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { webhook_url: "https://x.example/" } }),
    (err) => err.status === 422 && /environment-only/.test(err.detail),
  );
  const out = await demoRequest("PATCH", "/v1/settings", { body: { schedule_window: "off" } });
  assert.equal(out.settings.find((s) => s.key === "schedule_window").effective, "off");
  const schedule = await demoRequest("GET", "/v1/schedule");
  assert.equal(schedule.enabled, false);
  assert.equal(schedule.window, null);
  const reset = await demoRequest("PATCH", "/v1/settings", { body: { schedule_window: null } });
  assert.equal(reset.settings.find((s) => s.key === "schedule_window").effective, "03:00-07:00", "null resets to the default window");
});
