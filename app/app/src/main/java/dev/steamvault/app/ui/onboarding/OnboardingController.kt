package dev.steamvault.app.ui.onboarding

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.connection.ConnectionCheckResult
import dev.steamvault.app.net.connection.checkVaultConnection
import dev.steamvault.app.net.profile.CleartextNotAllowedException
import dev.steamvault.app.net.profile.ConnectivityProfile
import dev.steamvault.app.net.profile.PublicDomainProfile
import dev.steamvault.app.net.profile.SystemVpnProfile
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.CredentialStore
import dev.steamvault.app.storage.ProfileKind
import dev.steamvault.app.ui.onboarding.logic.FIRST_ONBOARDING_STEP
import dev.steamvault.app.ui.onboarding.logic.OnboardingStep
import dev.steamvault.app.ui.onboarding.logic.canAdvanceOnboardingStep
import dev.steamvault.app.ui.onboarding.logic.nextOnboardingStep
import dev.steamvault.app.ui.onboarding.logic.previousOnboardingStep

/** Which connectivity profile the user picked in step 1 -- see `net/profile/ConnectivityProfile.kt`. */
enum class ConnectivityProfileChoice { SYSTEM_VPN, PUBLIC_DOMAIN }

/** Governs [OnboardingController.canCancelWithoutFinishing] -- a first run has no working
 * connection to fall back to (Skip/Cancel is not offered); "Reconnect / switch
 * account" from Settings does, and can be dismissed leaving it untouched
 * (same distinction `web/js/onboarding.js`'s `mode` makes). */
enum class OnboardingMode { FIRST_RUN, RECONNECT }

/**
 * Everything the onboarding flow needs (WP 4b.7 brief) -- same thin-glue
 * shape every other screen controller in this app documents: state plus
 * suspend orchestration only, held via `remember` in `OnboardingScreen.kt`
 * (not an `androidx.lifecycle.ViewModel`, matching the house pattern).
 *
 * Step 1's connection test builds a THROWAWAY [VaultApiClient] against
 * whatever [profileChoice]/[baseUrlText]/[apiKeyText] currently hold --
 * nothing is persisted to [credentialStore] until [finish] is called on the
 * Done step, so a failed or abandoned attempt (Back, or a reconnect
 * cancelled) never partially overwrites an existing working connection.
 */
