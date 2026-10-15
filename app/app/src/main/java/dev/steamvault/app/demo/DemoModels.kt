package dev.steamvault.app.demo

import dev.steamvault.app.net.model.DepotEntry
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary

/** One depot row in a [DemoGame]'s cache — mirrors [DepotEntry] field for field. */
internal data class DemoDepot(val depotid: Int, val shared: Boolean, val sizeBytes: Long)

/**
 * WP CACHE-COMPLETE-1c: a stored completeness measurement that is NOT
 * "every byte there" (web demo `{expected, present, missing}`): bytes of the
 * current build, how many of them are on disk, and how many chunk files are
 * missing (`0` with [presentBytes] < [expectedBytes] = short only of
 * wrong-size chunks). [DemoState] applies the real null rules on top.
 */
internal data class DemoCompleteness(val expectedBytes: Long, val presentBytes: Long, val missingChunks: Int) {
    /** Short only of wrong-size chunks: a forced run cannot change it. */
    val damagedOnly: Boolean get() = missingChunks == 0 && presentBytes < expectedBytes
}

/**
 * Mutable demo-mode game state. [toSummary]/[toDetail] build the SAME real
 * response models [dev.steamvault.app.repo.GamesRepository] callers already
 * expect ([GameSummary]/[GameDetail]) — see `DemoState.kt`'s class kdoc for
 * why that is the shape-match guarantee, not a separate claim to verify.
 */
internal class DemoGame(
    val appid: Int,
    /** `null` for a row the demo's `POST /v1/prefill` inserted for an app
     * the vault never knew (WP APP-FIX-2): the real API inserts such a row
     * with `name = NULL` (api/vault_api/jobs.py), and only a depot mapping
     * names it later. The UI supplies the title from the owned list. */
    val name: String?,
    var status: String,
    var needsForce: Boolean,
    val depots: MutableList<DemoDepot>,
    var lastPrefillAt: String?,
    var lastManifestCheck: String?,
    var gcReclaimableBytes: Long,
    var gcHeldBackBytes: Long,
    /** WP AG-3: mirrors `GameSummary.installed_on`/`GameDetail.installed_on`
     * (WP AG-1) -- defaulted to empty so every EXISTING [seedGames] call
     * that does not care about this field keeps compiling unchanged, per
     * that field's own real-API default. See `DemoFixtures.kt::seedGames`
     * for the three states this fixture set deliberately exercises. */
    var installedOn: List<InstalledOnEntry> = emptyList(),
    /** WP UPD-3: the oracle's "newer build exists" verdict. Like the real
     * API (WP UPD-1) it only reaches the wire for a game with cache bytes,
     * and a finished prefill clears it ([DemoState]). */
    var updateAvailable: Boolean = false,
    /** WP TOOL-APPS-2d: the game this app is a content package of (the
     * server's resolved `parent_appid`, ADR-0022), null for a top-level
     * game. */
    val parentAppid: Int? = null,
    /** WP TOOL-APPS-2d: the server's `app_type` vocabulary, null = unknown. */
    val appType: String? = null,
    /** WP CACHE-COMPLETE-1c: the stored figure when it is not "complete";
     * `null` (the default) = measured complete -- every byte of the game's
     * depots is the current build. */
    var completeness: DemoCompleteness? = null,
    /** WP CACHE-COMPLETE-1c: the figure is not known (e.g. an announced
     * build whose manifest nobody fetched yet) -- the API's null case. */
    var completenessUnknown: Boolean = false,
    /** WP CACHE-COMPLETE-1c: when the figure was measured; `null` = the
     * seeded update check's time ([DemoState]). A finished demo prefill
     * re-stamps it. */
    var completenessCheckedAt: String? = null,
) {
    private fun totalSizeBytes(): Long? = depots.sumOf { it.sizeBytes }.takeIf { depots.isNotEmpty() }

    fun toSummary(): GameSummary = GameSummary(
        appid = appid,
        name = name,
        status = status,
        last_prefill_at = lastPrefillAt,
        last_manifest_check = lastManifestCheck,
        depot_count = depots.size,
        size_bytes = totalSizeBytes(),
        needs_force = needsForce,
        installed_on = installedOn,
        update_available = updateAvailable && depots.isNotEmpty(),
        parent_appid = parentAppid,
        app_type = appType,
    )

    fun toDetail(): GameDetail = GameDetail(
        appid = appid,
        name = name,
        status = status,
        last_prefill_at = lastPrefillAt,
        last_manifest_check = lastManifestCheck,
        depots = depots.map { DepotEntry(depotid = it.depotid, shared = it.shared, size_bytes = it.sizeBytes) },
        size_bytes = totalSizeBytes(),
        needs_force = needsForce,
        installed_on = installedOn,
        parent_appid = parentAppid,
        app_type = appType,
    )
}

/** Mutable demo-mode job state — mirrors [JobSummary]/[JobDetail] field for
 * field; [ticksLeft]/[gcExecute] are demo-only bookkeeping, never
 * serialized into either real response shape (same "never on the wire"
 * posture `web/js/demo-data.js`'s `_demoTicksLeft` documents for its own
 * job objects). */
internal class DemoJob(
    val id: Int,
    val appid: Int,
    val type: String,
    var status: String,
    val createdAt: String,
    var startedAt: String?,
    var finishedAt: String? = null,
    var updated: Int? = null,
    var upToDate: Int? = null,
    var summaryParseOk: Boolean? = null,
    var gcExecute: Boolean? = null,
    var pausedAt: String? = null,
    var logExcerpt: String? = null,
    var ticksLeft: Int,
    /** Demo-only: set on a job the user moved (D20); see DemoState.queuedInOrder. */
    var queueOrder: Int? = null,
) {
    fun toSummary(queuePosition: Int? = null): JobSummary = JobSummary(
        id = id,
        appid = appid,
        type = type,
        status = status,
        created_at = createdAt,
        started_at = startedAt,
        finished_at = finishedAt,
        updated = updated,
        up_to_date = upToDate,
        summary_parse_ok = summaryParseOk,
        gc_execute = gcExecute,
        paused_at = pausedAt,
        stop_request = null,
        queue_position = queuePosition,
    )

    fun toDetail(queuePosition: Int? = null): JobDetail = JobDetail(
        id = id,
        appid = appid,
        type = type,
        status = status,
        created_at = createdAt,
        started_at = startedAt,
        finished_at = finishedAt,
        updated = updated,
        up_to_date = upToDate,
        summary_parse_ok = summaryParseOk,
        gc_execute = gcExecute,
        paused_at = pausedAt,
        stop_request = null,
        log_excerpt = logExcerpt,
        queue_position = queuePosition,
    )
}
