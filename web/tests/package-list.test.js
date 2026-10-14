/**
 * Fake-DOM tests for the detail sheet's "Content packages" section (WP
 * TOOL-APPS-2c, `components/package-list.js`) plus source pins that
 * `game-detail-sheet.js` really builds, patches and keys it (the sheet
 * itself starts store loops at import time, see
 * game-detail-sheet-installed.test.js's header).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;
const {
  buildPackagesSection,
  patchPackagesSection,
  packagesStructuralKey,
  offersPackageRetry,
  packageFactsText,
  packagesTotalText,
} = await import("../js/components/package-list.js");

const GIB = 1_073_741_824;
const pkg = (appid, over = {}) => ({
  appid,
  name: `HELLDIVERS™ 2 - Pack ${appid}`,
  status: "done",
  last_prefill_at: "2026-10-01T00:00:00Z",
  size_bytes: 0.3 * GIB,
  installed_on: [],
  app_type: "dlc",
  parent_appid: 1,
  ...over,
});
const group = {
  appid: 1,
  name: "HELLDIVERS™ 2",
  packages: [
    pkg(11, { installed_on: [{ client_id: "pc-a", reported_at: "x" }] }),
    pkg(12, { status: "error", size_bytes: null, app_type: "music" }),
    pkg(13, { name: null, app_type: null, size_bytes: null, status: "idle", last_prefill_at: null }),
  ],
};
const none = () => undefined;

function build(over = {}) {
  const retried = [];
  const el = buildPackagesSection(group, {
    groupSizeBytes: 2.5 * GIB,
    liveJobFor: none,
    trackedJobFor: none,
    onRetry: (p) => retried.push(p.appid),
    ...over,
  });
  return { el, retried };
}

test("lists every package: short visible name, full name for title and screen readers, facts, status", () => {
  const { el } = build();
  assert.equal(el.querySelector("h4.sec").textContent, "Content packages");
  assert.equal(el.querySelector('[data-role="pkgtotal"]').textContent, "Total with content packages: 2.5 GB");
  const rows = el.querySelectorAll(".pkg-row");
  assert.deepEqual(rows.map((r) => r.dataset.appid), ["11", "12", "13"]);
  const first = rows[0];
  assert.equal(first.querySelector(".pkgname").textContent, "Pack 11");
  assert.equal(first.querySelector(".pkgname").getAttribute("aria-hidden"), "true");
  assert.equal(first.querySelector(".sr-only").textContent, "HELLDIVERS™ 2 - Pack 11");
  assert.equal(first.title, "HELLDIVERS™ 2 - Pack 11");
  assert.equal(first.querySelector(".pkgfacts").textContent, "App 11 · DLC · 0.3 GB · Installed on pc-a");
  assert.equal(first.querySelector(".pkgstate").textContent, "Current");
  assert.equal(rows[2].querySelector(".pkgname").textContent, "App 13");
  assert.equal(rows[2].querySelector(".pkgfacts").textContent, "App 13");
});

test("MUTATION TARGET -- only the failed package offers 'Retry download', and it retries that package", () => {
  const { el, retried } = build();
  const buttons = el.querySelectorAll(".pkg-row button");
  assert.equal(buttons.length, 1);
  const btn = el.querySelector('.pkg-row[data-appid="12"] button');
  assert.equal(btn.textContent, "Retry download");
  assert.equal(btn.getAttribute("aria-label"), "Retry download — HELLDIVERS™ 2 - Pack 12");
  assert.equal(el.querySelector('.pkg-row[data-appid="12"] .pkgstate').className, "pkgstate tx-error");
  btn.dispatchEvent({ type: "click" });
  assert.deepEqual(retried, [12]);
});

test("no Retry while the failed package already has a job (queued, running or paused)", () => {
  const queued = { id: 1, appid: 12, type: "prefill", status: "queued" };
  const { el } = build({ trackedJobFor: (id) => (id === 12 ? queued : undefined) });
  assert.equal(el.querySelectorAll(".pkg-row button").length, 0);
  assert.equal(offersPackageRetry(group.packages[1], undefined, undefined), true);
  assert.equal(offersPackageRetry(group.packages[0], undefined, undefined), false);
});

test("structural key follows status and the Retry offer; patch keeps sizes and the total current", () => {
  const lookups = { liveJobFor: none, trackedJobFor: none };
  assert.equal(packagesStructuralKey(group.packages, lookups), "11:cached:0,12:error:1,13:none:0");
  const { el } = build({ groupSizeBytes: null });
  assert.equal(el.querySelector('[data-role="pkgtotal"]').hidden, true, "no total line while unknown");
  const grown = group.packages.map((p) => (p.appid === 11 ? { ...p, size_bytes: 0.5 * GIB } : p));
  patchPackagesSection(el, grown, 3 * GIB);
  assert.equal(el.querySelector('[data-role="pkgtotal"]').hidden, false);
  assert.equal(el.querySelector('[data-role="pkgtotal"]').textContent, "Total with content packages: 3.0 GB");
  assert.match(el.querySelector('.pkg-row[data-appid="11"] .pkgfacts').textContent, /0\.5 GB/);
  assert.equal(packagesTotalText(null), null);
  assert.equal(packageFactsText(pkg(5, { app_type: "video", size_bytes: null })), "App 5 · Video");
});

test("wiring: the detail sheet builds, patches and keys the packages section", () => {
  const src = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");
  const fn = (head) => {
    const i = src.indexOf(head);
    assert.ok(i >= 0, `${head} not found`);
    return src.slice(i, src.indexOf("\n}\n", i));
  };
  assert.match(fn("function render() {"), /buildPackagesSection\(group, \{[\s\S]*onRetry: retryPackage/);
  assert.match(fn("function patchVolatile() {"), /patchPackagesSection\(contentEl, group\.packages, group\.group_size_bytes\)/);
  assert.match(fn("function computeStructuralKey() {"), /packages: group \? packagesStructuralKey\(group\.packages, packageJobLookups\(\)\) : null/);
  assert.match(fn("async function retryPackage(pkg, btn) {"), /api\.prefill\(\[pkg\.appid\]\)/);
});

test("detail structural key: unchanged for a game without packages, carries the package part otherwise", async () => {
  const { buildDetailStructuralKey } = await import("../js/lib/detail-render-plan.js");
  const base = { dispKind: "cached", trackedJobStatus: null, depotTags: [], installedBadge: "none" };
  assert.equal(buildDetailStructuralKey({ ...base, packages: null }), buildDetailStructuralKey(base));
  assert.notEqual(
    buildDetailStructuralKey({ ...base, packages: "11:cached:0" }),
    buildDetailStructuralKey({ ...base, packages: "11:error:1" }),
  );
});
