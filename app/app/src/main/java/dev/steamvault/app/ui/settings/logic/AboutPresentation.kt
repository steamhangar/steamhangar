package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.AboutComponentOut

/**
 * Settings → About: presentation of `GET /v1/about` (WP VER-2; Android
 * parity in WP APP-FEAT-2). The response is six components in a fixed
 * order with four status words; this file turns one entry into what a row
 * shows and decides nothing about the components themselves:
 *
 *  - `status` is shown as the server sent it, never recomputed. vault-core
 *    and vault-dns are ALWAYS `unknown` (never probed live, user decision
 *    "Weg A"); the per-component notes in `strings.xml` say so, so
 *    "Unknown" never reads as a fault. An unrecognised status word maps to
 *    [AboutStatus.UNKNOWN], never to OK.
 *  - `version`/`commit`: a value, `"invalid"` or `null`. `null` reads
 *    "unknown"; `"invalid"` is shown as sent (a baked value outside the
 *    VER-1 grammar), never a fabricated version; versions are verbatim
 *    (web about-view.js, WEB-FEAT-3). A hex commit is shortened
 *    to 7 characters; anything else is shown as sent, never shortened into
 *    something that looks like a real id.
 *  - A server from before WP VER-2 has no `/v1/about` and answers `404`:
 *    [classifyAboutError] reads that as [AboutLoadFailure.TOO_OLD] ("server
 *    too old" note), anything else as an error.
 *
 * Pure: no Android/Compose dependency. Covered in `AboutPresentationTest`.
 */
enum class AboutStatus { OK, UNREACHABLE, NOT_IN_USE, UNKNOWN }

fun aboutStatusFor(wire: String?): AboutStatus = when (wire) {
    "ok" -> AboutStatus.OK
    "unreachable" -> AboutStatus.UNREACHABLE
    "not_in_use" -> AboutStatus.NOT_IN_USE
    else -> AboutStatus.UNKNOWN
}

private val HEX_COMMIT = Regex("^[0-9a-fA-F]{8,64}$")

/** `null` = show the "unknown" word; a hex id of 8..64 characters is cut to
 * 7; anything else (including `"invalid"`) verbatim. */
fun aboutCommitText(commit: String?): String? {
    if (commit.isNullOrBlank()) return null
    return if (HEX_COMMIT.matches(commit)) commit.take(7) else commit
}

/** The components the UI knows a note for, by wire name. */
enum class AboutComponentKind(val wireName: String) {
    VAULT_API("vault-api"),
    VAULT_CORE("vault-core"),
    VAULT_RUNNER("vault-runner"),
    STEAMPREFILL("steamprefill"),
    VAULT_PROXY("vault-proxy"),
    VAULT_DNS("vault-dns"),
    ;

    companion object {
        fun fromWireName(name: String): AboutComponentKind? = entries.firstOrNull { it.wireName == name }
    }
}

/** Everything one About row shows. */
data class AboutRow(
    val name: String,
    val kind: AboutComponentKind?,
    val version: String?,
    val commit: String?,
    val status: AboutStatus,
    val detail: String?,
)

fun aboutRowFor(component: AboutComponentOut): AboutRow = AboutRow(
    name = component.name,
    kind = AboutComponentKind.fromWireName(component.name),
    version = aboutVersionText(component.version),
    commit = aboutCommitText(component.commit),
    status = aboutStatusFor(component.status),
    detail = component.detail?.takeIf { it.isNotBlank() },
)

enum class AboutLoadFailure { TOO_OLD, ERROR }

/** `404` (no such route before WP VER-2) is "server too old", a note, not
 * an error. Everything else is an error. */
fun classifyAboutError(error: Throwable): AboutLoadFailure =
    if ((error as? VaultApiError)?.status == 404) AboutLoadFailure.TOO_OLD else AboutLoadFailure.ERROR

/**
 * The About version cell, verbatim like web WEB-FEAT-3's `about-view.js`
 * `versionCell`: `null`/blank -> `null` (the "unknown" word), anything else
 * -- `"invalid"`, `"dev"`, `"0.1.0"` -- exactly as the server sent it. The
 * "dev build" / "v" prefix rule belongs to the web rail footer only, which
 * this app does not have.
 */
fun aboutVersionText(version: String?): String? = version?.takeIf { it.isNotBlank() }
