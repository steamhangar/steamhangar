package dev.steamvault.app.ui.pairing

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.pairing.PairingLink
import dev.steamvault.app.net.pairing.PairingParseResult
import dev.steamvault.app.net.pairing.PairingRejection
import dev.steamvault.app.net.pairing.PairingRequest
import dev.steamvault.app.ui.pairing.logic.PairingReplaceNotice
import dev.steamvault.app.ui.pairing.logic.existingDisplayHost
import dev.steamvault.app.ui.pairing.logic.replaceNoticeFor

/** What the pairing dialog shows (`PairingDialog.kt`). */
sealed class PairingUiState {
    data object Hidden : PairingUiState()

    /** The link was refused; the dialog explains why (`pairing_error_*`). */
    data class Rejected(val rejection: PairingRejection, val detail: String?) : PairingUiState()

    /**
     * "Pair with <host>?". [error] is the in-place failure message of the
     * last attempt (the onboarding connection-check wording). The data
     * class `toString` is safe: [PairingRequest.toString] redacts the key.
     */
    data class Confirm(
        val request: PairingRequest,
        val notice: PairingReplaceNotice,
        val existingHost: String?,
        val busy: Boolean = false,
        val error: String? = null,
    ) : PairingUiState()
}

/**
 * WP APP-PAIR-1: state of the pairing confirmation dialog.
 *
 * Holds no Android reference and no collaborator: MainActivity keeps ONE
 * instance at process scope (its companion object, same reasoning as
 * `PROCESS_PENDING_LOGIN_STATE`), so a dialog that is open during a
 * rotation is still open in the recreated Activity. The scanned link is
 * stripped from the Intent, so the process-scoped state is the only copy;
 * it is deliberately not written to a Bundle (the key would land in saved
 * instance state). Process death drops it -- the user scans again.
 *
 * The verification and the write are passed into [confirm] per call, so
 * the controller never retains the Activity.
 */
class PairingController {

    var state: PairingUiState by mutableStateOf<PairingUiState>(PairingUiState.Hidden)
        private set

    /**
     * A pairing link arrived. Parses it and opens the confirmation (or the
     * refusal). Ignored while a confirmed attempt is still checking the
     * connection -- swapping the request under a running check would make
     * the dialog show one vault while the other gets stored.
     */
    fun offer(rawLink: String, existingBaseUrl: String?, hasExistingKey: Boolean) {
        if (isBusy()) return
        state = when (val parsed = PairingLink.parse(rawLink)) {
            is PairingParseResult.Valid -> PairingUiState.Confirm(
                request = parsed.request,
                notice = replaceNoticeFor(existingBaseUrl, hasExistingKey, parsed.request),
                existingHost = existingDisplayHost(existingBaseUrl),
            )
            is PairingParseResult.Invalid -> PairingUiState.Rejected(parsed.rejection, parsed.detail)
        }
    }

    /** Cancel / OK / back press. Not while a check is running (the buttons are disabled then too). */
    fun dismiss() {
        if (isBusy()) return
        state = PairingUiState.Hidden
    }

    /**
     * The user tapped "Pair". [verifyAndApply] checks the connection and, on
     * success, stores it and moves the app on (MainActivity's
     * `verifyAndApplyPairing`); it returns `null` on success, else the
     * message shown in place in the dialog.
     *
     * A second tap while busy is a no-op. If the coroutine is cancelled
     * (the Activity is destroyed mid-check), the dialog drops back to
     * not-busy so the recreated Activity shows a usable Pair button
     * instead of a spinner that never ends.
     *
     * @return `true` once the pairing was applied.
     */
    suspend fun confirm(verifyAndApply: suspend (PairingRequest) -> String?): Boolean {
        val current = state as? PairingUiState.Confirm ?: return false
        if (current.busy) return false
        val busyState = current.copy(busy = true, error = null)
        state = busyState
        var completed = false
        var failure: String? = null
        try {
            failure = verifyAndApply(current.request)
            completed = true
        } finally {
            if (state === busyState) {
                state = if (completed && failure == null) {
                    PairingUiState.Hidden
                } else {
                    busyState.copy(busy = false, error = failure)
                }
            }
        }
        return failure == null
    }

    private fun isBusy(): Boolean = (state as? PairingUiState.Confirm)?.busy == true
}
