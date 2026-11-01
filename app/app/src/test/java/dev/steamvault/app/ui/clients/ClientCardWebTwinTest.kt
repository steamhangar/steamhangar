package dev.steamvault.app.ui.clients

import dev.steamvault.app.ui.clients.logic.AGENT_OUTDATED_PILL
import dev.steamvault.app.ui.clients.logic.BYPASS_HINT_SUMMARY
import dev.steamvault.app.ui.clients.logic.DEVICES_HEADING
import dev.steamvault.app.ui.clients.logic.IDLE_TEXT
import dev.steamvault.app.ui.clients.logic.NO_STATS_TEXT
import dev.steamvault.app.ui.clients.logic.OUTDATED_BOX_TITLE
import dev.steamvault.app.ui.clients.logic.RECENT_HEADING
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP ANDROID-REDESIGN-1a: the words of the rc20 Clients card
 * and tiles (web CLIENTS-1..4) must still be the web's. Each entry must
 * appear in the named web source as one double-quoted string (`quoted`) or
 * as a fragment of a template literal (`fragments`). The Kotlin side is
 * pinned by string equality in `ClientCardTest`; button labels are read
 * from `strings.xml` here. VALUE drift -> update the Kotlin port (and
 * strings.xml); GRAMMAR drift (the web changed the string form) -> adjust
 * this test. Never edit the web literal to make this pass.
 */
class ClientCardWebTwinTest {

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

    private val xml: String by lazy { File("src/main/res/values/strings.xml").readText(Charsets.UTF_8) }

    private fun res(name: String): String {
        val match = Regex("<string name=\"$name\"[^>]*>(.*?)</string>").find(xml) ?: error("no <string name=\"$name\"> found")
        return match.groupValues[1].replace("\\'", "'")
    }

    @Test
    fun `clients-page card and tile literals`() {
        assertQuoted(
            "lib/clients-page.js",
            listOf(
                "Downloading", "Online", "Offline", "No current data",
                IDLE_TEXT, "Content not linked to one game", "Loading into the cache",
                "Installed", "Loaded", "Hit rate", "Unlinked", "game", "games",
                OUTDATED_BOX_TITLE, NO_STATS_TEXT, "Nothing loaded",
                "Bypassing the cache", "Healthy",
                "Connected", "Nothing loading now", "from the cache", "No cache requests yet",
                "Agents", "online", "No agent paired",
            ),
        )
        assertFragments(
            "lib/clients-page.js",
            listOf(
                "`Agent \${info.agent} · server \${info.server}. Update vault-agent on this PC to match the server.`",
                "`\${n} outdated`",
                "`+\${n} more`",
                "`Nothing loaded \${phrase}`",
                "`\${text} not linked to a game`",
                "`\${loading} loading now`",
                "`of \${list.length}`",
                "`\${agents.length} paired · server version not known`",
                "`\${agents.length} paired · all match the server`",
                "`\${agents.length} paired`",
                "key: `agent:\${id}`,",
                "export const NOW_UNKNOWN_TEXT = `Loading now: \${DASH}`;",
                // Request-based hit rate, live_now first.
                "return hits + misses > 0 ? hits / (hits + misses) : null;",
                "if (client.live_now) return client.live_now;",
                "return isReleaseVersion(a) && isReleaseVersion(s) && a !== s;",
                // RELEASE-SPLIT-1b: the bundled-agent texts and rules
                // (ClientCard.kt `outdatedAgentText`, `agentVersionIssue`).
                "`Agent \${info.agent} is older than the minimum \${info.minimum}. Update vault-agent on this PC to match the server.`",
                "`Agent \${info.agent} · server's agent \${info.bundled}. Update vault-agent on this PC to match the server.`",
                "if (ref.same.includes(a)) return null;",
                "if (ref.minimum && compareReleaseVersions(a, ref.minimum) === -1) return \"minimum\";",
                "return \"bundled\";",
                "const chunks = (id) => id.match(/[0-9]+|[^0-9]+/g) || [];",
            ),
        )
        assertFragments(
            "lib/agent-install.js",
            listOf("const RELEASE_VERSION = /^[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z]+(?:\\.[0-9A-Za-z]+)*)?$/;"),
        )
    }

    @Test
    fun `agent health literals`() {
        assertQuoted("lib/clients-view.js", listOf("last seen: not reported", "version not reported", "game count not reported"))
        assertFragments(
            "lib/clients-view.js",
            listOf(
                "`last seen \${ago}`",
                "`agent \${v.trim()}`",
                "`\${games} · none of its downloads have reached the cache recently`",
                "`\${count} game\${count === 1 ? \"\" : \"s\"} reported`",
                "`\${lastSeenText(client, nowMs)} · \${agentVersionText(client)}`",
            ),
        )
    }

    @Test
    fun `card component and view literals`() {
        assertQuoted("components/client-card.js", listOf(AGENT_OUTDATED_PILL, RECENT_HEADING, BYPASS_HINT_SUMMARY, res("clients_remove_agent_button")))
        assertFragments("components/client-card.js", listOf("`Remove agent \${model.removeId}`"))
        assertEquals("Remove agent %1\$s", res("clients_remove_agent_button_cd"))
        assertQuoted("views/clients.js", listOf(DEVICES_HEADING))
        assertFragments("views/clients.js", listOf("const REMOVED_HIDE_MS = 30_000;"))
    }
}
