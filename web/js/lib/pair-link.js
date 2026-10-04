/**
 * Pairing links for Settings → "Add a device" (WP PAIR-1).
 *
 * User decision 2026-10-04 ("Weg A"): a new device is set up from this
 * already-configured web UI, with the ONE shared vault API key it already
 * holds, and no API change (ADR-0016 freeze). Short-lived codes and one
 * revocable key per device are the later D9 PAIR-2 (docs/PROJECT_PLAN.md).
 *
 * Two carriers, both built here:
 *
 *  - Android app: `steamhangar://pair?v=1&url=<base URL>&key=<API key>`.
 *    THIS STRING IS A CONTRACT with the Android package (built in parallel,
 *    it parses exactly this shape); do not change it without changing both
 *    sides. Pinned literally in web/tests/pair-link.test.js.
 *  - Another browser: `<origin>/#pair=<API key>`. The key travels in the
 *    fragment, which a browser never sends to a server, so it cannot land in
 *    an access log. The receiving page strips it at once
 *    (lib/pair-intake.js).
 *
 * Both values are percent-encoded with `encodeURIComponent` (agreed with the
 * Android side, APP-PAIR-1): `+` becomes `%2B` and is never emitted raw (the
 * app decodes a raw `+` as a space), `&`, `=`, `#`, `/`, `:` and every
 * non-ASCII character are escaped as UTF-8 `%XX`.
 *
 * Base URL: the web UI talks to vault-api on its own origin (web/js/api.js,
 * `window.location.origin`; the CSP is `connect-src 'self'`), so the URL the
 * app should use is the page origin, without a trailing slash.
 *
 * Pure: no DOM, no storage, no fetch.
 */

export const PAIR_URI_PREFIX = "steamhangar://pair?v=1";
export const PAIR_FRAGMENT_PARAM = "pair";

/**
 * Percent-encode one URI component (`encodeURIComponent`, see the module
 * header). Throws `URIError` for a string with a lone surrogate, which
 * {@link isUsableKey} refuses up front.
 * @param {string} value
 */
export function encodePairComponent(value) {
  return encodeURIComponent(String(value));
}

/**
 * A key that can be put in a link or a command: a non-empty string with no
 * control characters (an HTTP header value cannot carry them either, so a
 * key with one could never have worked).
 * @param {unknown} key
 */
export function isUsableKey(key) {
  if (typeof key !== "string" || key.length === 0 || /[\u0000-\u001f\u007f]/.test(key)) return false;
  try {
    encodeURIComponent(key); // a lone surrogate cannot be encoded
    return true;
  } catch {
    return false;
  }
}

/**
 * The key rule of the Android app (APP-PAIR-1, mirrored here, review
 * finding 8): printable ASCII only (U+0020..U+007E) and no space at either
 * end. A key outside it cannot be paired by QR; the phone option says so
 * instead of showing a code the app would refuse.
 * @param {unknown} key
 */
export function isAppPairableKey(key) {
  return typeof key === "string" && /^[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?$/.test(key);
}

/** Shown instead of the QR code when {@link isAppPairableKey} fails. */
export const APP_KEY_UNSUPPORTED_TEXT =
  "This hangar's API key has characters the Android app does not accept (it takes printable ASCII without spaces at either end), so there is no QR code for it. Use a key made of plain letters, digits and punctuation.";

/**
 * The vault-api base URL this page uses (web/js/api.js resolves every
 * request against `window.location.origin`).
 * @param {{origin: string}} location
 */
export function apiBaseUrl(location) {
  return String(location.origin).replace(/\/+$/, "");
}

/**
 * The Android pairing URI (the contract, see the module header). `url` is
 * reduced to `scheme://host[:port]` (the app refuses a path, query or
 * fragment), without a trailing slash.
 * @param {string} baseUrl
 * @param {string} key
 */
export function buildAppPairUri(baseUrl, key) {
  const origin = new URL(baseUrl).origin;
  return `${PAIR_URI_PREFIX}&url=${encodePairComponent(origin)}&key=${encodePairComponent(key)}`;
}

/**
 * The link that pairs another browser.
 * @param {string} origin
 * @param {string} key
 */
export function buildBrowserPairLink(origin, key) {
  return `${String(origin).replace(/\/+$/, "")}/#${PAIR_FRAGMENT_PARAM}=${encodePairComponent(key)}`;
}

/**
 * Read a pairing key from `location.hash`.
 *
 * Returns `null` when the fragment carries no `pair=` parameter (nothing to
 * do), `{key}` for a usable key, or `{error}` when the parameter is there
 * but empty, not decodable or not a usable key. In both non-null cases the
 * caller strips the WHOLE fragment.
 *
 * @param {string} hash e.g. "#pair=abc%2B1"
 * @returns {null | {key: string} | {error: string}}
 */
export function readPairFragment(hash) {
  const text = typeof hash === "string" ? hash.replace(/^#/, "") : "";
  if (!text) return null;
  const part = text.split("&").find((p) => p === PAIR_FRAGMENT_PARAM || p.startsWith(`${PAIR_FRAGMENT_PARAM}=`));
  if (part === undefined) return null;
  const raw = part.slice(PAIR_FRAGMENT_PARAM.length + 1);
  let key;
  try {
    key = decodeURIComponent(raw);
  } catch {
    return { error: "The pairing link is damaged (it could not be decoded)." };
  }
  if (!isUsableKey(key)) return { error: "The pairing link carries no usable API key." };
  return { key };
}

/**
 * The current URL without its fragment, for `history.replaceState`.
 * @param {{pathname: string, search?: string}} location
 */
export function urlWithoutFragment(location) {
  return `${location.pathname || "/"}${location.search || ""}`;
}

/**
 * What to do with a key that arrived by link.
 *
 *  - `"same"`: this browser already uses exactly this key — nothing to do.
 *  - `"confirm-replace"`: a DIFFERENT key is stored; never overwrite it
 *    without asking (it may be a different hangar).
 *  - `"verify"`: no key stored (first run or demo mode) — check it, store it.
 *
 * @param {{candidate: string, storedKey: string, demoMode: boolean}} ctx
 * @returns {"same" | "confirm-replace" | "verify"}
 */
export function pairIntakeAction({ candidate, storedKey, demoMode }) {
  if (storedKey && storedKey === candidate) return demoMode ? "verify" : "same";
  if (storedKey) return "confirm-replace";
  return "verify";
}
