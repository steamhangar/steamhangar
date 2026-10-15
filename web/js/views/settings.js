/**
 * Settings view (WP 4a.6; sections, expert mode and env pins WP D16-WEB).
 *
 * Layout (WP D16-WEB, docs/handover/d16-settings-audit.md 1.3 and the D16a
 * table sign-off 2026-10-14): collapsible sections as native
 * `<details>/<summary>` — Vault & connection (open by default), Download
 * window & updates, Disk & cleanup, Notifications, Steam, Expert, About.
 * A collapsed section shows a one-line state in its summary
 * (`sectionSummary`, same-section fields only) and a dot while it holds an
 * unsaved draft, so a draft is never hidden. Open state is remembered per
 * browser (guarded storage). Placement and wording live in
 * lib/settings-layout.js; a section with nothing to show is not built.
 *
 * - Normal page: only rows the API marks `expert: false`. Every
 *   `expert: true` row sits in the Expert section, which exists only while
 *   the separate "Show expert settings" switch is on (remembered per
 *   browser), split into Bandwidth & connections, Housekeeping and Debug.
 *   Switching it off hides the section but keeps its drafts (the switch row
 *   then shows the unsaved-changes dot).
 * - Env pins (Q1 2026-10-14: env always wins): a row with `editable: false`
 *   or `env_only: true` is plain text with a "Set via env (VAULT_…)" note —
 *   no input, no Reset. `shadowed` adds a note that a stored value is
 *   overridden. The masked webhook URL is such a row. (The
 *   VAULT_SETTINGS_READONLY mode keeps its disabled inputs: another state.)
 * - Explanations sit behind a per-field (i) disclosure (the About table's
 *   button, WP WEB-FIX-8); state (source and apply caption, sweep status,
 *   GC-risk warning, env notes) stays visible.
 * - Risky keys (API `risky: true`): Save opens one confirm dialog naming
 *   each risky key with old -> new value and what can break
 *   (components/risky-confirm.js); only "Save anyway" sends the PATCH, with
 *   `?confirm_risky=true`. A 428 the client did not foresee opens the same
 *   dialog for the keys the server names, then retries once. 409/422/428
 *   answers say "Nothing was saved" (the API validates the whole body
 *   first); anything else does not claim to know.
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
 *  - PCs — a pointer to the Clients page (WP D15c).
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
  EXPERT_GROUPS,
  coreApplyLine,
  SECTIONS,
  describeSaveError,
  displayValue,
  envNote,
  fieldHelp,
  fieldMeta,
  isPinned,
  isSectionOpen,
  placeEntries,
  readExpertSwitch,
  readOpenState,
  riskyItems,
  riskyKeysFrom428,
  riskyKeysInBody,
  sectionSummary,
  shadowNote,
  writeExpertSwitch,
  writeOpenState,
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
import { navigateTo, onViewChange } from "../router.js";
import { store } from "../store-singleton.js";
import { createStatusIcon } from "../components/status-icon.js";
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
  // failure only means the sweep status line and GC-risk warning are empty).
  schedule: null,
};

/** {[key]: {reset: true} | {value: string | string[]}} — only ever
 * populated by an actual user edit; see the module header. */
let drafts = {};

/** Remembered open sections, re-read on every mount. */
let openState = {};
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
 * Show the save bar exactly while something is dirty (WP WEB-FIX-8: the
 * bar is `position:fixed` above the bottom nav, app.css "Settings save
 * bar"). `.savebar-up` on the section reserves scroll room under the
 * page's last content. Any edit clears a previous save error from the bar.
 */
function markDirty() {
  paintDirtyDots();
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
    // WP 4d-web: a saved PATCH can change sweep_cached_gc_risk server-side,
    // so re-fetch the schedule (best-effort: stale beats a failed save).
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
    // failed (no warn toast on top: at BP-L the toast would sit over it).
    setBarLine(describeSaveError(err, { confirmed }), true);
  } finally {
    saving = false;
    if (mounted() && els.saveBtn) els.saveBtn.removeAttribute("aria-disabled");
  }
}

// ---------------------------------------------------------------------
// (i) disclosure — the About table's button (WP WEB-FIX-8), shared by the
// field help texts (WP D16-WEB).
// ---------------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

