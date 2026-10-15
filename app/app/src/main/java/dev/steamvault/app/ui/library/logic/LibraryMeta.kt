package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameMeta
import dev.steamvault.app.net.model.GameSummary
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/**
 * Library metadata filters (WP D23 LIBRARY-META-1c, Android twin of
 * `web/js/lib/library-meta.js`). Pure: no Compose, no storage access.
 *
 * Filters by category, genre, minimum rating and release-year range over the
 * optional `meta` of `GET /v1/games` ([GameMeta]; null = unknown). Like web
 * the filter runs CLIENT-side over the one list the Library already loads, so
 * the semantics are those of the API's `category`/`genre`/`min_rating`/
 * `year_from`/`year_to`/`include_unknown` parameters:
 *
 *  - categories are AND-ed, genres are any-of, all clauses are AND-ed;
 *  - a value that is known and fails a clause always excludes the game;
 *  - an unknown value (no meta, field null, no genres) excludes the game
 *    unless [MetaFilter.includeUnknown] is on.
 *
 * [applyMetaFilter]'s `unknownExcluded` equals the API's
 * `X-Meta-Unknown-Excluded`: games that fail ONLY because a value is unknown.
 * The whole panel is hidden while no row carries `meta` ([metaAvailable]).
 *
 * **String-resource rule, exception recorded** (same as `LibrarySort.kt`):
 * the labels are a verbatim port of the web constants and stay Kotlin
 * literals so `LibraryMetaWebTwinTest` can diff them against the web source.
 */

/** One category toggle: [key] is the stored id, [field] the `meta` field it reads. */
enum class MetaCategory(val key: String, val label: String, val field: String) {
    SINGLE("single", "Single-player", "single_player"),
    MULTI("multi", "Multiplayer", "multiplayer"),
    COOP("coop", "Co-op", "coop"),
    VR("vr", "VR", "vr"),
    ;

    /** The tri-state value of this category in [meta]; null = unknown. */
    fun valueIn(meta: GameMeta?): Boolean? = when (this) {
        SINGLE -> meta?.single_player
        MULTI -> meta?.multiplayer
        COOP -> meta?.coop
        VR -> meta?.vr
    }
}

const val META_MIN_YEAR = 1970
const val META_MAX_YEAR = 2100

/** Rating thresholds offered by the control (percent positive reviews). */
val RATING_STEPS: List<Int> = listOf(95, 90, 80, 70, 50)

object MetaFilterWording {
    const val PANEL = "Metadata filters"
    const val CATEGORY = "Category"
    const val GENRE = "Genre"
    const val RATING = "Min rating"
    const val YEAR_FROM = "From year"
    const val YEAR_TO = "To year"
    const val INCLUDE_UNKNOWN = "Include unknown"
    const val CLEAR = "Clear filters"
    const val ANY_RATING = "Any rating"
    const val ANY_GENRE = "Any genre"

    /** "80%+" like web's rating option text. */
    fun ratingStep(n: Int): String = "$n%+"
}

/** Steam genre ids -> labels (same map as web); unknown ids read "Genre <id>". */
val GENRE_LABELS: Map<Int, String> = mapOf(
    1 to "Action",
    2 to "Strategy",
    3 to "RPG",
    4 to "Casual",
    9 to "Racing",
    18 to "Sports",
    23 to "Indie",
    25 to "Adventure",
    28 to "Simulation",
    29 to "Massively Multiplayer",
    37 to "Free to Play",
    70 to "Early Access",
    81 to "Documentary",
    82 to "Episodic",
    84 to "Short",
    85 to "Tutorial",
)

fun genreLabel(id: Int): String = GENRE_LABELS[id] ?: "Genre $id"

data class MetaFilter(
    val categories: Set<MetaCategory> = emptySet(),
    val genres: Set<Int> = emptySet(),
    val minRating: Int? = null,
    val yearFrom: Int? = null,
    val yearTo: Int? = null,
    val includeUnknown: Boolean = false,
) {
    /** `includeUnknown` alone is not "active": it only changes how unknown values read. */
    val active: Boolean
        get() = categories.isNotEmpty() || genres.isNotEmpty() || minRating != null ||
            yearFrom != null || yearTo != null

    companion object {
        val DEFAULT = MetaFilter()
    }
}

private fun inRange(v: Int?, lo: Int, hi: Int): Int? = v?.takeIf { it in lo..hi }

/**
 * An untrusted filter made safe (web `normalizeMetaFilter`): out-of-range
 * numbers and bad genre ids are dropped; swapped years are put in order (the
 * API would answer 422 for from > to).
 */
fun normalizeMetaFilter(f: MetaFilter): MetaFilter {
    val genres = f.genres.filter { it in 1..100000 }.toSortedSet()
    val minRating = inRange(f.minRating, 1, 100)
    var from = inRange(f.yearFrom, META_MIN_YEAR, META_MAX_YEAR)
    var to = inRange(f.yearTo, META_MIN_YEAR, META_MAX_YEAR)
    if (from != null && to != null && from > to) {
        val t = from
        from = to
        to = t
    }
    return MetaFilter(f.categories, genres, minRating, from, to, f.includeUnknown)
}

