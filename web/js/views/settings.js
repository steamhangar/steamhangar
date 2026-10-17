/**
 * Settings view (WP 4a.6; sections, expert mode and env pins WP D16-WEB;
 * section nav WP SETTINGS-1; UI kit anatomy WP SETTINGS-2).
 *
 * Anatomy (WP SETTINGS-2, docs/design/rc20/settings.html + kit.html; the
 * root carries `kit`, ADR-0028): page head (h1 + subline), then a left
 * section nav (`nav.snav`: flat 13px items, the one-line state on the same
 * line right-aligned, accent-glow selection, `aria-current` on the active
 * one) with the `.exp` "Show expert settings" row under it, and ONE panel
 * at a time on the right — Vault & connection (first), Download window &
 * updates, Disk & cleanup, Notifications, Steam, About, and Expert settings
 * while the switch is on. Each panel is its h2 + blurb and one `.fcard` per
 * group (lib/settings-layout.js `CARD_TITLES`, the Expert groups'
 * `EXPERT_GROUPS` titles). When the Settings container is 760px or
 * narrower (kit.css `@container settings`), the nav is a scrolling strip
 * and the switch row moves to the end of the About panel — one DOM node,
 * moved, never duplicated. Every panel stays in the DOM (inactive ones
 * `hidden`), so a draft is never lost by switching; a nav item shows a dot
 * while its section holds an unsaved draft. A section with nothing to show
 * is not built. The selected section is per mount, not remembered; the
 * expert switch is (guarded storage).
 *
 * - Normal page: only rows the API marks `expert: false`. Every
 *   `expert: true` row sits in the Expert section, reachable only while
 *   the switch is on. Switching it off keeps its drafts (the switch row
 *   then shows the unsaved-changes dot).
 * - One field = components/settings-field.js: label, (i), state chip
 *   (Default / Changed here / From env), control, Reset for a stored value;
 *   the (i) holds the timing line first, then the help text. Env pins
 *   (Q1 2026-10-14: env always wins) are disabled inputs with the env note
 *   behind the (i). (VAULT_SETTINGS_READONLY keeps its disabled controls.)
 * - State stays visible: chips, the "Last sweep" grid field, the live
 *   GC-risk `.warn` box below the Garbage collection grid (while "include
 *   cached" is on and Auto-GC is not Execute, `gcRiskActive`, from the
 *   current draft or saved values), vault-core's apply state.
 * - Risky keys (API `risky: true`): Save opens one confirm dialog naming
 *   each risky key with old -> new value and what can break
 *   (components/risky-confirm.js); only "Save anyway" sends the PATCH, with
 *   `?confirm_risky=true`. A 428 the client did not foresee opens the same
 *   dialog for the keys the server names, then retries once. 409/422/428
 *   answers say "Nothing was saved" (the API validates the whole body
 *   first); anything else does not claim to know.
 * - The save bar sits at the end of the right column, `position:sticky` at
 *   the bottom (one row: dot + status line | Discard | Save), and exists
 *   only while something is dirty.
 *
 * The form itself (unchanged mechanics, WP 4a.6): the PATCH body is built
 * by `lib/settings-diff.js` from a `drafts` map populated ONLY by fields the
 * user actually edits, so "the body contains only changed keys" holds by
 * construction. Inputs are built once per fetched snapshot and never
 * replaced while the user is typing.
 *
 * Other surfaces on this page:
 *  - Steam — `GET`/`PUT`/`DELETE /v1/steam/key` (WP 4a.6r) plus the vault's
 *    library SteamID64 (WP WEB-FEAT-1), saved with its own button as a
 *    JSON STRING (a number is refused with 422: 17 digits exceed
 *    JavaScript's safe integers), and a "Preview" lookup. The typed key is
 *    handled by `lib/steam-key-form.js`'s `submitSteamKey`, which clears
 *    the input after every attempt (ADR-0004 "never retained").
 *  - Connection — "Reconnect / switch account" replays onboarding; it also
 *    renders in the load-error state (WP WEB-FIX-1 B2), and says so in demo
 *    mode (B1).
 *  - About — the component table from `GET /v1/about` (WP WEB-FEAT-3),
 *    fetched when this view opens and on Refresh, never polled; a 404 shows
 *    "server too old". The rail's version button opens and focuses it.
 *
 * The settings form is not polled. The one store-singleton subscription
 * ("clients") only rewrites the About "checked ... ago" line.
 */

import { api, isDemoMode } from "../api.js";
import { showToast } from "../components/toast.js";
import { confirmRiskyChange } from "../components/risky-confirm.js";
import { createSettingsField, createInfoDisclosure, createFieldChip } from "../components/settings-field.js";
import { createPill } from "../components/pill.js";
import { openOnboarding } from "../onboarding.js";
import { buildSettingsPatch } from "../lib/settings-diff.js";
import {
  appliesText,
  sourceLabel,
  canReset,
  effectiveAsInputValue,
  missingSettingKeys,
} from "../lib/settings-presentation.js";
import {
  CARD_TITLES,
  DEFAULT_SECTION,
  EXPERT_GROUPS,
  LAST_SWEEP_LABEL,
  PAGE_SUBLINE,
  coreApplyLine,
  SECTIONS,
  describeSaveError,
  displayValue,
  envNote,
  gcRiskActive,
  isPinned,
  navTarget,
  placeEntries,
  readExpertSwitch,
  riskyItems,
  riskyKeysFrom428,
  riskyKeysInBody,
  sectionSummary,
  shadowNote,
  writeExpertSwitch,
} from "../lib/settings-layout.js";
import {
  sweepTargetsMessage,
  cachedSweepGcRiskWarning,
  CACHED_SWEEP_GC_RISK_SHORT,
} from "../lib/schedule-presentation.js";
import { validSteamId64 } from "../lib/steamid.js";
import { submitSteamKey } from "../lib/steam-key-form.js";
import {
  INVALID_STEAMID64_MESSAGE,
  LIBRARY_SETTING_ABSENT_MESSAGE,
  NO_STEAM_KEY_MESSAGE,
  PRIVATE_PROFILE_MESSAGE,
  SAVE_OUTCOME,
  STEAM_LIBRARY_SETTING_KEY,
  describeLookupError,
  saveLibrarySteamId,
} from "../lib/owned-library.js";
import { onViewChange } from "../router.js";
import { store } from "../store-singleton.js";
import {
  ABOUT_TOO_OLD_MESSAGE,
  aboutComponents,
  checkedText,
  classifyAboutError,
  describeComponents,
  DASH_LABEL,
} from "../lib/about-view.js";

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed.";
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Guarded: reading `window.localStorage` itself can throw (LEARNINGS
 * WEB-FIX-5); lib/settings-layout.js catches it around this call. */
function getStorage() {
  return window.localStorage;
}

// ---------------------------------------------------------------------
// Module state — persists across re-mounts (same posture as
// views/downloads.js/library.js: views are re-created on every navigation
// with no unmount hook).
// ---------------------------------------------------------------------

const state = {
  loading: true,
  loadError: null,
  settingsResponse: null, // {readonly, settings: [...]}, last successful GET
  steamStatus: { configured: false, key_last4: null },
  lookup: null,
  // WP 4d-web: last GET /v1/schedule response, or null (best-effort; a
  // failure only means the "Last sweep" field is not shown).
  schedule: null,
};

/** {[key]: {reset: true} | {value: string | string[]}} — only ever
 * populated by an actual user edit; see the module header. */
let drafts = {};

