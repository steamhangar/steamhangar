package dev.steamvault.app.net

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

/**
 * WP CACHE-COMPLETE-1c (app): the completeness fields of `GET /v1/games`
 * and `GET /v1/games/{appid}` decode from the CURRENT wire shape
 * (api/vault_api/routers/games.py, CACHE-COMPLETE-1a/1b), default to
 * "not known" on an older server that sends none, and "Verify" /
 * "Complete now" reach `POST /v1/prefill/verify` with exactly the listed
 * app ids.
 */
class CacheCompletenessApiTest {

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
    fun `games decodes the completeness fields of the current wire shape`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """[{"appid":440,"name":"Partial","status":"done","depot_count":2,"size_bytes":4200000000,
                    |"needs_force":false,"installed_on":[],"cache_complete_pct":86,
                    |"cache_expected_bytes":4880000000,"cache_present_bytes":4200000000,
                    |"cache_complete_checked_at":"2026-10-14T09:00:00Z","cache_missing_chunks":412,
                    |"group_cache_complete_pct":80},
                    |{"appid":730,"name":"Unknown","status":"done","depot_count":1,"size_bytes":10,
                    |"needs_force":false,"cache_complete_pct":null,"cache_expected_bytes":null,
                    |"cache_present_bytes":null,"cache_complete_checked_at":null,
                    |"cache_missing_chunks":null,"group_cache_complete_pct":null}]"""
                    .trimMargin(),
            ),
        )

        val games = client.games().associateBy { it.appid }
        val partial = games.getValue(440)
        assertEquals(86, partial.cache_complete_pct)
        assertEquals(4_880_000_000L, partial.cache_expected_bytes)
        assertEquals(4_200_000_000L, partial.cache_present_bytes)
        assertEquals("2026-10-14T09:00:00Z", partial.cache_complete_checked_at)
        assertEquals(412, partial.cache_missing_chunks)
        assertEquals(80, partial.group_cache_complete_pct)

        val unknown = games.getValue(730)
        assertNull(unknown.cache_complete_pct)
        assertNull(unknown.cache_expected_bytes)
        assertNull(unknown.cache_present_bytes)
        assertNull(unknown.cache_complete_checked_at)
        assertNull(unknown.cache_missing_chunks)
        assertNull(unknown.group_cache_complete_pct)
    }

    @Test
    fun `an older server without the fields decodes as not known`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """[{"appid":10,"status":"done","depot_count":1,"size_bytes":5,"needs_force":false}]""",
            ),
        )
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"appid":10,"status":"done","depots":[],"needs_force":false}""",
            ),
        )

        val row = client.games().single()
        assertNull(row.cache_complete_pct)
        assertNull(row.cache_missing_chunks)
        assertNull(row.group_cache_complete_pct)
        val detail = client.game(10)
        assertNull(detail.cache_complete_pct)
        assertNull(detail.cache_expected_bytes)
    }

    @Test
    fun `game decodes the completeness fields too`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"appid":440,"status":"done","depots":[],"needs_force":false,
                    |"cache_complete_pct":99,"cache_expected_bytes":200,"cache_present_bytes":198,
                    |"cache_complete_checked_at":"2026-10-14T09:00:00Z","cache_missing_chunks":0,
                    |"group_cache_complete_pct":99}"""
                    .trimMargin(),
            ),
        )

        val detail = client.game(440)
        assertEquals(99, detail.cache_complete_pct)
        assertEquals(200L, detail.cache_expected_bytes)
        assertEquals(198L, detail.cache_present_bytes)
        assertEquals(0, detail.cache_missing_chunks)
        assertEquals(99, detail.group_cache_complete_pct)
    }

    @Test
    fun `prefillVerify POSTs exactly the listed appids to v1 prefill verify`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(202).setBody(
                """[{"appid":440,"job_id":7,"status":"queued","deduplicated":false},
                    |{"appid":730,"job_id":3,"status":"running","deduplicated":true}]"""
                    .trimMargin(),
            ),
        )

        val refs = client.prefillVerify(listOf(440, 730))

        assertEquals(listOf(440, 730), refs.map { it.appid })
        assertTrue(refs[1].deduplicated)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/prefill/verify", recorded.path)
        assertEquals("""{"appids":[440,730]}""", recorded.body.readUtf8())
        assertEquals("test-key-123", recorded.getHeader("X-Api-Key"))
    }
}
