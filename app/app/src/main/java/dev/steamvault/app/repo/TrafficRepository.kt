package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut

/**
 * Suspend-based repository over vault-api's ranged traffic reads (WP D15b
 * API, WP D15d app): the Downloads statistics header and graph, the Clients
 * page, and the Downloads history filter. Every method takes one of
 * `ui/traffic/logic/TrafficView.kt`'s `TRAFFIC_RANGES`.
 *
 * The history read lives here rather than on [JobsRepository] because it
 * belongs to the same feature (and the same "range" vocabulary); the jobs
 * poll every screen shares stays untouched.
 */
interface TrafficRepository {
    suspend fun throughput(range: String): ThroughputOut
    suspend fun series(range: String): SeriesOut
    suspend fun clientsOverview(range: String): ClientsOverviewOut
    suspend fun jobHistory(range: String, limit: Int): List<JobSummary>
}

class VaultTrafficRepository(private val client: VaultApiClient) : TrafficRepository {
    override suspend fun throughput(range: String): ThroughputOut = client.throughput(range)
    override suspend fun series(range: String): SeriesOut = client.throughputSeries(range)
    override suspend fun clientsOverview(range: String): ClientsOverviewOut = client.clientsOverview(range)
    override suspend fun jobHistory(range: String, limit: Int): List<JobSummary> = client.jobsInRange(range, limit)
}
