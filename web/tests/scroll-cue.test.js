// WP MOBILE-1b: the overflow cue's shared module (lib/scroll-cue.js) —
// which edge fades, the class toggling, the sideways-only reveal of the
// active item, and the one shared capturing scroll listener.
import { test } from "node:test";
import assert from "node:assert/strict";

/** A row with a real classList and settable scroll geometry. */
function fakeRow({ scrollLeft = 0, scrollWidth = 100, clientWidth = 100, items = [] } = {}) {
  const classes = new Set();
  const row = {
    scrollLeft, scrollWidth, clientWidth, offsetLeft: 0, isConnected: true, dataset: {},
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      contains: (c) => classes.has(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
    },
    classes,
    querySelector: (sel) => (sel.includes("aria-pressed") ? items.find((i) => i.pressed) || null : null),
  };
  for (const item of items) item.offsetParent = row;
  return row;
}

const listeners = [];
globalThis.document = {
  addEventListener: (type, fn, opts) => listeners.push({ type, fn, opts }),
};

const { cueState, paintScrollCue, revealActiveItem, watchScrollCue } = await import("../js/lib/scroll-cue.js");

test("cueState: fits, at the start, in the middle, at the end (1px slack)", () => {
  assert.deepEqual(cueState({ scrollLeft: 0, scrollWidth: 300, clientWidth: 300 }), { fits: true, atStart: true, atEnd: true });
  assert.deepEqual(cueState({ scrollLeft: 0, scrollWidth: 300.6, clientWidth: 300 }).fits, true, "sub-pixel overflow fits");
  assert.deepEqual(cueState({ scrollLeft: 0, scrollWidth: 500, clientWidth: 300 }), { fits: false, atStart: true, atEnd: false });
  assert.deepEqual(cueState({ scrollLeft: 120, scrollWidth: 500, clientWidth: 300 }), { fits: false, atStart: false, atEnd: false });
  assert.deepEqual(cueState({ scrollLeft: 199.5, scrollWidth: 500, clientWidth: 300 }), { fits: false, atStart: false, atEnd: true });
  assert.deepEqual(cueState({}), { fits: true, atStart: true, atEnd: true }, "no layout (test DOM): fits");
});

test("paintScrollCue sets exactly one state: fits, at-start, at-end, or neither (both edges fade)", () => {
  const row = fakeRow({ scrollWidth: 500, clientWidth: 300 });
  paintScrollCue(row);
  assert.deepEqual([...row.classes].sort(), ["at-start"]);
  row.scrollLeft = 100;
  paintScrollCue(row);
  assert.deepEqual([...row.classes], []);
  row.scrollLeft = 200;
  paintScrollCue(row);
  assert.deepEqual([...row.classes], ["at-end"]);
  row.scrollWidth = 300;
  paintScrollCue(row);
  assert.deepEqual([...row.classes], ["fits"]);
});

test("watchScrollCue adds .scue, paints without a ResizeObserver, and one capturing passive scroll listener repaints", () => {
  const a = watchScrollCue(fakeRow({ scrollWidth: 500, clientWidth: 300 }));
  const b = watchScrollCue(fakeRow());
  assert.ok(a.classes.has("scue") && a.classes.has("at-start"));
  assert.ok(b.classes.has("scue") && b.classes.has("fits"));
  const scroll = listeners.filter((l) => l.type === "scroll");
  assert.equal(scroll.length, 1, "one shared listener, not one per row");
  assert.deepEqual(scroll[0].opts, { capture: true, passive: true });
  a.scrollLeft = 200;
  scroll[0].fn({ target: a });
  assert.ok(a.classes.has("at-end") && !a.classes.has("at-start"));
  // A scroll of anything that is not a cue row is ignored.
  assert.doesNotThrow(() => scroll[0].fn({ target: { classList: { contains: () => false } } }));
  assert.doesNotThrow(() => scroll[0].fn({ target: null }));
});

test("revealActiveItem scrolls the row sideways (only) so the pressed item is centred, clamped", () => {
  const items = [0, 1, 2, 3, 4, 5].map((i) => ({ offsetLeft: i * 100, offsetWidth: 90, pressed: i === 4 }));
  const row = fakeRow({ scrollWidth: 600, clientWidth: 250, items });
  assert.equal(revealActiveItem(row), 400 - (250 - 90) / 2);
  assert.equal(row.scrollLeft, 320);
  assert.equal(revealActiveItem(row), null, "already visible: no move");
  const last = fakeRow({ scrollWidth: 600, clientWidth: 250, items: items.map((i, n) => ({ ...i, pressed: n === 5 })) });
  assert.equal(revealActiveItem(last), 350, "clamped to the scroll end");
  assert.equal(revealActiveItem(fakeRow({ scrollWidth: 250, clientWidth: 250, items })), null, "a row that fits never moves");
  assert.equal(revealActiveItem(fakeRow({ scrollWidth: 600, clientWidth: 250 })), null, "no active item");
});
