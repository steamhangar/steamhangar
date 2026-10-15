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

    private val screen = "src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt"
    private val controller = "src/main/java/dev/steamvault/app/ui/settings/SettingsController.kt"

    @Test
    fun `MUTATION PIN -- the form is driven by SettingsLayout, with the expert switch and the apply line`() {
        val text = code(screen)
        val start = text.indexOf("private fun SettingsForm(")
        val end = text.indexOf("private fun captionFor(", start)
        check(start >= 0 && end > start) { "expected SettingsForm before captionFor" }
        val body = text.substring(start, end)
        assertTrue(body.contains("SettingsLayout.placeEntries(response.settings)"))
        assertTrue(body.contains("controller.coreApplyLine"))
        assertTrue(body.contains("ExpertSwitchRow("))
        assertTrue(body.contains("if (controller.expertOn)"))
        assertTrue(body.contains("controller.isSectionOpen(block.id)"))
        assertTrue(body.contains("controller.setSectionOpen(block.id, !open)"))
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
