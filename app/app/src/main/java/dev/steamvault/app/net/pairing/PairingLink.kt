package dev.steamvault.app.net.pairing

import dev.steamvault.app.net.steam.SteamOpenIdConfig
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction

/**
 * WP APP-PAIR-1: the pairing deep link the web UI shows as a QR code
 * (package PAIR-1, built in parallel). The user scans it with the phone's
 * normal camera app, which opens this app through the `steamhangar://pair`
 * intent filter in `AndroidManifest.xml`. Contract, fixed and shared with
 * the web package:
 *
 * ```
 * steamhangar://pair?v=1&url=<percent-encoded base URL>&key=<percent-encoded API key>
 * ```
 *
 * The literal format is pinned in `PairingLinkContractTest` (literal
 * strings, never derived from these constants -- docs/LEARNINGS.md,
 * Android section).
 *
 * Note the scheme differs from the Steam OpenID callback's `steamvault://`
 * ([dev.steamvault.app.net.steam.SteamOpenIdConfig]): the two links can
 * never be confused, see [routeIncomingLink].
 */
object PairingLinkContract {
    const val SCHEME = "steamhangar"
    const val HOST = "pair"
    const val PARAM_VERSION = "v"
    const val PARAM_URL = "url"
    const val PARAM_KEY = "key"
    const val SUPPORTED_VERSION = "1"
}

/**
 * A pairing link that passed every check in [PairingLink.parse].
 *
 * Deliberately NOT a data class: a data class would generate `toString`
 * and `componentN` functions that expose [apiKey], and the key must never
 * reach a log, a crash report or a debugger's string preview. [toString]
 * is overridden to redact it.
 *
 * @property baseUrl normalized `scheme://host[:port]` -- exactly what the
 *   confirmation dialog shows and what gets stored, so the user confirms
 *   the stored value, not a raw link.
 * @property displayHost `host[:port]` (IPv6 in brackets, IDN in punycode as
 *   OkHttp normalizes it, which shows a homograph host for what it is).
 * @property usesCleartext `true` for `http://` -- the dialog warns.
 */
class PairingRequest(
    val baseUrl: String,
    val displayHost: String,
    val usesCleartext: Boolean,
    val apiKey: String,
) {
    override fun toString(): String = "PairingRequest(baseUrl=$baseUrl, apiKey=<redacted>)"
}

/** Every way [PairingLink.parse] can refuse a link; each has its own message (`strings.xml`, `pairing_error_*`). */
enum class PairingRejection {
    NOT_A_PAIRING_LINK,
    MALFORMED_LINK,
    DUPLICATE_PARAMETER,
    VERSION_MISSING,
    VERSION_UNSUPPORTED,
    URL_MISSING,
    URL_NOT_HTTP,
    URL_INVALID,
    URL_HAS_USERINFO,
    URL_HAS_FRAGMENT,
    URL_HAS_PATH_OR_QUERY,
    KEY_MISSING,
    KEY_EMPTY,
    KEY_INVALID_CHARACTERS,
}

sealed class PairingParseResult {
    class Valid(val request: PairingRequest) : PairingParseResult() {
        override fun toString(): String = "Valid($request)"
    }

    /**
     * @property detail only ever a SAFE, display-ready fragment: the
     *   sanitized version string for [PairingRejection.VERSION_UNSUPPORTED],
     *   the parameter name for [PairingRejection.DUPLICATE_PARAMETER].
     *   Never the key, never attacker-chosen free text of unbounded length.
     */
    data class Invalid(val rejection: PairingRejection, val detail: String? = null) : PairingParseResult()
}

/**
 * Pure parser for the pairing link -- no Android dependencies (OkHttp's
 * [HttpUrl] is a plain JVM library), so the whole accept/reject matrix is
 * JVM-unit-testable (`PairingLinkTest`).
 *
 * Checks, in order (the first failure wins, each with its own
 * [PairingRejection]):
 *  1. scheme `steamhangar`, host `pair`, no fragment on the link itself;
 *  2. the query: `v`, `url`, `key` each at most once (a duplicate is
 *     ambiguous -- which one would win -- so it is refused, not resolved);
 *     unknown parameters are ignored, so a later web version may add some;
 *  3. `v` present and exactly `1` -- checked BEFORE the other fields, since
 *     another version may encode them differently ("update the app");
 *  4. `url`: http/https only, `//` authority form, no backslash, no
 *     whitespace or control character, no userinfo, no fragment, no path
 *     beyond `/`, no query (vault-api is addressed at its root:
 *     `VaultApiClient.resolve` replaces the path, so a path in the link
 *     would be silently dropped -- refusing it is more honest);
 *  5. `key`: present, non-empty after trimming, printable ASCII only (the
 *     key travels as the `X-Api-Key` header; OkHttp throws on anything
 *     else). The onboarding check (`OnboardingController.verifyConnection`)
 *     applies its own trim + non-empty rule again on the same value.
 *
 * Decoding: `%XX` with ASCII hex digits only (never `Character.digit`, which
 * also accepts non-ASCII digits -- docs/LEARNINGS.md, Android section, same
 * class as `toLongOrNull`), strict UTF-8, and `+` read as a space -- the
 * form-encoding rule, so a link built with either `encodeURIComponent`
 * (never emits a raw `+`) or `URLSearchParams` (space as `+`, `+` as `%2B`)
 * decodes the same. A literal `+` in a key must therefore be sent as `%2B`,
 * which both web encoders do.
 *
 * Nothing in this file logs anything.
 */
