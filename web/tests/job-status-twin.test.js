/**
 * Twin pin, web side (WP ACTIVE-STATUS-1, audit C-5): the set of "active"
 * (in-flight) job statuses is defined ONCE per side and the sides agree:
 *   - web:     ACTIVE_JOB_STATUSES in web/js/lib/job-status.js
 *   - server:  ACTIVE_STATUSES in api/vault_api/jobs.py
 *   - Android: PollingIntervals.ACTIVE_JOB_STATUSES (polling/PollingIntervals.kt)
 * The Android side pins the web file in ActiveJobStatusesWebTwinTest.kt
 * (runs only in GitHub CI); this file runs here and catches a one-sided
 * edit at once. A new active status changes all three together.
 *
 * It also guards against a new local copy: no web module other than
 * lib/job-status.js may hold all three statuses in one array literal,
 * alone or with extra entries (demo-data.js and KNOWN_STATUSES excepted,
 * see below).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { ACTIVE_JOB_STATUSES } from "../js/lib/job-status.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repo = path.join(__dirname, "..", "..");
const read = (...parts) => readFileSync(path.join(repo, ...parts), "utf8");

const EXPECTED = ["paused", "queued", "running"];
const sorted = (xs) => [...xs].sort();

test("the web set is exactly queued, running, paused", () => {
  assert.ok(ACTIVE_JOB_STATUSES instanceof Set);
  assert.deepEqual(sorted(ACTIVE_JOB_STATUSES), EXPECTED);
});

test("twin: api/vault_api/jobs.py ACTIVE_STATUSES names the same statuses", () => {
  const py = read("api", "vault_api", "jobs.py");
  const m = /^ACTIVE_STATUSES = \(([^)]*)\)/m.exec(py);
  assert.ok(m, "GRAMMAR DRIFT: no 'ACTIVE_STATUSES = (...)' line in jobs.py -- widen this regex");
  const names = m[1].split(",").map((s) => s.trim()).filter(Boolean);
  const values = names.map((name) => {
    const v = new RegExp(`^${name} = "([^"]+)"`, "m").exec(py);
    assert.ok(v, `GRAMMAR DRIFT: jobs.py has no '${name} = "..."' constant -- widen this regex`);
    return v[1];
  });
  assert.deepEqual(
    sorted(values),
    sorted(ACTIVE_JOB_STATUSES),
    "VALUE DRIFT: the server's ACTIVE_STATUSES and web/js/lib/job-status.js differ -- change web, Android and this pin together",
  );
});

test("twin: Android PollingIntervals.ACTIVE_JOB_STATUSES names the same statuses", () => {
  const kt = read("app", "app", "src", "main", "java", "dev", "steamvault", "app", "polling", "PollingIntervals.kt");
  const m = /^\s*val ACTIVE_JOB_STATUSES: Set<String> = setOf\(([^)]*)\)/m.exec(kt);
  assert.ok(m, "GRAMMAR DRIFT: no public 'val ACTIVE_JOB_STATUSES: Set<String> = setOf(...)' in PollingIntervals.kt -- widen this regex");
  const values = [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
  assert.deepEqual(
    sorted(values),
    sorted(ACTIVE_JOB_STATUSES),
    "VALUE DRIFT: PollingIntervals.kt and web/js/lib/job-status.js differ -- change both (and ActiveJobStatusesWebTwinTest.kt)",
  );
});

/** All `.js` files under web/js, repo-relative with forward slashes. */
function webSources(dir = path.join(repo, "web", "js")) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) out.push(...webSources(full));
    else if (name.endsWith(".js")) out.push(path.relative(repo, full).split(path.sep).join("/"));
  }
  return out;
}

/** An array literal made only of string entries (`[...]`, also inside
 * `new Set([...])`), any quotes, any order, any length; entries cannot
 * contain quotes or newlines, which no status name does. */
