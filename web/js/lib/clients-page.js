/**
 * Clients page presentation (WP D15c), one row per entry of
 * `GET /v1/clients/overview` (WP D15b): agent PCs, agentless addresses and
 * the server's own prefill runner (WP D15-RUNNER, `kind: "runner"`).
 * Pure: no DOM. WP CLIENTS-4: `clientCardModel` and `summaryTiles` are
 * what the page renders (`components/client-card.js`, `views/clients.js`);
 * the one-line texts (`loadingNowText`, `statsText`, `recentAppsText`,
 * `installedText`, `connectionChip`) are the wording reference the Android
 * twin (`ClientsPage.kt`) ports and stay pinned here for it.
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
import { canRename, isolate } from "./client-names.js";
import { UNKNOWN_APP_NAME } from "./app-names.js";
import { isReleaseVersion } from "./agent-install.js";
import { DASH, RANGE_PHRASES, formatRate, formatSpeed, formatTrafficBytes } from "./traffic-view.js";
import { describeBypassClient, isRemovableClientId, presenceLine } from "./clients-view.js";

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
 * Split a formatted figure into the number and its unit for the kit's
 * `value<small>unit</small>` anatomy: "38.2 GB" -> {value "38.2", unit "GB"},
 * "75%" -> {value "75", unit "%"}, "< 1 MB" -> {value "< 1", unit "MB"}.
 * Anything else (the dash, a bare number) stays whole with no unit.
 * @param {string | null | undefined} text
 * @returns {{value: string, unit: string}}
 */
export function splitValueUnit(text) {
  if (typeof text !== "string" || !text) return { value: DASH, unit: "" };
  const pct = /^(\d+)%$/.exec(text);
  if (pct) return { value: pct[1], unit: "%" };
  const m = /^(<?\s?[\d.]+)\s([A-Za-z/]+)$/.exec(text);
  return m ? { value: m[1], unit: m[2] } : { value: text, unit: "" };
}

/**
 * Request hit rate over several clients' statistics: the sum of hits over
 * the sum of hits and misses (the same request-based rule as the server's
 * per-client `hit_rate`), `null` without a request to rate.
 * @param {Array<object>} clients
 * @returns {number | null}
 */
export function combinedHitRate(clients) {
  let hits = 0;
  let misses = 0;
  for (const c of clients) {
    const s = c && c.stats;
    if (!s) continue;
    if (Number.isFinite(s.hits) && s.hits > 0) hits += s.hits;
    if (Number.isFinite(s.misses) && s.misses > 0) misses += s.misses;
  }
  return hits + misses > 0 ? hits / (hits + misses) : null;
}

/** Hit rate as a whole percentage for a meter width, or null. */
function ratePercent(rate) {
  if (typeof rate !== "number" || !Number.isFinite(rate) || rate < 0) return null;
  return Math.round(Math.min(rate, 1) * 100);
}

/**
 * The four summary tiles of the page (WP CLIENTS-4, kit.html / clients.html:
 * Connected, Loaded, Hit rate with meter, Agents with the outdated pill),
 * computed from the merged cards only. The set is fixed; a tile without a
 * source shows the dash and says why (never a zero it cannot back).
 * @param {Array<object>} clients
 * @param {string} range
 * @param {unknown} [serverVersion] needed for the outdated count
 * @returns {Array<{key: string, label: string, value: string, unit: string, detail: string,
 *   meter?: number, outdated?: number | null, hi?: boolean}>}
 */
export function summaryTiles(clients, range, serverVersion) {
  const list = Array.isArray(clients) ? clients : [];
  const tiles = [];
  const connected = list.filter((c) => c.connected === true).length;
  const loading = list.filter((c) => c.loading_now && c.loading_now.loading === true).length;
  tiles.push({
    key: "connected",
    label: "Connected",
    value: String(connected),
    unit: `of ${list.length}`,
    detail: loading ? `${loading} loading now` : "Nothing loading now",
  });
  const withStats = list.filter((c) => c.stats && Number.isFinite(c.stats.bytes_served));
  const phrase = RANGE_PHRASES[range] || "";
  if (withStats.length) {
    const total = withStats.reduce((sum, c) => sum + c.stats.bytes_served, 0);
    tiles.push({ key: "loaded", label: "Loaded", ...splitValueUnit(formatTrafficBytes(total)), detail: phrase });
  } else {
    tiles.push({ key: "loaded", label: "Loaded", value: DASH, unit: "", detail: NO_STATS_TEXT });
  }
  const rate = combinedHitRate(withStats);
  const pct = ratePercent(rate);
  if (pct !== null) {
    tiles.push({ key: "hitrate", label: "Hit rate", value: String(pct), unit: "%", detail: "from the cache", meter: pct });
  } else {
    tiles.push({
      key: "hitrate",
      label: "Hit rate",
      value: DASH,
      unit: "",
      detail: withStats.length ? "No cache requests yet" : NO_STATS_TEXT,
    });
  }
  const agents = list.filter((c) => isAgentClient(c));
  const online = agents.filter((c) => c.connected === true).length;
  const serverKnown = isReleaseVersion(typeof serverVersion === "string" ? serverVersion.trim() : "");
  const outdated = serverKnown ? agents.filter((c) => outdatedAgentInfo(c, serverVersion)).length : null;
  let detail;
  if (!agents.length) detail = "No agent paired";
  else if (outdated === null) detail = `${agents.length} paired · server version not known`;
  else if (outdated === 0) detail = `${agents.length} paired · all match the server`;
  else detail = `${agents.length} paired`;
  tiles.push({ key: "agents", label: "Agents", value: String(online), unit: "online", detail, outdated, hi: !!outdated });
  return tiles;
}

