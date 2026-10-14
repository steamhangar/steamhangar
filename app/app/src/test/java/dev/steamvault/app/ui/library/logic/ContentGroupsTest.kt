package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP TOOL-APPS-2d (ADR-0022): grouping, selection expansion, failed
 * packages, the card model's group fields and the "Never played" gate.
 * Synthetic appids and names.
 */
class ContentGroupsTest {

    private fun g(
        appid: Int,
        name: String? = "Game $appid",
        status: String = "done",
        size: Long? = 1_000_000_000L,
        parent: Int? = null,
        type: String? = null,
        groupSize: Long? = null,
        update: Boolean = false,
        installedOn: List<InstalledOnEntry> = emptyList(),
        tool: Boolean = false,
    ) = GameSummary(
        appid = appid,
        name = name,
        status = status,
        last_prefill_at = if (status == "done") "2026-10-01T00:00:00Z" else null,
        depot_count = if (size != null) 1 else 0,
        size_bytes = size,
        parent_appid = parent,
        app_type = type,
        group_size_bytes = groupSize,
        update_available = update,
        installed_on = installedOn,
        tool_app = tool,
    )

    private val parent = g(100, "Starfall", groupSize = 2_500_000_000L, type = "game")
    private val pkgA = g(101, "Starfall - Voice Pack", parent = 100, type = "dlc", size = 300_000_000L)
    private val pkgB = g(102, "Starfall - Outfits", status = "error", size = null, parent = 100, type = "dlc")
    private val other = g(200, "Pixel Garden")

    @Test
    fun `packages are folded into their parent's card, input order kept`() {
        val grouped = groupGames(listOf(pkgA, parent, other, pkgB))
        assertEquals(listOf(100, 200), grouped.rows.map { it.appid })
        assertEquals(listOf(101, 102), grouped.packagesOf(100).map { it.appid })
        assertEquals(mapOf(101 to 100, 102 to 100), grouped.parentOf)
    }

    @Test
    fun `an older server's flat list (no parent_appid) renders exactly as before`() {
        val flat = listOf(parent.copy(parent_appid = null), pkgA.copy(parent_appid = null), other)
        val grouped = groupGames(flat)
        assertEquals(flat, grouped.rows)
        assertTrue(grouped.packagesByParent.isEmpty())
    }

    @Test
    fun `a package whose parent is missing, itself, a package, a tool app or owned-only stays a card`() {
        val orphan = g(300, parent = 999)
        val self = g(301, parent = 301)
        val nested = g(302, parent = 101)
        val tool = g(303, tool = true)
        val underTool = g(304, parent = 303)
        val ownedOnly = GameSummary(appid = 305, name = "Owned", status = "idle", depot_count = 0)
        val underOwned = g(306, parent = 305)
        val grouped = groupGames(listOf(parent, pkgA, orphan, self, nested, tool, underTool, ownedOnly, underOwned))
        assertEquals(listOf(100, 300, 301, 302, 303, 304, 305, 306), grouped.rows.map { it.appid })
        assertEquals(listOf(101), grouped.packagesOf(100).map { it.appid })
    }

    @Test
    fun `a tool app is never a package`() {
        val toolChild = g(400, parent = 100, tool = true)
        val grouped = groupGames(listOf(parent, toolChild))
        assertEquals(listOf(100, 400), grouped.rows.map { it.appid })
    }

    @Test
    fun `the group card ORs update_available and unions installed_on, keeps the parent's own fields`() {
        val pc = InstalledOnEntry("pc", "2026-10-01T00:00:00Z")
        val pcNewer = InstalledOnEntry("pc", "2026-10-02T00:00:00Z")
        val deck = InstalledOnEntry("deck", "2026-10-01T00:00:00Z")
        val p = parent.copy(installed_on = listOf(pc))
        val a = pkgA.copy(update_available = true, installed_on = listOf(pcNewer, deck))
        val row = groupGames(listOf(p, a)).rows.single()
        assertTrue(row.update_available)
        assertEquals(listOf(pcNewer, deck), row.installed_on)
        assertEquals(p.status, row.status)
        assertEquals(p.size_bytes, row.size_bytes)
    }

    @Test
    fun `the card shows the server's group size, a plain card its own`() {
        val grouped = groupGames(listOf(parent, pkgA, pkgB, other))
        val group = buildGameCardModel(
            grouped.rows[0], null, selected = false, selecting = false, packages = grouped.packagesOf(100),
        )
        assertEquals(formatBytesGB(2_500_000_000L), group.sizeLabel)
        assertEquals(2, group.packageCount)
        assertEquals(1, group.failedPackageCount)
        val plain = buildGameCardModel(other, null, selected = false, selecting = false)
        assertEquals(formatBytesGB(other.size_bytes), plain.sizeLabel)
        assertEquals(0, plain.packageCount)
        // No server group size (older server): the parent's own size.
        assertEquals(parent.size_bytes, displaySizeBytes(parent.copy(group_size_bytes = null), 2))
    }

    @Test
    fun `a package with a live job is not failed`() {
        val running = JobSummary(id = 1, appid = 102, type = "prefill", status = "running", created_at = "x")
        assertEquals(listOf(pkgB), failedPackages(listOf(pkgA, pkgB), emptyMap()))
        assertTrue(failedPackages(listOf(pkgA, pkgB), mapOf(102 to running)).isEmpty())
    }

