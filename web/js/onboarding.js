/**
 * Onboarding overlay (WP 4a.6).
 *
 * Ports docs/design/vault-app-mockup.html's 3-step first-run flow (frozen
 * round 7: Connect -> Steam identity -> Ready), adapted to the DECIDED
 * web-UI shape (docs/WORKPACKAGES.md Phase 4a header; ADR-0004 addendum):
 *
 *   Step 1 Connect — vault name (optional) + the vault API key, verified
 *     against a REAL server/key check (`api.checkVaultApiKey`) before
 *     `Continue` unlocks (web/js/lib/onboarding-steps.js's `canAdvance`).
 *     No server-URL/connection-profile fields: this page is served BY
 *     vault-api and the WP 4a.1 CSP is same-origin-only — see api.js's
 *     module header for the removed `getServerUrl`/`setServerUrl`.
 *   Step 2 Steam identity — OPTIONAL: the Steam Web API relay's key
 *     (WP 4a.6r) plus a SteamID64, with a live library lookup. This
 *     is a key-entry form, NOT Valve OpenID — that stays the native
 *     Android app's device-local path (ADR-0004 decision 2). "Continue
 *     without one" is simply the unconditional Continue button.
 *     WP WEB-FEAT-2: a lookup the relay answered also STORES the id as the
 *     vault's library SteamID64 (`steam_library_steamid`, the setting the
 *     Settings "Steam library" block edits), through the same helper as
 *     Settings' Save (`lib/owned-library.js`'s `saveLibrarySteamId`: JSON
 *     string, only a real change is sent). A failed lookup saves nothing;
 *     0 games (a private profile) still saves, since the id is valid. A
 *     read-only vault or an older vault-api without the setting keeps the
 *     lookup-only behaviour and says the id was not saved. The input is
 *     pre-filled from the stored value in the step-1 settings snapshot.
 *     The Library reads the setting on every open, and `finish()` reloads
 *     anyway, so the next library open lists the owned games.
 *   Step 3 Ready — summary, then reload the app so every module
 *     (store-singleton, app.js) re-initializes against the now-real
 *     localStorage/API state instead of trying to hot-patch it in place.
 *
 * All step-machine DECISIONS (gating, progress, whether to show at all)
 * live in lib/onboarding-steps.js and are unit-tested there; this module is
 * the DOM builder wired to that machine, same split as views/downloads.js.
 *
 * The overlay is built once and appended to `document.body` the first time
 * this module is imported (mirrors store-singleton.js's "start once, reuse
 * forever" posture) — `openOnboarding()`/`closeOnboarding()` only toggle
 * its visibility, never rebuild it.
 *
 * **Dialog semantics (review should-fix, WP 4a.6 cycle 2) — the cheap 80%,
 * not a full modal.** The overlay root carries `role="dialog"` +
 * `aria-modal="true"` + `aria-labelledby` pointing at the CURRENT step's
 * `<h2>` (updated on every `render()`, since the heading — and therefore
 * the accessible name — changes with the step). Opening moves focus to the
 * first real control (step 1's vault-name field); advancing/going back
 * moves focus to the new step's heading (`tabindex="-1"`, common
 * wizard-step pattern) so a screen reader announces the step changed.
 * `Escape` closes the overlay ONLY in `mode: "reconnect"` (Settings'
 * "Reconnect / switch account") — a first-run open has nothing behind it
 * to reveal, so `Escape` is a no-op there — and returns focus to whatever
 * invoked it (captured as `document.activeElement` at `openOnboarding()`
 * time, which is the "Start" button mid-click; the same path `onSkip()`'s
 * reconnect branch already used).
 *
 * **The full focus trap (WP 4a.8, closing the deferral this header used to
 * record).** `openOnboarding()`/`closeOnboarding()` push/pop `root` onto
 * `lib/modal-stack.js`'s shared stack, which marks `#app` `inert` +
 * `aria-hidden` for the duration — see that module's header for why `inert`
 * alone is both "a real focus trap" and "inert/aria-hidden on the app shell
 * behind it" in one mechanism. This applies on a FIRST-RUN open too (there
 * is nothing behind it worth revealing via Escape, per the paragraph above,
 * but `#app`'s nav/topbar are still real, still-rendered, still-focusable
 * DOM at that point and must not be Tab-reachable while the overlay is up).
 */

