package dev.steamvault.app.ui.settings

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File

/**
 * Cross-frontend wording contract for Settings → About (WP WEB-FIX-8).
 * Every expected value below is a LITERAL, hand-transcribed from
 * `web/js/lib/about-view.js` (`ABOUT_DISPLAY`, `COMPONENT_NOTES`,
 * `CORE_NOTES`, `DASH`, `DASH_LABEL`, `DASH_NOTE`) -- never derived from
 * `strings.xml` itself (docs/LEARNINGS.md "Android (Phase 4b)": a derived
 * round-trip is circular). The web side pins the same pairs by reading this
 * app's `strings.xml` (`web/tests/about-android-twin.test.js`), so a
 * one-sided edit fails on both platforms.
 *
 * One deliberate difference, not pinned: the vault-api note says "the web
 * UI" here and "this web UI" on the web.
 */
class AboutCrossFrontendContractTest {

    private val xml: String by lazy {
        val file = File("src/main/res/values/strings.xml")
        check(file.exists()) { "expected resource file at ${file.absolutePath}" }
        file.readText(Charsets.UTF_8)
    }

    private fun res(name: String): String {
        val match = Regex("<string name=\"$name\"[^>]*>(.*?)</string>").find(xml)
            ?: error("no <string name=\"$name\"> found")
        return match.groupValues[1].replace("\\'", "'")
    }

    @Test
    fun `status words match the web display states`() {
        assertEquals("OK", res("settings_about_status_ok"))
        assertEquals("Unreachable", res("settings_about_status_unreachable"))
        assertEquals("Not in use", res("settings_about_status_not_in_use"))
        assertEquals("Not checked", res("settings_about_status_not_checked"))
        assertEquals("Check", res("settings_about_status_check"))
        assertEquals("Not reported", res("settings_about_status_not_reported"))
        assertEquals("N/A", res("settings_about_status_not_applicable"))
    }

    @Test
    fun `the dash, its spoken label and the dash note match the web`() {
        assertEquals("—", res("settings_about_dash"))
        assertEquals("Not reported", res("settings_about_dash_label"))
        assertEquals("A dash means the component did not report this value.", res("settings_about_dash_note"))
    }

    @Test
    fun `vault-core notes match the web CORE_NOTES`() {
        assertEquals(
            "Recorded at vault-core's last start: the same version and commit as vault-api, so both come from " +
                "the same release. Not a live check: vault-api has no network path to vault-core.",
            res("settings_about_note_core_same_release"),
        )
        assertEquals(
            "The version or commit vault-core recorded at its last start differs from vault-api's. Usually " +
                "vault-core was not restarted after an update; restart it so both run the same release.",
            res("settings_about_note_core_mismatch"),
        )
        assertEquals(
            "vault-core's recorded version cannot be compared with vault-api's: one of them did not report a " +
                "valid version and commit.",
            res("settings_about_note_core_not_comparable"),
        )
        assertEquals(
            "vault-core has not recorded a version yet. It records one in the shared cache volume each time it starts.",
            res("settings_about_note_core_not_reported"),
        )
        assertEquals("Version recorded at vault-core's last start, not a live check.", res("settings_about_note_vault_core"))
    }

    @Test
    fun `component notes match the web COMPONENT_NOTES`() {
        assertEquals("OK means the runner checked in within the last 90 s.", res("settings_about_note_vault_runner"))
        assertEquals(
            "The SteamPrefill build that prefill jobs run with. SteamPrefill reports a version but no commit id.",
            res("settings_about_note_steamprefill"),
        )
        assertEquals(
            "The egress proxy does not report a version. Its status is reachability only.",
            res("settings_about_note_vault_proxy"),
        )
        assertEquals(
            "Optional component. vault-api does not check it, so there is no status to show.",
            res("settings_about_note_vault_dns"),
        )
    }

    @Test
    fun `MUTATION PIN -- no About string shows the word unknown`() {
        val about = Regex("<string name=\"(settings_about_[a-z_]+)\"[^>]*>(.*?)</string>").findAll(xml).toList()
        check(about.size >= 20) { "expected the About strings, found ${about.size}" }
        for (m in about) {
            assertFalse("${m.groupValues[1]} says 'unknown'", m.groupValues[2].contains("unknown", ignoreCase = true))
        }
    }
}
