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
import androidx.compose.runtime.mutableIntStateOf
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
import dev.steamvault.app.ui.library.logic.coverCandidates

/**
 * 2:3 portrait cover art (WP 4b.4 brief: "cover art via Steam CDN by appid
 * ... offline fallback tile with name"). Coil (`AsyncImage`) tries the real
 * Steam CDN URL; [onErrorFallback] flips to a deterministic gradient tile
 * carrying the game's name once Coil reports failure (offline, or an appid
 * the CDN has no art for) -- see `ui/library/logic/CoverArt.kt`'s kdoc for
 * the "Coil vs hand-rolled" justification and the fallback-hash rationale.
 *
 * WP APP-FIX-CARD, both opt-in for callers that draw over the cover (the
 * grid card's capsule pill and update badge): [fallbackTitleInsets] keeps
 * the fallback tile's title clear of them, and [fallbackTitleMaxLines]
 * caps the title, which then ellipsizes within the space left. Callers
 * that pass neither (list-row thumbnail, detail header) keep the plain,
 * clipped title.
 *
 * WP D10 COVER-CACHE-1: [cachedCoverUrl], vault-api's stored copy, is tried
 * before [coverUrl]; the fallback tile shows only after both failed.
 */
@Composable
fun CoverArtImage(
    coverUrl: String,
    name: String,
    fallbackHues: FallbackHues,
    modifier: Modifier = Modifier,
    cachedCoverUrl: String? = null,
    fallbackTitleInsets: PaddingValues = PaddingValues(),
    fallbackTitleMaxLines: Int = Int.MAX_VALUE,
) {
    // WP D10: vault-api's stored copy first (LAN, works offline), then the
    // CDN cover; the fallback tile only after every URL failed.
    val urls = remember(coverUrl, cachedCoverUrl) { coverCandidates(coverUrl, cachedCoverUrl) }
    var index by remember(urls) { mutableIntStateOf(0) }
    val failed = index >= urls.size

    Box(
        modifier = modifier.clip(RoundedCornerShape(10.dp)),
        contentAlignment = Alignment.Center,
    ) {
        if (!failed) {
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(urls[index])
                    .crossfade(true)
                    .build(),
                contentDescription = null, // decorative once loaded -- the card's own semantics carries the name/status.
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
                onError = { index += 1 },
            )
        } else {
            FallbackTile(
                name = name,
                hues = fallbackHues,
                titleInsets = fallbackTitleInsets,
                titleMaxLines = fallbackTitleMaxLines,
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
    titleMaxLines: Int,
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
            maxLines = titleMaxLines,
            // Ellipsis only where the caller set a limit: on a tiny tile it
            // would leave nothing but "…" (WP APP-FIX-CARD review).
            overflow = if (titleMaxLines == Int.MAX_VALUE) TextOverflow.Clip else TextOverflow.Ellipsis,
            // The tag sits inside both paddings, so its bounds are the
            // title's own (LibraryScreenshotTest checks it against the pill).
            modifier = Modifier
                .padding(titleInsets)
                .padding(8.dp)
                .testTag(COVER_FALLBACK_TITLE_TAG),
        )
    }
}

/** Most lines a GRID card's fallback title takes before it ellipsizes
 * (only [GameCard] passes it; list rows and the detail header keep no
 * limit). Five keeps the longest fixture title whole on a 360 dp grid card
 * at font scale 1.0; the tile's height cuts it earlier when that is
 * tighter. */
internal const val FALLBACK_TITLE_MAX_LINES = 5

/** Test tag of the fallback tile's title. */
internal const val COVER_FALLBACK_TITLE_TAG = "cover-fallback-title"