import {
  FIRST_STEP,
  LAST_STEP,
  STEP,
  canAdvance,
  nextStep,
  prevStep,
  progressPercent,
  stepTitle,
  shouldShowOnboarding,
} from "./lib/onboarding-steps.js";
import { validSteamId64 } from "./lib/steamid.js";
import { submitSteamKey } from "./lib/steam-key-form.js";
import {
  INVALID_STEAMID64_MESSAGE,
  PRIVATE_PROFILE_MESSAGE,
  SAVE_OUTCOME,
  describeLookupError,
  saveLibrarySteamId,
  steamIdFromSettings,
} from "./lib/owned-library.js";
import { api, checkVaultApiKey, getStoredApiKey, setStoredApiKey, isDemoMode, setDemoMode } from "./api.js";
import { showToast } from "./components/toast.js";
import { pushModal, popModal } from "./lib/modal-stack.js";

const MARK_SVG =
  '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 2.6 20.5 6v6.1c0 5-3.6 8.3-8.5 9.3-4.9-1-8.5-4.3-8.5-9.3V6L12 2.6Z"/><circle cx="12" cy="11.4" r="2.6"/><path d="M12 14v3.2"/></svg>';
const EYE_SVG =
  '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M1.6 10S4.7 4.6 10 4.6 18.4 10 18.4 10 15.3 15.4 10 15.4 1.6 10 1.6 10Z"/><circle cx="10" cy="10" r="2.5"/></svg>';
const CHECK_SVG =
  '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="m4 10.6 4 4L16.4 5.6"/></svg>';

