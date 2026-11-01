package dev.steamvault.app.ui.settings

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * D16 (Android half): source-scan pins for the Settings screen wiring that
 * a JVM test cannot render. Each one fails if its wiring is deleted.
 */
class SettingsD16WiringTest {

    private fun code(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
    }

    private val COMPOSABLE_FUN = Regex("""@Composable\s+(?:private\s+)?fun\s+(\w+)""")

    private fun nearestEnclosingComposable(text: String, callIdx: Int): String? =
        COMPOSABLE_FUN.findAll(text)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    /** First CALL of [call] (not its own `fun NAME(` definition). */
    private fun callIndex(text: String, call: String): Int {
        var from = 0
        while (true) {
            val idx = text.indexOf(call, from)
            if (idx < 0) return -1
            if (!text.substring(0, idx).trimEnd().endsWith("fun")) return idx
            from = idx + call.length
        }
    }

    private fun assertCalledFrom(call: String, expectedComposable: String) {
        val text = code(screen)
        val idx = callIndex(text, call)
        assertTrue("'$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "'$call' MOVED -- its nearest enclosing composable is no longer $expectedComposable",
            expectedComposable,
            nearestEnclosingComposable(text, idx),
        )
    }

    private val screen = "src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt"
    private val controller = "src/main/java/dev/steamvault/app/ui/settings/SettingsController.kt"

    @Test
    fun `MUTATION PIN -- each Settings row piece is called from the composable that owns it`() {
        assertCalledFrom("ExpertSwitchRow(controller.expertOn,", "SettingsScreen")
        assertCalledFrom("SettingsSectionStrip(sections = sections", "SettingsScreen")
        assertCalledFrom("SettingsForm(controller, current)", "SettingsScreen")
        assertCalledFrom("PinnedSettingRow(entry)", "SettingsForm")
        assertCalledFrom("SettingHelpRow(entry)", "SettingsForm")
        assertCalledFrom("FieldCard(block.title)", "SettingsForm")
        assertCalledFrom("RiskyConfirmDialog(controller, scope)", "SettingsScreen")
        assertCalledFrom("controller.confirmRisky()", "RiskyConfirmDialog")
        assertCalledFrom("controller.cancelRisky()", "RiskyConfirmDialog")
    }

    /** WP ANDROID-REDESIGN-1c: one panel at a time behind the strip; the
     * strip's labels are the web's `SECTIONS.nav` (SettingsLayout.NAV_LABELS). */
    @Test
    fun `MUTATION PIN -- the strip selects one panel through the controller, with the web nav labels`() {
        val text = code(screen)
        assertCalledFrom("onSelect = { controller.selectSection(it) }", "SettingsScreen")
        assertCalledFrom("visibleSections(placed, controller.expertOn)", "SettingsScreen")
        assertCalledFrom("SettingsLayout.navLabel(id)", "SettingsSectionStrip")
        assertCalledFrom("onClick = { onSelect(id) },", "SettingsSectionStrip")
        assertCalledFrom("PanelHeading(current)", "SettingsScreen")
        assertTrue("the old collapsible blocks are gone", !text.contains("isSectionOpen") && !text.contains("setSectionOpen"))
        assertTrue("no Open Clients pointer (rc20 visual check)", !text.contains("ClientsSection(") && !text.contains("settings_clients_open_button"))
    }

