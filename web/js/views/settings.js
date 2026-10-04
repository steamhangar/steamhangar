/**
 * Settings view (WP 4a.6).
 *
 * Three independent surfaces, each backed by its own real endpoint:
 *
 *  - Vault / Schedule / Webhook — one form over `GET`/`PATCH /v1/settings`
 *    (ADR-0009). The PATCH body is built by `lib/settings-diff.js` from a
 *    `drafts` map populated ONLY by fields the user actually edits — never
 *    pre-seeded with every field's current value — which is what makes
 *    "the body contains only changed keys" true by construction rather
 *    than by a diff that could be fooled by re-sending the same value
 *    (LEARNINGS "Testing discipline": that diff itself is unit-tested with
 *    a mutation pin in web/tests/settings-diff.test.js). Inputs are built
 *    ONCE per fetched snapshot and never replaced while the user is typing
 *    (`input`/`change` handlers only update `drafts` and the dirty-state
 *    banner) — replacing a live `<input>` node on every keystroke would
 *    steal focus and the cursor position, an unrelated but real class of
 *    bug to the round-7 "don't rebuild animated nodes" rule this codebase
 *    already avoids elsewhere (views/downloads.js, views/library.js).
 *  - Steam identity — `GET`/`PUT`/`DELETE /v1/steam/key` (WP 4a.6r; ADR-0004
 *    addendum) plus the vault's library SteamID64 (WP WEB-FEAT-1): the
 *    `steam_library_steamid` setting, saved with its own button as a JSON
 *    STRING via `PATCH /v1/settings` (a number is refused with 422 — 17
 *    digits exceed JavaScript's safe integers), and a "Preview" lookup of
 *    the typed id (`GET /v1/steam/owned-games`/`player-summaries`). The
 *    Library view reads the same setting, so this is the one place the
 *    account is chosen for every device. The typed key is
 *    handled by `lib/steam-key-form.js`'s `submitSteamKey`, which clears
 *    the input unconditionally after every submit attempt — see that
 *    module's header for the ADR-0004 "never retained" guarantee this
 *    pins headlessly.
 *  - Connection — a single "Reconnect / switch account" action that
 *    replays the onboarding overlay (onboarding.js), matching the
 *    mockup's Settings screen.
 *  - PCs (agents) — WP WEB-FEAT-3: the "Agents: N online, M offline" line
 *    and a "Show PCs" button that opens the existing clients sheet
 *    (components/clients-sheet.js). Reusing the sheet instead of a second
 *    list here keeps ONE rendering of a PC row (presence chip, last seen,
 *    version, games, bypass state) with one store subscription and one
 *    patch-in-place path, so the two entry points cannot drift; and it
 *    keeps the recorded WP 4a.1 decision "Clients is a sheet, not a nav
 *    item". Before this WP the sheet was reachable only from the bypass
 *    banner and the notifications, i.e. never on a healthy vault.
 *  - About — WP WEB-FEAT-3: the component table from `GET /v1/about` (WP
 *    VER-2), presented by lib/about-view.js. Fetched when this view opens
 *    and on the Refresh button, never polled; the server caches its answer
 *    for 60 s and the table says so. A 404 (a server from before VER-2: with
 *    a valid key an unknown route is 404) shows a "server too old" note;
 *    a 401 (key refused) and anything else show an error line. WP
 *    WEB-FIX-8: each row's explanations sit behind an (i) disclosure
 *    button, collapsed by default; a value not reported is a dash.
 *
 * The settings form itself is not polled (settings rarely change from
 * outside this screen, so fetch-on-mount plus fetch-after-save is enough).
 * The one store-singleton subscription here (WP WEB-FEAT-3, "clients")
 * only rewrites the agents-summary text and the About "checked ... ago"
 * line; it never rebuilds the form.
 */

import { api, isDemoMode } from "../api.js";
import { showToast } from "../components/toast.js";
import { openOnboarding } from "../onboarding.js";
import { buildSettingsPatch } from "../lib/settings-diff.js";
import {
  appliesText,
  sourceLabel,
  canReset,
  effectiveAsInputValue,
  missingSettingKeys,
} from "../lib/settings-presentation.js";
import { sweepTargetsMessage, cachedSweepGcRiskWarning } from "../lib/schedule-presentation.js";
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
import { openClientsSheet } from "../components/clients-sheet.js";
import { createStatusIcon } from "../components/status-icon.js";
import { agentsSummaryText } from "../lib/clients-view.js";
import {
  ABOUT_TOO_OLD_MESSAGE,
  aboutComponents,
  checkedText,
  classifyAboutError,
  describeComponents,
  DASH_LABEL,
} from "../lib/about-view.js";

