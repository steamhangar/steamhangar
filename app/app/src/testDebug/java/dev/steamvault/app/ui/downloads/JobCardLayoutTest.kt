package dev.steamvault.app.ui.downloads

import android.app.Application
import android.content.ComponentName
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.Density
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.steamvault.app.ui.downloads.logic.JobCardAction
import dev.steamvault.app.ui.downloads.logic.JobCardMode
import dev.steamvault.app.ui.downloads.logic.JobCardModel
import dev.steamvault.app.ui.status.StatusKind
import dev.steamvault.app.ui.theme.SteamVaultTheme
import dev.steamvault.app.ui.traffic.logic.HeroStatus
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
 * WP APP-DL-CARD-1 (operator report 2026-11-01): the Downloads job card at
 * the smallest supported width, 360dp at font scale 1.3 (the card is 336dp
 * wide inside the list's 12dp padding). Semantics and bounds only, no
 * golden: a long name wraps beside the status pill instead of squeezing it,
 * the long CAP-503 hold note stays inside the card behind its rule, the
 * buttons keep a 48dp touch target, the title block is a traversal group
 * (TalkBack reads name and meta before the pill centred beside them), the
 * blocks are composed title, pill, figures, notes, actions, and the pill's
 * word is read once.
 */
@RunWith(RobolectricTestRunner::class)
// NATIVE graphics: LEGACY (Robolectric's default) measures text at about one
// pixel per character, so a long name would never overflow (APP-DL-STATS-1).
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], application = Application::class, qualifiers = "w360dp-h800dp")
class JobCardLayoutTest {

    @get:Rule(order = 0)
    val hostActivity: TestRule = RegisterJobCardLayoutHostActivityRule()

    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private var px = 0f