    /** WP ANDROID-POLISH-1: the unsaved dots (web `paintDirtyDots`) come from the controller. */
    @Test
    fun `MUTATION PIN -- the strip tabs and the expert switch row show the controller's dirty dots`() {
        val text = code(screen)
        assertCalledFrom("val dirty = controller.dirtyDots", "SettingsScreen")
        assertCalledFrom("dirty = dirty.sections, onSelect", "SettingsScreen")
        assertCalledFrom("ExpertSwitchRow(controller.expertOn, dirty = dirty.expertSwitch)", "SettingsScreen")
        assertCalledFrom("if (id in dirty) UnsavedDot(stringResource(R.string.settings_section_unsaved))", "SettingsSectionStrip")
        assertCalledFrom("if (dirty) UnsavedDot(stringResource(R.string.settings_expert_unsaved))", "ExpertSwitchRow")
        val start = text.indexOf("private fun UnsavedDot(")
        val end = text.indexOf("private fun PanelHeading(", start)
        check(start >= 0 && end > start) { "expected UnsavedDot before PanelHeading" }
        assertTrue("the dot is spoken", text.substring(start, end).contains("contentDescription = spoken"))
        val strings = File("src/main/res/values/strings.xml").readText(Charsets.UTF_8)
        assertTrue(strings.contains("<string name=\"settings_section_unsaved\">(unsaved changes)</string>"))
        assertTrue(strings.contains("<string name=\"settings_expert_unsaved\">(unsaved expert changes)</string>"))
    }

    @Test
    fun `MUTATION PIN -- the expert switch is the last item of the About panel`() {
        val text = code(screen)
        val start = text.indexOf("\"about\" -> {")
        check(start >= 0) { "expected an About branch in SettingsScreen" }
        val about = text.indexOf("AboutSection(controller, scope)", start)
        val switch = text.indexOf("ExpertSwitchRow(controller.expertOn,", start)
        val next = text.indexOf("else -> {", start)
        assertTrue("About first, then the switch, both inside the About branch", about in (start + 1) until switch && switch < next)
        assertEquals("one switch only", 1, Regex("ExpertSwitchRow\\(controller").findAll(text).count())
    }

    @Test
    fun `MUTATION PIN -- the form is driven by SettingsLayout, per panel, with the apply line`() {
        val text = code(screen)
        val start = text.indexOf("private fun SettingsForm(")
        val end = text.indexOf("private fun captionFor(", start)
        check(start >= 0 && end > start) { "expected SettingsForm before captionFor" }
        val body = text.substring(start, end)
        assertTrue(body.contains("SettingsLayout.placeEntries(response.settings)"))
        assertTrue(body.contains("controller.coreApplyLine"))
        assertTrue(body.contains("if (sectionId == EXPERT_SECTION)"))
        assertTrue(body.contains("if (controller.expertOn)"))
        assertTrue(body.contains("placed.sections[sectionId].orEmpty()"))
        assertTrue("env-wins rows are plain text", body.contains("if (SettingsLayout.isPinned(entry))") && body.contains("PinnedSettingRow(entry)"))
        assertTrue("every row has its (i)", body.contains("SettingHelpRow(entry)"))
    }

    @Test
    fun `MUTATION PIN -- the risky dialog is mounted and only its yes confirms`() {
        val text = code(screen)
        assertTrue(text.contains("RiskyConfirmDialog(controller, scope)"))
        val start = text.indexOf("private fun RiskyConfirmDialog(")
        val end = text.indexOf("private fun SettingTextField(", start)
        check(start >= 0 && end > start) { "expected RiskyConfirmDialog before SettingTextField" }
        val body = text.substring(start, end)
        assertTrue(body.contains("controller.confirmRisky()"))
        assertTrue(body.contains("controller.cancelRisky()"))
        assertEquals("confirmRisky is called from the dialog only", 1, Regex("controller\\.confirmRisky\\(\\)").findAll(text).count())
        assertTrue("the save bar says when the change was not confirmed", text.contains("controller.riskyCancelled"))
    }

    @Test
    fun `MUTATION PIN -- the controller sends confirm_risky only from an explicit confirm`() {
        val text = code(controller)
        assertTrue(text.contains("settingsRepository.patchConfirmed(patch)"))
        val start = text.indexOf("suspend fun confirmRisky(")
        val end = text.indexOf("fun cancelRisky(", start)
        check(start >= 0 && end > start) { "expected confirmRisky before cancelRisky" }
        assertTrue(text.substring(start, end).contains("save(true)"))
        assertEquals("only one call site sends the confirmed PATCH", 1, Regex("patchConfirmed\\(patch\\)").findAll(text).count())
    }
}
