package dev.steamvault.app.ui.pairing

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP APP-PAIR-1: comment-stripped source scans (same technique as
 * `MainActivityIntentWiringTest`) for the two promises a JVM test cannot
 * observe through behaviour: the dialog never renders the key, and no
 * pairing file logs anything.
 */
class PairingWiringTest {

    private fun code(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
    }

    private val dialog = code("src/main/java/dev/steamvault/app/ui/pairing/PairingDialog.kt")

    private val pairingFiles = listOf(
        "src/main/java/dev/steamvault/app/net/pairing/PairingLink.kt",
        "src/main/java/dev/steamvault/app/ui/pairing/PairingController.kt",
        "src/main/java/dev/steamvault/app/ui/pairing/PairingDialog.kt",
        "src/main/java/dev/steamvault/app/ui/pairing/logic/PairingDecisions.kt",
    )

    @Test
    fun `MUTATION PIN -- the pairing dialog never reads the API key`() {
        assertFalse("PairingDialog.kt must not reference apiKey -- code:\n$dialog", dialog.contains("apiKey"))
    }

    @Test
    fun `MUTATION PIN -- the pairing dialog shows the URL that will be stored and the host in its title`() {
        assertTrue(dialog.contains("Text(text = state.request.baseUrl"))
        assertTrue(dialog.contains("stringResource(R.string.pairing_title, state.request.displayHost)"))
    }

    @Test
    fun `MUTATION PIN -- the replace notice is rendered for an already configured vault`() {
        assertTrue(dialog.contains("PairingReplaceNotice.REPLACES_OTHER_VAULT -> Text("))
        assertTrue(dialog.contains("stringResource(R.string.pairing_notice_replace, state.existingHost.orEmpty())"))
    }

    @Test
    fun `MUTATION PIN -- the same-vault notice is rendered`() {
        assertTrue(dialog.contains("PairingReplaceNotice.SAME_VAULT -> Text("))
        assertTrue(dialog.contains("stringResource(R.string.pairing_notice_same_vault)"))
    }

    @Test
    fun `MUTATION PIN -- the cleartext warning is rendered for an http address`() {
        val guard = dialog.indexOf("if (state.request.usesCleartext) {")
        assertTrue("expected the usesCleartext guard -- code:\n$dialog", guard >= 0)
        val warning = dialog.indexOf("stringResource(R.string.pairing_cleartext_warning)")
        assertTrue("the cleartext warning must be rendered inside that guard", warning > guard)
        assertTrue(
            "nothing else may sit between the guard and the warning",
            dialog.substring(guard, warning).count { it == '}' } == 0,
        )
    }

    @Test
    fun `MUTATION PIN -- a failed check is shown in place and the buttons lock while busy`() {
        assertTrue(dialog.contains("state.error?.let"))
        assertTrue(dialog.contains("TextButton(enabled = !state.busy && armed, onClick = onConfirm)"))
        assertTrue(dialog.contains("TextButton(enabled = !state.busy, onClick = { controller.dismiss() })"))
    }

    @Test
    fun `MUTATION PIN -- Pair stays disabled until the controller arm delay has passed`() {
        for (needle in listOf(
            "var armed by remember(state.shownAtMillis) { mutableStateOf(controller.isArmed()) }",
            "LaunchedEffect(state.shownAtMillis) {",
            "delay(controller.armRemainingMillis())",
            "armed = true",
        )) {
            assertTrue("PairingDialog.kt must contain `$needle` -- code:\n$dialog", dialog.contains(needle))
        }
    }

    @Test
    fun `no pairing source file logs or prints anything`() {
        for (path in pairingFiles) {
            val source = code(path)
            for (needle in listOf("Log.", "println(", "printStackTrace", "System.out", "System.err")) {
                assertFalse("$path must not contain `$needle`", source.contains(needle))
            }
        }
    }
}
