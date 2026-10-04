package dev.steamvault.app.ui.pairing.logic

import dev.steamvault.app.net.pairing.PairingRequest
import dev.steamvault.app.ui.onboarding.ConnectivityProfileChoice
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/*
 * WP APP-PAIR-1: the pure decisions behind the pairing dialog, kept free of
 * Android types so `PairingDecisionsTest` can pin each one on the JVM.
 */

/** What the confirmation dialog says about the connection the device already has. */
enum class PairingReplaceNotice {
    /** Nothing stored yet (first run, demo mode): nothing gets replaced. */
    NONE,

    /** A DIFFERENT vault is configured: pairing replaces that connection. */
    REPLACES_OTHER_VAULT,

    /** The SAME vault is configured: only the stored key is replaced. */
    SAME_VAULT,
}

/**
 * @param existingBaseUrl the base URL in `CredentialStore`, or `null`.
 * @param hasExistingKey whether `CredentialStore` holds a non-blank API key.
 *   A stored URL without a key is not a working connection, so nothing is
 *   "replaced" in that case.
 */
fun replaceNoticeFor(existingBaseUrl: String?, hasExistingKey: Boolean, request: PairingRequest): PairingReplaceNotice {
    if (existingBaseUrl.isNullOrBlank() || !hasExistingKey) return PairingReplaceNotice.NONE
    val existing = normalizedOrigin(existingBaseUrl)
    return if (existing != null && existing == request.baseUrl) {
        PairingReplaceNotice.SAME_VAULT
    } else {
        PairingReplaceNotice.REPLACES_OTHER_VAULT
    }
}

/**
 * How the "currently connected to ..." notice names the stored vault:
 * `host[:port]`, or the full `scheme://host[:port]` when that host equals
 * the paired one (only the scheme differs, e.g. `http://` stored and
 * `https://` paired -- "connected to hangar.lan, replaces that connection"
 * would read like a contradiction). The trimmed raw text when the stored
 * URL does not parse (it is whatever the user typed in onboarding).
 */
fun existingDisplayHost(existingBaseUrl: String?, request: PairingRequest): String? {
    val raw = existingBaseUrl?.trim()?.takeIf { it.isNotEmpty() } ?: return null
    val url = raw.toHttpUrlOrNull() ?: return raw
    val host = displayHostOf(url)
    return if (host == request.displayHost) "${url.scheme}://$host" else host
}

/** `scheme://host[:port]`, built exactly like `PairingLink.parse` builds `PairingRequest.baseUrl`. */
internal fun normalizedOrigin(baseUrl: String): String? {
    val url = baseUrl.trim().toHttpUrlOrNull() ?: return null
    return "${url.scheme}://${displayHostOf(url)}"
}

private fun displayHostOf(url: HttpUrl): String {
    val host = if (url.host.contains(':')) "[${url.host}]" else url.host
    return if (url.port != HttpUrl.defaultPort(url.scheme)) "$host:${url.port}" else host
}

/**
 * Which onboarding connectivity profile a paired URL gets. `https://` gets
 * [ConnectivityProfileChoice.PUBLIC_DOMAIN], the TLS-mandatory profile: it
 * reaches the same server, and additionally refuses any `http://` hop
 * (`CleartextPolicyInterceptor`). `http://` can only work with
 * [ConnectivityProfileChoice.SYSTEM_VPN] (LAN/VPN, cleartext allowed); the
 * dialog warns about it.
 */
fun pairingProfileChoice(request: PairingRequest): ConnectivityProfileChoice =
    if (request.usesCleartext) ConnectivityProfileChoice.SYSTEM_VPN else ConnectivityProfileChoice.PUBLIC_DOMAIN

/** Where the app goes after a pairing link has been verified. */
enum class PairingContinuation {
    /** Onboarding is on screen: load the connection into it and move to the Steam identity step. */
    CONTINUE_ONBOARDING,

    /** Not onboarding and no real connection (demo mode): open onboarding, then as above. */
    OPEN_ONBOARDING,

    /** A real connection exists (onboarding was finished): store the new one and go home. */
    STORE_AND_GO_HOME,
}

/**
 * "If onboarding wasn't finished, go to its next step (Steam identity);
 * else go home" (WP brief). Onboarding is finished exactly when a real
 * vault connection exists -- there is no separate flag
 * (`OnboardingSteps.kt::shouldShowOnboarding`). An open onboarding wins
 * even when a connection exists (Settings' "Reconnect / switch vault"
 * runs onboarding over a working connection): the user is in the middle
 * of that flow, and it persists on its own Done step as always.
 */
fun pairingContinuation(showOnboarding: Boolean, hasRealConnection: Boolean): PairingContinuation = when {
    showOnboarding -> PairingContinuation.CONTINUE_ONBOARDING
    hasRealConnection -> PairingContinuation.STORE_AND_GO_HOME
    else -> PairingContinuation.OPEN_ONBOARDING
}

/**
 * Whether a pairing link found in an `Intent` should open the dialog.
 *
 * MainActivity strips a consumed link (`intent.data = null`), which covers
 * rotation. Two re-deliveries survive the strip, because Android hands back
 * the ORIGINAL launch Intent: an Activity restored after process death
 * (`savedInstanceState != null`), and a relaunch from Recents
 * (`FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY`). Neither is the user scanning a
 * code, so neither offers pairing again.
 */
fun shouldOfferPairing(restoredInstance: Boolean, launchedFromHistory: Boolean): Boolean =
    !restoredInstance && !launchedFromHistory
