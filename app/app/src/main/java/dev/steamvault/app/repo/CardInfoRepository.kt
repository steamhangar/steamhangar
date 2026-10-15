package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.DepotPreviewOut
import dev.steamvault.app.net.model.StoreDetailsOut

/**
 * Suspend-based repository over the two card-enrichment reads of WP D19:
 * `GET /v1/oracle/{appid}/preview` (expected depots and size of a game never
 * downloaded) and `GET /v1/store/{appid}/details` (store description, modes,
 * rating). Both are best effort: the game detail sheet treats any failure as
 * "nothing extra to show". Same thin shape as every other repository here.
 */
interface CardInfoRepository {
    suspend fun depotPreview(appid: Int): DepotPreviewOut
    suspend fun storeDetails(appid: Int): StoreDetailsOut
}

class VaultCardInfoRepository(private val client: VaultApiClient) : CardInfoRepository {
    override suspend fun depotPreview(appid: Int): DepotPreviewOut = client.depotPreview(appid)
    override suspend fun storeDetails(appid: Int): StoreDetailsOut = client.storeDetails(appid)
}

/**
 * Answers `off` for everything, asks nothing. Demo mode uses it (that mode
 * is structurally network-free) and screens that are handed no repository
 * default to it, so the sheet then looks exactly as before D19.
 */
object OffCardInfoRepository : CardInfoRepository {
    override suspend fun depotPreview(appid: Int): DepotPreviewOut = DepotPreviewOut(appid = appid, state = "off")
    override suspend fun storeDetails(appid: Int): StoreDetailsOut = StoreDetailsOut(appid = appid, state = "off")
}
