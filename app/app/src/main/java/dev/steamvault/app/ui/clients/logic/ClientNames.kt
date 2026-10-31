package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientActivityOut

/**
 * Client names (WP ANDROID-GAPS-1) -- a Kotlin port of
 * `web/js/lib/client-names.js` (WP D15-NAMES): validation and wording for
 * the Clients page's Rename action. Pure: `ClientRenameDialog.kt` draws,
 * `ClientRenameFlow` runs the requests.
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * sentence is the web module's own, pinned by string equality in
 * `ClientNamesTest` and against the web source in `ClientGapsWebTwinTest`.
 *
 * The server owns the rule (api/vault_api/client_names.py): a name is
 * stored per ADDRESS (`PUT /v1/client-names/ip/{addr}`); every overview row
 * carries `name` (operator-set or null), `name_addr` (where a rename
 * writes, null = cannot be named) and `display_name`. [normalizeClientName]
 * mirrors the server's validation so the dialog can say what is wrong
 * before a round trip; a 422 from the server is still shown as is.
 */

/** Longest name in code points after trimming (`client_names.MAX_NAME_LENGTH`). */
const val MAX_CLIENT_NAME_LENGTH = 40

/** [normalizeClientName]'s answer. */
sealed class ClientNameCheck {
    data class Ok(val value: String) : ClientNameCheck()
    data class Invalid(val error: String) : ClientNameCheck()
}

// Control (Cc), format (Cf: zero-width, bidi), surrogate (Cs), private use
// (Co), unassigned (Cn), line (Zl) and paragraph (Zp) separators: the
// server's `_FORBIDDEN_CATEGORIES` (web: the FORBIDDEN regex).
private val FORBIDDEN_TYPES: Set<Int> = setOf(
    Character.CONTROL.toInt(),
    Character.FORMAT.toInt(),
    Character.SURROGATE.toInt(),
    Character.PRIVATE_USE.toInt(),
    Character.UNASSIGNED.toInt(),
    Character.LINE_SEPARATOR.toInt(),
    Character.PARAGRAPH_SEPARATOR.toInt(),
)

// Letter, Number, Symbol or Punctuation: the server's `_VISIBLE_MAJOR`
// (a name of combining marks alone renders as nothing).
private val VISIBLE_TYPES: Set<Int> = setOf(
    Character.UPPERCASE_LETTER, Character.LOWERCASE_LETTER, Character.TITLECASE_LETTER,
    Character.MODIFIER_LETTER, Character.OTHER_LETTER,
    Character.DECIMAL_DIGIT_NUMBER, Character.LETTER_NUMBER, Character.OTHER_NUMBER,
    Character.MATH_SYMBOL, Character.CURRENCY_SYMBOL, Character.MODIFIER_SYMBOL, Character.OTHER_SYMBOL,
    Character.CONNECTOR_PUNCTUATION, Character.DASH_PUNCTUATION, Character.START_PUNCTUATION,
    Character.END_PUNCTUATION, Character.INITIAL_QUOTE_PUNCTUATION, Character.FINAL_QUOTE_PUNCTUATION,
    Character.OTHER_PUNCTUATION,
).map { it.toInt() }.toSet()

private fun String.codePointTypes(): List<Int> {
    val types = ArrayList<Int>(length)
    var i = 0
    while (i < length) {
        val cp = codePointAt(i)
        types.add(Character.getType(cp))
        i += Character.charCount(cp)
    }
    return types
}

/** Web `normalizeClientName`: trim, then 1-40 code points, nothing
 * invisible, at least one visible character. */
fun normalizeClientName(raw: String?): ClientNameCheck {
    if (raw == null) return ClientNameCheck.Invalid("Enter a name.")
    val value = raw.trim()
    if (value.isEmpty()) return ClientNameCheck.Invalid("Enter a name.")
    if (value.codePointCount(0, value.length) > MAX_CLIENT_NAME_LENGTH) {
        return ClientNameCheck.Invalid("Use at most $MAX_CLIENT_NAME_LENGTH characters.")
    }
    val types = value.codePointTypes()
    if (types.any { it in FORBIDDEN_TYPES }) return ClientNameCheck.Invalid("Remove line breaks, tabs and invisible characters.")
    if (types.none { it in VISIBLE_TYPES }) {
        return ClientNameCheck.Invalid("Use at least one letter, digit, symbol or punctuation mark.")
    }
    return ClientNameCheck.Ok(value)
}

/** True when the server gave this client an address to store a name under
 * (the prefill runner and an unaddressed agent have none). */
fun canRename(client: ClientActivityOut?): Boolean = client != null && !client.name_addr.isNullOrEmpty()

/** True when the client has an operator-set name. */
fun hasCustomName(client: ClientActivityOut?): Boolean = client != null && !client.name.isNullOrEmpty()

/** Dialog title. */
fun renameTitle(displayName: String): String = "Rename ${isolate(displayName)}"

/** What the name is stored for, under the field. */
fun renameHint(client: ClientActivityOut): String {
    val base = "Stored for the address ${client.name_addr}: a machine that gets this address later shows this name too."
    if (client.kind == "agent" && !client.client_id.isNullOrEmpty()) {
        return "$base vault-agent keeps reporting this PC as ${isolate(client.client_id)}; the name you set is shown instead."
    }
    return base
}

/** Note after a successful save. */
fun renamedToastText(name: String): String = "Renamed to ${isolate(name)}."

/** Note after "Remove name": [displayName] is the client's `display_name`
 * in the server's next answer; `null` when that answer is not available. */
fun nameRemovedToastText(displayName: String?): String =
    if (!displayName.isNullOrEmpty()) "Name removed. Now shown as ${isolate(displayName)}." else "Name removed."

/** The server's own 404 for "no name stored": clearing already happened. */
fun isNameAlreadyGone(error: Throwable): Boolean =
    error is VaultApiError.NotFound && error.detail?.startsWith("No name stored") == true

/** Error line for a failed save or remove. A 404 that is not the handler's
 * own answer means the route does not exist: a server older than
 * D15-NAMES. */
fun renameErrorText(error: Throwable): String {
    if (error is VaultApiError.NotFound && !isNameAlreadyGone(error)) {
        return "This server cannot store client names yet. Update vault-api to rename clients."
    }
    return "Could not save the name: ${requestErrorReason(error)}"
}
