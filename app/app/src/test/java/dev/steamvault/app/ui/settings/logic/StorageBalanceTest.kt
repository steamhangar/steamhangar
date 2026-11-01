package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.demo.DemoCacheRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.CacheSummaryOut
import dev.steamvault.app.net.model.StorageBalanceOut
import dev.steamvault.app.net.model.UnmappedDepotsOut
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-RC21-1-TWINS: `StorageBalance.kt`, mirroring
 * `web/tests/storage-balance.test.js` (WP STORAGE-BALANCE-1) case by case:
 * an older server or a malformed balance is `null` (never zeros); the tiles
 * carry plain words, figures and depot counts; the server's warning shows
 * with its reason and highlights old builds; a negative residual prints
 * signed; the demo balance adds up.
 */
class StorageBalanceTest {

    private val gb = 1L shl 30

    private fun balance() = StorageBalanceOut(
        on_disk_bytes = 100 * gb,
        planned_bytes = 70 * gb,
        present_bytes = 60 * gb,
        measured_depots = 3,
        old_builds_bytes = 20 * gb,
        unmeasured_bytes = 15 * gb,
        unmeasured_depots = 2,
        unmapped_bytes = 5 * gb,
        unmapped_depots = 1,
        unexplained_bytes = 0,
        warning = true,
        warning_reason = "Old builds take 20.0 GB (20 % of 100.0 GB on disk), above the 15 % / 50.0 GB threshold.",
    )

    private fun summary(b: StorageBalanceOut?) = CacheSummaryOut(
        total_bytes = 100 * gb,
        unmapped_depots = UnmappedDepotsOut(count = 1, size_bytes = 5 * gb),
        free_disk_bytes = 1,
        storage_balance = b,
    )

    @Test
    fun `MUTATION TARGET -- an older server without storage_balance (or no summary) is null, never zeros`() {
        assertNull(storageBalanceView(null))
        assertNull("the pre-STORAGE-BALANCE-1 summary shape", storageBalanceView(summary(null)))
        // The real decode of an older server's body.
        val old = VaultJson.decodeFromString(
            CacheSummaryOut.serializer(),
            """{"total_bytes":1,"top_consumers":[],"unmapped_depots":{"count":0,"size_bytes":0},"free_disk_bytes":1}""",
        )
        assertNull(old.storage_balance)
        assertNull(storageBalanceView(old))
        assertNull("a negative size", storageBalanceView(summary(balance().copy(old_builds_bytes = -1))))
        assertNull("a missing field", storageBalanceView(summary(balance().copy(unmapped_depots = null))))
        // A balance the decoder fills partly (field missing on the wire).
        val partial = VaultJson.decodeFromString(
            CacheSummaryOut.serializer(),
            """{"total_bytes":1,"unmapped_depots":{"count":0,"size_bytes":0},"storage_balance":{"on_disk_bytes":1}}""",
        )
        assertNull(storageBalanceView(partial))
    }

    @Test
    fun `tiles -- plain words, figures, depot counts, the 'of planned' sub-line`() {
        val view = storageBalanceView(summary(balance()))!!
        val byKey = view.tiles.associateBy { it.key }
        assertEquals(
            "no Unexplained tile while the residual is 0",
            listOf("On disk", "Current builds", "Older builds", "Not measured", "No game"),
            view.tiles.map { it.label },
        )
        assertTrue(view.accounted)
        assertEquals("100 GB", byKey.getValue("on_disk").value)
        assertEquals("60.0 GB", byKey.getValue("current").value)
        assertEquals(
            "On disk, of the 70.0 GB the current builds of the measured games need (3 depots).",
            byKey.getValue("current").note,
        )
        assertEquals("20.0 GB", byKey.getValue("old_builds").value)
        assertEquals("2 depots", byKey.getValue("unmeasured").sub)
        assertEquals("1 depot", byKey.getValue("unmapped").sub)
        assertNull(byKey.getValue("on_disk").sub)
        assertEquals("Everything in the cache folder.", byKey.getValue("on_disk").note)
        assertEquals(
            "In measured depots but not part of the current build as last checked: older builds, damaged chunks, and new downloads since the last check.",
            byKey.getValue("old_builds").note,
        )
        assertEquals("Games, DLCs and tool packages without a completeness check.", byKey.getValue("unmeasured").note)
        assertEquals("Depot folders no game is mapped to.", byKey.getValue("unmapped").note)
        // The line under the tiles (web paintStorageBalance).
        assertEquals("${StorageBalanceText.ACCOUNTED} ${StorageBalanceText.NOTE}", view.note)
        assertTrue(view.note.startsWith("Everything on disk is accounted for"))
        // A real zero prints (formatBytesGBOrZero), never hides.
        val zero = storageBalanceView(summary(balance().copy(unmapped_bytes = 0, unmapped_depots = 0)))!!
        assertEquals("0.0 GB", zero.tiles.single { it.key == "unmapped" }.value)
        assertEquals("0 depots", zero.tiles.single { it.key == "unmapped" }.sub)
    }

    @Test
    fun `MUTATION TARGET -- the server's warning shows with its reason and highlights old builds`() {
        val view = storageBalanceView(summary(balance()))!!
        assertTrue(view.warning!!.startsWith("Old builds take 20.0 GB"))
        assertTrue(view.tiles.single { it.key == "old_builds" }.hi)

        val quiet = storageBalanceView(summary(balance().copy(warning = false, warning_reason = null)))!!
        assertNull(quiet.warning)
        assertFalse(quiet.tiles.any { it.hi })

        val bare = storageBalanceView(summary(balance().copy(warning_reason = null)))!!
        assertEquals(StorageBalanceText.WARNING_FALLBACK, bare.warning)
        val blank = storageBalanceView(summary(balance().copy(warning_reason = "   ")))!!
        assertEquals(StorageBalanceText.WARNING_FALLBACK, blank.warning)
        val padded = storageBalanceView(summary(balance().copy(warning_reason = "  reason  ")))!!
        assertEquals("reason", padded.warning)
        val absent = storageBalanceView(summary(balance().copy(warning = null)))!!
        assertNull("only warning == true warns", absent.warning)
    }

    @Test
    fun `a negative residual prints signed and explains itself`() {
        val view = storageBalanceView(summary(balance().copy(unexplained_bytes = -2 * gb)))!!
        val t = view.tiles.single { it.key == "unexplained" }
        assertFalse(view.accounted)
        assertEquals("−2.0 GB", t.value)
        assertTrue(t.note.startsWith("Should be 0"))
        assertTrue("part of the warning", t.hi)
        assertEquals(StorageBalanceText.NOTE, view.note)
        assertEquals("Unexplained", view.tiles.last().label)
    }

    @Test
    fun `demo data -- the balance adds up to on-disk with a zero residual`() = runTest {
        val summary = DemoCacheRepository(DemoState.fresh()).summary()
        val b = summary.storage_balance!!
        assertEquals(summary.total_bytes, b.on_disk_bytes)
        assertEquals(0L, b.unexplained_bytes)
        assertEquals(
            b.on_disk_bytes,
            b.present_bytes!! + b.old_builds_bytes!! + b.unmeasured_bytes!! + b.unmapped_bytes!! + b.unexplained_bytes!!,
        )
        assertTrue(b.present_bytes!! <= b.planned_bytes!!)
        assertNotNull(storageBalanceView(summary))
    }
}
