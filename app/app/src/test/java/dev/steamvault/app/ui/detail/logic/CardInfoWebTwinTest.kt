package dev.steamvault.app.ui.detail.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP D19: the wording, states, retry pacing and limits of
 * `DepotPreview.kt` / `StoreDetails.kt` must still be in
 * `web/js/lib/depot-preview.js`, `web/js/lib/store-details.js` and
 * `web/js/components/game-detail-sheet.js`; the two routes in `web/js/api.js`.
 * VALUE drift -> update the Kotlin; GRAMMAR drift -> adjust this test.
 */
class CardInfoWebTwinTest {
    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return file.readText(Charsets.UTF_8)
    }

    private val preview: String by lazy { read("../../web/js/lib/depot-preview.js") }
    private val store: String by lazy { read("../../web/js/lib/store-details.js") }
    private val sheet: String by lazy { read("../../web/js/components/game-detail-sheet.js") }
    private val api: String by lazy { read("../../web/js/api.js") }

    @Test
    fun `the expected block wording is still in the web sheet`() {
        assertTrue("Depot row", sheet.contains("`${DepotPreviewWording.DEPOT_PREFIX}\${r.depotid} ${DepotPreviewWording.DASH} \${r.sizeText || \"${DepotPreviewWording.DASH}\"}`"))
        assertTrue("DLC note", sheet.contains("\"${DepotPreviewWording.DLC_NOTE}\""))
        assertTrue("DLC entry", sheet.contains("`\${d.depotid} (DLC \${d.dlcappid})`"))
        assertTrue("Hint", sheet.contains("\"${DepotPreviewWording.HINT}\""))
        assertTrue("Size line", preview.contains("`Expected size \${size} (download \${download})`") && preview.contains("`Expected size \${size}`"))
    }

    @Test
    fun `preview states and retry pacing are still in the web source`() {
        for ((key, value) in listOf(
            "OFF" to PreviewState.OFF, "CACHED" to PreviewState.CACHED, "PENDING" to PreviewState.PENDING,
            "READY" to PreviewState.READY, "UNAVAILABLE" to PreviewState.UNAVAILABLE,
        )) {
            assertTrue("$key", Regex("$key:\\s*\"$value\"").containsMatchIn(preview))
        }
        assertTrue(preview.contains("PENDING_RETRY_MS = Object.freeze([${PREVIEW_PENDING_RETRY_MS.joinToString(", ")}])"))
    }

    @Test
    fun `store states, retry pacing, mode labels and the description cap are still in the web source`() {
        for ((key, value) in listOf(
            "OFF" to StoreState.OFF, "PENDING" to StoreState.PENDING,
            "READY" to StoreState.READY, "UNAVAILABLE" to StoreState.UNAVAILABLE,
        )) {
            assertTrue("$key", Regex("$key:\\s*\"$value\"").containsMatchIn(store))
        }
        assertTrue(store.contains("STORE_RETRY_MS = Object.freeze([${STORE_RETRY_MS.joinToString(", ")}])"))
        assertEquals(4, MODE_LABELS.size)
        val bad = MODE_LABELS.filter { (k, v) -> !Regex("\\b$k:\\s*\"${Regex.escape(v)}\"").containsMatchIn(store) }
        assertTrue("Mode labels not in web: ${bad.keys}", bad.isEmpty())
        val webModes = Regex("^\\s{2}\\w+:\\s*\"[^\"]+\",", RegexOption.MULTILINE)
            .findAll(store.substringAfter("MODE_LABELS").substringBefore("});")).count()
        assertEquals("web mode count", MODE_LABELS.size, webModes)
        assertTrue(store.contains("MAX_DESCRIPTION = $MAX_STORE_DESCRIPTION;"))
    }

    @Test
    fun `ellipsis, en dash and the rating grammar are still in the web source`() {
        assertTrue(store.contains("`\${t.slice(0, MAX_DESCRIPTION - 1)}…`"))
        assertTrue(store.contains("parts.join(\" – \")"))
        assertTrue(store.contains("`\${percent}% of \${total.toLocaleString(\"en-US\")} reviews`"))
        assertTrue(store.contains("`\${label} (\${count})`"))
        assertTrue(store.contains("Math.round((pos / total) * 100)"))
        // Android twins of the same glyphs
        assertEquals("\u2026", storeDescription(dev.steamvault.app.net.model.StoreDetailsOut(
            appid = 1, state = "ready", description = "z".repeat(700),
        ))!!.takeLast(1))
    }

    @Test
    fun `the block order and the two routes are still in the web source`() {
        assertTrue(sheet.contains("[\"genres\", genreList(state.store).join(\", \")]"))
        assertTrue(sheet.indexOf("\"genres\"") < sheet.indexOf("[\"by\", byLine") && sheet.indexOf("[\"by\", byLine") < sheet.indexOf("[\"rating\", ratingText"))
        assertTrue(api.contains("`/v1/oracle/\${encodeURIComponent(appid)}/preview`"))
        assertTrue(api.contains("`/v1/store/\${encodeURIComponent(appid)}/details`"))
    }

    @Test
    fun `the two-column breakpoint and the game-first order match the web source`() {
        val css = read("../../web/css/app.css")
        assertTrue(css.contains("@media (min-width:600px){\n  .store-details:not(.store-nodesc){ display:grid;"))
        assertEquals(600, dev.steamvault.app.ui.detail.STORE_TWO_COLUMN_MIN_WIDTH_DP)
        val render = sheet.substring(sheet.indexOf("function render()"))
        assertTrue(render.indexOf("buildHeader(gameLike, liveJob)") < render.indexOf("buildStoreBlock()"))
        assertTrue(render.indexOf("buildStoreBlock()") < render.indexOf("buildFactLines(gameLike)"))
    }
}
