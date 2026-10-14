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
 */

import { formatAgo, formatBytesGB } from "./format.js";

/** The four ranges every traffic endpoint and the jobs filter accept. */
export const RANGES = Object.freeze(["24h", "7d", "30d", "all"]);

/** Segmented-control labels, in `RANGES` order. */
export const RANGE_LABELS = Object.freeze({ "24h": "24 h", "7d": "7 days", "30d": "30 days", all: "All" });

/** Phrases for running text ("1.2 GB loaded in the last 7 days"). */
export const RANGE_PHRASES = Object.freeze({
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
 * @returns {Array<{key: string, label: string, value: string, title: string, detail: string | null}> | null}
 */
export function headerTiles(throughput) {
  if (!throughput || feedState(throughput) === "disabled") return null;
  const summary = throughput.summary || {};
  const current = throughput.current ? throughput.current.bytes_per_second : null;
  const phrase = RANGE_PHRASES[throughput.range] || "";
  const tile = (key, label, value, title, detail = null) => ({ key, label, value: value ?? DASH, title, detail });
  const inRange = Number.isInteger(summary.active_clients) ? summary.active_clients : null;
  return [
    tile(
      "current",
      "Current speed",
      formatSpeed(current),
      "Everything the cache served over the last few minutes, measured when the server last read the cache's event log.",
    ),
    tile(
      "average",
      "Average while loading",
      formatSpeed(summary.average_bytes_per_second),
      `Average speed ${phrase}, counting only the 5-minute periods in which something was loaded.`,
    ),
    tile("peak", "Peak speed", formatSpeed(summary.peak_bytes_per_second), `The busiest 5 minutes ${phrase}.`),
    tile(
      "loaded",
      "Loaded",
      formatTrafficBytes(summary.bytes_served),
      `Everything the cache served ${phrase}, the server's own prefill downloads included.`,
      runnerDetail(summary),
    ),
    tile(
      "hit-rate",
      "Hit rate",
      // Bytes, not requests: the same base as "Saved by the cache" next to it.
      formatRate(summary.byte_hit_rate),
      `Share of the bytes your machines loaded ${phrase} that came from the cache. The server's own prefill downloads are not counted.`,
    ),
    tile(
      "saved",
      "Saved by the cache",
      formatTrafficBytes(summary.hit_bytes),
      `Bytes your machines loaded ${phrase} from the cache's disk instead of the internet. The server's own prefill downloads are not counted.`,
    ),
    tile(
      "clients",
      "Active clients",
      Number.isInteger(throughput.active_clients_now) ? String(throughput.active_clients_now) : null,
      "Machines that loaded from the cache in the last few minutes. The server's own prefill runner is not counted.",
      inRange === null ? null : `${inRange} ${phrase}`,
    ),
  ];
}

/**
 * WP D15-RUNNER: "of which X by the prefill runner" under "Loaded"; `null`
 * when the server sent no runner figures (older vault-api) or the runner
 * loaded nothing in the range.
 * @param {object} summary
 * @returns {string | null}
 */
export function runnerDetail(summary) {
  const runner = summary && summary.prefill_runner;
  if (!runner || !Number.isFinite(runner.bytes_served) || runner.bytes_served <= 0) return null;
  const size = formatTrafficBytes(runner.bytes_served);
  return size ? `${size} by the prefill runner` : null;
}

/** The bucket a series point covers, for the graph's label. */
export function bucketWord(bucketSeconds) {
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
 * Left-edge caption for the graph: how far back it starts. "all" names the
 * first day instead of a duration.
 * @param {{range?: string, points?: Array<{t: string}>}} series
 * @returns {string}
 */
export function seriesStartText(series) {
  if (series.range === "24h") return "24 h ago";
  if (series.range === "7d") return "7 days ago";
  if (series.range === "30d") return "30 days ago";
  const first = Array.isArray(series.points) && series.points[0] ? series.points[0].t : null;
  const day = typeof first === "string" ? first.slice(0, 10) : null;
  return day ? `since ${day}` : "";
}

/**
 * The line under a running job's card: its speed at the cache (WP D13
 * SPEED-1, measured per job by depot in `GET /v1/throughput`'s `jobs`).
 * `null` when there is nothing honest to say: no throughput answer yet, the
 * job not listed, or the feed not `ok` (the header's note explains that).
 * @param {object | null | undefined} entry one `jobs[]` element
 * @param {string} state `feedState(...)` of the same answer
 * @returns {string | null}
 */
export function jobSpeedText(entry, state) {
  if (!entry || state !== "ok") return null;
  if (entry.bytes_per_second === null || entry.bytes_per_second === undefined) {
    return entry.exclusive_depots === 0
      ? "Speed not measured yet: the server does not know this game's depots yet."
      : null;
  }
  const speed = formatSpeed(entry.bytes_per_second);
  if (speed === null) return null;
  const sofar = formatTrafficBytes(entry.bytes_since_start);
  return sofar ? `${speed} · ${sofar} loaded so far` : speed;
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
