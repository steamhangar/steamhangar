/**
 * Connection-lost banner (WP WEB-FIX-2).
 *
 * The app-level "Lost connection to the vault — showing the last data
 * received. Retrying…" indicator. Same shell placement and styles as the
 * bypass banner (`components/bypass-banner.js`, index.html): it lives in
 * the shared `.banner-wrap`, in its own `.banner-slot`. The decision rule
 * (which errors count, the 20 s threshold, what clears it) is the pure
 * reducer in `lib/connection-watch.js` — see that header.
 *
 * On each show/clear TRANSITION (never per tick) it:
 *   - toggles the slot and the shared wrap,
 *   - announces once through `announce` (app.js passes the visually hidden
 *     role=status `#view-announcer`),
 *   - publishes the flag (`setConnectionLost`, connection-status.js) so
 *     Downloads can disable its job controls.
 *
 * Demo mode never shows it: no subscription is made at all.
 *
 * Dependency-injected factory, no import-time side effects (same posture
 * as auth-recovery.js), driven by web/tests/connection-banner.test.js.
 *
 * @param {{
 *   store: { subscribe: (kind: string, cb: (payload: any) => void) => () => void },
 *   isDemoMode: () => boolean,
 *   elements: { wrapEl: any, slotEl: any, textEl: any },
 *   announce: (text: string) => void,
 *   setConnectionLost: (lost: boolean) => void,
 *   now?: () => number,
 *   resources?: string[],
 * }} deps
 * @returns {{ lost: () => boolean, dispose: () => void }}
 */

import {
  initialConnectionState,
  nextConnectionState,
  connectionLostText,
  CONNECTION_RESTORED_TEXT,
} from "../lib/connection-watch.js";
import { syncBannerWrap } from "../lib/banner-wrap.js";

const DEFAULT_RESOURCES = Object.freeze(["jobs", "games", "clients", "cache"]);

export function createConnectionBanner({
  store,
  isDemoMode,
  elements: { wrapEl, slotEl, textEl },
  announce,
  setConnectionLost,
  now = Date.now,
  resources = DEFAULT_RESOURCES,
}) {
  let state = initialConnectionState();

  // MUTATION TARGET: demo data never fails, but a demo session must never
  // be told its (sample) vault went away either.
  if (isDemoMode()) return { lost: () => false, dispose() {} };

  function onPayload(payload) {
    const next = nextConnectionState(state, payload, now());
    const changed = next.lost !== state.lost;
    state = next;
    // MUTATION TARGET: without this guard every failing tick re-announces.
    if (!changed) return;
    if (state.lost) textEl.textContent = connectionLostText(state.lastSuccessMs);
    slotEl.hidden = !state.lost;
    syncBannerWrap(wrapEl);
    announce(state.lost ? textEl.textContent : CONNECTION_RESTORED_TEXT);
    setConnectionLost(state.lost);
  }

  const unsubscribes = resources.map((kind) => store.subscribe(kind, onPayload));

  return {
    lost: () => state.lost,
    dispose() {
      for (const off of unsubscribes) off();
    },
  };
}
