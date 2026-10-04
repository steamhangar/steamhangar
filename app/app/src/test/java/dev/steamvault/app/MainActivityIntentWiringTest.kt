package dev.steamvault.app

import org.junit.Assert.assertFalse
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

    /** Same as [matchingClose], for a text already cut out of [code]. */
    private fun matchingCloseIn(text: String, openIdx: Int): Int {
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
        error("unbalanced '$openCh' at index $openIdx")
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
    fun `MUTATION PIN -- handleIntent strips a consumed deep link after routing it and before handling it`() {
        // WP APP-PAIR-1 replaced the bare RETURN_TO prefix check with
        // routeIncomingLink (whose OpenID branch is that same prefix check,
        // pinned in PairingLinkTest); the strip now covers both link kinds.
        val body = block("private fun handleIntent(", '{')
        val routeIdx = body.indexOf("val route = routeIncomingLink(data)")
        val noneIdx = body.indexOf("if (route == IncomingLinkRoute.NONE) return")
        val strip = body.indexOf("intent.data = null")
        val offer = body.indexOf("offerPairing(data)")
        val openId = body.indexOf("lifecycleScope.launch")
        assertTrue("expected the routing call inside handleIntent -- body:\n$body", routeIdx >= 0)
        assertTrue("expected the NONE early return after routing -- body:\n$body", noneIdx > routeIdx)
        assertTrue(
            "handleIntent must clear the consumed link (`intent.data = null`) AFTER the NONE check " +
                "(WP APP-FIX-1 S2, WP APP-PAIR-1) -- body:\n$body",
            strip > noneIdx,
        )
        assertTrue("the strip must precede offering a pairing link -- body:\n$body", offer > strip)
        assertTrue("the strip must precede the OpenID completion -- body:\n$body", openId > strip)
    }

    // ---- WP APP-PAIR-1 -----------------------------------------------------------

    @Test
    fun `MUTATION PIN -- a pairing link is offered only through the re-delivery guard`() {
        val body = block("private fun handleIntent(", '{')
        assertTrue(
            "handleIntent must offer a pairing link only behind shouldOfferPairing(...) -- body:\n$body",
            body.contains("if (shouldOfferPairing(restoredInstance, launchedFromHistory)) offerPairing(data)"),
        )
        assertTrue(
            "launchedFromHistory must come from FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY -- body:\n$body",
            body.contains("(intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) != 0"),
        )
    }

    @Test
    fun `MUTATION PIN -- onCreate marks a restored instance, onNewIntent never does`() {
        val onCreate = block("override fun onCreate(", '{')
        assertTrue(
            "onCreate must pass `restoredInstance = savedInstanceState != null` -- body:\n$onCreate",
            onCreate.contains("handleIntent(intent, restoredInstance = savedInstanceState != null)"),
        )
        val onNewIntent = block("override fun onNewIntent(", '{')
        assertTrue(
            "onNewIntent must pass `restoredInstance = false` -- body:\n$onNewIntent",
            onNewIntent.contains("handleIntent(intent, restoredInstance = false)"),
        )
    }

    @Test
    fun `MUTATION PIN -- the pairing dialog is composed in setContent with the process-scoped controller`() {
        val content = block("setContent {", '{')
        val call = content.indexOf("PairingDialog(")
        assertTrue("setContent must compose PairingDialog(...) -- content:\n$content", call >= 0)
        val args = content.substring(call, matchingCloseIn(content, content.indexOf('(', call)) + 1)
        assertTrue("PairingDialog must get PROCESS_PAIRING -- call:\n$args", args.contains("controller = PROCESS_PAIRING"))
        assertTrue(
            "PairingDialog's onConfirm must run PROCESS_PAIRING.confirm with verifyAndApplyPairing -- call:\n$args",
            args.contains("PROCESS_PAIRING.confirm { request -> verifyAndApplyPairing(request) }"),
        )
        val companion = block("companion object", '{')
        assertTrue(
            "PROCESS_PAIRING must live in the companion object (process scope) -- companion:\n$companion",
            companion.contains("private val PROCESS_PAIRING = PairingController()"),
        )
    }

    @Test
    fun `MUTATION PIN -- pairing verifies first and stores only through the onboarding finish path`() {
        val body = block("private suspend fun verifyAndApplyPairing(", '{')
        val verify = body.indexOf("onboardingController.verifyConnection(choice, request.baseUrl, request.apiKey)")
        val bail = body.indexOf("if (failure != null) return failure")
        val firstApply = body.indexOf("applyVerifiedConnection(")
        assertTrue("expected the shared onboarding check -- body:\n$body", verify >= 0)
        assertTrue("a failed check must return before anything is applied -- body:\n$body", bail in (verify + 1) until firstApply)

        val home = body.substring(body.indexOf("PairingContinuation.STORE_AND_GO_HOME ->"))
        for (needle in listOf("onboardingController.finish()", "refreshVaultApiClient()", "destination = Destination.LIBRARY")) {
            assertTrue("the go-home branch must call `$needle` -- branch:\n$home", home.contains(needle))
        }
        assertFalse(
            "pairing must not write the credential store itself (one storage path) -- body:\n$body",
            body.contains("credentialStore.set"),
        )
        val open = body.substring(body.indexOf("PairingContinuation.OPEN_ONBOARDING ->"))
        assertTrue(
            "demo mode must open onboarding before applying -- branch:\n$open",
            open.indexOf("openOnboarding(OnboardingMode.FIRST_RUN)") in 0 until open.indexOf("applyVerifiedConnection("),
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
