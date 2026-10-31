package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.LiveCurrentOut
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.LoadingAppOut
import dev.steamvault.app.net.model.LoadingJobOut
import dev.steamvault.app.net.model.LoadingSourceOut
import dev.steamvault.app.net.model.SummaryOut
import dev.steamvault.app.net.model.ThroughputNowOut
import dev.steamvault.app.net.model.ThroughputOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.serialization.decodeFromString

/**
 * WP ANDROID-DL-2: [loadingNowModel], the twin of web `loadingNowModel`
 * (NOW-LOADING-1), and the `loading_sources` path through [mergeNow] /
 * [mergeLive]. The cases mirror web/tests/now-loading.test.js one for one;
 * every expected word is a LITERAL (docs/LEARNINGS.md, Android:
 * literal-vs-literal contracts), the web side is pinned by
 * `DownloadsHeroWebTwinTest`. Sample addresses 10.0.0.x.
 */
class LoadingNowTest {
    private val mib = 1024.0 * 1024.0
    private val feed = EventFeedOut(enabled = true, state = "ok", last_sweep_at = "2026-10-18T11:58:07Z")

    private fun source(
        kind: String,
        key: String? = when (kind) { "runner" -> "runner"; "unattributed" -> "unattributed"; else -> "addr:10.0.0.50" },
        name: String? = when (kind) { "runner" -> "Prefill runner"; "unattributed" -> null; else -> "10.0.0.50" },
        addresses: List<String> = if (kind == "unattributed") emptyList() else listOf("10.0.0.50"),
        known: Boolean? = true,
        bps: Double = 10 * mib,
        hit: Double = 0.0,
        miss: Double = 10 * mib,
        apps: List<LoadingAppOut> = emptyList(),
        phase: String? = if (kind == "runner") "download" else null,
        job: LoadingJobOut? = null,
    ) = LoadingSourceOut(
        kind = kind, key = key, name = name, addresses = addresses, known = known,
        bytes_per_second = bps, hit_bytes_per_second = hit, miss_bytes_per_second = miss,
        apps = apps, unattributed_bytes_per_second = 0.0, phase = phase, job = job,
    )

    private val runnerDl = source(
        "runner",
        bps = 30 * mib,
        miss = 30 * mib,
        apps = listOf(LoadingAppOut(275850, "No Man's Sky", 30 * mib)),
        job = LoadingJobOut(job_id = 200, appid = 275850, name = "No Man's Sky", stop_request = null),
    )

    private fun current(total: Double, runner: Double, hit: Double) = LiveCurrentOut(
        bytes_per_second = total,
        runner_bytes_per_second = runner,
        runner_hit_bytes_per_second = hit,
        runner_miss_bytes_per_second = runner - hit,
        window_start = "2026-10-18T11:59:50Z",
        window_end = "2026-10-18T12:00:07Z",
        window_seconds = 17,
    )

    /** A persisted-range answer whose `now` block carries [sources]. */
    private fun answer(
        sources: List<LoadingSourceOut>?,
        cur: LiveCurrentOut = current(30 * mib, 30 * mib, 0.0),
        state: String = "ok",
    ) = ThroughputOut(
        event_feed = feed.copy(state = state),
        range = "1h",
        tier = "1m",
        bucket_seconds = 60,
        active_window_minutes = 15,
        active_clients_now = 0,
        summary = SummaryOut(),
        now = if (state == "ok") {
            ThroughputNowOut(current = cur, active_clients = 0, runner_active = cur.runner_bytes_per_second > 0, window_seconds = 17, loading_sources = sources)
        } else {
            null
        },
    )

    private fun rowsOf(sources: List<LoadingSourceOut>, cur: LiveCurrentOut = current(30 * mib, 30 * mib, 0.0)) =
        loadingNowModel(mergeNow(answer(sources, cur)))!!.rows

    @Test
    fun `the block's words are the web's literals`() {
        assertEquals("Loading now", LOADING_NOW_LABEL)
        assertEquals("Unattributed traffic", LOADING_UNATTRIBUTED)
        assertEquals("New device", LOADING_NEW_DEVICE)
        assertEquals("No running job", LOADING_NO_JOB)
        assertEquals("Loading now", loadingNowModel(mergeNow(answer(listOf(runnerDl))))!!.label)
    }

