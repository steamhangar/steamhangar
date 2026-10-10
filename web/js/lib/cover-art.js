/**
 * Cover-art URL + deterministic fallback tile (WP 4a.3).
 *
 * Ports the mockup's capsule-art approach (docs/design/
 * vault-app-mockup-NOTES.md, "Covers"): 2:3 portrait artwork, and when it
 * can't be shown, a styled tile rather than a blank rectangle. The mockup
 * used per-title hues from its fake seed data (`g.h1`/`g.h2`); the real app
 * has real appids and no seed hues, so `fallbackHues`/`fallbackPattern`
 * below derive the same two ingredients deterministically FROM the appid —
 * same game always gets the same fallback look, no server round trip, no
 * randomness to make headless-test assertions flaky.
 *
 * **CSP note (deliberate extension — see api/vault_api/webui.py and
 * api/README.md's "Security headers" section for the paired change this WP
 * makes there).** `STEAM_CDN_HOST` is the exact, single host added to
 * `img-src`; this module is the ONLY place that constructs a URL against
 * it, so the CSP's scope and this module's scope are the same one line.
 * `library_600x900.jpg` is Valve's own portrait-capsule asset path — same
 * 2:3 ratio the mockup's fake covers already used (NOTES.md: "costs nothing
 * to swap in real Steam artwork later").
 *
 * Pure — no DOM, no fetch — so the URL shape and the fallback hashing are
 * unit-tested headlessly (web/tests/cover-art.test.js); the actual `<img>`
 * element + its onerror-swap-to-fallback wiring lives in
 * components/game-card.js, which is DOM-only and not unit-tested the same
 * way (mirrors the existing split between status-icon.js's pure glyph
 * tables and its DOM builder).
 *
 * **`headerArtUrl` (WP 4h.3).** Same host, same "this module is the ONLY
 * place that builds a URL against it" discipline, different asset:
 * `header.jpg` is Valve's wide 460x215 landscape capsule, used as hero/
 * header art at the top of the game detail card
 * (components/header-art.js) rather than the portrait grid/mini-cover
 * shape above. No CSP change needed — `STEAM_CDN_HOST` is already the
 * exact, single host `img-src` allows (api/vault_api/webui.py), and this
 * is a different PATH on the same host, not a new one.
 *
 * **Server-resolved covers (WP API-FIX-5).** Valve no longer serves the
 * legacy `library_600x900.jpg` path for newer games (404); their art lives
 * under a hashed path on `shared.akamai.steamstatic.com`. vault-api looks it
 * up and sends it as `cover_url` on `GET /v1/games[/{appid}]` rows.
 * `coverArtUrl(appid, coverUrl)` uses that URL when it is an https URL on
 * one of `STEAM_ASSET_HOSTS` (the same two hosts the CSP's `img-src`
 * allows, api/vault_api/webui.py), and the legacy URL otherwise (null, an
 * older server, an owned-only game, anything malformed). The image's own
 * error fallback in the components is unchanged. `headerArtUrl` still uses
 * the legacy path (no server lookup for it).
 */

export const STEAM_CDN_HOST = "cdn.akamai.steamstatic.com";

/** Host of the hashed store-item assets (WP API-FIX-5). */
export const STEAM_STORE_ASSET_HOST = "shared.akamai.steamstatic.com";

/** Every host a server-sent `cover_url` may name. */
export const STEAM_ASSET_HOSTS = Object.freeze([STEAM_CDN_HOST, STEAM_STORE_ASSET_HOST]);

/**
 * A server-sent `cover_url` if it is usable, else null: a string of at most
 * 512 ASCII characters, https, host exactly one of `STEAM_ASSET_HOSTS`, no
 * credentials, port or fragment, no whitespace or backslash.
 * @param {unknown} value
 * @returns {string | null}
 */
export function serverCoverUrl(value) {
  if (typeof value !== "string" || value.length === 0 || value.length > 512) return null;
  if (!/^[\x21-\x7e]+$/.test(value) || value.includes("\\") || value.includes("#")) return null;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  if (!STEAM_ASSET_HOSTS.includes(parsed.host)) return null;
  if (parsed.username || parsed.password || parsed.port) return null;
  // The URL parser lowercases the host; insist the original already was.
  if (!value.startsWith(`https://${parsed.host}/`)) return null;
  return value;
}

/**
 * Portrait cover for `appid`: the server's `cover_url` when usable (WP
 * API-FIX-5), else the legacy CDN path.
 * @param {number} appid
 * @param {unknown} [coverUrl] `cover_url` from a `GET /v1/games` row
 */
export function coverArtUrl(appid, coverUrl) {
  return serverCoverUrl(coverUrl) ?? `https://${STEAM_CDN_HOST}/steam/apps/${appid}/library_600x900.jpg`;
}

/** Wide (460x215) header/hero capsule art for the game detail card
 * (WP 4h.3). @param {number} appid */
export function headerArtUrl(appid) {
  return `https://${STEAM_CDN_HOST}/steam/apps/${appid}/header.jpg`;
}

/** Tiny deterministic string hash (FNV-1a), good enough for "pick a stable
 * decorative value from an integer id" — not a security primitive. */
function fnv1a(n) {
  let h = 0x811c9dc5;
  const str = String(n);
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Two hues (0-359) for the fallback tile's gradient, mirroring the
 * mockup's `--h1`/`--h2` custom properties. Always the same pair for the
 * same appid. */
export function fallbackHues(appid) {
  const h = fnv1a(appid);
  const h1 = h % 360;
  const h2 = (h >>> 9) % 360;
  return { h1, h2 };
}

/** Which of the mockup's six decorative overlay patterns (p0..p5,
 * css/app.css) this appid's fallback tile uses. */
export function fallbackPattern(appid) {
  return fnv1a(appid * 2654435761) % 6;
}
