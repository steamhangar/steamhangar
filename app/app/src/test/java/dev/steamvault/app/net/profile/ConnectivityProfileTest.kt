package dev.steamvault.app.net.profile

import okhttp3.Call
import okhttp3.Connection
import okhttp3.Interceptor
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Pins the cleartext policy's two independent enforcement layers
 * (WP 4b.2 brief): [PublicDomainProfile]'s constructor guard, and
 * [CleartextPolicyInterceptor]'s OkHttp-level gate. Neither test that
 * exercises the rejection path touches a real socket or `MockWebServer` —
 * that IS the point being pinned ("must throw before any socket I/O").
 */
class ConnectivityProfileTest {

    // ---- SystemVpnProfile: cleartext allowed ---------------------------

    @Test
    fun `SystemVpnProfile allows a plain http LAN URL`() {
        val profile = SystemVpnProfile("http://10.0.0.50:8080")
        assertTrue(profile.allowsCleartext)
        assertEquals("http://10.0.0.50:8080", profile.baseUrl)
    }

    @Test
    fun `SystemVpnProfile also allows https, for a Tailscale MagicDNS name`() {
        val profile = SystemVpnProfile("https://vault.tailnet.ts.net")
        assertTrue(profile.allowsCleartext)
    }

    @Test
    fun `SystemVpnProfile rejects an unparsable URL`() {
        assertThrows(IllegalArgumentException::class.java) {
            SystemVpnProfile("not a url")
        }
    }

    // ---- PublicDomainProfile: cleartext refused, at construction -------

    @Test
    fun `PublicDomainProfile plus http throws before any socket IO`() {
        // No MockWebServer, no OkHttpClient anywhere in this test: the
        // whole point being pinned is that construction itself fails, so
        // nothing downstream ever gets the chance to open a socket.
        val exception = assertThrows(CleartextNotAllowedException::class.java) {
            PublicDomainProfile("http://vault.example.org")
        }
        assertTrue(exception.message!!.contains("https"))
    }

    @Test
    fun `PublicDomainProfile accepts https`() {
        val profile = PublicDomainProfile("https://vault.example.org")
        assertFalse(profile.allowsCleartext)
        assertEquals("https://vault.example.org", profile.baseUrl)
    }

    @Test
    fun `PublicDomainProfile rejects an unparsable URL with a different exception than the http case`() {
        assertThrows(IllegalArgumentException::class.java) {
            PublicDomainProfile("not a url")
        }
    }

    // ---- CleartextPolicyInterceptor: the second, independent layer ----
    // Exercised directly against a fake Interceptor.Chain -- no real
    // OkHttpClient, no MockWebServer -- so a passing "blocks http" test
    // proves the interceptor never reaches chain.proceed() (the call that
    // would actually open a socket), not merely that SOME exception
    // eventually surfaced from deeper in a real HTTP stack.

    @Test
    fun `interceptor blocks http for a profile that disallows it, never proceeding the chain`() {
        val profile = FakeProfile(allowsCleartext = false)
        val interceptor = CleartextPolicyInterceptor(profile)
        val request = Request.Builder().url("http://vault.example.org/v1/health").build()
        val chain = FakeChain(request)

        assertThrows(CleartextNotAllowedException::class.java) {
            interceptor.intercept(chain)
        }
        assertFalse("chain.proceed() must never run for a blocked request", chain.proceeded)
    }

    @Test
    fun `interceptor lets http through for a profile that allows it (SystemVpn)`() {
        val profile = FakeProfile(allowsCleartext = true)
        val interceptor = CleartextPolicyInterceptor(profile)
        val request = Request.Builder().url("http://10.0.0.50/v1/health").build()
        val chain = FakeChain(request)

        interceptor.intercept(chain)

        assertTrue(chain.proceeded)
    }

    @Test
    fun `interceptor lets https through regardless of the profile`() {
        val profile = FakeProfile(allowsCleartext = false)
        val interceptor = CleartextPolicyInterceptor(profile)
        val request = Request.Builder().url("https://vault.example.org/v1/health").build()
        val chain = FakeChain(request)

        interceptor.intercept(chain)

        assertTrue(chain.proceeded)
    }

    private class FakeProfile(override val allowsCleartext: Boolean) : ConnectivityProfile {
        override val baseUrl: String = "unused"
    }

    /** Minimal `Interceptor.Chain` fake — just enough surface for [CleartextPolicyInterceptor]. */
    private class FakeChain(private val request: Request) : Interceptor.Chain {
        var proceeded: Boolean = false
            private set

        override fun request(): Request = request

        override fun proceed(request: Request): Response {
            proceeded = true
            return Response.Builder()
                .request(request)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .build()
        }

        override fun connection(): Connection? = null
        override fun call(): Call = throw UnsupportedOperationException("not needed by this test")
        override fun connectTimeoutMillis(): Int = 0
        override fun withConnectTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain = this
        override fun readTimeoutMillis(): Int = 0
        override fun withReadTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain = this
        override fun writeTimeoutMillis(): Int = 0
        override fun withWriteTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain = this
    }
}
