package dev.steamvault.app.ui.status

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
    fun `cancelled maps to stop`() {
        assertEquals(GlyphShape.STOP, glyphFor(StatusKind.CANCELLED))
    }

    @Test
    fun `notinuse maps to dash, muted disc, light ink (web k-notinuse)`() {
        assertEquals(GlyphShape.DASH, glyphFor(StatusKind.NOTINUSE))
        assertEquals(VaultColors.Dim2, backgroundFor(StatusKind.NOTINUSE))
        assertEquals(VaultColors.Text, inkFor(StatusKind.NOTINUSE))
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
