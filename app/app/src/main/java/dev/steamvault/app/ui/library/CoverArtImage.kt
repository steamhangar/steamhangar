package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import dev.steamvault.app.R
import dev.steamvault.app.ui.library.logic.FallbackHues

/**
 * 2:3 portrait cover art (WP 4b.4 brief: "cover art via Steam CDN by appid
 * ... offline fallback tile with name"). Coil (`AsyncImage`) tries the real
 * Steam CDN URL; [onErrorFallback] flips to a deterministic gradient tile
 * carrying the game's name once Coil reports failure (offline, or an appid
 * the CDN has no art for) -- see `ui/library/logic/CoverArt.kt`'s kdoc for
 * the "Coil vs hand-rolled" justification and the fallback-hash rationale.
 *
 * [fallbackTitleInsets] keeps the fallback tile's title clear of whatever
 * the caller draws over the cover (WP APP-FIX-CARD: the grid card's
 * capsule pill and update badge); the title is capped at
 * [FALLBACK_TITLE_MAX_LINES] and ellipsizes within the space left.
 */
@Composable
fun CoverArtImage(
    coverUrl: String,
    name: String,
    fallbackHues: FallbackHues,
    modifier: Modifier = Modifier,
    fallbackTitleInsets: PaddingValues = PaddingValues(),
) {
    var failed by remember(coverUrl) { mutableStateOf(false) }

    Box(
        modifier = modifier.clip(RoundedCornerShape(10.dp)),
        contentAlignment = Alignment.Center,
    ) {
        if (!failed) {
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(coverUrl)
                    .crossfade(true)
                    .build(),
                contentDescription = null, // decorative once loaded -- the card's own semantics carries the name/status.
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
                onError = { failed = true },
            )
        } else {
            FallbackTile(
                name = name,
                hues = fallbackHues,
                titleInsets = fallbackTitleInsets,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Composable
private fun FallbackTile(
    name: String,
    hues: FallbackHues,
    titleInsets: PaddingValues,
    modifier: Modifier = Modifier,
) {
    val color1 = Color.hsv(hues.h1.toFloat(), 0.55f, 0.35f)
    val color2 = Color.hsv(hues.h2.toFloat(), 0.55f, 0.20f)
    val description = stringResource(R.string.library_cover_fallback_description, name)
    Box(
        modifier = modifier
            .semantics { contentDescription = description }
            .background(Brush.linearGradient(listOf(color1, color2))),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = name,
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onBackground,
            textAlign = TextAlign.Center,
            maxLines = FALLBACK_TITLE_MAX_LINES,
            overflow = TextOverflow.Ellipsis,
            // The tag sits inside both paddings, so its bounds are the
            // title's own (LibraryScreenshotTest checks it against the pill).
            modifier = Modifier
                .padding(titleInsets)
                .padding(8.dp)
                .testTag(COVER_FALLBACK_TITLE_TAG),
        )
    }
}

/** Most lines the fallback tile's title takes before it ellipsizes. Five
 * keeps the longest fixture title whole on a 360 dp grid card at font
 * scale 1.0; the tile's height cuts it earlier when that is tighter. */
internal const val FALLBACK_TITLE_MAX_LINES = 5

/** Test tag of the fallback tile's title. */
internal const val COVER_FALLBACK_TITLE_TAG = "cover-fallback-title"
