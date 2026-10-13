package dev.steamvault.app.ui.library

import android.app.Application
import android.content.ComponentName
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.statusBars
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performScrollTo
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import coil.Coil
import coil.ImageLoader
import coil.intercept.Interceptor
import coil.request.ErrorResult
import coil.request.ImageResult
import com.github.takahirom.roborazzi.ExperimentalRoborazziApi
import com.github.takahirom.roborazzi.RoborazziOptions
import com.github.takahirom.roborazzi.RoborazziTaskType
import com.github.takahirom.roborazzi.captureRoboImage
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.downloads.logic.countPending
import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.nav.Destination
import dev.steamvault.app.ui.nav.MainShell
import dev.steamvault.app.ui.theme.SteamVaultTheme
import java.io.File
import kotlin.math.roundToInt
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
 * Screenshot tests of the Library screen (WP APP-TEST-1), Roborazzi on
 * Robolectric. They showed D12 (`docs/handover/post-0.1.0-roadmap.md`:
 * FilterChipsRow does not scroll, the layout toggle wraps, an empty area,
 * a tall header) red first; WP D12 fixed it and added [assertLayout],
 * which names the layout properties each shot must keep.
 *
 * Profiles (brief): 411 dp wide (Pixel reference) and 360 dp, font scale
 * 1.0 and 1.3, plus dark mode. The app theme is single-dark on purpose
 * (`ui/theme/Theme.kt`), so the `night` shot is expected to match its
 * `notnight` twin; it is there to catch a future change that starts
 * reading the system setting. One extra shot covers the List layout in the
 * worst-case profile.
 *
 * What is rendered is the screen INSIDE the app shell: edge-to-edge
 * window and the real [MainShell] that `MainActivity.setContent` uses
 * (outer Scaffold with the bottom navigation bar, its inset handling).
 * Rendering `LibraryScreen` alone would hide exactly the nesting that
 * caused D12's tall header. Robolectric reports zero system-bar insets on
 * its own, so [simulateSystemBars] dispatches fixed ones (status 24 dp,
 * navigation 48 dp) -- an approximation of a phone, constant across runs.
 *
 * Determinism: fixed fixtures ([LibraryScreenshotFixtures]); cover art
 * never touches the network -- [OfflineImageLoader] fails every request at
 * once, so each card shows `CoverArtImage`'s fallback tile (CI runners do
 * have internet, and a real Steam CDN image would make the goldens
 * flaky); infinite animations are cancelled by the Compose test clock.
 *
 * Golden handling lives in [ScreenshotGoldens] (verify existing, record
 * missing, never fail on a missing one). See app/README.md "Screenshot
 * tests" for the CI flow and how goldens get checked in.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
// A plain Application: VaultApplication.onCreate schedules WorkManager
// polling, which has no business in a rendering test.
@Config(sdk = [35], application = Application::class)
class LibraryScreenshotTest {

    @get:Rule(order = 0)
    val hostActivity: TestRule = RegisterHostActivityRule()

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
    fun library_411dp_font100() = shoot("library_411dp_font100", PIXEL_411)

    @Test
    fun library_411dp_font130() = shoot("library_411dp_font130", PIXEL_411, fontScale = 1.3f)

    @Test
    fun library_360dp_font100() = shoot("library_360dp_font100", SMALL_360)

    @Test
    fun library_360dp_font130() = shoot("library_360dp_font130", SMALL_360, fontScale = 1.3f)

    @Test
    fun library_411dp_font100_night() = shoot("library_411dp_font100_night", PIXEL_411, night = true)

    @Test
    fun library_360dp_font130_list() =
        shoot("library_360dp_font130_list", SMALL_360, fontScale = 1.3f, layout = LibraryLayout.LIST)

