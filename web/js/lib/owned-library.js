/**
 * Owned Steam games in the web library (WP WEB-FEAT-1).
 *
 * Before this WP the Library grid listed only what `GET /v1/games` knows
 * (apps with an `apps` row: prefilled or mapped at least once) and its
 * header called that list "owned", which it is not. This module holds the
 * pure half of the fix; `views/library.js` wires it to the DOM.
 *
 * Data sources:
 *   - The vault's ONE library SteamID64, the `steam_library_steamid` setting
 *     (`GET /v1/settings`, WP API-FEAT-1, ADR-0016 addendum). Read from the
 *     API on every library open, never from browser storage, so every
 *     device shows the same account. A JSON string (17 digits exceed
 *     JavaScript's safe integers): anything that is not a string is treated
 *     as "not set", never coerced, since a number would already be rounded
 *     to a different account.
 *   - `GET /v1/steam/owned-games?steamid=...` (the relay, which hits Steam).
 *     Fetched on library open and on the manual "Reload" action only, never
 *     on the poll interval (`createOwnedLibraryLoader` has no timer, and
 *     `views/library.js` calls `load()` from exactly those two places).
 *
 * Merge rule (`mergeOwnedLibrary`, same semantics as the Android sibling's
 * `LibraryMerge.kt`): union by appid, vault rows first and unchanged in
 * every cache-state field (the vault is the authority on what is cached),
 * then one synthesized row per owned appid the vault does not know. The
 * synthesized row is a plain `GameSummary`-shaped object (`status: "idle"`,
 * `size_bytes: null`, `depot_count: 0`, `last_prefill_at: null`,
 * `needs_force: false`, `installed_on: []`) so `dispKind` maps it to
 * "Not cached" through the existing code, plus `owned_only: true`. That
 * flag is what keeps a download for such a game in the detail sheet only
 * (user decision): `statusAction` returns no card quick action for it and
 * `classifyBulkSelection` never puts it into a bulk download. A vault row
 * the Steam list does not contain stays as it is (the cache knowledge is
 * still real). One thing IS taken from the owned list for a vault row: a
 * missing name (vault-api has no name for an app it never resolved), so the
 * card does not read "App 1234" when Steam just told us the title.
 *
 * Header counts (`librarySubtitle`):
 *   - SteamID set and the owned list loaded: "N owned · M on the cache".
 *     N is the number of distinct owned appids. M is every game the grid
 *     shows as cached, i.e. the "Cached" chip's count. Chosen over "cached
 *     among the owned games" because "on the cache" describes the cache,
 *     and the grid also shows cached vault games the Steam list does not
 *     contain (another account's purchases, family sharing). With the
 *     narrower count, a visible cached card would be missing from the
 *     header, and the header and the chip would show different numbers.
 *   - otherwise (no SteamID, still loading, the relay failed, or it
 *     returned 0 games — almost always a private profile, not an empty
 *     account, so "0 owned" would be a false claim):
 *     "V games on the vault · M on the cache", V = the rows `GET /v1/games`
 *     returned. Nothing in this branch claims to know what is owned; the
 *     notice line carries the private-profile hint.
 */

import { dispKind, isToolApp, KIND } from "./game-status.js";
import { buildSettingsPatch } from "./settings-diff.js";
import { validSteamId64 } from "./steamid.js";

/** Existing app strings, reused verbatim (the Settings Steam section's
 * no-key status line and the SteamID64 validation line). */
export const NO_STEAM_KEY_MESSAGE = "No Steam Web API key configured. Library queries answer 409 until one is set.";
export const INVALID_STEAMID64_MESSAGE = "That does not look like a valid SteamID64 (17 digits).";
export const PRIVATE_PROFILE_MESSAGE =
  "Steam returned no games for this SteamID64. The profile or its game details are probably private.";

export const STEAM_LIBRARY_SETTING_KEY = "steam_library_steamid";

/** An older vault-api (before WP API-FEAT-1) has no such setting. Shared by
 * the Settings caption and onboarding step 2's "not saved" line. */
export const LIBRARY_SETTING_ABSENT_MESSAGE =
  "This vault-api does not store a library SteamID64 yet (it needs a newer version).";
export const SETTINGS_READONLY_MESSAGE = "This vault's settings are read-only.";
export const LIBRARY_SETTING_ENV_ONLY_MESSAGE =
  "The library SteamID64 is set by the server environment; change it there.";

export const OWNED_STATUS = Object.freeze({
  /** Before the first load finished. */
  LOADING: "loading",
  /** The setting is blank: no SteamID configured. */
  UNSET: "unset",
  /** The owned list loaded (possibly empty). */
  READY: "ready",
  /** `GET /v1/settings` or the relay failed; vault games still show. */
  ERROR: "error",
});

