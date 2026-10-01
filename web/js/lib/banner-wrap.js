/**
 * Shared app-shell banner container (WP WEB-FIX-2).
 *
 * The connection banner and the bypass banner share ONE `.banner-wrap`
 * (index.html), because at BP-L that element owns the single "banner" grid
 * area (css/app.css) — two wraps would overlap in the same cell. Each
 * banner toggles its own `.banner-slot`; the wrap is shown while at least
 * one slot is, so it still takes no space when both are hidden.
 *
 * @param {{ hidden: boolean, children: ArrayLike<{ hidden: boolean }> }} wrapEl
 */
export function syncBannerWrap(wrapEl) {
  wrapEl.hidden = Array.from(wrapEl.children).every((slot) => slot.hidden);
}