/** The section whose panel is shown; reset on every mount. */
let activeSection = DEFAULT_SECTION;
/** The "Show expert settings" switch, re-read on every mount. */
let expertOn = false;
/** Field key -> the user's open/closed choice for its (i) text. */
const fieldInfoOpen = new Map();

let sectionEl = null;
function mounted() {
  return sectionEl !== null;
}
let els = null;

function entryByKey(key) {
  const list = state.settingsResponse && state.settingsResponse.settings;
  return (list || []).find((e) => e.key === key);
}

function readonly() {
  return !!(state.settingsResponse && state.settingsResponse.readonly);
}

// ---------------------------------------------------------------------
// Dirty-state bar (shared Save/Discard for the whole /v1/settings form)
// ---------------------------------------------------------------------

/** Shown in the save bar while it is up (WP WEB-FIX-8). */
export const UNSAVED_TEXT = "Unsaved changes";
/** The bar's status line while a PATCH is in flight. */
export const SAVING_TEXT = "Saving…";
/** The bar's status line while the risky-change dialog is open. */
export const CONFIRMING_TEXT = "Waiting for confirmation…";
/** The bar's line after "Cancel" in the risky-change dialog. */
export const NOT_CONFIRMED_TEXT = "Not saved: the change was not confirmed. Your edits are still here.";

/** The PATCH body the drafts would send (`{}` when nothing changes). */
function pendingBody() {
  if (!state.settingsResponse || state.settingsResponse.readonly) return {};
  return buildSettingsPatch(state.settingsResponse.settings, drafts);
}

/** True when the drafts would change something server-side: the same
 * builder the PATCH uses, so a field edited and typed back to its saved
 * value is NOT dirty (WP WEB-FIX-8: "revert -> hidden"). */
function isDirty() {
  return Object.keys(pendingBody()).length > 0;
}

/** Show the unsaved-changes dot on every section (and the expert switch)
 * holding a key of the pending body. */
function paintDirtyDots() {
  if (!mounted() || !els.dots) return;
  const keys = new Set(Object.keys(pendingBody()));
  for (const [id, dot] of els.dots) {
    const owned = els.sectionKeys.get(id) || new Set();
    dot.hidden = ![...owned].some((k) => keys.has(k));
  }
}

/**
 * Show the save bar exactly while something is dirty (WP WEB-FIX-8). The
 * bar is sticky at the bottom of the right column (kit.css `.savebar`, WP
 * SETTINGS-2); `.savebar-up` on the section only feeds the root's scroll
 * padding (app.css), so a control that receives focus is never under it.
 * Any edit clears a previous save error from the bar.
 */
function markDirty() {
  paintDirtyDots();
  paintGcWarn();
  if (!mounted() || !els.saveBar) return;
  const dirty = isDirty();
  els.saveBar.hidden = !dirty;
  els.section.classList.toggle("savebar-up", dirty);
  // Only on a change: the line is a live region, and rewriting the same
  // text on every keystroke would re-announce it. While a save is in
  // flight the line keeps its text.
  if (!saving && els.saveMsg.textContent !== UNSAVED_TEXT) {
    els.saveMsg.textContent = UNSAVED_TEXT;
    els.saveMsg.className = "savebar-msg";
  }
}

/** After a save or discard rebuilt the form, a keyboard user whose focus
 * was on the (now removed) bar lands on the page heading, not on <body>. */
function restoreFocusAfterBar(hadFocus) {
  if (hadFocus && mounted()) els.heading.focus();
}

function barHasFocus() {
  return !!(els && els.saveBar && els.saveBar.contains(document.activeElement));
}

function discardDrafts() {
  const hadFocus = barHasFocus();
  drafts = {};
  fullRender();
  restoreFocusAfterBar(hadFocus);
}

/** True while a save (incl. its confirm dialog) is in progress: Save is
 * `aria-disabled` plus this guard, not `disabled`, so keyboard focus stays
 * on the button (docs/LEARNINGS.md, WP WEB-FEAT-1). */
let saving = false;

function setBarLine(text, isError) {
  if (!mounted() || !els.saveBar) return;
  els.saveMsg.textContent = text;
  els.saveMsg.className = isError ? "savebar-msg is-error" : "savebar-msg";
}

/** Ask about risky keys; resolves true when the user confirmed. */
async function confirmRisky(entries, body, keys) {
  setBarLine(CONFIRMING_TEXT, false);
  return confirmRiskyChange(riskyItems(entries, body, keys));
}

async function saveDrafts() {
  if (saving) return;
  const entries = state.settingsResponse.settings;
  const body = buildSettingsPatch(entries, drafts);
  if (Object.keys(body).length === 0) {
    drafts = {};
    markDirty();
    return;
  }
  // The drafts this PATCH carries. Every edit replaces its key's draft
  // object, so a draft that is no longer the sent one was typed while the
  // save was in flight and must survive it.
  const sent = { ...drafts };
  saving = true;
  els.saveBtn.setAttribute("aria-disabled", "true");
  let confirmed = false;
  try {
    const risky = riskyKeysInBody(entries, body);
    if (risky.length > 0) {
      confirmed = await confirmRisky(entries, body, risky);
      if (!confirmed) {
        setBarLine(NOT_CONFIRMED_TEXT, true);
        return;
      }
    }
    setBarLine(SAVING_TEXT, false);
    let updated;
    try {
      updated = await api.patchSettings(body, { confirmRisky: confirmed });
    } catch (err) {
      // 428: the server knows a risky key this client did not (e.g. an
      // older settings snapshot). Ask once for exactly those, then retry.
      const named = err && err.status === 428 && !confirmed ? riskyKeysFrom428(err) : [];
      if (named.length === 0) throw err;
      if (!(await confirmRisky(entries, body, named))) {
        setBarLine(NOT_CONFIRMED_TEXT, true);
        return;
      }
      confirmed = true;
      setBarLine(SAVING_TEXT, false);
      updated = await api.patchSettings(body, { confirmRisky: true });
    }
    state.settingsResponse = updated;
    const later = {};
    for (const [key, draft] of Object.entries(drafts)) if (sent[key] !== draft) later[key] = draft;
    drafts = later;
    const hadFocus = barHasFocus();
    // The toast (role=status) announces the success; the bar is gone.
    showToast("Settings saved.");
    // WP 4d-web: a saved PATCH can change the sweep's state server-side,
    // so re-fetch the schedule for the "Last sweep" field (best-effort:
    // stale beats a failed save).
    try {
      state.schedule = await api.schedule();
    } catch {
      // leave state.schedule as it was
    }
    if (Object.keys(later).length > 0) {
      saving = false;
      markDirty();
    } else {
      fullRender();
      restoreFocusAfterBar(hadFocus);
    }
  } catch (err) {
    // The bar stays up with the drafts; its own role=status line says what
    // failed (no warn toast on top of it).
    setBarLine(describeSaveError(err, { confirmed }), true);
  } finally {
    saving = false;
    if (mounted() && els.saveBtn) els.saveBtn.removeAttribute("aria-disabled");
  }
}

/** The bar: dot + status line | Discard | Save, one row (kit.css). Never
 * built read-only. */