/**
 * The stored library SteamID64 from a `GET /v1/settings` response, or "".
 * @param {{settings?: Array<{key: string, effective: unknown}>} | null | undefined} response
 * @returns {string}
 */
export function steamIdFromSettings(response) {
  const list = response && Array.isArray(response.settings) ? response.settings : [];
  const entry = list.find((e) => e && e.key === STEAM_LIBRARY_SETTING_KEY);
  if (!entry || typeof entry.effective !== "string") return "";
  return entry.effective.trim();
}

/** True for a row this module synthesized from the owned list only. */
export function isOwnedOnly(game) {
  return !!game && game.owned_only === true;
}

/** The synthesized row for an owned game the vault has never seen. */
export function ownedOnlyRow(owned) {
  const name = typeof owned.name === "string" && owned.name.trim() ? owned.name : null;
  return {
    appid: owned.appid,
    name,
    status: "idle",
    last_prefill_at: null,
    last_manifest_check: null,
    depot_count: 0,
    size_bytes: null,
    needs_force: false,
    installed_on: [],
    owned_only: true,
  };
}

function validOwnedEntries(ownedGames) {
  if (!Array.isArray(ownedGames)) return [];
  return ownedGames.filter((g) => g && Number.isInteger(g.appid) && g.appid > 0);
}

/** Number of DISTINCT owned appids (the relay list is de-duplicated
 * defensively, same as the merge). */
export function countOwned(ownedGames) {
  return new Set(validOwnedEntries(ownedGames).map((g) => g.appid)).size;
}

/** A usable display name: a non-blank string. */
export function hasText(name) {
  return typeof name === "string" && name.trim() !== "";
}

/**
 * appid -> name from the owned list (first non-blank name per appid wins).
 * @param {object[] | null | undefined} ownedGames relay `games`
 * @returns {Map<number, string>}
 */
export function ownedNamesByAppid(ownedGames) {
  const names = new Map();
  for (const g of validOwnedEntries(ownedGames)) {
    if (!names.has(g.appid) && hasText(g.name)) names.set(g.appid, g.name);
  }
  return names;
}

/**
 * The title every surface shows for an app (WP WEB-FIX-4): the vault's
 * name, then the owned list's name, then "App <id>". vault-api has no name
 * for an app it never resolved: `POST /v1/prefill` inserts the `apps` row
 * with `name = NULL` (api/vault_api/jobs.py), and only a depot mapping
 * names it. A job for an owned-only game therefore has a vault row with no
 * name, and the job itself carries none (`JobSummary` has no name field).
 * @param {number} appid
 * @param {unknown} vaultName
 * @param {unknown} [ownedName]
 * @returns {string}
 */
export function appTitle(appid, vaultName, ownedName) {
  if (hasText(vaultName)) return vaultName.trim();
  if (hasText(ownedName)) return ownedName.trim();
  return `App ${appid}`;
}

/**
 * WP API-FIX-4: the title of an app from its `GET /v1/games` row — the
 * vault name, else the Steam tool app's name the server sends on that row
 * (`tool_app_name`; `JobSummary` itself carries no flag), else
 * `App <id>`. No client-side tool list: without the row there is no name.
 * @param {number} appid
 * @param {object | undefined} game
 * @returns {string}
 */
export function vaultRowTitle(appid, game) {
  return appTitle(appid, game?.name, isToolApp(game) ? game.tool_app_name : undefined);
}

/**
 * The vault rows with a missing name filled in from the owned list. Adds no
 * rows (unlike `mergeOwnedLibrary`) and leaves every other field alone, so
 * surfaces that must only see vault-known apps (Downloads, the detail
 * sheet's delete plan, the decision panel) can use it for names alone.
 * Returns the input array itself when there is nothing to fill.
 * @param {object[]} vaultGames
 * @param {object[] | null | undefined} ownedGames
 * @returns {object[]}
 */
export function fillMissingNames(vaultGames, ownedGames) {
  const vault = Array.isArray(vaultGames) ? vaultGames : [];
  const names = ownedNamesByAppid(ownedGames);
  if (names.size === 0) return vault;
  return vault.map((g) => {
    const ownedName = g && !hasText(g.name) ? names.get(g.appid) : undefined;
    return ownedName ? { ...g, name: ownedName } : g;
  });
}

/**
 * @param {object[]} vaultGames `GET /v1/games` rows.
 * @param {object[] | null | undefined} ownedGames relay `games`, or null/[]
 *   when there is no owned list (then the vault list comes back as is).
 * @returns {object[]}
 */
