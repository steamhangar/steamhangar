package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * vault-api's traffic endpoints (WP D15b, `api/vault_api/routers/traffic.py`):
 * `GET /v1/throughput`, `GET /v1/throughput/series` and
 * `GET /v1/clients/overview`, each with `?range=24h|7d|30d|all`. Field
 * names equal the server's Pydantic models so `VaultJson` maps them 1:1.
 *
 * Honest states (api/README.md "event_feed"): with the feed `disabled`
 * every traffic field is `null`, and `null` means "not measured", never
 * zero; with `never-swept`/`stale` the "current" values are `null`. The
 * presentation rules live in `ui/traffic/logic/TrafficView.kt`. Every
 * nullable field below therefore defaults to `null`, and every list to
 * empty, so an older or newer server never fails the whole decode.
 */
@Serializable
data class EventFeedOut(
    val enabled: Boolean = false,
    /** `disabled` | `never-swept` | `ok` | `stale`. */
    val state: String = "disabled",
    val last_sweep_at: String? = null,
    val sweep_interval_minutes: Int = 5,
    val traffic_since: String? = null,
    /** WP GRAPH-ZOOM: when the 1-minute tier (1 h / 6 h ranges) started
     * filling; `null` before its first row or from an older server. */
    val traffic_1m_since: String? = null,
)

@Serializable
data class CurrentOut(
    val bytes_per_second: Double,
    val window_start: String? = null,
    val window_end: String? = null,
    val window_seconds: Int? = null,
    /** WP METRIC-AUDIT: the prefill runner's part of [bytes_per_second]
     * (which is ALL traffic); `null` from an older server. */
    val runner_bytes_per_second: Double? = null,
    /** WP RUNNER-SPLIT-1: what the runner read from the cache (a verify);
     * `null` from a server before rc21 (no split: the runner downloads). */
    val runner_hit_bytes_per_second: Double? = null,
    /** WP RUNNER-SPLIT-1: what the runner loaded from the internet; `null`
     * from a server before rc21. */
    val runner_miss_bytes_per_second: Double? = null,
)

@Serializable
data class SummaryOut(
    val bytes_served: Long = 0,
    val hit_bytes: Long = 0,
    val miss_bytes: Long = 0,
    val requests: Long = 0,
    val hits: Long = 0,
    val misses: Long = 0,
    val errors: Long = 0,
    val hit_rate: Double? = null,
    val byte_hit_rate: Double? = null,
    val average_bytes_per_second: Double? = null,
    val peak_bytes_per_second: Double? = null,
    /** Distinct addresses in the range, the prefill runner excluded. */
    val active_clients: Int? = null,
    /** WP D15-RUNNER: bytes served to clients, the runner excluded;
     * [bytes_served] (everything) = this + [prefill_runner]'s bytes. `null`
     * from a server older than D15-RUNNER. */
    val client_bytes_served: Long? = null,
    /** WP D15-RUNNER: the prefill runner's own traffic in the range; `null`
     * from a server older than D15-RUNNER. */
    val prefill_runner: RunnerTrafficOut? = null,
    /** WP TILE-INTERNET: bytes that missed the cache and came from the
     * internet, the runner included; `null` from an older server. */
    val internet_bytes: Long? = null,
)

/** WP D15-RUNNER: the server's own prefill downloads in a range. */
@Serializable
data class RunnerTrafficOut(
    val bytes_served: Long = 0,
    val hit_bytes: Long = 0,
    val miss_bytes: Long = 0,
    val requests: Long = 0,
    val hits: Long = 0,
    val misses: Long = 0,
    val errors: Long = 0,
    /** WP METRIC-AUDIT: the runner's own byte hit rate; `null` from an
     * older server or without hits and misses. */
    val byte_hit_rate: Double? = null,
)

@Serializable
data class JobThroughputOut(
    val job_id: Int,
    val appid: Int,
    val name: String? = null,
    val started_at: String? = null,
    val bytes_per_second: Double? = null,
    val bytes_since_start: Long? = null,
    val exclusive_depots: Int = 0,
    /** Depots mapped to the app at all (shared or not); 0 on an older server. */
    val mapped_depots: Int = 0,
)

