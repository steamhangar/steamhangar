package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP APP-FIX-2: port of `web/tests/job-failure.test.js`'s cases for
 * `JobFailure.kt` (reason line, hint detection, newest-job rule) plus the
 * literal pins app/README.md's verbatim-port exception requires. Fixtures
 * are the web test's own excerpts (modeled on api/vault_api/worker.py).
 */
class JobFailureTest {

    private val failed = JobSummary(id = 7, appid = 20, type = "prefill", status = "error", created_at = "2026-10-03T00:00:00Z")

    private val notLoggedInExcerpt = listOf(
        "[...truncated...]",
        "   at SteamPrefill.Handlers.Steam.Steam3Session.LoginAsync() in /src/Steam3Session.cs:line 101",
        "Unhandled exception. System.InvalidOperationException: Failed to read input in non-interactive mode.",
        "[vault-api] SteamPrefill has no usable Steam session, and vault-api runs it non-interactively ...",
        "[vault-api] Prefill failed (reason=not_logged_in); the depot mapping for this app was left unchanged.",
    ).joinToString("\n")

    private val publicIpOutput = listOf(
        " Warning!  lancache.steamcontent.com is resolving to a public IP address",
        "(162.254.197.25).",
        "LancacheNotFoundException: Lancache server is resolving to a public IP : 162.254.197.25",
        "[vault-api] Prefill failed (reason=exit_code); the depot mapping for this app was left unchanged.",
    ).joinToString("\n")

    /** api/tests/test_api_fix_3_prefill_failed.py's EXPECTED_LAST_LINE_B, via the web test. */
    private val prefillFailedLine =
        "[vault-api] Prefill failed (reason=prefill_failed): SteamPrefill reported 1 app(s) as failed: " +
            "it could not download the depot manifests; a possible cause is HTTPS to *.steamcontent.com being " +
            "rewritten to a cache that does not pass port 443 through. The depot mapping and manifest state for " +
            "this app were left unchanged."

    // ---- reason ------------------------------------------------------------

    @Test
    fun `not_logged_in is read from vault-api's last line, CRLF and trailing blank lines tolerated`() {
        assertEquals("not_logged_in", jobFailureReason(failed, notLoggedInExcerpt))
        assertEquals("not_logged_in", jobFailureReason(failed, notLoggedInExcerpt + "\n\n  \n"))
        assertEquals("not_logged_in", jobFailureReason(failed, notLoggedInExcerpt.replace("\n", "\r\n")))
    }

    @Test
    fun `MUTATION PIN -- only the LAST non-empty line counts, and only at line start`() {
        assertNull(jobFailureReason(failed, "[vault-api] Prefill failed (reason=not_logged_in); x\nsomething printed later"))
        assertNull(jobFailureReason(failed, "echo [vault-api] Prefill failed (reason=not_logged_in)"))
        val two = "[vault-api] Prefill failed (reason=timeout); x\n[vault-api] Prefill failed (reason=not_logged_in); y"
        assertEquals("not_logged_in", jobFailureReason(failed, two))
    }

    @Test
    fun `MUTATION PIN -- only a failed PREFILL job has a reason`() {
        assertNull(jobFailureReason(failed.copy(status = "done"), notLoggedInExcerpt))
        assertNull(jobFailureReason(failed.copy(status = "cancelled"), notLoggedInExcerpt))
        assertNull(jobFailureReason(failed.copy(type = "gc"), notLoggedInExcerpt))
        assertNull(jobFailureReason(failed, null))
        assertNull(jobFailureReason(null, notLoggedInExcerpt))
    }

    // ---- hint ----------------------------------------------------------------

    @Test
    fun `not_logged_in gets the login hint`() {
        assertEquals(FailureHint.NOT_LOGGED_IN, jobFailureHint(failed, notLoggedInExcerpt))
    }

    @Test
    fun `MUTATION PIN -- the public-IP hint needs reason=exit_code AND the narrow phrase`() {
        assertEquals(FailureHint.PUBLIC_IP, jobFailureHint(failed, publicIpOutput))
        // The exception type alone is NOT the cause: SteamPrefill also throws
        // it for "Unable to detect Lancache server!" (LEARNINGS WP WEB-FIX-4).
        val heartbeat = "LancacheNotFoundException: Unable to detect Lancache server!\n" +
            "[vault-api] Prefill failed (reason=exit_code); the depot mapping for this app was left unchanged."
        assertNull(jobFailureHint(failed, heartbeat))
        // The phrase on a different reason is not this hint either.
        assertNull(jobFailureHint(failed, publicIpOutput.replace("reason=exit_code", "reason=timeout")))
    }

