package dev.steamvault.app.ui.library.logic

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP ANDROID-DL-2: every literal of [RepairWording] and every
 * template of `CacheRepair.kt` must still be in `web/js/lib/cache-repair.js`
 * (CACHE-REPAIR-UI-1), plus the reducer's and the error mapping's rules as
 * source fragments. VALUE drift -> update CacheRepair.kt; GRAMMAR drift ->
 * adjust this test. Never edit the web literals to satisfy it.
 */
class CacheRepairWebTwinTest {
    private val file = File("../../web/js/lib/cache-repair.js")
    private val js: String by lazy {
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        file.readText(Charsets.UTF_8)
    }

    private val quoted = listOf(
        RepairWording.LABEL,
        RepairWording.CHECKING_LABEL,
        RepairWording.HINT,
        RepairWording.NOTE,
        RepairWording.NOT_OWNED_NOTE,
        RepairWording.CONFIRM_LABEL,
        RepairWording.EXECUTING_LABEL,
        RepairWording.CANCEL_LABEL,
        RepairWording.CLOSE_LABEL,
        RepairWording.SHOW_JOB_LABEL,
        RepairWording.DONE_TITLE,
        RepairWording.NOOP_TITLE,
        RepairWording.NOTHING_TEXT,
        RepairWording.NOOP_TEXT,
        RepairWording.BUSY_TEXT,
        RepairWording.NOT_OWNED_TEXT,
        RepairWording.NETWORK_TEXT,
        RepairWording.EXECUTE_UNKNOWN_TEXT,
        RepairWording.FAILED_TEXT,
        RepairWording.PLAN_TAIL,
    )

    private val fragments = listOf(
        "return `\${REPAIR_LABEL} — \${title}`;",
        "return `\${REPAIR_LABEL} \${title}?`;",
        "return `Can't repair \${title}`;",
        "return `\${n} \${n === 1 ? \"file\" : \"files\"}`;",
        "if (typeof bytes !== \"number\" || !Number.isFinite(bytes) || bytes <= 0) return \"0 MB\";",
        "if (bytes < 1_073_741_824) return `\${(bytes / 1_048_576).toFixed(1)} MB`;",
        "`\${fileCount(n)} (\${repairSizeText(report.damaged_bytes)}) in the cache have the wrong size. `",
        "return `Depot \${depot.depotid} · \${fileCount(depot.damaged_chunks)} · \${repairSizeText(depot.damaged_bytes)}`;",
        "? `Depot \${which} could not be read and is left out.`",
        ": `Depots \${which} could not be read and are left out.`;",
        "`\${fileCount(report.damaged_chunks)} have the wrong size — more than the repair limit of \${report.max_files}. `",
        "\"That many mismatches point at a wrong target manifest or a broken cache. Nothing was deleted.\"",
        "const removed = `Deleted \${fileCount(report.removed_chunks)} (\${repairSizeText(report.removed_bytes)}).`;",
        "const skippedText = skipped ? ` \${fileCount(skipped)} changed meanwhile and stayed.` : \"\";",
        "? ` The already queued job #\${report.job_id} fetches fresh copies.`",
        ": ` Job #\${report.job_id} fetches fresh copies.`;",
        // Visibility.
        "const ACTIVE_JOB_STATUSES = new Set([\"queued\", \"running\", \"paused\"]);",
        "(j) => j && j.type === \"prefill\" && j.appid === appid && ACTIVE_JOB_STATUSES.has(j.status),",
        "if (!row || isToolApp(row) || isNotOwnedApp(row)) return false;",
        "if (!isDamagedOnly(row)) return false;",
        "if (!row || isToolApp(row) || !isDamagedOnly(row)) return \"\";",
        "return isNotOwnedApp(row) ? REPAIR_NOT_OWNED_NOTE : REPAIR_NOTE;",
        // The flow.
        "return state.token !== undefined && event.token === state.token;",
        "if (!report || !(report.damaged_chunks > 0)) return { kind: REPAIR_STATE.IDLE };",
        "if (report.exceeds_cap) return { kind: REPAIR_STATE.BLOCKED, ...base };",
        "return state.kind === REPAIR_STATE.CONFIRM\n        ? { kind: REPAIR_STATE.EXECUTING,",
        "return state.kind === REPAIR_STATE.CONFIRM || state.kind === REPAIR_STATE.BLOCKED || state.kind === REPAIR_STATE.DONE",
        // Errors.
        "return execute ? { message: REPAIR_EXECUTE_UNKNOWN_TEXT, refresh: true } : { message: REPAIR_NETWORK_TEXT, refresh: false };",
        "if (err && err.status === 409) {",
        "return { message: /not_owned/.test(detail) ? REPAIR_NOT_OWNED_TEXT : REPAIR_BUSY_TEXT, refresh: true };",
        "if (kind === ERROR_KINDS.SERVER && execute) return { message: REPAIR_EXECUTE_UNKNOWN_TEXT, refresh: true };",
        "return { message: detailOf(err) || (err && err.message) || REPAIR_FAILED_TEXT, refresh: false };",
    )

    @Test
    fun `the source is real`() {
        assertTrue("no 'export function reduceRepairFlow' -- re-check path/anchors", js.contains("export function reduceRepairFlow"))
    }

    @Test
    fun `every quoted wording literal is still in the web source`() {
        for (l in quoted) {
            if (!js.contains("\"$l\"")) {
                fail(
                    if (js.contains(l)) {
                        "GRAMMAR DRIFT: cache-repair.js contains \"$l\" but not as one double-quoted string -- adjust this test."
                    } else {
                        "VALUE DRIFT: cache-repair.js no longer contains \"$l\" -- update CacheRepair.kt."
                    },
                )
            }
        }
    }

    @Test
    fun `every template and rule fragment is still in the web source`() {
        for (f in fragments) {
            assertTrue("VALUE DRIFT: cache-repair.js no longer contains '$f' -- update CacheRepair.kt.", js.contains(f))
        }
    }

    /** The dry run sends no body, only the execute sends `{"execute": true}`. */
    @Test
    fun `the request shape follows the web's api call`() {
        val api = File("../../web/js/api.js").readText(Charsets.UTF_8)
        assertTrue(
            api.contains("request(\"POST\", `/v1/cache/\${appid}/repair`, execute === true ? { body: { execute: true } } : {}),"),
        )
    }
}