function staticIcon(svgMarkup) {
  const span = document.createElement("span");
  span.innerHTML = svgMarkup;
  return span.firstElementChild;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** A step's `<h2>`, given an id and `tabindex="-1"` so it can receive
 * programmatic focus on a step change (it is never a Tab stop otherwise —
 * this does not add it to the natural tab order). Used as both the visible
 * heading and the `aria-labelledby` target for the dialog's accessible
 * name (see the module header's "Dialog semantics" note). */
function stepHeading(step, text) {
  const h2 = el("h2", null, text);
  h2.id = `onb-step-${step}-heading`;
  h2.tabIndex = -1;
  return h2;
}

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed.";
}

// ---------------------------------------------------------------------
// State
// ---------------------------------------------------------------------

function freshState() {
  return {
    step: FIRST_STEP,
    mode: "first-run", // "first-run" | "reconnect" — governs Skip's behaviour
    tested: false,
    // WP WEB-FIX-1 round 2 (S1): sticky — set by the FIRST successful key
    // test and never cleared, because that test already stored the key and
    // ended demo mode (B1). A later failed re-test resets `tested`, not this.
    switched: false,
    settings: null, // the {readonly, settings} GET /v1/settings answer from the last successful test
    steamStatus: { configured: false, key_last4: null },
    lookup: null, // {gameCount, players} | {error} | null (nothing looked up yet)
  };
}

let state = freshState();
let root = null;
let els = {};
/** The element to return focus to when a `mode: "reconnect"` overlay
 * closes (Escape, or Skip) — captured as `document.activeElement` at
 * `openOnboarding()` time (the "Start" button mid-click). `null` for a
 * first-run open (nothing was focused for a reason to return to) and
 * cleared immediately after use, so a stale reference is never refocused
 * twice. */
let invokerEl = null;
/** Step 2's running Look up (relay call plus save), or null. Guards a
 * second click and is awaited by `finish()`. */
let pendingLookup = null;
/** True while `finish()` runs, so a second "Go to library" press cannot
 * start a second save/reload. */
let finishing = false;

// ---------------------------------------------------------------------
// Step 1 — Connect
// ---------------------------------------------------------------------

function buildStep1() {
  const section = el("section", "ostep");
  section.dataset.step = String(STEP.CONNECT);

  const heading = stepHeading(STEP.CONNECT, "Connect to your vault");
  section.append(heading);
  section.append(
    el(
      "p",
      "lede",
      "SteamHangar is already serving this page from your vault — the API key below is what lets THIS BROWSER talk to it.",
    ),
  );

  const nameField = el("div", "field");
  const nameLabel = el("label", null, "Name your vault (optional)");
  nameLabel.htmlFor = "onb-name";
  const nameInput = document.createElement("input");
  nameInput.className = "inp txt";
  nameInput.id = "onb-name";
  nameInput.type = "text";
  nameInput.maxLength = 24;
  nameInput.placeholder = "e.g. vault-01";
  nameInput.autocomplete = "off";
  nameField.append(nameLabel, nameInput, el("p", "foot-note", "Shown in the app; defaults to the server's own name."));

  const keyField = el("div", "field");
  const keyLabel = el("label", null, "Vault API key");
  keyLabel.htmlFor = "onb-key";
  const keyWrap = el("div", "inp");
  const keyInput = document.createElement("input");
  keyInput.id = "onb-key";
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.spellcheck = false;
  const eyeBtn = document.createElement("button");
  eyeBtn.type = "button";
  eyeBtn.setAttribute("aria-label", "Reveal API key");
  eyeBtn.appendChild(staticIcon(EYE_SVG));
  eyeBtn.addEventListener("click", () => {
    keyInput.type = keyInput.type === "password" ? "text" : "password";
  });
  keyWrap.append(keyInput, eyeBtn);
  keyField.append(keyLabel, keyWrap);

  const testBtn = el("button", "btn wide", "Test connection");
  testBtn.type = "button";
  const okLine = el("div", "okline");
  okLine.appendChild(staticIcon(CHECK_SVG));
  const okText = el("span");
  okLine.appendChild(okText);
  const errLine = el("p", "errline");
  errLine.hidden = true;

  testBtn.addEventListener("click", async () => {
    const key = keyInput.value.trim();
    if (!key) {
      errLine.hidden = false;
      errLine.textContent = "Enter the vault API key first.";
      return;
    }
    testBtn.disabled = true;
    testBtn.textContent = "Testing…";
    okLine.classList.remove("on");
    errLine.hidden = true;
    try {
      // WP WEB-FIX-1 (N5): this button TESTS. It stores the verified key
      // (step 2's relay calls need it on the wire) and pre-fills the name
      // field from the server — it no longer PATCHes `vault_name`; that
      // write happens in `finish()`, behind the button whose label says
      // the flow is being completed.
      const { settings } = await checkVaultApiKey(key);
      setStoredApiKey(key);
      // WP WEB-FIX-1 (B1): a verified real vault ends demo mode HERE, not
      // only at finish(). `api.js` routes every request by `isDemoMode()`
      // per call, so leaving the flag set until the final step would send
      // step 2's Steam-relay PUT/GET to the in-memory fixtures and report
      // a save that never reached the vault. (Before this WP nothing ever
      // wrote `"0"` — a user who chose "browse in demo mode" once was in
      // demo forever, reconnect included.)
      setDemoMode(false);
      state.tested = true;
      state.switched = true;
      state.settings = settings;
      const vaultNameEntry = settings.settings.find((s) => s.key === "vault_name");
      if (!nameInput.value && vaultNameEntry && vaultNameEntry.effective) {
        nameInput.value = vaultNameEntry.effective;
      }
      // WP WEB-FEAT-2: same pre-fill rule for step 2's SteamID64 — the
      // stored library id, never over something already typed.
      const storedSteamId = steamIdFromSettings(settings);
      if (!els.step2.idInput.value && storedSteamId) els.step2.idInput.value = storedSteamId;
      // WP WEB-FIX-1 (N3): no version suffix — `GET /v1/health` is a fixed
      // `{"status":"ok"}` (api/vault_api/routers/health.py); the server
      // version lives on `GET /v1/settings`'s `server_version`, which the
      // rail foot already shows.
      okText.textContent = "200 OK · vault-api";
      okLine.classList.add("on");
    } catch (err) {
      errLine.hidden = false;
      errLine.textContent = errorText(err);
      state.tested = false;
    } finally {
      testBtn.disabled = false;
      testBtn.textContent = "Test connection";
      render();
    }
  });

  section.append(
    nameField,
    keyField,
    testBtn,
    okLine,
    errLine,
    el(
      "p",
      "foot-note",
      "The key is stored on this browser only. It never leaves the app except as the X-Api-Key header on requests to this same server.",
    ),
  );

  els.step1 = { section, heading, nameInput, keyInput, errLine };
  return section;
}

// ---------------------------------------------------------------------
// Step 2 — Steam identity (optional)
// ---------------------------------------------------------------------

function renderSteamStatus() {
  const { statusLine, removeBtn } = els.step2;
  if (state.steamStatus.configured) {
    statusLine.textContent = `Relay key configured (••••${state.steamStatus.key_last4}).`;
    removeBtn.hidden = false;
  } else {
    statusLine.textContent = "No Steam Web API key configured yet.";
    removeBtn.hidden = true;
  }
}

function renderLookupResult() {
  const { lookupBody } = els.step2;
  lookupBody.replaceChildren();
  if (!state.lookup) return;
  if (state.lookup.error) {
    lookupBody.appendChild(el("p", "errline", state.lookup.error));
    return;
  }
  const persona = state.lookup.persona;
  if (persona) {
    lookupBody.appendChild(
      el("p", "foot-note", `Signed in as ${persona.personaname} · SteamID64 ${persona.steamid}`),
    );
  }
  lookupBody.appendChild(el("p", "foot-note", `${state.lookup.gameCount} games found.`));
  if (state.lookup.gameCount === 0) lookupBody.appendChild(el("p", "foot-note", PRIVATE_PROFILE_MESSAGE));
  const list = el("ul", "bullets");
  for (const g of state.lookup.preview) {
    const li = document.createElement("li");
    li.textContent = g.name;
    list.appendChild(li);
  }
  lookupBody.appendChild(list);
}

function buildStep2() {
  const section = el("section", "ostep");
  section.dataset.step = String(STEP.STEAM);

  const heading = stepHeading(STEP.STEAM, "Optional: link your Steam library");
  section.append(heading);
  section.append(
    el(
      "p",
      "lede",
      "Without this, SteamHangar still manages the cache — the library grid just lists app ids instead of covers and names.",
    ),
  );

  section.append(el("h4", "sec", "What gets fetched"));
  const bullets = el("ul", "bullets");
  const items = [
    ["Owned games", "the list behind your library grid."],
    ["Persona name & avatar", "your public profile display name and picture."],
  ];
  for (const [b, rest] of items) {
    const li = document.createElement("li");
    const strong = document.createElement("b");
    strong.textContent = b;
    li.append(strong, document.createTextNode(" — " + rest));
    bullets.appendChild(li);
  }
  section.append(bullets);

  const disclaim = el(
    "div",
    "disclaim",
    "This is read via a small opt-in relay on THIS server (never proxied through Valve credentials — ADR-0004 addendum): with a key configured, library queries leave your LAN toward Valve's servers. SteamHangar is a community project and is not affiliated with Valve Corporation.",
  );
  section.append(disclaim);

  section.append(el("h4", "sec", "Steam Web API key"));
  const statusLine = el("p", "foot-note", "");
  const keyField = el("div", "field");
  const keyLabel = el("label", null, "Web API key (32 hex characters)");
  keyLabel.htmlFor = "onb-steam-key";
  const keyInput = document.createElement("input");
  keyInput.id = "onb-steam-key";
  keyInput.className = "inp txt";
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.spellcheck = false;
  keyInput.placeholder = "from steamcommunity.com/dev/apikey";
  keyField.append(keyLabel, keyInput);

  const saveBtn = el("button", "btn sm", "Save key");
  saveBtn.type = "button";
  const removeBtn = el("button", "btn ghost sm", "Remove key");
  removeBtn.type = "button";
  removeBtn.hidden = true;
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
    renderSteamStatus();
    showToast("Steam Web API key saved.");
  });
  removeBtn.addEventListener("click", async () => {
    removeBtn.disabled = true;
    try {
      await api.deleteSteamKey();
      state.steamStatus = { configured: false, key_last4: null };
      state.lookup = null;
      renderSteamStatus();
      renderLookupResult();
    } catch (err) {
      showToast(errorText(err), { warn: true });
    } finally {
      removeBtn.disabled = false;
    }
  });

  const keyRow = el("div", "onbnav");
  keyRow.append(saveBtn, removeBtn);
  section.append(keyField, keyRow, keyErr, statusLine);

  section.append(el("h4", "sec", "Your Steam library"));
  const idField = el("div", "field");
  const idLabel = el("label", null, "SteamID64");
  idLabel.htmlFor = "onb-steam-steamid";
  idField.append(idLabel);
  const idInput = document.createElement("input");
  idInput.id = "onb-steam-steamid";
  idInput.className = "inp txt";
  idInput.type = "text";
  idInput.inputMode = "numeric";
  idInput.placeholder = "76561198042117903";
  idInput.autocomplete = "off";
  idInput.spellcheck = false;
  idField.appendChild(idInput);
  const idHint = el("p", "foot-note", "Look up checks this SteamID64 and saves it, so the Library lists every game it owns.");
  idHint.id = "onb-steam-steamid-hint";
  idInput.setAttribute("aria-describedby", idHint.id);
  idField.appendChild(idHint);
  const lookupBtn = el("button", "btn sm", "Look up");
  lookupBtn.type = "button";
  lookupBtn.dataset.role = "steamid-lookup";
  // aria-disabled + a click guard, not `disabled`: a disabled button drops
  // keyboard focus to <body> (LEARNINGS, WP WEB-FEAT-1).
  lookupBtn.setAttribute("aria-disabled", "false");
  const lookupBody = el("div");
  // Whether the id was stored. Built once and never hidden (an empty
  // <p> renders nothing visible); role=status sits on its text-only span,
  // so a screen reader never misses text that appears in the same render
  // as the region (LEARNINGS, WP WEB-FEAT-1).
  const saveNote = el("p", "foot-note");
  saveNote.dataset.role = "steamid-save-note";
  const saveText = el("span");
  saveText.setAttribute("role", "status");
  saveNote.appendChild(saveText);

  lookupBtn.addEventListener("click", () => {
    if (pendingLookup) return; // a lookup (and its save) is already running
    setSaveNote("");
    const steamid = validSteamId64(idInput.value.trim());
    if (!steamid) {
      state.lookup = { error: INVALID_STEAMID64_MESSAGE };
      renderLookupResult();
      return;
    }
    // The id is fixed here: editing the input while the request runs does
    // not change what gets saved. `finish()` awaits this promise, so the
    // reload never cuts a running save short.
    pendingLookup = runLookup(steamid)
      // Every expected failure is already shown in step 2; anything else is
      // a bug worth seeing in the console. Never rejects, so finish() is
      // never blocked by it.
      .catch((err) => {
        console.error("onboarding: Look up failed unexpectedly", err);
      })
      .finally(() => {
        pendingLookup = null;
      });
  });

  async function runLookup(steamid) {
    lookupBtn.setAttribute("aria-disabled", "true");
    try {
      let owned;
      let players;
      try {
        [owned, players] = await Promise.all([
          api.steamOwnedGames(steamid),
          api.steamPlayerSummaries(steamid).catch(() => null),
        ]);
      } catch (err) {
        // Nothing is saved for a lookup the relay refused (409 no key,
        // 422 invalid id, anything else).
        state.lookup = { error: describeLookupError(err) };
        return;
      }
      const games = Array.isArray(owned && owned.games) ? owned.games : [];
      state.lookup = {
        gameCount: owned && typeof owned.game_count === "number" ? owned.game_count : games.length,
        preview: games.slice(0, 8),
        persona: players && players.players && players.players[0],
      };
      renderLookupResult();
      setSaveNote(await saveLookedUpSteamId(steamid));
    } finally {
      lookupBtn.setAttribute("aria-disabled", "false");
      renderLookupResult();
    }
  }

  function setSaveNote(text) {
    if (saveText.textContent !== text) saveText.textContent = text;
  }

  section.append(idField, lookupBtn, lookupBody, saveNote);
  section.append(
    el("p", "foot-note", "Not ready to link an account? Continue without one — you can set this up later under Settings."),
  );

  els.step2 = { section, heading, keyInput, statusLine, removeBtn, lookupBody, idInput, setSaveNote };
  return section;
}

