package dev.steamvault.app.net

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.AboutOut
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.CacheSummaryOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.GcRequest
import dev.steamvault.app.net.model.HealthOut
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.OwnedGamesRelayOut
import dev.steamvault.app.net.model.PlayerSummariesRelayOut
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.PrefillRequest
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.buildSettingsPatch
import dev.steamvault.app.net.profile.CleartextPolicyInterceptor
import dev.steamvault.app.net.profile.ConnectivityProfile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

private fun defaultOkHttpClient(): OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(10, TimeUnit.SECONDS)
    .readTimeout(20, TimeUnit.SECONDS)
    .writeTimeout(20, TimeUnit.SECONDS)
    .callTimeout(45, TimeUnit.SECONDS)
    // BLOCKER B1 fix (WP 4b.2 Opus review): OkHttp follows an https<->http
    // redirect by default, forwarding X-Api-Key (not an Authorization-class
    // header OkHttp strips on host change) to wherever the Location header
    // points -- silently leaking the key on a downgrade. Refusing to
    // auto-follow a scheme-changing redirect at all is the primary fix.
    .followSslRedirects(false)
    // S2 fix (WP 4b.2 delta review): followSslRedirects(false) ALONE only
    // covers a SCHEME change (https<->http) -- an https-to-https redirect
    // to a DIFFERENT HOST still forwards X-Api-Key by default, since
    // OkHttp only strips Authorization-class headers on a host change, not
    // a caller-supplied header like X-Api-Key. No redirect is ever
    // legitimate for this client (every call targets an exact, known
    // `/v1/...` path), so redirects are refused outright, same-scheme or not.
    .followRedirects(false)
    // VaultApiClient's own wrapping OkHttpClient re-applies BOTH settings
    // unconditionally (see its kdoc), so they hold even for an injected
    // OkHttpClient that forgot them.
    .build()

/**
 * Thin, typed OkHttp client for the vault-api `/v1` surface the app needs
 * (WP 4b.2 brief's explicit list): games incl. detail, jobs + control,
 * prefill, cache summary/delete, gc, clients, settings GET/PATCH, health.
 *
 * `/v1/mapping` is wrapped ([mapping]) as of WP 4b.4 — the bulk-delete
 * confirm dialog needs the full depot->app table to compute
 * [dev.steamvault.app.ui.library.logic.MultiPlan]'s set-aware arithmetic,
 * the same "add it with the WP that needs it" rule web/js/api.js documents
 * (that WP's own multiplan.js port needed the identical fetch).
 *
 * **`/v1/steam/...` (the Steam Web API relay) IS wrapped, as of WP 4h.4 —
 * superseding the WP 4b.2-era exclusion note this kdoc used to carry.**
 * ADR-0004's second addendum removed the app's own device-local Steam Web
 * API key entirely: [steamOwnedGames]/[steamPlayerSummaries] are now the
 * ONLY path this app has to library/persona data, authenticated exactly
 * like every other route here (`X-Api-Key`), never Valve's Web API host
 * directly. See `net/steam/VaultRelayLibraryFetcher.kt`'s kdoc and
 * `app/README.md`'s "Steam library via the vault relay" section for the
 * full story, including the `409`/`422` states callers must branch on.
 *
 * `X-Api-Key` is attached to EVERY request, including `/v1/health` —
 * mirroring web/js/api.js's `request()`, which sends it unconditionally
 * rather than special-casing the one route api/README.md documents as
 * unauthenticated ("Auth"). [apiKeyProvider] is read fresh on every call
 * (not cached at construction) so a key change in
 * [dev.steamvault.app.storage.CredentialStore] takes effect on the very
 * next request without rebuilding this client.
 */
