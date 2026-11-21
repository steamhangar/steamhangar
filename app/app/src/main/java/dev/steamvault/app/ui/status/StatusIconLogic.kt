package dev.steamvault.app.ui.status

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import dev.steamvault.app.ui.theme.VaultColors

/**
 * Pure logic for the status-icon system (WP 4b.1) — deliberately kept free
 * of any Android/Compose runtime dependency beyond plain data types
 * (`Color` is a value class, not a framework call) so it is unit-testable
 * on the plain JVM without Robolectric or an emulator (none is available in
 * this environment).
 */

/** Which glyph shape a given [StatusKind] uses — ported from `KIND_GLYPH` in
 *  web/js/components/status-icon.js. */
enum class GlyphShape { CHECK, DOWNLOAD, REFRESH, BANG, PAUSE, STOP, DASH, HALF, QUESTION }

/** [StatusKind] -> [GlyphShape], 1:1 with web's `KIND_GLYPH` table. */
fun glyphFor(kind: StatusKind): GlyphShape = when (kind) {
    StatusKind.CACHED -> GlyphShape.CHECK
    StatusKind.NONE -> GlyphShape.DOWNLOAD
    StatusKind.STALE -> GlyphShape.REFRESH
    StatusKind.RUNNING -> GlyphShape.DOWNLOAD
    StatusKind.UPDATING -> GlyphShape.REFRESH
    StatusKind.VERIFY -> GlyphShape.REFRESH
    StatusKind.PAUSED -> GlyphShape.PAUSE
    StatusKind.ERROR -> GlyphShape.BANG
    StatusKind.WARN -> GlyphShape.BANG
    StatusKind.CANCELLED -> GlyphShape.STOP
    StatusKind.NOTINUSE -> GlyphShape.DASH
    // WP CACHE-COMPLETE-1c: web KIND_GLYPH `partial: "half"`, `unverified: "question"`.
    StatusKind.PARTIAL -> GlyphShape.HALF
    StatusKind.UNVERIFIED -> GlyphShape.QUESTION
}

/**
 * The kinds that carry motion at all, independent of the reduced-motion
 * setting — 1:1 with the web CSS rules `.sic.k-running .dla`,
 * `.sic.k-updating .rot`, `.sic.k-verify .rot` (theme.css). Every other kind
 * (cached, none, stale, paused, error, warn, cancelled) is completely still
 * by design — "a library at rest never flickers" (mockup NOTES.md round 5).
 */
private val ANIMATED_KINDS = setOf(StatusKind.RUNNING, StatusKind.UPDATING, StatusKind.VERIFY)

/**
 * The animate-or-not decision, extracted as a pure function so the
 * reduced-motion disable path is provable in a JVM unit test without a
 * device (WP brief).
 *
 * @param kind the status kind being rendered.
 * @param animatorsEnabled the live value of `ValueAnimator.areAnimatorsEnabled()`
 *   (see [dev.steamvault.app.ui.status.AnimatorsEnabled] for how the caller
 *   obtains it) — false when the system "Remove animations" accessibility
 *   toggle / Settings > Developer options > Animator duration scale is set
 *   to "Animation off".
 * @return true only if this kind is one of the motion-carrying kinds AND
 *   the system has not disabled animations.
 */
fun shouldAnimate(kind: StatusKind, animatorsEnabled: Boolean): Boolean =
    animatorsEnabled && kind in ANIMATED_KINDS

/**
 * The status tone of a kind (WP PILL-TONE-1, audit D-1): one tone per
 * state for the disc, the pill word and the pill ground, the `tone` of
 * `docs/design/status-vocabulary.json` (web `DEFAULT_PILL_TONE` in
 * `components/pill.js`). [wireName] is the JSON value; `null` is the
 * neutral grey pill. There is no accent tone: status colours never double
 * as the accent (mockup NOTES, "Palette").
 */
enum class StatusTone(val wireName: String?) {
    OK("ok"),
    RUN("run"),
    STALE("stale"),
    DANGER("danger"),
    NONE("none"),
    NEUTRAL(null),
}

/**
 * [StatusKind] -> [StatusTone], 1:1 with the vocabulary JSON (checked by
 * `StatusVocabularyContractTest`). Paused is the run tone of its disc (the
 * mockup's "static yellow" paused dot; the pause glyph and the missing
 * motion tell it from Downloading); verify turns on the run hue like
 * Updating (it was the accent until PILL-TONE-1); the update state and
 * "Partially cached" are orange ("a download would fix this").
 */
