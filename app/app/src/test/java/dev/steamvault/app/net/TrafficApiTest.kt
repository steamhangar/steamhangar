package dev.steamvault.app.net

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.profile.SystemVpnProfile
import dev.steamvault.app.ui.traffic.logic.headerTiles
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
 * WP D15d: the traffic endpoints over a real HTTP round trip -- the request
 * line (path + `range`), decoding of the server's shapes (WP D15b,
 * `api/vault_api/routers/traffic.py`) including the feed-disabled form
 * where every figure is `null`, and the old-server 404 the screens turn
 * into "no statistics" (`TrafficPoll`).
 */
class TrafficApiTest {
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

    private val okFeed =
        """{"enabled":true,"state":"ok","last_sweep_at":"2026-10-14T11:59:00Z","sweep_interval_minutes":5,"traffic_since":"2026-10-01T00:00:00Z"}"""

    @Test
    fun `throughput -- path, range and the ok shape`() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """{"event_feed":$okFeed,"range":"7d","tier":"1h","bucket_seconds":3600,"range_start":"2026-10-07T12:00:00Z",
                "active_window_minutes":15,
                "current":{"bytes_per_second":41943040.0,"window_start":"2026-10-14T11:54:00Z","window_end":"2026-10-14T11:59:00Z","window_seconds":300},
                "active_clients_now":2,
                "summary":{"bytes_served":5368709120,"hit_bytes":4294967296,"miss_bytes":1073741824,"requests":10,"hits":8,"misses":2,
                  "errors":0,"hit_rate":0.8,"byte_hit_rate":0.8,"average_bytes_per_second":2.0E7,"peak_bytes_per_second":6.0E7,"active_clients":3},
                "jobs":[{"job_id":7,"appid":10,"name":"G","started_at":"2026-10-14T11:00:00Z","bytes_per_second":null,"bytes_since_start":null,"exclusive_depots":0}]}""",
            ),
        )
        val t = client.throughput("7d")
        val req = server.takeRequest()
        assertEquals("/v1/throughput?range=7d", req.path)
        assertEquals("k", req.getHeader("X-Api-Key"))
        assertEquals(41943040.0, t.current!!.bytes_per_second, 0.0)
        assertEquals(5368709120L, t.summary!!.bytes_served)
        assertEquals(0, t.jobs.single().exclusive_depots)
        assertNull(t.jobs.single().bytes_per_second)
    }

    @Test
    fun `throughput -- the feed-disabled shape decodes and yields no tiles`() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """{"event_feed":{"enabled":false,"state":"disabled","last_sweep_at":null,"sweep_interval_minutes":5,"traffic_since":null},
                "range":"24h","tier":"5m","bucket_seconds":300,"range_start":null,"active_window_minutes":15,
                "current":null,"active_clients_now":null,"summary":null,"jobs":[]}""",
            ),
        )
        assertNull(headerTiles(client.throughput("24h")))
    }

    @Test
    fun `series and clients overview -- paths and shapes`() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """{"event_feed":$okFeed,"range":"30d","tier":"1h","bucket_seconds":3600,"range_start":"2026-09-14T12:00:00Z",
                "points":[{"t":"2026-09-14T12:00:00Z","bytes_served":3600,"hit_bytes":3000,"miss_bytes":600,"requests":2,
                "bytes_per_second":1.0,"peak_bytes_per_second":2.0}]}""",
            ),
        )
        server.enqueue(
            MockResponse().setBody(
                """{"event_feed":$okFeed,"range":"all","tier":"1d","range_start":null,"active_window_minutes":15,"clients":[
                {"key":"addr:10.0.0.9","kind":"address","client_id":null,"addresses":["10.0.0.9"],"connected":true,
                 "agent_presence":null,"cache_active":true,"last_active":"2026-10-14T11:58:00Z","last_seen_in_cache_log":"2026-10-14T11:58:00Z",
                 "last_reported_at":null,"installed_appids":null,"installed_app_count":null,
                 "stats":{"bytes_served":1,"hit_bytes":1,"miss_bytes":0,"requests":1,"hits":1,"misses":0,"hit_rate":1.0},
                 "loading_now":{"loading":false,"bytes_per_second":0.0,"apps":[],"unattributed_bytes_per_second":0.0},
                 "recent_apps":[{"appid":10,"name":null,"bytes_served":1,"last_seen":"2026-10-14T11:58:00Z"}],"recent_unattributed_bytes":0}]}""",
            ),
        )
        val s = client.throughputSeries("30d")
        assertEquals("/v1/throughput/series?range=30d", server.takeRequest().path)
        assertEquals(1, s.points.size)
        val o = client.clientsOverview("all")
        assertEquals("/v1/clients/overview?range=all", server.takeRequest().path)
        assertEquals("address", o.clients.single().kind)
        assertNull(o.clients.single().installed_app_count)
    }

    @Test
    fun `history fetch sends limit and range`() = runTest {
        server.enqueue(MockResponse().setBody("[]"))
        assertTrue(client.jobsInRange("24h", 200).isEmpty())
        val path = server.takeRequest().path!!
        assertTrue(path, path.startsWith("/v1/jobs?") && "limit=200" in path && "range=24h" in path)
    }

    @Test
    fun `an old server's 404 surfaces as NotFound`() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
        try {
            client.clientsOverview("7d")
            fail("expected NotFound")
        } catch (e: VaultApiError.NotFound) {
            assertEquals(404, e.status)
        }
    }
}
