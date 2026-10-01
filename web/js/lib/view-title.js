/**
 * Human-readable titles for the router's top-level views (WP WEB-FIX-1, S2).
 *
 * One place for the words `app.js`'s navigation announcer speaks — the same
 * words each view's own `<h1>` shows (`views/library.js`, `views/downloads.js`,
 * `views/settings.js`), so what a screen reader hears on navigation matches
 * what a sighted user reads at the top of the new view. Kept pure (no DOM,
 * no router import) so `web/tests/view-announcer.test.js` can pin the set
 * against LITERAL expected strings rather than deriving them from the
 * router's own `VIEWS` list (docs/LEARNINGS.md, Android section: a derived
 * round-trip is circular and cannot detect drift).
 */

export const VIEW_TITLES = Object.freeze({
  library: "Library",
  downloads: "Downloads",
  settings: "Settings",
});

/**
 * @param {string} view a router view name
 * @returns {string} the title to announce; the raw name if unknown, never
 *   an empty string (an empty live-region update announces nothing, which
 *   would silently hide a missing mapping).
 */
export function viewTitle(view) {
  return VIEW_TITLES[view] || String(view);
}
