package dev.steamvault.app.ui.nav

import dev.steamvault.app.R

/**
 * The app's top-level destinations (WP 4b.4 brief: "bottom nav
 * Library / Downloads / Settings -- THREE items per the frozen mockup and
 * the recorded WP 4a.1 decision; Clients is a sheet later").
 *
 * **Navigation choice: a plain state-based switcher, not Navigation
 * Compose.** Justification for the reviewer:
 *   - There are exactly three FLAT, always-visible top-level destinations
 *     with no back-stack semantics between them (tapping Library while on
 *     Settings does not "go back" anywhere -- it replaces the screen, same
 *     as the mockup's own `go()` which the design notes describe as a
 *     `closeAll()` + screen swap, not a push/pop).
 *   - No deep-linking requirement exists yet (the mockup's `#library`/
 *     `#downloads`/`#settings` URL hashes are a WEB-only affordance for
 *     reviewing the design; nothing in the WP briefs asks for Android deep
 *     links).
 *   - `MainActivity` already used exactly this pattern pre-4b.4 (a single
 *     `mutableStateOf` screen-state switch for the WP 4b.3 identity
 *     screen) -- this WP extends the SAME shape to three destinations
 *     rather than introducing a second, heavier navigation system
 *     alongside it.
 *   - Adding `androidx.navigation:navigation-compose` now would pull in a
 *     new pinned dependency, a `NavHost`/route-string surface, and a
 *     back-stack-scoped `ViewModelStore` machinery for zero of the above
 *     needs -- premature per "keep it simple". Revisit with an ADR if a
 *     later WP (Settings' onboarding replay, a detail-sheet-as-destination)
 *     genuinely needs push/pop or deep links.
 *
 * The mockup's "navigation dismisses transient surfaces" rule
 * (docs/design/vault-app-mockup-NOTES.md) is honoured at the call site
 * (`MainActivity`): switching [Destination] clears any open sheet/dialog
 * state before recomposing, the same place `go()`'s `closeAll()` sits in
 * the mockup.
 */
enum class Destination(val labelRes: Int) {
    LIBRARY(R.string.nav_library),

    /** WP D15d: the Clients page, right after Library like the web nav
     * (user decision 2026-10-13, D15, superseding the WP 4a.1 "Clients is a
     * sheet" ruling quoted above; the PCs sheet stays, opened from it). */
    CLIENTS(R.string.nav_clients),
    DOWNLOADS(R.string.nav_downloads),
    SETTINGS(R.string.nav_settings),
}
