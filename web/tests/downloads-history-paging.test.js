/**
 * WP DOWNLOADS-3 (rc20.1 operator report on the Downloads History):
 *
 *   - the list shows the first 10 rows, then "Show 20 more" (accessible
 *     name "Show 20 more of N") appends 20 at a time; a range change starts
 *     again at 10; an expanded row stays expanded when rows are appended;
 *     a full fetch page (HISTORY_LIMIT) ends with the "Older entries" note;
 *   - the idle subline counts the rows of the History list for the selected
 *     range, not the store's 20-job poll ("Idle · 20 in history" while the
 *     7-day list had ~130 rows);
 *   - the row's status glyph is labelled with the job outcome ("Done",
 *     "Not available to prefill (not owned)"), never the library words
 *     "Current" / "Not in use"; `createStatusIcon`'s optional `label`.
 *
 * Harness: fake-dom.js, a routing `fetch` fake (same as
 * downloads-history-delete-wiring.test.js).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", "test-key"]]);
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test", pathname: "/downloads", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const HOUR = 3_600_000;
/** Finished job `id`, `hoursAgo` old (ids descend with age, newest first). */
const job = (id, hoursAgo, status = "done") => ({
  id, appid: 1000 + (id % 7), type: "prefill", status, stop_request: null,
  created_at: iso(hoursAgo * HOUR + 60_000), started_at: iso(hoursAgo * HOUR + 30_000), finished_at: iso(hoursAgo * HOUR),
});
/** `n` finished jobs, one per hour from 30 minutes ago, newest first
 * (24 of them inside 24 h, none near the boundary). */
const finishedJobs = (n, startId = 5000) => Array.from({ length: n }, (_, i) => job(startId - i, i + 0.5));

const server = { jobs: [], rangeFetches: [] };

function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const p = u.pathname;
  if (p === "/v1/throughput" || p === "/v1/throughput/series" || p === "/v1/throughput/live") return respond(404, { detail: "Not Found" });
  if (p === "/v1/jobs") {
    const range = u.searchParams.get("range");
    const limit = Number(u.searchParams.get("limit") || 20);
    let list = server.jobs;
    if (range) {
      server.rangeFetches.push(range);
      const span = { "24h": 24 * HOUR, "7d": 7 * 24 * HOUR, "30d": 30 * 24 * HOUR }[range];
      if (span) list = list.filter((j) => !j.finished_at || Date.parse(j.finished_at) >= Date.now() - span);
    }
    return respond(200, list.slice(0, limit));
  }
  if (/^\/v1\/jobs\/\d+$/.test(p)) {
    const id = Number(p.split("/").pop());
    return respond(200, { ...server.jobs.find((j) => j.id === id), log_excerpt: "ok", log_truncated: false });
  }
  if (p === "/v1/games" || p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderDownloads, highlightJob } = await import("../js/views/downloads.js");
const { navigateTo } = await import("../js/router.js");
const { createStatusIcon, STATUS_LABEL } = await import("../js/components/status-icon.js");
const { createHistoryItem } = await import("../js/components/history-list.js");
const paging = await import("../js/lib/history-paging.js");

