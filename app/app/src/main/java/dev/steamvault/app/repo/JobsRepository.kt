package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobMoveOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef

/**
 * Suspend-based repository over `GET /v1/jobs`[`/{id}`] and job control
 * (WP 4b.2 brief). See [dev.steamvault.app.polling.PollingIntervals] for
 * the pure "poll fast while a job is active" decision this repository's
 * eventual WorkManager caller (WP 4b.8) will apply to [list]'s result —
 * not implemented here.
 */
interface JobsRepository {
    suspend fun list(limit: Int = 20): List<JobSummary>
    suspend fun detail(id: Int): JobDetail
    suspend fun prefill(appids: List<Int>): List<PrefillJobRef>
    /** `POST /v1/prefill/cached` (WP 4c-app) — see [VaultApiClient.prefillCached]'s kdoc. */
    suspend fun prefillCached(): List<PrefillJobRef>
    /** `POST /v1/prefill/verify` (WP CACHE-COMPLETE-1c) — see [VaultApiClient.prefillVerify]'s kdoc. */
    suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef>
    suspend fun cancel(id: Int): JobControlOut
    suspend fun pause(id: Int): JobControlOut
    suspend fun resume(id: Int): JobControlOut
    /** `POST /v1/jobs/{id}/move` (WP D20) -- see [VaultApiClient.moveJob]'s kdoc. */
    suspend fun moveJob(id: Int, position: Int): JobMoveOut
}

class VaultJobsRepository(private val client: VaultApiClient) : JobsRepository {
    override suspend fun list(limit: Int): List<JobSummary> = client.jobs(limit)
    override suspend fun detail(id: Int): JobDetail = client.job(id)
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = client.prefill(appids)
    override suspend fun prefillCached(): List<PrefillJobRef> = client.prefillCached()
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> = client.prefillVerify(appids)
    override suspend fun cancel(id: Int): JobControlOut = client.cancelJob(id)
    override suspend fun pause(id: Int): JobControlOut = client.pauseJob(id)
    override suspend fun resume(id: Int): JobControlOut = client.resumeJob(id)
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut = client.moveJob(id, position)
}
