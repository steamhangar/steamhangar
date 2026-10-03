package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.AboutComponentOut
import dev.steamvault.app.net.model.AboutOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** WP APP-FEAT-2: Settings → About presentation of `GET /v1/about`. */
class AboutPresentationTest {

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
    fun `MUTATION PIN -- the version cell is verbatim (web about-view versionCell), null or blank is unknown`() {
        assertEquals("dev", aboutVersionText("dev"))
        assertEquals("0.1.0", aboutVersionText("0.1.0"))
        assertEquals("invalid", aboutVersionText("invalid"))
        assertNull(aboutVersionText(null))
        assertNull(aboutVersionText("  "))
    }

    @Test
    fun `commit -- hex cut to 7, invalid and non-hex verbatim, null unknown`() {
        assertEquals("4f1c2d3", aboutCommitText("4f1c2d3e5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d"))
        assertEquals("invalid", aboutCommitText("invalid"))
        assertEquals("abc", aboutCommitText("abc"))
        assertNull(aboutCommitText(null))
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
        assertEquals(
            listOf("vault-api", "vault-core", "vault-runner", "steamprefill", "vault-proxy", "vault-dns"),
            AboutComponentKind.entries.map { it.wireName },
        )
        assertNull(aboutRowFor(AboutComponentOut("vault-new", null, null, "ok")).kind)
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
