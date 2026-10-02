/**
 * "Desktop site" hint banner (WP WEB-FIX-5).
 *
 * Shown when a phone browser renders the UI in "Desktop site" mode (the
 * rule and its assumptions: `lib/desktop-site-hint.js`). It is a third
 * `.banner-slot` in the shell's shared `#banner-wrap` (index.html), next to
 * the connection and bypass banners, so it adds no grid child of `#app`
 * and inherits the BP-L "banner" grid area and the `.banner` styles.
 *
 * Re-evaluated on `resize` and `orientationchange`, debounced. The slot is
 * written only when the computed visibility actually changes, so a burst of
 * resize events (or any other layout change) cannot make it flicker.
 *
 * The ✕ button dismisses it for good in this browser (localStorage, with an
 * in-memory fallback when storage is blocked) and removes the listeners.
 * The button hides with its slot, so focus would drop to <body>; it is
 * handed to `focusAfterDismiss` instead (app.js: the view root).
 *
 * The text and the close button's aria-label are set from
 * lib/desktop-site-hint.js only (one source of truth; index.html carries
 * neither, and the slot is hidden until this module shows it).
 *
 * No store, no API: works the same in demo mode. Dependency-injected
 * factory with no import-time side effects (same posture as
 * connection-banner.js), driven by web/tests/desktop-site-hint.test.js.
 *
 * @param {{
 *   elements: { wrapEl: any, hintSlotEl: any, hintTextEl: any, hintCloseBtn: any },
 *   readEnv: () => {
 *     screenWidth: number, screenHeight: number,
 *     layoutWidth: number, layoutHeight: number, coarsePointer: boolean,
 *   },
 *   eventTarget: { addEventListener: Function, removeEventListener: Function },
 *   getStorage: () => any,
 *   focusAfterDismiss?: () => void,
 *   debounceMs?: number,
 *   setTimer?: (fn: () => void, ms: number) => any,
 *   clearTimer?: (id: any) => void,
 * }} deps
 * @returns {{ visible: () => boolean, dispose: () => void }}
 */

import {
  isDesktopSiteOnPhone,
  createHintDismissal,
  DESKTOP_SITE_HINT_TEXT,
  DESKTOP_SITE_HINT_CLOSE_LABEL,
} from "../lib/desktop-site-hint.js";
import { syncBannerWrap } from "../lib/banner-wrap.js";

export const DESKTOP_HINT_DEBOUNCE_MS = 200;
const EVENTS = Object.freeze(["resize", "orientationchange"]);

export function createDesktopSiteHint({
  elements: { wrapEl, hintSlotEl, hintTextEl, hintCloseBtn },
  readEnv,
  eventTarget,
  getStorage,
  focusAfterDismiss = () => {},
  debounceMs = DESKTOP_HINT_DEBOUNCE_MS,
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
}) {
  const dismissal = createHintDismissal(getStorage);
  let shown = false;
  let timer = null;
  let disposed = false;

  hintTextEl.textContent = DESKTOP_SITE_HINT_TEXT;
  hintCloseBtn.setAttribute("aria-label", DESKTOP_SITE_HINT_CLOSE_LABEL);

  function compute() {
    if (dismissal.isDismissed()) return false;
    try {
      return isDesktopSiteOnPhone(readEnv());
    } catch {
      return false;
    }
  }

  function apply(next) {
    // MUTATION TARGET: only touch the DOM on a real change (no flicker).
    if (next === shown) return;
    shown = next;
    hintSlotEl.hidden = !shown;
    syncBannerWrap(wrapEl);
  }

  function onViewportChange() {
    if (timer !== null) clearTimer(timer);
    // MUTATION TARGET: debounce — evaluate once after the burst settles.
    timer = setTimer(() => {
      timer = null;
      if (!disposed) apply(compute());
    }, debounceMs);
  }

  function dispose() {
    disposed = true;
    if (timer !== null) clearTimer(timer);
    timer = null;
    for (const type of EVENTS) eventTarget.removeEventListener(type, onViewportChange);
  }

  hintCloseBtn.addEventListener("click", () => {
    dismissal.dismiss();
    apply(false);
    dispose();
    // MUTATION TARGET: without this, focus falls to <body>.
    try {
      focusAfterDismiss();
    } catch {
      // A focus failure must never undo the dismissal.
    }
  });

  if (dismissal.isDismissed()) {
    apply(false);
    return { visible: () => shown, dispose() {} };
  }

  for (const type of EVENTS) eventTarget.addEventListener(type, onViewportChange);
  apply(compute());

  return { visible: () => shown, dispose };
}

/**
 * Reads the live environment for the rule. Kept here (not in app.js) so
 * the wiring stays one call. `matchMedia` missing => not coarse => no hint.
 *
 * @param {any} win the browser `window`
 */
export function readBrowserEnv(win) {
  const docEl = win.document.documentElement;
  const coarse =
    typeof win.matchMedia === "function" && win.matchMedia("(pointer: coarse)").matches === true;
  return {
    screenWidth: win.screen.width,
    screenHeight: win.screen.height,
    layoutWidth: docEl.clientWidth || win.innerWidth,
    layoutHeight: docEl.clientHeight || win.innerHeight,
    coarsePointer: coarse,
  };
}
