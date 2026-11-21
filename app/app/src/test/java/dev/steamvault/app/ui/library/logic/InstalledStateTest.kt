package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP AG-3: pins [installedBadgeFor]'s three-way branch and the
 * cached-vs-not-cached decision it must NEVER collapse (both directions
 * exercised explicitly, see the two tests below named for that).
 */
class InstalledStateTest {

    private fun game(
        installedOn: List<InstalledOnEntry> = emptyList(),
        sizeBytes: Long? = null,
        status: String = "idle",
    ) = GameSummary(
        appid = 1,
        name = "Test Game",
        status = status,
        depot_count = if (sizeBytes != null) 1 else 0,
        size_bytes = sizeBytes,
        installed_on = installedOn,
    )

    @Test
    fun `empty installed_on -- NoSignal, badge shown iff installed_on non-empty`() {
        assertEquals(InstalledBadge.NoSignal, installedBadgeFor(game(installedOn = emptyList())))
        // A cached game with no fresh signal is STILL NoSignal -- cache
        // content alone must never manufacture a badge.
        assertEquals(InstalledBadge.NoSignal, installedBadgeFor(game(installedOn = emptyList(), sizeBytes = 5_000_000L)))
    }

    @Test
    fun `fresh entries AND cache content -- InstalledAndCached, not the not-cached branch`() {
        val entries = listOf(InstalledOnEntry("gaming-pc", "2026-08-22T09:15:03Z"))
        val badge = installedBadgeFor(game(installedOn = entries, sizeBytes = 5_000_000L))
        assertTrue("expected InstalledAndCached, got $badge", badge is InstalledBadge.InstalledAndCached)
    }

    @Test
    fun `fresh entries but NO cache content -- InstalledNotCached, not the cached branch (the installed-but-not-cached case)`() {
        val entries = listOf(InstalledOnEntry("gaming-pc", "2026-08-22T09:15:03Z"))
        // size_bytes null, hasVisibleCacheContent(game) == false.
        val badge = installedBadgeFor(game(installedOn = entries, sizeBytes = null))
        assertTrue("expected InstalledNotCached, got $badge", badge is InstalledBadge.InstalledNotCached)
    }

    @Test
    fun `size_bytes zero also counts as NOT cached (hasVisibleCacheContent's own rule)`() {
        val entries = listOf(InstalledOnEntry("gaming-pc", "2026-08-22T09:15:03Z"))
        val badge = installedBadgeFor(game(installedOn = entries, sizeBytes = 0L))
        assertTrue(badge is InstalledBadge.InstalledNotCached)
    }

    @Test
    fun `single entry -- additionalClientCount is zero, primary is that entry`() {
        val entries = listOf(InstalledOnEntry("gaming-pc", "2026-08-22T09:15:03Z"))
        val badge = installedBadgeFor(game(installedOn = entries, sizeBytes = 1L)) as InstalledBadge.InstalledAndCached
        assertEquals("gaming-pc", badge.display.primaryClientId)
        assertEquals("2026-08-22T09:15:03Z", badge.display.primaryReportedAt)
        assertEquals(0, badge.display.additionalClientCount)
    }

    @Test
    fun `multiple entries -- never silently dropped, most-recent reported_at is primary, rest counted`() {
        val entries = listOf(
            InstalledOnEntry("older-pc", "2026-08-20T09:00:00Z"),
            InstalledOnEntry("newest-pc", "2026-08-22T09:15:03Z"),
            InstalledOnEntry("middle-pc", "2026-08-21T09:00:00Z"),
        )
        val badge = installedBadgeFor(game(installedOn = entries, sizeBytes = 1L)) as InstalledBadge.InstalledAndCached
        assertEquals("newest-pc", badge.display.primaryClientId)
        assertEquals(2, badge.display.additionalClientCount)
    }

    @Test
    fun `MUTATION PIN -- cached-ness is hasVisibleCacheContent, not status or depot_count alone`() {
        val entries = listOf(InstalledOnEntry("gaming-pc", "2026-08-22T09:15:03Z"))
        // status "done" with size_bytes null is the last-cached-remnant shape
        // (api/README.md) -- hasVisibleCacheContent says NOT cached even
        // though status alone would suggest otherwise. If installedBadgeFor
        // were changed to read `status == "done"` instead of
        // hasVisibleCacheContent, this would flip to InstalledAndCached.
        val badge = installedBadgeFor(game(installedOn = entries, sizeBytes = null, status = "done"))
        assertTrue("expected InstalledNotCached for a done-but-sizeless remnant row", badge is InstalledBadge.InstalledNotCached)
    }
}
