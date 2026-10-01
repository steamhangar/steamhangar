package dev.steamvault.app.repo

import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SteamPersona
import dev.steamvault.app.net.steam.SteamLibraryFetcher
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.net.steam.SteamOpenIdVerifier
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.storage.ProfileKind
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private const val VALID_STEAM_ID = "76561198042117903"

/** Matches the fixed `stateGenerator` every test's [repo] helper injects
 * (WP 4b.7 replay-residual fix) -- deterministic so [callbackUrl]'s default
 * echoes back the SAME state a preceding `repo.buildLoginUrl()` call
 * started, without either side reading the other's internals. */
private const val TEST_STATE = "test-state-1"

private fun callbackUrl(
    steamId: String = VALID_STEAM_ID,
    signed: String = "signed,claimed_id,identity,return_to",
    mode: String = "id_res",
    /** `null` omits `state` from `return_to` entirely (simulates a
     * pre-4b.7 callback, or a provider that dropped the query string). */
    state: String? = TEST_STATE,
): String {
    fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")
    val claimedId = "https://steamcommunity.com/openid/id/$steamId"
    val returnTo = if (state != null) "${SteamOpenIdConfig.RETURN_TO}?state=$state" else SteamOpenIdConfig.RETURN_TO
    return "${SteamOpenIdConfig.RETURN_TO}?" +
        "openid.mode=$mode" +
        "&openid.claimed_id=" + enc(claimedId) +
        "&openid.identity=" + enc(claimedId) +
        "&openid.return_to=" + enc(returnTo) +
        "&openid.signed=" + enc(signed) +
        "&openid.sig=" + enc("Zm9vYmFy")
}

private class FakeOpenIdVerifier(
    var checkAuthResult: Boolean = true,
    private val loginUrl: String = "https://steamcommunity.com/openid/login?fake=1",
) : SteamOpenIdVerifier {
    var lastParams: Map<String, String>? = null
    var callCount = 0

    override fun buildLoginUrl(returnTo: String, realm: String): String = loginUrl

    override suspend fun checkAuthentication(params: Map<String, String>): Boolean {
        callCount++
        lastParams = params
        return checkAuthResult
    }
}

/** Fakes [SteamLibraryFetcher] (WP 4h.4: the vault-relay-backed
 * production implementation is [dev.steamvault.app.net.steam.VaultRelayLibraryFetcher],
 * covered separately in `VaultRelayLibraryFetcherTest`) -- this repository
 * only depends on the INTERFACE, so [SteamIdentityRepositoryImpl]'s own
 * logic (preconditions, Result wrapping) is what this file pins. */
private class FakeLibraryFetcher(
    var games: List<OwnedGame> = emptyList(),
    var persona: SteamPersona? = null,
    var throwOnGames: Exception? = null,
) : SteamLibraryFetcher {
    override suspend fun getOwnedGames(steamId64: String): List<OwnedGame> {
        throwOnGames?.let { throw it }
        return games
    }

    override suspend fun getPlayerSummary(steamId64: String): SteamPersona? = persona
}

class SteamIdentityRepositoryTest {

    private fun repo(
        verifier: FakeOpenIdVerifier = FakeOpenIdVerifier(),
        fetcher: FakeLibraryFetcher = FakeLibraryFetcher(),
        store: InMemoryCredentialStore = InMemoryCredentialStore(),
        // WP 4b.7: fixed, not the real SecureRandom generator, so tests can
        // predict what callbackUrl() must echo back without either side
        // reading the other's internals.
        stateGenerator: () -> String = { TEST_STATE },
    ): Triple<SteamIdentityRepository, FakeOpenIdVerifier, FakeLibraryFetcher> =
        Triple(
            SteamIdentityRepositoryImpl(
                credentialStore = store,
                openIdVerifier = verifier,
                libraryFetcher = fetcher,
                stateGenerator = stateGenerator,
            ),
            verifier,
            fetcher,
        )

    @Test
    fun `initial state is signed out with no persona`() {
        val (repo, _, _) = repo()
        val state = repo.state()
        assertFalse(state.isSignedIn)
        assertNull(state.steamId64)
        assertNull(state.personaName)
    }

