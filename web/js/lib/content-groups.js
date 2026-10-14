/**
 * Content packages grouped under their parent game (WP TOOL-APPS-2c,
 * ADR-0022; api/README.md "Content packages").
 *
 * `GET /v1/games` stays a flat list (decision Q2 A): since WP TOOL-APPS-2a a
 * row may carry `parent_appid` (the TOP-LEVEL game it is a content package
 * of, already resolved server-side, one level) and `app_type`. This module
 * groups that list for the Library. Pure: no DOM, no fetch.
 *
 * Rules (ADR-0022 §2/§3, user decisions Q3 B variant and Q4 A):
 * - A row is a package of a group only when its `parent_appid` names ANOTHER
 *   row of the same list that is itself top-level. A package whose parent is
 *   not in the list, a self reference, a parent that is itself a package, a
 *   tool app (`tool_app`) or an owned-only row stays an ordinary card. A
 *   missing or null `parent_appid` (an older vault-api) means ungrouped, so
 *   an old response renders exactly as before.
 * - The group card is the parent's own row plus: `packages` (the raw child
 *   rows), `update_available` true when the parent or any package has it,
 *   `installed_on` the union by client id. The group's size is the server's
 *   `group_size_bytes` on the parent row (WP TOOL-APPS-2: the union of the
 *   members' depots, each depot counted once — never the sum of the per-app
 *   sizes); a server without that field shows the parent's own size.
 *   `status`, `size_bytes`, `last_prefill_at` and every other field stay the
 *   parent's own, so the card's status is the parent's status.
 * - A package "failed" when it would show as Failed on its own card
 *   (`dispKind` is `error`: `status: "error"`, no live job, not a tool app).
 */

import { dispKind, KIND, hasVisibleCacheContent, isToolApp } from "./game-status.js";

/** The parent app id a row names, or null (missing, malformed, self). */
export function parentAppidOf(game) {
  const p = game?.parent_appid;
  if (!Number.isInteger(p) || p < 1 || p === game.appid) return null;
  return p;
}

/** Whether a row can be a member (package) of a group at all. */
function canBePackage(game) {
  return !!game && game.owned_only !== true && !isToolApp(game);
}

/** Whether a row can be the parent of a group. */
function canBeParent(game) {
  return !!game && game.owned_only !== true && !isToolApp(game) && parentAppidOf(game) === null;
}

/**
 * Union of `installed_on` lists by `client_id`, keeping the newest
 * `reported_at` per client and the parent's order first.
 * @param {object[]} members
 * @returns {{client_id: string, reported_at: string}[]}
 */
export function unionInstalledOn(members) {
  const byClient = new Map();
  for (const m of members) {
    for (const e of Array.isArray(m?.installed_on) ? m.installed_on : []) {
      if (!e || typeof e.client_id !== "string") continue;
      const prev = byClient.get(e.client_id);
      if (!prev || String(e.reported_at || "") > String(prev.reported_at || "")) byClient.set(e.client_id, e);
    }
  }
  return [...byClient.values()];
}

/**
 * Group a flat games list.
 * @param {object[]} games the merged library rows (vault rows plus owned-only rows)
 * @returns {{
 *   rows: object[],
 *   parentOf: Map<number, number>,
 * }} `rows` in the input order with packages removed and parents replaced
 *   by their group row; `parentOf` maps each grouped package appid to its
 *   parent appid.
 */
export function groupGames(games) {
  const list = Array.isArray(games) ? games : [];
  const byAppid = new Map();
  for (const g of list) if (g && !byAppid.has(g.appid)) byAppid.set(g.appid, g);

  const packagesByParent = new Map();
  const parentOf = new Map();
  for (const g of list) {
    const p = parentAppidOf(g);
    if (p === null || !canBePackage(g)) continue;
    const parent = byAppid.get(p);
    if (!canBeParent(parent)) continue;
    if (!packagesByParent.has(p)) packagesByParent.set(p, []);
    packagesByParent.get(p).push(g);
    parentOf.set(g.appid, p);
  }

  const rows = [];
  for (const g of list) {
    if (parentOf.has(g.appid)) continue;
    const packages = packagesByParent.get(g.appid);
    if (!packages) {
      rows.push(g);
      continue;
    }
    const members = [g, ...packages];
    rows.push({
      ...g,
      packages,
      update_available: members.some((m) => m.update_available === true)
        ? true
        : g.update_available,
      installed_on: unionInstalledOn(members),
    });
  }
  return { rows, parentOf };
}

