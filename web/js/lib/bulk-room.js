/**
 * Bulk bar height -> `--bulk-h` (WP WEB-FIX-6).
 *
 * In select mode the Library reserves scroll room at the end of the page
 * so the last row of cards can scroll above the fixed bulk action bar
 * (css/app.css, "Scroll room while the bulk bar is up"). The room is
 * derived from `--bulk-h`, the bar's border-box height. That height is not
 * a constant: the note under the count is one to three lines, and the
 * Delete / secondary / Download buttons wrap to a second row on a narrow
 * phone. So the bar is measured live and the value is written onto
 * `<html>` (inline style beats theme.css's `:root` fallback of 168px).
 *
 * One observer for the module's lifetime: views/library.js builds a new bar
 * per mount, so `watch()` drops the previous bar before observing the new
 * one, and library.js's view-change listener calls `unwatch()` when the
 * Library is left. A detached bar reports height 0; that
 * reading (any non-positive one) is ignored so the last real height
 * survives navigating away. The height is the border box's fractional
 * `getBoundingClientRect().height` (not the transform-free but integer-
 * rounded `offsetHeight`), rounded UP so the room never falls short by a
 * sub-pixel. The bar's hidden-state `translateY` moves the rect but does
 * not scale it, so the height is unaffected.
 *
 * Dependency-injected (no globals read here) so it is testable without a
 * browser: `ResizeObserverImpl` is `globalThis.ResizeObserver`, absent in
 * the fake DOM and in old browsers; without it this is a no-op and the CSS
 * fallback applies.
 */

/**
 * @param {{
 *   rootStyle: { setProperty(name: string, value: string): void } | null | undefined,
 *   ResizeObserverImpl: (new (cb: () => void) => {
 *     observe(el: unknown): void,
 *     disconnect(): void,
 *   }) | undefined,
 * }} deps
 * @returns {{ watch(el: { getBoundingClientRect(): { height: number } }): void, unwatch(): void }}
 */
export function createBulkHeightWatcher({ rootStyle, ResizeObserverImpl }) {
  if (typeof ResizeObserverImpl !== "function" || !rootStyle) {
    return { watch() {}, unwatch() {} };
  }
  let current = null;
  const observer = new ResizeObserverImpl(() => {
    if (!current) return;
    // A detached element's rect height is 0, so this one check covers both.
    const height = current.getBoundingClientRect().height;
    if (!(height > 0)) return;
    rootStyle.setProperty("--bulk-h", `${Math.ceil(height)}px`);
  });
  return {
    watch(el) {
      observer.disconnect();
      current = el;
      observer.observe(el);
    },
    unwatch() {
      observer.disconnect();
      current = null;
    },
  };
}
