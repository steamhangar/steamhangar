/**
 * Settings → About: presentation of `GET /v1/about` (WP WEB-FEAT-3; wording
 * reworked in WP WEB-FIX-8 after user feedback on rc9).
 *
 * The response (api/vault_api/routers/about.py `AboutOut`, WP VER-2) is
 * `{components: [{name, version, commit, status, checked_at, detail}, ...]}`
 * — six fixed components in a fixed order, four status words. This module
 * turns the list into the words the table shows. The server is frozen
 * (ADR-0016), so the friendlier vocabulary is decided HERE, from data the
 * server already sends:
 *
 *  - The word "unknown" is never shown. A version/commit the component does
 *    not report is an em dash ({@link DASH}); the reason sits behind the
 *    row's (i) button.
 *  - The server's generic `unknown` status means vault-api DID look and got
 *    a bad or unclear answer (api/vault_api/about.py: a proxy that forwards
 *    a host it must refuse, HTTP_PROXY unusable, an unreadable presence
 *    table, a probe past its deadline). It reads as a neutral "Check", and
 *    a Check row's (i) details start OPEN (`infoOpenByDefault`), so the
 *    server's reason is in view instead of hidden (review fix). An
 *    unrecognised status word is "Check" too, never OK.
 *  - vault-core: the server's status is ALWAYS `unknown` (no network path,
 *    ADR-0011, user decision "Weg A"); its version comes from a file
 *    vault-core's start hook wrote. When that recorded version AND commit
 *    equal vault-api's, both come from the same release: "OK" (the
 *    strongest signal there is without a network path; the (i) text says it
 *    was recorded at core's last start). When they differ, or cannot be
 *    compared: a neutral "Check", never red. When core never recorded one:
 *    "Not reported".
 *  - vault-dns: always `unknown` (never probed, optional): "N/A".
 *  - Every other status is shown as the server sent it. A status other than
 *    `unknown` for vault-core/vault-dns (a future server that probes them)
 *    is never overridden.
 *  - The answer is cached by the server for 60 s (`CACHE_TTL_SECONDS`), and
 *    `checked_at` says when vault-api looked. {@link checkedText} states
 *    both, so a Refresh that returns the same answer is not mistaken for a
 *    live check.
 *  - `version`/`commit` are a value, `"invalid"` or `null`. `null` reads as
 *    the dash; `"invalid"` reads "invalid" (a baked value outside the VER-1
 *    grammar), never a fabricated version.
 *
 * Android twin: app/app/src/main/java/dev/steamvault/app/ui/settings/logic/
 * AboutPresentation.kt plus the `settings_about_*` strings.
 * web/tests/about-android-twin.test.js (reads strings.xml) and
 * AboutCrossFrontendContractTest.kt (literals from this file) keep the two
 * on the same words.
 *
 * Pure: no DOM, no fetch. Covered in web/tests/about-view.test.js.
 */

import { formatAgo } from "./format.js";

/** Server-side answer cache, in seconds (api/vault_api/about.py
 * `CACHE_TTL_SECONDS`). Pinned against that constant in
 * web/tests/about-view.test.js. */
export const ABOUT_CACHE_SECONDS = 60;

/** Shown in a version/commit cell the component does not report. */
export const DASH = "—";

/** Accessible name of a {@link DASH} cell (a screen reader would otherwise
 * read "em dash" or nothing). */
export const DASH_LABEL = "Not reported";

/**
 * Display state -> what the status cell shows. `icon` is a
 * `createStatusIcon` kind (components/status-icon.js); `tone` the
 * text-colour class of the badge. Only `unreachable` uses a fault glyph and
 * the error tone: every other non-OK state is neutral, so a component that
 * is simply not checked never reads as a fault (docs/LEARNINGS.md, the
 * "cancelled" glyph entry, same class). "?" = look at the details, dash =
 * nothing to show.
 */