function buildSaveBar() {
  const bar = el("div", "savebar");
  bar.hidden = true; // markDirty() decides
  bar.dataset.role = "save-bar";
  bar.setAttribute("role", "region");
  bar.setAttribute("aria-label", "Unsaved settings");
  const msgWrap = el("div", "m");
  const dot = el("span", "dot");
  dot.setAttribute("aria-hidden", "true");
  els.saveMsg = el("p", "savebar-msg", UNSAVED_TEXT);
  els.saveMsg.dataset.role = "save-status";
  els.saveMsg.setAttribute("role", "status");
  msgWrap.append(dot, els.saveMsg);
  const discardBtn = el("button", "btn ghost", "Discard changes");
  discardBtn.type = "button";
  discardBtn.dataset.role = "settings-discard";
  discardBtn.addEventListener("click", discardDrafts);
  els.saveBtn = el("button", "btn pri", "Save changes");
  els.saveBtn.type = "button";
  els.saveBtn.dataset.role = "settings-save";
  els.saveBtn.addEventListener("click", saveDrafts);
  bar.append(msgWrap, discardBtn, els.saveBtn);
  els.saveBar = bar;
  return bar;
}

// ---------------------------------------------------------------------
// Fields and cards
// ---------------------------------------------------------------------

function draftValue(key) {
  return drafts[key] && "value" in drafts[key] ? drafts[key].value : undefined;
}

function setDraft(key, draft) {
  drafts[key] = draft;
  markDirty();
}

/** The saved or drafted value of `key` as the form currently shows it. */
function currentValue(key) {
  const entry = entryByKey(key);
  if (!entry) return undefined;
  const draft = drafts[key];
  if (draft && draft.reset) return entry.fallback;
  if (draft && "value" in draft) return draft.value;
  return entry.effective;
}

function paintGcWarn() {
  if (!mounted() || !els.gcWarn) return;
  els.gcWarn.hidden = !gcRiskActive(currentValue("sweep_include_cached"), currentValue("auto_gc"));
}

function buildField(entry) {
  return createSettingsField(entry, {
    readonly: readonly(),
    draftValue: draftValue(entry.key),
    onDraft: (draft) => setDraft(entry.key, draft),
    memory: fieldInfoOpen,
  });
}

/** One field card: an optional h3 title, then its content nodes. */
function fcard(title, ...nodes) {
  const card = el("div", "fcard");
  if (title) card.appendChild(el("h3", null, title));
  card.append(...nodes);
  return card;
}

/** The auto-fit field grid (kit `.fg`) for a list of rows. */
function buildFieldGrid(rows, extra = []) {
  const grid = el("div", "fg");
  for (const entry of rows) grid.appendChild(buildField(entry));
  grid.append(...extra);
  return grid;
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** The warning triangle of a `.warn` box (decorative). */
function warnGlyph() {
  const svg = document.createElementNS(SVG_NS, "svg");
  const attrs = {
    viewBox: "0 0 24 24",
    width: "16",
    height: "16",
    "aria-hidden": "true",
    focusable: "false",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "2",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
  };
  for (const k in attrs) svg.setAttribute(k, attrs[k]);
  for (const d of ["M12 3l10 18H2z", "M12 10v5", "M12 18h.01"]) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
  }
  return svg;
}

/** A tinted `.warn` box: glyph, then the sentence (and optional (i)). */
function warnBox(text, info = null) {
  const box = el("div", "warn");
  const body = el("div", "wt");
  const line = el("p", null, text);
  if (info) line.append(" ", info.btn);
  body.append(line);
  if (info) body.append(info.help);
  box.append(warnGlyph(), body);
  return box;
}

// ---------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------

/**
 * One settings panel (hidden unless it is the active section): h2 + blurb.
 * `keys` are the settings keys it carries (nav dot); `summaryText` is the
 * nav item's one-line state. The nav is built from what was registered here.
 */
function buildSection(id, { summaryText = "", keys = [] } = {}) {
  const meta = SECTIONS.find((s) => s.id === id);
  const panel = el("section", "settings-panel");
  panel.dataset.section = id;
  panel.hidden = id !== activeSection;
  const head = el("div", "ph");
  const title = el("h2", "sec-title", meta.title);
  if (id === "about") {
    // Focus target of the rail's version button.
    title.id = "settings-about";
    title.tabIndex = -1;
  }
  head.append(title, el("p", "sec-blurb", meta.blurb));
  panel.append(head);
  els.panels.set(id, panel);
  els.navInfo.set(id, { summaryText, keys });
  return { panel, title };
}

/** A form section with its one card; null when it has nothing to show. */
function buildFormSection(id, rows, { gridExtra = [], below = [] } = {}) {
  if (rows.length === 0 && gridExtra.length === 0 && below.length === 0) return null;
  const { panel } = buildSection(id, {
    summaryText: sectionSummary(id, entryByKey, rows),
    keys: rows.map((e) => e.key),
  });
  const content = [];
  if (rows.length > 0 || gridExtra.length > 0) content.push(buildFieldGrid(rows, gridExtra));
  content.push(...below);
  const card = fcard(CARD_TITLES[id], ...content);
  card.dataset.group = id;
  panel.append(card);
  return panel;
}

/** "Last sweep" as a grid field (WP 4d-web's line, `GET /v1/schedule`
 * verbatim); nothing while the schedule is unknown. */
function buildLastSweepField() {
  const statusText = sweepTargetsMessage(state.schedule);
  if (!statusText) return [];
  const field = el("div", "field");
  const head = el("div", "lh");
  head.append(el("span", "lbl", LAST_SWEEP_LABEL));
  const value = el("p", "val", statusText);
  value.dataset.role = "sweep-status";
  field.append(head, value);
  return [field];
}

/**
 * The cached-sweep GC-risk warning: one visible sentence in a `.warn` box
 * below the Garbage collection grid, its explanation behind (i). Always
 * built; `paintGcWarn` shows it exactly while "include cached" is on and
 * Auto-GC is not Execute, live from the drafts.
 */
function buildGcWarn() {
  const long = cachedSweepGcRiskWarning({ sweep_cached_gc_risk: true });
  const info = createInfoDisclosure({
    id: "settings-gc-risk",
    label: "this warning",
    paragraphs: long ? [long] : [],
    memory: fieldInfoOpen,
  });
  const warn = warnBox(CACHED_SWEEP_GC_RISK_SHORT, info);
  warn.dataset.role = "gc-risk";
  els.gcWarn = warn;
  return warn;
}

/** Vault & connection: the Identity card (vault name + any other row placed
 * here) and the Connection card. Always built. */
function buildVaultSection(rows) {
  const { panel } = buildSection("vault", {
    summaryText: sectionSummary("vault", entryByKey, rows),
    keys: rows.map((e) => e.key),
  });
  if (rows.length > 0) {
    const card = fcard(CARD_TITLES.vault, buildFieldGrid(rows));
    card.dataset.group = "vault";
    panel.append(card);
  }
  panel.append(buildConnectionCard());
  return panel;
}

/** The Expert panel (null when the API has no expert rows): vault-core's
 * apply state, then one card per group. Reached only while the switch is
 * on. */
function buildExpertPanel(groups) {
  const all = EXPERT_GROUPS.flatMap((g) => groups[g.id]);
  if (all.length === 0) return null;
  const { panel } = buildSection("expert", {
    summaryText: sectionSummary("expert", entryByKey, all),
    keys: all.map((e) => e.key),
  });
  // D16c: what vault-core reported about the core settings (state only).
  const apply = coreApplyLine(state.settingsResponse.core_apply);
  if (apply) {
    const line = apply.warn ? warnBox(apply.text) : el("p", "hint", apply.text);
    line.dataset.role = "core-apply";
    panel.appendChild(line);
  }
  for (const group of EXPERT_GROUPS) {
    const rows = groups[group.id];
    if (rows.length === 0) continue;
    const card = fcard(group.title, buildFieldGrid(rows));
    card.dataset.group = group.id;
    panel.appendChild(card);
  }
  return panel;
}

