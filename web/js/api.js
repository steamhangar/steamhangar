/**
 * vault-api HTTP client (WP 4a.2).
 *
 * Every route in api/README.md's "Endpoints" table requires `X-Api-Key`
 * except `GET /v1/health` (see api/README.md "Auth") — this module injects
 * that header from local storage on every request, classifies failures
 * into a small typed taxonomy so callers can branch on *kind* instead of
 * re-parsing status codes everywhere, and transparently serves in-memory
 * demo fixtures (demo-data.js) when demo mode is on, so the app works with
 * no vault present (NOTES open question 5).
 *
 * CSP note (WP 4a.1's headers, api/vault_api/webui.py): no inline scripts
 * here (this is a module file, loaded via <script type="module" src=...>),
 * and demo mode makes zero network requests — it is pure local data, not a
 * mock server.
 *
 * **No `serverUrl` setting (WP 4a.6 decision, recorded from the WP 4a.2
 * nit).** Earlier drafts of this module stored a `serverUrl` alongside the
 * API key, mirroring the mockup's Android-app "connection profile" concept
 * (System VPN / public domain / a typed server URL). That never applied
 * here: this page is served BY vault-api itself (WP 4a.1, `webui.py`), and
 * the CSP that ships with it is same-origin-only (`connect-src 'self'`) —
 * pointing `fetch()` at a different origin would be blocked by the browser
 * regardless of what a settings screen let someone type. `getServerUrl`/
 * `setServerUrl` and the `steamvault.serverUrl` storage key are deleted
 * outright rather than left as dead code; every request below resolves
 * against `window.location.origin`. Demo mode remains the one supported
 * "no real server" path (NOTES open question 5), unrelated to this.
 */

import { demoRequest } from "./demo-data.js";
import { ApiError, ERROR_KINDS, classifyHttpStatus } from "./errors.js";

export { ApiError, ERROR_KINDS, classifyHttpStatus };

const STORAGE_KEYS = Object.freeze({
  apiKey: "steamvault.apiKey",
  demoMode: "steamvault.demoMode",
});

function readLocalStorage(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch {
    // Private-browsing storage lockouts, disabled storage, etc. — the app
    // must still run (in particular, demo mode must still be reachable).
    return fallback;
  }
}

function writeLocalStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Same reasoning as readLocalStorage — never let a storage failure
    // throw out of a settings toggle.
  }
}

export function getStoredApiKey() {
  return readLocalStorage(STORAGE_KEYS.apiKey, "");
}

export function setStoredApiKey(key) {
  writeLocalStorage(STORAGE_KEYS.apiKey, key);
}

export function isDemoMode() {
  return readLocalStorage(STORAGE_KEYS.demoMode, "0") === "1";
}

export function setDemoMode(on) {
  writeLocalStorage(STORAGE_KEYS.demoMode, on ? "1" : "0");
}

function buildUrl(path, params) {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
  }
  return url;
}

/**
 * Low-level request. Returns the parsed JSON body (or `null` for a `204`),
 * or throws {@link ApiError}. Demo mode short-circuits before any network
 * call is made.
 *
 * @param {string} method
 * @param {string} path Must start with `/v1/...`.
 * @param {{body?: unknown, params?: Record<string, unknown>, signal?: AbortSignal}} [opts]
 */
export async function request(method, path, { body, params, signal } = {}) {
  if (isDemoMode()) {
    return demoRequest(method, path, { body, params });
  }

  let response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers: {
        "X-Api-Key": getStoredApiKey(),
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err && err.name === "AbortError") throw err; // caller-initiated cancellation, not a taxonomy error
    throw new ApiError(ERROR_KINDS.NETWORK, `Network request failed: ${method} ${path}`, {
      cause: err,
    });
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null; // non-JSON body; fall through, `detail` below uses raw text
    }
  }

  if (!response.ok) {
    const kind = classifyHttpStatus(response.status);
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : text || null;
    throw new ApiError(kind, `${method} ${path} failed (${response.status})`, {
      status: response.status,
      detail,
    });
  }

  return data;
}

