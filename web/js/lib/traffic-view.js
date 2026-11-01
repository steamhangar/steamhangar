/**
 * Presentation of vault-api's traffic endpoints (WP D15c, over WP D15b's
 * `GET /v1/throughput`, `/v1/throughput/series`, `/v1/clients/overview` and
 * the `GET /v1/jobs?range=` history filter). Pure: no DOM, no fetch; the
 * Downloads header (`views/downloads.js`) and the Clients page
 * (`views/clients.js`) build their nodes from what these functions return.
 * Unit-tested in web/tests/traffic-view.test.js.
 *
 * **Honest states (api/README.md "event_feed").** Every response carries
 * `event_feed.state`:
 *  - `disabled`: the server reads no event log. Every traffic field is
 *    `null`, and `null` there means "not measured", never zero. The views
 *    show one short note and no numbers at all (`feedNote`, and
 *    `headerTiles` returns no tiles).
 *  - `never-swept` / `stale`: stored totals may exist, but the "current"
 *    values (`current`, `active_clients_now`, `loading_now`,
 *    `cache_active`) are `null`. Those show a dash, and the note says why.
 *  - `ok`: everything shown.
 * A dash always stands for a `null` the server sent; a real `0` prints as
 * a number.
 *
 * **One "now" (WP GRAPH-RECENT-1 + LOADING-NOW-1).** "Current speed", the
 * status pill and "Loading now" read the live tier on every range: the live
 * view through `mergeLive`, the persisted ranges through `mergeNow` (the
 * server's `now` block). All three share one window, the live
 * current-speed window (`active_window_seconds`), the same one the Clients
 * page's `live_now` uses; the graph's newest buckets come from the same ring.
 */

import { formatAgo, formatBytesGB } from "./format.js";
import { PRODUCT_NAME } from "./product.js";

/** The four ranges every traffic endpoint and the jobs filter accept. */
export const RANGES = Object.freeze(["24h", "7d", "30d", "all"]);

/**
 * WP GRAPH-ZOOM: the Downloads statistics (header tiles and graph) also
 * offer 1 h and 6 h, answered by the server's 1-minute tier. The Clients
 * page and the History filter keep `RANGES` (no 1-minute per-depot rows).
 */
export const STATS_RANGES = Object.freeze(["live", "1h", "6h", "24h", "7d", "30d", "all"]);

/** Segmented-control labels for every range of `STATS_RANGES`. */
export const RANGE_LABELS = Object.freeze({
  live: "Live",
  "1h": "1 h",
  "6h": "6 h",
  "24h": "24 h",
  "7d": "7 days",
  "30d": "30 days",
  all: "All",
});

/** localStorage key of the Downloads statistics range (per browser). */
export const STATS_RANGE_KEY = "steamvault.downloadsRange";

/** The Downloads range when nothing valid is stored. */
export const DEFAULT_STATS_RANGE = "live";

/** @param {unknown} value @returns {boolean} */
export function isStatsRange(value) {
  return STATS_RANGES.includes(value);
}

/**
 * The range the user chose last on Downloads, or the default. Storage can be
 * missing, blocked or throw (private window): any failure gives the default.
 * @param {() => (Storage | null | undefined)} [getStorage]
 * @returns {string}
 */
export function readStoredStatsRange(getStorage = () => globalThis.localStorage) {
  try {
    const value = getStorage()?.getItem(STATS_RANGE_KEY);
    return isStatsRange(value) ? value : DEFAULT_STATS_RANGE;
  } catch {
    return DEFAULT_STATS_RANGE;
  }
}

/**
 * Remember the chosen range; a storage failure only loses the persistence.
 * @param {string} range
 * @param {() => (Storage | null | undefined)} [getStorage]
 * @returns {boolean} whether it was stored
 */
export function writeStoredStatsRange(range, getStorage = () => globalThis.localStorage) {
  if (!isStatsRange(range)) return false;
  try {
    const storage = getStorage();
    if (!storage) return false;
    storage.setItem(STATS_RANGE_KEY, range);
    return true;
  } catch {
    return false;
  }
}

/** Phrases for running text ("1.2 GB loaded in the last 7 days"). */
export const RANGE_PHRASES = Object.freeze({
  // WP GRAPH-LIVE: the live view's tiles summarise the last hour.
  live: "in the last hour",
  "1h": "in the last hour",
  "6h": "in the last 6 hours",
  "24h": "in the last 24 hours",
  "7d": "in the last 7 days",
  "30d": "in the last 30 days",
  all: "since recording started",
});

/** @param {unknown} value @returns {boolean} */
export function isRange(value) {
  return RANGES.includes(value);
}

/** The text a `null` value shows. */
export const DASH = "—";

const KIB = 1024;
const MIB = 1024 * 1024;
const GIB = 1024 * 1024 * 1024;