const WEBHOOK_EVENT_OPTIONS = [
  ["job.done", "Job finished"],
  ["job.error", "Job failed"],
  ["job.cancelled", "Job cancelled"],
  ["client.bypass_suspected", "Cache bypass suspected"],
  ["client.bypass_resolved", "Cache bypass resolved"],
];
const AUTO_GC_OPTIONS = [
  ["off", "Off"],
  ["dry-run", "Dry run"],
  ["execute", "Execute"],
];
// WP 4d-web: sweep_include_cached is the first genuine boolean setting this
// view surfaces. Reuses the exact `.segs`/`aria-pressed` segmented-button
// idiom `auto_gc` above already established (and passed review) rather
// than introducing the mockup's separate, never-yet-wired `.toggle` switch
// vocabulary — one working idiom, not two. Draft/effective values travel
// as the strings "true"/"false" (what `PATCH /v1/settings` expects for this
// key, `config.parse_strict_bool`'s grammar) — never a JSON boolean, which
// the real endpoint explicitly rejects (Pydantic lax-mode trap, LEARNINGS
// "Parsers").
const SWEEP_INCLUDE_CACHED_OPTIONS = [
  ["false", "Off"],
  ["true", "On"],
];

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
  // WP 4d-web: last GET /v1/schedule response, or null before the first
  // fetch / after a failed one. Best-effort on purpose (see loadSettings) —
  // a schedule fetch failure must not block the rest of this screen from
  // rendering; it only means the sweep-status line and the cached-GC-risk
  // warning below have nothing to show.
  schedule: null,
};

/** {[key]: {reset: true} | {value: string | string[]}} — only ever
 * populated by an actual user edit; see the module header. */
let drafts = {};

let sectionEl = null;
function mounted() {
  return sectionEl !== null;
}
let els = null;

function entryByKey(key) {
  const list = state.settingsResponse && state.settingsResponse.settings;
  return (list || []).find((e) => e.key === key);
}

// ---------------------------------------------------------------------
// Dirty-state bar (shared Save/Discard for the whole /v1/settings form)
// ---------------------------------------------------------------------

/** Shown in the save bar while it is up (WP WEB-FIX-8). */
export const UNSAVED_TEXT = "Unsaved changes";

/** True when the drafts would change something server-side: the same
 * builder the PATCH uses, so a field edited and typed back to its saved
 * value is NOT dirty (WP WEB-FIX-8: "revert -> hidden"). */
function isDirty() {
  if (!state.settingsResponse || state.settingsResponse.readonly) return false;
  return Object.keys(buildSettingsPatch(state.settingsResponse.settings, drafts)).length > 0;
}

/**
 * Show the save bar exactly while something is dirty (WP WEB-FIX-8: the
 * bar is `position:fixed` above the bottom nav, app.css "Settings save
 * bar", so it is on screen without scrolling wherever the user is on this
 * long page). `.savebar-up` on the section reserves scroll room under the
 * page's last content so the bar never covers it. Any edit clears a
 * previous save error from the bar.
 */
