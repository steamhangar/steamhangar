package dev.steamvault.app.demo

import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.ClientStatsOut
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.JobThroughputOut
import dev.steamvault.app.net.model.LiveCurrentOut
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.LivePointOut
import dev.steamvault.app.net.model.LoadingAppOut
import dev.steamvault.app.net.model.LoadingNowOut
import dev.steamvault.app.net.model.RecentAppOut
import dev.steamvault.app.net.model.RunnerTrafficOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.net.model.SummaryOut
import dev.steamvault.app.net.model.ThroughputOut
import java.time.Instant
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToLong
import kotlin.math.sin

/**
 * Demo-mode traffic fixtures (WP D15d), modeled on web `demo-traffic.js`
 * (WP D15c): `GET /v1/throughput`, `GET /v1/throughput/series` and
 * `GET /v1/clients/overview`, in the server's real shapes
 * (`net/model/Traffic.kt`). The numbers are synthetic and deterministic per
 * range and clock (no randomness): a daily rhythm with an evening peak,
 * scaled so the header, the graph and the client cards tell one story --
 * the online demo PC loading the running demo job's game, the offline one
 * idle, and two agentless addresses (one loading now, one last seen days
 * ago). The feed is always `ok` in demo mode, so every screen shows its
 * figures. Addresses are fictional (10.0.0.x like [seedClients]).
 *
 * WP D15-RUNNER (web demo-traffic.js): the server's prefill runner is its
 * own overview entry (`kind: "runner"`), and its bytes stay out of every
 * client figure: a tenth of each bucket is the runner's (`runner_bytes`),
 * hit and miss bytes are the clients' share only, and the summary carries
 * `client_bytes_served` and `prefill_runner` (bytes_served =
 * client_bytes_served + runner bytes).
 *
 * WP D15-NAMES: client rows carry the server's name fields (`name`,
 * `name_addr`, `display_name`). Names are stored per address
 * ([DEMO_CLIENT_NAMES]); the demo names one agent so the Clients page shows
 * an operator-set title with "vault-agent on <PC name>" under it. Renaming
 * and "Delete history" are web-only, so the demo has no way to change them.
 */

/** The demo state [DemoTrafficRepository] reads, taken in one lock. */
data class DemoTrafficSnapshot(
    val runningJobs: List<JobSummary>,
    val games: List<GameSummary>,
    val agents: List<ClientOut>,
)

/** Operator-set names by address (WP D15-NAMES; the server's
 * `client_names` table). The offline demo agent's address is named. */
internal val DEMO_CLIENT_NAMES: Map<String, String> = mapOf("10.0.0.57" to "Steam Deck")

/** The prefill runner's demo address (a Docker bridge gateway). */
internal const val DEMO_RUNNER_ADDRESS = "172.18.0.1"

private const val MIB = 1024.0 * 1024.0
private const val GIB = 1024.0 * MIB

/** [loadingMinutes]: for the 1-minute tier, how long ago the demo download
 * began (older minutes are empty, so the trimmed graph axis is visible). */
private data class Tier(val tier: String, val bucket: Int, val count: Int, val loadingMinutes: Int = 0)

private val TIERS: Map<String, Tier> = mapOf(
    // WP GRAPH-ZOOM: "1h" / "6h" read the 1-minute tier.
    "1h" to Tier("1m", 60, 60, loadingMinutes = 40),
    "6h" to Tier("1m", 60, 360, loadingMinutes = 150),
    "24h" to Tier("5m", 300, 288),
    "7d" to Tier("1h", 3600, 168),
    "30d" to Tier("1h", 3600, 720),
    // Demo "all" covers 45 days.
    "all" to Tier("1d", 86400, 45),
)

private fun tierFor(range: String): Tier = TIERS[range] ?: TIERS.getValue("24h")

private fun knownRange(range: String, fallback: String): String = if (range in TIERS) range else fallback

private fun iso(ms: Long): String = Instant.ofEpochSecond(Math.floorDiv(ms, 1000L)).toString()

private fun floorTo(ms: Long, seconds: Int): Long {
    val step = seconds * 1000L
    return Math.floorDiv(ms, step) * step
}