const STRING_ARRAY = /\[\s*(?:(["'])[^"'\n]*\1\s*,\s*)*(["'])[^"'\n]*\2\s*,?\s*\]/g;
const ACTIVE = ["queued", "running", "paused"];

/** The string entries of every array literal in `src` that contains ALL
 * three active statuses (alone or with extra entries), with the name the
 * literal is assigned to (`const NAME = ...` on the same line), if any. */
function activeLiterals(src) {
  const out = [];
  for (const m of src.matchAll(STRING_ARRAY)) {
    const entries = new Set([...m[0].matchAll(/["']([^"'\n]*)["']/g)].map((x) => x[1]));
    if (!ACTIVE.every((s) => entries.has(s))) continue;
    const lineStart = src.lastIndexOf("\n", m.index) + 1;
    const decl = /\bconst\s+(\w+)\s*=/.exec(src.slice(lineStart, m.index));
    out.push({ name: decl ? decl[1] : null, text: m[0] });
  }
  return out;
}

/** Whole files that may keep literals: the shared module itself, and
 * demo-data.js, which stands in for the SERVER in demo mode (it mirrors the
 * server's ACTIVE_STATUSES, not a frontend rule) and keeps its own copies;
 * the audit item and this package scope the frontend copies only. */
const ALLOWED_FILES = new Set(["web/js/lib/job-status.js", "web/js/demo-data.js"]);

/** Single declarations that contain the three statuses with a different
 * meaning. `KNOWN_STATUSES` (job-partition.js) is every status
 * `partitionJobs` sorts into a section (active + history), tied to its own
 * literal running/paused/queued filters, not "is this job in flight". */
const ALLOWED_DECLS = new Set(["web/js/lib/job-partition.js#KNOWN_STATUSES"]);

test("no web module keeps its own copy of the active set (import lib/job-status.js)", () => {
  const offenders = [];
  for (const file of webSources()) {
    if (ALLOWED_FILES.has(file)) continue;
    for (const lit of activeLiterals(read(file))) {
      if (lit.name && ALLOWED_DECLS.has(`${file}#${lit.name}`)) continue;
      offenders.push(`${file}${lit.name ? ` (${lit.name})` : ""}: ${lit.text}`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    "a literal holding all three active job statuses -- import ACTIVE_JOB_STATUSES from lib/job-status.js, or allowlist a declaration with a different meaning here with its reason",
  );
});

test("every allowlisted declaration still exists (no stale allowlist entry)", () => {
  for (const key of ALLOWED_DECLS) {
    const [file, name] = key.split("#");
    assert.ok(
      activeLiterals(read(file)).some((lit) => lit.name === name),
      `${key} no longer holds the three active statuses -- drop it from ALLOWED_DECLS`,
    );
  }
});

test("the guard sees copies in any shape (self-check of the regex)", () => {
  const shapes = [
    'const X = new Set(["running", "queued", "paused"]);',
    "const X = ['paused', 'queued', 'running'];",
    'const X = new Set(["queued", "running", "paused", "verifying"]);',
    'if (["queued",\n  "running",\n  "paused",\n].includes(s)) {}',
    'jobs.filter((j) => ["done", "queued", "running", "paused"].includes(j.status));',
  ];
  for (const src of shapes) assert.equal(activeLiterals(src).length, 1, src);
  // Not a copy: two of the three, or the statuses only in prose.
  assert.equal(activeLiterals('const LIVE = new Set(["running", "paused"]);').length, 0);
  assert.equal(activeLiterals("// queued, running or paused [see store]").length, 0);
  assert.deepEqual(
    activeLiterals('const KNOWN_STATUSES = new Set(["queued", "running", "paused", "done"]);').map((l) => l.name),
    ["KNOWN_STATUSES"],
  );
});

test("every unified site imports the shared set", () => {
  const sites = [
    "web/js/store.js",
    "web/js/notifications.js",
    "web/js/views/library.js",
    "web/js/components/game-detail-sheet.js",
    "web/js/lib/detail-job.js",
    "web/js/lib/cache-repair.js",
    "web/js/lib/cache-completeness.js",
    "web/js/lib/bulk-plan.js",
    "web/js/lib/updates-available.js",
    "web/js/lib/job-partition.js",
    "web/js/lib/traffic-view.js",
  ];
  for (const file of sites) {
    const src = read(file);
    assert.match(src, /import \{ ACTIVE_JOB_STATUSES \} from "(?:\.\.?\/)+(?:lib\/)?job-status\.js";/, `${file} no longer imports ACTIVE_JOB_STATUSES`);
    assert.match(src, /ACTIVE_JOB_STATUSES\.has\(/, `${file} imports ACTIVE_JOB_STATUSES but never uses it`);
  }
});