/** Store a looked-up SteamID64 as the vault's library id (WP WEB-FEAT-2)
 * and return the sentence saying whether that happened. Uses the step-1
 * settings snapshot (step 2 is only reachable after a verified key test)
 * and replaces it with the PATCH answer, so a second lookup compares
 * against the stored value. */
async function saveLookedUpSteamId(steamid) {
  const result = await saveLibrarySteamId(api, state.settings, steamid);
  if (result.outcome === SAVE_OUTCOME.SAVED || result.outcome === SAVE_OUTCOME.UNCHANGED) {
    if (result.settingsResponse) state.settings = result.settingsResponse;
    return `Saved ${steamid} — your library will show the games this SteamID64 owns.`;
  }
  const reason = /[.!?]$/.test(result.error) ? result.error : `${result.error}.`;
  return `Not saved: ${reason} The lookup above still worked.`;
}

// ---------------------------------------------------------------------
// Step 3 — Ready
// ---------------------------------------------------------------------

function buildStep3() {
  const section = el("section", "ostep");
  section.dataset.step = String(STEP.DONE);
  const heading = stepHeading(STEP.DONE, "You're set");
  section.append(heading);
  section.append(el("p", "lede", "The app will reload once to pick everything up."));
  const summary = el("div", "summary");
  section.append(summary);
  // WP WEB-FIX-1 (N5): where a failed vault-name save is reported — the
  // save now happens in finish(), and a toast would be wiped by the reload
  // that follows a successful one.
  const errLine = el("p", "errline");
  errLine.hidden = true;
  section.append(errLine);
  els.step3 = { section, heading, summary, errLine };
  return section;
}