export const ABOUT_DISPLAY = Object.freeze({
  ok: Object.freeze({ word: "OK", icon: "cached", tone: "tx-cached" }),
  unreachable: Object.freeze({ word: "Unreachable", icon: "error", tone: "tx-error" }),
  not_in_use: Object.freeze({ word: "Not in use", icon: "notinuse", tone: "tx-cancelled" }),
  check: Object.freeze({ word: "Check", icon: "unknown", tone: "tx-cancelled" }),
  not_reported: Object.freeze({ word: "Not reported", icon: "notinuse", tone: "tx-cancelled" }),
  not_applicable: Object.freeze({ word: "N/A", icon: "notinuse", tone: "tx-cancelled" }),
});

/** Server status word -> display state, for every row without a component
 * rule. Keys are exactly the server's four words (drift-guarded). */
export const ABOUT_STATUS = Object.freeze({
  ok: "ok",
  unreachable: "unreachable",
  not_in_use: "not_in_use",
  unknown: "check",
});

/**
 * The first (i) paragraph: one plain sentence per component on what its row
 * can and cannot mean. vault-core's normally comes from {@link CORE_NOTES}
 * (by comparison outcome); its entry here is used only when a future server
 * sends a status other than `unknown` for it.
 */
export const COMPONENT_NOTES = Object.freeze({
  "vault-api": "This server. It also serves this web UI.",
  "vault-core": "Version recorded at vault-core's last start, not a live check.",
  // 90 = api/vault_api/runner_presence.py PRESENCE_STALE_SECONDS (pinned in
  // web/tests/about-view.test.js).
  "vault-runner": "OK means the runner checked in within the last 90 s.",
  steamprefill: "The SteamPrefill build that prefill jobs run with. SteamPrefill reports a version but no commit id.",
  "vault-proxy": "The egress proxy does not report a version. Its status is reachability only.",
  "vault-dns": "Optional component. vault-api does not check it, so there is no status to show.",
});

/** vault-core's first (i) paragraph, by {@link coreComparison} outcome. */
export const CORE_NOTES = Object.freeze({
  same_release:
    "Recorded at vault-core's last start: the same version and commit as vault-api, so both come from the same release. " +
    "Not a live check: vault-api has no network path to vault-core.",
  mismatch:
    "The version or commit vault-core recorded at its last start differs from vault-api's. " +
    "Usually vault-core was not restarted after an update; restart it so both run the same release.",
  not_comparable:
    "vault-core's recorded version cannot be compared with vault-api's: one of them did not report a valid version and commit.",
  not_reported: "vault-core has not recorded a version yet. It records one in the shared cache volume each time it starts.",
});

/** Added to the (i) text when a dash is shown and the component's own note
 * does not already say why. */
export const DASH_NOTE = "A dash means the component did not report this value.";

/** Components whose note already explains every dash they show. */
const NOTE_EXPLAINS_DASH = new Set(["vault-proxy", "steamprefill"]);

/** Shown instead of the table when the server has no `GET /v1/about`. */
export const ABOUT_TOO_OLD_MESSAGE =
  "This server is older than this web UI and does not report component versions. Update the server to see them here.";

/** Shown for an entry without a usable name (never "unknown"). */
export const UNNAMED_COMPONENT = "Unnamed component";

const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

/** Display state key for a server status word, without component rules: an
 * unexpected word is "check", never OK. */
function stateFor(status) {
  return has(ABOUT_STATUS, status) ? ABOUT_STATUS[status] : "check";
}

/** The presentation of a server status word, without component rules.
 * @param {unknown} status */
export function statusPresentation(status) {
  return ABOUT_DISPLAY[stateFor(status)];
}

/**
 * The version cell. `null` -> {@link DASH}, anything else verbatim
 * (including `"invalid"`). Long values wrap in CSS; the full value is also
 * the title. `missing` is true for the dash.
 * @param {unknown} version
 * @returns {{text: string, title: string | null, missing: boolean}}
 */
