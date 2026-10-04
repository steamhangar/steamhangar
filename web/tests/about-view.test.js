/**
 * Headless tests for web/js/lib/about-view.js (WP WEB-FEAT-3, Settings →
 * About over `GET /v1/about`; WP WEB-FIX-8 wording: no "unknown", em dash
 * for unreported values, vault-core OK/Check/Not reported against
 * vault-api, vault-dns N/A).
 *
 * Pure module: no DOM. The status vocabulary, component names and the 60 s
 * cache are pinned against the SERVER source as plain text (the
 * demo-data-installed-on.test.js style): a coder here has no Python to
 * import the Pydantic model with. Every drift message names which edit
 * applies — VALUE drift (the server changed: fix about-view.js) or GRAMMAR
 * drift (same meaning, new spelling: widen the regex here).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  ABOUT_CACHE_SECONDS,
  ABOUT_DISPLAY,
  ABOUT_STATUS,
  ABOUT_TOO_OLD_MESSAGE,
  COMPONENT_NOTES,
  CORE_NOTES,
  DASH,
  DASH_LABEL,
  DASH_NOTE,
  aboutComponents,
  checkedText,
  classifyAboutError,
  commitCell,
  coreComparison,
  describeComponent,
  describeComponents,
  statusPresentation,
  versionCell,
} from "../js/lib/about-view.js";
import { STATUS_LABEL } from "../js/components/status-icon.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.join(__dirname, "..", "..", "api", "vault_api");
const aboutRouterSrc = readFileSync(path.join(apiDir, "routers", "about.py"), "utf8");
const aboutSrc = readFileSync(path.join(apiDir, "about.py"), "utf8");

/** The string members of the `Literal[...]` that follows `field:` inside
 * `class ComponentOut`. */
