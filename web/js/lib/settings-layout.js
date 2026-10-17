/**
 * Settings page layout and wording (WP D16-WEB = D16a-2 + D16b-2, rc18).
 *
 * Pure: no DOM, no fetch. `views/settings.js` builds the page from this.
 *
 * Layout (docs/handover/d16-settings-audit.md 1.3, D16a table sign-off
 * 2026-10-14; rc20 mockup docs/design/rc20/settings.html): a left section
 * navigation shows one section at a time; the normal sections carry only
 * the rows the API marks `expert: false`; every `expert: true` row sits in
 * the Expert section, which is only reachable while the "Show expert
 * settings" switch is on, split into three sub-groups. Placement is by KEY for the
 * known keys and by the API's `expert` flag for the rest, so a key the API
 * adds later still shows up (in Housekeeping or Other), never silently not
 * at all.
 *
 * Wording rule (audit 1.2): state stays visible, explanation goes behind
 * the (i) disclosure. `FIELD_META[key].help` is the explanation.
 */

/**
 * Sections in page order. `nav` is the navigation label, `title` the panel
 * heading, `blurb` the line under it. `form: true` = carries /v1/settings
 * fields.
 */
export const SECTIONS = Object.freeze([
  { id: "vault", nav: "Vault & connection", title: "Vault & connection", blurb: "Name of this vault and the account it is connected to.", form: true },
  { id: "window", nav: "Download window", title: "Download window & updates", blurb: "When the scheduled sweep may start downloads.", form: true },
  { id: "disk", nav: "Disk & cleanup", title: "Disk & cleanup", blurb: "What happens to old chunks after a prefill.", form: true },
  { id: "notify", nav: "Notifications", title: "Notifications", blurb: "Webhook calls for job and cache events.", form: true },
  { id: "other", nav: "Other settings", title: "Other settings", blurb: "Settings without a named place on this page.", form: true },
  { id: "steam", nav: "Steam", title: "Steam", blurb: "Identity used to list the games you own.", form: false },
  { id: "about", nav: "About", title: "About", blurb: "Component versions and their status.", form: false },
  { id: "expert", nav: "Expert settings", title: "Expert settings", blurb: "Bandwidth, housekeeping and debug. Changing connection caps asks for confirmation.", form: true },
]);

/** The section shown first. */
export const DEFAULT_SECTION = "vault";

/** The line under the page title (WP SETTINGS-2, rc20 settings.html). */
export const PAGE_SUBLINE = "Vault, Downloads, Disk, Notifications, Steam";

/**
 * Field-card titles (WP SETTINGS-2, rc20 settings.html + kit.html "field
 * card"): every panel is its h2 + blurb and one `.fcard` per group. A form
 * section's rows all go into its one card, so a key that does not strictly
 * fit the title stays in its section's card (the event-log rotation size in
 * "Garbage collection", the bypass warning in "Webhook") — placement is
 * `placeEntries`'s, unchanged. The Expert cards are `EXPERT_GROUPS`' titles.
 */
export const CARD_TITLES = Object.freeze({
  vault: "Identity",
  connection: "Connection",
  window: "Schedule",
  disk: "Garbage collection",
  notify: "Webhook",
  other: "Other settings",
  steamIdentity: "Steam identity",
  steamLibrary: "Steam library",
});

/** Label of the "Last sweep" grid field in the Schedule card. */
export const LAST_SWEEP_LABEL = "Last sweep";

/** Normal (non-expert) keys -> section. */
const NORMAL_PLACEMENT = Object.freeze({
  vault_name: "vault",
  schedule_window: "window",
  auto_gc: "disk",
  event_log_rotate_bytes: "disk",
  webhook_url: "notify",
  webhook_events: "notify",
  webhook_timeout_seconds: "notify",
  bypass_window_days: "notify",
});

/** Expert sub-groups (D16a sign-off: bandwidth & connections,
 * housekeeping, debug), each with its keys in display order. */