/** Deterministic bytes/s for a bucket starting at [ms]: idle at night, an
 * evening peak, nothing on some 5-minute buckets. A daily bucket starts at
 * midnight (idle), so it is sampled at its evening and scaled to a
 * plausible day average instead of reading as zero. */
private fun rateAt(ms: Long, bucketSeconds: Int): Double {
    if (bucketSeconds >= 86400) return (rateAt(ms + 18 * 3_600_000L, 3600) * 0.4).roundToLong().toDouble()
    val hour = Math.floorMod(ms / 3_600_000L, 24L) + Math.floorMod(ms / 60_000L, 60L) / 60.0
    val day = Math.floorDiv(ms, 86_400_000L)
    val evening = max(0.0, sin(((hour - 12) / 12) * PI))
    val dayFactor = 0.55 + 0.45 * abs(sin(day * 1.7))
    val gap = bucketSeconds <= 300 && Math.floorMod(ms / 300_000L, 7L) == 3L
    if (gap || evening < 0.08) return 0.0
    return (evening * dayFactor * 46 * MIB).roundToLong().toDouble()
}

/** Deterministic bytes/s of the 1-minute bucket [age] minutes before the
 * newest one: nothing before the download began, a wavy rate since, and a
 * short pause now and then (web `rateMinute`). */
private fun rateMinute(age: Int, loadingMinutes: Int): Double {
    if (age > loadingMinutes || age % 11 == 5) return 0.0
    return ((0.55 + 0.45 * abs(sin(age * 0.7))) * 38 * MIB).roundToLong().toDouble()
}

private fun feed(nowMs: Long) = EventFeedOut(
    enabled = true,
    state = "ok",
    // A sweep a minute ago: the feed is `ok` in demo mode.
    last_sweep_at = iso(nowMs - 60_000L),
    sweep_interval_minutes = 5,
    traffic_since = iso(floorTo(nowMs - 45 * 86_400_000L, 86400)),
    // The 1-minute tier started recording a day and a half ago in the demo.
    traffic_1m_since = iso(nowMs - 36 * 3_600_000L),
)

/** `GET /v1/throughput/series` (demo). */
fun demoSeries(range: String, nowMs: Long): SeriesOut {
    val t = tierFor(range)
    val last = floorTo(nowMs, t.bucket)
    val points = (t.count - 1 downTo 0).map { i ->
        val start = last - i * t.bucket * 1000L
        val bps = if (t.tier == "1m") rateMinute(i, t.loadingMinutes) else rateAt(start, t.bucket)
        val served = (bps * t.bucket).roundToLong()
        val runner = (served * 0.1).roundToLong()
        val clients = served - runner
        val hit = (clients * 0.82).roundToLong()
        SeriesPointOut(
            t = iso(start),
            bytes_served = served,
            hit_bytes = hit,
            miss_bytes = clients - hit,
            requests = (clients / (1.4 * MIB)).roundToLong(),
            runner_bytes = runner,
            bytes_per_second = bps,
            peak_bytes_per_second = if (t.bucket <= 300) bps else (bps * 1.35).roundToLong().toDouble(),
        )
    }
    return SeriesOut(
        event_feed = feed(nowMs),
        range = knownRange(range, "24h"),
        tier = t.tier,
        bucket_seconds = t.bucket,
        range_start = points.firstOrNull()?.t,
        points = points,
    )
}

/**
 * `GET /v1/throughput/live` (demo, WP GRAPH-LIVE; web `demoLive`): the
 * stitched series the server builds, in the same geometry (5 s points for
 * the last minute, 15 s up to 5 minutes, then 1-minute points up to an
 * hour), with a deterministic wavy rate so the demo graph moves.
 */
