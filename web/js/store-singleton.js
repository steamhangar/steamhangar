/**
 * Shared polling-store singleton (WP 4a.3).
 *
 * `createPollingStore()` (store.js, WP 4a.2) builds an independent set of
 * poll loops every time it is called; it was never instantiated anywhere
 * before this WP. Views are re-created on every navigation (`app.js`
 * replaces `#view-root`'s children on each route change, with no
 * mount/unmount lifecycle), so a view module that called
 * `createPollingStore()` itself would spin up a brand-new set of jobs/games/
 * clients loops every time the user navigated back to it — exactly the
 * "parallel polling" this WP's brief says not to create.
 *
 * One module-level instance, started (idempotently — `ResourceLoop.start()`
 * already no-ops if already running, store.js) the first time any view
 * needs it, and never stopped: notifications/badges are meant to keep
 * working regardless of which view is currently mounted (mirrors the
 * mockup's always-on notification poll, NOTES.md round 5). Later views
 * (downloads.js WP 4a.5, settings.js WP 4a.6) import this same module
 * instead of building their own store.
 */
import { createPollingStore } from "./store.js";
import { getStoredApiKey, isDemoMode } from "./api.js";

export const store = createPollingStore();

// Started the instant any view imports this module (ES modules are
// evaluated once and cached — the first `import` anywhere in the app is
// the only one that actually runs this line). `ResourceLoop.start()` is
// itself idempotent, so this is safe even if a future module imports
// store-singleton.js and also calls `store.start()` defensively.
//
// WP WEB-FIX-1 (N4): gated on a stored vault API key OR demo mode — the
// same predicate `components/rail-panel.js` already applies to its own
// one-time settings fetch. A genuine first run has neither, so every loop
// would otherwise 401 four times per cycle (jobs/games/clients/cache) and
// back off for the whole time the onboarding overlay is up. Nothing needs
// to start the loops later in that page's life: both onboarding exits
// (`finish()` after a verified key, "browse in demo mode") reload the page,
// and this line runs again against the new localStorage state.
export function shouldStartPolling({ hasApiKey, demoMode }) {
  return hasApiKey || demoMode;
}

if (shouldStartPolling({ hasApiKey: !!getStoredApiKey(), demoMode: isDemoMode() })) {
  store.start();
}
