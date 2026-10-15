package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.GameMeta
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.storage.InMemoryLibraryPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private fun meta(
    single: Boolean? = true,
    multi: Boolean? = false,
    coop: Boolean? = false,
    vr: Boolean? = false,
    genres: List<Int> = listOf(1),
    rating: Int? = 90,
    year: Int? = 2020,
): GameMeta = GameMeta(
    single_player = single,
    multiplayer = multi,
    coop = coop,
    vr = vr,
    genres = genres,
    review_percent = rating,
    review_score = 8,
    release_year = year,
    fetched_at = "2026-10-15T00:00:00Z",
)

private fun game(appid: Int, m: GameMeta?, name: String? = "G$appid"): GameSummary =
    GameSummary(appid = appid, name = name, status = "idle", depot_count = 0, meta = m)

private fun f(
    categories: Set<MetaCategory> = emptySet(),
    genres: Set<Int> = emptySet(),
    minRating: Int? = null,
    yearFrom: Int? = null,
    yearTo: Int? = null,
    includeUnknown: Boolean = false,
): MetaFilter = normalizeMetaFilter(MetaFilter(categories, genres, minRating, yearFrom, yearTo, includeUnknown))

private fun ids(rows: List<GameSummary>) = rows.map { it.appid }

/** JVM twin of the cases in `web/tests/library-meta.test.js` (WP D23). */
class LibraryMetaTest {

    @Test
    fun `metaAvailable is false while every meta is null and true with one`() {
        assertFalse(metaAvailable(listOf(game(1, null), game(2, null))))
        assertFalse(metaAvailable(emptyList()))
        assertTrue(metaAvailable(listOf(game(1, null), game(2, meta()))))
    }

    @Test
    fun `meta decodes from the wire and an older server without it reads as unknown`() {
        val with = VaultJson.decodeFromString<GameSummary>(
            """{"appid":1,"status":"idle","depot_count":0,"meta":{"single_player":true,"coop":null,"genres":[1,3],"review_percent":88,"release_year":2019,"fetched_at":"x","future_field":1}}""",
        )
        assertEquals(88, with.meta?.review_percent)
        assertEquals(listOf(1, 3), with.meta?.genres)
        assertNull(with.meta?.coop)
        val without = VaultJson.decodeFromString<GameSummary>("""{"appid":2,"status":"idle","depot_count":0}""")
        assertNull(without.meta)
        val nullMeta = VaultJson.decodeFromString<GameSummary>("""{"appid":3,"status":"idle","depot_count":0,"meta":null}""")
        assertNull(nullMeta.meta)
    }

    @Test
    fun `filter - categories AND, genres any-of, rating and year bounds inclusive`() {
        val rows = listOf(
            game(1, meta(coop = true, multi = true, genres = listOf(1, 3), rating = 80, year = 2015)),
            game(2, meta(coop = true, genres = listOf(2), rating = 79, year = 2015)),
            game(3, meta(vr = true, genres = listOf(3), rating = 95, year = 2024)),
        )
        fun run(flt: MetaFilter) = ids(applyMetaFilter(rows, flt).games)
        assertEquals(listOf(1, 2), run(f(categories = setOf(MetaCategory.COOP))))
        assertEquals(listOf(1), run(f(categories = setOf(MetaCategory.COOP, MetaCategory.MULTI))))
        assertEquals(listOf(1, 2, 3), run(f(genres = setOf(2, 3))))
        assertEquals(listOf(2), run(f(genres = setOf(2))))
        assertEquals(listOf(1, 3), run(f(minRating = 80)))
        assertEquals(listOf(1, 2), run(f(yearFrom = 2015, yearTo = 2015)))
        assertEquals(listOf(3), run(f(yearFrom = 2016)))
        assertEquals(emptyList<Int>(), run(f(categories = setOf(MetaCategory.VR), minRating = 96)))
        assertEquals(listOf(1, 2, 3), run(f()))
    }

