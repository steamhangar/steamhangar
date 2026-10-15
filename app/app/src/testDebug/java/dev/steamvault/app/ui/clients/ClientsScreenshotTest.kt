package dev.steamvault.app.ui.clients

import android.app.Application
import android.content.ComponentName
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import dev.steamvault.app.demo.DemoTrafficSnapshot
import dev.steamvault.app.demo.demoClientsOverview
import dev.steamvault.app.demo.demoLive
import dev.steamvault.app.demo.demoSeries
import dev.steamvault.app.demo.demoThroughput
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.repo.TrafficRepository
import dev.steamvault.app.ui.downloads.DownloadsStatsHeader
import dev.steamvault.app.ui.downloads.downloadsStatsPoll
import dev.steamvault.app.ui.library.ScreenshotGoldens
import dev.steamvault.app.ui.nav.Destination
import dev.steamvault.app.ui.nav.MainShell
import dev.steamvault.app.ui.theme.SteamVaultTheme
import dev.steamvault.app.ui.traffic.TrafficRanges
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TestRule
import org.junit.runner.Description
import org.junit.runner.RunWith
import org.junit.runners.model.Statement
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Screenshot tests of the Clients page and the Downloads statistics header
 * (APP-TEST-2, after WP D15d), Roborazzi on Robolectric like
 * `LibraryScreenshotTest`, whose golden handling ([ScreenshotGoldens]) they
 * share. The CI golden refresh records every `*ScreenshotTest` class and
 * accepts the union of their shot names (the first argument of each
 * `shoot` call; keep it a string literal there).
 *
 * Data: the demo's traffic builders (`demo/DemoTraffic.kt`) over a FIXED
 * snapshot and a FIXED clock ([NOW], an evening, so the graph has a
 * shape), so every pixel is reproducible; the demo banner is shown as in
 * demo mode. Profiles: 411 dp at font 1.0 and 360 dp at font 1.3, dark.
 * The Clients page is rendered in the real [MainShell], which also proves
 * the four bottom-nav labels stay on one line at 360 dp / 1.3.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], application = Application::class)
class ClientsScreenshotTest {

    @get:Rule(order = 0)
    val hostActivity: TestRule = RegisterClientsHostActivityRule()

    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun clients_411dp_font100_night() = shoot("clients_411dp_font100_night", widthDp = 411, heightDp = 891, fontScale = 1.0f, page = Page.CLIENTS)

    @Test
    fun clients_360dp_font130_night() = shoot("clients_360dp_font130_night", widthDp = 360, heightDp = 780, fontScale = 1.3f, page = Page.CLIENTS)

    @Test
    fun downloads_stats_411dp_font100_night() =
        shoot("downloads_stats_411dp_font100_night", widthDp = 411, heightDp = 560, fontScale = 1.0f, page = Page.STATS)

    @Test
    fun downloads_stats_360dp_font130_night() =
        shoot("downloads_stats_360dp_font130_night", widthDp = 360, heightDp = 640, fontScale = 1.3f, page = Page.STATS)

    private enum class Page { CLIENTS, STATS }

