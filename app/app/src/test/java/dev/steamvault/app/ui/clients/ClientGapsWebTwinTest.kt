package dev.steamvault.app.ui.clients

import dev.steamvault.app.ui.clients.logic.CLIENT_GONE_DETAIL_PREFIX
import dev.steamvault.app.ui.clients.logic.MAX_CLIENT_NAME_LENGTH
import dev.steamvault.app.ui.clients.logic.REMOVE_REREGISTER_TEXT
import dev.steamvault.app.ui.clients.logic.REMOVE_WHAT_TEXT
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_KEEPS
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_LEGEND
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_OPTIONS
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_TITLE
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_WHAT
import dev.steamvault.app.ui.downloads.logic.SCOPE_ALL
import dev.steamvault.app.ui.downloads.logic.SCOPE_OLDER
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP ANDROID-GAPS-1: the words of Remove PC, Rename, Delete
 * history and the onboarding SteamID64 must still be the web's. Each entry
 * must appear in the named web source as one double-quoted string
 * (`quoted`) or as a fragment of a template literal (`fragments`). The
 * Kotlin side is pinned by string equality in `ClientRemoveTest`,
 * `ClientNamesTest` and `HistoryDeleteTest`; button labels are read from
 * `strings.xml` here. VALUE drift -> update the Kotlin port (and
 * strings.xml); GRAMMAR drift (the web changed the string form) -> adjust
 * this test. Never edit the web literal to make this pass.
 */
class ClientGapsWebTwinTest {

    private fun web(path: String): String {
        val file = File("../../web/js/$path")
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return file.readText(Charsets.UTF_8)
    }

    private fun assertQuoted(path: String, literals: List<String>) {
        val js = web(path)
        for (l in literals) {
            if (!js.contains("\"$l\"")) {
                fail(
                    if (js.contains(l)) {
                        "GRAMMAR DRIFT: $path contains \"$l\" but not as one double-quoted string -- adjust this test."
                    } else {
                        "VALUE DRIFT: $path no longer contains \"$l\" -- update the Kotlin port."
                    },
                )
            }
        }
    }

    private fun assertFragments(path: String, fragments: List<String>) {
        val js = web(path)
        for (f in fragments) {
            if (!js.contains(f)) fail("VALUE DRIFT: $path no longer contains the fragment \"$f\" -- update the Kotlin port.")
        }
    }

    private val xml: String by lazy {
        val file = File("src/main/res/values/strings.xml")
        check(file.exists()) { "expected resource file at ${file.absolutePath}" }
        file.readText(Charsets.UTF_8)
    }

    private fun res(name: String): String {
        val match = Regex("<string name=\"$name\"[^>]*>(.*?)</string>").find(xml) ?: error("no <string name=\"$name\"> found")
        return match.groupValues[1].replace("\\'", "'")
    }

    @Test
    fun `Remove PC -- clients-view and clients-sheet literals`() {
        // REMOVE_WHAT_TEXT / REMOVE_REREGISTER_TEXT are two concatenated web strings each.
        assertQuoted(
            "lib/clients-view.js",
            listOf(
                "This deletes the reports this PC's agent sent (its installed-games lists) and its bypass status. ",
                "Cached games, downloads and cache statistics stay.",
                "If vault-agent still runs on this PC, its next report adds the PC back with a fresh history. ",
                "To retire the PC for good, stop or uninstall the agent there first.",
                CLIENT_GONE_DETAIL_PREFIX,
            ),
        )
        assertEquals(
            "This deletes the reports this PC's agent sent (its installed-games lists) and its bypass status. " +
                "Cached games, downloads and cache statistics stay.",
            REMOVE_WHAT_TEXT,
        )
        assertEquals(
            "If vault-agent still runs on this PC, its next report adds the PC back with a fresh history. " +
                "To retire the PC for good, stop or uninstall the agent there first.",
            REMOVE_REREGISTER_TEXT,
        )
        assertFragments(
            "lib/clients-view.js",
            listOf(
                "`Remove \${clientId}?`",
                "`\${clientId} removed from the list.`",
                "`Could not remove \${clientId}: \${reason}`",
                "'Cannot be removed here: the name contains \"/\", which the server\\'s remove request cannot address.'",
            ),
        )
        assertQuoted("components/clients-sheet.js", listOf(res("clients_remove_keep"), res("clients_remove_confirm"), res("clients_remove_busy"), "Request failed."))
        assertEquals("Remove", res("clients_remove_button"))
        assertFragments("components/clients-sheet.js", listOf("`Remove \${clientId}`"))
        assertEquals("Remove %1\$s", res("clients_remove_button_cd"))
    }