object PairingLink {

    private const val MAX_DETAIL_LENGTH = 16

    fun parse(rawLink: String): PairingParseResult {
        val link = rawLink.trim()

        // ---- 1. scheme + host -------------------------------------------
        val colon = link.indexOf(':')
        if (colon <= 0 || !link.substring(0, colon).equals(PairingLinkContract.SCHEME, ignoreCase = true)) {
            return invalid(PairingRejection.NOT_A_PAIRING_LINK)
        }
        val afterScheme = link.substring(colon + 1)
        if (!afterScheme.startsWith("//")) return invalid(PairingRejection.NOT_A_PAIRING_LINK)
        val hierarchical = afterScheme.substring(2)
        val queryStart = hierarchical.indexOf('?')
        val authorityAndPath = if (queryStart >= 0) hierarchical.substring(0, queryStart) else hierarchical
        val hostPart = authorityAndPath.substringBefore('#').removeSuffix("/")
        if (!hostPart.equals(PairingLinkContract.HOST, ignoreCase = true)) {
            return invalid(PairingRejection.NOT_A_PAIRING_LINK)
        }
        // A raw '#' anywhere means the link was not built per contract (the
        // base URL and key are percent-encoded, so neither can contain one).
        if (hierarchical.contains('#')) return invalid(PairingRejection.MALFORMED_LINK)
        val query = if (queryStart >= 0) hierarchical.substring(queryStart + 1) else ""

        // ---- 2. parameters ------------------------------------------------
        val known = setOf(PairingLinkContract.PARAM_VERSION, PairingLinkContract.PARAM_URL, PairingLinkContract.PARAM_KEY)
        val params = HashMap<String, String>()
        for (segment in query.split('&')) {
            if (segment.isEmpty()) continue
            val eq = segment.indexOf('=')
            val rawName = if (eq >= 0) segment.substring(0, eq) else segment
            val rawValue = if (eq >= 0) segment.substring(eq + 1) else ""
            // An undecodable NAME cannot be one of ours: ignore it like any
            // other unknown parameter.
            val name = formDecode(rawName) ?: continue
            if (name !in known) continue
            val value = formDecode(rawValue) ?: return invalid(PairingRejection.MALFORMED_LINK)
            if (params.containsKey(name)) return invalid(PairingRejection.DUPLICATE_PARAMETER, name)
            params[name] = value
        }

        // ---- 3. version -----------------------------------------------------
        val version = params[PairingLinkContract.PARAM_VERSION]?.trim()
        if (version.isNullOrEmpty()) return invalid(PairingRejection.VERSION_MISSING)
        if (version != PairingLinkContract.SUPPORTED_VERSION) {
            return invalid(PairingRejection.VERSION_UNSUPPORTED, sanitizeDetail(version))
        }

        // ---- 4. base URL ----------------------------------------------------
        val urlResult = parseBaseUrl(params[PairingLinkContract.PARAM_URL])
        if (urlResult is UrlCheck.Rejected) return invalid(urlResult.rejection)
        val url = (urlResult as UrlCheck.Accepted).url

        // ---- 5. key ---------------------------------------------------------
        val rawKey = params[PairingLinkContract.PARAM_KEY] ?: return invalid(PairingRejection.KEY_MISSING)
        val key = rawKey.trim()
        if (key.isEmpty()) return invalid(PairingRejection.KEY_EMPTY)
        if (key.any { it < ' ' || it > '~' }) return invalid(PairingRejection.KEY_INVALID_CHARACTERS)

        val host = if (url.host.contains(':')) "[${url.host}]" else url.host
        val displayHost = if (url.port != HttpUrl.defaultPort(url.scheme)) "$host:${url.port}" else host
        return PairingParseResult.Valid(
            PairingRequest(
                baseUrl = "${url.scheme}://$displayHost",
                displayHost = displayHost,
                usesCleartext = url.scheme == "http",
                apiKey = key,
            ),
        )
    }

    private sealed class UrlCheck {
        class Accepted(val url: HttpUrl) : UrlCheck()
        class Rejected(val rejection: PairingRejection) : UrlCheck()
    }