class OnboardingController(
    private val credentialStore: CredentialStore,
    private val identityRepository: SteamIdentityRepository,
    private val strings: OnboardingStrings,
    /** Overridable for tests -- the production default is a real OkHttp-backed client. */
    private val buildClient: (ConnectivityProfile, () -> String) -> VaultApiClient = { profile, keyProvider ->
        VaultApiClient(profile, keyProvider)
    },
) {
    var mode by mutableStateOf(OnboardingMode.FIRST_RUN)
        private set

    var step by mutableStateOf(FIRST_ONBOARDING_STEP)
        private set

    // ---- Step 1: Connect --------------------------------------------------

    var profileChoice by mutableStateOf(ConnectivityProfileChoice.SYSTEM_VPN)
    var baseUrlText by mutableStateOf("")
    var apiKeyText by mutableStateOf("")
    var testing by mutableStateOf(false)
        private set
    var tested by mutableStateOf(false)
        private set
    var connectionMessage by mutableStateOf<String?>(null)
        private set
    var connectionOk by mutableStateOf(false)
        private set

    // ---- Step 2: Steam identity (optional) ---------------------------------

    var identityState by mutableStateOf(identityRepository.state())
        private set
    var loginError by mutableStateOf<String?>(null)
        private set

    val canAdvance: Boolean get() = canAdvanceOnboardingStep(step, tested)

    /** Reconnect can bail out with the existing connection untouched; a
     * first run has nothing to fall back to, so there is nothing to cancel INTO. */
    val canCancelWithoutFinishing: Boolean get() = mode == OnboardingMode.RECONNECT

    /**
     * WP APP-DEMO: "Skip for now — browse in demo mode" (web's own wording,
     * `web/js/onboarding.js`'s `demoLink`), first-run only -- a reconnect
     * already has a working connection to fall back to (or the user would
     * not have reached Settings to trigger it), so offering demo mode there
     * would silently discard that connection with no warning. Hidden on the
     * [OnboardingStep.DONE] step too, same as the web port's
     * `els.demoLink.style.display` rule: finishing is one tap away by then,
     * skipping to demo instead would throw away a connection that just
     * tested successfully.
     */
    val canSkipToDemo: Boolean get() = mode == OnboardingMode.FIRST_RUN && step != OnboardingStep.DONE

    /** Reset all fields and (re)enter the flow. Called once per `openOnboarding`-equivalent. */
    fun start(mode: OnboardingMode) {
        this.mode = mode
        step = FIRST_ONBOARDING_STEP
        profileChoice = when (credentialStore.getProfileKind()) {
            ProfileKind.PUBLIC_DOMAIN -> ConnectivityProfileChoice.PUBLIC_DOMAIN
            else -> ConnectivityProfileChoice.SYSTEM_VPN
        }
        baseUrlText = credentialStore.getBaseUrl().orEmpty()
        apiKeyText = ""
        testing = false
        tested = false
        connectionMessage = null
        connectionOk = false
        identityState = identityRepository.state()
        loginError = null
    }

    fun next() {
        step = nextOnboardingStep(step, tested)
    }

    fun back() {
        step = previousOnboardingStep(step)
    }

    private fun buildProfile(choice: ConnectivityProfileChoice, baseUrl: String): ConnectivityProfile? = try {
        val url = baseUrl.trim()
        when (choice) {
            ConnectivityProfileChoice.SYSTEM_VPN -> SystemVpnProfile(url)
            ConnectivityProfileChoice.PUBLIC_DOMAIN -> PublicDomainProfile(url)
        }
    } catch (_: IllegalArgumentException) {
        null
    } catch (_: CleartextNotAllowedException) {
        null
    }

    /**
     * The onboarding "Test connection" action -- `net/connection/ConnectionCheck.kt`'s
     * two-step check (health reachability, then the authenticated settings
     * call), mirroring `web/js/api.js::checkVaultApiKey`'s reasoning (this
     * file's own kdoc has the full explanation). [tested] only ever becomes
     * `true` here, on an actually-verified [ConnectionCheckResult.Success]
     * -- there is no other path to it, so [next] can never leave step 1 on
     * an unverified guess.
     */
    suspend fun testConnection() {
        testing = true
        connectionMessage = null
        tested = false
        connectionOk = false
        try {
            val failure = verifyConnection(profileChoice, baseUrlText, apiKeyText)
            if (failure == null) {
                tested = true
                connectionOk = true
                connectionMessage = strings.connectionOk()
            } else {
                connectionMessage = failure
            }
        } finally {
            testing = false
        }
    }

    /**
     * The one connection check of this app, shared by [testConnection] (the
     * manual step-1 button) and the pairing link (WP APP-PAIR-1,
     * `MainActivity.verifyAndApplyPairing`): profile + URL validation, the
     * trim/non-empty key rule, then [checkVaultConnection]'s two steps
     * (health, then the authenticated settings call that actually proves
     * the key).
     *
     * Touches NO controller state, so a failed pairing attempt never
     * clobbers what the user has typed into step 1.
     *
     * @return `null` on success, else the user-facing failure message.
     */
    suspend fun verifyConnection(choice: ConnectivityProfileChoice, baseUrl: String, apiKey: String): String? {
        val profile = buildProfile(choice, baseUrl) ?: return strings.invalidUrl()
        val key = apiKey.trim()
        if (key.isEmpty()) return strings.enterApiKeyFirst()
        val client = buildClient(profile) { key }
        return when (val outcome = checkVaultConnection(health = { client.health() }, settings = { client.settings() })) {
            is ConnectionCheckResult.Success -> null
            is ConnectionCheckResult.Failure -> strings.connectionFailure(outcome.reason)
        }
    }

    /**
     * WP APP-PAIR-1: load a connection that [verifyConnection] has just
     * accepted (from a pairing link) into step 1 exactly as if the user had
     * typed it and pressed "Test connection" with success, then move on to
     * the Steam identity step. Persisting stays [finish]'s job -- the one
     * write path for a vault connection; pairing adds no second one.
     */
    fun applyVerifiedConnection(choice: ConnectivityProfileChoice, baseUrl: String, apiKey: String) {
        profileChoice = choice
        baseUrlText = baseUrl
        apiKeyText = apiKey
        testing = false
        tested = true
        connectionOk = true
        connectionMessage = strings.connectionOk()
        step = nextOnboardingStep(OnboardingStep.CONNECT, tested = true)
    }

    // ---- Step 2 orchestration -----------------------------------------------

    /** @return the `checkid_setup` URL to open in a Custom Tab. */
    fun buildSteamLoginUrl(): String {
        loginError = null
        return identityRepository.buildLoginUrl()
    }

    suspend fun completeSteamLogin(rawCallbackUrl: String) {
        when (val result = identityRepository.completeLogin(rawCallbackUrl)) {
            is SteamLoginResult.Success -> {
                identityState = identityRepository.state()
                loginError = null
            }
            is SteamLoginResult.Failure -> {
                identityState = identityRepository.state()
                loginError = result.reason
            }
        }
    }

    fun signOutSteam() {
        identityRepository.signOut()
        identityState = identityRepository.state()
    }

    // ---- Step 3: finish -------------------------------------------------------

    /** Persists the verified connection (and only the connection -- Steam
     * identity was already persisted incrementally by [completeSteamLogin]
     * as it happened). Only reachable via the Done step, which is only
     * reachable after step 1's [tested] gate passed. */
    fun finish() {
        credentialStore.setBaseUrl(baseUrlText.trim())
        credentialStore.setProfileKind(
            when (profileChoice) {
                ConnectivityProfileChoice.SYSTEM_VPN -> ProfileKind.SYSTEM_VPN
                ConnectivityProfileChoice.PUBLIC_DOMAIN -> ProfileKind.PUBLIC_DOMAIN
            },
        )
        credentialStore.setApiKey(apiKeyText.trim())
    }

    fun identitySummary(): SteamIdentityState = identityState
}