    @Test
    fun `unknown metadata is excluded and counted by default and kept with include-unknown`() {
        val rows = listOf(
            game(1, meta(rating = 90)),
            game(2, meta(rating = 40)), // known and failing: never counted as unknown
            game(3, null),
            game(4, meta(rating = null)),
            game(5, null, name = "owned only"),
        )
        val off = applyMetaFilter(rows, f(minRating = 80))
        assertEquals(listOf(1), ids(off.games))
        assertEquals(3, off.unknownExcluded)
        val on = applyMetaFilter(rows, f(minRating = 80, includeUnknown = true))
        assertEquals(listOf(1, 3, 4, 5), ids(on.games))
        assertEquals(0, on.unknownExcluded)
        // A known failing clause still excludes with include-unknown (API rule).
        assertFalse(matchesMeta(rows[1], f(minRating = 80, includeUnknown = true)))
        // Unknown for one clause but failing another is not "unknown-hidden".
        val mixed = applyMetaFilter(
            listOf(game(6, meta(rating = null, coop = false))),
            f(minRating = 80, categories = setOf(MetaCategory.COOP)),
        )
        assertEquals(0, mixed.unknownExcluded)
        // No active filter: nothing hidden or counted; include-unknown alone is not a filter.
        assertEquals(0, applyMetaFilter(rows, f()).unknownExcluded)
        assertEquals(5, applyMetaFilter(rows, f(includeUnknown = true)).games.size)
    }

    @Test
    fun `a null category flag is unknown and an empty genre list is unknown`() {
        val rows = listOf(game(1, meta(coop = null)), game(2, meta(coop = true)))
        val r = applyMetaFilter(rows, f(categories = setOf(MetaCategory.COOP)))
        assertEquals(listOf(2), ids(r.games))
        assertEquals(1, r.unknownExcluded)
        val g = listOf(game(1, meta(genres = emptyList())), game(2, meta(genres = listOf(1))))
        val gr = applyMetaFilter(g, f(genres = setOf(1)))
        assertEquals(listOf(2), ids(gr.games))
        assertEquals(1, gr.unknownExcluded)
    }

    @Test
    fun `unknownNote wording is neutral and singular-plural aware`() {
        assertEquals("", unknownNote(0))
        assertEquals("1 game hidden: metadata unknown", unknownNote(1))
        assertEquals("12 games hidden: metadata unknown", unknownNote(12))
        assertFalse(unknownNote(2).contains("steam", ignoreCase = true))
    }

    @Test
    fun `normalizeMetaFilter makes garbage safe and orders swapped years`() {
        val n = normalizeMetaFilter(
            MetaFilter(
                categories = setOf(MetaCategory.COOP, MetaCategory.VR),
                genres = setOf(3, -1, 1, 0, 200000),
                minRating = 101,
                yearFrom = 2020,
                yearTo = 2010,
            ),
        )
        assertEquals(setOf(MetaCategory.COOP, MetaCategory.VR), n.categories)
        assertEquals(listOf(1, 3), n.genres.toList())
        assertNull(n.minRating)
        assertEquals(2010 to 2020, n.yearFrom to n.yearTo)
        assertNull(normalizeMetaFilter(MetaFilter(yearFrom = 1800)).yearFrom)
        assertEquals(85, normalizeMetaFilter(MetaFilter(minRating = 85)).minRating)
    }

    @Test
    fun `availableGenres lists the ids in the data with web labels sorted by label`() {
        val out = availableGenres(
            listOf(game(1, meta(genres = listOf(3, 1))), game(2, meta(genres = listOf(999))), game(3, null)),
        )
        assertEquals(listOf(1, 999, 3), out.map { it.id })
        assertEquals(listOf("Action", "Genre 999", "RPG"), out.map { it.label })
    }

    @Test
    fun `stored filter round-trips and corrupted input falls back to the default`() {
        val flt = f(
            categories = setOf(MetaCategory.COOP, MetaCategory.VR),
            genres = setOf(3, 1),
            minRating = 80,
            yearFrom = 2010,
            yearTo = 2020,
            includeUnknown = true,
        )
        assertEquals(flt, metaFilterFromPrefValue(flt.toPrefValue()))
        assertEquals(MetaFilter.DEFAULT, metaFilterFromPrefValue(MetaFilter.DEFAULT.toPrefValue()))
        assertEquals(MetaFilter.DEFAULT, metaFilterFromPrefValue(null))
        assertEquals(MetaFilter.DEFAULT, metaFilterFromPrefValue(""))
        assertEquals(MetaFilter.DEFAULT, metaFilterFromPrefValue("not json"))
        assertEquals(MetaFilter.DEFAULT, metaFilterFromPrefValue("[1,2]"))
        // Hand-edited values are normalized on read like web's readStoredMetaFilter.
        val hostile = metaFilterFromPrefValue(
            """{"categories":["coop","bogus"],"genres":[3,"3",-1,"x"],"minRating":"85","yearFrom":2020,"yearTo":2010,"includeUnknown":"yes"}""",
        )
        assertEquals(setOf(MetaCategory.COOP), hostile.categories)
        assertEquals(setOf(3), hostile.genres)
        assertEquals(85, hostile.minRating)
        assertEquals(2010 to 2020, hostile.yearFrom to hostile.yearTo)
        assertFalse(hostile.includeUnknown)
        val prefs = InMemoryLibraryPreferences()
        prefs.setMetaFilter(flt)
        assertEquals(flt, prefs.getMetaFilter())
    }

