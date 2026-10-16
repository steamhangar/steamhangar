/**
 * "Content packages" section of the game detail sheet (WP TOOL-APPS-2c,
 * ADR-0022 §3, user decisions Q4 A and Q3 B variant).
 *
 * The group card in the Library shows only "+N content packages"; this is
 * where the packages are listed: name, app id, type, status, size and where
 * it is installed, plus a "Retry download" button for a package that failed
 * (its own `POST /v1/prefill`, the same request its own card would send).
 * A package name repeats the game's name more often than not ("<game> -
 * <what>"); the visible text drops that prefix, the full name stays in the
 * row's `title` and in a screen-reader-only span (the visible short text is
 * `aria-hidden`).
 *
 * DOM-building, store-free: `game-detail-sheet.js` passes the data and the
 * retry callback in, so web/tests/package-list.test.js drives it against the
 * fake DOM.
 */

import { STATUS_LABEL } from "./status-icon.js";
import {
  dispKind,
  KIND,
  statusWordFor,
  installedOnSummary,
  showsNotOwnedNote,
  NOT_OWNED_LIBRARY_NOTE,
} from "../lib/game-status.js";
import { formatBytesGB } from "../lib/format.js";
import { UNKNOWN_APP_NAME } from "../lib/app-names.js";
import { shortPackageName } from "../lib/content-groups.js";

export const PACKAGES_HEADING = "Content packages";
export const RETRY_LABEL = "Retry download";

/** Display words for `app_type` (the server's normalised vocabulary). */
export const APP_TYPE_LABEL = Object.freeze({
  game: "Game",
  dlc: "DLC",
  tool: "Tool",
  application: "Application",
  config: "Config",
  music: "Soundtrack",
  video: "Video",
  demo: "Demo",
});

/** The full name of a package row: its own name, else "Unknown app". */
export function packageFullName(pkg) {
  return typeof pkg?.name === "string" && pkg.name.trim() ? pkg.name.trim() : UNKNOWN_APP_NAME;
}

/** "Total with content packages: 2.5 GB", or null while unknown. */
export function packagesTotalText(groupSizeBytes) {
  const size = formatBytesGB(groupSizeBytes);
  return size ? `Total with content packages: ${size}` : null;
}

/** The facts line of one package: "App 123 · DLC · 0.3 GB · Installed on pc". */
export function packageFactsText(pkg) {
  const parts = [`App ${pkg.appid}`];
  const type = APP_TYPE_LABEL[pkg.app_type];
  if (type) parts.push(type);
  const size = formatBytesGB(pkg.size_bytes);
  if (size) parts.push(size);
  const installed = installedOnSummary(pkg.installed_on);
  if (installed) parts.push(`Installed on ${installed}`);
  return parts.join(" · ");
}

/**
 * Whether a package's row offers "Retry download": it shows as Failed and
 * has no prefill job queued, running or paused.
 * @param {object} pkg
 * @param {object | undefined} liveJob running/paused job of the package
 * @param {object | undefined} trackedJob queued/running/paused job
 */
export function offersPackageRetry(pkg, liveJob, trackedJob) {
  return dispKind(pkg, liveJob) === KIND.ERROR && !trackedJob;
}

/** The structural part of the section, for the sheet's patch-vs-rebuild key. */
export function packagesStructuralKey(packages, { liveJobFor, trackedJobFor }) {
  return packages
    .map((p) => {
      const live = liveJobFor(p.appid);
      return `${p.appid}:${dispKind(p, live)}:${offersPackageRetry(p, live, trackedJobFor(p.appid)) ? 1 : 0}${showsNotOwnedNote(p) ? ":n" : ""}`;
    })
    .join(",");
}

/**
 * @param {{appid: number, name?: string, packages: object[]}} group the open
 *   game's group row (lib/content-groups.js `groupGames`)
 * @param {{
 *   groupSizeBytes: number | null,
 *   liveJobFor: (appid: number) => object | undefined,
 *   trackedJobFor: (appid: number) => object | undefined,
 *   onRetry: (pkg: object, button: HTMLButtonElement) => void,
 * }} ctx
 * @returns {HTMLElement}
 */
export function buildPackagesSection(group, ctx) {
  const wrap = document.createElement("div");
  wrap.className = "detail-block pkgsec";
  const heading = document.createElement("h4");
  heading.className = "sec";
  heading.textContent = PACKAGES_HEADING;
  wrap.appendChild(heading);

  const total = document.createElement("p");
  total.className = "hint pkgtotal";
  total.dataset.role = "pkgtotal";
  const totalText = packagesTotalText(ctx.groupSizeBytes);
  total.textContent = totalText || "";
  total.hidden = !totalText;
  wrap.appendChild(total);

  const list = document.createElement("div");
  list.className = "pkg-list";
  for (const pkg of group.packages) list.appendChild(buildPackageRow(pkg, group.name, ctx));
  wrap.appendChild(list);
  return wrap;
}

function buildPackageRow(pkg, parentName, ctx) {
  const full = packageFullName(pkg);
  const live = ctx.liveJobFor(pkg.appid);
  const kind = dispKind(pkg, live);

  const row = document.createElement("div");
  row.className = "pkg-row";
  row.dataset.appid = String(pkg.appid);
  row.title = full;

  const main = document.createElement("div");
  main.className = "pkgmain";
  const name = document.createElement("span");
  name.className = "pkgname";
  name.setAttribute("aria-hidden", "true");
  name.textContent = shortPackageName(full, parentName);
  const srName = document.createElement("span");
  srName.className = "sr-only";
  srName.textContent = full;
  const facts = document.createElement("span");
  facts.className = "pkgfacts";
  facts.dataset.role = "pkgfacts";
  facts.textContent = packageFactsText(pkg);
  main.append(name, srName, facts);
  // WP NOT-OWNED-2: stays Cached, with a small neutral note (no red, no Retry).
  if (showsNotOwnedNote(pkg)) {
    const note = document.createElement("span");
    note.className = "pkgnote";
    note.dataset.role = "pkgnote";
    note.textContent = NOT_OWNED_LIBRARY_NOTE;
    main.appendChild(note);
  }
  row.appendChild(main);

  const state = document.createElement("span");
  state.className = "pkgstate tx-" + kind;
  state.textContent = statusWordFor(kind, STATUS_LABEL);
  row.appendChild(state);

  if (offersPackageRetry(pkg, live, ctx.trackedJobFor(pkg.appid))) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn sm";
    btn.textContent = RETRY_LABEL;
    btn.setAttribute("aria-label", `${RETRY_LABEL} — ${full}`);
    btn.addEventListener("click", () => ctx.onRetry(pkg, btn));
    row.appendChild(btn);
  }
  return row;
}

/**
 * Patch the volatile text of an already-built section (sizes, installed-on,
 * the total) without touching its structure.
 * @param {HTMLElement} container
 * @param {object[]} packages
 * @param {number | null} groupSizeBytes
 */
export function patchPackagesSection(container, packages, groupSizeBytes) {
  const total = container.querySelector('[data-role="pkgtotal"]');
  if (total) {
    const text = packagesTotalText(groupSizeBytes);
    total.textContent = text || "";
    total.hidden = !text;
  }
  for (const pkg of packages) {
    const row = container.querySelector(`.pkg-row[data-appid="${pkg.appid}"]`);
    const facts = row ? row.querySelector('[data-role="pkgfacts"]') : null;
    if (facts) facts.textContent = packageFactsText(pkg);
  }
}
