package dev.steamvault.app.net

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.settingAsStringOrNull
import dev.steamvault.app.net.model.settingPatchValue
import dev.steamvault.app.net.profile.CleartextPolicyInterceptor
import dev.steamvault.app.net.profile.PublicDomainProfile
import dev.steamvault.app.net.profile.SystemVpnProfile
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager

/**
 * Transport-level behaviour of [VaultApiClient] against a real (fake)
 * server (WP 4b.2 brief: MockWebServer, pinned in
 * `gradle/libs.versions.toml`) — headers, method/path/body encoding, and
 * the HTTP-status -> [VaultApiError] mapping for 401/404/409/422/5xx plus
 * a genuine connection failure for `network`. Full DTO field fidelity is
 * `SerializationRoundTripTest`'s job, not this file's — these fixtures are
 * kept minimal on purpose.
 */
class VaultApiClientTest {

    private lateinit var server: MockWebServer
    private lateinit var client: VaultApiClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = VaultApiClient(
            profile = SystemVpnProfile(server.url("/").toString().trimEnd('/')),
            apiKeyProvider = { "test-key-123" },
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `health sends X-Api-Key even for the one unauthenticated route, and decodes the fixed body`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok"}"""))

        val result = client.health()

        assertEquals("ok", result.status)
        val recorded = server.takeRequest()
        assertEquals("test-key-123", recorded.getHeader("X-Api-Key"))
        assertEquals("/v1/health", recorded.path)
        assertEquals("GET", recorded.method)
    }

    @Test
    fun `games hits GET v1 games and decodes a list`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """[{"appid":440,"name":"Team Fortress 2","status":"idle",
                    |"last_prefill_at":null,"last_manifest_check":null,
                    |"depot_count":1,"size_bytes":null,"needs_force":false}]"""
                    .trimMargin(),
            ),
        )

        val games = client.games()

        assertEquals(1, games.size)
        assertEquals(440, games[0].appid)
        assertEquals("/v1/games", server.takeRequest().path)
    }

    @Test
    fun `jobs sends limit as a query parameter`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("[]"))

        client.jobs(limit = 50)

        val recorded = server.takeRequest()
        assertEquals("/v1/jobs?limit=50", recorded.path)
    }

    @Test
    fun `prefill POSTs the appids body and decodes the 202 response`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(202).setBody(
                """[{"appid":440,"job_id":1,"status":"queued","deduplicated":false}]""",
            ),
        )

        val refs = client.prefill(listOf(440))

        assertEquals(1, refs.size)
        assertFalse(refs[0].deduplicated)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/prefill", recorded.path)
        assertEquals("""{"appids":[440]}""", recorded.body.readUtf8())
    }

    @Test
    fun `prefillCached POSTs with no body, ever -- not even an empty appids list`() = runTest {
        // api/README.md: this route declares no body parameter at all, and
        // ANY body (including one shaped like PrefillRequest) is silently
        // accepted and ignored server-side -- the client-side contract this
        // pins is that it never even TRIES to send one, so a caller cannot
        // accidentally reintroduce the "looks scoped, queues everything"
        // trap by adding a body parameter to prefillCached() later.
        server.enqueue(
            MockResponse().setResponseCode(202).setBody(
                """[{"appid":440,"job_id":1,"status":"queued","deduplicated":false}]""",
            ),
        )

        val refs = client.prefillCached()

        assertEquals(1, refs.size)
        assertFalse(refs[0].deduplicated)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/prefill/cached", recorded.path)
        assertEquals(0L, recorded.bodySize)
        // N4 (Opus review on this WP): assert the wire-level shape a
        // throwaway probe verified by hand, so it stays pinned instead of
        // relying on a review that no longer exists once this lands.
        assertEquals("test-key-123", recorded.getHeader("X-Api-Key"))
        assertEquals("0", recorded.getHeader("Content-Length"))
        // ByteArray(0).toRequestBody(null) (postEmpty's body, no MediaType)
        // means OkHttp adds no Content-Type header at all -- distinct from
        // "empty string": a caller checking for an ABSENT header, not a
        // blank one.
        assertNull(recorded.getHeader("Content-Type"))
        // Redirect posture matters here specifically because there is no
        // trailing slash on this route: api/README.md documents a 307 on
        // the slash form, and this client's followRedirects(false) means it
        // would never follow that redirect to begin with -- pin the
        // configuration directly rather than only via an end-to-end
        // redirect scenario (same S1b technique WP 4b.2's review required).
        assertFalse(client.debugHttpClientForTesting.followRedirects)
        assertFalse(client.debugHttpClientForTesting.followSslRedirects)
    }

    @Test
    fun `prefillCached decodes an empty selection as a normal 202, not an error`() = runTest {
        server.enqueue(MockResponse().setResponseCode(202).setBody("[]"))

        val refs = client.prefillCached()

        assertTrue(refs.isEmpty())
    }

    @Test
    fun `gc defaults to a dry run body when execute is omitted`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(202).setBody(
                """{"appid":440,"job_id":7,"status":"queued","type":"gc","mode":"dry-run","execute":false,"deduplicated":false}""",
            ),
        )

        val ref = client.gc(440)

        assertEquals("dry-run", ref.mode)
        val recorded = server.takeRequest()
        assertEquals("/v1/cache/440/gc", recorded.path)
        assertEquals("""{"execute":false}""", recorded.body.readUtf8())
    }

    @Test
    fun `pauseJob POSTs with no body`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"job_id":1,"status":"running","outcome":"requested","detail":"pause requested"}""",
            ),
        )

        val result = client.pauseJob(1)

        assertEquals("requested", result.outcome)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/jobs/1/pause", recorded.path)
        assertEquals(0L, recorded.bodySize)
    }

    @Test
    fun `cancelJob issues a bare DELETE`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"job_id":1,"status":"cancelled","outcome":"immediate","detail":"job cancelled"}""",
            ),
        )

        client.cancelJob(1)

        val recorded = server.takeRequest()
        assertEquals("DELETE", recorded.method)
        assertEquals("/v1/jobs/1", recorded.path)
    }

    @Test
    fun `patchSettings encodes a mixed set-and-clear body`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"readonly":false,"settings":[]}"""),
        )

        client.patchSettings(
            linkedMapOf(
                "auto_gc" to settingPatchValue("dry-run"),
                "vault_name" to null,
            ),
        )

        val recorded = server.takeRequest()
        assertEquals("PATCH", recorded.method)
        assertEquals("/v1/settings", recorded.path)
        assertEquals("""{"auto_gc":"dry-run","vault_name":null}""", recorded.body.readUtf8())
    }

    @Test
    fun `settings GET round-trips readonly and a null effective value`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"readonly":true,"settings":[{"key":"schedule_window","effective":null,"source":"default","fallback":null,"applies":"next_sweep","env_only":false}]}""",
            ),
        )

        val result = client.settings()

        assertTrue(result.readonly)
        assertNull(result.settings[0].effective.settingAsStringOrNull())
        assertEquals(JsonNull, result.settings[0].effective)
    }

    // ---- steam relay (WP 4h.4; ADR-0004 second addendum) -----------------

    @Test
    fun `steamOwnedGames GETs v1 steam owned-games with the steamid query param and decodes the games list`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"configured":true,"game_count":1,
                    |"games":[{"appid":440,"name":"Team Fortress 2","img_icon_url":"abc"}]}"""
                    .trimMargin(),
            ),
        )

        val result = client.steamOwnedGames("76561198042117903")

        assertEquals(1, result.games.size)
        assertEquals(440, result.games[0].appid)
        // WP 4h.0's default-gate shape: the key is textually absent on the
        // wire, which must decode as null, never a manufactured 0.
        assertNull(result.games[0].playtime_forever)
        // The request must land on THIS server (standing in for the vault)
        // -- a bounded wait so a regression that pointed the method at
        // api.steampowered.com instead fails this assertion instead of
        // hanging indefinitely.
        val recorded = server.takeRequest(2, TimeUnit.SECONDS)
        assertEquals("/v1/steam/owned-games?steamid=76561198042117903", recorded?.path)
        assertEquals("test-key-123", recorded?.getHeader("X-Api-Key"))
    }

    @Test
    fun `steamOwnedGames 409 maps to Validation -- no relay key configured server-side`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(409).setBody(
                """{"detail":"Steam relay is not configured. Set a Web API key via PUT /v1/steam/key."}""",
            ),
        )

        try {
            client.steamOwnedGames("76561198042117903")
            fail("expected VaultApiError.Validation")
        } catch (e: VaultApiError.Validation) {
            assertEquals(409, e.status)
        }
    }

    @Test
    fun `steamOwnedGames 422 maps to Validation -- a rejected steamid`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(422).setBody(
                """{"detail":"steamid must be a 17-digit SteamID64 in the individual-account range."}""",
            ),
        )

        try {
            client.steamOwnedGames("not-a-steamid")
            fail("expected VaultApiError.Validation")
        } catch (e: VaultApiError.Validation) {
            assertEquals(422, e.status)
        }
    }

    @Test
    fun `steamPlayerSummaries GETs v1 steam player-summaries and decodes the players list`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"configured":true,"players":[{"steamid":"76561198042117903","personaname":"Example"}]}""",
            ),
        )

        val result = client.steamPlayerSummaries("76561198042117903")

        assertEquals(1, result.players.size)
        assertEquals("Example", result.players[0].personaname)
        val recorded = server.takeRequest(2, TimeUnit.SECONDS)
        assertEquals("/v1/steam/player-summaries?steamid=76561198042117903", recorded?.path)
    }

    // ---- canary-key redaction (WP 4h.4 review nitpick) -------------------
    //
    // Restores, as ONE test covering four failure modes, the guarantee the
    // deleted SteamWebApiClientTest's three MUTATION PIN tests used to pin
    // by construction for the OLD device-local key (which lived in the
    // query string, not a header, so the two are not structurally
    // identical -- see VaultApiClient.kt's own kdoc on `execute()` for why
    // this client's error paths never format a header value into a
    // message at all): the vault-api key is a HEADER
    // (`X-Api-Key`), never the query string, on the relay routes exactly
    // like every other route this client wraps. This test does not rely
    // on that "by construction" reasoning alone -- it plants a canary key
    // and asserts it is absent from the resulting VaultApiError message
    // across a non-2xx response, a genuine network failure, and a
    // garbage (non-JSON) 200 body, so a future regression that DID start
    // interpolating request/response details into a message would be
    // caught here even if it happened to also leave the "it's a header,
    // not a query param" reasoning intact.

    private val canaryApiKey = "CANARY-KEY-MUST-NEVER-APPEAR-IN-A-VAULTAPIERROR-MESSAGE"

    @Test
    fun `MUTATION PIN -- a canary X-Api-Key never appears in a VaultApiError message across 4xx, 5xx, network, or garbage-body failures on the steam relay routes`() = runTest {
        val canaryClient = VaultApiClient(
            profile = SystemVpnProfile(server.url("/").toString().trimEnd('/')),
            apiKeyProvider = { canaryApiKey },
        )

        // 4xx
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"not configured"}"""))
        val e409 = runCatching { canaryClient.steamOwnedGames("76561198042117903") }.exceptionOrNull()
        assertFalse(e409?.message.orEmpty().contains(canaryApiKey))

        // 5xx
        server.enqueue(MockResponse().setResponseCode(502).setBody("upstream error, not json"))
        val e502 = runCatching { canaryClient.steamOwnedGames("76561198042117903") }.exceptionOrNull()
        assertFalse(e502?.message.orEmpty().contains(canaryApiKey))

        // garbage (non-JSON) 200 body
        server.enqueue(MockResponse().setResponseCode(200).setBody("not json at all"))
        val eGarbage = runCatching { canaryClient.steamOwnedGames("76561198042117903") }.exceptionOrNull()
        assertFalse(eGarbage?.message.orEmpty().contains(canaryApiKey))

        // network failure: nothing listening on this port
        val deadServer = MockWebServer().apply { start() }
        val deadPort = deadServer.port
        deadServer.shutdown()
        val deadClient = VaultApiClient(
            profile = SystemVpnProfile("http://127.0.0.1:$deadPort"),
            apiKeyProvider = { canaryApiKey },
        )
        val eNetwork = runCatching { deadClient.steamOwnedGames("76561198042117903") }.exceptionOrNull()
        assertFalse(eNetwork?.message.orEmpty().contains(canaryApiKey))

        // Defense in depth, mirroring the deleted SteamWebApiClientTest's
        // own pattern: the key DID legitimately have to reach the wire for
        // the 4xx/5xx/garbage cases above (that is the whole point of
        // X-Api-Key) -- confirm it as a header, never leaked into a path.
        var sawCanaryHeader = false
        var recorded = server.takeRequest(1, TimeUnit.SECONDS)
        while (recorded != null) {
            if (recorded.getHeader("X-Api-Key") == canaryApiKey) sawCanaryHeader = true
            assertFalse(recorded.path.orEmpty().contains(canaryApiKey))
            recorded = server.takeRequest(1, TimeUnit.SECONDS)
        }
        assertTrue("expected at least one recorded request to legitimately carry the canary as X-Api-Key", sawCanaryHeader)
    }

    // ---- error mapping --------------------------------------------------

    @Test
    fun `401 maps to Auth with the response detail`() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"missing X-Api-Key"}"""))

        try {
            client.games()
            fail("expected VaultApiError.Auth")
        } catch (e: VaultApiError.Auth) {
            assertEquals("auth", e.kind)
            assertEquals(401, e.status)
            assertEquals("missing X-Api-Key", e.detail)
        }
    }

    @Test
    fun `404 maps to NotFound`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(404).setBody("""{"detail":"Unknown appid 999999"}"""),
        )

        try {
            client.game(999999)
            fail("expected VaultApiError.NotFound")
        } catch (e: VaultApiError.NotFound) {
            assertEquals("not_found", e.kind)
            assertEquals("Unknown appid 999999", e.detail)
        }
    }

    @Test
    fun `409 maps to Validation, matching the shared web taxonomy fold`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(409).setBody("""{"detail":"Prefill job 1 for app 440 is queued."}"""),
        )

        try {
            client.deleteCache(440)
            fail("expected VaultApiError.Validation")
        } catch (e: VaultApiError.Validation) {
            assertEquals("validation", e.kind)
            assertEquals(409, e.status)
        }
    }

    @Test
    fun `422 maps to Validation`() = runTest {
        server.enqueue(MockResponse().setResponseCode(422).setBody("""{"detail":"appid must be >= 1"}"""))

        try {
            client.game(0)
            fail("expected VaultApiError.Validation")
        } catch (e: VaultApiError.Validation) {
            assertEquals(422, e.status)
        }
    }

    @Test
    fun `500 maps to Server, and a non-JSON body falls back to the raw text as detail`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(500).setBody("internal server error, not json"),
        )

        try {
            client.games()
            fail("expected VaultApiError.Server")
        } catch (e: VaultApiError.Server) {
            assertEquals("server", e.kind)
            assertEquals("internal server error, not json", e.detail)
        }
    }

    // ---- Redirect-based key leak (Opus review, BLOCKER B1 + delta S1/S2) --
    //
    // `MockWebServer.url()` derives its host from a REVERSE DNS lookup of
    // the loopback address, which on this project's dev machine resolves
    // to "lancache.steamcontent.com" (the lancache DNS override
    // core/vault-core's own PoC relies on) rather than "localhost" --
    // measured while writing the first of these tests, and the reason a
    // certificate SAN of "localhost" would otherwise stop matching. Every
    // test below builds hop URLs explicitly against "localhost:<port>"
    // instead of trusting `.url()`'s host.

    /**
     * One HeldCertificate ("localhost") plus the two SEPARATE
     * HandshakeCertificates OkHttp's own canonical self-signed-loopback
     * testing recipe uses: the server side holds the identity it presents,
     * the client side holds that SAME certificate as a TRUSTED issuer.
     * Reusing one instance for both roles is wrong -- `.heldCertificate(...)`
     * alone configures identity with an EMPTY trust store, so a client
     * built from it fails the handshake with "the trustAnchors parameter
     * must be non-empty" before any request is even sent (measured while
     * writing the first version of this test).
     */
    private class TlsFixture {
        private val heldCertificate = HeldCertificate.Builder()
            .addSubjectAlternativeName("localhost")
            .build()
        val serverSslSocketFactory: SSLSocketFactory =
            HandshakeCertificates.Builder().heldCertificate(heldCertificate).build().sslSocketFactory()
        private val clientCertificates: HandshakeCertificates =
            HandshakeCertificates.Builder().addTrustedCertificate(heldCertificate.certificate).build()
        val clientSslSocketFactory: SSLSocketFactory = clientCertificates.sslSocketFactory()
        val clientTrustManager: X509TrustManager = clientCertificates.trustManager
    }

    private fun newHttpsServer(tls: TlsFixture): MockWebServer =
        MockWebServer().apply {
            useHttps(tls.serverSslSocketFactory, false)
            start()
        }

    @Test
    fun `https to http redirect never reaches hop 2 for PublicDomainProfile -- canary key stays off the wire`() = runTest {
        val tls = TlsFixture()
        // hop 1: a real HTTPS MockWebServer -- PublicDomainProfile's own
        // constructor guard requires https, so the redirect source has to
        // genuinely be HTTPS for this to be the scenario the reviewer
        // demonstrated (not merely a plain-HTTP-to-plain-HTTP redirect,
        // which SystemVpnProfile allows on both ends anyway).
        val hop1 = newHttpsServer(tls)
        val hop2 = MockWebServer().apply { start() } // plain HTTP: the attacker-controlled downgrade target
        try {
            val hop1Base = "https://localhost:${hop1.port}"
            val hop2HealthUrl = "http://localhost:${hop2.port}/v1/health"

            hop1.enqueue(
                MockResponse()
                    .setResponseCode(302)
                    .setHeader("Location", hop2HealthUrl),
            )
            // hop2 would answer normally if it were EVER reached -- it must not be.
            hop2.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok"}"""))

            // A client that trusts hop1's self-signed test certificate but is
            // otherwise UNPINNED -- specifically, built WITHOUT
            // followSslRedirects(false)/followRedirects(false) itself, so this
            // test proves VaultApiClient re-applies those settings on its OWN
            // wrapping OkHttpClient rather than merely relying on
            // defaultOkHttpClient() having them (see VaultApiClient.kt's
            // `client` field kdoc).
            val trustingButUnpinnedOkHttpClient = OkHttpClient.Builder()
                .sslSocketFactory(tls.clientSslSocketFactory, tls.clientTrustManager)
                .build()

            val canaryKey = "canary-secret-must-never-leave-hop-1"
            val client = VaultApiClient(
                profile = PublicDomainProfile(hop1Base),
                apiKeyProvider = { canaryKey },
                okHttpClient = trustingButUnpinnedOkHttpClient,
            )

            var threw = false
            try {
                client.health()
            } catch (e: Exception) {
                // Either a VaultApiError wrapping the raw, un-followed 302
                // (what followSslRedirects(false)/followRedirects(false)
                // produce: OkHttp returns the redirect response as terminal
                // instead of building hop 2's request) or
                // CleartextNotAllowedException (the network-interceptor
                // backstop) are both an acceptable outcome here -- what this
                // test pins is what happened ON THE WIRE, asserted below, not
                // which exception type surfaced. (Each layer is pinned
                // STANDALONE separately, below.)
                threw = true
            }
            assertTrue("expected the redirect to be refused, not silently followed to hop2", threw)

            assertEquals("hop1 must receive exactly the one original request", 1, hop1.requestCount)
            assertEquals("hop2 must NEVER receive a request", 0, hop2.requestCount)

            // Defense in depth, per the review's explicit ask: even if a
            // future regression made hop2.requestCount nonzero, the canary key
            // must never appear on anything hop2 recorded. A short bounded
            // wait (not takeRequest()'s indefinite block) because the correct,
            // passing outcome is that NOTHING ever arrives.
            val strayRequest = hop2.takeRequest(200, TimeUnit.MILLISECONDS)
            assertNull("hop2 must never have received any request at all", strayRequest)
        } finally {
            // S3 (delta review): a failing assertion above must not leak
            // these servers for the rest of the test JVM fork's lifetime.
            hop1.shutdown()
            hop2.shutdown()
        }
    }

    @Test
    fun `S2 -- https to https CROSS-HOST redirect never reaches hop 2 either, same scheme or not`() = runTest {
        // followSslRedirects(false) alone only refuses a SCHEME change
        // (https<->http) -- an https-to-https redirect to a DIFFERENT HOST
        // (here: a different port, which is all a loopback test can vary)
        // is a same-scheme redirect that only plain followRedirects(false)
        // refuses. X-Api-Key is not an Authorization-class header, so
        // OkHttp does not strip it on the host change either -- an
        // unpinned client would forward the canary key to hop2 exactly as
        // in the downgrade case.
        val tls = TlsFixture()
        val hop1 = newHttpsServer(tls)
        val hop2 = newHttpsServer(tls) // SAME cert is fine: both present as "localhost", only the port differs
        try {
            val hop1Base = "https://localhost:${hop1.port}"
            val hop2HealthUrl = "https://localhost:${hop2.port}/v1/health"

            hop1.enqueue(
                MockResponse()
                    .setResponseCode(302)
                    .setHeader("Location", hop2HealthUrl),
            )
            hop2.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok"}"""))

            val trustingButUnpinnedOkHttpClient = OkHttpClient.Builder()
                .sslSocketFactory(tls.clientSslSocketFactory, tls.clientTrustManager)
                .build()

            val canaryKey = "canary-secret-must-never-leave-hop-1-cross-host"
            val client = VaultApiClient(
                profile = PublicDomainProfile(hop1Base),
                apiKeyProvider = { canaryKey },
                okHttpClient = trustingButUnpinnedOkHttpClient,
            )

            var threw = false
            try {
                client.health()
            } catch (e: Exception) {
                threw = true
            }
            assertTrue("expected the cross-host redirect to be refused, not silently followed to hop2", threw)

            assertEquals("hop1 must receive exactly the one original request", 1, hop1.requestCount)
            assertEquals("hop2 must NEVER receive a request", 0, hop2.requestCount)
            assertNull(
                "hop2 must never have received any request at all",
                hop2.takeRequest(200, TimeUnit.MILLISECONDS),
            )
        } finally {
            hop1.shutdown()
            hop2.shutdown()
        }
    }

    @Test
    fun `S1a -- CleartextPolicyInterceptor alone (network interceptor, redirect flags left at OkHttp defaults) still blocks the downgrade`() = runTest {
        // Isolates the interceptor's claim from the flag layer entirely:
        // this hand-built client carries ONLY CleartextPolicyInterceptor
        // (as a network interceptor) and leaves BOTH followSslRedirects and
        // followRedirects at OkHttp's own insecure default (explicit
        // `true` below, so this test cannot pass because the OTHER layer
        // silently helped). Per the delta review: the combined end-to-end
        // test above cannot tell "the interceptor works" apart from "the
        // flags happened to cover for it" -- this is the standalone proof.
        val tls = TlsFixture()
        val hop1 = newHttpsServer(tls)
        val hop2 = MockWebServer().apply { start() } // plain HTTP downgrade target
        try {
            val hop1Url = "https://localhost:${hop1.port}/v1/health"
            val hop2Url = "http://localhost:${hop2.port}/v1/health"

            hop1.enqueue(MockResponse().setResponseCode(302).setHeader("Location", hop2Url))
            hop2.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok"}"""))

            val profile = PublicDomainProfile("https://localhost:${hop1.port}")
            val canaryKey = "canary-secret-interceptor-alone"

            val rawClient = OkHttpClient.Builder()
                .sslSocketFactory(tls.clientSslSocketFactory, tls.clientTrustManager)
                .followSslRedirects(true) // OkHttp's own default, restated explicitly
                .followRedirects(true) // same
                .addNetworkInterceptor(CleartextPolicyInterceptor(profile))
                .build()

            val request = Request.Builder().url(hop1Url).header("X-Api-Key", canaryKey).build()

            var threw = false
            try {
                rawClient.newCall(request).execute()
            } catch (e: Exception) {
                threw = true
            }
            assertTrue("expected the network interceptor alone to block the downgrade", threw)

            // The interceptor throws AFTER OkHttp's ConnectInterceptor has
            // already opened a socket to hop2 (network interceptors run
            // downstream of connection setup) but BEFORE CallServerInterceptor
            // writes any HTTP request bytes -- so a raw TCP connection MAY
            // open, but hop2's dispatcher never records a request. That is
            // exactly what is asserted here, not "zero sockets" (that
            // stronger guarantee is the flag layer's and the constructor
            // guard's job, pinned separately).
            assertNull(
                "hop2 must never receive a fully-formed HTTP request, even though " +
                    "followSslRedirects/followRedirects(true) would otherwise have tried to send one",
                hop2.takeRequest(200, TimeUnit.MILLISECONDS),
            )
        } finally {
            hop1.shutdown()
            hop2.shutdown()
        }
    }

    @Test
    fun `S1b -- VaultApiClient's built OkHttpClient has both redirect flags disabled (config assertion)`() {
        // Isolates the FLAG layer's claim from the interceptor entirely: no
        // redirect ever happens in this test, no MockWebServer is even
        // started -- this only inspects the client's own configuration.
        // Uses the default OkHttpClient (no override passed), so this is
        // also the first test that observes defaultOkHttpClient()'s own
        // copy of these flags rather than always overriding it.
        val client = VaultApiClient(
            profile = SystemVpnProfile("http://192.168.1.50:8080"),
            apiKeyProvider = { "unused" },
        )

        val httpClient = client.debugHttpClientForTesting

        assertFalse(
            "followRedirects must be false -- no redirect is ever a legitimate outcome for this client",
            httpClient.followRedirects,
        )
        assertFalse("followSslRedirects must be false", httpClient.followSslRedirects)
    }

    @Test
    fun `an unreachable server maps to Network`() = runTest {
        val deadServer = MockWebServer()
        deadServer.start()
        val port = deadServer.port
        deadServer.shutdown() // nothing is listening on `port` anymore

        val deadClient = VaultApiClient(
            profile = SystemVpnProfile("http://127.0.0.1:$port"),
            apiKeyProvider = { "k" },
        )

        try {
            deadClient.health()
            fail("expected VaultApiError.Network")
        } catch (e: VaultApiError.Network) {
            assertEquals("network", e.kind)
            assertNull(e.status)
        }
    }

    // ---- WP UPD-3: update check ---------------------------------------

    @Test
    fun `checkUpdates POSTs v1 updates check with no body and decodes 202 started`() = runTest {
        server.enqueue(MockResponse().setResponseCode(202).setBody("""{"status":"started","enabled":true}"""))

        val out = client.checkUpdates()

        assertEquals("started", out.status)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/updates/check", recorded.path)
        assertEquals(0L, recorded.bodySize)
        assertEquals("test-key-123", recorded.getHeader("X-Api-Key"))
    }

    @Test
    fun `checkUpdates decodes 200 disabled as a normal answer, not an error`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"disabled","enabled":false}"""))

        val out = client.checkUpdates()

        assertEquals("disabled", out.status)
        assertFalse(out.enabled)
    }

    @Test
    fun `updatesStatus GETs v1 updates status, and a pre-UPD-1 server's 404 is NotFound`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"enabled":true,"scheduled":false,"running":true,"last_trigger":"manual",""" +
                    """"last_started_at":"2026-10-13T10:00:00Z","last_finished_at":null,"last_outcome":null,""" +
                    """"last_apps":null,"last_ok":null,"last_failed":null,"last_stale":null}""",
            ),
        )
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))

        assertTrue(client.updatesStatus().running)
        val recorded = server.takeRequest()
        assertEquals("GET", recorded.method)
        assertEquals("/v1/updates/status", recorded.path)
        try {
            client.updatesStatus()
            fail("expected VaultApiError.NotFound")
        } catch (_: VaultApiError.NotFound) {
            // expected
        }
    }
}