/** The (i) glyph: a 24-unit outline circle with an "i", stroked in
 * currentColor like the status-icon glyphs, decorative (the button
 * carries the name). */
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
 * A disclosure button: a native <button> (Enter/Space and the global
 * :focus-visible ring for free) with aria-expanded + aria-controls; a click
 * flips `hidden` on `detailEl` and records the choice in `memory` under
 * `name`, so a re-render keeps it.
 */
function buildDisclosure({ name, detailEl, openByDefault, memory, role, label }) {
  const btn = el("button", "about-info-btn");
  btn.type = "button";
  btn.dataset.role = role;
  btn.setAttribute("aria-label", label);
  btn.setAttribute("aria-controls", detailEl.id);
  btn.appendChild(infoGlyph());
  const apply = (open) => {
    btn.setAttribute("aria-expanded", String(open));
    detailEl.hidden = !open;
  };
  const isOpen = () => (memory.has(name) ? memory.get(name) : openByDefault);
  apply(isOpen());
  btn.addEventListener("click", () => {
    const open = !isOpen();
    memory.set(name, open);
    apply(open);
  });
  return btn;
}

/** The (i) button plus its hidden text block for one help id. */
function helpDisclosure(id, paragraphs, label) {
  const help = el("div", "field-help");
  help.id = `${id}-help`;
  help.dataset.role = "field-help";
  for (const text of paragraphs) help.appendChild(el("p", "foot-note", text));
  const btn = buildDisclosure({
    name: id,
    detailEl: help,
    openByDefault: false,
    memory: fieldInfoOpen,
    role: "field-info",
    label: `More about ${label}`,
  });
  return { btn, help };
}

// ---------------------------------------------------------------------
// One field. Pinned rows (env / env-only) are plain text; everything else
// is a text input, a segmented group or the event checkboxes, each with a
// "<source> · <applies>" caption and a Reset button for a stored value.
// ---------------------------------------------------------------------

function draftValue(key) {
  return drafts[key] && "value" in drafts[key] ? drafts[key].value : undefined;
}

function setDraft(key, draft) {
  drafts[key] = draft;
  markDirty();
}

function buildCaption(entry) {
  const caption = el("p", "foot-note", `${sourceLabel(entry.source)} · ${appliesText(entry.applies)}`);
  caption.dataset.role = "field-caption";
  return caption;
}

function buildResetButton(entry, onReset) {
  const resetBtn = el("button", "btn ghost sm field-reset", "Reset");
  resetBtn.type = "button";
  resetBtn.dataset.role = "field-reset";
  resetBtn.hidden = !canReset(entry) || readonly();
  resetBtn.addEventListener("click", () => {
    onReset();
    setDraft(entry.key, { reset: true });
  });
  return resetBtn;
}

/** Plain text for an env-pinned or env-only row (Q1/Q2): no input. */
function buildPinnedBody(field, entry) {
  const value = el("p", "setting-value", displayValue(entry));
  value.dataset.role = "setting-value";
  const note = el("p", "foot-note", envNote(entry));
  note.dataset.role = "env-note";
  field.append(value, note);
  const shadow = shadowNote(entry);
  if (shadow) {
    const s = el("p", "foot-note", shadow);
    s.dataset.role = "shadow-note";
    field.append(s);
  }
}

function buildTextBody(field, entry, meta, fieldId) {
  const input = document.createElement("input");
  input.id = fieldId;
  input.className = "inp txt";
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  if (meta.placeholder) input.placeholder = meta.placeholder;
  input.value = effectiveAsInputValue(entry);
  input.disabled = readonly();
  input.addEventListener("input", () => setDraft(entry.key, { value: input.value }));
  field.append(input, buildCaption(entry));
  field.append(
    buildResetButton(entry, () => {
      input.value = effectiveAsInputValue({ ...entry, effective: entry.fallback });
    }),
  );
}

function buildSegsBody(field, entry, meta, labelId) {
  const segs = el("div", "segs");
  segs.setAttribute("role", "group");
  segs.setAttribute("aria-labelledby", labelId);
  const current = draftValue(entry.key) ?? String(entry.effective);
  const buttons = [];
  const press = (value) => {
    for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.value === value));
  };
  for (const [value, label] of meta.options) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.dataset.value = value;
    btn.disabled = readonly();
    btn.addEventListener("click", () => {
      press(value);
      setDraft(entry.key, { value });
    });
    buttons.push(btn);
    segs.appendChild(btn);
  }
  press(current);
  field.append(segs, buildCaption(entry));
  field.append(buildResetButton(entry, () => press(String(entry.fallback))));
}

