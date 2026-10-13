package dev.steamvault.app.ui.nav

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * The main app shell (WP D12 APP-FIX-LAYOUT): an outer Scaffold that owns
 * only the bottom navigation, and the destination content below it. Shared
 * by `MainActivity.setContent` and the Library screenshot test, so the
 * test renders the real shell instead of a hand-kept copy.
 *
 * Window insets (the D12 "tall header" and blank band above the nav bar):
 * every destination brings its own Scaffold + TopAppBar, which applies the
 * status-bar inset itself. Before D12 the outer Scaffold ALSO padded its
 * content by the status bar (its default `contentWindowInsets` is
 * safeDrawing) and consumed nothing, so the inset was applied twice at the
 * top (a status-bar-high blank band above the app bar) and the inner
 * Scaffold padded its content by the navigation-bar inset a second time,
 * above the bottom nav that already handles it. Now:
 * - the outer Scaffold takes no window insets of its own
 *   (`contentWindowInsets = WindowInsets(0)`), so its content padding is
 *   just the bottom nav bar's height (which includes the navigation-bar
 *   inset, [androidx.compose.material3.NavigationBar] pads for it);
 * - that padding is consumed, so nothing inside re-applies the
 *   navigation-bar inset;
 * - the status-bar inset stays unconsumed and is applied exactly once, by
 *   the destination's TopAppBar, which then also paints behind the status
 *   bar (edge-to-edge).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MainShell(
    current: Destination,
    pendingJobsCount: Int,
    onSelect: (Destination) -> Unit,
    content: @Composable BoxScope.() -> Unit,
) {
    Scaffold(
        bottomBar = {
            BottomNavBar(
                current = current,
                pendingJobsCount = pendingJobsCount,
                onSelect = onSelect,
            )
        },
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .consumeWindowInsets(innerPadding),
            content = content,
        )
    }
}
