package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind

/**
 * Content packages grouped under their parent game (WP TOOL-APPS-2d,
 * ADR-0022; Kotlin twin of web `lib/content-groups.js`, WP TOOL-APPS-2c).
 *
 * `GET /v1/games` stays a flat list (decision Q2 A): since WP TOOL-APPS-2a a
 * row may carry `parent_appid` (the TOP-LEVEL game, already resolved
 * server-side, one level) and `app_type`, and since TOOL-APPS-2 a top-level
 * row carries `group_size_bytes` (the union of the group's depots, each
 * once). This file groups the list for the Library. Pure: no Compose, no
 * network.
 *
 * Rules (ADR-0022 §2/§3, user decisions Q3 B variant and Q4 A), the same as
 * web:
 * - A row is a package of a group only when its `parent_appid` names ANOTHER
 *   row of the same list that is itself top-level. A package whose parent is
 *   not in the list, a self reference, a parent that is itself a package, a
 *   Steam tool app (`tool_app`) or an owned-only row stays an ordinary card.
 *   A missing `parent_appid` (an older vault-api) means ungrouped, so an old
 *   response renders exactly as before.
 * - The group card is the parent's own row with `update_available` true
 *   when the parent or any package has it and `installed_on` the union by
 *   client id; `status` and every other field stay the parent's, so the
 *   card's status is the parent's status. Its size is the server's
 *   `group_size_bytes` ([displaySizeBytes]).
 * - A package "failed" when it would show as Failed on its own card
 *   ([dispKind] is ERROR: `status: "error"`, no live job, not a tool app).
 *
 * **String-resource rule, exception recorded** (same as `BulkPlan.kt` and
 * `LibraryFilters.kt`): the user-visible phrases below are a word-for-word
 * port of web `content-groups.js`/`package-list.js`/`group-delete.js` and
 * stay Kotlin literals so the two frontends can be diffed clause by clause;
 * `ContentGroupsContractTest` pins them as literals.
 */

/** The parent app id a row names, or null (missing, non-positive, self). */
fun parentAppidOf(game: GameSummary): Int? {
    val p = game.parent_appid ?: return null
    if (p < 1 || p == game.appid) return null
    return p
}

/** An owned-only row ([mergeLibrary]) is never grouped: it carries no server
 * classification, and a Steam-owned DLC is not in GetOwnedGames anyway. */
private fun isOwnedOnly(game: GameSummary): Boolean = !isKnownToVault(game)

private fun canBePackage(game: GameSummary): Boolean = !isOwnedOnly(game) && !isToolApp(game)

private fun canBeParent(game: GameSummary?): Boolean =
    game != null && !isOwnedOnly(game) && !isToolApp(game) && parentAppidOf(game) == null

/**
 * The grouped library.
 * @property rows the input order with packages removed and every parent
 *   replaced by its group row (see the module kdoc).
 * @property packagesByParent parent appid -> its package rows (raw, in input order).
 * @property parentOf package appid -> parent appid.
 */
data class GroupedLibrary(
    val rows: List<GameSummary>,
    val packagesByParent: Map<Int, List<GameSummary>>,
    val parentOf: Map<Int, Int>,
) {
    fun packagesOf(appid: Int): List<GameSummary> = packagesByParent[appid].orEmpty()
}

/** Union of `installed_on` by client id, keeping the newest `reported_at`
 * per client, the parent's clients first. */
fun unionInstalledOn(members: List<GameSummary>): List<InstalledOnEntry> {
    val byClient = LinkedHashMap<String, InstalledOnEntry>()
    for (m in members) {
        for (e in m.installed_on) {
            val prev = byClient[e.client_id]
            if (prev == null || e.reported_at > prev.reported_at) byClient[e.client_id] = e
        }
    }
    return byClient.values.toList()
}