    @Test
    fun `bulk selection expands a group card to the parent and every package, once each`() {
        val grouped = groupGames(listOf(parent, pkgA, pkgB, other))
        val expanded = expandSelection(listOf(other, parent, parent), grouped)
        assertEquals(listOf(200, 100, 101, 102), expanded.map { it.appid })
    }

    @Test
    fun `MUTATION PIN -- grouped bulk download targets equal the same rows picked ungrouped`() {
        val jobs = emptyList<JobSummary>()
        val all = listOf(parent, pkgA, pkgB, other)
        val grouped = groupGames(all)
        val viaGroup = buildBulkDownloadPlan(
            classifyBulkSelection(expandSelection(listOf(parent, other), grouped), jobs),
            4,
        )
        val ungrouped = buildBulkDownloadPlan(classifyBulkSelection(all, jobs), 4)
        assertEquals(ungrouped.primaryTargets.toSet(), viaGroup.primaryTargets.toSet())
        assertEquals(ungrouped.secondaryTargets.toSet(), viaGroup.secondaryTargets.toSet())
        assertEquals(listOf(102), viaGroup.primaryTargets) // the failed package needs a download
    }

    @Test
    fun `group has cache content when only a package holds bytes`() {
        val emptyParent = parent.copy(size_bytes = null)
        assertTrue(groupHasCacheContent(emptyParent, listOf(pkgA)))
        assertFalse(groupHasCacheContent(emptyParent, listOf(pkgB)))
    }

    @Test
    fun `short package name drops the parent prefix and a separator, nothing else`() {
        assertEquals("Voice Pack", shortPackageName("Starfall - Voice Pack", "Starfall"))
        assertEquals("Voice Pack", shortPackageName("STARFALL: Voice Pack", "Starfall"))
        assertEquals("Voice Pack", shortPackageName("Starfall — Voice Pack", "Starfall"))
        assertEquals("Starfall Voice Pack", shortPackageName("Starfall Voice Pack", "Starfall"))
        assertEquals("Other - Thing", shortPackageName("Other - Thing", "Starfall"))
        assertEquals("Starfall -", shortPackageName("Starfall -", "Starfall"))
        assertEquals("X", shortPackageName(" X ", null))
    }

    @Test
    fun `package facts line names id, type, size and where installed`() {
        val pkg = pkgA.copy(
            installed_on = listOf(
                InstalledOnEntry("pc", "2026-10-01T00:00:00Z"),
                InstalledOnEntry("deck", "2026-10-02T00:00:00Z"),
            ),
        )
        assertEquals("App 101 · DLC · ${formatBytesGB(300_000_000L)} · Installed on deck +1", packageFactsText(pkg))
        assertEquals("App 102 · DLC", packageFactsText(pkgB))
        assertEquals("App 5", packageFactsText(g(5, size = null, type = "beta")))
    }

    @Test
    fun `retry is offered only for a failed package with no queued, running or paused job`() {
        val queued = JobSummary(id = 2, appid = 102, type = "prefill", status = "queued", created_at = "x")
        assertTrue(offersPackageRetry(pkgB, null, null))
        assertFalse(offersPackageRetry(pkgB, null, queued))
        assertFalse(offersPackageRetry(pkgA, null, null))
    }

    @Test
    fun `Downloads fallback title names the parent for an unnamed package`() {
        val unnamed = pkgA.copy(name = null)
        assertEquals("Starfall · content 101", contentPackageFallbackTitle(unnamed) { if (it == 100) "Starfall" else null })
        assertNull(contentPackageFallbackTitle(other) { "x" })
        assertNull(contentPackageFallbackTitle(unnamed) { null })
    }

    // ---- "Never played" -------------------------------------------------------

    @Test
    fun `MUTATION PIN -- never played needs Cached, bytes, no tool flag and a REAL zero playtime`() {
        assertTrue(showsNeverPlayed(other, StatusKind.CACHED, 0))
        assertFalse("unknown playtime is not zero", showsNeverPlayed(other, StatusKind.CACHED, null))
        assertFalse("played", showsNeverPlayed(other, StatusKind.CACHED, 12))
        assertFalse("running", showsNeverPlayed(other, StatusKind.UPDATING, 0))
        assertFalse("no bytes", showsNeverPlayed(other.copy(size_bytes = null), StatusKind.CACHED, 0))
        assertFalse("tool app", showsNeverPlayed(other.copy(tool_app = true), StatusKind.CACHED, 0))
    }

    @Test
    fun `playtime map keeps real values only, first per appid`() {
        val owned = listOf(
            OwnedGame(appid = 1, name = "a", playtime_forever = 0),
            OwnedGame(appid = 1, name = "a", playtime_forever = 5),
            OwnedGame(appid = 2, name = "b"),
            OwnedGame(appid = 3, name = "c", playtime_forever = -1),
        )
        assertEquals(mapOf(1 to 0), ownedPlaytimeByAppid(owned))
        assertTrue(ownedPlaytimeByAppid(null).isEmpty())
    }

    @Test
    fun `card model carries the never-played decision`() {
        val model = buildGameCardModel(other, null, selected = false, selecting = false, ownedPlaytimeForever = 0)
        assertTrue(model.neverPlayed)
        assertFalse(buildGameCardModel(other, null, selected = false, selecting = false).neverPlayed)
    }
}
