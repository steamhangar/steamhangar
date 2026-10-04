package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.AboutComponentOut
import dev.steamvault.app.net.model.AboutOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP APP-FEAT-2 / WEB-FIX-8: Settings → About presentation of `GET /v1/about`. */
class AboutPresentationTest {

    private val full = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0"
    private val api = AboutComponentOut("vault-api", "0.1.0-rc9", full, "ok", null, "Also serves the web UI.")

    private fun core(
        version: String? = "0.1.0-rc9",
        commit: String? = full,
        status: String = "unknown",
    ) = AboutComponentOut("vault-core", version, commit, status, "2026-10-03T10:00:00Z", "Recorded at start.")

    @Test
    fun `MUTATION PIN -- status is passed through, an unknown word is UNKNOWN, never OK`() {
        assertEquals(AboutStatus.OK, aboutStatusFor("ok"))
        assertEquals(AboutStatus.UNREACHABLE, aboutStatusFor("unreachable"))
        assertEquals(AboutStatus.NOT_IN_USE, aboutStatusFor("not_in_use"))
        assertEquals(AboutStatus.UNKNOWN, aboutStatusFor("unknown"))
        assertEquals(AboutStatus.UNKNOWN, aboutStatusFor("degraded"))
        assertEquals(AboutStatus.UNKNOWN, aboutStatusFor(null))
    }

    @Test
    fun `server status without a component rule -- unknown reads NOT_CHECKED, never OK`() {
        assertEquals(AboutDisplayStatus.OK, aboutDisplayFor(AboutStatus.OK))
        assertEquals(AboutDisplayStatus.UNREACHABLE, aboutDisplayFor(AboutStatus.UNREACHABLE))
        assertEquals(AboutDisplayStatus.NOT_IN_USE, aboutDisplayFor(AboutStatus.NOT_IN_USE))
        assertEquals(AboutDisplayStatus.NOT_CHECKED, aboutDisplayFor(AboutStatus.UNKNOWN))
        val runner = aboutRowFor(AboutComponentOut("vault-runner", null, null, "degraded"), api)
        assertEquals(AboutDisplayStatus.NOT_CHECKED, runner.display)
    }

    @Test
    fun `MUTATION PIN -- the version cell is verbatim (web about-view versionCell), null or blank is the dash`() {
        assertEquals("dev", aboutVersionText("dev"))
        assertEquals("0.1.0", aboutVersionText("0.1.0"))
        assertEquals("invalid", aboutVersionText("invalid"))
        assertNull(aboutVersionText(null))
        assertNull(aboutVersionText("  "))
    }

    @Test
    fun `commit -- hex cut to 7, invalid and non-hex verbatim, null is the dash`() {
        assertEquals("4f1c2d3", aboutCommitText("4f1c2d3e5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d"))
        assertEquals("invalid", aboutCommitText("invalid"))
        assertEquals("abc", aboutCommitText("abc"))
        assertNull(aboutCommitText(null))
    }

    @Test
    fun `MUTATION PIN -- vault-core equal to vault-api in version AND commit is OK with the same-release note`() {
        val row = aboutRowFor(core(), api)
        assertEquals(AboutDisplayStatus.OK, row.display)
        assertEquals(AboutNote.CORE_SAME_RELEASE, row.note)
        assertEquals(AboutStatus.UNKNOWN, row.status)
        assertFalse(row.showDashNote)
        assertEquals("Recorded at start.", row.detail)
        assertEquals(AboutDisplayStatus.OK, aboutRowFor(core(commit = full.uppercase()), api).display)
    }

    @Test
    fun `MUTATION PIN -- vault-core differing in version OR commit is a neutral CHECK with the mismatch note`() {
        for (c in listOf(core(version = "0.1.0-rc8"), core(commit = "0".repeat(40)))) {
            val row = aboutRowFor(c, api)
            assertEquals(AboutDisplayStatus.CHECK, row.display)
            assertEquals(AboutNote.CORE_MISMATCH, row.note)
        }
    }

    @Test
    fun `MUTATION PIN -- vault-core never recorded is NOT_REPORTED, an incomparable one is CHECK, never OK`() {
        val missing = aboutRowFor(core(version = null, commit = null), api)
        assertEquals(AboutDisplayStatus.NOT_REPORTED, missing.display)
        assertEquals(AboutNote.CORE_NOT_REPORTED, missing.note)
        assertTrue(missing.showDashNote)
        val incomparable = listOf(
            core(version = "invalid", commit = null) to api,
            core(commit = null) to api,
            core() to api.copy(commit = null),
            core() to null,
            core(version = "dev", commit = null) to api.copy(version = "dev", commit = null),
        )
        for ((c, a) in incomparable) {
            assertEquals(CoreComparison.NOT_COMPARABLE, aboutCoreComparison(c, a))
            val row = aboutRowFor(c, a)
            assertEquals(AboutDisplayStatus.CHECK, row.display)
            assertEquals(AboutNote.CORE_NOT_COMPARABLE, row.note)
        }
    }

