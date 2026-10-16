/**
 * Demo-mode traffic fixtures (WP D15c): `GET /v1/throughput`,
 * `GET /v1/throughput/series` and `GET /v1/clients/overview` (WP D15b,
 * `api/vault_api/routers/traffic.py`), served by `demo-data.js`'s
 * `demoRequest`. DOM-free, like demo-data.js.
 *
 * Keys equal the server's Pydantic models (`ThroughputOut`, `SummaryOut`,
 * `CurrentOut`, `JobThroughputOut`, `SeriesOut`, `SeriesPointOut`,
 * `ClientsOverviewOut`, `ClientActivityOut`, ...), pinned by
 * web/tests/demo-data-traffic.test.js. The numbers are synthetic and
 * deterministic per range (no randomness): a daily rhythm with an evening
 * peak, scaled so the header, the graph and the client cards tell one
 * consistent story — workshop-pc downloading the running demo job's game,
 * loft-laptop offline, and two agentless addresses (one active, one not).
 * The feed is always `ok` in demo mode.
 *
 * WP D15-RUNNER: the server's prefill runner is its own overview entry
 * (`kind: "runner"`), and its bytes stay out of every client figure: a
 * tenth of each bucket is the runner's (`runner_bytes`), hit and miss bytes
 * are the clients' share only, and the summary carries `client_bytes_served`
 * and `prefill_runner` (bytes_served = client_bytes_served + runner bytes).
 *
 * WP D15-NAMES: "Delete history" in demo mode passes `sinceMs` (traffic
 * before it was deleted): series buckets that start before it are zero,
 * `traffic_since` moves up to it, and the per-client figures shrink to the
 * part of the range after it. Client rows carry the server's name fields
 * (`name`, `name_addr`, `display_name`).
 */

const MIB = 1024 * 1024;
const GIB = 1024 * MIB;

// WP GRAPH-ZOOM: "1h" / "6h" read the 1-minute tier. `loadingMinutes` is how
// long ago the demo's current download began: older minutes are empty, so
// the graph's trimmed X axis is visible (the span with data, not the range).
const TIERS = Object.freeze({
  "1h": { tier: "1m", bucket: 60, count: 60, loadingMinutes: 40 },
  "6h": { tier: "1m", bucket: 60, count: 360, loadingMinutes: 150 },
  "24h": { tier: "5m", bucket: 300, count: 288 },
  "7d": { tier: "1h", bucket: 3600, count: 168 },
  "30d": { tier: "1h", bucket: 3600, count: 720 },
  all: { tier: "1d", bucket: 86400, count: 45 },
});

/** Range -> how many seconds it spans; demo "all" covers 45 days. */
function tierFor(range) {
  return TIERS[range] || TIERS["24h"];
}

