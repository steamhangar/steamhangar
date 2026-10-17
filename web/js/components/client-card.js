/**
 * Clients card, three zones (WP CLIENTS-4, docs/design/rc20/kit.html
 * "Clients card, three zones"; ADR-0028).
 *
 *   zone 1  identity and health: icon tile, name, mono meta line, pills
 *           (Online/Offline/Downloading, "Agent outdated"), the outdated-
 *           agent box with "Show update command", the agent health row
 *   zone 2  activity and numbers: the "now" box (game + speed, or the
 *           dashed idle box) and three figures, hit rate with a meter
 *   zone 3  recent games as `.gchip`s with "+N more"
 *   footer  last-active text (+ bytes not linked to a game), Rename and
 *           Remove agent on the right, the remove error line
 *
 * Renders `lib/clients-page.js`'s `clientCardModel` and nothing else: no
 * figure or wording is computed here. The kit styles (`.kit .ccard …` in
 * css/kit.css) apply because the Clients view root carries `kit`; the
 * zones stack below a 700px container width (`@container`, not a viewport
 * breakpoint). The card keeps the `clcard` class and the `data-role` hooks
 * of the pre-CLIENTS-4 card, so the view's focus restore and the wiring
 * tests address the same nodes.
 */

import { createPill } from "./pill.js";
import { BYPASS_EXPLANATION, UNREMOVABLE_SLASH_NOTE } from "../lib/clients-view.js";
import { renameTitle } from "../lib/client-names.js";
import { OUTDATED_BOX_TITLE, moreChipText } from "../lib/clients-page.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const ICON_ATTRS = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';
/** Kind icons (static markup, no data in it). */
export const CLIENT_ICONS = Object.freeze({
  agent: `<svg width="20" height="20" ${ICON_ATTRS}><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>`,
  runner: `<svg width="20" height="20" ${ICON_ATTRS}><rect x="3" y="5" width="18" height="6" rx="2"/><rect x="3" y="13" width="18" height="6" rx="2"/></svg>`,
  address: `<svg width="20" height="20" ${ICON_ATTRS}><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></svg>`,
});
const UPDATE_ICON =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16"/></svg>';

const CHECK_ICON =
  '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5 10 17.5 19 7"/></svg>';

function button(text, className, role, ariaLabel, onClick) {
  const b = el("button", className, text);
  b.type = "button";
  b.dataset.role = role;
  if (ariaLabel) b.setAttribute("aria-label", ariaLabel);
  if (onClick) b.addEventListener("click", () => onClick());
  return b;
}

function identityZone(model, handlers) {
  const z = el("div", "z");
  z.dataset.zone = "identity";
  const ch = el("div", "ch");
  const ico = el("div", "ico");
  ico.setAttribute("aria-hidden", "true");
  ico.innerHTML = CLIENT_ICONS[model.kind] || CLIENT_ICONS.address;
  const id = el("div", "id");
  const nm = el("div", "nm", model.name);
  // Operator-chosen text: its own direction, never the page's.
  nm.setAttribute("dir", "auto");
  const meta = el("div", "meta", model.meta);
  meta.dataset.role = "client-kind";
  id.append(nm, meta);
  ch.append(ico, id);
  z.appendChild(ch);

  const pills = el("div", "pills");
  const conn = createPill(model.pill);
  conn.dataset.role = "connection";
  pills.appendChild(conn);
  if (model.outdated) {
    const p = createPill({ kind: "warn", text: "Agent outdated", tone: "stale" });
    p.dataset.role = "outdated-pill";
    pills.appendChild(p);
  }
  z.appendChild(pills);

  if (model.outdated) {
    const upd = el("div", "upd");
    upd.dataset.role = "agent-outdated";
    const icon = el("div", "upd-ico");
    icon.innerHTML = UPDATE_ICON;
    const txt = el("div");
    txt.append(el("b", null, OUTDATED_BOX_TITLE), el("span", null, model.outdated.text));
    const btn = button(
      "Show update command",
      "btn sm",
      "update-command",
      `Show update command for ${model.name}`,
      handlers.onUpdateCommand,
    );
    upd.append(icon, txt, btn);
    z.appendChild(upd);
  }

  if (model.health) {
    const row = el("div", model.health.bypass ? "agent bad" : "agent");
    row.dataset.role = "agent-health";
    const span = el("span", null, model.health.text);
    span.dataset.role = model.health.bypass ? "bypass" : "agent-presence";
    const head = el("b");
    if (!model.health.bypass) {
      // kit.html draws "✓ Healthy"; the check is an SVG so it never
      // depends on the font having the glyph.
      const check = el("span", "ok-ico");
      check.innerHTML = CHECK_ICON;
      head.appendChild(check);
    }
    head.append(model.health.head);
    row.append(head, span);
    z.appendChild(row);
    if (model.health.bypass) {
      const hint = el("p", "hint", BYPASS_EXPLANATION);
      hint.dataset.role = "bypass-hint";
      z.appendChild(hint);
    }
  }
  return z;
}

