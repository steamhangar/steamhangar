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
)

@Serializable
data class CurrentOut(
    val bytes_per_second: Double,
    val window_start: String? = null,
    val window_end: String? = null,
    val window_seconds: Int? = null,
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