/** Group a flat games list (the merged library rows). */
fun groupGames(games: List<GameSummary>): GroupedLibrary {
    val byAppid = LinkedHashMap<Int, GameSummary>()
    for (g in games) if (g.appid !in byAppid) byAppid[g.appid] = g

    val packagesByParent = LinkedHashMap<Int, MutableList<GameSummary>>()
    val parentOf = LinkedHashMap<Int, Int>()
    for (g in games) {
        val p = parentAppidOf(g) ?: continue
        if (!canBePackage(g) || g.appid in parentOf) continue
        if (!canBeParent(byAppid[p])) continue
        packagesByParent.getOrPut(p) { mutableListOf() }.add(g)
        parentOf[g.appid] = p
    }

    val rows = ArrayList<GameSummary>(games.size)
    for (g in games) {
        if (g.appid in parentOf) continue
        val packages = packagesByParent[g.appid]
        if (packages == null) {
            rows.add(g)
            continue
        }
        val members = listOf(g) + packages
        rows.add(
            g.copy(
                update_available = members.any { it.update_available },
                installed_on = unionInstalledOn(members),
            ),
        )
    }
    return GroupedLibrary(rows, packagesByParent, parentOf)
}

/** The size a card shows: the group's (server) union, else the row's own. */
fun displaySizeBytes(game: GameSummary, packageCount: Int): Long? =
    if (packageCount > 0 && game.group_size_bytes != null) game.group_size_bytes else game.size_bytes

/** Packages that show as Failed (module kdoc). */
fun failedPackages(packages: List<GameSummary>, liveJobsByAppid: Map<Int, JobSummary>): List<GameSummary> =
    packages.filter { dispKind(it, liveJobsByAppid[it.appid]) == StatusKind.ERROR }

/** Whether a group (or a plain row, `packages` empty) has bytes anywhere. */
fun groupHasCacheContent(game: GameSummary, packages: List<GameSummary>): Boolean =
    hasVisibleCacheContent(game) || packages.any { hasVisibleCacheContent(it) }

/**
 * The raw rows a bulk action sees for the picked cards: a group card adds
 * its parent's row and every package (ADR-0022 §4: bulk selection of a group
 * selects parent + all children, each classified as it would be
 * ungrouped). Card order, parent before its packages, no duplicates.
 * @param picked the picked rows (raw rows of the picked card appids).
 */
fun expandSelection(picked: List<GameSummary>, grouped: GroupedLibrary): List<GameSummary> {
    val out = ArrayList<GameSummary>()
    val seen = HashSet<Int>()
    for (row in picked) {
        for (member in listOf(row) + grouped.packagesOf(row.appid)) {
            if (seen.add(member.appid)) out.add(member)
        }
    }
    return out
}

private fun plural(n: Int, one: String, many: String): String = if (n == 1) one else many

/** "+1 content package" / "+N content packages". */
fun packagesLineText(count: Int): String = "+$count content ${plural(count, "package", "packages")}"

/** "1 failed" (the warning after the packages line). */
fun failedPackagesText(count: Int): String = "$count failed"

/** The card's spoken fragment for a group. */
fun packagesAriaText(count: Int, failedCount: Int): String {
    val base = "$count content ${plural(count, "package", "packages")}"
    return if (failedCount > 0) "$base, $failedCount failed" else base
}

/**
 * A package's display name in its parent's list: Steam often spells it
 * "<parent name> - <what>", redundant under the parent's heading. Drops a
 * leading parent name followed by a dash or colon separator; anything else
 * is returned unchanged (trimmed). The full name stays the spoken one.
 */
fun shortPackageName(name: String, parentName: String?): String {
    val full = name.trim()
    val prefix = parentName?.trim().orEmpty()
    if (prefix.isEmpty() || !full.lowercase().startsWith(prefix.lowercase())) return full
    val match = SEPARATOR_RE.matchEntire(full.substring(prefix.length)) ?: return full
    return match.groupValues[1]
}

private val SEPARATOR_RE = Regex("""^\s*[-–—:]\s*(\S.*)$""", RegexOption.DOT_MATCHES_ALL)

