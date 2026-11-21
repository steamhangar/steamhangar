package dev.steamvault.app.ui.status

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import dev.steamvault.app.ui.theme.VaultColors
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for the status-icon pure logic (WP 4b.1 brief:
 * "extract the animate-or-not decision into a pure function" + "JVM unit
 * tests for the pure logic (icon-kind mapping, animate-or-not decision)").
 * No Android framework/Robolectric dependency — everything under test is
 * plain Kotlin, runnable on the bare JVM (no emulator/device available in
 * this environment).
 */
class StatusIconLogicTest {

    // ---------- icon-kind -> glyph mapping ----------
    // 1:1 with KIND_GLYPH in web/js/components/status-icon.js — pin every
    // entry by name so a future edit that silently changes one kind's
    // glyph shape fails a named test instead of just looking different.

    @Test
    fun `cached maps to check`() {
        assertEquals(GlyphShape.CHECK, glyphFor(StatusKind.CACHED))
    }

    @Test
    fun `none maps to download`() {
        assertEquals(GlyphShape.DOWNLOAD, glyphFor(StatusKind.NONE))
    }

    @Test
    fun `running maps to download`() {
        assertEquals(GlyphShape.DOWNLOAD, glyphFor(StatusKind.RUNNING))
    }

    @Test
    fun `stale maps to refresh`() {
        assertEquals(GlyphShape.REFRESH, glyphFor(StatusKind.STALE))
    }

    @Test
    fun `updating maps to refresh`() {
        assertEquals(GlyphShape.REFRESH, glyphFor(StatusKind.UPDATING))
    }

    @Test
    fun `verify maps to refresh`() {
        assertEquals(GlyphShape.REFRESH, glyphFor(StatusKind.VERIFY))
    }

    @Test
    fun `paused maps to pause`() {
        assertEquals(GlyphShape.PAUSE, glyphFor(StatusKind.PAUSED))
    }

    @Test
    fun `error maps to bang`() {
        assertEquals(GlyphShape.BANG, glyphFor(StatusKind.ERROR))
    }

    @Test
    fun `warn maps to bang`() {
        assertEquals(GlyphShape.BANG, glyphFor(StatusKind.WARN))
    }

    @Test
    fun `cancelled maps to stop, muted disc, dark ink (web k-cancelled, CONTRAST-1)`() {
        assertEquals(GlyphShape.STOP, glyphFor(StatusKind.CANCELLED))
        assertEquals(VaultColors.Dim2, backgroundFor(StatusKind.CANCELLED))
        assertEquals(VaultColors.StatusIconInk, inkFor(StatusKind.CANCELLED))
    }

    @Test
    fun `notinuse maps to dash, muted disc, dark ink (web k-notinuse, CONTRAST-1)`() {
        assertEquals(GlyphShape.DASH, glyphFor(StatusKind.NOTINUSE))
        assertEquals(VaultColors.Dim2, backgroundFor(StatusKind.NOTINUSE))
        assertEquals(VaultColors.StatusIconInk, inkFor(StatusKind.NOTINUSE))
    }

    @Test
    fun `partial maps to half, stale disc, dark ink (web k-partial)`() {
        assertEquals(GlyphShape.HALF, glyphFor(StatusKind.PARTIAL))
        assertEquals(VaultColors.StatusStale, backgroundFor(StatusKind.PARTIAL))
        assertEquals(VaultColors.StatusIconInk, inkFor(StatusKind.PARTIAL))
    }

    @Test
    fun `unverified maps to question, the none-grey disc, dark ink (web k-unverified, PILL-TONE-1)`() {
        assertEquals(GlyphShape.QUESTION, glyphFor(StatusKind.UNVERIFIED))
        assertEquals(VaultColors.StatusNone, backgroundFor(StatusKind.UNVERIFIED))
        assertEquals(VaultColors.StatusIconInk, inkFor(StatusKind.UNVERIFIED))
    }

    // ---------- one tone per state (WP PILL-TONE-1, audit D-1) ----------
    // Literal expectations, not derived from toneFor: the web twin pins the
    // same table in web/tests/design-tokens-contract.test.js.

    @Test
    fun `the PILL-TONE-1 decisions -- verify and paused run, update and partial orange`() {
        assertEquals(StatusTone.RUN, toneFor(StatusKind.VERIFY))
        assertEquals(VaultColors.StatusRun, backgroundFor(StatusKind.VERIFY))
        assertEquals(StatusTone.RUN, toneFor(StatusKind.PAUSED))
        assertEquals(VaultColors.StatusRun, backgroundFor(StatusKind.PAUSED))
        assertEquals(StatusTone.STALE, toneFor(StatusKind.STALE))
        assertEquals(VaultColors.StatusStale, backgroundFor(StatusKind.STALE))
        assertEquals(StatusTone.STALE, toneFor(StatusKind.PARTIAL))
        assertEquals(StatusTone.NONE, toneFor(StatusKind.UNVERIFIED))
    }

    @Test
    fun `no disc and no pill uses the accent (status colours never double as accent)`() {
        for (kind in StatusKind.entries) {
            assertTrue("disc of $kind", backgroundFor(kind) != VaultColors.Accent)
            assertTrue("pill word of $kind", pillTextColor(toneFor(kind)) != VaultColors.Accent)
        }
    }

    @Test
    fun `the pill word takes the disc colour for every coloured tone, Dim for the neutral one`() {
        for (tone in StatusTone.entries) {
            val expected = if (tone == StatusTone.NEUTRAL) VaultColors.Dim else discColorFor(tone)
            assertEquals("pill word of $tone", expected, pillTextColor(tone))
        }
        assertEquals(VaultColors.Dim2, discColorFor(StatusTone.NEUTRAL))
        assertEquals(VaultColors.StatusNone, discColorFor(StatusTone.NONE))
    }

    @Test
    fun `the pill ground is the tone at 14 percent over Surface, opaque, Raised2 only for the neutral tone (web kit css)`() {
        assertEquals(VaultColors.Raised2, pillGroundColor(StatusTone.NEUTRAL))
        // Web `color-mix(in srgb, var(--T) 14%, var(--surface))`, rounded to #RRGGBB.
        val web = mapOf(
            StatusTone.OK to 0x1B3732,
            StatusTone.RUN to 0x31352C,
            StatusTone.STALE to 0x302925,
            StatusTone.DANGER to 0x32282F,
            StatusTone.NONE to 0x212D36,
        )
        for ((tone, rgb) in web) {
            val ground = pillGroundColor(tone)
            assertEquals("ground of $tone is opaque (independent of the parent)", 1f, ground.alpha, 0.001f)
            val argb = ground.toArgb()
            for (shift in listOf(16, 8, 0)) {
                val diff = kotlin.math.abs(((argb shr shift) and 0xFF) - ((rgb shr shift) and 0xFF))
                assertTrue("ground of $tone channel $shift vs web #${"%06X".format(rgb)}", diff <= 1)
            }
        }
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
    fun `every pill word is 4 point 5 to 1 or more on its own ground, whatever the parent (PILL-TONE-1)`() {
        // Web twin: css-contrast.test.js (ok 7.39, run 8.21, stale 5.16,
        // danger 5.24, none 4.60, neutral 5.05).
        val expected = mapOf(
            StatusTone.OK to 7.39,
            StatusTone.RUN to 8.21,
            StatusTone.STALE to 5.16,
            StatusTone.DANGER to 5.24,
            StatusTone.NONE to 4.60,
            StatusTone.NEUTRAL to 5.05,
        )
        assertEquals(StatusTone.entries.toSet(), expected.keys)
        for (tone in StatusTone.entries) {
            val r = contrast(pillTextColor(tone), pillGroundColor(tone))
            assertTrue("pill word of $tone is ${"%.2f".format(r)}:1", r >= 4.5)
            assertEquals("pill word of $tone", expected.getValue(tone), r, 0.03)
        }
    }

    @Test
    fun `every coloured card word is 4 point 5 to 1 or more on the card grounds (card word rule 2026-11-21)`() {
        // GameCard.kt cardStatusWordColor: StatusStale, StatusDanger, StatusNone
        // on the grid's Bg and the list row's Surface (web css-contrast twin).
        for (word in listOf(VaultColors.StatusStale, VaultColors.StatusDanger, VaultColors.StatusNone)) {
            for (ground in listOf(VaultColors.Bg, VaultColors.Surface)) {
                val r = contrast(word, ground)
                assertTrue("card word $word on $ground is ${"%.2f".format(r)}:1", r >= 4.5)
            }
        }
        assertEquals(5.65, contrast(VaultColors.StatusNone, VaultColors.Surface), 0.01)
        assertEquals(6.25, contrast(VaultColors.StatusNone, VaultColors.Bg), 0.01)
    }

    @Test
    fun `the glyph ink is 4 point 5 to 1 or more on every disc`() {
        for (kind in StatusKind.entries) {
            val r = contrast(inkFor(kind), backgroundFor(kind))
            assertTrue("ink on the $kind disc is ${"%.2f".format(r)}:1", r >= 4.5)
        }
    }

    @Test
    fun `every kind draws its glyph in the shared dark ink (GLYPH-CONTRAST-1)`() {
        for (kind in StatusKind.entries) {
            assertEquals("ink of $kind", VaultColors.StatusIconInk, inkFor(kind))
        }
    }

    @Test
    fun `the question hook's arc runs from the SVG start point to the SVG end point`() {
        fun pointAt(deg: Float): Pair<Double, Double> {
            val rad = Math.toRadians(deg.toDouble())
            return QUESTION_ARC_CX + QUESTION_ARC_R * kotlin.math.cos(rad) to QUESTION_ARC_CY + QUESTION_ARC_R * kotlin.math.sin(rad)
        }
        val (sx, sy) = pointAt(QUESTION_ARC_START_DEG)
        val (ex, ey) = pointAt(QUESTION_ARC_START_DEG + QUESTION_ARC_SWEEP_DEG)
        // web "M9.1 9.2a2.9 2.9 0 1 1 4.3 2.6": from (9.1,9.2) to (13.4,11.8).
        assertEquals(9.1, sx, 0.02)
        assertEquals(9.2, sy, 0.02)
        assertEquals(13.4, ex, 0.02)
        assertEquals(11.8, ey, 0.02)
        // large-arc flag 1, sweep flag 1: more than half a turn, clockwise (y down).
        assertTrue(QUESTION_ARC_SWEEP_DEG > 180f)
    }

    @Test
    fun `every StatusKind has a glyph mapping`() {
        // Exhaustiveness guard: if a new StatusKind is ever added without
        // updating glyphFor's `when`, this loop (not just the compiler)
        // catches it — glyphFor's `when` has no else branch, so a missing
        // case is already a compile error, but this also documents the
        // invariant as a runtime-checkable fact.
        for (kind in StatusKind.entries) {
            glyphFor(kind) // must not throw
        }
    }

    // ---------- wire-name round trip / unknown-kind fallback ----------

    @Test
    fun `wire name round trips for every kind`() {
        for (kind in StatusKind.entries) {
            assertEquals(kind, StatusKind.fromWireName(kind.wireName))
        }
    }

    @Test
    fun `unknown wire name falls back to none`() {
        assertEquals(StatusKind.NONE, StatusKind.fromWireName("totally-unrecognized-kind"))
    }

    // ---------- animate-or-not decision (the reduced-motion disable path) ----------
    // This is the fail-closed-direction pin the WP brief calls for: flip
    // the "reduced motion -> never animate" branch and one of these tests
    // must die (LEARNINGS.md "Testing discipline" — pin the default
    // direction, not just the happy path).

    @Test
    fun `running animates when animators are enabled`() {
        assertTrue(shouldAnimate(StatusKind.RUNNING, animatorsEnabled = true))
    }

    @Test
    fun `updating animates when animators are enabled`() {
        assertTrue(shouldAnimate(StatusKind.UPDATING, animatorsEnabled = true))
    }

    @Test
    fun `verify animates when animators are enabled`() {
        assertTrue(shouldAnimate(StatusKind.VERIFY, animatorsEnabled = true))
    }

    @Test
    fun `running never animates when animators are disabled (reduced motion)`() {
        assertFalse(shouldAnimate(StatusKind.RUNNING, animatorsEnabled = false))
    }

    @Test
    fun `updating never animates when animators are disabled (reduced motion)`() {
        assertFalse(shouldAnimate(StatusKind.UPDATING, animatorsEnabled = false))
    }

    @Test
    fun `verify never animates when animators are disabled (reduced motion)`() {
        assertFalse(shouldAnimate(StatusKind.VERIFY, animatorsEnabled = false))
    }

    @Test
    fun `static kinds never animate even when animators are enabled`() {
        val staticKinds = listOf(
            StatusKind.CACHED,
            StatusKind.STALE,
            StatusKind.NONE,
            StatusKind.PAUSED,
            StatusKind.ERROR,
            StatusKind.WARN,
            StatusKind.CANCELLED,
            StatusKind.NOTINUSE,
            StatusKind.PARTIAL,
            StatusKind.UNVERIFIED,
        )
        for (kind in staticKinds) {
            assertFalse(
                "expected $kind to stay still even with animators enabled",
                shouldAnimate(kind, animatorsEnabled = true),
            )
        }
    }

    @Test
    fun `no kind animates when animators are disabled`() {
        for (kind in StatusKind.entries) {
            assertFalse(
                "expected $kind to stay still under reduced motion",
                shouldAnimate(kind, animatorsEnabled = false),
            )
        }
    }

    // ---------- download glyph: "arrow falls through" (WP APP-FIX-3) ----------
    // Port of web/tests/status-icon-download.test.js's geometry pins
    // (WP WEB-FIX-7), measured on DOWNLOAD_ARROW_SEGMENTS -- the same list
    // StatusIcon.kt draws.

    private val half = GLYPH_STROKE_WIDTH_UNITS / 2f

    @Test
    fun `fall offset is linear from 0 to one period and clamped`() {
        assertEquals(0f, downloadFallOffset(0f), 1e-4f)
        assertEquals(16f, downloadFallOffset(0.5f), 1e-4f)
        assertEquals(DOWNLOAD_FALL_PERIOD, downloadFallOffset(1f), 1e-4f)
        assertEquals(0f, downloadFallOffset(-3f), 1e-4f)
        assertEquals(DOWNLOAD_FALL_PERIOD, downloadFallOffset(7f), 1e-4f)
    }

    @Test
    fun `period, duration and disc radius are the WEB-FIX-7 values`() {
        assertEquals(32f, DOWNLOAD_FALL_PERIOD, 0f)
        assertEquals(1600, DOWNLOAD_FALL_DURATION_MS)
        assertEquals(18.75f, BADGE_DISC_RADIUS_UNITS, 1e-4f)
    }

    @Test
    fun `the trailing arrow sits exactly one period above the leading one at every phase`() {
        for (i in 0..20) {
            val (lead, trail) = downloadArrowOffsets(i / 20f)
            assertEquals(DOWNLOAD_FALL_PERIOD, lead - trail, 1e-4f)
        }
    }

    @Test
    fun `MUTATION PIN -- at rest (and under reduced motion) the trailing arrow is fully outside the disc, the leading one fully inside`() {
        val (lead, trail) = downloadArrowOffsets(0f)
        assertTrue("parked trailing arrow must not peek into the disc", arrowNearestInkDistance(trail) > BADGE_DISC_RADIUS_UNITS)
        assertEquals(arrowTotalLength(), arrowLengthInsideDisc(lead), 1e-3f)
    }

    @Test
    fun `MUTATION PIN -- at the end frame the leading arrow has left the disc by at least 1 unit, so the snap back is invisible`() {
        val (lead, trail) = downloadArrowOffsets(1f)
        val margin = arrowNearestInkDistance(lead) - BADGE_DISC_RADIUS_UNITS
        assertTrue("leading arrow ink only $margin units outside the disc at the end frame", margin >= 1f)
        // ...and the trailing arrow then sits exactly at the rest position.
        assertEquals(0f, trail, 1e-4f)
    }

    @Test
    fun `at every phase at least one arrow is mostly (65 percent) inside the disc -- never an empty badge`() {
        val full = arrowTotalLength()
        var worst = Float.MAX_VALUE
        for (i in 0..240) {
            val (lead, trail) = downloadArrowOffsets(i / 240f)
            worst = minOf(worst, maxOf(arrowLengthInsideDisc(lead, inset = half), arrowLengthInsideDisc(trail, inset = half)))
        }
        assertTrue("worst phase shows only $worst of $full units", worst >= full * 0.65f)
    }

    @Test
    fun `the arrow segments are the SVG paths ported coordinate for coordinate`() {
        assertEquals(
            listOf(
                GlyphSegment(12f, 3.5f, 12f, 13f),
                GlyphSegment(7.4f, 8.7f, 12f, 13.3f),
                GlyphSegment(12f, 13.3f, 16.6f, 8.7f),
            ),
            DOWNLOAD_ARROW_SEGMENTS,
        )
    }
}
