/**
 * A minimal, real-shaped answer for the three statistics routes the
 * Downloads view polls (`GET /v1/throughput`, `/v1/throughput/series`,
 * `/v1/throughput/live`): the event-log feed is off (VAULT_EVENT_LOG unset),
 * the shapes of api/vault_api/routers/traffic.py (`ThroughputOut`,
 * `SeriesOut`, `LiveOut`) with every figure null or empty.
 *
 * For harnesses that test something else on the Downloads page. They used to
 * answer these routes with a 404, which stopped the statistics poll only
 * through the old "older server" path; OLD-SERVER-FALLBACK-1 removed that,
 * so no test may depend on an error path to terminate (review nit 8). The
 * poll itself keeps running while the view is mounted; the harnesses leave
 * the view in `after()`.
 */

const FEED_OFF = Object.freeze({
  enabled: false,
  state: "disabled",
  last_sweep_at: null,
  sweep_interval_minutes: 5,
  traffic_since: null,
  traffic_1m_since: null,
});

/**
 * The answer for a statistics path, or `null` for any other path.
 * @param {URL} url the request URL (its `range` query parameter is echoed)
 * @returns {object | null}
 */
export function throughputFixture(url) {
  const range = url.searchParams.get("range") || "24h";
  if (url.pathname === "/v1/throughput") {
    return {
      event_feed: { ...FEED_OFF },
      range,
      tier: "5m",
      bucket_seconds: 300,
      range_start: null,
      active_window_minutes: 15,
      current: null,
      active_clients_now: null,
      runner_active_now: null,
      summary: null,
      jobs: [],
      now: null,
    };
  }
  if (url.pathname === "/v1/throughput/series") {
    return { event_feed: { ...FEED_OFF }, range, tier: "5m", bucket_seconds: 300, range_start: null, points: [] };
  }
  if (url.pathname === "/v1/throughput/live") {
    return {
      event_feed: { ...FEED_OFF },
      range: "live",
      tier: "live",
      watcher_window_seconds: 15,
      live_since: null,
      range_start: null,
      current: null,
      active_clients: null,
      active_window_seconds: null,
      active_window_minutes: 1,
      loading_sources: null,
      points: [],
    };
  }
  // WP CAP-503-FIX-1b: the Downloads view also polls the client-priority
  // hold while a job is active; with the feed off nothing is ever held.
  if (url.pathname === "/v1/client-priority") {
    return {
      enabled: false,
      holding: false,
      since: null,
      last_client_at: null,
      resume_at: null,
      idle_gap_seconds: 120,
      clients: [],
      paused_job_ids: [],
      override_job_ids: [],
      restored: false,
      runner_address_unknown: false,
    };
  }
  return null;
}
