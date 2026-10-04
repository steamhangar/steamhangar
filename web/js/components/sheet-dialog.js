/**
 * Minimal reusable bottom-sheet dialog scaffold (WP 4a.7; focus trap +
 * background inert added WP 4a.8).
 *
 * Both the notifications panel (`components/notifications.js`) and the
 * clients sheet (`components/clients-sheet.js`) need the exact same a11y
 * dialog semantics — the WP brief points at "the dialog semantics pattern
 * from 4a.6" (`onboarding.js`): `role="dialog"` + `aria-modal="true"`, focus
 * moved into the surface on open, `Escape` closes it, and focus returns to
 * whatever invoked it on close. Factored into one module so the two sheets
 * cannot drift apart on this — same reasoning as `job-partition.js`'s
 * `jobStatusWord` being a single source of truth for two call sites rather
 * than two hand-copied implementations.
 *
 * **The full focus trap (WP 4a.8, closing the deferral this module's WP 4a.7
 * header used to record here).** `open()`/`close()` push/pop this sheet's
 * `backdrop` onto `lib/modal-stack.js`'s shared stack, which marks `#app`
 * (and any OTHER open overlay further down the stack) `inert` +
 * `aria-hidden` — see that module's header for why `inert` alone is both
 * the trap and the "background inert/aria-hidden" item, rather than two
 * separate mechanisms. `Escape`-to-close is now ALSO delegated to that
 * module (`pushModal`'s `onEscape` callback) rather than a listener bound
 * here directly — see its header, "Escape closes only the TOPMOST overlay",
 * for the live-found bug (Escape closing both this sheet AND a confirm
 * dialog stacked on top of it) that an independent per-instance listener
 * here caused and the centralized dispatcher fixes.
 *
 * Not unit-tested directly (same posture as `onboarding.js`,
 * `components/toast.js`, `components/status-icon.js` — DOM-building code
 * with no meaningful headless behaviour to assert beyond "does it call the
 * DOM API", see web/tests/README.md) — EXCEPT for the modal-stack wiring
 * itself, which `web/tests/dialog-wiring.test.js` pins against a fake DOM
 * (the "sheet closes on navigation" / "background inert" regression the
 * WP 4a.8 brief asks for). Verified live against a running vault-api
 * instance for everything else (see the coder's report).
 *
 * **Desktop overlay geometry (WP 4e.3) — presentation-only, added here as a
 * plain modifier class, not a second state model.** Below BP-L (1024px,
 * `css/theme.css`'s breakpoint table) every instance stays the mockup's
 * bottom sheet, unchanged. From BP-L up, the operator's decision
 * (`docs/PROJECT_PLAN.md`'s Phase 4e section) is that the three
 * `createSheetDialog` instances split into two different desktop shapes:
 * the game detail sheet becomes a centred card, the notifications panel and
 * the clients sheet become a right-edge drawer. `variant` ("center" |
 * "drawer") is passed once at construction and only ever appends a second,
 * static class (`sheet-backdrop--<variant>`/`sheet--<variant>`) alongside
 * the existing `sheet-backdrop`/`sheet` ones — every open()/close()/focus/
 * inert/Escape mechanic below is completely unaware of it, and
 * `css/app.css`'s BP-L block is the only place the variant classes do
 * anything at all (see that file's "desktop overlay geometry" section).
 * `variant` is optional (omit it and an instance stays a bottom sheet at
 * every width — used by `web/tests/dialog-wiring.test.js`'s throwaway test
 * sheets, which have no desktop-geometry opinion of their own).
 */

import { pushModal, popModal } from "../lib/modal-stack.js";

/**
 * `onClose` (WP PAIR-1, optional) runs on EVERY close path — the sheet's own
 * buttons, Escape (modal-stack), a backdrop tap, navigation — after the
 * sheet is hidden and before focus returns. The "Add a device" sheet uses it
 * to drop the revealed pairing secrets from the DOM.
 *
 * @param {{ariaLabel: string, variant?: "center"|"drawer", onClose?: () => void}} options
 * @returns {{
 *   backdrop: HTMLElement, sheet: HTMLElement, body: HTMLElement,
 *   open: () => void, close: () => void, isOpen: () => boolean,
 * }}
 */
export function createSheetDialog({ ariaLabel, variant, onClose }) {
  const backdrop = document.createElement("div");
  backdrop.className = variant ? `sheet-backdrop sheet-backdrop--${variant}` : "sheet-backdrop";

  const sheet = document.createElement("div");
  sheet.className = variant ? `sheet sheet--${variant}` : "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", ariaLabel);
  // Focus target on open: -1 keeps it out of the normal Tab order (it is
  // never meant to be tabbed TO, only focused programmatically), same
  // "tabindex=-1 landing spot" technique onboarding.js uses for its step
  // headings.
  sheet.tabIndex = -1;

  const grab = document.createElement("div");
  grab.className = "grab";
  const body = document.createElement("div");
  body.className = "body";
  sheet.append(grab, body);
  backdrop.appendChild(sheet);
  document.body.appendChild(backdrop);

  /** Element to return focus to on close — captured fresh on every open()
   * (mirrors onboarding.js's `invokerEl`), so whichever control opened this
   * particular sheet (the bell, a notification row, the bypass banner's
   * Details button, ...) gets focus back, not whichever one happened to
   * open it first. */
  let invokerEl = null;

  function isOpen() {
    return backdrop.classList.contains("on");
  }

  function open() {
    invokerEl = document.activeElement;
    backdrop.classList.add("on");
    // WP 4a.8: #app (and any overlay under this one) goes inert, and Escape
    // is now routed through the shared stack's single dispatcher — see that
    // module's header for why a listener bound here directly is the bug.
    pushModal(backdrop, close);
    sheet.focus();
  }

  function close() {
    if (!isOpen()) return;
    backdrop.classList.remove("on");
    popModal(backdrop);
    if (typeof onClose === "function") onClose();
    if (invokerEl && typeof invokerEl.focus === "function") invokerEl.focus();
    invokerEl = null;
  }

  // Tapping the backdrop itself (outside the sheet) dismisses it — the
  // real-DOM equivalent of the mockup's scrim-tap-to-close affordance.
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });

  return { backdrop, sheet, body, open, close, isOpen };
}
