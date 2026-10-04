package dev.steamvault.app.ui.pairing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.pairing.PairingRejection
import dev.steamvault.app.ui.pairing.logic.PairingReplaceNotice

/**
 * WP APP-PAIR-1: the pairing confirmation, composed by MainActivity above
 * whatever screen is showing (onboarding or the main shell), so a link
 * that arrives at any point gets the same dialog.
 *
 * Shows the normalized URL that will be stored, never the key
 * (`PairingWiringTest` pins that this file does not read `apiKey`).
 * [onConfirm] starts `PairingController.confirm` in MainActivity's scope.
 */
@Composable
fun PairingDialog(controller: PairingController, onConfirm: () -> Unit) {
    when (val state = controller.state) {
        PairingUiState.Hidden -> Unit
        is PairingUiState.Rejected -> AlertDialog(
            onDismissRequest = { controller.dismiss() },
            title = { Text(stringResource(R.string.pairing_invalid_title)) },
            text = { Text(rejectionMessage(state.rejection, state.detail)) },
            confirmButton = {
                TextButton(onClick = { controller.dismiss() }) {
                    Text(stringResource(R.string.pairing_invalid_ok))
                }
            },
        )
        is PairingUiState.Confirm -> AlertDialog(
            onDismissRequest = { controller.dismiss() },
            title = { Text(stringResource(R.string.pairing_title, state.request.displayHost)) },
            text = { ConfirmBody(state) },
            confirmButton = {
                TextButton(enabled = !state.busy, onClick = onConfirm) {
                    Text(stringResource(R.string.pairing_confirm))
                }
            },
            dismissButton = {
                TextButton(enabled = !state.busy, onClick = { controller.dismiss() }) {
                    Text(stringResource(R.string.pairing_cancel))
                }
            },
        )
    }
}

@Composable
private fun ConfirmBody(state: PairingUiState.Confirm) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            text = stringResource(R.string.pairing_url_label),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(text = state.request.baseUrl, style = MaterialTheme.typography.bodyLarge)
        Text(text = stringResource(R.string.pairing_key_hint), style = MaterialTheme.typography.bodyMedium)

        if (state.request.usesCleartext) {
            Text(
                text = stringResource(R.string.pairing_cleartext_warning),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
        }

        when (state.notice) {
            PairingReplaceNotice.REPLACES_OTHER_VAULT -> Text(
                text = stringResource(R.string.pairing_notice_replace, state.existingHost.orEmpty()),
                style = MaterialTheme.typography.bodyMedium,
            )
            PairingReplaceNotice.SAME_VAULT -> Text(
                text = stringResource(R.string.pairing_notice_same_vault),
                style = MaterialTheme.typography.bodyMedium,
            )
            PairingReplaceNotice.NONE -> Unit
        }

        if (state.busy) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp).size(16.dp))
                Text(text = stringResource(R.string.pairing_checking), style = MaterialTheme.typography.bodyMedium)
            }
        }

        state.error?.let { message ->
            Text(
                text = message,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun rejectionMessage(rejection: PairingRejection, detail: String?): String = when (rejection) {
    PairingRejection.NOT_A_PAIRING_LINK -> stringResource(R.string.pairing_error_not_a_pairing_link)
    PairingRejection.MALFORMED_LINK -> stringResource(R.string.pairing_error_malformed)
    PairingRejection.DUPLICATE_PARAMETER -> stringResource(R.string.pairing_error_duplicate_parameter, detail.orEmpty())
    PairingRejection.VERSION_MISSING -> stringResource(R.string.pairing_error_version_missing)
    PairingRejection.VERSION_UNSUPPORTED -> stringResource(R.string.pairing_error_version_unsupported, detail.orEmpty())
    PairingRejection.URL_MISSING -> stringResource(R.string.pairing_error_url_missing)
    PairingRejection.URL_NOT_HTTP -> stringResource(R.string.pairing_error_url_not_http)
    PairingRejection.URL_INVALID -> stringResource(R.string.pairing_error_url_invalid)
    PairingRejection.URL_HAS_USERINFO -> stringResource(R.string.pairing_error_url_userinfo)
    PairingRejection.URL_HAS_FRAGMENT -> stringResource(R.string.pairing_error_url_fragment)
    PairingRejection.URL_HAS_PATH_OR_QUERY -> stringResource(R.string.pairing_error_url_path)
    PairingRejection.KEY_MISSING -> stringResource(R.string.pairing_error_key_missing)
    PairingRejection.KEY_EMPTY -> stringResource(R.string.pairing_error_key_empty)
    PairingRejection.KEY_INVALID_CHARACTERS -> stringResource(R.string.pairing_error_key_characters)
}