    @Test
    fun `Rename -- client-names and the Clients view's dialog literals`() {
        assertQuoted(
            "lib/client-names.js",
            listOf(
                "Enter a name.",
                "Remove line breaks, tabs and invisible characters.",
                "Use at least one letter, digit, symbol or punctuation mark.",
                "Name removed.",
                "No name stored",
                "This server cannot store client names yet. Update vault-api to rename clients.",
            ),
        )
        assertFragments(
            "lib/client-names.js",
            listOf(
                "export const MAX_CLIENT_NAME_LENGTH = $MAX_CLIENT_NAME_LENGTH;",
                "`Use at most \${MAX_CLIENT_NAME_LENGTH} characters.`",
                "`Rename \${isolate(displayName)}`",
                "`Stored for the address \${client.name_addr}: a machine that gets this address later shows this name too.`",
                "`\${base} vault-agent keeps reporting this PC as \${isolate(client.client_id)}; the name you set is shown instead.`",
                "`Renamed to \${isolate(name)}.`",
                "`Name removed. Now shown as \${isolate(displayName)}.`",
                "`Could not save the name: \${detail}`",
            ),
        )
        assertQuoted(
            "views/clients.js",
            listOf(
                res("clients_rename_label"),
                res("clients_rename_clear"),
                res("clients_rename_cancel"),
                res("clients_rename_save"),
                res("clients_rename_saving"),
                res("clients_rename_clearing"),
            ),
        )
    }

    @Test
    fun `Delete history -- history-delete and the Downloads dialog literals`() {
        assertQuoted(
            "lib/history-delete.js",
            listOf(SCOPE_OLDER, SCOPE_ALL, HISTORY_DELETE_TITLE, HISTORY_DELETE_WHAT, HISTORY_DELETE_KEEPS) +
                HISTORY_DELETE_OPTIONS.flatMap { listOf(it.label, it.desc) } +
                listOf(
                    "Delete everything",
                    "Delete older than 30 days",
                    "This server cannot delete history yet. Update vault-api to use this.",
                ),
        )
        assertFragments(
            "lib/history-delete.js",
            listOf(
                "`History deleted: \${jobs} and all traffic statistics.`",
                "`History older than 30 days deleted: \${jobs} and the traffic statistics from that time.`",
                "`\${n} finished download\${n === 1 ? \"\" : \"s\"}`",
                "`Could not delete history: \${detail}`",
            ),
        )
        assertQuoted(
            "views/downloads.js",
            listOf(
                HISTORY_DELETE_LEGEND,
                res("downloads_history_delete_keep"),
                res("downloads_history_delete_busy"),
                res("downloads_history_delete_button"),
                res("downloads_history_delete_button_cd"),
            ),
        )
    }

    @Test
    fun `onboarding SteamID64 -- onboarding literals`() {
        assertQuoted(
            "onboarding.js",
            listOf(
                res("onboarding_library_heading"),
                res("onboarding_library_id_label"),
                res("onboarding_library_id_hint"),
                res("onboarding_library_lookup"),
                "76561198042117903",
            ),
        )
        assertFragments(
            "onboarding.js",
            listOf(
                "`Saved \${steamid} — your library will show the games this SteamID64 owns.`",
                "`Not saved: \${reason} The lookup above still worked.`",
            ),
        )
        assertEquals("Saved %1\$s — your library will show the games this SteamID64 owns.", res("onboarding_library_saved"))
        assertEquals("Not saved: %1\$s The lookup above still worked.", res("onboarding_library_not_saved"))
    }
}
