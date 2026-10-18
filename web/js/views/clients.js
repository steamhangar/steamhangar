/**
 * Clients view (WP D15c).
 *
 * Every machine the vault knows, from `GET /v1/clients/overview` (WP D15b):
 * PCs running vault-agent and agentless addresses that only appear in the
 * cache's event log (a phone, a console, a PC without the agent). Per
 * client: connected or not, what is loading now, installed games (agent
 * PCs only), what was loaded recently, bytes and hit rate for the selected
 * range, and when it was last active. Wording and the honest-state rules
 * live in `lib/clients-page.js` and `lib/traffic-view.js`.
 *
 * **One card per device (WP CLIENTS-1).** The agent data of
 * `GET /v1/clients` (store `clients` snapshot: last report, agent version,
 * bypass verdict) is merged into the matching card by
 * `lib/clients-page.js`'s `mergeAgentData` (rule: equal `client_id`). The
 * separate "PCs running vault-agent" list and "Manage PCs" are gone; an
 * agent card carries the presence line, the bypass note and a Remove button
 * (the sheet's confirm, `requestPcRemoval`).
 *
 * **A nav entry, not a sheet (user decision 2026-10-13, D15).** This
 * diverges from the WP 4a.1 ruling "Clients is a sheet, not a nav item"
 * (docs/LEARNINGS.md, Web UI): the user asked for its own page under
 * Library. The PCs sheet (`components/clients-sheet.js`) stays, reached
 * from the bypass banner and the notifications.
 *
 * **Add a device (D15, CLIENTS-2).** The header button opens
 * `components/add-device-sheet.js` (pairing and the agent install);
 * Settings keeps a pointer.
 *
 * **UI kit (WP CLIENTS-4, ADR-0028).** The section carries `kit` and
 * composes from css/kit.css: `.page-head`, four `.tile`s (`summaryTiles`:
 * Connected, Loaded, Hit rate, Agents with the outdated pill), a `.sechd`
 * ("Devices" + the range `.seg`) and the cards, one per row (operator
 * decision 2026-10-17). A card is `components/client-card.js` rendering
 * `clientCardModel`; this view composes, wires the actions and keeps the
 * module state (range, remove errors, rename dialog).
 *
 * Polling: the overview is fetched while this view is on screen only
 * (`createViewPoll`, the store's own loop machinery), and stopped on
 * navigation. WP WEB-FIX-20.2: every mount fetches the overview AND the
 * store's `GET /v1/clients` at once (`store.refresh`), and while the page
 * is visible both are refreshed every {@link CLIENTS_POLL_MS} (5 s), so a
 * client that started loading shows up without a page reload. A hidden tab
 * parks the poll (no requests); leaving the view stops it. The "Loading
 * now" box itself can be no newer than the server's last event-log sweep
 * (`loading_now` is computed over the swept window, see
 * api/vault_api/routers/traffic.py); the poll picks a new sweep up within
 * 5 s instead of 30 s. The list is rebuilt per answer or tick.
 *
 * **Rename (WP D15-NAMES).** Every card whose row carries a `name_addr`
 * has a "Rename" button. It opens a small dialog (the `.dialog-backdrop`/
 * `.dialog` markup of the other confirm dialogs, stacked through
 * `lib/modal-stack.js`: Escape closes it, the page behind is inert) with
 * one labelled text field, the address the name is stored for, and Save /
 * Cancel, plus "Remove name" when the client has one. Save sends
 * `PUT /v1/client-names/ip/{name_addr}`, Remove `DELETE` (its own 404 =
 * already removed); the dialog stays open on failure and shows the error
 * in a `role=alert` line. The card title is the server's `display_name`;
 * the rule "an explicit name wins over the agent's PC name" lives on the
 * server (`api/vault_api/client_names.py`). Because the cards now hold a
 * button, a rebuild puts focus back on the same card's Rename button when
 * it had it, and closing the dialog focuses the CURRENT card's button (a
 * poll may have rebuilt the list meanwhile). Wording:
 * `lib/client-names.js`.
 */