fun demoLive(nowMs: Long): LiveOut {
    val fineStart = floorTo(nowMs, 15) - 45_000L
    val mediumStart = floorTo(fineStart, 60) - 180_000L
    val coarseStart = mediumStart - 3_240_000L
    val spans = ArrayList<Triple<Long, Int, String>>()
    var at = coarseStart
    while (at < mediumStart) { spans.add(Triple(at, 60, "persisted")); at += 60_000L }
    at = mediumStart
    while (at < fineStart) { spans.add(Triple(at, 15, "live")); at += 15_000L }
    at = fineStart
    while (at <= floorTo(nowMs, 5)) { spans.add(Triple(at, 5, "live")); at += 5_000L }
    val points = spans.map { (start, bucket, source) ->
        val age = Math.floorDiv(nowMs - start, 60_000L)
        val wave = 0.6 + 0.4 * abs(sin(start / 9_000.0))
        val bps = if (age > 30) 0.0 else (wave * 36 * MIB).roundToLong().toDouble()
        val served = (bps * bucket).roundToLong()
        val runner = (served * 0.1).roundToLong()
        val clients = served - runner
        val hit = (clients * 0.82).roundToLong()
        LivePointOut(
            t = iso(start),
            bucket_seconds = bucket,
            source = source,
            bytes_served = served,
            hit_bytes = hit,
            miss_bytes = clients - hit,
            requests = (clients / (1.4 * MIB)).roundToLong(),
            runner_bytes = runner,
            bytes_per_second = bps,
            peak_bytes_per_second = bps,
        )
    }
    val recent = points.takeLast(4)
    val windowSeconds = 15 + (Math.floorMod(nowMs, 5000L) / 1000L).toInt()
    val bps = recent.sumOf { it.bytes_per_second } / recent.size
    return LiveOut(
        event_feed = feed(nowMs),
        range = "live",
        tier = "live",
        watcher_window_seconds = 30,
        live_since = iso(nowMs - 15 * 60_000L),
        range_start = points.first().t,
        current = LiveCurrentOut(
            bytes_per_second = bps,
            runner_bytes_per_second = bps * 0.1,
            window_start = iso(nowMs - windowSeconds * 1000L),
            window_end = iso(nowMs),
            window_seconds = windowSeconds,
        ),
        points = points,
    )
}

private fun summaryFor(range: String, nowMs: Long): SummaryOut {
    val series = demoSeries(range, nowMs)
    var served = 0L
    var runner = 0L
    var hit = 0L
    var requests = 0L
    var active = 0.0
    var peak = 0.0
    for (p in series.points) {
        served += p.bytes_served
        runner += p.runner_bytes
        hit += p.hit_bytes
        requests += p.requests
        if (p.bytes_served > 0) active += series.bucket_seconds / 300.0
        peak = max(peak, p.peak_bytes_per_second)
    }
    val hits = (requests * 0.83).roundToLong()
    val clients = served - runner
    val runnerHit = (runner * 0.6).roundToLong()
    val runnerRequests = (runner / (1.4 * MIB)).roundToLong()
    val runnerHits = (runnerRequests * 0.6).roundToLong()
    return SummaryOut(
        bytes_served = served,
        hit_bytes = hit,
        miss_bytes = clients - hit,
        requests = requests,
        hits = hits,
        misses = requests - hits,
        errors = requests / 4000,
        hit_rate = if (requests > 0) hits.toDouble() / requests else null,
        byte_hit_rate = if (clients > 0) hit.toDouble() / clients else null,
        average_bytes_per_second = if (active > 0) served / (active * 300) else null,
        peak_bytes_per_second = peak,
        active_clients = if (range == "24h") 3 else 4,
        client_bytes_served = clients,
        internet_bytes = clients - hit + (runner - runnerHit),
        prefill_runner = RunnerTrafficOut(
            bytes_served = runner,
            hit_bytes = runnerHit,
            miss_bytes = runner - runnerHit,
            requests = runnerRequests,
            hits = runnerHits,
            misses = runnerRequests - runnerHits,
            errors = 0,
            byte_hit_rate = if (runner > 0) runnerHit.toDouble() / runner else null,
        ),
    )
}

private fun nameOf(appid: Int, games: List<GameSummary>): String? = games.firstOrNull { it.appid == appid }?.name

/** `GET /v1/throughput` (demo). Each running job gets a speed while the
 * online demo PC loads it. */
