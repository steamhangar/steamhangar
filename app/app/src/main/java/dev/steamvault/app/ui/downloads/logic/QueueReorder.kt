package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.JobSummary

/**
 * Pure helpers for reordering the Downloads queue (WP D20, Android twin of
 * `web/js/lib/queue-reorder.js`). No Compose, no network:
 * [dev.steamvault.app.ui.downloads.DownloadsController] owns the optimistic
 * state and the request, this file owns the arithmetic.
 *
 * Positions are 0-based and count QUEUED jobs only, exactly like the
 * `position` of `POST /v1/jobs/{id}/move` (api/README.md): 0 = runs next.
 * Android reorders with accessible move-up / move-down buttons on each row
 * instead of web's pointer drag.
 */

/** One step of the move buttons. */
enum class QueueMove { UP, DOWN }

/**
 * [ids] with [id] taken out and re-inserted at [toIndex] (clamped to the
 * valid range). Returns a new list; an unknown [id] returns a copy.
 */
fun reorderIds(ids: List<Int>, id: Int, toIndex: Int): List<Int> {
    val list = ids.toMutableList()
    val from = list.indexOf(id)
    if (from == -1) return list
    list.removeAt(from)
    list.add(toIndex.coerceIn(0, list.size), id)
    return list
}

/**
 * Where [move] sends the row at [index] of [length] rows; `null` when it
 * would not change anything (already first for UP, last for DOWN) -- the
 * button is disabled then.
 */
fun moveTargetIndex(move: QueueMove, index: Int, length: Int): Int? {
    val target = when (move) {
        QueueMove.UP -> index - 1
        QueueMove.DOWN -> index + 1
    }
    return if (target < 0 || target > length - 1 || target == index) null else target
}

/**
 * [jobs] with `queue_position` rewritten for a move that has been decided
 * but not yet confirmed by the server (the optimistic view). Jobs that are
 * not queued are returned unchanged (same objects).
 */
fun withQueuePositions(jobs: List<JobSummary>, orderedIds: List<Int>): List<JobSummary> =
    jobs.map { j ->
        val at = orderedIds.indexOf(j.id)
        if (at == -1 || j.status != "queued") j else j.copy(queue_position = at)
    }

/**
 * Queue draw order (web `byQueueOrder`): the server's `queue_position` when
 * both rows carry it, else the legacy oldest-job-id-first FIFO (an older API
 * sends none, so it renders exactly as before).
 */
val QUEUE_ORDER: Comparator<JobSummary> = Comparator { a, b ->
    val pa = a.queue_position
    val pb = b.queue_position
    if (pa != null && pb != null && pa != pb) pa.compareTo(pb) else a.id.compareTo(b.id)
}