import { store } from "../store-singleton.js";
import { createViewPoll } from "../store.js";
import { api, getStoredApiKey, isDemoMode } from "../api.js";
import { ERROR_KINDS } from "../errors.js";
import { onViewChange } from "../router.js";
import { requestPcRemoval } from "../components/clients-sheet.js";
import { openAddDeviceSheet } from "../components/add-device-sheet.js";
import { createRangeSegs } from "../components/range-segs.js";
import { showToast } from "../components/toast.js";
import { pushModal, popModal } from "../lib/modal-stack.js";
import {
  canRename,
  hasCustomName,
  isNameAlreadyGone,
  nameRemovedToastText,
  normalizeClientName,
  renameErrorText,
  renameHint,
  renamedToastText,
  renameTitle,
} from "../lib/client-names.js";
import { createClientCard } from "../components/client-card.js";
import { createPill } from "../components/pill.js";
import { FEED_TEXT, feedNote, feedState, isRange, joinNotes, refreshFailedText } from "../lib/traffic-view.js";
import {
  AGENTLESS_NOTE,
  NO_CLIENTS_TEXT,
  clientCardModel,
  clientName,
  clientsSummaryText,
  isAddressClient,
  isAgentClient,
  mergeAgentData,
  outdatedAgentsPillText,
  summaryTiles,
} from "../lib/clients-page.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed.";
}

/** Module state; survives re-mounts. `data` is the last overview answer
 * for `range`; `unsupported` is a 404 (a server older than D15b);
 * `noFigures` is the last known verdict "no statistics to range over"
 * (feed disabled or unsupported), kept while an answer is pending. */
const state = { range: "7d", data: null, error: null, unsupported: false, noFigures: false };

/** `server_version` of `GET /v1/settings`, read on each visit; null while
 * unknown (no outdated-agent warning then). */
let serverVersion = null;

/** Inline remove errors by client_id (kept across list rebuilds). */
const removeErrors = new Map();

/** client_id -> ms of a successful Remove. Hides the PC until the store's
 * agent snapshot has caught up (no stale agent-only card comes back), for
 * at most REMOVED_HIDE_MS: an agent that re-reports is listed again. */
const removedIds = new Map();
const REMOVED_HIDE_MS = 30_000;

function hiddenRemoved(id, nowMs = Date.now()) {
  const at = removedIds.get(id);
  if (at === undefined) return false;
  if (nowMs - at > REMOVED_HIDE_MS) {
    removedIds.delete(id);
    return false;
  }
  return true;
}

/** The mounted section's nodes, or null (see downloads.js on why
 * `onViewChange` nulling this is the whole staleness signal). */
let els = null;

/** WP WEB-FIX-20.2: the Clients page's refresh cadence while it is visible. */
export const CLIENTS_POLL_MS = 5000;

const poll = createViewPoll({
  fetcher: () => {
    // The agent data (presence, bypass verdict) on the same cadence: a
    // nudge of the store's own loop, coalesced while one is in flight.
    store.refresh("clients");
    return api.clientsOverview(state.range);
  },
  intervalMs: CLIENTS_POLL_MS,
  onTick: (data) => {
    // An answer for a range the user has since left: the refresh the range
    // change asked for is already queued behind it.
    if (!data || data.range !== state.range) return;
    state.data = data;
    state.error = null;
    state.unsupported = false;
    state.noFigures = feedState(data) === "disabled";
    paint();
  },
  onError: (err) => {
    if (err && err.kind === ERROR_KINDS.NOT_FOUND) {
      // A server older than D15b: polling again cannot change that answer
      // (an upgrade restarts vault-api; the next visit to this view asks
      // again), so the poll stops here.
      state.unsupported = true;
      state.noFigures = true;
      poll.stop();
    } else {
      state.error = errorText(err);
    }
    paint();
  },
});

function setRange(range) {
  if (!isRange(range) || range === state.range) return;
  state.range = range;
  state.data = null;
  state.error = null;
  paint();
  poll.refresh();
}

