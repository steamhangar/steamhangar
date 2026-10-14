package dev.steamvault.app.ui.library.logic

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Cross-frontend wording contract for WP TOOL-APPS-2d (twin of web
 * TOOL-APPS-2c `lib/content-groups.js`, `components/package-list.js`,
 * `lib/group-delete.js`, and of WEB-FEAT-SUGGEST-2's "Never played" pill).
 * Every expected value is a hand-transcribed LITERAL from the web source,
 * never derived from the Kotlin constants under test (docs/LEARNINGS.md:
 * "cross-frontend contracts are pinned with LITERAL expected sets").
 */
class ContentGroupsContractTest {

    @Test
    fun `group card line and its warning`() {
        assertEquals("+1 content package", packagesLineText(1))
        assertEquals("+3 content packages", packagesLineText(3))
        assertEquals("1 failed", failedPackagesText(1))
        assertEquals("3 content packages", packagesAriaText(3, 0))
        assertEquals("3 content packages, 1 failed", packagesAriaText(3, 1))
    }

    @Test
    fun `detail sheet section`() {
        assertEquals("Content packages", PACKAGES_HEADING)
        assertEquals("Retry download", PACKAGE_RETRY_LABEL)
        assertEquals("Total with content packages: ${formatBytesGB(2_500_000_000L)}", packagesTotalText(2_500_000_000L))
        assertEquals(null, packagesTotalText(null))
        assertEquals(
            mapOf(
                "game" to "Game", "dlc" to "DLC", "tool" to "Tool", "application" to "Application",
                "config" to "Config", "music" to "Soundtrack", "video" to "Video", "demo" to "Demo",
            ),
            APP_TYPE_LABEL,
        )
    }

    @Test
    fun `grouped delete wording`() {
        assertEquals(
            "The list of content packages changed since this preview. Nothing was deleted — check the list below and confirm again.",
            MEMBERS_CHANGED_NOTICE,
        )
        assertEquals("Not possible right now", BLOCKED_HEADING)
        assertEquals("Deleting X frees about 0 GB.", groupDeleteIntro("X", 0L))
    }

    /** web components/group-delete-plan.js and views/library.js after
     * a8ec24e ("Deleted (N)" became "Will be deleted (N)"). */
    @Test
    fun `grouped delete plan block wording`() {
        assertEquals("Will be deleted (3)", willBeDeletedLabel(3))
        assertEquals("game", memberRoleWord("parent"))
        assertEquals("package", memberRoleWord("package"))
        assertEquals("Kept (1 shared depot)", keptDepotsLabel(1))
        assertEquals("Kept (2 shared depots)", keptDepotsLabel(2))
        assertEquals("553853 kept — Glasswing still uses it", keptDepotLine(553853, listOf("Glasswing")))
        assertEquals("0 GB freed", freedText(0L))
        assertEquals(null, unusableDepotsText(0))
        assertEquals("1 unreadable depot entry — the delete will report it as failed.", unusableDepotsText(1))
        assertEquals("2 unreadable depot entries — the delete will report them as failed.", unusableDepotsText(2))
        assertEquals("Not deleted: X (not a content package).", keptChildrenText(listOf(GroupPlanKeptChild(1, "X", "not a content package"))))
        assertEquals(
            "Not possible right now: X — Download running. Wait for it to finish or cancel it in Downloads.",
            blockedText(listOf(GroupPlanBlocked(1, "X", "Download running"))),
        )
        assertEquals(
            "Deleting 2 games and their content packages frees about 0 GB.",
            multiGroupDeleteIntro(2, 0L),
        )
    }

    @Test
    fun `never played pill`() {
        assertEquals("Never played", NEVER_PLAYED_LABEL)
        assertEquals("Unplayed", NEVER_PLAYED_COMPACT_LABEL)
        assertEquals("Fully cached, never played", NEVER_PLAYED_DESCRIPTION)
    }
}
