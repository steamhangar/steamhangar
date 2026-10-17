/**
 * WP ID-AUDIT: no surface titles an app with a bare Steam id. The one neutral
 * fallback is `UNKNOWN_APP_NAME`; the technical id stays only on detail lines
 * built for it (job card "appid" line, package facts).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CONTENT_PACKAGE_NAME, UNKNOWN_APP_NAME } from "../js/lib/app-names.js";
import { appLabel, recentAppsText, loadingNowText } from "../js/lib/clients-page.js";
import { gameDisplayName } from "../js/lib/game-status.js";
import { appTitle } from "../js/lib/owned-library.js";
import { buildDepotPresentation } from "../js/lib/depot-presentation.js";
import { packageFullName } from "../js/components/package-list.js";

test("the fallback is a neutral phrase without a digit", () => {
  assert.equal(UNKNOWN_APP_NAME, "Unknown app");
  assert.doesNotMatch(UNKNOWN_APP_NAME, /\d/);
});

test("every title helper uses the fallback, with or without an app id", () => {
  assert.equal(appLabel({ appid: 3419430, name: null }), UNKNOWN_APP_NAME);
  assert.equal(appLabel({ appid: 3419430, name: "  " }), UNKNOWN_APP_NAME);
  assert.equal(gameDisplayName({ appid: 1465470, name: null }), UNKNOWN_APP_NAME);
  assert.equal(gameDisplayName(undefined), UNKNOWN_APP_NAME);
  assert.equal(appTitle(1962700, null, null), UNKNOWN_APP_NAME);
  assert.equal(packageFullName({ appid: 7 }), CONTENT_PACKAGE_NAME);
  const p = buildDepotPresentation({ depotid: 1, sizeBytes: 1, shared: true, others: [42], holderAppids: [] }, new Map(), false);
  assert.equal(p.coOwners[0].name, UNKNOWN_APP_NAME);
});

test("a name from the server still wins over the fallback", () => {
  assert.equal(appLabel({ appid: 1, name: "Real Game" }), "Real Game");
  assert.equal(appTitle(1, null, "Owned Name"), "Owned Name");
});

test("Clients page lines never print a bare id for an unnamed app", () => {
  const recent = recentAppsText({ recent_apps: [{ appid: 1465470, name: null, bytes_served: 0 }, { appid: 3419430, name: null, bytes_served: 0 }] });
  assert.match(recent, /^Loaded recently: Unknown app/);
  assert.doesNotMatch(recent, /1465470|3419430/);
  const loading = loadingNowText({ loading_now: { loading: true, bytes_per_second: 0, apps: [{ appid: 3419430, name: null }] } });
  assert.doesNotMatch(loading, /3419430/);
});

test("call sites: no `App ${id}` title fallback is left in the web sources", () => {
  const files = [
    "../js/views/library.js",
    "../js/components/game-detail-sheet.js",
    "../js/components/package-list.js",
    "../js/lib/game-status.js",
    "../js/lib/owned-library.js",
    "../js/lib/clients-page.js",
    "../js/lib/depot-presentation.js",
  ];
  for (const f of files) {
    const src = readFileSync(new URL(f, import.meta.url), "utf8");
    for (const line of src.split("\n")) {
      if (/^\s*(\*|\/\/)/.test(line)) continue;
      // The two deliberate technical detail lines (sheet id line, package facts).
      if (/appidLine\.textContent|const parts = \[/.test(line)) continue;
      assert.doesNotMatch(line, /`App \$\{/, `${f}: ${line.trim()}`);
    }
  }
});
