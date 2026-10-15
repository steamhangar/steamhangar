package dev.steamvault.app.net

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.profile.SystemVpnProfile
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** WP D19 (app): the two card-info reads reach the right paths and decode the current wire shape. */
class CardInfoApiTest {

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
    fun `depotPreview reads the oracle preview path with the key and decodes a ready payload`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"appid":1366540,"state":"ready","expected":true,"checked_at":"2026-10-15T10:00:00Z",
                    |"total_size_bytes":5368709120,"total_download_bytes":2147483648,
                    |"depots":[{"depotid":1366541,"size_bytes":5368709120,"download_bytes":2147483648},
                    |{"depotid":1366543,"size_bytes":null,"download_bytes":null}],
                    |"dlc_depots":[{"depotid":1366542,"dlcappid":1500}],"extra_future_field":1}"""
                    .trimMargin(),
            ),
        )
        val out = client.depotPreview(1366540)
        val recorded = server.takeRequest()
        assertEquals("/v1/oracle/1366540/preview", recorded.path)
        assertEquals("GET", recorded.method)
        assertEquals("test-key-123", recorded.getHeader("X-Api-Key"))
        assertEquals("ready", out.state)
        assertEquals(5368709120L, out.total_size_bytes)
        assertEquals(listOf(1366541, 1366543), out.depots.map { it.depotid })
        assertNull(out.depots[1].size_bytes)
        assertEquals(1500, out.dlc_depots.single().dlcappid)
    }

    @Test
    fun `an off payload decodes with empty defaults`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"appid":5,"state":"off"}"""))
        val out = client.depotPreview(5)
        assertEquals("off", out.state)
        assertTrue(out.depots.isEmpty())
        assertTrue(out.dlc_depots.isEmpty())
        assertNull(out.total_size_bytes)
    }

    @Test
    fun `storeDetails reads the store details path and decodes a ready payload`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"appid":7,"state":"ready","description":"A game.","genres":["Action"],
                    |"developers":["Studio"],"release_date":"3 Mar, 2023","modes":["single_player"],
                    |"review_summary":"Very Positive","review_positive":90,"review_negative":10}"""
                    .trimMargin(),
            ),
        )
        val out = client.storeDetails(7)
        val recorded = server.takeRequest()
        assertEquals("/v1/store/7/details", recorded.path)
        assertEquals("test-key-123", recorded.getHeader("X-Api-Key"))
        assertEquals("A game.", out.description)
        assertEquals(listOf("single_player"), out.modes)
        assertEquals(90L, out.review_positive)
        assertEquals("Very Positive", out.review_summary)
    }

    @Test
    fun `an off store payload decodes with empty defaults`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"appid":7,"state":"off"}"""))
        val out = client.storeDetails(7)
        assertEquals("off", out.state)
        assertTrue(out.genres.isEmpty() && out.developers.isEmpty() && out.modes.isEmpty())
        assertNull(out.description)
    }

    @Test
    fun `an older server without the routes maps 404 to NotFound`() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
        try {
            client.depotPreview(1)
            org.junit.Assert.fail("expected NotFound")
        } catch (e: VaultApiError.NotFound) {
            // expected
        }
    }
}