    @Test
    fun `rating and year sort options exist only with metadata and normalize back`() {
        assertEquals(
            listOf(LibrarySortKey.NAME, LibrarySortKey.SIZE, LibrarySortKey.STATE, LibrarySortKey.UPDATED),
            availableSortKeys(lastPlayedAvailable = false),
        )
        assertEquals(
            listOf(
                LibrarySortKey.NAME, LibrarySortKey.SIZE, LibrarySortKey.STATE, LibrarySortKey.UPDATED,
                LibrarySortKey.RATING, LibrarySortKey.YEAR,
            ),
            availableSortKeys(lastPlayedAvailable = false, metaAvailable = true),
        )
        val rating = LibrarySort(LibrarySortKey.RATING, SortDirection.DESC)
        assertEquals(LibrarySort.DEFAULT, normalizeSort(rating, lastPlayedAvailable = false))
        val year = LibrarySort(LibrarySortKey.YEAR, SortDirection.ASC)
        assertEquals(year, normalizeSort(year, lastPlayedAvailable = false, metaAvailable = true))
        assertEquals(SortDirection.DESC, LibrarySortKey.RATING.defaultDir)
        assertEquals(SortDirection.ASC, LibrarySortKey.NAME.defaultDir)
        assertEquals(rating, LibrarySort.fromPrefValue("rating:desc"))
    }

    @Test
    fun `picking rating or year resets the direction only when crossing the group`() {
        val nameAsc = LibrarySort(LibrarySortKey.NAME, SortDirection.ASC)
        assertEquals(
            LibrarySort(LibrarySortKey.RATING, SortDirection.DESC),
            sortAfterKeyPick(nameAsc, LibrarySortKey.RATING),
        )
        val ratingAsc = LibrarySort(LibrarySortKey.RATING, SortDirection.ASC)
        assertEquals(
            LibrarySort(LibrarySortKey.YEAR, SortDirection.ASC),
            sortAfterKeyPick(ratingAsc, LibrarySortKey.YEAR),
        )
        assertEquals(
            LibrarySort(LibrarySortKey.SIZE, SortDirection.ASC),
            sortAfterKeyPick(ratingAsc, LibrarySortKey.SIZE),
        )
        val sizeDesc = LibrarySort(LibrarySortKey.SIZE, SortDirection.DESC)
        assertEquals(
            LibrarySort(LibrarySortKey.STATE, SortDirection.DESC),
            sortAfterKeyPick(sizeDesc, LibrarySortKey.STATE),
        )
    }

    @Test
    fun `sort by rating and year puts unknown last in both directions, ties by name`() {
        val rows = listOf(
            game(1, meta(rating = 70, year = 2010)),
            game(2, null),
            game(3, meta(rating = 95, year = 2022)),
            game(4, meta(rating = null, year = null)),
            game(5, meta(rating = 70, year = 2010), name = "A"),
        )
        fun by(key: LibrarySortKey, dir: SortDirection) =
            ids(sortGames(rows, LibrarySort(key, dir), emptyMap()))
        assertEquals(listOf(3, 5, 1, 2, 4), by(LibrarySortKey.RATING, SortDirection.DESC))
        assertEquals(listOf(5, 1, 3, 2, 4), by(LibrarySortKey.RATING, SortDirection.ASC))
        assertEquals(listOf(3, 5, 1, 2, 4), by(LibrarySortKey.YEAR, SortDirection.DESC))
        assertEquals(listOf(5, 1, 3, 2, 4), by(LibrarySortKey.YEAR, SortDirection.ASC))
    }
}
