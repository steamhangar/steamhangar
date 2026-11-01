package dev.steamvault.app.ui.clients

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.polling.PollingIntervals
import dev.steamvault.app.repo.ClientsRepository
import dev.steamvault.app.ui.clients.logic.REMOVED_HIDE_MS
import dev.steamvault.app.ui.clients.logic.isClientAlreadyGone
import dev.steamvault.app.ui.clients.logic.isRemovableClientId
import dev.steamvault.app.ui.clients.logic.removeErrorText
import dev.steamvault.app.ui.clients.logic.removedToastText
import dev.steamvault.app.ui.clients.logic.requestErrorReason
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Everything the clients sheet needs (WP 4b.10 brief) — same thin-glue
 * shape `ui/downloads/DownloadsController.kt` / `ui/detail/DetailController.kt`
 * document: state + suspend orchestration only, every DECISION delegates to
 * `ui/clients/logic/ClientsView.kt`'s pure functions.
 *
 * **Hoisted at `MainActivity` level, not owned by a single [dev.steamvault.app.ui.nav.Destination].**
 * The app has four destinations since WP D15d (Clients is one of them,
 * `ui/clients/ClientsScreen.kt`); this PCs sheet is held the same way
 * [dev.steamvault.app.ui.settings.SettingsController] is
 * (`MainActivity.clientsControllerState`), reachable from wherever it needs
 * to be opened FROM (the Clients page's "Manage PCs", a bypass notification
 * tap) regardless of which bottom-nav destination happens to be showing
 * underneath it — mirroring exactly how `web/js/components/clients-sheet.js`
 * floats over whatever view is current.
 *
 * **Foreground-only, poll only while [isOpen] (WP brief; same constraint
 * every screen in this app has before WP 4b.8's WorkManager wiring, applied
 * here to a surface that is not even always visible).** `web/js/store.js`
 * polls `GET /v1/clients` unconditionally (its bypass banner and
 * notifications differ both need a live snapshot regardless of the sheet
 * being open) — this app has no persistent bypass banner (out of this WP's
 * scope; the WorkManager-based background differ already covers the
 * notification half via its own independent poll,
 * `notifications/NotificationPollWorker.kt`), so the ONLY consumer of a live
 * `GET /v1/clients` poll on this platform is the sheet itself, and polling
 * only while it is open is the honest, resource-respecting scope for this
 * WP rather than inventing an always-on foreground loop nothing else reads.
 */
