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
 *    addendum) plus a SteamID64 library-preview lookup
 *    (`GET /v1/steam/owned-games`/`player-summaries`). The typed key is
 *    handled by `lib/steam-key-form.js`'s `submitSteamKey`, which clears
 *    the input unconditionally after every submit attempt — see that
 *    module's header for the ADR-0004 "never retained" guarantee this
 *    pins headlessly.
 *  - Connection — a single "Reconnect / switch account" action that
 *    replays the onboarding overlay (onboarding.js), matching the
 *    mockup's Settings screen.
 *
 * No live polling here (no store-singleton subscription): settings rarely
 * change from outside this screen, so a plain fetch-on-mount plus
 * fetch-after-save is enough — there is no round-7-style animated node to
 * protect from a naive rebuild.
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
import { onViewChange } from "../router.js";

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

function markDirty() {
  if (!mounted()) return;
  const dirty = Object.keys(drafts).length > 0;
  els.saveBar.hidden = !dirty;
}

function discardDrafts() {
  drafts = {};
  fullRender();
}

async function saveDrafts() {
  const entries = state.settingsResponse.settings;
  const body = buildSettingsPatch(entries, drafts);
  if (Object.keys(body).length === 0) {
    drafts = {};
    markDirty();
    return;
  }
  els.saveBtn.disabled = true;
  try {
    const updated = await api.patchSettings(body);
    state.settingsResponse = updated;
    drafts = {};
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
  } catch (err) {
    showToast(errorText(err), { warn: true });
  } finally {
    if (mounted()) els.saveBtn.disabled = false;
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
    statusLine.textContent = "No Steam Web API key configured. Library queries answer 409 until one is set.";
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

  wrap.append(el("h4", "sec", "Library preview"));
  const idField = el("div", "field");
  const idLabel = el("label", null, "SteamID64");
  idLabel.htmlFor = "settings-steam-steamid";
  idField.append(idLabel);
  const idInput = document.createElement("input");
  idInput.id = "settings-steam-steamid";
  idInput.className = "inp txt";
  idInput.type = "text";
  idInput.inputMode = "numeric";
  idInput.placeholder = "76561198042117903";
  idField.appendChild(idInput);
  const lookupBtn = el("button", "btn sm", "Look up");
  lookupBtn.type = "button";
  const lookupBody = document.createElement("div");

  lookupBtn.addEventListener("click", async () => {
    const steamid = validSteamId64(idInput.value.trim());
    if (!steamid) {
      state.lookup = { error: "That does not look like a valid SteamID64 (17 digits)." };
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
      state.lookup = { error: errorText(err) };
    } finally {
      lookupBtn.disabled = false;
      renderLookupResult();
    }
  });

  wrap.append(idField, lookupBtn, lookupBody);

  els.steam = { statusLine, removeBtn, lookupBody };
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

function buildAboutSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "About"));
  wrap.append(
    el(
      "p",
      "hint",
      "SteamHangar is a community project and is not affiliated with Valve Corporation. “Steam” is a trademark of Valve Corporation.",
    ),
  );
  return wrap;
}

// ---------------------------------------------------------------------
// Top-level render
// ---------------------------------------------------------------------

function fullRender() {
  if (!mounted()) return;
  els.body.replaceChildren();

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

  els.saveBar = el("div", "onbnav");
  els.saveBar.hidden = Object.keys(drafts).length === 0;
  const discardBtn = el("button", "btn ghost wide", "Discard changes");
  discardBtn.type = "button";
  discardBtn.addEventListener("click", discardDrafts);
  els.saveBtn = el("button", "btn primary wide", "Save changes");
  els.saveBtn.type = "button";
  els.saveBtn.addEventListener("click", saveDrafts);
  if (state.settingsResponse.readonly) {
    els.saveBar.hidden = true;
  } else {
    els.saveBar.append(discardBtn, els.saveBtn);
  }
  els.body.appendChild(els.saveBar);

  els.body.append(buildSteamSection());
  renderSteamStatusLine();
  renderLookupResult();

  els.body.append(buildConnectionSection(), buildAboutSection());
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
});

export function renderSettings() {
  const section = el("section", "view view-settings");
  const h1 = el("h1", null, "Settings");
  const body = document.createElement("div");
  section.append(h1, body);

  sectionEl = section;
  els = { section, body };
  drafts = {};
  loadSettings();
  return section;
}