@Serializable
data class ThroughputOut(
    val event_feed: EventFeedOut = EventFeedOut(),
    val range: String,
    val tier: String? = null,
    val bucket_seconds: Int? = null,
    val range_start: String? = null,
    val active_window_minutes: Int? = null,
    val current: CurrentOut? = null,
    /** Addresses active within the window, the prefill runner excluded. */
    val active_clients_now: Int? = null,
    /** WP D15-RUNNER: the runner loaded within the window; `null` when the
     * feed is not `ok` (or from an older server). */
    val runner_active_now: Boolean? = null,
    val summary: SummaryOut? = null,
    val jobs: List<JobThroughputOut> = emptyList(),
    /** WP GRAPH-RECENT-1 + LOADING-NOW-1: the "now" figures from the live
     * tier (one window); `null` with the feed not `ok` or from a server
     * before rc21. Read through `mergeNow`. */
    val now: ThroughputNowOut? = null,
    /** WP GRAPH-LIVE: set by the app (never on the wire) when [current] was
     * taken from the live tier (`mergeLive` / `mergeNow`); the header words
     * follow it. */
    val live: Boolean = false,
    /** WP LOADING-NOW-1: set by the app (never on the wire) when
     * [active_clients_now] was read from the live tier; its window in
     * seconds (the current-speed window). `null` = the swept count, whose
     * window is [active_window_minutes]. */
    val active_window_seconds: Int? = null,
)

/**
 * WP GRAPH-RECENT-1 + LOADING-NOW-1: the `now` block of `GET /v1/throughput`
 * (`ThroughputNowOut`): "Current speed", the runner flag and "Loading now"
 * from the live tier, all over one window ([window_seconds]), on every range.
 */
@Serializable
data class ThroughputNowOut(
    val current: LiveCurrentOut? = null,
    /** Client addresses served bytes in the window, the runner excluded. */
    val active_clients: Int? = null,
    /** The prefill runner was served bytes in the window. */
    val runner_active: Boolean? = null,
    val window_seconds: Int? = null,
    val live_since: String? = null,
)

@Serializable
data class SeriesPointOut(
    /** Bucket start (UTC). */
    val t: String,
    val bytes_served: Long = 0,
    val hit_bytes: Long = 0,
    val miss_bytes: Long = 0,
    val requests: Long = 0,
    /** WP D15-RUNNER: the prefill runner's share of [bytes_served]; 0 from
     * an older server. */
    val runner_bytes: Long = 0,
    val bytes_per_second: Double = 0.0,
    val peak_bytes_per_second: Double = 0.0,
    /** WP RUNNER-SPLIT-1: the runner's cache reads (HIT) in [runner_bytes];
     * `null` from a server before rc21 (all runner bytes count as from the
     * internet then). */
    val runner_hit_bytes: Long? = null,
    /** WP RUNNER-SPLIT-1: the runner's MISS bytes; `null` from an older server. */
    val runner_miss_bytes: Long? = null,
)

@Serializable
data class SeriesOut(
    val event_feed: EventFeedOut = EventFeedOut(),
    val range: String,
    val tier: String? = null,
    val bucket_seconds: Int = 300,
    val range_start: String? = null,
    /** Oldest first. */
    val points: List<SeriesPointOut> = emptyList(),
)

/** WP GRAPH-LIVE: one point of `GET /v1/throughput/live`; [bucket_seconds]
 * is 5, 15 or 60 and varies along the series; [source] `live` | `persisted`. */
@Serializable
data class LivePointOut(
    val t: String,
    val bucket_seconds: Int = 5,
    val source: String = "live",
    val bytes_served: Long = 0,
    val hit_bytes: Long = 0,
    val miss_bytes: Long = 0,
    val requests: Long = 0,
    val runner_bytes: Long = 0,
    val bytes_per_second: Double = 0.0,
    val peak_bytes_per_second: Double = 0.0,
    /** WP RUNNER-SPLIT-1: as on [SeriesPointOut]; `null` from an older server. */
    val runner_hit_bytes: Long? = null,
    val runner_miss_bytes: Long? = null,
)