function renderSummary() {
  const { summary } = els.step3;
  summary.replaceChildren();
  const rows = [
    ["Vault name", els.step1.nameInput.value.trim() || "(server default)"],
    ["API key", state.tested ? "verified" : "not verified"],
    ["Steam identity", state.steamStatus.configured ? "configured" : "not linked"],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.append(el("span", null, label), el("span", null, value));
    summary.appendChild(row);
  }
}

// ---------------------------------------------------------------------
// Chrome: header, track, footer nav, skip
// ---------------------------------------------------------------------

/** `els.step1.heading` / `els.step2.heading` / `els.step3.heading` by step
 * number — used by `render()` (accessible name) and the nav handlers
 * (focus-on-step-change). */
function headingForStep(step) {
  if (step === STEP.CONNECT) return els.step1.heading;
  if (step === STEP.STEAM) return els.step2.heading;
  return els.step3.heading;
}

/** Escape closes the overlay, but ONLY in `mode: "reconnect"` — a
 * first-run open has nothing behind it to reveal, so Escape is
 * intentionally a no-op there (module header "Dialog semantics"). Bound to
 * `document`, not `root`: even with the WP 4a.8 focus trap in place, Escape
 * must keep working no matter which descendant of `root` currently holds
 * focus, the same reasoning `sheet-dialog.js`'s identical choice documents.
 * Guarded on `root` actually being visible so it is a no-op the rest of the
 * time the module is loaded. */