/** One card: the view-model of `lib/clients-page.js` rendered by
 * `components/client-card.js`; this view only wires the actions. */
function buildCard(client, range, nowMs) {
  const agent = client.agent || null;
  const model = clientCardModel(client, {
    range,
    serverVersion,
    nowMs,
    removeError: agent ? removeErrors.get(agent.client_id) || null : null,
  });
  return createClientCard(model, {
    onRename: () => openRename(client),
    onRemove: () => openRemove(model.removeId),
    onUpdateCommand: () => openAddDeviceSheet({ tab: null }),
  });
}

function buildEmptyState() {
  const box = el("div", "cl-empty");
  box.dataset.role = "clients-empty";
  const btn = el("button", "btn pri sm", "Add a device");
  btn.type = "button";
  btn.dataset.role = "add-device-empty";
  btn.addEventListener("click", () => openAddDeviceSheet());
  box.append(el("p", "hint", NO_CLIENTS_TEXT), btn);
  return box;
}

/** The button with `role` of the card with `key`, in the list as it is NOW. */
function cardButton(key, role) {
  if (!els || key == null) return null;
  const card = Array.from(els.list.querySelectorAll(".ccard")).find((c) => c.dataset.clientKey === key);
  return card ? card.querySelector(`[data-role="${role}"]`) : null;
}

function openRemove(clientId) {
  const key = `agent:${clientId}`;
  if (removeErrors.delete(clientId)) paint();
  requestPcRemoval(clientId, {
    focusTarget: () => cardButton(key, "remove-pc"),
    onError: (message) => {
      removeErrors.set(clientId, message);
      paint();
    },
    onRemoved: () => {
      removeErrors.delete(clientId);
      removedIds.set(clientId, Date.now());
      paint();
      poll.refresh();
      // The card is gone: the heading is the focus landing spot.
      if (els && els.h1) els.h1.focus();
    },
  });
}

/** The Rename button of the card with `key`, in the list as it is NOW. */
function renameButtonFor(key) {
  return cardButton(key, "rename-client");
}

function paintTiles(clients, range) {
  els.tiles.replaceChildren();
  const tiles = clients ? summaryTiles(clients, range, serverVersion) : [];
  for (const t of tiles) {
    const tile = el("div", t.hi ? "tile hi" : "tile");
    tile.dataset.tile = t.key;
    const v = el("div", "v", t.value);
    if (t.unit) v.appendChild(el("small", null, t.unit));
    tile.append(el("div", "k", t.label), v);
    if (typeof t.meter === "number") {
      const m = el("div", "meter");
      const bar = el("i");
      bar.style.setProperty("width", `${t.meter}%`);
      m.appendChild(bar);
      tile.appendChild(m);
    }
    const n = el("div", "n", t.detail);
    if (t.outdated) {
      const pill = createPill({ kind: "warn", text: outdatedAgentsPillText(t.outdated), tone: "stale" });
      pill.dataset.role = "agents-outdated";
      n.textContent = "";
      n.appendChild(pill);
    }
    tile.appendChild(n);
    els.tiles.appendChild(tile);
  }
  els.tiles.hidden = !tiles.length;
}

/** The client key of the card holding `node`, or null. */
function cardKeyOf(node) {
  for (let n = node; n; n = n.parentNode) {
    if (n.dataset && n.dataset.clientKey) return n.dataset.clientKey;
    if (els && n === els.list) return null;
  }
  return null;
}

