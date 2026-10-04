package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.AboutComponentOut

/**
 * Settings → About: presentation of `GET /v1/about` (WP VER-2; Android
 * parity in WP APP-FEAT-2; wording reworked in WP WEB-FIX-8 after user
 * feedback on rc9). Twin of web `web/js/lib/about-view.js`: same display
 * states, same rules, same words (`settings_about_*` in `strings.xml`,
 * pinned by `AboutCrossFrontendContractTest` here and by
 * `web/tests/about-android-twin.test.js` on the web side).
 *
 * The server is frozen (ADR-0016), so the friendlier vocabulary is decided
 * HERE from what the server already sends:
 *
 *  - The word "unknown" is never shown. A version/commit the component does
 *    not report is an em dash; the reason sits behind the row's (i) button.
 *    The server's generic `unknown` status reads "Not checked"
 *    ([AboutDisplayStatus.NOT_CHECKED]); an unrecognised status word too,
 *    never OK.
 *  - vault-core: the server's status is ALWAYS `unknown` (no network path,
 *    user decision "Weg A"). When its recorded version AND commit equal
 *    vault-api's (same answer), both come from the same release: OK. When
 *    they differ or cannot be compared: a neutral CHECK. Never recorded:
 *    NOT_REPORTED. See [aboutCoreComparison].
 *  - vault-dns: always `unknown` (optional, never probed): N/A.
 *  - Any other status, and a status other than `unknown` for vault-core or
 *    vault-dns (a future server that probes them), is shown as sent.
 *  - `version`/`commit`: a value, `"invalid"` or `null`. `null` is the dash;
 *    `"invalid"` is shown as sent (a baked value outside the VER-1 grammar),
 *    never a fabricated version; versions are verbatim. A hex commit is
 *    shortened to 7 characters; anything else is shown as sent, never
 *    shortened into something that looks like a real id.
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

/**
 * What the status cell shows (web `ABOUT_DISPLAY`). Only [UNREACHABLE] is a
 * fault (error colour); every other non-OK state is neutral.
 */
enum class AboutDisplayStatus { OK, UNREACHABLE, NOT_IN_USE, NOT_CHECKED, CHECK, NOT_REPORTED, NOT_APPLICABLE }

/** The display state for a server status without component rules (web
 * `ABOUT_STATUS`). */
fun aboutDisplayFor(status: AboutStatus): AboutDisplayStatus = when (status) {
    AboutStatus.OK -> AboutDisplayStatus.OK
    AboutStatus.UNREACHABLE -> AboutDisplayStatus.UNREACHABLE
    AboutStatus.NOT_IN_USE -> AboutDisplayStatus.NOT_IN_USE
    AboutStatus.UNKNOWN -> AboutDisplayStatus.NOT_CHECKED
}

private val HEX_COMMIT = Regex("^[0-9a-fA-F]{8,64}$")

/** `null` = show the dash; a hex id of 8..64 characters is cut to 7;
 * anything else (including `"invalid"`) verbatim. */
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

/** vault-core's recorded build against vault-api's (web `coreComparison`). */
enum class CoreComparison { SAME_RELEASE, MISMATCH, NOT_COMPARABLE, NOT_REPORTED }

private fun comparable(value: String?): Boolean = !value.isNullOrEmpty() && value != "invalid"

/**
 * [CoreComparison.SAME_RELEASE] needs version AND commit present, valid and
 * equal on both sides (commits compared case-insensitively); a missing or
 * `"invalid"` value on either side is [CoreComparison.NOT_COMPARABLE] -- a
 * "dev" build on both sides would otherwise read OK on the version alone.
 * No recorded version at all is [CoreComparison.NOT_REPORTED].
 */
fun aboutCoreComparison(core: AboutComponentOut, api: AboutComponentOut?): CoreComparison {
    if (core.version.isNullOrEmpty()) return CoreComparison.NOT_REPORTED
    if (api == null || !listOf(core.version, core.commit, api.version, api.commit).all(::comparable)) {
        return CoreComparison.NOT_COMPARABLE
    }
    val same = core.version == api.version && core.commit.equals(api.commit, ignoreCase = true)
    return if (same) CoreComparison.SAME_RELEASE else CoreComparison.MISMATCH
}

/** Which first (i) paragraph a row shows (web `COMPONENT_NOTES` /
 * `CORE_NOTES`); the UI maps each to a string resource. */
