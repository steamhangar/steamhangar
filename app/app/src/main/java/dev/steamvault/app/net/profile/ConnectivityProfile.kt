package dev.steamvault.app.net.profile

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * One HTTP connectivity profile for reaching vault-api: where it lives,
 * and whether cleartext HTTP is allowed for it (WP 4b.2 brief).
 *
 * `tsnet` (an embedded userspace Tailscale client reaching vault-api with
 * no OS-level route or manually-entered IP) is explicitly post-v1 per
 * docs/WORKPACKAGES.md's Phase 4b header. This interface is the seam a
 * future `TsnetProfile` implements — no tsnet dependency exists in this
 * module and no placeholder class pretends to implement it yet, per the
 * WP brief.
 */
interface ConnectivityProfile {
    /** e.g. `"http://192.168.1.50:8080"` or `"https://vault.example.org"`. No trailing slash required. */
    val baseUrl: String

    /** Whether an `http://` base URL / request is permitted for this profile. */
    val allowsCleartext: Boolean
}

/**
 * The OS routes this directly — LAN or a VPN/Tailscale interface — so
 * plain HTTP to whatever IP or hostname the user entered is acceptable;
 * there is no public CA-signed certificate story for a private address.
 * First connectivity profile per docs/WORKPACKAGES.md Phase 4b
 * ("System-VPN profile first, tsnet post-v1").
 *
 * @throws IllegalArgumentException if [baseUrl] is not a parseable `http`/`https` URL.
 */
class SystemVpnProfile(override val baseUrl: String) : ConnectivityProfile {
    override val allowsCleartext: Boolean = true

    init {
        val parsed = baseUrl.toHttpUrlOrNull()
            ?: throw IllegalArgumentException("SystemVpnProfile: not a valid http(s) URL: $baseUrl")
        require(parsed.scheme == "http" || parsed.scheme == "https") {
            "SystemVpnProfile: unsupported scheme ${parsed.scheme}:// in $baseUrl"
        }
    }
}

/**
 * A domain reached over the open internet — HTTPS only, no exceptions.
 * Constructing this with an `http://` [baseUrl] fails HERE, at
 * construction, before any `Request` object — let alone any socket —
 * exists for it. [CleartextPolicyInterceptor] is the second, independent
 * enforcement point at the OkHttp layer (see its kdoc for why both exist).
 *
 * @throws CleartextNotAllowedException if [baseUrl] parses but is not `https://`.
 * @throws IllegalArgumentException if [baseUrl] is not a parseable URL at all.
 */
class PublicDomainProfile(override val baseUrl: String) : ConnectivityProfile {
    override val allowsCleartext: Boolean = false

    init {
        val parsed = baseUrl.toHttpUrlOrNull()
            ?: throw IllegalArgumentException("PublicDomainProfile: not a valid URL: $baseUrl")
        if (parsed.scheme != "https") {
            throw CleartextNotAllowedException(
                "PublicDomainProfile requires https:// (got ${parsed.scheme}://) for " +
                    "$baseUrl — cleartext HTTP is refused for public-domain endpoints."
            )
        }
    }
}

/** Thrown by [PublicDomainProfile]'s constructor and by [CleartextPolicyInterceptor]. */
class CleartextNotAllowedException(message: String) : IllegalStateException(message)
