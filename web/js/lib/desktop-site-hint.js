/**
 * "Desktop site" hint: detection rule and dismissal store (WP WEB-FIX-5).
 *
 * Pure, no DOM. The banner itself is `components/desktop-site-hint.js`.
 *
 * Background (user report, confirmed on a Pixel in Chrome, 2026-10-02):
 * with Chrome's "Desktop site" switched on, a phone ignores
 * `<meta name="viewport" content="width=device-width">` and lays the page
 * out on a ~980 CSS px virtual viewport, then scales it down to fit. The UI
 * looks zoomed out and tiny. Turning "Desktop site" off fixes it. The user
 * chose a dismissible hint over forcing a zoom.
 *
 * **The rule** (`isDesktopSiteOnPhone`), all four must hold:
 *   1. `coarsePointer` — `(pointer: coarse)` matches. Keeps a narrow
 *      desktop monitor or a small desktop window out.
 *   2. `shortSide < 600` — the physical screen (`screen.width/height`, CSS
 *      px) is phone-sized. 600 is the usual phone/tablet split (Android's
 *      sw600dp). Tablets and unfolded foldables are excluded on purpose:
 *      Chrome's tablet desktop mode uses the real window width, not the 980
 *      px virtual viewport (developer.chrome.com/blog/desktop-mode), so the
 *      page is not scaled down there and the hint would be wrong.
 *   3. `layoutWidth > 900` — the layout viewport is desktop-wide. Chrome's
 *      desktop-mode width is 980.
 *   4. `layoutWidth >= 1.5 x` the screen width IN THE CURRENT ORIENTATION.
 *      Rule 3 alone hits a phone in landscape in normal mode: a Pixel 7 is
 *      412x915, so landscape gives a real 915px layout. The ratio check
 *      sees 915/915 = 1.0 and stays quiet; desktop mode in portrait is
 *      980/412 = 2.4. The orientation comes from the window's own aspect,
 *      which desktop-mode scaling preserves (980 wide stays narrower than
 *      it is tall in portrait).
 *
 * Known gap: desktop mode in LANDSCAPE on a phone (980 vs ~915, ratio ~1.07)
 * is not detected. The page is barely scaled there, so the hint would add
 * little; the portrait case is the reported one.
 *
 * `layoutWidth` is `document.documentElement.clientWidth` (the layout
 * viewport, which is what the CSS breakpoints see), NOT `innerWidth`:
 * `clientWidth` does not move with pinch zoom.
 *
 * **Assumption, not verified on a device:** Chrome on Android keeps
 * `screen.width/height` at the device's CSS px size (412x915 on a Pixel 7)
 * when "Desktop site" is on. Desktop mode changes the user agent and the
 * viewport, not the screen metrics Blink reports. Public docs do not state
 * it explicitly, and no Android device is available in this devbox. If a
 * device ever reports the 980 width as `screen.width`, rule 2 fails and the
 * hint simply never shows (the failure direction is "no hint", not "wrong
 * hint"). Same for `(pointer: coarse)`, which is hardware-derived.
 * A page zoom below 100% on a phone can also widen the layout past 900 and
 * would show the hint; that setting is rare and the hint text still points
 * at the browser menu, where both controls live.
 */

export const DESKTOP_SITE_HINT_TEXT =
  "Desktop view is on. Turn off 'Desktop site' in your browser menu for the phone layout.";

export const DESKTOP_SITE_HINT_CLOSE_LABEL = "Dismiss desktop view hint";

export const DESKTOP_SITE_HINT_STORAGE_KEY = "steamvault.desktopSiteHintDismissed";

export const PHONE_SHORT_SIDE_MAX = 600; // exclusive
export const DESKTOP_LAYOUT_MIN = 900; // exclusive
export const LAYOUT_TO_SCREEN_RATIO = 1.5; // inclusive

/**
 * @param {{
 *   screenWidth: number, screenHeight: number,
 *   layoutWidth: number, layoutHeight: number,
 *   coarsePointer: boolean,
 * }} env
 * @returns {boolean}
 */
export function isDesktopSiteOnPhone(env) {
  if (!env) return false;
  const { screenWidth, screenHeight, layoutWidth, layoutHeight, coarsePointer } = env;
  const nums = [screenWidth, screenHeight, layoutWidth, layoutHeight];
  if (!nums.every((n) => Number.isFinite(n) && n > 0)) return false;
  // MUTATION TARGET (rule 1): pointer guard.
  if (coarsePointer !== true) return false;
  const shortSide = Math.min(screenWidth, screenHeight);
  const longSide = Math.max(screenWidth, screenHeight);
  // MUTATION TARGET (rule 2): phone-sized screen only.
  if (shortSide >= PHONE_SHORT_SIDE_MAX) return false;
  // MUTATION TARGET (rule 3): desktop-wide layout.
  if (layoutWidth <= DESKTOP_LAYOUT_MIN) return false;
  const orientedScreenWidth = layoutWidth <= layoutHeight ? shortSide : longSide;
  // MUTATION TARGET (rule 4): layout much wider than the screen it is on.
  return layoutWidth >= orientedScreenWidth * LAYOUT_TO_SCREEN_RATIO;
}

/**
 * Dismissal flag, persisted per browser. Every storage access is wrapped:
 * `getStorage()` itself may throw (blocked site data makes even reading
 * `window.localStorage` throw), and so may getItem/setItem. When storage
 * fails, the in-memory flag still holds for the life of the page.
 *
 * @param {() => ({ getItem: Function, setItem: Function } | null | undefined)} getStorage
 * @returns {{ isDismissed: () => boolean, dismiss: () => void }}
 */
export function createHintDismissal(getStorage) {
  let dismissed = false;
  try {
    const storage = getStorage();
    dismissed = !!storage && storage.getItem(DESKTOP_SITE_HINT_STORAGE_KEY) === "1";
  } catch {
    dismissed = false;
  }
  return {
    isDismissed: () => dismissed,
    dismiss() {
      // MUTATION TARGET: the in-memory flag must be set BEFORE (and
      // regardless of) the storage write.
      dismissed = true;
      try {
        const storage = getStorage();
        if (storage) storage.setItem(DESKTOP_SITE_HINT_STORAGE_KEY, "1");
      } catch {
        // Storage blocked or full: dismissal holds for this page only.
      }
    },
  };
}
