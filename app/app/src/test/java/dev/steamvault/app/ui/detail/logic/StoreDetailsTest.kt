package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.StoreDetailsOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP D19-1c (app): store-block decisions (web twin: `web/tests/store-details.test.js`). */
class StoreDetailsTest {

    private fun ready(
        description: String? = "A game.",
        genres: List<String> = listOf("Action", "Indie"),
        developers: List<String> = listOf("Some Studio"),
        release: String? = "3 Mar, 2023",
        modes: List<String> = listOf("single_player", "co_op"),
        summary: String? = "Overwhelmingly Positive",
        pos: Long? = 970,
        neg: Long? = 30,
        state: String = "ready",
    ) = StoreDetailsOut(
        appid = 1, state = state, description = description, genres = genres, developers = developers,
        release_date = release, modes = modes, review_summary = summary, review_positive = pos, review_negative = neg,
    )

    @Test
    fun `every non-ready state yields nothing at all`() {
        for (state in listOf("off", "pending", "unavailable", "bogus")) {
            val p = ready(state = state)
            assertFalse(state, isStoreReady(p))
            assertFalse(state, hasStoreContent(p))
            assertNull(storeDescription(p))
            assertTrue(storeModeChips(p).isEmpty())
            assertTrue(storeGenres(p).isEmpty())
            assertNull(storeByLine(p))
            assertNull(storeRatingText(p))
            assertTrue(storeInfoLines(p).isEmpty())
        }
        assertFalse(hasStoreContent(null))
    }

    @Test
    fun `description is trimmed, blank is absent, and it is capped at 600 with an ellipsis`() {
        assertEquals("A game.", storeDescription(ready(description = "  A game.  ")))
        assertNull(storeDescription(ready(description = "   ")))
        assertNull(storeDescription(ready(description = null)))
        val exact = "x".repeat(600)
        assertEquals(exact, storeDescription(ready(description = exact)))
        val capped = storeDescription(ready(description = "y".repeat(601)))!!
        assertEquals(600, capped.length)
        assertTrue(capped.endsWith("…"))
        assertEquals("y".repeat(599), capped.dropLast(1))
    }

    @Test
    fun `mode chips keep server order and drop unknown flags`() {
        assertEquals(
            listOf("Single-player", "Multiplayer", "Co-op", "VR"),
            storeModeChips(ready(modes = listOf("single_player", "multiplayer", "co_op", "vr"))),
        )
        assertEquals(listOf("VR", "Single-player"), storeModeChips(ready(modes = listOf("vr", "teleport", "single_player"))))
        assertTrue(storeModeChips(ready(modes = emptyList())).isEmpty())
    }

    @Test
    fun `genres drop blanks and by-line joins developers with the release date`() {
        assertEquals(listOf("Action", "Indie"), storeGenres(ready(genres = listOf("Action", " ", "Indie"))))
        assertEquals("Some Studio – 3 Mar, 2023", storeByLine(ready()))
        assertEquals("A, B – 3 Mar, 2023", storeByLine(ready(developers = listOf("A", "", "B"))))
        assertEquals("Some Studio", storeByLine(ready(release = null)))
        assertEquals("3 Mar, 2023", storeByLine(ready(developers = emptyList())))
        assertNull(storeByLine(ready(developers = emptyList(), release = " ")))
    }

    @Test
    fun `rating is overall only, rounds half up, groups thousands, and needs usable counts`() {
        assertEquals("Overwhelmingly Positive (97% of 1,000 reviews)", storeRatingText(ready()))
        assertEquals("98% of 200 reviews", storeRatingText(ready(summary = null, pos = 195, neg = 5)))
        assertEquals("1% of 1,234,567 reviews", storeRatingText(ready(summary = " ", pos = 12_345, neg = 1_222_222)))
        assertNull(storeRatingText(ready(pos = 0, neg = 0)))
        assertNull(storeRatingText(ready(pos = -1, neg = 5)))
        assertNull(storeRatingText(ready(pos = null, neg = 5)))
        assertNull(storeRatingText(ready(pos = 5, neg = null)))
        assertEquals("0% of 3 reviews", storeRatingText(ready(summary = null, pos = 0, neg = 3)))
    }

    @Test
    fun `info lines come in the web order and empty ones are dropped`() {
        assertEquals(
            listOf("Action, Indie", "Some Studio – 3 Mar, 2023", "Overwhelmingly Positive (97% of 1,000 reviews)"),
            storeInfoLines(ready()),
        )
        assertEquals(listOf("Some Studio"), storeInfoLines(ready(genres = emptyList(), release = null, pos = null)))
    }

    @Test
    fun `a ready payload with nothing in it has no content, so no block`() {
        val empty = StoreDetailsOut(appid = 1, state = "ready")
        assertFalse(hasStoreContent(empty))
        assertTrue(hasStoreContent(StoreDetailsOut(appid = 1, state = "ready", modes = listOf("vr"))))
        assertFalse(hasStoreContent(StoreDetailsOut(appid = 1, state = "ready", modes = listOf("nope"))))
    }

    @Test
    fun `retry pacing is four tries`() {
        assertEquals(listOf(1500L, 3000L, 6000L, 12000L), STORE_RETRY_MS)
    }
}
