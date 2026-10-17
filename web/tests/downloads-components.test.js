/**
 * WP DOWNLOADS-2: the three Downloads components on their own —
 * components/downloads-hero.js (pure `heroModel`/`graphModel`/`splitFigure`/
 * `devicesLoading`, plus the built card), components/job-row.js (job card,
 * attention and queue rows; no progress bar without progress data) and
 * components/history-list.js (row + hanging panel in every excerpt state).
 *
 * Harness: fake-dom.js only, no store, no fetch. Text and attributes are
 * asserted, never node identity (docs/LEARNINGS.md, WEB-FIX-1).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;

const { heroModel, graphModel, splitFigure, devicesLoading, createDownloadsHero, GRAPH_GRID } = await import("../js/components/downloads-hero.js");
const { jobProgress, createJobCard, createAttentionRow, createQueueRow, verifyAllAriaLabel } = await import("../js/components/job-row.js");
const { createHistoryItem, setHistoryItemExpanded, paintHistoryPanel, TRUNCATED_NOTE } = await import("../js/components/history-list.js");
const { selectExcerptDisplay } = await import("../js/lib/log-excerpt.js");

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");

function throughput({ state = "ok", current = 40 * MIB, clients = 2, runner = null } = {}) {
  return {
    event_feed: { enabled: state !== "disabled", state, last_sweep_at: iso(60_000), sweep_interval_minutes: 5 },
    range: "6h",
    tier: "5m",
    bucket_seconds: 300,
    active_window_minutes: 15,
    current: current === null ? null : { bytes_per_second: current, window_seconds: 480 },
    active_clients_now: clients,
    summary:
      state === "disabled"
        ? null
        : {
            bytes_served: 60 * GIB,
            hit_bytes: 50 * GIB,
            miss_bytes: 10 * GIB,
            byte_hit_rate: 50 / 60,
            average_bytes_per_second: 50 * MIB,
            peak_bytes_per_second: 100 * MIB,
            active_clients: 3,
            prefill_runner: runner,
          },
    jobs: [],
  };
}

// ---------------------------------------------------------------------
// downloads-hero.js
// ---------------------------------------------------------------------

test("splitFigure: number + unit, percent, '< 1 MB', the dash", () => {
  assert.deepEqual(splitFigure("60.0 GB"), { value: "60.0", unit: "GB" });
  assert.deepEqual(splitFigure("38.2 MB/s"), { value: "38.2", unit: "MB/s" });
  assert.deepEqual(splitFigure("83%"), { value: "83", unit: "%" });
  assert.deepEqual(splitFigure("< 1 MB"), { value: "< 1", unit: "MB" });
  assert.deepEqual(splitFigure("—"), { value: "—", unit: "" });
  assert.deepEqual(splitFigure(null), { value: "—", unit: "" });
});

test("MUTATION PIN: devicesLoading only with a fresh feed; zero reads 'no device loading', unknown shows nothing", () => {
  assert.equal(devicesLoading(throughput({ clients: 1 })).text, "1 device loading");
  assert.equal(devicesLoading(throughput({ clients: 3 })).text, "3 devices loading");
  assert.equal(devicesLoading(throughput({ clients: 0 })).text, "no device loading");
  assert.equal(devicesLoading(throughput({ clients: null })), null);
  assert.equal(devicesLoading(throughput({ state: "stale", clients: 2 })), null);
  assert.equal(devicesLoading(null), null);
});

test("MUTATION PIN: heroModel maps the figures onto the kit anatomy (big, pill, caption, mini, four tiles)", () => {
  const m = heroModel({ throughput: throughput({ runner: { bytes_served: 20 * GIB, hit_bytes: 5 * GIB, miss_bytes: 15 * GIB } }), rateCap: "Limit 8 MB/s, all day" });
  assert.equal(m.note, null);
  const f = m.figures;
  assert.deepEqual([f.big.value, f.big.unit], ["40.0", "MB/s"]);
  assert.deepEqual(f.status, { kind: "running", text: "Downloading" });
  assert.deepEqual(f.caption, ["Limit ", { b: "8 MB/s" }, ", all day", " · ", "2 devices loading"]);
  assert.deepEqual(f.mini.map((i) => i.key), ["average", "peak", "runner"]);
  assert.deepEqual(f.mini[2].parts, ["Prefill runner ", { b: "20.0 GB" }, " of it, ", { b: "5.0 GB" }, " from the cache"]);
  assert.deepEqual(
    f.tiles.map((t) => `${t.label}=${t.value}${t.unit}`),
    ["Served=60.0GB", "From internet=25.0GB", "Saved by cache=50.0GB", "Hit rate=83%"],
  );
  assert.equal(Math.round(f.tiles[3].meter * 100), 83);
  assert.equal(f.tiles[0].meter, null);
});

test("heroModel: an idle cache says Idle; an unknown current speed shows a dash and no pill", () => {
  assert.deepEqual(heroModel({ throughput: throughput({ current: 0 }) }).figures.status, { kind: "notinuse", text: "Idle" });
  const stale = heroModel({ throughput: throughput({ state: "stale", current: null }) });
  assert.equal(stale.figures.status, null);
  assert.equal(stale.figures.big.value, "—");
  assert.match(stale.note, /out of date/);
});

test("heroModel: no figures before an answer, with the feed off, or for an old server", () => {
  assert.deepEqual(heroModel({}), { note: "Loading statistics…", figures: null });
  assert.equal(heroModel({ throughput: throughput({ state: "disabled" }) }).figures, null);
  assert.match(heroModel({ throughput: throughput({ state: "disabled" }) }).note, /^Traffic statistics are off/);
  assert.match(heroModel({ unsupported: true }).note, /does not report traffic statistics/);
});

test("MUTATION PIN: graphModel — 600x96 geometry, peak caption, null for another range or an all-zero series", () => {
  const series = {
    event_feed: { state: "ok" },
    range: "6h",
    bucket_seconds: 300,
    range_start: iso(900_000),
    points: [0, 10, 5].map((v, i) => ({ t: iso((3 - i) * 300_000), bytes_per_second: v * MIB })),
  };
  series.range_start = series.points[0].t;
  const g = graphModel(series, "6h");
  assert.equal(g.line, "M0 96 L300 0 L600 48");
  assert.deepEqual(g.axis, ["6 h ago", "peak 10.0 MB/s", "now"]);
  assert.equal(graphModel(series, "24h"), null, "an answer for another range is not drawn");
  assert.equal(graphModel({ ...series, points: series.points.map((p) => ({ ...p, bytes_per_second: 0 })) }, "6h"), null);
  assert.deepEqual(GRAPH_GRID, [24, 48, 72]);
});

test("createDownloadsHero: paint fills the card; the pill is rebuilt only when its kind changes", () => {
  const hero = createDownloadsHero({ range: "6h", onRangeChange() {} });
  const q = (role) => hero.el.querySelector(`[data-role="${role}"]`);
  assert.equal(hero.rangeEl.className, "seg rangesegs");
  const state = { throughput: throughput(), series: null, range: "6h", rateCap: null, unsupported: false, error: null, noFigures: false };
  hero.paint(state);
  assert.equal(q("stats-current").textContent, "40.0MB/s");
  assert.equal(q("stats-pill").textContent, "Downloading");
  const pill = q("stats-pill").children[0];
  pill.setAttribute("data-marker", "kept");
  hero.paint(state);
  assert.equal(q("stats-pill").children[0].getAttribute("data-marker"), "kept", "same kind: the animated pill stays");
  hero.paint({ ...state, throughput: throughput({ current: 0 }) });
  assert.equal(q("stats-pill").textContent, "Idle");
  assert.equal(q("stats-grid").querySelectorAll(".tile").length, 4);
  assert.equal(q("stats-graph").hidden, true, "no series, no graph");
  hero.paint({ ...state, throughput: null, noFigures: true, unsupported: true });
  assert.equal(q("stats-live").hidden, true);
  assert.equal(hero.rangeEl.hidden, true);
});

// ---------------------------------------------------------------------
// job-row.js
// ---------------------------------------------------------------------

test("MUTATION PIN: no fake progress — the bar exists only with a real 0..1 progress value", () => {
  assert.equal(jobProgress({}), null);
  assert.equal(jobProgress({ progress: 1.2 }), null);
  assert.equal(jobProgress({ progress: "0.5" }), null);
  assert.equal(jobProgress({ progress: 0.62 }), 0.62);
  const job = { id: 7, appid: 70, status: "running" };
  const plain = createJobCard({ job, title: "Seven", kind: "running", word: "Downloading" });
  assert.equal(plain.querySelector('[data-role="job-progress"]'), null);
  const real = createJobCard({ job: { ...job, progress: 0.62 }, title: "Seven", kind: "running", word: "Downloading" });
  const bar = real.querySelector('[data-role="job-progress"]');
  assert.equal(bar.getAttribute("aria-valuenow"), "62");
  assert.equal(bar.children[0].style.getPropertyValue("width"), "62%");
});

test("createJobCard: name, meta, pill, speed line for a running job; hold note instead for a paused one", () => {
  const job = { id: 7, appid: 70, status: "running" };
  const card = createJobCard({ job, title: "Seven", kind: "updating", word: "Updating" });
  assert.equal(card.className, "job");
  assert.equal(card.getAttribute("data-dk"), "updating");
  assert.equal(card.querySelector(".nm").getAttribute("data-appid"), "70");
  assert.equal(card.querySelector(".meta").textContent, "job #7 · appid 70");
  assert.equal(card.querySelector('[data-role="job-pill"]').className, "pill run");
  assert.equal(card.querySelector('[data-role="job-speed"]').hidden, true);
  const held = createJobCard({ job: { ...job, status: "paused" }, title: "Seven", kind: "paused", word: "Paused", held: true, holdNote: "held" });
  assert.equal(held.className, "job held");
  assert.equal(held.querySelector('[data-role="job-speed"]'), null);
  assert.equal(held.querySelector('[data-role="hold-note"]').textContent, "held");
});

test("createAttentionRow / createQueueRow: the kit .jr anatomy; a row without a button shows its note", () => {
  const btn = dom.document.createElement("button");
  btn.textContent = "Update";
  const row = createAttentionRow({ appid: 10, title: "Aurora", meta: "3.0 GB", button: btn, dataKey: "updateAppid" });
  assert.equal(row.className, "jr urow");
  assert.equal(row.getAttribute("data-update-appid"), "10");
  assert.deepEqual(row.children.map((c) => c.textContent), ["Aurora3.0 GB", "", "Update"]);
  const damaged = createAttentionRow({ appid: 11, title: "Broken", meta: "99% on disk", button: null, note: "Damaged", dataKey: "incompleteAppid" });
  assert.equal(damaged.querySelector('[data-role="damaged-note"]').textContent, "Damaged");
  assert.equal(damaged.children.length, 3, "the 72px column stays (empty)");
  const grip = dom.document.createElement("span");
  const remove = dom.document.createElement("button");
  remove.textContent = "Remove";
  const q = createQueueRow({ jobId: 5, appid: 12, title: "Queued", size: "1.0 GB", grip, removeButton: remove });
  assert.equal(q.className, "jr qrow");
  assert.equal(q.getAttribute("data-jid"), "5");
  assert.equal(q.querySelector(".sz").textContent, "1.0 GB");
  assert.equal(q.querySelector(".sz").getAttribute("data-size-appid"), "12");
  assert.equal(verifyAllAriaLabel(1), "Verify all 1 game");
  assert.equal(verifyAllAriaLabel(2), "Verify all 2 games");
});

// ---------------------------------------------------------------------
// history-list.js
// ---------------------------------------------------------------------

function item(job = { id: 9, appid: 90, status: "error", type: "prefill" }, expanded = false) {
  let toggled = 0;
  const el = createHistoryItem({ job, title: "Nine", kind: "error", meta: "job #9 · Failed · now", expanded, onToggle: () => (toggled += 1) });
  return { el, toggled: () => toggled };
}

test("createHistoryItem: a row button with aria-expanded/-controls and a hidden hanging panel", () => {
  const { el, toggled } = item();
  assert.equal(el.className, "hitem");
  const row = el.querySelector(".hrow");
  assert.equal(row.tagName, "BUTTON");
  assert.equal(row.getAttribute("aria-expanded"), "false");
  assert.equal(row.getAttribute("aria-controls"), "hpanel-9");
  assert.equal(el.querySelector(".hpanel").hidden, true);
  assert.equal(el.querySelector(".hrow .meta").textContent, "job #9 · Failed · now");
  row.dispatchEvent({ type: "click" });
  assert.equal(toggled(), 1);
  setHistoryItemExpanded(el, true);
  assert.equal(el.classList.contains("open"), true);
  assert.equal(row.getAttribute("aria-expanded"), "true");
});

test("MUTATION PIN: paintHistoryPanel — a known failure: titled hint, bordered command, Retry, output folded in a closed <details>", () => {
  const { el } = item(undefined, true);
  const panel = el.querySelector(".hpanel");
  let raw = null;
  paintHistoryPanel(panel, {
    display: selectExcerptDisplay({ expanded: true, excerpt: "[...truncated...]\nline one\nline two" }),
    hint: "not_logged_in",
    failed: true,
    retry: { offer: "retry", make: () => Object.assign(dom.document.createElement("button"), { textContent: "Retry" }) },
    rawOpen: false,
    onRawToggle: (open) => (raw = open),
  });
  assert.equal(panel.hidden, false);
  assert.equal(panel.children[0].className, "failhint");
  assert.equal(panel.querySelector(".failhint-title").textContent, "Steam login missing");
  assert.equal(panel.querySelector("code.cmd") !== null, true);
  assert.deepEqual(panel.querySelectorAll("button").map((b) => b.textContent), ["Retry"]);
  const details = panel.querySelector("details.rawlog");
  assert.equal(details.open, false);
  assert.equal(details.querySelector(".truncnote").textContent, TRUNCATED_NOTE);
  assert.equal(details.querySelector("pre.logbox").textContent, "line one\nline two", "the boxed log inside the details");
  details.open = true;
  details.dispatchEvent({ type: "toggle" });
  assert.equal(raw, true);
});

test("paintHistoryPanel: generic failure keeps the log unfolded; a done job shows the boxed excerpt; collapsed empties it", () => {
  const { el } = item(undefined, true);
  const panel = el.querySelector(".hpanel");
  paintHistoryPanel(panel, {
    display: selectExcerptDisplay({ expanded: true, excerpt: "disk full" }),
    failed: true,
    retry: { offer: "newer", make: null },
  });
  assert.equal(panel.querySelector(".failhint").dataset.hint, "generic");
  assert.equal(panel.querySelector("details"), null);
  assert.equal(panel.querySelector("pre.logbox").textContent, "disk full");
  assert.match(panel.textContent, /A newer job for this game exists/);

  paintHistoryPanel(panel, { display: selectExcerptDisplay({ expanded: true, excerpt: "ok\ndone" }) });
  assert.equal(panel.children.length, 1);
  assert.equal(panel.children[0].className, "logbox");

  paintHistoryPanel(panel, { display: selectExcerptDisplay({ expanded: true, loading: true }) });
  assert.equal(panel.textContent, "Loading log…");
  paintHistoryPanel(panel, { display: selectExcerptDisplay({ expanded: false }) });
  assert.equal(panel.hidden, true);
  assert.equal(panel.children.length, 0);
});