// ---------------------------------------------------------------------
// Expert switch and its placement
// ---------------------------------------------------------------------

/** Compact = the Settings container is at most this wide: the SAME number
 * as kit.css's `@container settings (max-width: 760px)`, which turns the
 * nav into a strip. Measured on the view root with a ResizeObserver (no
 * observer, e.g. the node test harness = wide). */
export const COMPACT_MAX_WIDTH = 760;
let compact = false;
let resizeObserver = null;

function observeWidth(section) {
  if (resizeObserver) resizeObserver.disconnect();
  resizeObserver = null;
  if (typeof ResizeObserver !== "function") return;
  resizeObserver = new ResizeObserver((entries) => {
    const last = entries[entries.length - 1];
    const width = last && last.contentRect ? last.contentRect.width : 0;
    if (width <= 0) return; // detached or not laid out: keep the placement
    const next = width <= COMPACT_MAX_WIDTH;
    if (next === compact) return;
    compact = next;
    if (mounted()) placeExpertSwitch();
  });
  resizeObserver.observe(section);
}

/** Wide: the switch row sits under the nav. Compact: it is the LAST item of
 * the About panel instead (never persistently visible). One DOM node, so
 * state, ids and listeners stay single. */
function placeExpertSwitch() {
  const row = els.expertRow;
  if (!row) return;
  const aboutPanel = aboutEls && aboutEls.panel;
  if (compact && aboutPanel) aboutPanel.append(row);
  else if (els.navEl) els.navEl.append(row);
}

/** The "Show expert settings" row (`.exp`: text + small switch); null
 * without expert rows. */
function buildExpertSwitch(groups) {
  const all = EXPERT_GROUPS.flatMap((g) => groups[g.id]);
  if (all.length === 0) return null;
  const row = el("div", "exp");
  const tx = el("div", "tx");
  const ttl = el("span", "ttl", "Show expert settings");
  ttl.id = "settings-expert-switch-label";
  tx.append(ttl, el("small", null, "Bandwidth and connections, housekeeping and debug settings."));
  const dot = el("span", "dot");
  dot.dataset.role = "expert-dirty";
  dot.hidden = true;
  dot.append(el("span", "sr-only", "(unsaved expert changes)"));
  const sw = el("button", "sw");
  sw.type = "button";
  sw.dataset.role = "expert-switch";
  sw.setAttribute("role", "switch");
  sw.setAttribute("aria-labelledby", ttl.id);
  sw.setAttribute("aria-checked", String(expertOn));
  row.append(tx, dot, sw);
  els.dots.set("expert-switch", dot);
  els.sectionKeys.set("expert-switch", new Set(all.map((e) => e.key)));
  sw.addEventListener("click", () => {
    expertOn = !expertOn;
    writeExpertSwitch(getStorage, expertOn);
    sw.setAttribute("aria-checked", String(expertOn));
    const item = els.navItems.get("expert");
    if (item) item.hidden = !expertOn;
    if (!expertOn && activeSection === "expert") selectSection(DEFAULT_SECTION);
  });
  els.expertRow = row;
  return row;
}

// ---------------------------------------------------------------------
// Section navigation
// ---------------------------------------------------------------------