function markDirty() {
  if (!mounted() || !els.saveBar) return;
  const dirty = isDirty();
  els.saveBar.hidden = !dirty;
  els.section.classList.toggle("savebar-up", dirty);
  // Only on a change: the line is a live region, and rewriting the same
  // text on every keystroke would re-announce it.
  if (els.saveMsg.textContent !== UNSAVED_TEXT) {
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

/** True while a PATCH is in flight: Save is `aria-disabled` plus this
 * guard, not `disabled`, so keyboard focus stays on the button
 * (docs/LEARNINGS.md, WP WEB-FEAT-1). */
let saving = false;

async function saveDrafts() {
  if (saving) return;
  const entries = state.settingsResponse.settings;
  const body = buildSettingsPatch(entries, drafts);
  if (Object.keys(body).length === 0) {
    drafts = {};
    markDirty();
    return;
  }
  saving = true;
  els.saveBtn.setAttribute("aria-disabled", "true");
  els.saveMsg.textContent = "Saving…";
  try {
    const updated = await api.patchSettings(body);
    state.settingsResponse = updated;
    drafts = {};
    const hadFocus = barHasFocus();
    // The toast (role=status) announces the success; the bar is gone.
    showToast("Settings saved.");
    // WP 4d-web: a saved PATCH can change sweep_include_cached/auto_gc,
    // which changes sweep_cached_gc_risk server-side — re-fetch so the
    // warning below reflects the just-saved values immediately rather than
    // whatever GET /v1/schedule answered at page load. Best-effort, same
    // reasoning as loadSettings(): a failed refetch must not undo the
    // successful save or block the rest of this render.
    try {
      state.schedule = await api.schedule();
    } catch {
      // leave state.schedule as it was — stale is better than crashing a
      // successful save.
    }
    fullRender();
    restoreFocusAfterBar(hadFocus);
  } catch (err) {
    // The bar stays up with the drafts; its own role=status line says what
    // failed (no warn toast on top: at BP-L the toast would sit over the
    // bar, and the line already announces it).
    if (mounted() && els.saveBar) {
      els.saveMsg.textContent = `Could not save: ${errorText(err)}`;
      els.saveMsg.className = "savebar-msg is-error";
    }
  } finally {
    saving = false;
    if (mounted() && els.saveBtn) els.saveBtn.removeAttribute("aria-disabled");
  }
}

// ---------------------------------------------------------------------
// One field: label, input, source/applies caption, optional Reset button.
// `onInput(value)` is called on every edit to update `drafts`.
// ---------------------------------------------------------------------

function buildTextField({ entry, label, placeholder, hint, onInput }) {
  const field = el("div", "field");
  const fieldId = `settings-${entry.key}`;
  const labelEl = el("label", null, label);
  labelEl.htmlFor = fieldId;
  field.append(labelEl);
  const input = document.createElement("input");
  input.id = fieldId;
  input.className = "inp txt";
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  if (placeholder) input.placeholder = placeholder;
  input.value = effectiveAsInputValue(entry);
  input.disabled = state.settingsResponse.readonly;
  input.addEventListener("input", () => onInput(input.value));
  field.appendChild(input);

  const caption = el("p", "foot-note");
  const resetBtn = el("button", "btn ghost sm", "Reset");
  resetBtn.type = "button";
  resetBtn.style.marginTop = "6px";
  resetBtn.hidden = !canReset(entry) || state.settingsResponse.readonly;
  resetBtn.addEventListener("click", () => {
    drafts[entry.key] = { reset: true };
    input.value = effectiveAsInputValue({ ...entry, effective: entry.fallback });
    markDirty();
  });
  caption.textContent = `${sourceLabel(entry.source)} · ${appliesText(entry.applies)}`;
  field.append(caption, resetBtn);
  if (hint) field.append(el("p", "foot-note", hint));
  return field;
}

// ---------------------------------------------------------------------
// Vault section
// ---------------------------------------------------------------------

function buildVaultSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "Vault"));
  const entry = entryByKey("vault_name");
  wrap.append(
    buildTextField({
      entry,
      label: "Vault name",
      placeholder: "e.g. vault-01",
      onInput: (value) => {
        drafts.vault_name = { value };
        markDirty();
      },
    }),
  );
  return wrap;
}

// ---------------------------------------------------------------------
// Schedule section
// ---------------------------------------------------------------------

function buildScheduleSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "Schedule"));

  wrap.append(
    buildTextField({
      entry: entryByKey("schedule_window"),
      label: "Sweep window",
      placeholder: "22:00-06:00, blank to disable",
      hint: "Overnight windows are allowed (e.g. 22:00-06:00). Blank disables scheduled sweeps.",
      onInput: (value) => {
        drafts.schedule_window = { value };
        markDirty();
      },
    }),
  );
  wrap.append(
    buildTextField({
      entry: entryByKey("schedule_interval_minutes"),
      label: "Sweep interval (minutes)",
      onInput: (value) => {
        drafts.schedule_interval_minutes = { value };
        markDirty();
      },
    }),
  );
  wrap.append(
    buildTextField({
      entry: entryByKey("schedule_client_stale_days"),
      label: "Client staleness (days)",
      onInput: (value) => {
        drafts.schedule_client_stale_days = { value };
        markDirty();
      },
    }),
  );

  wrap.append(buildSweepIncludeCachedField());

  const autoGcEntry = entryByKey("auto_gc");
  const field = el("div", "field");
  const autoGcLabel = el("label", null, "Auto-GC after a prefill");
  autoGcLabel.id = "settings-auto_gc-label";
  field.append(autoGcLabel);
  const segs = el("div", "segs");
  segs.setAttribute("role", "group");
  segs.setAttribute("aria-labelledby", autoGcLabel.id);
  const current = drafts.auto_gc && "value" in drafts.auto_gc ? drafts.auto_gc.value : autoGcEntry.effective;
  const buttons = [];
  for (const [mode, label] of AUTO_GC_OPTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.setAttribute("aria-pressed", String(mode === current));
    btn.addEventListener("click", () => {
      drafts.auto_gc = { value: mode };
      for (const other of buttons) other.setAttribute("aria-pressed", "false");
      btn.setAttribute("aria-pressed", "true");
      markDirty();
    });
    if (state.settingsResponse.readonly) btn.disabled = true;
    buttons.push(btn);
    segs.appendChild(btn);
  }
  field.appendChild(segs);
  const caption = el(
    "p",
    "foot-note",
    `${sourceLabel(autoGcEntry.source)} · ${appliesText(autoGcEntry.applies)}`,
  );
  field.appendChild(caption);
  if (canReset(autoGcEntry) && !state.settingsResponse.readonly) {
    const resetBtn = el("button", "btn ghost sm", "Reset");
    resetBtn.type = "button";
    resetBtn.style.marginTop = "6px";
    resetBtn.addEventListener("click", () => {
      drafts.auto_gc = { reset: true };
      for (const btn of buttons) btn.setAttribute("aria-pressed", String(btn.textContent === labelFor(autoGcEntry.fallback)));
      markDirty();
    });
    field.appendChild(resetBtn);
  }
  wrap.append(field);

  wrap.append(buildSweepStatusBlock());
  return wrap;
}
function labelFor(mode) {
  const found = AUTO_GC_OPTIONS.find(([m]) => m === mode);
  return found ? found[1] : mode;
}

/**
 * The `sweep_include_cached` toggle — WP 4d-web. Structurally the same
 * segmented-button group as `auto_gc` above (see `SWEEP_INCLUDE_CACHED_
 * OPTIONS`'s comment for why this reuses that idiom rather than the
 * mockup's separate, unwired `.toggle` switch), just with two options
 * instead of three, and string values `"false"`/`"true"` instead of an
 * enum's own words.
 */
function buildSweepIncludeCachedField() {
  const entry = entryByKey("sweep_include_cached");
  const field = el("div", "field");
  const label = el("label", null, "Include cached games in the sweep");
  label.id = "settings-sweep_include_cached-label";
  field.append(label);
  const segs = el("div", "segs");
  segs.setAttribute("role", "group");
  segs.setAttribute("aria-labelledby", label.id);
  const current =
    drafts.sweep_include_cached && "value" in drafts.sweep_include_cached
      ? drafts.sweep_include_cached.value
      : String(entry.effective);
  const buttons = [];
  for (const [value, label2] of SWEEP_INCLUDE_CACHED_OPTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label2;
    btn.dataset.value = value;
    btn.setAttribute("aria-pressed", String(value === current));
    btn.addEventListener("click", () => {
      drafts.sweep_include_cached = { value };
      for (const other of buttons) other.setAttribute("aria-pressed", "false");
      btn.setAttribute("aria-pressed", "true");
      markDirty();
    });
    if (state.settingsResponse.readonly) btn.disabled = true;
    buttons.push(btn);
    segs.appendChild(btn);
  }
  field.appendChild(segs);
  field.appendChild(
    el("p", "foot-note", `${sourceLabel(entry.source)} · ${appliesText(entry.applies)}`),
  );
  field.appendChild(
    el(
      "p",
      "foot-note",
      "When on, the next sweep also refreshes every game that already has content on disk, not only games a PC agent reports as installed.",
    ),
  );
  if (canReset(entry) && !state.settingsResponse.readonly) {
    const resetBtn = el("button", "btn ghost sm", "Reset");
    resetBtn.type = "button";
    resetBtn.style.marginTop = "6px";
    resetBtn.addEventListener("click", () => {
      drafts.sweep_include_cached = { reset: true };
      const fallbackStr = String(entry.fallback);
      for (const btn of buttons) btn.setAttribute("aria-pressed", String(btn.dataset.value === fallbackStr));
      markDirty();
    });
    field.appendChild(resetBtn);
  }
  return field;
}