function onDocumentKeydown(event) {
  if (event.key !== "Escape") return;
  if (!root || root.classList.contains("gone")) return;
  if (state.mode !== "reconnect") return;
  event.preventDefault();
  closeOnboarding();
}

function buildOverlay() {
  root = el("div", "onb gone");
  root.id = "onboarding-root";
  // Dialog semantics (review should-fix, cheap 80% — see module header):
  // aria-labelledby is kept in sync with the current step's heading by
  // render(), since the "You're set" step is a DIFFERENT dialog title, not
  // a static one set once here.
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  document.addEventListener("keydown", onDocumentKeydown);

  const header = el("header", "onbhead");
  const mark = el("div", "mark");
  mark.appendChild(staticIcon(MARK_SVG));
  const titleWrap = el("div");
  titleWrap.style.flex = "1";
  const wordmark = el("div", "wordmark", "SteamHangar");
  const stepnum = el("div", "stepnum", "");
  titleWrap.append(wordmark, stepnum);
  const skipBtn = el("button", "btn ghost sm", "Skip");
  skipBtn.type = "button";
  skipBtn.addEventListener("click", onSkip);
  header.append(mark, titleWrap, skipBtn);

  const track = el("div", "track");
  const trackFill = el("i");
  track.appendChild(trackFill);

  const rest = el("div", "rest");
  const step1 = buildStep1();
  const step2 = buildStep2();
  const step3 = buildStep3();
  rest.append(step1, step2, step3);

  const footer = el("div", "onbfoot");
  const nav = el("div", "onbnav");
  const backBtn = el("button", "btn ghost wide", "Back");
  backBtn.type = "button";
  backBtn.addEventListener("click", () => {
    state.step = prevStep(state.step);
    render();
    // Focus the new step's heading (tabindex="-1", never a natural Tab
    // stop) so a screen reader announces the step changed (independent of
    // the WP 4a.8 Tab-trap below — this is about ANNOUNCING a step change,
    // not about containing focus).
    headingForStep(state.step).focus();
  });
  const nextBtn = el("button", "btn primary wide", "Continue");
  nextBtn.type = "button";
  nextBtn.addEventListener("click", () => {
    if (state.step === LAST_STEP) {
      finish();
      return;
    }
    state.step = nextStep(state.step, state);
    render();
    headingForStep(state.step).focus();
  });
  nav.append(backBtn, nextBtn);
  const demoLink = el("button", "skiplink", "Skip for now — browse in demo mode");
  demoLink.type = "button";
  demoLink.addEventListener("click", onDemoSkip);
  footer.append(nav, demoLink);

  root.append(header, track, rest, footer);
  document.body.appendChild(root);

  els = {
    ...els,
    root,
    stepnum,
    track: trackFill,
    steps: [step1, step2, step3],
    backBtn,
    nextBtn,
    demoLink,
  };
}

