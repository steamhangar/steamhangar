package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP CACHE-COMPLETE-1c (app) -- the card and detail-sheet half of
 * `CacheCompleteness.kt`, the same guarantees `web/tests/
 * cache-completeness.test.js` pins by name for the web twin:
 *  C1 only a known 100 gets the green check ("Current", CACHED);
 *  C2 below 100 -> "Partially cached N%" / "Partial N%", icon PARTIAL;
 *  C3 unknown (null or malformed) with bytes on disk -> NOT the green check:
 *     "Cached · not verified" / "Not verified", icon UNVERIFIED;
 *  C4 "Update available" wins over every completeness state;
 *  C5 non-cached kinds keep their own status;
 *  C6 a group card reads `group_cache_complete_pct`, a plain card its own;
 *  C8 "Complete now"/Verify only help with a missing chunk file;
 *  plus "Never played" hidden on a partial card, kept on an unknown one.
 */
class CacheCompletenessTest {

    private val gb = 1_073_741_824L

    private fun row(
        appid: Int = 10,
        pct: Int? = 100,
        missing: Int? = if (pct == null) null else 0,
        size: Long? = 3 * gb,
        update: Boolean = false,
        tool: Boolean = false,
        groupPct: Int? = null,
        parent: Int? = null,
    ) = GameSummary(
        appid = appid,
        name = "Aurora $appid",
        status = "done",
        last_prefill_at = "2026-10-01T00:00:00Z",
        depot_count = 1,
        size_bytes = size,
        update_available = update,
        tool_app = tool,
        cache_complete_pct = pct,
        cache_expected_bytes = pct?.let { 3 * gb },
        cache_present_bytes = pct?.let { 3 * gb * it / 100 },
        cache_complete_checked_at = pct?.let { "2026-10-14T09:00:00Z" },
        cache_missing_chunks = missing,
        group_cache_complete_pct = groupPct,
        parent_appid = parent,
    )

    private fun model(game: GameSummary, packages: List<GameSummary> = emptyList(), job: JobSummary? = null, playtime: Int? = null) =
        buildGameCardModel(game, job, selected = false, selecting = false, packages = packages, ownedPlaytimeForever = playtime)

    @Test
    fun `knownPct accepts 0 to 100 only`() {
        assertEquals(0, knownPct(0))
        assertEquals(100, knownPct(100))
        assertNull(knownPct(101))
        assertNull(knownPct(-1))
        assertNull(knownPct(null))
    }

    @Test
    fun `MUTATION TARGET C1 -- only a known 100 is the green check`() {
        val m = model(row(pct = 100))
        assertEquals(StatusKind.CACHED, m.kind)
        assertNull(m.completeness)
        assertEquals(StatusKind.CACHED, cardIconKind(m))
        assertEquals(StatusKind.CACHED, cardIconKind(row(pct = 100), StatusKind.CACHED))
    }

    @Test
    fun `MUTATION TARGET C2 -- below 100 reads Partially cached N, compact Partial N, icon partial`() {
        val m = model(row(pct = 42, missing = 3))
        assertEquals("the kind stays CACHED (chips, actions)", StatusKind.CACHED, m.kind)
        assertEquals(CompletenessOverlay(StatusKind.PARTIAL, 42), m.completeness)
        assertEquals("Partially cached 42%", m.completeness!!.word)
        assertEquals("Partial 42%", m.completeness!!.compact)
        assertEquals(StatusKind.PARTIAL, cardIconKind(m))
        assertEquals(StatusKind.PARTIAL, cardIconKind(row(pct = 0, missing = 5), StatusKind.CACHED))
        assertEquals("Partially cached 0%", model(row(pct = 0, missing = 5)).completeness!!.word)
        assertEquals("Partially cached 99%", model(row(pct = 99, missing = 0)).completeness!!.word)
    }

    @Test
    fun `MUTATION TARGET C3 -- unknown with bytes on disk is never the green check`() {
        for (g in listOf(row(pct = null), row(pct = 140, missing = 0), row(pct = -3, missing = 0))) {
            val m = model(g)
            assertEquals(StatusKind.CACHED, m.kind)
            assertEquals(CompletenessOverlay(StatusKind.UNVERIFIED, null), m.completeness)
            assertEquals("Cached · not verified", m.completeness!!.word)
            assertEquals("Not verified", m.completeness!!.compact)
            assertEquals(StatusKind.UNVERIFIED, cardIconKind(m))
        }
    }

    @Test
    fun `an older server without the fields reads as not verified, never as complete`() {
        val old = GameSummary(appid = 7, status = "done", depot_count = 1, size_bytes = 5 * gb)
        assertEquals(StatusKind.UNVERIFIED, cardIconKind(model(old)))
    }

    @Test
    fun `MUTATION TARGET C4 -- Update available wins over partial, unknown and complete`() {
        for (g in listOf(row(pct = 42, missing = 3, update = true), row(pct = null, update = true), row(pct = 100, update = true))) {
            val m = model(g)
            assertTrue(m.updateState)
            assertNull(m.completeness)
            assertEquals(StatusKind.STALE, cardIconKind(m))
            assertEquals(StatusKind.STALE, cardIconKind(g, StatusKind.CACHED))
        }
    }

    @Test
    fun `MUTATION TARGET C5 -- non-cached kinds keep their own status, whatever the figure`() {
        val running = JobSummary(id = 1, appid = 10, type = "prefill", status = "running", created_at = "2026-10-14T09:00:00Z")
        assertNull(model(row(pct = 42, missing = 3), job = running).completeness)
        assertNull(completenessOverlay(row(pct = 42, missing = 3), StatusKind.ERROR, isGroup = false))
        assertNull(completenessOverlay(row(pct = null), StatusKind.NONE, isGroup = false))
        assertNull(model(row(pct = null, tool = true)).completeness)
        assertEquals(StatusKind.NONE, cardIconKind(model(row(pct = null, size = null))))
    }

