package dev.steamvault.app.repo

import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SteamPersona
import dev.steamvault.app.net.steam.SteamLibraryFetcher
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.net.steam.SteamOpenIdVerifier
import dev.steamvault.app.storage.CredentialStore

/**
 * PERSONA-LOAD-1: shared support for the controller tests that drive the
 * REAL [SteamIdentityRepositoryImpl] (login, persona fetch, persistence)
 * and fake only its two network seams, so the tests pin the shipped
 * persona path rather than a fake that re-implements it.
 */
internal const val PERSONA_TEST_STEAM_ID = "76561198042117903"
internal const val PERSONA_TEST_NAME = "Example Persona"
internal const val PERSONA_TEST_VAULT = "http://10.0.0.50:8080"
private const val PERSONA_TEST_STATE = "persona-test-state"

/** Answers `check_authentication` with `true`; no network. */
internal class AcceptingOpenIdVerifier : SteamOpenIdVerifier {
    override fun buildLoginUrl(returnTo: String, realm: String): String =
        "https://steamcommunity.com/openid/login?fake=1"

    override suspend fun checkAuthentication(params: Map<String, String>): Boolean = true
}

/** The relay seam: answers the persona (or throws [personaFailure]) and
 * counts the calls. */
internal class PersonaFetcher(
    var persona: SteamPersona? = SteamPersona(PERSONA_TEST_STEAM_ID, PERSONA_TEST_NAME),
    var personaFailure: Exception? = null,
) : SteamLibraryFetcher {
    var personaCalls = 0

    override suspend fun getOwnedGames(steamId64: String): List<OwnedGame> = emptyList()

    override suspend fun getPlayerSummary(steamId64: String): SteamPersona? {
        personaCalls++
        personaFailure?.let { throw it }
        return persona
    }
}

internal fun personaTestRepository(store: CredentialStore, fetcher: PersonaFetcher): SteamIdentityRepository =
    SteamIdentityRepositoryImpl(
        credentialStore = store,
        openIdVerifier = AcceptingOpenIdVerifier(),
        libraryFetcher = fetcher,
        stateGenerator = { PERSONA_TEST_STATE },
    )

/** A callback that passes every check of [SteamIdentityRepositoryImpl.completeLogin]
 * for a login started with `buildLoginUrl(PERSONA_TEST_VAULT)`. */
internal fun personaTestCallbackUrl(): String {
    fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")
    val claimedId = "https://steamcommunity.com/openid/id/$PERSONA_TEST_STEAM_ID"
    val returnTo = "$PERSONA_TEST_VAULT/auth/steam/app-return?state=$PERSONA_TEST_STATE"
    return "${SteamOpenIdConfig.APP_LINK}?" +
        "openid.mode=id_res" +
        "&openid.claimed_id=" + enc(claimedId) +
        "&openid.identity=" + enc(claimedId) +
        "&openid.return_to=" + enc(returnTo) +
        "&openid.signed=" + enc("signed,claimed_id,identity,return_to") +
        "&openid.sig=" + enc("Zm9vYmFy")
}
