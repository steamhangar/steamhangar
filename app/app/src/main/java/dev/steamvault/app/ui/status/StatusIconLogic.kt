package dev.steamvault.app.ui.status

import androidx.compose.ui.graphics.Color
import dev.steamvault.app.ui.theme.VaultColors

/**
 * Pure logic for the status-icon system (WP 4b.1) — deliberately kept free
 * of any Android/Compose runtime dependency beyond plain data types
 * (`Color` is a value class, not a framework call) so it is unit-testable
 * on the plain JVM without Robolectric or an emulator (none is available in
 * this environment).
 */

/** Which glyph shape a given [StatusKind] uses — ported from `KIND_GLYPH` in
 *  web/js/components/status-icon.js. */
enum class GlyphShape { CHECK, DOWNLOAD, REFRESH, BANG, PAUSE, STOP, DASH }

/** [StatusKind] -> [GlyphShape], 1:1 with web's `KIND_GLYPH` table. */
fun glyphFor(kind: StatusKind): GlyphShape = when (kind) {
    StatusKind.CACHED -> GlyphShape.CHECK
    StatusKind.NONE -> GlyphShape.DOWNLOAD
    StatusKind.STALE -> GlyphShape.REFRESH
    StatusKind.RUNNING -> GlyphShape.DOWNLOAD
    StatusKind.UPDATING -> GlyphShape.REFRESH
    StatusKind.VERIFY -> GlyphShape.REFRESH
    StatusKind.PAUSED -> GlyphShape.PAUSE
    StatusKind.ERROR -> GlyphShape.BANG
    StatusKind.WARN -> GlyphShape.BANG
    StatusKind.CANCELLED -> GlyphShape.STOP
    StatusKind.NOTINUSE -> GlyphShape.DASH
}

/**
 * The kinds that carry motion at all, independent of the reduced-motion
 * setting — 1:1 with the web CSS rules `.sic.k-running .dla`,
 * `.sic.k-updating .rot`, `.sic.k-verify .rot` (theme.css). Every other kind
 * (cached, none, stale, paused, error, warn, cancelled) is completely still
 * by design — "a library at rest never flickers" (mockup NOTES.md round 5).
 */
private val ANIMATED_KINDS = setOf(StatusKind.RUNNING, StatusKind.UPDATING, StatusKind.VERIFY)

/**
 * The animate-or-not decision, extracted as a pure function so the
 * reduced-motion disable path is provable in a JVM unit test without a
 * device (WP brief).
 *
 * @param kind the status kind being rendered.
 * @param animatorsEnabled the live value of `ValueAnimator.areAnimatorsEnabled()`
 *   (see [dev.steamvault.app.ui.status.AnimatorsEnabled] for how the caller
 *   obtains it) — false when the system "Remove animations" accessibility
 *   toggle / Settings > Developer options > Animator duration scale is set
 *   to "Animation off".
 * @return true only if this kind is one of the motion-carrying kinds AND
 *   the system has not disabled animations.
 */
fun shouldAnimate(kind: StatusKind, animatorsEnabled: Boolean): Boolean =
    animatorsEnabled && kind in ANIMATED_KINDS

/**
 * Background colour for a status-icon circle — 1:1 with the `.sic.k-*`
 * background rules in web/css/theme.css. [StatusKind.PAUSED] intentionally
 * shares `--run` with RUNNING/UPDATING (same as the web CSS), and
 * [StatusKind.VERIFY] uses the accent colour, not a status colour — both
 * ported unchanged from the CSS, not independent Android choices.
 */
fun backgroundFor(kind: StatusKind): Color = when (kind) {
    StatusKind.CACHED -> VaultColors.StatusOk
    StatusKind.NONE -> VaultColors.StatusNone
    StatusKind.STALE -> VaultColors.StatusStale
    StatusKind.PAUSED -> VaultColors.StatusRun
    StatusKind.RUNNING -> VaultColors.StatusRun
    StatusKind.UPDATING -> VaultColors.StatusRun
    StatusKind.VERIFY -> VaultColors.Accent
    StatusKind.ERROR -> VaultColors.StatusDanger
    StatusKind.WARN -> VaultColors.StatusStale
    StatusKind.CANCELLED -> VaultColors.Dim2
    // web theme.css: `.sic.k-unknown, .sic.k-notinuse{ background:var(--dim-2); color:var(--text); }`
    StatusKind.NOTINUSE -> VaultColors.Dim2
}

/**
 * Glyph ink colour for a status-icon circle. Every kind uses the shared
 * dark ink (`.sic { color:#08120F }`) EXCEPT cancelled, which uses the
 * light text colour against its muted background (`.sic.k-cancelled
 * { color:var(--text) }`) — ported unchanged from theme.css.
 */
fun inkFor(kind: StatusKind): Color = when (kind) {
    StatusKind.CANCELLED -> VaultColors.Text
    StatusKind.NOTINUSE -> VaultColors.Text
    else -> VaultColors.StatusIconInk
}