    @Test
    fun `MUTATION TARGET C6 -- a group card reads the group figure, a plain card its own`() {
        val parent = row(appid = 500, pct = 100, groupPct = 75)
        val pkg = row(appid = 501, pct = 50, missing = 4, parent = 500)
        val group = model(parent, packages = listOf(pkg))
        assertEquals(CompletenessOverlay(StatusKind.PARTIAL, 75), group.completeness)
        assertNull("a plain card reads its own 100", model(parent).completeness)
        val unknownGroup = model(row(appid = 500, pct = 100, groupPct = null), packages = listOf(pkg))
        assertEquals(StatusKind.UNVERIFIED, unknownGroup.completeness?.icon)
        assertEquals(StatusKind.PARTIAL, cardIconKind(parent, StatusKind.CACHED, isGroup = true))
        assertEquals(StatusKind.CACHED, cardIconKind(parent, StatusKind.CACHED, isGroup = false))
    }

    @Test
    fun `Never played is hidden on a partial card and kept on an unknown one`() {
        assertTrue(model(row(pct = 100), playtime = 0).neverPlayed)
        assertFalse(model(row(pct = 80, missing = 2), playtime = 0).neverPlayed)
        assertTrue("its rule was bytes-based; unknown keeps it", model(row(pct = null), playtime = 0).neverPlayed)
    }

    @Test
    fun `the card model stays equal across two identical poll ticks`() {
        assertEquals(model(row(pct = 42, missing = 3)), model(row(pct = 42, missing = 3)))
        assertFalse(model(row(pct = 42, missing = 3)) == model(row(pct = 43, missing = 3)))
    }

    @Test
    fun `installedOnNames names every client, null when none`() {
        val entries = listOf(
            InstalledOnEntry(client_id = "Demon", reported_at = "2026-10-14T09:00:00Z"),
            InstalledOnEntry(client_id = "Frame", reported_at = "2026-10-14T08:00:00Z"),
        )
        assertEquals("Demon, Frame", installedOnNames(entries))
        assertNull(installedOnNames(emptyList()))
        assertEquals("Demon, Frame", model(row().copy(installed_on = entries)).installedNames)
    }

    @Test
    fun `MUTATION TARGET C8 -- completable vs damaged-only`() {
        assertTrue(isCompletable(row(pct = 42, missing = 3)))
        assertFalse(isDamagedOnly(row(pct = 42, missing = 3)))
        assertFalse(isCompletable(row(pct = 99, missing = 0)))
        assertTrue(isDamagedOnly(row(pct = 99, missing = 0)))
        assertFalse(isCompletable(row(pct = 100, missing = 0)))
        assertFalse(isDamagedOnly(row(pct = 100, missing = 0)))
        assertFalse(isCompletable(row(pct = null)))
        assertFalse(isDamagedOnly(row(pct = null)))
        assertFalse("missing unknown is not completable", isCompletable(row(pct = 42, missing = null)))
    }

    @Test
    fun `detail line -- own figure with bytes and time, unknown says so, 100 omits of`() {
        val p = row(pct = 80, missing = 9).copy(cache_expected_bytes = 4 * gb, cache_present_bytes = (3.2 * gb).toLong())
        assertTrue(completenessLine(p), completenessLine(p).matches(Regex("""^80% complete — 3\.2 GB of 4\.0 GB on disk · checked .+$""")))
        assertTrue(completenessLine(row()).startsWith("100% complete — 3.0 GB on disk · checked "))
        assertEquals("100% complete — 3.0 GB on disk", completenessLine(row().copy(cache_complete_checked_at = null)))
        assertEquals("Completeness unknown — not measured for the current build.", completenessLine(row(pct = null)))
        assertEquals(
            "nothing present still prints a figure",
            "0% complete — 0.0 GB of 3.0 GB on disk",
            completenessLine(row(pct = 0, missing = 3).copy(cache_present_bytes = 0, cache_complete_checked_at = null)),
        )
    }

    @Test
    fun `detail group line only for a group row with a known group figure`() {
        val parent = row(appid = 500, groupPct = 75)
        assertEquals("With content packages: 75% complete", groupCompletenessLine(parent, isGroup = true))
        assertNull("a plain row", groupCompletenessLine(parent, isGroup = false))
        assertNull(groupCompletenessLine(null, isGroup = true))
        assertNull(groupCompletenessLine(row(appid = 500, groupPct = null), isGroup = true))
    }

    @Test
    fun `detail block shown for a non-tool game with bytes, own or a package's, never for a tool app`() {
        assertTrue(showsCompletenessBlock(row(), emptyList()))
        assertFalse(showsCompletenessBlock(row(size = null), emptyList()))
        assertFalse(showsCompletenessBlock(row(tool = true), emptyList()))
        assertTrue(showsCompletenessBlock(row(appid = 500, size = null), listOf(row(appid = 501, parent = 500))))
    }

    @Test
    fun `MUTATION TARGET C8 -- Verify sends members with bytes, skips tool apps and damaged-only members, appid order`() {
        val members = listOf(
            row(appid = 30),
            row(appid = 10, pct = 50, missing = 4),
            row(appid = 40, pct = 99, missing = 0),
            row(appid = 20, size = null),
            row(appid = 50, tool = true),
            row(appid = 5, pct = null),
        )
        assertEquals(listOf(5, 10, 30), verifyAppids(members))
        assertEquals(emptyList<Int>(), verifyAppids(emptyList()))
        assertEquals("Verify — Aurora", verifySpokenLabel("Aurora"))
    }
}
