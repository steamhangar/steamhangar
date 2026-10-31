package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.RepairDepotOut
import dev.steamvault.app.net.model.RepairOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-DL-2: `CacheRepair.kt`, the twin of web `lib/cache-repair.js`
 * (CACHE-REPAIR-UI-1). Cases mirror web/tests/cache-repair.test.js; every
 * expected string is a LITERAL. The reducer's headline guarantee (EXECUTE
 * only after a confirm) and the per-flow token are pinned by name.
 */
class CacheRepairTest {

    private fun row(
        appid: Int = 500,
        pct: Int? = 99,
        missing: Int? = 0,
        status: String = "done",
        notOwned: Boolean = false,
        tool: Boolean = false,
    ) = GameSummary(
        appid = appid, name = "Emberreach", status = status, depot_count = 1,
        cache_complete_pct = pct, cache_missing_chunks = missing, not_owned = notOwned, tool_app = tool,
    )

    private fun job(appid: Int, status: String, type: String = "prefill") =
        JobSummary(id = 7, appid = appid, type = type, status = status, created_at = "2026-10-31T10:00:00Z")

    private fun report(
        damaged: Int = 3,
        bytes: Long = 4_404_019L,
        exceeds: Boolean = false,
        unreadable: List<Int> = emptyList(),
        removed: Int = 0,
        removedBytes: Long = 0,
        skipped: Map<String, Int> = emptyMap(),
        jobId: Int? = null,
        dedup: Boolean? = null,
    ) = RepairOut(
        appid = 500, damaged_chunks = damaged, damaged_bytes = bytes,
        depots = if (damaged > 0) listOf(RepairDepotOut(2010081, "1", damaged, bytes)) else emptyList(),
        unreadable_depots = unreadable, exceeds_cap = exceeds, max_files = 2000,
        removed_chunks = removed, removed_bytes = removedBytes, skipped = skipped, job_id = jobId, job_deduplicated = dedup,
    )

    @Test
    fun `wording is the web's`() {
        assertEquals("Repair", RepairWording.LABEL)
        assertEquals("Checking…", RepairWording.CHECKING_LABEL)
        assertEquals("Damaged chunks — Verify can't fix these, Repair can.", RepairWording.NOTE)
        assertEquals("Delete and repair", RepairWording.CONFIRM_LABEL)
        assertEquals("Cancel", RepairWording.CANCEL_LABEL)
        assertEquals("Show job", RepairWording.SHOW_JOB_LABEL)
        assertEquals("Repair — Emberreach", repairSpokenLabel("Emberreach"))
        assertEquals("Repair Emberreach?", repairConfirmTitle("Emberreach"))
        assertEquals("Can't repair Emberreach", repairBlockedTitle("Emberreach"))
        assertEquals("1 file", repairFileCount(1))
        assertEquals("12 files", repairFileCount(12))
    }

    @Test
    fun `sizes, plan, depot lines and the unreadable note`() {
        assertEquals("0 MB", repairSizeText(0))
        assertEquals("0 MB", repairSizeText(null))
        assertEquals("4.2 MB", repairSizeText(4_404_019L))
        assertEquals("2.0 GB", repairSizeText(2L * 1_073_741_824L))
        assertEquals(
            "3 files (4.2 MB) in the cache have the wrong size. Repair deletes them, then queues a forced download that fetches fresh copies.",
            repairPlanText(report()),
        )
        assertEquals("Depot 2010081 · 3 files · 4.2 MB", repairDepotLine(report().depots.single()))
        assertEquals("", unreadableDepotsText(report()))
        assertEquals("Depot 11 could not be read and is left out.", unreadableDepotsText(report(unreadable = listOf(11))))
        assertEquals("Depots 11, 12 could not be read and are left out.", unreadableDepotsText(report(unreadable = listOf(11, 12))))
        assertEquals(
            "2001 files have the wrong size — more than the repair limit of 2000. " +
                "That many mismatches point at a wrong target manifest or a broken cache. Nothing was deleted.",
            repairBlockedText(report(damaged = 2001, exceeds = true)),
        )
    }

    @Test
    fun `the result sentence`() {
        assertEquals("Deleted 3 files (4.2 MB). Job #41 fetches fresh copies.", repairDoneText(report(removed = 3, removedBytes = 4_404_019L, jobId = 41)))
        assertEquals(
            "Deleted 2 files (4.2 MB). 1 file changed meanwhile and stayed. The already queued job #41 fetches fresh copies.",
            repairDoneText(report(removed = 2, removedBytes = 4_404_019L, skipped = mapOf("size_changed" to 1, "gone" to 0), jobId = 41, dedup = true)),
        )
        assertEquals("Deleted 1 file (0 MB).", repairDoneText(report(removed = 1)))
    }

    @Test
    fun `who gets the button and the note`() {
        assertTrue(showsRepair(row(), emptyList()))
        assertEquals(RepairWording.NOTE, repairNoteFor(row()))
        // Missing chunk files: Verify, not Repair.
        assertFalse(showsRepair(row(missing = 3), emptyList()))
        assertEquals("", repairNoteFor(row(missing = 3)))
        assertFalse(showsRepair(row(pct = 100), emptyList()))
        assertFalse(showsRepair(row(pct = null, missing = null), emptyList()))
        assertFalse(showsRepair(null, emptyList()))
        // Tool apps never; not owned: no button, the note says why.
        assertFalse(showsRepair(row(tool = true), emptyList()))
        assertEquals("", repairNoteFor(row(tool = true)))
        assertFalse(showsRepair(row(notOwned = true), emptyList()))
        assertEquals(
            "Damaged chunks — can't be repaired: this Steam account doesn't own the game.",
            repairNoteFor(row(notOwned = true)),
        )
        // An active prefill of its own hides it; a finished one or a GC does not.
        for (s in listOf("queued", "running", "paused")) assertFalse(s, showsRepair(row(), listOf(job(500, s))))
        assertTrue(showsRepair(row(), listOf(job(500, "done"))))
        assertTrue(showsRepair(row(), listOf(job(500, "running", type = "gc"))))
        assertTrue(showsRepair(row(), listOf(job(501, "running"))))
    }

