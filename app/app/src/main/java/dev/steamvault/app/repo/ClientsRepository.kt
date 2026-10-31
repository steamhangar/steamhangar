package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.ClientOut

/**
 * Suspend-based repository over `GET /v1/clients` (WP 4b.2 brief) plus the
 * client-management writes the web UI already has (WP ANDROID-GAPS-1):
 * remove a PC (`DELETE /v1/clients/{client_id}`, WP AG-1) and set or clear
 * an operator name per address (`PUT`/`DELETE /v1/client-names/ip/{addr}`,
 * WP D15-NAMES).
 */
interface ClientsRepository {
    suspend fun list(): List<ClientOut>

    /** Forget one PC's reports and bypass state; throws the server's 404
     * ("Unknown client_id ...") when nothing is stored for [clientId]. */
    suspend fun remove(clientId: String)

    /** Store [name] for the address [addr]. */
    suspend fun setName(addr: String, name: String)

    /** Clear the name stored for [addr]; throws the server's 404 ("No name
     * stored ...") when there is none. */
    suspend fun clearName(addr: String)
}

class VaultClientsRepository(private val client: VaultApiClient) : ClientsRepository {
    override suspend fun list(): List<ClientOut> = client.clients()
    override suspend fun remove(clientId: String) = client.deleteClient(clientId)
    override suspend fun setName(addr: String, name: String) = client.setClientName(addr, name)
    override suspend fun clearName(addr: String) = client.clearClientName(addr)
}
