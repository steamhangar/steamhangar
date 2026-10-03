/**
 * Headless tests for web/js/lib/about-view.js (WP WEB-FEAT-3, Settings →
 * About over `GET /v1/about`).
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
  ABOUT_STATUS,
  ABOUT_TOO_OLD_MESSAGE,
  COMPONENT_NOTES,
  aboutComponents,
  checkedText,
  classifyAboutError,
  commitCell,
  describeComponent,
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

test("every status word: own word, own icon kind, the icon kind exists in the status-icon vocabulary", () => {
  const expected = {
    ok: ["OK", "cached"],
    unreachable: ["Unreachable", "error"],
    not_in_use: ["Not in use", "notinuse"],
    unknown: ["Unknown", "unknown"],
  };
  for (const [status, [word, icon]] of Object.entries(expected)) {
    const p = statusPresentation(status);
    assert.equal(p.word, word, `${status} word`);
    assert.equal(p.icon, icon, `${status} icon`);
    assert.ok(icon in STATUS_LABEL, `${icon} must be a real createStatusIcon kind, not the "none" fallback`);
  }
});

test("MUTATION TARGET: 'unknown' and 'not_in_use' never reuse a fault glyph (warn/error) — vault-core is ALWAYS unknown", () => {
  for (const status of ["unknown", "not_in_use"]) {
    const icon = statusPresentation(status).icon;
    assert.ok(!["warn", "error"].includes(icon), `${status} renders as a fault (${icon})`);
  }
  assert.notEqual(statusPresentation("unknown").icon, statusPresentation("not_in_use").icon, "two meanings, two shapes");
});

test("an unexpected status word reads as Unknown, never as OK", () => {
  assert.equal(statusPresentation("exploded").word, "Unknown");
  assert.equal(statusPresentation(undefined).word, "Unknown");
  assert.equal(statusPresentation("__proto__").word, "Unknown");
});

test("version cell: null -> 'unknown', 'invalid' verbatim, a value verbatim with itself as the title", () => {
  assert.deepEqual(versionCell(null), { text: "unknown", title: null });
  assert.deepEqual(versionCell(""), { text: "unknown", title: null });
  assert.deepEqual(versionCell("invalid"), { text: "invalid", title: "invalid" });
  assert.deepEqual(versionCell("0.1.0-rc.8"), { text: "0.1.0-rc.8", title: "0.1.0-rc.8" });
});

test("MUTATION TARGET: commit cell shortens a hex id to 7 characters with the full id as title", () => {
  const full = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0";
  assert.deepEqual(commitCell(full), { text: "3f9c2a7", title: full });
  assert.equal(commitCell(full).text.length, 7);
});

test("commit cell: null -> 'unknown'; 'invalid' and non-hex values are never shortened into a fake id", () => {
  assert.deepEqual(commitCell(null), { text: "unknown", title: null });
  assert.deepEqual(commitCell("invalid"), { text: "invalid", title: "invalid" });
  assert.equal(commitCell("not-a-commit-id").text, "not-a-commit-id");
  assert.equal(commitCell("abc1234").text, "abc1234", "a 7-char id stays as is");
});

test("describeComponent: vault-core and vault-dns carry their plain-word notes; the server detail is passed through", () => {
  const core = describeComponent({
    name: "vault-core",
    version: "0.1.0",
    commit: null,
    status: "unknown",
    checked_at: "2026-10-03T12:00:00Z",
    detail: "Recorded at vault-core's last start, 2026-10-02T10:00:00Z. <b>not markup</b>",
  });
  assert.match(core.note, /recorded at vault-core's last start/i);
  assert.match(core.note, /not a live check/);
  assert.equal(core.detail, "Recorded at vault-core's last start, 2026-10-02T10:00:00Z. <b>not markup</b>");
  assert.equal(core.statusWord, "Unknown");
  const dns = describeComponent({ name: "vault-dns", version: null, commit: null, status: "unknown", detail: null });
  assert.match(dns.note, /^Unknown, not probed/);
  assert.equal(dns.detail, null);
  assert.equal(dns.version.text, "unknown");
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
