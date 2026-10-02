/**
 * Notification bell + panel (WP 4a.7).
 *
 * Bell lives in the app-shell topbar (`index.html`, present on every view)
 * rather than the mockup's Library-header placement — the only way to make
 * it reachable from Downloads/Settings too without editing
 * `web/js/views/library.js` (this WP's explicit constraint). The panel
 * itself ports the mockup's round-6 "Notifications lead somewhere" model:
 * every row is a button that navigates to what it's about, and the panel
 * closes on the way.
 *
 * This module owns the actual notification LOG (the mutable
 * `state.log`/`state.nextId` pair) and is the only consumer of
 * `lib/notification-log.js`'s pure functions — it does not diff anything
 * itself, it only appends the event batches the store's `"notifications"`
 * channel already hands it (`web/js/notifications.js`'s differ, run inside
 * `store.js`). Session-only, in-memory: see `lib/notification-log.js`'s
 * module header for why this WP does not persist it to localStorage.
 *
 * DOM-building component, not unit-tested directly (see
 * `components/sheet-dialog.js`'s header for the general reasoning); every
 * piece of actual DECISION logic it leans on (`lib/notification-log.js`) is
 * pure and covered in web/tests/notification-log.test.js.
 */

import { store } from "../store-singleton.js";
import { navigateTo, onViewChange } from "../router.js";
import { createStatusIcon } from "./status-icon.js";
import { createSheetDialog } from "./sheet-dialog.js";
import { openClientsSheet } from "./clients-sheet.js";
import { highlightJob } from "../views/downloads.js";
import { openDetail } from "./game-detail-sheet.js";
import { formatTimestamp } from "../lib/format.js";
import { appTitle, ownedNamesByAppid } from "../lib/owned-library.js";
import { ownedLibrary } from "../owned-singleton.js";
import {
  metaFor,
  appendNotifications,
  unreadCount,
  markAllRead,
  navigationTargetFor,
} from "../lib/notification-log.js";

const state = {
  log: [], // newest-first; starts empty on purpose, see notification-log.js
  nextId: 1,
};

const bell = document.getElementById("btn-notifs");
const pip = document.getElementById("npip");

// WP 4e.3: "drawer" — the operator's decision treats notifications as an
// ambient side panel, not a focused task, so from BP-L up it appears at the
// right edge instead of the bottom (docs/PROJECT_PLAN.md's Phase 4e
// section). No motion either way — like every other overlay in this app, it
// is shown/hidden via a plain `display` toggle, no transition (Opus review,
// WP 4e.3 fix round: "slides in" overclaimed a slide animation that does
// not exist anywhere in this codebase's overlays). Below BP-L it stays the
// mockup's bottom sheet, unchanged.
const dialog = createSheetDialog({ ariaLabel: "Notifications", variant: "drawer" });

const heading = document.createElement("h2");
heading.textContent = "Notifications";
const intro = document.createElement("p");
intro.className = "hint";
intro.textContent =
  "Polled from the server — there is no push in v1. Each line is a change the app noticed between polls.";
const list = document.createElement("div");
const closeBtn = document.createElement("button");
closeBtn.type = "button";
closeBtn.className = "btn wide ghost";
closeBtn.textContent = "Close";
closeBtn.addEventListener("click", () => dialog.close());

dialog.body.append(heading, intro, list, closeBtn);

// ---------------------------------------------------------------------
// Name lookups — the differ's events carry ids, not display names (except
// update_ready, which already has one — notifications.js's
// diffGamesForNotifications). Reads the SAME store snapshot the games poll
// keeps current, same pattern as views/downloads.js's `nameFor`.
// ---------------------------------------------------------------------
// WP WEB-FIX-4: vault name, then the owned-games list's name (an
// owned-only game queued from its detail sheet has a vault row with no
// name), then "App <id>" — lib/owned-library.js's appTitle.
function gameNameFor(appid) {
  const games = store.snapshot("games") || [];
  const game = games.find((g) => g.appid === appid);
  return appTitle(appid, game && game.name, ownedNamesByAppid(ownedLibrary.current().games).get(appid));
}

function titleFor(entry) {
  switch (entry.type) {
    case "update_ready":
      return entry.name || gameNameFor(entry.appid);
    case "job_finished":
    case "job_failed":
      return gameNameFor(entry.appid);
    case "bypass_suspected":
    case "bypass_resolved":
      return `Client ${entry.clientId}`;
    default:
      return "Notice";
  }
}

