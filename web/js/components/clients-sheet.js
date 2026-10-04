/**
 * Clients sheet (WP 4a.7).
 *
 * Real `GET /v1/clients` data (api/vault_api/routers/clients.py's
 * `ClientOut`, WP 3.11's bypass_suspected) in the mockup's round-5
 * "Bypassing" / "Healthy" grouping. Per docs/WORKPACKAGES.md's Phase 4a
 * header (the recorded WP 4a.1 decision) **Clients is a sheet, not a nav
 * item**. Entry points: the bypass banner's "Details" button
 * (`components/bypass-banner.js`), a bypass_suspected/bypass_resolved row
 * in the notifications panel (`components/notifications.js`), and — WP
 * WEB-FEAT-3 — the "Show PCs" button in Settings → "PCs (agents)"
 * (`views/settings.js`), which makes the list reachable when nothing is
 * bypassing. Still no nav item.
 *
 * WP WEB-FEAT-3 also shows, per PC: a presence chip (the server's
 * `presence` field, read by `lib/clients-view.js`'s `presenceOf` and never
 * recomputed here), "last seen ... ago" from `last_reported_at`, and the
 * agent version ("version unknown" for `agent_version: null`); and an
 * "Agents: N online, M offline" line at the top. Offline PCs stay listed.
 * The relative time and the presence chip are repainted on every clients
 * tick while the sheet is open (text only, no row rebuild), so "4 min ago"
 * does not freeze while the sheet sits open.
 *
 * Data flows exclusively through the WP 4a.2 store (`store-singleton.js`)
 * — no parallel poll loop, same posture as `views/downloads.js`. Patch-in-
 * place on poll ticks per `lib/clients-render-plan.js` (round-7 pattern,
 * ported here for a different concrete reason than its animation-safety
 * origin — see that module's header: avoiding a scroll-position reset on
 * the sheet's own 20s poll cadence while it happens to be open).
 *
 * WP WEB-FEAT-4: every row has a "Remove" button that opens a confirm
 * dialog (the `.dialog-backdrop`/`.dialog` alertdialog markup the detail
 * sheet's delete confirm uses, stacked on this sheet through
 * `lib/modal-stack.js`) and then calls `DELETE /v1/clients/{client_id}`
 * (WP AG-1). The dialog names the PC, says what is deleted and that a
 * still-running agent lists the PC again with its next report (wording in
 * `lib/clients-view.js`). Success, and a 404 (already gone), drop the row
 * at once, force the next clients tick to re-render the list, and nudge
 * the store; any other failure leaves the row and shows the error inline on
 * it. A row whose id contains "/" gets a note instead of the button
 * (`isRemovableClientId`: the server's route cannot address that id).
 *
 * DOM wiring is pinned with fake-dom in web/tests/settings-about-pcs-
 * wiring.test.js (WEB-FEAT-3) and web/tests/clients-remove-wiring.test.js
 * (WEB-FEAT-4); the painted result is not measured in a browser here.
 */

import { store } from "../store-singleton.js";
import { onViewChange } from "../router.js";
import { api } from "../api.js";
import { ERROR_KINDS } from "../errors.js";
import { pushModal, popModal } from "../lib/modal-stack.js";
import { showToast } from "./toast.js";
import { createStatusIcon } from "./status-icon.js";
import { createSheetDialog } from "./sheet-dialog.js";
import {
  isRemovableClientId,
  UNREMOVABLE_SLASH_NOTE,
  removeConfirmTitle,
  REMOVE_WHAT_TEXT,
  REMOVE_REREGISTER_TEXT,
  removedToastText,
  removeErrorText,
  partitionClients,
  addressesText,
  describeHealthyClient,
  describeBypassClient,
  BYPASS_EXPLANATION,
  presenceOf,
  presenceWord,
  presenceLine,
  agentsSummaryText,
} from "../lib/clients-view.js";
import { planClientsUpdate } from "../lib/clients-render-plan.js";

