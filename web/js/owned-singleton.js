/**
 * The one owned-games list for the whole page (WP WEB-FIX-4).
 *
 * WEB-FEAT-1 kept the owned-list loader inside `views/library.js`, so only
 * the Library grid knew a title Steam had told us. Downloads, the detail
 * sheet, the notification panel and the decision panel read the raw
 * `GET /v1/games` rows instead, and a job for an owned-only game read
 * "App 4358690" there: vault-api inserts the `apps` row with no name when
 * the job is queued (`api/vault_api/jobs.py`), and a job carries no name at
 * all. This module holds that one loader (same posture as
 * `store-singleton.js`) so every surface can fall back to the owned name
 * (`lib/owned-library.js`'s `appTitle` order: vault name, owned name,
 * "App <id>").
 *
 * Fetch rule, unchanged from WEB-FEAT-1: the relay hits Steam, so nothing
 * here has a timer and nothing polls it. `load()` is called by the Library
 * (on open and on its Reload button). The one other caller is
 * `loadIfNeverLoaded()`, used by Downloads: if a job on screen has no vault
 * name and no load has been started in this page's life, it starts exactly
 * one. Opening `/downloads` directly (a bookmark, a notification) would
 * otherwise keep "App <id>" until the user happens to visit the Library.
 * After that first load, Downloads never asks again; a failed or empty load
 * is not retried from there (the Library's Reload is the retry).
 */
import { api } from "./api.js";
import { createOwnedLibraryLoader } from "./lib/owned-library.js";

const listeners = new Set();
let loadStarted = false;

const loader = createOwnedLibraryLoader({
  apiClient: api,
  onChange: (state) => {
    for (const fn of listeners) fn(state);
  },
});

export const ownedLibrary = {
  /** Fetch the stored SteamID64 and its owned list (one relay call). */
  load() {
    loadStarted = true;
    return loader.load();
  },
  /** Start one load unless one was ever started in this page's life.
   * @returns {boolean} whether a load was started */
  loadIfNeverLoaded() {
    if (loadStarted) return false;
    ownedLibrary.load();
    return true;
  },
  /** The loader state: `{status, steamid, games, error, errorStatus, loading}`. */
  current: () => loader.current(),
  /** Called on every state change; returns an unsubscribe function. */
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};
