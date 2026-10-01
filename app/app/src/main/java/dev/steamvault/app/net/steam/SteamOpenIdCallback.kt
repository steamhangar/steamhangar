package dev.steamvault.app.net.steam

import java.io.UnsupportedEncodingException
import java.net.URLDecoder

/**
 * Parsing (and the pre-verification checks that do NOT need a network call)
 * for the OpenID 2.0 `id_res` callback Valve redirects the Custom Tab back
 * to (WP 4b.3). Everything here is pure Kotlin, deliberately independent of
 * both `android.net.Uri` (unavailable on the JVM test runtime this project
 * verifies against -- app/README.md's "no instrumented tests" note) and
 * `okhttp3.HttpUrl` (which only parses `http`/`https` URLs -- the callback
 * arrives on this app's own `steamvault://` custom scheme, see
 * [SteamOpenIdConfig]). The call site (`MainActivity.onNewIntent`) hands
 * this object the raw `Intent.dataString`, a plain `String`.
 *
 * **What this file does NOT prove on its own: the callback params could
 * still be entirely attacker-forged.** Any app can send SteamHangar an
 * Android `Intent` naming this custom scheme with arbitrary query
 * parameters -- [parse] only checks SHAPE (required fields present, `mode`
 * says `id_res`). The actual trust decision is [SteamOpenIdClient
 * .checkAuthentication] (a network round trip to Valve, POSTing every
 * param this file extracted back with `openid.mode=check_authentication`)
 * plus [signedCoversClaimedId] below -- see that class's kdoc for exactly
 * what check_authentication does and does not prove, and why the
 * signed-fields check is a SEPARATE, additional requirement on top of it
 * (OpenID 2.0 spec: `is_valid:true` only proves the fields NAMED in
 * `openid.signed` were not tampered with -- it says nothing about a field
 * that was never in that list to begin with).
 */
object SteamOpenIdCallback {

    /**
     * Every field this app's login flow requires present and syntactically
     * intact before even attempting `check_authentication` -- a callback
     * missing any of these cannot be verified at all, whatever it claims.
     */
    private val REQUIRED_KEYS = listOf(
        "openid.mode",
        "openid.claimed_id",
        "openid.signed",
        "openid.sig",
        "openid.return_to",
    )

    /**
     * Parses the raw callback URL (e.g. `Intent.dataString`) into the full
     * `openid.*` parameter map, or `null` if it is malformed, incomplete,
     * or not an `id_res` assertion (Valve also uses this same return_to for
     * a "cancel" outcome: `openid.mode=cancel`, which has none of the
     * signature fields and must never be treated as a login attempt).
     *
     * Any query parameter NOT prefixed `openid.` is silently ignored (never
     * forwarded to `check_authentication`, never inspected) -- Valve's
     * assertion is entirely carried in `openid.*` fields, and a stray extra
     * parameter (from a malicious deep-link sender, or a future Valve
     * addition) has no business influencing this parse.
     *
     * A duplicate key keeps its LAST occurrence, matching standard query
     * string semantics (`java.net.URI`/most web frameworks) -- there is
     * exactly one legitimate value for each `openid.*` field in a genuine
     * Valve redirect, so this only matters for a hostile/malformed input,
     * where "some deterministic behaviour" is all that is required.
     */
    fun parse(rawCallbackUrl: String): Map<String, String>? {
        val queryStart = rawCallbackUrl.indexOf('?')
        if (queryStart < 0 || queryStart == rawCallbackUrl.length - 1) return null
        val query = rawCallbackUrl.substring(queryStart + 1)

        val params = mutableMapOf<String, String>()
        for (pair in query.split('&')) {
            if (pair.isEmpty()) continue
            val eq = pair.indexOf('=')
            if (eq < 0) return null // a bare flag with no '=' is not a valid openid.* param
            val name = decode(pair.substring(0, eq)) ?: return null
            // Only the FIRST '=' splits name from value -- a base64 openid.sig
            // legitimately contains further '=' padding characters, which
            // must stay part of the value, not be treated as more pairs.
            val value = decode(pair.substring(eq + 1)) ?: return null
            if (name.startsWith("openid.")) {
                params[name] = value
            }
        }

        if (REQUIRED_KEYS.any { it !in params }) return null
        if (params.getValue("openid.mode") != "id_res") return null
        return params
    }

    private fun decode(raw: String): String? = try {
        URLDecoder.decode(raw, "UTF-8")
    } catch (_: IllegalArgumentException) {
        null // malformed percent-encoding (e.g. a lone "%")
    } catch (_: UnsupportedEncodingException) {
        null // unreachable ("UTF-8" is always supported), kept for defensiveness
    }