    @Test
    fun `a downloading runner names its job's game and the pill's word`() {
        val row = rowsOf(listOf(runnerDl)).single()
        assertEquals("runner", row.kind)
        assertEquals("Prefill runner", row.name)
        assertEquals("No Man's Sky · job #200 · Downloading", row.detail)
        assertEquals("30.0 MB/s", row.speed)
    }

    @Test
    fun `a verifying runner reads Verifying, like the pill`() {
        val verify = runnerDl.copy(phase = "verify", hit_bytes_per_second = 30 * mib, miss_bytes_per_second = 0.0)
        val t = mergeNow(answer(listOf(verify), current(30 * mib, 30 * mib, 30 * mib)))
        val row = loadingNowModel(t)!!.rows.single()
        assertTrue(row.detail, row.detail.endsWith("Verifying"))
        assertFalse(row.detail.contains("Downloading"))
        assertEquals("Verifying", heroStatus(t.current)!!.text)
    }

    @Test
    fun `runner traffic without a running job says so (the cancel case)`() {
        val orphan = runnerDl.copy(job = null)
        val t = mergeNow(answer(listOf(orphan)))
        assertEquals("No running job · No Man's Sky · Downloading", loadingNowModel(t)!!.rows.single().detail)
        // The pill still says Downloading: the row explains it.
        assertEquals("Downloading", heroStatus(t.current)!!.text)
        assertEquals("No running job · Downloading", rowsOf(listOf(orphan.copy(apps = emptyList()))).single().detail)
    }

    @Test
    fun `a pending cancel or pause is shown on the runner row`() {
        val cancel = runnerDl.copy(job = runnerDl.job!!.copy(stop_request = "cancel"))
        assertTrue(rowsOf(listOf(cancel)).single().detail.endsWith(" · cancel requested"))
        val pause = runnerDl.copy(job = runnerDl.job!!.copy(stop_request = "pause"))
        assertTrue(rowsOf(listOf(pause)).single().detail.endsWith(" · pause requested"))
        // A runner row without a server name keeps the web's own word.
        assertEquals("Prefill runner", rowsOf(listOf(runnerDl.copy(name = null))).single().name)
    }

    @Test
    fun `two devices with their games, named as on the Clients page`() {
        val pc = source(
            "client", key = "agent:pc-one", name = "Gaming PC", addresses = listOf("10.0.0.20"),
            bps = 12 * mib, hit = 12 * mib, miss = 0.0,
            apps = listOf(LoadingAppOut(1, "Game One", 8 * mib), LoadingAppOut(2, "Game Two", 4 * mib)),
        )
        val laptop = source(
            "client", key = "addr:10.0.0.21", name = "Laptop", addresses = listOf("10.0.0.21"),
            bps = 4 * mib, hit = 1 * mib, miss = 3 * mib,
            apps = listOf(LoadingAppOut(3, null, 4 * mib)),
        )
        val rows = rowsOf(listOf(pc, laptop), current(16 * mib, 0.0, 0.0))
        assertEquals(listOf("Gaming PC", "Laptop"), rows.map { it.name })
        assertEquals("Game One, Game Two · from the cache", rows[0].detail)
        assertEquals("12.0 MB/s", rows[0].speed)
        assertEquals("agent:pc-one", rows[0].key)
        // A game without a name is "Unknown app", never a bare id; a mixed
        // split says how much came from the cache.
        assertEquals("Unknown app · 25% from the cache", rows[1].detail)
    }

    @Test
    fun `a device loading only from the internet, and one without a linked game`() {
        assertEquals("content not linked to one game · from the internet", rowsOf(listOf(source("client", name = "Laptop"))).single().detail)
        // Nothing measured: no cache word at all.
        assertEquals("content not linked to one game", rowsOf(listOf(source("client", bps = 0.0, miss = 0.0))).single().detail)
    }

    @Test
    fun `an unknown address is a New device with its address`() {
        val row = rowsOf(listOf(source("client", known = false, apps = listOf(LoadingAppOut(9, "Game Nine", 1.0))))).single()
        assertEquals("New device", row.name)
        assertEquals("10.0.0.50 · Game Nine · from the internet", row.detail)
        // Missing `known` (never false) keeps the server's name.
        assertEquals("10.0.0.50", rowsOf(listOf(source("client", known = null))).single().name)
    }