export const EXPERT_GROUPS = Object.freeze([
  {
    id: "bandwidth",
    title: "Bandwidth & connections",
    keys: [
      "upstream_rate",
      "upstream_max_conns",
      "prefill_max_threads",
      "prefill_timeout_seconds",
      "tls_client_max_conns",
      "tls_max_conns",
    ],
  },
  {
    id: "housekeeping",
    title: "Housekeeping",
    keys: [
      "schedule_interval_minutes",
      "schedule_client_stale_days",
      "sweep_include_cached",
      "gc_grace_days",
      "manifest_keep",
      "agent_report_keep",
      "client_stats_keep",
      "event_sweep_interval_minutes",
      "miss_trigger_cooldown_minutes",
      "miss_trigger_max_per_sweep",
      "manifest_oracle_timeout",
    ],
  },
  {
    id: "debug",
    title: "Debug",
    keys: ["upstream_edge", "upstream_pool_hosts", "resolver", "log_level"],
  },
]);

/** Keys the view renders itself outside the generic field list. */
const HANDLED_ELSEWHERE = new Set(["steam_library_steamid"]);

/**
 * Split a `GET /v1/settings` list into the page's places.
 *
 * - Env-only rows show only when the layout names them (the webhook URL);
 *   the path/privacy rows stay off the page, as before D16.
 * - An `expert: true` row goes to its named expert group, an unknown one to
 *   Housekeeping; an unknown non-expert row goes to "other".
 * - Groups and sections keep the layout's key order, unknown keys after.
 *
 * @param {Array<{key: string, expert?: boolean, env_only?: boolean}>} entries
 * @returns {{sections: Record<string, object[]>, expert: Record<string, object[]>}}
 */
export function placeEntries(entries) {
  const sections = {};
  const expert = {};
  for (const s of SECTIONS) sections[s.id] = [];
  for (const g of EXPERT_GROUPS) expert[g.id] = [];
  const order = new Map();
  Object.keys(NORMAL_PLACEMENT).forEach((k, i) => order.set(k, i));
  for (const g of EXPERT_GROUPS) g.keys.forEach((k, i) => order.set(k, i));
  for (const entry of entries || []) {
    if (!entry || HANDLED_ELSEWHERE.has(entry.key)) continue;
    const named = NORMAL_PLACEMENT[entry.key];
    if (entry.env_only) {
      if (named) sections[named].push(entry);
      continue;
    }
    if (entry.expert === true) {
      const group = EXPERT_GROUPS.find((g) => g.keys.includes(entry.key));
      expert[group ? group.id : "housekeeping"].push(entry);
      continue;
    }
    sections[named || "other"].push(entry);
  }
  const rank = (e) => (order.has(e.key) ? order.get(e.key) : 1000);
  for (const list of [...Object.values(sections), ...Object.values(expert)]) {
    list.sort((a, b) => rank(a) - rank(b));
  }
  return { sections, expert };
}

/**
 * Labels, the (i) explanation and the control kind per key. A key without
 * an entry here still renders (label = its key, help = the API's grammar).
 */
