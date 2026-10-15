package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.storage.InMemoryLibraryPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

private fun row(
    appid: Int,
    name: String? = "Game $appid",
    sizeBytes: Long? = null,
    groupSize: Long? = null,
    prefill: String? = null,
): GameSummary = GameSummary(
    appid = appid,
    name = name,
    status = "idle",
    last_prefill_at = prefill,
    last_manifest_check = null,
    depot_count = 0,
    size_bytes = sizeBytes,
    group_size_bytes = groupSize,
    needs_force = false,
)

private fun ids(rows: List<GameSummary>) = rows.map { it.appid }

private fun sort(rows: List<GameSummary>, key: LibrarySortKey, dir: SortDirection, lastPlayed: Map<Int, Int> = emptyMap()) =
    ids(sortGames(rows, LibrarySort(key, dir), emptyMap(), emptyMap(), lastPlayed))

class LibrarySortTest {

    @Test
    fun `name sorts A-Z and Z-A, case-insensitive, name-less last both ways`() {
        val rows = listOf(
            row(1, "banana"), row(2, null), row(3, "Apple"), row(4, "  "), row(5, "cherry"),
        )
        assertEquals(listOf(3, 1, 5, 2, 4), sort(rows, LibrarySortKey.NAME, SortDirection.ASC))
        assertEquals(listOf(5, 1, 3, 2, 4), sort(rows, LibrarySortKey.NAME, SortDirection.DESC))
    }

    @Test
    fun `name compare ignores accents like web base sensitivity`() {
        val rows = listOf(row(1, "Zebra"), row(2, "\u00c9clair"), row(3, "eagle"), row(4, "Dog"))
        assertEquals(listOf(4, 3, 2, 1), sort(rows, LibrarySortKey.NAME, SortDirection.ASC))
    }

    @Test
    fun `size flips with direction, missing last, group size wins, ties by name`() {
        val rows = listOf(
            row(1, "B", sizeBytes = 10),
            row(2, sizeBytes = null),
            row(3, "A", sizeBytes = 10),
            row(4, sizeBytes = 5, groupSize = 50),
        )
        assertEquals(listOf(3, 1, 4, 2), sort(rows, LibrarySortKey.SIZE, SortDirection.ASC))
        assertEquals(listOf(4, 3, 1, 2), sort(rows, LibrarySortKey.SIZE, SortDirection.DESC))
    }

    @Test
    fun `updated uses the newest member, never cached last`() {
        val rows = listOf(
            row(1, prefill = "2026-10-01T00:00:00Z"),
            row(2),
            row(3, prefill = "2026-09-01T00:00:00Z"),
        )
        val packages = mapOf(3 to listOf(row(9, prefill = "2026-10-10T00:00:00Z")))
        val out = ids(sortGames(rows, LibrarySort(LibrarySortKey.UPDATED, SortDirection.DESC), emptyMap(), packages, emptyMap()))
        assertEquals(listOf(3, 1, 2), out)
    }

    @Test
    fun `last played treats 0 as never played and sorts it last`() {
        val rows = listOf(row(1), row(2), row(3), row(4))
        val played = mapOf(1 to 300, 2 to 0, 3 to 900)
        assertEquals(listOf(3, 1, 2, 4), sort(rows, LibrarySortKey.PLAYED, SortDirection.DESC, played))
    }

    @Test
    fun `last played option is gated by the flag and a stored choice falls back`() {
        assertFalse(lastPlayedAvailable(ownedLastPlayedByAppid(listOf(OwnedGame(appid = 1, name = "x")))))
        val on = ownedLastPlayedByAppid(listOf(OwnedGame(appid = 1, name = "x", rtime_last_played = 5)))
        assertTrue(lastPlayedAvailable(on))
        assertFalse(LibrarySortKey.PLAYED in availableSortKeys(false))
        assertTrue(LibrarySortKey.PLAYED in availableSortKeys(true))
        val stored = LibrarySort(LibrarySortKey.PLAYED, SortDirection.DESC)
        assertEquals(LibrarySort.DEFAULT, normalizeSort(stored, false))
        assertEquals(stored, normalizeSort(stored, true))
    }

    @Test
    fun `pref value round-trips and bad input falls back to the default`() {
        val s = LibrarySort(LibrarySortKey.SIZE, SortDirection.DESC)
        assertEquals(s, LibrarySort.fromPrefValue(s.prefValue))
        assertEquals(LibrarySort.DEFAULT, LibrarySort.fromPrefValue(null))
        assertEquals(LibrarySort.DEFAULT, LibrarySort.fromPrefValue("bogus"))
        assertEquals(LibrarySort.DEFAULT, LibrarySort.fromPrefValue("size:sideways"))
        val prefs = InMemoryLibraryPreferences()
        prefs.setSort(s)
        assertEquals(s, prefs.getSort())
    }

    @Test
    fun `direction label reads A-Z for name and Ascending otherwise`() {
        assertEquals("A-Z", LibrarySortWording.directionLabel(LibrarySort.DEFAULT))
        assertEquals("Z-A", LibrarySortWording.directionLabel(LibrarySort(LibrarySortKey.NAME, SortDirection.DESC)))
        assertEquals("Ascending", LibrarySortWording.directionLabel(LibrarySort(LibrarySortKey.SIZE, SortDirection.ASC)))
        assertEquals("Descending", LibrarySortWording.directionLabel(LibrarySort(LibrarySortKey.SIZE, SortDirection.DESC)))
    }
}