function render() {
  els.stepnum.textContent = stepTitle(state.step);
  els.track.style.width = progressPercent(state.step) + "%";
  for (const sec of els.steps) sec.classList.toggle("on", Number(sec.dataset.step) === state.step);
  els.backBtn.style.display = state.step > FIRST_STEP ? "" : "none";
  els.nextBtn.disabled = !canAdvance(state.step, state);
  els.nextBtn.textContent = state.step === LAST_STEP ? "Go to library" : "Continue";
  els.demoLink.style.display = state.step === LAST_STEP ? "none" : "block";
  // Dialog semantics: the accessible name tracks the CURRENT step's
  // heading — "Connect to your vault" and "You're set" are different
  // dialogs from an assistive-tech point of view, not one static title.
  root.setAttribute("aria-labelledby", headingForStep(state.step).id);
  if (state.step === STEP.DONE) renderSummary();
}

function onSkip() {
  if (state.mode === "first-run") {
    onDemoSkip();
    return;
  }
  closeOnboarding(); // reconnect flow: bail out with whatever was already configured, unchanged
}

function onDemoSkip() {
  setDemoMode(true);
  window.location.reload();
}

/** The vault-name write `finish()` owes (WP WEB-FIX-1, N5 — moved here from
 * the "Test connection" handler). Returns `null` when there is nothing to
 * send: no verified settings snapshot, a blank name, a read-only vault, or
 * a name equal to the server's current one. */
function pendingVaultName() {
  if (!state.tested || !state.settings) return null;
  const desiredName = els.step1.nameInput.value.trim();
  if (!desiredName || state.settings.readonly) return null;
  const entry = (state.settings.settings || []).find((s) => s.key === "vault_name");
  if (!entry || desiredName === (entry.effective || "")) return null;
  return desiredName;
}

/** Complete the flow: save the vault name if the user changed it, then
 * reload so every module re-initializes against the stored key
 * (module header, step 3). A failed name save is shown on this step and
 * does NOT reload — the user can retry ("Go to library" again), or go Back
 * and clear the name to continue without it. Demo mode was already ended
 * at key-test time (B1, step 1's handler); `setDemoMode(false)` here is the
 * belt to that suspenders, so a verified connect can never reload into demo. */
async function finish() {
  if (finishing) return;
  finishing = true;
  // Busy feedback while a step-2 save or the vault-name save runs:
  // aria-disabled + the `finishing` guard, never `disabled` (which drops
  // focus to <body>; LEARNINGS, WP WEB-FEAT-1). Restored on every path that
  // leaves the overlay open (a failed name save waits for a retry).
  const btn = els.nextBtn;
  const label = btn.textContent;
  btn.setAttribute("aria-disabled", "true");
  btn.textContent = "Saving…";
  try {
    await finishOnce();
  } finally {
    finishing = false;
    btn.setAttribute("aria-disabled", "false");
    btn.textContent = label;
  }
}