export const FIELD_META = Object.freeze({
  vault_name: {
    label: "Vault name",
    placeholder: "e.g. vault-01",
    help: "Shown in the side rail and sent as vault_name with every webhook.",
  },
  schedule_window: {
    label: "Download window",
    placeholder: "03:00-07:00, or off to disable",
    help:
      "One window for automatic downloads and bandwidth. Inside it, automatic downloads run at full speed. " +
      "Outside it, no automatic downloads start and the upstream (WAN) rate cap applies. Overnight windows are allowed " +
      "(22:00-06:00). off (or an empty field) turns the scheduler off; the upstream rate cap then applies around the clock. " +
      "Times are in the server's time zone. Reset goes back to the default window.",
  },
  schedule_interval_minutes: {
    label: "Sweep interval (minutes)",
    help: "Minutes between two scheduled sweeps inside the download window.",
  },
  schedule_client_stale_days: {
    label: "Client staleness (days)",
    help: "Installed-games reports older than this are ignored by the sweep.",
  },
  sweep_include_cached: {
    label: "Include cached games in the sweep",
    kind: "segs",
    options: [
      ["false", "Off"],
      ["true", "On"],
    ],
    help: "When on, the sweep also refreshes every game that already has content on disk, not only games a PC agent reports as installed.",
  },
  auto_gc: {
    label: "Auto-GC after a prefill",
    kind: "segs",
    options: [
      ["off", "Off"],
      ["dry-run", "Dry run"],
      ["execute", "Execute"],
    ],
    help:
      "Garbage collection after each prefill. Dry run only reports what it would delete; Execute deletes chunks " +
      "no game references any more, once the GC grace period has passed.",
  },
  gc_grace_days: {
    label: "GC grace period (days)",
    help: "Unreferenced chunks stay protected this many days before GC may delete them.",
  },
  manifest_keep: { label: "Manifests kept per depot", help: "Archived manifests kept per depot." },
  agent_report_keep: { label: "Reports kept per PC", help: "Installed-games snapshots kept per agent." },
  client_stats_keep: { label: "Client statistics rows kept", help: "Statistics rows kept per client." },
  event_sweep_interval_minutes: {
    label: "Event-log sweep interval (minutes)",
    help: "How often vault-api reads vault-core's event log (cache misses, client traffic).",
  },
  miss_trigger_cooldown_minutes: {
    label: "Cache-miss trigger cooldown (minutes)",
    help: "After a cache miss queued a prefill for a game, wait this long before the next one for that game. 0 turns the trigger off.",
  },
  miss_trigger_max_per_sweep: {
    label: "Cache-miss prefills per sweep",
    help: "At most this many prefill jobs one event-log sweep may queue from cache misses.",
  },
  bypass_window_days: {
    label: "Bypass warning after (days)",
    help: "A PC with no traffic through the cache for this many days is flagged as \"bypass suspected\".",
  },
  prefill_max_threads: {
    label: "Prefill threads per job",
    help: "SteamPrefill download threads per job. Must not exceed the upstream connection cap.",
  },
  prefill_timeout_seconds: { label: "Prefill timeout (seconds)", help: "Time limit for one prefill job." },
  manifest_oracle_timeout: {
    label: "Update-check timeout (seconds)",
    help: "Seconds per request to the manifest oracle, the service the update check asks.",
  },
  webhook_url: {
    label: "Webhook URL",
    help: "Set only in the environment (VAULT_WEBHOOK_URL). Shown masked, because the URL can carry credentials.",
  },
  webhook_events: {
    label: "Webhook events",
    kind: "events",
    options: [
      ["job.done", "Job finished"],
      ["job.error", "Job failed"],
      ["job.cancelled", "Job cancelled"],
      ["client.bypass_suspected", "Cache bypass suspected"],
      ["client.bypass_resolved", "Cache bypass resolved"],
    ],
    help: "Which events the webhook delivers.",
  },
  webhook_timeout_seconds: { label: "Webhook timeout (seconds)", help: "Seconds per webhook delivery attempt." },
  upstream_rate: {
    label: "Upstream (WAN) rate cap",
    placeholder: "800k, 2m or off",
    help:
      "The vault's upstream (WAN) budget: total bytes per second vault-core fetches from the internet for cache misses outside the download window. " +
      "k and m are nginx units (k = 1024). off = no cap.",
  },
  upstream_max_conns: {
    label: "Upstream (WAN) connection cap",
    help: "The vault's upstream (WAN) budget: connections from vault-core to the internet at the same time. At least the prefill threads.",
  },
  tls_client_max_conns: {
    label: "HTTPS sessions per client",
    help: "Passed-through HTTPS sessions per client address. At most the total.",
  },
  tls_max_conns: { label: "HTTPS sessions in total", help: "Passed-through HTTPS sessions for all clients together." },
  event_log_rotate_bytes: {
    label: "Event-log rotation size (bytes)",
    help: "vault-core starts a new event log at this size. 0 turns rotation off.",
  },
  upstream_edge: {
    label: "Steam edge host",
    help: "The one Steam server vault-core keeps connections open to. off = the older per-name pool (rollback switch).",
  },
  upstream_pool_hosts: {
    label: "Legacy pool hosts",
    help: "Up to four Steam hosts for the older per-name pool. Used only while the edge host is off.",
  },
  resolver: {
    label: "DNS resolver for vault-core",
    help: "DNS server vault-core uses to find upstream servers. Never one that answers their names with a LAN address, such as this vault's own DNS.",
  },
  log_level: {
    label: "Log level",
    kind: "segs",
    options: [
      ["DEBUG", "Debug"],
      ["INFO", "Info"],
      ["WARNING", "Warning"],
      ["ERROR", "Error"],
      ["CRITICAL", "Critical"],
    ],
    help: "How much vault-api and vault-runner write to their logs.",
  },
});