    /**
     * Extracts the `state` query parameter from an `openid.return_to` value
     * (WP 4b.7 replay-residual fix -- see [SteamLoginState]/[PendingLoginState]).
     * `return_to` is itself a full URL string (this app's own
     * `steamvault://auth/openid-return?state=...`), so this is a small,
     * independent query-string parse over THAT value, not over the outer
     * callback URL [parse] already handled -- Valve's OpenID assertion
     * echoes `return_to` back verbatim as one of the signed `openid.*`
     * fields, so whatever this app embedded in it at login time survives
     * the round trip unmodified for a genuine, unmodified assertion.
     *
     * Returns `null` if [returnTo] has no query string at all, or no
     * `state` parameter in it -- both treated identically by
     * [PendingLoginState.consume] (a `null` actual can never match a
     * pending state, since a pending state is only ever set to a non-null,
     * freshly generated value).
     */
    fun stateFromReturnTo(returnTo: String): String? {
        val queryStart = returnTo.indexOf('?')
        if (queryStart < 0 || queryStart == returnTo.length - 1) return null
        val query = returnTo.substring(queryStart + 1)
        for (pair in query.split('&')) {
            if (pair.isEmpty()) continue
            val eq = pair.indexOf('=')
            if (eq < 0) continue
            val name = decode(pair.substring(0, eq)) ?: continue
            if (name == "state") return decode(pair.substring(eq + 1))
        }
        return null
    }

    /**
     * OpenID 2.0's actual security requirement on top of a bare
     * `is_valid:true`: `openid.signed` is a comma-separated list naming
     * exactly which fields Valve's signature covers, and a field NOT in
     * that list was never cryptographically checked at all -- an
     * assertion can pass `check_authentication` while `openid.claimed_id`
     * itself was never part of the signed payload (a compliant OpenID
     * provider always signs it, but this app must not simply ASSUME that;
     * a provider bug or a downgraded/malicious response is exactly the
     * scenario this check exists for).
     *
     * **Scope, stated honestly (WP brief: "document what is and isn't
     * checked").** This function checks ONLY that `claimed_id` is a member
     * of the signed set -- the field the persisted SteamID64 comes from.
     * Its sibling [signedCoversReturnTo] covers the SECOND field this app
     * branches on for a security decision since WP 4b.7: `return_to`
     * carries the per-login `state` that [stateFromReturnTo] feeds into
     * [PendingLoginState.consume] (WP APP-FIX-1 S4a -- until then the kdoc
     * here claimed this app "never branches on return_to's value", which
     * stopped being true the day the state check landed). Neither function
     * requires `response_nonce`, `op_endpoint`, or `identity` to be signed:
     * those matter for a fully general OpenID relying party (nonce replay
     * windows, trust-root matching) but this app never reads their values
     * for anything security-relevant.
     */
    fun signedCoversClaimedId(signedFieldList: String): Boolean =
        signedCovers(signedFieldList, "claimed_id")

    /**
     * WP APP-FIX-1 (S4a): `return_to` must be inside `openid.signed` for the
     * same reason `claimed_id` must -- a field outside the signed set was
     * never covered by `check_authentication` at all, and `return_to` is
     * where the per-login `state` this app's replay defence trusts lives
     * (see [signedCoversClaimedId]'s kdoc). OpenID 2.0 (section 10.1) lists
     * `return_to` among the fields an OP MUST sign, so a genuine Valve
     * assertion always passes; this check exists for the downgraded or
     * forged response that omits it.
     */
    fun signedCoversReturnTo(signedFieldList: String): Boolean =
        signedCovers(signedFieldList, "return_to")

    /** Exact-member test over the comma-separated `openid.signed` list --
     * never a substring match (`not_claimed_id` must not count). */
    private fun signedCovers(signedFieldList: String, field: String): Boolean =
        signedFieldList.split(',').any { it.trim() == field }

    /**
     * Extracts and validates the SteamID64 out of `openid.claimed_id`.
     * Valve's claimed_id always has the exact shape
     * `https://steamcommunity.com/openid/id/<steamid64>` -- anything else
     * (wrong host, extra path segments, a non-SteamID64 tail) is rejected
     * rather than best-effort-parsed; this is the one value that ends up
     * persisted and trusted as "who is signed in", so it is held to the
     * same strict-shape standard as [SteamId64.validate] itself.
     */
    fun steamId64From(claimedId: String): String? {
        val prefix = "https://steamcommunity.com/openid/id/"
        if (!claimedId.startsWith(prefix)) return null
        val candidate = claimedId.substring(prefix.length)
        if (candidate.isEmpty() || candidate.contains('/')) return null
        return SteamId64.validate(candidate)
    }
}
