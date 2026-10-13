package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.UpdateCheckOut
import dev.steamvault.app.net.model.UpdateStatusOut

/**
 * Suspend-based repository over the update check (WP UPD-1 API, WP UPD-3
 * app): [check] starts a check in the background and answers at once (it
 * never starts a download), [status] says whether it still runs and what
 * the last run found. Same thin shape as every other repository here, so
 * demo mode and tests hand the Library an in-memory implementation.
 */
interface UpdatesRepository {
    suspend fun check(): UpdateCheckOut
    suspend fun status(): UpdateStatusOut
}

class VaultUpdatesRepository(private val client: VaultApiClient) : UpdatesRepository {
    override suspend fun check(): UpdateCheckOut = client.checkUpdates()
    override suspend fun status(): UpdateStatusOut = client.updatesStatus()
}