export function versionCell(version) {
  if (typeof version !== "string" || !version) return { text: DASH, title: null, missing: true };
  return { text: version, title: version, missing: false };
}

/**
 * The commit cell: a hex id shortened to 7 characters with the full id as
 * the title; `null` -> {@link DASH}; `"invalid"` or any non-hex value shown
 * as sent (never shortened into something that looks like a real id).
 * @param {unknown} commit
 * @returns {{text: string, title: string | null, missing: boolean}}
 */
export function commitCell(commit) {
  if (typeof commit !== "string" || !commit) return { text: DASH, title: null, missing: true };
  if (/^[0-9a-f]{8,64}$/i.test(commit)) return { text: commit.slice(0, 7), title: commit, missing: false };
  return { text: commit, title: commit, missing: false };
}

/** A version/commit that can be compared: a non-empty string other than
 * the server's `"invalid"`. */
function comparable(value) {
  return typeof value === "string" && value !== "" && value !== "invalid";
}

/**
 * vault-core's recorded build against vault-api's, from the same answer.
 * `same_release` needs version AND commit present, valid and equal on both
 * sides (commits compared case-insensitively); a missing or `"invalid"`
 * value on either side is `not_comparable` — a "dev" build on both sides
 * would otherwise read OK on the version alone.
 * @param {{version?: unknown, commit?: unknown}} core
 * @param {{version?: unknown, commit?: unknown} | null | undefined} api
 * @returns {"same_release" | "mismatch" | "not_comparable" | "not_reported"}
 */
export function coreComparison(core, api) {
  if (typeof core.version !== "string" || !core.version) return "not_reported";
  if (!api || ![core.version, core.commit, api.version, api.commit].every(comparable)) return "not_comparable";
  const same = core.version === api.version && core.commit.toLowerCase() === api.commit.toLowerCase();
  return same ? "same_release" : "mismatch";
}

/**
 * Everything one table row shows. `api` is vault-api's entry from the same
 * answer (only vault-core's row uses it).
 * @param {{name?: unknown, version?: unknown, commit?: unknown, status?: unknown, detail?: unknown}} component
 * @param {object | null} [api]
 */
export function describeComponent(component, api = null) {
  const c = component || {};
  const name = typeof c.name === "string" && c.name ? c.name : UNNAMED_COMPONENT;
  let state = stateFor(c.status);
  let note = has(COMPONENT_NOTES, name) ? COMPONENT_NOTES[name] : null;
  if (c.status === "unknown" && name === "vault-core") {
    const cmp = coreComparison(c, api);
    state = cmp === "same_release" ? "ok" : cmp === "not_reported" ? "not_reported" : "check";
    note = CORE_NOTES[cmp];
  } else if (c.status === "unknown" && name === "vault-dns") {
    state = "not_applicable";
  }
  const display = ABOUT_DISPLAY[state];
  const version = versionCell(c.version);
  const commit = commitCell(c.commit);
  const detail = typeof c.detail === "string" && c.detail.trim() ? c.detail : null;
  const dashNote = (version.missing || commit.missing) && !NOTE_EXPLAINS_DASH.has(name) ? DASH_NOTE : null;
  return {
    name,
    version,
    commit,
    state,
    statusWord: display.word,
    statusIcon: display.icon,
    statusTone: display.tone,
    note,
    dashNote,
    detail,
    /** The (i) paragraphs, in order: note, dash reason, server detail. */
    info: [note, dashNote, detail].filter((x) => x !== null),
    /** A Check row's details start open, every other row's collapsed. */
    infoOpenByDefault: state === "check",
  };
}

/**
 * Every row of an answer, vault-core compared against vault-api's entry in
 * the same list.
 * @param {object[] | null | undefined} components
 */
export function describeComponents(components) {
  if (!Array.isArray(components)) return [];
  const api = components.find((c) => c && c.name === "vault-api") || null;
  return components.map((c) => describeComponent(c, api));
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
