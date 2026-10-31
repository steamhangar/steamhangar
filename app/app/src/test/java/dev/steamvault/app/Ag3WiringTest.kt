package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP AG-3's UI/controller wiring (round 2 fix, blocker
 * B1). Same source-text-scan technique `DemoModeUiWiringTest`/
 * `DemoModeImportAllowlistTest` already use for exactly this reason: a JVM
 * unit test cannot render Compose (no emulator, no Compose test rule in
 * this project -- `app/README.md`'s standing constraint), so the model-layer
 * tests (`InstalledStateTest`, `GameCardModelTest`, `SchedulePresentationTest`,
 * `SerializationRoundTripTest`) prove the DECISION logic is right, but they
 * cannot prove any screen actually CALLS that logic. Round 1 shipped exactly
 * that gap: the reviewer deleted the badge line out of `GameCard.kt`/
 * `GameListRow.kt`, the installed section out of `GameDetailSheet.kt`, the
 * toggle and status/risk block out of `SettingsScreen.kt` (together --
 * lint stays clean because the now-uncalled private composables keep their
 * string resource references alive, so no unused-resource lint fires
 * either), the schedule fetch out of `load()`, the re-fetch out of `save()`,
 * and `gameSummaryFrom`'s `installed_on` copy -- and 643/643 stayed green
 * through every one of them. This file is the fix: one named pin per
 * deletion, each asserting a literal call/assignment is present (and, where
 * ordering is the actual guarantee, that it falls in the right place), so
 * removing any one of those wires again fails a test that names exactly
 * which edit applies.
 */
class Ag3WiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    /** Matches `@Composable` immediately followed (across whitespace/
     * newlines) by an optional `private` and a `fun NAME(` -- same regex
     * `DemoModeUiWiringTest` already established for this codebase's UI
     * layer (every composable, top-level or helper, is written exactly
     * this way). */
    private val COMPOSABLE_FUN = Regex("""@Composable\s+(?:private\s+)?fun\s+(\w+)""")

    /** The name of the nearest `@Composable fun` whose declaration precedes
     * [callIdx] in [code] -- `null` if none does. */
    private fun nearestEnclosingComposable(code: String, callIdx: Int): String? =
        COMPOSABLE_FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    // -----------------------------------------------------------------
    // Library grid card + list row (GameCard.kt / GameListRow.kt)
    //
    // Round 2 fix (reviewer-verified upgrade): the previous version of
    // these two pins scanned a pre-sliced text WINDOW (GameCard.kt) or the
    // WHOLE FILE via a bare `contains` (GameListRow.kt) for the literal
    // call text, with no check on which composable actually ENCLOSES it.
    // Measured failure in both directions: moving the badge block into a
    // new private helper that is NEVER CALLED from GameCard/GameListRow
    // stayed green (the literal call text is still textually present
    // somewhere in the scanned window/file, so `contains` finds it, even
    // though nothing in the render tree ever reaches it and the badge
    // silently vanishes from the grid) -- and, in the other direction, the
    // SAME helper correctly called but simply defined below `CapsulePill`
    // (still outside the old GameCard-to-CapsulePill window) made the old
    // pin fail with "deleting this call", the wrong diagnosis for a
    // legitimate refactor. Fixed by adopting `DemoModeUiWiringTest`'s own
    // round-3 technique verbatim: find the call ANYWHERE in the
    // (comment-stripped) file, then require its nearest enclosing
    // `@Composable fun` to be LITERALLY named `GameCard`/`GameListRow` --
    // a helper is not an acceptable substitute here either, called or not,
    // same stance that file's own kdoc already takes for the demo banner.
    // This kills the dead-helper case (enclosing name is the helper's, not
    // GameCard's -- FAILS, correctly) and gives an ACCURATE message for the
    // moved-and-called case (also FAILS, but says "found it enclosed by
    // 'X' instead", never "deleted").
    // -----------------------------------------------------------------

    @Test
    fun `MUTATION PIN -- GameCard renders the installed badge via installedBadgeText, directly in its own composable`() {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/library/GameCard.kt"))
        val callIdx = text.indexOf("installedBadgeText(model.installedBadge)")
        assertTrue(
            "GameCard.kt must call installedBadgeText(model.installedBadge) somewhere in the file to render " +
                "the WP AG-3 badge -- this call is entirely ABSENT, exactly round 1's B1 regression " +
                "(deleted, not moved).",
            callIdx >= 0,
        )
        val enclosing = nearestEnclosingComposable(text, callIdx)
        assertEquals(
            "installedBadgeText(model.installedBadge) exists in GameCard.kt, but its nearest enclosing " +
                "@Composable function is '$enclosing', not GameCard's own top-level 'GameCard' -- a helper " +
                "is not an acceptable substitute here (called or not): if never called from GameCard, the " +
                "badge silently vanishes from the grid with no compile/lint signal; if it IS called, this " +
                "message says exactly what moved, instead of a false 'deleted' diagnosis.",
            "GameCard",
            enclosing,
        )
    }

    @Test
    fun `MUTATION PIN -- GameListRow renders the installed badge via installedBadgeText, directly in its own composable`() {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/library/GameListRow.kt"))
        val callIdx = text.indexOf("installedBadgeText(model.installedBadge)")
        assertTrue(
            "GameListRow.kt must call installedBadgeText(model.installedBadge) somewhere in the file to " +
                "render the WP AG-3 badge -- this call is entirely ABSENT, exactly round 1's B1 regression " +
                "(deleted, not moved).",
            callIdx >= 0,
        )
        val enclosing = nearestEnclosingComposable(text, callIdx)
        assertEquals(
            "installedBadgeText(model.installedBadge) exists in GameListRow.kt, but its nearest enclosing " +
                "@Composable function is '$enclosing', not GameListRow's own top-level 'GameListRow' -- a " +
                "helper is not an acceptable substitute here (called or not): if never called from " +
                "GameListRow, the badge silently vanishes from the list row with no compile/lint signal; " +
                "if it IS called, this message says exactly what moved, instead of a false 'deleted' " +
                "diagnosis.",
            "GameListRow",
            enclosing,
        )
    }

    // -----------------------------------------------------------------
    // Detail sheet (GameDetailSheet.kt)
    // -----------------------------------------------------------------

    @Test
    fun `MUTATION PIN -- gameSummaryFrom copies installed_on across from GameDetail`() {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/detail/GameDetailSheet.kt"))
        val start = text.indexOf("private fun gameSummaryFrom(")
        check(start >= 0) { "expected to find gameSummaryFrom in GameDetailSheet.kt" }
        val end = text.indexOf("internal fun GameDetailSheetBody(", start)
        check(end > start) { "expected GameDetailSheetBody to follow gameSummaryFrom in GameDetailSheet.kt" }
        val body = text.substring(start, end)

        assertTrue(
            "gameSummaryFrom() must copy installed_on = detail.installed_on -- without it, " +
                "installedBadgeFor(gameSummary) downstream always sees an empty list, silently hiding every " +
                "badge on the detail sheet (found body:\n$body)",
            body.contains("installed_on = detail.installed_on"),
        )
    }

    @Test
    fun `MUTATION PIN -- LoadedDetailBody calls installedBadgeFor and renders the not-cached warning gated on that branch`() {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/detail/GameDetailSheet.kt"))
        val start = text.indexOf("private fun LoadedDetailBody(")
        check(start >= 0) { "expected to find LoadedDetailBody in GameDetailSheet.kt" }
        val end = text.indexOf("private fun JobControlRow(", start)
        check(end > start) { "expected JobControlRow to follow LoadedDetailBody in GameDetailSheet.kt" }
        val body = text.substring(start, end)

        val badgeCallIdx = body.indexOf("installedBadgeFor(gameSummary)")
        assertTrue(
            "LoadedDetailBody must call installedBadgeFor(gameSummary) (found body:\n$body)",
            badgeCallIdx >= 0,
        )

        val badgeDisplayIdx = body.indexOf("installedBadgeText(installedBadge, includeTimestamp = true)")
        assertTrue(
            "LoadedDetailBody must render the badge's own display text via " +
                "installedBadgeText(installedBadge, includeTimestamp = true) -- deleting just this Text " +
                "call (leaving installedBadgeFor's call and the warning block intact) removed the " +
                "'installed on <client>' line entirely (found body:\n$body)",
            badgeDisplayIdx > badgeCallIdx,
        )

        val notCachedGateIdx = body.indexOf("is InstalledBadge.InstalledNotCached")
        assertTrue(
            "LoadedDetailBody must branch on InstalledBadge.InstalledNotCached to decide whether to show the " +
                "protection-gap warning (found body:\n$body)",
            notCachedGateIdx > badgeCallIdx,
        )

        val warningStringIdx = body.indexOf("detail_installed_not_cached_warning")
        assertTrue(
            "LoadedDetailBody must reference detail_installed_not_cached_warning -- the explicit statement " +
                "the whole installed_on field exists for -- AFTER the InstalledNotCached gate opens, not " +
                "unconditionally and not before it (found body:\n$body)",
            warningStringIdx > notCachedGateIdx,
        )
    }

    // -----------------------------------------------------------------
    // Settings screen (SettingsScreen.kt)
    // -----------------------------------------------------------------

    @Test
    fun `MUTATION PIN -- SettingsForm renders the sweep_include_cached toggle AND the schedule status block`() {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt"))
        val start = text.indexOf("private fun SettingsForm(")
        check(start >= 0) { "expected to find SettingsForm in SettingsScreen.kt" }
        val end = text.indexOf("private fun captionFor(", start)
        check(end > start) { "expected captionFor to follow SettingsForm in SettingsScreen.kt" }
        val body = text.substring(start, end)

        assertTrue(
            "SettingsForm must call SweepIncludeCachedField(...) for the sweep_include_cached entry -- " +
                "deleting this call (round 1's B1 regression) removed the toggle entirely while lint and " +
                "643/643 stayed green (found body:\n$body)",
            body.contains("SweepIncludeCachedField("),
        )
        assertTrue(
            "SettingsForm must call SweepStatusBlock(controller.schedule) to render the last-sweep status " +
                "line and the GC-risk warning (found body:\n$body)",
            body.contains("SweepStatusBlock(controller.schedule)"),
        )
    }

    // -----------------------------------------------------------------
    // Settings controller (SettingsController.kt)
    // -----------------------------------------------------------------

    @Test
    fun `MUTATION PIN -- load() fetches and assigns the schedule from scheduleRepository`() {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/settings/SettingsController.kt"))
        val start = text.indexOf("suspend fun load(")
        check(start >= 0) { "expected to find load() in SettingsController.kt" }
        val end = text.indexOf("fun setDraft(", start)
        check(end > start) { "expected setDraft to follow load() in SettingsController.kt" }
        val body = text.substring(start, end)

        assertTrue(
            "load() must assign schedule from scheduleRepository.get() (best-effort) -- deleting this left " +
                "the toggle's status/risk block permanently blank while every other assertion stayed green " +
                "(found body:\n$body)",
            body.contains("scheduleRepository.get()") && body.contains("schedule = try"),
        )
    }

    @Test
    fun `MUTATION PIN -- save() re-fetches and assigns the schedule from scheduleRepository after a successful PATCH`() {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/settings/SettingsController.kt"))
        val start = text.indexOf("suspend fun save(")
        check(start >= 0) { "expected to find save() in SettingsController.kt" }
        val end = text.indexOf("fun dismissToast(", start)
        check(end > start) { "expected dismissToast to follow save() in SettingsController.kt" }
        val body = text.substring(start, end)

        val patchIdx = body.indexOf("settingsRepository.patch(patch)")
        val scheduleReassignIdx = body.indexOf("schedule = try")
        val scheduleGetIdx = body.indexOf("scheduleRepository.get()")

        assertTrue("expected save() to call settingsRepository.patch(patch) (found body:\n$body)", patchIdx >= 0)
        assertTrue(
            "save() must re-fetch the schedule (schedule = try { scheduleRepository.get() ... }) AFTER a " +
                "successful PATCH, so sweep_include_cached/auto_gc changes reflect in the risk warning " +
                "immediately -- deleting this re-fetch stayed 643/643 green (found body:\n$body)",
            scheduleReassignIdx > patchIdx && scheduleGetIdx > patchIdx,
        )
    }
}
