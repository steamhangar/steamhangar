package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.OwnedGame

/**
 * Merge the vault's known games with the signed-in user's Steam library
 * (WP 4b.4 brief: "Steam library (owned games) merge per the mockup's
 * model where applicable... games not in the vault yet shown from the
 * Steam library when identity + key exist").
 *
 * The mockup's whole library IS one list of (fake) owned games, each
 * annotated with a vault status -- there is no separate "owned" vs "vault"
 * concept there. The real app has two genuinely different data sources
 * (`GET /v1/games` only ever contains apps with an `apps` table row --
 * i.e. apps that have been prefilled or manually mapped at least once,
 * `api/vault_api/routers/games.py`'s `list_games` query -- while
 * `GetOwnedGames` is the phone's own, on-device view of the FULL Steam
 * library, ADR-0004), so this function is the merge step the mockup never
 * needed: union by appid, vault data wins where it exists (it is the
 * authoritative cache-state source), and an owned-but-never-prefilled game
 * gets a synthetic NOT-CACHED row so it still shows up as a "download to
 * cache" candidate.
 *
 * The synthetic row is a real [GameSummary] (not a separate sealed variant)
 * specifically so it flows through [dispKind]/[statusAction]/the filter and
 * bulk-plan logic completely UNCHANGED -- `status = "idle"`,
 * `last_prefill_at = null`, `depot_count = 0`, `size_bytes = null` is
 * exactly the shape [dispKind] already maps to [dev.steamvault.app.ui.status.StatusKind.NONE]
 * (never prefilled, no active job), which matches the mockup's own rule for
 * an uncached game: "Depots unknown until the first download" -- inventing
 * a size or a depot list here would be lying about server knowledge, the
 * same reasoning `hasVisibleCacheContent`'s null-size handling already
 * documents.
 *
 * `needs_force = false` (review fix): [GameSummary.needs_force] is a
 * SERVER-computed signal ("api/vault_api/routers/games.py"'s `GameSummary`
 * kdoc: true for a never-filled app OR after a deletion that changed/left
 * uncertain what's on disk) about a row that exists in `apps`/
 * `depot_app_map` -- for a synthetic row the server has never seen at all,
 * there is no server claim to represent, honest or otherwise, so this
 * mirrors [size_bytes]/`depot_count`'s own "nothing invented" rule rather
 * than asserting the field's semantic TRUE value for a case it was never
 * defined over. This field is presentation-inert here regardless (WP
 * 4b.4's `GameCardModel`/`StatusIcon` never read it; only the not-yet-built
 * detail/GC screens, WP 4b.6, will), so the choice is about correctness of
 * the synthesized shape, not observable behaviour today.
 *
 * A vault row for an appid the Steam library no longer lists (uninstalled,
 * refunded, family-shared access revoked) is DELIBERATELY kept as-is,
 * un-merged and unmarked -- the vault's cache/mapping knowledge about it is
 * still real and still actionable (mirrors mockup-notes.md's "a deleted
 * game stays a co-owner... nothing is invented, nothing is dropped").
 *
 * @param ownedGames `null` when there is no Steam identity, no Web API key
 *   configured, or the on-device fetch failed -- ANY of those cases must
 *   leave the vault-only view fully functional (WP brief, mockup-notes.md
 *   open question 5), so this function's only job in that case is to
 *   return [vaultGames] completely unchanged.
 */
fun mergeLibrary(vaultGames: List<GameSummary>, ownedGames: List<OwnedGame>?): List<GameSummary> {
    if (ownedGames.isNullOrEmpty()) return vaultGames

    val knownAppids = vaultGames.mapTo(HashSet()) { it.appid }
    // WP APP-FIX-2 (parity with web's mergeOwnedLibrary): the one thing a
    // vault row takes from the owned list is a MISSING name -- vault-api
    // has no name for an app it never resolved, so an owned game queued
    // from its detail sheet would otherwise read "App 1234" here while
    // Steam just told us its title. Every cache-state field stays the
    // vault's.
    val filledVault = fillMissingNames(vaultGames, ownedGames)
    val ownedOnly = ownedGames
        .filter { it.appid > 0 && it.appid !in knownAppids }
        // GetOwnedGames can legitimately list the same appid twice across
        // Steam's own quirks (e.g. demo + full game sharing an id in some
        // catalog edge cases) -- de-dupe defensively so the grid never
        // shows two rows with the same key.
        .distinctBy { it.appid }
        .map { owned ->
            GameSummary(
                appid = owned.appid,
                name = owned.name.ifBlank { null },
                status = "idle",
                last_prefill_at = null,
                last_manifest_check = null,
                depot_count = 0,
                size_bytes = null,
                needs_force = false,
            )
        }

    return filledVault + ownedOnly
}

/** A usable display name: non-null and not blank (web `hasText`). */
fun hasText(name: String?): Boolean = name != null && name.isNotBlank()

/**
 * The title every surface shows for an app (WP APP-FIX-2, port of web
 * `owned-library.js::appTitle`): the vault's name, then the owned list's
 * name, then "App <id>". vault-api has no name for an app it never
 * resolved: `POST /v1/prefill` inserts the `apps` row with `name = NULL`
 * (api/vault_api/jobs.py), and only a depot mapping names it later. A job
 * for an owned-only game therefore has a vault row with no name, and the
 * job itself carries none (`JobSummary` has no name field).
 *
 * "App <id>" stays a Kotlin literal under app/README.md's verbatim-port
 * exception: it is web's own fallback, pinned by string equality in
 * `OwnedNamesTest`.
 */
fun appTitle(appid: Int, vaultName: String?, ownedName: String? = null): String {
    if (vaultName != null && hasText(vaultName)) return vaultName.trim()
    if (ownedName != null && hasText(ownedName)) return ownedName.trim()
    return "App $appid"
}

/** appid -> name from the owned list; the first non-blank name per appid
 * wins, entries with a non-positive appid are ignored (web
 * `ownedNamesByAppid`). */
fun ownedNamesByAppid(ownedGames: List<OwnedGame>?): Map<Int, String> {
    val names = LinkedHashMap<Int, String>()
    for (g in ownedGames.orEmpty()) {
        if (g.appid > 0 && g.appid !in names && hasText(g.name)) names[g.appid] = g.name
    }
    return names
}

/**
 * The vault rows with a missing name filled in from the owned list (web
 * `fillMissingNames`). Adds no rows and leaves every other field alone, so
 * surfaces that must only see vault-known apps (Downloads) can use it for
 * names alone. Returns [vaultGames] itself when there is nothing to fill.
 */
fun fillMissingNames(vaultGames: List<GameSummary>, ownedGames: List<OwnedGame>?): List<GameSummary> {
    val names = ownedNamesByAppid(ownedGames)
    if (names.isEmpty()) return vaultGames
    return vaultGames.map { g ->
        val ownedName = if (!hasText(g.name)) names[g.appid] else null
        if (ownedName != null) g.copy(name = ownedName) else g
    }
}
