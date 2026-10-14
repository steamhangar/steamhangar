package dev.steamvault.app.ui.demo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for the parts of WP APP-DEMO that a JVM unit test cannot
 * exercise behaviourally (no emulator, no Compose test rule in this project
 * -- `app/README.md`'s standing constraint): the persistent demo
 * indicator's presence on every data-bearing screen and its placement
 * outside any scrolling container (brief constraint 1), demo mode being
 * torn down at the one place a real connection is ever built (brief
 * constraint 4), and -- added in review round 2 -- the real Steam identity
 * section being gated off entirely while demo mode is active (S1: a demo
 * screenshot must never be able to carry a real, on-device SteamID64/
 * persona). Same source-text-scan technique `SteamKeyIsolationTest`/
 * `DemoModeImportAllowlistTest` already use.
 */
class DemoModeUiWiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    /** Path -> the exact top-level composable function name that must be
     * the banner's DIRECT, IMMEDIATE enclosing function on that screen --
     * WP APP-DEMO review round 3, F2. */
    private val SCREENS = mapOf(
        "src/main/java/dev/steamvault/app/ui/library/LibraryScreen.kt" to "LibraryScreen",
        "src/main/java/dev/steamvault/app/ui/downloads/DownloadsScreen.kt" to "DownloadsScreen",
        "src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt" to "SettingsScreen",
        "src/main/java/dev/steamvault/app/ui/detail/GameDetailSheet.kt" to "GameDetailSheet",
        "src/main/java/dev/steamvault/app/ui/clients/ClientsSheet.kt" to "ClientsSheet",
        // WP D15d: the Clients page.
        "src/main/java/dev/steamvault/app/ui/clients/ClientsScreen.kt" to "ClientsScreen",
    )

    /** Matches `@Composable` immediately followed (across whitespace/
     * newlines) by an optional `private` and a `fun NAME(` -- every
     * top-level and helper composable in this codebase's UI layer is
     * written exactly this way (`@OptIn(...)` , if present, precedes
     * `@Composable` rather than sitting between it and `fun`). */
    private val COMPOSABLE_FUN = Regex("""@Composable\s+(?:private\s+)?fun\s+(\w+)""")

    /** Any of these inside the window between a screen's top-level
     * function and its banner call means the banner is (or could become)
     * a descendant of a scrolling/lazy container -- widened from
     * round 2's `verticalScroll`-only check after the reviewer's own
     * `LazyColumn { item { ... } }` counter-example. */
    private val SCROLLING_CONTAINER_TOKENS = listOf("verticalScroll", "LazyColumn", "LazyVerticalGrid", "scrollable")

    /**
     * MUTATION PIN (WP APP-DEMO review round 2 B2, strengthened in round 3
     * F2, tightened in round 4, Fix 1). Round 3's version counted bare
     * `DemoModeBanner()` occurrences -- the reviewer measured that
     * replacing the guarded call site with an UNGUARDED
     * `DemoModeBanner()` (no `if (demoMode)` at all) in `DownloadsScreen.kt`
     * still built green at 611/611: the count stayed exactly 1, so the
     * pin never noticed the guard itself had vanished. The direction is
     * benign (a demo banner shown to a REAL connected user, not a real
     * connection hidden from a demo user) but the coverage loss was real
     * -- this WP's whole point is that the indicator is unconditionally
     * accurate, and "shows on a real vault too" is exactly the confusion
     * constraint 1 exists to prevent, just aimed the other way.
     *
     * Fixed by counting the GUARDED literal `if (demoMode) DemoModeBanner()`
     * for the "exactly one" requirement (round 3's other two defeats --
     * the extracted-helper and `LazyColumn { item { ... } }` shapes --
     * remain covered exactly as round 3 left them, unaffected by this
     * change), AND by separately asserting the bare, unguarded form never
     * appears at all: every `DemoModeBanner()` call in the file must be
     * part of the guarded literal, with no leftover unguarded occurrence.
     *
     * This version checks, per screen, FOUR things at once:
     * 1. Exactly ONE `DemoModeBanner()` call exists in the file at all
     *    (comments stripped) -- catches a second, badly-placed call.
     * 2. Exactly ONE of those is the GUARDED literal
     *    `if (demoMode) DemoModeBanner()` -- catches the guard itself
     *    being dropped, which (1) alone cannot see.
     * 3. The banner's nearest enclosing `@Composable fun NAME(...)` is
     *    literally [SCREENS]'s expected name for that file -- i.e. the
     *    screen's OWN top-level composable, never a helper (private or
     *    not) extracted for or around the banner call.
     * 4. The span from that top-level function's own `@Composable` to the
     *    banner call contains NONE of [SCROLLING_CONTAINER_TOKENS].
     */
    @Test
    fun `MUTATION PIN -- the demo mode banner appears exactly once, GUARDED, per screen, directly in the screen's own top-level composable, outside any scrolling container`() {
        val violations = mutableListOf<String>()
        for ((path, expectedFunctionName) in SCREENS) {
            val code = stripComments(read(path))

            val totalCount = Regex("""DemoModeBanner\(\)""").findAll(code).count()
            if (totalCount != 1) {
                violations.add("$path: expected exactly 1 DemoModeBanner() call, found $totalCount")
                continue
            }

            val guardedCount = Regex("""if \(demoMode\) DemoModeBanner\(\)""").findAll(code).count()
            if (guardedCount != 1) {
                violations.add(
                    "$path: expected the ONE DemoModeBanner() call to be guarded as " +
                        "'if (demoMode) DemoModeBanner()', found $guardedCount such guarded occurrence(s) " +
                        "against $totalCount total call(s) -- an unguarded DemoModeBanner() would render on a " +
                        "REAL connected user's screen",
                )
                continue
            }

            val bannerIdx = code.indexOf("DemoModeBanner()")
            val enclosing = COMPOSABLE_FUN.findAll(code)
                .map { it.range.first to it.groupValues[1] }
                .filter { (start, _) -> start < bannerIdx }
                .maxByOrNull { (start, _) -> start }
            if (enclosing == null) {
                violations.add("$path: no @Composable fun precedes the DemoModeBanner() call")
                continue
            }
            val (composableIdx, actualFunctionName) = enclosing
            if (actualFunctionName != expectedFunctionName) {
                violations.add(
                    "$path: banner's enclosing composable is '$actualFunctionName', expected the screen's own " +
                        "top-level '$expectedFunctionName' -- a helper (e.g. an extracted DemoBannerRow-style " +
                        "wrapper) is not an acceptable substitute",
                )
                continue
            }

            val window = code.substring(composableIdx, bannerIdx)
            val foundTokens = SCROLLING_CONTAINER_TOKENS.filter { window.contains(it) }
            if (foundTokens.isNotEmpty()) {
                violations.add("$path: banner's own top-level composable contains $foundTokens before the banner call")
            }
        }
        assertEquals(
            "the demo mode banner (WP brief constraint 1) must appear exactly once, GUARDED behind 'if " +
                "(demoMode)', directly inside the screen's own top-level composable, never behind a helper and " +
                "never inside a scrolling/lazy container. Violations: $violations",
            emptyList<String>(),
            violations,
        )
    }

    /**
     * MUTATION PIN: [dev.steamvault.app.MainActivity.refreshVaultApiClient]
     * is the ONE place a real [dev.steamvault.app.net.VaultApiClient] gets
     * built from [dev.steamvault.app.storage.CredentialStore] -- WP brief
     * constraint 4 ("switching to a real connection must not leave demo
     * state behind") is satisfied by that same function unconditionally
     * clearing `demoState`. Deleting that line (or moving it out of this
     * function) is exactly the regression this pins: demo repositories
     * would keep being handed out by `LibraryDestinationContent`/
     * `DownloadsDestinationContent` (which check `demoState` FIRST) even
     * after a real connection exists.
     */
    @Test
    fun `MUTATION PIN -- refreshVaultApiClient unconditionally clears demoState`() {
        val text = read("src/main/java/dev/steamvault/app/MainActivity.kt")
        val start = text.indexOf("private fun refreshVaultApiClient(")
        check(start >= 0) { "expected to find refreshVaultApiClient in MainActivity.kt" }
        val nextFun = text.indexOf("private fun enterDemoMode(", start)
        check(nextFun > start) { "expected enterDemoMode to follow refreshVaultApiClient in MainActivity.kt" }
        val body = text.substring(start, nextFun)

        assertTrue(
            "refreshVaultApiClient() must clear demoState (found body:\n$body)",
            body.contains("demoState = null"),
        )
    }

    /**
     * MUTATION PIN: [dev.steamvault.app.MainActivity.enterDemoMode] must
     * build a brand-new [dev.steamvault.app.demo.DemoState] every time it
     * runs (WP brief constraint 4, the other direction: "re-entering must
     * not carry stale state") -- calling anything OTHER than
     * `DemoState.fresh()` here (e.g. reusing a cached instance) would leak
     * a previous demo session's mutations into the next one.
     */
    @Test
    fun `MUTATION PIN -- enterDemoMode always builds a fresh DemoState`() {
        val text = read("src/main/java/dev/steamvault/app/MainActivity.kt")
        val start = text.indexOf("private fun enterDemoMode(")
        check(start >= 0) { "expected to find enterDemoMode in MainActivity.kt" }
        val end = text.indexOf("private fun openOnboarding(", start)
        check(end > start) { "expected openOnboarding to follow enterDemoMode in MainActivity.kt" }
        val body = text.substring(start, end)

        assertTrue(
            "enterDemoMode() must call DemoState.fresh() (found body:\n$body)",
            body.contains("DemoState.fresh()"),
        )
    }

    /**
     * MUTATION PIN (WP APP-DEMO review round 2, S1). `SteamIdentitySection`
     * in `SettingsScreen.kt` must gate on `demoMode` and `return` BEFORE
     * ever reading `controller.identityState` (which is backed by the
     * real, on-device `CredentialStore`, unmodified for demo mode) --
     * otherwise a real SteamID64/persona left over from a prior session,
     * or one persisted moments earlier during onboarding Step 2 (before
     * the user tapped "Skip for now"), could render on a demo screenshot.
     * Reverting the gate (removing the `if (demoMode) { ... return }`
     * block, or moving it AFTER the `identityState` read) is exactly what
     * this test is built to catch: the ORDERING is the guarantee, not just
     * the gate's presence somewhere in the function.
     */
    @Test
    fun `MUTATION PIN -- SteamIdentitySection gates on demoMode and returns before reading real CredentialStore-backed identity`() {
        val text = read("src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt")
        val start = text.indexOf("private fun SteamIdentitySection(")
        check(start >= 0) { "expected to find SteamIdentitySection in SettingsScreen.kt" }
        val end = text.indexOf("private fun steamLibraryStatusText(", start)
        check(end > start) { "expected steamLibraryStatusText to follow SteamIdentitySection in SettingsScreen.kt" }
        val body = text.substring(start, end)

        val gateIdx = body.indexOf("if (demoMode) {")
        val identityReadIdx = body.indexOf("controller.identityState")
        assertTrue("expected an 'if (demoMode) {' gate in SteamIdentitySection (found body:\n$body)", gateIdx >= 0)
        assertTrue(
            "expected 'controller.identityState' to be read at least once in SteamIdentitySection (found body:\n$body)",
            identityReadIdx >= 0,
        )
        val returnIdx = body.indexOf("return", gateIdx)
        assertTrue(
            "expected a 'return' inside the demoMode gate, before the first identityState read (found body:\n$body)",
            returnIdx in gateIdx until identityReadIdx,
        )
    }

    /**
     * MUTATION PIN (WP APP-DEMO review round 2, S1). `ConnectionSection`'s
     * Disconnect action (`CredentialStore.clear()` -- the WHOLE store,
     * Steam identity included) must be hidden while `demoMode` is true;
     * offering it would let a demo-mode tap silently wipe a real identity
     * left over from before this demo session started. Reconnect is
     * deliberately NOT gated -- it is the documented way to leave demo mode.
     */
    @Test
    fun `MUTATION PIN -- ConnectionSection hides Disconnect while demoMode is true`() {
        val text = read("src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt")
        val start = text.indexOf("private fun ConnectionSection(")
        check(start >= 0) { "expected to find ConnectionSection in SettingsScreen.kt" }
        val body = text.substring(start)

        val gateIdx = body.indexOf("if (!demoMode) {")
        val buttonIdx = body.indexOf("settings_disconnect_button")
        assertTrue("expected an 'if (!demoMode) {' gate in ConnectionSection (found body:\n$body)", gateIdx >= 0)
        assertTrue("expected 'settings_disconnect_button' in ConnectionSection (found body:\n$body)", buttonIdx >= 0)
        assertTrue(
            "expected the Disconnect button reference to fall AFTER the '!demoMode' gate opens, not before it " +
                "(found body:\n$body)",
            gateIdx < buttonIdx,
        )
    }

    /**
     * MUTATION PIN (WP APP-DEMO review round 2, S2). A configuration change
     * (typically rotation) re-runs `onCreate` with no
     * `android:configChanges` declared for this Activity, so
     * `MainActivity.onCreate` restores demo mode from the
     * `KEY_WAS_IN_DEMO_MODE` flag saved in `onSaveInstanceState` -- but the
     * ORDERING matters, not just the restore's existence: `refreshVaultApiClient()`
     * (the one place `demoState` is ever cleared, and the one place a real
     * connection is ever built from `CredentialStore`) must run FIRST, so a
     * real connection found there always wins over a saved "was in demo
     * mode" flag from before rotation. Restoring demo mode before that call
     * -- or not at all -- is exactly the regression WP brief constraint 4
     * ("switching to a real connection must not leave demo state behind")
     * warns about, just triggered by rotation instead of onboarding.
     */
    @Test
    fun `MUTATION PIN -- onCreate restores demo mode across rotation, but only AFTER refreshVaultApiClient runs`() {
        // Comments stripped (this file's own kdoc/inline comments legitimately
        // NAME "refreshVaultApiClient()"/"enterDemoMode()" while explaining
        // the ordering -- an un-stripped scan would find those mentions
        // instead of the real call sites and pass regardless of the actual
        // code order, exactly the false-negative `DemoModeImportAllowlistTest`
        // guards against for a different scan).
        val text = stripComments(read("src/main/java/dev/steamvault/app/MainActivity.kt"))
        val onCreateStart = text.indexOf("override fun onCreate(")
        check(onCreateStart >= 0) { "expected to find onCreate in MainActivity.kt" }
        val refreshDefStart = text.indexOf("private fun refreshVaultApiClient(", onCreateStart)
        check(refreshDefStart > onCreateStart) { "expected refreshVaultApiClient's definition to follow onCreate in MainActivity.kt" }
        val window = text.substring(onCreateStart, refreshDefStart)

        val refreshCallIdx = window.indexOf("refreshVaultApiClient()")
        val restoreCheckIdx = window.indexOf("KEY_WAS_IN_DEMO_MODE")
        val enterDemoCallIdx = window.indexOf("enterDemoMode()")

        assertTrue("expected a refreshVaultApiClient() call inside onCreate (found window:\n$window)", refreshCallIdx >= 0)
        assertTrue(
            "expected a KEY_WAS_IN_DEMO_MODE restore check inside onCreate (found window:\n$window)",
            restoreCheckIdx >= 0,
        )
        assertTrue(
            "expected refreshVaultApiClient() to run BEFORE the demo-mode restore check in onCreate " +
                "(found window:\n$window)",
            refreshCallIdx < restoreCheckIdx,
        )
        assertTrue(
            "expected an enterDemoMode() call gated behind the restore check in onCreate (found window:\n$window)",
            enterDemoCallIdx in restoreCheckIdx..window.length,
        )
    }
}
