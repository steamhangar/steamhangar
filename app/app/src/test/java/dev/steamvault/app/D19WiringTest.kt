package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP D19's model-to-pixel wiring: the logic is pinned in
 * `DepotPreviewTest` / `StoreDetailsTest` / `DetailControllerCardInfoTest`;
 * these prove the sheet draws the blocks and the repository reaches the
 * controller. Same comment-stripped scan as `Upd3WiringTest`.
 */
class D19WiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val COMPOSABLE_FUN = Regex("""@Composable\s+(?:private\s+|internal\s+)?fun\s+(\w+)""")

    private fun enclosing(code: String, idx: Int): String? =
        COMPOSABLE_FUN.findAll(code).filter { it.range.first < idx }.maxByOrNull { it.range.first }?.groupValues?.get(1)

    private fun assertCalledFrom(path: String, call: String, expected: String) {
        val code = stripComments(read(path))
        var from = 0
        var idx = -1
        while (true) {
            val i = code.indexOf(call, from)
            if (i < 0) break
            if (!code.substring(0, i).trimEnd().endsWith("fun")) { idx = i; break }
            from = i + call.length
        }
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals("$path: '$call' MOVED -- no longer inside $expected", expected, enclosing(code, idx))
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val sheet = "$base/ui/detail/GameDetailSheet.kt"
    private val blocks = "$base/ui/detail/CardInfoBlocks.kt"

    @Test
    fun `MUTATION PIN -- the untracked body draws the store block and the expected block`() {
        assertCalledFrom(sheet, "StoreDetailsBlock(", "NotTrackedBody")
        assertCalledFrom(sheet, "ExpectedDepotsBlock(", "NotTrackedBody")
    }

    @Test
    fun `MUTATION PIN -- the loaded body draws the expected block only when the model says so`() {
        assertCalledFrom(sheet, "showsExpectedBlock(", "NotTrackedBody")
        val code = stripComments(read(sheet))
        val loaded = code.substring(code.indexOf("private fun LoadedDetailBody"))
        assertTrue(loaded.contains("showsExpectedBlock("))
        assertTrue(loaded.contains("ExpectedDepotsBlock("))
        assertTrue(loaded.contains("StoreDetailsBlock("))
    }

    @Test
    fun `MUTATION PIN -- CARD-LAYOUT-1 the game block comes before the hangar blocks in the loaded body`() {
        val code = stripComments(read(sheet))
        val loaded = code.substring(code.indexOf("private fun LoadedDetailBody"))
        val store = loaded.indexOf("StoreDetailsBlock(")
        assertTrue(store >= 0)
        assertEquals("one call site only", store, loaded.lastIndexOf("StoreDetailsBlock("))
        for (hangar in listOf("TOOL_APP_NOTE", "detail_last_download", "CompletenessSection(", "installedBadgeFor(", "PackagesSection(")) {
            val at = loaded.indexOf(hangar)
            assertTrue("$hangar present", at >= 0)
            assertTrue("StoreDetailsBlock must precede $hangar", store < at)
        }
    }

    @Test
    fun `MUTATION PIN -- the repository reaches the controller, real in the app and off in demo`() {
        val lib = stripComments(read("$base/ui/library/LibraryScreen.kt"))
        assertTrue(lib.contains("cardInfoRepository: CardInfoRepository = OffCardInfoRepository"))
        assertTrue(Regex("DetailController\\([^)]*cardInfoRepository", RegexOption.DOT_MATCHES_ALL).containsMatchIn(lib))
        val main = stripComments(read("$base/MainActivity.kt"))
        assertTrue(main.contains("cardInfoRepository = OffCardInfoRepository"))
        assertTrue(main.contains("cardInfoRepository = remember(client) { VaultCardInfoRepository(client) }"))
    }

    @Test
    fun `the blocks draw plain text only -- no images, links or HTML`() {
        val code = stripComments(read(blocks))
        for (banned in listOf("Image(", "AsyncImage", "ClickableText", "LinkAnnotation", "Html", "uriHandler", "UriHandler", "painterResource")) {
            assertFalse("CardInfoBlocks.kt must stay plain text: found $banned", code.contains(banned))
        }
        assertTrue(code.contains("Text("))
    }
}
