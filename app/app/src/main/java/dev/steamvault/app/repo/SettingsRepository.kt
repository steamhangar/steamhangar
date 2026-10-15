package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.SettingsOut
import kotlinx.serialization.json.JsonElement

/**
 * Suspend-based repository over `GET`/`PATCH /v1/settings` (ADR-0009). Same
 * thin "typed name for the client call" shape as [GamesRepository]/
 * [JobsRepository]/[CacheRepository]/[ClientsRepository]/[MappingRepository]
 * -- extracted from [dev.steamvault.app.ui.settings.SettingsController]
 * (which used to call [VaultApiClient] directly for these two methods) so
 * demo mode (WP APP-DEMO) has the same repository seam every other screen
 * already had to substitute its own in-memory fixture without touching
 * [VaultApiClient] at all -- see
 * [dev.steamvault.app.demo.DemoSettingsRepository].
 */
interface SettingsRepository {
    suspend fun get(): SettingsOut
    suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut

    /**
     * D16: the PATCH after the user confirmed a risky change (`?confirm_risky=true`).
     * Default = the plain [patch], so a repository without a server-side
     * confirmation step (demo, fixtures) needs no override.
     */
    suspend fun patchConfirmed(updates: Map<String, JsonElement?>): SettingsOut = patch(updates)
}

class VaultSettingsRepository(private val client: VaultApiClient) : SettingsRepository {
    override suspend fun get(): SettingsOut = client.settings()
    override suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut = client.patchSettings(updates)
    override suspend fun patchConfirmed(updates: Map<String, JsonElement?>): SettingsOut =
        client.patchSettingsConfirmed(updates)
}
