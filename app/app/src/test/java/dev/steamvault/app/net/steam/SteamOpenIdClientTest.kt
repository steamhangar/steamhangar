package dev.steamvault.app.net.steam

import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager

/**
 * `check_authentication` behaviour (WP brief: "MockWebServer, is_valid:
 * true/false/garbage/redirect-refused"). [SteamOpenIdClient] takes an
 * injectable `loginUrl` specifically so this file can point it at a local
 * `MockWebServer` -- production code (`SteamIdentityRepositoryImpl`'s
 * default constructor argument) never overrides it; see [hostPin] for the
 * literal production-value pin, kept in a SEPARATE test from all the
 * behavioural ones below (same split `VaultApiClientTest`'s S1b uses for
 * "config assertion" vs. "behaviour against a real socket").
 */
class SteamOpenIdClientTest {

    private lateinit var server: MockWebServer

    @After
    fun tearDown() {
        if (::server.isInitialized) server.shutdown()
    }

    private val sampleParams = mapOf(
        "openid.mode" to "id_res",
        "openid.claimed_id" to "https://steamcommunity.com/openid/id/76561198042117903",
        "openid.identity" to "https://steamcommunity.com/openid/id/76561198042117903",
        "openid.return_to" to SteamOpenIdConfig.APP_LINK,
        "openid.signed" to "signed,claimed_id,identity,return_to",
        "openid.sig" to "Zm9vYmFy",
    )

    // ---- host pin (config only, no network) --------------------------------

    @Test
    fun `hostPin -- the production login endpoint is the literal Valve URL`() {
        assertEquals("https://steamcommunity.com/openid/login", SteamOpenIdConfig.LOGIN_ENDPOINT)
        assertEquals("steamcommunity.com", SteamOpenIdConfig.STEAM_HOST)
    }

    // ---- is_valid variants --------------------------------------------------

    @Test
    fun `is_valid true is accepted`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(200).setBody("ns:http://specs.openid.net/auth/2.0\nis_valid:true\n"))
        val client = SteamOpenIdClient(loginUrl = server.url("/openid/login").toString())

        assertTrue(client.checkAuthentication(sampleParams))

        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        val body = recorded.body.readUtf8()
        assertTrue("mode must be overridden to check_authentication", body.contains("openid.mode=check_authentication"))
        assertFalse("the original id_res mode must not be sent", body.contains("openid.mode=id_res"))
    }

    @Test
    fun `is_valid false is rejected`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(200).setBody("ns:http://specs.openid.net/auth/2.0\nis_valid:false\n"))
        val client = SteamOpenIdClient(loginUrl = server.url("/openid/login").toString())

        assertFalse(client.checkAuthentication(sampleParams))
    }

    @Test
    fun `garbage body is rejected`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(200).setBody("<html>not an openid response</html>"))
        val client = SteamOpenIdClient(loginUrl = server.url("/openid/login").toString())

        assertFalse(client.checkAuthentication(sampleParams))
    }

    @Test
    fun `empty body is rejected`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(200).setBody(""))
        val client = SteamOpenIdClient(loginUrl = server.url("/openid/login").toString())

        assertFalse(client.checkAuthentication(sampleParams))
    }

    @Test
    fun `a non-2xx status is rejected even with a true-looking body`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(500).setBody("is_valid:true"))
        val client = SteamOpenIdClient(loginUrl = server.url("/openid/login").toString())

        assertFalse(client.checkAuthentication(sampleParams))
    }

    @Test
    fun `an oversized response is refused rather than fully buffered`() = runTest {
        server = MockWebServer().apply { start() }
        // Padding first, real answer last -- the bounded read must cut this
        // off before ever seeing the "is_valid:true" line, so a naive
        // "read everything then parse" implementation would wrongly accept
        // this while the bounded one correctly rejects it.
        val padding = "x".repeat((SteamOpenIdClient.MAX_RESPONSE_BYTES + 1024).toInt())
        server.enqueue(MockResponse().setResponseCode(200).setBody("$padding\nis_valid:true\n"))
        val client = SteamOpenIdClient(loginUrl = server.url("/openid/login").toString())

        assertFalse(client.checkAuthentication(sampleParams))
    }

    @Test
    fun `an unreachable server maps to false, not an exception`() = runTest {
        val deadServer = MockWebServer().apply { start() }
        val port = deadServer.port
        deadServer.shutdown()
        val client = SteamOpenIdClient(loginUrl = "http://127.0.0.1:$port/openid/login")

        assertFalse(client.checkAuthentication(sampleParams))
    }

    // ---- redirect refusal (same posture as WP 4b.2's VaultApiClient) ------

    private class TlsFixture {
        private val heldCertificate = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val serverSslSocketFactory: SSLSocketFactory =
            HandshakeCertificates.Builder().heldCertificate(heldCertificate).build().sslSocketFactory()
        private val clientCertificates: HandshakeCertificates =
            HandshakeCertificates.Builder().addTrustedCertificate(heldCertificate.certificate).build()
        val clientSslSocketFactory: SSLSocketFactory = clientCertificates.sslSocketFactory()
        val clientTrustManager: X509TrustManager = clientCertificates.trustManager
    }

    @Test
    fun `a redirect response is refused, never followed to a second host`() = runTest {
        val tls = TlsFixture()
        val hop1 = MockWebServer().apply { useHttps(tls.serverSslSocketFactory, false); start() }
        val hop2 = MockWebServer().apply { start() }
        try {
            hop1.enqueue(
                MockResponse().setResponseCode(302).setHeader("Location", "http://localhost:${hop2.port}/openid/login"),
            )
            hop2.enqueue(MockResponse().setResponseCode(200).setBody("is_valid:true"))

            val trustingClient = OkHttpClient.Builder()
                .sslSocketFactory(tls.clientSslSocketFactory, tls.clientTrustManager)
                .build()
            val client = SteamOpenIdClient(
                loginUrl = "https://localhost:${hop1.port}/openid/login",
                okHttpClient = trustingClient,
            )

            assertFalse(client.checkAuthentication(sampleParams))
            assertEquals(1, hop1.requestCount)
            assertEquals(0, hop2.requestCount)
            assertNull(hop2.takeRequest(200, TimeUnit.MILLISECONDS))
        } finally {
            hop1.shutdown()
            hop2.shutdown()
        }
    }

    @Test
    fun `S1b -- SteamOpenIdClient's built OkHttpClient has both redirect flags disabled`() {
        val client = SteamOpenIdClient()
        val httpClient = client.debugHttpClientForTesting
        assertFalse(httpClient.followRedirects)
        assertFalse(httpClient.followSslRedirects)
    }

    // ---- isValidTrueStrict (internal, pure) --------------------------------

    @Test
    fun `MUTATION PIN -- isValidTrueStrict requires an exact line match, not a substring`() {
        assertTrue(isValidTrueStrict("ns:foo\nis_valid:true"))
        assertFalse(isValidTrueStrict("is_valid:trueXYZ"))
        assertFalse(isValidTrueStrict("Xis_valid:true"))
        assertFalse(isValidTrueStrict("is_valid: true"))
        assertFalse(isValidTrueStrict(""))
        assertFalse(isValidTrueStrict("some garbage that never mentions the field"))
    }

    @Test
    fun `isValidTrueStrict tolerates surrounding whitespace and CRLF line endings`() {
        assertTrue(isValidTrueStrict("ns:foo\r\nis_valid:true\r\n"))
        assertTrue(isValidTrueStrict("  is_valid:true  "))
    }
}
