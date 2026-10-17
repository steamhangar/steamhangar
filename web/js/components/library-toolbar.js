/**
 * Library page head, toolbar row and status line (WP LIBRARY-2).
 *
 * Builds the static DOM of `docs/design/rc20/library.html` on the UI kit
 * (css/kit.css, ADR-0028): the `.page-head` with the "Check updates" split
 * button, the toolbar row (search, filter chips `.fchip`, the fused sort
 * group `.sortg`, the icon layout switch `.segi`, the Filters popover and
 * the Select button) and the one status line under it. DOM only: no state,
 * no store, no fetch — `views/library.js` owns the state, wires the
 * listeners and calls the `paint*` helpers below on every render.
 *
 * Class names that tests and the view look up (`.lib-split`, `.split-more`,
 * `.lib-bar`, `.lib-tools`, `.lib-sort-dir`, `.lib-select`, `.lib-upd`,
 * `.lib-owned-text`) stay stable; the visual rules for every class here live
 * in the `.kit`-scoped Library block at the end of css/kit.css.
 */

const SEARCH_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>';
const CLEAR_SVG =
  '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m5.5 5.5 9 9M14.5 5.5l-9 9"/></svg>';
const CARET_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
const SELECT_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12l3 3 5-6"/></svg>';
const FILTER_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>';
export const SORT_ASC_SVG =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg>';
export const SORT_DESC_SVG =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14M6 13l6 6 6-6"/></svg>';

/** The three layout buttons' glyphs, keyed like views/library.js's layouts. */
const LAYOUT_SVG = {
  grid2:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4" width="7" height="16" rx="1.5"/><rect x="13" y="4" width="7" height="16" rx="1.5"/></svg>',
  grid3:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="5" height="16" rx="1"/><rect x="9.5" y="4" width="5" height="16" rx="1"/><rect x="16" y="4" width="5" height="16" rx="1"/></svg>',
  list:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
};

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function button(cls, text) {
  const b = el("button", cls, text);
  b.type = "button";
  return b;
}

/** Static, non-interpolated decorative markup only (same trust level as the
 * literal SVGs in index.html's nav). */
function icon(markup) {
  const span = el("span", "ico");
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = markup;
  return span;
}

/**
 * The page head: title, subline and the split button. "Check updates" (the
 * primary half) is always rendered; views/library.js paints its state
 * (loading, busy, disabled, hidden only on a vault-api without the route).
 * The caret menu holds the one action that DOWNLOADS, "Check & update all
 * cached games", so it is never the default.
 * @param {{checkUpdatesLabel: string, checkAndUpdateLabel: string}} labels
 */
function buildHead(labels) {
  const head = el("div", "page-head lib-head");
  const titles = el("div", "lib-titles");
  const h1 = el("h1", null, "Library");
  const sub = el("div", "sub lib-sub");
  titles.append(h1, sub);

  const split = el("div", "lib-split");
  const checkUpdatesBtn = button("btn pri", labels.checkUpdatesLabel);
  checkUpdatesBtn.dataset.action = "check-updates";
  const more = button("btn pri split-more");
  more.setAttribute("aria-haspopup", "menu");
  more.setAttribute("aria-label", "More update actions");
  more.title = "More update actions";
  // Visible only while the primary half is absent (older vault-api).
  const moreLabel = el("span", "split-lbl", "Updates");
  more.append(moreLabel, icon(CARET_SVG));
  const menu = el("div", "pop menu");
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "Update actions");
  const checkAndUpdateBtn = button("menuitem", labels.checkAndUpdateLabel);
  checkAndUpdateBtn.dataset.action = "check-and-update";
  checkAndUpdateBtn.setAttribute("role", "menuitem");
  menu.appendChild(checkAndUpdateBtn);
  split.append(checkUpdatesBtn, more, menu);
  head.append(titles, split);
  return { head, sub, split, checkUpdatesBtn, more, menu, checkAndUpdateBtn };
}

/**
 * The toolbar row `.lib-bar`: `.lib-filters` (search + chips) and
 * `.lib-tools` (sort group, layout switch, Filters, Select). It wraps onto a
 * second row when the chips need the room; css/kit.css lets the chips wrap
 * (never clipped) and, in a narrow container, scroll inside their own row.
 * @param {{query: string, layout: string, layoutLabels: Record<string,string>}} opts
 */
