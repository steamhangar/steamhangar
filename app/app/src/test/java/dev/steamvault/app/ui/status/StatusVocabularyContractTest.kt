package dev.steamvault.app.ui.status

import dev.steamvault.app.ui.theme.VaultColors
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * Status vocabulary contract, Android side (WP TOKENS-SYNC-1), sibling of
 * [StatusIconWebTwinTest]. `docs/design/status-vocabulary.json` lists every
 * web status kind with its word, glyph, pill tone and motion; the web twin
 * (`web/tests/design-tokens-contract.test.js`) checks STATUS_LABEL,
 * KIND_GLYPH, DEFAULT_PILL_TONE and theme.css's animation rules against the
 * same file. Here [StatusKind], strings.xml, [glyphFor], [shouldAnimate]
 * and, since WP PILL-TONE-1 (one tone per state for disc and pill word),
 * [toneFor] are checked against it; [backgroundFor] derives the disc from
 * that tone, and the tone's colours are pinned against the web tokens below.
 *
 * Paths are relative to the module directory (`app/app`), the unit-test
 * working directory, like the other `*WebTwinTest` files.
 */
class StatusVocabularyContractTest {

    /** Web kinds the app does not port (literal, see StatusKind.kt). */
    private val webOnlyKinds = setOf("unknown")

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private val vocabulary: List<JsonObject> by lazy {
        Json.parseToJsonElement(read("../../docs/design/status-vocabulary.json")).jsonArray.map { it.jsonObject }
    }

    private fun entry(kind: StatusKind): JsonObject =
        vocabulary.firstOrNull { it["kind"]!!.jsonPrimitive.content == kind.wireName }
            ?: error("status-vocabulary.json has no kind \"${kind.wireName}\"")

    private fun stringResource(xml: String, name: String): String =
        Regex("<string name=\"$name\">(.*?)</string>").find(xml)?.groupValues?.get(1)
            ?: error("no <string name=\"$name\"> in strings.xml")

    @Test
    fun `StatusKind covers every vocabulary kind except the web-only ones, in the same order`() {
        val jsonKinds = vocabulary.map { it["kind"]!!.jsonPrimitive.content }
        assertEquals(jsonKinds.filter { it !in webOnlyKinds }, StatusKind.entries.map { it.wireName })
    }

    @Test
    fun `strings xml words equal the vocabulary words`() {
        val xml = read("src/main/res/values/strings.xml")
        for (kind in StatusKind.entries) {
            assertEquals(
                "strings.xml status_${kind.wireName} vs status-vocabulary.json",
                entry(kind)["word"]!!.jsonPrimitive.content,
                stringResource(xml, "status_${kind.wireName}"),
            )
        }
    }

    @Test
    fun `glyphFor equals the vocabulary glyphs`() {
        for (kind in StatusKind.entries) {
            assertEquals(
                "glyphFor(${kind.name}) vs status-vocabulary.json",
                entry(kind)["glyph"]!!.jsonPrimitive.content,
                glyphFor(kind).name.lowercase(),
            )
        }
    }

    @Test
    fun `shouldAnimate equals the vocabulary animated flags`() {
        for (kind in StatusKind.entries) {
            assertEquals(
                "shouldAnimate(${kind.name}, true) vs status-vocabulary.json",
                entry(kind)["animated"]!!.jsonPrimitive.boolean,
                shouldAnimate(kind, animatorsEnabled = true),
            )
        }
    }

    @Test
    fun `toneFor equals the vocabulary tones (PILL-TONE-1)`() {
        for (kind in StatusKind.entries) {
            val json = entry(kind)["tone"]!!
            val expected = if (json is JsonNull) null else json.jsonPrimitive.content
            assertEquals("toneFor(${kind.name}) vs status-vocabulary.json", expected, toneFor(kind).wireName)
        }
    }

    @Test
    fun `the disc of every kind is its tone's colour, the web token of that tone (PILL-TONE-1)`() {
        // Literal tone -> colour table, the twin of TONE_TOKEN in
        // web/tests/design-tokens-contract.test.js (DesignTokensContractTest
        // pins each VaultColors value to tokens.json).
        val disc = mapOf(
            "ok" to VaultColors.StatusOk,
            "run" to VaultColors.StatusRun,
            "stale" to VaultColors.StatusStale,
            "danger" to VaultColors.StatusDanger,
            "none" to VaultColors.StatusNone,
            null to VaultColors.Dim2,
        )
        for (kind in StatusKind.entries) {
            val json = entry(kind)["tone"]!!
            val tone = if (json is JsonNull) null else json.jsonPrimitive.content
            assertEquals("backgroundFor(${kind.name}) vs tone $tone", disc.getValue(tone), backgroundFor(kind))
        }
    }
}