/** Show one panel, mark its nav item `aria-current`. */
function selectSection(id) {
  if (!els.panels.has(id)) return;
  activeSection = id;
  for (const [pid, panel] of els.panels) panel.hidden = pid !== id;
  for (const [nid, item] of els.navItems) {
    if (nid === id) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");
  }
  // Compact: the nav is a scrolling strip; keep the chosen item in it.
  const current = els.navItems.get(id);
  if (compact && current && typeof current.scrollIntoView === "function") {
    current.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

function onNavKey(ev) {
  const current = ev.target && ev.target.dataset ? ev.target.dataset.nav : undefined;
  const ids = [...els.navItems].filter(([, item]) => !item.hidden).map(([id]) => id);
  const next = navTarget(ids, current, ev.key);
  if (!next) return;
  ev.preventDefault();
  selectSection(next);
  els.navItems.get(next).focus();
}

/** The section nav: one flat item per built panel (Expert only while the
 * switch is on): label, unsaved dot, state on the right. */
function buildNav() {
  const nav = el("nav", "snav");
  nav.setAttribute("aria-label", "Settings sections");
  const list = el("div", "snav-list");
  els.navItems = new Map();
  for (const meta of SECTIONS) {
    const info = els.navInfo.get(meta.id);
    if (!info) continue;
    const item = el("button", "snav-item");
    item.type = "button";
    item.dataset.nav = meta.id;
    item.append(el("span", "snav-label", meta.nav));
    if (info.keys.length > 0) {
      const dot = el("span", "dot");
      dot.dataset.role = "section-dirty";
      dot.hidden = true;
      dot.append(el("span", "sr-only", "(unsaved changes)"));
      item.append(dot);
      els.dots.set(meta.id, dot);
      els.sectionKeys.set(meta.id, new Set(info.keys));
    }
    if (info.summaryText) {
      const st = el("span", "snav-state", info.summaryText);
      st.dataset.role = "section-state";
      item.append(st);
    }
    if (meta.id === "expert") item.hidden = !expertOn;
    item.addEventListener("click", () => selectSection(meta.id));
    item.addEventListener("keydown", onNavKey);
    els.navItems.set(meta.id, item);
    list.append(item);
  }
  nav.append(list);
  els.navEl = nav;
  return nav;
}

// ---------------------------------------------------------------------
// Steam (WP 4a.6r relay, WP WEB-FEAT-1 library SteamID64)
// ---------------------------------------------------------------------

function renderSteamStatusLine() {
  const { statusRow, removeBtn } = els.steam;
  const pill = state.steamStatus.configured
    ? createPill({ kind: "cached", text: `Relay key configured (••••${state.steamStatus.key_last4}).`, tone: "ok" })
    : createPill({ kind: "notinuse", text: NO_STEAM_KEY_MESSAGE, tone: null });
  pill.dataset.role = "steam-status";
  statusRow.replaceChildren(pill);
  removeBtn.hidden = !state.steamStatus.configured;
}

function renderLookupResult() {
  const { lookupBody } = els.steam;
  lookupBody.replaceChildren();
  // Empty = hidden, so the card keeps no gap for it.
  lookupBody.hidden = !state.lookup;
  if (!state.lookup) return;
  if (state.lookup.error) {
    lookupBody.appendChild(el("p", "errline", state.lookup.error));
    return;
  }
  if (state.lookup.persona) {
    lookupBody.appendChild(
      el("p", "hint", `Signed in as ${state.lookup.persona.personaname} · SteamID64 ${state.lookup.persona.steamid}`),
    );
  }
  lookupBody.appendChild(el("p", "hint", `${state.lookup.gameCount} games found.`));
  if (state.lookup.gameCount === 0) lookupBody.appendChild(el("p", "hint", PRIVATE_PROFILE_MESSAGE));
  const list = el("ul", "bullets");
  for (const g of state.lookup.preview) {
    list.appendChild(el("li", null, g.name));
  }
  lookupBody.appendChild(list);
}

const STEAM_IDENTITY_HELP =
  "With a relay key configured, library lookups leave your LAN toward Valve. The key is a revocable, read-only Steam Web API key, never a password.";
const STEAM_LIBRARY_HELP =
  "The Library lists every game this SteamID64 owns, on every device that uses this vault. Leave it blank and save to list only the games the vault knows.";

/** The Steam identity card: relay status pill above, then the key field
 * (input + Save key [primary] + Remove key on one row). */
function buildSteamIdentityCard() {
  const statusRow = el("div", "pillrow");

  const keyField = el("div", "field");
  const keyHead = el("div", "lh");
  const keyLabel = el("label", null, "Web API key (32 hex characters)");
  keyLabel.htmlFor = "settings-steam-key";
  const keyInfo = createInfoDisclosure({
    id: "settings-steam-identity",
    label: "the Steam Web API key",
    paragraphs: [STEAM_IDENTITY_HELP],
    memory: fieldInfoOpen,
  });
  keyHead.append(keyLabel, keyInfo.btn);
  const keyInput = el("input", "in mono");
  keyInput.id = "settings-steam-key";
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.spellcheck = false;
  keyInput.placeholder = "from steamcommunity.com/dev/apikey";

  const saveBtn = el("button", "btn pri", "Save key");
  saveBtn.type = "button";
  const removeBtn = el("button", "btn ghost", "Remove key");
  removeBtn.type = "button";
  const keyErr = el("p", "errline");
  keyErr.hidden = true;

  saveBtn.addEventListener("click", async () => {
    keyErr.hidden = true;
    saveBtn.disabled = true;
    const outcome = await submitSteamKey(keyInput, api);
    saveBtn.disabled = false;
    if (!outcome.ok) {
      keyErr.hidden = false;
      keyErr.textContent = outcome.error;
      return;
    }
    state.steamStatus = outcome.result;
    renderSteamStatusLine();
    showToast("Steam Web API key saved.");
  });
  removeBtn.addEventListener("click", async () => {
    removeBtn.disabled = true;
    try {
      await api.deleteSteamKey();
      state.steamStatus = { configured: false, key_last4: null };
      state.lookup = null;
      renderSteamStatusLine();
      renderLookupResult();
      showToast("Steam Web API key removed.");
    } catch (err) {
      showToast(errorText(err), { warn: true });
    } finally {
      removeBtn.disabled = false;
    }
  });

  const row = el("div", "irow");
  row.append(keyInput, saveBtn, removeBtn);
  keyField.append(keyHead, row, keyInfo.help);

  els.steam = { ...els.steam, statusRow, removeBtn };
  const card = fcard(CARD_TITLES.steamIdentity, statusRow, keyField, keyErr);
  card.dataset.group = "steam-identity";
  return card;
}

/**
 * The Steam library card (WP WEB-FEAT-1): the SteamID64 input pre-filled
 * from the `steam_library_steamid` setting, then "Save SteamID64" (its own
 * PATCH, independent of the shared save bar, like "Save key"), "Preview"
 * for the typed id and "Reset" on the same row. Saving never rebuilds the
 * rest of the form, so unsaved drafts elsewhere on the screen survive it.
 */
function buildSteamLibraryCard() {
  const entry = entryByKey(STEAM_LIBRARY_SETTING_KEY);
  const ro = state.settingsResponse.readonly;
  // D16-WEB (Q1): set via env -> a disabled input with the env note behind
  // (i), no Save/Reset; Preview still checks the pinned id.
  const pinned = !!entry && isPinned(entry);

  const idField = el("div", "field");
  const head = el("div", "lh");
  const idLabel = el("label", null, "SteamID64");
  idLabel.htmlFor = "settings-steam-steamid";
  // The timing line (or, pinned, the env note) opens the (i) block, like
  // every other field's; with no setting on the server it is shown below
  // the row instead, because it explains why Save is missing.
  const caption = el("p");
  caption.dataset.role = "steamid-caption";
  const lead = [];
  if (pinned) {
    const note = el("p", null, envNote(entry));
    note.dataset.role = "env-note";
    lead.push(note);
    const shadow = shadowNote(entry);
    if (shadow) {
      const s = el("p", null, shadow);
      s.dataset.role = "shadow-note";
      lead.push(s);
    }
  }
  const info = createInfoDisclosure({
    id: "settings-steam-library",
    label: "SteamID64",
    paragraphs: [STEAM_LIBRARY_HELP],
    lead,
    memory: fieldInfoOpen,
  });
  head.append(idLabel, info.btn);
  if (entry) head.append(createFieldChip(entry));

  const idInput = el("input", "in mono");
  idInput.id = "settings-steam-steamid";
  idInput.type = "text";
  idInput.inputMode = "numeric";
  idInput.autocomplete = "off";
  idInput.spellcheck = false;
  idInput.placeholder = "76561198042117903";
  if (pinned) {
    idInput.disabled = true;
    idInput.value = displayValue(entry);
    idInput.dataset.role = "setting-value";
  } else {
    // Never disabled: Preview checks whatever is typed, and must work on a
    // read-only vault and on an older vault-api without this setting too.
    // Only Save/Reset are gated (WEB-FEAT-1 review S3).
    idInput.value = entry ? effectiveAsInputValue(entry) : "";
  }
  const typedId = () => (pinned ? String((entry && entry.effective) || "").trim() : idInput.value.trim());
  const idErr = el("p", "errline");
  idErr.dataset.role = "steamid-error";
  idErr.hidden = true;

  const saveBtn = el("button", "btn pri", "Save SteamID64");
  saveBtn.type = "button";
  saveBtn.dataset.role = "steamid-save";
  saveBtn.hidden = ro || !entry || pinned;
  const lookupBtn = el("button", "btn", "Preview");
  lookupBtn.type = "button";
  lookupBtn.dataset.role = "steamid-preview";
  const resetBtn = el("button", "btn ghost", "Reset");
  resetBtn.type = "button";
  resetBtn.dataset.role = "steamid-reset";
  resetBtn.title = "Remove the override — back to the environment or default value";
  const lookupBody = el("div", "lookup");
  const visibleCaption = el("div", "capslot");

  function paintCaption() {
    const current = entryByKey(STEAM_LIBRARY_SETTING_KEY);
    if (pinned) {
      caption.hidden = true;
      visibleCaption.hidden = true;
      resetBtn.hidden = true;
      return;
    }
    if (current) {
      caption.className = "";
      caption.textContent = `${sourceLabel(current.source)} · ${appliesText(current.applies)}`;
      if (caption.parentNode !== info.help) info.help.insertBefore(caption, info.help.firstChild);
      visibleCaption.hidden = true;
    } else {
      caption.className = "hint";
      caption.textContent = `${LIBRARY_SETTING_ABSENT_MESSAGE} Preview still works.`;
      visibleCaption.replaceChildren(caption);
      visibleCaption.hidden = false;
    }
    // Same rule as every other setting's Reset (settings-presentation.js's
    // canReset): only a `db` override has anything to clear.
    resetBtn.hidden = ro || !current || !canReset(current);
  }
  paintCaption();

  function showIdError(text) {
    idErr.hidden = !text;
    idErr.textContent = text || "";
  }

  saveBtn.addEventListener("click", async () => {
    showIdError(null);
    if (pinned) return;
    const typed = idInput.value.trim();
    // Shared with onboarding step 2 (WP WEB-FEAT-2): validation, the same
    // body builder as the shared Save bar (only a real change is sent, the
    // value is the trimmed STRING — never a Number()), and the 422 wording.
    if (typed && !validSteamId64(typed)) {
      showIdError(INVALID_STEAMID64_MESSAGE);
      return;
    }
    saveBtn.disabled = true;
    try {
      const result = await saveLibrarySteamId(api, state.settingsResponse, typed);
      if (result.outcome === SAVE_OUTCOME.UNCHANGED) {
        showToast("SteamID64 unchanged.");
      } else if (result.outcome === SAVE_OUTCOME.SAVED) {
        state.settingsResponse = result.settingsResponse;
        paintCaption();
        showToast(typed ? "Library SteamID64 saved." : "Library SteamID64 cleared.");
      } else {
        showIdError(result.error);
      }
    } finally {
      saveBtn.disabled = false;
    }
  });

  resetBtn.addEventListener("click", async () => {
    showIdError(null);
    // {reset: true} -> `null` in the body (buildSettingsPatch), which deletes
    // the override row: back to the env value or the blank default. Blank +
    // Save stays the explicit "" override.
    const body = buildSettingsPatch(state.settingsResponse.settings, {
      [STEAM_LIBRARY_SETTING_KEY]: { reset: true },
    });
    if (Object.keys(body).length === 0) return;
    resetBtn.disabled = true;
    try {
      state.settingsResponse = await api.patchSettings(body);
      const current = entryByKey(STEAM_LIBRARY_SETTING_KEY);
      idInput.value = current ? effectiveAsInputValue(current) : "";
      paintCaption();
      showToast("Library SteamID64 reset.");
    } catch (err) {
      showIdError(errorText(err));
    } finally {
      resetBtn.disabled = false;
    }
  });

  lookupBtn.addEventListener("click", async () => {
    const steamid = validSteamId64(typedId());
    if (!steamid) {
      state.lookup = { error: INVALID_STEAMID64_MESSAGE };
      renderLookupResult();
      return;
    }
    lookupBtn.disabled = true;
    try {
      const [owned, players] = await Promise.all([
        api.steamOwnedGames(steamid),
        api.steamPlayerSummaries(steamid).catch(() => null),
      ]);
      state.lookup = {
        gameCount: owned.game_count,
        preview: owned.games.slice(0, 8),
        persona: players && players.players && players.players[0],
      };
    } catch (err) {
      // Preview checks the TYPED id, so a 422 is about that id, not the
      // stored one; 409 is the shared no-key text; anything else stays the
      // plain server error, as before this WP.
      state.lookup = { error: describeLookupError(err) };
    } finally {
      lookupBtn.disabled = false;
      renderLookupResult();
    }
  });

  const row = el("div", "irow");
  row.append(idInput, saveBtn, lookupBtn, resetBtn);
  idField.append(head, row, info.help, visibleCaption);

  els.steam = { ...els.steam, lookupBody };
  const card = fcard(CARD_TITLES.steamLibrary, idField, idErr, lookupBody);
  card.dataset.group = "steam-library";
  return card;
}

// ---------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------

/**
 * The Connection card — the ONLY manual way back into the connect flow, so
 * (WP WEB-FIX-1, B2) it renders in the load-error state too, not just under
 * a successful `GET /v1/settings` (a 401 from a rotated key is exactly when
 * it is needed). In demo mode (B1) it says so, in plain words, and offers
 * the connect action instead of a "reconnect" that would suggest a vault
 * is already attached.
 */
function buildConnectionCard() {
  const nodes = [];
  const demo = isDemoMode();
  if (demo) {
    const notice = el(
      "p",
      "hint",
      "Demo mode — every screen shows built-in sample data, not a vault. Connect to a vault to manage a real cache.",
    );
    notice.dataset.role = "demo-notice";
    nodes.push(notice);
  }
  const row = el("div", "act");
  const text = el("div");
  text.append(
    el("b", null, demo ? "Connect to a vault" : "Reconnect / switch account"),
    el(
      "span",
      null,
      demo
        ? "Enter your vault's API key to leave demo mode."
        : "Run the first-launch flow again to change the vault API key or the Steam relay identity.",
    ),
  );
  const btn = el("button", "btn", demo ? "Connect" : "Start");
  btn.type = "button";
  btn.dataset.role = "connect";
  btn.addEventListener("click", () => openOnboarding({ mode: "reconnect" }));
  row.append(text, btn);
  nodes.push(row);
  const card = fcard(CARD_TITLES.connection, ...nodes);
  card.dataset.group = "connection";
  return card;
}

// ---------------------------------------------------------------------
// About section (WP WEB-FEAT-3: GET /v1/about)
// ---------------------------------------------------------------------

/** Module state, survives re-mounts like `state` above. `phase`:
 * "idle" | "loading" | "loaded" | "too_old" | "error". `components` is
 * kept across a failed Refresh (the last good table stays visible under
 * the error line); a "too old" answer clears it. */
const about = { phase: "idle", components: null, error: null, gen: 0 };
/** Component name -> the user's open/closed choice for its (i) details
 * (WP WEB-FIX-8). Module state, so a Refresh (which rebuilds the table)
 * keeps it; without a choice the row's `infoOpenByDefault` applies. */
const aboutInfoOpen = new Map();
/** The About section's live nodes for the CURRENT mount, or null. */
let aboutEls = null;
/** Set by the rail's version button (requestAboutFocus) before it
 * navigates here: the next full render moves focus to the About heading. */
let focusAboutOnRender = false;

/**
 * Ask the Settings view to bring the About section into view and focus its
 * heading on its next render. The rail footer's version button calls this
 * right before `navigateTo("settings")` (app.js).
 */
export function requestAboutFocus() {
  focusAboutOnRender = true;
}

function setRefreshBusy(busy) {
  if (!aboutEls) return;
  // aria-disabled + a click guard instead of `disabled`, so keyboard focus
  // stays on the button across a refresh (docs/LEARNINGS.md, WP WEB-FEAT-1).
  if (busy) aboutEls.refreshBtn.setAttribute("aria-disabled", "true");
  else aboutEls.refreshBtn.removeAttribute("aria-disabled");
}

function aboutAnnouncement() {
  if (about.phase === "loaded") return "Component versions refreshed.";
  if (about.phase === "too_old") return ABOUT_TOO_OLD_MESSAGE;
  if (about.phase === "error") return `Could not refresh: ${about.error}`;
  return "";
}

/**
 * Fetch `GET /v1/about`. One request at a time: a call while one is in
 * flight is ignored (that one will paint whichever mount is current when
 * it lands). `announce` is true only for the Refresh button: the role=status
 * line then says what happened, because a refresh within the server's 60 s
 * cache can return an identical table.
 */
async function loadAbout({ announce = false } = {}) {
  if (about.phase === "loading") return;
  const gen = ++about.gen;
  about.phase = "loading";
  setRefreshBusy(true);
  if (announce && aboutEls) aboutEls.status.textContent = "Refreshing component versions…";
  paintAbout();
  try {
    const response = await api.about();
    if (gen !== about.gen) return;
    const components = aboutComponents(response);
    if (!components) throw new Error("the server's answer has no component list");
    about.components = components;
    about.error = null;
    about.phase = "loaded";
  } catch (err) {
    if (gen !== about.gen) return;
    if (classifyAboutError(err) === "too_old") {
      about.phase = "too_old";
      about.components = null;
      about.error = null;
    } else {
      about.phase = "error";
      about.error = errorText(err);
    }
  }
  setRefreshBusy(false);
  paintAbout();
  if (announce && aboutEls) aboutEls.status.textContent = aboutAnnouncement();
}

/** about-view.js tone -> kit pill tone: OK green, Unreachable red, every
 * other state the neutral pill (never red for "not reported"). */
const ABOUT_PILL_TONE = Object.freeze({ "tx-cached": "ok", "tx-error": "danger" });

/** One status cell: a kit pill wrapping the status icon; the glyph is
 * aria-hidden, the visible word is the accessible text. */
function buildStatusPill(view) {
  const pill = createPill({ kind: view.statusIcon, text: view.statusWord, tone: ABOUT_PILL_TONE[view.statusTone] ?? null });
  pill.dataset.role = "about-status";
  const icon = pill.querySelector(".sic");
  if (icon) icon.setAttribute("aria-hidden", "true");
  return pill;
}

function cellWithTitle(tag, className, cell) {
  const node = el(tag, className, cell.text);
  if (cell.title && cell.title !== cell.text) node.setAttribute("title", cell.title);
  // WP WEB-FIX-8: a value the component does not report is a dash; name it
  // for screen readers (the visible dash itself is hidden from them).
  if (cell.missing) {
    node.setAttribute("aria-hidden", "true");
    const wrap = el("span");
    wrap.append(node, el("span", "sr-only", DASH_LABEL));
    return wrap;
  }
  return node;
}

/** A DOM id from a component name (letters, digits and dashes only). */
function aboutInfoId(name) {
  return "about-info-" + name.toLowerCase().replace(/[^a-z0-9-]+/g, "-");
}

/** The (i) glyph of the About rows: a 24-unit outline circle with an "i",
 * stroked in currentColor, decorative (the button carries the name). */
function infoGlyph() {
  const svg = document.createElementNS(SVG_NS, "svg");
  const attrs = {
    viewBox: "0 0 24 24",
    "aria-hidden": "true",
    focusable: "false",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "2",
    "stroke-linecap": "round",
  };
  for (const k in attrs) svg.setAttribute(k, attrs[k]);
  const parts = [
    ["circle", { cx: "12", cy: "12", r: "9" }],
    ["path", { d: "M12 11v6" }],
    ["path", { d: "M12 7.5v.01" }],
  ];
  for (const [tag, a] of parts) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const k in a) node.setAttribute(k, a[k]);
    svg.appendChild(node);
  }
  return svg;
}

