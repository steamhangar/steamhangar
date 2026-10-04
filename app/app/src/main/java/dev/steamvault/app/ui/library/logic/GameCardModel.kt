package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind

/**
 * The card's render-diff decision: everything a `GameCard`/list row needs to
 * draw itself, and NOTHING else -- a plain, structurally-equal `data class`
 * on purpose (WP 4b.4 brief: "the DECISION functions for 'did this row
 * structurally change' should still exist for stable keys/animation
 * preservation").
 *
 * **How this replaces the mockup's DOM-patching machinery (round 7).** The
 * web mockup had to hand-write `repaintAll()` vs `tickProgress()` /
 * `patchGridProgress()` because reassigning `innerHTML` destroys and
 * recreates the animated `<svg>` node on every tick (docs/design/
 * vault-app-mockup-NOTES.md round 7: "the animated svg was a different node
 * on every tick"). Compose does not have that failure mode by construction:
 * a `@Composable` function with only stable, `equals()`-comparable
 * parameters is SKIPPED entirely on recomposition when its new arguments
 * equal its previous ones (the compiler infers "skippable" for a function
 * whose parameter types are all stable, and [GameCardModel] -- a data class
 * of primitives/String/enum/nullable primitives -- qualifies). So the real
 * mechanism this WP relies on is:
 *
 *   1. `LazyVerticalGrid`/`LazyColumn` items are keyed by `appid`
 *      (`items(models, key = { it.appid })` in `LibraryScreen.kt`) -- item
 *      IDENTITY survives a list reorder/refresh, which is what lets Compose
 *      match "the same row" across two poll ticks at all.
 *   2. [buildGameCardModel] is a pure function of `(game, liveJob,
 *      selected)` -- given the SAME inputs it returns an EQUAL
 *      [GameCardModel] (not the same instance, but `==`), because every
 *      field is a value type.
 *   3. `GameCard(model, ...)`'s body -- including the `StatusIcon` composable
 *      that owns the `rememberInfiniteTransition` animation -- is therefore
 *      never re-executed for a row whose derived model didn't change, even
 *      though the PARENT recomposed (a fresh jobs/games poll always
 *      produces new List/JobSummary instances). The animation's
 *      `Animation` object lives inside `StatusIcon`'s own composition scope
 *      and is untouched as long as Compose does not re-enter that scope.
 *
 * [isJobStateTransition] (`GameStatus.kt`) and this file's equality
 * guarantee are the same class of proof for two different layers: the
 * former says "the SERVER-level state didn't change", the latter says
 * "therefore the UI-level model didn't change either, so Compose will
 * skip". `GameCardModelTest` pins exactly this: building the model twice
 * from two functionally-identical-but-distinct `GameSummary`/`JobSummary`
 * list instances (simulating two poll ticks that returned the same data)
 * produces `==` models -- the precondition Compose's skip mechanism relies
 * on. A true Compose recomposition-count assertion needs the Compose UI
 * test harness, which needs a device/Robolectric (neither available in
 * this environment, same constraint every other Compose file in this
 * module documents) -- this is the JVM-provable half of the guarantee.
 */
data class GameCardModel(
    val appid: Int,
    val name: String,
    val kind: StatusKind,
    /** `null` when there is nothing honest to print (never-cached game, or
     * a cached game whose size is momentarily unknown) -- see
     * [formatBytesGB]'s kdoc. */
    val sizeLabel: String?,
    val coverUrl: String,
    val fallbackHues: FallbackHues,
    val fallbackPattern: Int,
    val action: StatusAction?,
    val selected: Boolean,
    /** `false` for a synthetic Steam-owned-but-never-prefilled row
     * ([mergeLibrary]) -- reserved for the detail sheet (WP 4b.6) to decide
     * whether a depot list / delete action exists at all, not used by the
     * Library grid itself yet. */
    val isKnownToVault: Boolean,
    /** WP AG-3: which of the three `installed_on` states applies to this
     * row -- see `InstalledState.kt`'s `installedBadgeFor` for the decision
     * and its copy-rule kdoc (an empty list is "no fresh signal", never
     * "not installed anywhere"). */
    val installedBadge: InstalledBadge,
    /** WP API-FIX-4: a Steam tool app -- the card shows
     * [TOOL_APP_STATE_WORD] instead of [kind]'s label, and is drawn muted
     * (shown, not hidden). */
    val toolApp: Boolean = false,
)

/**
 * WP WEB-FIX-9 (twin of web `pillClassName`): whether the capsule pill draws
 * its dark ground. The ground exists to carry the size over the cover art;
 * around a lone status icon it was a small grey lozenge with nothing in it
 * (user feedback on rc9: "a grey ring where the status word should be").
 * Icon-only pills draw no ground; the status word stays in the line under
 * the cover, so nothing on the card is said twice.
 */
fun capsulePillHasGround(model: GameCardModel): Boolean = model.sizeLabel != null

/**
 * @param selecting `true` while multi-select is active -- see
 *   [statusAction]'s `selecting` param.
 */
fun buildGameCardModel(
    game: GameSummary,
    liveJob: JobSummary?,
    selected: Boolean,
    selecting: Boolean,
): GameCardModel {
    val kind = dispKind(game, liveJob)
    return GameCardModel(
        appid = game.appid,
        name = gameDisplayName(game),
        kind = kind,
        sizeLabel = formatBytesGB(game.size_bytes),
        coverUrl = coverArtUrl(game.appid),
        fallbackHues = fallbackHues(game.appid),
        fallbackPattern = fallbackPattern(game.appid),
        action = statusAction(game, liveJob, selecting),
        selected = selected,
        isKnownToVault = isKnownToVault(game),
        installedBadge = installedBadgeFor(game),
        toolApp = isToolApp(game),
    )
}