/**
 * The running download glyph, "arrow falls through" (WP APP-FIX-3, Android
 * parity with web WP WEB-FIX-7; replaces the old drift-and-fade, which read
 * as "nothing is happening"). Two identical arrows one [DOWNLOAD_FALL_PERIOD]
 * apart fall through the badge, clipped to the badge's own disc, in a
 * seamless linear loop. Transform only: no opacity animation anywhere.
 *
 * Geometry (all in the glyph's 24-unit grid, the same grid the SVG paths in
 * `web/js/components/status-icon.js` use):
 *  - the glyph box is [GLYPH_BOX_FRACTION] (64%) of the badge, centred, so
 *    the badge disc is centred on (12,12) with radius
 *    [BADGE_DISC_RADIUS_UNITS] = 12 / 0.64 = 18.75 (y -6.75..30.75);
 *  - at rest (and under reduced motion) the trailing arrow is parked one
 *    period above the leading one, fully outside the disc, so the static
 *    glyph is ONE arrow;
 *  - at the end frame (offset = period) the leading arrow has fully left
 *    the disc (nearest ink 3.4 units outside) and the trailing one sits
 *    exactly at the rest position, so the snap back to 0 shows no change.
 *    That needs the period to clear the exit (P >= ~28.6) and to keep the
 *    parked arrow hidden at rest (P > ~21.4); 32 meets both with margin;
 *  - linear: constant speed hides the seam; 1.6 s per period (~20 units/s).
 *
 * Both values are twin-pinned against the web source
 * (`StatusIconWebTwinTest`): the period against `DOWNLOAD_FALL_PERIOD` in
 * status-icon.js, the duration against `vault-dlfall`'s use in theme.css.
 */
const val DOWNLOAD_FALL_PERIOD = 32f

/** One full fall period, in milliseconds (web: `vault-dlfall 1.6s linear infinite`). */
const val DOWNLOAD_FALL_DURATION_MS = 1600

/** The glyph box's share of the badge diameter (web `.sic svg{ width:64% }`). */
const val GLYPH_BOX_FRACTION = 0.64f

/** The badge disc's radius in glyph units: 12 / 0.64 = 18.75. */
const val BADGE_DISC_RADIUS_UNITS = 12f / GLYPH_BOX_FRACTION

/** Stroke width of every line glyph, in glyph units (web `stroke-width="2.7"`). */
const val GLYPH_STROKE_WIDTH_UNITS = 2.7f

/** A straight stroke of a glyph, in glyph units. */
data class GlyphSegment(val x1: Float, val y1: Float, val x2: Float, val y2: Float)

/**
 * The download arrow as straight segments -- the SVG paths "M12 3.5V13" and
 * "M7.4 8.7 12 13.3 16.6 8.7", ported coordinate for coordinate. The SAME
 * list is what `StatusIcon.kt` draws and what the geometry functions below
 * measure, so the tests pin the drawn shape, not a copy of it.
 */
val DOWNLOAD_ARROW_SEGMENTS: List<GlyphSegment> = listOf(
    GlyphSegment(12f, 3.5f, 12f, 13f),
    GlyphSegment(7.4f, 8.7f, 12f, 13.3f),
    GlyphSegment(12f, 13.3f, 16.6f, 8.7f),
)

/**
 * The falling group's vertical offset in glyph units at [progress] (0..1 of
 * one period): linear from 0 to [DOWNLOAD_FALL_PERIOD]. Clamped.
 */
fun downloadFallOffset(progress: Float): Float = progress.coerceIn(0f, 1f) * DOWNLOAD_FALL_PERIOD

/**
 * Vertical offsets of the two arrows at [progress]: the leading arrow at
 * [downloadFallOffset], the trailing one exactly one period above it. Only
 * the RUNNING kind draws the trailing arrow (web: only `running` builds
 * `g.dlnext`); at progress 0 (reduced motion) the trailing arrow is the
 * parked, clipped-away one.
 */
fun downloadArrowOffsets(progress: Float): List<Float> {
    val lead = downloadFallOffset(progress)
    return listOf(lead, lead - DOWNLOAD_FALL_PERIOD)
}

/**
 * The closest any ink of the arrow (shifted down by [dy]) comes to the disc
 * centre, minus the stroke half-width: a value greater than
 * [BADGE_DISC_RADIUS_UNITS] means the arrow is fully outside the disc.
 * Sampled along each segment (web test helper `nearestEdge`).
 */
fun arrowNearestInkDistance(dy: Float, segments: List<GlyphSegment> = DOWNLOAD_ARROW_SEGMENTS): Float {
    var best = Float.MAX_VALUE
    for (s in segments) {
        for (i in 0..SAMPLES) {
            val t = i.toFloat() / SAMPLES
            val x = s.x1 + (s.x2 - s.x1) * t
            val y = s.y1 + (s.y2 - s.y1) * t + dy
            best = minOf(best, kotlin.math.hypot(x - 12f, y - 12f) - GLYPH_STROKE_WIDTH_UNITS / 2f)
        }
    }
    return best
}

/**
 * Stroke length of the arrow (shifted down by [dy]) whose centre line lies
 * inside the disc shrunk by [inset] (web test helper `insideLength`).
 * `inset = stroke half-width` counts only length whose ink is fully inside.
 */
fun arrowLengthInsideDisc(
    dy: Float,
    inset: Float = 0f,
    radius: Float = BADGE_DISC_RADIUS_UNITS,
    segments: List<GlyphSegment> = DOWNLOAD_ARROW_SEGMENTS,
): Float {
    var total = 0f
    for (s in segments) {
        val len = kotlin.math.hypot(s.x2 - s.x1, s.y2 - s.y1)
        for (i in 0 until SAMPLES) {
            val t = (i + 0.5f) / SAMPLES
            val x = s.x1 + (s.x2 - s.x1) * t
            val y = s.y1 + (s.y2 - s.y1) * t + dy
            if (kotlin.math.hypot(x - 12f, y - 12f) <= radius - inset) total += len / SAMPLES
        }
    }
    return total
}

/** Total stroke length of the arrow. */
fun arrowTotalLength(segments: List<GlyphSegment> = DOWNLOAD_ARROW_SEGMENTS): Float =
    segments.sumOf { kotlin.math.hypot(it.x2 - it.x1, it.y2 - it.y1).toDouble() }.toFloat()

private const val SAMPLES = 200
