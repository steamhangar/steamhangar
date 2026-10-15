package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.DepotPreviewOut
import dev.steamvault.app.net.model.DepotEntry
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.PreviewDepotOut
import dev.steamvault.app.net.model.PreviewDlcDepotOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP D19 (app): decisions and wording of the expected-depots block (web twin: `web/tests/depot-preview.test.js`). */
class DepotPreviewTest {

    private val GB = 1_073_741_824L

    private fun ready(
        size: Long? = 5 * GB,
        download: Long? = 2 * GB,
        depots: List<PreviewDepotOut> = listOf(PreviewDepotOut(1366541, 5 * GB, 2 * GB), PreviewDepotOut(1366543, null, null)),
        dlc: List<PreviewDlcDepotOut> = emptyList(),
        state: String = "ready",
    ) = DepotPreviewOut(
        appid = 1366540, state = state, total_size_bytes = size, total_download_bytes = download,
        depots = depots, dlc_depots = dlc,
    )

    private fun detail(depots: List<DepotEntry>) = GameDetail(appid = 1, status = "idle", depots = depots)

    @Test
    fun `only a ready payload with depots is ready -- off loading unavailable cached and empty are not`() {
        assertTrue(isPreviewReady(ready()))
        for (state in listOf("off", "pending", "unavailable", "cached")) {
            assertFalse(state, isPreviewReady(ready(state = state)))
            assertNull(state, expectedSizeText(ready(state = state)))
            assertTrue(state, expectedDepotRows(ready(state = state)).isEmpty())
        }
        assertFalse(isPreviewReady(ready(depots = emptyList())))
        assertFalse(isPreviewReady(null))
        assertNull(expectedSizeText(null))
    }

    @Test
    fun `size line says Expected, adds the download only when known, and is null without a size`() {
        assertEquals("Expected size 5.0 GB (download 2.0 GB)", expectedSizeText(ready()))
        assertEquals("Expected size 5.0 GB", expectedSizeText(ready(download = null)))
        assertNull(expectedSizeText(ready(size = null)))
        assertNull(expectedSizeText(ready(size = 0)))
    }

    @Test
    fun `depot rows keep a sizeless depot as a dash row, never as 0`() {
        val rows = expectedDepotRows(ready())
        assertEquals(listOf(1366541, 1366543), rows.map { it.depotid })
        assertEquals("Depot 1366541 — 5.0 GB", expectedDepotLine(rows[0]))
        assertNull(rows[1].sizeText)
        assertEquals("Depot 1366543 — —", expectedDepotLine(rows[1]))
    }

    @Test
    fun `DLC depots are listed apart, a missing or zero dlcappid prints the bare depot id`() {
        assertNull(expectedDlcNote(ready()))
        val note = expectedDlcNote(
            ready(dlc = listOf(PreviewDlcDepotOut(1366542, 1500), PreviewDlcDepotOut(7, null), PreviewDlcDepotOut(8, 0))),
        )
        assertEquals("Not included in the size (DLC depots): 1366542 (DLC 1500), 7, 8", note)
        assertNull(expectedDlcNote(ready(state = "pending", dlc = listOf(PreviewDlcDepotOut(1, 2)))))
    }

    @Test
    fun `wantsPreview only for a game with no observed depots`() {
        assertTrue(wantsPreview(null))
        assertTrue(wantsPreview(detail(emptyList())))
        assertFalse(wantsPreview(detail(listOf(DepotEntry(1, false, 10)))))
    }

    @Test
    fun `the block shows only with a size line, and never once depots are observed`() {
        assertTrue(showsExpectedBlock(ready(), null))
        assertTrue(showsExpectedBlock(ready(), detail(emptyList())))
        assertFalse(showsExpectedBlock(ready(), detail(listOf(DepotEntry(1, false, 10)))))
        assertFalse(showsExpectedBlock(ready(size = null), null))
        assertFalse(showsExpectedBlock(null, null))
    }

    @Test
    fun `retry pacing is four tries`() {
        assertEquals(listOf(1500L, 3000L, 6000L, 12000L), PREVIEW_PENDING_RETRY_MS)
    }
}
