package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.ui.status.StatusKind
import java.time.Instant

/**
 * Library sort (WP LIB-SORT, rc19; twin of `web/js/lib/library-sort.js`).
 * Pure: no Compose, no Android. Options: Name (A-Z / Z-A through the
 * direction toggle), Size, State, "Last cached or updated" and -- ONLY while
 * the operator's `relay_expose_last_played` privacy setting is on -- "Last
 * played". "Newly bought" is dropped (Steam has no purchase date).
 *
 * **Data sources (no schema added):** size = `group_size_bytes` of a group
 * row else `size_bytes`; state = [dispKind] ranked in-progress, cached,
 * installed-but-not-cached, not cached, other; last cached or updated =
 * `last_prefill_at` (newest of a group's members); last played = the owned
 * list's `rtime_last_played`, which the relay OMITS while the flag is off, so
 * "the flag is on" is exactly "some owned game carries it"
 * ([lastPlayedAvailable]).
 *
 * **Ordering rules (same as web):** stable; ties fall back to the name
 * (A-Z), then the appid. Rows without a value for the chosen key and
 * name-less cards (ID fallbacks) sort LAST in both directions; the toggle
 * only flips the rows that have a value.
 *
 * **String-resource rule, exception recorded** (same as `LibraryFilters.kt`):
 * the labels are a verbatim port of web's `SORT_OPTIONS` / `SORT_*_LABEL`
 * and stay Kotlin literals so `LibrarySortWebTwinTest` can diff them against
 * the web source.
 */
enum class LibrarySortKey(val prefValue: String, val label: String) {
    NAME("name", "Name"),
    SIZE("size", "Size"),
    STATE("state", "State"),
    UPDATED("updated", "Last cached or updated"),
    PLAYED("played", "Last played"),
}

enum class SortDirection(val prefValue: String) {
    ASC("asc"),
    DESC("desc"),
}

data class LibrarySort(val key: LibrarySortKey, val dir: SortDirection) {
    /** `key:dir`, the one string the preferences store. */
    val prefValue: String get() = "${key.prefValue}:${dir.prefValue}"

    companion object {
        val DEFAULT = LibrarySort(LibrarySortKey.NAME, SortDirection.ASC)

        /** Unknown, null or corrupted input falls back to [DEFAULT]. */
        fun fromPrefValue(value: String?): LibrarySort {
            val parts = value?.split(":") ?: return DEFAULT
            if (parts.size != 2) return DEFAULT
            val key = LibrarySortKey.entries.firstOrNull { it.prefValue == parts[0] } ?: return DEFAULT
            val dir = SortDirection.entries.firstOrNull { it.prefValue == parts[1] } ?: return DEFAULT
            return LibrarySort(key, dir)
        }
    }
}

object LibrarySortWording {
    const val SORT_LABEL = "Sort"
    const val ASCENDING = "Ascending"
    const val DESCENDING = "Descending"
    const val NAME_ASC = "A-Z"
    const val NAME_DESC = "Z-A"

    /** The direction toggle's spoken/visible label for [sort]. */
    fun directionLabel(sort: LibrarySort): String = when {
        sort.key == LibrarySortKey.NAME && sort.dir == SortDirection.ASC -> NAME_ASC
        sort.key == LibrarySortKey.NAME -> NAME_DESC
        sort.dir == SortDirection.ASC -> ASCENDING
        else -> DESCENDING
    }
}

/** Owned-list `rtime_last_played` by appid (unix seconds); empty while the
 * relay withholds it (flag off). Only real non-negative values. */
fun ownedLastPlayedByAppid(ownedGames: List<OwnedGame>?): Map<Int, Int> {
    val out = LinkedHashMap<Int, Int>()
    for (g in ownedGames.orEmpty()) {
        val v = g.rtime_last_played ?: continue
        if (v >= 0 && g.appid !in out) out[g.appid] = v
    }
    return out
}

/** Whether the "Last played" option is offered: the relay sent the field. */
fun lastPlayedAvailable(lastPlayedByAppid: Map<Int, Int>): Boolean = lastPlayedByAppid.isNotEmpty()

/** Options the control offers right now. */
fun availableSortKeys(lastPlayedAvailable: Boolean): List<LibrarySortKey> =
    LibrarySortKey.entries.filter { it != LibrarySortKey.PLAYED || lastPlayedAvailable }

