/**
 * Clients sheet presentation logic (WP 4a.7).
 *
 * Pure transforms over `GET /v1/clients` (api/vault_api/routers/clients.py's
 * `ClientOut`) for the clients sheet: which section a client belongs in
 * (mockup round 5's "Bypassing" / "Healthy" headings), the stats line for a
 * healthy client, and the careful, not-accusing wording for a
 * bypass-suspected one.
 *
 * **No separate hostname field exists.** `ClientOut.client_id` already IS
 * the human-facing label — `api/vault_api/agent_reports.py`: "a client id
 * is an operator-set label (hostname, 'steam-deck', ...)". The WP brief's
 * "address, hostname if present" maps onto `source_addrs` being the field
 * that CAN legitimately be empty (reports stored before schema v9 never
 * recorded a source address — same module, `ClientOut.source_addrs`'s own
 * docstring), not onto the id itself.
 *
 * **Wording keeps the backend's own "fails toward NOT accusing" posture**
 * (routers/clients.py's module docstring: "a false positive here sends an
 * operator hunting a network fault that does not exist"). A bypass-
 * suspected row states what was OBSERVED (games reported, nothing seen in
 * the cache log) and lists plausible innocent causes — never a verdict like
 * "your DNS is broken".
 *
 * **Presence (WP WEB-FEAT-3) is READ, never computed here.** `GET
 * /v1/clients` carries `presence: "online" | "offline"` (WP AGENT-FEAT-1,
 * `agent_reports.presence` on the server, "the one place the rule lives").
 * {@link presenceOf} only reads that field; nothing in this module compares
 * `last_reported_at` or `offline_after` against a clock to decide online vs
 * offline (docs/LEARNINGS.md: two call sites computing the same predicate
 * diverge). `last_reported_at` is used for the "last seen ... ago" WORDS
 * only. A server older than AGENT-FEAT-1 sends no `presence` at all: that
 * reads "presence unknown", not a guess.
 *
 * Pure only — no DOM, no fetch. Covered in web/tests/clients-view.test.js
 * and web/tests/clients-presence.test.js.
 */

import { formatBytesGB, formatAgo } from "./format.js";

/**
 * @param {object[] | null | undefined} clients `GET /v1/clients` snapshot.
 * @returns {{bypassing: object[], healthy: object[]}} order preserved from
 *   the input within each bucket.
 */
export function partitionClients(clients) {
  const list = Array.isArray(clients) ? clients : [];
  return {
    bypassing: list.filter((c) => !!(c && c.bypass_suspected)),
    healthy: list.filter((c) => !(c && c.bypass_suspected)),
  };
}

/**
 * hits/(hits+misses) as a rounded whole-number percentage, or `null` when
 * there have been zero cache requests to compute a rate from — never
 * fabricate "0%" for "no data yet" (same "nothing honest to print" posture
 * as `lib/format.js`'s `formatBytesGB`/`formatTimestamp`). Non-finite or
 * missing counters are treated as 0 rather than propagating `NaN` into the
 * UI (LEARNINGS "Parsers" section: never trust a field's shape blindly).
 * @param {{cache_hits?: number, cache_misses?: number} | null | undefined} client
 * @returns {number | null}
 */
export function hitRatePercent(client) {
  const hits = safeCount(client && client.cache_hits);
  const misses = safeCount(client && client.cache_misses);
  const total = hits + misses;
  if (total <= 0) return null;
  return Math.round((hits / total) * 100);
}

