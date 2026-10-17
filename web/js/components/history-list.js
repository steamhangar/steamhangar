/**
 * Downloads History rows (WP DOWNLOADS-2, docs/design/rc20/kit.html
 * "Downloads, width-aware" + docs/design/rc20/downloads.html's expanded
 * row): each finished job is one `.hitem` holding
 *
 *   - `button.hrow` — status disc, name, mono meta ("job #N · Done ·
 *     time"), chevron; `aria-expanded` says whether its panel is open;
 *   - `div.hpanel` — the hanging panel right under it (`hidden` while
 *     collapsed): for a known failure a titled hint (prose, the bordered
 *     command box, the Retry offer) and the full SteamPrefill output folded
 *     into a closed `<details>` with a boxed, scrollable log; for a failure
 *     without a known cause the generic failure lines and Retry, then the
 *     boxed log unfolded (there the log IS the diagnosis); for every other
 *     job just the boxed log excerpt.
 *
 * The panel is filled lazily: the view fetches `GET /v1/jobs/{id}` on the
 * first expand (`lib/log-excerpt.js` decides loading/error/empty/ready) and
 * calls `paintHistoryPanel` again when the answer lands. All text goes in
 * through textContent; nothing from the log is ever parsed as markup.
 * Retry buttons are built by the view (`makeRetry`) so its busy and
 * offline rules apply.
 */
import { createStatusIcon } from "./status-icon.js";
import { EXCERPT_STATE } from "../lib/log-excerpt.js";
import { GENERIC_FAILURE, HINTS, NEWER_JOB_LINE } from "../lib/job-failure.js";
import { TOOL_APP_NOTE } from "../lib/game-status.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/** The note above a truncated log. */
export const TRUNCATED_NOTE = "Truncated — showing the last portion of the output.";
export const EMPTY_LOG_TEXT = "No log output for this job.";
export const LOADING_LOG_TEXT = "Loading log…";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function chevron() {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [k, v] of Object.entries({ width: "14", height: "14", viewBox: "0 0 20 20", fill: "none", stroke: "currentColor", "stroke-width": "2", "aria-hidden": "true" })) {
    svg.setAttribute(k, v);
  }
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", "m7.5 4.5 6 5.5-6 5.5");
  svg.appendChild(path);
  return svg;
}

/**
 * One History item (row + its hanging panel).
 * @param {{job: object, title: string, kind: string, word?: string, meta: string, expanded: boolean, onToggle: () => void}} options
 *   `kind` is `jobIconKind(job)`; `word` the job outcome
 *   (`jobStatusWord(job)`, the same word as in the meta line); `meta` the
 *   "job #N · word · time" line.
 * @returns {HTMLDivElement}
 */
export function createHistoryItem({ job, title, kind, word = "", meta, expanded, onToggle }) {
  const item = el("div", expanded ? "hitem open" : "hitem");
  item.dataset.jid = String(job.id);

  const row = el("button", "hrow");
  row.type = "button";
  row.setAttribute("aria-expanded", String(expanded));
  const panelId = `hpanel-${job.id}`;
  row.setAttribute("aria-controls", panelId);

  // The disc reuses library glyphs (the "cached" check for a DONE job, the
  // "notinuse" dash for a not-owned one). Their library words ("Current",
  // "Not in use") are wrong for a job and leaked into copy/paste, so the
  // icon's label is the job outcome (WP DOWNLOADS-3). The icon stays
  // decorative for screen readers: the meta line already says the word
  // (WP 4a.5).
  const icon = el("span", "hic");
  icon.setAttribute("aria-hidden", "true");
  icon.appendChild(createStatusIcon(kind, { size: "sm", label: word }));

  const info = el("span", "hinfo");
  const nm = el("span", "nm", title);
  nm.dataset.appid = String(job.appid);
  info.append(nm, el("span", "meta", meta));

  const chev = el("span", "chev");
  chev.appendChild(chevron());
  row.append(icon, info, chev);
  row.addEventListener("click", () => onToggle());

  const panel = el("div", "hpanel");
  panel.id = panelId;
  panel.hidden = !expanded;

  item.append(row, panel);
  return item;
}