/** What can go wrong, per risky key (the confirm dialog names it). */
export const RISK_TEXT = Object.freeze({
  prefill_max_threads: "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
  upstream_max_conns: "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
  tls_client_max_conns: "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
  tls_max_conns: "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
  upstream_edge: "A wrong edge host makes every cache miss fail, so downloads stop until it is fixed.",
  upstream_pool_hosts: "Wrong hosts make cache misses fail while the older per-name pool is in use.",
  resolver: "A resolver that answers upstream names with a LAN address makes vault-core fetch from itself, and downloads fail.",
});
export const RISK_FALLBACK_TEXT = "This debug setting can break downloads.";

/** @returns {{label: string, help: string, kind: string, options?: string[][], placeholder?: string}} */
export function fieldMeta(entry) {
  const meta = FIELD_META[entry.key] || {};
  return {
    label: meta.label || entry.key,
    help: meta.help || "",
    kind: meta.kind || "text",
    options: meta.options,
    placeholder: meta.placeholder,
  };
}

/**
 * The (i) text of a field: the explanation, the accepted format (the API's
 * `grammar`) and, for keys only vault-core reads, the one thing vault-api
 * cannot know (`env_visible: false`).
 * @returns {string[]} paragraphs
 */
export function fieldHelp(entry) {
  const meta = fieldMeta(entry);
  const out = [];
  if (meta.help) out.push(meta.help);
  if (entry.grammar) out.push(`Format: ${entry.grammar}.`);
  if (entry.env_visible === false && !entry.env_only) {
    out.push(
      `vault-api cannot see vault-core's environment: if ${entry.env_var || "the variable"} is set there, that value wins over this one.`,
    );
  }
  return out;
}

/**
 * True when the row is shown as plain text (Q1: env always wins; Q2: the
 * webhook URL is env-only). An older server without `editable` keeps its
 * inputs (its env values were overridable).
 */
export function isPinned(entry) {
  return entry.env_only === true || entry.editable === false;
}

/**
 * True for a vault-core key this build cannot hand to vault-core (D16b-1
 * review B3: `VAULT_CORE_SETTINGS_FILE` is blank): the API's
 * `reason: "core_file_disabled"`. A row without a `reason` field (a server
 * between D16b and its review) is judged by its shape (core target, not
 * editable, not from env), so it is never mislabelled "set via env".
 */
export function isCoreFileDisabled(entry) {
  if ("reason" in entry) return entry.reason === "core_file_disabled";
  return entry.editable === false && !entry.env_only && entry.source !== "env" && entry.target === "core";
}

/** The note under a plain-text row: "Set via env (VAULT_NAME)", or why a
 * vault-core key is not editable in this build. */
export function envNote(entry) {
  if (isCoreFileDisabled(entry)) {
    const name = entry.env_var ? ` (${entry.env_var})` : "";
    return `Applies to vault-core only via env in this build${name}. Shown: the built-in default; vault-core's own environment may set another value.`;
  }
  return entry.env_var ? `Set via env (${entry.env_var})` : "Set via env";
}

/** The note for a stored value that env overrides, or null. */
export function shadowNote(entry) {
  if (!entry.shadowed) return null;
  if (entry.env_only) return "A value stored in Settings is ignored: this setting comes only from the environment.";
  return "A value stored in Settings is overridden by env.";
}

