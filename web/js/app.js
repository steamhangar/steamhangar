/**
 * App shell entry point (WP 4a.1).
 *
 * Wires the bottom nav to the router and swaps the active view. No API
 * calls, no data fetching, no polling here — that lands in WP 4a.2 (API
 * client + polling store); this work package is scaffolding only.
 */
import { VIEWS, DEFAULT_VIEW, currentView, navigateTo, onViewChange } from "./router.js";
import { initToast } from "./components/toast.js";
import { renderLibrary } from "./views/library.js";
import { renderDownloads } from "./views/downloads.js";
import { renderSettings, requestAboutFocus } from "./views/settings.js";
import { maybeShowOnboardingOnStartup } from "./onboarding.js";
// WP 4a.7 — side-effect imports: both components bind their DOM elements
// and store subscriptions at module load (same posture as views/downloads.js's
// module-level nav-pip wiring), so importing them is all app.js needs to do.
// clients-sheet.js is not imported directly here — both of these already
// import it (it is the shared target their "Details"/notification-tap
// actions open), and ES modules are evaluated once and cached.
import "./components/notifications.js";
import "./components/bypass-banner.js";
// WP 4e.6, Opus review should-fix S3: rail-panel.js is a plain, dependency-
// injected factory (`createRailPanel`) with NO import-time side effects of
// its own — the same `store.js`/`store-singleton.js` split, applied to this
// component instead of self-wiring on import like notifications.js/
// bypass-banner.js above. This is the one real, side-effecting call (the
// `store-singleton.js` role), which is why it needs the store/api imports
// below rather than a bare `import "./components/rail-panel.js";`.
import { createRailPanel } from "./components/rail-panel.js";
// WP 4h.2 — same DI-factory posture as rail-panel.js just above (see that
// import's comment): decision-panel.js has no import-time side effects, so
// it needs the real DOM/store/router handed in here.
import { createDecisionPanel } from "./components/decision-panel.js";
// WP WEB-FIX-1 (B2): a rotated/revoked vault API key used to leave the UI
// dead with no recovery surface — every store `{error}` payload is dropped
// by every view. Same DI-factory posture as rail-panel/decision-panel above.
import { createAuthRecovery } from "./components/auth-recovery.js";
// WP WEB-FIX-2: the connection-lost banner, same DI-factory posture.
import { createConnectionBanner } from "./components/connection-banner.js";
import { setConnectionLost } from "./connection-status.js";
// WP WEB-FIX-5: the "Desktop site" hint, same DI-factory posture.
import { createDesktopSiteHint, readBrowserEnv } from "./components/desktop-site-hint.js";
import { store } from "./store-singleton.js";
import { ownedLibrary } from "./owned-singleton.js";
import { api, checkVaultApiKey, getStoredApiKey, isDemoMode, setDemoMode, setStoredApiKey } from "./api.js";
import { openOnboarding, isOnboardingOpen } from "./onboarding.js";
// WP PAIR-1: the receiving side of a browser pairing link (#pair=<key>).
import { showToast } from "./components/toast.js";
import { confirmPairReplace } from "./components/pair-confirm.js";
import {
  PAIR_TEXT,
  runPairIntake,
  setPairedNotice,
  takePairFromLocation,
  takePairedNotice,
} from "./lib/pair-intake.js";
import { viewTitle } from "./lib/view-title.js";

// WP PAIR-1: read and strip a `#pair=<key>` fragment before anything else
// in this module runs, so the key leaves the address bar and the history
// entry at once (lib/pair-intake.js). The intake itself runs after the
// first paint, at the bottom of this file.
const pairCandidate = takePairFromLocation(window);
let pairIntakeBusy = pairCandidate !== null;
const getSessionStorage = () => window.sessionStorage;

const RENDERERS = {
  library: renderLibrary,
  downloads: renderDownloads,
  settings: renderSettings,
};

const viewRoot = document.getElementById("view-root");
const navButtons = Array.from(document.querySelectorAll(".nav-btn"));
// WP WEB-FIX-1 (S2): the visually-hidden role="status" node index.html
// declares next to #toast. `<main>` itself carries NO aria-live any more —
// see index.html's comment on why a live region over the whole view root
// was wrong (every poll tick would be announced).
const viewAnnouncer = document.getElementById("view-announcer");
let announcedView = null;

