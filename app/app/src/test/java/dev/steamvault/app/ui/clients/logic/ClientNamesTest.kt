package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientActivityOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-GAPS-1: client names (Rename on the Clients page), port of web
 * `client-names.js`. Validation mirrors the server's rule; wording is
 * pinned by literal equality (web side: `ClientGapsWebTwinTest`).
 */
class ClientNamesTest {

    private fun ok(raw: String) = (normalizeClientName(raw) as ClientNameCheck.Ok).value
    private fun err(raw: String?) = (normalizeClientName(raw) as ClientNameCheck.Invalid).error

    @Test
    fun `MUTATION PIN -- normalizeClientName trims and accepts 1 to 40 code points`() {
        assertEquals("Steam Deck", ok("  Steam Deck  "))
        assertEquals("a".repeat(40), ok("a".repeat(40)))
        assertEquals("Use at most 40 characters.", err("a".repeat(41)))
        // 40 astral characters are 80 UTF-16 units but 40 code points: accepted.
        val astral = "🎮".repeat(40)
        assertEquals(astral, ok(astral))
        assertEquals("Enter a name.", err("   "))
        assertEquals("Enter a name.", err(null))
    }

    @Test
    fun `MUTATION PIN -- control, format and separator characters are refused`() {
        val invisible = "Remove line breaks, tabs and invisible characters."
        assertEquals(invisible, err("Living\tRoom"))
        assertEquals(invisible, err("Living​Room")) // zero-width space (Cf)
        assertEquals(invisible, err("Living‮Room")) // bidi override (Cf)
        assertEquals(invisible, err("Living Room")) // line separator (Zl)
        assertEquals(invisible, err("LivingRoom")) // private use (Co)
    }

    @Test
    fun `MUTATION PIN -- a name needs one visible character, combining marks alone are refused`() {
        assertEquals("Use at least one letter, digit, symbol or punctuation mark.", err("́̂"))
        assertEquals("#", ok("#"))
        assertEquals("7", ok("7"))
    }

    private fun client(kind: String = "agent", nameAddr: String? = "10.0.0.42", name: String? = null, clientId: String? = "gaming-pc") =
        ClientActivityOut(key = "agent:x", kind = kind, client_id = clientId, name = name, name_addr = nameAddr)

    @Test
    fun `canRename needs a name_addr, hasCustomName needs a name`() {
        assertTrue(canRename(client()))
        assertFalse(canRename(client(kind = "runner", nameAddr = null)))
        assertFalse(canRename(client(nameAddr = "")))
        assertFalse(canRename(null))
        assertTrue(hasCustomName(client(name = "Desk")))
        assertFalse(hasCustomName(client(name = "")))
    }

    @Test
    fun `wording is the web's, operator text isolated`() {
        assertEquals("Rename ⁨Desk⁩", renameTitle("Desk"))
        assertEquals("Renamed to ⁨Desk⁩.", renamedToastText("Desk"))
        assertEquals("Name removed. Now shown as ⁨gaming-pc⁩.", nameRemovedToastText("gaming-pc"))
        assertEquals("Name removed.", nameRemovedToastText(null))
        assertEquals(
            "Stored for the address 10.0.0.42: a machine that gets this address later shows this name too. " +
                "vault-agent keeps reporting this PC as ⁨gaming-pc⁩; the name you set is shown instead.",
            renameHint(client()),
        )
        assertEquals(
            "Stored for the address 10.0.0.61: a machine that gets this address later shows this name too.",
            renameHint(client(kind = "address", nameAddr = "10.0.0.61", clientId = null)),
        )
    }

    @Test
    fun `MUTATION PIN -- a foreign 404 means an old server, the handler's own 404 means already cleared`() {
        val gone = VaultApiError.NotFound("x", 404, "No name stored for '10.0.0.42'")
        val noRoute = VaultApiError.NotFound("x", 404, "Not Found")
        assertTrue(isNameAlreadyGone(gone))
        assertFalse(isNameAlreadyGone(noRoute))
        assertEquals("This server cannot store client names yet. Update vault-api to rename clients.", renameErrorText(noRoute))
        assertEquals(
            "Could not save the name: Name must be 1-40 characters",
            renameErrorText(VaultApiError.Validation("x", 422, "Name must be 1-40 characters")),
        )
    }
}
