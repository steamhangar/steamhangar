package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.HistoryDeleteOut

/**
 * "Delete history" (WP D15-NAMES API, WP ANDROID-GAPS-1 app):
 * `DELETE /v1/history?scope=`, [scope] being `all` or `older-than-30d`
 * (`ui/downloads/logic/HistoryDelete.kt`).
 */
interface HistoryRepository {
    suspend fun delete(scope: String): HistoryDeleteOut
}

class VaultHistoryRepository(private val client: VaultApiClient) : HistoryRepository {
    override suspend fun delete(scope: String): HistoryDeleteOut = client.deleteHistory(scope)
}
