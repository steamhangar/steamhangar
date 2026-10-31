package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.error.VaultApiError

/**
 * "Remove" a PC from the PCs sheet (WP ANDROID-GAPS-1) -- a Kotlin port of
 * the "Remove a PC" block of `web/js/lib/clients-view.js` (WP WEB-FEAT-4).
 * Pure: `ClientsController` runs the request, `ClientsSheet.kt` draws.
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * sentence below is the web module's own, pinned by string equality in
 * `ClientRemoveTest` and against the web source in `ClientGapsWebTwinTest`.
 *
 * What the server does (`DELETE /v1/clients/{client_id}`, WP AG-1): it
 * removes the stored reports of that id and its bypass verdict, nothing
 * else (cached games, jobs, mapping and the per-address cache statistics
 * stay). It is not a ban: a still-running agent's next report lists the PC
 * again. The confirm text says exactly that.
 */

/**
 * Whether the server's remove route can address [clientId] at all (web
 * `isRemovableClientId`): a client_id containing "/" is a valid report key,
 * but the server decodes `%2F` before routing, so the request never reaches
 * the handler and answers a generic 404 while the PC stays listed. Such a
 * row gets [UNREMOVABLE_SLASH_NOTE] instead of the button. MUTATION TARGET.
 */
fun isRemovableClientId(clientId: String?): Boolean =
    !clientId.isNullOrEmpty() && !clientId.contains("/")

/** Start of the 404 detail the remove handler itself raises
 * (`f"Unknown client_id {client_id!r}"`, routers/clients.py). */
const val CLIENT_GONE_DETAIL_PREFIX = "Unknown client_id"

/**
 * Whether a failed remove means "this PC is already gone": a 404 whose
 * detail is the handler's own text -- the same end state as a 204. Any
 * other 404 (a server older than AG-1, a proxy's page) is a real error.
 * MUTATION TARGET.
 */
fun isClientAlreadyGone(error: Throwable): Boolean =
    error is VaultApiError &&
        error.status == 404 &&
        error.detail?.startsWith(CLIENT_GONE_DETAIL_PREFIX) == true

/** Shown on a row whose id contains "/" instead of the Remove button. */
const val UNREMOVABLE_SLASH_NOTE =
    "Cannot be removed here: the name contains \"/\", which the server's remove request cannot address."

/** Confirm dialog title for one PC. */
fun removeConfirmTitle(clientId: String): String = "Remove $clientId?"

/** Confirm dialog: what is deleted (the endpoint's two tables) and what stays. */
const val REMOVE_WHAT_TEXT =
    "This deletes the reports this PC's agent sent (its installed-games lists) and its bypass status. " +
        "Cached games, downloads and cache statistics stay."

/** Confirm dialog: the "not a ban" consequence, as the server behaves. */
const val REMOVE_REREGISTER_TEXT =
    "If vault-agent still runs on this PC, its next report adds the PC back with a fresh history. " +
        "To retire the PC for good, stop or uninstall the agent there first."

/** Note after a successful remove (204, or 404 = already gone). */
fun removedToastText(clientId: String): String = "$clientId removed from the list."

/** Inline error line on the row when the remove failed. */
fun removeErrorText(clientId: String, reason: String): String = "Could not remove $clientId: $reason"

/** The reason part of [removeErrorText] (web `clients-sheet.js::errorText`):
 * the server's detail, else the error's message. */
fun requestErrorReason(error: Throwable): String {
    val detail = (error as? VaultApiError)?.detail
    if (!detail.isNullOrEmpty()) return detail
    return error.message?.takeIf { it.isNotEmpty() } ?: "Request failed."
}