    private fun shoot(
        name: String,
        screen: ScreenSize,
        fontScale: Float = 1.0f,
        night: Boolean = false,
        layout: LibraryLayout = LibraryLayout.GRID_2,
    ) {
        // The rule already launched the host activity with the @Config
        // defaults; apply this profile and recreate it so the new
        // configuration (width, density, night, font scale) is the one the
        // activity is created with. setQualifiers first: it rebuilds the
        // configuration, setFontScale then adjusts it.
        RuntimeEnvironment.setQualifiers(screen.qualifiers(night))
        RuntimeEnvironment.setFontScale(fontScale)
        composeRule.activityRule.scenario.recreate()
        composeRule.runOnUiThread { composeRule.activity.enableEdgeToEdge() }

        composeRule.setContent {
            SteamVaultTheme { AppShell(layout) }
        }
        composeRule.waitForIdle()
        simulateSystemBars()
        awaitStableFallbackTiles()
        // Again right before the capture: the frames advanced while waiting
        // for the tiles must not have replaced the simulated insets.
        assertSystemBarsArrived()

        ScreenshotGoldens.capture(composeRule.onRoot(), name)
        // After the capture, so a failing check still leaves the image.
        assertLayout()
    }

    /**
     * WP D12 layout checks, in every profile (the goldens show the layout,
     * these name what must hold):
     * - no doubled status-bar inset: the search field sits right under ONE
     *   status bar plus the TopAppBar;
     * - every filter chip is single-line (all the same height) and
     *   reachable by scrolling the chip row;
     * - the cards start right under the controls, not below an empty band.
     */
    private fun assertLayout() {
        val density = composeRule.activity.resources.displayMetrics.density
        fun dp(px: Float) = px / density

        val searchTop = dp(composeRule.onNode(hasSetTextAction()).fetchSemanticsNode().boundsInRoot.top)
        val maxSearchTop = STATUS_BAR_DP + TOP_APP_BAR_DP + TOOLBAR_TOP_PADDING_DP + LAYOUT_TOLERANCE_DP
        assertTrue(
            "search field top ${searchTop}dp is below ${maxSearchTop}dp: the status-bar inset is applied twice",
            searchTop <= maxSearchTop,
        )

        val chipMatcher = SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Checkbox)
        val chips = composeRule.onAllNodes(chipMatcher).fetchSemanticsNodes()
        assertEquals("filter chips rendered", EXPECTED_CHIPS, chips.size)
        val heights = chips.map { dp(it.boundsInRoot.height) }
        assertTrue(
            "filter chips differ in height ($heights dp): a label wrapped to more than one line",
            heights.max() - heights.min() <= LAYOUT_TOLERANCE_DP,
        )
        val chipsBottom = dp(chips.maxOf { it.boundsInRoot.bottom })
        for (index in chips.indices) {
            composeRule.onAllNodes(chipMatcher)[index].performScrollTo().assertIsDisplayed()
        }

