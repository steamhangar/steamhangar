package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.BlockingJobOut
import dev.steamvault.app.net.model.DeletePlanOut
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.jsonObject

/**
 * Grouped delete: a game together with its content packages (WP
 * TOOL-APPS-2d; ADR-0022 §4, api/README.md "Grouped delete"; Kotlin twin of
 * web `lib/group-delete.js`, WP TOOL-APPS-2c).
 *
 * The two-step server contract, so the user is always shown every app a
 * grouped delete removes:
 *   1. `GET /v1/cache/{appid}/delete-plan?with_packages=true` -- the members
 *      (parent + the packages the SERVER chose), the depots deleted with
 *      their bytes, the depots kept because an app outside the set still
 *      uses them, the children kept because they are not content packages,
 *      and in-flight jobs (`blocked_by`) that would make the delete a 409.
 *   2. `DELETE /v1/cache/{appid}?with_packages=true&members=<the plan's
 *      member ids>` -- 409 with `{server_members, confirmed_members}` when
 *      the server's set changed since the preview: nothing was deleted, the
 *      plan is fetched again and confirmed again.
 *
 * Pure: wording and decisions only (literal-string exception as in
 * `ContentGroups.kt`).
 */

/** The app ids to confirm: exactly the plan's members, parent included. */
fun planMemberIds(plan: DeletePlanOut): List<Int> = plan.members.map { it.appid }

/** True when an in-flight job of a member makes the delete a 409. */
fun isPlanBlocked(plan: DeletePlanOut): Boolean = plan.blocked_by.isNotEmpty()

private val lenientJson = Json { ignoreUnknownKeys = true }

/**
 * True for the 409 "members differ" answer (fetch the plan again). The
 * client keeps a non-string `detail` as its JSON text
 * ([dev.steamvault.app.net.VaultApiClient]'s `extractDetail`), so the
 * object is parsed back here; any other 409 (an in-flight job, a string
 * detail) is not a mismatch.
 */
fun isMembersMismatch(error: VaultApiError): Boolean {
    if (error.status != 409) return false
    val detail = error.detail ?: return false
    return try {
        lenientJson.parseToJsonElement(detail).jsonObject["server_members"] is JsonArray
    } catch (_: Exception) {
        false
    }
}

const val MEMBERS_CHANGED_NOTICE =
    "The list of content packages changed since this preview. Nothing was deleted — check the list below and confirm again."
const val BLOCKED_HEADING = "Not possible right now"

private fun count(n: Int, one: String, many: String): String = "$n ${if (n == 1) one else many}"

/** "Starfall Legion and its 2 content packages" / "Starfall Legion". */
fun groupSubject(parentName: String, packageCount: Int): String =
    if (packageCount > 0) {
        "$parentName and its ${count(packageCount, "content package", "content packages")}"
    } else {
        parentName
    }

private val KEPT_CHILD_REASON = mapOf(
    "not_a_package_type" to "not a content package",
    "type_unknown" to "type unknown",
    "type_only_inferred" to "type not confirmed by Steam",
)

/** Why a child of the game stays: the server's reason as a phrase. */
fun keptChildReason(reason: String): String = KEPT_CHILD_REASON[reason] ?: "not a content package"

/** "Download running" / "Cleanup queued" for a `blocked_by` entry. */
fun blockingJobText(job: BlockingJobOut): String {
    val what = if (job.type == "gc") "Cleanup" else "Download"
    return "$what ${job.status}"
}

/** One app in the plan, display-ready. */
data class GroupPlanMember(val appid: Int, val role: String, val name: String)

/** One kept depot, display-ready. */
data class GroupPlanKeptDepot(val depotid: Int, val usedBy: List<String>)

/** One kept child, display-ready. */
data class GroupPlanKeptChild(val appid: Int, val name: String, val reason: String)

/** One blocking job, display-ready. */
data class GroupPlanBlocked(val appid: Int, val name: String, val text: String)

/** The plan in display terms (web `describeGroupPlan`). */
data class GroupPlanDescription(
    val subject: String,
    val parent: GroupPlanMember,
    val packages: List<GroupPlanMember>,
    val freedBytes: Long,
    val keptDepots: List<GroupPlanKeptDepot>,
    val keptChildren: List<GroupPlanKeptChild>,
    val blocked: List<GroupPlanBlocked>,
)

/**
 * @param nameFor the display name for an app id, given the server's own
 *   name for it (may be null).
 */
fun describeGroupPlan(plan: DeletePlanOut, nameFor: (Int, String?) -> String): GroupPlanDescription {
    val members = plan.members.map { GroupPlanMember(it.appid, it.role, nameFor(it.appid, it.name)) }
    val parent = members.firstOrNull { it.role == "parent" } ?: GroupPlanMember(plan.appid, "parent", nameFor(plan.appid, null))
    val packages = members.filter { it.role != "parent" }
    return GroupPlanDescription(
        subject = groupSubject(parent.name, packages.size),
        parent = parent,
        packages = packages,
        freedBytes = plan.total_bytes,
        keptDepots = plan.skipped_shared.map { s -> GroupPlanKeptDepot(s.depotid, s.shared_with.map { nameFor(it, null) }) },
        keptChildren = plan.kept_children.map { GroupPlanKeptChild(it.appid, nameFor(it.appid, it.name), keptChildReason(it.reason)) },
        blocked = plan.blocked_by.map { GroupPlanBlocked(it.appid, nameFor(it.appid, null), blockingJobText(it)) },
    )
}

/** "Deleting X and its N content packages frees about Y." */
fun groupDeleteIntro(subject: String, freedBytes: Long): String =
    "Deleting $subject frees about ${formatBytesGB(freedBytes) ?: "0 GB"}."

/** "<names> still use(s) it" for a kept depot. */
fun keptDepotWhy(usedBy: List<String>): String =
    if (usedBy.isEmpty()) "another app still uses it" else "${usedBy.joinToString(", ")} still ${if (usedBy.size > 1) "use" else "uses"} it"

/** "Not deleted: X (reason), Y (reason)." */
fun keptChildrenText(children: List<GroupPlanKeptChild>): String =
    "Not deleted: ${children.joinToString(", ") { "${it.name} (${it.reason})" }}."

/** The blocked warning line. */
fun blockedText(blocked: List<GroupPlanBlocked>): String =
    "$BLOCKED_HEADING: ${blocked.joinToString("; ") { "${it.name} — ${it.text}" }}. Wait for it to finish or cancel it in Downloads."