function isoSeconds(ms) {
  return new Date(Math.floor(ms / 1000) * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

function floorTo(ms, seconds) {
  const step = seconds * 1000;
  return Math.floor(ms / step) * step;
}

/** Deterministic bytes/s for a bucket starting at `ms`: idle at night, an
 * evening peak, nothing on some buckets. */
function rateAt(ms, bucketSeconds) {
  const hour = new Date(ms).getUTCHours() + new Date(ms).getUTCMinutes() / 60;
  const day = Math.floor(ms / 86_400_000);
  const evening = Math.max(0, Math.sin(((hour - 12) / 12) * Math.PI)); // 12..24h
  const dayFactor = 0.55 + 0.45 * Math.abs(Math.sin(day * 1.7));
  const gap = bucketSeconds <= 300 && Math.floor(ms / 300_000) % 7 === 3;
  if (gap || evening < 0.08) return 0;
  return Math.round(evening * dayFactor * 46 * MIB);
}

/** Deterministic bytes/s of the 1-minute bucket `age` minutes before the
 * newest one: nothing before the download began, a wavy rate since, and a
 * short pause now and then. */
function rateMinute(age, loadingMinutes) {
  if (age > loadingMinutes || age % 11 === 5) return 0;
  return Math.round((0.55 + 0.45 * Math.abs(Math.sin(age * 0.7))) * 38 * MIB);
}

function feed(nowMs, sinceMs = null) {
  const recorded = floorTo(nowMs - 45 * 86_400_000, 86400);
  return {
    enabled: true,
    state: "ok",
    // A sweep a minute ago: the feed is `ok` in demo mode.
    last_sweep_at: isoSeconds(nowMs - 60_000),
    sweep_interval_minutes: 5,
    traffic_since: sinceMs === null || sinceMs <= recorded ? isoSeconds(recorded) : isoSeconds(sinceMs),
    // The 1-minute tier started recording a day and a half ago in the demo.
    traffic_1m_since: isoSeconds(nowMs - 36 * 3_600_000),
  };
}

/** `GET /v1/throughput/series` (demo). */
export function demoSeries(range, nowMs = Date.now(), sinceMs = null) {
  const t = tierFor(range);
  const last = floorTo(nowMs, t.bucket);
  const points = [];
  for (let i = t.count - 1; i >= 0; i--) {
    const start = last - i * t.bucket * 1000;
    const raw = t.tier === "1m" ? rateMinute(i, t.loadingMinutes) : rateAt(start, t.bucket);
    const bps = sinceMs !== null && start < sinceMs ? 0 : raw;
    const served = Math.round(bps * t.bucket);
    const runner = Math.round(served * 0.1);
    const clients = served - runner;
    const hit = Math.round(clients * 0.82);
    points.push({
      t: isoSeconds(start),
      bytes_served: served,
      hit_bytes: hit,
      miss_bytes: clients - hit,
      requests: Math.round(clients / (1.4 * MIB)),
      runner_bytes: runner,
      bytes_per_second: bps,
      peak_bytes_per_second: t.bucket <= 300 ? bps : Math.round(bps * 1.35),
    });
  }
  return {
    event_feed: feed(nowMs, sinceMs),
    range: TIERS[range] ? range : "24h",
    tier: t.tier,
    bucket_seconds: t.bucket,
    range_start: points.length ? points[0].t : null,
    points,
  };
}

/**
 * `GET /v1/throughput/live` (demo, WP GRAPH-LIVE): the stitched series the
 * server builds, in the same geometry (5 s for the last minute, 15 s up to
 * 5 minutes, then 1-minute points up to an hour), with a deterministic wavy
 * rate so the demo graph moves.
 */
export function demoLive(nowMs = Date.now(), sinceMs = null) {
  const fineStart = floorTo(nowMs, 15) - 45_000;
  const mediumStart = floorTo(fineStart, 60) - 180_000;
  const coarseStart = mediumStart - 3_240_000;
  const spans = [];
  for (let at = coarseStart; at < mediumStart; at += 60_000) spans.push([at, 60, "persisted"]);
  for (let at = mediumStart; at < fineStart; at += 15_000) spans.push([at, 15, "live"]);
  for (let at = fineStart; at <= floorTo(nowMs, 5); at += 5_000) spans.push([at, 5, "live"]);
  const points = spans.map(([start, bucket, source]) => {
    const age = Math.floor((nowMs - start) / 60_000);
    const wave = 0.6 + 0.4 * Math.abs(Math.sin(start / 9_000));
    const bps = sinceMs !== null && start < sinceMs ? 0 : age > 30 ? 0 : Math.round(wave * 36 * MIB);
    const served = Math.round(bps * bucket);
    const runner = Math.round(served * 0.1);
    const clients = served - runner;
    const hit = Math.round(clients * 0.82);
    return {
      t: isoSeconds(start),
      bucket_seconds: bucket,
      source,
      bytes_served: served,
      hit_bytes: hit,
      miss_bytes: clients - hit,
      requests: Math.round(clients / (1.4 * MIB)),
      runner_bytes: runner,
      bytes_per_second: bps,
      peak_bytes_per_second: bps,
    };
  });
  const recent = points.slice(-4);
  const windowSeconds = 15 + Math.floor((nowMs % 5000) / 1000);
  const bps = recent.reduce((sum, p) => sum + p.bytes_per_second, 0) / recent.length;
  return {
    event_feed: feed(nowMs, sinceMs),
    range: "live",
    tier: "live",
    watcher_window_seconds: 30,
    live_since: isoSeconds(nowMs - 15 * 60_000),
    range_start: points[0].t,
    current: {
      bytes_per_second: bps,
      runner_bytes_per_second: bps * 0.1,
      window_start: isoSeconds(nowMs - windowSeconds * 1000),
      window_end: isoSeconds(nowMs),
      window_seconds: windowSeconds,
    },
    active_clients: 2,
    active_window_minutes: 5,
    points,
  };
}

function summaryFor(range, nowMs, sinceMs) {
  const { points, bucket_seconds: bucket } = demoSeries(range, nowMs, sinceMs);
  let served = 0;
  let runner = 0;
  let hit = 0;
  let requests = 0;
  let active = 0;
  let peak = 0;
  for (const p of points) {
    served += p.bytes_served;
    runner += p.runner_bytes;
    hit += p.hit_bytes;
    requests += p.requests;
    if (p.bytes_served > 0) active += bucket / 300;
    peak = Math.max(peak, p.peak_bytes_per_second);
  }
  const hits = Math.round(requests * 0.83);
  const clients = served - runner;
  const runnerHit = Math.round(runner * 0.6);
  const runnerRequests = Math.round(runner / (1.4 * MIB));
  const runnerHits = Math.round(runnerRequests * 0.6);
  return {
    bytes_served: served,
    hit_bytes: hit,
    miss_bytes: clients - hit,
    requests,
    hits,
    misses: requests - hits,
    errors: Math.round(requests / 4000),
    hit_rate: requests ? hits / requests : null,
    byte_hit_rate: clients ? hit / clients : null,
    average_bytes_per_second: active ? served / (active * 300) : null,
    peak_bytes_per_second: peak,
    active_clients: served === 0 ? 0 : range === "24h" ? 3 : 4,
    client_bytes_served: clients,
    internet_bytes: clients - hit + (runner - runnerHit),
    prefill_runner: {
      bytes_served: runner,
      hit_bytes: runnerHit,
      miss_bytes: runner - runnerHit,
      requests: runnerRequests,
      hits: runnerHits,
      misses: runnerRequests - runnerHits,
      byte_hit_rate: runner ? runnerHit / runner : null,
      errors: 0,
    },
  };
}

/** `GET /v1/throughput` (demo). `runningJobs`: `[{id, appid, started_at,
 * name}]`; each gets a speed while the workshop PC loads it. */
export function demoThroughput(range, runningJobs, nowMs = Date.now(), sinceMs = null) {
  const t = tierFor(range);
  const f = feed(nowMs, sinceMs);
  const end = Date.parse(f.last_sweep_at);
  const windowSeconds = 300 + (Math.floor(end / 1000) % 300);
  const jobs = runningJobs.map((j) => ({
    job_id: j.id,
    appid: j.appid,
    name: j.name ?? null,
    started_at: j.started_at ?? null,
    bytes_per_second: 31.5 * MIB,
    bytes_since_start: Math.round(8.8 * GIB),
    exclusive_depots: 1,
    mapped_depots: 1,
  }));
  return {
    event_feed: f,
    range: TIERS[range] ? range : "24h",
    tier: t.tier,
    bucket_seconds: t.bucket,
    range_start: demoSeries(range, nowMs, sinceMs).range_start,
    active_window_minutes: 15,
    current: {
      bytes_per_second: jobs.length ? 38.2 * MIB : 2.1 * MIB,
      window_start: isoSeconds(end - windowSeconds * 1000),
      window_end: isoSeconds(end),
      window_seconds: windowSeconds,
      runner_bytes_per_second: jobs.length ? 6.4 * MIB : 0,
    },
    active_clients_now: 2,
    runner_active_now: jobs.length > 0,
    summary: summaryFor(range, nowMs, sinceMs),
    jobs,
  };
}

function stats(bytes, hitRate) {
  const hit = Math.round(bytes * hitRate);
  const requests = Math.round(bytes / (1.4 * MIB));
  const hits = Math.round(requests * hitRate);
  return {
    bytes_served: bytes,
    hit_bytes: hit,
    miss_bytes: bytes - hit,
    requests,
    hits,
    misses: requests - hits,
    hit_rate: requests ? hits / requests : null,
  };
}

const RANGE_SCALE = Object.freeze({ "24h": 1, "7d": 4.2, "30d": 13, all: 19 });

/** Share of `range` (0..1) that lies after `sinceMs` (WP D15-NAMES), in
 * whole buckets: right after "Delete everything" it is exactly 0. */
function keptShare(range, nowMs, sinceMs) {
  if (sinceMs === null || sinceMs === undefined) return 1;
  const t = tierFor(range);
  const buckets = Math.floor((nowMs - sinceMs) / (t.bucket * 1000));
  return Math.max(0, Math.min(1, buckets / t.count));
}

/**
 * `GET /v1/clients/overview` (demo).
 * @param {string} range
 * @param {{agents: Array<object>, runningGame: {appid: number, name: string|null} | null,
 *   games: Array<{appid: number, name: string|null}>, installed: Map<string, number[]>}} ctx
 *   `agents` are demo `GET /v1/clients` rows (with presence).
 */
export function demoClientsOverview(range, ctx, nowMs = Date.now()) {
  const sinceMs = ctx.historySinceMs ?? null;
  const f = feed(nowMs, sinceMs);
  const scale = (RANGE_SCALE[range] || 1) * keptShare(range, nowMs, sinceMs);
  const addressName = typeof ctx.addressName === "function" ? ctx.addressName : (addr) => ({ name: null, name_addr: addr, display_name: addr });
  const named = (appid) => {
    const g = ctx.games.find((x) => x.appid === appid);
    return g ? g.name ?? null : null;
  };
  const recent = (appids, base) =>
    appids.map((appid, i) => ({
      appid,
      name: named(appid),
      bytes_served: Math.round(base * scale / (i + 1)),
      last_seen: isoSeconds(nowMs - (i + 1) * 3 * 3_600_000),
    }));
  const idle = { loading: false, bytes_per_second: 0, apps: [], unattributed_bytes_per_second: 0 };
  const gameIds = ctx.games.map((g) => g.appid);
  const clients = ctx.agents.map((a) => {
    const online = a.presence === "online";
    const loadingGame = online && ctx.runningGame ? ctx.runningGame : null;
    const installed = ctx.installed.get(a.client_id) || [];
    const lastCache = online ? isoSeconds(nowMs - 60_000) : a.last_seen_in_cache_log;
    const lastActive = [lastCache, a.last_reported_at].filter(Boolean).sort().pop() || null;
    return {
      key: `agent:${a.client_id}`,
      kind: "agent",
      client_id: a.client_id,
      addresses: a.source_addrs.slice(0, 1),
      connected: online,
      agent_presence: a.presence,
      cache_active: online,
      last_active: lastActive,
      last_seen_in_cache_log: lastCache,
      last_reported_at: a.last_reported_at,
      installed_appids: installed,
      installed_app_count: installed.length,
      stats: online ? stats(Math.round(14.6 * GIB * scale), 0.81) : stats(range === "24h" ? 0 : Math.round(0.3 * GIB * scale), 0.5),
      loading_now: loadingGame
        ? {
            loading: true,
            bytes_per_second: 33.7 * MIB,
            apps: [{ appid: loadingGame.appid, name: loadingGame.name ?? null, bytes_per_second: 31.5 * MIB }],
            unattributed_bytes_per_second: 2.2 * MIB,
          }
        : idle,
      recent_apps:
        scale <= 0 ? [] : online ? recent(gameIds.slice(0, 4), 6 * GIB) : range === "24h" ? [] : recent(gameIds.slice(3, 4), 0.3 * GIB),
      recent_unattributed_bytes: online ? Math.round(0.4 * GIB * scale) : 0,
      // The demo /v1/clients row already carries the server's name fields.
      name: a.name ?? null,
      name_addr: a.name_addr ?? null,
      display_name: a.display_name ?? a.client_id,
    };
  });
  // Two agentless addresses: a machine loading right now, and one last
  // active days ago (listed: seen within 30 days).
  clients.push({
    key: "addr:10.10.0.57",
    kind: "address",
    client_id: null,
    addresses: ["10.10.0.57"],
    connected: true,
    agent_presence: null,
    cache_active: true,
    last_active: isoSeconds(nowMs - 2 * 60_000),
    last_seen_in_cache_log: isoSeconds(nowMs - 2 * 60_000),
    last_reported_at: null,
    installed_appids: null,
    installed_app_count: null,
    stats: stats(Math.round(3.1 * GIB * scale), 0.92),
    loading_now: { loading: true, bytes_per_second: 4.5 * MIB, apps: [], unattributed_bytes_per_second: 4.5 * MIB },
    recent_apps: scale > 0 ? recent(gameIds.slice(0, 1), 2.4 * GIB) : [],
    recent_unattributed_bytes: Math.round(0.7 * GIB * scale),
    ...addressName("10.10.0.57"),
  });
  const oldMs = nowMs - 4 * 86_400_000;
  const oldInRange = range !== "24h" && (sinceMs === null || oldMs >= sinceMs);
  clients.push({
    key: "addr:10.10.0.63",
    kind: "address",
    client_id: null,
    addresses: ["10.10.0.63"],
    connected: false,
    agent_presence: null,
    cache_active: false,
    last_active: isoSeconds(oldMs),
    last_seen_in_cache_log: isoSeconds(oldMs),
    last_reported_at: null,
    installed_appids: null,
    installed_app_count: null,
    stats: stats(oldInRange ? Math.round(1.2 * GIB) : 0, 0.64),
    loading_now: idle,
    recent_apps: oldInRange ? recent(gameIds.slice(1, 2), 1.2 * GIB / scale) : [],
    recent_unattributed_bytes: 0,
    ...addressName("10.10.0.63"),
  });
  // WP D15-RUNNER: the server's own prefill runner, loading the running
  // demo job's game while one runs.
  const runnerGame = ctx.runningGame || null;
  const runnerSeen = runnerGame ? isoSeconds(nowMs - 60_000) : isoSeconds(nowMs - 9 * 3_600_000);
  clients.push({
    key: "runner",
    kind: "runner",
    client_id: null,
    addresses: ["172.18.0.1"],
    connected: !!runnerGame,
    agent_presence: null,
    cache_active: !!runnerGame,
    last_active: runnerSeen,
    last_seen_in_cache_log: runnerSeen,
    last_reported_at: null,
    installed_appids: null,
    installed_app_count: null,
    stats: stats(Math.round(4.8 * GIB * scale), 0.6),
    loading_now: runnerGame
      ? {
          loading: true,
          bytes_per_second: 12.4 * MIB,
          apps: [{ appid: runnerGame.appid, name: runnerGame.name ?? null, bytes_per_second: 12.4 * MIB }],
          unattributed_bytes_per_second: 0,
        }
      : idle,
    recent_apps: scale > 0 ? recent(gameIds.slice(0, 2), 3 * GIB) : [],
    recent_unattributed_bytes: 0,
    name: null,
    name_addr: null,
    display_name: "Prefill runner",
  });
  // Server order: connected first, then newest last_active.
  clients.sort((a, b) => Number(b.connected === true) - Number(a.connected === true) || String(b.last_active).localeCompare(String(a.last_active)));
  return {
    event_feed: f,
    range: TIERS[range] ? range : "7d",
    tier: tierFor(range).tier,
    range_start: demoSeries(range, nowMs, sinceMs).range_start,
    active_window_minutes: 15,
    clients,
  };
}
