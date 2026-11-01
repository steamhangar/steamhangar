package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.demo.seedAbout
import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.AboutOut
import dev.steamvault.app.net.model.AboutReleaseComponentOut
import dev.steamvault.app.net.model.AboutReleaseOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP RELEASE-SPLIT-1c (ADR-0031, ABOUT-VERSION-1 Weg A), Android twin of
 * `web/tests/release-split-1c.test.js`: Settings → About shows the product
 * version as the main line and Server/Agent/App below it, "(unchanged since
 * ...)" for a component older than the product. The words are LITERALS
 * transcribed from `web/js/lib/about-view.js` (`RELEASE_LABELS`,
 * `releaseValueText`); the web side pins the same strings by reading this
 * app's `strings.xml`.
 */
class ReleaseSplit1cAboutTest {

    private val rc23 = AboutReleaseOut(
        version = "0.1.0-rc23",
        components = listOf(
            AboutReleaseComponentOut("server", "0.1.0-rc22"),
            AboutReleaseComponentOut("agent", "0.1.0-rc17"),
            AboutReleaseComponentOut("app", "0.1.0-rc23"),
        ),
    )

    @Test
    fun `product line, then server agent app with unchanged since for older components`() {
        val view = aboutReleaseFor(rc23)!!
        assertEquals("SteamHangar 0.1.0-rc23", view.productLine)
        assertEquals(
            listOf(
                Triple(AboutReleaseComponent.SERVER, "0.1.0-rc22", "0.1.0-rc22"),
                Triple(AboutReleaseComponent.AGENT, "0.1.0-rc17", "0.1.0-rc17"),
                Triple(AboutReleaseComponent.APP, "0.1.0-rc23", null),
            ),
            view.rows.map { Triple(it.component, it.version, it.unchangedSince) },
        )
    }

    @Test
    fun `MUTATION PIN -- unchanged since follows the natural version order, not string order`() {
        val view = aboutReleaseFor(
            AboutReleaseOut(
                version = "0.1.0",
                components = listOf(
                    AboutReleaseComponentOut("server", "0.1.0-rc21.1"),
                    AboutReleaseComponentOut("agent", "0.1.0-rc9"),
                    AboutReleaseComponentOut("app", "0.1.0"),
                ),
            ),
        )!!
        assertEquals(listOf("0.1.0-rc21.1", "0.1.0-rc9", null), view.rows.map { it.unchangedSince })
    }

    @Test
    fun `nothing for a server before 1c or a local build, junk versions left out, fixed order`() {
        assertNull("server before 1c", aboutReleaseFor(null))
        assertNull(
            "local build",
            aboutReleaseFor(AboutReleaseOut(null, listOf("server", "agent", "app").map { AboutReleaseComponentOut(it, null) })),
        )
        val junk = aboutReleaseFor(
            AboutReleaseOut(
                "0.1.0-rc23",
                listOf(
                    AboutReleaseComponentOut("app", "0.1.0-rc23"),
                    AboutReleaseComponentOut("server", "dev-1234567"),
                    AboutReleaseComponentOut("agent", "invalid"),
                    AboutReleaseComponentOut("x", "1.0.0"),
                ),
            ),
        )!!
        assertEquals(listOf(AboutReleaseComponent.APP), junk.rows.map { it.component })
        val reversed = aboutReleaseFor(rc23.copy(components = rc23.components.reversed()))!!
        assertEquals(AboutReleaseComponent.entries.toList(), reversed.rows.map { it.component })
        val noProduct = aboutReleaseFor(rc23.copy(version = "dev"))!!
        assertNull(noProduct.productLine)
        assertEquals(listOf(null, null, null), noProduct.rows.map { it.unchangedSince })
    }

    @Test
    fun `decodes release, and an answer without it`() {
        val withRelease = VaultJson.decodeFromString(
            AboutOut.serializer(),
            """{"components":[],"release":{"version":"0.1.0-rc23","components":[{"name":"server","version":"0.1.0-rc22"},{"name":"agent","version":null},{"name":"app","version":"0.1.0-rc23"}]}}""",
        )
        assertEquals("0.1.0-rc22", withRelease.release!!.components[0].version)
        assertNull(withRelease.release!!.components[1].version)
        assertNull(VaultJson.decodeFromString(AboutOut.serializer(), """{"components":[]}""").release)
    }

    @Test
    fun `demo shows the block, consistent with the demo vault-api version`() {
        val about = seedAbout()
        val view = aboutReleaseFor(about.release)!!
        val api = about.components.first { it.name == "vault-api" }.version
        assertEquals("SteamHangar $api", view.productLine)
        assertEquals(3, view.rows.size)
    }

    // ---- words (literals from web about-view.js) ----------------------------

    private val xml: String by lazy { File("src/main/res/values/strings.xml").readText(Charsets.UTF_8) }

    private fun res(name: String): String {
        val match = Regex("<string name=\"$name\"[^>]*>(.*?)</string>").find(xml)
            ?: error("no <string name=\"$name\"> found")
        return match.groupValues[1].replace("\\'", "'")
    }

    @Test
    fun `labels and the unchanged-since value match web RELEASE_LABELS and releaseValueText`() {
        assertEquals("Server", res("settings_about_release_server"))
        assertEquals("Agent", res("settings_about_release_agent"))
        assertEquals("App", res("settings_about_release_app"))
        assertEquals("%1\$s (unchanged since %2\$s)", res("settings_about_release_unchanged"))
    }

    // ---- wiring (comment-stripped source scan, enclosing composable) --------

    private val screen = "src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt"

    private fun code(): String =
        File(screen).readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val composableFun = Regex("""@Composable\s+(?:private\s+)?fun\s+(\w+)""")

    private fun assertCalledFrom(call: String, expected: String) {
        val code = code()
        var from = 0
        var idx = -1
        while (true) {
            idx = code.indexOf(call, from)
            if (idx < 0 || !code.substring(0, idx).trimEnd().endsWith("fun")) break
            from = idx + call.length
        }
        assertTrue("'$call' is DELETED -- the wiring is gone", idx >= 0)
        val enclosing = composableFun.findAll(code).filter { it.range.first < idx }.maxByOrNull { it.range.first }?.groupValues?.get(1)
        assertEquals("'$call' MOVED -- its nearest enclosing composable is no longer $expected", expected, enclosing)
    }

    @Test
    fun `MUTATION PIN -- About renders the release block above the rows, with the twin strings`() {
        assertCalledFrom("aboutReleaseFor(controller.about?.release)", "AboutSection")
        assertCalledFrom("AboutReleaseBlock(it)", "AboutSection")
        val code = code()
        val section = code.indexOf("private fun AboutSection(")
        assertTrue(
            "the block comes before the component rows",
            code.indexOf("AboutReleaseBlock(it)", section) < code.indexOf("AboutRowView(row)", section),
        )
        assertCalledFrom("R.string.settings_about_release_unchanged", "AboutReleaseBlock")
        assertCalledFrom("R.string.settings_about_release_server", "AboutReleaseBlock")
        assertCalledFrom("view.productLine?.let", "AboutReleaseBlock")
    }
}