/** Repaint the note, the sub-line and the list from `state`. */
function paint() {
  if (!els) return;
  const data = state.data;
  let note = null;
  if (state.unsupported) note = FEED_TEXT.unsupported;
  else if (state.error && !data) note = `Could not load clients: ${state.error}`;
  else if (data) note = joinNotes(state.error ? refreshFailedText(state.error) : null, feedNote(data));
  els.note.textContent = note || "";
  els.note.hidden = !note;
  // No statistics (feed disabled, old server): no range selector either.
  els.range.hidden = state.noFigures;

  // A rebuild must not take focus from a card's Rename or Remove button.
  const active = document.activeElement;
  const focusRole =
    active && active.dataset && (active.dataset.role === "rename-client" || active.dataset.role === "remove-pc") && els.list.contains(active)
      ? active.dataset.role
      : null;
  const focusKey = focusRole ? cardKeyOf(active) : null;
  els.list.replaceChildren();
  const agentRows = agentSnapshot();
  if (state.unsupported) {
    // No overview: the agent PCs alone, still listed and removable.
    const only = mergeAgentData([], agentRows);
    els.sub.textContent = only.length ? clientsSummaryText(only) : "";
    paintTiles(only.length ? only : null, state.range);
    const nowMs = Date.now();
    for (const client of only) els.list.appendChild(buildCard(client, state.range, nowMs));
    els.agentlessNote.hidden = true;
    return;
  }
  if (!data) {
    els.sub.textContent = "";
    paintTiles(null, state.range);
    if (!state.error) els.list.appendChild(el("p", "empty", "Loading clients…"));
    els.agentlessNote.hidden = true;
    return;
  }
  const shownRows = (Array.isArray(data.clients) ? data.clients : []).filter((c) => !(isAgentClient(c) && hiddenRemoved(c.client_id)));
  const clients = mergeAgentData(shownRows, agentRows);
  els.sub.textContent = clientsSummaryText(clients);
  paintTiles(clients.length ? clients : null, data.range);
  if (!clients.length) els.list.appendChild(buildEmptyState());
  const nowMs = Date.now();
  for (const client of clients) els.list.appendChild(buildCard(client, data.range, nowMs));
  els.agentlessNote.hidden = !clients.some((c) => isAddressClient(c));
  if (focusKey) {
    const again = cardButton(focusKey, focusRole);
    if (again) again.focus();
  }
}

// ---------------------------------------------------------------------
// Rename dialog (WP D15-NAMES)
// ---------------------------------------------------------------------

const renameBackdrop = document.createElement("div");
renameBackdrop.className = "dialog-backdrop";
const renameDialog = document.createElement("form");
renameDialog.className = "dialog cl-rename-dialog";
renameDialog.setAttribute("role", "dialog");
renameDialog.setAttribute("aria-modal", "true");
renameDialog.setAttribute("aria-labelledby", "cl-rename-title");
renameDialog.setAttribute("aria-describedby", "cl-rename-hint");
renameDialog.dataset.role = "rename-dialog";
renameDialog.noValidate = true;
const renameTitleEl = el("h3");
renameTitleEl.id = "cl-rename-title";
const renameField = el("div", "field");
const renameLabel = el("label", null, "Name");
renameLabel.htmlFor = "cl-rename-input";
const renameInput = document.createElement("input");
renameInput.id = "cl-rename-input";
renameInput.className = "inp txt";
renameInput.type = "text";
renameInput.autocomplete = "off";
renameInput.spellcheck = false;
renameInput.dataset.role = "rename-input";
renameInput.setAttribute("dir", "auto");
renameInput.setAttribute("aria-describedby", "cl-rename-hint cl-rename-error");
renameField.append(renameLabel, renameInput);
const renameHintEl = el("p", "foot-note");
renameHintEl.id = "cl-rename-hint";
const renameError = el("p", "errline");
renameError.id = "cl-rename-error";
renameError.dataset.role = "rename-error";
renameError.setAttribute("role", "alert");
renameError.hidden = true;
const renameRow = el("div", "row");
const renameClear = el("button", "btn ghost sm", "Remove name");
renameClear.type = "button";
renameClear.dataset.role = "rename-clear";
const renameCancel = el("button", "btn ghost sm", "Cancel");
renameCancel.type = "button";
renameCancel.dataset.role = "rename-cancel";
const renameSave = el("button", "btn primary sm", "Save");
renameSave.type = "submit";
renameSave.dataset.role = "rename-save";
renameRow.append(renameClear, renameCancel, renameSave);
renameDialog.append(renameTitleEl, renameField, renameHintEl, renameError, renameRow);
renameBackdrop.appendChild(renameDialog);
document.body.appendChild(renameBackdrop);