    private val checking = RepairFlowState.Checking(500, "Emberreach", token = 1)

    @Test
    fun `the flow -- dry run to confirm, blocked over the cap, idle with nothing damaged`() {
        val start = reduceRepairFlow(RepairFlowState.Idle, RepairEvent.Start(500, "Emberreach", 1))
        assertEquals(checking, start)
        val confirm = reduceRepairFlow(checking, RepairEvent.DryRunOk(1, report()))
        assertTrue(confirm is RepairFlowState.Confirm)
        assertTrue(reduceRepairFlow(checking, RepairEvent.DryRunOk(1, report(damaged = 2001, exceeds = true))) is RepairFlowState.Blocked)
        assertEquals(RepairFlowState.Idle, reduceRepairFlow(checking, RepairEvent.DryRunOk(1, report(damaged = 0))))
        assertEquals(RepairFlowState.Idle, reduceRepairFlow(checking, RepairEvent.DryRunFailed(1)))
        // A second start while a flow runs is a no-op.
        assertSame(checking, reduceRepairFlow(checking, RepairEvent.Start(501, "Other", 2)))
    }

    @Test
    fun `MUTATION PIN -- EXECUTING is reached only by Confirm from CONFIRM`() {
        val confirm = RepairFlowState.Confirm(500, "Emberreach", 1, report())
        val executing = reduceRepairFlow(confirm, RepairEvent.Confirm)
        assertTrue(executing is RepairFlowState.Executing)
        val others = listOf(
            RepairFlowState.Idle,
            checking,
            RepairFlowState.Blocked(500, "Emberreach", 1, report(exceeds = true)),
            executing,
            RepairFlowState.Done(500, "Emberreach", 1, report()),
        )
        for (s in others) assertSame("Confirm from $s must be a no-op", s, reduceRepairFlow(s, RepairEvent.Confirm))
        // Close is a no-op while the execute is in flight.
        assertSame(executing, reduceRepairFlow(executing, RepairEvent.Close))
        assertEquals(RepairFlowState.Idle, reduceRepairFlow(confirm, RepairEvent.Close))
        assertEquals(RepairFlowState.Idle, reduceRepairFlow(executing, RepairEvent.Reset))
    }

    @Test
    fun `MUTATION PIN -- a late answer of another flow is dropped (per-flow token)`() {
        assertSame(checking, reduceRepairFlow(checking, RepairEvent.DryRunOk(2, report())))
        assertSame(checking, reduceRepairFlow(checking, RepairEvent.DryRunFailed(2)))
        val executing = RepairFlowState.Executing(500, "Emberreach", 3, report())
        assertSame(executing, reduceRepairFlow(executing, RepairEvent.ExecuteOk(1, report(removed = 3, jobId = 9))))
        assertSame(executing, reduceRepairFlow(executing, RepairEvent.ExecuteFailed(1, "boom")))
        val done = reduceRepairFlow(executing, RepairEvent.ExecuteOk(3, report(removed = 3, jobId = 9)))
        assertEquals(9, (done as RepairFlowState.Done).report.job_id)
        val failed = reduceRepairFlow(executing, RepairEvent.ExecuteFailed(3, "boom"))
        assertEquals("boom", (failed as RepairFlowState.Confirm).error)
    }

    @Test
    fun `errors -- network, 409 busy vs not_owned, 5xx on execute, the server's detail`() {
        val net = VaultApiError.Network("POST failed")
        assertEquals(RepairErrorOutcome("Could not reach vault-api — nothing was changed.", false), describeRepairError(net, execute = false))
        assertEquals(
            RepairErrorOutcome("The repair did not answer cleanly — some files may already be deleted. Re-checking Downloads…", true),
            describeRepairError(net, execute = true),
        )
        val busy = VaultApiError.forHttpStatus(409, "POST", "/v1/cache/500/repair", "Job 7 for app 500 is running; retry once it has finished.")
        assertEquals(RepairWording.BUSY_TEXT, describeRepairError(busy, execute = false).message)
        assertTrue(describeRepairError(busy, execute = false).refresh)
        val notOwned = VaultApiError.forHttpStatus(409, "POST", "/v1/cache/500/repair", "The last prefill of app 500 ended not_owned.")
        assertEquals(RepairWording.NOT_OWNED_TEXT, describeRepairError(notOwned, execute = true).message)
        val server = VaultApiError.forHttpStatus(500, "POST", "/v1/cache/500/repair", "guard")
        assertEquals(RepairWording.EXECUTE_UNKNOWN_TEXT, describeRepairError(server, execute = true).message)
        assertEquals(RepairErrorOutcome("guard", false), describeRepairError(server, execute = false))
        val cap = VaultApiError.forHttpStatus(422, "POST", "/v1/cache/500/repair", "2001 damaged chunk files exceed the repair cap")
        assertEquals(RepairErrorOutcome("2001 damaged chunk files exceed the repair cap", false), describeRepairError(cap, execute = true))
        val bare = VaultApiError.forHttpStatus(404, "POST", "/v1/cache/500/repair", null)
        assertEquals("POST /v1/cache/500/repair failed (404)", describeRepairError(bare, execute = false).message)
    }
}
