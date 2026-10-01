package dev.steamvault.app

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP APP-FIX-1's MainActivity wiring (review round 2,
 * B1). Same comment-stripped source-text-scan technique as
 * `DemoModeUiWiringTest`/`Ag3WiringTest`: a JVM unit test cannot build an
 * Activity (no emulator, no Robolectric -- `app/README.md`'s standing
 * constraint), and `SteamIdentityRepositoryTest`/`PendingLoginState` tests
 * prove the repository logic only. Without these pins, reverting any of
 * the three wirings below (process-scoped pending login state, stripping a
 * consumed OpenID callback, removing consumed notification extras) left
 * the whole suite green.
 */
class MainActivityIntentWiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val code: String = stripComments(read("src/main/java/dev/steamvault/app/MainActivity.kt"))

    /** Index of the bracket that closes the one opened at [openIdx]
     * (same bracket kind), counting nesting. */
    private fun matchingClose(text: String, openIdx: Int): Int {
        val openCh = text[openIdx]
        val closeCh = if (openCh == '(') ')' else '}'
        var depth = 0
        for (i in openIdx until text.length) {
            when (text[i]) {
                openCh -> depth++
                closeCh -> {
                    depth--
                    if (depth == 0) return i
                }
            }
        }
        error("unbalanced '$openCh' at index $openIdx in MainActivity.kt")
    }

    /** The body text of the block (`{...}` or `(...)`) whose header is
     * [anchor] -- the first [opener] after the anchor up to its matching
     * close. */
    private fun block(anchor: String, opener: Char): String {
        val start = code.indexOf(anchor)
        check(start >= 0) { "expected to find `$anchor` in MainActivity.kt" }
        val openAt = code.indexOf(opener, start)
        check(openAt >= 0) { "expected a '$opener' after `$anchor` in MainActivity.kt" }
        return code.substring(openAt, matchingClose(code, openAt) + 1)
    }

    @Test
    fun `MUTATION PIN -- the identity repository is built with the process-scoped pending login state`() {
        val call = block("SteamIdentityRepositoryImpl(", '(')
        assertTrue(
            "SteamIdentityRepositoryImpl(...) in MainActivity must pass " +
                "`pendingLoginState = PROCESS_PENDING_LOGIN_STATE` (WP APP-FIX-1 P1) -- call:\n$call",
            call.contains("pendingLoginState = PROCESS_PENDING_LOGIN_STATE"),
        )
    }

    @Test
    fun `MUTATION PIN -- PROCESS_PENDING_LOGIN_STATE lives in the companion object`() {
        val companion = block("companion object", '{')
        assertTrue(
            "`private val PROCESS_PENDING_LOGIN_STATE = PendingLoginState()` must sit inside MainActivity's " +
                "companion object (process scope, WP APP-FIX-1 P1) -- companion:\n$companion",
            companion.contains("private val PROCESS_PENDING_LOGIN_STATE = PendingLoginState()"),
        )
    }

    @Test
    fun `MUTATION PIN -- handleIntent strips a consumed OpenID callback after the RETURN_TO check`() {
        val body = block("private fun handleIntent(", '{')
        val returnToIdx = body.indexOf("if (!data.startsWith(SteamOpenIdConfig.RETURN_TO)) return")
        assertTrue("expected the RETURN_TO check inside handleIntent -- body:\n$body", returnToIdx >= 0)
        val strip = body.indexOf("intent.data = null", returnToIdx)
        assertTrue(
            "handleIntent must clear the consumed callback (`intent.data = null`) AFTER the RETURN_TO check " +
                "(WP APP-FIX-1 S2) -- body:\n$body",
            strip > returnToIdx,
        )
    }

    @Test
    fun `MUTATION PIN -- handleNotificationTap removes both consumed notification extras`() {
        val body = block("private fun handleNotificationTap(", '{')
        for (extra in listOf("EXTRA_DESTINATION", "EXTRA_OPEN_CLIENTS_SHEET")) {
            assertTrue(
                "handleNotificationTap must call `intent.removeExtra(NotificationRouting.$extra)` " +
                    "(WP APP-FIX-1 S2) -- body:\n$body",
                body.contains("intent.removeExtra(NotificationRouting.$extra)"),
            )
        }
    }
}
