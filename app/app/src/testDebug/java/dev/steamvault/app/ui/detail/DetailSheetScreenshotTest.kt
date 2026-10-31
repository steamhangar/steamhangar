package dev.steamvault.app.ui.detail

import android.app.Application
import android.content.ComponentName
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.BottomSheetDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Surface
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onRoot
import coil.Coil
import dev.steamvault.app.R
import dev.steamvault.app.demo.DEMO_GROUP_PARENT_APPID
import dev.steamvault.app.demo.DemoCacheRepository
import dev.steamvault.app.demo.DemoJobsRepository
import dev.steamvault.app.demo.DemoMappingRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.OffCardInfoRepository
import dev.steamvault.app.ui.library.OfflineImageLoader
import dev.steamvault.app.ui.library.ScreenshotGoldens
import dev.steamvault.app.ui.library.logic.RepairWording
import dev.steamvault.app.ui.library.logic.isDamagedOnly
import dev.steamvault.app.ui.library.logic.repairSpokenLabel
import dev.steamvault.app.ui.theme.SteamVaultTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
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
 * Screenshot tests of the OPEN game detail sheet (WP ANDROID-REDESIGN-1c,
 * the ANDROID-REDESIGN-1b review should-fix): one golden per tab --
 * Overview, Depots, Maintenance -- at 360 dp and font scale 1.3.
 *
 * Robolectric's root capture does not include a `ModalBottomSheet` dialog
 * window (docs/LEARNINGS.md), so this renders the sheet's content
 * composable [GameDetailSheetBody] directly, on the sheet's own container
 * colour; [GameDetailSheet] calls the same composable inside its sheet.
 *
 * Data: the demo's content-package group (Emberline Cartography, appid
 * [DEMO_GROUP_PARENT_APPID]): a game with three content packages whose
 * own cache is short only of wrong-size chunks, so it is Repair-eligible
 * (Repair in the header and on Maintenance). The demo stamps its times from
 * the wall clock; every timestamp is replaced by [FIXED_TIME], so each
 * golden is reproducible. Cover art fails offline at once
 * ([OfflineImageLoader]), so the header shows the fallback tile.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], application = Application::class)
class DetailSheetScreenshotTest {

    @get:Rule(order = 0)
    val hostActivity: TestRule = RegisterDetailHostActivityRule()

    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Before
    fun installOfflineImageLoader() {
        Coil.setImageLoader(OfflineImageLoader.create(RuntimeEnvironment.getApplication()))
    }

    @After
    fun resetImageLoader() {
        Coil.reset()
    }

    @Test
    fun detail_overview_360dp_font130() = shoot("detail_overview_360dp_font130", DetailTab.OVERVIEW)

    @Test
    fun detail_depots_360dp_font130() = shoot("detail_depots_360dp_font130", DetailTab.DEPOTS)

    @Test
    fun detail_maintenance_360dp_font130() = shoot("detail_maintenance_360dp_font130", DetailTab.MAINTENANCE)

    @OptIn(ExperimentalMaterial3Api::class)
    private fun shoot(name: String, tab: DetailTab) {
        RuntimeEnvironment.setQualifiers("w360dp-h1400dp-night-420dpi")
        RuntimeEnvironment.setFontScale(1.3f)
        composeRule.activityRule.scenario.recreate()

        val demo = DemoState.fresh()
        val games = demo.listGameSummaries().map { it.pinned() }
        val parent = games.single { it.appid == DEMO_GROUP_PARENT_APPID }
        assertTrue("the demo group's game must be Repair-eligible (damaged chunks only)", isDamagedOnly(parent))
        assertTrue("the demo group's game must have content packages", games.any { it.parent_appid == DEMO_GROUP_PARENT_APPID })

        composeRule.setContent {
            SteamVaultTheme {
                Surface(color = BottomSheetDefaults.ContainerColor, modifier = Modifier.fillMaxSize()) {
                    val scope = rememberCoroutineScope()
                    val controller = remember {
                        DetailController(
                            PinnedGamesRepository(demo),
                            DemoJobsRepository(demo),
                            DemoMappingRepository(demo),
                            DemoCacheRepository(demo),
                            OffCardInfoRepository,
                            AndroidDetailStrings(composeRule.activity.resources),
                        )
                    }
                    LaunchedEffect(controller) { controller.open(scope, DEMO_GROUP_PARENT_APPID, parent.name, isKnownToVault = true) }
                    if (controller.openAppid != null) {
                        GameDetailSheetBody(
                            controller = controller,
                            appid = DEMO_GROUP_PARENT_APPID,
                            games = games,
                            jobs = emptyList(),
                            scope = scope,
                            onLibraryChanged = {},
                            initialTab = tab,
                        )
                    }
                }
            }
        }
        composeRule.waitForIdle()
        awaitLoadedSheet()

        ScreenshotGoldens.capture(composeRule.onRoot(), name)
        // After the capture, so a failing check still leaves the image.
        assertSheet(tab, parent.name.orEmpty())
    }

