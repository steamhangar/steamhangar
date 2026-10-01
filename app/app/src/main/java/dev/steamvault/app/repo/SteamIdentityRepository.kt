package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.steam.PendingLoginState
import dev.steamvault.app.net.steam.SteamLibraryFetcher
import dev.steamvault.app.net.steam.SteamLoginState
import dev.steamvault.app.net.steam.SteamOpenIdCallback
import dev.steamvault.app.net.steam.SteamOpenIdClient
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.net.steam.SteamOpenIdVerifier
import dev.steamvault.app.net.steam.VaultRelayLibraryFetcher
import dev.steamvault.app.storage.CredentialStore

/**
 * Everything the identity screen needs to render (WP 4b.3 brief: "signed-in
 * state shows steamid/persona + sign-out"). Lost its third field,
 * `hasWebApiKey`, in WP 4h.4 (ADR-0004's second addendum): there is no
 * device-local Steam Web API key left to have configured or not -- library
 * data flows through vault-api's own relay unconditionally once signed in.
 */
data class SteamIdentityState(
    val steamId64: String?,
    val personaName: String?,
) {
    val isSignedIn: Boolean get() = steamId64 != null
}

/** The outcome of [SteamIdentityRepository.completeLogin] -- never throws, always one of these two. */
sealed class SteamLoginResult {
    data class Success(val steamId64: String) : SteamLoginResult()

    /** [reason] is always a fixed, human-readable, key/secret-free string -- safe to show in the UI as-is. */
    data class Failure(val reason: String) : SteamLoginResult()
}

/**
 * Steam identity (OpenID sign-in state + library data) — the data layer WP
 * 4b.3 asks for, updated by WP 4h.4 (ADR-0004's second addendum): OpenID
 * sign-in is still fully independent of vault-api (identity established on
 * Valve's page, never a credential this app sees), but library/persona
 * fetching now goes THROUGH vault-api's relay ([libraryFetcher]'s
 * production implementation, [VaultRelayLibraryFetcher], talks to
 * [VaultApiClient]) rather than being isolated from it — see
 * `SteamKeyIsolationTest`'s updated invariant (no direct-to-Valve Web API
 * host reference anywhere in `src/main` anymore) for what is STILL
 * structurally guaranteed after this change.
 */
interface SteamIdentityRepository {
    /** Current persisted state, read fresh from [CredentialStore] every call. */
    fun state(): SteamIdentityState

    /** The `checkid_setup` URL to open in a Custom Tab. */
    fun buildLoginUrl(): String

    /**
     * Verifies [rawCallbackUrl] (the raw deep-link URL Valve redirected
     * back to, e.g. `Intent.dataString`) against Valve via
     * `check_authentication`, and on success persists the extracted
     * SteamID64. Never throws — every failure mode (malformed callback,
     * an unsigned `claimed_id`, a `check_authentication` rejection, an
     * invalid SteamID64) becomes [SteamLoginResult.Failure].
     */
    suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult

    /**
     * Best-effort persona-name refresh via `GetPlayerSummaries` (relayed
     * through vault-api as of WP 4h.4) — WP brief: "persona name optional".
     * Requires a signed-in [state]; returns `false` (without throwing) if
     * not signed in or the call fails for any reason (no vault-api
     * connection, no relay key configured server-side, a network failure),
     * `true` once the persona name is persisted.
     */
    suspend fun refreshPersonaName(): Boolean

    /**
     * `GetOwnedGames`'s game count only — WP brief: "library fetch happens
     * in 4b.4 — expose the repository, render a count preview only".
     * Superseded for the actual library grid by [ownedGames] below (WP
     * 4b.4), which this delegates to so the two never drift.
     */
    suspend fun ownedGamesCountPreview(): Result<Int>

    /**
     * The full owned-library list (WP 4b.4 brief: "Steam library (owned
     * games) merge per the mockup's model"; WP 4h.4: now relayed through
     * vault-api). `Result.failure` — never a thrown exception — when not
     * signed in or the relay call itself fails for any reason (no vault-api
     * connection, `409` no relay key configured server-side, `422` a
     * rejected steamid, a network failure); the Library screen's merge
     * logic (`ui/library/logic/LibraryMerge.kt`) treats absence of this
     * data as "the vault-only view must be fully functional" (mockup-
     * notes.md open question 5 / WP brief), not an error state. Settings'
     * Steam-identity section (`ui/settings/logic/SteamLibraryStatus.kt`)
     * is where the SPECIFIC reason (not configured / private-or-empty /
     * generic failure) gets a dedicated message instead.
     */
    suspend fun ownedGames(): Result<List<OwnedGame>>

    /** Clears everything Steam-identity-related (steamid, persona) — WP brief: "sign-out clears everything". */
    fun signOut()
}