function buildBar({ query, layout, layoutLabels }) {
  const bar = el("div", "lib-bar");

  const searchWrap = el("label", "search lib-search");
  searchWrap.appendChild(icon(SEARCH_SVG));
  const searchInput = el("input");
  searchInput.type = "text";
  searchInput.id = "lib-q";
  searchInput.inputMode = "search";
  searchInput.placeholder = "Search your library";
  searchInput.autocomplete = "off";
  searchInput.spellcheck = false;
  searchInput.setAttribute("aria-label", "Search library by title");
  searchInput.value = query;
  const qClear = button("qx");
  qClear.setAttribute("aria-label", "Clear search");
  qClear.innerHTML = CLEAR_SVG;
  searchWrap.append(searchInput, qClear);
  searchWrap.classList.toggle("filled", query.length > 0);

  const chips = el("div", "chips lib-chips");
  chips.setAttribute("role", "group");
  chips.setAttribute("aria-label", "Filter library");

  const filters = el("div", "lib-filters");
  filters.append(searchWrap, chips);

  // One fused box: the option select and an icon-only direction button.
  const sortWrap = el("div", "sortg");
  sortWrap.setAttribute("role", "group");
  sortWrap.setAttribute("aria-label", "Sort library");
  const sortSelect = el("select", "lib-sort-sel");
  const sortDirBtn = button("lib-sort-dir");
  sortWrap.append(sortSelect, sortDirBtn);

  const layoutSegs = el("div", "segi");
  layoutSegs.setAttribute("role", "group");
  layoutSegs.setAttribute("aria-label", "Library layout");
  for (const key of ["grid2", "grid3", "list"]) {
    const b = button();
    b.dataset.layout = key;
    b.title = layoutLabels[key];
    b.setAttribute("aria-label", layoutLabels[key]);
    b.setAttribute("aria-pressed", String(layout === key));
    b.innerHTML = LAYOUT_SVG[key];
    layoutSegs.appendChild(b);
  }

  // Filters: always visible, the count always shown ("Filters 0").
  const filtersWrap = el("div", "lib-filtersbtn");
  const filtersBtn = button("btn lib-filters-btn");
  filtersBtn.setAttribute("aria-label", "Filters");
  filtersBtn.append(icon(FILTER_SVG), document.createTextNode("Filters"));
  const filtersCount = el("span", "fcount", "0");
  filtersCount.setAttribute("aria-hidden", "true");
  filtersBtn.appendChild(filtersCount);
  const metaPanel = el("div", "lib-meta pop");
  metaPanel.setAttribute("role", "group");
  metaPanel.hidden = true;
  filtersWrap.append(filtersBtn, metaPanel);

  // A labelled Select button replaces the old permanent long-press hint
  // (long-press / right-click on a card still select).
  const selectBtn = button("btn lib-select");
  selectBtn.title = "Select games";
  selectBtn.setAttribute("aria-label", "Select games");
  selectBtn.append(icon(SELECT_SVG), document.createTextNode("Select"));

  const tools = el("div", "lib-tools");
  tools.append(sortWrap, layoutSegs, filtersWrap, selectBtn);
  bar.append(filters, tools);

  return {
    bar,
    searchWrap,
    searchInput,
    qClear,
    chips,
    sortSelect,
    sortDirBtn,
    layoutSegs,
    filtersWrap,
    filtersBtn,
    filtersCount,
    metaPanel,
    selectBtn,
  };
}

/**
 * The ONE status line under the toolbar: "Updates last checked … ·
 * automatic checks on · Reload Steam library", plus the owned-library
 * notice when there is one. Children are flat so css/kit.css can draw the
 * "·" separators between the visible, non-empty ones. The live region is
 * the owned-text span alone and is never `hidden` (an empty span renders
 * nothing; LEARNINGS, WP WEB-FEAT-1).
 * @param {HTMLButtonElement} settingsBtn
 * @param {HTMLButtonElement} reloadBtn
 */
function buildStatusLine(settingsBtn, reloadBtn) {
  const stat = el("div", "lib-stat");
  const updLine = el("span", "lib-upd");
  updLine.hidden = true;
  const ownedText = el("span", "lib-owned-text");
  ownedText.setAttribute("role", "status");
  stat.append(updLine, ownedText, settingsBtn, reloadBtn);
  stat.hidden = true;
  return { stat, updLine, ownedText };
}

/**
 * Build the head, the toolbar row and the status line.
 * @param {{
 *   query: string,
 *   layout: string,
 *   layoutLabels: Record<string,string>,
 *   checkUpdatesLabel: string,
 *   checkAndUpdateLabel: string,
 *   settingsBtn: HTMLButtonElement,
 *   reloadBtn: HTMLButtonElement,
 * }} opts
 */
export function buildLibraryToolbar(opts) {
  const head = buildHead(opts);
  const bar = buildBar(opts);
  const status = buildStatusLine(opts.settingsBtn, opts.reloadBtn);
  return { ...head, ...bar, ...status };
}

/**
 * Repaint the filter chips: sentence-case label and a mono count (kit
 * `.fchip`), `aria-pressed` on the active one.
 * @param {HTMLElement} chipsEl
 * @param {{key: string, label: string, count: number}[]} counts
 * @param {string} activeKey
 */
export function paintChips(chipsEl, counts, activeKey) {
  chipsEl.replaceChildren();
  for (const c of counts) {
    const btn = button("fchip");
    btn.dataset.f = c.key;
    btn.setAttribute("aria-pressed", String(activeKey === c.key));
    // The space keeps the accessible name "Cached 22", not "Cached22".
    btn.appendChild(document.createTextNode(c.label + " "));
    const n = el("b", "n", String(c.count));
    btn.appendChild(n);
    chipsEl.appendChild(btn);
  }
}

/**
 * Repaint the Filters button: the label stays "Filters", the count is always
 * visible (0 included) and reaches assistive tech through the aria-label.
 * @param {{filtersBtn: HTMLElement, filtersCount: HTMLElement}} refs
 * @param {number} n active filter count
 * @param {(n: number) => string} ariaLabel
 */
export function paintFiltersButton(refs, n, ariaLabel) {
  refs.filtersCount.textContent = String(n);
  refs.filtersBtn.classList.toggle("on", n > 0);
  refs.filtersBtn.setAttribute("aria-label", ariaLabel(n));
}