function safeCount(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

/**
 * The addresses line for a client row. Empty for a client whose retained
 * reports predate schema v9 (`ClientOut.source_addrs` is `[]` then) — an
 * honest "no known address" rather than a blank space.
 * @param {{source_addrs?: string[]} | null | undefined} client
 * @returns {string}
 */
export function addressesText(client) {
  const addrs = client && Array.isArray(client.source_addrs) ? client.source_addrs : [];
  return addrs.length ? addrs.join(", ") : "no known address";
}

/** One-line stats summary for a HEALTHY client's row. */
export function describeHealthyClient(client) {
  const games = gamesReportedText(client);
  const bytes = formatBytesGB(client && client.bytes_served);
  const bytesText = bytes ? `${bytes} served` : "nothing served yet";
  const rate = hitRatePercent(client);
  const rateText = rate == null ? "no cache requests yet" : `${rate}% hit`;
  return `${games} · ${bytesText} · ${rateText}`;
}

/** One-line summary for a BYPASS-SUSPECTED client's row — states what was
 * observed, never a cause (that part is `BYPASS_EXPLANATION` below, shown
 * once per section rather than repeated per row). */
export function describeBypassClient(client) {
  const games = gamesReportedText(client);
  return `${games} · none of its downloads have reached the cache recently`;
}

function gamesReportedText(client) {
  const count = client && typeof client.app_count === "number" ? client.app_count : null;
  return count == null ? "game count unknown" : `${count} game${count === 1 ? "" : "s"} reported`;
}

/** Shared explanatory hint shown under the "Bypassing" section — general
 * possible causes, never a specific accusation about any one client
 * (mirrors the backend's own disqualification-chain design, routers/
 * clients.py). */
export const BYPASS_EXPLANATION =
  "This does not necessarily mean anything is wrong. Common causes: " +
  "DNS-over-HTTPS in the browser or OS, the machine resolving Steam's CDN " +
  "over IPv6 (bypassing vault-dns), or simply nothing downloaded yet in the " +
  "current reporting window.";

/**
 * The bypass banner's message text (empty string when nobody is
 * suspected — callers should hide the banner in that case, see
 * `lib/bypass-banner.js`'s `bypassBannerVisible`). Singular/plural wording
 * are both literal-pinned in tests.
 * @param {object[] | null | undefined} clients
 * @returns {string}
 */
export function bypassBannerText(clients) {
  const { bypassing } = partitionClients(clients);
  if (!bypassing.length) return "";
  if (bypassing.length === 1) {
    return `Client ${bypassing[0].client_id} is bypassing the cache — check its DNS.`;
  }
  return `${bypassing.length} clients are bypassing the cache — check their DNS.`;
}

// ---------------------------------------------------------------------
// Presence, last seen, agent version (WP WEB-FEAT-3)
// ---------------------------------------------------------------------

/** Visible words for the server's two presence values. */
export const PRESENCE_WORD = Object.freeze({ online: "Online", offline: "Offline" });

/** Chip word for a client whose server sent no presence (older than WP
 * AGENT-FEAT-1). */
export const PRESENCE_UNKNOWN_WORD = "Presence unknown";

/**
 * The server's `presence` field, verbatim when it is one of the two
 * documented words, otherwise `null` ("not reported"). MUTATION TARGET: this
 * must never look at `last_reported_at`/`offline_after` — a client the
 * server calls online stays online here even if its timestamp looks old to
 * this browser's clock, and vice versa.
 * @param {{presence?: unknown} | null | undefined} client
 * @returns {"online" | "offline" | null}
 */
export function presenceOf(client) {
  const p = client ? client.presence : undefined;
  return p === "online" || p === "offline" ? p : null;
}

/** The presence chip's word: "Online", "Offline" or "Presence unknown". */
export function presenceWord(client) {
  const p = presenceOf(client);
  return p ? PRESENCE_WORD[p] : PRESENCE_UNKNOWN_WORD;
}

/**
 * "last seen 4 min ago" from `last_reported_at` — words only, never a
 * presence decision (see the module header).
 * @param {{last_reported_at?: unknown} | null | undefined} client
 * @param {number} [nowMs]
 */
export function lastSeenText(client, nowMs = Date.now()) {
  const ago = formatAgo(client ? client.last_reported_at : null, nowMs);
  return ago ? `last seen ${ago}` : "last seen: unknown";
}

/**
 * "agent 0.1.0", or "version unknown" for `agent_version: null` (an agent
 * from before AGENT-FEAT-1, or a server that does not send the field). The
 * value is untrusted text; the caller assigns it with textContent.
 * @param {{agent_version?: unknown} | null | undefined} client
 */
export function agentVersionText(client) {
  const v = client ? client.agent_version : null;
  return typeof v === "string" && v.trim() ? `agent ${v.trim()}` : "version unknown";
}

/** The row's second line: "last seen 4 min ago · agent 0.1.0". The game
 * count stays in the stats line ({@link describeHealthyClient}). */
export function presenceLine(client, nowMs = Date.now()) {
  return `${lastSeenText(client, nowMs)} · ${agentVersionText(client)}`;
}

/**
 * Counts by the server's presence field. `null` when there is no list yet
 * (no poll landed): nothing honest to count.
 * @param {object[] | null | undefined} clients
 * @returns {{online: number, offline: number, unknown: number, total: number} | null}
 */
export function agentsSummary(clients) {
  if (!Array.isArray(clients)) return null;
  const counts = { online: 0, offline: 0, unknown: 0, total: clients.length };
  for (const c of clients) {
    const p = presenceOf(c);
    if (p) counts[p] += 1;
    else counts.unknown += 1;
  }
  return counts;
}

/**
 * "Agents: 1 online, 1 offline" — shared by the Settings "PCs (agents)"
 * section, the About section and the PCs sheet. `null` before the first
 * `GET /v1/clients` answer.
 * @param {object[] | null | undefined} clients
 * @returns {string | null}
 */
export function agentsSummaryText(clients) {
  const s = agentsSummary(clients);
  if (!s) return null;
  if (s.total === 0) return "Agents: none have reported yet";
  const base = `Agents: ${s.online} online, ${s.offline} offline`;
  return s.unknown > 0 ? `${base}, ${s.unknown} without presence (server older than this web UI)` : base;
}

// ---------------------------------------------------------------------
// Remove a PC (WP WEB-FEAT-4)
// ---------------------------------------------------------------------
//
// `DELETE /v1/clients/{client_id}` (WP AG-1, api/vault_api/routers/
// clients.py + agent_reports.delete_client) removes exactly two things for
// that id: every stored report (`agent_reports`: the installed-games lists
// the agent sent, the table that makes a PC appear in the list at all) and
// its stored bypass verdict (`client_bypass_state`). It does NOT touch
// cached games, jobs, the depot mapping or the per-address cache traffic
// statistics. It is not a ban: `POST /v1/agent/installed` stores a report
// for any valid client_id without checking that it exists, so a still-
// running agent's next report lists the PC again with a fresh history
// (api/tests/test_clients_api.py, "reappears cleanly on the next report").
// The confirm text below says exactly that, nothing stronger.

/**
 * Whether the server's remove route can address this client_id at all.
 * Measured against the real router (TestClient, 2026-10-04): a client_id
 * containing "/" is a valid report key, but Starlette decodes `%2F` before
 * routing, so `DELETE /v1/clients/a%2Fb` never reaches the handler and
 * answers 404 while the PC stays listed. Every other printable character
 * tried (space, ?, #, %, +, &, non-ASCII) round-trips through
 * `encodeURIComponent`. The remove flow reads a 404 as "already gone", so
 * offering the button for such an id would report a success that did not
 * happen; the row says why instead ({@link UNREMOVABLE_SLASH_NOTE}).
 * The round-trip was measured against TestClient only, not through a
 * production reverse proxy; a proxy that rejects an encoded character
 * (e.g. a 400) lands on the inline-error path, never on "removed".
 * MUTATION TARGET.
 * @param {unknown} clientId
 * @returns {boolean}
 */
export function isRemovableClientId(clientId) {
  return typeof clientId === "string" && clientId.length > 0 && !clientId.includes("/");
}

/** Start of the 404 detail `DELETE /v1/clients/{client_id}`'s own handler
 * raises (`f"Unknown client_id {client_id!r}"`, routers/clients.py; pinned
 * in web/tests/clients-remove.test.js). */
export const CLIENT_GONE_DETAIL_PREFIX = "Unknown client_id";

/**
 * Whether a failed remove means "this PC is already gone": a 404 whose
 * detail is the handler's own text. Any other 404 (an unknown route on a
 * server older than AG-1, a proxy's 404 page) is a real error and is shown
 * on the row. MUTATION TARGET.
 * @param {unknown} err ApiError from api.deleteClient
 */
export function isClientAlreadyGone(err) {
  return (
    !!err &&
    err.status === 404 &&
    typeof err.detail === "string" &&
    err.detail.startsWith(CLIENT_GONE_DETAIL_PREFIX)
  );
}

/** Shown on a row whose id contains "/" instead of the Remove button. */
export const UNREMOVABLE_SLASH_NOTE =
  'Cannot be removed here: the name contains "/", which the server\'s remove request cannot address.';

/** Confirm dialog title for one PC. */
export function removeConfirmTitle(clientId) {
  return `Remove ${clientId}?`;
}

/** Confirm dialog: what is deleted (the endpoint's two tables) and what stays. */
export const REMOVE_WHAT_TEXT =
  "This deletes the reports this PC's agent sent (its installed-games lists) and its bypass status. " +
  "Cached games, downloads and cache statistics stay.";

/** Confirm dialog: the "not a ban" consequence, as the server behaves. */
export const REMOVE_REREGISTER_TEXT =
  "If vault-agent still runs on this PC, its next report adds the PC back with a fresh history. " +
  "To retire the PC for good, stop or uninstall the agent there first.";

/** Toast after a successful remove (204, or 404 = already gone). */
export function removedToastText(clientId) {
  return `${clientId} removed from the list.`;
}

/** Inline error line on the row when the remove failed. */
export function removeErrorText(clientId, reason) {
  return `Could not remove ${clientId}: ${reason}`;
}
