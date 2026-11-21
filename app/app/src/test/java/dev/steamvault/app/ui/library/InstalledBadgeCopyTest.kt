package dev.steamvault.app.ui.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP AG-3 round 2 fix, blocker B2. The package's headline copy rule --
 * `installed_on` empty means "no fresh signal," which covers BOTH "never
 * installed anywhere" AND "installed, but the agent went quiet"
 * indistinguishably (api/README.md "Installed state per app") -- was
 * enforced by prose alone through round 1: three kdoc statements saying
 * `[]` must never render as "not installed anywhere," and nothing
 * mechanical stopping `installedBadgeText`'s `NoSignal` branch from doing
 * exactly that. The reviewer measured it: swapping `InstalledBadge.NoSignal
 * -> null` for `InstalledBadge.NoSignal -> "Not installed anywhere"` built
 * green at 643/643 with clean lint. This file is two independent pins:
 *
 *  1. A literal source check that the `NoSignal` branch of
 *     `installedBadgeText`'s `when` maps to `null` -- not a behavioral
 *     Compose call (this project has no Compose test rule / emulator to
 *     invoke a `@Composable` function from a plain JVM test, `app/README.md`'s
 *     standing constraint), but the same source-text-scan technique
 *     `DemoModeUiWiringTest`/`DemoModeImportAllowlistTest` already use for
 *     exactly this reason.
 *  2. A comment-stripped scan of every badge-path source file AND every
 *     `strings.xml` entry the badge path can render, asserting the
 *     forbidden phrase never appears in production text. Comments are
 *     stripped first because THIS kdoc, `InstalledBadgeText.kt`'s own
 *     kdoc, and `InstalledState.kt`'s kdoc all legitimately NAME the
 *     forbidden phrase while explaining why production code must never
 *     say it -- the same "prose may name it, code may not" exception
 *     `DemoModeImportAllowlistTest`'s `stripComments` already documents.
 */
class InstalledBadgeCopyTest {

    private val forbiddenPhrase = Regex("not installed anywhere", RegexOption.IGNORE_CASE)

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    /** The source of [name]'s body in InstalledBadgeText.kt (comments
     * stripped), from `fun NAME(` up to [nextDeclaration], the declaration
     * that follows it -- tight, so a pin on one function cannot pass on a
     * neighbour's text. */
    private fun functionBody(name: String, nextDeclaration: String): String {
        val text = stripComments(read("src/main/java/dev/steamvault/app/ui/library/InstalledBadgeText.kt"))
        val start = text.indexOf("fun $name(")
        check(start >= 0) { "expected to find $name in InstalledBadgeText.kt" }
        val end = text.indexOf(nextDeclaration, start)
        check(end > start) { "expected '$nextDeclaration' to follow $name in InstalledBadgeText.kt" }
        return text.substring(start, end)
    }

    private val noSignalToNull = Regex("""InstalledBadge\.NoSignal\s*->\s*null""")

    @Test
    fun `MUTATION PIN -- installedBadgeText maps InstalledBadge NoSignal to the literal null, not a string`() {
        val body = functionBody("installedBadgeText", "fun installedBadgeSpokenText(")

        assertTrue(
            "installedBadgeText's NoSignal branch must map to the literal 'null' -- a string here (even an " +
                "innocuous one) is exactly the B2 regression: 'installed_on' == [] means NO FRESH SIGNAL, " +
                "which covers both never-installed and agent-gone-quiet indistinguishably, so this branch " +
                "must render NOTHING (found body:\n$body)",
            noSignalToNull.containsMatchIn(body),
        )
    }

    @Test
    fun `MUTATION PIN -- installedDetailLineText maps InstalledBadge NoSignal to the literal null, not a string`() {
        val body = functionBody("installedDetailLineText", "private fun installedLabel(")

        assertTrue(
            "installedDetailLineText's NoSignal branch must map to the literal 'null' -- the detail sheet's " +
                "installed-on line must render NOTHING for 'installed_on' == [] (no fresh signal), same rule " +
                "as installedBadgeText (found body:\n$body)",
            noSignalToNull.containsMatchIn(body),
        )
    }

    @Test
    fun `MUTATION PIN -- no badge-path source file ever spells the forbidden phrase in production code`() {
        val badgePathFiles = listOf(
            "src/main/java/dev/steamvault/app/ui/library/InstalledBadgeText.kt",
            "src/main/java/dev/steamvault/app/ui/library/logic/InstalledState.kt",
            "src/main/java/dev/steamvault/app/ui/library/GameCard.kt",
            "src/main/java/dev/steamvault/app/ui/library/GameListRow.kt",
            "src/main/java/dev/steamvault/app/ui/detail/GameDetailSheet.kt",
        )
        val violations = mutableListOf<String>()
        for (path in badgePathFiles) {
            val code = stripComments(read(path))
            if (forbiddenPhrase.containsMatchIn(code)) {
                violations.add(path)
            }
        }
        assertEquals(
            "no badge-path source file (comments stripped -- kdoc prose IS allowed to name the phrase while " +
                "explaining why production code never says it) may contain the literal phrase " +
                "'not installed anywhere'. Violations: $violations",
            emptyList<String>(),
            violations,
        )
    }

    @Test
    fun `MUTATION PIN -- no strings xml resource the badge path can render spells the forbidden phrase`() {
        val stringsXml = read("src/main/res/values/strings.xml")
        val badgeResourceNames = listOf(
            "library_installed_on",
            "library_installed_not_cached",
            "library_installed_with_timestamp",
            "detail_installed_not_cached_warning",
        )
        val violations = mutableListOf<String>()
        for (name in badgeResourceNames) {
            val stringMatch = Regex("<string name=\"$name\">(.*?)</string>").find(stringsXml)
            check(stringMatch != null) { "expected <string name=\"$name\"> in strings.xml" }
            if (forbiddenPhrase.containsMatchIn(stringMatch.groupValues[1])) {
                violations.add("string/$name")
            }
        }
        val pluralsMatch = Regex("<plurals name=\"library_installed_additional_clients\">(.*?)</plurals>", RegexOption.DOT_MATCHES_ALL)
            .find(stringsXml)
        check(pluralsMatch != null) { "expected <plurals name=\"library_installed_additional_clients\"> in strings.xml" }
        if (forbiddenPhrase.containsMatchIn(pluralsMatch.groupValues[1])) {
            violations.add("plurals/library_installed_additional_clients")
        }

        assertEquals(
            "no strings.xml entry the badge path renders may contain the literal phrase " +
                "'not installed anywhere'. Violations: $violations",
            emptyList<String>(),
            violations,
        )
    }

    @Test
    fun `guard is not vacuous -- the forbidden-phrase regex actually matches the sentence it targets`() {
        assertTrue(forbiddenPhrase.containsMatchIn("This game is not installed anywhere on your network."))
        assertFalse(forbiddenPhrase.containsMatchIn("Installed on gaming-pc"))
    }
}
