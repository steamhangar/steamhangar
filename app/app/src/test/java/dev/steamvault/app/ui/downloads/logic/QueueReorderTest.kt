package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.JobMoveOut
import dev.steamvault.app.net.model.JobSummary
import kotlinx.serialization.decodeFromString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

class QueueReorderTest {
    private fun job(id: Int, status: String = "queued", pos: Int? = null) = JobSummary(
        id = id,
        appid = id,
        type = "prefill",
        status = status,
        created_at = "2026-08-01T00:00:00Z",
        queue_position = pos,
    )

    @Test
    fun `reorderIds moves one id and clamps the target`() {
        assertEquals(listOf(2, 1, 3), reorderIds(listOf(1, 2, 3), 2, 0))
        assertEquals(listOf(1, 3, 2), reorderIds(listOf(1, 2, 3), 2, 2))
        assertEquals(listOf(2, 3, 1), reorderIds(listOf(1, 2, 3), 1, 99))
        assertEquals(listOf(3, 1, 2), reorderIds(listOf(1, 2, 3), 3, -5))
        assertEquals(listOf(1, 2, 3), reorderIds(listOf(1, 2, 3), 42, 0))
    }

    @Test
    fun `moveTargetIndex is null at the edges`() {
        assertEquals(0, moveTargetIndex(QueueMove.UP, 1, 3))
        assertEquals(2, moveTargetIndex(QueueMove.DOWN, 1, 3))
        assertNull(moveTargetIndex(QueueMove.UP, 0, 3))
        assertNull(moveTargetIndex(QueueMove.DOWN, 2, 3))
        assertNull(moveTargetIndex(QueueMove.DOWN, 0, 1))
    }

    @Test
    fun `withQueuePositions rewrites queued rows only`() {
        val running = job(9, "running")
        val out = withQueuePositions(listOf(job(1), job(2), running), listOf(2, 1))
        assertEquals(1, out[0].queue_position)
        assertEquals(0, out[1].queue_position)
        assertSame(running, out[2])
    }

    @Test
    fun `QUEUE_ORDER uses queue_position and falls back to id`() {
        val byPos = listOf(job(1, pos = 2), job(2, pos = 0), job(3, pos = 1)).sortedWith(QUEUE_ORDER)
        assertEquals(listOf(2, 3, 1), byPos.map { it.id })
        val legacy = listOf(job(3), job(1), job(2)).sortedWith(QUEUE_ORDER)
        assertEquals(listOf(1, 2, 3), legacy.map { it.id })
        val mixed = listOf(job(3, pos = 0), job(1)).sortedWith(QUEUE_ORDER)
        assertEquals(listOf(3, 1), mixed.map { it.id })
        val tie = listOf(job(5, pos = 1), job(4, pos = 1)).sortedWith(QUEUE_ORDER)
        assertEquals(listOf(4, 5), tie.map { it.id })
    }

    @Test
    fun `QUEUE_ORDER is total and antisymmetric over mixed rows`() {
        // The old comparator was cyclic here: 1 < 2 (id), 3 < 1 (pos), 2 < 3 (id).
        val rows = listOf(job(2), job(1, pos = 5), job(3, pos = 0))
        for (a in rows) for (b in rows) {
            assertEquals(-Integer.signum(QUEUE_ORDER.compare(b, a)), Integer.signum(QUEUE_ORDER.compare(a, b)))
        }
        for (perm in listOf(rows, rows.reversed(), listOf(rows[1], rows[2], rows[0]))) {
            assertEquals(listOf(3, 1, 2), perm.sortedWith(QUEUE_ORDER).map { it.id })
        }
    }

    @Test
    fun `partitionJobs draws the queue in server order`() {
        val p = partitionJobs(listOf(job(1, pos = 1), job(2, pos = 0), job(3, "running")))
        assertEquals(listOf(2, 1), p.queued.map { it.id })
    }

    @Test
    fun `queue_position decodes and an older API without it still decodes`() {
        val with = VaultJson.decodeFromString<JobSummary>(
            """{"id":1,"appid":2,"type":"prefill","status":"queued","created_at":"x","queue_position":3}""",
        )
        assertEquals(3, with.queue_position)
        val without = VaultJson.decodeFromString<JobSummary>(
            """{"id":1,"appid":2,"type":"prefill","status":"queued","created_at":"x"}""",
        )
        assertNull(without.queue_position)
        val out = VaultJson.decodeFromString<JobMoveOut>(
            """{"job_id":1,"status":"queued","outcome":"immediate","detail":"Moved.","queue_position":0}""",
        )
        assertEquals(0, out.queue_position)
    }
}
