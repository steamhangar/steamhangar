package dev.steamvault.app

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP API-FIX-5 wiring pins for the detail sheet, same source-scan technique
 * as [Ag3WiringTest] (no Compose rendering in JVM tests). The library card
 * is pinned behaviourally in `GameCardModelTest`; these two lines are the
 * detail sheet's only path from `GameDetail.cover_url` to the image.
 */
class CoverArtWiringTest {

    private fun source(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
    }

    private val detailSheet = "src/main/java/dev/steamvault/app/ui/detail/GameDetailSheet.kt"

    @Test
    fun `MUTATION PIN -- the detail header passes detail cover_url into coverArtUrl`() {
        val text = source(detailSheet)
        val start = text.indexOf("private fun DetailHeader(")
        check(start >= 0) { "expected to find DetailHeader in GameDetailSheet.kt" }
        val body = text.substring(start)
        assertTrue(
            "DetailHeader must call coverArtUrl(appid, detail?.cover_url) -- without it, new games' " +
                "detail covers fall back to the legacy path, which Valve answers with 404",
            body.contains("coverUrl = coverArtUrl(appid, detail?.cover_url)"),
        )
    }

    @Test
    fun `MUTATION PIN -- gameSummaryFrom copies cover_url across from GameDetail`() {
        val text = source(detailSheet)
        val start = text.indexOf("private fun gameSummaryFrom(")
        check(start >= 0) { "expected to find gameSummaryFrom in GameDetailSheet.kt" }
        val end = text.indexOf("private fun GameDetailSheetBody(", start)
        check(end > start) { "expected GameDetailSheetBody to follow gameSummaryFrom" }
        assertTrue(
            "gameSummaryFrom() must copy cover_url = detail.cover_url",
            text.substring(start, end).contains("cover_url = detail.cover_url"),
        )
    }

    @Test
    fun `MUTATION PIN -- WP D10 the detail header passes the stored cover into CoverArtImage`() {
        val text = source(detailSheet)
        val start = text.indexOf("private fun DetailHeader(")
        check(start >= 0) { "expected to find DetailHeader in GameDetailSheet.kt" }
        assertTrue(
            "DetailHeader must pass cachedCoverUrl = vaultCoverUrl(appid, detail?.cover_cached_url)",
            text.substring(start).contains("cachedCoverUrl = vaultCoverUrl(appid, detail?.cover_cached_url)"),
        )
        val summaryStart = text.indexOf("private fun gameSummaryFrom(")
        val summaryEnd = text.indexOf("private fun GameDetailSheetBody(", summaryStart)
        assertTrue(
            "gameSummaryFrom() must copy cover_cached_url = detail.cover_cached_url",
            text.substring(summaryStart, summaryEnd).contains("cover_cached_url = detail.cover_cached_url"),
        )
    }

    @Test
    fun `MUTATION PIN -- WP D10 both library rows pass the stored cover into CoverArtImage`() {
        for (path in listOf(
            "src/main/java/dev/steamvault/app/ui/library/GameCard.kt",
            "src/main/java/dev/steamvault/app/ui/library/GameListRow.kt",
        )) {
            assertTrue(
                "$path must pass cachedCoverUrl = model.cachedCoverUrl",
                source(path).contains("cachedCoverUrl = model.cachedCoverUrl"),
            )
        }
    }

    @Test
    fun `MUTATION PIN -- WP D10 VaultApplication is Coil's ImageLoaderFactory with a sized disk cache`() {
        val text = source("src/main/java/dev/steamvault/app/VaultApplication.kt")
        assertTrue(text.contains("class VaultApplication : Application(), ImageLoaderFactory"))
        assertTrue(text.contains("override fun newImageLoader(): ImageLoader = buildCoverImageLoader(this)"))
        assertTrue(text.contains(".maxSizeBytes(COVER_DISK_CACHE_BYTES)"))
        assertTrue(text.contains(".directory(app.cacheDir.resolve(COVER_DISK_CACHE_DIR))"))
    }
}