const initialSnapshot = store.snapshot("clients");
const state = {
  clients: Array.isArray(initialSnapshot) ? initialSnapshot : [],
  // WP WEB-FEAT-3: false until a `GET /v1/clients` answer has landed, so the
  // summary line never says "none have reported yet" before it knows.
  loaded: Array.isArray(initialSnapshot),
  // WP WEB-FEAT-4: inline error per client_id from the last failed Remove;
  // kept here (not only in the DOM) so a row rebuilt by a poll tick keeps
  // showing it. Cleared on a later success and when the sheet is reopened.
  removeErrors: new Map(),
  // WP WEB-FEAT-4: set after a remove dropped a row locally. The next
  // clients tick then re-renders the whole list even when its diff against
  // the store's previous snapshot is empty (the agent re-reported between
  // the DELETE and the poll), so the list never keeps hiding a PC the
  // server lists.
  forceFullRender: false,
};

// WP 4e.3: "drawer" — same ambient-side-panel treatment as the notifications
// panel (docs/PROJECT_PLAN.md's Phase 4e section): from BP-L up this appears
// at the right edge instead of the bottom. No motion either way — a plain
// `display` toggle, same as every other overlay here (Opus review, WP 4e.3
// fix round: "slides in" overclaimed an animation this codebase's overlays
// do not have). Below BP-L it stays the mockup's bottom sheet, unchanged.
const dialog = createSheetDialog({ ariaLabel: "PCs (agents)", variant: "drawer" });

const heading = document.createElement("h2");
heading.textContent = "PCs (agents)";
// WP WEB-FEAT-3: "Agents: N online, M offline" — the same text as Settings'
// "PCs (agents)" and About sections (lib/clients-view.js's
// agentsSummaryText). Not a live region: it changes on background polls,
// and announcing those would be noise.
const summary = document.createElement("p");
summary.className = "foot-note";
summary.dataset.role = "agents-summary";
const intro = document.createElement("p");
intro.className = "hint";
intro.textContent =
  "Machines running vault-agent, matched against what actually arrived at the cache. Online and offline are the server's verdict: offline once a PC has missed two reports plus 5 minutes.";

const bypassHeading = document.createElement("h4");
bypassHeading.className = "sec";
bypassHeading.textContent = "Bypassing";
bypassHeading.hidden = true;
const bypassBody = document.createElement("div");

const healthyHeading = document.createElement("h4");
healthyHeading.className = "sec";
healthyHeading.textContent = "Healthy";
healthyHeading.hidden = true;
const healthyBody = document.createElement("div");

const emptyMsg = document.createElement("p");
emptyMsg.className = "hint";
emptyMsg.textContent = "No clients have reported yet.";
emptyMsg.hidden = true;

const closeBtn = document.createElement("button");
closeBtn.type = "button";
closeBtn.className = "btn wide ghost";
closeBtn.textContent = "Close";
closeBtn.addEventListener("click", () => dialog.close());

dialog.body.append(
  heading,
  summary,
  intro,
  emptyMsg,
  bypassHeading,
  bypassBody,
  healthyHeading,
  healthyBody,
  closeBtn,
);

function buildRow(client, { bypass }, nowMs) {
  const card = document.createElement("div");
  card.className = "jobcard" + (bypass ? " bypass" : "");
  card.dataset.clientId = client.client_id;

  const top = document.createElement("div");
  top.className = "jobtop";

  const info = document.createElement("div");
  const nm = document.createElement("div");
  nm.className = "nm";
  nm.textContent = client.client_id;
  const sm = document.createElement("div");
  sm.className = "sm";
  sm.dataset.statsLine = "";
  sm.textContent = statsLine(client, { bypass });
  // WP WEB-FEAT-3: "last seen 4 min ago · agent 0.1.0".
  const pres = document.createElement("div");
  pres.className = "sm pcs-line";
  pres.dataset.presenceLine = "";
  info.append(nm, sm, pres);

  const badges = document.createElement("div");
  badges.className = "pcs-badges";

  // WP WEB-FEAT-3: presence chip. Word first (the dot is decoration,
  // aria-hidden), colour third — same shape-word-colour order as the
  // status icons.
  const chip = document.createElement("span");
  chip.dataset.presenceChip = "";
  const dot = document.createElement("span");
  dot.className = "pdot";
  dot.setAttribute("aria-hidden", "true");
  const chipWord = document.createElement("span");
  chipWord.dataset.role = "presence-word";
  chip.append(dot, chipWord);

  const badge = document.createElement("span");
  badge.className = "badge " + (bypass ? "tx-warn" : "tx-cached");
  const icon = createStatusIcon(bypass ? "warn" : "cached", { size: "sm" });
  // The shared status-icon vocabulary's built-in sr-only label is
  // game-caching wording ("Current"/"Warning") reused here for its shape
  // only — this row's own visible word ("Healthy"/"Bypassing") is the
  // correct accessible text, so the icon's label must not also be read
  // (same "avoid double/mismatched announcement" posture as the
  // notifications panel row's icon — components/notifications.js).
  icon.setAttribute("aria-hidden", "true");
  badge.appendChild(icon);
  const word = document.createElement("span");
  word.textContent = bypass ? "Bypassing" : "Healthy";
  badge.appendChild(word);

  badges.append(chip, badge);
  top.append(info, badges);
  card.appendChild(top);
  paintPresence(card, client, nowMs);

  if (bypass) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = BYPASS_EXPLANATION;
    card.appendChild(hint);
  }

  card.appendChild(buildRemoveControls(client.client_id));

  return card;
}