/** WP GRAPH-LIVE: the live "Current speed" (a window of 15-20 seconds). */
@Serializable
data class LiveCurrentOut(
    val bytes_per_second: Double = 0.0,
    val runner_bytes_per_second: Double = 0.0,
    val window_start: String? = null,
    val window_end: String? = null,
    val window_seconds: Int? = null,
    /** WP RUNNER-SPLIT-1: as on [CurrentOut]; `null` from an older server. */
    val runner_hit_bytes_per_second: Double? = null,
    val runner_miss_bytes_per_second: Double? = null,
)

/**
 * WP GRAPH-LIVE: `GET /v1/throughput/live` (`api/vault_api/routers/traffic.py`
 * `LiveOut`): the stitched series (5 s points for the last minute, 15 s up
 * to 5 minutes, then 1-minute points up to an hour), oldest first. With the
 * feed disabled [current] is `null` and [points] empty.
 */
@Serializable
data class LiveOut(
    val event_feed: EventFeedOut = EventFeedOut(),
    val range: String = "live",
    val tier: String = "live",
    val watcher_window_seconds: Int = 30,
    val live_since: String? = null,
    val range_start: String? = null,
    val current: LiveCurrentOut? = null,
    /** WP LIVE-DELAY-1 / LOADING-NOW-1: client addresses served bytes in the
     * [current] window, counted from the ring (the runner excluded); `null`
     * with the feed disabled or from an older server. */
    val active_clients: Int? = null,
    /** WP LOADING-NOW-1: the window of [active_clients] in seconds. */
    val active_window_seconds: Int? = null,
    /** Deprecated since rc21 (the window in whole minutes); kept for an
     * rc20 server, which sends only this one. */
    val active_window_minutes: Int? = null,
    val points: List<LivePointOut> = emptyList(),
)

@Serializable
data class ClientStatsOut(
    val bytes_served: Long = 0,
    val hit_bytes: Long = 0,
    val miss_bytes: Long = 0,
    val requests: Long = 0,
    val hits: Long = 0,
    val misses: Long = 0,
    val hit_rate: Double? = null,
)

@Serializable
data class LoadingAppOut(
    val appid: Int,
    val name: String? = null,
    val bytes_per_second: Double = 0.0,
)

@Serializable
data class LoadingNowOut(
    val loading: Boolean = false,
    val bytes_per_second: Double = 0.0,
    val apps: List<LoadingAppOut> = emptyList(),
    val unattributed_bytes_per_second: Double = 0.0,
)

@Serializable
data class RecentAppOut(
    val appid: Int,
    val name: String? = null,
    val bytes_served: Long = 0,
    val last_seen: String? = null,
)

@Serializable
data class ClientActivityOut(
    /** `agent:<client_id>`, `addr:<address>` or `runner`. */
    val key: String,
    /** `agent`, `address` or `runner` (WP D15-RUNNER: the server's own
     * prefill runner, at most one entry). */
    val kind: String,
    val client_id: String? = null,
    val addresses: List<String> = emptyList(),
    val connected: Boolean? = null,
    val agent_presence: String? = null,
    val cache_active: Boolean? = null,
    val last_active: String? = null,
    val last_seen_in_cache_log: String? = null,
    val last_reported_at: String? = null,
    val installed_appids: List<Int>? = null,
    val installed_app_count: Int? = null,
    val stats: ClientStatsOut? = null,
    val loading_now: LoadingNowOut? = null,
    val recent_apps: List<RecentAppOut>? = null,
    val recent_unattributed_bytes: Long? = null,
    /** WP D15-NAMES: the operator-set name, `null` when none. */
    val name: String? = null,
    /** WP D15-NAMES: the address a rename writes to; `null` = cannot be
     * named. Read by nothing in the app yet (renaming is web-only). */
    val name_addr: String? = null,
    /** WP D15-NAMES: [name] when set, else the agent's client_id, else the
     * address. `null` from a server older than D15-NAMES. */
    val display_name: String? = null,
)

@Serializable
data class ClientsOverviewOut(
    val event_feed: EventFeedOut = EventFeedOut(),
    val range: String,
    val tier: String? = null,
    val range_start: String? = null,
    val active_window_minutes: Int? = null,
    val clients: List<ClientActivityOut> = emptyList(),
)
