package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind

/**
 * Bulk-download split semantics (WP 4b.4) — Kotlin port of
 * `web/js/lib/bulk-plan.js`'s classify/plan pair (docs/design/
 * vault-app-mockup-NOTES.md round 5, "Bulk actions never silently
 * re-download a cached game"): multi-select classifies the picked set by
 * REAL cache state and targets only what needs bytes — the button spells
 * out the skip count, re-download is always an explicit secondary, never
 * folded into the primary. Pure — no Compose, no network — the
 * classification and the resulting label/note text are both unit-testable
 * (`BulkPlanTest`, mirroring `web/tests/bulk-plan.test.js`'s named cases).
 *
 * Narrower than the mockup by exactly the same divergence documented in
 * `GameStatus.kt`: no "stale" status, so the mockup's three-way split
 * (free / stale-update / cached) collapses to two buckets —
 * `needsDownload` (not cached OR errored — see [classifyBulkSelection]'s
 * kdoc for why `error` joins `none`) and `current` (cached). `busy`
 * (already has a job in flight) is excluded from both, as in the mockup.
 *
 * [classifyBulkDeleteEligibility] lives here too, next to the download
 * split, rather than inlined in the Library screen (same review-fix shape
 * the web port's WP 4a.3 review applied): the mockup's own delete-
 * eligibility rule is has-cache-content, which for the real API means
 * [hasVisibleCacheContent], NOT "kind != none" — `dispKind(g) != NONE` also
 * matches ERROR, and an errored app with ZERO bytes has no depot mappings
 * left to delete (`DELETE /v1/cache/{appid}` 404s: "appid has no
 * depot_app_map rows").
 *
 * **String-resource rule, exception recorded (WP 4b.4 review fix; see
 * app/README.md's "String resources" conventions section for the general
 * policy).** The button labels/notes built in [buildBulkDownloadPlan]
 * (`"Download %d of %d"`, `"All cached — nothing to download"`,
 * `"%d already cached — not re-downloaded."`, etc.) stay Kotlin string
 * literals here, NOT `strings.xml` resources, on purpose: they are a
 * line-for-line, word-for-word port of `web/js/lib/bulk-plan.js`'s own
 * literal strings (see that file's `buildBulkDownloadPlan`), and the value
 * of that port is exactly its ABILITY TO DIFF against the web source one
 * clause at a time — routing through resource indirection (a `strings.xml`
 * entry plus format-arg plumbing) would obscure that diff and make the next
 * person to touch either side re-derive the correspondence by hand instead
 * of reading it off two side-by-side literals. This is a narrow,
 * deliberate exception: it applies to strings whose entire reason for
 * existing is "the exact wording bulk-plan.js already decided", not to
 * general UI chrome (which does belong in resources — see `LibraryStrings.kt`
 * for the analogous dynamic-toast case and why THAT one could not simply
 * mirror this same exception).
 */

/** Appids with a prefill job that is queued, running or paused right now —
 * shared by both classifiers below (queued jobs count as busy here, unlike
 * [findLiveJob] — dedupe protection needs the queued case too; mockup
 * parity: a job counts as busy if it is queued, running or paused). */
private fun busyAppidsFromJobs(jobs: List<JobSummary>): Set<Int> =
    jobs.filter { it.type == "prefill" && it.status in setOf("queued", "running", "paused") }
        .map { it.appid }
        .toSet()

data class BulkSelectionClassification(
    val busy: List<GameSummary>,
    val needsDownload: List<GameSummary>,
    val current: List<GameSummary>,
    /** WP API-FIX-4: picked Steam tool apps -- never a target (web
     * `toolApps`): vault-api answers 422 for one, which fails the whole
     * bulk request. */
    val toolApps: List<GameSummary> = emptyList(),
)

/** @param games the SELECTED games (already resolved from the picked appid set — callers own that lookup). */
fun classifyBulkSelection(games: List<GameSummary>, jobs: List<JobSummary>): BulkSelectionClassification {
    val busyAppids = busyAppidsFromJobs(jobs)
    val busy = mutableListOf<GameSummary>()
    val rest = mutableListOf<GameSummary>()
    val toolApps = mutableListOf<GameSummary>()
    for (g in games) {
        when {
            isToolApp(g) -> toolApps.add(g)
            g.appid in busyAppids -> busy.add(g)
            else -> rest.add(g)
        }
    }

    val needsDownload = mutableListOf<GameSummary>()
    val current = mutableListOf<GameSummary>()
    for (g in rest) {
        // A live job can't be true here (busyAppids already excludes it), so
        // dispKind's cache-only branch is exactly what we want: NONE and
        // ERROR both mean "not successfully cached" -- an errored app gets
        // the same "needs a (re)download" treatment as a never-downloaded
        // one, matching statusAction's retry decision in GameStatus.kt.
        val kind = dispKind(g, null)
        (if (kind == StatusKind.CACHED) current else needsDownload).add(g)
    }
    return BulkSelectionClassification(busy, needsDownload, current, toolApps)
}