function finiteNonNegative(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function oneDecimalOrWhole(value) {
  return value >= 100 ? value.toFixed(0) : value.toFixed(1);
}

/**
 * Bytes per second as "40.0 MB/s" (binary units, the same 1024 base as
 * `formatBytesGB`'s "GB"). A real zero prints as "0 MB/s".
 * @param {number | null | undefined} bytesPerSecond
 * @returns {string | null} `null` for anything that is not a measurement.
 */
export function formatSpeed(bytesPerSecond) {
  if (!finiteNonNegative(bytesPerSecond)) return null;
  if (bytesPerSecond === 0) return "0 MB/s";
  if (bytesPerSecond < KIB) return "< 1 KB/s";
  if (bytesPerSecond < MIB) return `${Math.round(bytesPerSecond / KIB)} KB/s`;
  if (bytesPerSecond < GIB) return `${oneDecimalOrWhole(bytesPerSecond / MIB)} MB/s`;
  return `${oneDecimalOrWhole(bytesPerSecond / GIB)} GB/s`;
}

/**
 * A byte total for traffic figures: "0 GB", "< 1 MB", "312 MB", "1.2 GB".
 * Unlike `formatBytesGB` a real zero prints (nothing was loaded is a fact
 * here), and small totals stay readable in MB.
 * @param {number | null | undefined} bytes
 * @returns {string | null}
 */
export function formatTrafficBytes(bytes) {
  if (!finiteNonNegative(bytes)) return null;
  if (bytes === 0) return "0 GB";
  if (bytes < MIB) return "< 1 MB";
  if (bytes < GIB) return `${Math.round(bytes / MIB)} MB`;
  return formatBytesGB(bytes);
}

/**
 * A 0..1 ratio as a whole percentage ("83%").
 * @param {number | null | undefined} rate
 * @returns {string | null}
 */
export function formatRate(rate) {
  if (typeof rate !== "number" || !Number.isFinite(rate) || rate < 0) return null;
  return `${Math.round(Math.min(rate, 1) * 100)}%`;
}

/** Notes for the feed states and an old server (one place for the words). */
export const FEED_TEXT = Object.freeze({
  disabled:
    "Traffic statistics are off: the server does not read the cache's event log. Set VAULT_EVENT_LOG and VAULT_EVENT_LOG_PATH in the server's .env to see speeds, hit rate and machines without an agent.",
  unsupported: "This server does not report traffic statistics yet. Update vault-api to see them.",
  neverSwept: (minutes) =>
    `No traffic data yet. The server reads the cache's event log every ${minutes} min; the first numbers appear after that.`,
  stale: (ago) =>
    `Traffic data is out of date: the server last read the cache's event log ${ago}. Current values show a dash until it reads it again.`,
  staleNever: "Traffic data is out of date. Current values show a dash until the server reads the cache's event log again.",
});

/**
 * The note when a refresh fails after an earlier answer: the old figures
 * stay on screen, this line says they were not updated.
 * @param {string} reason
 * @returns {string}
 */
export function refreshFailedText(reason) {
  return `Couldn't refresh: ${reason}`;
}

/**
 * Several notes as one line, skipping the empty ones; `null` when none.
 * @param {...(string | null | undefined)} notes
 * @returns {string | null}
 */
export function joinNotes(...notes) {
  const parts = notes.filter((n) => typeof n === "string" && n);
  return parts.length ? parts.join(" ") : null;
}

/**
 * The feed's state, defensively: anything unreadable counts as `disabled`
 * (show no numbers rather than invent them).
 * @param {{event_feed?: {state?: string}} | null | undefined} response
 * @returns {"disabled"|"never-swept"|"stale"|"ok"}
 */
export function feedState(response) {
  const state = response && response.event_feed ? response.event_feed.state : null;
  return state === "ok" || state === "stale" || state === "never-swept" ? state : "disabled";
}

/**
 * The note above the numbers, or `null` when the feed is `ok`.
 * @param {{event_feed?: object} | null | undefined} response
 * @param {number} [nowMs]
 * @returns {string | null}
 */
export function feedNote(response, nowMs = Date.now()) {
  const state = feedState(response);
  const feed = (response && response.event_feed) || {};
  if (state === "ok") return null;
  if (state === "disabled") return FEED_TEXT.disabled;
  if (state === "never-swept") {
    const minutes = Number.isInteger(feed.sweep_interval_minutes) && feed.sweep_interval_minutes > 0 ? feed.sweep_interval_minutes : 5;
    return FEED_TEXT.neverSwept(minutes);
  }
  const ago = formatAgo(feed.last_sweep_at, nowMs);
  return ago ? FEED_TEXT.stale(ago) : FEED_TEXT.staleNever;
}

/**
 * The Downloads header's figures from `GET /v1/throughput`. `null` before
 * the first answer and with the feed disabled (the note stands alone then).
 * Each tile: `{key, label, value, title, detail}`; `value` is the dash for
 * a `null` the server sent; `detail` is an optional second line.
 * @param {object | null | undefined} throughput
 * @param {string | null} [rateCap] WP RATE-TILE: `rateCapLine(...)`, its own
 *   line on the "Current speed" tile only; `null` = not known.
 * @returns {Array<{key: string, label: string, value: string, title: string, detail: string | null, limit: string | null}> | null}
 */
export function headerTiles(throughput, rateCap = null) {
  if (!throughput || feedState(throughput) === "disabled") return null;
  const summary = throughput.summary || {};
  const current = throughput.current ? throughput.current.bytes_per_second : null;
  const phrase = RANGE_PHRASES[throughput.range] || "";
  const fine = throughput.bucket_seconds === 60; // the 1-minute tier (1 h / 6 h once it has filled)
  const tile = (key, label, value, title, detail = null, limit = null) => ({ key, label, value: value ?? DASH, title, detail, limit });
  const windowMinutes = Number.isInteger(throughput.active_window_minutes) ? throughput.active_window_minutes : 15;
  // WP LOADING-NOW-1: a live answer counts over the current-speed window (seconds).
  const windowSeconds = activeWindowSeconds(throughput);
  const inRange = Number.isInteger(summary.active_clients) ? summary.active_clients : null;
  return [
    tile(
      "current",
      "Current speed",
      formatSpeed(current),
      throughput.live
        ? `Everything the cache served over the last 15-20 seconds (all traffic, ${PRODUCT_NAME}'s own prefill downloads included), read live from the cache's event log.`
        : `Everything the cache served over the last 5-10 minutes (all traffic, ${PRODUCT_NAME}'s own prefill downloads included), measured when the server last read the cache's event log.`,
      currentDetail(throughput),
      rateCap,
    ),
    tile(
      "average",
      "Average while loading",
      formatSpeed(summary.average_bytes_per_second),
      `Average speed ${phrase}, counting only the ${fine ? "1-minute" : "5-minute"} periods in which something was loaded.`,
    ),
    tile(
      "peak",
      "Peak speed",
      formatSpeed(summary.peak_bytes_per_second),
      `The busiest ${fine ? "minute" : "5 minutes"} ${phrase}.`,
    ),
    tile(
      "loaded",
      "Served (cache + prefill)",
      formatTrafficBytes(summary.bytes_served),
      `Everything the cache served ${phrase}, ${PRODUCT_NAME}'s own prefill downloads included. "Loaded from the internet" plus "Saved by the cache" adds up to this only when ${PRODUCT_NAME}'s own prefill downloads had no cache hits (see the line below).`,
      runnerDetail(summary),
    ),
    tile(
      "internet",
      "Loaded from the internet",
      formatTrafficBytes(internetBytes(summary)),
      `Bytes ${phrase} that were not in the cache and came from the internet, ${PRODUCT_NAME}'s own prefill downloads included.`,
    ),
    tile(
      "hit-rate",
      "Hit rate",
      // Bytes, not requests: the same base as "Saved by the cache" next to it.
      formatRate(summary.byte_hit_rate),
      `Share of the bytes your machines loaded ${phrase} that came from the cache. ${PRODUCT_NAME}'s own prefill downloads are not counted.`,
      hitRateDetail(summary),
    ),
    tile(
      "saved",
      "Saved by the cache",
      formatTrafficBytes(summary.hit_bytes),
      `Bytes your machines loaded ${phrase} from the cache's disk instead of the internet. ${PRODUCT_NAME}'s own prefill downloads are not counted.`,
    ),
    tile(
      "clients",
      "Loading now",
      Number.isInteger(throughput.active_clients_now) ? String(throughput.active_clients_now) : null,
      `${windowSeconds ? `Machines the cache served in the last ${windowSeconds} seconds (the window of "Current speed").` : `Machines with cache traffic in the last ${windowMinutes} minutes.`} ${PRODUCT_NAME}'s own prefill downloads are not counted.${inRange === null ? "" : ` ${inRange} machines loaded something ${phrase}.`}`,
      windowSeconds ? `machines the cache served in the last ${windowSeconds} s` : `machines with cache traffic in the last ${windowMinutes} min`,
    ),
  ];
}

/**
 * WP CLIENT-ROW-1: the Downloads page's "Client downloading" row. `null`
 * (no row, no heading) unless the feed is `ok` and at least one machine had
 * cache traffic inside the active window; the server's own prefill runner
 * is not counted (it is the job cards' business). Same source as the
 * "Loading now" tile: `active_clients_now`, which `mergeLive` and
 * `mergeNow` take from the live ring (WP LOADING-NOW-1: over the
 * current-speed window, `active_window_seconds`).
 * @param {object | null | undefined} throughput
 * @returns {{label: string, detail: string, title: string} | null}
 */
export function clientDownloadingRow(throughput) {
  if (!throughput || feedState(throughput) !== "ok") return null;
  const n = throughput.active_clients_now;
  if (!Number.isInteger(n) || n < 1) return null;
  const minutes = Number.isInteger(throughput.active_window_minutes) ? throughput.active_window_minutes : 15;
  const seconds = activeWindowSeconds(throughput);
  const machines = `${n} ${n === 1 ? "machine" : "machines"}`;
  return {
    label: "Client downloading",
    detail: seconds ? `${machines} served by the cache in the last ${seconds} s` : `${machines} with cache traffic in the last ${minutes} min`,
    title: `Machines other than this server that loaded something from the cache recently. ${PRODUCT_NAME}'s own prefill downloads are not counted.`,
  };
}

/**
 * WP LOADING-NOW-1: the window of `active_clients_now` in seconds when it
 * was read from the live tier (`mergeLive` / `mergeNow` set
 * `active_window_seconds`), else `null` (the swept count's window is
 * `active_window_minutes`).
 * @param {object} throughput
 * @returns {number | null}
 */
export function activeWindowSeconds(throughput) {
  const s = throughput ? throughput.active_window_seconds : null;
  return Number.isInteger(s) && s > 0 ? s : null;
}

/**
 * WP RATE-TILE: bytes per second of an `upstream_rate` value (digits with an
 * optional k = x1024 or m = x1048576); `null` for no cap, blank, `off` or
 * anything unreadable (a cap is never guessed).
 * @param {unknown} raw
 * @returns {number | null}
 */
export function parseRateCap(raw) {
  if (typeof raw !== "string") return null;
  const m = /^([1-9][0-9]*)([kKmM]?)$/.exec(raw.trim());
  if (!m) return null;
  const bytes = Number(m[1]) * { k: 1024, m: 1048576, "": 1 }[m[2].toLowerCase()];
  return Number.isFinite(bytes) && bytes > 0 ? bytes : null;
}

/**
 * Is `nowMs` inside the daily `HH:MM-HH:MM` window, read on the server's own
 * clock (`server_timezone` = "UTC+02:00")? Start inclusive, end exclusive,
 * overnight windows wrap (twin of the API's `ScheduleWindow.contains`).
 * `null` when the window or the zone cannot be read: no claim either way.
 * @param {unknown} window
 * @param {unknown} serverTimezone
 * @param {number} nowMs
 * @returns {boolean | null}
 */
export function insideWindow(window, serverTimezone, nowMs) {
  const w = /^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$/.exec(String(window ?? ""));
  const z = /^UTC(?:([+-])(\d{2}):(\d{2}))?$/.exec(String(serverTimezone ?? ""));
  if (!w || !z || !Number.isFinite(nowMs)) return null;
  const offset = z[1] ? (z[1] === "-" ? -1 : 1) * (Number(z[2]) * 60 + Number(z[3])) : 0;
  const start = Number(w[1]) * 60 + Number(w[2]);
  const end = Number(w[3]) * 60 + Number(w[4]);
  const minute = ((((Math.floor(nowMs / 60000) + offset) % 1440) + 1440) % 1440);
  return end < start ? minute >= start || minute < end : minute >= start && minute < end;
}

/**
 * WP RATE-TILE: the line under "Current speed" about the upstream (internet)
 * rate cap. The cap applies only OUTSIDE the download window (the one window,
 * `schedule.window`; none = capped around the clock), so the line says "in
 * force now" or "not in force now" and names the window; without a readable
 * schedule it states the value and the rule and claims neither. Known limit:
 * the deprecated env pin `VAULT_UPSTREAM_RATE_WINDOW` can make vault-core use
 * a window other than the schedule window; the API does not expose it, so the
 * in-force statement may then be wrong. `null`
 * while the settings are not known.
 * @param {{settings?: object[], core_apply?: {pending?: unknown} | null} | null | undefined} settings `GET /v1/settings`
 * @param {{window?: unknown, server_timezone?: unknown} | null | undefined} schedule `GET /v1/schedule`
 * @param {number} nowMs
 * @returns {string | null}
 */
export function rateCapLine(settings, schedule, nowMs) {
  const row = settings && Array.isArray(settings.settings) ? settings.settings.find((r) => r && r.key === "upstream_rate") : null;
  if (!row) return null;
  const bytes = parseRateCap(row.effective);
  if (bytes === null) {
    const none = row.effective === null || row.effective === undefined || row.effective === "" || row.effective === "off";
    return none ? "No limit" : null;
  }
  const limit = `Limit ${formatSpeed(bytes).replace(".0 ", " ")}`; // a cap reads "8 MB/s", not "8.0 MB/s"
  const pending = settings.core_apply && settings.core_apply.pending === true ? " (change not applied yet)" : "";
  if (!schedule) return `${limit}, outside the download window${pending}`;
  const window = typeof schedule.window === "string" && schedule.window.trim() ? schedule.window : null;
  if (window === null) return `${limit}, all day${pending}`;
  const inside = insideWindow(window, schedule.server_timezone, nowMs);
  const m = /^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$/.exec(window);
  const span = `download window ${m ? `${m[1]}–${m[2]}` : window.trim()}`;
  if (inside === null) return `${limit}, outside the ${span}${pending}`;
  return inside
    ? `${limit}, not in force now (inside the ${span})${pending}`
    : `${limit}, in force now (outside the ${span})${pending}`;
}

/**
 * Detail line of "Current speed": scope and window, plus the runner's part.
 * WP RUNNER-NAME-1: the runner is named by the product (`PRODUCT_NAME`).
 */
function currentDetail(throughput) {
  const liveSeconds = throughput.live && throughput.current ? throughput.current.window_seconds : null;
  if (Number.isInteger(liveSeconds) && liveSeconds > 0) {
    const liveScope = `all traffic incl. ${PRODUCT_NAME}, last ${liveSeconds} s`;
    const liveRunner = formatSpeed(throughput.current.runner_bytes_per_second);
    return liveRunner ? `${liveScope} · ${liveRunner} by ${PRODUCT_NAME}` : liveScope;
  }
  const scope = `all traffic incl. ${PRODUCT_NAME}, 5-10 min window`;
  const runner = throughput.current ? formatSpeed(throughput.current.runner_bytes_per_second) : null;
  return runner ? `${scope} · ${runner} by ${PRODUCT_NAME}` : scope;
}

/** Detail line of "Hit rate": the scope, and the runner's own rate (WP RUNNER-NAME-1: by product name). */
function hitRateDetail(summary) {
  const runner = summary.prefill_runner;
  const rate = runner ? formatRate(runner.byte_hit_rate) : null;
  const scope = "clients only, own prefill excluded";
  return rate ? `${scope} (${PRODUCT_NAME}: ${rate})` : scope;
}

/** Tooltip of a job card's speed line. */
export const JOB_SPEED_NOTE = "Exclusive depots only: bytes on depots that belong to this game alone, from every address.";
/** WP JOB-SIZE-1: the tooltip while a first download counts expected depots. */
export const JOB_SPEED_EXPECTED_NOTE =
  `Expected depots: the server has not mapped this game yet, so the speed and size count ${PRODUCT_NAME}'s own prefill bytes on the depots the oracle expects (an estimate until the download ends).`;

/**
 * The tooltip of a job's speed line (WP JOB-SIZE-1).
 * @param {object | null | undefined} entry one `jobs[]` element
 * @returns {string}
 */
export function jobSpeedNote(entry) {
  return entry && entry.expected === true ? JOB_SPEED_EXPECTED_NOTE : JOB_SPEED_NOTE;
}

/**
 * WP TILE-INTERNET: all-scope miss bytes. The server's `internet_bytes`;
 * an older server without it falls back to client misses plus the runner's
 * misses; `null` when neither is known (shown as a dash).
 * @param {object} summary
 * @returns {number | null}
 */
export function internetBytes(summary) {
  if (!summary) return null;
  if (finiteNonNegative(summary.internet_bytes)) return summary.internet_bytes;
  const runner = summary.prefill_runner;
  if (!finiteNonNegative(summary.miss_bytes)) return null;
  if (runner && finiteNonNegative(runner.miss_bytes)) return summary.miss_bytes + runner.miss_bytes;
  return runner ? null : summary.miss_bytes;
}

/**
 * WP D15-RUNNER: "of which X by the prefill runner" under "Loaded" (WP
 * RUNNER-NAME-1: "X by SteamHangar", the product's name); `null`
 * when the server sent no runner figures (older vault-api) or the runner
 * loaded nothing in the range.
 * @param {object} summary
 * @returns {string | null}
 */
export function runnerDetail(summary) {
  const runner = summary && summary.prefill_runner;
  if (!runner || !Number.isFinite(runner.bytes_served) || runner.bytes_served <= 0) return null;
  const size = formatTrafficBytes(runner.bytes_served);
  if (!size) return null;
  const cached = Number.isFinite(runner.hit_bytes) && runner.hit_bytes > 0 ? formatTrafficBytes(runner.hit_bytes) : null;
  return cached ? `${size} by ${PRODUCT_NAME}, ${cached} of that from the cache` : `${size} by ${PRODUCT_NAME}`;
}

/** The bucket a series point covers, for the graph's label. */
export function bucketWord(bucketSeconds) {
  if (bucketSeconds === 5) return "5 seconds";
  if (bucketSeconds === 15) return "15 seconds";
  if (bucketSeconds === 60) return "minute";
  if (bucketSeconds === 300) return "5 minutes";
  if (bucketSeconds === 3600) return "hour";
  if (bucketSeconds === 86400) return "day";
  return `${Math.round(bucketSeconds / 60)} minutes`;
}

/**
 * Polyline geometry for the throughput graph (`GET /v1/throughput/series`
 * points, oldest first). Coordinates are in a `width` x `height` box with
 * y growing downwards, rounded to 0.1. `line` is an SVG path through every
 * point, `area` the same path closed along the bottom edge. With one point
 * the line is flat across the box; with no points, `null`.
 * @param {Array<{bytes_per_second: number}>} points
 * @param {{width: number, height: number}} box
 * @returns {{line: string, area: string, maxBytesPerSecond: number} | null}
 */
export function seriesGeometry(points, { width, height }) {
  if (!Array.isArray(points) || points.length === 0) return null;
  const values = points.map((p) => (p && finiteNonNegative(p.bytes_per_second) ? p.bytes_per_second : 0));
  const max = Math.max(...values);
  const round = (v) => Math.round(v * 10) / 10;
  const n = values.length;
  const xs = n === 1 ? [0, width] : values.map((_, i) => (i / (n - 1)) * width);
  const ys = (n === 1 ? [values[0], values[0]] : values).map((v) => (max > 0 ? height - (v / max) * height : height));
  const coords = xs.map((x, i) => `${round(x)} ${round(ys[i])}`);
  const line = "M" + coords.join(" L");
  const area = `${line} L${round(xs[xs.length - 1])} ${height} L${round(xs[0])} ${height} Z`;
  return { line, area, maxBytesPerSecond: max };
}

/**
 * The graph's accessible name: what it shows and its highest value.
 * @param {{range?: string, bucket_seconds?: number}} series
 * @param {number} maxBytesPerSecond
 * @returns {string}
 */
export function seriesLabel(series, maxBytesPerSecond) {
  const phrase = RANGE_PHRASES[series.range] || "";
  const per = bucketWord(series.bucket_seconds);
  return `Average speed per ${per} ${phrase}. Highest: ${formatSpeed(maxBytesPerSecond) || "0 MB/s"}.`;
}

/**
 * WP GRAPH-ZOOM: the series without its empty lead-in. A zero-filled range
 * whose traffic all fell into the last stretch would be a flat line with a
 * spike at the edge; the graph shows only the span that has data, starting
 * one bucket before the first non-zero point so the line rises from the
 * floor. Trailing zeros stay (the cache really is idle now). An all-zero
 * series is returned unchanged (nothing to zoom to).
 * @param {Array<{bytes_per_second: number}>} points
 * @returns {Array<object>}
 */
export function trimSeries(points) {
  if (!Array.isArray(points)) return [];
  const first = points.findIndex((p) => p && finiteNonNegative(p.bytes_per_second) && p.bytes_per_second > 0);
  if (first <= 0) return points.slice();
  return points.slice(first - 1);
}

/**
 * Left-edge caption for the graph: how far back the SHOWN span starts. A
 * span that starts at the range's own start keeps the plain wording
 * ("24 h ago"); a trimmed one says how long ago its first point is. "all"
 * names the first day instead of a duration.
 * @param {{range?: string, range_start?: string | null, points?: Array<{t: string}>}} series
 * @param {number} [nowMs]
 * @returns {string}
 */
export function seriesStartText(series, nowMs = Date.now()) {
  const first = Array.isArray(series.points) && series.points[0] ? series.points[0].t : null;
  if (series.range === "all") {
    const day = typeof first === "string" ? first.slice(0, 10) : null;
    return day ? `since ${day}` : "";
  }
  const fixed =
    { live: "1 h ago", "1h": "1 h ago", "6h": "6 h ago", "24h": "24 h ago", "7d": "7 days ago", "30d": "30 days ago" }[series.range] || "";
  if (typeof first === "string" && typeof series.range_start === "string" && first !== series.range_start) {
    return formatAgo(first, nowMs) || fixed;
  }
  return fixed;
}

/** WP JOB-SIZE-1: a first download's size is the oracle's expectation. */
export const EXPECTED_SIZE_NOTE = " (expected size)";
/** WP JOB-SIZE-1 (ETA): no estimate in a job's first seconds (unstable). */
export const JOB_ETA_MIN_AGE_MS = 20_000;
/** WP JOB-SIZE-1 (ETA): an estimate above a day says nothing useful. */
export const JOB_ETA_MAX_SECONDS = 86_400;

/** WP JOB-SIZE-1 (ETA): weight of the newest speed sample in the EMA. */
export const ETA_SMOOTHING = 0.3;

/**
 * One EMA step over the ETA speed. A missing or non-positive sample resets
 * (`null`): there is no speed to estimate with, and the next real sample
 * starts afresh instead of being dragged down by a stale zero.
 * @param {number | null | undefined} previous
 * @param {number | null | undefined} sample
 * @param {number} [alpha]
 * @returns {number | null}
 */
export function smoothEtaRate(previous, sample, alpha = ETA_SMOOTHING) {
  if (typeof sample !== "number" || !Number.isFinite(sample) || sample <= 0) return null;
  if (typeof previous !== "number" || !Number.isFinite(previous) || previous <= 0) return sample;
  return previous + alpha * (sample - previous);
}

/**
 * Per-job smoothed ETA speeds across throughput polls (WP JOB-SIZE-1). The
 * live runner speed is a ~15-20 s window polled every few seconds; the EMA
 * keeps the ETA from jumping on every poll. `observe` advances each job once
 * per new `jobs` list and forgets jobs the list no longer has. WP
 * WEB-ETA-EMA-1: keyed on the identity of `throughput.jobs`, not of the
 * answer object (Android twin: `throughput.jobs === last`). The live view
 * re-merges its cached 30 s summary every 3 s (`mergeLive` makes a new
 * object around the same `jobs` array); a repaint or a re-merge of the same
 * list is no sample. `clear` forgets everything (the view mounts).
 */
export class EtaSmoother {
  constructor(alpha = ETA_SMOOTHING) {
    this.alpha = alpha;
    this.rates = new Map();
    /** The `jobs` list of the last step (identity), `null` before the first. */
    this.last = null;
  }

  /** @param {{jobs?: Array<object>} | null | undefined} throughput */
  observe(throughput) {
    if (!throughput) return;
    const jobs = throughput.jobs || [];
    if (jobs === this.last) return;
    this.last = jobs;
    const next = new Map();
    for (const job of jobs) {
      const rate = smoothEtaRate(this.rates.get(job.job_id), job.eta_bytes_per_second, this.alpha);
      if (rate !== null) next.set(job.job_id, rate);
    }
    this.rates = next;
  }

  /** @param {number} jobId @returns {number | null} */
  rate(jobId) {
    return this.rates.has(jobId) ? this.rates.get(jobId) : null;
  }

  /** Forget every rate (view mount; Android `EtaSmoother.clear`). */
  clear() {
    this.rates = new Map();
    this.last = null;
  }
}

/**
 * Seconds a running job still needs, or `null` when there is nothing honest
 * to say (WP JOB-SIZE-1): `remaining_bytes / eta_bytes_per_second`. The
 * server matches the divisor to the remainder (`preview`: the runner's total
 * speed over chunks still to request; `completeness`: its MISS speed over
 * bytes still to fetch) and takes it from the live tier, or from a swept
 * window only when that started with the job. `rate` is the smoothed speed
 * (`EtaSmoother`); without it the entry's own value is used. Never for a
 * Verifying card, a job younger than 20 s, a zero or unknown speed, nothing
 * remaining, or more than a day.
 * @param {object | null | undefined} entry one `jobs[]` element
 * @param {{verifying?: boolean, nowMs?: number, rate?: number | null}} [opts]
 * @returns {number | null}
 */
export function jobEtaSeconds(entry, { verifying = false, nowMs = Date.now(), rate } = {}) {
  if (verifying || !entry) return null;
  const remaining = entry.remaining_bytes;
  const speed = rate === undefined ? entry.eta_bytes_per_second : rate;
  if (!finiteNonNegative(remaining) || remaining <= 0) return null;
  if (!finiteNonNegative(speed) || speed <= 0) return null;
  const started = Date.parse(entry.started_at || "");
  if (!Number.isFinite(started) || nowMs - started < JOB_ETA_MIN_AGE_MS) return null;
  const seconds = remaining / speed;
  if (!Number.isFinite(seconds) || seconds > JOB_ETA_MAX_SECONDS) return null;
  return seconds;
}

/**
 * "about 6 min left", "about 2 h 5 min left"; rounded up to whole minutes,
 * always marked as approximate. `null` for anything that is not a positive
 * finite number.
 * @param {number | null | undefined} seconds
 * @returns {string | null}
 */
export function formatEta(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return null;
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  if (minutes < 60) return `about ${minutes} min left`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `about ${hours} h ${rest} min left` : `about ${hours} h left`;
}

/** The speed line of a Verifying job card (WP DOWNLOADS-1, decided by the user). */
export const VERIFY_SPEED_TEXT = "Verifying files, no download speed";

/**
 * The line under a running job's card: its speed at the cache (WP D13
 * SPEED-1, measured per job by depot in `GET /v1/throughput`'s `jobs`).
 * `null` when there is nothing honest to say: no throughput answer yet, the
 * job not listed, or the feed not `ok` (the header's note explains that).
 * @param {object | null | undefined} entry one `jobs[]` element
 * @param {string} state `feedState(...)` of the same answer
 * @param {{verifying?: boolean, nowMs?: number, etaRate?: number | null}} [opts] the card is a Verifying run; the clock and the smoothed speed for the ETA
 * @returns {string | null}
 */
export function jobSpeedText(entry, state, { verifying = false, nowMs = Date.now(), etaRate } = {}) {
  // WP DOWNLOADS-1 (VERIFY-SPEED-TEXT-1): a Verify run compares files and
  // downloads nothing, so "not measured" would read as a fault. Without a
  // measured speed the line says what is happening instead; a speed the
  // server did measure still wins.
  if (verifying) {
    const measured = entry && state === "ok" && entry.bytes_per_second !== null && entry.bytes_per_second !== undefined;
    if (!measured) return VERIFY_SPEED_TEXT;
  }
  if (!entry || state !== "ok") return null;
  if (entry.bytes_per_second === null || entry.bytes_per_second === undefined) {
    if (entry.exclusive_depots !== 0) return null;
    // `mapped_depots` (server >= MAP-FROM-MANIFEST-1) tells "no depots known"
    // from "all depots shared"; an older server omits it and keeps the old line.
    return Number(entry.mapped_depots) > 0
      ? "Speed not measured: this game's depots are shared with other apps."
      : "Speed not measured yet: the server does not know this game's depots yet.";
  }
  const speed = formatSpeed(entry.bytes_per_second);
  if (speed === null) return null;
  const sofar = formatTrafficBytes(entry.bytes_since_start);
  const eta = formatEta(jobEtaSeconds(entry, { verifying, nowMs, rate: etaRate }));
  const tail = eta ? ` · ${eta}` : "";
  // WP JOB-SIZE-1: a first download counts the oracle's expected depots and
  // names their expected download size; marked, since the server has not
  // mapped the game yet.
  const total = entry.expected === true ? formatTrafficBytes(entry.expected_download_bytes) : null;
  if (sofar && total) return `${speed} · ${sofar} of ~${total} loaded so far` + EXPECTED_SIZE_NOTE + tail;
  return (sofar ? `${speed} · ${sofar} loaded so far` : speed) + tail;
}

// ---------------------------------------------------------------------
// History filter (GET /v1/jobs?range=, WP D15b)
// ---------------------------------------------------------------------

const RANGE_MS = Object.freeze({ "24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000 });
const IN_FLIGHT = new Set(["queued", "running", "paused"]);

/**
 * The server's history rule, client-side, for the rows the store's poll
 * holds: queued/running/paused jobs always stay; a finished job stays when
 * its finish (else start, else creation) is within the range. A job with
 * no readable time stays (hiding it would be a guess).
 * @param {Array<object>} jobs
 * @param {string} range
 * @param {number} [nowMs]
 * @returns {Array<object>}
 */
export function filterJobsByRange(jobs, range, nowMs = Date.now()) {
  if (!Array.isArray(jobs)) return [];
  const span = RANGE_MS[range];
  if (!span) return jobs.slice();
  const cutoff = nowMs - span;
  return jobs.filter((job) => {
    if (IN_FLIGHT.has(job.status)) return true;
    const t = Date.parse(job.finished_at || job.started_at || job.created_at || "");
    return !Number.isFinite(t) || t >= cutoff;
  });
}

/**
 * One job list from the store's poll (newest 20, refreshed every few
 * seconds) and the history fetch (`?range=`, up to 200, fetched on demand).
 * The same job in both keeps the store's copy, which is the newer one. The
 * store's rows keep their order and come first (they are the newest), the
 * fetched rows the store does not hold follow in their own order — both
 * lists are newest first as `GET /v1/jobs` returns them.
 * @param {Array<object>} fresh
 * @param {Array<object>} deep
 * @returns {Array<object>}
 */
export function mergeJobLists(fresh, deep) {
  const out = Array.isArray(fresh) ? fresh.slice() : [];
  const seen = new Set(out.map((j) => j.id));
  for (const job of Array.isArray(deep) ? deep : []) {
    if (!seen.has(job.id)) {
      seen.add(job.id);
      out.push(job);
    }
  }
  return out;
}

/** The history list's empty line for a range. */
export function emptyHistoryText(range) {
  return range === "all" || !RANGE_PHRASES[range] ? "Nothing finished yet." : `Nothing finished ${RANGE_PHRASES[range]}.`;
}

// ---------------------------------------------------------------------
// Live view (GET /v1/throughput/live, WP GRAPH-LIVE)
// ---------------------------------------------------------------------

/** The live view's poll cadence (only while the page is visible). */
export const LIVE_POLL_MS = 3000;

/** How often the live view refreshes its persisted 1 h summary tiles. */
export const LIVE_SUMMARY_MS = 30000;

/** The persisted range whose summary backs the live view's tiles. */
export const LIVE_SUMMARY_RANGE = "1h";

/**
 * The live answer as a series the graph can draw. The stitched points have
 * 5, 15 and 60 s buckets; `seriesGeometry` spaces points evenly, so every
 * point is repeated to fill a uniform 5 s grid (a 15 s point three times, a
 * minute twelve times) and the time axis stays true.
 * @param {object | null | undefined} live
 * @returns {object | null}
 */
export function liveSeries(live) {
  if (!live || !Array.isArray(live.points)) return null;
  const points = [];
  for (const p of live.points) {
    const repeat = Math.max(1, Math.round((p.bucket_seconds || 5) / 5));
    for (let i = 0; i < repeat; i += 1) points.push(p);
  }
  return {
    event_feed: live.event_feed,
    range: "live",
    tier: "live",
    bucket_seconds: 5,
    range_start: live.range_start,
    points,
  };
}

/**
 * The header's figures for the live view: the persisted 1 h summary with
 * "Current speed" and "Loading now" taken from the live tier, and (WP
 * NOW-LOADING-1) `loading_sources`, what is loading, from the live answer
 * only (`null` without it). `null` until the summary is in.
 * @param {object | null | undefined} summary `GET /v1/throughput?range=1h`
 * @param {object | null | undefined} live
 * @returns {object | null}
 */
export function mergeLive(summary, live) {
  if (!summary) return null;
  // WP NOW-LOADING-1: "what is loading now" only from the live answer.
  const merged = { ...summary, range: "live", live: false, loading_sources: null };
  if (live && live.current && feedState(live) === "ok") {
    merged.current = live.current;
    merged.live = true;
    if (Array.isArray(live.loading_sources)) merged.loading_sources = live.loading_sources;
    // "Loading now" from the ring too: the persisted count lags a sweep.
    if (Number.isInteger(live.active_clients)) {
      merged.active_clients_now = live.active_clients;
      if (Number.isInteger(live.active_window_minutes)) merged.active_window_minutes = live.active_window_minutes;
      // WP LOADING-NOW-1: the count's window is the current-speed window.
      merged.active_window_seconds = Number.isInteger(live.active_window_seconds) ? live.active_window_seconds : null;
    }
  }
  return merged;
}

/**
 * WP GRAPH-RECENT-1 + LOADING-NOW-1: a persisted range's answer
 * (`GET /v1/throughput?range=1h|6h|...`) with "Current speed", the status
 * pill's runner figure and "Loading now" taken from its `now` block (the
 * live tier, one window) instead of the last sweep, so the header agrees
 * with the graph's newest point (which the server stitches from the same
 * ring). Unchanged without a `now` block (feed not `ok`, a server before
 * rc21, `null`). Pure; the input is not modified.
 * @param {object | null | undefined} throughput
 * @returns {object | null | undefined}
 */
export function mergeNow(throughput) {
  const now = throughput && throughput.now;
  if (!now || !now.current || feedState(throughput) !== "ok") return throughput;
  const merged = { ...throughput, current: now.current, live: true };
  if (Number.isInteger(now.active_clients)) {
    merged.active_clients_now = now.active_clients;
    merged.active_window_seconds = Number.isInteger(now.window_seconds) ? now.window_seconds : null;
  }
  if (typeof now.runner_active === "boolean") merged.runner_active_now = now.runner_active;
  // WP NOW-LOADING-1: one row per source, from the same read as the figures.
  if (Array.isArray(now.loading_sources)) merged.loading_sources = now.loading_sources;
  return merged;
}