/** True for a group row built by `groupGames`. */
export function isGroup(game) {
  return Array.isArray(game?.packages) && game.packages.length > 0;
}

/** The size a card shows: for a group the server's `group_size_bytes`
 * (the depot union); a server without that field, or a plain row, shows the
 * row's own `size_bytes`. No extra request either way. */
export function displaySizeBytes(game) {
  if (isGroup(game) && typeof game.group_size_bytes === "number") return game.group_size_bytes;
  return game?.size_bytes ?? null;
}

/** Packages of a group that show as Failed (module header). */
export function failedPackages(game, liveJobsByAppid) {
  if (!isGroup(game)) return [];
  return game.packages.filter((p) => dispKind(p, liveJobsByAppid?.get(p.appid)) === KIND.ERROR);
}

/** Whether a group (or plain row) has cache content anywhere in it. */
export function groupHasCacheContent(game) {
  if (hasVisibleCacheContent(game)) return true;
  return isGroup(game) && game.packages.some(hasVisibleCacheContent);
}

/** Every app id of a card: the parent, then its packages. */
export function memberAppids(game) {
  return isGroup(game) ? [game.appid, ...game.packages.map((p) => p.appid)] : [game.appid];
}

/**
 * The raw rows a bulk action sees for the picked cards: a group card adds
 * its parent's raw row and every package (ADR-0022 §4: bulk selection of a
 * group selects parent + all children, each classified as it would be
 * ungrouped). Order: card order, parent before its packages; no duplicates.
 * @param {object[]} pickedRows display rows (group rows or plain rows)
 * @param {Map<number, object>} rawByAppid the ungrouped rows by appid
 * @returns {object[]}
 */
export function expandSelection(pickedRows, rawByAppid) {
  const out = [];
  const seen = new Set();
  for (const row of pickedRows) {
    for (const id of memberAppids(row)) {
      if (seen.has(id)) continue;
      const raw = rawByAppid.get(id);
      if (!raw) continue;
      seen.add(id);
      out.push(raw);
    }
  }
  return out;
}

/** The structural part of a group card (`data-gk`): package count and how
 * many failed. "" for a plain card. */
export function groupCardKey(game, liveJobsByAppid) {
  if (!isGroup(game)) return "";
  return `${game.packages.length}:${failedPackages(game, liveJobsByAppid).length}`;
}

const plural = (n, one, many) => (n === 1 ? one : many);

/** "+1 content package" / "+N content packages". */
export function packagesLineText(count) {
  return `+${count} content ${plural(count, "package", "packages")}`;
}

/** "1 failed" (the warning after the packages line). */
export function failedPackagesText(count) {
  return `${count} failed`;
}

/** The card's accessible-name fragment for a group. */
export function packagesAriaText(count, failedCount) {
  const base = `${count} content ${plural(count, "package", "packages")}`;
  return failedCount ? `${base}, ${failedCount} failed` : base;
}

/**
 * A package's display name in its parent's list: Steam often spells it
 * "<parent name> - <what>" (api/README.md keeps the name as Steam spells
 * it), which is redundant under the parent's own heading. Drops a leading
 * parent name followed by a dash or colon separator; anything else is
 * returned unchanged. Callers keep the full name for title/aria.
 * @param {string} name
 * @param {string | null | undefined} parentName
 * @returns {string}
 */
export function shortPackageName(name, parentName) {
  if (typeof name !== "string") return name;
  const full = name.trim();
  if (typeof parentName !== "string" || !parentName.trim()) return full;
  const prefix = parentName.trim();
  if (!full.toLowerCase().startsWith(prefix.toLowerCase())) return full;
  const rest = full.slice(prefix.length);
  const m = rest.match(/^\s*[-–—:]\s*(\S.*)$/s);
  return m ? m[1] : full;
}

/**
 * Title of a job's app in the Downloads view when the app has no name of
 * its own: "<parent name> · content <appid>" for a grouped package
 * (ADR-0022 §2), else null (the caller's usual fallback applies).
 * @param {object | undefined} game the app's raw row
 * @param {(appid: number) => string | null} parentTitle
 */
export function contentPackageFallbackTitle(game, parentTitle) {
  const p = parentAppidOf(game);
  if (p === null) return null;
  const title = parentTitle(p);
  return title ? `${title} · content ${game.appid}` : null;
}
