/**
 * Clients page presentation (WP D15c), one row per entry of
 * `GET /v1/clients/overview` (WP D15b): agent PCs, agentless addresses and
 * the server's own prefill runner (WP D15-RUNNER, `kind: "runner"`).
 * Pure: no DOM; `views/clients.js` builds the cards from these strings.
 * Unit-tested in web/tests/clients-page.test.js.
 *
 * Wording rules:
 *  - The card title is the server's `display_name` (WP D15-NAMES): the
 *    operator-set name when there is one, else the agent's PC name, else
 *    the address. Without an operator-set name an address client is named
 *    by its address: the event log carries IPs only (no reverse DNS by
 *    design), so the page does not invent one. A server older than
 *    D15-NAMES sends no `display_name`; the title then falls back to the
 *    pre-D15-NAMES rule.
 *  - When an operator-set name replaces the title, the line under it keeps
 *    what it replaced (the agent's PC name, or the address), so the machine
 *    stays recognisable.
 *  - An address client is described as "No agent" and "seen in the cache
 *    log" and nothing more: no line here may claim the address is a PC, a
 *    person or a game console.
 *  - The prefill runner (WP D15-RUNNER) is the server itself downloading;
 *    its title is the server's `display_name` ("Prefill runner") and the
 *    line under it says so. Traffic from before the runner first marked its
 *    address stays with that address (api/README.md "The prefill runner in
 *    the client figures").
 *  - "Installed" exists for agent clients only (`installed_app_count`).
 *  - A `null` the server sent (feed not `ok`) shows the dash; a real zero
 *    prints.
 */

import { formatAgo } from "./format.js";
import { isolate } from "./client-names.js";
import { UNKNOWN_APP_NAME } from "./app-names.js";
import { DASH, RANGE_PHRASES, formatRate, formatSpeed, formatTrafficBytes } from "./traffic-view.js";

/** How many recently loaded apps a card names before "+N more". */
export const RECENT_APPS_SHOWN = 3;

/** @param {object} client @returns {boolean} */
export function isAgentClient(client) {
  return !!client && client.kind === "agent";
}

/** WP D15-RUNNER. @param {object} client @returns {boolean} */
export function isRunnerClient(client) {
  return !!client && client.kind === "runner";
}

/** An agentless address (neither an agent nor the prefill runner). */
export function isAddressClient(client) {
  return !!client && !isAgentClient(client) && !isRunnerClient(client);
}

/**
 * The card's title: the server's `display_name` (an operator-set name wins),
 * else the agent's client_id, or the address.
 * @param {object} client
 * @returns {string}
 */
export function clientName(client) {
  if (typeof client.display_name === "string" && client.display_name) return client.display_name;
  if (isAgentClient(client) && typeof client.client_id === "string" && client.client_id) return client.client_id;
  if (Array.isArray(client.addresses) && client.addresses[0]) return client.addresses[0];
  const key = typeof client.key === "string" ? client.key : "";
  return key.startsWith("addr:") ? key.slice(5) : key || DASH;
}

/**
 * The line under the title: what kind of client this is and its addresses.
 * @param {object} client
 * @returns {string}
 */
export function clientKindText(client) {
  const named = typeof client.name === "string" && client.name !== "";
  const addrs = Array.isArray(client.addresses) ? client.addresses.filter(Boolean) : [];
  if (isRunnerClient(client)) {
    return addrs.length ? `This server's prefill downloads · ${addrs.join(", ")}` : "This server's prefill downloads";
  }
  if (!isAgentClient(client)) {
    return named && addrs.length ? `No agent · ${addrs.join(", ")} · seen in the cache log` : "No agent · seen in the cache log";
  }
  const agent = named && typeof client.client_id === "string" && client.client_id ? `vault-agent on ${isolate(client.client_id)}` : "vault-agent";
  return addrs.length ? `${agent} · ${addrs.join(", ")}` : `${agent} · no address in the cache log`;
}

/** Connection chip: word and chip class (the `.pchip` vocabulary). */
export function connectionChip(client) {
  // "Agent" wording only for a vault-agent client; an address has no agent.
  const agent = isAgentClient(client);
  if (client.connected === true) return { word: agent ? "Agent online" : "Loading", cls: "pchip pchip-on" };
  if (client.connected === false) return { word: agent ? "Agent offline" : "No agent", cls: "pchip pchip-off" };
  return { word: "No current data", cls: "pchip pchip-unknown" };
}

