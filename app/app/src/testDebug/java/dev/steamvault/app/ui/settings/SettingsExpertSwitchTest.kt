package dev.steamvault.app.ui.settings

import android.app.Application
import android.content.ComponentName
import androidx.activity.ComponentActivity
import androidx.compose.material3.SwitchColors
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import dev.steamvault.app.R
import dev.steamvault.app.demo.DemoAboutRepository
import dev.steamvault.app.demo.DemoScheduleRepository
import dev.steamvault.app.demo.DemoSettingsRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.demo.DemoSteamRelayRepository
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.ui.settings.logic.SettingsLayout
import dev.steamvault.app.ui.theme.SteamVaultTheme
import dev.steamvault.app.ui.theme.VaultColors
import dev.steamvault.app.ui.theme.vaultSwitchColors
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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

/**
 * WP APP-EXPERT-SWITCH-1 (operator report 2026-11-01): the "Show expert
 * settings" switch read as disabled while off and sat below the About
 * disclaimer. Semantics and colours only, no screenshot and no golden.
 *
 *  - Off-state colours: the real [vaultSwitchColors] (web `.sw`) read in
 *    composition. The --raised-2 track is only 1.40:1 on Bg, so the edge
 *    carries the control: [VaultColors.LineStrong], 3:1 or more on Bg and
 *    Surface (WCAG 1.4.11, DESIGN.md "Contrast floors"); the old --line
 *    edge was 1.52:1. Ratios use Compose's 8-bit ARGB storage
 *    (`toArgb()`, the accent glow is 41/255 and its composite is rounded per
 *    channel), the twin of web css-contrast.test.js.
 *  - Placement: in the About panel the switch row follows the component
 *    list (below Refresh) and the trademark line closes the panel (web
 *    compact layout, settings-expert-placement.test.js).
 *  - The row is one switch for TalkBack and touch: Role.Switch, off, and a
 *    tap anywhere on it turns expert mode on.
 *
 * Same Robolectric set-up as `GameCardInstalledSemanticsTest` (plain
 * Application, the bare host activity registered).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = Application::class)
class SettingsExpertSwitchTest {

    @get:Rule(order = 0)
    val hostActivity: TestRule = RegisterExpertSwitchHostActivityRule()

    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun `MUTATION TARGET -- the off switch has a line-strong edge, 3 to 1 on Bg and Surface, its knob 3 to 1 on the track`() {
        var captured: SwitchColors? = null
        composeRule.setContent { SteamVaultTheme { captured = vaultSwitchColors() } }
        composeRule.waitForIdle()
        val colors = checkNotNull(captured) { "vaultSwitchColors() was not composed" }

        // Off: --raised-2 track, --line-strong edge, --dim knob.
        assertEquals("off track (web .sw background)", VaultColors.Raised2, colors.uncheckedTrackColor)
        assertEquals("off edge (web .sw border, APP-EXPERT-SWITCH-1)", VaultColors.LineStrong, colors.uncheckedBorderColor)
        assertEquals("off knob (web .sw::after)", VaultColors.Dim, colors.uncheckedThumbColor)
        assertTrue("the track alone is under 3:1 on Bg, so the edge must carry it", contrast(colors.uncheckedTrackColor, VaultColors.Bg) < 3.0)
        assertTrue("the old --line edge was under 3:1 on Bg", contrast(VaultColors.Line, VaultColors.Bg) < 3.0)
        assertEquals("off edge on Bg", 3.83, contrast(colors.uncheckedBorderColor, VaultColors.Bg), 0.005)
        assertEquals("off edge on Surface", 3.45, contrast(colors.uncheckedBorderColor, VaultColors.Surface), 0.005)
        assertEquals("off knob on the off track", 5.06, contrast(colors.uncheckedThumbColor, colors.uncheckedTrackColor), 0.005)

        // On: --accent edge and knob on the 16 % accent glow (41/255 in Compose).
        assertEquals(VaultColors.Accent, colors.checkedBorderColor)
        assertEquals(VaultColors.Accent, colors.checkedThumbColor)
        assertEquals(VaultColors.AccentGlow, colors.checkedTrackColor)
        assertEquals("accent glow alpha byte", 41, (colors.checkedTrackColor.toArgb() ushr 24) and 0xFF)
        val onTrack = over(colors.checkedTrackColor, VaultColors.Bg)
        assertEquals("on knob on the composited on track (Bg)", 7.94, contrast(colors.checkedThumbColor, onTrack), 0.005)
        assertTrue("on edge on Bg", contrast(colors.checkedBorderColor, VaultColors.Bg) >= 3.0)
    }

    @Test
    fun `MUTATION TARGET -- in About the switch row sits below Refresh and above the trademark line, which closes the panel`() {
        val controller = controller()
        render(controller)

        val app = RuntimeEnvironment.getApplication()
        val refresh = span(composeRule.onNodeWithText(app.getString(R.string.settings_about_refresh)).fetchSemanticsNode())
        val row = span(composeRule.onNodeWithTag(EXPERT_SWITCH_ROW_TAG).fetchSemanticsNode())
        val legal = span(composeRule.onNodeWithText(app.getString(R.string.settings_about_trademark)).fetchSemanticsNode())
        assertTrue("the switch row $row starts below Refresh $refresh", row.first >= refresh.second)
        assertTrue("the trademark line $legal starts below the switch row $row: never the switch below the disclaimer", legal.first >= row.second)
    }

    @Test
    fun `the whole row is one switch for TalkBack and touch`() {
        val controller = controller()
        render(controller)
        val app = RuntimeEnvironment.getApplication()

        val row = composeRule.onNodeWithTag(EXPERT_SWITCH_ROW_TAG)
        row.assert(SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Switch))
        row.assertIsOff()
        val texts = row.fetchSemanticsNode().config.getOrNull(SemanticsProperties.Text).orEmpty().map { it.text }
        assertTrue("the row carries its label: $texts", texts.contains(app.getString(R.string.settings_expert_switch)))
        assertFalse("precondition: expert mode off", controller.expertOn)

        row.performScrollTo().performClick()
        composeRule.waitForIdle()
        assertTrue("a tap on the row turns expert mode on", controller.expertOn)
        composeRule.onNodeWithTag(EXPERT_SWITCH_ROW_TAG).assertIsOn()
    }

    private fun controller(): SettingsController {
        val demo = DemoState.fresh()
        return SettingsController(
            ExpertSwitchRowsSettings(DemoSettingsRepository(demo)),
            DemoScheduleRepository(demo),
            InMemoryCredentialStore(),
            ExpertSwitchNoIdentity(),
            ExpertSwitchStrings(),
            DemoSteamRelayRepository(demo),
            DemoAboutRepository(demo),
        ).also { it.selectSection("about") }
    }

    private fun render(controller: SettingsController) {
        composeRule.setContent {
            SteamVaultTheme {
                SettingsScreen(
                    controller = controller,
                    onSignInSteamClick = {},
                    onReconnectClick = {},
                    onDisconnected = {},
                    onRequestNotificationPermission = {},
                    demoMode = false,
                )
            }
        }
        composeRule.waitForIdle()
        composeRule.waitUntil(timeoutMillis = 5_000) { controller.about != null && controller.settingsResponse != null }
        composeRule.waitForIdle()
        assertEquals("precondition: the About panel is shown", "about", controller.section)
    }

    /** Top and bottom of [node] in the root, unclipped (`boundsInRoot` is
     * clipped by the scrolling column, so a node below the viewport would
     * collapse onto its edge). */
    private fun span(node: SemanticsNode): Pair<Float, Float> {
        val top = node.positionInRoot.y
        return top to top + node.size.height
    }

    /** `fg` (with its alpha) over the opaque `ground`, per channel in 8 bits like Compose's sRGB Color. */
    private fun over(fg: Color, ground: Color): Color {
        val f = fg.toArgb()
        val g = ground.toArgb()
        val a = (f ushr 24) and 0xFF
        fun ch(shift: Int): Int {
            val fc = (f shr shift) and 0xFF
            val gc = (g shr shift) and 0xFF
            return Math.round(fc * a / 255.0 + gc * (1 - a / 255.0)).toInt()
        }
        return Color(red = ch(16), green = ch(8), blue = ch(0))
    }

    /** WCAG 2.x relative luminance of an opaque colour, from its 8-bit channels. */
    private fun luminance(c: Color): Double {
        val argb = c.toArgb()
        fun lin(v: Int): Double {
            val s = v / 255.0
            return if (s <= 0.04045) s / 12.92 else Math.pow((s + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * lin((argb shr 16) and 0xFF) + 0.7152 * lin((argb shr 8) and 0xFF) + 0.0722 * lin(argb and 0xFF)
    }

    private fun contrast(a: Color, b: Color): Double {
        val (hi, lo) = listOf(luminance(a), luminance(b)).sortedDescending()
        return (hi + 0.05) / (lo + 0.05)
    }
}

/** The demo settings with the expert flag on every key of an Expert group
 * (the demo itself marks none, so the switch row would be hidden). */
private class ExpertSwitchRowsSettings(private val base: SettingsRepository) : SettingsRepository {
    private val expertKeys = SettingsLayout.EXPERT_GROUPS.flatMap { it.keys }.toSet()

    private fun mark(out: SettingsOut): SettingsOut {
        val marked = out.settings.map { if (it.key in expertKeys) it.copy(expert = true) else it }
        check(marked.any { it.expert }) { "no demo key of an Expert group; pick another fixture" }
        return out.copy(settings = marked)
    }

    override suspend fun get(): SettingsOut = mark(base.get())
    override suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut = mark(base.patch(updates))
}

private class ExpertSwitchNoIdentity : SteamIdentityRepository {
    override fun state(): SteamIdentityState = SteamIdentityState(null, null)
    override fun buildLoginUrl(vaultBaseUrl: String): String? = null
    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult = SteamLoginResult.Success("76561198042117903")
    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {}
}

private class ExpertSwitchStrings : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable) = "load failed"
    override fun savedToast() = "saved"
    override fun saveFailedFallback() = "save failed"
    override fun libraryIdSaved() = "id saved"
    override fun libraryIdCleared() = "id cleared"
    override fun libraryIdUnchanged() = "id unchanged"
    override fun libraryIdReset() = "id reset"
}

/** Same reason as `GameCardInstalledSemanticsTest`'s rule: register the bare
 * host activity with Robolectric for the test only (order 0). Own name: a
 * private top-level class still takes a JVM class name in this package. */
private class RegisterExpertSwitchHostActivityRule : TestRule {
    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            val app = RuntimeEnvironment.getApplication()
            shadowOf(app.packageManager).addActivityIfNotPresent(ComponentName(app, ComponentActivity::class.java))
            base.evaluate()
        }
    }
}
