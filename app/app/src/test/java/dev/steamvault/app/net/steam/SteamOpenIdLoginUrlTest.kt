package dev.steamvault.app.net.steam

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Literal expected URL pin (WP brief: "OpenID URL construction (literal
 * expected URL pin)") -- the FULL string is hand-typed here, not built
 * from [SteamOpenIdConfig]'s own constants, so a mutation to any one query
 * parameter's name or value is caught (`docs/LEARNINGS.md` 4a.6r rule).
 *
 * **Measured, not assumed** (`docs/LEARNINGS.md` "Testing discipline":
 * verify empirically rather than trusting a remembered API contract).
 * `okhttp3.HttpUrl.Builder.addQueryParameter` percent-encodes `:` and `/`
 * inside a query VALUE (`%3A`/`%2F`) even though both characters are
 * legal, unencoded, in the query component of RFC 3986 -- OkHttp's query
 * encoder is stricter than the grammar strictly requires. This was
 * confirmed by first running this test with a hand-guessed unencoded
 * literal, reading the actual failure output, and adopting THAT as the
 * pinned literal below, rather than assuming either form.
 */
class SteamOpenIdLoginUrlTest {

    @Test
    fun `builds the exact literal checkid_setup URL for a vault return_to and realm (WP D11)`() {
        val expected = "https://steamcommunity.com/openid/login" +
            "?openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0" +
            "&openid.mode=checkid_setup" +
            "&openid.return_to=http%3A%2F%2F192.168.1.50%3A8080%2Fauth%2Fsteam%2Fapp-return%3Fstate%3Dabc" +
            "&openid.realm=http%3A%2F%2F192.168.1.50%3A8080%2F" +
            "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select" +
            "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"

        val target = SteamOpenIdReturnUrl.forVault("http://192.168.1.50:8080", "abc")!!
        assertEquals(expected, SteamOpenIdLoginUrl.build(target.returnTo, target.realm))
    }

    @Test
    fun `MUTATION PIN -- mode is literally checkid_setup, never id_res or check_authentication`() {
        val url = SteamOpenIdLoginUrl.build("http://h/auth/steam/app-return", "http://h/")
        assertEquals(true, url.contains("openid.mode=checkid_setup"))
        assertEquals(false, url.contains("openid.mode=id_res"))
        assertEquals(false, url.contains("openid.mode=check_authentication"))
    }

    @Test
    fun `a custom return_to and realm are both reflected verbatim (percent-encoded)`() {
        val url = SteamOpenIdLoginUrl.build(returnTo = "https://example.org/cb", realm = "https://example.org/")
        assertEquals(true, url.contains("openid.return_to=https%3A%2F%2Fexample.org%2Fcb"))
        assertEquals(true, url.contains("openid.realm=https%3A%2F%2Fexample.org%2F"))
    }
}
