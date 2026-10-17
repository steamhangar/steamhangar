/**
 * Disclosure behaviour for a trigger button plus a panel (WP LIBRARY-1):
 * the Library's split-button menu and its Filters popover share it.
 *
 * - The trigger carries `aria-expanded` (and `aria-haspopup`, set by the
 *   caller); the panel is shown by toggling its `hidden` property.
 * - Escape closes and returns focus to the trigger; a click outside both
 *   closes without moving focus.
 * - `menu: true` adds the menu keyboard model: ArrowDown / ArrowUp on the
 *   trigger open the menu and focus the first / last `[role=menuitem]`,
 *   arrows / Home / End move between items, Tab closes, and activating an
 *   item closes the menu and returns focus to the trigger.
 * - The document listeners exist only while open, so a disposed or
 *   detached disclosure leaks nothing; `dispose()` closes and unbinds.
 *
 * DOM-only, no fetch, no store access.
 */

/**
 * @param {{
 *   trigger: HTMLElement,
 *   panel: HTMLElement,
 *   menu?: boolean,
 *   doc?: Document,
 *   onOpen?: () => void,
 *   onClose?: () => void,
 * }} opts
 */
export function createDisclosure({ trigger, panel, menu = false, doc = document, onOpen, onClose }) {
  let isOpen = false;

  const items = () =>
    // Array.from: a real NodeList has no .filter (it threw on the first tap).
    Array.from(panel.querySelectorAll('[role="menuitem"]')).filter((n) => !n.hidden && n.getAttribute("aria-disabled") !== "true");

  function focusItem(i) {
    const list = items();
    if (!list.length) return;
    list[(i + list.length) % list.length].focus();
  }

  function onDocKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close({ restoreFocus: true });
    }
  }

  function onDocClick(e) {
    const t = e.target;
    if (panel.contains(t) || trigger.contains(t)) return;
    close();
  }

  function open({ focus = "none" } = {}) {
    if (isOpen) return;
    isOpen = true;
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    doc.addEventListener("keydown", onDocKeydown);
    doc.addEventListener("click", onDocClick);
    if (menu && focus !== "none") focusItem(focus === "last" ? -1 : 0);
    if (onOpen) onOpen();
  }

  function close({ restoreFocus = false } = {}) {
    if (!isOpen) return;
    isOpen = false;
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    doc.removeEventListener("keydown", onDocKeydown);
    doc.removeEventListener("click", onDocClick);
    if (restoreFocus) trigger.focus();
    if (onClose) onClose();
  }

  function onTriggerClick() {
    if (isOpen) close();
    else open({ focus: menu ? "first" : "none" });
  }

  function onTriggerKeydown(e) {
    if (!menu) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (isOpen) focusItem(e.key === "ArrowDown" ? 0 : -1);
      else open({ focus: e.key === "ArrowDown" ? "first" : "last" });
    }
  }

  function onPanelKeydown(e) {
    if (!menu) return;
    const list = items();
    const at = list.indexOf(doc.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusItem(at + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusItem(at < 0 ? -1 : at - 1);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusItem(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusItem(-1);
    } else if (e.key === "Tab") {
      close();
    }
  }

  function onPanelClick(e) {
    if (!menu) return;
    // Any item activation closes the menu and puts focus back on the
    // trigger (the item itself may be disabled by the action it starts).
    const t = e.target;
    if (t && t !== panel) close({ restoreFocus: true });
  }

  trigger.setAttribute("aria-expanded", "false");
  panel.hidden = true;
  trigger.addEventListener("click", onTriggerClick);
  trigger.addEventListener("keydown", onTriggerKeydown);
  panel.addEventListener("keydown", onPanelKeydown);
  panel.addEventListener("click", onPanelClick);

  return {
    open,
    close,
    toggle: onTriggerClick,
    isOpen: () => isOpen,
    dispose() {
      close();
      trigger.removeEventListener("click", onTriggerClick);
      trigger.removeEventListener("keydown", onTriggerKeydown);
      panel.removeEventListener("keydown", onPanelKeydown);
      panel.removeEventListener("click", onPanelClick);
    },
  };
}
