package dev.steamvault.app.net

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.profile.SystemVpnProfile
import dev.steamvault.app.ui.clients.logic.isClientAlreadyGone
import dev.steamvault.app.ui.clients.logic.isNameAlreadyGone
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * WP ANDROID-GAPS-1: the four client-management calls over a real HTTP
 * round trip -- method, path encoding, body, the `204` success without a
 * body, and the error mapping the UI's "already gone" checks rely on.
 */
class ClientGapsApiTest {
    private lateinit var server: MockWebServer
    private lateinit var client: VaultApiClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = VaultApiClient(SystemVpnProfile(server.url("/").toString().trimEnd('/')), apiKeyProvider = { "k" })
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `MUTATION PIN -- deleteClient sends DELETE with the id as one encoded segment and accepts an empty 204`() = runTest {
        server.enqueue(MockResponse().setResponseCode(204))
        client.deleteClient("living room #1?")
        val req = server.takeRequest()
        assertEquals("DELETE", req.method)
        assertEquals("/v1/clients/living%20room%20%231%3F", req.path)
        assertEquals("k", req.getHeader("X-Api-Key"))
    }

    @Test
    fun `deleteClient maps the handler's 404 to an already-gone error and a bare 404 to a real one`() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Unknown client_id 'gaming-pc'"}"""))
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
        try {
            client.deleteClient("gaming-pc")
            fail("expected a 404")
        } catch (e: VaultApiError) {
            assertTrue(e is VaultApiError.NotFound)
            assertTrue(isClientAlreadyGone(e))
        }
        try {
            client.deleteClient("gaming-pc")
            fail("expected a 404")
        } catch (e: VaultApiError) {
            assertTrue(!isClientAlreadyGone(e))
            assertEquals("Not Found", e.detail)
        }
    }

    @Test
    fun `setClientName PUTs exactly the name body to the encoded address`() = runTest {
        server.enqueue(
            MockResponse().setBody("""{"key_type":"ip","key":"fd00::7","name":"Desk","updated_at":"2026-10-31T10:00:00Z"}"""),
        )
        client.setClientName("fd00::7", "Desk")
        val req = server.takeRequest()
        assertEquals("PUT", req.method)
        assertEquals("/v1/client-names/ip/fd00::7", req.path)
        assertEquals("""{"name":"Desk"}""", req.body.readUtf8())
        assertTrue(req.getHeader("Content-Type")!!.startsWith("application/json"))
    }

    @Test
    fun `clearClientName DELETEs and keeps the server's 404 detail`() = runTest {
        server.enqueue(MockResponse().setResponseCode(204))
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"No name stored for '10.0.0.57'"}"""))
        client.clearClientName("10.0.0.57")
        val req = server.takeRequest()
        assertEquals("DELETE", req.method)
        assertEquals("/v1/client-names/ip/10.0.0.57", req.path)
        try {
            client.clearClientName("10.0.0.57")
            fail("expected a 404")
        } catch (e: VaultApiError) {
            assertTrue(isNameAlreadyGone(e))
        }
    }

    @Test
    fun `deleteHistory sends the scope and decodes the answer`() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """{"scope":"older-than-30d","cutoff":"2026-09-14T08:00:00Z","jobs_deleted":12,
                "traffic_rows_deleted":{"client_traffic":840,"client_depot_traffic":2210,"server_traffic":31}}""",
            ),
        )
        server.enqueue(MockResponse().setBody("""{"scope":"all","cutoff":null,"jobs_deleted":0,"traffic_rows_deleted":{"client_traffic":0,"client_depot_traffic":0,"server_traffic":0}}"""))
        val older = client.deleteHistory("older-than-30d")
        val req = server.takeRequest()
        assertEquals("DELETE", req.method)
        assertEquals("/v1/history?scope=older-than-30d", req.path)
        assertEquals(12, older.jobs_deleted)
        assertEquals(2210, older.traffic_rows_deleted.client_depot_traffic)
        val all = client.deleteHistory("all")
        assertEquals("/v1/history?scope=all", server.takeRequest().path)
        assertNull(all.cutoff)
        assertEquals("all", all.scope)
    }

    @Test
    fun `deleteHistory on a server older than D15-NAMES is a NotFound`() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
        try {
            client.deleteHistory("all")
            fail("expected a 404")
        } catch (e: VaultApiError) {
            assertTrue(e is VaultApiError.NotFound)
        }
    }
}