fun toneFor(kind: StatusKind): StatusTone = when (kind) {
    StatusKind.CACHED -> StatusTone.OK
    StatusKind.RUNNING, StatusKind.UPDATING, StatusKind.VERIFY, StatusKind.PAUSED -> StatusTone.RUN
    StatusKind.STALE, StatusKind.WARN, StatusKind.PARTIAL -> StatusTone.STALE
    StatusKind.ERROR -> StatusTone.DANGER
    StatusKind.NONE, StatusKind.UNVERIFIED -> StatusTone.NONE
    StatusKind.CANCELLED, StatusKind.NOTINUSE -> StatusTone.NEUTRAL
}

/**
 * Disc colour of a tone (web: the `.sic.k-*` backgrounds in theme.css).
 * The neutral tone's disc is Dim2 (WP CONTRAST-1), its word Dim
 * ([pillTextColor]), the one split of the "one tone" rule.
 */
fun discColorFor(tone: StatusTone): Color = when (tone) {
    StatusTone.OK -> VaultColors.StatusOk
    StatusTone.RUN -> VaultColors.StatusRun
    StatusTone.STALE -> VaultColors.StatusStale
    StatusTone.DANGER -> VaultColors.StatusDanger
    StatusTone.NONE -> VaultColors.StatusNone
    StatusTone.NEUTRAL -> VaultColors.Dim2
}

/** Word colour of a pill in [tone] (web kit.css `.kit .pill.<tone>`, the
 * plain `.kit .pill` is Dim). */
fun pillTextColor(tone: StatusTone): Color = when (tone) {
    StatusTone.NEUTRAL -> VaultColors.Dim
    else -> discColorFor(tone)
}

/** Ground of a pill in [tone]: the tone at 14% composited over Surface,
 * an OPAQUE colour (web `color-mix(in srgb, var(--T) 14%, var(--surface))`),
 * so the word's contrast does not depend on what the pill sits on (a
 * translucent tint over a lighter parent would lower it); Raised2 for the
 * neutral pill (web `.kit .pill`). The none pill sat on Raised2 too until
 * WP PILL-TONE-1: its word was 4.46:1 there, under 4.5 for small text;
 * 4.60:1 on its own tint. `StatusIconLogicTest` asserts 4.5:1 or more for
 * every tone. */
fun pillGroundColor(tone: StatusTone): Color = when (tone) {
    StatusTone.NEUTRAL -> VaultColors.Raised2
    else -> discColorFor(tone).copy(alpha = PILL_GROUND_ALPHA).compositeOver(VaultColors.Surface)
}

/** Share of the tone in a coloured pill's ground (web `14%`). */
const val PILL_GROUND_ALPHA = 0.14f

/**
 * Background colour for a status-icon circle: the disc colour of the
 * kind's [toneFor] tone, so disc and pill word can never disagree (WP
 * PILL-TONE-1). 1:1 with the `.sic.k-*` background rules in
 * web/css/theme.css (the web test checks those against the same JSON).
 */
fun backgroundFor(kind: StatusKind): Color = discColorFor(toneFor(kind))

/**
 * Glyph ink colour for a status-icon circle: the shared dark ink
 * (`.sic { color:#08120F }`) for every kind. The muted Dim2 discs
 * (cancelled, notinuse; unverified until WP PILL-TONE-1, now StatusNone,
 * 6.22:1) used the light Text ink until WP CONTRAST-1 (GLYPH-CONTRAST-1,
 * audit 2026-11-20): Text on Dim2 is 2.81:1, under the 3:1 a glyph needs;
 * the dark ink is 5.79:1. Web twin: no `.sic.k-*` rule in theme.css sets
 * its own `color` any more.
 */
fun inkFor(@Suppress("UNUSED_PARAMETER") kind: StatusKind): Color = VaultColors.StatusIconInk

/**
 * The running download glyph, "arrow falls through" (WP APP-FIX-3, Android
 * parity with web WP WEB-FIX-7; replaces the old drift-and-fade, which read
 * as "nothing is happening"). Two identical arrows one [DOWNLOAD_FALL_PERIOD]
 * apart fall through the badge, clipped to the badge's own disc, in a
 * seamless linear loop. Transform only: no opacity animation anywhere.
 *
 * Geometry (all in the glyph's 24-unit grid, the same grid the SVG paths in
 * `web/js/components/status-icon.js` use):
 *  - the glyph box is [GLYPH_BOX_FRACTION] (64%) of the badge, centred, so
 *    the badge disc is centred on (12,12) with radius
 *    [BADGE_DISC_RADIUS_UNITS] = 12 / 0.64 = 18.75 (y -6.75..30.75);
 *  - at rest (and under reduced motion) the trailing arrow is parked one
 *    period above the leading one, fully outside the disc, so the static
 *    glyph is ONE arrow;
 *  - at the end frame (offset = period) the leading arrow has fully left
 *    the disc (nearest ink 3.4 units outside) and the trailing one sits
 *    exactly at the rest position, so the snap back to 0 shows no change.
 *    That needs the period to clear the exit (P >= ~28.6) and to keep the
 *    parked arrow hidden at rest (P > ~21.4); 32 meets both with margin;
 *  - linear: constant speed hides the seam; 1.6 s per period (~20 units/s).
 *
 * Both values are twin-pinned against the web source
 * (`StatusIconWebTwinTest`): the period against `DOWNLOAD_FALL_PERIOD` in
 * status-icon.js, the duration against `vault-dlfall`'s use in theme.css.
 */