    /** The detail answer arrived and the cover flipped to its fallback tile. */
    private fun awaitLoadedSheet() {
        repeat(STABLE_POLL_LIMIT) {
            composeRule.waitForIdle()
            val tabs = composeRule.onAllNodes(hasTestTag(DETAIL_TABS_TAG)).fetchSemanticsNodes().size
            val covers = composeRule
                .onAllNodes(hasContentDescription(COVER_FALLBACK_PREFIX, substring = true), useUnmergedTree = true)
                .fetchSemanticsNodes().size
            if (tabs == 1 && covers > 0) return
            composeRule.mainClock.advanceTimeByFrame()
        }
        fail("the detail sheet never finished loading within $STABLE_POLL_LIMIT idles")
    }

    private fun assertSheet(tab: DetailTab, title: String) {
        val res = composeRule.activity.resources
        // The tab row is there and the requested tab is the selected one.
        val selectedLabel = when (tab) {
            DetailTab.OVERVIEW -> res.getString(R.string.detail_tab_overview)
            DetailTab.DEPOTS -> res.getString(R.string.detail_tab_depots)
            DetailTab.MAINTENANCE -> res.getString(R.string.detail_tab_maintenance)
        }
        val selected = composeRule
            .onAllNodes(SemanticsMatcher.expectValue(SemanticsProperties.Selected, true))
            .fetchSemanticsNodes()
            .map { node -> node.config.getOrNull(SemanticsProperties.Text).orEmpty().joinToString("") { it.text } }
        assertEquals("selected tab", listOf(selectedLabel), selected)
        // Repair in the header on every tab (REDESIGN-1b).
        composeRule.onAllNodes(hasContentDescription(repairSpokenLabel(title))).fetchSemanticsNodes().let {
            assertTrue("no Repair button for '$title'", it.isNotEmpty())
        }
        when (tab) {
            DetailTab.OVERVIEW -> {
                composeRule.onNode(hasTestTag(DETAIL_OVERVIEW_GROUP_LINE_TAG), useUnmergedTree = true).assertExists()
                composeRule.onNode(hasTestTag(PACKAGES_SECTION_TAG), useUnmergedTree = true).assertExists()
            }
            DetailTab.DEPOTS ->
                assertTrue(
                    "no Depots heading",
                    composeRule.onAllNodes(hasText(res.getString(R.string.detail_depots_heading)), useUnmergedTree = true)
                        .fetchSemanticsNodes().size >= 2, // the tab and the heading
                )
            DetailTab.MAINTENANCE -> {
                composeRule.onNode(hasTestTag(DETAIL_COMPLETE_LINE_TAG), useUnmergedTree = true).assertExists()
                // Header and the Verify box each offer Repair.
                assertEquals(
                    "Repair buttons (header + Maintenance)",
                    2,
                    composeRule.onAllNodes(hasText(RepairWording.LABEL), useUnmergedTree = true).fetchSemanticsNodes().size,
                )
            }
        }
    }

    /** The demo's games, each detail with its times pinned. */
    private class PinnedGamesRepository(private val demo: DemoState) : GamesRepository {
        override suspend fun list(): List<GameSummary> = demo.listGameSummaries().map { it.pinned() }
        override suspend fun detail(appid: Int): GameDetail = demo.gameDetail(appid).pinned()
    }

    private companion object {
        const val STABLE_POLL_LIMIT = 50

        /** Prefix of `R.string.library_cover_fallback_description`. */
        const val COVER_FALLBACK_PREFIX = "No cover art available for"
    }
}

/** Every wall-clock time of the demo, replaced (see the class kdoc). */
private const val FIXED_TIME = "2026-10-14T18:30:00Z"

private fun String?.fixed(): String? = this?.let { FIXED_TIME }

private fun GameSummary.pinned(): GameSummary = copy(
    last_prefill_at = last_prefill_at.fixed(),
    last_manifest_check = last_manifest_check.fixed(),
    update_checked_at = update_checked_at.fixed(),
    cache_complete_checked_at = cache_complete_checked_at.fixed(),
    installed_on = installed_on.map { it.copy(reported_at = FIXED_TIME) },
)

private fun GameDetail.pinned(): GameDetail = copy(
    last_prefill_at = last_prefill_at.fixed(),
    last_manifest_check = last_manifest_check.fixed(),
    cache_complete_checked_at = cache_complete_checked_at.fixed(),
    installed_on = installed_on.map { it.copy(reported_at = FIXED_TIME) },
)

/** Same reason as `LibraryScreenshotTest`'s rule: register the bare host
 * activity with Robolectric for the test only. Must run before the compose
 * rule (order 0). */
private class RegisterDetailHostActivityRule : TestRule {
    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            val app = RuntimeEnvironment.getApplication()
            shadowOf(app.packageManager).addActivityIfNotPresent(ComponentName(app, ComponentActivity::class.java))
            base.evaluate()
        }
    }
}