/** A value as plain text (pinned rows, the confirm dialog). */
export function displayValue(entry, value = entry.effective) {
  if (value === null || value === undefined || value === "") {
    return entry.key === "webhook_url" ? "Not set (webhooks off)" : "Not set";
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "None";
    const options = (FIELD_META[entry.key] || {}).options;
    const names = options ? value.map((v) => (options.find(([o]) => o === v) || [v, v])[1]) : value;
    return names.join(", ");
  }
  if (typeof value === "boolean") return value ? "On" : "Off";
  const options = (FIELD_META[entry.key] || {}).options;
  if (options) {
    const found = options.find(([o]) => o === String(value));
    if (found) return found[1];
  }
  return String(value);
}

/**
 * One line for a collapsed section's summary. Reads only fields of that
 * section (audit 1.3, LEARNINGS 4d-web "one field over-claims").
 * @param {string} sectionId
 * @param {(key: string) => object | undefined} byKey
 * @param {object[]} [rows] the section's rows (Expert: all expert rows)
 * @returns {string}
 */
export function sectionSummary(sectionId, byKey, rows = []) {
  const eff = (k) => {
    const e = byKey(k);
    return e ? e.effective : undefined;
  };
  if (sectionId === "vault") {
    const name = eff("vault_name");
    return name ? `Name: ${name}` : "No vault name set";
  }
  if (sectionId === "window") {
    const w = eff("schedule_window");
    if (w === undefined) return "";
    return w === null || w === "" || w === "off" ? "Scheduler off" : `Window ${w}`;
  }
  if (sectionId === "disk") {
    const e = byKey("auto_gc");
    return e ? `Auto-GC: ${displayValue(e)}` : "";
  }
  if (sectionId === "notify") {
    const url = eff("webhook_url");
    if (url === undefined) return "";
    if (!url) return "Webhook off";
    const events = eff("webhook_events");
    return Array.isArray(events) ? `Webhook on · ${events.length} events` : "Webhook on";
  }
  if (sectionId === "steam") {
    const id = eff("steam_library_steamid");
    if (id === undefined) return "";
    return id ? "Library SteamID64 set" : "No library SteamID64";
  }
  if (sectionId === "expert" || sectionId === "other") {
    const env = rows.filter((e) => e.source === "env").length;
    const here = rows.filter((e) => e.source === "db").length;
    if (!env && !here) return "All at defaults";
    const parts = [];
    if (here) parts.push(`${here} changed here`);
    if (env) parts.push(`${env} set via env`);
    return parts.join(" · ");
  }
  return "";
}

/** Risky keys a PATCH body sets or clears (D16b: both need confirm). */
export function riskyKeysInBody(entries, body) {
  const byKey = new Map((entries || []).map((e) => [e.key, e]));
  return Object.keys(body || {}).filter((k) => byKey.get(k) && byKey.get(k).risky === true);
}

/**
 * Dialog lines for risky keys: label, old -> new, what can break. A
 * cleared value (`null`) reverts to the fallback.
 * @returns {Array<{key: string, label: string, from: string, to: string, risk: string}>}
 */
export function riskyItems(entries, body, keys) {
  const byKey = new Map((entries || []).map((e) => [e.key, e]));
  return keys.map((key) => {
    const entry = byKey.get(key) || { key };
    const next = body[key];
    return {
      key,
      label: fieldMeta(entry).label,
      from: displayValue(entry),
      to: next === null ? `${displayValue(entry, entry.fallback)} (default)` : displayValue(entry, next),
      risk: RISK_TEXT[key] || RISK_FALLBACK_TEXT,
    };
  });
}

/** The keys a 428 answer names (`detail.risky_keys`), else []. */
export function riskyKeysFrom428(err) {
  const detail = err && err.detail;
  return detail && Array.isArray(detail.risky_keys) ? detail.risky_keys.filter((k) => typeof k === "string") : [];
}

function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d && typeof d.msg === "string" ? d.msg : null)).filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  return null;
}

/**
 * The save bar's line for a failed PATCH. 409/422/428/403 are answered
 * before anything is written (routers/settings.py validates the whole body
 * first), so those say "Nothing was saved"; any other failure does not
 * claim to know.
 */
