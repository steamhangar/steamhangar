package dev.steamvault.app.ui.library.logic

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP D23: every label, genre id and threshold of
 * `LibraryMeta.kt` must still be in `web/js/lib/library-meta.js` (the panel's
 * aria-label and the "N%+" option text live in `web/js/views/library.js`).
 * VALUE drift -> update LibraryMeta.kt; GRAMMAR drift -> adjust this test.
 */
class LibraryMetaWebTwinTest {
    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return file.readText(Charsets.UTF_8)
    }

    private val meta: String by lazy { read("../../web/js/lib/library-meta.js") }
    private val view: String by lazy { read("../../web/js/views/library.js") }

    @Test
    fun `category keys labels and fields are still in the web source`() {
        val bad = MetaCategory.entries.filter {
            !Regex(
                "key:\\s*\"${it.key}\",\\s*label:\\s*\"${Regex.escape(it.label)}\",\\s*field:\\s*\"${it.field}\"",
            ).containsMatchIn(meta)
        }
        assertTrue("Not a key/label/field triple in web: $bad", bad.isEmpty())
    }

    @Test
    fun `wording literals are still in the web source`() {
        val wording = mapOf(
            "category" to MetaFilterWording.CATEGORY,
            "genre" to MetaFilterWording.GENRE,
            "rating" to MetaFilterWording.RATING,
            "yearFrom" to MetaFilterWording.YEAR_FROM,
            "yearTo" to MetaFilterWording.YEAR_TO,
            "includeUnknown" to MetaFilterWording.INCLUDE_UNKNOWN,
            "clear" to MetaFilterWording.CLEAR,
            "anyRating" to MetaFilterWording.ANY_RATING,
            "anyGenre" to MetaFilterWording.ANY_GENRE,
        )
        val bad = wording.filter { (key, text) -> !Regex("$key:\\s*\"${Regex.escape(text)}\"").containsMatchIn(meta) }
        assertTrue("Not in web META wording: ${bad.keys}", bad.isEmpty())
        assertTrue("Panel aria-label drifted", view.contains("\"${MetaFilterWording.PANEL}\""))
        assertTrue("Rating option text drifted", view.contains("`\${n}%+`") && MetaFilterWording.ratingStep(80) == "80%+")
    }

    @Test
    fun `genre map thresholds and unknown wording are still in the web source`() {
        val badGenres = GENRE_LABELS.filter { (id, label) -> !Regex("\\b$id:\\s*\"${Regex.escape(label)}\"").containsMatchIn(meta) }
        assertTrue("Genre ids not in web: ${badGenres.keys}", badGenres.isEmpty())
        val webGenreCount = Regex("^\\s{2}\\d+:\\s*\"[^\"]+\",", RegexOption.MULTILINE)
            .findAll(meta.substringAfter("GENRE_LABELS").substringBefore("});")).count()
        assertTrue("Web has $webGenreCount genre labels, Android ${GENRE_LABELS.size}", webGenreCount == GENRE_LABELS.size)
        assertTrue(meta.contains("`Genre \${id}`"))
        assertTrue(meta.contains("RATING_STEPS = Object.freeze([${RATING_STEPS.joinToString(", ")}])"))
        assertTrue(meta.contains("MIN_YEAR = $META_MIN_YEAR;"))
        assertTrue(meta.contains("MAX_YEAR = $META_MAX_YEAR;"))
        assertTrue(meta.contains("hidden: metadata unknown"))
        assertTrue(unknownNote(2).endsWith("hidden: metadata unknown"))
    }

    @Test
    fun `rating and year sort labels are still in the web sort source`() {
        val sort = read("../../web/js/lib/library-sort.js")
        for (key in listOf(LibrarySortKey.RATING, LibrarySortKey.YEAR)) {
            assertTrue(
                "Sort option ${key.name} drifted",
                Regex("key:\\s*SORT_KEY\\.${key.name},\\s*label:\\s*\"${Regex.escape(key.label)}\"").containsMatchIn(sort),
            )
        }
    }
}
