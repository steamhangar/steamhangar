package dev.steamvault.app.ui.detail

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.DepotEntry
import dev.steamvault.app.net.model.DepotPreviewOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobMoveOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.PreviewDepotOut
import dev.steamvault.app.net.model.StoreDetailsOut
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.CardInfoRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

private fun nope(): Nothing = error("not used here")

private class CiGames(private val detail: GameDetail?, private val fail: Boolean = false) : GamesRepository {
    override suspend fun list(): List<GameSummary> = nope()
    override suspend fun detail(appid: Int): GameDetail {
        if (fail) throw VaultApiError.Server("boom", 500)
        return detail ?: GameDetail(appid = appid, status = "idle")
    }
}

private object CiJobs : JobsRepository {
    override suspend fun list(limit: Int): List<JobSummary> = nope()
    override suspend fun detail(id: Int): JobDetail = nope()
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = nope()
    override suspend fun prefillCached(): List<PrefillJobRef> = nope()
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> = nope()
    override suspend fun cancel(id: Int): JobControlOut = nope()
    override suspend fun pause(id: Int): JobControlOut = nope()
    override suspend fun resume(id: Int): JobControlOut = nope()
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut = nope()
}

private object CiMapping : MappingRepository {
    override suspend fun list(): List<MappingEntry> = emptyList()
}

private object CiCache : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = nope()
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = nope()
}

private object CiStrings : DetailStrings {
    override fun queuedForDownload() = "queued"
    override fun pauseRequested() = "pause"
    override fun resuming() = "resume"
    override fun cancelRequested() = "cancel"
    override fun actionFailedFallback() = "failed"
    override fun loadErrorFallback() = "load failed"
    override fun freed(freedText: String) = "freed $freedText"
    override fun zeroBytesLabel() = "0 B"
}

/** Scripted card-info answers; records every ask. */
private class CiInfo(
    private val previews: MutableList<() -> DepotPreviewOut> = mutableListOf(),
    private val stores: MutableList<() -> StoreDetailsOut> = mutableListOf(),
) : CardInfoRepository {
    var previewAsks = 0
    var storeAsks = 0
    override suspend fun depotPreview(appid: Int): DepotPreviewOut {
        previewAsks++
        return (previews.getOrNull(previewAsks - 1) ?: previews.last())()
    }
    override suspend fun storeDetails(appid: Int): StoreDetailsOut {
        storeAsks++
        return (stores.getOrNull(storeAsks - 1) ?: stores.last())()
    }
}

private fun readyPreview(appid: Int = 1) = DepotPreviewOut(
    appid = appid, state = "ready", total_size_bytes = 1_073_741_824L,
    depots = listOf(PreviewDepotOut(10, 1_073_741_824L, null)),
)
private fun pendingPreview() = DepotPreviewOut(appid = 1, state = "pending")
private fun readyStore() = StoreDetailsOut(appid = 1, state = "ready", description = "A game.")

/** WP D19 (app): when the sheet asks for the expected depots and the store block, and what it keeps. */
class DetailControllerCardInfoTest {

    private fun controller(info: CardInfoRepository, detail: GameDetail? = null, failGames: Boolean = false) =
        DetailController(CiGames(detail, failGames), CiJobs, CiMapping, CiCache, info, CiStrings)

    @Test
    fun `an untracked game asks both reads and keeps the answers`() = runTest {
        val info = CiInfo(mutableListOf({ readyPreview() }), mutableListOf({ readyStore() }))
        val c = controller(info)
        c.open(this, 1, "G", isKnownToVault = false)
        advanceUntilIdle()
        assertEquals(1, info.previewAsks)
        assertEquals(1, info.storeAsks)
        assertEquals("ready", c.preview?.state)
        assertEquals("A game.", c.store?.description)
    }

