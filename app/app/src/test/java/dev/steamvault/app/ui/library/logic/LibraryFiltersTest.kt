package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

private fun game(
    appid: Int,
    name: String,
    status: String = "idle",
    sizeBytes: Long? = null,
    updateAvailable: Boolean = false,
): GameSummary =
    GameSummary(
        appid = appid,
        name = name,
        status = status,
        last_prefill_at = null,
        last_manifest_check = null,
        depot_count = 0,
        size_bytes = sizeBytes,
        needs_force = false,
        update_available = updateAvailable,
    )

private fun job(appid: Int, status: String = "running"): JobSummary = JobSummary(
    id = appid,
    appid = appid,
    type = "prefill",
    status = status,
    created_at = "2026-08-01T00:00:00Z",
)

class LibraryFiltersTest {

    private val notCached = game(1, "Nebula Drift")
    private val cached = game(2, "Ironwood Hollow", status = "done", sizeBytes = 5_000_000_000)
    private val failed = game(3, "Tundra Protocol", status = "error")
    private val downloading = game(4, "Meridian Rally")
    private val outdated = game(5, "Glasswing", status = "done", sizeBytes = 2_000_000_000, updateAvailable = true)

    private val games = listOf(notCached, cached, failed, downloading, outdated)
    private val liveJobsByAppid = indexLiveJobsByAppid(listOf(job(4, "running")))

    // ---- normalizeQuery / matchesQuery ----------------------------------

    @Test
    fun `normalizeQuery trims and lowercases`() {
        assertEquals("nebula", normalizeQuery("  Nebula  "))
        assertEquals("", normalizeQuery(null))
    }

    @Test
    fun `matchesQuery is case-insensitive substring (query pre-normalized by the caller, mirrors normalizeQuery contract)`() {
        assertTrue(matchesQuery(notCached, "drift"))
        assertTrue(matchesQuery(notCached, normalizeQuery("NEBULA")))
        assertFalse(matchesQuery(notCached, "hollow"))
    }

    @Test
    fun `matchesQuery always true for an empty query`() {
        assertTrue(matchesQuery(notCached, ""))
    }

    // ---- the recorded chip set: All Cached Not-cached Downloading Failed Updates --

    @Test
    fun `chip set is exactly All Cached Not-cached Installed-not-cached Downloading Failed Updates, in order`() {
        assertEquals(
            listOf("all", "cached", "none", "installed", "downloading", "failed", "updates"),
            FILTER_DEFS.map { it.key },
        )
    }

    @Test
    fun `filterByKey falls back to all for an unknown key`() {
        assertEquals("all", filterByKey("update_ready").key)
    }

    @Test
    fun `chip set labels match the web port's FILTER_DEFS labels verbatim`() {
        // Literal-vs-literal pin (docs/LEARNINGS.md "Android (Phase 4b)":
        // cross-frontend contracts need LITERAL expected sets, never
        // derived from the enum/list under test) -- hand-transcribed from
        // web/js/lib/library-filters.js's own FILTER_DEFS, not read back
        // from this file. This is what qualifies FILTER_DEFS's labels for
        // the literal-Kotlin-string exception this file's kdoc documents,
        // instead of routing them through strings.xml. "Updates" is the
        // UPD-2/UPD-3 brief's label (D14), written for both frontends at once.
        assertEquals(
            listOf("All", "Cached", "Not cached", "Installed, not cached", "Downloading", "Failed", "Updates"),
            FILTER_DEFS.map { it.label },
        )
    }

    // ---- visibleGames: search AND chip -----------------------------------

    @Test
    fun `all chip with no query returns everything`() {
        val visible = visibleGames(games, query = "", filterKey = "all", liveJobsByAppid = liveJobsByAppid)
        assertEquals(games.map { it.appid }, visible.map { it.appid })
    }

    @Test
    fun `cached chip returns only cached-with-bytes games`() {
        val visible = visibleGames(games, query = "", filterKey = "cached", liveJobsByAppid = liveJobsByAppid)
        assertEquals(listOf(cached.appid, outdated.appid), visible.map { it.appid })
    }

    @Test
    fun `MUTATION PIN -- updates chip returns only games flagged update_available`() {
        val visible = visibleGames(games, query = "", filterKey = "updates", liveJobsByAppid = liveJobsByAppid)
        assertEquals(listOf(outdated.appid), visible.map { it.appid })
    }

    @Test
    fun `MUTATION PIN -- the Updates chip is hidden only on a vault without the update check`() {
        assertEquals(emptySet<String>(), hiddenChipKeys(updatesSupported = true))
        assertEquals(setOf("updates"), hiddenChipKeys(updatesSupported = false))
        val keys = chipCounts(games, "", liveJobsByAppid, hiddenChipKeys(updatesSupported = false)).map { it.key }
        assertEquals(listOf("all", "cached", "none", "installed", "downloading", "failed"), keys)
    }

    @Test
    fun `none chip returns only not-cached games`() {
        val visible = visibleGames(games, query = "", filterKey = "none", liveJobsByAppid = liveJobsByAppid)
        assertEquals(listOf(notCached.appid), visible.map { it.appid })
    }

    @Test
    fun `downloading chip returns only games with a live job`() {
        val visible = visibleGames(games, query = "", filterKey = "downloading", liveJobsByAppid = liveJobsByAppid)
        assertEquals(listOf(downloading.appid), visible.map { it.appid })
    }

    @Test
    fun `failed chip returns only error-status games`() {
        val visible = visibleGames(games, query = "", filterKey = "failed", liveJobsByAppid = liveJobsByAppid)
        assertEquals(listOf(failed.appid), visible.map { it.appid })
    }

    @Test
    fun `search and chip are ANDed`() {
        val visible = visibleGames(
            games,
            query = "tundra",
            filterKey = "failed",
            liveJobsByAppid = liveJobsByAppid,
        )
        assertEquals(listOf(failed.appid), visible.map { it.appid })

        val emptyIntersection = visibleGames(
            games,
            query = "tundra",
            filterKey = "cached",
            liveJobsByAppid = liveJobsByAppid,
        )
        assertTrue(emptyIntersection.isEmpty())
    }

    // ---- chipCounts: recompute against the current query -----------------

    @Test
    fun `chipCounts against an empty query count the whole library`() {
        val counts = chipCounts(games, query = "", liveJobsByAppid = liveJobsByAppid).associateBy { it.key }
        assertEquals(5, counts.getValue("all").count)
        assertEquals(2, counts.getValue("cached").count)
        assertEquals(1, counts.getValue("none").count)
        assertEquals(1, counts.getValue("downloading").count)
        assertEquals(1, counts.getValue("failed").count)
        assertEquals(1, counts.getValue("updates").count)
        // WP LIB-FILTER-INST-1: always listed, 0 when nothing matches.
        assertEquals(0, counts.getValue("installed").count)
    }

    @Test
    fun `chipCounts narrow to only what the current query would show`() {
        val counts = chipCounts(games, query = "meridian", liveJobsByAppid = liveJobsByAppid).associateBy { it.key }
        assertEquals(1, counts.getValue("all").count)
        assertEquals(0, counts.getValue("cached").count)
        assertEquals(1, counts.getValue("downloading").count)
        assertEquals(0, counts.getValue("failed").count)
        assertEquals(0, counts.getValue("updates").count)
    }
}