export function describeSaveError(err, { confirmed = false } = {}) {
  const status = err && err.status;
  const detail = detailText(err && err.detail);
  if (status === 409) {
    return `Not saved: ${detail || "a value is set via env, and env always wins."} Nothing was saved.`;
  }
  if (status === 422) {
    return `Not saved: ${detail || "the server refused a value."} Nothing was saved.`;
  }
  if (status === 428) {
    if (confirmed) {
      return "Not saved: the server still asked for confirmation of settings that can break downloads. Nothing was saved.";
    }
    return "Not saved: the change includes settings that can break downloads and was not confirmed. Nothing was saved.";
  }
  if (status === 403) {
    return `Not saved: ${detail || "the settings are read-only."}`;
  }
  return `Could not save: ${detail || (err && err.message) || "Request failed."}`;
}

/**
 * vault-core's apply state for the core settings (D16c `core_apply`), as
 * one line for the Expert section, or null when vault-core has not
 * reported yet. State only: what vault-core said, never a guess.
 * @returns {{text: string, warn: boolean} | null}
 */
export function coreApplyLine(coreApply) {
  if (!coreApply || typeof coreApply !== "object") return null;
  if (coreApply.pending === true) {
    return { text: "vault-core: a change is waiting to be applied.", warn: false };
  }
  if (coreApply.state === "rejected") {
    const why = coreApply.reason ? `: ${coreApply.reason}` : ".";
    return { text: `vault-core rejected the last settings change${why} It keeps running with the settings it had.`, warn: true };
  }
  if (coreApply.state === "applied") {
    return { text: "vault-core: settings applied.", warn: false };
  }
  if (coreApply.state === "none") {
    return { text: "vault-core has not seen a settings file yet; it runs on its environment and defaults.", warn: false };
  }
  return null;
}

// ---------------------------------------------------------------------
// Remembered expert switch (guarded storage, LEARNINGS WEB-FIX-5)
// ---------------------------------------------------------------------

export const EXPERT_STORAGE_KEY = "steamvault.settingsExpert";

function safeStorage(getStorage) {
  try {
    const s = getStorage();
    return s && typeof s.getItem === "function" ? s : null;
  } catch {
    return null;
  }
}

export function readExpertSwitch(getStorage) {
  const s = safeStorage(getStorage);
  if (!s) return false;
  try {
    return s.getItem(EXPERT_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeExpertSwitch(getStorage, on) {
  const s = safeStorage(getStorage);
  if (!s) return;
  try {
    s.setItem(EXPERT_STORAGE_KEY, on ? "1" : "0");
  } catch {
    // storage full or blocked: the page still works, it just forgets
  }
}

/**
 * Per-field state chip. Env-only rows and env-sourced values read "From
 * env"; a stored override reads "Changed here"; everything else "Default".
 * @param {{source?: string, env_only?: boolean}} entry
 * @returns {{kind: "default"|"changed"|"env", text: string}}
 */
export function fieldChip(entry) {
  if (entry && (entry.env_only === true || entry.source === "env")) {
    return { kind: "env", text: "From env" };
  }
  if (entry && entry.source === "db") return { kind: "changed", text: "Changed here" };
  return { kind: "default", text: "Default" };
}

/**
 * Next nav item for a keyboard key (vertical list, arrows wrap).
 * @param {readonly string[]} ids visible nav ids in order
 * @param {string} current focused id
 * @param {string} key KeyboardEvent.key
 * @returns {string|null} id to move to, or null when the key does not navigate
 */
export function navTarget(ids, current, key) {
  const i = ids.indexOf(current);
  if (i < 0 || ids.length === 0) return null;
  const n = ids.length;
  if (key === "ArrowDown" || key === "ArrowRight") return ids[(i + 1) % n];
  if (key === "ArrowUp" || key === "ArrowLeft") return ids[(i - 1 + n) % n];
  if (key === "Home") return ids[0];
  if (key === "End") return ids[n - 1];
  return null;
}

/**
 * Whether the "cached sweep can be collected by GC" warning applies: the
 * sweep includes cached games while Auto-GC is anything but Execute.
 */
export function gcRiskActive(includeCached, autoGc) {
  if (includeCached === null || includeCached === undefined) return false;
  return String(includeCached) === "true" && String(autoGc) !== "execute";
}
