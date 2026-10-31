package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.error.VaultApiError
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-GAPS-1: Remove a PC. Wording pinned by literal string equality
 * (the web side of each literal is pinned in `ClientGapsWebTwinTest`), and
 * the two MUTATION TARGETS of web `clients-view.js` by name.
 */
class ClientRemoveTest {

    @Test
    fun `MUTATION PIN -- isRemovableClientId refuses an id with a slash, an empty and a missing id`() {
        assertTrue(isRemovableClientId("gaming-pc"))
        assertTrue(isRemovableClientId("pc with space #1 ?&%+"))
        assertFalse(isRemovableClientId("lab/pc-1"))
        assertFalse(isRemovableClientId(""))
        assertFalse(isRemovableClientId(null))
    }

    @Test
    fun `MUTATION PIN -- only the handler's own 404 counts as already gone`() {
        assertTrue(isClientAlreadyGone(VaultApiError.NotFound("x", 404, "Unknown client_id 'gaming-pc'")))
        // A route that does not exist (server older than AG-1) is a real error.
        assertFalse(isClientAlreadyGone(VaultApiError.NotFound("x", 404, "Not Found")))
        assertFalse(isClientAlreadyGone(VaultApiError.NotFound("x", 404, null)))
        assertFalse(isClientAlreadyGone(VaultApiError.Server("x", 500, "Unknown client_id 'gaming-pc'")))
        assertFalse(isClientAlreadyGone(IllegalStateException("Unknown client_id")))
    }

    @Test
    fun `the confirm, note and error sentences are the web's`() {
        assertEquals("Remove gaming-pc?", removeConfirmTitle("gaming-pc"))
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
        assertEquals("gaming-pc removed from the list.", removedToastText("gaming-pc"))
        assertEquals("Could not remove gaming-pc: boom", removeErrorText("gaming-pc", "boom"))
        assertEquals(
            "Cannot be removed here: the name contains \"/\", which the server's remove request cannot address.",
            UNREMOVABLE_SLASH_NOTE,
        )
    }

    @Test
    fun `the error reason is the server's detail, else the message, else a fallback`() {
        assertEquals("database is locked", requestErrorReason(VaultApiError.Server("DELETE x failed (500)", 500, "database is locked")))
        assertEquals("DELETE x failed (500)", requestErrorReason(VaultApiError.Server("DELETE x failed (500)", 500, null)))
        assertEquals("Request failed.", requestErrorReason(IllegalStateException()))
    }
}
