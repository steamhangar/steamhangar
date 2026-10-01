/**
 * Bypass banner (WP 4a.7).
 *
 * Shown while any client has `bypass_suspected` (`GET /v1/clients`, WP 3.11)
 * — ports the mockup's round-3 `#bypass-banner`, but placed in the app
 * shell (`index.html`, below the topbar, present on every view) instead of
 * inside the Library screen: the only way to make it reachable without
 * editing `web/js/views/library.js` (this WP's explicit constraint), and
 * arguably more useful — a DNS misconfiguration is not a Library-only
 * concern. Unlike the notifications panel / clients sheet, the banner is
 * NOT a transient surface (it doesn't close on navigation) — it is a
 * persistent status indicator, same category as the Downloads nav pip.
 *
 * "Details" opens the clients sheet (`components/clients-sheet.js`).
 * "Dismiss" hides the banner until the underlying condition actually
 * changes (`lib/bypass-banner.js`'s `nextBypassDismissState` — reusing the
 * notifications differ's own bypass_suspected/bypass_resolved transition
 * events, not a fixed timeout or "until next report" the mockup used).
 *
 * DOM-building component, not unit-tested directly; the decision logic it
 * leans on (`lib/bypass-banner.js`, `lib/clients-view.js`'s
 * `bypassBannerText`) is pure and covered in web/tests/bypass-banner.test.js
 * and web/tests/clients-view.test.js.
 */

import { store } from "../store-singleton.js";
import { showToast } from "./toast.js";
import { openClientsSheet } from "./clients-sheet.js";
import { bypassBannerVisible, nextBypassDismissState } from "../lib/bypass-banner.js";
import { bypassBannerText } from "../lib/clients-view.js";
import { syncBannerWrap } from "../lib/banner-wrap.js";

const state = {
  clients: store.snapshot("clients") || [],
  dismissed: false,
};

// WP WEB-FIX-2: #bypass-banner-wrap is now this banner's own .banner-slot
// inside the shell's shared #banner-wrap (shared with the connection banner).
const wrap = document.getElementById("bypass-banner-wrap");
const sharedWrap = document.getElementById("banner-wrap");
const textEl = document.getElementById("bypass-banner-text");
const detailsBtn = document.getElementById("bypass-details");
const dismissBtn = document.getElementById("bypass-dismiss");

function render() {
  const visible = bypassBannerVisible(state.clients) && !state.dismissed;
  wrap.hidden = !visible;
  syncBannerWrap(sharedWrap);
  if (visible) textEl.textContent = bypassBannerText(state.clients);
}

detailsBtn.addEventListener("click", () => openClientsSheet());
dismissBtn.addEventListener("click", () => {
  state.dismissed = true;
  render();
  showToast("Bypass warning hidden until it changes.");
});

store.subscribe("clients", ({ items }) => {
  if (!Array.isArray(items)) return; // {error} payload — leave the banner as-is
  state.clients = items;
  render();
});

// Reusing the differ's own transition events for the un-dismiss rule — see
// lib/bypass-banner.js's module header for why this deliberately does NOT
// re-derive the transition itself from consecutive clients snapshots.
store.subscribe("notifications", (events) => {
  const next = nextBypassDismissState(state.dismissed, events);
  if (next !== state.dismissed) {
    state.dismissed = next;
    render();
  }
});

render(); // paint immediately from whatever snapshot already exists