/** Pill text of the Agents tile for `n` outdated agents. */
export function outdatedAgentsPillText(n) {
  return `${n} outdated`;
}

/**
 * WP CLIENTS-3 (Weg A, frontend only): an agent is outdated when its reported
 * version differs from the server's. "Differs", not "older": a newer agent is
 * flagged too, because that pair is untested. A version that is missing or
 * outside the release grammar (`0.1.0`, `0.1.0-rc19.3`; not `dev`) is unknown
 * and never warns.
 * @param {unknown} agentVersion
 * @param {unknown} serverVersion
 * @returns {boolean}
 */
export function isAgentOutdated(agentVersion, serverVersion) {
  const a = typeof agentVersion === "string" ? agentVersion.trim() : "";
  const s = typeof serverVersion === "string" ? serverVersion.trim() : "";
  return isReleaseVersion(a) && isReleaseVersion(s) && a !== s;
}

/**
 * The version pair of an outdated agent card, else `null`.
 * @param {object} client a merged card (`agent` = the `GET /v1/clients` row)
 * @param {unknown} serverVersion
 * @returns {{agent: string, server: string} | null}
 */
export function outdatedAgentInfo(client, serverVersion) {
  const agent = client && client.agent ? client.agent.agent_version : null;
  if (!isAgentOutdated(agent, serverVersion)) return null;
  return { agent: agent.trim(), server: serverVersion.trim() };
}

/** Wording of the outdated-agent warning on a card. */
export function outdatedAgentText(info) {
  return `Agent ${info.agent} · server ${info.server}. Update vault-agent on this PC to match the server.`;
}

/** A figure or list the server sent no statistics for (feed disabled). */
export const NO_STATS_TEXT = "No traffic statistics";

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

// ---------------------------------------------------------------------
// Three-zone card model (WP CLIENTS-4, kit.html "Clients card, three zones")
// ---------------------------------------------------------------------

/**
 * Status pill of a card. Agent: "Online"/"Offline" from the server's
 * presence verdict (`agent_presence`, else `connected`), never computed
 * here. Runner and address: "Downloading" while the cache sees them load
 * (kit.html's runner card), else "Online"/"Offline" from `connected`
 * (cache activity within the server's active window). Null: the server
 * has no current data (feed not `ok`).
 * @param {object} client
 * @returns {{kind: string, text: string, tone?: string | null}}
 */
export function connectionPill(client) {
  if (!isAgentClient(client) && client.loading_now && client.loading_now.loading === true) {
    return { kind: "running", text: "Downloading" };
  }
  let on = client.connected;
  if (isAgentClient(client) && (client.agent_presence === "online" || client.agent_presence === "offline")) {
    on = client.agent_presence === "online";
  }
  if (on === true) return { kind: "cached", text: "Online" };
  if (on === false) return { kind: "notinuse", text: "Offline" };
  return { kind: "unknown", text: "No current data" };
}

/** Text of the dashed box when nothing loads, and when nothing is known. */
export const IDLE_TEXT = "Loading nothing right now";
export const NOW_UNKNOWN_TEXT = `Loading now: ${DASH}`;

/**
 * Zone 2's "now" box: the game(s) and speed while loading, else the dashed
 * idle box (with the dash when the feed has no current data).
 * @param {object} client
 * @returns {{loading: true, title: string, sub: string, speed: string} | {loading: false, text: string}}
 */
export function nowBox(client) {
  const now = client.loading_now;
  if (!now) return { loading: false, text: NOW_UNKNOWN_TEXT };
  if (!now.loading) return { loading: false, text: IDLE_TEXT };
  const apps = Array.isArray(now.apps) ? now.apps.map(appLabel) : [];
  return {
    loading: true,
    title: apps.length ? apps.join(", ") : "Content not linked to one game",
    sub: "Loading into the cache",
    speed: formatSpeed(now.bytes_per_second) || DASH,
  };
}

/**
 * Zone 2's three figures. Agent: Installed games, Loaded, Hit rate.
 * Runner and address (no installed list): Loaded, Hit rate, Unlinked (bytes
 * not linked to one game), as kit.html's runner card. A figure the server
 * did not send is the dash, never a zero.
 * @param {object} client
 * @returns {Array<{key: string, label: string, value: string, unit: string, meter?: number}>}
 */