    @Test
    fun `traffic no device explains reads Unattributed traffic`() {
        val rows = rowsOf(listOf(runnerDl, source("unattributed", bps = 2 * mib)))
        assertEquals(listOf("runner", "unattributed"), rows.map { it.kind })
        assertEquals("Unattributed traffic", rows[1].name)
        assertEquals("more devices at once than the live view tells apart", rows[1].detail)
        assertEquals("2.0 MB/s", rows[1].speed)
        // No measurement: the dash, never a made-up zero.
        assertEquals("—", rowsOf(listOf(source("unattributed").copy(bytes_per_second = null))).single().speed)
    }

    @Test
    fun `no block while nothing loads, with a feed that is not ok, or from an older server`() {
        assertNull(loadingNowModel(mergeNow(answer(emptyList()))))
        assertNull(loadingNowModel(mergeNow(answer(listOf(runnerDl), state = "stale"))))
        assertNull(loadingNowModel(mergeNow(answer(null))))
        assertNull(loadingNowModel(null))
        // Feed state is checked on the answer itself, too.
        assertNull(loadingNowModel(ThroughputOut(event_feed = feed.copy(state = "never-swept"), range = "1h", loading_sources = listOf(runnerDl))))
        // Unknown kinds are skipped, not invented.
        assertNull(loadingNowModel(mergeNow(answer(listOf(LoadingSourceOut(kind = "mystery", bytes_per_second = 1.0))))))
    }

    @Test
    fun `the live view takes the rows from the live answer only`() {
        // Rows of another window on the summary (its `now`, or a merged copy)
        // must never leak into the live view.
        val stale = listOf(source("client", name = "Stale summary row"))
        val summary = answer(stale).copy(loading_sources = stale)
        val live = LiveOut(event_feed = feed, current = current(30 * mib, 30 * mib, 0.0), active_clients = 0, active_window_seconds = 17, loading_sources = listOf(runnerDl))
        assertEquals(listOf("Prefill runner"), loadingNowModel(mergeLive(summary, live))!!.rows.map { it.name })
        // A live answer without rows (an older server) shows no block, not the summary's.
        assertNull(loadingNowModel(mergeLive(summary, live.copy(loading_sources = null))))
        // A live feed that is not ok: no rows either.
        assertNull(mergeLive(summary, live.copy(event_feed = feed.copy(state = "stale"))).loading_sources)
    }

    /** The server's shape (api/README.md "`now.loading_sources`") decodes 1:1. */
    @Test
    fun `the wire shape decodes, incl the runner's job and a pending stop`() {
        val json = """{"current":{"bytes_per_second":31457280.0,"runner_bytes_per_second":31457280.0},"active_clients":1,
            "runner_active":true,"window_seconds":17,"live_since":null,"loading_sources":[
            {"kind":"runner","key":"runner","name":"Prefill runner","addresses":["172.20.0.5"],"known":true,
             "bytes_per_second":31457280.0,"hit_bytes_per_second":0.0,"miss_bytes_per_second":31457280.0,
             "apps":[{"appid":275850,"name":"No Man's Sky","bytes_per_second":31457280.0}],
             "unattributed_bytes_per_second":0.0,"phase":"download",
             "job":{"job_id":200,"appid":275850,"name":"No Man's Sky","stop_request":"cancel"}},
            {"kind":"client","key":"addr:10.0.0.20","name":"10.0.0.20","addresses":["10.0.0.20"],"known":false,
             "bytes_per_second":1.0,"hit_bytes_per_second":1.0,"miss_bytes_per_second":0.0,"apps":[],
             "unattributed_bytes_per_second":1.0,"phase":null,"job":null}]}"""
        val now = dev.steamvault.app.net.VaultJson.decodeFromString<ThroughputNowOut>(json)
        val rows = loadingNowModel(mergeNow(answer(now.loading_sources)))!!.rows
        assertEquals("No Man's Sky · job #200 · Downloading · cancel requested", rows[0].detail)
        assertEquals("New device", rows[1].name)
        assertEquals("10.0.0.20 · content not linked to one game · from the cache", rows[1].detail)
    }
}
