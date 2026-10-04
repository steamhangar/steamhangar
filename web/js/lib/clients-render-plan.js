/**
 * Clients-sheet poll-tick render decision (WP 4a.7).
 *
 * Same shape as `lib/render-plan.js` (games tick) and
 * `lib/downloads-render-plan.js` (jobs tick) — this WP's brief asks
 * explicitly for "patch-in-place on poll ticks per round-7, reuse the
 * render-plan pattern". Unlike those two modules there is no ANIMATED node
 * at stake here (a client row's status glyph is the static "warn"/"cached"
 * kind — components/status-icon.js — neither of which ever moves), so the
 * round-7 concern this pattern originally exists for (never recreate an
 * in-flight animation) does not literally apply. The pattern is still worth
 * reusing for a different, real reason: the clients sheet can legitimately
 * be open while its 20s poll ticks (`store.js`'s `clientsMs` interval), and
 * a full list rebuild on every tick would reset scroll position for no
 * reason on the far more common case (a hit-rate counter ticking up) —
 * only an actual `bypass_suspected` flip changes which SECTION a client
 * belongs in and genuinely needs its row moved.
 *
 * Pure DECISION only — no DOM. `components/clients-sheet.js` owns applying
 * the verdict.
 */

/**
 * @param {{isFirst: boolean, added: object[], updated: {prev: object, curr: object}[], removed: object[]} | null | undefined} diff
 *   `diffByKey`'s result for the `GET /v1/clients` snapshot (keyed by
 *   `client_id`).
 * @returns {{full: boolean, patch: string[], rebuild: string[]}}
 *   `full: true` means "re-render both sections from the current snapshot";
 *   `patch`/`rebuild` are meaningless in that case. Otherwise `patch` lists
 *   client ids whose stats changed with their SECTION unchanged (update the
 *   row's text in place), and `rebuild` lists client ids whose
 *   `bypass_suspected` flipped (the row must move to the other section).
 */
export function planClientsUpdate(diff) {
  if (!diff || diff.isFirst) {
    return { full: true, patch: [], rebuild: [] };
  }
  if ((diff.added && diff.added.length) || (diff.removed && diff.removed.length)) {
    // A client appearing/disappearing from GET /v1/clients is rare (a brand
    // new agent report, or a Remove — WP WEB-FEAT-4) — same accepted
    // simplification as the games/jobs render-plan siblings.
    return { full: true, patch: [], rebuild: [] };
  }

  const patch = [];
  const rebuild = [];
  for (const { prev, curr } of diff.updated || []) {
    // ---------------------------------------------------------------
    // MUTATION TARGET: a bypass_suspected flip must land in `rebuild`,
    // NOT `patch`. If this branch were removed/weakened, a client that
    // just started (or stopped) being suspected would keep sitting in the
    // wrong section — visually claiming the opposite of what
    // GET /v1/clients now reports — until some unrelated later tick
    // happened to force a full render.
    // ---------------------------------------------------------------
    if (!!prev.bypass_suspected !== !!curr.bypass_suspected) {
      rebuild.push(curr.client_id);
    } else {
      patch.push(curr.client_id);
    }
  }
  return { full: false, patch, rebuild };
}
