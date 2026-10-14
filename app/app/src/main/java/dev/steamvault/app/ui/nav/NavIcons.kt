package dev.steamvault.app.ui.nav

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.unit.dp

/**
 * Bottom-nav icons (WP 4b.4), hand-drawn on a Canvas rather than pulled from
 * `androidx.compose.material:material-icons-extended` -- same house style
 * `StatusIcon.kt` already established (WP 4b.1's kdoc: every status glyph is
 * a coordinate-for-coordinate Canvas port, not an icon-pack lookup). The
 * extended icon pack is not a dependency of this project (only
 * material-icons-CORE ships transitively via material3, and core has no
 * "download"/grid glyph that fits), and hand-drawing a few simple shapes is
 * cheaper and more controllable than adding a large new icon-pack
 * dependency for a handful of glyphs. Each icon reads
 * [LocalContentColor] so `NavigationBarItem`'s own selected/unselected tint
 * (Material3's standard mechanism for icon slots) applies automatically,
 * the same as a stock `Icon(...)` would get.
 */

private val NAV_ICON_SIZE = 22.dp

/** Library: a simple 2x2 grid, echoing the mockup's grid-layout toggle
 * icon (docs/design/vault-app-mockup.html header controls) more literally
 * than a generic "list" glyph would. */
@Composable
fun LibraryNavIcon(modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(NAV_ICON_SIZE)) {
        val cell = size.minDimension * 0.42f
        val gap = size.minDimension * 0.16f
        val originX = (size.width - (cell * 2 + gap)) / 2f
        val originY = (size.height - (cell * 2 + gap)) / 2f
        val corner = CornerRadius(cell * 0.18f)
        for (row in 0..1) {
            for (col in 0..1) {
                drawRoundRect(
                    color = color,
                    topLeft = Offset(originX + col * (cell + gap), originY + row * (cell + gap)),
                    size = Size(cell, cell),
                    cornerRadius = corner,
                )
            }
        }
    }
}

/** Clients (WP D15d): a monitor on a stand -- "machines that load from
 * the cache", the same idea as web's Clients nav glyph, drawn in this
 * file's stroke style. */
@Composable
fun ClientsNavIcon(modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(NAV_ICON_SIZE)) {
        val scale = size.minDimension / 24f
        val strokeWidth = 2.2f * scale
        val stroke = Stroke(width = strokeWidth, cap = StrokeCap.Round, join = StrokeJoin.Round)
        drawRoundRect(
            color = color,
            topLeft = Offset(3.5f * scale, 4.5f * scale),
            size = Size(17f * scale, 11.5f * scale),
            cornerRadius = CornerRadius(1.8f * scale),
            style = stroke,
        )
        drawLine(color, Offset(12f * scale, 16f * scale), Offset(12f * scale, 19.5f * scale), strokeWidth, StrokeCap.Round)
        drawLine(color, Offset(8f * scale, 19.5f * scale), Offset(16f * scale, 19.5f * scale), strokeWidth, StrokeCap.Round)
    }
}

/** Downloads: the same arrow-into-baseline shape as `StatusIcon`'s static
 * DOWNLOAD glyph (`StatusIcon.kt`'s "M12 3.5V13" + baseline), un-animated --
 * this is chrome, not a status indicator, so it never carries motion. */
@Composable
fun DownloadsNavIcon(modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(NAV_ICON_SIZE)) {
        val scale = size.minDimension / 24f
        val strokeWidth = 2.4f * scale
        val stroke = Stroke(width = strokeWidth, cap = StrokeCap.Round, join = StrokeJoin.Round)
        fun pt(x: Float, y: Float) = Offset(x * scale, y * scale)

        val arrow = Path().apply {
            moveTo(pt(12f, 3.5f).x, pt(12f, 3.5f).y)
            lineTo(pt(12f, 13f).x, pt(12f, 13f).y)
            moveTo(pt(7.4f, 8.7f).x, pt(7.4f, 8.7f).y)
            lineTo(pt(12f, 13.3f).x, pt(12f, 13.3f).y)
            lineTo(pt(16.6f, 8.7f).x, pt(16.6f, 8.7f).y)
        }
        drawPath(arrow, color = color, style = stroke)

        val baseline = Path().apply {
            moveTo(pt(5f, 19.6f).x, pt(5f, 19.6f).y)
            lineTo(pt(19f, 19.6f).x, pt(19f, 19.6f).y)
        }
        drawPath(baseline, color = color, style = stroke)
    }
}

/** Settings: the mockup's round-6 "clean gear" (body circle, hub, eight
 * teeth at exact 45-degree intervals of identical length -- mockup-notes.md
 * "A clean gear"). */
@Composable
fun SettingsNavIcon(modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(NAV_ICON_SIZE)) {
        val scale = size.minDimension / 24f
        val strokeWidth = 2.2f * scale
        val stroke = Stroke(width = strokeWidth, cap = StrokeCap.Round)
        val center = Offset(12f * scale, 12f * scale)
        val bodyRadius = 5.4f * scale

        drawCircle(color = color, radius = bodyRadius, center = center, style = stroke)
        drawCircle(color = color, radius = 1.6f * scale, center = center)

        val toothLength = 2.4f * scale
        val toothInner = bodyRadius + 0.4f * scale
        for (i in 0 until 8) {
            rotate(degrees = i * 45f, pivot = center) {
                drawLine(
                    color = color,
                    start = Offset(center.x, center.y - toothInner),
                    end = Offset(center.x, center.y - (toothInner + toothLength)),
                    strokeWidth = strokeWidth,
                    cap = StrokeCap.Round,
                )
            }
        }
    }
}
