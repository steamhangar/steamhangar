package dev.steamvault.app.ui.onboarding.logic

/**
 * Onboarding step machine (WP 4b.7) -- pure decision logic for the 3-step
 * first-run flow, ported from `web/js/lib/onboarding-steps.js` (WP 4a.6,
 * itself ported from `docs/design/vault-app-mockup.html`'s frozen round-7
 * onboarding), adapted to what THIS app's step 1 actually needs to collect
 * -- a connectivity profile choice, a base URL, and a vault API key (the
 * mockup's original shape), unlike the web port's step 1, which dropped the
 * server-URL/profile fields entirely because the web app is served BY
 * vault-api itself (`web/js/lib/onboarding-steps.js`'s own module header
 * explains why that shortcut does not apply here -- a native app has no
 * "already being served by the thing it is connecting to" starting point).
 *
 *   Step 1 Connect — connectivity profile (System-VPN vs. public domain) +
 *                    base URL + vault API key, gated on a REAL, verified
 *                    connection test (`net/connection/ConnectionCheck.kt`) --
 *                    same as the web port's step 1, [canAdvance] refuses to
 *                    leave this step on an unverified guess.
 *   Step 2 Steam    — optional: Steam OpenID sign-in (this app's OWN
 *                     device-local flow, WP 4b.3 -- unlike the web port,
 *                     which uses the vault-api Steam relay for identity
 *                     too). WP 4h.4 removed this step's OTHER original
 *                     half -- a device-local Steam Web API key entry field
 *                     -- entirely: library data is relayed through
 *                     vault-api unconditionally once signed in, so there is
 *                     no key left for this step to collect.
 *   Step 3 Done     — summary, then the caller persists to
 *                     [dev.steamvault.app.storage.CredentialStore] and
 *                     shows the real app.
 *
 * WP APP-DEMO adds one more exit besides finishing step 3: "Skip for now —
 * browse in demo mode" (steps 1/2 only, mirroring the web port's own
 * demo-skip link) leaves the flow without a vault-api connection at all,
 * using [dev.steamvault.app.demo.DemoState] fixtures instead — see
 * [shouldShowOnboarding]'s own kdoc for how that interacts with this
 * module's original "first run only" rule.
 *
 * Kept pure (no Android framework, no network) so the gating rule is
 * testable head-on, same reasoning as the web source: step 1 cannot be left
 * until [OnboardingController]'s connection test has actually succeeded
 * against a live server, not merely "the fields are non-blank" -- advancing
 * on an unverified guess would silently persist a broken connection into
 * [dev.steamvault.app.storage.CredentialStore].
 */
enum class OnboardingStep { CONNECT, STEAM, DONE }

val FIRST_ONBOARDING_STEP = OnboardingStep.CONNECT

private val ORDER = OnboardingStep.entries

/** @return 33 | 66 | 100 -- matches the web port's [progressPercent] rounding. */
fun onboardingProgressPercent(step: OnboardingStep): Int =
    Math.round((step.ordinal + 1) * 100.0 / ORDER.size).toInt()

/**
 * Can the flow leave [step] right now? Mirrors
 * `onboarding-steps.js::canAdvance` exactly: step 1 requires [tested], every
 * other step has nothing blocking it (step 2 is optional by design -- the
 * WP brief's "skippable" boundary; step 3 is terminal, `next` is a no-op
 * there per [nextOnboardingStep]).
 */
fun canAdvanceOnboardingStep(step: OnboardingStep, tested: Boolean): Boolean = when (step) {
    OnboardingStep.CONNECT -> tested
    OnboardingStep.STEAM, OnboardingStep.DONE -> true
}

/** @return the step to move to (unchanged if [canAdvanceOnboardingStep] refuses, or already last). */
fun nextOnboardingStep(step: OnboardingStep, tested: Boolean): OnboardingStep {
    if (!canAdvanceOnboardingStep(step, tested)) return step
    val next = step.ordinal + 1
    return if (next < ORDER.size) ORDER[next] else step
}

/** Mockup rule (mirrored from the web port): step 1 has no Back -- a no-op there. */
fun previousOnboardingStep(step: OnboardingStep): OnboardingStep {
    val prev = step.ordinal - 1
    return if (prev >= 0) ORDER[prev] else step
}

/**
 * Should the onboarding flow be shown at all? Mirrors the web port's own
 * `shouldShowOnboarding({hasApiKey, demoMode})` exactly (WP APP-DEMO):
 * first run only -- no working vault-api connection configured yet AND
 * demo mode has not already been chosen. [demoMode] is itself an
 * onboarding EXIT (`MainActivity`'s "Skip for now -- browse in demo mode"
 * action), not a state that reopens it, same as the web port's own
 * "Skip for now" comment documents.
 */
fun shouldShowOnboarding(hasVaultConnection: Boolean, demoMode: Boolean): Boolean =
    !hasVaultConnection && !demoMode
