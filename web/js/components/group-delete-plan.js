/**
 * The grouped-delete part of a delete confirmation (WP TOOL-APPS-2c,
 * ADR-0022 §4): every app the delete removes (the game and each content
 * package the server chose), the bytes freed, the depots kept because
 * another cached game uses them, the children kept because they are not
 * content packages, and — when a member has a job in flight — why the
 * delete is not possible right now. Reuses the delete dialogs' existing
 * `.cflabel`/`.cflist`/`.cftotal` look so the plain and the grouped delete
 * read the same way. Used by views/library.js (bulk) and
 * components/game-detail-sheet.js (single game).
 */

import { formatBytesGB, formatBytesGBOrZero } from "../lib/format.js";
import { describeGroupPlan, BLOCKED_HEADING } from "../lib/group-delete.js";

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

/**
 * Build the block for one plan.
 * @param {object} plan `DeletePlanOut`
 * @param {(appid: number, serverName?: string | null) => string} nameFor
 * @param {{showSubject?: boolean}} [opts] `showSubject`: a heading with the
 *   game's name (bulk dialogs with several groups).
 * @returns {{node: HTMLElement, blocked: boolean, freedBytes: number, subject: string}}
 */
export function buildGroupDeletePlan(plan, nameFor, { showSubject = false } = {}) {
  const d = describeGroupPlan(plan, nameFor);
  const wrap = el("div", "cfgroup");
  wrap.dataset.appid = String(plan.appid);
  if (showSubject) wrap.appendChild(el("p", "cflabel", d.subject));

  wrap.appendChild(el("p", "cflabel", `Will be deleted (${1 + d.packages.length})`));
  const ul = el("ul", "cflist");
  ul.dataset.role = "members";
  for (const m of [d.parent, ...d.packages]) {
    const li = el("li", "free");
    li.dataset.appid = String(m.appid);
    li.append(el("span", "did", m.name), el("span", "mk", m.role === "parent" ? "game" : "package"));
    ul.appendChild(li);
  }
  wrap.appendChild(ul);

  if (d.keptDepots.length) {
    wrap.appendChild(el("p", "cflabel", `Kept (${d.keptDepots.length} shared ${d.keptDepots.length === 1 ? "depot" : "depots"})`));
    const kept = el("ul", "cflist");
    kept.dataset.role = "kept-depots";
    for (const k of d.keptDepots) {
      const li = el("li", "keep");
      li.append(
        el("span", "did", String(k.depotid)),
        el("span", "mk", "kept"),
        el("span", "why", k.usedBy.length ? `${k.usedBy.join(", ")} still ${k.usedBy.length > 1 ? "use" : "uses"} it` : "another app still uses it"),
      );
      kept.appendChild(li);
    }
    wrap.appendChild(kept);
  }

  if (d.keptChildren.length) {
    const p = el("p", "cfsolo");
    p.dataset.role = "kept-children";
    p.textContent = `Not deleted: ${d.keptChildren.map((c) => `${c.name} (${c.reason})`).join(", ")}.`;
    wrap.appendChild(p);
  }

  // Review nit: mapping rows with an unusable depot id (poisoned database);
  // the delete reports them in `failed`.
  const unusable = Array.isArray(plan.unusable) ? plan.unusable.length : 0;
  if (unusable) {
    const p = el("p", "cfsolo");
    p.dataset.role = "unusable";
    p.textContent = `${unusable} unreadable depot ${unusable === 1 ? "entry" : "entries"} — the delete will report ${unusable === 1 ? "it" : "them"} as failed.`;
    wrap.appendChild(p);
  }

  const total = el("p", "cftotal");
  const b = el("b", null, formatBytesGBOrZero(d.freedBytes) || "0 GB");
  total.append(b, " freed");
  wrap.appendChild(total);

  if (d.blocked.length) {
    const warn = el("p", "cfsolo cfblocked");
    warn.dataset.role = "blocked";
    warn.textContent = `${BLOCKED_HEADING}: ${d.blocked.map((j) => `${j.name} — ${j.text}`).join("; ")}. Wait for it to finish or cancel it in Downloads.`;
    wrap.appendChild(warn);
  }
  return { node: wrap, blocked: d.blocked.length > 0, freedBytes: d.freedBytes, subject: d.subject };
}

/**
 * The 409 re-confirm notice (review fix, a11y): a status line that takes
 * focus, so it is announced and focus does not fall to <body> while the
 * plan is fetched again.
 * @param {string} text
 */
export function buildNotice(text) {
  const p = el("p", "cfsolo cfnotice", text);
  p.dataset.role = "notice";
  p.setAttribute("role", "status");
  p.tabIndex = -1;
  return p;
}

/** "Deleting X and its N content packages frees about Y." */
export function groupDeleteIntro(subject, freedBytes) {
  return `Deleting ${subject} frees about ${formatBytesGB(freedBytes) || "0 GB"}.`;
}