    @Test
    fun `vault-core with a status other than unknown is never overridden`() {
        assertEquals(AboutDisplayStatus.UNREACHABLE, aboutRowFor(core(status = "unreachable"), api).display)
        assertEquals(AboutNote.VAULT_CORE, aboutRowFor(core(status = "unreachable"), api).note)
    }

    @Test
    fun `MUTATION PIN -- vault-dns unknown is NOT_APPLICABLE with dashes and the dash note`() {
        val row = aboutRowFor(AboutComponentOut("vault-dns", null, null, "unknown"), api)
        assertEquals(AboutDisplayStatus.NOT_APPLICABLE, row.display)
        assertEquals(AboutNote.VAULT_DNS, row.note)
        assertNull(row.version)
        assertNull(row.commit)
        assertTrue(row.showDashNote)
    }

    @Test
    fun `MUTATION PIN -- vault-proxy keeps OK with dashes, its own note explains them`() {
        val row = aboutRowFor(AboutComponentOut("vault-proxy", null, null, "ok"), api)
        assertEquals(AboutDisplayStatus.OK, row.display)
        assertEquals(AboutNote.VAULT_PROXY, row.note)
        assertFalse(row.showDashNote)
        assertEquals(
            AboutDisplayStatus.NOT_CHECKED,
            aboutRowFor(AboutComponentOut("vault-proxy", null, null, "unknown"), api).display,
        )
    }

    @Test
    fun `steamprefill -- version, a dash for the commit, no extra dash note`() {
        val row = aboutRowFor(AboutComponentOut("steamprefill", "3.7.1", null, "ok"), api)
        assertEquals("3.7.1", row.version)
        assertNull(row.commit)
        assertEquals(AboutDisplayStatus.OK, row.display)
        assertFalse(row.showDashNote)
    }

    @Test
    fun `aboutRowsFor compares vault-core against vault-api from the same list`() {
        assertEquals(AboutDisplayStatus.OK, aboutRowsFor(listOf(api, core()))[1].display)
        assertEquals(AboutDisplayStatus.CHECK, aboutRowsFor(listOf(api.copy(version = "0.1.0"), core()))[1].display)
        assertEquals("no vault-api entry", AboutDisplayStatus.CHECK, aboutRowsFor(listOf(core()))[0].display)
    }

    @Test
    fun `MUTATION PIN -- 404 is server too old, anything else an error`() {
        assertEquals(AboutLoadFailure.TOO_OLD, classifyAboutError(VaultApiError.NotFound("x", 404, "Not Found")))
        assertEquals(AboutLoadFailure.ERROR, classifyAboutError(VaultApiError.Validation("x", 500, "boom")))
        assertEquals(AboutLoadFailure.ERROR, classifyAboutError(IllegalStateException("no connection")))
    }

    @Test
    fun `a row keeps the server's detail and maps the six known names`() {
        val row = aboutRowFor(AboutComponentOut("vault-core", "0.1.0", null, "unknown", "2026-10-03T10:00:00Z", "Recorded at start."))
        assertEquals(AboutComponentKind.VAULT_CORE, row.kind)
        assertEquals("0.1.0", row.version)
        assertNull(row.commit)
        assertEquals(AboutStatus.UNKNOWN, row.status)
        assertEquals("Recorded at start.", row.detail)
        assertTrue(row.hasInfo)
        assertEquals(
            listOf("vault-api", "vault-core", "vault-runner", "steamprefill", "vault-proxy", "vault-dns"),
            AboutComponentKind.entries.map { it.wireName },
        )
        val unnamed = aboutRowFor(AboutComponentOut("vault-new", "1", "abc", "ok"))
        assertNull(unnamed.kind)
        assertFalse("no note, no dash, no detail: nothing behind an (i)", unnamed.hasInfo)
    }

    @Test
    fun `the real wire shape decodes`() {
        val json = """{"components":[{"name":"vault-api","version":"0.1.0","commit":"4f1c2d3e5a6b","status":"ok",""" +
            """"checked_at":"2026-10-03T10:00:00Z","detail":"Also serves the web UI."},""" +
            """{"name":"vault-dns","version":null,"commit":null,"status":"unknown","checked_at":"2026-10-03T10:00:00Z","detail":null}]}"""
        val out = VaultJson.decodeFromString(AboutOut.serializer(), json)
        assertEquals(2, out.components.size)
        assertNull(out.components[1].version)
        assertEquals("ok", out.components[0].status)
    }
}
