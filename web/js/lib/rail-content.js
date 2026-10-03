/**
 * Pure presentation logic for the rail's two content pieces (WP 4e.6 — the
 * left rail earns its narrowed width with the vault name at the top and a
 * cache used/free summary at the bottom, instead of carrying only the three
 * nav items WP 4e.1 shipped it with).
 *
 * Both functions below take whatever shape the wiring layer
 * (components/rail-panel.js) currently has on hand — which may be
 * `null`/`undefined` (nothing fetched/polled yet, or the last attempt
 * failed) — and return either a real, printable result or `null` for
 * "nothing honest to print" (the same contract format.js's `formatBytesGB`
 * already uses). The caller renders NOTHING when the result is `null`,
 * never a placeholder that could be mistaken for a real value — this is the
 * brief's "absence renders as unknown, never as zero" rule, applied to two
 * NEW pieces of data rather than restated in prose only.
 *
 * No DOM, no fetch, no timers — see web/tests/rail-content.test.js for the
 * headless pins, including the unknown-vs-zero mutation targets.
 */

import { formatBytesGBOrZero } from "./format.js";

/**
 * The rail head's vault-name text, given whatever the last `GET
 * /v1/settings` response was (the same shape `views/settings.js` already
 * consumes — `{readonly, settings: [{key, effective, ...}, ...]}`), or
 * `null`/`undefined` if nothing has been fetched yet or the fetch failed.
 *
 * @param {{settings?: Array<{key: string, effective?: unknown}>} | null | undefined} settingsResponse
 * @returns {string | null} `null` when there is nothing honest to show
 *   (no response yet, malformed response, or an empty/unset vault name —
 *   an unnamed vault is not a fabricated name, it is simply nothing to
 *   print here, same as `formatBytesGB`'s "nothing to print" for zero).
 */
export function vaultNameFromSettings(settingsResponse) {
  if (!settingsResponse || !Array.isArray(settingsResponse.settings)) return null;
  const entry = settingsResponse.settings.find((s) => s && s.key === "vault_name");
  if (!entry || typeof entry.effective !== "string") return null;
  const trimmed = entry.effective.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * The rail foot's used/free lines, given whatever the store's "cache"
 * resource last snapshotted (the raw `GET /v1/cache/summary` body — see
 * api/README.md's "`GET /v1/cache/summary`" section for the shape:
 * `{total_bytes, top_consumers, unmapped_depots, free_disk_bytes}`), or
 * `null`/`undefined` before the first successful poll / after a failed one.
 *
 * @param {{total_bytes?: unknown, free_disk_bytes?: unknown} | null | undefined} summary
 * @returns {{usedText: string, freeText: string | null} | null} `null` when
 *   there is nothing honest to show at all (no summary yet, or a shape that
 *   is not a real summary — `total_bytes` is never absent from the shipped
 *   response, so a missing/invalid one means "not a real answer", not "the
 *   vault has zero bytes cached"). `freeText` is independently `null` when
 *   `free_disk_bytes` itself is `null` — vault-api's own documented
 *   contract for "undeterminable" — which must stay unknown here too, never
 *   become a fabricated, alarming "0 B free".
 */
export function cacheFootFromSummary(summary) {
  if (!summary || typeof summary !== "object") return null;
  const usedText = formatBytesGBOrZero(summary.total_bytes);
  if (usedText === null) return null; // not a real summary — total_bytes is never legitimately absent
  const freeText = formatBytesGBOrZero(summary.free_disk_bytes); // null stays null (undeterminable), never "0 B"
  return { usedText, freeText };
}

/** Display-length cap for {@link versionFromSettings} — long enough for any
 * realistic semver-ish string ("1.4.0-beta.12+build.456" is 23 chars), short
 * enough that a pathological server value cannot visually overrun the
 * rail's own fixed width even before CSS's own `overflow:hidden` (belt AND
 * suspenders — see app.css's `.rail-version` rule for the other layer).
 * Applied BEFORE the "v" prefix below, so the worst-case rendered length is
 * this plus one character, never unbounded either way. */
const VERSION_MAX_LEN = 24;

/**
 * The rail foot's optional version line, given the same `GET /v1/settings`
 * response {@link vaultNameFromSettings} reads.
 *
 * **Confirmed shape (WP 4e.7, `api/`, landed in parallel — coordinator
 * report): a top-level `server_version` STRING at JSON path
 * `$.server_version`, a SIBLING of `readonly` (not a row inside the
 * `settings` array — a version has no source precedence and is not
 * settable; `PATCH` rejects it as an unrecognised key)** — e.g. `{readonly,
 * settings: [...], server_version: "0.1.0"}`. Absent, non-string, or empty
 * after trimming are all the SAME case ("nothing to show") — the field is a
 * hand-maintained constant on the server (no release-tagging process yet),
 * so a malformed value is a real possibility and "show nothing" is the only
 * honest fallback for all three, not just the "key truly missing" one.
 *
 * The value is untrusted server text (WP 4e.7 does not validate its format
 * beyond "it is a string" any more than any other settings value is): this
 * function only clamps LENGTH (never markup — the caller assigns it via
 * `textContent`, never `innerHTML`, so there is no markup to sanitize). The
 * returned string is prefixed with `v` only when it starts with a digit, and
 * the bare local-build value `dev` reads "dev build" (WP VER-2: it used to
 * render as "vdev") — this is the SERVER's own reported version, deliberately
 * not labelled "Release" (no release-tagging process exists yet — WP 5.5)
 * and never a frontend-hardcoded fallback: `VAULT_WEB_DIR` can point this
 * `web/` at a different image than the one actually running, so the two
 * really can diverge, which is precisely what an operator needs to see.
 *
 * @param {{server_version?: unknown} | null | undefined} settingsResponse
 * @returns {string | null} `null` when the field is absent, not a string,
 *   or empty after trimming — never a placeholder standing in for it.
 */
export function versionFromSettings(settingsResponse) {
  if (!settingsResponse || typeof settingsResponse.server_version !== "string") return null;
  const trimmed = settingsResponse.server_version.trim();
  if (!trimmed) return null;
  // WP VER-2: a locally built image reports `dev` (WP VER-1's Dockerfile
  // default), which the old "always prefix v" rule rendered as "vdev". Only
  // a value that starts with a digit is a release number and gets the `v`;
  // the bare `dev` default reads "dev build"; anything else (`dev-1a2b3c4`,
  // `ci-1a2b3c4`, `v0.1.0`, `V2`) is shown as the server sent it.
  if (/^dev$/i.test(trimmed)) return "dev build";
  const clamped = trimmed.length > VERSION_MAX_LEN ? trimmed.slice(0, VERSION_MAX_LEN - 1) + "…" : trimmed;
  return /^[0-9]/.test(clamped) ? `v${clamped}` : clamped;
}