async function finishOnce() {
  const { errLine } = els.step3;
  errLine.hidden = true;
  // WP WEB-FEAT-2: a step-2 lookup may still be saving the SteamID64; the
  // reload below would abort that PATCH. It never rejects (runLookup
  // reports every failure in step 2's lines).
  if (pendingLookup) await pendingLookup;
  const name = pendingVaultName();
  if (name !== null) {
    try {
      await api.patchSettings({ vault_name: name });
    } catch (err) {
      errLine.hidden = false;
      errLine.textContent = `Vault name not saved: ${errorText(err)} — try again, or go Back and clear the name to continue without it.`;
      return;
    }
  }
  if (state.tested) setDemoMode(false);
  window.location.reload();
}

/** Whether the overlay is currently showing (WP WEB-FIX-1, B2 — the guard
 * `components/auth-recovery.js` uses so a store-driven reconnect never
 * resets a flow the user is already in). */
export function isOnboardingOpen() {
  return !!root && !root.classList.contains("gone");
}

// ---------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------

/** Show the onboarding overlay. `mode: "reconnect"` is used from Settings'
 * "Reconnect / switch account" — Skip then just closes instead of enabling
 * demo mode, since a working connection may already exist. `notice`
 * (WP WEB-FIX-1, B2) is shown in step 1's error line so a reconnect the app
 * opened on its own (a 401 from the stored key) says why it appeared. */
export function openOnboarding({ mode = "first-run", notice = "" } = {}) {
  if (!root) buildOverlay();
  state = freshState();
  state.mode = mode;
  // Reconnect is always a click (Settings' "Start" button) — capture it as
  // the element to return focus to on close. First-run has no such
  // invoker (it opens itself at app startup): leave it null so
  // closeOnboarding()'s refocus step is a no-op there.
  invokerEl = mode === "reconnect" ? document.activeElement : null;
  els.step1.keyInput.value = "";
  els.step1.nameInput.value = "";
  els.step1.errLine.hidden = !notice;
  els.step1.errLine.textContent = notice;
  els.step2.idInput.value = "";
  els.step2.setSaveNote("");
  renderLookupResult();
  els.step3.errLine.hidden = true;
  render();
  if (getStoredApiKey()) {
    // Only worth asking on reconnect — a first run has no valid vault API
    // key yet, so this call would just 401 (harmless, but pure console
    // noise: DevTools logs every non-2xx fetch regardless of the JS-level
    // rejection handler below).
    api.getSteamKey().then(
      (status) => {
        state.steamStatus = status;
        renderSteamStatus();
      },
      () => {}, // offline/unexpected — leave the default "not configured" state
    );
  }
  root.classList.remove("gone");
  document.body.classList.add("onboarding");
  pushModal(root); // WP 4a.8: #app goes inert/aria-hidden while the overlay is up
  // Move focus to the first real control (module header "Dialog
  // semantics") — not the heading: this is the START of the flow, there is
  // nothing to announce a "change" from yet.
  els.step1.nameInput.focus();
}

/** Hide the overlay. WP WEB-FIX-1 round 2 (S1): if a key test succeeded
 * in this open, the key is already stored and demo mode already ended (B1),
 * so closing without finishing (Skip or Escape) would leave the app
 * half-switched — polling hits the real vault while views still show their
 * demo-era state. Reload the same way `finish()` does so every module
 * re-initializes against the stored key. */
export function closeOnboarding() {
  if (!root) return;
  root.classList.add("gone");
  document.body.classList.remove("onboarding");
  popModal(root);
  if (invokerEl) {
    invokerEl.focus();
    invokerEl = null;
  }
  if (state.switched) {
    state.switched = false; // one reload per open, even if called twice
    window.location.reload();
  }
}

/** Call once at app startup. Opens the flow automatically on a genuine
 * first run (no stored vault API key, demo mode not already chosen) — see
 * lib/onboarding-steps.js's `shouldShowOnboarding`. */
export function maybeShowOnboardingOnStartup() {
  if (shouldShowOnboarding({ hasApiKey: !!getStoredApiKey(), demoMode: isDemoMode() })) {
    openOnboarding({ mode: "first-run" });
  }
}