enum class AboutNote {
    VAULT_API,
    VAULT_CORE,
    CORE_SAME_RELEASE,
    CORE_MISMATCH,
    CORE_NOT_COMPARABLE,
    CORE_NOT_REPORTED,
    VAULT_RUNNER,
    STEAMPREFILL,
    VAULT_PROXY,
    VAULT_DNS,
}

/** Everything one About row shows. [version]/[commit] `null` = the dash.
 * [status] is the server's word, [display] what the row shows. */
data class AboutRow(
    val name: String,
    val kind: AboutComponentKind?,
    val version: String?,
    val commit: String?,
    val status: AboutStatus,
    val display: AboutDisplayStatus,
    val note: AboutNote?,
    /** Add the "A dash means ..." paragraph (a dash is shown and the
     * component's own note does not already explain it). */
    val showDashNote: Boolean,
    val detail: String?,
) {
    /** Whether the row has anything behind its (i) button. */
    val hasInfo: Boolean get() = note != null || showDashNote || detail != null
}

private fun noteFor(kind: AboutComponentKind?): AboutNote? = when (kind) {
    AboutComponentKind.VAULT_API -> AboutNote.VAULT_API
    AboutComponentKind.VAULT_CORE -> AboutNote.VAULT_CORE
    AboutComponentKind.VAULT_RUNNER -> AboutNote.VAULT_RUNNER
    AboutComponentKind.STEAMPREFILL -> AboutNote.STEAMPREFILL
    AboutComponentKind.VAULT_PROXY -> AboutNote.VAULT_PROXY
    AboutComponentKind.VAULT_DNS -> AboutNote.VAULT_DNS
    null -> null
}

/** `api` is vault-api's entry from the same answer (only vault-core's row
 * uses it). */
fun aboutRowFor(component: AboutComponentOut, api: AboutComponentOut? = null): AboutRow {
    val kind = AboutComponentKind.fromWireName(component.name)
    val status = aboutStatusFor(component.status)
    var display = aboutDisplayFor(status)
    var note = noteFor(kind)
    if (component.status == "unknown" && kind == AboutComponentKind.VAULT_CORE) {
        when (aboutCoreComparison(component, api)) {
            CoreComparison.SAME_RELEASE -> { display = AboutDisplayStatus.OK; note = AboutNote.CORE_SAME_RELEASE }
            CoreComparison.MISMATCH -> { display = AboutDisplayStatus.CHECK; note = AboutNote.CORE_MISMATCH }
            CoreComparison.NOT_COMPARABLE -> { display = AboutDisplayStatus.CHECK; note = AboutNote.CORE_NOT_COMPARABLE }
            CoreComparison.NOT_REPORTED -> { display = AboutDisplayStatus.NOT_REPORTED; note = AboutNote.CORE_NOT_REPORTED }
        }
    } else if (component.status == "unknown" && kind == AboutComponentKind.VAULT_DNS) {
        display = AboutDisplayStatus.NOT_APPLICABLE
    }
    val version = aboutVersionText(component.version)
    val commit = aboutCommitText(component.commit)
    val noteExplainsDash = kind == AboutComponentKind.VAULT_PROXY || kind == AboutComponentKind.STEAMPREFILL
    return AboutRow(
        name = component.name,
        kind = kind,
        version = version,
        commit = commit,
        status = status,
        display = display,
        note = note,
        showDashNote = (version == null || commit == null) && !noteExplainsDash,
        detail = component.detail?.takeIf { it.isNotBlank() },
    )
}

/** Every row of an answer, vault-core compared against vault-api's entry
 * in the same list (web `describeComponents`). */
fun aboutRowsFor(components: List<AboutComponentOut>): List<AboutRow> {
    val api = components.firstOrNull { it.name == AboutComponentKind.VAULT_API.wireName }
    return components.map { aboutRowFor(it, api) }
}

enum class AboutLoadFailure { TOO_OLD, ERROR }

/** `404` (no such route before WP VER-2) is "server too old", a note, not
 * an error. Everything else is an error. */
fun classifyAboutError(error: Throwable): AboutLoadFailure =
    if ((error as? VaultApiError)?.status == 404) AboutLoadFailure.TOO_OLD else AboutLoadFailure.ERROR

/**
 * The About version cell, verbatim like web WEB-FEAT-3's `about-view.js`
 * `versionCell`: `null`/blank -> `null` (the dash), anything else
 * -- `"invalid"`, `"dev"`, `"0.1.0"` -- exactly as the server sent it. The
 * "dev build" / "v" prefix rule belongs to the web rail footer only, which
 * this app does not have.
 */
fun aboutVersionText(version: String?): String? = version?.takeIf { it.isNotBlank() }