    @Test
    fun `a tracked game with observed depots skips the preview but still asks the store`() = runTest {
        val info = CiInfo(mutableListOf({ readyPreview() }), mutableListOf({ readyStore() }))
        val c = controller(info, GameDetail(appid = 1, status = "done", depots = listOf(DepotEntry(10, false, 5))))
        c.open(this, 1, "G", isKnownToVault = true)
        advanceUntilIdle()
        assertEquals(0, info.previewAsks)
        assertNull(c.preview)
        assertEquals(1, info.storeAsks)
        assertNotNull(c.store)
    }

    @Test
    fun `a tracked game without depots asks the preview`() = runTest {
        val info = CiInfo(mutableListOf({ readyPreview() }), mutableListOf({ readyStore() }))
        val c = controller(info, GameDetail(appid = 1, status = "idle"))
        c.open(this, 1, "G", isKnownToVault = true)
        advanceUntilIdle()
        assertEquals(1, info.previewAsks)
        assertEquals("ready", c.preview?.state)
    }

    @Test
    fun `pending is asked again after 1500 ms and the ready answer replaces it`() = runTest {
        val info = CiInfo(mutableListOf({ pendingPreview() }, { readyPreview() }), mutableListOf({ readyStore() }))
        val c = controller(info)
        c.open(this, 1, "G", isKnownToVault = false)
        advanceTimeBy(100)
        assertEquals(1, info.previewAsks)
        assertEquals("pending", c.preview?.state)
        advanceTimeBy(1_400)
        assertEquals(1, info.previewAsks)
        advanceTimeBy(100)
        assertEquals(2, info.previewAsks)
        assertEquals("ready", c.preview?.state)
        advanceUntilIdle()
        assertEquals(2, info.previewAsks)
    }

    @Test
    fun `a server that stays pending is asked five times in all, then left alone`() = runTest {
        val info = CiInfo(mutableListOf({ pendingPreview() }), mutableListOf({ StoreDetailsOut(appid = 1, state = "pending") }))
        val c = controller(info)
        c.open(this, 1, "G", isKnownToVault = false)
        advanceUntilIdle()
        assertEquals(5, info.previewAsks)
        assertEquals(5, info.storeAsks)
        assertEquals("pending", c.preview?.state)
    }

    @Test
    fun `a failing read leaves null and never raises`() = runTest {
        val info = CiInfo(
            mutableListOf({ throw VaultApiError.NotFound("nf", 404) }),
            mutableListOf({ throw VaultApiError.Server("boom", 500) }),
        )
        val c = controller(info)
        c.open(this, 1, "G", isKnownToVault = false)
        advanceUntilIdle()
        assertNull(c.preview)
        assertNull(c.store)
    }

    @Test
    fun `a card whose own data failed to load asks nothing`() = runTest {
        val info = CiInfo(mutableListOf({ readyPreview() }), mutableListOf({ readyStore() }))
        val c = controller(info, failGames = true)
        c.open(this, 1, "G", isKnownToVault = true)
        advanceUntilIdle()
        assertNotNull(c.loadError)
        assertEquals(0, info.previewAsks)
        assertEquals(0, info.storeAsks)
    }

    @Test
    fun `close clears both and stops a pending retry`() = runTest {
        val info = CiInfo(mutableListOf({ pendingPreview() }), mutableListOf({ readyStore() }))
        val c = controller(info)
        c.open(this, 1, "G", isKnownToVault = false)
        advanceTimeBy(100)
        c.close()
        advanceUntilIdle()
        assertNull(c.preview)
        assertNull(c.store)
        assertEquals(1, info.previewAsks)
    }

    @Test
    fun `reopening another game never receives the earlier game's answer`() = runTest {
        val info = CiInfo(mutableListOf({ pendingPreview() }, { readyPreview(1) }), mutableListOf({ readyStore() }))
        val c = controller(info)
        c.open(this, 1, "A", isKnownToVault = false)
        advanceTimeBy(100)
        c.open(this, 2, "B", isKnownToVault = false)
        advanceUntilIdle()
        assertEquals(2, c.openAppid)
        // the retry of game 1 was cancelled; game 2's own ask was the second one
        assertEquals(2, info.previewAsks)
        assertEquals("ready", c.preview?.state)
    }
}
