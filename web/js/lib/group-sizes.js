/**
 * Depot lists for exact group sizes (WP TOOL-APPS-2c, ADR-0022 §2).
 *
 * A group card shows the union of its members' depots, each depot counted
 * once. `GET /v1/games` carries no depot ids, so for a group with two or
 * more members that have bytes, the depot list of each such member is read
 * from `GET /v1/games/{appid}` (lib/content-groups.js
 * `membersNeedingDepots`). This module caches those lists and fetches them
 * only when needed: a cached list stays valid while the member's
 * `size_bytes` and `depot_count` on the games list are unchanged (the
 * "signature"); a changed signature (a download growing, a delete) asks
 * again once. A failed read is remembered for that signature too, so a
 * broken detail route is not hammered on every poll tick; the card then
 * keeps showing the parent's own size.
 *
 * No timer of its own: `request()` is called by the views after each games
 * tick, never on a schedule.
 */

function signature(game) {
  return `${game?.size_bytes ?? "null"}:${game?.depot_count ?? "null"}`;
}

/**
 * @param {{
 *   fetchDetail: (appid: number) => Promise<{depots?: object[]}>,
 *   onChange?: () => void,
 * }} deps
 */
export function createGroupSizeResolver({ fetchDetail, onChange }) {
  /** appid -> {sig, depots|null} */
  const cache = new Map();
  /** appid -> sig being fetched */
  const inFlight = new Map();
  const listeners = new Set();
  if (onChange) listeners.add(onChange);

  function notify() {
    for (const fn of listeners) {
      try {
        fn();
      } catch {
        // a listener's own failure must not stop the others
      }
    }
  }

  /** The cached depot list for this row, or undefined when none is known
   * for its current signature. */
  function depotsFor(game) {
    const hit = cache.get(game?.appid);
    if (!hit || hit.sig !== signature(game) || !Array.isArray(hit.depots)) return undefined;
    return hit.depots;
  }

  /** Ask for every row whose list is missing or stale (and not already
   * asked for at this signature). */
  function request(games) {
    for (const game of Array.isArray(games) ? games : []) {
      const appid = game?.appid;
      if (!Number.isInteger(appid)) continue;
      const sig = signature(game);
      const hit = cache.get(appid);
      if (hit && hit.sig === sig) continue;
      if (inFlight.get(appid) === sig) continue;
      inFlight.set(appid, sig);
      Promise.resolve()
        .then(() => fetchDetail(appid))
        .then(
          (detail) => {
            const depots = Array.isArray(detail?.depots) ? detail.depots : [];
            return depots.map((d) => ({ depotid: d.depotid, size_bytes: d.size_bytes ?? null }));
          },
          () => null,
        )
        .then((depots) => {
          if (inFlight.get(appid) !== sig) return; // superseded by a newer request
          inFlight.delete(appid);
          cache.set(appid, { sig, depots });
          if (depots) notify();
        });
    }
  }

  return {
    depotsFor,
    request,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    /** Test hook: forget everything. */
    reset() {
      cache.clear();
      inFlight.clear();
    },
  };
}
