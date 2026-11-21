/**
 * WP APP-DL-CARD-1: the design pass of the Downloads job card (operator
 * report 2026-11-01). Pins the hierarchy the card now has on both
 * platforms (Android twin: `JobCard` in DownloadsScreen.kt, pinned by
 * `JobCardDesignTwinTest`): title block beside the status pill, then the
 * figures (speed line, figures in bold), the notes, the actions; spacing on
 * the --sp scale; the name in the card-title size.
 *
 * Harness: fake-dom.js only, no store, no fetch.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;

const { createJobCard, fillJobSpeed } = await import("../js/components/job-row.js");
const { jobFigureParts, jobSpeedText, JOB_FIGURE_PATTERN, VERIFY_SPEED_TEXT } = await import("../js/lib/traffic-view.js");

const read = (...p) => readFileSync(new URL(`../${p.join("/")}`, import.meta.url), "utf8");
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const kitCss = stripComments(read("css", "kit.css"));

/** The declarations of the first rule whose selector is exactly `selector`. */
function ruleBody(css, selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|[}\\n])\\s*${esc}\\s*\\{([^}]*)\\}`).exec(css);
  return m ? m[1] : null;
}

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;

test("jobFigureParts: speeds, byte totals and durations in bold, the words untouched", () => {
  assert.deepEqual(jobFigureParts("31.5 MB/s · 8.8 GB loaded so far"), [{ b: "31.5 MB/s" }, " · ", { b: "8.8 GB" }, " loaded so far"]);
  assert.deepEqual(jobFigureParts("0 MB/s · 0 GB loaded so far"), [{ b: "0 MB/s" }, " · ", { b: "0 GB" }, " loaded so far"]);
  assert.deepEqual(jobFigureParts("< 1 KB/s · < 1 MB loaded so far"), [{ b: "< 1 KB/s" }, " · ", { b: "< 1 MB" }, " loaded so far"]);
  assert.deepEqual(
    jobFigureParts("1.2 GB/s · 3.0 GB of ~51.6 GB loaded so far (expected size) · about 2 h 5 min left"),
    [{ b: "1.2 GB/s" }, " · ", { b: "3.0 GB" }, " of ", { b: "~51.6 GB" }, " loaded so far (expected size) · about ", { b: "2 h" }, " ", { b: "5 min" }, " left"],
  );
  assert.deepEqual(jobFigureParts("312 KB/s"), [{ b: "312 KB/s" }]);
  assert.deepEqual(jobFigureParts("40.0 MB/s · about 2 h left"), [{ b: "40.0 MB/s" }, " · about ", { b: "2 h" }, " left"]);
});

test("jobFigureParts: a line without a figure is one plain part, nothing is no part", () => {
  assert.deepEqual(jobFigureParts(VERIFY_SPEED_TEXT), [VERIFY_SPEED_TEXT]);
  const shared = "Speed not measured: this game's depots are shared with other apps.";
  assert.deepEqual(jobFigureParts(shared), [shared]);
  assert.deepEqual(jobFigureParts(""), []);
  assert.deepEqual(jobFigureParts(null), []);
  assert.deepEqual(jobFigureParts(undefined), []);
  // A unit inside a word is no figure ("5 hosts", "3 minutes").
  assert.deepEqual(jobFigureParts("5 hosts, 3 minutes"), ["5 hosts, 3 minutes"]);
});

test("jobFigureParts on real jobSpeedText lines: the parts join back to the line, every figure bold", () => {
  const started = new Date(Date.now() - 600_000).toISOString();
  const entries = [
    { bytes_per_second: 31.5 * MIB, bytes_since_start: 8.8 * GIB, exclusive_depots: 1, started_at: started },
    { bytes_per_second: 0, bytes_since_start: 0, exclusive_depots: 1, started_at: started },
    { bytes_per_second: 500, bytes_since_start: 1000, exclusive_depots: 1, started_at: started },
    {
      bytes_per_second: 40 * MIB, eta_bytes_per_second: 40 * MIB, bytes_since_start: 3 * GIB, remaining_bytes: 4 * GIB,
      expected: true, expected_download_bytes: 51.6 * GIB, exclusive_depots: 1, started_at: started,
    },
  ];
  for (const entry of entries) {
    const text = jobSpeedText(entry, "ok");
    assert.ok(text, JSON.stringify(entry));
    const parts = jobFigureParts(text);
    assert.equal(parts.map((p) => (typeof p === "string" ? p : p.b)).join(""), text);
    const bold = parts.filter((p) => typeof p !== "string").map((p) => p.b);
    assert.ok(bold.length >= 2, `${text}: ${bold.join("|")}`);
    assert.match(bold[0], /\/s$/, "the speed comes first");
  }
});

test("JOB_FIGURE_PATTERN has no backslash (the Android twin pins the same string)", () => {
  assert.equal(JOB_FIGURE_PATTERN.includes("\\"), false);
  assert.match(read("js", "lib", "traffic-view.js"), /export const JOB_FIGURE_PATTERN = "[^"\\]+";/);
});

test("fillJobSpeed: bold figures, hidden without text, rebuilt only when the words change", () => {
  const card = createJobCard({ job: { id: 7, appid: 70, status: "running" }, title: "Seven", kind: "running", word: "Downloading" });
  const line = card.querySelector('[data-role="job-speed"]');
  assert.equal(line.hidden, true);
  fillJobSpeed(line, "31.5 MB/s · 8.8 GB loaded so far");
  assert.equal(line.hidden, false);
  assert.equal(line.textContent, "31.5 MB/s · 8.8 GB loaded so far");
  assert.deepEqual(line.querySelectorAll("b").map((b) => b.textContent), ["31.5 MB/s", "8.8 GB"]);
  // Same words: the nodes stay (a 3 s live repaint does not churn the DOM).
  line.querySelector("b").setAttribute("data-mark", "kept");
  fillJobSpeed(line, "31.5 MB/s · 8.8 GB loaded so far");
  assert.equal(line.querySelector("b").getAttribute("data-mark"), "kept");
  fillJobSpeed(line, "32.0 MB/s · 8.9 GB loaded so far");
  assert.equal(line.querySelector("b").getAttribute("data-mark"), null);
  assert.deepEqual(line.querySelectorAll("b").map((b) => b.textContent), ["32.0 MB/s", "8.9 GB"]);
  fillJobSpeed(line, VERIFY_SPEED_TEXT);
  assert.equal(line.querySelectorAll("b").length, 0);
  assert.equal(line.textContent, VERIFY_SPEED_TEXT);
  fillJobSpeed(line, null);
  assert.equal(line.hidden, true);
  assert.equal(line.textContent, "");
  // The pill is never touched by a speed write.
  assert.equal(card.querySelector('[data-role="job-pill"]').textContent, "Downloading");
});

test("the card's order: title + pill, figures (or hold note), client note, stop note, actions", () => {
  const job = { id: 7, appid: 70, status: "running" };
  const roleOf = (c) => c.getAttribute("data-role") || c.className;
  const running = createJobCard({ job, title: "Seven", kind: "running", word: "Downloading" });
  assert.deepEqual(running.children.map(roleOf), ["t", "job-speed", "client-note", "stop-note", "job-acts"]);
  const t = running.children[0];
  assert.equal(t.children.length, 2, "title block, then the pill");
  assert.deepEqual(t.children[0].children.map((c) => c.className), ["nm", "meta"]);
  assert.equal(t.children[1].getAttribute("data-role"), "job-pill");
  const held = createJobCard({ job: { ...job, status: "paused" }, title: "Seven", kind: "paused", word: "Paused", held: true, holdNote: "held" });
  assert.deepEqual(held.children.map(roleOf), ["t", "hold-note", "client-note", "stop-note", "job-acts"]);
});

test("kit.css: the card spacing is on the scale, the name is a card title, figures are bold like the hero's", () => {
  const card = ruleBody(kitCss, ".kit .job");
  assert.ok(card, ".kit .job rule");
  assert.match(card, /padding:var\(--sp-3\) var\(--sp-4\);/);
  assert.match(card, /gap:var\(--sp-3\);/);
  assert.match(ruleBody(kitCss, ".kit .job .t") || "", /gap:var\(--sp-3\);/);
  assert.match(ruleBody(kitCss, ".kit .job .nm") || "", /font-size:var\(--fs-4\); font-weight:500;/);
  const bold = ruleBody(kitCss, ".kit .job .m2 b");
  assert.equal(bold, ruleBody(kitCss, ".kit .live .hcap b"), "the job figures look like the Throughput caption's");
  assert.match(bold || "", /color:var\(--text\); font-weight:500;/);
  // The notes keep their rule (the long held note wraps beside it).
  assert.match(ruleBody(kitCss, ".kit .job .holdnote, .kit .job .stopnote") || "", /border-left:2px solid var\(--line\); padding-left:var\(--sp-2\);/);
});

test("MUTATION PIN: the Downloads view writes the speed line through fillJobSpeed", () => {
  const view = read("js", "views", "downloads.js");
  const start = view.indexOf("function paintJobSpeeds(");
  assert.ok(start >= 0, "paintJobSpeeds is gone");
  const body = view.slice(start, view.indexOf("\n}\n", start));
  assert.ok(body.includes("fillJobSpeed(line, text);"), "paintJobSpeeds no longer calls fillJobSpeed");
  assert.equal(body.includes("line.textContent ="), false, "paintJobSpeeds writes plain text again (no bold figures)");
});
