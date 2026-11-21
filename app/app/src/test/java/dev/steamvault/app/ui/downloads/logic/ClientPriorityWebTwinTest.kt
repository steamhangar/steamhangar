package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pins for WP CAP-503-FIX-1b: every literal of [ClientPriorityWording]
 * must still be one double-quoted string in `web/js/lib/client-priority.js`,
 * the poll cadence and the decisions that pick a text must match, and
 * `web/js/views/downloads.js` must still call each text where Android shows
 * it (a web string port can depend on the caller, ANDROID-VERIFY-SPEED-1).
 * VALUE drift -> update ClientPriority.kt; GRAMMAR drift (the web changed
 * the string form) -> adjust [normalised] here.
 */
class ClientPriorityWebTwinTest {
    private fun web(path: String): String {
        val f = File("../../web/js/$path")
        check(f.exists()) { "No file at ${f.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return f.readText(Charsets.UTF_8)
    }

    private val lib: String by lazy { web("lib/client-priority.js") }
    private val normalised: String by lazy { lib.replace(Regex("\"\\s*\\+\\s*\""), "").replace(Regex("\"\\s*\\n\\s*\""), "") }

    private val literals = listOf(
        ClientPriorityWording.HELD_SEEN,
        ClientPriorityWording.HELD_RESTORED,
        ClientPriorityWording.HELD_PENDING,
        ClientPriorityWording.PAUSING_CLIENT,
        ClientPriorityWording.OVERRIDE_SEEN,
        ClientPriorityWording.OVERRIDE_ANY,
        ClientPriorityWording.QUEUE_SEEN,
        ClientPriorityWording.QUEUE_RESTORED,
        ClientPriorityWording.QUEUE_PENDING,
    )

    @Test
    fun `the source is real`() {
        assertTrue("no 'export const WORDING' -- re-check path/anchors", lib.contains("export const WORDING"))
    }

    @Test
    fun `every wording literal is still in the web source`() {
        for (l in literals) {
            if (!normalised.contains("\"$l\"")) {
                fail(
                    if (normalised.contains(l)) {
                        "GRAMMAR DRIFT: web client-priority.js contains \"$l\" but not as one double-quoted string -- adjust this test."
                    } else {
                        "VALUE DRIFT: web client-priority.js no longer contains \"$l\" -- update ClientPriority.kt."
                    },
                )
            }
        }
    }

    @Test
    fun `the web has exactly these nine texts`() {
        val keys = Regex("""^\s{2}(\w+):""", RegexOption.MULTILINE)
            .findAll(lib.substringAfter("export const WORDING").substringBefore("});"))
            .map { it.groupValues[1] }.toList()
        assertTrue("web WORDING keys changed: $keys -- port the new text", keys.size == literals.size)
    }

    @Test
    fun `the poll cadence matches the web`() {
        assertTrue(
            "CLIENT_PRIORITY_POLL_MS drifted from web (expected $CLIENT_PRIORITY_POLL_MS)",
            Regex("CLIENT_PRIORITY_POLL_MS\\s*=\\s*${CLIENT_PRIORITY_POLL_MS}\\s*;").containsMatchIn(lib),
        )
    }

    @Test
    fun `the web picks each text by the same rules`() {
        val rules = listOf(
            // held: only a paused job with pause_reason client
            "if (!job || job.status !== \"paused\" || job.pause_reason !== \"client\") return null;",
            // restored never names a device
            "if (!isHolding(cp) || cp.restored === true || !Array.isArray(cp.clients)) return null;",
            "if (cp.restored === true) return fill(WORDING.heldRestored, { time });",
            "if (cp.restored === true) return fill(WORDING.queueRestored, { time });",
            // pausing: running + stop_request pause + client
            "if (!job || job.status !== \"running\" || job.stop_request !== \"pause\" || job.pause_reason !== \"client\") return null;",
            // override: only while holding, running or queued
            "if (!job || job.client_override !== true || !isHolding(cp)) return null;",
            "if (job.status !== \"running\" && job.status !== \"queued\") return null;",
            // queue: an override-only queue does not wait
            "if (!queued.some((j) => j && j.client_override !== true)) return null;",
            // device phrase and clock
            "return `\${names.slice(0, -1).join(\", \")} and \${names[names.length - 1]}`;",
            "return Number.isNaN(ms) ? null : formatClockHHMM(ms);",
        )
        for (r in rules) assertTrue("LOGIC DRIFT: web client-priority.js no longer has `$r` -- port the change to ClientPriority.kt", lib.contains(r))
    }

    @Test
    fun `the web view calls each text where the app shows it`() {
        val view = web("views/downloads.js")
        val calls = listOf(
            "clientHoldNote(job, state.clientPriority) || (job.type === \"gc\" ? GC_HOLD_NOTE : HOLD_NOTE)",
            "clientOverrideNote(job, state.clientPriority)",
            "clientPausingNote(job) || \"Pause requested",
            "queueHoldNote(queued, state.clientPriority)",
            "fetcher: async () => (hasActiveJob(state.jobs) ? api.clientPriority() : null),",
        )
        for (c in calls) assertTrue("CALL-SITE DRIFT: web downloads.js no longer has `$c` -- check the Android screen still matches", view.contains(c))
        assertTrue("web clock helper moved", web("lib/connection-watch.js").contains("return `\${pad(d.getHours())}:\${pad(d.getMinutes())}`;"))
    }
}