        val coversTop = dp(
            composeRule
                .onAllNodes(hasContentDescription(COVER_FALLBACK_PREFIX, substring = true), useUnmergedTree = true)
                .fetchSemanticsNodes()
                .minOf { it.boundsInRoot.top },
        )
        assertTrue(
            "first card starts at ${coversTop}dp, more than ${MAX_CHIPS_TO_CARDS_DP}dp below the chips (${chipsBottom}dp)",
            coversTop - chipsBottom <= MAX_CHIPS_TO_CARDS_DP,
        )
        composeRule
            .onAllNodes(hasContentDescription(COVER_FALLBACK_PREFIX, substring = true), useUnmergedTree = true)
            .onFirst()
            .assertIsDisplayed()
    }

    private fun simulateSystemBars() {
        composeRule.runOnUiThread {
            val decor = composeRule.activity.window.decorView
            val density = decor.resources.displayMetrics.density
            val insets = WindowInsetsCompat.Builder()
                .setInsets(WindowInsetsCompat.Type.statusBars(), Insets.of(0, (STATUS_BAR_DP * density).roundToInt(), 0, 0))
                .setVisible(WindowInsetsCompat.Type.statusBars(), true)
                .setInsets(WindowInsetsCompat.Type.navigationBars(), Insets.of(0, 0, 0, (NAV_BAR_DP * density).roundToInt()))
                .setVisible(WindowInsetsCompat.Type.navigationBars(), true)
                .build()
            ViewCompat.dispatchApplyWindowInsets(decor, insets)
        }
        composeRule.waitForIdle()
        assertSystemBarsArrived()
    }

    /**
     * The dispatch must actually reach Compose's WindowInsets, or every
     * golden silently shows a phone without system bars (Robolectric
     * re-dispatching its own empty insets would do exactly that).
     */
    private fun assertSystemBarsArrived() {
        val density = composeRule.activity.resources.displayMetrics.density
        val expected = SystemBarsPx(
            top = (STATUS_BAR_DP * density).roundToInt(),
            bottom = (NAV_BAR_DP * density).roundToInt(),
        )
        assertEquals(
            "simulated system-bar insets (status ${STATUS_BAR_DP}dp, navigation ${NAV_BAR_DP}dp) did not reach " +
                "Compose's WindowInsets -- the screenshot would show no system bars",
            expected,
            observedSystemBars,
        )
    }

    /**
     * Every visible card's cover request fails in [OfflineImageLoader] and
     * flips that card to its fallback tile. Waits until the number of
     * fallback tiles is non-zero AND unchanged across two consecutive
     * idles, so a capture never lands while later cards are still flipping.
     */
    private fun awaitStableFallbackTiles() {
        var previous = -1
        repeat(STABLE_POLL_LIMIT) {
            composeRule.waitForIdle()
            val count = composeRule
                .onAllNodes(hasContentDescription(COVER_FALLBACK_PREFIX, substring = true), useUnmergedTree = true)
                .fetchSemanticsNodes().size
            if (count > 0 && count == previous) return
            previous = count
            composeRule.mainClock.advanceTimeByFrame()
        }
        fail("cover fallback tiles never settled within $STABLE_POLL_LIMIT idles (last count: $previous)")
    }

    /** `MainActivity.setContent`'s main shell ([MainShell], shared since
     * WP D12) with Library selected. */
    @Composable
    private fun AppShell(layout: LibraryLayout) {
        val fixtures = LibraryScreenshotFixtures
        val ownedLibrary = remember {
            OwnedLibraryController(
                FixedSettingsRepository(fixtures.settings),
                FixedSteamRelayRepository(fixtures.ownedGames),
            )
        }
        var jobsSnapshot by remember { mutableStateOf(emptyList<JobSummary>()) }
        // Read here so simulateSystemBars can assert its insets arrived.
        val density = LocalDensity.current
        val bars = SystemBarsPx(
            top = WindowInsets.statusBars.getTop(density),
            bottom = WindowInsets.navigationBars.getBottom(density),
        )
        SideEffect { observedSystemBars = bars }
        MainShell(
            current = Destination.LIBRARY,
            pendingJobsCount = countPending(jobsSnapshot),
            onSelect = {},
        ) {
            LibraryScreen(
                    gamesRepository = remember { FixedGamesRepository(fixtures.vaultGames) },
                jobsRepository = remember { FixedJobsRepository(fixtures.jobs) },
                mappingRepository = remember { EmptyMappingRepository() },
                cacheRepository = remember { UnusedCacheRepository() },
                updatesRepository = remember { FixedUpdatesRepository() },
                ownedLibrary = ownedLibrary,
                libraryPreferences = remember { FixedLibraryPreferences(layout) },
                onJobsSnapshot = { jobsSnapshot = it },
                onOpenSettings = {},
                demoMode = false,
            )
        }
    }

    /** Last system-bar insets Compose saw in [AppShell], in px. */
    private var observedSystemBars: SystemBarsPx? = null

    data class SystemBarsPx(val top: Int, val bottom: Int)

    /** Robolectric resolution qualifiers for one screen size. */
    data class ScreenSize(val widthDp: Int, val heightDp: Int) {
        fun qualifiers(night: Boolean): String =
            "w${widthDp}dp-h${heightDp}dp-${if (night) "night" else "notnight"}-${DENSITY}"
    }

    private companion object {
        /** Pixel reference width (brief); 891 dp tall like a current Pixel. */
        val PIXEL_411 = ScreenSize(widthDp = 411, heightDp = 891)

        /** Common small-phone width. */
        val SMALL_360 = ScreenSize(widthDp = 360, heightDp = 780)

        /** One density for every profile: layout depends on dp only, and a
         * shared density keeps the goldens comparable side by side. */
        const val DENSITY = "420dpi"

        const val STABLE_POLL_LIMIT = 50

        const val STATUS_BAR_DP = 24
        const val NAV_BAR_DP = 48

        /** Material 3 small TopAppBar container height. */
        const val TOP_APP_BAR_DP = 64

        /** `LibraryToolbar`'s vertical padding above the search field. */
        const val TOOLBAR_TOP_PADDING_DP = 8
        const val LAYOUT_TOLERANCE_DP = 4

        /** Chip row padding + grid content padding, with slack; the D12
         * bug left ~130 dp here at 411 dp. */
        const val MAX_CHIPS_TO_CARDS_DP = 32

        /** All, Cached, Not cached, Downloading, Failed, Updates. */
        const val EXPECTED_CHIPS = 6

        /** Prefix of `R.string.library_cover_fallback_description`. */
        const val COVER_FALLBACK_PREFIX = "No cover art available for"
    }
}

