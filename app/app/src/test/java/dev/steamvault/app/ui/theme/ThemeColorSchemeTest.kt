package dev.steamvault.app.ui.theme

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-SHEET-BG-1: no Material 3 baseline colour may leak into the app.
 *
 * The detail sheet goldens rendered on #1D1B20, the Material 3 baseline dark
 * `surfaceContainerLow`, because [VaultDarkColorScheme] set only the
 * pre-1.2 slots and ModalBottomSheet takes its container from
 * `surfaceContainerLow` (material3 1.3.1, `SheetBottomTokens`). Dialogs,
 * menus, the navigation bar, cards, the switch track, the snackbar and the
 * selected segment / chip / nav indicator read other unset slots the same
 * way. These tests pin:
 *
 * 1. every slot to a LITERAL hex and its VaultColors token name (never
 *    derived from the code under test, docs/learnings/android.md);
 * 2. that the literal table covers every colour slot [ColorScheme] has,
 *    found by reflection: a Material update that adds a slot fails here
 *    until Theme.kt maps it (the slot would otherwise start on the
 *    baseline again);
 * 3. that no slot equals its `darkColorScheme()` baseline default;
 * 4. that the text role on each container stays at 4.5:1 or more.
 *
 * Reflection reads the Kotlin getters of `Color`-typed properties, which
 * compile to zero-argument methods returning `long` named
 * `get<Slot>-<hash>` (inline-class name mangling). In material3 1.3.1 those
 * are exactly the 36 colour slots; the class's other properties (cached
 * component colours) return objects.
 */
class ThemeColorSchemeTest {

    private class Pin(val read: (ColorScheme) -> Color, val token: String, val argb: Long)

    /** Every slot, LITERAL: token name and hex of the VaultColors value it must carry. */
    private val pins: Map<String, Pin> = linkedMapOf(
        "primary" to Pin({ it.primary }, "Accent", 0xFF2ED9CE),
        "onPrimary" to Pin({ it.onPrimary }, "AccentInk", 0xFF04211F),
        "primaryContainer" to Pin({ it.primaryContainer }, "AccentDeep", 0xFF12A79F),
        "onPrimaryContainer" to Pin({ it.onPrimaryContainer }, "AccentInk", 0xFF04211F),
        "inversePrimary" to Pin({ it.inversePrimary }, "Accent", 0xFF2ED9CE),
        "secondary" to Pin({ it.secondary }, "AccentDeep", 0xFF12A79F),
        "onSecondary" to Pin({ it.onSecondary }, "AccentInk", 0xFF04211F),
        "secondaryContainer" to Pin({ it.secondaryContainer }, "Raised2", 0xFF213035),
        "onSecondaryContainer" to Pin({ it.onSecondaryContainer }, "Text", 0xFFE6EFF1),
        "tertiary" to Pin({ it.tertiary }, "Accent", 0xFF2ED9CE),
        "onTertiary" to Pin({ it.onTertiary }, "AccentInk", 0xFF04211F),
        "tertiaryContainer" to Pin({ it.tertiaryContainer }, "Raised2", 0xFF213035),
        "onTertiaryContainer" to Pin({ it.onTertiaryContainer }, "Text", 0xFFE6EFF1),
        "background" to Pin({ it.background }, "Bg", 0xFF081113),
        "onBackground" to Pin({ it.onBackground }, "Text", 0xFFE6EFF1),
        "surface" to Pin({ it.surface }, "Surface", 0xFF111D20),
        "onSurface" to Pin({ it.onSurface }, "Text", 0xFFE6EFF1),
        "surfaceVariant" to Pin({ it.surfaceVariant }, "Raised", 0xFF18262A),
        "onSurfaceVariant" to Pin({ it.onSurfaceVariant }, "Dim", 0xFF8DA1A7),
        "surfaceTint" to Pin({ it.surfaceTint }, "Accent", 0xFF2ED9CE),
        "inverseSurface" to Pin({ it.inverseSurface }, "Raised2", 0xFF213035),
        "inverseOnSurface" to Pin({ it.inverseOnSurface }, "Text", 0xFFE6EFF1),
        "error" to Pin({ it.error }, "StatusDanger", 0xFFFF6F6F),
        // The one literal outside VaultColors, kept from before this WP.
        "onError" to Pin({ it.onError }, "Color.Black", 0xFF000000),
        "errorContainer" to Pin({ it.errorContainer }, "Raised2", 0xFF213035),
        "onErrorContainer" to Pin({ it.onErrorContainer }, "StatusDanger", 0xFFFF6F6F),
        "outline" to Pin({ it.outline }, "Line", 0xFF26363B),
        "outlineVariant" to Pin({ it.outlineVariant }, "LineSoft", 0xFF1D2A2E),
        "scrim" to Pin({ it.scrim }, "Bg", 0xFF081113),
        "surfaceBright" to Pin({ it.surfaceBright }, "Raised2", 0xFF213035),
        "surfaceDim" to Pin({ it.surfaceDim }, "Bg", 0xFF081113),
        "surfaceContainerLowest" to Pin({ it.surfaceContainerLowest }, "Bg", 0xFF081113),
        // ModalBottomSheet: the web `.sheet` is --surface.
        "surfaceContainerLow" to Pin({ it.surfaceContainerLow }, "Surface", 0xFF111D20),
        // DropdownMenu: the web `.pop` is --raised-2.
        "surfaceContainer" to Pin({ it.surfaceContainer }, "Raised2", 0xFF213035),
        // AlertDialog: the web `.dialog` is --raised.
        "surfaceContainerHigh" to Pin({ it.surfaceContainerHigh }, "Raised", 0xFF18262A),
        "surfaceContainerHighest" to Pin({ it.surfaceContainerHighest }, "Raised2", 0xFF213035),
    )

