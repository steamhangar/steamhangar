package dev.steamvault.app.net.steam

import java.security.SecureRandom
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SteamLoginStateTest {

    // ---- SteamLoginState.generate ------------------------------------------

    @Test
    fun `generate produces a non-empty, URL-safe token`() {
        val state = SteamLoginState.generate()
        assertTrue(state.isNotEmpty())
        // Base64 URL-safe alphabet only -- no '+', '/', or '=' padding, so
        // this can be embedded directly in a query string without further
        // percent-encoding surprises.
        assertTrue(state.matches(Regex("^[A-Za-z0-9_-]+$")))
    }

    @Test
    fun `generate uses a real CSPRNG by default -- two calls never collide`() {
        val a = SteamLoginState.generate()
        val b = SteamLoginState.generate()
        assertNotEquals(a, b)
    }

    @Test
    fun `MUTATION PIN -- generate output length reflects the full entropy, not a truncated stub`() {
        // 24 raw bytes, base64url WITHOUT padding: ceil(24 * 8 / 6) = 32 chars.
        // A mutation that shrinks STATE_BYTES (e.g. to 1 byte) changes this
        // length and must be caught here, not just "generate() returns
        // something".
        val state = SteamLoginState.generate()
        assertEquals(32, state.length)
    }

    @Test
    fun `generate is deterministic for a seeded SecureRandom -- confirms the byte source, not a hidden extra randomness input`() {
        val a = SteamLoginState.generate(SecureRandom.getInstance("SHA1PRNG").apply { setSeed(42) })
        val b = SteamLoginState.generate(SecureRandom.getInstance("SHA1PRNG").apply { setSeed(42) })
        assertEquals(a, b)
    }

    // ---- PendingLoginState.consume -- single-use semantics -----------------

    @Test
    fun `consume matches the exact value start() was given`() {
        val pending = PendingLoginState()
        pending.start("s1")
        assertTrue(pending.consume("s1"))
    }

    @Test
    fun `consume fails for a mismatched value`() {
        val pending = PendingLoginState()
        pending.start("s1")
        assertFalse(pending.consume("s2"))
    }

    @Test
    fun `consume fails when nothing was ever started`() {
        val pending = PendingLoginState()
        assertFalse(pending.consume("anything"))
    }

    @Test
    fun `consume fails for a null actual value even when something is pending`() {
        val pending = PendingLoginState()
        pending.start("s1")
        assertFalse(pending.consume(null))
    }

    @Test
    fun `MUTATION PIN -- a consumed (matched) state cannot be replayed`() {
        val pending = PendingLoginState()
        pending.start("s1")
        assertTrue(pending.consume("s1")) // first use: matches and clears
        assertFalse(pending.consume("s1")) // replay of the SAME value: nothing pending anymore
    }

    @Test
    fun `MUTATION PIN -- a mismatched (fake) callback does not kill the pending sign-in`() {
        // WP SEC-FIX-2 (N1): any co-installed app can send a callback with a
        // made-up state; it must fail without clearing the real attempt.
        val pending = PendingLoginState()
        pending.start("s1")
        assertFalse(pending.consume("wrong")) // fake callback: rejected
        assertFalse(pending.consume(null)) // stateless fake: rejected
        assertTrue(pending.consume("s1")) // the genuine callback still completes
        assertFalse(pending.consume("s1")) // and is still single-use afterwards
    }

    @Test
    fun `a fresh start() after a previous one replaces the pending value entirely`() {
        val pending = PendingLoginState()
        pending.start("s1")
        pending.start("s2")
        assertFalse(pending.consume("s1")) // s1 is no longer the pending value
        pending.start("s3")
        assertFalse(pending.consume("s2")) // s2 was replaced by s3 before ever being consumed
    }
}
