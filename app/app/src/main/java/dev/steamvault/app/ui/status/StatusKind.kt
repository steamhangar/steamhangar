package dev.steamvault.app.ui.status

import dev.steamvault.app.R

/**
 * The full status-icon kind set (WP 4b.1).
 *
 * MUST match `STATUS_LABEL`'s keys in `web/js/components/status-icon.js`
 * 1:1 (same kind names) so the Android and web frontends never disagree on
 * vocabulary — that consistency is a WP requirement, not a style choice.
 * [labelRes] points at the English string resource carrying the same word
 * as the web `STATUS_LABEL` value for that kind.
 */
enum class StatusKind(val wireName: String, val labelRes: Int) {
    CACHED("cached", R.string.status_cached),
    RUNNING("running", R.string.status_running),
    UPDATING("updating", R.string.status_updating),
    STALE("stale", R.string.status_stale),
    NONE("none", R.string.status_none),
    PAUSED("paused", R.string.status_paused),
    VERIFY("verify", R.string.status_verify),
    ERROR("error", R.string.status_error),
    WARN("warn", R.string.status_warn),
    CANCELLED("cancelled", R.string.status_cancelled),

    /**
     * WP API-FIX-4: web's neutral "notinuse" kind (dash glyph, muted disc),
     * ported for the Steam tool-app card (228980). The library shows its own
     * word for it ("Steam tool package", `GameStatus.kt`); this label is the
     * web STATUS_LABEL word. Web's "unknown" kind is still web-only.
     */
    NOTINUSE("notinuse", R.string.status_notinuse),

    /**
     * WP CACHE-COMPLETE-1c (twin of web `partial`): a cached game with only
     * part of its current build on disk -- orange half-disc. A DISPLAY
     * overlay of a CACHED card (`CacheCompleteness.kt`), never a game's
     * own kind; the card shows its own word ("Partially cached N%"), this
     * label is the icon's fallback.
     */
    PARTIAL("partial", R.string.status_partial),

    /**
     * WP CACHE-COMPLETE-1c (twin of web `unverified`): a cached game whose
     * completeness is not known -- neutral question glyph. Overlay only,
     * like [PARTIAL].
     */
    UNVERIFIED("unverified", R.string.status_unverified);

    companion object {
        /**
         * Resolve a wire-format kind name to a [StatusKind], falling back to
         * [NONE] for anything unrecognized — mirrors the web component's
         * `kind in STATUS_LABEL ? kind : "none"` fallback so an unknown kind
         * never renders as a blank/invalid icon on either frontend.
         */
        fun fromWireName(name: String): StatusKind =
            entries.firstOrNull { it.wireName == name } ?: NONE
    }
}
