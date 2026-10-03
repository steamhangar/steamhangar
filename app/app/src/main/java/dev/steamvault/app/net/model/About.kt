package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * One entry of `GET /v1/about` (WP VER-2) --
 * `vault_api/routers/about.py::ComponentOut`, field names verbatim
 * snake_case per this package's convention.
 *
 * The server's model is strict and closed (six component names in a fixed
 * order, four status words), but this client decodes leniently on purpose:
 * `name`/`status` are plain strings, so a newer server that adds a
 * component or a status word still decodes and the unknown value is shown
 * as sent (`ui/settings/logic/AboutPresentation.kt` maps an unknown status
 * to the neutral "Unknown" word, never to OK).
 *
 * @param version the component's version, `"invalid"` for a baked value
 *   outside the VER-1 grammar, `null` when not known.
 * @param commit the commit id, `"invalid"`, or `null` when not known.
 * @param checked_at when vault-api looked (UTC); the answer is cached by the
 *   server for up to 60 s.
 * @param detail one or two plain sentences from the server; shown verbatim.
 */
@Serializable
data class AboutComponentOut(
    val name: String,
    val version: String? = null,
    val commit: String? = null,
    val status: String,
    val checked_at: String? = null,
    val detail: String? = null,
)

/** `GET /v1/about`'s envelope (`vault_api/routers/about.py::AboutOut`). */
@Serializable
data class AboutOut(
    val components: List<AboutComponentOut> = emptyList(),
)
