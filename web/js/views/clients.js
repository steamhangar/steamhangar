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
 * Settings keeps a pointer. Cards sit in an auto-fill grid under summary
 * tiles (`summaryTiles`, real figures only).
 *
 * Polling: the overview is fetched while this view is on screen only
 * (`createViewPoll`, 30 s, the store's own loop machinery), and stopped on
 * navigation. The agent data follows the store's `clients` ticks. The list
 * is rebuilt per answer or tick.
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
import { createViewPoll, VIEW_POLL_MS } from "../store.js";
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
import {
  BYPASS_EXPLANATION,
  UNREMOVABLE_SLASH_NOTE,
  describeBypassClient,
  isRemovableClientId,
  presenceLine,
} from "../lib/clients-view.js";
import { FEED_TEXT, feedNote, feedState, isRange, joinNotes, refreshFailedText } from "../lib/traffic-view.js";
import {
  AGENTLESS_NOTE,
  NO_CLIENTS_TEXT,
  clientKindText,
  clientName,
  clientsSummaryText,
  connectionChip,
  installedText,
  isAddressClient,
  isAgentClient,
  isRunnerClient,
  lastActiveText,
  loadingNowText,
  mergeAgentData,
  recentAppsText,
  statsText,
  outdatedAgentInfo,
  outdatedAgentText,
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

const poll = createViewPoll({
  fetcher: () => api.clientsOverview(state.range),
  intervalMs: VIEW_POLL_MS,
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

function line(text, role) {
  const node = el("div", "sm cl-line", text);
  if (role) node.dataset.role = role;
  return node;
}

function buildCard(client, range, nowMs) {
  const card = el("div", "jobcard clcard");
  card.dataset.clientKey = String(client.key);
  card.dataset.kind = isAgentClient(client) ? "agent" : isRunnerClient(client) ? "runner" : "address";
  const agent = client.agent || null;
  if (agent && agent.bypass_suspected) card.classList.add("bypass");

  const top = el("div", "jobtop");
  const info = el("div");
  const title = el("div", "nm", clientName(client));
  // Operator-chosen text: its own direction, never the page's.
  title.setAttribute("dir", "auto");
  info.append(title, line(clientKindText(client), "client-kind"));
  const chip = connectionChip(client);
  const chipEl = el("span", chip.cls);
  chipEl.dataset.role = "connection";
  const dot = el("span", "pdot");
  dot.setAttribute("aria-hidden", "true");
  chipEl.append(dot, el("span", null, chip.word));
  top.append(info, chipEl);
  card.appendChild(top);

  card.appendChild(line(loadingNowText(client), "loading-now"));
  const installed = installedText(client);
  if (installed) card.appendChild(line(installed, "installed"));
  const stats = statsText(client, range);
  if (stats) card.appendChild(line(stats, "stats"));
  const recent = recentAppsText(client);
  if (recent) card.appendChild(line(recent, "recent"));
  card.appendChild(line(lastActiveText(client, nowMs), "last-active"));
  const outdated = outdatedAgentInfo(client, serverVersion);
  if (outdated) {
    const upd = el("div", "cl-upd");
    upd.dataset.role = "agent-outdated";
    const btn = el("button", "btn ghost sm", "Update command");
    btn.type = "button";
    btn.dataset.role = "update-command";
    btn.setAttribute("aria-label", `Update command for ${clientName(client)}`);
    btn.addEventListener("click", () => openAddDeviceSheet({ tab: null }));
    upd.append(el("strong", null, "Agent outdated"), el("p", null, outdatedAgentText(outdated)), btn);
    card.appendChild(upd);
  }
  if (agent) {
    // Server verdicts, read as sent (lib/clients-view.js): never recomputed.
    card.appendChild(line(presenceLine(agent, nowMs), "agent-presence"));
    if (agent.bypass_suspected) {
      card.appendChild(line(`Bypassing the cache · ${describeBypassClient(agent)}`, "bypass"));
      card.appendChild(el("p", "hint", BYPASS_EXPLANATION));
    }
  }
  const removable = agent && isRemovableClientId(agent.client_id);
  if (canRename(client) || removable) {
    const acts = el("div", "jobacts");
    if (canRename(client)) {
      const btn = el("button", "btn ghost sm", "Rename");
      btn.type = "button";
      btn.dataset.role = "rename-client";
      // Every card has one: the accessible name says which (label-in-name).
      btn.setAttribute("aria-label", renameTitle(clientName(client)));
      btn.addEventListener("click", () => openRename(client));
      acts.appendChild(btn);
    }
    if (removable) {
      const rm = el("button", "btn ghost sm", "Remove");
      rm.type = "button";
      rm.dataset.role = "remove-pc";
      rm.setAttribute("aria-label", `Remove ${agent.client_id}`);
      rm.addEventListener("click", () => openRemove(agent.client_id));
      acts.appendChild(rm);
    }
    card.appendChild(acts);
  }
  if (agent && !removable) {
    const note = el("p", "foot-note", UNREMOVABLE_SLASH_NOTE);
    note.dataset.role = "remove-unavailable";
    card.appendChild(note);
  }
  if (agent) {
    const message = removeErrors.get(agent.client_id);
    const err = el("p", "errline", message || "");
    err.dataset.role = "remove-error";
    err.setAttribute("role", "alert");
    err.hidden = !message;
    card.appendChild(err);
  }
  return card;
}

/** The button with `role` of the card with `key`, in the list as it is NOW. */
function cardButton(key, role) {
  if (!els || key == null) return null;
  const card = Array.from(els.list.querySelectorAll(".clcard")).find((c) => c.dataset.clientKey === key);
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
    const tile = el("div", t.warn ? "cl-tile warn" : "cl-tile");
    tile.dataset.tile = t.key;
    tile.append(el("div", "k", t.label), el("div", "v", t.value), el("div", "n", t.detail));
    els.tiles.appendChild(tile);
  }
  els.tiles.hidden = !tiles.length;
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
  const focusKey =
    focusRole && active.parentNode && active.parentNode.parentNode ? active.parentNode.parentNode.dataset.clientKey : null;
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
  if (!clients.length) els.list.appendChild(el("p", "hint", NO_CLIENTS_TEXT));
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

export function renderClients() {
  const section = el("section", "view view-clients");
  const head = el("div", "dl-head cl-head");
  const title = el("div");
  const h1 = el("h1", null, "Clients");
  h1.tabIndex = -1;
  const sub = el("span", "dl-sub");
  sub.dataset.role = "clients-sub";
  title.append(h1, sub);
  const addBtn = el("button", "btn primary", "Add a device");
  addBtn.type = "button";
  addBtn.dataset.role = "add-device";
  addBtn.setAttribute("aria-label", "Add a device");
  addBtn.addEventListener("click", () => openAddDeviceSheet());
  head.append(title, addBtn);

  const tiles = el("section", "cl-tiles");
  tiles.dataset.role = "clients-tiles";
  tiles.setAttribute("aria-label", "Summary");
  tiles.hidden = true;

  const range = createRangeSegs({
    label: "Statistics range",
    value: state.range,
    onChange: setRange,
    role: "clients-range",
  });

  const note = el("p", "hint cl-note");
  note.dataset.role = "clients-note";
  note.hidden = true;
  const list = el("div", "cl-list");
  list.dataset.role = "clients-list";
  const agentlessNote = el("p", "foot-note", AGENTLESS_NOTE);
  agentlessNote.dataset.role = "agentless-note";
  agentlessNote.hidden = true;

  range.el.hidden = state.noFigures;
  section.append(head, tiles, range.el, note, list, agentlessNote);

  els = { section, h1, sub, tiles, range: range.el, note, list, agentlessNote };
  paint();
  // Same gate as the store's own loops (store-singleton.js): no key and no
  // demo mode means the onboarding overlay is up and every request would
  // be a 401.
  if (getStoredApiKey() || isDemoMode()) {
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