function buildEventsBody(field, entry, meta) {
  const selected = new Set(draftValue(entry.key) ?? (Array.isArray(entry.effective) ? entry.effective : []));
  const checkboxes = [];
  for (const [value, label] of meta.options) {
    const row = el("label", "srow");
    row.style.cursor = "pointer";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = selected.has(value);
    cb.disabled = readonly();
    cb.dataset.event = value;
    cb.addEventListener("change", () => {
      setDraft(entry.key, { value: checkboxes.filter((c) => c.checked).map((c) => c.dataset.event) });
    });
    checkboxes.push(cb);
    const grow = el("span", "grow");
    grow.append(el("span", "ttl", label));
    row.append(cb, grow);
    field.appendChild(row);
  }
  field.append(buildCaption(entry));
  field.append(
    buildResetButton(entry, () => {
      const back = new Set(Array.isArray(entry.fallback) ? entry.fallback : []);
      for (const cb of checkboxes) cb.checked = back.has(cb.dataset.event);
    }),
  );
}

function buildSettingField(entry) {
  const meta = fieldMeta(entry);
  const fieldId = `settings-${entry.key}`;
  const pinned = isPinned(entry);
  const field = el("div", pinned ? "field is-pinned" : "field");
  field.dataset.key = entry.key;
  const head = el("div", "field-head");
  let labelEl;
  if (!pinned && meta.kind === "text") {
    labelEl = el("label", null, meta.label);
    labelEl.htmlFor = fieldId;
  } else {
    labelEl = el("span", "field-label", meta.label);
  }
  labelEl.id = `${fieldId}-label`;
  head.append(labelEl);
  field.append(head);
  if (meta.kind === "events" && !pinned) {
    field.setAttribute("role", "group");
    field.setAttribute("aria-labelledby", labelEl.id);
  }
  const paragraphs = fieldHelp(entry);
  let help = null;
  if (paragraphs.length > 0) {
    const d = helpDisclosure(fieldId, paragraphs, meta.label);
    head.append(d.btn);
    help = d.help;
  }
  if (pinned) buildPinnedBody(field, entry);
  else if (meta.kind === "segs" && meta.options) buildSegsBody(field, entry, meta, labelEl.id);
  else if (meta.kind === "events" && meta.options) buildEventsBody(field, entry, meta);
  else buildTextBody(field, entry, meta, fieldId);
  if (help) field.append(help);
  return field;
}

// ---------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------

/**
 * One collapsible section. `keys` are the settings keys it carries (for the
 * unsaved-changes dot); `summaryText` is the collapsed one-liner.
 */
function buildSection(id, { summaryText = "", keys = [], summaryId } = {}) {
  const meta = SECTIONS.find((s) => s.id === id);
  const details = el("details", "settings-sec");
  details.dataset.section = id;
  details.open = isSectionOpen(openState, id);
  const summary = el("summary", "sec-summary");
  if (summaryId) summary.id = summaryId;
  // A real heading inside <summary> (allowed: one heading element), so
  // heading navigation still reaches every section title.
  summary.append(el("h2", "sec-title", meta.title));
  if (summaryText) {
    const st = el("span", "sec-state", summaryText);
    st.dataset.role = "section-state";
    summary.append(st);
  }
  if (keys.length > 0) {
    const dot = el("span", "sec-dirty");
    dot.dataset.role = "section-dirty";
    dot.hidden = true;
    dot.append(el("span", "sr-only", "(unsaved changes)"));
    summary.append(dot);
    els.dots.set(id, dot);
    els.sectionKeys.set(id, new Set(keys));
  }
  details.addEventListener("toggle", () => {
    // Opened by the rail's version button: not the user's choice, so not
    // remembered (the browser fires this toggle asynchronously).
    if (id === "about" && aboutOpenedByRail) {
      aboutOpenedByRail = false;
      return;
    }
    openState = { ...openState, [id]: details.open };
    writeOpenState(getStorage, openState);
  });
  const body = el("div", "sec-body");
  details.append(summary, body);
  els.sectionsById.set(id, details);
  return { details, body };
}

/** A form section from its placed rows; null when it has none. */
function buildFormSection(id, rows, extra) {
  if (rows.length === 0 && !extra) return null;
  const { details, body } = buildSection(id, {
    summaryText: sectionSummary(id, entryByKey, rows),
    keys: rows.map((e) => e.key),
  });
  for (const entry of rows) body.appendChild(buildSettingField(entry));
  if (extra) body.append(extra);
  return details;
}