const renameFlow = { client: null, invokerEl: null, busy: false };

function setRenameError(message) {
  renameError.textContent = message || "";
  renameError.hidden = !message;
}

function setRenameBusy(busy, label) {
  renameFlow.busy = busy;
  // aria-disabled + click guards, not `disabled`: a disabled button drops
  // keyboard focus (LEARNINGS, WP WEB-FEAT-1).
  for (const b of [renameClear, renameCancel, renameSave]) {
    if (busy) b.setAttribute("aria-disabled", "true");
    else b.removeAttribute("aria-disabled");
  }
  renameSave.textContent = busy && label === "save" ? "Saving…" : "Save";
  renameClear.textContent = busy && label === "clear" ? "Removing…" : "Remove name";
}

function openRename(client) {
  if (renameFlow.busy || !canRename(client)) return;
  renameFlow.client = client;
  renameFlow.invokerEl = document.activeElement;
  renameTitleEl.textContent = renameTitle(clientName(client));
  renameInput.value = hasCustomName(client) ? client.name : "";
  renameInput.placeholder = clientName(client);
  renameHintEl.textContent = renameHint(client);
  renameClear.hidden = !hasCustomName(client);
  setRenameError(null);
  setRenameBusy(false);
  renameBackdrop.classList.add("on");
  pushModal(renameBackdrop, () => {
    if (!renameFlow.busy) closeRename();
  });
  renameInput.focus();
  if (typeof renameInput.select === "function") renameInput.select();
}

function closeRename() {
  if (!renameBackdrop.classList.contains("on")) return;
  renameBackdrop.classList.remove("on");
  popModal(renameBackdrop);
  const key = renameFlow.client ? renameFlow.client.key : null;
  const invoker = renameFlow.invokerEl;
  renameFlow.client = null;
  renameFlow.invokerEl = null;
  // A poll may have rebuilt the list while the dialog was open: prefer the
  // card's CURRENT button over the captured (maybe detached) one.
  const target = renameButtonFor(key) || invoker;
  if (target && typeof target.focus === "function") target.focus();
}

async function saveRename() {
  const client = renameFlow.client;
  if (renameFlow.busy || !client) return;
  const checked = normalizeClientName(renameInput.value);
  if (!checked.ok) {
    setRenameError(checked.error);
    renameInput.focus();
    return;
  }
  setRenameBusy(true, "save");
  try {
    await api.setClientName(client.name_addr, checked.value);
  } catch (err) {
    setRenameBusy(false);
    setRenameError(renameErrorText(err));
    return;
  }
  setRenameBusy(false);
  closeRename();
  showToast(renamedToastText(checked.value));
  poll.refresh();
  store.refreshNow();
}

async function clearRename() {
  const client = renameFlow.client;
  if (renameFlow.busy || !client) return;
  setRenameBusy(true, "clear");
  try {
    await api.clearClientName(client.name_addr);
  } catch (err) {
    if (!isNameAlreadyGone(err)) {
      setRenameBusy(false);
      setRenameError(renameErrorText(err));
      return;
    }
  }
  // The toast names what the card shows NOW: the client's display_name in
  // the server's next answer (the DELETE itself answers 204, no body).
  let shownAs = null;
  const range = state.range;
  try {
    const data = await api.clientsOverview(range);
    const row = data && Array.isArray(data.clients) ? data.clients.find((c) => c.key === client.key) : null;
    shownAs = row && typeof row.display_name === "string" ? row.display_name : null;
    if (data && data.range === state.range) {
      state.data = data;
      state.error = null;
      paint();
    }
  } catch {
    shownAs = null; // the toast then says only "Name removed."
  }
  setRenameBusy(false);
  closeRename();
  showToast(nameRemovedToastText(shownAs));
  poll.refresh();
  store.refreshNow();
}

renameDialog.addEventListener("submit", (event) => {
  if (event && typeof event.preventDefault === "function") event.preventDefault();
  saveRename();
});
renameClear.addEventListener("click", () => clearRename());
renameCancel.addEventListener("click", () => {
  if (!renameFlow.busy) closeRename();
});