/** Display words for `app_type` (web `APP_TYPE_LABEL`). */
val APP_TYPE_LABEL: Map<String, String> = mapOf(
    "game" to "Game",
    "dlc" to "DLC",
    "tool" to "Tool",
    "application" to "Application",
    "config" to "Config",
    "music" to "Soundtrack",
    "video" to "Video",
    "demo" to "Demo",
)

const val PACKAGES_HEADING = "Content packages"
const val PACKAGE_RETRY_LABEL = "Retry download"

/** The full name of a package row: its own name, else "App <id>". */
fun packageFullName(pkg: GameSummary): String =
    pkg.name?.trim()?.takeIf { it.isNotEmpty() } ?: "App ${pkg.appid}"

/** "Total with content packages: 2.5 GB", or null while unknown. */
fun packagesTotalText(groupSizeBytes: Long?): String? =
    formatBytesGB(groupSizeBytes)?.let { "Total with content packages: $it" }

/** The facts line of one package: "App 123 · DLC · 0.3 GB · Installed on pc". */
fun packageFactsText(pkg: GameSummary): String {
    val parts = mutableListOf("App ${pkg.appid}")
    APP_TYPE_LABEL[pkg.app_type]?.let { parts.add(it) }
    formatBytesGB(pkg.size_bytes)?.let { parts.add(it) }
    if (pkg.installed_on.isNotEmpty()) {
        val newest = pkg.installed_on.maxBy { it.reported_at }
        val more = pkg.installed_on.size - 1
        parts.add(if (more > 0) "Installed on ${newest.client_id} +$more" else "Installed on ${newest.client_id}")
    }
    return parts.joinToString(" · ")
}

/** Whether a package row offers "Retry download": it shows as Failed and
 * has no prefill job queued, running or paused (web `offersPackageRetry`). */
fun offersPackageRetry(pkg: GameSummary, liveJob: JobSummary?, trackedJob: JobSummary?): Boolean =
    dispKind(pkg, liveJob) == StatusKind.ERROR && trackedJob == null

/**
 * Title of a job's app in Downloads when the app has no name of its own:
 * "<parent title> · content <appid>" for a grouped package (ADR-0022 §2),
 * else null (the caller's usual fallback applies).
 */
fun contentPackageFallbackTitle(game: GameSummary?, parentTitle: (Int) -> String?): String? {
    if (game == null) return null
    val p = parentAppidOf(game) ?: return null
    val title = parentTitle(p) ?: return null
    return "$title · content ${game.appid}"
}

// ---- "Never played" pill (twin of web WP WEB-FEAT-SUGGEST-2) ---------------

const val NEVER_PLAYED_LABEL = "Never played"

/** The pill's short word where the card is narrow (3-column grid). */
const val NEVER_PLAYED_COMPACT_LABEL = "Unplayed"

/** The pill's full meaning: the spoken label. */
const val NEVER_PLAYED_DESCRIPTION = "Fully cached, never played"

/**
 * Owned-list playtime by appid: only real, non-negative values (the relay
 * sends `playtime_forever` only with the operator's ADR-0010 gate on, which
 * is off by default -- then this map is empty and no pill shows).
 */
fun ownedPlaytimeByAppid(ownedGames: List<dev.steamvault.app.net.model.OwnedGame>?): Map<Int, Int> {
    val out = LinkedHashMap<Int, Int>()
    for (g in ownedGames.orEmpty()) {
        val v = g.playtime_forever ?: continue
        if (v >= 0 && g.appid !in out) out[g.appid] = v
    }
    return out
}

/**
 * True when the card may say "Never played" (web `showsNeverPlayed`): its
 * shown status is Cached, the game has bytes in the cache, it is not a Steam
 * tool app, and the owned list reported a real, explicit zero playtime.
 * An unknown playtime is never read as zero.
 */
fun showsNeverPlayed(game: GameSummary, kind: StatusKind, ownedPlaytimeForever: Int?): Boolean =
    kind == StatusKind.CACHED && !isToolApp(game) && hasVisibleCacheContent(game) && ownedPlaytimeForever == 0
