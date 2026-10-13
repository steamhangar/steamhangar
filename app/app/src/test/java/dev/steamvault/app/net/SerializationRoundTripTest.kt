package dev.steamvault.app.net

import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.CacheSummaryOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.HealthOut
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.UpdateCheckOut
import dev.steamvault.app.net.model.UpdateStatusOut
import dev.steamvault.app.net.model.settingAsBooleanOrNull
import dev.steamvault.app.net.model.settingAsIntOrNull
import dev.steamvault.app.net.model.settingAsStringListOrNull
import dev.steamvault.app.net.model.settingAsStringOrNull
import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Wire-shape round trips against fixtures MODELED on the documented
 * response shapes in api/README.md's "Endpoints" table and the Pydantic
 * models it mirrors (`vault_api/routers` sources, read at git HEAD for this
 * WP — see each fixture's comment for the exact source). Synthetic data
 * throughout (docs/LEARNINGS.md "Testing discipline": "Fixtures: synthetic
 * only, modeled on real structure").
 *
 * One deliberate deviation from the WP brief's "copied verbatim from
 * api/README.md examples": no single README curl transcript currently
 * shows every field together after the schema's v4->v13 history (e.g. the
 * WP 1.3-era `GET /v1/games` transcript predates `needs_force` and
 * `last_manifest_check` entirely) — a fixture built from one such stale
 * transcript would silently under-test the CURRENT shape. These fixtures
 * are instead built directly from the "Endpoints" table's field list and
 * the router source's field ORDER/types, which is the more current and
 * more complete source of truth; each fixture cites exactly which. One
 * fixture (`JobControlOut`'s anchor test, below) IS lifted verbatim from an
 * api test's own asserted response body, per the WP 4b.2 Opus review's
 * should-fix suggestion.
 *
 * **STRICT decoding, in addition to production `VaultJson` (WP 4b.2 Opus
 * review should-fix).** [VaultJson] sets `ignoreUnknownKeys = true`
 * deliberately (see its own kdoc) — but that same leniency means a TYPO'D
 * fixture key silently vanishes into "unknown, ignored" instead of failing
 * the test that is supposed to prove the fixture matches the real shape.
 * [strictJson] below is `ignoreUnknownKeys = false` and used ONLY here, as
 * an anti-drift check alongside (never instead of) the production
 * [VaultJson] decode: every fixture in this file except the one
 * DELIBERATELY-unknown-field test at the bottom is decoded through
 * [decodeStrictAndLenient], so a fixture key that does not match any
 * property name in the target data class throws immediately instead of
 * silently decoding as if that field had never been mentioned.
 *
 * **What strict decoding does NOT catch (documented, not silently
 * assumed away):** a fixture that OMITS a field the data class gives a
 * Kotlin default to (e.g. `GameSummary.needs_force: Boolean = false`) is
 * indistinguishable, to both `VaultJson` and `strictJson`, from a real
 * server response that genuinely omitted it — decoding still succeeds,
 * silently absorbing the default. Every such field in this client's
 * models is nullable/defaulted specifically so an OLDER or NEWER
 * vault-api can omit it without breaking decode (see `VaultJson`'s kdoc);
 * the same mechanism that makes forward/backward compatibility work also
 * means a fixture-authoring mistake that drops one of these fields is NOT
 * caught by either Json configuration here. Only a field with NO Kotlin
 * default (e.g. `GameSummary.appid`, `.depot_count`) fails loudly
 * (`MissingFieldException`) if a fixture omits it.
 */
class SerializationRoundTripTest {

    /** Test-only anti-drift check — see this file's class kdoc. Never used for production decoding. */
    private val strictJson = Json {
        ignoreUnknownKeys = false
        isLenient = false
    }

    /**
     * Decode [json] as [T] through BOTH [strictJson] (thrown away — its
     * only job is to fail on a fixture key that doesn't match any
     * property, i.e. a typo) and the production [VaultJson] (whose result
     * is what the caller actually asserts against).
     */
    private inline fun <reified T> decodeStrictAndLenient(json: String): T {
        strictJson.decodeFromString<T>(json)
        return VaultJson.decodeFromString(json)
    }

    @Test
    fun `HealthOut -- GET v1 health, api README Auth section`() {
        val json = """{"status":"ok"}"""
        val decoded = decodeStrictAndLenient<HealthOut>(json)
        assertEquals("ok", decoded.status)
    }

    @Test
    fun `GameSummary -- GET v1 games row, games py GameSummary`() {
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"done",
             "last_prefill_at":"2026-08-05T20:44:34Z",
             "last_manifest_check":"2026-08-06T03:00:01Z",
             "depot_count":2,"size_bytes":6000000,"needs_force":false}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameSummary>(json)

        assertEquals(440, decoded.appid)
        assertEquals("Team Fortress 2", decoded.name)
        assertEquals("done", decoded.status)
        assertEquals("2026-08-05T20:44:34Z", decoded.last_prefill_at)
        assertEquals("2026-08-06T03:00:01Z", decoded.last_manifest_check)
        assertEquals(2, decoded.depot_count)
        assertEquals(6000000L, decoded.size_bytes)
        assertEquals(false, decoded.needs_force)
    }

    @Test
    fun `GameSummary -- an uncached never-prefilled app decodes with nulls, not defaults masking them`() {
        val json = """
            {"appid":730,"name":null,"status":"idle","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":0,"size_bytes":null,
             "needs_force":true}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameSummary>(json)

        assertNull(decoded.name)
        assertNull(decoded.last_prefill_at)
        assertNull(decoded.last_manifest_check)
        assertNull(decoded.size_bytes)
        assertTrue(decoded.needs_force)
    }

    @Test
    fun `GameDetail -- GET v1 games appid, games py GameDetail, with a shared depot`() {
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"done",
             "last_prefill_at":"2026-08-05T20:44:34Z","last_manifest_check":null,
             "depots":[{"depotid":441,"shared":false,"size_bytes":1000000},
                       {"depotid":900,"shared":true,"size_bytes":5000000}],
             "size_bytes":6000000,"needs_force":false}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameDetail>(json)

        assertEquals(2, decoded.depots.size)
        assertEquals(441, decoded.depots[0].depotid)
        assertEquals(false, decoded.depots[0].shared)
        assertEquals(900, decoded.depots[1].depotid)
        assertEquals(true, decoded.depots[1].shared)
        assertEquals(5000000L, decoded.depots[1].size_bytes)
    }

    @Test
    fun `PrefillJobRef list -- POST v1 prefill 202 response, jobs py PrefillJobRef`() {
        val json = """
            [{"appid":440,"job_id":1,"status":"queued","deduplicated":false},
             {"appid":440,"job_id":1,"status":"queued","deduplicated":true}]
        """.trimIndent()

        val decoded = decodeStrictAndLenient<List<PrefillJobRef>>(json)

        assertEquals(2, decoded.size)
        assertEquals(false, decoded[0].deduplicated)
        assertEquals(true, decoded[1].deduplicated)
        assertEquals(1, decoded[1].job_id)
    }

    @Test
    fun `JobSummary -- GET v1 jobs row, jobs py JobSummary, a paused prefill`() {
        val json = """
            {"id":7,"appid":440,"type":"prefill","status":"paused",
             "created_at":"2026-08-10T10:00:00Z","started_at":"2026-08-10T10:00:05Z",
             "finished_at":null,"updated":null,"up_to_date":null,
             "summary_parse_ok":null,"gc_execute":null,
             "paused_at":"2026-08-10T10:05:00Z","stop_request":null}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobSummary>(json)

        assertEquals("paused", decoded.status)
        assertEquals("2026-08-10T10:05:00Z", decoded.paused_at)
        assertNull(decoded.gc_execute)
        assertNull(decoded.stop_request)
    }

    @Test
    fun `JobSummary -- a finished GC execute run carries gc_execute true`() {
        val json = """
            {"id":8,"appid":440,"type":"gc","status":"done",
             "created_at":"2026-08-10T10:00:00Z","started_at":"2026-08-10T10:00:05Z",
             "finished_at":"2026-08-10T10:01:00Z","updated":null,"up_to_date":null,
             "summary_parse_ok":null,"gc_execute":true,"paused_at":null,"stop_request":null}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobSummary>(json)

        assertEquals("gc", decoded.type)
        assertEquals(true, decoded.gc_execute)
    }

    @Test
    fun `JobDetail -- GET v1 jobs id, jobs py JobDetail, adds log_excerpt over JobSummary`() {
        val json = """
            {"id":1,"appid":440,"type":"prefill","status":"done",
             "created_at":"2026-08-05T20:44:34Z","started_at":"2026-08-05T20:44:35Z",
             "finished_at":"2026-08-05T20:44:36Z","updated":2,"up_to_date":0,
             "summary_parse_ok":true,"gc_execute":null,"paused_at":null,"stop_request":null,
             "log_excerpt":"[vault-api] Depot mapping updated: added=[441, 442]"}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobDetail>(json)

        assertEquals(2, decoded.updated)
        assertEquals(0, decoded.up_to_date)
        assertEquals(true, decoded.summary_parse_ok)
        assertTrue(decoded.log_excerpt!!.contains("Depot mapping updated"))
    }

    @Test
    fun `JobControlOut -- DELETE v1 jobs id and pause-resume, jobs py JobControlOut`() {
        val json = """{"job_id":1,"status":"queued","outcome":"resumed","detail":"job re-queued at the front"}"""
        val decoded = decodeStrictAndLenient<JobControlOut>(json)
        assertEquals("resumed", decoded.outcome)
        assertEquals("job re-queued at the front", decoded.detail)
    }

    @Test
    fun `JobControlOut -- anchor fixture lifted verbatim from api tests test_job_control py`() {
        // Lifted verbatim from api/tests/test_job_control.py (cancelling a
        // queued job): `assert body == {"job_id": job_id, "status":
        // "cancelled", "outcome": "immediate", "detail":
        // jobs.CANCELLED_QUEUED_MESSAGE}` -- `job_id` is a test-local int
        // there (any positive int fixture value proves the same thing);
        // `CANCELLED_QUEUED_MESSAGE`'s literal string is copied verbatim
        // from vault_api/jobs.py.
        val json = """
            {"job_id":3,"status":"cancelled","outcome":"immediate",
             "detail":"[vault-api] Cancelled while queued: this job was never started, so nothing ran and nothing on disk was touched."}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobControlOut>(json)

        assertEquals(3, decoded.job_id)
        assertEquals("cancelled", decoded.status)
        assertEquals("immediate", decoded.outcome)
        assertTrue(decoded.detail.contains("never started"))
    }

    @Test
    fun `CacheSummaryOut -- GET v1 cache summary, cache py CacheSummaryOut`() {
        val json = """
            {"total_bytes":6250000,
             "top_consumers":[{"appid":440,"name":"Team Fortress 2","size_bytes":6000000},
                               {"appid":730,"name":"Counter-Strike 2","size_bytes":5000000}],
             "unmapped_depots":{"count":1,"size_bytes":250000},
             "free_disk_bytes":119640584192}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<CacheSummaryOut>(json)

        assertEquals(6250000L, decoded.total_bytes)
        assertEquals(2, decoded.top_consumers.size)
        assertEquals(1, decoded.unmapped_depots.count)
        assertEquals(119640584192L, decoded.free_disk_bytes)
    }

    @Test
    fun `CacheDeletionOut -- DELETE v1 cache appid, cache py CacheDeletionOut, a last-remnant plus a failure`() {
        val json = """
            {"appid":440,
             "deleted_depots":[{"depotid":441,"size_bytes_freed":1000000,"shared_with_uncached":[]},
                                {"depotid":900,"size_bytes_freed":50000,"shared_with_uncached":[730]}],
             "skipped_shared":[{"depotid":901,"shared_with":[730]}],
             "failed":[{"depotid":902,"error":"WinError 5: access is denied"}],
             "total_bytes_freed":1050000}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<CacheDeletionOut>(json)

        assertEquals(2, decoded.deleted_depots.size)
        assertEquals(listOf(730), decoded.deleted_depots[1].shared_with_uncached)
        assertEquals(1, decoded.skipped_shared.size)
        assertEquals(1, decoded.failed.size)
        assertEquals("WinError 5: access is denied", decoded.failed[0].error)
        assertEquals(1050000L, decoded.total_bytes_freed)
    }

    @Test
    fun `GcJobRef -- POST v1 cache appid gc 202 response, cache py GcJobRef, dry run by default`() {
        val json = """{"appid":440,"job_id":7,"status":"queued","type":"gc","mode":"dry-run","execute":false,"deduplicated":false}"""
        val decoded = decodeStrictAndLenient<GcJobRef>(json)
        assertEquals("dry-run", decoded.mode)
        assertEquals(false, decoded.execute)
    }

    @Test
    fun `ClientOut -- GET v1 clients row, clients py ClientOut, bypass suspected`() {
        val json = """
            {"client_id":"gaming-pc","first_seen":"2026-08-05T20:44:34Z",
             "last_reported_at":"2026-08-05T20:44:36Z","app_count":3,
             "source_addrs":["192.168.1.42"],"cache_hits":120,"cache_misses":4,
             "bytes_served":734003200,"last_seen_in_cache_log":"2026-08-10T09:00:00Z",
             "bypass_suspected":true}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<ClientOut>(json)

        assertEquals("gaming-pc", decoded.client_id)
        assertEquals(listOf("192.168.1.42"), decoded.source_addrs)
        assertEquals(true, decoded.bypass_suspected)
    }

    @Test
    fun `ClientOut -- event feed off decodes with the documented 0 null false defaults`() {
        val json = """
            {"client_id":"steam-deck","first_seen":"2026-08-05T20:44:36Z",
             "last_reported_at":"2026-08-05T20:44:36Z","app_count":1,
             "source_addrs":[],"cache_hits":0,"cache_misses":0,"bytes_served":0,
             "last_seen_in_cache_log":null,"bypass_suspected":false}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<ClientOut>(json)

        assertEquals(0, decoded.cache_hits)
        assertNull(decoded.last_seen_in_cache_log)
        assertEquals(false, decoded.bypass_suspected)
    }

    @Test
    fun `SettingsOut -- GET v1 settings, settings py SettingsOut, heterogeneous effective fallback values`() {
        val json = """
            {"readonly":false,
             "settings":[
               {"key":"vault_name","effective":"my-vault","source":"default","fallback":"","applies":"restart-required","env_only":false},
               {"key":"schedule_window","effective":null,"source":"default","fallback":null,"applies":"next_sweep","env_only":false},
               {"key":"schedule_interval_minutes","effective":30,"source":"env","fallback":30,"applies":"next_sweep","env_only":false},
               {"key":"auto_gc","effective":"dry-run","source":"db","fallback":"off","applies":"immediately","env_only":false},
               {"key":"webhook_events","effective":["job_finished","job_failed"],"source":"default","fallback":["job_finished","job_failed"],"applies":"restart-required","env_only":false},
               {"key":"vault_api_key_placeholder_never_sent","effective":null,"source":"env","fallback":null,"applies":"restart-required","env_only":true}
             ]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<SettingsOut>(json)

        assertEquals(false, decoded.readonly)
        assertEquals(6, decoded.settings.size)

        val vaultName = decoded.settings[0]
        assertEquals("my-vault", vaultName.effective.settingAsStringOrNull())
        assertEquals("", vaultName.fallback.settingAsStringOrNull())

        val scheduleWindow = decoded.settings[1]
        assertNull(scheduleWindow.effective.settingAsStringOrNull())

        val intervalMinutes = decoded.settings[2]
        assertEquals(30, intervalMinutes.effective.settingAsIntOrNull())
        assertEquals("env", intervalMinutes.source)

        val autoGc = decoded.settings[3]
        assertEquals("dry-run", autoGc.effective.settingAsStringOrNull())

        val webhookEvents = decoded.settings[4]
        assertEquals(listOf("job_finished", "job_failed"), webhookEvents.effective.settingAsStringListOrNull())

        val envOnly = decoded.settings[5]
        assertEquals(true, envOnly.env_only)
        assertNull(envOnly.effective.settingAsIntOrNull())
        assertNull(envOnly.effective.settingAsBooleanOrNull())
    }

    @Test
    fun `unknown future field is ignored, not a decode failure -- VaultJson ignoreUnknownKeys`() {
        // Deliberately VaultJson-only, NOT decodeStrictAndLenient: this is
        // the one fixture that's supposed to have a field strictJson would
        // reject -- that rejection is the exact behavior being proven
        // absent from the production decode path.
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":1,"size_bytes":null,"needs_force":false,
             "a_field_this_client_has_never_heard_of":{"nested":["whatever"]}}
        """.trimIndent()

        val decoded = VaultJson.decodeFromString<GameSummary>(json)

        assertEquals(440, decoded.appid)
    }

    // -----------------------------------------------------------------
    // WP AG-3: installed_on (GameSummary/GameDetail) and GET /v1/schedule.
    // -----------------------------------------------------------------

    @Test
    fun `GameSummary -- installed_on carries fresh agent-report entries, api README Installed state per app`() {
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":0,"size_bytes":null,"needs_force":false,
             "installed_on":[{"client_id":"gaming-pc","reported_at":"2026-08-22T09:15:03Z"}]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameSummary>(json)

        assertEquals(1, decoded.installed_on.size)
        assertEquals("gaming-pc", decoded.installed_on[0].client_id)
        assertEquals("2026-08-22T09:15:03Z", decoded.installed_on[0].reported_at)
    }

    @Test
    fun `GameSummary -- a pre-AG-1 server response with no installed_on key decodes to an empty list, not a crash`() {
        // Forward-compat direction is the wrong way round here: THIS is the
        // backward-compat direction -- an OLDER vault-api that has never
        // heard of installed_on. Only VaultJson (production leniency), not
        // decodeStrictAndLenient -- strictJson has no default to fall back
        // on for a class-defined default the same way VaultJson does not
        // need one either, but this fixture is deliberately missing the key
        // to prove the Kotlin default (`= emptyList()`) is what saves it.
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":0,"size_bytes":null,"needs_force":false}
        """.trimIndent()

        val decoded = VaultJson.decodeFromString<GameSummary>(json)

        assertEquals(emptyList<Any>(), decoded.installed_on)
    }

    @Test
    fun `GameDetail -- installed_on round trips alongside depots`() {
        val json = """
            {"appid":730,"name":"Counter-Strike 2","status":"done",
             "last_prefill_at":"2026-08-05T20:44:34Z","last_manifest_check":null,
             "depots":[],"size_bytes":null,"needs_force":false,
             "installed_on":[{"client_id":"steam-deck","reported_at":"2026-08-22T08:00:00Z"},
                              {"client_id":"gaming-pc","reported_at":"2026-08-22T09:15:03Z"}]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameDetail>(json)

        assertEquals(2, decoded.installed_on.size)
        assertEquals("steam-deck", decoded.installed_on[0].client_id)
        assertEquals("gaming-pc", decoded.installed_on[1].client_id)
    }

    @Test
    fun `ScheduleOut -- GET v1 schedule, schedule py ScheduleOut, a real sweep result with the risk flag on`() {
        val json = """
            {"enabled":true,"window":"22:00-06:00","overnight":true,"interval_minutes":180,
             "client_stale_days":7,"server_timezone":"UTC+02:00",
             "last_sweep_at":"2026-08-22T22:00:05Z","last_sweep_targets":3,"last_sweep_enqueued":1,
             "next_eligible_at":"2026-08-23T22:00:00Z","sweep_include_cached":true,
             "sweep_cached_gc_risk":true}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<ScheduleOut>(json)

        assertTrue(decoded.enabled)
        assertEquals("22:00-06:00", decoded.window)
        assertTrue(decoded.overnight)
        assertEquals(180, decoded.interval_minutes)
        assertEquals(7, decoded.client_stale_days)
        assertEquals(3, decoded.last_sweep_targets)
        assertEquals(1, decoded.last_sweep_enqueued)
        assertTrue(decoded.sweep_include_cached)
        assertTrue(decoded.sweep_cached_gc_risk)
    }

    @Test
    fun `ScheduleOut -- a vault that has never swept decodes with the null triple, not zeros`() {
        val json = """
            {"enabled":false,"window":null,"overnight":false,"interval_minutes":180,
             "client_stale_days":7,"server_timezone":"UTC+00:00",
             "last_sweep_at":null,"last_sweep_targets":null,"last_sweep_enqueued":null,
             "next_eligible_at":null,"sweep_include_cached":true,"sweep_cached_gc_risk":false}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<ScheduleOut>(json)

        assertFalse(decoded.enabled)
        assertNull(decoded.window)
        assertNull(decoded.last_sweep_at)
        assertNull(decoded.last_sweep_targets)
        assertNull(decoded.last_sweep_enqueued)
        assertFalse(decoded.sweep_cached_gc_risk)
    }

    /**
     * The Kotlin-type-system equivalent of `web/tests/schedule-presentation
     * .test.js`'s "MUTATION PIN: only a literal boolean true triggers the
     * warning" — see `SchedulePresentation.kt`'s kdoc. [ScheduleOut.sweep_cached_gc_risk]
     * is declared `Boolean`, and [VaultJson] sets `isLenient = false`
     * (production instance, not [strictJson] — this is exactly the
     * behavior a real network response goes through), so a wire value that
     * is a JSON number or JSON string for this field must fail decoding
     * outright rather than silently coercing to `true`.
     */
    @Test
    fun `MUTATION PIN -- sweep_cached_gc_risk as a JSON number fails to decode, never coerces to true`() {
        val json = """
            {"enabled":true,"window":null,"overnight":false,"interval_minutes":180,
             "client_stale_days":7,"server_timezone":"UTC+00:00",
             "last_sweep_at":null,"last_sweep_targets":null,"last_sweep_enqueued":null,
             "next_eligible_at":null,"sweep_include_cached":true,"sweep_cached_gc_risk":1}
        """.trimIndent()
        try {
            VaultJson.decodeFromString<ScheduleOut>(json)
            fail("expected a SerializationException decoding a JSON number into a Boolean field")
        } catch (_: SerializationException) {
            // expected
        }
    }

    /**
     * MEASURED, not assumed: a quoted JSON string `"true"`/`"false"` for a
     * `Boolean` field DOES decode successfully through [VaultJson] (this
     * was tried as a "fails to decode" pin first — it failed, i.e. the
     * decode SUCCEEDED, which is the discovery being pinned here instead).
     * kotlinx.serialization's boolean decoding reads the [JsonPrimitive]'s
     * string CONTENT ("true"/"false") regardless of whether that content
     * arrived quoted or bare, and `isLenient = false` does not change that
     * — it governs unquoted/bare-token acceptance and comments/trailing
     * commas, not this coercion. So [VaultJson]/the Kotlin `Boolean` type
     * closes the NUMERIC half of the wire-level mutation hole (see the test
     * above: a JSON number fails outright) but NOT a string that happens to
     * spell "true"/"false" — that residual is recorded here rather than
     * silently assumed closed, correcting `SchedulePresentation.kt`'s kdoc.
     */
    @Test
    fun `MEASURED -- sweep_cached_gc_risk as the JSON STRING true still decodes successfully to true, a residual not a pin`() {
        val json = """
            {"enabled":true,"window":null,"overnight":false,"interval_minutes":180,
             "client_stale_days":7,"server_timezone":"UTC+00:00",
             "last_sweep_at":null,"last_sweep_targets":null,"last_sweep_enqueued":null,
             "next_eligible_at":null,"sweep_include_cached":true,"sweep_cached_gc_risk":"true"}
        """.trimIndent()
        val decoded = VaultJson.decodeFromString<ScheduleOut>(json)
        assertTrue(decoded.sweep_cached_gc_risk)
    }

    @Test
    fun `MEASURED -- but a string that is not literally true or false still fails to decode`() {
        val json = """
            {"enabled":true,"window":null,"overnight":false,"interval_minutes":180,
             "client_stale_days":7,"server_timezone":"UTC+00:00",
             "last_sweep_at":null,"last_sweep_targets":null,"last_sweep_enqueued":null,
             "next_eligible_at":null,"sweep_include_cached":true,"sweep_cached_gc_risk":"yes"}
        """.trimIndent()
        try {
            VaultJson.decodeFromString<ScheduleOut>(json)
            fail("expected a SerializationException decoding a non-boolean-spelled string into a Boolean field")
        } catch (_: SerializationException) {
            // expected
        }
    }

    // -----------------------------------------------------------------
    // WP UPD-3: update check (API WP UPD-1, vault_api/routers/updates.py).
    // -----------------------------------------------------------------

    @Test
    fun `GameSummary -- update_available and update_checked_at decode, api UPD-1`() {
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"done","last_prefill_at":"2026-08-05T20:44:34Z",
             "last_manifest_check":null,"depot_count":2,"size_bytes":6000000,"needs_force":false,
             "update_available":true,"update_checked_at":"2026-10-13T10:01:00Z"}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameSummary>(json)

        assertTrue(decoded.update_available)
        assertEquals("2026-10-13T10:01:00Z", decoded.update_checked_at)
    }

    @Test
    fun `GameSummary -- a pre-UPD-1 server with no update fields decodes to no badge, not a crash`() {
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":0,"size_bytes":null,"needs_force":false}
        """.trimIndent()

        val decoded = VaultJson.decodeFromString<GameSummary>(json)

        assertFalse(decoded.update_available)
        assertNull(decoded.update_checked_at)
    }

    @Test
    fun `UpdateCheckOut -- started and disabled bodies of POST v1 updates check`() {
        val started = decodeStrictAndLenient<UpdateCheckOut>("""{"status":"started","enabled":true}""")
        assertEquals("started", started.status)
        assertTrue(started.enabled)
        val disabled = decodeStrictAndLenient<UpdateCheckOut>("""{"status":"disabled","enabled":false}""")
        assertEquals("disabled", disabled.status)
        assertFalse(disabled.enabled)
    }

    @Test
    fun `UpdateStatusOut -- a finished row with every UpdateCheckStatusOut field`() {
        val json = """
            {"enabled":true,"scheduled":false,"running":false,"last_trigger":"manual",
             "last_started_at":"2026-10-13T10:00:00Z","last_finished_at":"2026-10-13T10:01:00Z",
             "last_outcome":"completed","last_apps":12,"last_ok":11,"last_failed":1,"last_stale":3}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<UpdateStatusOut>(json)

        assertFalse(decoded.running)
        assertEquals("completed", decoded.last_outcome)
        assertEquals(12, decoded.last_apps)
        assertEquals(1, decoded.last_failed)
        assertEquals(3, decoded.last_stale)
    }

    @Test
    fun `UpdateStatusOut -- a running row carries nulls for outcome and counters`() {
        val json = """
            {"enabled":true,"scheduled":true,"running":true,"last_trigger":"scheduled",
             "last_started_at":"2026-10-13T10:00:00Z","last_finished_at":null,"last_outcome":null,
             "last_apps":null,"last_ok":null,"last_failed":null,"last_stale":null}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<UpdateStatusOut>(json)

        assertTrue(decoded.running)
        assertNull(decoded.last_outcome)
        assertNull(decoded.last_stale)
    }
}
