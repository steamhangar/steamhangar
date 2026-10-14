package dev.steamvault.app.net.steam

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Something implementing the login-URL + verification steps, so [dev.steamvault.app.repo.SteamIdentityRepositoryImpl] tests can fake it. */
interface SteamOpenIdVerifier {
    /** The `checkid_setup` URL to open in a Custom Tab -- pure, no network. */
    fun buildLoginUrl(returnTo: String, realm: String): String

    /**
     * POSTs every `openid.*` param [SteamOpenIdCallback.parse] extracted
     * back to Valve with `openid.mode` overridden to
     * `check_authentication`, per OpenID 2.0 -- the step that actually
     * proves the callback was not forged (an Android deep-link `Intent`
     * carrying arbitrary attacker-chosen query parameters is trivially
     * sendable by any app, so [SteamOpenIdCallback.parse] alone proves
     * nothing about authenticity).
     *
     * @return `true` only for a literal, exact `is_valid:true` line in
     *   Valve's response body -- anything else (`is_valid:false`, a
     *   malformed/garbage body, a non-2xx status, a redirect, a network
     *   failure) is `false`. Fail-closed by construction: there is no
     *   path in this function that returns `true` without having read and
     *   strictly matched that exact line (docs/LEARNINGS.md "Testing
     *   discipline": pin the fail-closed DIRECTION, not just the happy
     *   path).
     */
    suspend fun checkAuthentication(params: Map<String, String>): Boolean
}

/**
 * The verification half of Steam OpenID login (WP 4b.3): posts back to
 * Valve's login endpoint with `openid.mode=check_authentication` and
 * parses the strict `is_valid:true` response line. Reuses the exact OkHttp
 * security posture WP 4b.2's `VaultApiClient` established and
 * `docs/LEARNINGS.md` ("Android (Phase 4b)") pins as load-bearing:
 * `followSslRedirects(false)` + `followRedirects(false)` (no redirect is
 * ever legitimate for a fixed, known endpoint), HTTPS only (the endpoint
 * constant is always `https://`, never derived from any caller input), and
 * a bounded response read (Valve's `check_authentication` response is a
 * couple of short text lines; anything wildly larger is refused rather
 * than buffered in full).
 *
 * **Host pin.** [SteamOpenIdConfig.LOGIN_ENDPOINT] is the literal,
 * hardcoded URL every production call targets -- [loginUrl] exists as a
 * constructor parameter ONLY so `SteamOpenIdClientTest` can point this
 * class at a local `MockWebServer` for behavioural round trips (redirect
 * refusal, `is_valid` variants, oversized-body refusal); the production
 * `MainActivity`/`SteamIdentityRepositoryImpl` wiring never overrides it,
 * and a literal-equality test pins [SteamOpenIdConfig.LOGIN_ENDPOINT]'s
 * value itself (`docs/LEARNINGS.md` 4a.6r rule: assert the literal string,
 * not a value derived from the same constant under test).
 */
class SteamOpenIdClient(
    private val loginUrl: String = SteamOpenIdConfig.LOGIN_ENDPOINT,
    okHttpClient: OkHttpClient = defaultSteamOkHttpClient(),
) : SteamOpenIdVerifier {

    private val client: OkHttpClient = okHttpClient.newBuilder()
        .followSslRedirects(false)
        .followRedirects(false)
        .build()

    /** Test-only escape hatch, same pattern as `VaultApiClient.debugHttpClientForTesting`. */
    internal val debugHttpClientForTesting: OkHttpClient get() = client

    override fun buildLoginUrl(
        returnTo: String,
        realm: String,
    ): String = SteamOpenIdLoginUrl.build(returnTo, realm)

    override suspend fun checkAuthentication(params: Map<String, String>): Boolean {
        val bodyBuilder = FormBody.Builder()
        for ((key, value) in params) {
            bodyBuilder.add(key, if (key == "openid.mode") "check_authentication" else value)
        }
        if ("openid.mode" !in params) {
            bodyBuilder.add("openid.mode", "check_authentication")
        }
        val request = Request.Builder().url(loginUrl).post(bodyBuilder.build()).build()

        val response = try {
            withContext(Dispatchers.IO) { client.newCall(request).execute() }
        } catch (_: IOException) {
            return false // fail-closed: an unreachable/broken connection is never "verified"
        }

        return response.use { resp ->
            if (!resp.isSuccessful) return@use false
            val text = readBounded(resp.body?.source(), MAX_RESPONSE_BYTES) ?: return@use false
            isValidTrueStrict(text)
        }
    }

    companion object {
        /**
         * Valve's `check_authentication` answer is a handful of short
         * `key:value` text lines -- generous headroom while still refusing
         * a body designed to exhaust memory (same reasoning as
         * `api/vault_api/steam_relay.py::MAX_RESPONSE_BYTES`, scaled down
         * since this endpoint's real payload is tiny by comparison).
         */
        const val MAX_RESPONSE_BYTES = 8L * 1024
    }
}

internal fun defaultSteamOkHttpClient(): OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(10, TimeUnit.SECONDS)
    .readTimeout(15, TimeUnit.SECONDS)
    .writeTimeout(15, TimeUnit.SECONDS)
    .callTimeout(30, TimeUnit.SECONDS)
    .followSslRedirects(false)
    .followRedirects(false)
    .build()

/**
 * Reads at most `maxBytes + 1` bytes from [source] and returns them as
 * text, or `null` if the body is larger than [maxBytes] -- the bounded-read
 * discipline `docs/LEARNINGS.md`'s Parsers section and
 * `api/vault_api/steam_relay.py::http_fetch` both apply: a hostile or
 * misbehaving server's response body is never buffered past this bound
 * before being rejected.
 *
 * `BufferedSource.request(n)` (not a single `read(sink, n)` call) is
 * required here: `read()` may return fewer bytes than asked for on one
 * call even when more remain (it stops at whatever the underlying source
 * handed back for that one call, e.g. a TCP segment boundary) -- `request`
 * is the primitive that actually blocks until either `n` bytes are
 * buffered or the source is exhausted, which is what makes the
 * `buffer.size > maxBytes` check below a true bound on the FULL body, not
 * just on one read chunk of it.
 */
internal fun readBounded(source: okio.BufferedSource?, maxBytes: Long): String? {
    if (source == null) return null
    source.request(maxBytes + 1)
    val buffered = source.buffer
    if (buffered.size > maxBytes) return null
    return buffered.readUtf8()
}

/**
 * Strict `is_valid:true` line match (WP brief: "strict `is_valid:true`
 * parsing"). Finds the FIRST line whose trimmed text starts with
 * `is_valid:` and requires it to equal exactly `is_valid:true` --
 * `is_valid:false`, `is_valid:True`, `is_valid:true `, a missing
 * `is_valid` line entirely, or any other body all return `false`. Never
 * uses a substring/`contains` check against the whole body: a garbage
 * document that happens to contain the text `is_valid:true` somewhere
 * inside an unrelated line (e.g. echoed back inside an error message) must
 * not be accepted.
 */
internal fun isValidTrueStrict(body: String): Boolean {
    val line = body.lineSequence()
        .map { it.trim() }
        .firstOrNull { it.startsWith("is_valid:") }
    return line == "is_valid:true"
}
