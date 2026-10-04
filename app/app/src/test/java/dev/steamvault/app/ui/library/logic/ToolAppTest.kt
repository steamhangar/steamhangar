package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.R
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Steam tool apps on Android (WP API-FIX-4), the twin of
 * `web/tests/tool-app.test.js`.
 *
 * `GET /v1/games` flags an app vault-api never prefills
 * (`api/vault_api/tool_apps.py`: 228980, "Steamworks Common
 * Redistributables") with `tool_app` + `tool_app_name`. Production
 * 2026-10-04 showed it as "App 228980 / Failed / Installed but not cached /
 * Retry download". Pinned here: the server's name, the neutral NOTINUSE
 * kind and "Steam tool package" word (never ERROR, even with the old
 * `status: "error"`), no action, no "not cached" badge, never a bulk target,
 * and the copy equal to the web source (twin pin).
 */
class ToolAppTest {

    /** The production row: no vault name, `error` from the old failed jobs,
     * installed on a PC, no cached bytes of its own. */
    private fun redist(
        status: String = "error",
        sizeBytes: Long? = null,
        name: String? = null,
        toolAppName: String? = "Steamworks Common Redistributables",
        installed: Boolean = true,
    ): GameSummary = GameSummary(
        appid = 228980,
        name = name,
        status = status,
        last_prefill_at = null,
        last_manifest_check = null,
        depot_count = 0,
        size_bytes = sizeBytes,
        needs_force = true,
        installed_on = if (installed) listOf(InstalledOnEntry("gaming-pc", "2026-10-04T08:00:00Z")) else emptyList(),
        tool_app = true,
        tool_app_name = toolAppName,
    )

    private fun failedGame(appid: Int = 440, sizeBytes: Long? = null, status: String = "error"): GameSummary = GameSummary(
        appid = appid,
        name = "Team Fortress 2",
        status = status,
        depot_count = 1,
        size_bytes = sizeBytes,
        needs_force = true,
        installed_on = listOf(InstalledOnEntry("gaming-pc", "2026-10-04T08:00:00Z")),
    )

    // ---------- copy, literal and twin-pinned against the web ----------

    @Test
    fun `the tool-app words are the agreed copy`() {
        assertEquals("Steam tool package", TOOL_APP_STATE_WORD)
        assertEquals("Steam tool package — cached together with the games that use it.", TOOL_APP_NOTE)
    }

    @Test
    fun `TOOL_APP_STATE_WORD and TOOL_APP_NOTE equal web game-status js`() {
        val file = File("../../web/js/lib/game-status.js")
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        val js = file.readText(Charsets.UTF_8)
        fun webConst(name: String): String {
            val m = Regex("""export const $name = "([^"]*)";""").find(js)
                ?: error("GRAMMAR DRIFT: no 'export const $name = \"...\";' in web/js/lib/game-status.js -- widen this regex")
            return m.groupValues[1]
        }
        assertEquals("VALUE DRIFT: update GameStatus.kt", webConst("TOOL_APP_STATE_WORD"), TOOL_APP_STATE_WORD)
        assertEquals("VALUE DRIFT: update GameStatus.kt", webConst("TOOL_APP_NOTE"), TOOL_APP_NOTE)
    }

    // ---------- wire format ----------

    @Test
    fun `GameSummary decodes tool_app and tool_app_name strictly`() {
        val strict = Json { ignoreUnknownKeys = false }
        val json = """
            {"appid":228980,"name":null,"status":"error","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":0,"size_bytes":null,"needs_force":true,
             "installed_on":[],"tool_app":true,"tool_app_name":"Steamworks Common Redistributables"}
        """.trimIndent()
        val decoded = strict.decodeFromString<GameSummary>(json)
        assertTrue(decoded.tool_app)
        assertEquals("Steamworks Common Redistributables", decoded.tool_app_name)
    }

    @Test
    fun `an older server without the flag decodes as an ordinary app`() {
        val json = """{"appid":228980,"status":"error","depot_count":0}"""
        val decoded = Json { ignoreUnknownKeys = true }.decodeFromString<GameSummary>(json)
        assertFalse(decoded.tool_app)
        assertNull(decoded.tool_app_name)
        assertEquals(StatusKind.ERROR, dispKind(decoded, null))
    }

    // ---------- GameStatus.kt ----------

    @Test
    fun `MUTATION TARGET -- dispKind maps a tool app to NOTINUSE, never ERROR`() {
        assertEquals(StatusKind.NOTINUSE, dispKind(redist(), null))
        assertEquals(StatusKind.NOTINUSE, dispKind(redist(status = "done", sizeBytes = 1000L), null))
        assertEquals(StatusKind.ERROR, dispKind(failedGame(), null))
    }

    @Test
    fun `a live job still wins for a tool app`() {
        val job = JobSummary(id = 1, appid = 228980, type = "prefill", status = "running", created_at = "2026-10-04T08:00:00Z")
        assertEquals(StatusKind.RUNNING, dispKind(redist(), job))
    }

    @Test
    fun `MUTATION TARGET -- statusAction offers no download or retry for a tool app`() {
        assertNull(statusAction(redist(), null, selecting = false))
        assertNull(statusAction(redist(status = "idle"), null, selecting = false))
        assertEquals(StatusAction(StatusActionType.RETRY), statusAction(failedGame(), null, selecting = false))
    }

    @Test
    fun `MUTATION TARGET -- installedBadgeFor never says not cached for a tool app`() {
        assertTrue(installedBadgeFor(redist()) is InstalledBadge.InstalledAndCached)
        assertEquals(InstalledBadge.NoSignal, installedBadgeFor(redist(installed = false)))
        assertTrue(installedBadgeFor(failedGame()) is InstalledBadge.InstalledNotCached)
    }

    @Test
    fun `gameDisplayName uses the vault name, then the tool name, then App id`() {
        assertEquals("Steamworks Common Redistributables", gameDisplayName(redist()))
        assertEquals("Redist (vault)", gameDisplayName(redist(name = "  Redist (vault)  ")))
        assertEquals("App 228980", gameDisplayName(redist(toolAppName = "  ")))
        assertEquals("App 440", gameDisplayName(failedGame().copy(name = null, tool_app_name = "X")))
    }

    // ---------- GameCardModel.kt ----------

    @Test
    fun `MUTATION TARGET -- the card model carries the tool name, NOTINUSE, no action, toolApp`() {
        val model = buildGameCardModel(redist(), null, selected = false, selecting = false)
        assertEquals("Steamworks Common Redistributables", model.name)
        assertEquals(StatusKind.NOTINUSE, model.kind)
        assertNull(model.action)
        assertTrue(model.toolApp)
        assertTrue(model.installedBadge is InstalledBadge.InstalledAndCached)

        val ordinary = buildGameCardModel(failedGame(), null, selected = false, selecting = false)
        assertFalse(ordinary.toolApp)
        assertEquals(StatusKind.ERROR, ordinary.kind)
    }

    // ---------- the status word follows the computed kind (review S1) ----------

    @Test
    fun `MUTATION TARGET -- toolAppStateWordFor names only the NOTINUSE kind`() {
        assertEquals("Steam tool package", toolAppStateWordFor(StatusKind.NOTINUSE))
        for (kind in StatusKind.entries.filter { it != StatusKind.NOTINUSE }) {
            assertNull("$kind must use its own label", toolAppStateWordFor(kind))
        }
    }

    @Test
    fun `MUTATION TARGET -- a tool app with a live job reads Downloading, not Steam tool package`() {
        val running = JobSummary(id = 7, appid = 228980, type = "prefill", status = "running", created_at = "2026-10-04T08:00:00Z")
        val model = buildGameCardModel(redist(), running, selected = false, selecting = false)
        assertEquals(StatusKind.RUNNING, model.kind)
        assertNull(toolAppStateWordFor(model.kind)) // so the card shows the kind's own label ...
        assertEquals(R.string.status_running, model.kind.labelRes) // ... "Downloading"
        assertTrue(model.toolApp) // still muted

        val paused = buildGameCardModel(redist(), running.copy(status = "paused"), selected = false, selecting = false)
        assertEquals(StatusKind.PAUSED, paused.kind)
        assertNull(toolAppStateWordFor(paused.kind))

        val idle = buildGameCardModel(redist(), null, selected = false, selecting = false)
        assertEquals("Steam tool package", toolAppStateWordFor(idle.kind))
    }

    // ---------- LibraryFilters.kt ----------

    @Test
    fun `a tool app is in no Failed and no Not cached filter`() {
        val failed = FILTER_DEFS.first { it.key == "failed" }
        val none = FILTER_DEFS.first { it.key == "none" }
        assertFalse(failed.predicate(redist(), null))
        assertFalse(none.predicate(redist(), null))
        assertTrue(failed.predicate(failedGame(), null))
    }

    // ---------- BulkPlan.kt (word for word with web bulk-plan.js) ----------

    @Test
    fun `MUTATION TARGET -- bulk never targets a picked tool app`() {
        val picked = listOf(redist(), failedGame())
        val classification = classifyBulkSelection(picked, emptyList())
        assertEquals(listOf(228980), classification.toolApps.map { it.appid })
        assertEquals(listOf(440), classification.needsDownload.map { it.appid })
        val plan = buildBulkDownloadPlan(classification, picked.size)
        assertEquals(listOf(440), plan.primaryTargets)
        assertEquals("Download 1 of 2", plan.primaryLabel)
        assertEquals("1 Steam tool package — cached together with the games that use it.", plan.note)
    }

    @Test
    fun `bulk with only a tool app picked has nothing to download`() {
        val plan = buildBulkDownloadPlan(classifyBulkSelection(listOf(redist()), emptyList()), 1)
        assertFalse(plan.primaryEnabled)
        assertEquals("Nothing to download here", plan.primaryLabel)
        assertTrue(plan.primaryTargets.isEmpty())
        assertTrue(plan.secondaryTargets.isEmpty())
    }

    @Test
    fun `bulk never offers a cached tool app for re-download`() {
        val picked = listOf(redist(status = "done", sizeBytes = 1000L), failedGame(sizeBytes = 1000L, status = "done"))
        val plan = buildBulkDownloadPlan(classifyBulkSelection(picked, emptyList()), 2)
        assertEquals(listOf(440), plan.secondaryTargets)
    }
}
