/**
 * Downloads History paging and the page subline count (WP DOWNLOADS-3).
 *
 * The History list fetches up to `HISTORY_LIMIT` finished jobs for the
 * chosen range (`GET /v1/jobs?range=`, the API's maximum page). Rendering
 * all of them made the page several screens long, so the view shows the
 * first `HISTORY_FIRST_PAGE` rows and a "Show 20 more" button that appends
 * `HISTORY_PAGE_STEP` at a time. A range change starts again at the first
 * page.
 *
 * The page subline ("Idle · N in history") counts the same set the list
 * shows for the selected range. It used to count the store's plain
 * `GET /v1/jobs` poll, whose default page is 20 jobs, so a 7-day list of
 * ~130 rows read "20 in history". Until the range's own fetch has
 * answered there is no honest number, and the subline says "Idle" only.
 *
 * Pure: no DOM.
 */

/** The History fetch's page size (the API's maximum; no server paging). */
export const HISTORY_LIMIT = 200;
/** Rows shown before the first "Show more". */
export const HISTORY_FIRST_PAGE = 10;
/** Rows each "Show more" appends. */
export const HISTORY_PAGE_STEP = 20;

/** The last line when the fetch hit HISTORY_LIMIT and every row is shown. */
export const HISTORY_CAPPED_TEXT = "Older entries are not shown; narrow the range or delete history.";

/**
 * What to render of `total` rows when `shown` are allowed.
 * @param {number} total rows in the list for the range
 * @param {number} shown rows the user has asked to see so far
 * @param {{capped?: boolean}} [opts] the fetch returned HISTORY_LIMIT rows
 * @returns {{visible: number, remaining: number, next: number, capped: boolean}}
 *   `next` is how many the next "Show more" appends (0: no button);
 *   `capped` is true only once every fetched row is on screen.
 */
export function historyPage(total, shown, { capped = false } = {}) {
  const t = Number.isFinite(total) && total > 0 ? Math.floor(total) : 0;
  const s = Number.isFinite(shown) && shown > 0 ? Math.floor(shown) : HISTORY_FIRST_PAGE;
  const visible = Math.min(t, s);
  const remaining = t - visible;
  return {
    visible,
    remaining,
    next: Math.min(HISTORY_PAGE_STEP, remaining),
    capped: !!capped && remaining === 0 && t > 0,
  };
}

/** The button's visible text. */
export function showMoreText(next) {
  return `Show ${next} more`;
}

/** The button's accessible name: how many it adds and how many are left. */
export function showMoreLabel(next, remaining) {
  return `Show ${next} more of ${remaining}`;
}

/** The row count after one more click. */
export function nextShown(shown) {
  return (Number.isFinite(shown) && shown > 0 ? shown : HISTORY_FIRST_PAGE) + HISTORY_PAGE_STEP;
}

/** True when a range fetch returned a full page, i.e. older rows may exist. */
export function isHistoryCapped(fetchedCount) {
  return Number.isFinite(fetchedCount) && fetchedCount >= HISTORY_LIMIT;
}

/**
 * The page subline while nothing runs.
 * @param {{loaded: boolean, count: number, capped?: boolean}} history
 *   `loaded`: the range fetch has answered; `count`: rows in the History
 *   list for the range; `capped`: that fetch hit HISTORY_LIMIT.
 * @returns {string}
 */
export function idleSubtitle({ loaded, count, capped = false }) {
  if (!loaded) return "Idle";
  return `Idle · ${count}${capped ? "+" : ""} in history`;
}