/**
 * The per-row (i) disclosure (WP WEB-FIX-8): a native <button> with
 * aria-expanded + aria-controls flipping `hidden` on the details row. Until
 * the user chooses, a "Check" row starts open (its reason must not hide
 * behind the (i)) and every other row collapsed; the choice survives a
 * Refresh.
 */
function buildInfoButton(name, detailRow, openByDefault) {
  const btn = el("button", "about-info-btn");
  btn.type = "button";
  btn.dataset.role = "about-info";
  btn.setAttribute("aria-label", `Details for ${name}`);
  btn.setAttribute("aria-controls", detailRow.id);
  btn.appendChild(infoGlyph());
  const isOpen = () => (aboutInfoOpen.has(name) ? aboutInfoOpen.get(name) : openByDefault);
  const apply = (open) => {
    btn.setAttribute("aria-expanded", String(open));
    detailRow.hidden = !open;
  };
  apply(isOpen());
  btn.addEventListener("click", () => {
    const open = !isOpen();
    aboutInfoOpen.set(name, open);
    apply(open);
  });
  return btn;
}

/**
 * The component table. Explicit ARIA table roles: below BP-M the CSS turns
 * every row into a stacked block (app.css "About table"), and browsers drop
 * native table semantics from a `display:block` table — the roles keep them.
 */
