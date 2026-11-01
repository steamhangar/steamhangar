package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP RUNNER-NAME-1: the product's name is one constant per platform
 * ([PRODUCT_NAME] here, `PRODUCT_NAME` in web `lib/product.js`), and the
 * runner labels read it. Pins: the two constants agree, `app_name` agrees
 * with them (so the D18 rename cannot change one and forget the other), and
 * the Kotlin label sources spell neither the name nor the old "Prefill
 * runner" label themselves.
 */
class ProductNameWebTwinTest {

    @Test
    fun `the product name is the web's and the launcher's`() {
        assertEquals("SteamHangar", PRODUCT_NAME)
        val web = File("../../web/js/lib/product.js")
        check(web.exists()) { "No file at ${web.absolutePath}: MOVED -> update this path." }
        assertTrue(
            "VALUE DRIFT: web lib/product.js no longer exports PRODUCT_NAME = \"$PRODUCT_NAME\"",
            web.readText(Charsets.UTF_8).contains("export const PRODUCT_NAME = \"$PRODUCT_NAME\";"),
        )
        val strings = File("src/main/res/values/strings.xml").readText(Charsets.UTF_8)
        assertTrue("app_name differs from PRODUCT_NAME", strings.contains("<string name=\"app_name\">$PRODUCT_NAME</string>"))
    }

    @Test
    fun `MUTATION TARGET -- the runner labels read PRODUCT_NAME, never a literal`() {
        val sources = listOf(
            "ui/traffic/logic/DownloadsHero.kt",
            "ui/traffic/logic/TrafficView.kt",
            "ui/clients/logic/ClientsPage.kt",
        )
        for (path in sources) {
            val code = File("src/main/java/dev/steamvault/app/$path").readText(Charsets.UTF_8)
                .replace(Regex("/\\*[\\s\\S]*?\\*/"), "")
                .replace(Regex("(?m)^\\s*//.*$"), "")
            assertTrue("$path does not import PRODUCT_NAME", code.contains("import dev.steamvault.app.PRODUCT_NAME\n"))
            assertFalse("$path spells the product's name itself", Regex("SteamHangar|GameHangar").containsMatchIn(code))
            assertFalse("$path still shows the old \"prefill runner\" label", Regex("(?i)prefill runner").containsMatchIn(code))
        }
    }
}
