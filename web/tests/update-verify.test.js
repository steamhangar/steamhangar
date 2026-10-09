/**
 * WP WEB-FEAT-6: the Updating / Verifying states (user decision
 * 2026-10-09: show them).
 *
 * The API has no `verify` job status and no live phase field, so both are
 * derived from the games row of an app whose prefill job is `running`
 * (lib/game-status.js `liveRunKind`, module header there):
 *   last_prefill_at null                     -> running  "Downloading"
 *   last_prefill_at set, needs_force false   -> updating "Updating"
 *   last_prefill_at set, needs_force true    -> verify   "Verifying"
 *
 * Pinned here: the derivation and its "never from live bytes" rule, the
 * paused/queued/GC boundaries, the Downloads card helpers
 * (lib/job-partition.js `activeJobKind`/`activeJobWord`) and their word
 * table against STATUS_LABEL, the library card's DOM (fake-dom), and the
 * detail-sheet header wiring (source scan, same idiom as
 * tool-app.test.js). The Downloads view's DOM wiring is pinned in
 * downloads-update-verify.test.js (own process: it starts the store).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";
import { KIND, dispKind, liveRunKind, statusAction, statusIconKind, statusWordFor } from "../js/lib/game-status.js";
import { activeJobKind, activeJobWord, partitionJobs, countPending, jobIconKind, jobStatusWord } from "../js/lib/job-partition.js";
import { STATUS_LABEL } from "../js/components/status-icon.js";
import { visibleGames } from "../js/lib/library-filters.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");

const T = "2026-10-09T08:00:00Z";
function game(over = {}) {
  return {
    appid: 440,
    name: "Aurora Cascade",
    status: "running",
    last_prefill_at: null,
    last_manifest_check: null,
    depot_count: 2,
    size_bytes: null,
    needs_force: true,
    installed_on: [],
    tool_app: false,
    tool_app_name: null,
    ...over,
  };
}
const fresh = (over = {}) => game({ last_prefill_at: null, needs_force: true, ...over });
const refresh = (over = {}) => game({ last_prefill_at: T, needs_force: false, size_bytes: 5e9, ...over });
const reverify = (over = {}) => game({ last_prefill_at: T, needs_force: true, size_bytes: 5e9, ...over });
const job = (status, over = {}) => ({ id: 7, appid: 440, type: "prefill", status, stop_request: null, ...over });

// ---------------------------------------------------------------------
// lib/game-status.js
// ---------------------------------------------------------------------

test("KIND gains exactly updating and verify, with the status-icon wire names", () => {
  assert.equal(KIND.UPDATING, "updating");
  assert.equal(KIND.VERIFY, "verify");
  assert.deepEqual(
    Object.values(KIND).sort(),
    ["cached", "error", "none", "paused", "running", "tool", "updating", "verify"],
  );
});

test("MUTATION TARGET -- liveRunKind: the three-way mapping", () => {
  assert.equal(liveRunKind(fresh()), "running", "never completed -> Downloading");
  assert.equal(liveRunKind(refresh()), "updating", "completed copy, non-forced run -> Updating");
  assert.equal(liveRunKind(reverify()), "verify", "completed copy, forced run -> Verifying");
});

test("MUTATION TARGET -- liveRunKind ignores live bytes (a first fill never turns into Updating)", () => {
  // size_bytes grows during a first fill; the decision must not follow it.
  assert.equal(liveRunKind(fresh({ size_bytes: 12e9 })), "running");
  assert.equal(liveRunKind(fresh({ size_bytes: 12e9, needs_force: false })), "running");
  // ...and a completed copy whose bytes the size cache has not seen yet is
  // still a refresh, not a download.
  assert.equal(liveRunKind(refresh({ size_bytes: null })), "updating");
});

test("liveRunKind: no games row yet -> running; needs_force must be literally true", () => {
  assert.equal(liveRunKind(undefined), "running");
  assert.equal(liveRunKind(null), "running");
  assert.equal(liveRunKind(refresh({ needs_force: undefined })), "updating", "older server without the field");
  assert.equal(liveRunKind(refresh({ needs_force: 1 })), "updating", "strictly true, like isToolApp");
});

test("MUTATION TARGET -- dispKind: a running live job takes liveRunKind; paused stays paused", () => {
  assert.equal(dispKind(refresh(), job("running")), "updating");
  assert.equal(dispKind(reverify(), job("running")), "verify");
  assert.equal(dispKind(fresh(), job("running")), "running");
  assert.equal(dispKind(refresh(), job("paused")), "paused");
  assert.equal(dispKind(reverify(), job("paused")), "paused");
  // Without a live job the cache state is unchanged by this WP.
  assert.equal(dispKind(refresh({ status: "done" }), undefined), "cached");
  assert.equal(dispKind(reverify({ status: "done" }), undefined), "cached");
});

test("statusAction: an Updating or Verifying run can be paused like any running job", () => {
  assert.deepEqual(statusAction(refresh(), job("running"), false), { type: "pause", title: "Pause download" });
  assert.deepEqual(statusAction(reverify(), job("running"), false), { type: "pause", title: "Pause download" });
  assert.equal(statusAction(reverify(), job("running"), true), null, "multi-select still wins");
});

test("words and icon kinds: Updating / Verifying from STATUS_LABEL, icon kind passes through", () => {
  assert.equal(statusIconKind("updating"), "updating");
  assert.equal(statusIconKind("verify"), "verify");
  assert.equal(statusWordFor("updating", STATUS_LABEL), "Updating");
  assert.equal(statusWordFor("verify", STATUS_LABEL), "Verifying");
});

test("library filters: an Updating/Verifying game is under Downloading, not under Cached", () => {
  const games = [refresh({ appid: 1 }), reverify({ appid: 2 }), refresh({ appid: 3, status: "done" })];
  const liveJobsByAppid = new Map([[1, job("running", { appid: 1 })], [2, job("running", { appid: 2 })]]);
  const keys = (filterKey) => visibleGames(games, { query: "", filterKey, liveJobsByAppid }).map((g) => g.appid);
  assert.deepEqual(keys("downloading"), [1, 2]);
  assert.deepEqual(keys("cached"), [3]);
});

// ---------------------------------------------------------------------
// lib/job-partition.js (Downloads card helpers)
// ---------------------------------------------------------------------

test("MUTATION TARGET -- activeJobKind: running prefill follows the games row", () => {
  assert.equal(activeJobKind(job("running"), refresh()), "updating");
  assert.equal(activeJobKind(job("running"), reverify()), "verify");
  assert.equal(activeJobKind(job("running"), fresh()), "running");
  assert.equal(activeJobKind(job("running"), undefined), "running", "games not polled yet");
});

test("activeJobKind: paused, GC and finished jobs keep jobIconKind", () => {
  assert.equal(activeJobKind(job("paused"), reverify()), "paused");
  assert.equal(activeJobKind(job("running", { type: "gc" }), refresh()), "running", "GC is no prefill run");
  for (const s of ["queued", "done", "error", "cancelled", "weird"]) {
    assert.equal(activeJobKind(job(s), refresh()), jobIconKind(job(s)), s);
  }
});

test("MUTATION TARGET -- activeJobWord: Updating / Verifying, else jobStatusWord", () => {
  assert.equal(activeJobWord(job("running"), refresh()), "Updating");
  assert.equal(activeJobWord(job("running"), reverify()), "Verifying");
  assert.equal(activeJobWord(job("running"), fresh()), "Downloading");
  assert.equal(activeJobWord(job("paused"), refresh()), "Paused");
  assert.equal(activeJobWord(job("running", { type: "gc" }), refresh()), "Collecting garbage");
  assert.equal(activeJobWord(job("done"), refresh()), jobStatusWord(job("done")));
});

test("activeJobWord's literal words equal STATUS_LABEL (two copies, one vocabulary)", () => {
  assert.equal(activeJobWord(job("running"), refresh()), STATUS_LABEL.updating);
  assert.equal(activeJobWord(job("running"), reverify()), STATUS_LABEL.verify);
});

test("partition and pip are unchanged: verify is a kind of a running job, not a status", () => {
  const jobs = [job("running", { id: 1 }), job("paused", { id: 2 }), job("queued", { id: 3 })];
  const p = partitionJobs(jobs);
  assert.deepEqual(p.running.map((j) => j.id), [1]);
  assert.equal(countPending(jobs), 3);
  // A job whose STATUS is "verify" (not something the API sends) is still
  // an unknown status, routed to history.
  assert.deepEqual(partitionJobs([job("verify", { id: 9 })]).history.map((j) => j.id), [9]);
});

// ---------------------------------------------------------------------
// components/game-card.js (fake DOM)
// ---------------------------------------------------------------------

function noop() {}
const ctx = (liveJob) => ({ liveJob, picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop });

async function withCard(fn) {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const mod = await import("../js/components/game-card.js");
  return fn(mod);
}

for (const [label, g, kind, word] of [
  ["Updating", refresh(), "updating", "Updating"],
  ["Verifying", reverify(), "verify", "Verifying"],
  ["Downloading", fresh(), "running", "Downloading"],
]) {
  test(`MUTATION TARGET -- card: a running job over a ${label} row renders ${kind}`, () =>
    withCard(({ buildCard, cardStructuralKey }) => {
      const card = buildCard(g, ctx(job("running")));
      assert.equal(card.dataset.dk, kind);
      assert.equal(cardStructuralKey(g, job("running")), kind, "the patch planner sees the same key");
      const state = card.querySelector(".state");
      assert.equal(state.textContent, word);
      assert.equal(state.classList.contains(`tx-${kind}`), true);
      const icons = card.querySelectorAll(".sic");
      assert.equal(icons.length, 2, "pill icon and meta-row icon");
      for (const icon of icons) assert.equal(icon.classList.contains(`k-${kind}`), true, icon.className);
      assert.match(card.getAttribute("aria-label"), new RegExp(` — ${word}`));
    }));
}

test("card: the refresh glyph (.rot) is what an Updating/Verifying card draws", () =>
  withCard(({ buildCard }) => {
    for (const g of [refresh(), reverify()]) {
      const card = buildCard(g, ctx(job("running")));
      for (const icon of card.querySelectorAll(".sic")) {
        // SVG groups carry their class as an attribute (status-icon.js svgEl).
        const groups = icon.querySelectorAll("g").map((g) => g.getAttribute("class"));
        assert.deepEqual(groups, ["rot"], "only the turning-arrows group, no download arrow");
      }
    }
  }));

test("card: a game whose live run is Updating keeps the pause action", () =>
  withCard(({ buildCard }) => {
    const card = buildCard(refresh(), ctx(job("running")));
    const pill = card.querySelector("button.cappill");
    assert.ok(pill, "pill is a button");
    assert.equal(pill.title, "Pause download");
  }));

// ---------------------------------------------------------------------
// CSS: colours exist for the two words; the glyph turns
// ---------------------------------------------------------------------

test("css: tx-updating / tx-verify colours and the vault-turn animation are present", () => {
  const app = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
  const theme = readFileSync(path.join(webDir, "css", "theme.css"), "utf8");
  assert.match(app, /\.tx-updating\{ color:var\(--run\); \}/);
  assert.match(app, /\.tx-verify\{ color:var\(--accent\); \}/);
  assert.match(theme, /\.sic\.k-updating \.rot,\s*\n\.sic\.k-verify \.rot\{[^}]*animation:vault-turn/);
});

// ---------------------------------------------------------------------
// components/game-detail-sheet.js (wiring, source scan)
// ---------------------------------------------------------------------

function functionBody(src, signature) {
  const idx = src.indexOf(signature);
  if (idx === -1) return null;
  const start = src.indexOf("{", idx);
  let depth = 1;
  let j = start + 1;
  while (depth > 0 && j < src.length) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") depth--;
    j++;
  }
  return src.slice(start + 1, j - 1);
}

test("sheet wiring: the header and the structural key both take dispKind(gameLike, liveJob)", () => {
  const src = readFileSync(path.join(webDir, "js", "components", "game-detail-sheet.js"), "utf8");
  const header = functionBody(src, "function buildHeader(gameLike, liveJob) {");
  assert.ok(header, "buildHeader() not found");
  assert.match(header, /const kind = dispKind\(gameLike, liveJob\);/);
  assert.match(header, /createStatusIcon\(statusIconKind\(kind\)/);
  assert.match(header, /word\.className = "tx-" \+ kind;/);
  const key = functionBody(src, "function computeStructuralKey() {");
  assert.ok(key, "computeStructuralKey() not found");
  assert.match(key, /dispKind: dispKind\(gameLike, liveJob\)/, "a Downloading->Verifying flip rebuilds the header");
});
