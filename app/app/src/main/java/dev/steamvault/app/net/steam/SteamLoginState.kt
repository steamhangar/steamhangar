package dev.steamvault.app.net.steam

import java.security.SecureRandom
import java.util.Base64

/**
 * Per-login random `state` parameter (WP 4b.7) -- closes the WP 4b.3
 * "Known residual: no request<->callback binding (replay)" documented in
 * `app/README.md` and `docs/LEARNINGS.md`'s "Android (Phase 4b)" section.
 *
 * The residual: nothing tied a specific `checkid_setup` request to the
 * callback that later arrived at `MainActivity.onNewIntent` -- a malicious
 * app already installed on the device could capture a GENUINE, Valve-signed
 * OpenID assertion for the ATTACKER's own Steam account (by triggering its
 * own sign-in against this app's exact, non-per-attempt-unique `return_to`)
 * and replay it into SteamHangar's intent-filter; `check_authentication`
 * would legitimately return `is_valid:true` since it IS an unmodified Valve
 * assertion, just not the one THIS app's button press initiated.
 *
 * The standard OpenID/OAuth fix: generate a fresh, unguessable `state` value
 * per login attempt, remember it, embed it in `openid.return_to`, and refuse
 * any callback whose echoed-back `state` does not match the one THIS
 * attempt started -- see [PendingLoginState] for the single-use consumption
 * half and `SteamOpenIdCallback.stateFromReturnTo` for extracting the value
 * out of a callback's `openid.return_to`.
 */
object SteamLoginState {
    /** 192 bits of CSPRNG entropy -- comfortably unguessable, short enough
     * to stay a normal-sized query parameter once base64url-encoded. */
    private const val STATE_BYTES = 24

    /**
     * Generates a fresh, URL-safe, CSPRNG-backed state token. [random]
     * defaults to a new [SecureRandom] per call (cheap; `SecureRandom` does
     * not need to be reused across calls to stay cryptographically sound)
     * but is overridable so tests can inject a seeded instance without
     * making the PRODUCTION path any less random.
     */
    fun generate(random: SecureRandom = SecureRandom()): String {
        val bytes = ByteArray(STATE_BYTES)
        random.nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }
}

/**
 * Single-use holder for the CURRENT login attempt's expected `state` value.
 * Not thread-safe by design -- a login attempt is always driven from the
 * main thread (a Compose click handler starts it, `MainActivity.onNewIntent`
 * completes it), the same single-flow assumption the rest of the WP 4b.3
 * OpenID plumbing already makes.
 *
 * **Single-use is the load-bearing property, not just "compares a string".**
 * [consume] clears the pending value on a MATCH -- a second call with the
 * same value (a replay of the exact same callback URL) finds nothing pending
 * and fails closed. This is what makes a captured, genuine callback
 * un-replayable a second time.
 *
 * A MISMATCH leaves the pending value intact (WP SEC-FIX-2, N1): any app on
 * the device can fire a `vaultapp://` callback with a made-up state, and if
 * a mismatch cleared the pending value, such a fake callback would kill the
 * user's real sign-in in flight. Keeping it costs nothing: the state is 24
 * CSPRNG bytes, so repeated guessing is not a practical attack, and the next
 * [start] replaces the value anyway.
 */
class PendingLoginState {
    private var expected: String? = null

    /** Called once per login attempt, right before building the `checkid_setup` URL. */
    fun start(state: String) {
        expected = state
    }

    /**
     * Reports whether [actual] matches the pending state and, only if it
     * does, consumes (clears) it. `false` when nothing is pending (no login
     * was started, or a previous attempt already consumed it), when [actual]
     * is `null` (the callback's `return_to` carried no `state` at all -- an
     * older caller, a forged deep link, or a provider that dropped the query
     * string), or when the two values differ. On `false` the pending value
     * is left unchanged.
     */
    fun consume(actual: String?): Boolean {
        val current = expected
        val matches = current != null && actual != null && current == actual
        if (matches) {
            expected = null
        }
        return matches
    }
}
