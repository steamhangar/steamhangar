/**
 * Auth-failure recovery (WP WEB-FIX-1, B2).
 *
 * Before this module, a vault API key that was rotated or revoked server-
 * side left the web UI silently dead: every polling-store subscriber
 * (`views/library.js`, `views/downloads.js`, `components/bypass-banner.js`,
 * `clients-sheet.js`, `decision-panel.js`, `rail-panel.js`,
 * `game-detail-sheet.js`) drops the store's `{error}` payloads on purpose
 * (a transient poll failure must not blank a working screen), and nothing
 * anywhere consumed `ERROR_KINDS.AUTH` except `checkVaultApiKey` itself. So
 * the loops backed off against a 401 forever, the Library kept its last
 * snapshot, and the only way back — Settings → Connection → "Start" — was
 * itself unreachable because `views/settings.js` early-returned on its own
 * load error before rendering that section (fixed in the same WP).
 *
 * This is the ONE consumer of an AUTH-kind store error: on the FIRST
 * `{error}` tick of any resource whose error is `kind === "auth"`, open the
 * onboarding overlay in `mode: "reconnect"` with a notice explaining why —
 * the existing "enter a key, test it, reload" flow, no new UI surface.
 *
 * Guards, each load-bearing:
 *   - once per page life (`fired`): four loops 401 on every cycle, and a
 *     user who dismissed the reconnect dialog (Escape/Skip) must not have
 *     it re-thrown at them every few seconds. Settings → Connection stays
 *     available as the manual entry; a completed reconnect reloads the
 *     page, which resets this guard naturally. A reconnect closed WITHOUT
 *     finishing (Escape/Skip) leaves the guard used for the rest of this
 *     page load — except when a key test succeeded in that open: the key is
 *     then already stored, so `closeOnboarding()` reloads too (round 2, S1)
 *     and the guard resets with the page.
 *   - only with a stored key (`getStoredApiKey()`): a 401 with NO key
 *     stored is a first run, where the first-run overlay is already the
 *     right dialog and `store-singleton.js` no longer polls anyway (N4).
 *   - never while the overlay is already open (`isOnboardingOpen()`): a
 *     reconnect the user opened themselves, or a first-run open, must not
 *     be reset mid-flow by `openOnboarding()`'s `state = freshState()`.
 *     This case does NOT consume `fired` — if the user closes that dialog
 *     unresolved and the next tick still 401s, the notice still gets its
 *     one chance.
 *
 * Same dependency-injected factory shape as `rail-panel.js`/
 * `decision-panel.js` (no import-time side effects, every collaborator
 * passed in) so `web/tests/auth-recovery.test.js` drives it with a fake
 * store and a recording `openOnboarding` — no `document`, no network.
 *
 * @param {{
 *   store: { subscribe: (kind: string, cb: (payload: any) => void) => () => void },
 *   openOnboarding: (opts: { mode: string, notice?: string }) => void,
 *   isOnboardingOpen: () => boolean,
 *   getStoredApiKey: () => string,
 *   resources?: string[],
 * }} deps
 * @returns {{ fired: () => boolean, dispose: () => void }}
 */

import { ERROR_KINDS } from "../errors.js";

export const AUTH_RECOVERY_NOTICE =
  "The vault rejected the stored API key (it may have been rotated or revoked). Enter the current key to reconnect.";

const DEFAULT_RESOURCES = Object.freeze(["jobs", "games", "clients", "cache"]);

export function createAuthRecovery({
  store,
  openOnboarding,
  isOnboardingOpen,
  getStoredApiKey,
  resources = DEFAULT_RESOURCES,
}) {
  let fired = false;

  function onPayload(payload) {
    if (!payload || !payload.error) return;
    // MUTATION TARGET: loosening this to "any error" turns every offline
    // blip into a reconnect dialog — the NETWORK/SERVER kinds are exactly
    // the transient failures the subscribers' drop-the-error convention
    // exists for.
    if (payload.error.kind !== ERROR_KINDS.AUTH) return;
    if (fired) return;
    if (!getStoredApiKey()) return;
    if (isOnboardingOpen()) return;
    fired = true;
    openOnboarding({ mode: "reconnect", notice: AUTH_RECOVERY_NOTICE });
  }

  const unsubscribes = resources.map((kind) => store.subscribe(kind, onPayload));

  return {
    fired: () => fired,
    dispose() {
      for (const off of unsubscribes) off();
    },
  };
}