export function mergeOwnedLibrary(vaultGames, ownedGames) {
  const vault = Array.isArray(vaultGames) ? vaultGames : [];
  const owned = validOwnedEntries(ownedGames);
  if (owned.length === 0) return vault;

  // A copy: fillMissingNames hands back its input when it fills nothing.
  const merged = fillMissingNames(vault, owned).slice();
  const known = new Set(vault.map((g) => g.appid));
  for (const g of owned) {
    if (known.has(g.appid)) continue;
    known.add(g.appid);
    merged.push(ownedOnlyRow(g));
  }
  return merged;
}

const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/**
 * @param {{
 *   games: object[],
 *   liveJobsByAppid?: Map<number, object>,
 *   owned: {status: string, games: object[]},
 * }} ctx `games` is the MERGED list the grid shows.
 * @returns {string}
 */
export function librarySubtitle({ games, liveJobsByAppid, owned }) {
  const list = Array.isArray(games) ? games : [];
  const cachedCount = list.filter((g) => dispKind(g, liveJobsByAppid?.get(g.appid)) === KIND.CACHED).length;
  const ownedCount = owned && owned.status === OWNED_STATUS.READY ? countOwned(owned.games) : 0;
  if (ownedCount > 0) {
    return `${ownedCount} owned · ${cachedCount} on the cache`;
  }
  const vaultCount = list.filter((g) => !isOwnedOnly(g)).length;
  return `${plural(vaultCount, "game")} on the vault · ${cachedCount} on the cache`;
}

function detailText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed.";
}

/**
 * Relay (or settings) failure -> the sentence the library shows. 409 and
 * 422 reuse the existing Settings strings; everything else names the
 * failure. Every variant leaves the vault games on screen.
 * @param {unknown} err ApiError-shaped
 */
export function describeOwnedLoadError(err) {
  const status = err && typeof err.status === "number" ? err.status : null;
  if (status === 409) return NO_STEAM_KEY_MESSAGE;
  if (status === 422) return `The stored library SteamID64 was rejected. ${INVALID_STEAMID64_MESSAGE}`;
  return `Could not load your Steam library: ${detailText(err)}`;
}

/**
 * A failed lookup of a TYPED SteamID64 (Settings' Preview, onboarding
 * step 2's Look up) -> its sentence. Unlike `describeOwnedLoadError`, a 422
 * is about the typed id, not the stored one.
 * @param {unknown} err ApiError-shaped
 */
export function describeLookupError(err) {
  const status = err && typeof err.status === "number" ? err.status : null;
  if (status === 409) return NO_STEAM_KEY_MESSAGE;
  if (status === 422) return INVALID_STEAMID64_MESSAGE;
  return detailText(err);
}

/** `saveLibrarySteamId` outcomes. */
export const SAVE_OUTCOME = Object.freeze({
  SAVED: "saved",
  /** The stored value already equals the typed one: no PATCH was sent. */
  UNCHANGED: "unchanged",
  /** The vault's settings are read-only: no PATCH was sent. */
  READONLY: "readonly",
  /** Older vault-api without the setting: no PATCH was sent. */
  ABSENT: "absent",
  /** The setting is env-only on this server: no PATCH was sent. */
  ENV_ONLY: "env-only",
  /** The typed text is neither blank nor a valid SteamID64: no PATCH. */
  INVALID: "invalid",
  /** The PATCH failed. */
  ERROR: "error",
});

/**
 * Store the library SteamID64 (WP WEB-FEAT-2; shared by Settings' Save and
 * onboarding step 2's Look up). The PATCH body comes from the same builder
 * as the Settings form (`buildSettingsPatch`): only a real change is sent,
 * and the value is the trimmed STRING, never a Number (17 digits exceed
 * JavaScript's safe integers; the API answers a number with 422). A blank
 * `typed` is an explicit "" override (= not set).
 *
 * @param {{patchSettings: (body: object) => Promise<any>}} apiClient
 * @param {{readonly?: boolean, settings?: object[]} | null | undefined} settingsResponse
 *   the last `GET`/`PATCH /v1/settings` answer
 * @param {string} typed
 * @returns {Promise<{outcome: string, settingsResponse?: object, error?: string}>}
 */
