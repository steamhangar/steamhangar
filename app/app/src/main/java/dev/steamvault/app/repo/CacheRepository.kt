package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.DeletePlanOut
import dev.steamvault.app.net.model.GcJobRef

/**
 * Suspend-based repository over `DELETE /v1/cache/{appid}` (WP 4b.4 brief:
 * bulk delete) and `POST /v1/cache/{appid}/gc` (WP 4b.6 brief: detail
 * sheet's GC action). Same thin "typed name for the client call" shape as
 * [GamesRepository]/[JobsRepository]/[MappingRepository]. `GET
 * /v1/cache/summary` still has no caller (no WP needs it yet), same "add it
 * with the WP that needs it" rule the other repositories document.
 */
interface CacheRepository {
    suspend fun delete(appid: Int): CacheDeletionOut

    /** @param execute `false` (the default, matching the server's own
     *   default) queues a dry run; `true` queues a real deletion. Never
     *   call this with `true` except from a flow that has already shown the
     *   dry-run plan to the user and received an explicit second confirm --
     *   see `ui/detail/logic/GcFlow.kt`'s kdoc for the state machine that
     *   enforces this on the caller side. */
    suspend fun gc(appid: Int, execute: Boolean = false): GcJobRef

    /** WP TOOL-APPS-2b: the server's grouped-delete preview for [appid]
     * (`GET /v1/cache/{appid}/delete-plan?with_packages=true`). Defaulted
     * so a test double that never deletes a group needs no body. */
    suspend fun deletePlan(appid: Int): DeletePlanOut =
        throw UnsupportedOperationException("deletePlan is not implemented by this repository")

    /** WP TOOL-APPS-2b: delete [appid] with its content packages, confirming
     * exactly [members] (the plan's member ids, parent included). */
    suspend fun deleteWithPackages(appid: Int, members: List<Int>): CacheDeletionOut =
        throw UnsupportedOperationException("deleteWithPackages is not implemented by this repository")
}

class VaultCacheRepository(private val client: VaultApiClient) : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = client.deleteCache(appid)
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = client.gc(appid, execute)
    override suspend fun deletePlan(appid: Int): DeletePlanOut = client.deletePlan(appid)
    override suspend fun deleteWithPackages(appid: Int, members: List<Int>): CacheDeletionOut =
        client.deleteCacheWithPackages(appid, members)
}
