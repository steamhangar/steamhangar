package dev.steamvault.app.ui.downloads

import android.app.Application
import android.content.ComponentName
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.theme.SteamVaultTheme
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

/**
 * WP APP-DL-STATS-1 review (must-fix): the graph's axis captions on the
 * "all" range at 360dp / font 1.3. The graph row there is about 304dp wide;
 * "since 2026-09-01" plus "highest 19.0 MB/s (1 day avg)" plus "now" need
 * about 331dp. Three plain Texts in a SpaceBetween row squeezed "now" (it
 * got only the remainder and wrapped); [GraphAxisCaptions] keeps the edge
 * captions on one line at their own width and lets the middle one wrap.
 *
 * Semantics only, no golden: "now" must be exactly as wide and as tall as a
 * free-standing "now" in the same style (one line, not squeezed), inside
 * the row, and right of the middle caption.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = Application::class)
class GraphAxisCaptionsTest {

    @get:Rule(order = 0)
    val hostActivity: TestRule = RegisterAxisHostActivityRule()

    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun `MUTATION TARGET -- on all at 360dp and font 1_3 the end caption keeps its own single-line width`() {
        val rowWidth = 304.dp
        var rowPx = 0f
        composeRule.setContent {
            val base = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(base.density, fontScale = 1.3f)) {
                SteamVaultTheme {
                    rowPx = with(LocalDensity.current) { rowWidth.toPx() }
                    Column {
                        GraphAxisCaptions(
                            start = "since 2026-09-01",
                            middle = "highest 19.0 MB/s (1 day avg)",
                            end = "now",
                            modifier = Modifier.width(rowWidth).testTag(ROW),
                        )
                        // The reference: the same caption, free-standing, in the same style.
                        Text(
                            "now",
                            style = MaterialTheme.typography.labelSmall,
                            maxLines = 1,
                            softWrap = false,
                            modifier = Modifier.wrapContentWidth().testTag(REF),
                        )
                    }
                }
            }
        }
        composeRule.waitForIdle()
        val end = composeRule.onNodeWithTag(GRAPH_END_TAG).fetchSemanticsNode().boundsInRoot
        val ref = composeRule.onNodeWithTag(REF).fetchSemanticsNode().boundsInRoot
        val middle = composeRule.onNodeWithTag(GRAPH_HIGHEST_TAG).fetchSemanticsNode().boundsInRoot
        val row = composeRule.onNodeWithTag(ROW).fetchSemanticsNode().boundsInRoot
        assertEquals("\"now\" was squeezed (width ${end.width} vs its own ${ref.width})", ref.width, end.width, 0.5f)
        assertEquals("\"now\" wrapped (height ${end.height} vs one line ${ref.height})", ref.height, end.height, 0.5f)
        assertTrue("\"now\" leaves the row (right ${end.right} > ${row.right})", end.right <= row.right + 0.5f)
        assertTrue("the middle caption overlaps \"now\"", middle.right <= end.left + 0.5f)
        assertEquals("precondition: the row is the 304dp graph width", rowPx, row.width, 1f)
    }

    private companion object {
        const val ROW = "axis-row"
        const val REF = "axis-ref-now"
    }
}

private class RegisterAxisHostActivityRule : TestRule {
    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            val app = RuntimeEnvironment.getApplication()
            shadowOf(app.packageManager).addActivityIfNotPresent(ComponentName(app, ComponentActivity::class.java))
            base.evaluate()
        }
    }
}