export function cardStats(client) {
  const s = client.stats || null;
  const loaded = { key: "loaded", label: "Loaded", ...splitValueUnit(s ? formatTrafficBytes(s.bytes_served) : null) };
  const pct = s ? ratePercent(s.hit_rate) : null;
  const rate = pct === null ? { key: "hitrate", label: "Hit rate", value: DASH, unit: "" } : { key: "hitrate", label: "Hit rate", value: String(pct), unit: "%", meter: pct };
  if (isAgentClient(client)) {
    const n = client.installed_app_count;
    const installed = Number.isInteger(n)
      ? { key: "installed", label: "Installed", value: String(n), unit: n === 1 ? "game" : "games" }
      : { key: "installed", label: "Installed", value: DASH, unit: "" };
    return [installed, loaded, rate];
  }
  const unlinked = { key: "unlinked", label: "Unlinked", ...splitValueUnit(formatTrafficBytes(client.recent_unattributed_bytes)) };
  return [loaded, rate, unlinked];
}

/**
 * Zone 3: the top recently loaded games as chips plus "+N more"; `null`
 * when the server sent no list (feed disabled).
 * @param {object} client
 * @returns {{chips: Array<{name: string, size: string | null}>, more: number} | null}
 */
export function recentChips(client) {
  const apps = Array.isArray(client.recent_apps) ? client.recent_apps : null;
  if (!apps) return null;
  return {
    chips: apps.slice(0, RECENT_APPS_SHOWN).map((a) => ({ name: appLabel(a), size: formatTrafficBytes(a.bytes_served) })),
    more: Math.max(0, apps.length - RECENT_APPS_SHOWN),
  };
}

/** "+2 more" chip text. */
export function moreChipText(n) {
  return `+${n} more`;
}

/** Zone 3 with an empty list. */
export function noRecentText(range) {
  const phrase = RANGE_PHRASES[range];
  return phrase ? `Nothing loaded ${phrase}` : "Nothing loaded";
}

/**
 * The agent health row (agent data only): "Healthy" or the careful
 * "Bypassing the cache" observation, with the server's presence line.
 * Verdicts are read as sent (lib/clients-view.js), never recomputed.
 * @param {object | null} agent the `GET /v1/clients` row
 * @param {number} nowMs
 * @returns {{bypass: boolean, head: string, text: string} | null}
 */
export function agentHealth(agent, nowMs) {
  if (!agent) return null;
  if (agent.bypass_suspected) return { bypass: true, head: "Bypassing the cache", text: describeBypassClient(agent) };
  return { bypass: false, head: "Healthy", text: presenceLine(agent, nowMs) };
}

/** Footer note for bytes of an agent card not linked to one game (runner
 * and address cards show them as the Unlinked figure instead). */
export function unlinkedNote(client) {
  if (!isAgentClient(client)) return null;
  const b = client.recent_unattributed_bytes;
  const text = formatTrafficBytes(b);
  return text && b > 0 ? `${text} not linked to a game` : null;
}

/** Heading of the outdated-agent box ("differs", not "older": CLIENTS-3). */
export const OUTDATED_BOX_TITLE = "Agent version differs from the server";

/**
 * Everything one card shows, as data (`components/client-card.js` renders
 * it). Pure: no DOM.
 * @param {object} client a merged card (`mergeAgentData`)
 * @param {{range: string, serverVersion?: unknown, nowMs?: number, removeError?: string | null}} ctx
 *   `removeError`: the inline error of a failed Remove, kept by the view.
 */
export function clientCardModel(client, { range, serverVersion = null, nowMs = Date.now(), removeError = null } = {}) {
  const agent = client.agent || null;
  const kind = isAgentClient(client) ? "agent" : isRunnerClient(client) ? "runner" : "address";
  const outdated = outdatedAgentInfo(client, serverVersion);
  const removeId = agent && typeof agent.client_id === "string" ? agent.client_id : null;
  return {
    key: String(client.key),
    kind,
    name: clientName(client),
    meta: clientKindText(client),
    pill: connectionPill(client),
    outdated: outdated ? { ...outdated, text: outdatedAgentText(outdated) } : null,
    health: agentHealth(agent, nowMs),
    now: nowBox(client),
    stats: cardStats(client),
    recent: recentChips(client),
    noRecent: noRecentText(range),
    lastActive: lastActiveText(client, nowMs),
    unlinked: unlinkedNote(client),
    rename: canRename(client),
    removeId: removeId && isRemovableClientId(removeId) ? removeId : null,
    removeBlocked: !!agent && !(removeId && isRemovableClientId(removeId)),
    // The error line exists on every agent card (hidden when empty), so a
    // later failure has a stable role=alert node to land in.
    removeError: agent ? removeError || "" : null,
  };
}
