package dev.steamvault.app.ui.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/** Toast corners, web `.toast{ border-radius:var(--r-m) }` (12px). */
val VaultToastShape: Shape = RoundedCornerShape(12.dp)

/**
 * The app's toast, twin of the web `.toast` (theme.css), WP ANDROID-SHEET-BG-1:
 * the Snackbar ground is the theme's `inverseSurface` (--raised-2) with
 * `inverseOnSurface` text (--text); this adds the `1px solid var(--line)`
 * edge and the 3px leading key line (`.toast-key`, --accent), which a warn
 * toast swaps to --stale (`.toast.warn .toast-key`): "pay attention", never
 * "failed" (the Library's paused-dedupe toast, LibraryScreen.kt). The key
 * stretches to the text's height like `align-self:stretch`.
 *
 * WP SNACKBAR-LIVE-1: every toast is a polite live region, so TalkBack reads
 * it when it appears (Detail, Downloads, Library, Clients); call sites add
 * no live region of their own.
 */
@Composable
fun VaultSnackbar(message: String, modifier: Modifier = Modifier, warn: Boolean = false) {
    Snackbar(
        modifier = modifier
            .semantics { liveRegion = LiveRegionMode.Polite }
            .border(1.dp, VaultColors.Line, VaultToastShape),
        shape = VaultToastShape,
    ) {
        Row(modifier = Modifier.height(IntrinsicSize.Min), verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .width(3.dp)
                    .fillMaxHeight()
                    .background(if (warn) VaultColors.StatusStale else VaultColors.Accent, RoundedCornerShape(percent = 50)),
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(message)
        }
    }
}