const val DOWNLOAD_FALL_PERIOD = 32f

/** One full fall period, in milliseconds (web: `vault-dlfall 1.6s linear infinite`). */
const val DOWNLOAD_FALL_DURATION_MS = 1600

/** The glyph box's share of the badge diameter (web `.sic svg{ width:64% }`). */
const val GLYPH_BOX_FRACTION = 0.64f

/** The badge disc's radius in glyph units: 12 / 0.64 = 18.75. */
const val BADGE_DISC_RADIUS_UNITS = 12f / GLYPH_BOX_FRACTION

/** Stroke width of every line glyph, in glyph units (web `stroke-width="2.7"`). */
const val GLYPH_STROKE_WIDTH_UNITS = 2.7f

/**
 * WP CACHE-COMPLETE-1c: the question glyph's hook, web `buildQuestion`'s
 * "M9.1 9.2a2.9 2.9 0 1 1 4.3 2.6" as a centre-parameterised arc (Compose's
 * `arcTo` takes a bounding box and angles, not SVG's endpoint form). For
 * endpoints P1 (9.1,9.2), P2 (13.4,11.8) and radius 2.9 the chord is 5.025,
 * so the centre sits 1.448 from the chord's midpoint (11.25,10.5); the
 * large-arc/sweep flags (1 1) pick the centre above the chord, (12.0,9.26).
 * P1 lies at 181.2 deg, P2 at 61.1 deg; clockwise (y down) from P1 to P2 is
 * 239.9 deg. `StatusIconLogicTest` checks both endpoints against these.
 */
const val QUESTION_ARC_CX = 12.0f
const val QUESTION_ARC_CY = 9.26f
const val QUESTION_ARC_R = 2.9f
const val QUESTION_ARC_START_DEG = 181.2f
const val QUESTION_ARC_SWEEP_DEG = 239.9f

/** A straight stroke of a glyph, in glyph units. */
data class GlyphSegment(val x1: Float, val y1: Float, val x2: Float, val y2: Float)

/**
 * The download arrow as straight segments -- the SVG paths "M12 3.5V13" and
 * "M7.4 8.7 12 13.3 16.6 8.7", ported coordinate for coordinate. The SAME
 * list is what `StatusIcon.kt` draws and what the test-only geometry helpers
 * (`arrowNearestInkDistance`, `arrowLengthInsideDisc`, `arrowTotalLength` in
 * src/test `StatusIconArrowGeometry.kt`, WP STATUS-ICON-TEST-HELPERS-1)
 * measure, so the tests pin the drawn shape, not a copy of it.
 */
val DOWNLOAD_ARROW_SEGMENTS: List<GlyphSegment> = listOf(
    GlyphSegment(12f, 3.5f, 12f, 13f),
    GlyphSegment(7.4f, 8.7f, 12f, 13.3f),
    GlyphSegment(12f, 13.3f, 16.6f, 8.7f),
)

/**
 * The falling group's vertical offset in glyph units at [progress] (0..1 of
 * one period): linear from 0 to [DOWNLOAD_FALL_PERIOD]. Clamped.
 */
fun downloadFallOffset(progress: Float): Float = progress.coerceIn(0f, 1f) * DOWNLOAD_FALL_PERIOD

/**
 * Vertical offsets of the two arrows at [progress]: the leading arrow at
 * [downloadFallOffset], the trailing one exactly one period above it. Only
 * the RUNNING kind draws the trailing arrow (web: only `running` builds
 * `g.dlnext`); at progress 0 (reduced motion) the trailing arrow is the
 * parked, clipped-away one.
 */
fun downloadArrowOffsets(progress: Float): List<Float> {
    val lead = downloadFallOffset(progress)
    return listOf(lead, lead - DOWNLOAD_FALL_PERIOD)
}