class SteamIdentityRepositoryImpl(
    private val credentialStore: CredentialStore,
    private val openIdVerifier: SteamOpenIdVerifier = SteamOpenIdClient(),
    /**
     * The CURRENT [VaultApiClient], or `null` if no vault-api connection is
     * configured yet — read fresh on every [libraryFetcher] call (WP 4h.4),
     * never captured once: this repository is constructed once, long
     * before a connection necessarily exists (Steam sign-in, unlike library
     * fetching, is reachable during onboarding). Production default `{
     * null }` only matters when [libraryFetcher] is ALSO left at its
     * default -- [dev.steamvault.app.MainActivity] always supplies its own
     * lambda over `vaultApiClientState`.
     */
    vaultApiClientProvider: () -> VaultApiClient? = { null },
    private val libraryFetcher: SteamLibraryFetcher = VaultRelayLibraryFetcher(vaultApiClientProvider),
    /** WP 4b.7 replay-residual fix -- see [PendingLoginState]'s kdoc. The
     * holder's LIFETIME is the caller's choice (WP APP-FIX-1 P1 corrected
     * this kdoc, which used to claim "repository lifetime == app process
     * lifetime" while [dev.steamvault.app.MainActivity] built one repository
     * per Activity instance): the default here is per-repository-instance,
     * which is what tests want; production passes
     * `MainActivity.PROCESS_PENDING_LOGIN_STATE`, a process-scoped holder,
     * so a pending attempt survives an Activity recreation while the Custom
     * Tab is in front. Never persisted: a login attempt that outlives the
     * PROCESS (a killed-and-restarted app while the Custom Tab is open)
     * simply fails closed on the way back in, rather than being silently
     * exempt from the state check. */
    private val pendingLoginState: PendingLoginState = PendingLoginState(),
    /** Overridable ONLY for deterministic tests -- the production default
     * is [SteamLoginState.generate]'s real `SecureRandom` path. */
    private val stateGenerator: () -> String = { SteamLoginState.generate() },
) : SteamIdentityRepository {

    override fun state(): SteamIdentityState = SteamIdentityState(
        steamId64 = credentialStore.getSteamId64(),
        personaName = credentialStore.getSteamPersonaName(),
    )

    override fun buildLoginUrl(): String {
        // WP 4b.7 replay-residual fix: a fresh, single-use random state per
        // login attempt, embedded in return_to and checked BEFORE the
        // network round trip in completeLogin below -- see
        // SteamLoginState.kt's module kdoc for the attack this closes.
        val state = stateGenerator()
        pendingLoginState.start(state)
        val returnTo = "${SteamOpenIdConfig.RETURN_TO}?state=$state"
        return openIdVerifier.buildLoginUrl(returnTo = returnTo, realm = SteamOpenIdConfig.REALM)
    }

    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult {
        val params = SteamOpenIdCallback.parse(rawCallbackUrl)
            ?: return SteamLoginResult.Failure("The Steam sign-in response was malformed or incomplete.")

        // WP 4b.7 replay-residual fix: checked BEFORE signedCoversClaimedId
        // and BEFORE the check_authentication network call (brief:
        // "callback with missing/wrong state rejected BEFORE
        // check_authentication"). consume() is single-use -- a second call
        // with the exact same (valid) callback fails here the second time,
        // since the pending state was already cleared by the first.
        val callbackState = SteamOpenIdCallback.stateFromReturnTo(params.getValue("openid.return_to"))
        if (!pendingLoginState.consume(callbackState)) {
            return SteamLoginResult.Failure("This sign-in link has expired or was already used — please sign in again.")
        }

        if (!SteamOpenIdCallback.signedCoversClaimedId(params.getValue("openid.signed"))) {
            return SteamLoginResult.Failure("Steam's response did not sign the account identifier — rejected.")
        }

        // WP APP-FIX-1 (S4a): return_to carries the `state` the gate above
        // just trusted, so it is now a field this app branches on for a
        // security decision and must be inside the signed set too --
        // otherwise check_authentication's is_valid:true says nothing about
        // it (OpenID 2.0 lists return_to among the fields an OP MUST sign;
        // this app must not merely assume Valve did).
        if (!SteamOpenIdCallback.signedCoversReturnTo(params.getValue("openid.signed"))) {
            return SteamLoginResult.Failure("Steam's response did not sign the return address — rejected.")
        }

        val verified = openIdVerifier.checkAuthentication(params)
        if (!verified) {
            return SteamLoginResult.Failure("Steam did not confirm this sign-in — please try again.")
        }

        val steamId64 = SteamOpenIdCallback.steamId64From(params.getValue("openid.claimed_id"))
            ?: return SteamLoginResult.Failure("Steam returned an account identifier this app could not recognize.")

        credentialStore.setSteamId64(steamId64)
        return SteamLoginResult.Success(steamId64)
    }

    override suspend fun refreshPersonaName(): Boolean {
        val steamId64 = credentialStore.getSteamId64() ?: return false
        val persona = try {
            libraryFetcher.getPlayerSummary(steamId64)
        } catch (_: Exception) {
            null
        } ?: return false
        credentialStore.setSteamPersonaName(persona.personaName)
        return true
    }

    override suspend fun ownedGamesCountPreview(): Result<Int> =
        ownedGames().map { it.size }

    override suspend fun ownedGames(): Result<List<OwnedGame>> {
        val steamId64 = credentialStore.getSteamId64()
            ?: return Result.failure(IllegalStateException("not signed in with Steam"))
        return try {
            Result.success(libraryFetcher.getOwnedGames(steamId64))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    override fun signOut() {
        credentialStore.clearSteamIdentity()
    }
}