class VaultApiClient(
    private val profile: ConnectivityProfile,
    private val apiKeyProvider: () -> String,
    okHttpClient: OkHttpClient = defaultOkHttpClient(),
) {
    private val baseUrl: HttpUrl = profile.baseUrl.toHttpUrl()

    // BLOCKER B1 + S2 fix (WP 4b.2 Opus review, both rounds): re-applied
    // HERE, unconditionally on top of whatever OkHttpClient was passed in
    // (the production default above, or a test's/future caller's own
    // client), so none of this can be silently lost by construction:
    //  - followSslRedirects(false): stops OkHttp's RetryAndFollowUpInterceptor
    //    from ever building a follow-up request for an https<->http redirect
    //    in the first place -- the redirect target's socket is never opened.
    //  - followRedirects(false): same, but for ANY redirect, including an
    //    https-to-https one to a DIFFERENT HOST (which followSslRedirects
    //    alone does not cover -- see defaultOkHttpClient()'s S2 comment).
    //    No redirect is ever a legitimate outcome for this client.
    //  - addInterceptor: the pre-socket gate for the ORIGINAL request (see
    //    CleartextPolicyInterceptor's kdoc for exactly what this layer does
    //    and does NOT cover -- it runs once, wrapping the whole call). Note
    //    this is ADDITIVE: an injected OkHttpClient that already carries its
    //    own interceptors (or, in principle, its own CleartextPolicyInterceptor)
    //    keeps them -- `newBuilder()` copies the existing interceptor list
    //    and this call appends to it, it does not replace anything. Running
    //    the same check twice on such a client is harmless (idempotent).
    //  - addNetworkInterceptor: the per-HOP gate, including any OkHttp-
    //    internal follow-up (redirect, auth-challenge retry, ...) that skips
    //    application interceptors entirely -- this is what actually sees and
    //    can reject an individual http:// request before its own socket opens.
    // Every layer above is pinned STANDALONE as well as end-to-end in
    // VaultApiClientTest (WP 4b.2 delta review, should-fix S1): the
    // network-interceptor-alone test builds a client with BOTH redirect
    // flags left at OkHttp's insecure default (true) and proves the
    // interceptor still blocks the downgrade; the config-assertion test
    // below inspects `debugHttpClientForTesting` to prove the flag layer
    // itself lands correctly (which also finally exercises
    // defaultOkHttpClient()'s copy of these flags, previously untested).
    private val client: OkHttpClient = okHttpClient.newBuilder()
        .followSslRedirects(false)
        .followRedirects(false)
        .addInterceptor(CleartextPolicyInterceptor(profile))
        .addNetworkInterceptor(CleartextPolicyInterceptor(profile))
        .build()

    /**
     * Test-only escape hatch (WP 4b.2 delta review, should-fix S1b): lets
     * `VaultApiClientTest` assert directly that the built [client] carries
     * `followRedirects == false && followSslRedirects == false`, rather
     * than only ever observing those flags indirectly through an
     * end-to-end redirect scenario (which — per the same review round —
     * cannot tell "the flag layer is present" apart from "the interceptor
     * layer alone happened to cover for its absence"). `internal`, not
     * `public`: this is a white-box test seam, not part of the client's
     * API surface.
     */
    internal val debugHttpClientForTesting: OkHttpClient
        get() = client

    // ---- games --------------------------------------------------------

    suspend fun health(): HealthOut = get("/v1/health")

    suspend fun games(): List<GameSummary> = get("/v1/games")

    suspend fun game(appid: Int): GameDetail = get("/v1/games/$appid")

    // ---- jobs -----------------------------------------------------------

    suspend fun jobs(limit: Int = 20): List<JobSummary> =
        get("/v1/jobs", params = mapOf("limit" to limit.toString()))

    suspend fun job(id: Int): JobDetail = get("/v1/jobs/$id")

    suspend fun prefill(appids: List<Int>): List<PrefillJobRef> =
        post("/v1/prefill", PrefillRequest(appids))

    /**
     * `POST /v1/prefill/cached` (Phase 4c, WP 4c-api/4c-app): selects every
     * app that currently has cache content and queues a prefill for each,
     * server-side. **No request body ever** — api/README.md documents that
     * ANY body this route receives (including an `{"appids": [...]}` shape
     * that would look plausible for this client's own [prefill] method) is
     * silently accepted and ignored, so sending one here would be worse
     * than a no-op: it would look like a scoped call while the server
     * queues EVERY cached app regardless. [postEmpty] is the same "no body"
     * plumbing [pauseJob]/[resumeJob] already use. Always `202`, including
     * an empty selection (`[]`) — never an error on its own account.
     */
    suspend fun prefillCached(): List<PrefillJobRef> = postEmpty("/v1/prefill/cached")

    suspend fun cancelJob(id: Int): JobControlOut = delete("/v1/jobs/$id")

    suspend fun pauseJob(id: Int): JobControlOut = postEmpty("/v1/jobs/$id/pause")

    suspend fun resumeJob(id: Int): JobControlOut = postEmpty("/v1/jobs/$id/resume")

    // ---- cache ----------------------------------------------------------

    suspend fun deleteCache(appid: Int): CacheDeletionOut = delete("/v1/cache/$appid")

    suspend fun cacheSummary(): CacheSummaryOut = get("/v1/cache/summary")

    suspend fun gc(appid: Int, execute: Boolean = false): GcJobRef =
        post("/v1/cache/$appid/gc", GcRequest(execute))

    // ---- mapping ------------------------------------------------------

    /** Full depot->app mapping table (WP 4b.4: bulk-delete plan input). */
    suspend fun mapping(): List<MappingEntry> = get("/v1/mapping")

    // ---- clients ----------------------------------------------------------

    suspend fun clients(): List<ClientOut> = get("/v1/clients")

    // ---- settings ---------------------------------------------------------

    suspend fun settings(): SettingsOut = get("/v1/settings")

    /** @param updates key -> new value ([dev.steamvault.app.net.model.settingPatchValue]), or `null` to clear the override. */
    suspend fun patchSettings(updates: Map<String, JsonElement?>): SettingsOut =
        patch("/v1/settings", buildSettingsPatch(updates))

    // ---- schedule (WP AG-3) -----------------------------------------------

    /** `GET /v1/schedule` — scheduler config + last-sweep bookkeeping,
     * incl. the `sweep_include_cached`/`sweep_cached_gc_risk` fields WP 4d
     * added. Read-only endpoint; the writable half of this config lives at
     * [settings]/[patchSettings] (`sweep_include_cached` is one of its keys). */
    suspend fun schedule(): ScheduleOut = get("/v1/schedule")

    // ---- about (WP VER-2) ----------------------------------------------------

    /** `GET /v1/about` -- version and status of every component. A server
     * from before WP VER-2 has no such route and answers `404`
     * ([dev.steamvault.app.net.error.VaultApiError.NotFound]); the Settings
     * About section reads that as "server too old", not as an error. */
    suspend fun about(): AboutOut = get("/v1/about")

    // ---- steam relay (WP 4h.4; ADR-0004 second addendum) -----------------
    // The device-local Steam Web API key and its direct-to-Valve calls are
    // gone -- library/persona data now flows exclusively through this
    // client, same as everything else it wraps. `net/steam/
    // VaultRelayLibraryFetcher.kt` is the one production caller.

    /**
     * `GET /v1/steam/owned-games` (`vault_api/routers/steam.py::get_owned_games`).
     * `409` (no key configured server-side) and `422` (rejected `steamid`)
     * both surface as [dev.steamvault.app.net.error.VaultApiError.Validation]
     * (distinguished by `.status`) via [execute]'s normal error mapping —
     * no special handling needed here.
     */
    suspend fun steamOwnedGames(steamId64: String): OwnedGamesRelayOut =
        get("/v1/steam/owned-games", params = mapOf("steamid" to steamId64))

    /** `GET /v1/steam/player-summaries` (`vault_api/routers/steam.py::get_player_summaries`). */
    suspend fun steamPlayerSummaries(steamId64: String): PlayerSummariesRelayOut =
        get("/v1/steam/player-summaries", params = mapOf("steamid" to steamId64))

    // ---- plumbing -----------------------------------------------------

    private fun resolve(path: String, params: Map<String, String>): HttpUrl {
        val builder = baseUrl.newBuilder().encodedPath(path)
        for ((key, value) in params) builder.addQueryParameter(key, value)
        return builder.build()
    }

    private suspend inline fun <reified T> get(
        path: String,
        params: Map<String, String> = emptyMap(),
    ): T = execute("GET", path, params) { it.get() }

    private suspend inline fun <reified T> delete(path: String): T =
        execute("DELETE", path, emptyMap()) { it.delete() }

    private suspend inline fun <reified T, reified B> post(path: String, body: B): T {
        val encoded = VaultJson.encodeToString(body)
        return execute("POST", path, emptyMap()) { it.post(encoded.toRequestBody(JSON_MEDIA_TYPE)) }
    }

    private suspend inline fun <reified T> postEmpty(path: String): T =
        execute("POST", path, emptyMap()) { it.post(ByteArray(0).toRequestBody(null)) }

    private suspend inline fun <reified T> patch(path: String, body: JsonObject): T {
        val encoded = VaultJson.encodeToString(JsonObject.serializer(), body)
        return execute("PATCH", path, emptyMap()) { it.patch(encoded.toRequestBody(JSON_MEDIA_TYPE)) }
    }

    private suspend inline fun <reified T> execute(
        method: String,
        path: String,
        params: Map<String, String>,
        applyMethod: (Request.Builder) -> Request.Builder,
    ): T {
        val requestBuilder = Request.Builder()
            .url(resolve(path, params))
            .header("X-Api-Key", apiKeyProvider())
        val request = applyMethod(requestBuilder).build()

        val response = try {
            withContext(Dispatchers.IO) { client.newCall(request).execute() }
        } catch (e: IOException) {
            throw VaultApiError.Network("$method $path failed: ${e.message ?: "network error"}", cause = e)
        }

        response.use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw VaultApiError.forHttpStatus(resp.code, method, path, extractDetail(text))
            }
            if (text.isBlank()) {
                // No route in this client's surface returns an empty 204
                // body today; fail with a clear error rather than let
                // decodeFromString throw a confusing SerializationException
                // if one ever does.
                throw VaultApiError.Unknown("$method $path returned an empty body", resp.code)
            }
            return try {
                VaultJson.decodeFromString(text)
            } catch (e: SerializationException) {
                throw VaultApiError.Unknown(
                    "$method $path returned unparsable JSON", resp.code, cause = e,
                )
            }
        }
    }

    /**
     * Best-effort: vault-api's error bodies are `{"detail": "..."}`
     * (FastAPI's `HTTPException`) — extract that string if present, else
     * fall back to the raw body text. Never throws: a malformed error body
     * must not hide the ORIGINAL error behind a parsing exception.
     */
    private fun extractDetail(text: String): String? {
        if (text.isBlank()) return null
        return try {
            val obj = VaultJson.parseToJsonElement(text).jsonObject
            when (val d = obj["detail"]) {
                null -> text
                is JsonPrimitive -> d.contentOrNull ?: d.toString()
                else -> d.toString()
            }
        } catch (_: Exception) {
            text
        }
    }
}
