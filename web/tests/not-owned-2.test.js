/**
 * WP NOT-OWNED-2: a content package that ended `not_owned` but has cache
 * bytes stays Cached with a small neutral note; the not_owned word stays for
 * apps without bytes; verify drops known-not-owned packages.
 *
 * Run: node --test "web/tests/*.test.js"
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { KIND, dispKind, statusAction, isNotOwnedApp, showsNotOwnedNote, NOT_OWNED_LIBRARY_NOTE } =
  await import("../js/lib/game-status.js");
const { verifyAppids } = await import("../js/lib/cache-completeness.js");
const { offersRetryFor } = await import("../js/lib/job-failure.js");
const { buildPackagesSection, packagesStructuralKey, offersPackageRetry } = await import(
  "../js/components/package-list.js"
);

const GIB = 1_073_741_824;
const row = (over = {}) => ({ appid: 7, name: "Pack", status: "done", size_bytes: 0.3 * GIB, ...over });

test("the note text is the agreed neutral wording", () => {
  assert.equal(NOT_OWNED_LIBRARY_NOTE, "Not part of your library");
});

test("a not_owned app WITH bytes stays Cached, with the note, no action, no Retry", () => {
  for (const g of [row({ not_owned: true }), row({ status: "not_owned" })]) {
    assert.equal(isNotOwnedApp(g), true);
    assert.equal(dispKind(g, undefined), KIND.CACHED);
    assert.equal(showsNotOwnedNote(g), true);
    assert.equal(statusAction(g, undefined, false), null);
    assert.equal(offersRetryFor(g), false);
    assert.equal(offersPackageRetry(g, undefined, undefined), false);
  }
});

test("a not_owned app WITHOUT bytes keeps the not_owned word kind", () => {
  const g = row({ status: "not_owned", size_bytes: null });
  assert.equal(dispKind(g, undefined), KIND.NOT_OWNED);
  assert.equal(showsNotOwnedNote(g), false);
  assert.equal(dispKind(row({ not_owned: true, status: "idle", size_bytes: null }), undefined), KIND.NOT_OWNED);
});

test("an ordinary cached app is unaffected, a tool app is never flagged", () => {
  assert.equal(isNotOwnedApp(row()), false);
  assert.equal(dispKind(row(), undefined), KIND.CACHED);
  assert.equal(isNotOwnedApp(row({ tool_app: true, not_owned: true })), false);
});

test("verifyAppids drops a known-not-owned package", () => {
  const members = [row({ appid: 1 }), row({ appid: 2, not_owned: true }), row({ appid: 3, status: "not_owned" })];
  assert.deepEqual(verifyAppids(members), [1]);
});

test("the package row shows Cached plus the note, and the key marks it", () => {
  const group = { appid: 1, name: "Game", packages: [row({ appid: 11, not_owned: true }), row({ appid: 12 })] };
  const el = buildPackagesSection(group, {
    groupSizeBytes: null,
    liveJobFor: () => undefined,
    trackedJobFor: () => undefined,
    onRetry: () => {},
  });
  const r11 = el.querySelector('.pkg-row[data-appid="11"]');
  assert.equal(r11.querySelector(".pkgnote").textContent, "Not part of your library");
  assert.equal(r11.querySelector(".pkgstate").className, "pkgstate tx-cached");
  assert.equal(r11.querySelectorAll("button").length, 0);
  assert.equal(el.querySelector('.pkg-row[data-appid="12"] .pkgnote'), null);
  assert.equal(
    packagesStructuralKey(group.packages, { liveJobFor: () => undefined, trackedJobFor: () => undefined }),
    "11:cached:0:n,12:cached:0",
  );
});