/** WP WEB-FEAT-4: the row's Remove button (or the "/" note), plus its
 * inline error line. */
function buildRemoveControls(clientId) {
  const wrap = document.createElement("div");
  wrap.className = "pcs-remove";

  if (isRemovableClientId(clientId)) {
    const acts = document.createElement("div");
    acts.className = "jobacts";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn ghost sm";
    btn.textContent = "Remove";
    // Visible word first, then the PC: every row has a "Remove" button, so
    // the accessible name says which PC (label-in-name kept).
    btn.setAttribute("aria-label", `Remove ${clientId}`);
    btn.dataset.role = "remove-pc";
    btn.addEventListener("click", () => openRemoveConfirm(clientId));
    acts.appendChild(btn);
    wrap.appendChild(acts);
  } else {
    const note = document.createElement("p");
    note.className = "foot-note";
    note.dataset.role = "remove-unavailable";
    note.textContent = UNREMOVABLE_SLASH_NOTE;
    wrap.appendChild(note);
  }

  const err = document.createElement("p");
  err.className = "errline";
  err.dataset.role = "remove-error";
  err.setAttribute("role", "alert");
  const message = state.removeErrors.get(clientId);
  err.textContent = message || "";
  err.hidden = !message;
  wrap.appendChild(err);
  return wrap;
}

/** Presence chip + "last seen" line of one row, from the server's fields
 * only (`presenceOf` reads `client.presence`; nothing here looks at a
 * clock to decide online/offline — the clock only words "N min ago"). */
function paintPresence(card, client, nowMs) {
  const chip = card.querySelector("[data-presence-chip]");
  const chipWord = card.querySelector('[data-role="presence-word"]');
  const line = card.querySelector("[data-presence-line]");
  const p = presenceOf(client);
  if (chip) chip.className = "pchip " + (p === "online" ? "pchip-on" : p === "offline" ? "pchip-off" : "pchip-unknown");
  if (chipWord) chipWord.textContent = presenceWord(client);
  if (line) line.textContent = presenceLine(client, nowMs);
}

function statsLine(client, { bypass }) {
  const stats = bypass ? describeBypassClient(client) : describeHealthyClient(client);
  return `${addressesText(client)} · ${stats}`;
}

function paintSummary() {
  const text = agentsSummaryText(state.loaded ? state.clients : null);
  summary.textContent = text || "Agents: waiting for the first answer from the server…";
}

function fullRender() {
  const { bypassing, healthy } = partitionClients(state.clients);
  const nowMs = Date.now(); // one clock for every row of this paint

  paintSummary();
  emptyMsg.hidden = state.clients.length > 0;

  bypassHeading.hidden = bypassing.length === 0;
  bypassBody.replaceChildren(...bypassing.map((c) => buildRow(c, { bypass: true }, nowMs)));

  healthyHeading.hidden = healthy.length === 0;
  healthyBody.replaceChildren(...healthy.map((c) => buildRow(c, { bypass: false }, nowMs)));
}