    /** The VaultColors tokens a slot may carry, by name (plus the documented onError literal). */
    private val tokens: Map<String, Color> = mapOf(
        "Bg" to VaultColors.Bg,
        "Surface" to VaultColors.Surface,
        "Raised" to VaultColors.Raised,
        "Raised2" to VaultColors.Raised2,
        "Line" to VaultColors.Line,
        "LineSoft" to VaultColors.LineSoft,
        "Text" to VaultColors.Text,
        "Dim" to VaultColors.Dim,
        "Accent" to VaultColors.Accent,
        "AccentDeep" to VaultColors.AccentDeep,
        "AccentInk" to VaultColors.AccentInk,
        "StatusDanger" to VaultColors.StatusDanger,
        "Color.Black" to Color.Black,
    )

    /** Colour slot name -> its getter, read from [ColorScheme] by reflection. */
    private fun reflectedSlots(): Map<String, java.lang.reflect.Method> =
        ColorScheme::class.java.methods
            .filter { it.parameterCount == 0 && it.returnType == java.lang.Long.TYPE && it.name.startsWith("get") }
            .associateBy { m -> m.name.removePrefix("get").substringBefore('-').replaceFirstChar { it.lowercaseChar() } }

    private fun raw(scheme: ColorScheme, getter: java.lang.reflect.Method): Long = getter.invoke(scheme) as Long

    @Test
    fun `every slot of the app scheme carries its literal vault token`() {
        for ((slot, pin) in pins) {
            val actual = pin.read(VaultDarkColorScheme)
            assertEquals(
                "VaultDarkColorScheme.$slot must be ${pin.token} #${"%08X".format(pin.argb)}",
                pin.argb.toInt(),
                actual.toArgb(),
            )
            val token = tokens[pin.token] ?: error("no token named ${pin.token} in this test's token map")
            assertEquals("VaultDarkColorScheme.$slot must be VaultColors.${pin.token}", token, actual)
        }
    }