    private fun shoot(name: String, widthDp: Int, heightDp: Int, fontScale: Float, page: Page) {
        RuntimeEnvironment.setQualifiers("w${widthDp}dp-h${heightDp}dp-night-420dpi")
        RuntimeEnvironment.setFontScale(fontScale)
        composeRule.activityRule.scenario.recreate()
        composeRule.runOnUiThread { composeRule.activity.enableEdgeToEdge() }

        val repo = FixedTrafficRepository()
        composeRule.setContent {
            SteamVaultTheme {
                when (page) {
                    Page.CLIENTS -> MainShell(current = Destination.CLIENTS, pendingJobsCount = 1, onSelect = {}) {
                        ClientsScreen(
                            trafficRepository = repo,
                            ranges = remember { TrafficRanges() },
                            onManagePcs = {},
                            demoMode = true,
                            clock = { NOW },
                        )
                    }
                    Page.STATS -> Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
                        val scope = rememberCoroutineScope()
                        val poll = remember { downloadsStatsPoll(repo) }
                        LaunchedEffect(poll) { poll.refreshOnce() }
                        Box(Modifier.statusBarsPadding().padding(12.dp)) { DownloadsStatsHeader(poll, scope) }
                    }
                }
            }
        }
        composeRule.waitForIdle()
        ScreenshotGoldens.capture(composeRule.onRoot(), name)
        // After the capture, so a failing check still leaves the image.
        when (page) {
            Page.CLIENTS -> assertClientsPage()
            Page.STATS -> assertStatsHeader()
        }
    }

    private fun assertClientsPage() {
        // The answer arrived: cards of both kinds are on screen.
        composeRule.onNode(hasText("vault-agent · 10.0.0.42", substring = true), useUnmergedTree = true).assertExists()
        // WP D15-RUNNER: the prefill runner is its own card (second: connected,
        // as recent as the online PC) and counts like the web counts it.
        composeRule.onNode(hasText("This server's prefill downloads · 172.18.0.1"), useUnmergedTree = true).assertExists()
        composeRule.onNode(hasText("5 clients · 3 connected"), useUnmergedTree = true).assertExists()
        // Review item: with four items every nav label is ONE line -- the
        // long "Downloads" is as tall as the short "Settings".
        val downloads = composeRule.onNode(hasText("Downloads"), useUnmergedTree = true).fetchSemanticsNode().size.height
        val settings = composeRule.onNode(hasText("Settings"), useUnmergedTree = true).fetchSemanticsNode().size.height
        assertEquals("the Downloads nav label wrapped (height $downloads vs Settings $settings)", settings, downloads)
    }

    private fun assertStatsHeader() {
        for (label in listOf("Current speed", "Average while loading", "Peak speed", "Served (cache + prefill)", "Loaded from the internet", "Hit rate", "Saved by the cache", "Loading now")) {
            composeRule.onNode(hasContentDescription("$label: ", substring = true)).assertExists()
        }
        // WP GRAPH-ZOOM: a fresh TrafficRanges opens on 6 h, the 1-minute tier.
        val graph = composeRule.onNode(hasContentDescription("Average speed per minute in the last 6 hours.", substring = true))
        graph.assertExists()
        assertTrue(composeRule.onAllNodes(hasContentDescription("Not available", substring = true)).fetchSemanticsNodes().isEmpty())
    }

    /** The demo's traffic builders over a fixed snapshot and clock. */
    private class FixedTrafficRepository : TrafficRepository {
        override suspend fun throughput(range: String): ThroughputOut = demoThroughput(range, SNAPSHOT, NOW)
        override suspend fun series(range: String): SeriesOut = demoSeries(range, NOW)
        override suspend fun throughputLive(): LiveOut = demoLive(NOW)
        override suspend fun clientsOverview(range: String): ClientsOverviewOut = demoClientsOverview(range, SNAPSHOT, NOW)
        override suspend fun jobHistory(range: String, limit: Int): List<JobSummary> = emptyList()
    }

    private companion object {
        /** An evening (UTC), so the demo's daily rhythm draws a real graph. */
        val NOW: Long = Instant.parse("2026-10-14T19:30:00Z").toEpochMilli()

        fun ago(seconds: Long): String = Instant.ofEpochMilli(NOW).minusSeconds(seconds).toString()

        val SNAPSHOT = DemoTrafficSnapshot(
            runningJobs = listOf(
                JobSummary(id = 901, appid = 4_010_030, type = "prefill", status = "running", created_at = ago(120), started_at = ago(90)),
            ),
            games = listOf(
                GameSummary(
                    appid = 4_010_010, name = "Duskfall Array", status = "done", depot_count = 1,
                    installed_on = listOf(InstalledOnEntry(client_id = "demo-livingroom-pc", reported_at = ago(600))),
                ),
                GameSummary(appid = 4_010_020, name = "Cobalt Isthmus", status = "idle", depot_count = 1),
                GameSummary(appid = 4_010_030, name = "Wrenfield Static", status = "running", depot_count = 1),
                GameSummary(appid = 4_010_040, name = "Halcyon Ledger", status = "done", depot_count = 1),
            ),
            agents = listOf(
                ClientOut(
                    client_id = "demo-livingroom-pc", first_seen = ago(30 * 86_400L), last_reported_at = ago(600),
                    source_addrs = listOf("10.0.0.42"), last_seen_in_cache_log = ago(600), presence = "online",
                ),
                ClientOut(
                    client_id = "demo-steamdeck", first_seen = ago(10 * 86_400L), last_reported_at = ago(7_200),
                    source_addrs = listOf("10.0.0.57"), last_seen_in_cache_log = ago(7_200), presence = "offline",
                ),
            ),
        )
    }
}

/** Same reason as `LibraryScreenshotTest`'s rule: register the bare host
 * activity with Robolectric for the test only, so the APK stays untouched.
 * Must run before the compose rule (order 0). */
private class RegisterClientsHostActivityRule : TestRule {
    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            val app = RuntimeEnvironment.getApplication()
            shadowOf(app.packageManager).addActivityIfNotPresent(ComponentName(app, ComponentActivity::class.java))
            base.evaluate()
        }
    }
}