/** True when any row carries stored library metadata (web `metaAvailable`). */
fun metaAvailable(rows: List<GameSummary>): Boolean = rows.any { it.meta != null }

/** `true` / `false` when the clause is decidable, `null` when unknown. */
private fun clauses(game: GameSummary, f: MetaFilter): List<Boolean?> {
    val m = game.meta
    val out = ArrayList<Boolean?>()
    for (c in MetaCategory.entries) if (c in f.categories) out.add(c.valueIn(m))
    val minRating = f.minRating
    if (minRating != null) out.add(m?.review_percent?.let { it >= minRating })
    val year = m?.release_year
    val yearFrom = f.yearFrom
    val yearTo = f.yearTo
    if (yearFrom != null) out.add(year?.let { it >= yearFrom })
    if (yearTo != null) out.add(year?.let { it <= yearTo })
    if (f.genres.isNotEmpty()) {
        val have = m?.genres.orEmpty()
        out.add(if (have.isEmpty()) null else have.any { it in f.genres })
    }
    return out
}

/** Whether [game] passes the filter (unknown values per `includeUnknown`). */
fun matchesMeta(game: GameSummary, f: MetaFilter): Boolean {
    if (!f.active) return true
    return clauses(game, f).all { it ?: f.includeUnknown }
}

data class MetaFilterResult(val games: List<GameSummary>, val unknownExcluded: Int)

/**
 * Apply the filter. `unknownExcluded` counts the games left out ONLY because
 * some value is unknown (0 while `includeUnknown` is on or no filter is
 * active) -- the twin of `X-Meta-Unknown-Excluded`.
 */
fun applyMetaFilter(games: List<GameSummary>, f: MetaFilter): MetaFilterResult {
    if (!f.active) return MetaFilterResult(games, 0)
    val kept = ArrayList<GameSummary>()
    var unknownExcluded = 0
    for (g in games) {
        val cs = clauses(g, f)
        if (cs.all { it ?: f.includeUnknown }) {
            kept.add(g)
        } else if (!f.includeUnknown && cs.none { it == false }) {
            unknownExcluded += 1
        }
    }
    return MetaFilterResult(kept, unknownExcluded)
}

data class GenreOption(val id: Int, val label: String)

private val GENRE_COLLATOR: java.text.Collator = java.text.Collator.getInstance(java.util.Locale.ROOT).apply {
    strength = java.text.Collator.PRIMARY
}

/** Genres present in the data, labelled and sorted by label (then id). */
fun availableGenres(rows: List<GameSummary>): List<GenreOption> {
    val ids = LinkedHashSet<Int>()
    for (g in rows) g.meta?.genres?.let { ids.addAll(it) }
    return synchronized(GENRE_COLLATOR) {
        ids.map { GenreOption(it, genreLabel(it)) }
            .sortedWith { a, b ->
                val c = GENRE_COLLATOR.compare(a.label, b.label)
                if (c != 0) c else a.id.compareTo(b.id)
            }
    }
}

/** "N games hidden: metadata unknown", or "" when nothing is hidden. */
fun unknownNote(n: Int): String =
    if (n <= 0) "" else "$n ${if (n == 1) "game" else "games"} hidden: metadata unknown"

// ---- persistence (per device, like the sort) ---------------------------

/** The one string the preferences store: a small JSON object. */
fun MetaFilter.toPrefValue(): String = buildJsonObject {
    putJsonArray("categories") { for (c in categories) add(JsonPrimitive(c.key)) }
    putJsonArray("genres") { for (g in genres.sorted()) add(JsonPrimitive(g)) }
    put("minRating", minRating)
    put("yearFrom", yearFrom)
    put("yearTo", yearTo)
    put("includeUnknown", includeUnknown)
}.toString()

private fun JsonElement?.asIntOrNull(): Int? {
    val p = this as? JsonPrimitive ?: return null
    val text = p.contentOrNull ?: return null
    return if (p.isString) text.trim().toIntOrNull() else text.toIntOrNull()
}

/** Corrupted, null or unknown input falls back to [MetaFilter.DEFAULT]; the
 * parsed value is always normalized (web `readStoredMetaFilter`). */
fun metaFilterFromPrefValue(value: String?): MetaFilter {
    if (value.isNullOrBlank()) return MetaFilter.DEFAULT
    val parsed: JsonElement? = try {
        Json.parseToJsonElement(value)
    } catch (_: IllegalArgumentException) {
        null
    }
    val obj = parsed as? JsonObject ?: return MetaFilter.DEFAULT
    val keys = (obj["categories"] as? JsonArray).orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content }
    val cats = MetaCategory.entries.filter { it.key in keys }.toSet()
    val genres = (obj["genres"] as? JsonArray).orEmpty().mapNotNull { it.asIntOrNull() }.toSet()
    val unknownPrim = obj["includeUnknown"] as? JsonPrimitive
    val unknown = unknownPrim != null && !unknownPrim.isString && unknownPrim.booleanOrNull == true
    return normalizeMetaFilter(
        MetaFilter(
            categories = cats,
            genres = genres,
            minRating = obj["minRating"].asIntOrNull(),
            yearFrom = obj["yearFrom"].asIntOrNull(),
            yearTo = obj["yearTo"].asIntOrNull(),
            includeUnknown = unknown,
        ),
    )
}
