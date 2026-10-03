package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.AboutOut

/**
 * Suspend-based repository over `GET /v1/about` (WP VER-2; Android parity in
 * WP APP-FEAT-2). Same thin "typed name for the client call" shape as every
 * other repository here, so demo mode can hand Settings an in-memory
 * fixture ([dev.steamvault.app.demo.DemoAboutRepository]) without touching
 * [VaultApiClient].
 */
interface AboutRepository {
    suspend fun get(): AboutOut
}

class VaultAboutRepository(private val client: VaultApiClient) : AboutRepository {
    override suspend fun get(): AboutOut = client.about()
}