    private fun parseBaseUrl(raw: String?): UrlCheck {
        val text = raw?.trim()
        if (text.isNullOrEmpty()) return UrlCheck.Rejected(PairingRejection.URL_MISSING)

        val schemeEnd = text.indexOf(':')
        val scheme = if (schemeEnd > 0) text.substring(0, schemeEnd).lowercase() else null
        if (scheme != "http" && scheme != "https") return UrlCheck.Rejected(PairingRejection.URL_NOT_HTTP)

        // OkHttp's parser is lenient where a strict reader is not: it accepts
        // `https:host` without slashes and reads '\' as '/', which is how
        // "https://evil.example\@good.example" gets two different hosts in
        // two different parsers. Refuse those shapes before OkHttp sees them.
        if (!text.startsWith("//", startIndex = schemeEnd + 1)) return UrlCheck.Rejected(PairingRejection.URL_INVALID)
        if (text.any { it == '\\' || it.isWhitespace() || it.isISOControl() }) {
            return UrlCheck.Rejected(PairingRejection.URL_INVALID)
        }
        if (text.contains('#')) return UrlCheck.Rejected(PairingRejection.URL_HAS_FRAGMENT)
        // OkHttp also skips any number of extra slashes ("https:///host"
        // parses with host "host"); an empty authority is refused here.
        val authority = text.substring(schemeEnd + 3).takeWhile { it != '/' && it != '?' }
        if (authority.isEmpty()) return UrlCheck.Rejected(PairingRejection.URL_INVALID)
        if (authority.contains('@')) return UrlCheck.Rejected(PairingRejection.URL_HAS_USERINFO)

        val url = text.toHttpUrlOrNull() ?: return UrlCheck.Rejected(PairingRejection.URL_INVALID)
        if (url.host.isEmpty()) return UrlCheck.Rejected(PairingRejection.URL_INVALID)
        if (url.username.isNotEmpty() || url.password.isNotEmpty()) {
            return UrlCheck.Rejected(PairingRejection.URL_HAS_USERINFO)
        }
        if (url.encodedPath != "/" || url.encodedQuery != null) {
            return UrlCheck.Rejected(PairingRejection.URL_HAS_PATH_OR_QUERY)
        }
        return UrlCheck.Accepted(url)
    }

    private fun invalid(rejection: PairingRejection, detail: String? = null) =
        PairingParseResult.Invalid(rejection, detail)

    /** Keeps an attacker-chosen version string display-safe: ASCII letters, digits, `.`, `-`, `_`, at most 16 chars. */
    private fun sanitizeDetail(value: String): String =
        value.filter { it in 'a'..'z' || it in 'A'..'Z' || it in '0'..'9' || it == '.' || it == '-' || it == '_' }
            .take(MAX_DETAIL_LENGTH)
            .ifEmpty { "?" }

    /**
     * application/x-www-form-urlencoded decoding with strict rules: `+` is a
     * space, `%XX` needs two ASCII hex digits, the byte sequence must be
     * valid UTF-8. Returns `null` on any violation instead of guessing.
     */
    internal fun formDecode(raw: String): String? {
        val bytes = ByteArrayOutputStream()
        var i = 0
        while (i < raw.length) {
            val c = raw[i]
            when {
                c == '+' -> {
                    bytes.write(' '.code)
                    i++
                }
                c == '%' -> {
                    if (i + 2 >= raw.length) return null
                    val hi = asciiHex(raw[i + 1])
                    val lo = asciiHex(raw[i + 2])
                    if (hi < 0 || lo < 0) return null
                    bytes.write(hi * 16 + lo)
                    i += 3
                }
                else -> {
                    val codePoint = raw.codePointAt(i)
                    val chars = Character.toChars(codePoint)
                    if (chars.size == 1 && Character.isSurrogate(chars[0])) return null
                    bytes.write(String(chars).toByteArray(Charsets.UTF_8))
                    i += chars.size
                }
            }
        }
        return try {
            Charsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes.toByteArray()))
                .toString()
        } catch (_: CharacterCodingException) {
            null
        }
    }

    private fun asciiHex(c: Char): Int = when (c) {
        in '0'..'9' -> c - '0'
        in 'a'..'f' -> c - 'a' + 10
        in 'A'..'F' -> c - 'A' + 10
        else -> -1
    }
}

/** Which of MainActivity's deep-link entry points an incoming `Intent` data string belongs to. */
enum class IncomingLinkRoute { STEAM_OPENID_CALLBACK, PAIRING, NONE }

private val PAIRING_LINK_PREFIX = Regex(
    "^${PairingLinkContract.SCHEME}://${PairingLinkContract.HOST}/?([?#].*)?$",
    setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
)

/**
 * Routes an incoming `Intent.dataString` (WP APP-PAIR-1). The two custom
 * schemes differ (`steamvault://auth/openid-return` vs.
 * `steamhangar://pair`), so the routes are disjoint by construction: the
 * OpenID check is the exact prefix test `MainActivity.handleIntent` always
 * used, and a pairing link needs the `steamhangar` scheme AND the `pair`
 * host (any case -- RFC 3986 schemes and hosts are case-insensitive; the
 * parser then refuses whatever is malformed with a message). Anything
 * else is [IncomingLinkRoute.NONE] and ignored, as before this WP.
 */
fun routeIncomingLink(dataString: String?): IncomingLinkRoute = when {
    dataString == null -> IncomingLinkRoute.NONE
    dataString.startsWith(SteamOpenIdConfig.RETURN_TO) -> IncomingLinkRoute.STEAM_OPENID_CALLBACK
    PAIRING_LINK_PREFIX.matches(dataString) -> IncomingLinkRoute.PAIRING
    else -> IncomingLinkRoute.NONE
}