/**
 * The "did the last scheduled sweep actually do anything" line, plus the
 * "keeping the cache current without collecting" warning when the server
 * reports the risk condition — WP 4d-web. Both pieces of text come from
 * `lib/schedule-presentation.js`, fed the server's own `GET /v1/schedule`
 * response verbatim (`state.schedule`): neither is computed here from
 * `sweep_include_cached`/`auto_gc` a second time (see that module's header
 * for why re-deriving `sweep_cached_gc_risk` client-side would be exactly
 * the two-copies-diverge mistake docs/LEARNINGS.md warns about). Renders
 * nothing at all — not a placeholder — while `state.schedule` is still
 * null (no fetch yet, or the fetch failed); this is a status readout, not
 * a required part of the form.
 */
function buildSweepStatusBlock() {
  const wrap = document.createDocumentFragment();
  const statusText = sweepTargetsMessage(state.schedule);
  if (statusText) wrap.append(el("p", "foot-note", statusText));
  const riskText = cachedSweepGcRiskWarning(state.schedule);
  if (riskText) wrap.append(el("p", "settings-warn", riskText));
  return wrap;
}

// ---------------------------------------------------------------------
// Webhook section
// ---------------------------------------------------------------------

function buildWebhookSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "Webhook"));

  wrap.append(
    buildTextField({
      entry: entryByKey("webhook_url"),
      label: "Webhook URL",
      placeholder: "https://... , blank to disable",
      hint: "Restart-required: vault-api only starts the delivery thread at boot if a webhook was already configured then (api/README.md “The honest gap”).",
      onInput: (value) => {
        drafts.webhook_url = { value };
        markDirty();
      },
    }),
  );

  const eventsEntry = entryByKey("webhook_events");
  const field = el("div", "field");
  field.setAttribute("role", "group");
  const eventsLabel = el("label", null, "Events sent");
  eventsLabel.id = "settings-webhook_events-label";
  field.setAttribute("aria-labelledby", eventsLabel.id);
  field.append(eventsLabel);
  const currentList = new Set(
    drafts.webhook_events && "value" in drafts.webhook_events
      ? drafts.webhook_events.value
      : eventsEntry.effective,
  );
  const checkboxes = [];
  for (const [value, label] of WEBHOOK_EVENT_OPTIONS) {
    const row = el("label", "srow");
    row.style.cursor = "pointer";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = currentList.has(value);
    cb.disabled = state.settingsResponse.readonly;
    cb.addEventListener("change", () => {
      const selected = checkboxes.filter((c) => c.checked).map((c) => c.dataset.event);
      drafts.webhook_events = { value: selected };
      markDirty();
    });
    cb.dataset.event = value;
    checkboxes.push(cb);
    const grow = el("span", "grow");
    grow.append(el("span", "ttl", label));
    row.append(cb, grow);
    field.appendChild(row);
  }
  const caption = el(
    "p",
    "foot-note",
    `${sourceLabel(eventsEntry.source)} · ${appliesText(eventsEntry.applies)}`,
  );
  field.appendChild(caption);
  wrap.append(field);
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

function buildSteamSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "Steam identity"));
  wrap.append(
    el(
      "p",
      "hint",
      "With a relay key configured, library queries leave your LAN toward Valve (ADR-0004 addendum) — the key is a revocable, read-scoped Web API key, never a password.",
    ),
  );

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
  wrap.append(el("h4", "sec", "Steam library"));
  wrap.append(
    el(
      "p",
      "hint",
      "The Library lists every game this SteamID64 owns, on every device that uses this vault. Leave it blank and save to list only the games the vault knows.",
    ),
  );
  const entry = entryByKey(STEAM_LIBRARY_SETTING_KEY);
  const readonly = state.settingsResponse.readonly;

  const idField = el("div", "field");
  const idLabel = el("label", null, "SteamID64");
  idLabel.htmlFor = "settings-steam-steamid";
  idField.append(idLabel);
  const idInput = document.createElement("input");
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
  const caption = el("p", "foot-note");
  caption.dataset.role = "steamid-caption";
  idField.appendChild(caption);
  const idErr = el("p", "errline");
  idErr.dataset.role = "steamid-error";
  idErr.hidden = true;

  const saveBtn = el("button", "btn sm", "Save SteamID64");
  saveBtn.type = "button";
  saveBtn.dataset.role = "steamid-save";
  saveBtn.hidden = readonly || !entry;
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
    const steamid = validSteamId64(idInput.value.trim());
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
// PCs (agents) section (WP WEB-FEAT-3)
// ---------------------------------------------------------------------