function textFor(entry) {
  switch (entry.type) {
    case "job_finished":
      return "Download finished — now current on the cache.";
    case "job_failed":
      return `Job #${entry.jobId} failed — open Downloads for the log.`;
    case "update_ready":
      return "A newer manifest is available upstream — an update is ready to download.";
    case "bypass_suspected":
      return "Reports installed games, but none of its downloads have reached the cache.";
    case "bypass_resolved":
      return "Downloads from this client are reaching the cache again.";
    default:
      return "";
  }
}

/** aria-label suffix stating where a tap leads (mockup round 6: "each
 * row's aria-label states its destination"). */
function destinationText(entry) {
  const target = navigationTargetFor(entry);
  if (target.kind === "downloads") return "Opens this job in Downloads";
  if (target.kind === "clients") return "Opens the client list";
  if (target.kind === "detail") return "Opens this game's detail sheet";
  return "Opens the Library";
}

function buildRow(entry) {
  const meta = metaFor(entry.type);
  const title = titleFor(entry);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "notif" + (entry.read ? "" : " unread");
  btn.setAttribute("aria-label", `${meta.word}: ${title}. ${destinationText(entry)}`);
  btn.addEventListener("click", () => activate(entry));

  const icon = createStatusIcon(meta.icon, { size: "sm" });
  icon.setAttribute("aria-hidden", "true");

  const body = document.createElement("span");
  body.className = "nbody";
  const nt = document.createElement("span");
  nt.className = "nt";
  const nw = document.createElement("span");
  nw.className = "nw " + meta.tx;
  nw.textContent = meta.word;
  nt.append(nw, document.createTextNode(title));
  const nx = document.createElement("span");
  nx.className = "nx";
  nx.textContent = textFor(entry);
  const nwhen = document.createElement("span");
  nwhen.className = "nwhen";
  nwhen.textContent = formatTimestamp(entry.at);
  body.append(nt, nx, nwhen);

  const undot = document.createElement("span");
  undot.className = "undot";
  undot.setAttribute("aria-hidden", "true");

  const go = document.createElement("span");
  go.className = "ngo";
  go.setAttribute("aria-hidden", "true");
  go.appendChild(chevron());

  btn.append(icon, body, undot, go);
  return btn;
}

function chevron() {
  const span = document.createElement("span");
  span.innerHTML =
    '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="m7.5 4.5 6 5.5-6 5.5"/></svg>';
  return span.firstElementChild;
}

function renderList() {
  if (!state.log.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent =
      "Nothing to report. Downloads, updates and bypass warnings show up here.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...state.log.map(buildRow));
}

function updateBadge() {
  const count = unreadCount(state.log);
  const shown = count > 9 ? "9+" : String(count);
  pip.textContent = shown;
  pip.classList.toggle("on", count > 0);
  bell.setAttribute(
    "aria-label",
    count > 0 ? `Notifications, ${count} unread` : "Notifications, no unread",
  );
}

/** Navigate to what a notification is about, then close the panel — mirrors
 * the mockup's `openNote()`/round-5 "navigation dismisses transient
 * surfaces" rule (dismissing THIS panel first, same as any other transient
 * surface, is what `dialog.close()` already does). */
function activate(entry) {
  dialog.close();
  const target = navigationTargetFor(entry);
  if (target.kind === "downloads") {
    navigateTo("downloads");
    highlightJob(target.jobId);
  } else if (target.kind === "clients") {
    openClientsSheet();
  } else if (target.kind === "detail") {
    // WP 4a.4 target upgrade (the recorded WP 4a.7 TODO): navigate to
    // Library first (the detail sheet is a modal surface over it, same as
    // every other view it can be opened from) so closing the sheet leaves
    // the user somewhere sensible, then open it.
    navigateTo("library");
    openDetail(target.appid, target.name || gameNameFor(target.appid));
  } else {
    navigateTo("library");
  }
}

function openPanel() {
  // Render FIRST (so this open still shows which rows were unread up to
  // now), THEN mark everything read — same order as the mockup's
  // `openNotifs()`, so the badge clears on open but the just-opened list
  // still visually distinguishes what was new.
  renderList();
  dialog.open();
  state.log = markAllRead(state.log);
  updateBadge();
}

bell.addEventListener("click", () => {
  if (dialog.isOpen()) {
    dialog.close();
  } else {
    openPanel();
  }
});

onViewChange(() => dialog.close()); // navigation dismisses transient surfaces (mockup rule)

store.subscribe("notifications", (events) => {
  const { log, nextId } = appendNotifications(state.log, events, {
    at: new Date().toISOString(),
    startId: state.nextId,
  });
  state.log = log;
  state.nextId = nextId;
  updateBadge();
  if (dialog.isOpen()) renderList();
});

updateBadge(); // paint "0, no unread" immediately at load, before any poll tick