/**
 * The "did the last scheduled sweep do anything" line and the
 * cached-sweep GC-risk warning (WP 4d-web), from `GET /v1/schedule`
 * verbatim. The warning is one visible sentence; its explanation sits
 * behind (i). Renders nothing while the schedule is unknown.
 */
function buildSweepStatusBlock() {
  const wrap = document.createDocumentFragment();
  const statusText = sweepTargetsMessage(state.schedule);
  if (statusText) {
    const p = el("p", "foot-note", statusText);
    p.dataset.role = "sweep-status";
    wrap.append(p);
  }
  const riskText = cachedSweepGcRiskWarning(state.schedule);
  if (riskText) {
    const warn = el("div", "settings-warn");
    warn.dataset.role = "gc-risk";
    const head = el("div", "field-head");
    head.append(el("span", null, CACHED_SWEEP_GC_RISK_SHORT));
    const d = helpDisclosure("settings-gc-risk", [riskText], "this warning");
    head.append(d.btn);
    warn.append(head, d.help);
    wrap.append(warn);
  }
  return wrap;
}

/** Vault & connection: vault name (+ any other row placed here), the
 * Connection row and the PCs pointer. Always built. */
function buildVaultSection(rows) {
  const { details, body } = buildSection("vault", {
    summaryText: sectionSummary("vault", entryByKey, rows),
    keys: rows.map((e) => e.key),
  });
  for (const entry of rows) body.appendChild(buildSettingField(entry));
  body.append(buildConnectionSection(), buildPcsSection());
  return details;
}

/** The Expert switch row + the Expert section (hidden while off). */
function buildExpertArea(groups) {
  const all = EXPERT_GROUPS.flatMap((g) => groups[g.id]);
  if (all.length === 0) return null;
  const wrap = document.createDocumentFragment();

  const row = el("div", "srow expert-switch");
  const grow = el("span", "grow");
  const ttl = el("span", "ttl", "Show expert settings");
  ttl.id = "settings-expert-switch-label";
  grow.append(ttl, el("span", "desc", "Bandwidth and connections, housekeeping and debug settings."));
  const dot = el("span", "sec-dirty");
  dot.dataset.role = "expert-dirty";
  dot.hidden = true;
  dot.append(el("span", "sr-only", "(unsaved expert changes)"));
  const sw = el("button", "toggle");
  sw.type = "button";
  sw.dataset.role = "expert-switch";
  sw.setAttribute("role", "switch");
  sw.setAttribute("aria-labelledby", ttl.id);
  sw.setAttribute("aria-checked", String(expertOn));
  row.append(grow, dot, sw);
  els.dots.set("expert-switch", dot);
  els.sectionKeys.set("expert-switch", new Set(all.map((e) => e.key)));

  const { details, body } = buildSection("expert", {
    summaryText: sectionSummary("expert", entryByKey, all),
    keys: all.map((e) => e.key),
  });
  details.hidden = !expertOn;
  // D16c: what vault-core reported about the core settings (state only).
  const apply = coreApplyLine(state.settingsResponse.core_apply);
  if (apply) {
    const line = el("p", apply.warn ? "settings-warn" : "foot-note", apply.text);
    line.dataset.role = "core-apply";
    body.appendChild(line);
  }
  for (const group of EXPERT_GROUPS) {
    const rows = groups[group.id];
    if (rows.length === 0) continue;
    const sub = el("div", "expert-group");
    sub.dataset.group = group.id;
    sub.append(el("h4", "sec", group.title));
    for (const entry of rows) sub.appendChild(buildSettingField(entry));
    body.appendChild(sub);
  }
  sw.addEventListener("click", () => {
    expertOn = !expertOn;
    writeExpertSwitch(getStorage, expertOn);
    sw.setAttribute("aria-checked", String(expertOn));
    details.hidden = !expertOn;
    if (expertOn && !details.open) details.open = true;
  });
  wrap.append(row, details);
  return wrap;
}

// ---------------------------------------------------------------------
// Steam identity section (WP 4a.6r relay)
// ---------------------------------------------------------------------