/** A stored choice made safe: "Last played" while the flag is off falls back
 * to Name A-Z (and comes back when the flag does). */
fun normalizeSort(choice: LibrarySort, lastPlayedAvailable: Boolean): LibrarySort =
    if (choice.key == LibrarySortKey.PLAYED && !lastPlayedAvailable) LibrarySort.DEFAULT else choice

private fun realName(g: GameSummary): String? = g.name?.takeIf { it.isNotBlank() }

private fun stateRank(g: GameSummary, liveJob: JobSummary?): Int {
    val kind = dispKind(g, liveJob)
    return when {
        kind == StatusKind.RUNNING || kind == StatusKind.UPDATING ||
            kind == StatusKind.VERIFY || kind == StatusKind.PAUSED -> 0
        kind == StatusKind.CACHED -> 1
        installedBadgeFor(g) is InstalledBadge.InstalledNotCached -> 2
        kind == StatusKind.NONE -> 3
        else -> 4
    }
}

private fun parseMillis(iso: String?): Long? =
    if (iso == null) null else try {
        Instant.parse(iso).toEpochMilli()
    } catch (_: java.time.format.DateTimeParseException) {
        null
    }

private fun valueOf(
    g: GameSummary,
    key: LibrarySortKey,
    liveJob: JobSummary?,
    packages: List<GameSummary>,
    lastPlayed: Map<Int, Int>,
): Long? = when (key) {
    LibrarySortKey.NAME -> null
    LibrarySortKey.SIZE -> g.group_size_bytes ?: g.size_bytes
    LibrarySortKey.STATE -> stateRank(g, liveJob).toLong()
    LibrarySortKey.UPDATED -> (listOf(g) + packages).mapNotNull { parseMillis(it.last_prefill_at) }.maxOrNull()
    // 0 is Steam's "never played": no value, not the oldest date.
    LibrarySortKey.PLAYED -> lastPlayed[g.appid]?.takeIf { it > 0 }?.toLong()
}

private class SortRow(val game: GameSummary, val name: String?, val value: Long?)

// Primary strength: case and accents are ignored, like web's
// localeCompare(..., { sensitivity: "base" }). Collator is not thread-safe,
// but sorting runs on the composition thread; guard anyway via synchronized.
private val NAME_COLLATOR: java.text.Collator = java.text.Collator.getInstance(java.util.Locale.ROOT).apply {
    strength = java.text.Collator.PRIMARY
}

private fun compareNamesAsc(a: String?, b: String?): Int = when {
    a == null && b == null -> 0
    a == null -> 1
    b == null -> -1
    else -> NAME_COLLATOR.compare(a, b)
}

private fun compareRows(x: SortRow, y: SortRow, byName: Boolean, sign: Int): Int {
    if (byName) {
        val c = compareNamesAsc(x.name, y.name)
        // Name-less last whatever the direction; real names flip with it.
        if (c != 0) return if (x.name == null || y.name == null) c else sign * c
    } else {
        val xv = x.value
        val yv = y.value
        if (xv == null || yv == null) {
            if (xv != yv) return if (xv == null) 1 else -1
        } else if (xv != yv) {
            return sign * xv.compareTo(yv)
        }
        val c = compareNamesAsc(x.name, y.name)
        if (c != 0) return c
    }
    return x.game.appid.compareTo(y.game.appid)
}

/**
 * @param choice already normalized ([normalizeSort])
 * @param packagesByParent a group card's packages ([GroupedLibrary.packagesByParent])
 * @return a new list; the input is not touched.
 */
fun sortGames(
    games: List<GameSummary>,
    choice: LibrarySort,
    liveJobsByAppid: Map<Int, JobSummary>,
    packagesByParent: Map<Int, List<GameSummary>> = emptyMap(),
    lastPlayed: Map<Int, Int> = emptyMap(),
): List<GameSummary> {
    val sign = if (choice.dir == SortDirection.DESC) -1 else 1
    val byName = choice.key == LibrarySortKey.NAME
    val rows = games.map { g ->
        SortRow(
            g,
            realName(g),
            valueOf(g, choice.key, liveJobsByAppid[g.appid], packagesByParent[g.appid].orEmpty(), lastPlayed),
        )
    }
    // sortedWith is stable, like Array.prototype.sort in web.
    return synchronized(NAME_COLLATOR) {
        rows.sortedWith(Comparator { x, y -> compareRows(x, y, byName, sign) }).map { it.game }
    }
}