function renderView(view) {
  const render = RENDERERS[view] || RENDERERS[DEFAULT_VIEW];
  viewRoot.replaceChildren(render());

  // Announce the new view by title, once per navigation. The very first
  // paint (page load) is deliberately NOT announced: the document title and
  // the view's own <h1> already carry it, and a live-region update racing
  // page load is noise, not information.
  if (announcedView !== null) viewAnnouncer.textContent = viewTitle(view);
  announcedView = view;

  for (const btn of navButtons) {
    if (btn.dataset.view === view) {
      btn.setAttribute("aria-current", "page");
    } else {
      btn.removeAttribute("aria-current");
    }
  }
}

for (const btn of navButtons) {
  if (!VIEWS.includes(btn.dataset.view)) continue;
  btn.addEventListener("click", () => navigateTo(btn.dataset.view));
}

onViewChange(renderView);
initToast();
createRailPanel({
  elements: {
    headEl: document.getElementById("rail-head"),
    vaultNameEl: document.getElementById("rail-vault-name"),
    footEl: document.getElementById("rail-foot"),
    cacheEl: document.getElementById("rail-cache"),
    versionEl: document.getElementById("rail-version"),
    createElement: (tag) => document.createElement(tag),
  },
  store,
  apiClient: api,
  getStoredApiKey,
  isDemoMode,
  // WP WEB-FEAT-3: the rail's version line opens Settings → About.
  onVersionActivate: () => {
    requestAboutFocus();
    navigateTo("settings");
  },
});
createDecisionPanel({
  elements: {
    rootEl: document.getElementById("decision-panel"),
    bodyEl: document.getElementById("dp-body"),
    collapseBtn: document.getElementById("dp-collapse"),
    dismissBtn: document.getElementById("dp-dismiss"),
    appEl: document.getElementById("app"),
    createElement: (tag) => document.createElement(tag),
  },
  store,
  onViewChange,
  getCurrentView: currentView,
  // WEB-FIX-5 review: reading `window.localStorage` itself throws when site
  // data is blocked, which would stop this module before the first paint.
  // decision-panel.js's readFlag/writeFlag already catch a null storage.
  storage: (() => {
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  })(),
  ownedNames: ownedLibrary,
});
// WP PAIR-1: while a pairing link is being handled, a 401 from the old key
// must not throw the reconnect dialog over the pairing confirm.
createAuthRecovery({
  store,
  openOnboarding,
  isOnboardingOpen: () => isOnboardingOpen() || pairIntakeBusy,
  getStoredApiKey,
});
createConnectionBanner({
  store,
  isDemoMode,
  elements: {
    wrapEl: document.getElementById("banner-wrap"),
    slotEl: document.getElementById("conn-banner"),
    textEl: document.getElementById("conn-banner-text"),
  },
  announce: (text) => {
    viewAnnouncer.textContent = text;
  },
  setConnectionLost,
});
createDesktopSiteHint({
  elements: {
    wrapEl: document.getElementById("banner-wrap"),
    hintSlotEl: document.getElementById("desktop-hint"),
    hintTextEl: document.getElementById("desktop-hint-text"),
    hintCloseBtn: document.getElementById("desktop-hint-close"),
  },
  readEnv: () => readBrowserEnv(window),
  eventTarget: window,
  getStorage: () => window.localStorage,
  // The ✕ hides with its slot; hand focus to the view root (tabindex=-1).
  focusAfterDismiss: () => viewRoot.focus({ preventScroll: true }),
});
renderView(currentView());
// WP 4a.6: shows the 3-step onboarding overlay on top of whatever view just
// rendered when no vault API key is stored yet and demo mode was not
// already chosen (lib/onboarding-steps.js's shouldShowOnboarding). The
// overlay covers the whole shell (css/app.css `.onb`) so which view sits
// underneath does not matter.
//
// WP PAIR-1: the "Paired." toast survives the reload a successful pairing
// ends with. With a pairing link the first-run check waits for the link's
// outcome: a paired browser reloads into the normal start view, any other
// outcome falls back to the usual first-run check.
if (takePairedNotice(getSessionStorage)) showToast(PAIR_TEXT.paired, { duration: 4000 });
if (pairCandidate) {
  runPairIntake({
    candidate: pairCandidate,
    getStoredApiKey,
    isDemoMode,
    setStoredApiKey,
    setDemoMode,
    checkKey: checkVaultApiKey,
    confirmReplace: confirmPairReplace,
    notify: (text, { warn = false } = {}) => showToast(text, { warn, duration: warn ? 7000 : 3000 }),
    setPairedNotice: () => setPairedNotice(getSessionStorage),
    reload: () => window.location.reload(),
  }).then((outcome) => {
    if (outcome === "paired") return; // the page is reloading
    pairIntakeBusy = false;
    maybeShowOnboardingOnStartup();
  });
} else {
  maybeShowOnboardingOnStartup();
}
