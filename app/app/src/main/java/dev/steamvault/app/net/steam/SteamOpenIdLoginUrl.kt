package dev.steamvault.app.net.steam

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Where Steam sends the browser back to, and how that answer reaches the app.
 *
 * **WP D11 (APP-FIX-LOGIN): Steam refuses a custom-scheme `return_to`.**
 * WP 4b.3 used `steamvault://auth/openid-return` as both `return_to` and
 * `realm`, with an "on-device only" caveat; on a device Steam answered
 * "Invalid return protocol" -- its OpenID provider only returns to
 * `http(s)`. Since D11 the app asks Steam to return to a page on the
 * user's OWN vault-api ([VAULT_RETURN_PATH] under the configured base
 * URL, plain `http://` on a LAN is fine) and that page hands the answer,
 * query unchanged, to [APP_LINK] (`api/vault_api/routers/app_return.py`).
 * [APP_LINK] keeps the manifest's `BROWSABLE` intent filter and
 * `MainActivity.onNewIntent` exactly as before.
 *
 * The trust decision did not move: vault-api only forwards, it never
 * verifies or stores the answer. The app still checks the per-login
 * `state` (now inside the signed `http(s)` `return_to`), the signed-field
 * list and `check_authentication` against [STEAM_HOST] itself
 * (`SteamIdentityRepositoryImpl.completeLogin`).
 *
 * `realm` is the vault's origin (`scheme://host[:port]/`, see
 * [SteamOpenIdReturnUrl]): OpenID 2.0 requires `return_to` to sit under
 * the realm, and the origin is also the name Steam shows on its sign-in
 * page.
 */
object SteamOpenIdConfig {
    /** The app's deep link, matched by `AndroidManifest.xml`'s intent filter. */
    const val SCHEME = "steamvault"
    const val HOST = "auth"
    const val PATH = "/openid-return"
    const val APP_LINK = "$SCHEME://$HOST$PATH"

    /** vault-api's public hand-off page; must match
     * `api/vault_api/routers/app_return.py::APP_RETURN_PATH`. */
    const val VAULT_RETURN_PATH = "/auth/steam/app-return"

    /** The one host this app will ever send an OpenID request/verification to. */
    const val STEAM_HOST = "steamcommunity.com"
    const val LOGIN_ENDPOINT = "https://$STEAM_HOST/openid/login"
}

/** The `return_to` and `realm` one login attempt sends to Steam. */
data class SteamOpenIdReturn(val returnTo: String, val realm: String)

/**
 * Builds the vault-hosted `return_to`/`realm` pair (WP D11) -- pure, no
 * network, JVM-testable.
 *
 * Only the vault base URL's scheme, host and port are used: path, query,
 * fragment and any userinfo are dropped, the same way `VaultApiClient`
 * replaces the path for every call. `null` when [vaultBaseUrl] is not an
 * `http(s)` URL (blank, malformed, another scheme): the caller then has
 * no address Steam could return to.
 */
object SteamOpenIdReturnUrl {
    fun forVault(vaultBaseUrl: String, state: String): SteamOpenIdReturn? {
        val base = vaultBaseUrl.trim().toHttpUrlOrNull() ?: return null
        val origin = base.newBuilder()
            .username("")
            .password("")
            .query(null)
            .fragment(null)
        val realm = origin.encodedPath("/").build().toString()
        val returnTo = origin.encodedPath(SteamOpenIdConfig.VAULT_RETURN_PATH)
            .addQueryParameter("state", state)
            .build()
            .toString()
        return SteamOpenIdReturn(returnTo = returnTo, realm = realm)
    }

    /**
     * Shape check on a callback's (signed) `openid.return_to`: an `http(s)`
     * URL whose path is exactly [SteamOpenIdConfig.VAULT_RETURN_PATH].
     * Defence in depth next to the per-login `state`, which is what
     * actually binds a callback to this app's own login attempt.
     */
    fun isVaultReturnTo(returnTo: String): Boolean {
        val url = returnTo.toHttpUrlOrNull() ?: return false
        return url.encodedPath == SteamOpenIdConfig.VAULT_RETURN_PATH
    }
}

/**
 * Pure `checkid_setup` URL construction -- no network, no Android
 * framework dependency, fully JVM-testable (the WP brief's explicit ask:
 * "OpenID URL construction (literal expected URL pin)").
 *
 * Built via `okhttp3.HttpUrl.Builder` (the same URL-building primitive
 * [dev.steamvault.app.net.VaultApiClient] already uses) rather than hand
 * assembled string concatenation, so query-parameter percent-encoding is
 * OkHttp's own well-tested implementation, not a hand-rolled one.
 */
object SteamOpenIdLoginUrl {
    fun build(returnTo: String, realm: String): String {
        val builder = SteamOpenIdConfig.LOGIN_ENDPOINT.toHttpUrl().newBuilder()
        builder.addQueryParameter("openid.ns", "http://specs.openid.net/auth/2.0")
        builder.addQueryParameter("openid.mode", "checkid_setup")
        builder.addQueryParameter("openid.return_to", returnTo)
        builder.addQueryParameter("openid.realm", realm)
        builder.addQueryParameter(
            "openid.identity",
            "http://specs.openid.net/auth/2.0/identifier_select",
        )
        builder.addQueryParameter(
            "openid.claimed_id",
            "http://specs.openid.net/auth/2.0/identifier_select",
        )
        return builder.build().toString()
    }
}
