/**
 * Minimal client-side router (WP 4a.1 scaffold).
 *
 * Uses the History API (real paths — `/library`, `/downloads`, ...) rather
 * than hash routing, because vault-api's SPA fallback
 * (api/vault_api/webui.py, `_SPA_ROUTES`) serves `index.html` for exactly
 * these top-level paths and must stay in sync with this list. If a new
 * top-level view is ever added here, it MUST also be added to
 * `_SPA_ROUTES` in api/vault_api/webui.py, or a hard page load / deep link
 * to it will 404 instead of opening the app.
 *
 * `kit` (WP UI-KIT-1) is a hidden route: the UI kit page (views/kit.js)
 * renders every primitive from the real stylesheets for visual checks and
 * the screenshot goldens (WP WEB-GOLDENS-1). It has no nav-rail button on
 * purpose; it is reached by URL only.
 */

export const VIEWS = ["library", "clients", "downloads", "settings", "kit"];
export const DEFAULT_VIEW = "library";

function viewFromPath(pathname) {
  const segment = pathname.replace(/^\/+/, "").split("/")[0];
  return VIEWS.includes(segment) ? segment : DEFAULT_VIEW;
}

export function currentView() {
  return viewFromPath(window.location.pathname);
}

const listeners = new Set();

/** Register a callback invoked with the new view name on every navigation. */
export function onViewChange(handler) {
  listeners.add(handler);
  return () => listeners.delete(handler);
}

function notify(view) {
  for (const handler of listeners) handler(view);
}

/** Navigate to `view`, updating the URL (pushState) and notifying listeners. */
export function navigateTo(view) {
  const target = VIEWS.includes(view) ? view : DEFAULT_VIEW;
  const path = `/${target}`;
  if (window.location.pathname !== path) {
    window.history.pushState({ view: target }, "", path);
  }
  notify(target);
}

window.addEventListener("popstate", () => notify(currentView()));