    private fun show(
        model: JobCardModel,
        speedLine: JobSpeedLine? = null,
        holdNote: String? = null,
        clientNote: String? = null,
        pausingNote: String? = null,
        refWord: String = "",
    ) {
        composeRule.setContent {
            val base = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(base.density, fontScale = 1.3f)) {
                SteamVaultTheme {
                    px = with(LocalDensity.current) { 1.dp.toPx() }
                    Column {
                        Box(Modifier.width(CARD_WIDTH).testTag(CARD)) {
                            JobCard(
                                model = model,
                                speedLine = speedLine,
                                busy = false,
                                holdNote = holdNote,
                                clientNote = clientNote,
                                pausingNote = pausingNote,
                                onPause = {},
                                onResume = {},
                                onCancel = {},
                            )
                        }
                        // References, free-standing and unconstrained: the name in the card's
                        // title style and the pill as the card draws it.
                        for ((text, tag) in listOf(model.name to REF_NAME, refWord to REF_WORD)) {
                            Text(
                                text,
                                style = titleStyle(),
                                maxLines = 1,
                                softWrap = false,
                                modifier = Modifier.wrapContentWidth(unbounded = true).testTag(tag),
                            )
                        }
                        Box(Modifier.wrapContentWidth(unbounded = true).testTag(REF_PILL)) {
                            StatusPill(HeroStatus(model.kind, model.statusWord))
                        }
                    }
                }
            }
        }
        composeRule.waitForIdle()
    }

    private fun bounds(tag: String): Rect = composeRule.onNodeWithTag(tag).fetchSemanticsNode().boundsInRoot

    /** Nodes under [tag] (merged tree) that [match]. */
    private fun nodesUnder(tag: String, match: (SemanticsNode) -> Boolean): List<SemanticsNode> {
        val out = ArrayList<SemanticsNode>()
        fun walk(node: SemanticsNode) {
            if (match(node)) out.add(node)
            node.children.forEach { walk(it) }
        }
        walk(composeRule.onNodeWithTag(tag).fetchSemanticsNode())
        return out
    }

    /** The pill under [tag]: the one node whose description is [word]. */
    private fun pillBounds(tag: String, word: String): Rect =
        nodesUnder(tag) { it.config.getOrNull(SemanticsProperties.ContentDescription) == listOf(word) }.single().boundsInRoot

    /** The one Text node under [tag] that reads [text]. */
    private fun textBounds(tag: String, text: String): Rect =
        nodesUnder(tag) { n -> n.config.getOrNull(SemanticsProperties.Text)?.map { it.text } == listOf(text) }.single().boundsInRoot

    @Test
    fun `MUTATION TARGET -- a long name wraps beside the pill, the long hold note and the buttons stay in the card`() {
        show(HELD, holdNote = LONG_HOLD_NOTE)
        val card = bounds(CARD)
        // Precondition: the name on one line plus the pill do not fit the card's inner width.
        val inner = card.width - 2 * 16 * px
        val needed = bounds(REF_NAME).width + bounds(REF_PILL).width
        assertTrue("precondition: name + pill must overflow the card ($needed px needed, $inner px there)", needed > inner)

        val pill = pillBounds(CARD, "Paused")
        val ref = pillBounds(REF_PILL, "Paused")
        assertEquals("the pill was squeezed (width ${pill.width} vs its own ${ref.width})", ref.width, pill.width, 0.5f)
        assertEquals("the pill wrapped (height ${pill.height} vs ${ref.height})", ref.height, pill.height, 0.5f)
        assertTrue("the pill leaves the card's padding (right ${pill.right} > ${card.right - 16 * px})", pill.right <= card.right - 16 * px + 0.5f)
        val name = textBounds(CARD, LONG_NAME)
        assertTrue("the name runs under the pill (name right ${name.right} > pill left ${pill.left})", name.right <= pill.left + 0.5f)
        assertTrue("the name did not wrap (height ${name.height})", name.height > bounds(REF_NAME).height * 1.5f)

        // The long CAP-503 hold note: inside the card, inset behind the 2dp rule.
        val note = textBounds(CARD, LONG_HOLD_NOTE)
        assertTrue("the hold note starts left of the rule inset (${note.left} < ${card.left + 24 * px})", note.left >= card.left + 24 * px - 0.5f)
        assertTrue("the hold note leaves the card (${note.right} > ${card.right - 16 * px})", note.right <= card.right - 16 * px + 0.5f)
        assertTrue("the hold note sits below the title block", note.top >= name.bottom)

        for (label in listOf("Resume", "Cancel")) {
            val b = composeRule.onNode(hasText(label) and hasClickAction()).fetchSemanticsNode().boundsInRoot
            assertTrue("$label leaves the card (${b.right} > ${card.right})", b.right <= card.right + 0.5f)
            assertTrue("$label sits above the note", b.top >= note.bottom)
        }
        // The action row reserves the 48dp touch height (Material's minimum
        // interactive size): note -> 12dp gap -> >= 48dp row -> 12dp padding.
        val rowSpace = card.bottom - note.bottom
        assertTrue("the action row is under 48dp tall (${rowSpace / px} dp below the note)", rowSpace >= (12 + 48 + 12) * px - 0.5f)
    }

    @Test
    fun `the title block is a traversal group, composed before the pill, notes and actions`() {
        show(HELD, holdNote = LONG_HOLD_NOTE)
        // Composition order (what TalkBack follows inside traversal groups).
        assertEquals(
            listOf(LONG_NAME, META, "Paused", LONG_HOLD_NOTE, "Resume", "Cancel"),
            readingOrder().filter { it in setOf(LONG_NAME, META, "Paused", LONG_HOLD_NOTE, "Resume", "Cancel") },
        )
        // The pill is centred on the two-line block, so by position TalkBack would
        // read name, pill, meta; the traversal group keeps name and meta together.
        val groups = nodesUnder(CARD) { it.config.getOrNull(SemanticsProperties.IsTraversalGroup) == true }
        val title = groups.singleOrNull { g -> g.children.map { c -> c.config.getOrNull(SemanticsProperties.Text)?.joinToString { it.text } } == listOf(LONG_NAME, META) }
        assertTrue("the title block (name + meta) is not one traversal group: ${groups.map { g -> g.children.size }}", title != null)
    }

    @Test
    fun `a GC hold's long pill leaves the name a usable column inside the card`() {
        show(GC_HELD, refWord = GC_NAME_WORD)
        val card = bounds(CARD)
        val pill = pillBounds(CARD, GC_WORD)
        val ref = pillBounds(REF_PILL, GC_WORD)
        assertEquals("the GC pill was squeezed", ref.width, pill.width, 0.5f)
        assertTrue("the GC pill leaves the card", pill.right <= card.right - 16 * px + 0.5f)
        val name = textBounds(CARD, GC_NAME)
        assertTrue("the name runs under the pill", name.right <= pill.left + 0.5f)
        assertTrue("the name column is under 40dp (${name.width / px} dp)", name.width >= 40 * px)
        assertTrue("the name leaves the card", name.left >= card.left + 16 * px - 0.5f)
    }

    /**
     * KNOWN NIT (APP-DL-CARD-1 review N3, kept for web parity): at 360dp /
     * font 1.3 the "Garbage collection paused" pill (about 240dp) leaves the
     * name about 55dp, less than "Wrenfield" needs, so the name breaks inside
     * the word (the web `.nm{ overflow-wrap:anywhere }` does the same at a
     * narrow column). The mono meta ("job #… · appid …") wraps line by line
     * in the same ~55dp column. This test records that state; when a fix lands (pill
     * under the name, or a shorter word), flip it to `>=`.
     */
    @Test
    fun `KNOWN NIT -- next to the GC pill the name breaks inside a word at 360dp and font 1_3`() {
        show(GC_HELD, refWord = GC_NAME_WORD)
        val name = textBounds(CARD, GC_NAME)
        val word = bounds(REF_WORD)
        assertTrue(
            "the name column (${name.width / px} dp) now fits \"$GC_NAME_WORD\" (${word.width / px} dp): the nit is fixed, flip this test",
            name.width < word.width,
        )
    }

    @Test
    fun `MUTATION TARGET -- a running card reads its pill word once and its figures with the note`() {
        val line = JobSpeedLine("31.5 MB/s · 8.8 GB loaded so far", "Measured at the cache.")
        show(RUNNING_PAUSING, speedLine = line, clientNote = CLIENT_NOTE, pausingNote = PAUSING_NOTE)
        // Only the pill says "Downloading" (the old card read the icon's label and the word).
        val spoken = readingOrder().count { it == "Downloading" }
        assertEquals("the status word must be read exactly once", 1, spoken)
        composeRule.onNodeWithContentDescription("${line.text}. ${line.note}").assertExists()
        val order = readingOrder()
        val wanted = listOf("Downloading", "${line.text}. ${line.note}", CLIENT_NOTE, PAUSING_NOTE, "Pausing…", "Cancel")
        assertEquals(wanted, order.filter { it in wanted })
        val card = bounds(CARD)
        val stop = textBounds(CARD, PAUSING_NOTE)
        assertTrue("the action row is under 48dp tall", card.bottom - stop.bottom >= (12 + 48 + 12) * px - 0.5f)
    }

    /** Text or description of every node under the card, depth first in composition (child) order. */
    private fun readingOrder(): List<String> {
        val out = ArrayList<String>()
        fun walk(node: SemanticsNode) {
            val desc = node.config.getOrNull(SemanticsProperties.ContentDescription)
            val text = node.config.getOrNull(SemanticsProperties.Text)
            when {
                desc != null -> out.addAll(desc)
                text != null -> out.addAll(text.map { it.text })
            }
            node.children.forEach { walk(it) }
        }
        walk(composeRule.onNodeWithTag(CARD).fetchSemanticsNode())
        return out
    }

    private companion object {
        val CARD_WIDTH = 336.dp
        const val CARD = "job-card"
        const val REF_NAME = "ref-name"
        const val REF_PILL = "ref-pill"
        const val REF_WORD = "ref-word"
        const val LONG_NAME = "The Long Dark Survival Edition: Wintermute Redux"
        const val META = "job #266 · appid 4010030"
        const val GC_NAME = "Wrenfield Static"
        const val GC_NAME_WORD = "Wrenfield"
        const val GC_WORD = "Garbage collection paused"

        /** The card's title style (DownloadsScreen.kt `jobCardTitleStyle`, web `.nm` --fs-4). */
        @androidx.compose.runtime.Composable
        fun titleStyle() = MaterialTheme.typography.titleMedium.copy(fontSize = 15.sp, fontWeight = FontWeight.Medium)
        // The longest CAP-503 hold note shape (web lib/client-priority.js clientHoldNote).
        const val LONG_HOLD_NOTE =
            "Paused to give workshop-livingroom-pc the full speed through the cache. Resumes by itself at about 12:02 " +
                "unless a device keeps loading. Resume runs it now, alongside workshop-livingroom-pc."
        const val CLIENT_NOTE = "Runs alongside workshop-livingroom-pc by your choice."
        const val PAUSING_NOTE = "Pausing to give workshop-livingroom-pc the full speed through the cache."

        val HELD = JobCardModel(
            jobId = 266, appid = 4_010_030, name = LONG_NAME, kind = StatusKind.PAUSED, statusWord = "Paused",
            mode = JobCardMode.HELD,
            action = JobCardAction(
                showPause = false, pauseEnabled = false, showResume = true, showCancel = true,
                cancelEnabled = true, pausing = false, cancelling = false,
            ),
        )
        val GC_HELD = JobCardModel(
            jobId = 268, appid = 4_010_030, name = GC_NAME, kind = StatusKind.PAUSED, statusWord = GC_WORD,
            mode = JobCardMode.HELD, gcHold = true,
            action = JobCardAction(
                showPause = false, pauseEnabled = false, showResume = true, showCancel = true,
                cancelEnabled = true, pausing = false, cancelling = false,
            ),
        )
        val RUNNING_PAUSING = JobCardModel(
            jobId = 267, appid = 4_010_040, name = "Breathedge", kind = StatusKind.RUNNING, statusWord = "Downloading",
            mode = JobCardMode.ACTIVE,
            action = JobCardAction(
                showPause = true, pauseEnabled = false, showResume = false, showCancel = true,
                cancelEnabled = true, pausing = true, cancelling = false,
            ),
        )
    }
}

/** Same reason as `LibraryScreenshotTest`'s rule: register the bare host
 * activity with Robolectric for the test only. Must run before the compose
 * rule (order 0). Own name: private test classes share one JVM namespace
 * per package (android.md, rc23 wave 2). */
private class RegisterJobCardLayoutHostActivityRule : TestRule {
    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            val app = RuntimeEnvironment.getApplication()
            shadowOf(app.packageManager).addActivityIfNotPresent(ComponentName(app, ComponentActivity::class.java))
            base.evaluate()
        }
    }
}
