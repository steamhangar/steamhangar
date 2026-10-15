package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.ui.library.logic.UNKNOWN_APP_NAME
import dev.steamvault.app.ui.library.logic.MultiPlanDepotRow

/**
 * Depot-sharing WORDING decision for the detail sheet (WP 4b.6 brief:
 * "Depots with computed sharing (holders/sole-holder/orphan wording)") --
 * presentation logic layered on top of `ui/library/logic/MultiPlan.kt`'s
 * [MultiPlanDepotRow] arithmetic, which this sheet reuses AS-IS for a
 * single-id batch (`buildMultiPlan(listOf(appid), ...)`,
 * `DetailController`'s kdoc) rather than re-deriving the co-owner/holder
 * computation a second time -- the ADR-0003 guarantee ("a shared depot with
 * an outside cached holder is never counted as freed",
 * docs/design/vault-app-mockup-NOTES.md round 5 "Shared is a mapping fact;
 * kept is a cache-state fact") therefore applies here for free, proven once
 * in `MultiPlanTest` and pinned again for the single-id shape in
 * `SingleGameDeletePlanTest`.
 *
 * `MultiPlanDepotRow.free` answers "if THIS app is deleted, does this depot
 * lose its last cached holder" -- that is exactly "sole holder" WHEN this
 * app itself currently holds the depot (mockup round 5, "Shared · sole
 * holder": cache-state truth leads over mapping truth), but a DIFFERENT,
 * rarer case when it does not: mockup-notes.md's sample-data note seeds
 * Meridian Rally as "a previously deleted game... mapping rows intact,
 * nothing on disk" specifically so "a shared depot with no cached co-owner"
 * exists even though the VIEWED game is not a holder either. [DepotShareTag]
 * keeps those two apart ([SOLE_HOLDER] vs [ORPHANED]) by taking
 * [thisAppIsHolder] as an explicit input, computed ONCE per detail (by the
 * same [dev.steamvault.app.ui.library.logic.hasProtectedCacheContent]
 * predicate used for every OTHER co-owner in [MultiPlanDepotRow.holderAppids]
 * itself) rather than re-deriving it per depot row.
 *
 * Pure -- no Compose, no network. Covered by `DepotPresentationTest`.
 */
enum class DepotShareTag { EXCLUSIVE, SOLE_HOLDER, PROTECTED, ORPHANED }

/** One row of the co-owner expander (mockup: "tap a shared depot row to see
 * its co-owners... each co-owner with its current status"). [cached] mirrors
 * [MultiPlanDepotRow.holderAppids] membership -- `false` renders as the
 * mockup's verbatim "not cached · mapping kept" wording. */
data class CoOwnerRow(val appid: Int, val name: String, val cached: Boolean)

data class DepotPresentation(
    val depotid: Int,
    val sizeBytes: Long?,
    val tag: DepotShareTag,
    /** Every OTHER app mapping this depot ([MultiPlanDepotRow.others]) --
     * empty for [DepotShareTag.EXCLUSIVE]. */
    val coOwners: List<CoOwnerRow>,
)

/**
 * @param row one [MultiPlanDepotRow] from `buildMultiPlan(listOf(thisAppid), ...)`.
 * @param gamesByAppid for resolving a co-owner's display name -- falls back
 *   to "Unknown app" for one never seen on a `GET /v1/games` poll, same
 *   literal convention `ui/library/logic/GameCardModel.kt` and
 *   `ui/downloads/logic/JobCardModel.kt::nameFor` already use.
 * @param thisAppIsHolder whether the game THIS sheet is showing currently
 *   protects its own shared depots (see module kdoc) -- irrelevant for an
 *   [DepotShareTag.EXCLUSIVE] row, decisive for the SOLE_HOLDER/ORPHANED
 *   split.
 */
fun buildDepotPresentation(
    row: MultiPlanDepotRow,
    gamesByAppid: Map<Int, GameSummary>,
    thisAppIsHolder: Boolean,
): DepotPresentation {
    val tag = when {
        !row.shared -> DepotShareTag.EXCLUSIVE
        row.holderAppids.isNotEmpty() -> DepotShareTag.PROTECTED
        thisAppIsHolder -> DepotShareTag.SOLE_HOLDER
        else -> DepotShareTag.ORPHANED
    }
    val coOwners = row.others.map { appid ->
        CoOwnerRow(
            appid = appid,
            name = gamesByAppid[appid]?.name?.takeIf { it.isNotBlank() } ?: UNKNOWN_APP_NAME,
            cached = appid in row.holderAppids,
        )
    }
    return DepotPresentation(depotid = row.depotid, sizeBytes = row.sizeBytes, tag = tag, coOwners = coOwners)
}
