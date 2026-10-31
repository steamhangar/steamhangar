package dev.steamvault.app.ui.clients

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.error
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.repo.ClientsRepository
import dev.steamvault.app.ui.clients.logic.ClientNameCheck
import dev.steamvault.app.ui.clients.logic.canRename
import dev.steamvault.app.ui.clients.logic.clientName
import dev.steamvault.app.ui.clients.logic.hasCustomName
import dev.steamvault.app.ui.clients.logic.isNameAlreadyGone
import dev.steamvault.app.ui.clients.logic.nameRemovedToastText
import dev.steamvault.app.ui.clients.logic.normalizeClientName
import dev.steamvault.app.ui.clients.logic.renameErrorText
import dev.steamvault.app.ui.clients.logic.renameHint
import dev.steamvault.app.ui.clients.logic.renameTitle
import dev.steamvault.app.ui.clients.logic.renamedToastText
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** Which request of the rename dialog is running. */
enum class RenameBusy { SAVE, CLEAR }

/**
 * The Clients page's Rename flow (WP ANDROID-GAPS-1, twin of web
 * `views/clients.js`'s rename dialog, WP D15-NAMES). State plus suspend
 * orchestration only; every decision and sentence is
 * `ui/clients/logic/ClientNames.kt`'s.
 *
 * - [open] only for a client the server gave a `name_addr`
 *   ([canRename]); the field starts with the operator-set name, empty
 *   otherwise (the current title is the placeholder).
 * - Save checks the name locally first ([normalizeClientName]), then sends
 *   `PUT /v1/client-names/ip/{name_addr}`; the dialog stays open on any
 *   failure and shows the reason.
 * - Remove name sends `DELETE`; the server's own 404 ("No name stored")
 *   counts as done. The note then names what the card shows now, from a
 *   fresh overview answer ([clear]'s `displayNameAfter`).
 * - Cancel, back and the scrim wait while a request runs ([busy]).
 */
class ClientRenameFlow(private val repository: ClientsRepository) {
    var target by mutableStateOf<ClientActivityOut?>(null)
        private set
    var input by mutableStateOf("")
    var error by mutableStateOf<String?>(null)
        private set
    var busy by mutableStateOf<RenameBusy?>(null)
        private set

    /** The note after a success (web: the toast); the screen shows and clears it. */
    var notice by mutableStateOf<String?>(null)

    fun open(client: ClientActivityOut) {
        if (busy != null || !canRename(client)) return
        target = client
        input = if (hasCustomName(client)) client.name.orEmpty() else ""
        error = null
    }

    fun cancel() {
        if (busy == null) target = null
    }

    /** @param onChanged re-fetch the page after a stored name. */
    suspend fun save(onChanged: suspend () -> Unit) {
        val client = target ?: return
        val addr = client.name_addr
        if (busy != null || addr.isNullOrEmpty()) return
        val value = when (val checked = normalizeClientName(input)) {
            is ClientNameCheck.Invalid -> {
                error = checked.error
                return
            }
            is ClientNameCheck.Ok -> checked.value
        }
        busy = RenameBusy.SAVE
        try {
            repository.setName(addr, value)
        } catch (e: VaultApiError) {
            busy = null
            error = renameErrorText(e)
            return
        }
        busy = null
        target = null
        notice = renamedToastText(value)
        onChanged()
    }

    /**
     * @param displayNameAfter the client's `display_name` in a fresh
     *   overview answer (by card key), `null` when that answer failed.
     * @param onChanged re-fetch the page.
     */
    suspend fun clear(displayNameAfter: suspend (String) -> String?, onChanged: suspend () -> Unit) {
        val client = target ?: return
        val addr = client.name_addr
        if (busy != null || addr.isNullOrEmpty()) return
        busy = RenameBusy.CLEAR
        try {
            repository.clearName(addr)
        } catch (e: VaultApiError) {
            if (!isNameAlreadyGone(e)) {
                busy = null
                error = renameErrorText(e)
                return
            }
        }
        val shownAs = try {
            displayNameAfter(client.key)
        } catch (_: VaultApiError) {
            null
        }
        busy = null
        target = null
        notice = nameRemovedToastText(shownAs)
        onChanged()
    }
}

/**
 * The rename dialog: title "Rename <name>", a "Name" field (placeholder:
 * the current title), the hint saying the name is stored per address, the
 * error line (announced when it appears, web role=alert), and Remove name
 * (only when a name is set) / Cancel / Save. The field gets focus on open;
 * the keyboard's Done saves (web: the form's submit). [scope] is the
 * SCREEN's: the dialog leaves composition when a request succeeds, and the
 * re-fetch after it must outlive the dialog.
 */
@Composable
fun ClientRenameDialog(
    flow: ClientRenameFlow,
    scope: CoroutineScope,
    displayNameAfter: suspend (String) -> String?,
    onChanged: suspend () -> Unit,
) {
    val client = flow.target ?: return
    val busy = flow.busy
    val focus = remember { FocusRequester() }
    val save = { scope.launch { flow.save(onChanged) } }
    AlertDialog(
        onDismissRequest = { flow.cancel() },
        title = { Text(renameTitle(clientName(client))) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val errorText = flow.error
                OutlinedTextField(
                    value = flow.input,
                    onValueChange = { flow.input = it },
                    label = { Text(stringResource(R.string.clients_rename_label)) },
                    placeholder = { Text(clientName(client)) },
                    singleLine = true,
                    isError = errorText != null,
                    keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { save() }),
                    modifier = Modifier
                        .fillMaxWidth()
                        .focusRequester(focus)
                        .semantics { if (errorText != null) error(errorText) },
                )
                // Inside the dialog's own composition, so the field is attached.
                LaunchedEffect(client.key) { focus.requestFocus() }
                Text(
                    renameHint(client),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (errorText != null) {
                    Text(
                        errorText,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive },
                    )
                }
                if (hasCustomName(client)) {
                    TextButton(
                        onClick = {
                            scope.launch { flow.clear(displayNameAfter, onChanged) }
                        },
                    ) {
                        Text(
                            stringResource(if (busy == RenameBusy.CLEAR) R.string.clients_rename_clearing else R.string.clients_rename_clear),
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { save() }) {
                Text(stringResource(if (busy == RenameBusy.SAVE) R.string.clients_rename_saving else R.string.clients_rename_save))
            }
        },
        dismissButton = {
            TextButton(onClick = { flow.cancel() }) { Text(stringResource(R.string.clients_rename_cancel)) }
        },
    )
}