/** Agents line text from the store's latest clients snapshot; a waiting
 * text before the first answer (never "none" before it knows). */
function currentAgentsText() {
  const snap = store.snapshot("clients");
  return agentsSummaryText(Array.isArray(snap) ? snap : null) || "Agents: waiting for the first answer from the server…";
}

function buildPcsSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "PCs (agents)"));
  const summaryLine = el("p", "foot-note", currentAgentsText());
  summaryLine.dataset.role = "agents-summary";
  wrap.append(summaryLine);
  const row = el("div", "srow");
  const grow = el("span", "grow");
  grow.append(
    el("span", "ttl", "PCs running vault-agent"),
    el(
      "span",
      "desc",
      "Online or offline, last seen, agent version, games and cache bypass state for every PC that has reported — offline PCs included.",
    ),
  );
  const btn = el("button", "btn ghost sm", "Show PCs");
  btn.type = "button";
  btn.dataset.role = "open-pcs";
  btn.addEventListener("click", () => openClientsSheet());
  row.append(grow, btn);
  wrap.append(row);
  els.agentsLines.push(summaryLine);
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
/** Component names whose (i) details are open (WP WEB-FIX-8). Module
 * state, so a Refresh (which rebuilds the table) keeps them open; default
 * collapsed. */
const aboutInfoOpen = new Set();
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

const SVG_NS = "http://www.w3.org/2000/svg";

/** The (i) glyph: a 24-unit outline circle with an "i", stroked in
 * currentColor like the status-icon glyphs (components/status-icon.js:
 * viewBox 0 0 24 24, round caps), decorative (the button carries the
 * name). */
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

/** A DOM id from a component name (letters, digits and dashes only). */
function aboutInfoId(name) {
  return "about-info-" + name.toLowerCase().replace(/[^a-z0-9-]+/g, "-");
}

/**
 * The per-row (i) disclosure button (WP WEB-FIX-8): a native <button>, so
 * Enter/Space and the global :focus-visible ring come for free;
 * aria-expanded + aria-controls point at the row's details. Toggling flips
 * `hidden` on the details row and records the choice in `aboutInfoOpen`.
 */
function buildInfoButton(name, detailRow) {
  const btn = el("button", "about-info-btn");
  btn.type = "button";
  btn.dataset.role = "about-info";
  btn.setAttribute("aria-label", `Details for ${name}`);
  btn.setAttribute("aria-controls", detailRow.id);
  btn.appendChild(infoGlyph());
  const apply = (open) => {
    btn.setAttribute("aria-expanded", String(open));
    detailRow.hidden = !open;
  };
  apply(aboutInfoOpen.has(name));
  btn.addEventListener("click", () => {
    const open = !aboutInfoOpen.has(name);
    if (open) aboutInfoOpen.add(name);
    else aboutInfoOpen.delete(name);
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
    nameCell.appendChild(el("span", "about-name-text", view.name));
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
      nameCell.appendChild(buildInfoButton(view.name, detailRow));
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
  const wrap = document.createDocumentFragment();
  const heading = el("h4", "sec", "About");
  heading.id = "settings-about";
  // Programmatic focus target for the rail's version button only; never in
  // the Tab order (same landing-spot technique as the sheets).
  heading.tabIndex = -1;
  wrap.append(heading);

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
  aboutEls = { heading, refreshBtn, status, content, checked };
  setRefreshBusy(about.phase === "loading");
  paintAbout();
  return wrap;
}

// The settings form is not polled, but the agents line and the About
// "checked ... ago" text follow the store's clients ticks (every 20 s):
// text only, nothing rebuilt. An `{error}` tick keeps the last text.
store.subscribe("clients", (payload) => {
  if (!mounted() || !payload || payload.error) return;
  const text = agentsSummaryText(Array.isArray(payload.items) ? payload.items : null);
  if (text) for (const line of els.agentsLines) line.textContent = text;
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

  els.body.append(buildVaultSection(), buildScheduleSection(), buildWebhookSection());

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

  els.body.append(buildSteamSection());
  renderSteamStatusLine();
  renderLookupResult();

  els.agentsLines = [];
  els.body.append(buildConnectionSection(), buildPcsSection(), buildAboutSection());

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
  els = { section, heading: h1, body, agentsLines: [] };
  aboutEls = null;
  drafts = {};
  loadSettings();
  // WP WEB-FEAT-3: About loads when Settings opens (in parallel with the
  // settings form), never on a timer.
  loadAbout();
  return section;
}
