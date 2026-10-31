package dev.steamvault.app.ui.library

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
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.library.logic.LibraryLayout

/**
 * WP ANDROID-REDESIGN-1b: the Library toolbar's small glyphs (web
 * library.html: search, layout segments, sort direction), hand-drawn on a
 * Canvas in the same house style as `ui/nav/NavIcons.kt` (no icon-pack
 * dependency). Each reads [LocalContentColor], so a button's enabled /
 * selected tint applies. Decorative only: the button that hosts a glyph
 * carries the spoken label.
 */
private val GLYPH_SIZE = 18.dp

private fun stroke(scale: Float) = Stroke(width = 1.9f * scale, cap = StrokeCap.Round, join = StrokeJoin.Round)

/** Magnifier (web `.search svg`: circle r7 at 11/11, handle to 20/20). */
@Composable
internal fun SearchGlyph(modifier: Modifier = Modifier, size: Dp = GLYPH_SIZE) {
    val color = LocalContentColor.current
    Canvas(modifier.size(size)) {
        val s = this.size.minDimension / 24f
        drawCircle(color, radius = 7f * s, center = Offset(11f * s, 11f * s), style = stroke(s))
        drawLine(color, Offset(16.2f * s, 16.2f * s), Offset(20f * s, 20f * s), 1.9f * s, StrokeCap.Round)
    }
}

/** The layout segment glyphs: 2x2 tiles, 3x3 tiles, list rows. */
@Composable
internal fun LayoutGlyph(layout: LibraryLayout, modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(GLYPH_SIZE)) {
        val s = this.size.minDimension / 24f
        when (layout) {
            LibraryLayout.GRID_2, LibraryLayout.GRID_3 -> {
                val n = if (layout == LibraryLayout.GRID_2) 2 else 3
                val gap = 2.5f * s
                val span = 18f * s
                val cell = (span - gap * (n - 1)) / n
                for (r in 0 until n) {
                    for (c in 0 until n) {
                        drawRoundRect(
                            color = color,
                            topLeft = Offset(3f * s + c * (cell + gap), 3f * s + r * (cell + gap)),
                            size = Size(cell, cell),
                            cornerRadius = CornerRadius(1.2f * s),
                            style = stroke(s * 0.8f),
                        )
                    }
                }
            }
            LibraryLayout.LIST -> {
                for (y in listOf(6f, 12f, 18f)) {
                    drawCircle(color, radius = 1.3f * s, center = Offset(4.5f * s, y * s))
                    drawLine(color, Offset(9f * s, y * s), Offset(20.5f * s, y * s), 1.9f * s, StrokeCap.Round)
                }
            }
        }
    }
}

/** Sort direction: an arrow up (ascending) or down (descending). */
@Composable
internal fun SortDirectionGlyph(descending: Boolean, modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(GLYPH_SIZE)) {
        val s = this.size.minDimension / 24f
        val (tip, tail) = if (descending) 19f to 5f else 5f to 19f
        val wing = if (descending) -5f else 5f
        drawLine(color, Offset(12f * s, tail * s), Offset(12f * s, tip * s), 1.9f * s, StrokeCap.Round)
        val head = Path().apply {
            moveTo(7f * s, (tip + wing) * s)
            lineTo(12f * s, tip * s)
            lineTo(17f * s, (tip + wing) * s)
        }
        drawPath(head, color, style = stroke(s))
    }
}

/** A small chevron pointing down (dropdown affordance). */
@Composable
internal fun ChevronDownGlyph(modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(GLYPH_SIZE)) {
        val s = this.size.minDimension / 24f
        val p = Path().apply {
            moveTo(7f * s, 10f * s)
            lineTo(12f * s, 15f * s)
            lineTo(17f * s, 10f * s)
        }
        drawPath(p, color, style = stroke(s))
    }
}

/** Select mode: a rounded square with a check (on) or without (off). */
@Composable
internal fun SelectGlyph(on: Boolean, modifier: Modifier = Modifier) {
    val color = LocalContentColor.current
    Canvas(modifier.size(GLYPH_SIZE)) {
        val s = this.size.minDimension / 24f
        drawRoundRect(
            color = color,
            topLeft = Offset(4f * s, 4f * s),
            size = Size(16f * s, 16f * s),
            cornerRadius = CornerRadius(3f * s),
            style = stroke(s),
        )
        if (on) {
            val p = Path().apply {
                moveTo(8f * s, 12.5f * s)
                lineTo(11f * s, 15.5f * s)
                lineTo(16.5f * s, 9f * s)
            }
            drawPath(p, color, style = stroke(s))
        }
    }
}
