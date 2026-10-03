package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pins for WP APP-FIX-2 (docs/LEARNINGS.md "twin config files need
 * twin pins"): `JobFailure.kt` is a verbatim port of
 * `web/js/lib/job-failure.js`, so every literal it restates must still
 * appear in the web source, and the two README anchors must still exist in
 * deploy/README.md (the web has the same README guards).
 *
 * The JS source is normalised only by joining `"..." + "..."`
 * concatenations; a JS string is matched with its quotes. Failure messages
 * name the edit: VALUE drift -> update JobFailure.kt to the new web text;
 * GRAMMAR drift (the web moved to another string form) -> adjust
 * [normalisedJs] here.
 */
class JobFailureWebTwinTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) {
            "No file at ${file.absolutePath}. If it was MOVED or RENAMED in the repo, update this path in " +
                "JobFailureWebTwinTest. If it was DELETED (the web twin no longer exists), retire this pin " +
                "deliberately together with the ported literals in JobFailure.kt -- do not just delete the test."
        }
        return file.readText(Charsets.UTF_8)
    }

    private val js: String by lazy { read("../../web/js/lib/job-failure.js") }
    private val normalisedJs: String by lazy { js.replace(Regex("\"\\s*\\+\\s*\""), "") }

    /**
     * VALUE drift: the text is gone from the web source in every string form
     * -> update JobFailure.kt to the new web text. GRAMMAR drift: the same
     * text is still there but no longer as one double-quoted string (single
     * quotes, a template literal, a different concatenation) -> adjust this
     * test's normalisation, not JobFailure.kt.
     */
    private fun assertInWeb(literal: String) {
        if (normalisedJs.contains("\"$literal\"")) return
        val otherForm = normalisedJs.contains("'$literal'") || normalisedJs.contains("`$literal`") ||
            normalisedJs.contains(literal)
        if (otherForm) {
            fail(
                "GRAMMAR DRIFT: web/js/lib/job-failure.js still contains \"$literal\" but not as one double-quoted " +
                    "string -- adjust THIS test's normalisation; do not touch JobFailure.kt for this failure.",
            )
        }
        fail(
            "VALUE DRIFT: web/js/lib/job-failure.js no longer contains \"$literal\" -- update the literal in " +
                "JobFailure.kt to the web's new text (and its string-equality pin in JobFailureTest).",
        )
    }

    @Test
    fun `the source is real (guards against a vacuous pass on a wrong path)`() {
        assertTrue(
            "GRAMMAR DRIFT or wrong file: job-failure.js has no 'export const HINTS' -- re-check the path/anchors here",
            js.contains("export const HINTS"),
        )
        assertTrue(
            "GRAMMAR DRIFT or wrong file: job-failure.js has no 'export function jobFailureHint' -- re-check here",
            js.contains("export function jobFailureHint"),
        )
    }

    @Test
    fun `every single-string literal of the port appears in the web source`() {
        assertInWeb(LOGIN_COMMAND)
        assertInWeb(PUBLIC_IP_README_SECTION)
        assertInWeb(NEWER_JOB_LINE)
        assertInWeb(RETRY_LINE)
        assertInWeb(OUTPUT_SUMMARY)
        for (hint in FailureHint.entries) {
            val text = FAILURE_HINTS.getValue(hint)
            assertInWeb(text.title)
            assertInWeb(text.body)
            assertInWeb(text.codeIntro)
            assertInWeb(hint.wireName)
        }
        assertInWeb(FAILURE_HINTS.getValue(FailureHint.NOT_LOGGED_IN).after)
    }

    @Test
    fun `the reason words and the narrow public-IP phrase are the web's`() {
        assertInWeb(FailureReason.NOT_LOGGED_IN)
        assertInWeb(FailureReason.EXIT_CODE)
        assertTrue(
            "VALUE DRIFT: web PUBLIC_IP_MARKERS no longer is /is resolving to a public IP/ -- update PUBLIC_IP_MARKER " +
                "in JobFailure.kt (the narrow-phrase rule, LEARNINGS WP WEB-FIX-4).",
            js.contains("/is resolving to a public IP/"),
        )
        assertTrue(
            "VALUE DRIFT: web REASON_LINE regex changed -- update REASON_LINE in JobFailure.kt to the same pattern.",
            js.contains("""/^\[vault-api\] Prefill failed \(reason=([a-z_]+)\)/"""),
        )
    }

    @Test
    fun `the public-IP code block and README pointer match the web (code is a single-quoted JS string)`() {
        val text = FAILURE_HINTS.getValue(FailureHint.PUBLIC_IP)
        val escapedCode = "'" + text.code.replace("\n", "\\n") + "'"
        if (!normalisedJs.contains(escapedCode)) {
            val grammar = text.code.lines().all { line -> line.isBlank() || js.contains(line.trim()) }
            fail(
                if (grammar) {
                    "GRAMMAR DRIFT: every line of the public-IP code block is still in job-failure.js, but not as one " +
                        "single-quoted string with \\n escapes -- adjust THIS test's escaping, not JobFailure.kt."
                } else {
                    "VALUE DRIFT: the public-IP code block differs from web HINTS.public_ip.code -- update " +
                        "FAILURE_HINTS in JobFailure.kt."
                },
            )
        }
        assertTrue(
            "VALUE DRIFT: the web public-IP 'after' text no longer contains the DNS-rewrite sentence -- update " +
                "FAILURE_HINTS in JobFailure.kt.",
            js.contains("\"Or set up a DNS rewrite for lancache.steamcontent.com on the resolver the container uses. \""),
        )
        assertTrue(
            "GRAMMAR or VALUE DRIFT: the web no longer builds the README pointer as a template literal around " +
                "PUBLIC_IP_README_SECTION -- re-check the 'after' text in JobFailure.kt, then this expectation.",
            js.contains("`See deploy/README.md, “\${PUBLIC_IP_README_SECTION}”.`"),
        )
        assertTrue(
            "the Kotlin 'after' text must start with the web's DNS-rewrite sentence -- fix JobFailure.kt.",
            text.after.startsWith("Or set up a DNS rewrite for lancache.steamcontent.com on the resolver the container uses. "),
        )
    }

    @Test
    fun `DRIFT GUARD -- deploy README still documents the login command and the public-IP heading`() {
        val readme = read("../../deploy/README.md")
        val joined = readme.replace(Regex("\\\\\\r?\\n\\s*"), " ").replace(Regex("[ \\t]+"), " ")
        assertTrue("deploy/README.md no longer contains: $LOGIN_COMMAND", joined.contains(LOGIN_COMMAND))
        val headings = readme.lines()
            .filter { Regex("^#{1,6} ").containsMatchIn(it) }
            .map { it.replace(Regex("^#{1,6} "), "").replace("`", "").trim() }
        assertTrue("no heading \"$PUBLIC_IP_README_SECTION\" in deploy/README.md", PUBLIC_IP_README_SECTION in headings)
    }
}
