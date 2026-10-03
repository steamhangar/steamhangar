/**
 * Rail head/foot wiring (WP 4e.6 — the left rail earning its narrowed
 * width: vault name at the top, cache used/free space (plus an optional
 * server version, coordinator addition mid-WP) at the bottom, instead of
 * carrying only the three nav items WP 4e.1 shipped it with).
 *
 * **Testable via dependency injection, NOT the "components/*.js are DOM-
 * building glue, not unit-tested directly" convention this file used to
 * cite (Opus review should-fix S3, WP 4e.6 review round).** That convention
 * is a default, not a hard rule — `store.js`'s own `createPollingStore`
 * already takes its `apiClient`/`intervals` as constructor arguments for
 * exactly this reason, and `web/tests/dialog-wiring.test.js` already drives
 * `components/sheet-dialog.js`/`components/status-icon.js` headlessly via
 * `fake-dom.js`. Before this fix, two real wiring bugs in this exact module
 * had NO test able to see them: deleting `if (payload.error) return;`
 * passed the full suite (a failed cache poll would blank the rail back to
 * "unknown" instead of leaving the last real number on screen), and
 * deleting `if (foot.freeText !== null)` also passed the full suite AND
 * rendered a literal `"Free null"` — a fabricated value, the precise
 * failure this package exists to prevent. `createRailPanel()` below takes
 * every real dependency (the DOM elements, the store, the API client, the
 * two gating predicates) as one options object, so
 * `web/tests/rail-panel-wiring.test.js` can drive it with `fake-dom.js`
 * elements and trivial fake store/api stand-ins — no real `document`, no
 * real network, no jsdom.
 *
 * **This file has ZERO import-time side effects on purpose — the SAME
 * split `store.js`/`store-singleton.js` already use, applied here for the
 * same reason.** `store.js` exports `createPollingStore`, a plain factory
 * with no side effects of its own; `store-singleton.js` is the SEPARATE,
 * one-line file that actually calls it and starts polling. This module
 * plays `store.js`'s role: no `store-singleton.js` import, no `document`
 * access, nothing runs merely by importing it. `app.js` plays
 * `store-singleton.js`'s role — it performs the one real, side-effecting
 * call (real DOM elements, the real store singleton, the real API client)
 * — rather than this file self-wiring on import the way
 * `bypass-banner.js`/`notifications.js` do. That self-wiring pattern is
 * right for a component with no dependencies worth injecting; it is wrong
 * here, because it would force every importer — including a test file that
 * only wants `createRailPanel` — to also evaluate `store-singleton.js`
 * (which starts real, indefinitely-retrying poll loops against `fetch`)
 * and query real DOM ids that do not exist outside a browser.
 *
 * Two independent data sources, two independent lifecycles:
 *
 *  - Cache summary (used/free): `store.subscribe("cache", ...)` — added to
 *    store.js BY THIS WP (see that module's header for why an earlier
 *    brief draft's "already polled" premise was not true as of `git log`).
 *    Paints immediately from whatever snapshot already exists
 *    (`store.snapshot("cache")`, `undefined` before the first successful
 *    poll — same pattern bypass-banner.js already uses for "clients"), then
 *    on every later tick, EXCEPT a failed one (`{error}`), which leaves the
 *    last successful render as-is rather than blanking it back to "unknown"
 *    — same convention bypass-banner.js's own "clients" subscription
 *    already uses. This is a genuine, ongoing poll: disk usage changes
 *    while the app sits open (downloads, deletes, GC), so this is NOT a
 *    fetch-on-mount-only value.
 *
 *  - Vault name + version: a ONE-TIME `GET /v1/settings` (`api.getSettings`,
 *    the SAME method views/settings.js already uses — no new endpoint, no
 *    new client method), gated exactly like onboarding.js's reconnect-path
 *    `getSteamKey()` call: skipped entirely when there is no stored API key
 *    and demo mode is off, since a genuine first run has no valid key yet
 *    and the call would just 401 for nothing this module could show anyway
 *    (see onboarding.js's own comment on this exact gate for the "pure
 *    console noise" reasoning). Deliberately NOT added to store.js's poll
 *    loops — vault_name/version change at human speed (a rename, a vault-
 *    api upgrade), not poll speed, matching settings.js's own documented
 *    "fetch-on-mount... is enough" posture for the exact same data. If the
 *    user later renames the vault from Settings or through onboarding, the
 *    rail head does NOT pick that up until the next reload (onboarding's
 *    own "Ready" step already reloads the page; a plain Settings save does
 *    not) — a known, accepted gap, not silently hidden: re-fetching on every
 *    settings save would mean either polling this endpoint too or teaching
 *    settings.js/onboarding.js to publish into a shared cache, both bigger
 *    than this package's brief ("keep your diff tight" — WP 4e.3 follows in
 *    the same files).
 *
 * **Empty containers are hidden too, not just their text (Opus review
 * should-fix S4).** "Render nothing, never a placeholder" used to apply
 * only to the TEXT inside `#rail-vault-name`/`#rail-cache`/`#rail-version` —
 * their wrapping `.rail-head`/`.rail-foot` elements stayed visible
 * regardless, so a default install (no `vault_name` set, and every build
 * before WP 4e.7 merges `server_version`) showed an empty box with a
 * `border-bottom` divider above the nav and dead space (the version line's
 * own `margin-top`) below it — the opposite of "the width earns itself".
 * `headEl.hidden`/`footEl.hidden`/`versionEl.hidden` are now toggled
 * alongside the text, via the plain `hidden` attribute (the CSS guard this
 * needs — `.rail-head[hidden]`/`.rail-foot[hidden]` — lives in app.css's
 * BP-L block, next to the `display:block` override each one would
 * otherwise win the cascade against; `#rail-version` carries no author
 * `display` rule of its own, so it needs no matching guard — see
 * css-hygiene.test.js's own scope note for why only elements with an author
 * `display` rule need one). `.rail-foot` is hidden only when BOTH the cache
 * summary AND the version line have nothing to show — either alone is
 * enough to keep the foot visible, so a build with cache data but no
 * `server_version` yet (every build today) still shows the used/free
 * lines, just without a stray empty paragraph's margin underneath them.
 */