    @Test
    fun `buildLoginUrl delegates to the verifier`() {
        val (repo, _, _) = repo(verifier = FakeOpenIdVerifier(loginUrl = "https://steamcommunity.com/openid/login?x=1"))
        assertEquals("https://steamcommunity.com/openid/login?x=1", repo.buildLoginUrl())
    }

    @Test
    fun `completeLogin succeeds end to end and persists the SteamID64`() = runTest {
        val (repo, verifier, _) = repo()

        repo.buildLoginUrl()
        val result = repo.completeLogin(callbackUrl())

        assertTrue(result is SteamLoginResult.Success)
        assertEquals(VALID_STEAM_ID, (result as SteamLoginResult.Success).steamId64)
        assertEquals(VALID_STEAM_ID, repo.state().steamId64)
        assertTrue(repo.state().isSignedIn)
        assertEquals(1, verifier.callCount)
    }

    @Test
    fun `completeLogin fails on a malformed callback without calling the verifier at all`() = runTest {
        val (repo, verifier, _) = repo()

        val result = repo.completeLogin("not-even-a-url")

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `MUTATION PIN -- completeLogin fails when signed does not cover claimed_id, without calling the verifier`() = runTest {
        val (repo, verifier, _) = repo()

        repo.buildLoginUrl()
        val result = repo.completeLogin(callbackUrl(signed = "signed,identity,return_to"))

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    /** WP APP-FIX-1 (S4a): `return_to` carries the `state` the replay gate
     * trusts, so a callback whose `openid.signed` covers `claimed_id` but
     * NOT `return_to` must be rejected before `check_authentication` -- the
     * state it echoed back matches, which is exactly why the signature
     * requirement is the load-bearing part here. */
    @Test
    fun `MUTATION PIN -- completeLogin fails when signed does not cover return_to, without calling the verifier`() = runTest {
        val (repo, verifier, _) = repo()

        repo.buildLoginUrl()
        val result = repo.completeLogin(callbackUrl(signed = "signed,claimed_id,identity"))

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `completeLogin fails when check_authentication rejects the assertion, and nothing is persisted`() = runTest {
        val (repo, verifier, _) = repo(verifier = FakeOpenIdVerifier(checkAuthResult = false))

        repo.buildLoginUrl()
        val result = repo.completeLogin(callbackUrl())

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(1, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `completeLogin fails for a claimed_id that is not a valid SteamID64, even after a passing check_authentication`() = runTest {
        val (repo, _, _) = repo()

        repo.buildLoginUrl()
        val result = repo.completeLogin(callbackUrl(steamId = "00000000000000000"))

        assertTrue(result is SteamLoginResult.Failure)
        assertNull(repo.state().steamId64)
    }

    // ---- ownedGamesCountPreview / ownedGames (WP 4h.4: no key precondition anymore) --

    @Test
    fun `ownedGamesCountPreview fails when not signed in`() = runTest {
        val (repo, _, _) = repo()
        val result = repo.ownedGamesCountPreview()
        assertTrue(result.isFailure)
    }

    @Test
    fun `MUTATION PIN -- ownedGamesCountPreview succeeds once signed in, with no Web API key precondition left to check`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val fetcher = FakeLibraryFetcher(games = listOf(OwnedGame(440, "TF2")))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        val result = repo.ownedGamesCountPreview()

        assertEquals(1, result.getOrNull())
    }

    @Test
    fun `ownedGamesCountPreview surfaces a fetcher failure as a Result failure, not an exception`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val fetcher = FakeLibraryFetcher(throwOnGames = RuntimeException("boom"))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        val result = repo.ownedGamesCountPreview()

        assertTrue(result.isFailure)
    }

    @Test
    fun `ownedGames fails when not signed in`() = runTest {
        val (repo, _, _) = repo()
        assertTrue(repo.ownedGames().isFailure)
    }

    @Test
    fun `MUTATION PIN -- ownedGames returns the full list once signed in, with no Web API key precondition left to check`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val games = listOf(OwnedGame(440, "TF2"), OwnedGame(570, "Dota 2"))
        val (repo, _, _) = repo(fetcher = FakeLibraryFetcher(games = games), store = store)

        val result = repo.ownedGames()

        assertEquals(games, result.getOrNull())
    }

    @Test
    fun `ownedGames returns an empty list unchanged -- the maybe-private-or-empty case is not this layer's job to interpret`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val (repo, _, _) = repo(fetcher = FakeLibraryFetcher(games = emptyList()), store = store)

        val result = repo.ownedGames()

        assertTrue(result.isSuccess)
        assertTrue(result.getOrNull()!!.isEmpty())
    }

    @Test
    fun `ownedGames surfaces a fetcher failure -- e g a VaultApiError -- as a Result failure, not an exception`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val fetcher = FakeLibraryFetcher(throwOnGames = RuntimeException("boom"))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        assertTrue(repo.ownedGames().isFailure)
    }

