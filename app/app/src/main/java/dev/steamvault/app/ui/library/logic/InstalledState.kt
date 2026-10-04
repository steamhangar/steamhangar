package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry

/**
 * The installed-state badge (WP AG-3) — Kotlin counterpart of the
 * `installed_on` field WP AG-1 added to `GET /v1/games`/`GET /v1/games/{appid}`
 * (`api/README.md` "Installed state per app"). Pure decision logic only, no
 * Compose/resources — mirrors the shape `ui/detail/logic/DetailWording.kt`'s
 * `ConfirmedCurrentWording`/`confirmedCurrentWording` already establishes for
 * "one enum names the branch, the caller resolves it to text via
 * `strings.xml`". Shared by the library card and the detail sheet so there is
 * exactly one place that decides which of the three states applies —
 * `docs/LEARNINGS.md`'s "two call sites computing the same predicate diverge"
 * entry is exactly the failure mode this file exists to avoid.
 *
 * **Copy rule (api/README.md, both this file's callers must honor it):**
 * `installed_on` is PRE-FILTERED server-side to fresh agent reports only —
 * an empty list means "no fresh signal", which covers BOTH "never installed
 * anywhere" AND "installed, but the reporting agent has gone quiet"
 * indistinguishably. [InstalledBadge.NoSignal] must never be rendered as
 * "not installed anywhere" — nothing in this file, or any caller, may say
 * that sentence.
 *
 * **Cache-content predicate reused, not reinvented.** Whether a game
 * currently "has cache content" is decided by
 * [hasVisibleCacheContent] — the SAME byte-based predicate
 * `ui/library/logic/GameStatus.kt`'s `dispKind` already uses to paint the
 * library card's status icon green. This file does not introduce a second
 * definition of "cached" (e.g. `status == "done"` alone, which — per that
 * file's own kdoc — can be true for a last-cached-remnant row with
 * `size_bytes: null`, i.e. NOT actually cached from the user's point of
 * view).
 */
sealed class InstalledBadge {
    /** `installed_on` is empty. Renders as nothing, or a neutral "no signal"
     * treatment — never "not installed anywhere" (see file kdoc). */
    object NoSignal : InstalledBadge()

    /** Fresh `installed_on` entries exist AND the game currently has visible
     * cache content — the ordinary "installed on \<client\>" affordance. */
    data class InstalledAndCached(val display: InstalledOnDisplay) : InstalledBadge()

    /**
     * Fresh `installed_on` entries exist but the game has NO visible cache
     * content — the motivating scenario for the whole `installed_on` field:
     * a game installed on a gaming PC that the vault is not protecting.
     * Rendered as an explicit statement, not folded into the same wording
     * [InstalledAndCached] uses.
     */
    data class InstalledNotCached(val display: InstalledOnDisplay) : InstalledBadge()
}

/**
 * What a badge composable needs to render one line of text, without ever
 * silently dropping entries when more than one client reports a game
 * installed: the most-recently-reported client is the "primary" one shown
 * by name, and [additionalClientCount] (>= 0) is the count of every OTHER
 * fresh entry, for a "(+N more)" suffix the composable appends via
 * `strings.xml` pluralization — never fabricated as a fixed default.
 *
 * `reported_at` values are ISO-8601 UTC (`"...Z"`, api/README.md), which
 * sorts correctly as a plain string — no `Instant` parsing needed just to
 * pick the most recent one.
 */
data class InstalledOnDisplay(
    val primaryClientId: String,
    val primaryReportedAt: String,
    val additionalClientCount: Int,
)

private fun buildDisplay(entries: List<InstalledOnEntry>): InstalledOnDisplay {
    val primary = entries.maxBy { it.reported_at }
    return InstalledOnDisplay(
        primaryClientId = primary.client_id,
        primaryReportedAt = primary.reported_at,
        additionalClientCount = entries.size - 1,
    )
}

/**
 * @return the badge state for one game. Takes a [GameSummary] rather than
 *   either wire model directly — the detail sheet already converts its
 *   `GameDetail` to a `GameSummary` via `ui/detail/GameDetailSheet.kt`'s
 *   own `gameSummaryFrom` before calling [dispKind]/[hasVisibleCacheContent]/
 *   [hasProtectedCacheContent] on it (that function now copies `installed_on`
 *   across too), so this is the ONE entry point both the library card and
 *   the detail sheet call — no second, `GameDetail`-shaped copy of this
 *   decision to drift from it.
 */
fun installedBadgeFor(game: GameSummary): InstalledBadge {
    if (game.installed_on.isEmpty()) return InstalledBadge.NoSignal
    val display = buildDisplay(game.installed_on)
    // WP API-FIX-4: a Steam tool app is never "installed but not cached" --
    // its depots are cached with the games that use them (web
    // installedBadgeState has the same branch).
    if (isToolApp(game)) return InstalledBadge.InstalledAndCached(display)
    return if (hasVisibleCacheContent(game)) {
        InstalledBadge.InstalledAndCached(display)
    } else {
        InstalledBadge.InstalledNotCached(display)
    }
}