/**
 * `createAndroidComposeRule<ComponentActivity>()` launches a bare
 * `ComponentActivity`, which the app manifest does not declare.
 * `androidx.compose.ui:ui-test-manifest` would add it -- but as a
 * debugImplementation, i.e. as an exported activity inside every debug APK.
 * Registering it with Robolectric's package manager for the test alone
 * keeps the APK untouched. Must run before the compose rule (order 0).
 */
private class RegisterHostActivityRule : TestRule {
    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            val app = RuntimeEnvironment.getApplication()
            shadowOf(app.packageManager).addActivityIfNotPresent(ComponentName(app, ComponentActivity::class.java))
            base.evaluate()
        }
    }
}

/** Coil loader that answers every request with an immediate error, so
 * `CoverArtImage` deterministically shows its offline fallback tile.
 * `awaitStableFallbackTiles` relies on Coil running this interceptor on
 * the Main dispatcher (Robolectric's paused looper), so `waitForIdle`
 * covers it; a loader with its own `interceptorDispatcher` would break that. */
private object OfflineImageLoader {
    fun create(app: Application): ImageLoader = ImageLoader.Builder(app)
        .components {
            add(
                object : Interceptor {
                    override suspend fun intercept(chain: Interceptor.Chain): ImageResult = ErrorResult(
                        drawable = null,
                        request = chain.request,
                        throwable = IllegalStateException("offline in screenshot tests"),
                    )
                },
            )
        }
        .build()
}

/**
 * Golden handling (WP APP-TEST-1), decided per image so a partly populated
 * golden directory works:
 *
 * - golden exists -> VERIFY: a pixel difference fails the test; Roborazzi
 *   writes `<name>_compare.png` and `<name>_actual.png` to [diffDir].
 * - golden missing -> RECORD into [newGoldenDir] and pass. CI uploads that
 *   directory as an artifact; a human reviews the images and commits them
 *   to [goldenDir] (app/README.md "Screenshot tests").
 * - `-Psteamhangar.screenshots.record=true` -> RECORD every image straight
 *   into [goldenDir] (overwriting).
 *
 * Paths are relative to Gradle's test working directory, the `:app`
 * module directory (the same assumption the source-scan tests in src/test
 * make).
 */
@OptIn(ExperimentalRoborazziApi::class)
internal object ScreenshotGoldens {
    private const val RECORD_PROPERTY = "steamhangar.screenshots.record"

    val goldenDir = File("src/testDebug/screenshots")
    private val outputDir = File("build/outputs/roborazzi")
    val newGoldenDir = File(outputDir, "new-goldens")
    val diffDir = File(outputDir, "diffs")

    fun capture(node: SemanticsNodeInteraction, name: String) {
        check(File("src/main/AndroidManifest.xml").isFile) {
            "screenshot tests must run with the :app module as working directory, got ${File(".").absolutePath}"
        }
        val golden = File(goldenDir, "$name.png")
        when {
            System.getProperty(RECORD_PROPERTY) == "true" ->
                node.captureRoboImage(golden, options(RoborazziTaskType.Record))
            golden.isFile ->
                node.captureRoboImage(golden, options(RoborazziTaskType.Verify))
            else -> {
                val recorded = File(newGoldenDir, "$name.png")
                node.captureRoboImage(recorded, options(RoborazziTaskType.Record))
                println("Screenshot golden missing: recorded ${recorded.path}; review it and check it in as ${golden.path}")
            }
        }
    }

    private fun options(taskType: RoborazziTaskType) = RoborazziOptions(
        taskType = taskType,
        compareOptions = RoborazziOptions.CompareOptions(outputDirectoryPath = diffDir.path),
    )
}
