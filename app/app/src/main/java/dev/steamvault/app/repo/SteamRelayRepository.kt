package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.OwnedGamesRelayOut
import dev.steamvault.app.net.model.PlayerSummariesRelayOut

/**
 * vault-api's Steam relay for an EXPLICIT SteamID64 (WP APP-FEAT-1): the
 * vault's stored library id (`steam_library_steamid`, WP API-FEAT-1) for the
 * Library, and the id typed into Settings for the "Preview" lookup. Unlike
 * [SteamIdentityRepository.ownedGames], nothing here reads the device's own
 * Steam OpenID sign-in: the account the Library shows is chosen once, on the
 * vault, for every device (same model as `web/js/lib/owned-library.js`).
 *
 * Errors are whatever [VaultApiClient] throws (`409` no relay key on the
 * server, `422` a rejected steamid, ...), unwrapped, same as
 * [dev.steamvault.app.net.steam.VaultRelayLibraryFetcher].
 */
interface SteamRelayRepository {
    suspend fun ownedGames(steamId64: String): OwnedGamesRelayOut
    suspend fun playerSummaries(steamId64: String): PlayerSummariesRelayOut
}

class VaultSteamRelayRepository(private val client: VaultApiClient) : SteamRelayRepository {
    override suspend fun ownedGames(steamId64: String): OwnedGamesRelayOut = client.steamOwnedGames(steamId64)
    override suspend fun playerSummaries(steamId64: String): PlayerSummariesRelayOut =
        client.steamPlayerSummaries(steamId64)
}
