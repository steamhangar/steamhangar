package dev.steamvault.app.ui.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * ANDROID-INSTALLED-WORDING-1 (audit finding D-13): ONE wording for "installed
 * on a PC, but nothing in the vault cache", on both frontends.
 *
 *  - Card / list badge: web `installedBadgeText` (lib/game-status.js) reads
 *    "Installed, not cached · <client>", the same words as the Library chip;
 *    Android `library_installed_not_cached` is the same template with
 *    `%1$s` for the client.
 *  - Detail note: web `buildInstalledNote` (components/game-detail-sheet.js)
 *    is a bytes-only sentence; the web dropped "the vault is not protecting
 *    it" on purpose (that function's header). Android
 *    `detail_installed_not_cached_warning` is the same sentence.
 *
 * Literal expected values on both sides, never one side derived from the
 * other (docs/learnings/android.md, "Cross-frontend contracts ... LITERAL").
 * A wording change on either frontend fails here until the other follows.
 */
class InstalledNotCachedWebTwinTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path." }
        return file.readText(Charsets.UTF_8)
    }

    private fun androidString(name: String): String {
        val strings = read("src/main/res/values/strings.xml")
        val match = Regex("<string name=\"$name\">(.*?)</string>").find(strings)
        check(match != null) { "expected <string name=\"$name\"> in strings.xml" }
        return match.groupValues[1]
    }

    @Test
    fun `MUTATION TARGET -- the badge reads like the web badge`() {
        assertTrue(
            "VALUE DRIFT: web lib/game-status.js installedBadgeText no longer returns $WEB_BADGE_LINE",
            read("../../web/js/lib/game-status.js").contains(WEB_BADGE_LINE),
        )
        assertEquals(
            "library_installed_not_cached must be the web badge with %1\$s for the client",
            "Installed, not cached · %1\$s",
            androidString("library_installed_not_cached"),
        )
    }

    @Test
    fun `MUTATION TARGET -- the detail note reads like the web note`() {
        assertTrue(
            "VALUE DRIFT: web components/game-detail-sheet.js buildInstalledNote no longer sets $WEB_NOTE_LINE",
            read("../../web/js/components/game-detail-sheet.js").contains(WEB_NOTE_LINE),
        )
        assertEquals(
            "detail_installed_not_cached_warning must be the web detail note, character for character",
            NOTE_TEXT,
            androidString("detail_installed_not_cached_warning"),
        )
    }

    @Test
    fun `neither Android string says the vault is not protecting the game`() {
        for (name in listOf("library_installed_not_cached", "detail_installed_not_cached_warning")) {
            assertFalse(
                "$name uses the status-based word the web removed on purpose",
                Regex("protect", RegexOption.IGNORE_CASE).containsMatchIn(androidString(name)),
            )
        }
    }

    private companion object {
        const val NOTE_TEXT = "Installed, not cached — no cached bytes for this game right now."
        const val WEB_BADGE_LINE = "if (state === INSTALLED_BADGE.NOT_CACHED) return `Installed, not cached · \${summary}`;"
        val WEB_NOTE_LINE = "note.textContent = \"$NOTE_TEXT\";"
    }
}