function renderSteamStatusLine() {
  const { statusLine, removeBtn } = els.steam;
  if (state.steamStatus.configured) {
    statusLine.textContent = `Relay key configured (••••${state.steamStatus.key_last4}).`;
    removeBtn.hidden = false;
  } else {
    statusLine.textContent = NO_STEAM_KEY_MESSAGE;
    removeBtn.hidden = true;
  }
}

function renderLookupResult() {
  const { lookupBody } = els.steam;
  lookupBody.replaceChildren();
  if (!state.lookup) return;
  if (state.lookup.error) {
    lookupBody.appendChild(el("p", "errline", state.lookup.error));
    return;
  }
  if (state.lookup.persona) {
    lookupBody.appendChild(
      el(
        "p",
        "foot-note",
        `Signed in as ${state.lookup.persona.personaname} · SteamID64 ${state.lookup.persona.steamid}`,
      ),
    );
  }
  lookupBody.appendChild(el("p", "foot-note", `${state.lookup.gameCount} games found.`));
  if (state.lookup.gameCount === 0) lookupBody.appendChild(el("p", "foot-note", PRIVATE_PROFILE_MESSAGE));
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

function buildSteamSection() {
  const wrap = document.createDocumentFragment();
  // D16a-2: the explanation sits behind (i); the status line below is state.
  const steamHelp = helpDisclosure("settings-steam-identity", [STEAM_IDENTITY_HELP], "Steam identity");
  const steamHead = el("h4", "sec", "Steam identity");
  steamHead.append(steamHelp.btn);
  wrap.append(steamHead, steamHelp.help);

  const statusLine = el("p", "foot-note", "");

  const keyField = el("div", "field");
  const keyLabel = el("label", null, "Web API key (32 hex characters)");
  keyLabel.htmlFor = "settings-steam-key";
  keyField.append(keyLabel);
  const keyInput = document.createElement("input");
  keyInput.id = "settings-steam-key";
  keyInput.className = "inp txt";
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.spellcheck = false;
  keyInput.placeholder = "from steamcommunity.com/dev/apikey";
  keyField.appendChild(keyInput);

  const saveBtn = el("button", "btn sm", "Save key");
  saveBtn.type = "button";
  const removeBtn = el("button", "btn ghost sm", "Remove key");
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

  const keyRow = document.createElement("div");
  keyRow.style.display = "flex";
  keyRow.style.gap = "8px";
  keyRow.append(saveBtn, removeBtn);
  wrap.append(keyField, keyRow, keyErr, statusLine);

  wrap.append(buildSteamLibraryBlock());
  els.steam = { ...els.steam, statusLine, removeBtn };
  return wrap;
}

/**
 * The library SteamID64 (WP WEB-FEAT-1): input pre-filled from the
 * `steam_library_steamid` setting, "Save" (its own PATCH, independent of the
 * shared Save bar above, like "Save key"), and "Preview" for the typed id.
 * Saving never rebuilds the rest of the form, so unsaved drafts elsewhere
 * on the screen survive it.
 */
function buildSteamLibraryBlock() {
  const wrap = document.createDocumentFragment();
  const libHelp = helpDisclosure("settings-steam-library", [STEAM_LIBRARY_HELP], "Steam library");
  const libHead = el("h4", "sec", "Steam library");
  libHead.append(libHelp.btn);
  wrap.append(libHead, libHelp.help);
  const entry = entryByKey(STEAM_LIBRARY_SETTING_KEY);
  const readonly = state.settingsResponse.readonly;
  // D16-WEB (Q1): set via env -> plain text, no input, no Save/Reset;
  // Preview still checks the pinned id.
  const pinned = !!entry && isPinned(entry);

  const idField = el("div", pinned ? "field is-pinned" : "field");
  let idInput = null;
  if (pinned) {
    idField.append(el("span", "field-label", "SteamID64"));
    buildPinnedBody(idField, entry);
  } else {
    const idLabel = el("label", null, "SteamID64");
    idLabel.htmlFor = "settings-steam-steamid";
    idField.append(idLabel);
    idInput = document.createElement("input");
    idInput.id = "settings-steam-steamid";
    idInput.className = "inp txt";
    idInput.type = "text";
    idInput.inputMode = "numeric";
    idInput.autocomplete = "off";
    idInput.spellcheck = false;
    idInput.placeholder = "76561198042117903";
    idInput.value = entry ? effectiveAsInputValue(entry) : "";
    // Never disabled: Preview checks whatever is typed, and must work on a
    // read-only vault and on an older vault-api without this setting too.
    // Only Save/Reset are gated (WEB-FEAT-1 review S3).
    idField.appendChild(idInput);
  }
  const typedId = () => (idInput ? idInput.value.trim() : String((entry && entry.effective) || "").trim());
  const caption = el("p", "foot-note");
  caption.dataset.role = "steamid-caption";
  idField.appendChild(caption);
  const idErr = el("p", "errline");
  idErr.dataset.role = "steamid-error";
  idErr.hidden = true;

  const saveBtn = el("button", "btn sm", "Save SteamID64");
  saveBtn.type = "button";
  saveBtn.dataset.role = "steamid-save";
  saveBtn.hidden = readonly || !entry || pinned;
  const resetBtn = el("button", "btn ghost sm", "Reset");
  resetBtn.type = "button";
  resetBtn.dataset.role = "steamid-reset";
  resetBtn.title = "Remove the override — back to the environment or default value";
  const lookupBtn = el("button", "btn ghost sm", "Preview");
  lookupBtn.type = "button";
  lookupBtn.dataset.role = "steamid-preview";
  const lookupBody = document.createElement("div");

  function paintCaption() {
    const current = entryByKey(STEAM_LIBRARY_SETTING_KEY);
    if (pinned) {
      caption.hidden = true;
      resetBtn.hidden = true;
      return;
    }
    caption.textContent = current
      ? `${sourceLabel(current.source)} · ${appliesText(current.applies)}`
      : `${LIBRARY_SETTING_ABSENT_MESSAGE} Preview still works.`;
    // Same rule as every other setting's Reset (settings-presentation.js's
    // canReset): only a `db` override has anything to clear.
    resetBtn.hidden = readonly || !current || !canReset(current);
  }
  paintCaption();

  function showIdError(text) {
    idErr.hidden = !text;
    idErr.textContent = text || "";
  }

  saveBtn.addEventListener("click", async () => {
    showIdError(null);
    if (!idInput) return;
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
      if (idInput) idInput.value = current ? effectiveAsInputValue(current) : "";
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

  const row = el("div", "btnrow");
  row.append(saveBtn, resetBtn, lookupBtn);
  wrap.append(idField, row, idErr, lookupBody);

  els.steam = { ...els.steam, lookupBody };
  return wrap;
}

// ---------------------------------------------------------------------
// Connection + About sections
// ---------------------------------------------------------------------

/**
 * Connection section — the ONLY manual way back into the connect flow, so
 * (WP WEB-FIX-1, B2) it renders in the load-error state too, not just under
 * a successful `GET /v1/settings` (a 401 from a rotated key is exactly when
 * it is needed). In demo mode (B1) it says so, in plain words, and offers
 * the connect action instead of a "reconnect" that would suggest a vault
 * is already attached — until this WP nothing in the UI ever cleared the
 * demo flag, so this was the door out of a one-way room.
 */
function buildConnectionSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "Connection"));
  const demo = isDemoMode();
  if (demo) {
    const notice = el(
      "p",
      "hint",
      "Demo mode — every screen shows built-in sample data, not a vault. Connect to a vault to manage a real cache.",
    );
    notice.dataset.role = "demo-notice";
    wrap.append(notice);
  }
  const row = el("div", "srow");
  const grow = el("span", "grow");
  grow.append(
    el("span", "ttl", demo ? "Connect to a vault" : "Reconnect / switch account"),
    el(
      "span",
      "desc",
      demo
        ? "Enter your vault's API key to leave demo mode."
        : "Run the first-launch flow again to change the vault API key or the Steam relay identity.",
    ),
  );
  const btn = el("button", "btn ghost sm", demo ? "Connect" : "Start");
  btn.type = "button";
  btn.dataset.role = "connect";
  btn.addEventListener("click", () => openOnboarding({ mode: "reconnect" }));
  row.append(grow, btn);
  wrap.append(row);
  return wrap;
}