export async function saveLibrarySteamId(apiClient, settingsResponse, typed) {
  const entries = settingsResponse && Array.isArray(settingsResponse.settings) ? settingsResponse.settings : [];
  const entry = entries.find((e) => e && e.key === STEAM_LIBRARY_SETTING_KEY);
  if (!entry) return { outcome: SAVE_OUTCOME.ABSENT, error: LIBRARY_SETTING_ABSENT_MESSAGE };
  if (entry.env_only) return { outcome: SAVE_OUTCOME.ENV_ONLY, error: LIBRARY_SETTING_ENV_ONLY_MESSAGE };
  if (settingsResponse.readonly) return { outcome: SAVE_OUTCOME.READONLY, error: SETTINGS_READONLY_MESSAGE };
  const value = typeof typed === "string" ? typed.trim() : "";
  if (value && !validSteamId64(value)) return { outcome: SAVE_OUTCOME.INVALID, error: INVALID_STEAMID64_MESSAGE };
  const body = buildSettingsPatch(entries, { [STEAM_LIBRARY_SETTING_KEY]: { value } });
  if (Object.keys(body).length === 0) return { outcome: SAVE_OUTCOME.UNCHANGED };
  try {
    return { outcome: SAVE_OUTCOME.SAVED, settingsResponse: await apiClient.patchSettings(body) };
  } catch (err) {
    const status = err && typeof err.status === "number" ? err.status : null;
    return {
      outcome: SAVE_OUTCOME.ERROR,
      error: status === 422 ? `${INVALID_STEAMID64_MESSAGE} (${detailText(err)})` : detailText(err),
    };
  }
}

/** Which follow-up the notice line offers. */
export const NOTICE_ACTION = Object.freeze({ SETTINGS: "settings", RELOAD: "reload" });

/**
 * The one line under the library header about the owned list, or null when
 * there is nothing to say (owned list loaded with games).
 * @param {{status: string, games: object[], error?: string|null, errorStatus?: number|null, loading?: boolean}} owned
 * @returns {{text: string, actions: string[], warn: boolean} | null}
 */
export function ownedNotice(owned) {
  if (!owned) return null;
  if (owned.status === OWNED_STATUS.LOADING) {
    return { text: "Loading your Steam library…", actions: [], warn: false };
  }
  if (owned.status === OWNED_STATUS.UNSET) {
    return {
      text: "Only games the vault knows are listed. Set your SteamID64 in Settings to list every game you own.",
      actions: [NOTICE_ACTION.SETTINGS],
      warn: false,
    };
  }
  if (owned.status === OWNED_STATUS.ERROR) {
    const fixInSettings = owned.errorStatus === 409 || owned.errorStatus === 422;
    return {
      text: `${owned.error} Showing vault games only.`,
      actions: fixInSettings ? [NOTICE_ACTION.SETTINGS, NOTICE_ACTION.RELOAD] : [NOTICE_ACTION.RELOAD],
      warn: true,
    };
  }
  if (owned.status === OWNED_STATUS.READY && countOwned(owned.games) === 0) {
    return { text: PRIVATE_PROFILE_MESSAGE, actions: [NOTICE_ACTION.RELOAD], warn: true };
  }
  return null;
}

/**
 * The owned-list fetch, with no timer of its own: it runs exactly when
 * `load()` is called. A generation token drops a superseded result (a
 * second `load()` while the first is in flight wins).
 *
 * @param {{
 *   apiClient: {getSettings: () => Promise<any>, steamOwnedGames: (steamid: string) => Promise<any>},
 *   onChange: (state: object) => void,
 * }} deps
 */
export function createOwnedLibraryLoader({ apiClient, onChange }) {
  let generation = 0;
  let state = { status: OWNED_STATUS.LOADING, steamid: null, games: [], error: null, errorStatus: null, loading: false };

  function set(next) {
    state = next;
    onChange(state);
  }

  async function load() {
    const myGeneration = ++generation;
    set({ ...state, loading: true });
    let steamid;
    try {
      steamid = steamIdFromSettings(await apiClient.getSettings());
    } catch (err) {
      if (myGeneration !== generation) return;
      set({
        status: OWNED_STATUS.ERROR,
        steamid: null,
        games: [],
        error: `Could not read the library SteamID64 from the vault settings: ${detailText(err)}`,
        errorStatus: null,
        loading: false,
      });
      return;
    }
    if (myGeneration !== generation) return;
    if (!steamid) {
      set({ status: OWNED_STATUS.UNSET, steamid: "", games: [], error: null, errorStatus: null, loading: false });
      return;
    }
    try {
      const response = await apiClient.steamOwnedGames(steamid);
      if (myGeneration !== generation) return;
      const games = response && Array.isArray(response.games) ? response.games : [];
      set({ status: OWNED_STATUS.READY, steamid, games, error: null, errorStatus: null, loading: false });
    } catch (err) {
      if (myGeneration !== generation) return;
      set({
        status: OWNED_STATUS.ERROR,
        steamid,
        games: [],
        error: describeOwnedLoadError(err),
        errorStatus: err && typeof err.status === "number" ? err.status : null,
        loading: false,
      });
    }
  }

  return {
    load,
    current: () => state,
  };
}