function statCell(stat) {
  const cell = el("div");
  cell.dataset.stat = stat.key;
  const v = el("div", "v", stat.value);
  if (stat.unit) v.appendChild(el("small", null, stat.unit));
  cell.append(el("div", "k", stat.label), v);
  if (typeof stat.meter === "number") {
    const m = el("div", "meter");
    const bar = el("i");
    bar.style.setProperty("width", `${stat.meter}%`);
    m.appendChild(bar);
    cell.appendChild(m);
  }
  return cell;
}

function activityZone(model) {
  const z = el("div", "z");
  z.dataset.zone = "activity";
  const now = model.now;
  let box;
  if (now.loading) {
    box = el("div", "now");
    const left = el("div");
    left.append(el("b", null, now.title), el("span", null, now.sub));
    box.append(left, el("span", "spd", now.speed));
  } else {
    box = el("div", "now idle", now.text);
  }
  box.dataset.role = "loading-now";
  const stats = el("div", "stats");
  stats.dataset.role = "stats";
  for (const s of model.stats) stats.appendChild(statCell(s));
  z.append(box, stats);
  return z;
}

function recentZone(model) {
  const z = el("div", "z");
  z.dataset.zone = "recent";
  z.appendChild(el("span", "sec", "Recently loaded"));
  const r = model.recent;
  if (!r) {
    const p = el("p", "hint", "No traffic statistics");
    p.dataset.role = "recent-none";
    z.appendChild(p);
  } else if (!r.chips.length) {
    const p = el("p", "hint", model.noRecent);
    p.dataset.role = "recent-empty";
    z.appendChild(p);
  } else {
    const list = el("div", "pills");
    list.dataset.role = "recent";
    for (const c of r.chips) {
      const chip = el("span", "gchip", c.name);
      chip.setAttribute("dir", "auto");
      if (c.size) chip.appendChild(el("b", null, c.size));
      list.appendChild(chip);
    }
    if (r.more > 0) list.appendChild(el("span", "gchip more", moreChipText(r.more)));
    z.appendChild(list);
  }
  return z;
}

function footer(model, handlers) {
  const cf = el("div", "cf");
  const info = el("span");
  const last = el("span", null, model.lastActive);
  last.dataset.role = "last-active";
  info.appendChild(last);
  if (model.unlinked) {
    const u = el("span", null, model.unlinked);
    u.dataset.role = "unlinked";
    info.append(" · ", u);
  }
  cf.appendChild(info);
  if (model.rename || model.removeId) {
    const acts = el("div", "acts");
    if (model.rename) {
      // Every card has one: the accessible name says which (label-in-name).
      acts.appendChild(button("Rename", "btn sm", "rename-client", renameTitle(model.name), handlers.onRename));
    }
    if (model.removeId) {
      acts.appendChild(button("Remove agent", "btn sm ghost", "remove-pc", `Remove agent ${model.removeId}`, handlers.onRemove));
    }
    cf.appendChild(acts);
  }
  if (model.removeBlocked) {
    const note = el("p", "foot-note", UNREMOVABLE_SLASH_NOTE);
    note.dataset.role = "remove-unavailable";
    cf.appendChild(note);
  }
  if (model.removeError !== null) {
    const err = el("p", "errline", model.removeError);
    err.dataset.role = "remove-error";
    err.setAttribute("role", "alert");
    err.hidden = !model.removeError;
    cf.appendChild(err);
  }
  return cf;
}

/**
 * @param {ReturnType<typeof import("../lib/clients-page.js").clientCardModel>} model
 * @param {{onRename?: () => void, onRemove?: () => void, onUpdateCommand?: () => void}} [handlers]
 * @returns {HTMLElement}
 */
export function createClientCard(model, handlers = {}) {
  const card = el("article", "ccard clcard");
  if (model.outdated || (model.health && model.health.bypass)) card.classList.add("warn");
  if (model.health && model.health.bypass) card.classList.add("bypass");
  card.dataset.clientKey = model.key;
  card.dataset.kind = model.kind;
  card.setAttribute("aria-label", model.name);
  card.append(identityZone(model, handlers), activityZone(model), recentZone(model), footer(model, handlers));
  return card;
}