function buildAboutTable(components) {
  const table = el("table", "about-table");
  table.setAttribute("role", "table");
  table.setAttribute("aria-label", "Component versions");
  const thead = el("thead");
  thead.setAttribute("role", "rowgroup");
  const headRow = el("tr");
  headRow.setAttribute("role", "row");
  for (const label of ["Component", "Version", "Commit", "Status"]) {
    const th = el("th", null, label);
    th.setAttribute("scope", "col");
    th.setAttribute("role", "columnheader");
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  const tbody = el("tbody");
  tbody.setAttribute("role", "rowgroup");
  for (const view of describeComponents(components)) {
    const tr = el("tr", "about-row");
    tr.setAttribute("role", "row");
    tr.dataset.component = view.name;
    const nameCell = el("th", "about-name");
    nameCell.setAttribute("scope", "row");
    nameCell.setAttribute("role", "rowheader");
    // The row header is named by the name span alone, so the (i) button's
    // own label never joins it (review nit).
    const nameText = el("span", "about-name-text", view.name);
    nameText.id = aboutInfoId(view.name).replace("about-info-", "about-name-");
    nameCell.setAttribute("aria-labelledby", nameText.id);
    nameCell.appendChild(nameText);
    const versionCell = el("td", "about-version");
    versionCell.setAttribute("role", "cell");
    versionCell.dataset.label = "Version";
    versionCell.appendChild(cellWithTitle("span", "mono", view.version));
    const commitCell = el("td", "about-commit");
    commitCell.setAttribute("role", "cell");
    commitCell.dataset.label = "Commit";
    commitCell.appendChild(cellWithTitle("span", "mono", view.commit));
    const statusCell = el("td", "about-status");
    statusCell.setAttribute("role", "cell");
    statusCell.dataset.label = "Status";
    statusCell.appendChild(buildStatusPill(view));
    tr.append(nameCell, versionCell, commitCell, statusCell);
    tbody.appendChild(tr);

    // WP WEB-FIX-8: every explanation sits behind the row's (i) button,
    // collapsed by default — note, dash reason, then the server's detail.
    if (view.info.length > 0) {
      const detailRow = el("tr", "about-detail");
      detailRow.setAttribute("role", "row");
      detailRow.dataset.detailFor = view.name;
      detailRow.id = aboutInfoId(view.name);
      const td = el("td");
      td.setAttribute("role", "cell");
      td.colSpan = 4;
      if (view.note) td.appendChild(el("p", "hint", view.note));
      if (view.dashNote) td.appendChild(el("p", "hint", view.dashNote));
      // Server text, rendered with textContent (el() never parses markup).
      if (view.detail) {
        const d = el("p", "hint", view.detail);
        d.dataset.role = "about-detail";
        td.appendChild(d);
      }
      detailRow.appendChild(td);
      nameCell.appendChild(buildInfoButton(view.name, detailRow, view.infoOpenByDefault));
      tbody.appendChild(detailRow);
    }
  }
  table.append(thead, tbody);
  return table;
}

function paintAboutChecked() {
  if (!aboutEls) return;
  const text = about.components ? checkedText(about.components, Date.now()) : null;
  aboutEls.checked.hidden = !text;
  aboutEls.checked.textContent = text || "";
}

/** Repaint the About content area (never the heading or the Refresh
 * button, so focus survives a refresh). */
function paintAbout() {
  if (!aboutEls) return;
  const nodes = [];
  if (about.phase === "too_old") {
    const note = el("p", "hint", ABOUT_TOO_OLD_MESSAGE);
    note.dataset.role = "about-too-old";
    nodes.push(note);
  } else {
    if (about.phase === "error") {
      const line = el("p", "errline", `Could not load component versions: ${about.error}`);
      line.dataset.role = "about-error";
      nodes.push(line);
    }
    if (about.components) {
      nodes.push(buildAboutTable(about.components));
    } else if (about.phase === "loading" || about.phase === "idle") {
      nodes.push(el("p", "empty", "Loading component versions…"));
    }
  }
  aboutEls.content.replaceChildren(...nodes);
  paintAboutChecked();
}

/** The About panel: one card whose head row has the "Checked …" line on
 * the left and Refresh on the right, then the table and the trademark
 * note. */
function buildAboutSection() {
  // Its heading is the focus target of the rail's version button
  // (fullRender selects this section first).
  const { panel, title: heading } = buildSection("about");

  const head = el("div", "about-head");
  const left = el("div", "about-head-text");
  const checked = el("p", "hint");
  checked.dataset.role = "about-checked";
  // The one live region of this section: written only by a Refresh click.
  const status = el("p", "hint about-status-line");
  status.setAttribute("role", "status");
  status.dataset.role = "about-refresh-status";
  left.append(checked, status);
  const refreshBtn = el("button", "btn sm", "Refresh");
  refreshBtn.type = "button";
  refreshBtn.dataset.role = "about-refresh";
  refreshBtn.addEventListener("click", () => {
    if (refreshBtn.getAttribute("aria-disabled") === "true") return;
    loadAbout({ announce: true });
  });
  head.append(left, refreshBtn);

  const content = el("div", "about-content");
  const card = fcard(
    null,
    head,
    content,
    el(
      "p",
      "hint",
      "SteamHangar is a community project and is not affiliated with Valve Corporation. “Steam” is a trademark of Valve Corporation.",
    ),
  );
  card.dataset.group = "about";
  panel.append(card);
  aboutEls = { heading, panel, refreshBtn, status, content, checked };
  setRefreshBusy(about.phase === "loading");
  paintAbout();
  return panel;
}

// The settings form is not polled, but the About "checked ... ago" text
// follows the store's clients ticks (every 20 s): text only, nothing
// rebuilt. An `{error}` tick keeps the last text. (The agents line moved to
// the Clients page with the PC management, WP D15c.)
store.subscribe("clients", (payload) => {
  if (!mounted() || !payload || payload.error) return;
  paintAboutChecked();
});

// ---------------------------------------------------------------------
// Top-level render
// ---------------------------------------------------------------------

function resetPanelState() {
  els.dots = new Map();
  els.sectionKeys = new Map();
  els.panels = new Map();
  els.navInfo = new Map();
  els.navItems = new Map();
  els.gcWarn = null;
  els.expertRow = null;
  els.navEl = null;
}

function fullRender() {
  if (!mounted()) return;
  els.body.replaceChildren();
  // The save bar is rebuilt below (or not at all: loading, error, read-only).
  els.saveBar = null;
  els.section.classList.remove("savebar-up");
  resetPanelState();

  if (state.loading) {
    els.body.appendChild(el("p", "empty", "Loading settings…"));
    return;
  }
  if (state.loadError) {
    els.body.appendChild(el("p", "errline", `Could not load settings: ${state.loadError}`));
    // WP WEB-FIX-1 (B2): the reconnect entry must survive a failed load —
    // before this line the early return hid the one control that fixes the
    // most likely cause (a 401 from a rotated key).
    els.body.append(buildConnectionCard());
    focusAboutOnRender = false; // no About section in the error state
    return;
  }

  if (state.settingsResponse.readonly) {
    els.body.appendChild(
      el(
        "p",
        "hint",
        "This vault-api is running with VAULT_SETTINGS_READONLY set — values below are shown for reference and cannot be changed here.",
      ),
    );
  }

  // The rail's version button asks for About: select it before building.
  if (focusAboutOnRender) activeSection = "about";
  else if (activeSection === "expert" && !expertOn) activeSection = DEFAULT_SECTION;

  const placed = placeEntries(state.settingsResponse.settings);
  const main = el("div", "settings-main");
  main.append(buildVaultSection(placed.sections.vault));
  const sections = [
    ["window", { gridExtra: buildLastSweepField() }],
    ["disk", { below: [buildGcWarn()] }],
    ["notify", {}],
    ["other", {}],
  ];
  for (const [id, extra] of sections) {
    const node = buildFormSection(id, placed.sections[id], extra);
    if (node) main.append(node);
  }

  const steam = buildSection("steam", { summaryText: sectionSummary("steam", entryByKey) });
  steam.panel.append(buildSteamIdentityCard(), buildSteamLibraryCard());
  main.append(steam.panel);
  renderSteamStatusLine();
  renderLookupResult();

  main.append(buildAboutSection());

  const expertPanel = buildExpertPanel(placed.expert);
  if (expertPanel) main.append(expertPanel);

  // WP WEB-FIX-8 / SETTINGS-2: the save bar is the last node of the right
  // column (so the last in the Tab order, after every field incl. the
  // Expert section) and sticks to the bottom of the viewport while the
  // column is in view. Never built read-only.
  if (!state.settingsResponse.readonly) main.append(buildSaveBar());

  const lay = el("div", "settings-lay");
  buildExpertSwitch(placed.expert);
  lay.append(buildNav(), main);
  placeExpertSwitch();
  els.body.append(lay);
  selectSection(els.panels.has(activeSection) ? activeSection : DEFAULT_SECTION);
  markDirty();

  if (focusAboutOnRender) {
    focusAboutOnRender = false;
    aboutEls.heading.focus();
    if (typeof aboutEls.heading.scrollIntoView === "function") aboutEls.heading.scrollIntoView({ block: "start" });
  }
}

async function loadSettings() {
  state.loading = true;
  state.loadError = null;
  fullRender();
  try {
    // WP 4d-web review fix (S1): GET /v1/schedule joins this SAME
    // Promise.all with its own `.catch(() => null)`, so a stalled or failed
    // /v1/schedule never gates the first paint of the screen and never
    // turns into "Could not load settings" — it only means the "Last sweep"
    // field has nothing to show.
    const [settingsResponse, steamStatus, schedule] = await Promise.all([
      api.getSettings(),
      api.getSteamKey(),
      api.schedule().catch(() => null),
    ]);
    // WP WEB-FIX-1 (P2): a response lacking one of the required keys fails
    // into the same error path a rejected fetch takes, naming the gap,
    // instead of throwing inside fullRender() after this try/catch.
    const missing = missingSettingKeys(settingsResponse);
    if (missing.length > 0) {
      throw new Error(`the server's settings response is missing: ${missing.join(", ")}`);
    }
    state.settingsResponse = settingsResponse;
    state.steamStatus = steamStatus;
    state.schedule = schedule;
    state.loading = false;
  } catch (err) {
    state.loading = false;
    state.loadError = errorText(err);
  }
  fullRender();
}

// ---------------------------------------------------------------------
// View lifecycle
// ---------------------------------------------------------------------

onViewChange((view) => {
  if (view === "settings") return;
  sectionEl = null;
  aboutEls = null;
  if (resizeObserver) resizeObserver.disconnect();
  resizeObserver = null;
  // A rail click that navigated here and then away before Settings loaded
  // must not move focus on some later, unrelated visit (review nit).
  focusAboutOnRender = false;
});

export function renderSettings() {
  // `kit`: this page composes from css/kit.css (ADR-0028, WP SETTINGS-2).
  const section = el("section", "view view-settings kit");
  const head = el("div", "page-head");
  const title = el("div");
  const h1 = el("h1", null, "Settings");
  // Focus target after Save/Discard removed the focused bar (WP WEB-FIX-8);
  // never in the Tab order.
  h1.tabIndex = -1;
  title.append(h1, el("div", "sub", PAGE_SUBLINE));
  head.append(title);
  const body = el("div", "settings-body");
  section.append(head, body);

  sectionEl = section;
  els = { section, heading: h1, body };
  resetPanelState();
  aboutEls = null;
  drafts = {};
  activeSection = DEFAULT_SECTION;
  expertOn = readExpertSwitch(getStorage);
  observeWidth(section);
  loadSettings();
  // WP WEB-FEAT-3: About loads when Settings opens (in parallel with the
  // settings form), never on a timer.
  loadAbout();
  return section;
}