import { vaultNameFromSettings, cacheFootFromSummary, versionFromSettings } from "../lib/rail-content.js";

/**
 * Builds the rail head/foot's render+wiring logic against injected
 * dependencies. `app.js` calls this once with the real DOM/store/api
 * (see that module); `rail-panel-wiring.test.js` calls it with fakes.
 *
 * @param {{
 *   elements: {
 *     headEl: object, vaultNameEl: object,
 *     footEl: object, cacheEl: object, versionEl: object,
 *     createElement: (tag: string) => object,
 *   },
 *   store: { subscribe: Function, snapshot: Function },
 *   apiClient: { getSettings: () => Promise<object> },
 *   getStoredApiKey: () => string,
 *   isDemoMode: () => boolean,
 *   onVersionActivate?: () => void,
 * }} deps
 *   `onVersionActivate` (WP WEB-FEAT-3, optional): called when the version
 *   line — a `<button>` in index.html — is clicked; app.js passes "open
 *   Settings → About". The line's TEXT is unchanged (only the server's own
 *   version, "dev build" for `dev`); its accessible name becomes
 *   "About: <version>" so a screen reader hears where it leads.
 * @returns {{ renderCacheFoot: Function, renderSettingsSourced: Function }}
 *   exposed ONLY for `rail-panel-wiring.test.js` to drive directly without
 *   re-triggering the one-time fetch/subscription side effects a second
 *   time — production code never calls these.
 */