// ---------------------------------------------------------------------
// PCs (agents) pointer (WP D15c)
// ---------------------------------------------------------------------

/** WP D15c: adding, pairing and removing PCs moved to the Clients page
 * (views/clients.js). Settings keeps a short pointer so the old way there
 * still leads somewhere. */
function buildPcsSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "PCs (agents)"));
  const row = el("div", "srow");
  const grow = el("span", "grow");
  grow.append(
    el("span", "ttl", "PCs and devices"),
    el("span", "desc", "Adding, pairing and removing PCs is on the Clients page now."),
  );
  const btn = el("button", "btn ghost sm", "Open Clients");
  btn.type = "button";
  btn.dataset.role = "open-clients";
  btn.addEventListener("click", () => navigateTo("clients"));
  row.append(grow, btn);
  wrap.append(row);
  return wrap;
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
/** True between the rail opening a closed About section and the toggle
 * event that opening fires (see buildSection). */
let aboutOpenedByRail = false;

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

/** One status cell: the badge pattern of the clients sheet — glyph
 * aria-hidden, the visible word is the accessible text. */
function buildStatusBadge(view) {
  const badge = el("span", `badge ${view.statusTone}`);
  badge.dataset.role = "about-status";
  const icon = createStatusIcon(view.statusIcon, { size: "sm" });
  icon.setAttribute("aria-hidden", "true");
  badge.append(icon, el("span", null, view.statusWord));
  return badge;
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

/**
 * The per-row (i) disclosure (WP WEB-FIX-8). Until the user chooses, a
 * "Check" row starts open (its reason must not hide behind the (i)) and
 * every other row collapsed; the choice survives a Refresh.
 */
function buildInfoButton(name, detailRow, openByDefault) {
  return buildDisclosure({
    name,
    detailEl: detailRow,
    openByDefault,
    memory: aboutInfoOpen,
    role: "about-info",
    label: `Details for ${name}`,
  });
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
    statusCell.appendChild(buildStatusBadge(view));
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
      if (view.note) td.appendChild(el("p", "foot-note", view.note));
      if (view.dashNote) td.appendChild(el("p", "foot-note", view.dashNote));
      // Server text, rendered with textContent (el() never parses markup).
      if (view.detail) {
        const d = el("p", "foot-note", view.detail);
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

function buildAboutSection() {
  // D16-WEB: a collapsible section like the others; its summary is the
  // focus target of the rail's version button (fullRender opens it first).
  const { details, body: wrap } = buildSection("about", { summaryId: "settings-about" });
  const heading = details.querySelector("summary");

  const head = el("div", "about-head");
  const refreshBtn = el("button", "btn ghost sm", "Refresh");
  refreshBtn.type = "button";
  refreshBtn.dataset.role = "about-refresh";
  refreshBtn.addEventListener("click", () => {
    if (refreshBtn.getAttribute("aria-disabled") === "true") return;
    loadAbout({ announce: true });
  });
  // The one live region of this section: written only by a Refresh click.
  const status = el("span", "foot-note about-status-line");
  status.setAttribute("role", "status");
  status.dataset.role = "about-refresh-status";
  head.append(refreshBtn, status);

  const content = el("div", "about-content");
  const checked = el("p", "foot-note");
  checked.dataset.role = "about-checked";
  wrap.append(head, content, checked);
  wrap.append(
    el(
      "p",
      "hint",
      "SteamHangar is a community project and is not affiliated with Valve Corporation. “Steam” is a trademark of Valve Corporation.",
    ),
  );
  aboutEls = { heading, details, refreshBtn, status, content, checked };
  setRefreshBusy(about.phase === "loading");
  paintAbout();
  return details;
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

function fullRender() {
  if (!mounted()) return;
  els.body.replaceChildren();
  // The save bar is rebuilt below (or not at all: loading, error, read-only).
  els.saveBar = null;
  els.section.classList.remove("savebar-up");
  els.dots = new Map();
  els.sectionKeys = new Map();
  els.sectionsById = new Map();

  if (state.loading) {
    els.body.appendChild(el("p", "empty", "Loading settings…"));
    return;
  }
  if (state.loadError) {
    els.body.appendChild(el("p", "errline", `Could not load settings: ${state.loadError}`));
    // WP WEB-FIX-1 (B2): the reconnect entry must survive a failed load —
    // before this line the early return hid the one control that fixes the
    // most likely cause (a 401 from a rotated key).
    els.body.append(buildConnectionSection());
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

  const placed = placeEntries(state.settingsResponse.settings);
  els.body.append(buildVaultSection(placed.sections.vault));
  for (const [id, extra] of [
    ["window", buildSweepStatusBlock()],
    ["disk", null],
    ["notify", null],
    ["other", null],
  ]) {
    // A fragment with no children counts as "nothing extra".
    const node = buildFormSection(id, placed.sections[id], extra && extra.childNodes.length > 0 ? extra : null);
    if (node) els.body.append(node);
  }

  const steam = buildSection("steam", { summaryText: sectionSummary("steam", entryByKey) });
  steam.body.append(buildSteamSection());
  els.body.append(steam.details);
  renderSteamStatusLine();
  renderLookupResult();

  const expertArea = buildExpertArea(placed.expert);
  if (expertArea) els.body.append(expertArea);

  // WP WEB-FIX-8: the save bar keeps its place in the DOM (and so in the
  // Tab order) right after the form it saves, but is `position:fixed`
  // above the bottom nav (app.css "Settings save bar"), so it is visible
  // without scrolling while anything is dirty. Never built read-only.
  if (!state.settingsResponse.readonly) {
    els.saveBar = el("div", "savebar");
    els.saveBar.hidden = true; // markDirty() below decides
    els.saveBar.dataset.role = "save-bar";
    els.saveBar.setAttribute("role", "region");
    els.saveBar.setAttribute("aria-label", "Unsaved settings");
    els.saveMsg = el("p", "savebar-msg", UNSAVED_TEXT);
    els.saveMsg.dataset.role = "save-status";
    els.saveMsg.setAttribute("role", "status");
    const btns = el("div", "onbnav");
    const discardBtn = el("button", "btn ghost wide", "Discard changes");
    discardBtn.type = "button";
    discardBtn.dataset.role = "settings-discard";
    discardBtn.addEventListener("click", discardDrafts);
    els.saveBtn = el("button", "btn primary wide", "Save changes");
    els.saveBtn.type = "button";
    els.saveBtn.dataset.role = "settings-save";
    els.saveBtn.addEventListener("click", saveDrafts);
    btns.append(discardBtn, els.saveBtn);
    els.saveBar.append(els.saveMsg, btns);
    els.body.appendChild(els.saveBar);
  }
  markDirty();

  els.body.append(buildAboutSection());

  if (focusAboutOnRender) {
    focusAboutOnRender = false;
    if (!aboutEls.details.open) {
      aboutOpenedByRail = true;
      aboutEls.details.open = true;
    }
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
    // Promise.all with its own `.catch(() => null)`, rather than a second
    // `await` chained after this whole block settles. Chaining it after
    // used to gate first paint of the ENTIRE screen (including the readonly
    // banner and even a "Could not load settings" error) on a THIRD round
    // trip that api.js itself documents has no client-side timeout — a
    // stalled /v1/schedule left the screen on the loading skeleton
    // indefinitely, with no error at all, exactly the outcome this
    // function's error handling exists to avoid. Catching it INSIDE the
    // array (not letting it reject the whole Promise.all) keeps both the
    // parallelism and the independent failure: a schedule failure still
    // never turns into "Could not load settings" for the rest of the
    // screen — it only means buildSweepStatusBlock() has nothing to show
    // (sweepTargetsMessage/cachedSweepGcRiskWarning both already treat a
    // null schedule as "print nothing").
    const [settingsResponse, steamStatus, schedule] = await Promise.all([
      api.getSettings(),
      api.getSteamKey(),
      api.schedule().catch(() => null),
    ]);
    // WP WEB-FIX-1 (P2): every section below dereferences `entryByKey(k).key`
    // unguarded. A response lacking one of the eight keys used to throw
    // INSIDE fullRender(), after this try/catch had already passed — an
    // uncaught error and "Loading settings…" on screen forever. Fail into
    // the same error path a rejected fetch takes, naming the gap.
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
  // A rail click that navigated here and then away before Settings loaded
  // must not move focus on some later, unrelated visit (review nit).
  focusAboutOnRender = false;
});

export function renderSettings() {
  const section = el("section", "view view-settings");
  const h1 = el("h1", null, "Settings");
  // Focus target after Save/Discard removed the focused bar (WP WEB-FIX-8);
  // never in the Tab order.
  h1.tabIndex = -1;
  const body = document.createElement("div");
  section.append(h1, body);

  sectionEl = section;
  els = { section, heading: h1, body, dots: new Map(), sectionKeys: new Map(), sectionsById: new Map() };
  aboutEls = null;
  drafts = {};
  openState = readOpenState(getStorage);
  expertOn = readExpertSwitch(getStorage);
  loadSettings();
  // WP WEB-FEAT-3: About loads when Settings opens (in parallel with the
  // settings form), never on a timer.
  loadAbout();
  return section;
}