/**
 * Typed convenience wrappers over the endpoints the polling store and the
 * views built on top of it (4a.3/4a.5) need. Field names are passed
 * through verbatim from api/README.md's "Endpoints" table / the router
 * response models — no renaming layer, so a payload can be handed straight
 * to diff-utils.js / notifications.js keyed the same way the server keys it.
 *
 * Deliberately NOT wrapped here (out of this WP's scope — no current
 * consumer needs them yet): /v1/oracle*, /v1/schedule, /v1/stats. Add them
 * with the WP that first needs them rather than guessing their call shape
 * now.
 *
 * `gc` (WP 4a.4) mirrors the Android sibling's `CacheRepository.gc`
 * semantics exactly: `execute` defaults to `false` (dry run) on both sides,
 * matching the server's "dry run in three independent places" rule
 * (api/README.md "Garbage collection") — a caller must pass `execute: true`
 * explicitly to ever queue a deleting run.
 *
 * `mapping` (WP 4a.3) IS wrapped here: the Library view's bulk-delete
 * confirm dialog needs the full depot->app table to compute the set-aware
 * multiPlan preview (web/js/lib/multiplan.js) — `GET /v1/games/{appid}`'s
 * per-depot `shared: true/false` boolean names no owner ids, so it alone
 * cannot answer "which OTHER apps map this depot". Called on demand (only
 * when a bulk-delete confirm is opened), never on a poll loop.
 *
 * `prefillCached` (WP 4c-web) calls `POST /v1/prefill/cached` — the
 * library-header "Check & update all cached games" trigger
 * (`views/library.js`). **No body is ever sent** (`request()` above only
 * adds a body/`Content-Type` when `body !== undefined`, and this call never
 * passes one) — api/README.md is explicit that the route ignores any body
 * anyway, so sending `{appids: [...]}` here by mistake would silently queue
 * every cached app instead of the ids in that list; not passing one at all
 * is the only way to avoid inviting that mistake later. No dedicated
 * timeout: this repo has no `AbortController`-based timeout wired up for
 * ANY request today (`GET /v1/cache/summary` included), so "give this call
 * the same client timeout as GET /v1/cache/summary" (the WP brief) is
 * satisfied by construction — both go through the same bare `fetch()` with
 * no caller-supplied deadline — rather than by inventing a new mechanism
 * for this one route alone.
 *
 * `getSettings`/`patchSettings` and the `steam*` methods (WP 4a.6) back the
 * Settings view and the onboarding flow — see api/README.md's "Persisted
 * settings" and "Steam Web API relay" sections for the exact response
 * shapes. `patchSettings` takes the body `web/js/lib/settings-diff.js`
 * builds (only the keys that actually changed) and returns straight through
 * to `PATCH /v1/settings`, which answers with the same shape `GET` does.
 *
 * `schedule` (WP 4d-web) wraps `GET /v1/schedule` — the one route the WP
 * 4a.2 header above explicitly deferred ("Add them with the WP that first
 * needs them"). The Settings view is the first real consumer: it reads
 * `last_sweep_targets`/`last_sweep_at`/`last_sweep_enqueued` (the
 * "did the schedule actually do anything" signal) and `sweep_cached_gc_risk`
 * (the already-computed warning condition — see
 * `api/vault_api/routers/schedule.py`'s own docstring on that field: "a UI
 * can render this as a banner without re-deriving the two settings'
 * interaction itself"). No caller re-derives that boolean from
 * `sweep_include_cached`/`auto_gc` client-side; see
 * `web/js/lib/schedule-presentation.js`'s header for why.
 */
