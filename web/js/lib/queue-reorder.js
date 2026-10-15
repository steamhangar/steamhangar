/**
 * Pure helpers for reordering the Downloads queue (WP D20 QUEUE-ORDER-1b).
 * No DOM, no fetch: `views/downloads.js` owns the pointer and keyboard glue,
 * this module owns the arithmetic so it can be tested headless.
 *
 * Positions are 0-based and count QUEUED jobs only, exactly like the
 * `position` of `POST /v1/jobs/{id}/move` (api/README.md): 0 = runs next.
 */

/**
 * `ids` with `id` taken out and re-inserted at `toIndex` (clamped to the
 * valid range). Returns a new array; an unknown `id` returns a copy.
 *
 * @param {number[]} ids queued job ids in their current order
 * @param {number} id the job to move
 * @param {number} toIndex target position
 * @returns {number[]}
 */
export function reorderIds(ids, id, toIndex) {
  const list = Array.isArray(ids) ? ids.slice() : [];
  const from = list.indexOf(id);
  if (from === -1) return list;
  list.splice(from, 1);
  const to = Math.max(0, Math.min(Number.isFinite(toIndex) ? Math.trunc(toIndex) : from, list.length));
  list.splice(to, 0, id);
  return list;
}

/**
 * Where a key press moves the focused row: ArrowUp/ArrowDown one place,
 * Home/End to the ends. `null` when the key is not a move key or the move
 * would not change anything (already at that end).
 *
 * @param {string} key `KeyboardEvent.key`
 * @param {number} index the row's current position
 * @param {number} length how many rows the queue has
 * @returns {number | null}
 */
export function keyTargetIndex(key, index, length) {
  let target;
  if (key === "ArrowUp") target = index - 1;
  else if (key === "ArrowDown") target = index + 1;
  else if (key === "Home") target = 0;
  else if (key === "End") target = length - 1;
  else return null;
  if (target < 0 || target > length - 1 || target === index) return null;
  return target;
}

/**
 * Insertion index of a dragged row among the OTHER rows: how many of them
 * have their vertical midpoint above the pointer.
 *
 * @param {number[]} otherMidpoints viewport Y of each other row's middle,
 *   top to bottom
 * @param {number} pointerY pointer clientY
 * @returns {number}
 */
export function dropIndex(otherMidpoints, pointerY) {
  let index = 0;
  for (const mid of otherMidpoints) {
    if (mid < pointerY) index += 1;
  }
  return index;
}

/**
 * The queued jobs with `queue_position` rewritten for a move that has been
 * decided but not yet confirmed by the server (the optimistic view). Other
 * jobs are returned unchanged (same objects).
 *
 * @param {object[]} jobs the full jobs snapshot
 * @param {number[]} orderedIds queued job ids in the new order
 * @returns {object[]}
 */
export function withQueuePositions(jobs, orderedIds) {
  return (Array.isArray(jobs) ? jobs : []).map((j) => {
    const at = orderedIds.indexOf(j.id);
    return at === -1 || j.status !== "queued" ? j : { ...j, queue_position: at };
  });
}