fun demoThroughput(range: String, snapshot: DemoTrafficSnapshot, nowMs: Long): ThroughputOut {
    val t = tierFor(range)
    val f = feed(nowMs)
    val end = nowMs - 60_000L
    val windowSeconds = 300 + Math.floorMod(end / 1000L, 300L).toInt()
    val jobs = snapshot.runningJobs.map { j ->
        JobThroughputOut(
            job_id = j.id,
            appid = j.appid,
            name = nameOf(j.appid, snapshot.games),
            started_at = j.started_at,
            bytes_per_second = 31.5 * MIB,
            bytes_since_start = (8.8 * GIB).roundToLong(),
            exclusive_depots = 1,
        )
    }
    return ThroughputOut(
        event_feed = f,
        range = knownRange(range, "24h"),
        tier = t.tier,
        bucket_seconds = t.bucket,
        range_start = demoSeries(range, nowMs).range_start,
        active_window_minutes = 15,
        current = CurrentOut(
            bytes_per_second = if (jobs.isNotEmpty()) 38.2 * MIB else 2.1 * MIB,
            window_start = iso(end - windowSeconds * 1000L),
            window_end = iso(end),
            window_seconds = windowSeconds,
            runner_bytes_per_second = if (jobs.isNotEmpty()) 6.4 * MIB else 0.0,
        ),
        active_clients_now = 2,
        runner_active_now = jobs.isNotEmpty(),
        summary = summaryFor(range, nowMs),
        jobs = jobs,
    )
}

private fun stats(bytes: Long, hitRate: Double): ClientStatsOut {
    val hit = (bytes * hitRate).roundToLong()
    val requests = (bytes / (1.4 * MIB)).roundToLong()
    val hits = (requests * hitRate).roundToLong()
    return ClientStatsOut(
        bytes_served = bytes,
        hit_bytes = hit,
        miss_bytes = bytes - hit,
        requests = requests,
        hits = hits,
        misses = requests - hits,
        hit_rate = if (requests > 0) hits.toDouble() / requests else null,
    )
}

private val RANGE_SCALE: Map<String, Double> = mapOf("24h" to 1.0, "7d" to 4.2, "30d" to 13.0, "all" to 19.0)

private val IDLE = LoadingNowOut(loading = false, bytes_per_second = 0.0, apps = emptyList(), unattributed_bytes_per_second = 0.0)

/** The server's name fields for a client named by [addr] (`null` = cannot
 * be named), [fallback] being its title without an operator-set name. */
private data class DemoName(val name: String?, val nameAddr: String?, val displayName: String)

private fun demoName(addr: String?, fallback: String): DemoName {
    val name = addr?.let { DEMO_CLIENT_NAMES[it] }
    return DemoName(name, addr, name ?: fallback)
}

/** `GET /v1/clients/overview` (demo): the demo agents, two agentless
 * addresses and the prefill runner, server order (connected first, then
 * newest activity). */
