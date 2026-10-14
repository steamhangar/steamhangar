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
 */

const MIB = 1024 * 1024;
const GIB = 1024 * MIB;

const TIERS = Object.freeze({
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

function feed(nowMs) {
  return {
    enabled: true,
    state: "ok",
    // A sweep a minute ago: the feed is `ok` in demo mode.
    last_sweep_at: isoSeconds(nowMs - 60_000),
    sweep_interval_minutes: 5,
    traffic_since: isoSeconds(floorTo(nowMs - 45 * 86_400_000, 86400)),
  };
}

/** `GET /v1/throughput/series` (demo). */
export function demoSeries(range, nowMs = Date.now()) {
  const t = tierFor(range);
  const last = floorTo(nowMs, t.bucket);
  const points = [];
  for (let i = t.count - 1; i >= 0; i--) {
    const start = last - i * t.bucket * 1000;
    const bps = rateAt(start, t.bucket);
    const served = Math.round(bps * t.bucket);
    const hit = Math.round(served * 0.82);
    points.push({
      t: isoSeconds(start),
      bytes_served: served,
      hit_bytes: hit,
      miss_bytes: served - hit,
      requests: Math.round(served / (1.4 * MIB)),
      bytes_per_second: bps,
      peak_bytes_per_second: t.bucket === 300 ? bps : Math.round(bps * 1.35),
    });
  }
  return {
    event_feed: feed(nowMs),
    range: TIERS[range] ? range : "24h",
    tier: t.tier,
    bucket_seconds: t.bucket,
    range_start: points.length ? points[0].t : null,
    points,
  };
}

function summaryFor(range, nowMs) {
  const { points, bucket_seconds: bucket } = demoSeries(range, nowMs);
  let served = 0;
  let hit = 0;
  let requests = 0;
  let active = 0;
  let peak = 0;
  for (const p of points) {
    served += p.bytes_served;
    hit += p.hit_bytes;
    requests += p.requests;
    if (p.bytes_served > 0) active += bucket / 300;
    peak = Math.max(peak, p.peak_bytes_per_second);
  }
  const hits = Math.round(requests * 0.83);
  return {
    bytes_served: served,
    hit_bytes: hit,
    miss_bytes: served - hit,
    requests,
    hits,
    misses: requests - hits,
    errors: Math.round(requests / 4000),
    hit_rate: requests ? hits / requests : null,
    byte_hit_rate: served ? hit / served : null,
    average_bytes_per_second: active ? served / (active * 300) : null,
    peak_bytes_per_second: peak,
    active_clients: range === "24h" ? 3 : 4,
  };
}

/** `GET /v1/throughput` (demo). `runningJobs`: `[{id, appid, started_at,
 * name}]`; each gets a speed while the workshop PC loads it. */
export function demoThroughput(range, runningJobs, nowMs = Date.now()) {
  const t = tierFor(range);
  const f = feed(nowMs);
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
  }));
  return {
    event_feed: f,
    range: TIERS[range] ? range : "24h",
    tier: t.tier,
    bucket_seconds: t.bucket,
    range_start: demoSeries(range, nowMs).range_start,
    active_window_minutes: 15,
    current: {
      bytes_per_second: jobs.length ? 38.2 * MIB : 2.1 * MIB,
      window_start: isoSeconds(end - windowSeconds * 1000),
      window_end: isoSeconds(end),
      window_seconds: windowSeconds,
    },
    active_clients_now: 2,
    summary: summaryFor(range, nowMs),
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

/**
 * `GET /v1/clients/overview` (demo).
 * @param {string} range
 * @param {{agents: Array<object>, runningGame: {appid: number, name: string|null} | null,
 *   games: Array<{appid: number, name: string|null}>, installed: Map<string, number[]>}} ctx
 *   `agents` are demo `GET /v1/clients` rows (with presence).
 */
export function demoClientsOverview(range, ctx, nowMs = Date.now()) {
  const f = feed(nowMs);
  const scale = RANGE_SCALE[range] || 1;
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
      recent_apps: online ? recent(gameIds.slice(0, 4), 6 * GIB) : range === "24h" ? [] : recent(gameIds.slice(3, 4), 0.3 * GIB),
      recent_unattributed_bytes: online ? Math.round(0.4 * GIB * scale) : 0,
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
    recent_apps: recent(gameIds.slice(0, 1), 2.4 * GIB),
    recent_unattributed_bytes: Math.round(0.7 * GIB * scale),
  });
  const oldMs = nowMs - 4 * 86_400_000;
  const oldInRange = range !== "24h";
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
  });
  // Server order: connected first, then newest last_active.
  clients.sort((a, b) => Number(b.connected === true) - Number(a.connected === true) || String(b.last_active).localeCompare(String(a.last_active)));
  return {
    event_feed: f,
    range: TIERS[range] ? range : "7d",
    tier: tierFor(range).tier,
    range_start: demoSeries(range, nowMs).range_start,
    active_window_minutes: 15,
    clients,
  };
}
