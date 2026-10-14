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
 * **A nav entry, not a sheet (user decision 2026-10-13, D15).** This
 * diverges from the WP 4a.1 ruling "Clients is a sheet, not a nav item"
 * (docs/LEARNINGS.md, Web UI): the user asked for its own page under
 * Library. The PCs sheet (`components/clients-sheet.js`) stays, reached
 * from the bypass banner, the notifications and the "Manage PCs" button
 * here.
 *
 * **PC add/manage moved here from Settings (D15).** The "PCs (agents)"
 * block of `views/settings.js` (agents summary, "Manage PCs" opening the
 * PCs sheet with its Remove buttons, "Add a device" opening
 * `components/add-device-sheet.js` for pairing and the agent install) is
 * built by this view now, unchanged in behaviour; Settings keeps a pointer.
 *
 * Polling: the overview is fetched while this view is on screen only
 * (`createViewPoll`, 30 s, the store's own loop machinery), and stopped on
 * navigation. The agents summary follows the store's `clients` ticks, as
 * it did in Settings. The list is rebuilt per answer; its cards hold no
 * focusable control, so a rebuild cannot take focus from anything.
 */

import { store } from "../store-singleton.js";
import { createViewPoll, VIEW_POLL_MS } from "../store.js";
import { api, getStoredApiKey, isDemoMode } from "../api.js";
import { ERROR_KINDS } from "../errors.js";
import { onViewChange } from "../router.js";
import { openClientsSheet } from "../components/clients-sheet.js";
import { openAddDeviceSheet } from "../components/add-device-sheet.js";
import { createRangeSegs } from "../components/range-segs.js";
import { agentsSummaryText } from "../lib/clients-view.js";
import { FEED_TEXT, feedNote, isRange } from "../lib/traffic-view.js";
import {
  AGENTLESS_NOTE,
  NO_CLIENTS_TEXT,
  clientKindText,
  clientName,
  clientsSummaryText,
  connectionChip,
  installedText,
  isAgentClient,
  lastActiveText,
  loadingNowText,
  recentAppsText,
  statsText,
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
 * for `range`; `unsupported` is a 404 (a server older than D15b). */
const state = { range: "7d", data: null, error: null, unsupported: false };

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
    paint();
  },
  onError: (err) => {
    if (err && err.kind === ERROR_KINDS.NOT_FOUND) state.unsupported = true;
    else state.error = errorText(err);
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
  card.dataset.kind = isAgentClient(client) ? "agent" : "address";

  const top = el("div", "jobtop");
  const info = el("div");
  info.append(el("div", "nm", clientName(client)), line(clientKindText(client), "client-kind"));
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
  return card;
}

/** Repaint the note, the sub-line and the list from `state`. */
function paint() {
  if (!els) return;
  const data = state.data;
  let note = null;
  if (state.unsupported) note = FEED_TEXT.unsupported;
  else if (state.error && !data) note = `Could not load clients: ${state.error}`;
  else if (data) note = feedNote(data);
  els.note.textContent = note || "";
  els.note.hidden = !note;

  els.list.replaceChildren();
  if (state.unsupported) {
    els.sub.textContent = "";
    els.agentlessNote.hidden = true;
    return;
  }
  if (!data) {
    els.sub.textContent = "";
    if (!state.error) els.list.appendChild(el("p", "empty", "Loading clients…"));
    els.agentlessNote.hidden = true;
    return;
  }
  const clients = Array.isArray(data.clients) ? data.clients : [];
  els.sub.textContent = clientsSummaryText(clients);
  if (!clients.length) els.list.appendChild(el("p", "hint", NO_CLIENTS_TEXT));
  const nowMs = Date.now();
  for (const client of clients) els.list.appendChild(buildCard(client, data.range, nowMs));
  els.agentlessNote.hidden = !clients.some((c) => !isAgentClient(c));
}

// ---------------------------------------------------------------------
// Manage PCs (moved from Settings → "PCs (agents)", WP WEB-FEAT-3/PAIR-1)
// ---------------------------------------------------------------------

/** Agents line text from the store's latest clients snapshot; a waiting
 * text before the first answer (never "none" before it knows). */
function currentAgentsText() {
  const snap = store.snapshot("clients");
  return agentsSummaryText(Array.isArray(snap) ? snap : null) || "Agents: waiting for the first answer from the server…";
}

function buildManageSection() {
  const wrap = document.createDocumentFragment();
  wrap.append(el("h4", "sec", "Manage PCs"));
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
      "Online or offline, last report, agent version and cache bypass state for every PC that has reported, with Remove for a PC you no longer use.",
    ),
  );
  const btn = el("button", "btn ghost sm", "Manage PCs");
  btn.type = "button";
  btn.dataset.role = "open-pcs";
  btn.addEventListener("click", () => openClientsSheet());
  row.append(grow, btn);
  wrap.append(row);
  const addRow = el("div", "srow");
  const addGrow = el("span", "grow");
  addGrow.append(
    el("span", "ttl", "Add a device"),
    el(
      "span",
      "desc",
      "Pair the Android app or another browser, or get the install command for vault-agent on a Windows PC, without typing the address and API key.",
    ),
  );
  const addBtn = el("button", "btn ghost sm", "Add");
  addBtn.type = "button";
  addBtn.dataset.role = "add-device";
  addBtn.setAttribute("aria-label", "Add a device");
  addBtn.addEventListener("click", () => openAddDeviceSheet());
  addRow.append(addGrow, addBtn);
  wrap.append(addRow);
  return { fragment: wrap, summaryLine };
}

store.subscribe("clients", (payload) => {
  if (!els || !payload || payload.error) return;
  const text = agentsSummaryText(Array.isArray(payload.items) ? payload.items : null);
  if (text) els.agentsLine.textContent = text;
});

// ---------------------------------------------------------------------
// View lifecycle
// ---------------------------------------------------------------------

onViewChange((view) => {
  if (view === "clients") return;
  els = null;
  poll.stop();
});

export function renderClients() {
  const section = el("section", "view view-clients");
  const head = el("div", "dl-head");
  const h1 = el("h1", null, "Clients");
  const sub = el("span", "dl-sub");
  sub.dataset.role = "clients-sub";
  head.append(h1, sub);

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

  const manage = buildManageSection();
  section.append(head, range.el, note, list, agentlessNote, manage.fragment);

  els = { section, sub, note, list, agentlessNote, agentsLine: manage.summaryLine };
  paint();
  // Same gate as the store's own loops (store-singleton.js): no key and no
  // demo mode means the onboarding overlay is up and every request would
  // be a 401.
  if (getStoredApiKey() || isDemoMode()) {
    poll.start();
    poll.refresh();
  }
  return section;
}