/** Name of an app in a list: the server's name, else the neutral "Unknown app" (never a bare id). */
export function appLabel(app) {
  return typeof app.name === "string" && app.name.trim() ? app.name : UNKNOWN_APP_NAME;
}

/**
 * "Loading now" line.
 * @param {object} client
 * @returns {string}
 */
export function loadingNowText(client) {
  const now = client.loading_now;
  if (!now) return `Loading now: ${DASH}`;
  if (!now.loading) return "Loading now: nothing";
  const speed = formatSpeed(now.bytes_per_second) || DASH;
  const apps = Array.isArray(now.apps) ? now.apps.map(appLabel) : [];
  if (apps.length) return `Loading now: ${speed} · ${apps.join(", ")}`;
  return `Loading now: ${speed} · content not linked to one game`;
}

/**
 * Installed games line; agent clients only (`null` for an address).
 * @param {object} client
 * @returns {string | null}
 */
export function installedText(client) {
  if (!isAgentClient(client)) return null;
  const n = client.installed_app_count;
  if (!Number.isInteger(n)) return "Installed games: not reported";
  return `${n} game${n === 1 ? "" : "s"} installed`;
}

/**
 * Bytes and hit rate over the selected range; `null` with no stats (feed
 * disabled).
 * @param {object} client
 * @param {string} range
 * @returns {string | null}
 */
export function statsText(client, range) {
  const s = client.stats;
  if (!s) return null;
  const loaded = formatTrafficBytes(s.bytes_served) || DASH;
  const rate = formatRate(s.hit_rate);
  const phrase = RANGE_PHRASES[range] || "";
  return `${loaded} loaded ${phrase}` + (rate ? ` · hit rate ${rate}` : "");
}

/**
 * The top recently loaded apps; `null` when the server sent none (feed
 * disabled) or the range has nothing.
 * @param {object} client
 * @returns {string | null}
 */
export function recentAppsText(client) {
  const apps = Array.isArray(client.recent_apps) ? client.recent_apps : null;
  if (!apps) return null;
  const parts = apps.slice(0, RECENT_APPS_SHOWN).map((a) => {
    const size = formatTrafficBytes(a.bytes_served);
    return size ? `${appLabel(a)} (${size})` : appLabel(a);
  });
  const more = apps.length - RECENT_APPS_SHOWN;
  if (more > 0) parts.push(`+${more} more`);
  const other = formatTrafficBytes(client.recent_unattributed_bytes);
  if (other && client.recent_unattributed_bytes > 0) parts.push(`${other} not linked to one game`);
  return parts.length ? `Loaded recently: ${parts.join(", ")}` : null;
}

/**
 * "Last active 4 min ago".
 * @param {object} client
 * @param {number} [nowMs]
 * @returns {string}
 */
export function lastActiveText(client, nowMs = Date.now()) {
  const ago = formatAgo(client.last_active, nowMs);
  return ago ? `Last active ${ago}` : "Last active: never";
}

/**
 * The page's sub-line: how many clients, how many connected.
 * @param {Array<object>} clients
 * @returns {string}
 */
export function clientsSummaryText(clients) {
  const list = Array.isArray(clients) ? clients : [];
  const connected = list.filter((c) => c.connected === true).length;
  return `${list.length} client${list.length === 1 ? "" : "s"} · ${connected} connected`;
}

/**
 * Summary tiles of the page (WP CLIENTS-2), computed from the merged cards
 * only: no tile without a source. "Loaded" needs at least one client with
 * statistics (feed on); "Agents" needs at least one agent client.
 * @param {Array<object>} clients
 * @param {string} range
 * @returns {Array<{key: string, label: string, value: string, detail: string}>}
 */