export function createRailPanel({ elements, store, apiClient, getStoredApiKey, isDemoMode, onVersionActivate }) {
  const { headEl, vaultNameEl, footEl, cacheEl, versionEl, createElement } = elements;
  if (typeof onVersionActivate === "function") {
    versionEl.addEventListener("click", () => onVersionActivate());
  }

  // Foot visibility depends on TWO independently-updating sources (the
  // cache-summary poll and the one-time settings fetch) — tracked here so
  // either one's render function can decide "is there ANYTHING to show in
  // the foot right now" without the other's callback needing to run first.
  let lastCacheFoot = null; // cacheFootFromSummary()'s last result: {usedText, freeText} | null
  let lastVersionText = null; // versionFromSettings()'s last result: string | null

  function updateFootVisibility() {
    footEl.hidden = lastCacheFoot === null && lastVersionText === null;
  }

  function cacheRow(label, value) {
    const row = createElement("p");
    row.className = "rail-cache-row";
    const k = createElement("span");
    k.className = "k";
    k.textContent = label;
    const v = createElement("span");
    v.className = "v";
    v.textContent = value;
    row.append(k, v);
    return row;
  }

  /** `summary`: the raw `GET /v1/cache/summary` body, or `null`/`undefined`
   * for "not known yet" (no poll landed, or the last one failed). Never
   * touches `versionEl`/`lastVersionText` — that field lives on the
   * settings response, not the cache summary. */
  function renderCacheFoot(summary) {
    const foot = cacheFootFromSummary(summary);
    lastCacheFoot = foot;
    cacheEl.replaceChildren();
    if (foot) {
      cacheEl.appendChild(cacheRow("Used", foot.usedText));
      // MUTATION TARGET (S3): deleting this guard renders foot.freeText
      // (`null` when free_disk_bytes is undeterminable) as the literal
      // string "null" via a fabricated "Free" row — exactly the failure
      // this package exists to prevent, not merely an unlikely edge case.
      if (foot.freeText !== null) cacheEl.appendChild(cacheRow("Free", foot.freeText));
    }
    updateFootVisibility();
  }

  /** `settingsResponse`: the raw `GET /v1/settings` body, or `null`/
   * `undefined` for "not fetched yet / fetch failed". Populates BOTH the
   * vault-name head and the optional version foot line from the one
   * response — see lib/rail-content.js for why they are two separate pure
   * functions (one setting-store entry vs. a top-level `server_version`
   * field, WP 4e.7) reading the same object. */
  function renderSettingsSourced(settingsResponse) {
    const vaultName = vaultNameFromSettings(settingsResponse);
    headEl.hidden = vaultName === null;
    vaultNameEl.textContent = vaultName || "";

    const version = versionFromSettings(settingsResponse);
    lastVersionText = version;
    versionEl.hidden = version === null;
    versionEl.textContent = version || "";
    if (version === null) versionEl.removeAttribute("aria-label");
    else versionEl.setAttribute("aria-label", `About: ${version}`);
    updateFootVisibility();
  }

  // Cache summary: subscribe first, then paint from whatever snapshot the
  // store already has (it may have ticked before this module was imported
  // — same ordering bypass-banner.js/notifications.js already rely on for
  // "clients"). A `{error}` tick is deliberately IGNORED here, not treated
  // as "clear to unknown" — same convention bypass-banner.js's own
  // "clients" subscription already uses. The GENUINE unknown states
  // (before the first poll ever lands, or a poll that resolves with a
  // malformed body) are already covered by `cacheFootFromSummary` returning
  // `null` for those inputs — this guard only skips the ADDITIONAL case of
  // "a poll that flat-out failed", which is not one of those.
  store.subscribe("cache", (payload) => {
    // MUTATION TARGET (S3): deleting this guard calls
    // renderCacheFoot(payload.item) on an ERROR payload too — `item` is
    // undefined there, so cacheFootFromSummary(undefined) returns null and
    // the foot is blanked back to "unknown" on every transient failure,
    // instead of leaving the last real number on screen.
    if (payload.error) return;
    renderCacheFoot(payload.item);
  });
  renderCacheFoot(store.snapshot("cache"));

  // Vault name/version: paint the honest "not known yet" state immediately
  // (empty text AND a hidden container, not a placeholder), then attempt
  // the one-time fetch — see the module header for the exact gating
  // reasoning.
  renderSettingsSourced(null);
  if (getStoredApiKey() || isDemoMode()) {
    apiClient.getSettings().then(renderSettingsSourced, () => {}); // offline/401/unexpected — stays "not known", never retried automatically
  }

  return { renderCacheFoot, renderSettingsSourced };
}