    @Test
    fun `ownedGamesCountPreview delegates to ownedGames -- the two can never drift`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val games = listOf(OwnedGame(440, "TF2"), OwnedGame(570, "Dota 2"))
        val (repo, _, _) = repo(fetcher = FakeLibraryFetcher(games = games), store = store)

        assertEquals(2, repo.ownedGamesCountPreview().getOrNull())
    }

    // ---- refreshPersonaName (WP 4h.4: no key precondition anymore) -------

    @Test
    fun `refreshPersonaName is false when not signed in`() = runTest {
        val (repo, _, _) = repo()
        assertFalse(repo.refreshPersonaName())
    }

    @Test
    fun `refreshPersonaName persists the persona name once signed in`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val fetcher = FakeLibraryFetcher(persona = SteamPersona(VALID_STEAM_ID, "Example"))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        assertTrue(repo.refreshPersonaName())
        assertEquals("Example", repo.state().personaName)
    }

    @Test
    fun `signOut clears only Steam identity fields, leaving the vault connection intact`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setApiKey("vault-key")
            setBaseUrl("http://192.168.1.50:8080")
            setProfileKind(ProfileKind.SYSTEM_VPN)
        }
        val (repo, _, _) = repo(store = store)
        repo.buildLoginUrl()
        repo.completeLogin(callbackUrl())
        assertTrue(repo.state().isSignedIn)

        repo.signOut()

        val state = repo.state()
        assertFalse(state.isSignedIn)
        assertNull(state.personaName)
        assertEquals("vault-key", store.getApiKey())
        assertEquals("http://192.168.1.50:8080", store.getBaseUrl())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
    }

    // ---- WP 4b.7: replay-residual fix (per-login state parameter) --------

    @Test
    fun `completeLogin fails when no login was ever started, without calling the verifier`() = runTest {
        val (repo, verifier, _) = repo() // no buildLoginUrl() call -- nothing pending

        val result = repo.completeLogin(callbackUrl())

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `completeLogin fails when return_to carries no state at all, without calling the verifier`() = runTest {
        val (repo, verifier, _) = repo()

        repo.buildLoginUrl()
        val result = repo.completeLogin(callbackUrl(state = null))

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `completeLogin fails when the callback state does not match the pending one, without calling the verifier`() = runTest {
        val (repo, verifier, _) = repo()

        repo.buildLoginUrl()
        val result = repo.completeLogin(callbackUrl(state = "attacker-supplied-state"))

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `MUTATION PIN -- a consumed state cannot be replayed`() = runTest {
        val (repo, verifier, _) = repo()

        repo.buildLoginUrl()
        val first = repo.completeLogin(callbackUrl())
        assertTrue(first is SteamLoginResult.Success)
        assertEquals(1, verifier.callCount)

        // Same exact callback URL, replayed a second time -- the state it
        // carries was already consumed by the first completeLogin() call,
        // so this must fail WITHOUT reaching check_authentication again.
        val replay = repo.completeLogin(callbackUrl())
        assertTrue(replay is SteamLoginResult.Failure)
        assertEquals(1, verifier.callCount) // unchanged -- the verifier was never called again
    }

    @Test
    fun `each buildLoginUrl call invalidates the previous attempt's pending state`() = runTest {
        var call = 0
        val (repo, verifier, _) = repo(stateGenerator = { "state-${call++}" })

        repo.buildLoginUrl() // pending = state-0
        repo.buildLoginUrl() // pending = state-1, state-0 is no longer valid

        val result = repo.completeLogin(callbackUrl(state = "state-0"))

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
    }
}