function literalMembers(field) {
  const cls = /class ComponentOut\(BaseModel\):([\s\S]*?)(?=\nclass |\n@router)/.exec(aboutRouterSrc);
  assert.ok(cls, "class ComponentOut not found in api/vault_api/routers/about.py (VALUE drift: it moved or was renamed)");
  const m = new RegExp(`\\n\\s+${field}:\\s*Literal\\[([^\\]]*)\\]`).exec(cls[1]);
  assert.ok(
    m,
    `ComponentOut.${field} is no longer a Literal[...] — VALUE drift if the field changed meaning (update about-view.js), ` +
      "GRAMMAR drift if only the spelling changed (widen literalMembers here)",
  );
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

test("drift guard: ABOUT_STATUS covers exactly the server's four status words", () => {
  assert.deepEqual(
    Object.keys(ABOUT_STATUS).sort(),
    literalMembers("status").sort(),
    "VALUE drift: ComponentOut.status gained or lost a word — add/remove its presentation in about-view.js",
  );
});

test("drift guard: COMPONENT_NOTES has a note for exactly the server's six component names", () => {
  assert.deepEqual(
    Object.keys(COMPONENT_NOTES).sort(),
    literalMembers("name").sort(),
    "VALUE drift: ComponentOut.name changed — add/remove the component's note in about-view.js",
  );
});

test("drift guard: ABOUT_CACHE_SECONDS equals api/vault_api/about.py CACHE_TTL_SECONDS", () => {
  const m = /^CACHE_TTL_SECONDS\s*=\s*([0-9.]+)\s*$/m.exec(aboutSrc);
  assert.ok(m, "CACHE_TTL_SECONDS not found as a plain number (GRAMMAR drift: widen this regex; VALUE drift if it was removed)");
  assert.equal(Number(m[1]), ABOUT_CACHE_SECONDS, "VALUE drift: the server cache changed — update ABOUT_CACHE_SECONDS");
});

test("drift guard: the vault-runner note's '90 s' equals api/vault_api/runner_presence.py PRESENCE_STALE_SECONDS", () => {
  const src = readFileSync(path.join(apiDir, "runner_presence.py"), "utf8");
  const m = /^PRESENCE_STALE_SECONDS\s*=\s*([0-9.]+)\s*$/m.exec(src);
  assert.ok(m, "PRESENCE_STALE_SECONDS not found as a plain number (GRAMMAR drift: widen this regex; VALUE drift if it was removed)");
  const note = /within the last (\d+) s\b/.exec(COMPONENT_NOTES["vault-runner"]);
  assert.ok(note, "the vault-runner note no longer states a number of seconds — re-point this guard");
  assert.equal(Number(note[1]), Number(m[1]), "VALUE drift: the runner freshness threshold changed — update the note in about-view.js");
});

// WP WEB-FIX-8: the wording the user asked for (rc9 feedback, 2026-10-04).
// Literal expected words, never derived from ABOUT_DISPLAY itself.
test("every display state: own word, own icon kind, the icon kind exists in the status-icon vocabulary", () => {
  const expected = {
    ok: ["OK", "cached"],
    unreachable: ["Unreachable", "error"],
    not_in_use: ["Not in use", "notinuse"],
    not_checked: ["Not checked", "unknown"],
    check: ["Check", "unknown"],
    not_reported: ["Not reported", "notinuse"],
    not_applicable: ["N/A", "notinuse"],
  };
  assert.deepEqual(Object.keys(ABOUT_DISPLAY).sort(), Object.keys(expected).sort());
  for (const [state, [word, icon]] of Object.entries(expected)) {
    assert.equal(ABOUT_DISPLAY[state].word, word, `${state} word`);
    assert.equal(ABOUT_DISPLAY[state].icon, icon, `${state} icon`);
    assert.ok(icon in STATUS_LABEL, `${icon} must be a real createStatusIcon kind, not the "none" fallback`);
  }
  assert.deepEqual(
    Object.fromEntries(["ok", "unreachable", "not_in_use", "unknown"].map((s) => [s, statusPresentation(s).word])),
    { ok: "OK", unreachable: "Unreachable", not_in_use: "Not in use", unknown: "Not checked" },
  );
});

test("MUTATION TARGET: only 'unreachable' is a fault — Check / Not checked / Not reported / N/A are neutral glyph and tone", () => {
  for (const state of ["not_in_use", "not_checked", "check", "not_reported", "not_applicable"]) {
    const p = ABOUT_DISPLAY[state];
    assert.ok(!["warn", "error"].includes(p.icon), `${state} renders as a fault glyph (${p.icon})`);
    assert.equal(p.tone, "tx-cancelled", `${state} must use the neutral tone, not ${p.tone}`);
  }
  assert.equal(ABOUT_DISPLAY.unreachable.tone, "tx-error");
  assert.notEqual(ABOUT_DISPLAY.not_checked.icon, ABOUT_DISPLAY.not_in_use.icon, "two meanings, two shapes");
});

test("an unexpected status word reads as Not checked, never as OK", () => {
  assert.equal(statusPresentation("exploded").word, "Not checked");
  assert.equal(statusPresentation(undefined).word, "Not checked");
  assert.equal(statusPresentation("__proto__").word, "Not checked");
  assert.equal(describeComponent({ name: "vault-runner", status: "exploded" }).statusWord, "Not checked");
});

test("MUTATION TARGET: version cell: null -> em dash (marked missing), 'invalid' verbatim, a value verbatim with itself as the title", () => {
  assert.equal(DASH, "—");
  assert.deepEqual(versionCell(null), { text: "—", title: null, missing: true });
  assert.deepEqual(versionCell(""), { text: "—", title: null, missing: true });
  assert.deepEqual(versionCell("invalid"), { text: "invalid", title: "invalid", missing: false });
  assert.deepEqual(versionCell("0.1.0-rc.8"), { text: "0.1.0-rc.8", title: "0.1.0-rc.8", missing: false });
  assert.equal(DASH_LABEL, "Not reported");
});

test("MUTATION TARGET: commit cell shortens a hex id to 7 characters with the full id as title", () => {
  const full = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0";
  assert.deepEqual(commitCell(full), { text: "3f9c2a7", title: full, missing: false });
  assert.equal(commitCell(full).text.length, 7);
});

test("commit cell: null -> em dash; 'invalid' and non-hex values are never shortened into a fake id", () => {
  assert.deepEqual(commitCell(null), { text: "—", title: null, missing: true });
  assert.deepEqual(commitCell("invalid"), { text: "invalid", title: "invalid", missing: false });
  assert.equal(commitCell("not-a-commit-id").text, "not-a-commit-id");
  assert.equal(commitCell("abc1234").text, "abc1234", "a 7-char id stays as is");
});

const FULL = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0";
const API = { name: "vault-api", version: "0.1.0-rc9", commit: FULL, status: "ok", detail: "Also serves the web UI." };
const coreWith = (extra) => ({
  name: "vault-core",
  version: "0.1.0-rc9",
  commit: FULL,
  status: "unknown",
  detail: "Recorded at vault-core's last start, 2026-10-02T10:00:00Z. <b>not markup</b>",
  ...extra,
});

test("MUTATION TARGET: vault-core equal to vault-api (version AND commit) is OK, the (i) says recorded at last start", () => {
  const core = describeComponent(coreWith({}), API);
  assert.equal(core.statusWord, "OK");
  assert.equal(core.statusIcon, "cached");
  assert.equal(core.state, "ok");
  assert.match(core.note, /^Recorded at vault-core's last start: the same version and commit as vault-api/);
  assert.match(core.note, /Not a live check/);
  assert.equal(core.detail, "Recorded at vault-core's last start, 2026-10-02T10:00:00Z. <b>not markup</b>", "server detail verbatim");
  assert.deepEqual(core.info, [core.note, core.detail], "no dash, so no dash note");
  // A commit in another case is the same commit.
  assert.equal(describeComponent(coreWith({ commit: FULL.toUpperCase() }), API).statusWord, "OK");
});

test("MUTATION TARGET: vault-core differing from vault-api in version OR commit is a neutral Check explaining the mismatch", () => {
  for (const extra of [{ version: "0.1.0-rc8" }, { commit: "0".repeat(40) }]) {
    const core = describeComponent(coreWith(extra), API);
    assert.equal(core.statusWord, "Check", JSON.stringify(extra));
    assert.equal(core.statusTone, "tx-cancelled");
    assert.match(core.note, /differs from vault-api's/);
    assert.match(core.note, /not restarted after an update/);
  }
  assert.equal(coreComparison(coreWith({ version: "0.1.0-rc8" }), API), "mismatch");
});

test("MUTATION TARGET: vault-core that never recorded a version is Not reported; one that cannot be compared is Check", () => {
  const missing = describeComponent(coreWith({ version: null, commit: null, detail: "No version recorded yet." }), API);
  assert.equal(missing.statusWord, "Not reported");
  assert.match(missing.note, /has not recorded a version yet/);
  assert.equal(missing.version.text, "—");
  assert.equal(missing.dashNote, DASH_NOTE);
  // "invalid", a null commit on either side, or no vault-api entry: no
  // comparison possible, so never OK — and never "Not reported" either.
  const cases = [
    [coreWith({ version: "invalid", commit: null }), API],
    [coreWith({ commit: null }), API],
    [coreWith({}), { ...API, commit: null }],
    [coreWith({}), null],
    [coreWith({ version: "dev", commit: null }), { ...API, version: "dev", commit: null }],
  ];
  for (const [core, api] of cases) {
    const v = describeComponent(core, api);
    assert.equal(v.statusWord, "Check", JSON.stringify([core.version, core.commit, api && api.commit]));
    assert.match(v.note, /cannot be compared/);
  }
});

test("vault-core with a status other than 'unknown' (a future server that probes it) is never overridden", () => {
  assert.equal(describeComponent(coreWith({ status: "unreachable" }), API).statusWord, "Unreachable");
  assert.equal(describeComponent(coreWith({ status: "ok", version: "x" }), API).statusWord, "OK");
});

test("MUTATION TARGET: vault-dns 'unknown' is N/A, dashes in both cells, the (i) says optional and not checked", () => {
  const dns = describeComponent({ name: "vault-dns", version: null, commit: null, status: "unknown", detail: "Optional (compose profile dns)." }, API);
  assert.equal(dns.statusWord, "N/A");
  assert.equal(dns.version.text, "—");
  assert.equal(dns.commit.text, "—");
  assert.match(dns.note, /^Optional component\. vault-api does not check it/);
  assert.deepEqual(dns.info, [dns.note, DASH_NOTE, "Optional (compose profile dns)."]);
});

test("MUTATION TARGET: vault-proxy keeps OK with dashes; its (i) says the egress proxy does not report a version (no extra dash note)", () => {
  const proxy = describeComponent({ name: "vault-proxy", version: null, commit: null, status: "ok", detail: null }, API);
  assert.equal(proxy.statusWord, "OK");
  assert.equal(proxy.version.text, "—");
  assert.equal(proxy.commit.text, "—");
  assert.match(proxy.note, /egress proxy does not report a version/i);
  assert.equal(proxy.dashNote, null);
  assert.deepEqual(proxy.info, [proxy.note]);
  // Its other server statuses stay the server's (vault-proxy 'unknown' =
  // a misconfigured HTTP_PROXY or an unexpected answer: Not checked).
  assert.equal(describeComponent({ name: "vault-proxy", status: "unknown" }, API).statusWord, "Not checked");
});

test("steamprefill: OK with its version and a dash for the commit, explained by its own note", () => {
  const sp = describeComponent({ name: "steamprefill", version: "3.7.1", commit: null, status: "ok", detail: null }, API);
  assert.deepEqual([sp.version.text, sp.commit.text, sp.statusWord], ["3.7.1", "—", "OK"]);
  assert.match(sp.note, /reports a version but no commit id/);
  assert.equal(sp.dashNote, null);
});

test("MUTATION TARGET: no row of any state shows the word 'unknown' in its visible cells", () => {
  const rows = describeComponents([
    { ...API, commit: null },
    coreWith({ version: null, commit: null }),
    { name: "vault-runner", version: null, commit: null, status: "unknown" },
    { name: "steamprefill", version: null, commit: null, status: "unknown" },
    { name: "vault-proxy", version: null, commit: null, status: "unknown" },
    { name: "vault-dns", version: null, commit: null, status: "unknown" },
    { name: null, status: "weird" },
  ]);
  for (const r of rows) {
    const visible = [r.name, r.version.text, r.commit.text, r.statusWord].join(" | ");
    assert.doesNotMatch(visible, /unknown/i, visible);
  }
  assert.equal(rows[6].name, "Unnamed component");
});

test("describeComponents compares vault-core against vault-api from the SAME list", () => {
  const rows = describeComponents([API, coreWith({})]);
  assert.equal(rows[1].statusWord, "OK");
  const rows2 = describeComponents([{ ...API, version: "0.1.0" }, coreWith({})]);
  assert.equal(rows2[1].statusWord, "Check");
  assert.deepEqual(describeComponents(null), []);
});

test("CORE_NOTES: exactly the four comparison outcomes", () => {
  assert.deepEqual(Object.keys(CORE_NOTES).sort(), ["mismatch", "not_comparable", "not_reported", "same_release"]);
});

test("aboutComponents: only an {components: [...]} body is a component list", () => {
  assert.equal(aboutComponents(null), null);
  assert.equal(aboutComponents({}), null);
  assert.equal(aboutComponents({ components: "x" }), null);
  assert.deepEqual(aboutComponents({ components: [{ name: "vault-api" }, null] }), [{ name: "vault-api" }]);
});

test("relative time: checkedText uses the OLDEST checked_at and states the 60 s server cache", () => {
  const now = Date.parse("2026-10-03T12:10:00Z");
  const text = checkedText(
    [
      { checked_at: "2026-10-03T12:09:30Z" },
      { checked_at: "2026-10-03T12:05:00Z" }, // oldest: 5 min
      { checked_at: "garbage" },
    ],
    now,
  );
  assert.match(text, /^Checked by the server 5 min ago\./);
  assert.match(text, /up to 60 s/);
  assert.match(text, /Refresh can show the same result/);
  assert.equal(checkedText([{ checked_at: "2026-10-03T12:09:50Z" }], now).startsWith("Checked by the server just now."), true);
  assert.equal(checkedText([{ checked_at: null }], now), null);
  assert.equal(checkedText(null, now), null);
});

test("MUTATION TARGET: only 404 is 'server too old' (a note); 401 (key refused) and anything else is an error", () => {
  assert.equal(classifyAboutError({ status: 404 }), "too_old");
  assert.equal(classifyAboutError({ status: 401 }), "error");
  assert.equal(classifyAboutError({ status: 500 }), "error");
  assert.equal(classifyAboutError({ status: 403 }), "error");
  assert.equal(classifyAboutError(new Error("network")), "error");
  assert.equal(classifyAboutError(null), "error");
  assert.match(ABOUT_TOO_OLD_MESSAGE, /older than this web UI/);
});