export function summaryTiles(clients, range) {
  const list = Array.isArray(clients) ? clients : [];
  const tiles = [];
  const connected = list.filter((c) => c.connected === true).length;
  const loading = list.filter((c) => c.loading_now && c.loading_now.loading === true).length;
  tiles.push({
    key: "connected",
    label: "Connected",
    value: `${connected} of ${list.length}`,
    detail: loading ? `${loading} loading now` : "Nothing loading now",
  });
  const withStats = list.filter((c) => c.stats && Number.isFinite(c.stats.bytes_served));
  if (withStats.length) {
    const total = withStats.reduce((sum, c) => sum + c.stats.bytes_served, 0);
    tiles.push({
      key: "loaded",
      label: "Loaded",
      value: formatTrafficBytes(total) || DASH,
      detail: RANGE_PHRASES[range] || "",
    });
  }
  const agents = list.filter((c) => isAgentClient(c));
  if (agents.length) {
    const online = agents.filter((c) => c.connected === true).length;
    tiles.push({ key: "agents", label: "Agents", value: `${online} online`, detail: `${agents.length} paired` });
  }
  return tiles;
}

/** Note under the list, for the agentless rows. */
export const AGENTLESS_NOTE =
  "Machines without vault-agent show their address and cache traffic only. A PC with vault-agent can also show up here by its address until the server can match the two.";

/** Empty list: nothing reported and nothing in the cache log. */
export const NO_CLIENTS_TEXT = "No clients to show. A machine appears here once it loads through the cache or runs vault-agent.";

// ---------------------------------------------------------------------
// One card per device (WP CLIENTS-1)
// ---------------------------------------------------------------------

/**
 * Merge the agent rows (`GET /v1/clients`) into the overview rows
 * (`GET /v1/clients/overview`) so a device shows once.
 *
 * Matching rule: an overview row of `kind: "agent"` and an agent row are the
 * same device when their `client_id` is exactly equal (the overview builds
 * its agent rows from the same reports). The address-to-agent mapping is done
 * by the server (`address_owners`), so an address row never joins an agent
 * card here: an agent's `source_addrs` can be a shared forwarding hop, and a
 * guess would fold two PCs into one. The documented leftover (an old agent
 * behind a proxy: an agent card without traffic plus an address card) stays
 * two cards.
 *
 * - client only (address, runner, or an agent row without agent data):
 *   returned with `agent: null`.
 * - both: the overview row plus `agent` (the `GET /v1/clients` row).
 * - agent only (the overview has no row for it, e.g. it is unavailable):
 *   a card built from the agent row alone, with no traffic figures (never
 *   zeros).
 *
 * Inputs are not mutated. A `null` agent list (no answer yet) leaves every
 * card as the overview sent it.
 * @param {object[] | null | undefined} overviewClients
 * @param {object[] | null | undefined} agentClients
 * @returns {object[]}
 */
export function mergeAgentData(overviewClients, agentClients) {
  const rows = Array.isArray(overviewClients) ? overviewClients : [];
  const agents = new Map();
  for (const a of Array.isArray(agentClients) ? agentClients : []) {
    if (a && typeof a.client_id === "string" && a.client_id && !agents.has(a.client_id)) agents.set(a.client_id, a);
  }
  const used = new Set();
  const merged = rows.map((row) => {
    if (!isAgentClient(row) || typeof row.client_id !== "string") return { ...row, agent: null };
    const agent = agents.get(row.client_id) || null;
    if (agent) used.add(row.client_id);
    return { ...row, agent };
  });
  for (const [id, a] of agents) {
    if (used.has(id)) continue;
    const known = a.presence === "online" || a.presence === "offline";
    merged.push({
      key: `agent:${id}`,
      kind: "agent",
      client_id: id,
      addresses: Array.isArray(a.source_addrs) ? a.source_addrs : [],
      connected: known ? a.presence === "online" : null,
      agent_presence: known ? a.presence : null,
      cache_active: null,
      last_active: a.last_reported_at || null,
      last_seen_in_cache_log: a.last_seen_in_cache_log || null,
      last_reported_at: a.last_reported_at || null,
      installed_appids: null,
      installed_app_count: Number.isInteger(a.app_count) ? a.app_count : null,
      stats: null,
      loading_now: null,
      recent_apps: null,
      recent_unattributed_bytes: null,
      name: a.name ?? null,
      name_addr: a.name_addr ?? null,
      display_name: a.display_name,
      agent: a,
    });
  }
  return merged;
}
