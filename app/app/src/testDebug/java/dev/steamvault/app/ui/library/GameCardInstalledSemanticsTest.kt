package dev.steamvault.app.ui.library

import android.app.Application
import android.content.ComponentName
import androidx.activity.ComponentActivity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import coil.Coil
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.InstalledBadge
import dev.steamvault.app.ui.library.logic.buildGameCardModel
import dev.steamvault.app.ui.status.StatusKind
import dev.steamvault.app.ui.theme.SteamVaultTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
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

/**
 * ANDROID-INSTALLED-NITS-1: what TalkBack hears for the grid card's
 * installed badge (twin of web `installedAriaFragment`, components/
 * game-card.js). Semantics only, no screenshot and no golden: a GameCard
 * for a game installed on two clients with nothing cached.
 *
 *  - Status "Not cached" (kind NONE): the badge Text still SHOWS
 *    "Installed, not cached · <client>", but its description is the plain
 *    "Installed on <every client>", so "not cached" is not spoken twice.
 *  - Any other status (here a running download): the description keeps the
 *    not-cached wording, naming every client.
 *  - The card is one clickable object, so its MERGED node carries the
 *    badge's description (that merged node is what TalkBack reads).
 *
 * Same Robolectric set-up as `LibraryScreenshotTest` (plain Application,
 * the bare host activity registered, [OfflineImageLoader] so the cover
 * never touches the network).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = Application::class)
class GameCardInstalledSemanticsTest {

    @get:Rule(order = 0)
    val hostActivity: TestRule = RegisterCardHostActivityRule()

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
    fun `MUTATION TARGET -- a Not cached card speaks its badge as plain installed on, naming every client`() {
        val model = buildGameCardModel(GAME, liveJob = null, selected = false, selecting = false)
        assertEquals("precondition: status word Not cached", StatusKind.NONE, model.kind)
        assertTrue("precondition: installed, not cached", model.installedBadge is InstalledBadge.InstalledNotCached)
        render(model)

        val badge = unmergedBadgeNode(VISIBLE_BADGE_TEXT)
        val description = descriptionOf(badge)
        assertEquals("the badge's spoken text", "Installed on $NAMES", description)
        assertFalse("'not cached' must not be spoken twice: $description", description.contains("not cached", ignoreCase = true))
        assertMergedCardCarries(description)
    }

    @Test
    fun `a card with another status word keeps the not-cached wording in its description`() {
        val model = buildGameCardModel(GAME, liveJob = RUNNING_JOB, selected = false, selecting = false)
        assertEquals("precondition: a running download", StatusKind.RUNNING, model.kind)
        assertTrue("precondition: installed, not cached", model.installedBadge is InstalledBadge.InstalledNotCached)
        render(model)

        val description = descriptionOf(unmergedBadgeNode(VISIBLE_BADGE_TEXT))
        assertEquals("the badge's spoken text", "Installed, not cached · $NAMES", description)
        assertMergedCardCarries(description)
    }

    private fun render(model: GameCardModel) {
        composeRule.setContent {
            SteamVaultTheme {
                GameCard(
                    model = model,
                    selecting = false,
                    onOpen = {},
                    onLongPress = {},
                    onToggleSelect = {},
                    onAction = { _, _ -> },
                )
            }
        }
        composeRule.waitForIdle()
    }

    /** The one unmerged node whose visible text contains [visibleText]
     * (the badge Text, "(+1 more)" follows the client). */
    private fun unmergedBadgeNode(visibleText: String): SemanticsNode {
        val nodes = composeRule
            .onAllNodes(hasText(visibleText, substring = true), useUnmergedTree = true)
            .fetchSemanticsNodes()
        assertEquals("unmerged nodes showing '$visibleText'", 1, nodes.size)
        return nodes.single()
    }

    private fun descriptionOf(node: SemanticsNode): String {
        val descriptions = checkNotNull(node.config.getOrNull(SemanticsProperties.ContentDescription)) {
            "the badge Text has no content description"
        }
        return descriptions.joinToString(" ")
    }

    /** The card's merged, clickable node includes the badge's description. */
    private fun assertMergedCardCarries(description: String) {
        val merged = composeRule.onAllNodes(hasContentDescription(description)).fetchSemanticsNodes()
        assertEquals("merged nodes carrying '$description'", 1, merged.size)
        val card = merged.single()
        assertTrue("the merged node is not the clickable card", card.config.contains(SemanticsActions.OnClick))
        val texts = card.config.getOrNull(SemanticsProperties.Text).orEmpty().map { it.text }
        assertTrue("the merged node is not this game's card: $texts", texts.any { it == GAME_NAME })
    }

    private companion object {
        const val GAME_NAME = "Lantern Vale"
        const val PRIMARY_CLIENT = "deck-one"
        const val NAMES = "deck-one, frame-pc"
        const val VISIBLE_BADGE_TEXT = "Installed, not cached · $PRIMARY_CLIENT"

        /** Synthetic appid and names (docs/learnings "Fixtures: synthetic
         * only"); idle, nothing cached, installed on two clients. The first
         * entry is the newer report, so it is the badge's primary client. */
        val GAME = GameSummary(
            appid = 4_020_010,
            name = GAME_NAME,
            status = "idle",
            depot_count = 0,
            installed_on = listOf(
                InstalledOnEntry(client_id = PRIMARY_CLIENT, reported_at = "2026-10-14T18:30:00Z"),
                InstalledOnEntry(client_id = "frame-pc", reported_at = "2026-10-14T18:00:00Z"),
            ),
        )

        val RUNNING_JOB = JobSummary(
            id = 4_020_901,
            appid = 4_020_010,
            type = "prefill",
            status = "running",
            created_at = "2026-10-14T18:31:00Z",
            started_at = "2026-10-14T18:31:05Z",
        )
    }
}

/** Same reason as `LibraryScreenshotTest`'s rule: register the bare host
 * activity with Robolectric for the test only. Must run before the compose
 * rule (order 0). Own name: a private top-level class still takes a JVM
 * class name in this package. */
private class RegisterCardHostActivityRule : TestRule {
    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            val app = RuntimeEnvironment.getApplication()
            shadowOf(app.packageManager).addActivityIfNotPresent(ComponentName(app, ComponentActivity::class.java))
            base.evaluate()
        }
    }
}