class ClientsController(
    private val clientsRepository: ClientsRepository,
    private val strings: ClientsStrings,
    private val clock: () -> Long = { System.currentTimeMillis() },
) {
    var isOpen by mutableStateOf(false)
        private set

    var clients by mutableStateOf<List<ClientOut>>(emptyList())
        private set

    var loadError by mutableStateOf<String?>(null)
        private set

    /**
     * Opens the sheet and paints it from the latest snapshot first, then
     * kicks an immediate fetch — mirrors `clients-sheet.js::openClientsSheet`
     * ("painting it from the latest snapshot first"). The periodic re-fetch
     * while open is [pollForever], driven by `ClientsSheet.kt`'s
     * `repeatOnLifecycle`.
     */
    fun open(scope: CoroutineScope) {
        isOpen = true
        removeNotice = null
        scope.launch { refreshOnce() }
    }

    /** Mockup rule (`docs/design/vault-app-mockup-NOTES.md`, "navigation
     * dismisses transient surfaces" — the clients sheet is explicitly named
     * alongside the detail sheet and the notifications panel): called from
     * `MainActivity`'s bottom-nav `onSelect` and from the sheet's own
     * dismiss request. */
    fun close() {
        isOpen = false
        // A confirm left open stays with the sheet that showed it; a running
        // request finishes and reports on the row (the flag stays set).
        if (!removeBusy) removeTarget = null
    }

    // ---- Remove a PC (WP ANDROID-GAPS-1, web WEB-FEAT-4) -------------------

    /** The PC the open confirm dialog is about; `null` = no dialog. */
    var removeTarget by mutableStateOf<String?>(null)
        private set

    /** A remove request is running: the dialog's buttons and dismiss wait. */
    var removeBusy by mutableStateOf(false)
        private set

    /** Inline error per client_id of the last failed remove (web
     * `state.removeErrors`), shown on that row until the next attempt. */
    var removeErrors by mutableStateOf<Map<String, String>>(emptyMap())
        private set

    /** "<id> removed from the list." after a success (web: the toast),
     * shown as a polite live line in the sheet until it closes. */
    var removeNotice by mutableStateOf<String?>(null)
        private set

    /** Opens the confirm for [clientId] (the row's Remove button). Ignored
     * while a request runs or for an id the server's route cannot address. */
    fun requestRemove(clientId: String) {
        if (removeBusy || !isRemovableClientId(clientId)) return
        removeErrors = removeErrors - clientId
        removeNotice = null
        removeTarget = clientId
    }

    /** "Keep", back or scrim: closes the confirm unless a request runs. */
    fun cancelRemove() {
        if (!removeBusy) removeTarget = null
    }

    /**
     * "Remove" in the confirm: `DELETE /v1/clients/{client_id}`. A 204, or
     * the handler's own 404 ("already gone", [isClientAlreadyGone]), drops
     * the row at once and re-fetches the list (a still-running agent may
     * list the PC again with its next report); any other failure keeps the
     * row and shows the server's reason on it.
     */
    suspend fun confirmRemove() {
        val clientId = removeTarget ?: return
        if (removeBusy) return
        removeBusy = true
        var failure: VaultApiError? = null
        try {
            clientsRepository.remove(clientId)
        } catch (e: VaultApiError) {
            if (!isClientAlreadyGone(e)) failure = e
        } finally {
            removeBusy = false
            removeTarget = null
        }
        if (failure != null) {
            removeErrors = removeErrors + (clientId to removeErrorText(clientId, requestErrorReason(failure)))
            return
        }
        removeErrors = removeErrors - clientId
        clients = clients.filter { it.client_id != clientId }
        val now = clock()
        // Expired entries hide nothing any more (review N5): drop them.
        removedAt = removedAt.filterValues { now - it <= REMOVED_HIDE_MS } + (clientId to now)
        removeNotice = removedToastText(clientId)
        refreshOnce()
    }

    /** WP ANDROID-REDESIGN-1a (web `removedIds`): client_id -> time of a
     * successful remove. The Clients page hides that PC's overview row for
     * [REMOVED_HIDE_MS] ([hiddenRemoved]) until the overview has caught up;
     * an agent that reports again is listed again after that. Entries older
     * than that are dropped at the next remove. */
    var removedAt by mutableStateOf<Map<String, Long>>(emptyMap())
        private set

    /** The page's snackbar read the remove note: drop it. */
    fun clearRemoveNotice() {
        removeNotice = null
    }

    /**
     * WP ANDROID-REDESIGN-1a: the Clients page merges these agent rows into
     * its cards (web: the store's `GET /v1/clients` snapshot, polled at the
     * same [PollingIntervals.CLIENTS_MS]). Runs only while the page is
     * STARTED (`ClientsScreen.kt`'s `repeatOnLifecycle`); first fetch at once.
     */
    suspend fun pollForPage() {
        while (true) {
            refreshOnce()
            delay(PollingIntervals.CLIENTS_MS)
        }
    }

    suspend fun refreshOnce() {
        try {
            clients = clientsRepository.list()
            loadError = null
        } catch (e: VaultApiError) {
            loadError = e.message ?: strings.loadErrorFallback()
        }
    }

    /**
     * Same cadence `web/js/store.js`'s `clientsMs` interval uses
     * (`PollingIntervals.CLIENTS_MS`, WP 4b.2 — unused by any caller until
     * this WP).
     *
     * **Review fix (N3/N4): corrected lifecycle description.** This does
     * NOT run continuously across the sheet closing and reopening — the
     * `LaunchedEffect` that calls this (`ClientsSheet.kt`) only exists
     * while the `ClientsSheet` composable itself is in the tree, which
     * `MainActivity` only ever composes while [isOpen] is `true`
     * (`clientsControllerState?.let { if (it.isOpen) ClientsSheet(it) }`).
     * Closing the sheet therefore CANCELS this coroutine outright, and
     * reopening starts a fresh one — the `if ([isOpen])` guard below is
     * NOT "stay idle while closed" load-bearing logic (that case cannot
     * be observed: the loop is torn down before it could ever see
     * `isOpen == false`); it is a narrow defensive check against the
     * brief window between [close] flipping the flag and Compose actually
     * cancelling the effect on the next recomposition, so a fetch never
     * fires for a tick that lands in that gap.
     */
    suspend fun pollForever() {
        while (true) {
            delay(PollingIntervals.CLIENTS_MS)
            if (isOpen) refreshOnce()
        }
    }
}
