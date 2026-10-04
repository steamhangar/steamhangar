package dev.steamvault.app.ui.status

import androidx.compose.ui.graphics.toArgb
import dev.steamvault.app.R
import dev.steamvault.app.ui.theme.VaultColors
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * Cross-frontend wire-name contract (should-fix from the WP 4b.1 review).
 *
 * StatusKind's wireName/label pairs are required to stay 1:1 with
 * web/js/components/status-icon.js's STATUS_LABEL table (see StatusKind.kt's
 * kdoc). The previous "wire name round trips for every kind" test in
 * StatusIconLogicTest was CIRCULAR: it derived its expectation from
 * StatusKind.entries itself
 * (`StatusKind.fromWireName(kind.wireName) == kind`), so a transcription
 * slip that renames a kind's wireName (e.g. "cancelled" -> "caneled") stays
 * internally consistent and the test still passes — this was the reviewer's
 * surviving mutation. Every expected value here is a LITERAL, hand-written
 * constant instead, so a rename is compared against a fixed point that
 * doesn't move with the mutation.
 *
 * Reading web/js/components/status-icon.js at test time is out of scope for
 * this app/-only WP; the literals below are hand-transcribed from that
 * file's STATUS_LABEL table and are the intended manual-sync point if it
 * ever changes (documented in app/README.md). Repo precedent for pinning a
 * literal cross-file contract this way: api/tests/test_webui.py's
 * test_router_js_views_match_webui_spa_routes.
 */
class StatusIconCrossFrontendContractTest {

    /** web STATUS_LABEL's keys, spelled out — NOT StatusKind.entries.map { it.wireName }. */
    private val expectedWireNames = setOf(
        "cached",
        "running",
        "updating",
        "stale",
        "none",
        "paused",
        "verify",
        "error",
        "warn",
        "cancelled",
        // WP API-FIX-4 (Steam tool-app card). Web's "unknown" is not ported.
        "notinuse",
    )

    /** web STATUS_LABEL's values, spelled out. */
    private val expectedWordByWireName = mapOf(
        "cached" to "Current",
        "running" to "Downloading",
        "updating" to "Updating",
        "stale" to "Update ready",
        "none" to "Not cached",
        "paused" to "Paused",
        "verify" to "Verifying",
        "error" to "Failed",
        "warn" to "Warning",
        "cancelled" to "Cancelled",
        "notinuse" to "Not in use",
    )

    /** The English string-resource name each wire name is expected to use. */
    private val stringResourceNameByWireName = mapOf(
        "cached" to "status_cached",
        "running" to "status_running",
        "updating" to "status_updating",
        "stale" to "status_stale",
        "none" to "status_none",
        "paused" to "status_paused",
        "verify" to "status_verify",
        "error" to "status_error",
        "warn" to "status_warn",
        "cancelled" to "status_cancelled",
        "notinuse" to "status_notinuse",
    )

    @Test
    fun `StatusKind wire names match the literal frontend contract exactly`() {
        assertEquals(11, expectedWireNames.size)
        val actual = StatusKind.entries.map { it.wireName }.toSet()
        assertEquals(expectedWireNames, actual)
    }

    @Test
    fun `no two StatusKind entries share a wire name`() {
        val names = StatusKind.entries.map { it.wireName }
        assertEquals(names.size, names.toSet().size)
    }

    @Test
    fun `strings xml carries the literal frontend word for every wire name`() {
        val stringsXml = readResFile("strings.xml")
        for ((wireName, expectedWord) in expectedWordByWireName) {
            val resName = stringResourceNameByWireName.getValue(wireName)
            assertEquals(
                "strings.xml <string name=\"$resName\"> must read \"$expectedWord\" " +
                    "to match web STATUS_LABEL.$wireName",
                expectedWord,
                extractStringResource(stringsXml, resName),
            )
        }
    }

    @Test
    fun `every StatusKind labelRes points at the correct string resource and word`() {
        // Cross-checks StatusKind.labelRes -> the RIGHT resource, not just
        // that SOME resource in the file carries the right word — a
        // labelRes pointed at the wrong-but-coincidentally-correct string
        // would otherwise slip past the previous test alone.
        val stringsXml = readResFile("strings.xml")
        val resNameById = mapOf(
            R.string.status_cached to "status_cached",
            R.string.status_running to "status_running",
            R.string.status_updating to "status_updating",
            R.string.status_stale to "status_stale",
            R.string.status_none to "status_none",
            R.string.status_paused to "status_paused",
            R.string.status_verify to "status_verify",
            R.string.status_error to "status_error",
            R.string.status_warn to "status_warn",
            R.string.status_cancelled to "status_cancelled",
            R.string.status_notinuse to "status_notinuse",
        )
        for (kind in StatusKind.entries) {
            val expectedResName = stringResourceNameByWireName.getValue(kind.wireName)
            val actualResName = resNameById[kind.labelRes]
            assertEquals(
                "StatusKind.${kind.name}.labelRes must point at R.string.$expectedResName",
                expectedResName,
                actualResName,
            )
            assertEquals(
                expectedWordByWireName.getValue(kind.wireName),
                extractStringResource(stringsXml, actualResName!!),
            )
        }
    }

    @Test
    fun `colors xml vault_bg matches VaultColors Bg (reviewer extra)`() {
        val colorsXml = readResFile("colors.xml")
        val xmlHex = extractColorResource(colorsXml, "vault_bg").removePrefix("#").uppercase()
        val kotlinHex = String.format("%06X", VaultColors.Bg.toArgb() and 0xFFFFFF)
        assertEquals(xmlHex, kotlinHex)
    }

    // ---------- helpers ----------

    private fun readResFile(fileName: String): String {
        val file = File("src/main/res/values/$fileName")
        check(file.exists()) { "expected resource file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun extractStringResource(xml: String, name: String): String {
        val regex = Regex("<string name=\"$name\">(.*?)</string>")
        val match = regex.find(xml) ?: error("no <string name=\"$name\"> found")
        return match.groupValues[1]
    }

    private fun extractColorResource(xml: String, name: String): String {
        val regex = Regex("<color name=\"$name\">(.*?)</color>")
        val match = regex.find(xml) ?: error("no <color name=\"$name\"> found")
        return match.groupValues[1]
    }
}
