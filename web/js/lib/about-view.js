/**
 * Settings → About: presentation of `GET /v1/about` (WP WEB-FEAT-3).
 *
 * The response (api/vault_api/routers/about.py `AboutOut`, WP VER-2) is
 * `{components: [{name, version, commit, status, checked_at, detail}, ...]}`
 * — six fixed components in a fixed order, four status words. This module
 * turns one entry into the words the table shows; it decides nothing about
 * the components themselves. In particular:
 *
 *  - `status` is shown as the server sent it (word + icon). vault-core is
 *    ALWAYS `unknown` (its version comes from a file its start hook wrote,
 *    never from a live probe — user decision "Weg A"), vault-dns is ALWAYS
 *    `unknown` (never probed). The static notes below say so in plain words
 *    next to the server's own `detail`, so "Unknown" never reads as a fault.
 *  - The answer is cached by the server for 60 s (`CACHE_TTL_SECONDS`), and
 *    `checked_at` says when vault-api looked. {@link checkedText} states
 *    both, so a Refresh that returns the same answer is not mistaken for a
 *    live check.
 *  - `version`/`commit` are a value, `"invalid"` or `null`. `null` reads
 *    "unknown"; `"invalid"` reads "invalid" (a baked value outside the VER-1
 *    grammar), never a fabricated version.
 *
 * Pure: no DOM, no fetch. Covered in web/tests/about-view.test.js.
 */

import { formatAgo } from "./format.js";

/** Server-side answer cache, in seconds (api/vault_api/about.py
 * `CACHE_TTL_SECONDS`). Pinned against that constant in
 * web/tests/about-view.test.js. */
export const ABOUT_CACHE_SECONDS = 60;

/**
 * Status word -> what the table shows. `icon` is a `createStatusIcon` kind
 * (components/status-icon.js); `tone` the text-colour class of the badge.
 * `unknown`/`not_in_use` get their own neutral glyphs ("?" and a dash):
 * reusing the warning or error glyph would report a component that is
 * simply not checked as a fault (docs/LEARNINGS.md, the "cancelled" glyph
 * entry, same class).
 */
export const ABOUT_STATUS = Object.freeze({
  ok: Object.freeze({ word: "OK", icon: "cached", tone: "tx-cached" }),
  unreachable: Object.freeze({ word: "Unreachable", icon: "error", tone: "tx-error" }),
  not_in_use: Object.freeze({ word: "Not in use", icon: "notinuse", tone: "tx-cancelled" }),
  unknown: Object.freeze({ word: "Unknown", icon: "unknown", tone: "tx-cancelled" }),
});

/**
 * One plain sentence per component on what its status can and cannot mean.
 * Shown above the server's own `detail` (which is rendered verbatim, via
 * textContent).
 */
export const COMPONENT_NOTES = Object.freeze({
  "vault-api": "This server. It also serves this web UI.",
  "vault-core": "Version recorded at vault-core's last start, not a live check.",
  // 90 = api/vault_api/runner_presence.py PRESENCE_STALE_SECONDS (pinned in
  // web/tests/about-view.test.js).
  "vault-runner": "OK means the runner checked in within the last 90 s.",
  steamprefill: "The SteamPrefill build that prefill jobs run with.",
  "vault-proxy": "Reachability only; the egress proxy reports no version.",
  "vault-dns": "Unknown, not probed: vault-api never checks the optional DNS container.",
});

/** Shown instead of the table when the server has no `GET /v1/about`. */
export const ABOUT_TOO_OLD_MESSAGE =
  "This server is older than this web UI and does not report component versions. Update the server to see them here.";

/** @param {unknown} status */
export function statusPresentation(status) {
  return Object.prototype.hasOwnProperty.call(ABOUT_STATUS, status) ? ABOUT_STATUS[status] : ABOUT_STATUS.unknown;
}

/**
 * The version cell. `null` -> "unknown", anything else verbatim (including
 * `"invalid"`). Long values wrap in CSS; the full value is also the title.
 * @param {unknown} version
 * @returns {{text: string, title: string | null}}
 */
export function versionCell(version) {
  if (typeof version !== "string" || !version) return { text: "unknown", title: null };
  return { text: version, title: version };
}

/**
 * The commit cell: a hex id shortened to 7 characters with the full id as
 * the title; `null` -> "unknown"; `"invalid"` or any non-hex value shown as
 * sent (never shortened into something that looks like a real id).
 * @param {unknown} commit
 * @returns {{text: string, title: string | null}}
 */
export function commitCell(commit) {
  if (typeof commit !== "string" || !commit) return { text: "unknown", title: null };
  if (/^[0-9a-f]{8,64}$/i.test(commit)) return { text: commit.slice(0, 7), title: commit };
  return { text: commit, title: commit };
}

/**
 * Everything one table row shows.
 * @param {{name?: unknown, version?: unknown, commit?: unknown, status?: unknown, detail?: unknown}} component
 */
export function describeComponent(component) {
  const c = component || {};
  const name = typeof c.name === "string" ? c.name : "unknown component";
  const status = statusPresentation(c.status);
  return {
    name,
    version: versionCell(c.version),
    commit: commitCell(c.commit),
    statusWord: status.word,
    statusIcon: status.icon,
    statusTone: status.tone,
    note: Object.prototype.hasOwnProperty.call(COMPONENT_NOTES, name) ? COMPONENT_NOTES[name] : null,
    detail: typeof c.detail === "string" && c.detail.trim() ? c.detail : null,
  };
}

/**
 * The component list from a response, or `null` when the body is not an
 * `{components: [...]}` object (nothing honest to render).
 * @param {unknown} response
 * @returns {object[] | null}
 */
export function aboutComponents(response) {
  if (!response || typeof response !== "object" || !Array.isArray(response.components)) return null;
  return response.components.filter((c) => c && typeof c === "object");
}

/**
 * "Checked by the server 2 min ago. ..." from the OLDEST `checked_at` in
 * the list (the most conservative age), plus the cache sentence. `null`
 * when no entry carries a readable timestamp.
 * @param {object[] | null | undefined} components
 * @param {number} [nowMs]
 * @returns {string | null}
 */
export function checkedText(components, nowMs = Date.now()) {
  if (!Array.isArray(components)) return null;
  let oldest = null;
  for (const c of components) {
    const t = c && typeof c.checked_at === "string" ? Date.parse(c.checked_at) : NaN;
    if (Number.isFinite(t) && (oldest === null || t < oldest.t)) oldest = { t, iso: c.checked_at };
  }
  if (!oldest) return null;
  const ago = formatAgo(oldest.iso, nowMs);
  return (
    `Checked by the server ${ago}. ` +
    `The server reuses this answer for up to ${ABOUT_CACHE_SECONDS} s, so Refresh can show the same result.`
  );
}

/**
 * How a failed `GET /v1/about` is shown. Only a 404 is "too old" (a note,
 * not an error): with a VALID key an unknown route answers 404 — the
 * pre-auth body guard (api/README.md "Auth", WP SEC-FIX-4) checks the key
 * first and lets a keyed request through to routing — so a server from
 * before WP VER-2 says 404. A 401 always means the key was refused, on any
 * server version; it is an error like every other status (review fix).
 * @param {{status?: number} | null | undefined} err
 * @returns {"too_old" | "error"}
 */
export function classifyAboutError(err) {
  const status = err && typeof err.status === "number" ? err.status : null;
  return status === 404 ? "too_old" : "error";
}