/** Open or close an item in place (no rebuild, keeps focus on the row). */
export function setHistoryItemExpanded(item, expanded) {
  item.classList.toggle("open", expanded);
  const row = item.querySelector(".hrow");
  if (row) row.setAttribute("aria-expanded", String(expanded));
}

function para(text, className) {
  return el("p", className, text);
}

/** The boxed, scrollable log (max-height in kit.css). */
function logBox(lines) {
  const pre = el("pre", "logbox", lines.join("\n"));
  pre.dataset.role = "log-box";
  return pre;
}

/** The Retry line of a failure block: the button on the newest job of an
 * app, else why there is none (a newer job exists, or a Steam tool app). */
function appendRetryOffer(box, offer, retryLine, makeRetry) {
  if (offer === "tool") {
    box.appendChild(para(TOOL_APP_NOTE));
  } else if (offer === "retry" && makeRetry) {
    box.appendChild(para(retryLine));
    const acts = el("div", "acts");
    acts.appendChild(makeRetry());
    box.appendChild(acts);
  } else {
    box.appendChild(para(NEWER_JOB_LINE));
  }
}

function genericFailure(retry) {
  const box = el("div", "failhint");
  box.dataset.hint = "generic";
  box.append(para(GENERIC_FAILURE.title, "failhint-title"), para(GENERIC_FAILURE.body));
  appendRetryOffer(box, retry.offer, GENERIC_FAILURE.retry, retry.make);
  return box;
}

/**
 * Fill (or empty) an item's hanging panel.
 *
 * @param {HTMLElement} panel the item's `.hpanel`
 * @param {{
 *   display: {state: string, lines: string[], truncated: boolean, message?: string},
 *   hint?: string | null,         // jobFailureHint(job, excerpt)
 *   failed?: boolean,             // isFailedPrefill(job)
 *   retry?: {offer: "retry"|"newer"|"tool", make: () => HTMLElement},
 *   rawOpen?: boolean,            // the <details> state to restore
 *   onRawToggle?: (open: boolean) => void,
 * }} options
 */
export function paintHistoryPanel(panel, { display, hint = null, failed = false, retry = { offer: "newer", make: null }, rawOpen = false, onRawToggle = null }) {
  panel.replaceChildren();
  panel.hidden = display.state === EXCERPT_STATE.COLLAPSED;
  if (display.state === EXCERPT_STATE.COLLAPSED) return;

  if (display.state === EXCERPT_STATE.LOADING) {
    panel.appendChild(para(LOADING_LOG_TEXT, "loading"));
    return;
  }
  if (display.state === EXCERPT_STATE.ERROR) {
    panel.appendChild(para(`Could not load the log: ${display.message}`, "errmsg"));
    return;
  }
  if (display.state === EXCERPT_STATE.EMPTY) {
    if (failed) panel.appendChild(genericFailure(retry));
    panel.appendChild(para(EMPTY_LOG_TEXT, "emptymsg"));
    return;
  }

  let outputParent = panel;
  if (hint && HINTS[hint]) {
    const text = HINTS[hint];
    const box = el("div", "failhint");
    box.dataset.hint = hint;
    const code = el("code", "cmd", text.code);
    box.append(para(text.title, "failhint-title"), para(text.body), para(text.codeIntro), code, para(text.after));
    appendRetryOffer(box, retry.offer, text.retry, retry.make);
    const raw = el("details", "rawlog");
    raw.open = !!rawOpen;
    raw.addEventListener("toggle", () => {
      if (onRawToggle) onRawToggle(raw.open);
    });
    raw.appendChild(el("summary", null, text.outputSummary));
    panel.append(box, raw);
    outputParent = raw;
  } else if (failed) {
    panel.appendChild(genericFailure(retry));
  }
  if (display.truncated) outputParent.appendChild(para(TRUNCATED_NOTE, "truncnote"));
  outputParent.appendChild(logBox(display.lines));
}