    @Test
    fun `MUTATION PIN -- prefill_failed is recognised but gets NO hint block (the cause is the output's last line)`() {
        val excerpt = "[8:07:55 PM] Unexpected download error : Unable to download manifests!  Skipping app...\n$prefillFailedLine"
        assertEquals(FailureReason.PREFILL_FAILED, jobFailureReason(failed, excerpt))
        assertNull(jobFailureHint(failed, excerpt))
        assertNull(failureHintViewFor(failed, excerpt, listOf(failed)))
    }

    // ---- Retry only on the newest prefill job for the app ---------------------

    @Test
    fun `MUTATION PIN -- Retry is offered on the newest prefill job only, a later GC job does not count`() {
        val newerPrefill = failed.copy(id = 9, status = "done")
        val laterGc = failed.copy(id = 10, type = "gc", status = "done")
        val otherApp = failed.copy(id = 11, appid = 21)
        assertTrue(isNewestPrefillJobForApp(failed, listOf(failed, laterGc, otherApp)))
        assertFalse(isNewestPrefillJobForApp(failed, listOf(failed, newerPrefill)))
        assertFalse(isNewestPrefillJobForApp(null, listOf(failed)))

        assertTrue(failureHintViewFor(failed, notLoggedInExcerpt, listOf(failed, laterGc))!!.retryOffered)
        assertFalse(failureHintViewFor(failed, notLoggedInExcerpt, listOf(failed, newerPrefill))!!.retryOffered)
    }

    // ---- literal pins (app/README.md verbatim-port exception) --------------------

    @Test
    fun `hint texts are the web literals, by string equality`() {
        assertEquals(
            "docker compose exec -it vault-runner /opt/steamprefill/SteamPrefill select-apps",
            LOGIN_COMMAND,
        )
        assertEquals("Fix it (only needed with a dedicated VAULT_CORE_BIND)", PUBLIC_IP_README_SECTION)
        assertEquals("A newer job for this game exists (see above).", NEWER_JOB_LINE)
        val login = FAILURE_HINTS.getValue(FailureHint.NOT_LOGGED_IN)
        assertEquals("Steam login missing", login.title)
        assertEquals(LOGIN_COMMAND, login.code)
        assertEquals("Then press Retry.", login.retry)
        assertEquals("Show the full SteamPrefill output", login.outputSummary)
        assertTrue(login.body.contains("never sees or stores your Steam credentials"))
        val publicIp = FAILURE_HINTS.getValue(FailureHint.PUBLIC_IP)
        assertEquals("The prefill cannot find the cache", publicIp.title)
        assertTrue(publicIp.after.endsWith("See deploy/README.md, “$PUBLIC_IP_README_SECTION”."))
        assertTrue(publicIp.code.contains("lancache.steamcontent.com:<vault-core private IPv4>"))
        assertEquals(setOf("not_logged_in", "public_ip"), FailureHint.entries.map { it.wireName }.toSet())
    }

    // ---- titles (WP APP-FIX-2): vault name, then owned name, then "App N" ----

    @Test
    fun `MUTATION PIN -- a job title falls back to the owned name before App N`() {
        val unnamed = mapOf(20 to GameSummary(appid = 20, name = null, status = "error", depot_count = 0))
        assertEquals("Steam Twenty", nameFor(20, unnamed, mapOf(20 to "Steam Twenty")))
        assertEquals("App 20", nameFor(20, unnamed, emptyMap()))
        val named = mapOf(20 to GameSummary(appid = 20, name = "Vault Twenty", status = "done", depot_count = 1))
        assertEquals("Vault Twenty", nameFor(20, named, mapOf(20 to "Steam Twenty")))
        assertEquals("Steam Twenty", buildHistoryRowModel(failed, unnamed, mapOf(20 to "Steam Twenty")).name)
        assertEquals("Steam Twenty", buildQueueRowModel(failed, 1, unnamed, mapOf(20 to "Steam Twenty")).name)
    }
}