export const api = {
  health: () => request("GET", "/v1/health"),
  games: () => request("GET", "/v1/games"),
  game: (appid) => request("GET", `/v1/games/${appid}`),
  mapping: () => request("GET", "/v1/mapping"),
  jobs: (limit = 20) => request("GET", "/v1/jobs", { params: { limit } }),
  job: (id) => request("GET", `/v1/jobs/${id}`),
  prefill: (appids) => request("POST", "/v1/prefill", { body: { appids } }),
  prefillCached: () => request("POST", "/v1/prefill/cached"),
  cancelJob: (id) => request("DELETE", `/v1/jobs/${id}`),
  pauseJob: (id) => request("POST", `/v1/jobs/${id}/pause`),
  resumeJob: (id) => request("POST", `/v1/jobs/${id}/resume`),
  deleteCache: (appid) => request("DELETE", `/v1/cache/${appid}`),
  gc: (appid, execute = false) => request("POST", `/v1/cache/${appid}/gc`, { body: { execute } }),
  cacheSummary: () => request("GET", "/v1/cache/summary"),
  clients: () => request("GET", "/v1/clients"),
  // WP WEB-FEAT-4: Remove in the PCs sheet. `client_id` is operator-chosen
  // free text, so it is percent-encoded as ONE path segment. Resolves
  // `null` (204); 404 rejects with kind `not_found` and the caller decides
  // what that means (the PCs sheet reads it as "already gone").
  deleteClient: (clientId) => request("DELETE", `/v1/clients/${encodeURIComponent(clientId)}`),
  // WP WEB-FEAT-3: Settings → About. Fetched when the Settings view opens
  // and on its Refresh button, never polled (the server caches 60 s).
  about: () => request("GET", "/v1/about"),
  getSettings: () => request("GET", "/v1/settings"),
  patchSettings: (body) => request("PATCH", "/v1/settings", { body }),
  schedule: () => request("GET", "/v1/schedule"),
  getSteamKey: () => request("GET", "/v1/steam/key"),
  putSteamKey: (key) => request("PUT", "/v1/steam/key", { body: { key } }),
  deleteSteamKey: () => request("DELETE", "/v1/steam/key"),
  steamOwnedGames: (steamid) => request("GET", "/v1/steam/owned-games", { params: { steamid } }),
  steamPlayerSummaries: (steamid) =>
    request("GET", "/v1/steam/player-summaries", { params: { steamid } }),
};

/**
 * Test a CANDIDATE vault API key before it is stored anywhere (onboarding
 * step 1, and the Settings "reconnect" flow) — deliberately bypasses
 * `getStoredApiKey()`/demo mode, the only place in this module that does.
 *
 * `GET /v1/health` is the one endpoint that answers with no auth at all
 * (api/README.md "Auth"), so hitting it only proves the server is up, not
 * that `key` is correct — a settings screen that called this "test
 * connection" and stopped there would show a false-positive "200 OK" for a
 * wrong key. This checks BOTH: reachability via `/v1/health`, then the key
 * itself via one authenticated call (`GET /v1/settings`, chosen because
 * both onboarding and the Settings view need its response anyway — no
 * wasted request). A `401` from the second call is reported distinctly
 * (`"That API key was rejected."`) rather than folded into the generic
 * validation-kind message.
 *
 * @param {string} key
 * @returns {Promise<{health: unknown, settings: unknown}>}
 */
export async function checkVaultApiKey(key) {
  let healthResponse;
  try {
    healthResponse = await fetch(new URL("/v1/health", window.location.origin));
  } catch (err) {
    throw new ApiError(ERROR_KINDS.NETWORK, "Could not reach the server.", { cause: err });
  }
  if (!healthResponse.ok) {
    throw new ApiError(
      classifyHttpStatus(healthResponse.status),
      `The server answered ${healthResponse.status} on /v1/health.`,
      { status: healthResponse.status },
    );
  }
  const health = await healthResponse.json().catch(() => ({}));

  let settingsResponse;
  try {
    settingsResponse = await fetch(new URL("/v1/settings", window.location.origin), {
      headers: { "X-Api-Key": key },
    });
  } catch (err) {
    throw new ApiError(ERROR_KINDS.NETWORK, "Could not reach the server.", { cause: err });
  }
  if (!settingsResponse.ok) {
    const kind = classifyHttpStatus(settingsResponse.status);
    const message =
      kind === ERROR_KINDS.AUTH
        ? "That API key was rejected."
        : `The server answered ${settingsResponse.status}.`;
    throw new ApiError(kind, message, { status: settingsResponse.status });
  }
  const settings = await settingsResponse.json();
  return { health, settings };
}