fun demoClientsOverview(range: String, snapshot: DemoTrafficSnapshot, nowMs: Long): ClientsOverviewOut {
    val scale = RANGE_SCALE[range] ?: 1.0
    val games = snapshot.games
    val gameIds = games.map { it.appid }
    fun recent(appids: List<Int>, base: Double): List<RecentAppOut> = appids.mapIndexed { i, appid ->
        RecentAppOut(
            appid = appid,
            name = nameOf(appid, games),
            bytes_served = (base * scale / (i + 1)).roundToLong(),
            last_seen = iso(nowMs - (i + 1) * 3 * 3_600_000L),
        )
    }
    val running = snapshot.runningJobs.firstOrNull()
    val clients = snapshot.agents.map { a ->
        val online = a.presence == "online"
        val installed = games.filter { g -> g.installed_on.any { it.client_id == a.client_id } }.map { it.appid }
        val lastCache = if (online) iso(nowMs - 60_000L) else a.last_seen_in_cache_log
        val lastActive = listOfNotNull(lastCache, a.last_reported_at).maxOrNull()
        val addresses = a.source_addrs.take(1)
        val names = demoName(addresses.firstOrNull(), a.client_id)
        ClientActivityOut(
            key = "agent:${a.client_id}",
            kind = "agent",
            client_id = a.client_id,
            addresses = addresses,
            connected = online,
            agent_presence = a.presence,
            cache_active = online,
            last_active = lastActive,
            last_seen_in_cache_log = lastCache,
            last_reported_at = a.last_reported_at,
            installed_appids = installed,
            installed_app_count = installed.size,
            stats = if (online) {
                stats((14.6 * GIB * scale).roundToLong(), 0.81)
            } else {
                stats(if (range == "24h") 0L else (0.3 * GIB * scale).roundToLong(), 0.5)
            },
            loading_now = if (online && running != null) {
                LoadingNowOut(
                    loading = true,
                    bytes_per_second = 33.7 * MIB,
                    apps = listOf(LoadingAppOut(running.appid, nameOf(running.appid, games), 31.5 * MIB)),
                    unattributed_bytes_per_second = 2.2 * MIB,
                )
            } else {
                IDLE
            },
            recent_apps = when {
                online -> recent(gameIds.take(4), 6 * GIB)
                range == "24h" -> emptyList()
                else -> recent(gameIds.drop(3).take(1), 0.3 * GIB)
            },
            recent_unattributed_bytes = if (online) (0.4 * GIB * scale).roundToLong() else 0L,
            name = names.name,
            name_addr = names.nameAddr,
            display_name = names.displayName,
        )
    }.toMutableList()
    val loading = demoName("10.0.0.61", "10.0.0.61")
    val old = demoName("10.0.0.63", "10.0.0.63")
    // Two agentless addresses: a machine loading right now, and one last
    // active days ago (still listed: seen within 30 days).
    clients.add(
        ClientActivityOut(
            key = "addr:10.0.0.61",
            kind = "address",
            addresses = listOf("10.0.0.61"),
            connected = true,
            cache_active = true,
            last_active = iso(nowMs - 2 * 60_000L),
            last_seen_in_cache_log = iso(nowMs - 2 * 60_000L),
            stats = stats((3.1 * GIB * scale).roundToLong(), 0.92),
            loading_now = LoadingNowOut(
                loading = true,
                bytes_per_second = 4.5 * MIB,
                apps = emptyList(),
                unattributed_bytes_per_second = 4.5 * MIB,
            ),
            recent_apps = recent(gameIds.take(1), 2.4 * GIB),
            recent_unattributed_bytes = (0.7 * GIB * scale).roundToLong(),
            name = loading.name,
            name_addr = loading.nameAddr,
            display_name = loading.displayName,
        ),
    )
    val oldMs = nowMs - 4 * 86_400_000L
    val oldInRange = range != "24h"
    clients.add(
        ClientActivityOut(
            key = "addr:10.0.0.63",
            kind = "address",
            addresses = listOf("10.0.0.63"),
            connected = false,
            cache_active = false,
            last_active = iso(oldMs),
            last_seen_in_cache_log = iso(oldMs),
            stats = stats(if (oldInRange) (1.2 * GIB).roundToLong() else 0L, 0.64),
            loading_now = IDLE,
            recent_apps = if (oldInRange) recent(gameIds.drop(1).take(1), 1.2 * GIB / scale) else emptyList(),
            recent_unattributed_bytes = 0L,
            name = old.name,
            name_addr = old.nameAddr,
            display_name = old.displayName,
        ),
    )
    // WP D15-RUNNER: the server's own prefill runner, loading the running
    // demo job's game while one runs. It cannot be named (`name_addr` null).
    val runnerSeen = if (running != null) iso(nowMs - 60_000L) else iso(nowMs - 9 * 3_600_000L)
    clients.add(
        ClientActivityOut(
            key = "runner",
            kind = "runner",
            addresses = listOf(DEMO_RUNNER_ADDRESS),
            connected = running != null,
            cache_active = running != null,
            last_active = runnerSeen,
            last_seen_in_cache_log = runnerSeen,
            stats = stats((4.8 * GIB * scale).roundToLong(), 0.6),
            loading_now = if (running != null) {
                LoadingNowOut(
                    loading = true,
                    bytes_per_second = 12.4 * MIB,
                    apps = listOf(LoadingAppOut(running.appid, nameOf(running.appid, games), 12.4 * MIB)),
                    unattributed_bytes_per_second = 0.0,
                )
            } else {
                IDLE
            },
            recent_apps = recent(gameIds.take(2), 3 * GIB),
            recent_unattributed_bytes = 0L,
            name = null,
            name_addr = null,
            display_name = "Prefill runner",
        ),
    )
    val ordered = clients.sortedWith(
        compareByDescending<ClientActivityOut> { it.connected == true }.thenByDescending { it.last_active ?: "" },
    )
    return ClientsOverviewOut(
        event_feed = feed(nowMs),
        range = knownRange(range, "7d"),
        tier = tierFor(range).tier,
        range_start = demoSeries(range, nowMs).range_start,
        active_window_minutes = 15,
        clients = ordered,
    )
}
