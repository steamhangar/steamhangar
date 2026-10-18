/**
 * Overflow cue for sideways-scrolling rows (WP MOBILE-1b,
 * docs/design/rc21/mobile.html section 2, decision Q2 Weg B).
 *
 * A row that scrolls sideways (the kit's `.seg`, the Library chips on a
 * phone, the Settings section strip, the detail sheet's tabs) carries the
 * class `scue`; css/kit.css fades the edge(s) behind which more content
 * sits with a mask. Which edge fades is set here, by toggling three
 * classes on the row:
 *
 *   - `fits`     nothing to scroll: no mask at all;
 *   - `at-start` scrolled to the very left: only the right edge fades;
 *   - `at-end`   scrolled to the very right: only the left edge fades;
 *   - neither    somewhere in between: both edges fade.
 *
 * One shared, passive, capturing `scroll` listener on the document repaints
 * whichever `.scue` row scrolled (scroll events do not bubble, but they do
 * pass the capture phase), and one shared ResizeObserver repaints a row
 * whose box changed (a rotated phone, 200 % text). No scroll-driven
 * animations: this works in every browser the app supports.
 *
 * `watchScrollCue(row)` is called by whoever builds a row; on the first
 * paint it also scrolls the row (sideways only, never the page) so its
 * pressed / selected / current item is in view.
 */

/** Slack in px: sub-pixel layouts never reach the exact end. */
const SLACK = 1;

const ACTIVE_ITEM = '[aria-pressed="true"], [aria-selected="true"], [aria-current="true"]';

/**
 * Which state a row is in. Pure, for the unit test.
 * @param {{scrollLeft: number, scrollWidth: number, clientWidth: number}} box
 * @returns {{fits: boolean, atStart: boolean, atEnd: boolean}}
 */
export function cueState({ scrollLeft, scrollWidth, clientWidth }) {
  const max = Math.max(0, (scrollWidth || 0) - (clientWidth || 0));
  if (max <= SLACK) return { fits: true, atStart: true, atEnd: true };
  // RTL scrolls with negative scrollLeft; the app is LTR, but stay sane.
  const left = Math.abs(scrollLeft || 0);
  return { fits: false, atStart: left <= SLACK, atEnd: left >= max - SLACK };
}

/** Apply `cueState` to a row's classes. */
export function paintScrollCue(row) {
  if (!row || !row.classList) return;
  const st = cueState(row);
  row.classList.toggle("fits", st.fits);
  row.classList.toggle("at-start", !st.fits && st.atStart);
  row.classList.toggle("at-end", !st.fits && st.atEnd);
}

/**
 * Scroll `row` sideways so its active item is fully visible (centred when
 * it can be). Only `row.scrollLeft` moves: `scrollIntoView` would also
 * scroll the page vertically.
 * @returns {number|null} the new scrollLeft, or null when nothing moved
 */
export function revealActiveItem(row) {
  if (!row || typeof row.querySelector !== "function") return null;
  const item = row.querySelector(ACTIVE_ITEM);
  if (!item) return null;
  const max = Math.max(0, row.scrollWidth - row.clientWidth);
  if (max <= SLACK) return null;
  const start = item.offsetLeft - row.offsetLeft * (item.offsetParent === row ? 0 : 1);
  const end = start + item.offsetWidth;
  const view = row.scrollLeft;
  if (start >= view && end <= view + row.clientWidth) return null; // already visible
  const target = Math.min(max, Math.max(0, start - (row.clientWidth - item.offsetWidth) / 2));
  row.scrollLeft = target;
  return target;
}

let listening = false;
let observer = null;
/** Rows the observer has seen laid out at least once. */
const laidOut = new WeakSet();

function onScroll(event) {
  const row = event.target;
  if (row && row.classList && row.classList.contains("scue")) paintScrollCue(row);
}

function ensureShared() {
  if (!listening && typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    listening = true;
  }
  if (!observer && typeof ResizeObserver === "function") {
    observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const row = entry.target;
        if (row.isConnected === false) {
          // A row that was on the page and left it (a re-render) is let
          // go; one built but not attached yet stays observed.
          if (laidOut.has(row)) observer.unobserve(row);
          continue;
        }
        if (!laidOut.has(row)) {
          laidOut.add(row);
          revealActiveItem(row);
        }
        paintScrollCue(row);
      }
    });
  }
}

/**
 * Wire a row: give it the `scue` class, paint it now and whenever it
 * scrolls or resizes, and reveal its active item on the first layout.
 * Safe without a DOM layout (tests): the classes just stay as painted.
 * @param {HTMLElement} row
 * @returns {HTMLElement} the row
 */
export function watchScrollCue(row) {
  if (!row || !row.classList) return row;
  row.classList.add("scue");
  ensureShared();
  if (observer) observer.observe(row);
  else paintScrollCue(row);
  return row;
}

/** Repaint after the row's content changed without its box changing. */
export function refreshScrollCue(row) {
  paintScrollCue(row);
}