/** WP WEB-FEAT-3: repaint every row's presence chip and "last seen" text,
 * plus the summary line — text only, rows are not rebuilt (scroll position
 * and focus survive). Runs on every clients tick while the sheet is open. */
function repaintPresence() {
  const nowMs = Date.now();
  paintSummary();
  for (const client of state.clients) {
    const card = dialog.body.querySelector(`.jobcard[data-client-id="${cssEscape(client.client_id)}"]`);
    if (card) paintPresence(card, client, nowMs);
  }
}

/** Update just `.sm`'s text for clients whose SECTION did not change
 * (`lib/clients-render-plan.js`'s `patch` list) — never rebuilds the row,
 * so an open sheet's scroll position survives an unrelated stats tick. */
function patchStats(clientIds) {
  const byId = new Map(state.clients.map((c) => [c.client_id, c]));
  for (const id of clientIds) {
    const client = byId.get(id);
    if (!client) continue;
    const card = dialog.body.querySelector(`.jobcard[data-client-id="${cssEscape(id)}"]`);
    const sm = card ? card.querySelector('[data-stats-line]') : null;
    if (sm) sm.textContent = statsLine(client, { bypass: !!client.bypass_suspected });
  }
}

// `client_id` is operator-chosen free text (agent_reports.py) and could in
// principle contain characters that break a naive `[data-client-id="..."]`
// selector — CSS.escape is the standard way to quote an attribute-selector
// value safely. Falls back to the raw string on a runtime with no
// CSS.escape (none realistically targeted here, but cheap insurance).
function cssEscape(value) {
  return typeof CSS !== "undefined" && typeof CSS.escape === "function"
    ? CSS.escape(value)
    : String(value).replace(/["\\]/g, "\\$&");
}

// ---------------------------------------------------------------------
// Remove confirm (WP WEB-FEAT-4) — a persistent overlay on top of the
// sheet, the same alertdialog markup/CSS as the detail sheet's delete
// confirm. Escape closes it first (lib/modal-stack.js), the sheet behind it
// is inert while it is open, focus starts on "Keep" (never on the
// destructive button) and returns to the invoker on cancel.
// ---------------------------------------------------------------------

const removeBackdrop = document.createElement("div");
removeBackdrop.className = "dialog-backdrop";
const removeDialogEl = document.createElement("div");
removeDialogEl.className = "dialog pcs-remove-dialog";
removeDialogEl.setAttribute("role", "alertdialog");
removeDialogEl.setAttribute("aria-modal", "true");
removeDialogEl.setAttribute("aria-labelledby", "pcs-remove-title");
removeDialogEl.dataset.role = "remove-confirm";
const removeTitle = document.createElement("h3");
removeTitle.id = "pcs-remove-title";
const removeWhat = document.createElement("p");
removeWhat.textContent = REMOVE_WHAT_TEXT;
const removeAgain = document.createElement("p");
removeAgain.textContent = REMOVE_REREGISTER_TEXT;
const removeRow = document.createElement("div");
removeRow.className = "row";
const removeNo = document.createElement("button");
removeNo.type = "button";
removeNo.className = "btn ghost sm";
removeNo.textContent = "Keep";
removeNo.dataset.role = "remove-cancel";
const removeYes = document.createElement("button");
removeYes.type = "button";
removeYes.className = "btn danger sm";
removeYes.textContent = "Remove";
removeYes.dataset.role = "remove-confirm-yes";
removeRow.append(removeNo, removeYes);
removeDialogEl.append(removeTitle, removeWhat, removeAgain, removeRow);
removeBackdrop.appendChild(removeDialogEl);
document.body.appendChild(removeBackdrop);

removeNo.addEventListener("click", () => {
  if (!removeFlow.busy) closeRemoveConfirm(); // aria-disabled while busy
});
removeYes.addEventListener("click", () => confirmRemove());

const removeFlow = {
  clientId: null, // the PC the open dialog is about
  invokerEl: null,
  busy: false,
};

function openRemoveConfirm(clientId) {
  if (removeFlow.busy) return;
  removeFlow.clientId = clientId;
  removeFlow.invokerEl = document.activeElement;
  removeTitle.textContent = removeConfirmTitle(clientId);
  removeYes.textContent = "Remove";
  removeBackdrop.classList.add("on");
  pushModal(removeBackdrop, () => closeRemoveConfirm());
  removeNo.focus();
}

function closeRemoveConfirm({ restoreFocus = true } = {}) {
  if (!removeBackdrop.classList.contains("on")) return;
  removeBackdrop.classList.remove("on");
  popModal(removeBackdrop);
  const invoker = removeFlow.invokerEl;
  removeFlow.invokerEl = null;
  if (removeFlow.busy) return; // the in-flight request decides where focus goes
  removeFlow.clientId = null;
  if (restoreFocus && invoker && typeof invoker.focus === "function") invoker.focus();
}

function setRemoveBusy(busy) {
  removeFlow.busy = busy;
  // aria-disabled + the click guard below, not `disabled`: a disabled
  // button drops keyboard focus (LEARNINGS, WP WEB-FEAT-1).
  for (const b of [removeNo, removeYes]) {
    if (busy) b.setAttribute("aria-disabled", "true");
    else b.removeAttribute("aria-disabled");
  }
  removeYes.textContent = busy ? "Removing…" : "Remove";
}

function rowFor(clientId) {
  return dialog.body.querySelector(`.jobcard[data-client-id="${cssEscape(clientId)}"]`);
}

/** Paint one row's inline remove error from `state.removeErrors`. */
function paintRemoveError(clientId) {
  const card = rowFor(clientId);
  const err = card ? card.querySelector('[data-role="remove-error"]') : null;
  if (!err) return;
  const message = state.removeErrors.get(clientId);
  err.textContent = message || "";
  err.hidden = !message;
}

async function confirmRemove() {
  const clientId = removeFlow.clientId;
  if (removeFlow.busy || clientId == null) return;
  setRemoveBusy(true);
  let failure = null;
  try {
    await api.deleteClient(clientId);
  } catch (err) {
    // 404: nothing left to delete for this id — another tab or an earlier
    // click already removed it. Same end state as a 204.
    if (!(err && err.kind === ERROR_KINDS.NOT_FOUND)) failure = err;
  }
  setRemoveBusy(false);
  removeFlow.clientId = null;
  closeRemoveConfirm({ restoreFocus: false });

  if (failure) {
    state.removeErrors.set(clientId, removeErrorText(clientId, errorText(failure)));
    paintRemoveError(clientId);
    const btn = rowFor(clientId)?.querySelector('[data-role="remove-pc"]');
    if (btn) btn.focus();
    return;
  }

  state.removeErrors.delete(clientId);
  state.clients = state.clients.filter((c) => c.client_id !== clientId);
  state.forceFullRender = true;
  if (dialog.isOpen()) {
    fullRender();
    // The invoking row is gone; the sheet itself is the focus landing spot.
    dialog.sheet.focus();
  }
  showToast(removedToastText(clientId));
  store.refreshNow();
}

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed.";
}

store.subscribe("clients", ({ items, diff }) => {
  if (!Array.isArray(items)) return; // {error} payload — nothing to render
  const plan = planClientsUpdate(diff);
  state.clients = items;
  state.loaded = true;
  const forceFull = state.forceFullRender;
  state.forceFullRender = false;
  if (!dialog.isOpen()) return; // sheet isn't showing right now — nothing to paint
  if (forceFull || plan.full || plan.rebuild.length) {
    fullRender();
  } else {
    if (plan.patch.length) patchStats(plan.patch);
    repaintPresence();
  }
});

/** Open the clients sheet, painting it from the latest snapshot first. */
export function openClientsSheet() {
  state.removeErrors.clear(); // a reopened sheet starts without stale errors
  fullRender();
  dialog.open();
}

// Navigation dismisses transient surfaces (mockup rule, NOTES "Behavior
// rule for the real app" — the clients sheet is explicitly named alongside
// the detail sheet and the notifications panel). Without this, tapping a
// bottom-nav item while the sheet is open would leave it painted over the
// new view, same class of bug as the mockup's original overlay bug.
// WP WEB-FEAT-4: the remove confirm on top of it goes too (it belongs to the
// sheet); an in-flight remove still finishes and refreshes the store.
onViewChange(() => {
  closeRemoveConfirm({ restoreFocus: false });
  dialog.close();
});