// ---------------------------------------------------------------------
// Agent data and "Add a device"
// ---------------------------------------------------------------------

/** The store's latest `GET /v1/clients` snapshot, or null before the first
 * answer (the cards then carry the overview's own fields only). */
function agentSnapshot() {
  const snap = store.snapshot("clients");
  return Array.isArray(snap) ? snap.filter((a) => !hiddenRemoved(a.client_id)) : null;
}

store.subscribe("clients", (payload) => {
  if (!els || !payload || payload.error) return;
  if (Array.isArray(payload.items)) {
    const ids = new Set(payload.items.map((a) => a.client_id));
    for (const id of removedIds.keys()) if (!ids.has(id)) removedIds.delete(id);
  }
  paint();
});

// ---------------------------------------------------------------------
// View lifecycle
// ---------------------------------------------------------------------

onViewChange((view) => {
  if (view === "clients") return;
  // The rename dialog belongs to this page; an in-flight save still ends.
  if (renameBackdrop.classList.contains("on")) {
    renameBackdrop.classList.remove("on");
    popModal(renameBackdrop);
    // An in-flight save/remove keeps its own reference to the client.
    renameFlow.client = null;
    renameFlow.invokerEl = null;
  }
  els = null;
  poll.stop();
});

const PLUS_ICON =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';

export function renderClients() {
  // `kit`: this page composes from css/kit.css (ADR-0028, WP CLIENTS-4).
  const section = el("section", "view view-clients kit");
  const head = el("div", "page-head");
  const title = el("div");
  const h1 = el("h1", null, "Clients");
  h1.tabIndex = -1;
  const sub = el("div", "sub");
  sub.dataset.role = "clients-sub";
  title.append(h1, sub);
  const addBtn = el("button", "btn pri");
  addBtn.type = "button";
  addBtn.dataset.role = "add-device";
  addBtn.setAttribute("aria-label", "Add a device");
  const plus = el("span", "btn-ico");
  plus.innerHTML = PLUS_ICON;
  addBtn.append(plus, "Add a device");
  addBtn.addEventListener("click", () => openAddDeviceSheet());
  head.append(title, addBtn);

  const tiles = el("section", "tiles cl-tiles");
  tiles.dataset.role = "clients-tiles";
  tiles.setAttribute("aria-label", "Summary");
  tiles.hidden = true;

  const range = createRangeSegs({
    label: "Statistics range",
    value: state.range,
    onChange: setRange,
    role: "clients-range",
  });
  // The kit's segmented control, not the legacy `.segs` (app.css).
  range.el.className = "seg";
  const sechd = el("div", "sechd");
  sechd.append(el("span", "sec", "Devices"), range.el);

  const note = el("p", "hint cl-note");
  note.dataset.role = "clients-note";
  note.hidden = true;
  // One card per row (operator decision 2026-10-17); the list is the
  // container the cards' zone stacking queries (kit.css `.ccards`).
  const list = el("div", "ccards");
  list.dataset.role = "clients-list";
  const agentlessNote = el("p", "foot-note", AGENTLESS_NOTE);
  agentlessNote.dataset.role = "agentless-note";
  agentlessNote.hidden = true;

  range.el.hidden = state.noFigures;
  section.append(head, tiles, sechd, note, list, agentlessNote);

  els = { section, h1, sub, tiles, range: range.el, note, list, agentlessNote };
  paint();
  // Same gate as the store's own loops (store-singleton.js): no key and no
  // demo mode means the onboarding overlay is up and every request would
  // be a 401.
  if (getStoredApiKey() || isDemoMode()) {
    // WP WEB-FIX-20.2: the overview AND the agent snapshot now, not on the
    // store's next 20 s tick (the fetcher nudges the store loop).
    poll.start();
    poll.refresh();
    api.getSettings().then(
      (s) => {
        serverVersion = s && typeof s.server_version === "string" ? s.server_version : null;
        paint();
      },
      () => {
        serverVersion = null;
        paint();
      },
    );
  }
  return section;
}
