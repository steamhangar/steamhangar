package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP PILL-TONE-1 (audit D-1, one tone per state): the
 * three pill composables take word, ground and dot from the status tone of
 * `ui/status/StatusIconLogic.kt` (`toneFor`, `pillTextColor`,
 * `pillGroundColor`, `discColorFor`), the same tone the status disc
 * (`backgroundFor`) is drawn in. The tone table itself is pinned in
 * `StatusIconLogicTest` and `StatusVocabularyContractTest`; these pins
 * prove the pixels use it (docs/learnings/testing.md: "pinning the
 * pure/model layer proves NOTHING about the pixels"). Same comment-stripped
 * source scan, strong generation, as `AndroidDl1WiringTest`: the call's
 * nearest enclosing function must be the intended one, and the message
 * tells "deleted" from "moved".
 */
class PillTone1WiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val FUN = Regex("""(?:@Composable\s+)?(?:private\s+)?fun\s+(?:<[^>]+>\s+)?(\w+)""")

    private fun nearestEnclosingFun(code: String, callIdx: Int): String? =
        FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    private fun callIndex(code: String, call: String): Int {
        var from = 0
        while (true) {
            val idx = code.indexOf(call, from)
            if (idx < 0) return -1
            if (!code.substring(0, idx).trimEnd().endsWith("fun")) return idx
            from = idx + call.length
        }
    }

    private fun assertCalledFrom(path: String, call: String, expectedFun: String) {
        val code = stripComments(read(path))
        val idx = callIndex(code, call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "$path: '$call' MOVED -- its nearest enclosing function is no longer $expectedFun",
            expectedFun,
            nearestEnclosingFun(code, idx),
        )
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val stats = "$base/ui/downloads/DownloadsStats.kt"
    private val sheet = "$base/ui/detail/GameDetailSheet.kt"
    private val clients = "$base/ui/clients/ClientCardViews.kt"
    private val logic = "$base/ui/status/StatusIconLogic.kt"
    private val card = "$base/ui/library/GameCard.kt"

    @Test
    fun `MUTATION PIN -- the status disc is drawn in the colour of the kind's tone`() {
        assertTrue(
            "backgroundFor must derive the disc from toneFor (one tone per state)",
            stripComments(read(logic)).contains("fun backgroundFor(kind: StatusKind): Color = discColorFor(toneFor(kind))"),
        )
    }

    @Test
    fun `MUTATION PIN -- the Downloads hero pill takes word and ground from the disc's tone`() {
        assertCalledFrom(stats, "val tone = toneFor(status.kind)", "StatusPill")
        assertCalledFrom(stats, ".background(pillGroundColor(tone),", "StatusPill")
        assertCalledFrom(stats, "color = pillTextColor(tone),", "StatusPill")
    }

    @Test
    fun `MUTATION PIN -- the detail header pill takes word and ground from the disc's tone`() {
        assertCalledFrom(sheet, "val tone = toneFor(cardIconKind(row, kind, isGroup))", "DetailHeader")
        assertCalledFrom(sheet, ".background(pillGroundColor(tone),", "DetailHeader")
        assertCalledFrom(sheet, "color = pillTextColor(tone),", "DetailHeader")
    }

    @Test
    fun `MUTATION PIN -- the client card pill takes dot, word and ground from one tone, no accent`() {
        assertCalledFrom(clients, "val tone = pillTone(pill.look, stale)", "StatusPill")
        assertCalledFrom(clients, "val dot = discColorFor(tone)", "StatusPill")
        assertCalledFrom(clients, "val text = pillTextColor(tone)", "StatusPill")
        assertCalledFrom(clients, "val bg = pillGroundColor(tone)", "StatusPill")
        assertCalledFrom(clients, "look == PillLook.RUNNING -> toneFor(StatusKind.RUNNING)", "pillTone")
        val code = stripComments(read(clients))
        val start = code.indexOf("private fun pillTone(")
        val end = code.indexOf("private fun StatusPill(", start)
        assertTrue("pillTone / StatusPill not found in order", start >= 0 && end > start)
        assertFalse("the client pill must not use the accent (PILL-TONE-1)", code.substring(start, end).contains("Accent"))
    }

    @Test
    fun `MUTATION PIN -- the card word follows the card word rule, grey states in the none grey (web kit stt state)`() {
        assertCalledFrom(card, "Color = when (cardIconKind(model)) {", "cardStatusWordColor")
        assertCalledFrom(card, "StatusKind.STALE, StatusKind.PARTIAL -> VaultColors.StatusStale", "cardStatusWordColor")
        assertCalledFrom(card, "StatusKind.ERROR -> VaultColors.StatusDanger", "cardStatusWordColor")
        assertCalledFrom(card, "StatusKind.NONE, StatusKind.UNVERIFIED -> VaultColors.StatusNone", "cardStatusWordColor")
    }
}
