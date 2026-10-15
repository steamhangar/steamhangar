package dev.steamvault.app.ui.traffic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.ui.traffic.logic.DEFAULT_STATS_RANGE
import dev.steamvault.app.ui.traffic.logic.isStatsRange
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP D15d: the ranged poll's state machine (web `createViewPoll` + the
 * views' `state`): old data survives a failed refresh, a 404 means an old
 * server and stops the poll, an answer for a range the user left is
 * dropped, and the "no figures" verdict only changes on an answer.
 */
class TrafficPollTest {

    private data class Answer(val range: String, val figures: Boolean = true)

    @Test
    fun `a failed refresh keeps the previous answer and reports the error`() = runTest {
        var fail = false
        val poll = TrafficPoll(
            initialRange = "7d",
            fetch = { r -> if (fail) throw VaultApiError.Server("GET x failed (500)", 500, "db locked") else Answer(r) },
            hasFigures = { it.figures },
        )
        poll.refreshOnce()
        assertEquals(Answer("7d"), poll.data)
        fail = true
        poll.refreshOnce()
        assertEquals("the old figures stay on screen", Answer("7d"), poll.data)
        assertEquals("db locked", poll.error)
        fail = false
        poll.refreshOnce()
        assertNull(poll.error)
    }

    @Test
    fun `a 404 marks an old server, hides the figures and stops polling`() = runTest {
        var calls = 0
        val poll = TrafficPoll<Answer>(
            initialRange = "24h",
            fetch = { calls++; throw VaultApiError.NotFound("GET /v1/throughput failed (404)", 404, "Not Found") },
            hasFigures = { true },
        )
        poll.pollForever(intervalMs = 10L)
        assertTrue(poll.unsupported)
        assertTrue(poll.noFigures)
        assertNull("a 404 is not shown as an error, the unsupported note says it", poll.error)
        assertEquals("pollForever returns after the 404 instead of asking again", 1, calls)
        poll.refreshOnce()
        assertEquals(1, calls)
    }

    @Test
    fun `no figures follows the answer, and holds while the next answer is pending`() = runTest {
        val gate = CompletableDeferred<Unit>()
        var waitOnGate = false
        val poll = TrafficPoll(
            initialRange = "7d",
            fetch = { r -> if (waitOnGate) gate.await(); Answer(r, figures = false) },
            hasFigures = { it.figures },
        )
        poll.refreshOnce()
        assertTrue(poll.noFigures)
        waitOnGate = true
        poll.selectRange(this, "30d")
        testScheduler.runCurrent()
        assertNull("the old range's answer is dropped at once", poll.data)
        assertTrue("the verdict holds while pending", poll.noFigures)
        gate.complete(Unit)
        testScheduler.advanceUntilIdle()
        assertEquals(Answer("30d", figures = false), poll.data)
    }

    @Test
    fun `an answer for a range the user has since left is dropped`() = runTest {
        val slow = CompletableDeferred<Unit>()
        val poll = TrafficPoll(
            initialRange = "24h",
            fetch = { r -> if (r == "24h") slow.await(); Answer(r) },
            hasFigures = { true },
        )
        val first = launch { poll.refreshOnce() }
        testScheduler.runCurrent()
        poll.selectRange(this, "all")
        testScheduler.advanceUntilIdle()
        assertEquals(Answer("all"), poll.data)
        slow.complete(Unit)
        first.join()
        assertEquals("the late 24 h answer must not replace the All answer", Answer("all"), poll.data)
        assertEquals("all", poll.range)
    }

    @Test
    fun `selectRange ignores the current range and unknown values`() = runTest {
        var calls = 0
        val poll = TrafficPoll(initialRange = "7d", fetch = { r -> calls++; Answer(r) }, hasFigures = { true })
        poll.selectRange(this, "7d")
        poll.selectRange(this, "1y")
        testScheduler.advanceUntilIdle()
        assertEquals(0, calls)
        assertFalse(poll.unsupported)
    }

    @Test
    fun `WP GRAPH-ZOOM -- the statistics poll takes 1 h and 6 h, the default poll does not`() = runTest {
        val clients = TrafficPoll(initialRange = "7d", fetch = { r -> Answer(r) }, hasFigures = { true })
        clients.selectRange(this, "1h")
        testScheduler.advanceUntilIdle()
        assertEquals("the Clients page keeps its four ranges", "7d", clients.range)
        val stats = TrafficPoll(
            initialRange = DEFAULT_STATS_RANGE,
            fetch = { r -> Answer(r) },
            hasFigures = { true },
            isValidRange = ::isStatsRange,
        )
        assertEquals("6h", stats.range)
        stats.selectRange(this, "1h")
        testScheduler.advanceUntilIdle()
        assertEquals("1h", stats.range)
        assertEquals(Answer("1h"), stats.data)
        stats.selectRange(this, "1y")
        assertEquals("1h", stats.range)
    }
}
