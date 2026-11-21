package dev.steamvault.app.ui.status

/*
 * Test-only measuring helpers for the running download glyph ("arrow falls
 * through", WP APP-FIX-3). Moved here from src/main StatusIconLogic.kt by
 * WP STATUS-ICON-TEST-HELPERS-1 (audit nit C-19): no production code calls
 * them, only StatusIconLogicTest's geometry pins (the Kotlin port of
 * web/tests/status-icon-download.test.js's `nearestEdge`/`insideLength`).
 * They measure the production DOWNLOAD_ARROW_SEGMENTS (src/main, the same
 * list StatusIcon.kt draws) against the production disc radius and stroke
 * width, so the tests still pin the drawn shape, not a copy of it. The
 * geometry itself (glyph grid, disc radius, period) is documented on
 * DOWNLOAD_FALL_PERIOD in StatusIconLogic.kt.
 */

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
