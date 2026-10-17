/**
 * Downloads rows (WP DOWNLOADS-2, docs/design/rc20/kit.html "Downloads,
 * width-aware"): the job card of the Active column and the kit's
 * attention row (`.jr`: name + meta, a spacer or size column, a 72px
 * button column) shared by Queue, Updates available and Incomplete.
 *
 * These builders only make the markup; every handler, busy state and
 * offline gate stays in `views/downloads.js`, which passes the finished
 * buttons in (so one place keeps the WEB-FEAT-1 aria-disabled rules). The
 * name node always carries `data-appid`, which the view's `patchNames`
 * rewrites when a better title arrives.
 *
 * **No fake progress.** vault-api's job rows carry no progress figure
 * (only a speed and the bytes since start, measured at the cache), so the
 * card draws its progress bar only when a job really has a 0..1
 * `progress` value (`jobProgress`); today none does, and the card shows
 * the speed line alone.
 */
import { createPill } from "./pill.js";

/** The 72px row buttons' words (kit.html: "Update" / "Verify" / "Remove"). */
export const ROW_UPDATE_LABEL = "Update";
export const ROW_VERIFY_LABEL = "Verify";
export const ROW_REMOVE_LABEL = "Remove";

/** The section-head "all" buttons (kit.html: "Update all" / "Verify all"). */
export const UPDATE_ALL_LABEL = "Update all";
export const VERIFY_ALL_LABEL = "Verify all";

/** Accessible name of "Verify all": the visible words plus the count. */
export function verifyAllAriaLabel(count) {
  return `${VERIFY_ALL_LABEL} ${count} ${count === 1 ? "game" : "games"}`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * A job's progress as 0..1, or `null` when the data has none (every job
 * today: the API reports no progress, and the card must not invent one).
 * @param {{progress?: unknown} | null | undefined} job
 * @returns {number | null}
 */
export function jobProgress(job) {
  const p = job ? job.progress : undefined;
  return typeof p === "number" && Number.isFinite(p) && p >= 0 && p <= 1 ? p : null;
}

/** The name node every row shares (`patchNames` keeps it current). */
function nameNode(appid, title) {
  const nm = el("div", "nm", title);
  nm.dataset.appid = String(appid);
  return nm;
}

/**
 * The Active column's job card: name and job meta, a status pill (the
 * running/updating/verify/paused word on the status-icon disc), the
 * progress bar only with real progress, the speed line (`data-role=
 * job-speed`, painted by the view from the statistics poll), the stop and
 * hold notes, and an empty action row (`data-role=job-acts`) the view fills.
 *
 * @param {{job: object, title: string, kind: string, word: string, held?: boolean, holdNote?: string}} options
 * @returns {HTMLDivElement}
 */
export function createJobCard({ job, title, kind, word, held = false, holdNote = "" }) {
  const card = el("div", held ? "job held" : "job");
  card.dataset.jid = String(job.id);
  // WP WEB-FEAT-6: the pill's kind, read back when a games poll lands.
  card.dataset.dk = kind;

  const top = el("div", "t");
  const info = el("div");
  info.append(nameNode(job.appid, title), el("div", "meta", `job #${job.id} · appid ${job.appid}`));
  const pill = createPill({ kind, text: word });
  pill.dataset.role = "job-pill";
  top.append(info, pill);
  card.appendChild(top);

  const progress = jobProgress(job);
  if (progress !== null) {
    const bar = el("div", "bar");
    bar.dataset.role = "job-progress";
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
    bar.setAttribute("aria-label", `${title} progress`);
    const fill = el("i");
    fill.style.setProperty("width", `${Math.round(progress * 100)}%`);
    bar.appendChild(fill);
    card.appendChild(bar);
  }

  if (!held) {
    // WP D15c (D13 SPEED-1): hidden until the statistics poll has a line.
    const speed = el("p", "m2");
    speed.dataset.role = "job-speed";
    speed.title = "Measured at the cache over the last few minutes, on this game's own depots.";
    speed.hidden = true;
    card.appendChild(speed);
  } else if (holdNote) {
    const note = el("p", "hint holdnote", holdNote);
    note.dataset.role = "hold-note";
    card.appendChild(note);
  }

  const stopNote = el("p", "hint stopnote");
  stopNote.dataset.role = "stop-note";
  stopNote.hidden = true;
  card.appendChild(stopNote);

  const acts = el("div", "acts");
  acts.dataset.role = "job-acts";
  card.appendChild(acts);
  return card;
}

/**
 * One attention row (Updates available, Incomplete): name over a mono meta
 * line, an empty middle column, the 72px button. A row without a button
 * (`button: null`, e.g. a game short only of damaged chunks) shows `note`
 * under the meta instead.
 *
 * @param {{appid: number, title: string, meta: string, button: HTMLElement|null, note?: string, dataKey: string}} options
 *   `dataKey` names the row's data attribute (`updateAppid` / `incompleteAppid`).
 * @returns {HTMLDivElement}
 */
export function createAttentionRow({ appid, title, meta, button, note = "", dataKey }) {
  const row = el("div", "jr urow");
  row.dataset[dataKey] = String(appid);
  const info = el("div");
  info.append(nameNode(appid, title), el("div", "meta", meta));
  if (!button && note) {
    const n = el("div", "unote", note);
    n.dataset.role = "damaged-note";
    info.appendChild(n);
  }
  row.append(info, el("span"), button || el("span"));
  return row;
}

/**
 * One queue row: the grip (the view wires drag and the keyboard path on
 * it), the name, the game's size, and the ghost Remove button.
 *
 * @param {{jobId: number, appid: number, title: string, size: string, grip: HTMLElement, removeButton: HTMLElement}} options
 * @returns {HTMLDivElement}
 */
export function createQueueRow({ jobId, appid, title, size, grip, removeButton }) {
  const row = el("div", "jr qrow");
  row.dataset.jid = String(jobId);
  const lead = el("div", "qlead");
  lead.append(grip, nameNode(appid, title));
  const sz = el("span", "sz", size);
  sz.dataset.sizeAppid = String(appid); // the view's patchNames keeps it current
  row.append(lead, sz, removeButton);
  return row;
}
