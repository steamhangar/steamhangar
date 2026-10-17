/**
 * A small WAI-ARIA tab set (WP LIBRARY-DETAIL-1): automatic activation,
 * roving tabindex, Left/Right (wrapping), Home and End. Built for the game
 * detail sheet, which rebuilds its body on structural changes and so needs
 * the selected tab handed back in on every build; the Add-a-device drawer
 * (`components/add-device-sheet.js`, WP ADD-DEVICE-2) uses it too, with an
 * icon per tab and `select(null)` for "no tab chosen yet".
 *
 * Every panel is built by the caller and passed in; the helper only wires
 * ids, `aria-controls`/`aria-labelledby`, `hidden` and the keyboard. All
 * panels stay in the DOM (hidden ones included) so the caller's in-place
 * patching keeps finding its nodes.
 */

/**
 * @param {object} opts
 * @param {string} opts.idPrefix unique per tab set, e.g. "detail"
 * @param {string} opts.label accessible name of the tab list
 * @param {{id: string, label: string, panel: HTMLElement, icon?: Node}[]} opts.tabs
 *   `icon` (optional) is put in front of the label; it should carry no text
 *   (aria-hidden), so the tab's name stays the label.
 * @param {string} opts.selected id of the tab shown first (falls back to the first)
 * @param {(id: string) => void} [opts.onSelect] called after a user selection
 * @returns {{tablist: HTMLElement, select: (id: string|null) => void, selectedId: () => string|null}}
 */
export function createTabs({ idPrefix, label, tabs, selected, onSelect }) {
  const tablist = document.createElement("div");
  tablist.className = "tabs";
  tablist.setAttribute("role", "tablist");
  tablist.setAttribute("aria-label", label);

  let current = tabs.some((t) => t.id === selected) ? selected : tabs[0].id;

  const buttons = tabs.map((t, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tab";
    if (t.icon) btn.append(t.icon, t.label);
    else btn.textContent = t.label;
    btn.id = `${idPrefix}-tab-${t.id}`;
    btn.dataset.role = `tab-${t.id}`;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-controls", `${idPrefix}-panel-${t.id}`);
    btn.addEventListener("click", () => choose(t.id));
    btn.addEventListener("keydown", (event) => {
      let next = null;
      if (event.key === "ArrowRight") next = (i + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (i + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next === null) return;
      event.preventDefault();
      choose(tabs[next].id);
      buttons[next].focus();
    });
    t.panel.id = `${idPrefix}-panel-${t.id}`;
    t.panel.setAttribute("role", "tabpanel");
    t.panel.setAttribute("aria-labelledby", btn.id);
    return btn;
  });
  tablist.append(...buttons);

  /** Select a tab by id; `null` selects none (every panel hidden). */
  function select(id) {
    current = tabs.some((t) => t.id === id) ? id : null;
    tabs.forEach((t, i) => {
      const on = t.id === current;
      buttons[i].setAttribute("aria-selected", on ? "true" : "false");
      // Roving tabindex: only the selected tab is in the Tab order, or the
      // first one when none is selected, so the keyboard can still enter.
      buttons[i].tabIndex = on || (current === null && i === 0) ? 0 : -1;
      t.panel.hidden = !on;
    });
  }

  function choose(id) {
    if (id === current) return;
    select(id);
    if (onSelect) onSelect(id);
  }

  select(current);
  return { tablist, select, selectedId: () => current };
}
