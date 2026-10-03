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
) {
    fun toSummary(): JobSummary = JobSummary(
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
    )

    fun toDetail(): JobDetail = JobDetail(
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
    )
}
