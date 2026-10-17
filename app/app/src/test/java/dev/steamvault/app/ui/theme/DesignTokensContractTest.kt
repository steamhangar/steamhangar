package dev.steamvault.app.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.float
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Shared design-token contract, Android side (WP TOKENS-SYNC-1, ADR-0028).
 *
 * `docs/design/tokens.json` is the one source both frontends are checked
 * against; the web twin is `web/tests/design-tokens-contract.test.js`, which
 * checks theme.css against the same file. No generator: a value changed in
 * Color.kt (or theme.css) without the JSON fails CI. The file is read the
 * same way the `*WebTwinTest` files read web sources: relative to the
 * module directory (`app/app`), which Gradle uses as the unit-test working
 * directory, so the repo root is `../..`.
 *
 * The Kotlin-name -> JSON-key map below is LITERAL (docs/LEARNINGS.md,
 * Android: cross-frontend contracts are never derived from the code under
 * test), and a source scan of Color.kt keeps it complete.
 *
 * Type.kt is deliberately NOT covered: it is a Material 3 type ramp (22 /
 * 16 / 16 / 14 / 11 sp, mono 13 sp), not an sp mirror of the web `--fs-*`
 * px scale (11/12/13/15/18/24/32), so there is no clear mapping to pin.
 * Aligning it is part of the rc21 Compose anatomy follow-up.
 */
class DesignTokensContractTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private val tokens: JsonObject by lazy {
        Json.parseToJsonElement(read("../../docs/design/tokens.json")).jsonObject
    }

    private fun group(name: String): JsonObject =
        tokens[name]?.jsonObject ?: error("tokens.json has no \"$name\" group")

    /** Opaque VaultColors, Kotlin name -> (value, tokens.json `color` key). */
    private val opaque: Map<String, Pair<Color, String>> = mapOf(
        "Bg" to (VaultColors.Bg to "bg"),
        "Surface" to (VaultColors.Surface to "surface"),
        "Raised" to (VaultColors.Raised to "raised"),
        "Raised2" to (VaultColors.Raised2 to "raised-2"),
        "Line" to (VaultColors.Line to "line"),
        "LineSoft" to (VaultColors.LineSoft to "line-soft"),
        "Text" to (VaultColors.Text to "text"),
        "Dim" to (VaultColors.Dim to "dim"),
        "Dim2" to (VaultColors.Dim2 to "dim-2"),
        "Accent" to (VaultColors.Accent to "accent"),
        "AccentDeep" to (VaultColors.AccentDeep to "accent-deep"),
        "AccentInk" to (VaultColors.AccentInk to "accent-ink"),
        "StatusOk" to (VaultColors.StatusOk to "ok"),
        "StatusRun" to (VaultColors.StatusRun to "run"),
        "StatusStale" to (VaultColors.StatusStale to "stale"),
        "StatusNone" to (VaultColors.StatusNone to "none"),
        "StatusDanger" to (VaultColors.StatusDanger to "danger"),
    )

    private fun rgbHex(color: Color): String = String.format("#%06X", color.toArgb() and 0xFFFFFF)

    private fun alphaByte(color: Color): Int = (color.toArgb() ushr 24) and 0xFF

    @Test
    fun `every opaque VaultColors value equals tokens json color`() {
        val json = group("color")
        for ((kotlinName, pair) in opaque) {
            val (color, key) = pair
            val expected = json[key]?.jsonPrimitive?.content ?: error("tokens.json color has no \"$key\" (VaultColors.$kotlinName)")
            assertEquals("VaultColors.$kotlinName vs tokens.json color.$key", expected, rgbHex(color))
            assertEquals("VaultColors.$kotlinName must be opaque", 0xFF, alphaByte(color))
        }
    }

    @Test
    fun `tokens json has no opaque colour that VaultColors lacks`() {
        assertEquals(opaque.values.map { it.second }.toSet(), group("color").keys)
    }

    @Test
    fun `AccentGlow equals tokens json color_alpha accent-glow`() {
        val glow = group("color_alpha")["accent-glow"]?.jsonObject ?: error("tokens.json color_alpha has no accent-glow")
        assertEquals(glow["rgb"]!!.jsonPrimitive.content, rgbHex(VaultColors.AccentGlow))
        val expectedAlpha = (glow["alpha"]!!.jsonPrimitive.float * 255f).roundToInt()
        val actualAlpha = alphaByte(VaultColors.AccentGlow)
        assertTrue("AccentGlow alpha $actualAlpha vs tokens.json $expectedAlpha/255", abs(actualAlpha - expectedAlpha) <= 1)
        assertEquals(setOf("accent-glow"), group("color_alpha").keys)
    }

    @Test
    fun `StatusIconInk equals tokens json color_literal sic-ink`() {
        assertEquals(group("color_literal")["sic-ink"]!!.jsonPrimitive.content, rgbHex(VaultColors.StatusIconInk))
        assertEquals(0xFF, alphaByte(VaultColors.StatusIconInk))
    }

    @Test
    fun `Dim2 is the lifted 7A91A0 (UI-KIT-1, literal pin)`() {
        assertEquals("#7A91A0", rgbHex(VaultColors.Dim2))
    }

    @Test
    fun `Color kt declares exactly the colours this contract covers`() {
        val code = read("src/main/java/dev/steamvault/app/ui/theme/Color.kt")
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
        val declared = Regex("""^\s*val (\w+)\s*=""", RegexOption.MULTILINE).findAll(code).map { it.groupValues[1] }.toSet()
        assertEquals(opaque.keys + setOf("AccentGlow", "StatusIconInk"), declared)
    }
}