/**
 * Which of the SELECTED games can actually be sent to
 * `DELETE /v1/cache/{appid}` without a guaranteed 404 — has real bytes on
 * the cache right now, AND is not busy (deleting under an in-flight prefill
 * is a 409 anyway). Deliberately [hasVisibleCacheContent], not
 * `dispKind(...) != NONE`: an ERROR status with zero visible bytes has no
 * depot mappings to delete; an ERROR status WITH bytes (a half-deleted or
 * partially-failed run) genuinely has content to clean up and stays
 * eligible.
 * @param games the SELECTED games.
 */
fun classifyBulkDeleteEligibility(games: List<GameSummary>, jobs: List<JobSummary>): List<GameSummary> {
    val busyAppids = busyAppidsFromJobs(jobs)
    return games.filter { it.appid !in busyAppids && hasVisibleCacheContent(it) }
}

private fun plural(n: Int, noun: String): String = "$n $noun" + if (n == 1) "" else "s"

/** The bulk-download bar's three visible outcomes (mockup round 5, narrowed
 * by one branch since there is no "stale" bucket — see module kdoc). */
data class BulkDownloadPlan(
    val primaryEnabled: Boolean,
    val primaryLabel: String,
    val primaryTargets: List<Int>,
    val note: String,
    val secondaryLabel: String?,
    val secondaryTargets: List<Int>,
)

fun buildBulkDownloadPlan(classification: BulkSelectionClassification, totalPicked: Int): BulkDownloadPlan {
    val (busy, needsDownload, current, toolApps) = classification
    // WP API-FIX-4: the web's appended sentence for picked tool apps, word for word.
    val toolAppsNote = when (toolApps.size) {
        0 -> ""
        1 -> "1 Steam tool package — cached together with the games that use it."
        else -> "${toolApps.size} Steam tool packages — cached together with the games that use them."
    }
    fun withToolApps(note: String): String = listOf(note, toolAppsNote).filter { it.isNotEmpty() }.joinToString(" ")

    if (needsDownload.isNotEmpty()) {
        val skipped = totalPicked - needsDownload.size - toolApps.size
        return BulkDownloadPlan(
            primaryEnabled = true,
            primaryLabel = if (needsDownload.size < totalPicked) {
                "Download ${needsDownload.size} of $totalPicked"
            } else {
                "Download ${plural(needsDownload.size, "game")}"
            },
            primaryTargets = needsDownload.map { it.appid },
            note = withToolApps(
                when {
                    skipped > 0 -> "$skipped already cached — not re-downloaded."
                    needsDownload.size > 1 && toolApps.isEmpty() -> "All ${needsDownload.size} are new to the cache."
                    else -> ""
                },
            ),
            secondaryLabel = null,
            secondaryTargets = emptyList(),
        )
    }

    if (current.isNotEmpty()) {
        return BulkDownloadPlan(
            primaryEnabled = false,
            primaryLabel = if (toolApps.isNotEmpty()) "Nothing to download here" else "All cached — nothing to download",
            primaryTargets = emptyList(),
            note = withToolApps(
                if (toolApps.isNotEmpty()) {
                    "${current.size} cached — re-download only if you need to refetch from Steam."
                } else {
                    "Every selected game is current. Re-download only if you need to refetch from Steam."
                },
            ),
            secondaryLabel = "Re-download ${current.size}",
            secondaryTargets = current.map { it.appid },
        )
    }

    if (toolApps.isNotEmpty()) {
        return BulkDownloadPlan(
            primaryEnabled = false,
            primaryLabel = "Nothing to download here",
            primaryTargets = emptyList(),
            note = withToolApps(if (busy.isNotEmpty()) "${busy.size} already downloading." else ""),
            secondaryLabel = null,
            secondaryTargets = emptyList(),
        )
    }

    val allBusy = busy.size == totalPicked && totalPicked > 0
    return BulkDownloadPlan(
        primaryEnabled = false,
        primaryLabel = if (allBusy) "Already downloading" else "Nothing to download",
        primaryTargets = emptyList(),
        note = if (allBusy) "Every selected game already has a job in flight." else "",
        secondaryLabel = null,
        secondaryTargets = emptyList(),
    )
}
