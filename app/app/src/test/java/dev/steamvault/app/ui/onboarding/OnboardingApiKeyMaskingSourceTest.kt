package dev.steamvault.app.ui.onboarding

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pin (same source-text technique as `SteamKeyIsolationTest` /
 * `EncryptedCredentialStoreSourceTest`) for WP APP-FIX-1 finding S1: the
 * onboarding API-key field must be masked like a password by default, with
 * a password keyboard (no autocorrect/suggestion learning of the secret).
 *
 * Compose UI itself cannot be instantiated on this project's JVM test
 * runtime (app/README.md "no instrumented tests"), so this pins the SHAPE
 * of the call site -- which is exactly what a regression would change:
 * dropping `visualTransformation` or the `KeyboardType.Password` option
 * from the `OutlinedTextField` that binds `controller.apiKeyText`.
 */
class OnboardingApiKeyMaskingSourceTest {

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    /** Comment-stripped (review round 2, S2): the call site's own WP
     * APP-FIX-1 comment names `PasswordVisualTransformation`, so an
     * un-stripped scan could be satisfied by prose instead of code. */
    private val source: String = File("src/main/java/dev/steamvault/app/ui/onboarding/OnboardingScreen.kt").let {
        check(it.exists()) { "expected source file at ${it.absolutePath}" }
        stripComments(it.readText(Charsets.UTF_8))
    }

    /** The text of the ONE `OutlinedTextField(...)` call that binds the API-key field. */
    private fun apiKeyFieldCall(): String {
        val bind = source.indexOf("value = controller.apiKeyText")
        check(bind >= 0) { "OnboardingScreen.kt no longer binds controller.apiKeyText to a text field" }
        val start = source.lastIndexOf("OutlinedTextField(", bind)
        check(start >= 0) { "controller.apiKeyText is not bound inside an OutlinedTextField call" }
        // The call ends at the first `modifier = Modifier.fillMaxWidth(),\n    )`
        // after the binding -- every field in ConnectStep closes that way.
        val end = source.indexOf("\n    )", bind)
        check(end > start) { "could not find the end of the API-key OutlinedTextField call" }
        return source.substring(start, end)
    }

    @Test
    fun `MUTATION PIN -- the API-key field is masked with PasswordVisualTransformation`() {
        val call = apiKeyFieldCall()
        assertTrue(
            "the OutlinedTextField bound to controller.apiKeyText must set visualTransformation using " +
                "PasswordVisualTransformation() (WP APP-FIX-1 S1) -- call site:\n$call",
            call.contains(
                "visualTransformation = if (apiKeyVisible) VisualTransformation.None else PasswordVisualTransformation(),",
            ),
        )
    }

    @Test
    fun `MUTATION PIN -- the API-key field starts hidden`() {
        val bind = source.indexOf("value = controller.apiKeyText")
        check(bind >= 0) { "OnboardingScreen.kt no longer binds controller.apiKeyText to a text field" }
        val start = source.lastIndexOf("OutlinedTextField(", bind)
        check(start >= 0) { "controller.apiKeyText is not bound inside an OutlinedTextField call" }
        val stateDecl = source.lastIndexOf("var apiKeyVisible by rememberSaveable { mutableStateOf(false) }", start)
        assertTrue(
            "the API-key visibility toggle must default to hidden: expected " +
                "`var apiKeyVisible by rememberSaveable { mutableStateOf(false) }` before the API-key " +
                "OutlinedTextField (WP APP-FIX-1 S1)",
            stateDecl >= 0,
        )
    }

    @Test
    fun `the API-key field uses a password keyboard`() {
        val call = apiKeyFieldCall()
        assertTrue(
            "the OutlinedTextField bound to controller.apiKeyText must request KeyboardType.Password " +
                "(WP APP-FIX-1 S1) -- call site:\n$call",
            call.contains("KeyboardType.Password"),
        )
    }

    @Test
    fun `MUTATION PIN -- the API-key field disables autocorrect`() {
        val call = apiKeyFieldCall()
        assertTrue(
            "the OutlinedTextField bound to controller.apiKeyText must set autoCorrectEnabled = false " +
                "(WP APP-FIX-1 S1) -- call site:\n$call",
            call.contains("autoCorrectEnabled = false"),
        )
    }
}