after(() => {
  store.stop();
  navigateTo("library");
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 3000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const role = (root, r) => root.querySelector(`[data-role="${r}"]`);
const rows = (s) => s.querySelectorAll(".hitem");
const sub = (s) => s.querySelector(".page-head .sub");
const rangeBtn = (s, label) => role(s, "history-range").querySelectorAll("button").find((b) => b.textContent === label);

async function mount() {
  navigateTo("library");
  store.start();
  const s = renderDownloads();
  dom.appRoot.replaceChildren(s);
  await until(() => rows(s).length > 0 || s.querySelector(".dl-history .empty") !== null, "history painted");
  return s;
}

beforeEach(() => {
  server.rangeFetches = [];
});

// ---------------------------------------------------------------------
// Pure helpers (lib/history-paging.js)
// ---------------------------------------------------------------------

test("MUTATION PIN: historyPage — 10 first, then 20 per step; the button text and name; the capped note only at the end", () => {
  assert.equal(paging.HISTORY_LIMIT, 200);
  assert.equal(paging.HISTORY_FIRST_PAGE, 10);
  assert.equal(paging.HISTORY_PAGE_STEP, 20);
  assert.deepEqual(paging.historyPage(127, 10), { visible: 10, remaining: 117, next: 20, capped: false });
  assert.deepEqual(paging.historyPage(127, 110), { visible: 110, remaining: 17, next: 17, capped: false });
  assert.deepEqual(paging.historyPage(127, 130), { visible: 127, remaining: 0, next: 0, capped: false });
  assert.deepEqual(paging.historyPage(4, 10), { visible: 4, remaining: 0, next: 0, capped: false });
  assert.deepEqual(paging.historyPage(0, 10, { capped: true }), { visible: 0, remaining: 0, next: 0, capped: false });
  assert.equal(paging.historyPage(200, 30, { capped: true }).capped, false, "the note waits until every row is shown");
  assert.equal(paging.historyPage(200, 210, { capped: true }).capped, true);
  assert.equal(paging.nextShown(10), 30);
  assert.equal(paging.showMoreText(20), "Show 20 more");
  assert.equal(paging.showMoreLabel(20, 117), "Show 20 more of 117");
  assert.equal(paging.isHistoryCapped(199), false);
  assert.equal(paging.isHistoryCapped(200), true);
  assert.equal(paging.HISTORY_CAPPED_TEXT, "Older entries are not shown; narrow the range or delete history.");
});

test("MUTATION PIN: idleSubtitle — no number before the range fetch, then the list's own count (+ when capped)", () => {
  assert.equal(paging.idleSubtitle({ loaded: false, count: 20 }), "Idle");
  assert.equal(paging.idleSubtitle({ loaded: true, count: 127 }), "Idle · 127 in history");
  assert.equal(paging.idleSubtitle({ loaded: true, count: 200, capped: true }), "Idle · 200+ in history");
});

// ---------------------------------------------------------------------
// Status glyph words (components/status-icon.js, history-list.js)
// ---------------------------------------------------------------------

test("MUTATION TARGET: createStatusIcon's optional label replaces the kind's word; glyph and class stay", () => {
  const plain = createStatusIcon("cached", { size: "sm" });
  assert.equal(plain.querySelector(".sr-only").textContent, STATUS_LABEL.cached);
  const done = createStatusIcon("cached", { size: "sm", label: "Done" });
  assert.equal(done.querySelector(".sr-only").textContent, "Done");
  assert.equal(done.className, plain.className, "same kind, same colour class");
  assert.equal(done.querySelector("path").getAttribute("d"), plain.querySelector("path").getAttribute("d"), "same check glyph");
  assert.equal(createStatusIcon("notinuse", { label: "" }).querySelector(".sr-only").textContent, "Not in use", "empty label keeps the kind's word");
});

test("MUTATION TARGET: a History row's glyph says the job outcome, never Current / Not in use", () => {
  const cases = [
    ["cached", "Done"],
    ["error", "Failed"],
    ["cancelled", "Cancelled"],
    ["cached", "Garbage collected"],
    ["notinuse", "Not available to prefill (not owned)"],
  ];
  for (const [kind, word] of cases) {
    const item = createHistoryItem({ job: { id: 1, appid: 2 }, title: "T", kind, word, meta: `job #1 · ${word} · now`, expanded: false, onToggle() {} });
    const label = item.querySelector(".hic .sr-only").textContent;
    assert.equal(label, word, `${kind} -> ${word}`);
    assert.ok(!item.textContent.includes("Current") && !item.textContent.includes("Not in use"), `${word}: no library word in the row text`);
  }
});

// ---------------------------------------------------------------------
// Wiring (views/downloads.js)
// ---------------------------------------------------------------------

test("MUTATION TARGET: 127 rows — first 10, 'Show 20 more of 117', appends 20 and moves focus to the first new row; expanded row stays expanded", async () => {
  server.jobs = finishedJobs(127);
  const s = await mount();
  await until(() => rows(s).length === 10 && role(s, "history-more"), "first page");
  const more = role(s, "history-more");
  assert.equal(more.textContent, "Show 20 more");
  assert.equal(more.getAttribute("aria-label"), "Show 20 more of 117");
  // Expand the 3rd row, then page.
  rows(s)[2].querySelector(".hrow").dispatchEvent({ type: "click" });
  assert.equal(rows(s)[2].querySelector(".hrow").getAttribute("aria-expanded"), "true");
  more.dispatchEvent({ type: "click" });
  assert.equal(rows(s).length, 30);
  assert.equal(rows(s)[2].querySelector(".hrow").getAttribute("aria-expanded"), "true", "expanded row survives the append");
  assert.equal(dom.document.activeElement, rows(s)[10].querySelector(".hrow"), "focus on the first appended row");
  assert.equal(role(s, "history-more").getAttribute("aria-label"), "Show 20 more of 97");
  for (let i = 0; i < 5; i++) role(s, "history-more").dispatchEvent({ type: "click" });
  assert.equal(rows(s).length, 127);
  assert.equal(role(s, "history-more"), null, "no button once everything is shown");
  assert.equal(role(s, "history-capped"), null, "127 < HISTORY_LIMIT: nothing is cut off");
  // collapse again so later tests start clean
  rows(s)[2].querySelector(".hrow").dispatchEvent({ type: "click" });
});

test("MUTATION TARGET: changing the range starts again at 10 rows", async () => {
  server.jobs = finishedJobs(127);
  const s = await mount();
  await until(() => role(s, "history-more"), "button");
  role(s, "history-more").dispatchEvent({ type: "click" });
  assert.ok(rows(s).length > 10);
  rangeBtn(s, "7 days").dispatchEvent({ type: "click" });
  await until(() => server.rangeFetches.includes("7d"), "7d fetch");
  await until(() => role(s, "history-more").getAttribute("aria-label") === "Show 20 more of 117", "7d answer painted");
  assert.equal(rows(s).length, 10, "reset to the first page");
  rangeBtn(s, "All").dispatchEvent({ type: "click" });
  await until(() => rows(s).length === 10 && server.rangeFetches.includes("all"), "back on All");
});

test("MUTATION TARGET: the idle subline counts the History list for the range, not the store's 20-job poll", async () => {
  server.jobs = finishedJobs(127);
  const s = await mount();
  await until(() => /in history/.test(sub(s).textContent), "subline count");
  assert.equal(sub(s).textContent, "Idle · 127 in history", "not 'Idle · 20 in history'");
  rangeBtn(s, "24 h").dispatchEvent({ type: "click" });
  await until(() => sub(s).textContent === "Idle · 24 in history", "24h count");
  rangeBtn(s, "All").dispatchEvent({ type: "click" });
  await until(() => sub(s).textContent === "Idle · 127 in history", "All again");
});

test("MUTATION TARGET: a deep-linked row beyond the first page is paged in and expanded (remount, then highlightJob)", async () => {
  server.jobs = finishedJobs(127);
  let s = await mount();
  await until(() => role(s, "history-more") && role(s, "history-more").getAttribute("aria-label") === "Show 20 more of 117", "history loaded");
  s = await mount(); // a notification click remounts; history is already loaded
  await until(() => rows(s).length === 10, "first page again");
  const target = server.jobs[25]; // row 26
  const proto = Object.getPrototypeOf(s);
  const hadScroll = Object.hasOwn(proto, "scrollIntoView");
  if (!hadScroll) proto.scrollIntoView = function scrollIntoView() {}; // fake-dom has no layout
  try {
    highlightJob(target.id);
  } finally {
    if (!hadScroll) delete proto.scrollIntoView;
  }
  const item = s.querySelector(`.hitem[data-jid="${target.id}"]`);
  assert.ok(item, "row 26 is on screen");
  assert.equal(item.querySelector(".hrow").getAttribute("aria-expanded"), "true");
  assert.equal(rows(s).length, 30, "paged in by whole steps");
  item.querySelector(".hrow").dispatchEvent({ type: "click" }); // collapse for later tests
});

test("MUTATION TARGET: a full fetch page ends with the 'Older entries are not shown' note and a '+' count", async () => {
  server.jobs = finishedJobs(230);
  const s = await mount();
  await until(() => sub(s).textContent === "Idle · 200+ in history", "capped count");
  for (let i = 0; i < 10 && role(s, "history-more"); i++) role(s, "history-more").dispatchEvent({ type: "click" });
  assert.equal(rows(s).length, 200);
  assert.equal(role(s, "history-more"), null);
  assert.equal(role(s, "history-capped").textContent, "Older entries are not shown; narrow the range or delete history.");
});
