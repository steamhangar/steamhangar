package dev.steamvault.app.net

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.profile.SystemVpnProfile
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * WP ANDROID-DL-2: `POST /v1/cache/{appid}/repair` on the wire. The dry run
 * sends NO body (the server's default), only the execute sends
 * `{"execute":true}` (web `api.repairCache`); the report decodes from the
 * server's `RepairOut`, and the group's unmeasured fields from `GET
 * /v1/games` (GROUP-VERIFIED-1) default to 0 on an older server.
 */
class RepairApiTest {

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

    private val dryRunBody =
        """{"appid":553850,"mode":"dry-run","execute":false,"damaged_chunks":3,"damaged_bytes":4404019,
          |"depots":[{"depotid":553851,"manifestid":"7340032000000000000","damaged_chunks":3,"damaged_bytes":4404019}],
          |"unreadable_depots":[553853],"exceeds_cap":false,"max_files":2000,"removed_chunks":0,"removed_bytes":0,
          |"skipped":{},"job_id":null,"job_status":null,"job_deduplicated":null}""".trimMargin()

    @Test
    fun `the dry run sends no body and decodes the plan`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody(dryRunBody))
        val r = client.repairCache(553850)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/v1/cache/553850/repair", req.path)
        assertEquals(0L, req.bodySize)
        assertEquals(3, r.damaged_chunks)
        assertEquals(553851, r.depots.single().depotid)
        assertEquals(listOf(553853), r.unreadable_depots)
        assertNull(r.job_id)
    }

    @Test
    fun `the execute sends execute true and decodes the job`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(202).setBody(
                dryRunBody.replace("\"mode\":\"dry-run\",\"execute\":false", "\"mode\":\"execute\",\"execute\":true")
                    .replace("\"removed_chunks\":0,\"removed_bytes\":0", "\"removed_chunks\":3,\"removed_bytes\":4404019")
                    .replace("\"skipped\":{},\"job_id\":null,\"job_status\":null,\"job_deduplicated\":null", "\"skipped\":{\"size_changed\":1},\"job_id\":41,\"job_status\":\"queued\",\"job_deduplicated\":false"),
            ),
        )
        val r = client.repairCache(553850, execute = true)
        assertEquals("""{"execute":true}""", server.takeRequest().body.readUtf8())
        assertEquals(3, r.removed_chunks)
        assertEquals(mapOf("size_changed" to 1), r.skipped)
        assertEquals(41, r.job_id)
    }

    @Test
    fun `a 409 carries the server's detail for the busy vs not_owned split`() = runTest {
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"The last prefill of app 553850 ended not_owned."}"""))
        try {
            client.repairCache(553850)
            fail("expected 409")
        } catch (e: VaultApiError) {
            assertEquals(409, e.status)
            assertEquals("The last prefill of app 553850 ended not_owned.", e.detail)
        }
    }

    @Test
    fun `games decode the group's unmeasured fields, 0 from an older server`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """[{"appid":553850,"name":"HD","status":"done","depot_count":3,"group_cache_complete_pct":100,
                  |"group_cache_unmeasured_depots":8,"group_cache_unmeasured_bytes":2254857830},
                  |{"appid":440,"name":"Old","status":"done","depot_count":1}]""".trimMargin(),
            ),
        )
        val g = client.games().associateBy { it.appid }
        assertEquals(8, g.getValue(553850).group_cache_unmeasured_depots)
        assertEquals(2_254_857_830L, g.getValue(553850).group_cache_unmeasured_bytes)
        assertEquals(0, g.getValue(440).group_cache_unmeasured_depots)
        assertEquals(0L, g.getValue(440).group_cache_unmeasured_bytes)
    }
}