    @Test
    fun `the pins cover every colour slot ColorScheme has`() {
        val slots = reflectedSlots().keys
        // Guards the reflection itself: if the getter naming changed, the
        // set would be empty and the comparison below meaningless.
        assertTrue("expected 36 colour slots in material3 1.3.1, reflection found ${slots.size}: $slots", slots.size >= 36)
        val unpinned = slots - pins.keys
        val gone = pins.keys - slots
        assertTrue(
            "ColorScheme slot(s) $unpinned have no mapping: set them in Theme.kt VaultDarkColorScheme to a VaultColors " +
                "token (else they start on the Material baseline) and pin them here",
            unpinned.isEmpty(),
        )
        assertTrue("pinned slot(s) $gone no longer exist in ColorScheme: drop them here", gone.isEmpty())
    }

    @Test
    fun `no slot keeps its Material 3 baseline value`() {
        val baseline = darkColorScheme()
        val leaked = reflectedSlots().filter { (_, getter) -> raw(VaultDarkColorScheme, getter) == raw(baseline, getter) }.keys
        assertTrue("VaultDarkColorScheme slot(s) $leaked still carry the Material 3 baseline colour", leaked.isEmpty())
    }

    /** WCAG 2.x relative luminance of an opaque colour. */
    private fun luminance(c: Color): Double {
        val argb = c.toArgb()
        fun lin(v: Int): Double {
            val s = v / 255.0
            return if (s <= 0.04045) s / 12.92 else Math.pow((s + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * lin((argb shr 16) and 0xFF) + 0.7152 * lin((argb shr 8) and 0xFF) + 0.0722 * lin(argb and 0xFF)
    }

    private fun contrast(a: Color, b: Color): Double {
        val (hi, lo) = listOf(luminance(a), luminance(b)).sortedDescending()
        return (hi + 0.05) / (lo + 0.05)
    }

    @Test
    fun `the text role on every container is 4 point 5 to 1 or more`() {
        val s = VaultDarkColorScheme
        val pairs = listOf(
            Triple("onSurface on surfaceContainerLowest", s.onSurface, s.surfaceContainerLowest),
            Triple("onSurface on surfaceContainerLow (sheet)", s.onSurface, s.surfaceContainerLow),
            Triple("onSurface on surfaceContainer (menu)", s.onSurface, s.surfaceContainer),
            Triple("onSurface on surfaceContainerHigh (dialog)", s.onSurface, s.surfaceContainerHigh),
            Triple("onSurface on surfaceContainerHighest (card)", s.onSurface, s.surfaceContainerHighest),
            // Dialog body text and the sheet's secondary lines use onSurfaceVariant.
            Triple("onSurfaceVariant on surfaceContainerLow", s.onSurfaceVariant, s.surfaceContainerLow),
            Triple("onSurfaceVariant on surfaceContainerHigh", s.onSurfaceVariant, s.surfaceContainerHigh),
            Triple("onSurfaceVariant on surfaceContainerHighest", s.onSurfaceVariant, s.surfaceContainerHighest),
            Triple("inverseOnSurface on inverseSurface (snackbar)", s.inverseOnSurface, s.inverseSurface),
            Triple("inversePrimary on inverseSurface (snackbar action)", s.inversePrimary, s.inverseSurface),
            Triple("onSecondaryContainer on secondaryContainer (selected segment)", s.onSecondaryContainer, s.secondaryContainer),
            Triple("onPrimaryContainer on primaryContainer", s.onPrimaryContainer, s.primaryContainer),
            Triple("onTertiaryContainer on tertiaryContainer", s.onTertiaryContainer, s.tertiaryContainer),
            Triple("onErrorContainer on errorContainer", s.onErrorContainer, s.errorContainer),
        )
        for ((name, fg, bg) in pairs) {
            val ratio = contrast(fg, bg)
            assertTrue("$name is ${"%.2f".format(ratio)}:1, below 4.5:1", ratio >= 4.5)
        }
    }
}
