/**
 * Owned Steam games in the library (WP WEB-FEAT-1) — the pure half
 * (`web/js/lib/owned-library.js`) plus the two decision functions it bends
 * (`statusAction` in game-status.js, `classifyBulkSelection`/
 * `buildBulkDownloadPlan` in bulk-plan.js).
 *
 * Guarantees pinned here, each by name:
 *   G1 merge: union by appid, owned duplicates collapse, vault rows win for
 *      every cache-state field, owned-only rows are flagged `owned_only`.
 *   G2 header: "N owned · M on the cache" ONLY when the owned list loaded;
 *      otherwise "V games on the vault · M on the cache" — never "owned".
 *   G3 errors: 409 -> the existing no-key string, 422 -> the existing
 *      invalid-SteamID string, 0 games -> private-profile hint; a failure
 *      never empties the vault list.
 *   G4 detail-only download: no card quick action, never a bulk target.
 *   G5 the stored SteamID is read only as a JSON string.
 *   G6 the loader has no timer: it fetches exactly once per load() call.
 *
 * Fixtures are synthetic (LEARNINGS "Testing discipline").
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mergeOwnedLibrary,
  ownedOnlyRow,
  isOwnedOnly,
  countOwned,
  librarySubtitle,
  describeOwnedLoadError,
  ownedNotice,
  steamIdFromSettings,
  createOwnedLibraryLoader,
  OWNED_STATUS,
  NOTICE_ACTION,
  NO_STEAM_KEY_MESSAGE,
  INVALID_STEAMID64_MESSAGE,
  PRIVATE_PROFILE_MESSAGE,
} from "../js/lib/owned-library.js";
import { statusAction, dispKind, KIND } from "../js/lib/game-status.js";
import { classifyBulkSelection, buildBulkDownloadPlan, classifyBulkDeleteEligibility } from "../js/lib/bulk-plan.js";
import { chipCounts, visibleGames } from "../js/lib/library-filters.js";
import { ApiError, ERROR_KINDS } from "../js/errors.js";

const STEAMID = "76561198042117903";

function vaultGame(appid, overrides = {}) {
  return {
    appid,
    name: `Vault ${appid}`,
    status: "done",
    last_prefill_at: "2026-09-01T00:00:00Z",
    last_manifest_check: null,
    depot_count: 1,
    size_bytes: 1_000_000_000,
    needs_force: false,
    installed_on: [],
    ...overrides,
  };
}

const VAULT = [
  vaultGame(10), // cached, also owned
  vaultGame(20, { status: "idle", size_bytes: null, last_prefill_at: null }), // known, not cached, also owned
  vaultGame(30), // cached, NOT in the owned list (e.g. another account's purchase)
];
const OWNED = [
  { appid: 10, name: "Owned Ten (Steam title)" },
  { appid: 20, name: "Owned Twenty" },
  { appid: 40, name: "Owned Forty" },
  { appid: 40, name: "Owned Forty (duplicate row)" },
  { appid: 50, name: "Owned Fifty" },
];

// ---------------------------------------------------------------------
// G1 merge
// ---------------------------------------------------------------------

test("G1: merge is the union by appid — vault rows first, each owned-only appid exactly once", () => {
  const merged = mergeOwnedLibrary(VAULT, OWNED);
  assert.deepEqual(
    merged.map((g) => g.appid),
    [10, 20, 30, 40, 50],
  );
});

test("G1 MUTATION PIN: vault data wins for cache state — an owned duplicate of a vault game changes nothing but a missing name", () => {
  const merged = mergeOwnedLibrary(VAULT, OWNED);
  const ten = merged.find((g) => g.appid === 10);
  assert.equal(ten.status, "done");
  assert.equal(ten.size_bytes, 1_000_000_000);
  assert.equal(ten.name, "Vault 10", "a vault name is kept; the Steam title only fills a MISSING one");
  assert.equal(isOwnedOnly(ten), false, "a vault game is never flagged owned_only");
  assert.equal(dispKind(ten, undefined), KIND.CACHED);
  // A vault row whose cache state is "not cached" stays the vault's row too.
  const twenty = merged.find((g) => g.appid === 20);
  assert.equal(twenty.status, "idle");
  assert.equal(isOwnedOnly(twenty), false);
});

test("G1: a vault row with no name takes the owned title (and nothing else)", () => {
  const nameless = [vaultGame(10, { name: null })];
  const [row] = mergeOwnedLibrary(nameless, [{ appid: 10, name: "From Steam" }]);
  assert.equal(row.name, "From Steam");
  assert.equal(row.size_bytes, 1_000_000_000);
  assert.equal(isOwnedOnly(row), false);
});

test("G1: owned-only rows are flagged and shaped so dispKind reads them as Not cached", () => {
  const merged = mergeOwnedLibrary(VAULT, OWNED);
  const forty = merged.find((g) => g.appid === 40);
  assert.equal(isOwnedOnly(forty), true);
  assert.equal(forty.name, "Owned Forty", "the FIRST owned row for a duplicated appid wins");
  assert.equal(forty.size_bytes, null);
  assert.equal(forty.status, "idle");
  assert.equal(forty.needs_force, false);
  assert.deepEqual(forty.installed_on, []);
  assert.equal(dispKind(forty, undefined), KIND.NONE);
});

test("G1/G3: no owned list (null, [], or garbage) returns the vault list unchanged", () => {
  assert.equal(mergeOwnedLibrary(VAULT, null), VAULT);
  assert.equal(mergeOwnedLibrary(VAULT, []), VAULT);
  assert.equal(mergeOwnedLibrary(VAULT, [{ appid: "x" }, null, { appid: -1 }]), VAULT);
  assert.deepEqual(mergeOwnedLibrary(null, OWNED.slice(0, 1)).map((g) => g.appid), [10]);
});

test("G1: the 'Not cached' chip counts owned-only rows, 'All' shows everything", () => {
  const merged = mergeOwnedLibrary(VAULT, OWNED);
  const counts = Object.fromEntries(chipCounts(merged, { query: "", liveJobsByAppid: new Map() }).map((c) => [c.key, c.count]));
  assert.equal(counts.all, 5);
  assert.equal(counts.cached, 2);
  assert.equal(counts.none, 3, "20 (vault, not cached) + 40 + 50 (owned-only)");
  assert.equal(visibleGames(merged, { query: "", filterKey: "all", liveJobsByAppid: new Map() }).length, 5);
});

test("countOwned counts DISTINCT valid appids", () => {
  assert.equal(countOwned(OWNED), 4);
  assert.equal(countOwned([]), 0);
  assert.equal(countOwned(null), 0);
});

// ---------------------------------------------------------------------
// G2 header
// ---------------------------------------------------------------------

test("G2 MUTATION PIN: with the owned list loaded the header reads 'N owned · M on the cache' — N distinct owned, M every cached card", () => {
  const merged = mergeOwnedLibrary(VAULT, OWNED);
  const text = librarySubtitle({
    games: merged,
    liveJobsByAppid: new Map(),
    owned: { status: OWNED_STATUS.READY, games: OWNED },
  });
  // N = 4 distinct owned appids (NOT merged.length 5, NOT the vault's 3);
  // M = 2 = the Cached chip (10 and 30 — 30 is cached but not owned).
  assert.equal(text, "4 owned · 2 on the cache");
});

test("G2 MUTATION PIN: without a SteamID the header never says 'owned' — it counts the vault's own games", () => {
  for (const status of [OWNED_STATUS.UNSET, OWNED_STATUS.LOADING, OWNED_STATUS.ERROR]) {
    const text = librarySubtitle({ games: VAULT, liveJobsByAppid: new Map(), owned: { status, games: [] } });
    assert.equal(text, "3 games on the vault · 2 on the cache", `status ${status}`);
    assert.doesNotMatch(text, /owned/);
  }
  assert.equal(
    librarySubtitle({ games: [VAULT[0]], owned: { status: OWNED_STATUS.UNSET, games: [] } }),
    "1 game on the vault · 1 on the cache",
  );
});

test("G2 MUTATION PIN: READY with 0 owned games (private profile) uses the vault wording, never '0 owned'", () => {
  const text = librarySubtitle({ games: VAULT, liveJobsByAppid: new Map(), owned: { status: OWNED_STATUS.READY, games: [] } });
  assert.equal(text, "3 games on the vault · 2 on the cache");
});

test("G2: the vault count excludes owned-only rows even if a merged list is passed", () => {
  const merged = mergeOwnedLibrary(VAULT, OWNED);
  const text = librarySubtitle({ games: merged, owned: { status: OWNED_STATUS.ERROR, games: [] } });
  assert.equal(text, "3 games on the vault · 2 on the cache");
});

test("G2: a live download stops counting as 'on the cache' (same dispKind rule as the grid)", () => {
  const live = new Map([[10, { appid: 10, status: "running", type: "prefill" }]]);
  const text = librarySubtitle({ games: VAULT, liveJobsByAppid: live, owned: { status: OWNED_STATUS.UNSET, games: [] } });
  assert.equal(text, "3 games on the vault · 1 on the cache");
});

// ---------------------------------------------------------------------
// G3 errors and notices
// ---------------------------------------------------------------------

test("G3: the reused strings are the Settings view's exact wording", () => {
  assert.equal(NO_STEAM_KEY_MESSAGE, "No Steam Web API key configured. Library queries answer 409 until one is set.");
  assert.equal(INVALID_STEAMID64_MESSAGE, "That does not look like a valid SteamID64 (17 digits).");
});

test("G3 MUTATION PIN: 409 -> no Steam key, 422 -> invalid SteamID, anything else names the failure", () => {
  const e409 = new ApiError(ERROR_KINDS.VALIDATION, "GET /v1/steam/owned-games failed (409)", { status: 409, detail: "not configured" });
  const e422 = new ApiError(ERROR_KINDS.VALIDATION, "x", { status: 422, detail: "bad id" });
  const e502 = new ApiError(ERROR_KINDS.SERVER, "x", { status: 502, detail: "Steam upstream error" });
  const eNet = new ApiError(ERROR_KINDS.NETWORK, "Network request failed: GET /v1/steam/owned-games");
  assert.equal(describeOwnedLoadError(e409), NO_STEAM_KEY_MESSAGE);
  assert.equal(describeOwnedLoadError(e422), `The stored library SteamID64 was rejected. ${INVALID_STEAMID64_MESSAGE}`);
  assert.equal(describeOwnedLoadError(e502), "Could not load your Steam library: Steam upstream error");
  assert.equal(describeOwnedLoadError(eNet), "Could not load your Steam library: Network request failed: GET /v1/steam/owned-games");
});

test("G3: notices — unset points at Settings, 409/422 offer Settings + Reload, other errors Reload, 0 games is the private hint", () => {
  const unset = ownedNotice({ status: OWNED_STATUS.UNSET, games: [] });
  assert.match(unset.text, /Set your SteamID64 in Settings/);
  assert.deepEqual(unset.actions, [NOTICE_ACTION.SETTINGS]);

  const noKey = ownedNotice({ status: OWNED_STATUS.ERROR, games: [], error: NO_STEAM_KEY_MESSAGE, errorStatus: 409 });
  assert.equal(noKey.text, `${NO_STEAM_KEY_MESSAGE} Showing vault games only.`);
  assert.deepEqual(noKey.actions, [NOTICE_ACTION.SETTINGS, NOTICE_ACTION.RELOAD]);
  assert.equal(noKey.warn, true);

  const upstream = ownedNotice({ status: OWNED_STATUS.ERROR, games: [], error: "Could not load your Steam library: x", errorStatus: 502 });
  assert.deepEqual(upstream.actions, [NOTICE_ACTION.RELOAD]);
  assert.match(upstream.text, /Showing vault games only\.$/);

  const empty = ownedNotice({ status: OWNED_STATUS.READY, games: [] });
  assert.equal(empty.text, PRIVATE_PROFILE_MESSAGE);
  assert.match(empty.text, /probably private/);

  assert.equal(ownedNotice({ status: OWNED_STATUS.READY, games: OWNED }), null, "a loaded, non-empty list needs no notice");
  assert.match(ownedNotice({ status: OWNED_STATUS.LOADING, games: [] }).text, /^Loading/);
});

// ---------------------------------------------------------------------
// G4 detail-only download
// ---------------------------------------------------------------------

test("G4 MUTATION PIN: an owned-only card has NO quick action; a vault 'not cached' card keeps its download action", () => {
  const ownedOnly = ownedOnlyRow({ appid: 40, name: "Owned Forty" });
  assert.equal(statusAction(ownedOnly, undefined, false), null, "deleting the owned_only guard in statusAction brings the card download back");
  const vaultNone = vaultGame(20, { status: "idle", size_bytes: null, last_prefill_at: null });
  assert.equal(statusAction(vaultNone, undefined, false).type, "download", "existing behaviour for vault-known games is unchanged");
});

test("G4 MUTATION PIN: a picked owned-only game is never a bulk download target, and the note says why", () => {
  const merged = mergeOwnedLibrary(VAULT, OWNED);
  const picked = merged.filter((g) => [20, 40, 50].includes(g.appid));
  const cls = classifyBulkSelection(picked, []);
  assert.deepEqual(cls.needsDownload.map((g) => g.appid), [20]);
  assert.deepEqual(cls.notOnVault.map((g) => g.appid), [40, 50]);
  const plan = buildBulkDownloadPlan(cls, picked.length);
  assert.deepEqual(plan.primaryTargets, [20]);
  assert.equal(plan.primaryLabel, "Download 1 of 3");
  assert.equal(plan.note, "2 games not on the vault yet — open each one to download.");
  assert.doesNotMatch(plan.note, /already cached/, "owned-only picks are not 'already cached'");
});

test("G4: only owned-only picks -> nothing to download, primary disabled", () => {
  const picked = [ownedOnlyRow({ appid: 40, name: "x" })];
  const plan = buildBulkDownloadPlan(classifyBulkSelection(picked, []), 1);
  assert.equal(plan.primaryEnabled, false);
  assert.deepEqual(plan.primaryTargets, []);
  assert.equal(plan.primaryLabel, "Nothing to download here");
  assert.equal(plan.note, "1 game not on the vault yet — open it to download.");
});

test("G4: cached + owned-only picks -> no 'Every selected game is current' claim, re-download only the cached one", () => {
  const picked = [vaultGame(10), ownedOnlyRow({ appid: 40, name: "x" })];
  const plan = buildBulkDownloadPlan(classifyBulkSelection(picked, []), 2);
  assert.equal(plan.primaryEnabled, false);
  assert.deepEqual(plan.secondaryTargets, [10]);
  assert.doesNotMatch(plan.note, /Every selected game is current/);
  assert.match(plan.note, /1 game not on the vault yet/);
});

test("G4: an owned-only pick is never bulk-delete eligible (no bytes)", () => {
  assert.deepEqual(classifyBulkDeleteEligibility([ownedOnlyRow({ appid: 40, name: "x" })], []), []);
});

test("G4: without owned-only picks the bulk plan text is exactly as before", () => {
  const picked = [vaultGame(10), vaultGame(20, { status: "idle", size_bytes: null, last_prefill_at: null })];
  const plan = buildBulkDownloadPlan(classifyBulkSelection(picked, []), 2);
  assert.equal(plan.note, "1 already cached — not re-downloaded.");
  // A classification object from before this WP (no notOnVault key) still works.
  const legacy = buildBulkDownloadPlan({ busy: [], needsDownload: [picked[1]], current: [picked[0]] }, 2);
  assert.equal(legacy.note, "1 already cached — not re-downloaded.");
});

// ---------------------------------------------------------------------
// G5 stored SteamID
// ---------------------------------------------------------------------

test("G5 MUTATION PIN: the library SteamID is read from GET /v1/settings only as a string", () => {
  const resp = (effective) => ({ settings: [{ key: "vault_name", effective: "v" }, { key: "steam_library_steamid", effective }] });
  assert.equal(steamIdFromSettings(resp(STEAMID)), STEAMID);
  assert.equal(steamIdFromSettings(resp(` ${STEAMID} `)), STEAMID);
  assert.equal(steamIdFromSettings(resp("")), "");
  assert.equal(steamIdFromSettings(resp(76561198042117900)), "", "a JSON number is already rounded — never used");
  assert.equal(steamIdFromSettings(resp(null)), "");
  assert.equal(steamIdFromSettings({ settings: [{ key: "vault_name", effective: "v" }] }), "", "an older server without the key");
  assert.equal(steamIdFromSettings(null), "");
});

// ---------------------------------------------------------------------
// G6 loader
// ---------------------------------------------------------------------

function fakeApi({ steamid = STEAMID, owned = { configured: true, game_count: 2, games: OWNED.slice(0, 2) }, ownedError = null, settingsError = null } = {}) {
  const calls = { settings: 0, owned: [] };
  return {
    calls,
    apiClient: {
      async getSettings() {
        calls.settings++;
        if (settingsError) throw settingsError;
        return { settings: [{ key: "steam_library_steamid", effective: steamid }] };
      },
      async steamOwnedGames(id) {
        calls.owned.push(id);
        if (ownedError) throw ownedError;
        return owned;
      },
    },
  };
}

test("G6: load() reads the setting, then fetches the owned list for THAT steamid, once", async () => {
  const { apiClient, calls } = fakeApi();
  const seen = [];
  const loader = createOwnedLibraryLoader({ apiClient, onChange: (s) => seen.push(s.status) });
  await loader.load();
  assert.equal(calls.settings, 1);
  assert.deepEqual(calls.owned, [STEAMID]);
  assert.equal(loader.current().status, OWNED_STATUS.READY);
  assert.equal(loader.current().games.length, 2);
  assert.deepEqual(seen, [OWNED_STATUS.LOADING, OWNED_STATUS.READY]);
});

test("G6 MUTATION PIN: the loader has no timer — no fetch happens without another load() call", async () => {
  const { apiClient, calls } = fakeApi();
  const loader = createOwnedLibraryLoader({ apiClient, onChange: () => {} });
  await loader.load();
  await new Promise((r) => setTimeout(r, 50));
  assert.deepEqual(calls.owned, [STEAMID]);
  await loader.load();
  assert.deepEqual(calls.owned, [STEAMID, STEAMID], "a second load() (the manual Reload) fetches again");
});

test("G6: a blank setting never calls the relay", async () => {
  const { apiClient, calls } = fakeApi({ steamid: "" });
  const loader = createOwnedLibraryLoader({ apiClient, onChange: () => {} });
  await loader.load();
  assert.equal(loader.current().status, OWNED_STATUS.UNSET);
  assert.deepEqual(calls.owned, []);
});

test("G6/G3: a 409 from the relay becomes an ERROR state with the no-key text and an empty owned list", async () => {
  const err = new ApiError(ERROR_KINDS.VALIDATION, "x", { status: 409, detail: "not configured" });
  const { apiClient } = fakeApi({ ownedError: err });
  const loader = createOwnedLibraryLoader({ apiClient, onChange: () => {} });
  await loader.load();
  const s = loader.current();
  assert.equal(s.status, OWNED_STATUS.ERROR);
  assert.equal(s.error, NO_STEAM_KEY_MESSAGE);
  assert.equal(s.errorStatus, 409);
  assert.deepEqual(s.games, []);
  assert.equal(mergeOwnedLibrary(VAULT, s.games), VAULT, "the vault list survives the failure");
});

test("G6: a settings failure is an ERROR state too (the relay is never called with a guessed id)", async () => {
  const { apiClient, calls } = fakeApi({ settingsError: new ApiError(ERROR_KINDS.SERVER, "boom", { status: 500, detail: "db locked" }) });
  const loader = createOwnedLibraryLoader({ apiClient, onChange: () => {} });
  await loader.load();
  assert.equal(loader.current().status, OWNED_STATUS.ERROR);
  assert.match(loader.current().error, /db locked/);
  assert.deepEqual(calls.owned, []);
});

test("G6: a superseded load() result is dropped (the later call wins)", async () => {
  let release;
  const gate = new Promise((r) => (release = r));
  let n = 0;
  const apiClient = {
    async getSettings() {
      return { settings: [{ key: "steam_library_steamid", effective: STEAMID }] };
    },
    async steamOwnedGames() {
      n++;
      if (n === 1) {
        await gate;
        return { games: [{ appid: 1, name: "stale" }] };
      }
      return { games: [{ appid: 2, name: "fresh" }] };
    },
  };
  const loader = createOwnedLibraryLoader({ apiClient, onChange: () => {} });
  const first = loader.load();
  await new Promise((r) => setTimeout(r, 5));
  await loader.load();
  release();
  await first;
  assert.deepEqual(loader.current().games.map((g) => g.appid), [2]);
});

test("G6 MUTATION PIN: an OLDER load whose settings read lands after a newer load finished must not overwrite READY", async () => {
  let releaseFirstSettings;
  const firstSettings = new Promise((r) => (releaseFirstSettings = r));
  let settingsCalls = 0;
  const ownedCalls = [];
  const apiClient = {
    async getSettings() {
      settingsCalls++;
      if (settingsCalls === 1) {
        await firstSettings; // the first load's settings read is slow...
        return { settings: [{ key: "steam_library_steamid", effective: "" }] }; // ...and stale (blank)
      }
      return { settings: [{ key: "steam_library_steamid", effective: STEAMID }] };
    },
    async steamOwnedGames(id) {
      ownedCalls.push(id);
      return { games: [{ appid: 2, name: "fresh" }] };
    },
  };
  const loader = createOwnedLibraryLoader({ apiClient, onChange: () => {} });
  const first = loader.load();
  await loader.load(); // the newer load completes first
  assert.equal(loader.current().status, OWNED_STATUS.READY);
  releaseFirstSettings();
  await first;
  assert.equal(loader.current().status, OWNED_STATUS.READY, "the stale blank setting must not flip the state to UNSET");
  assert.deepEqual(loader.current().games.map((g) => g.appid), [2]);
  assert.deepEqual(ownedCalls, [STEAMID], "the superseded load never reached the relay");
});
