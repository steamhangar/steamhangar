/**
 * lib/bulk-room.js (WP WEB-FIX-6): the bulk bar's live height -> `--bulk-h`.
 * Fake ResizeObserver and a recording style object; no browser.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createBulkHeightWatcher } from "../js/lib/bulk-room.js";

function harness() {
  const writes = [];
  const rootStyle = { setProperty: (name, value) => writes.push([name, value]) };
  const observers = [];
  class FakeRO {
    constructor(cb) {
      this.cb = cb;
      this.targets = [];
      observers.push(this);
    }
    observe(el) { this.targets.push(el); }
    disconnect() { this.targets = []; }
    fire() { this.cb([]); }
  }
  return { writes, rootStyle, observers, FakeRO };
}

/** A bar whose border-box height (getBoundingClientRect) is `height`. */
function bar(height) {
  return { height, getBoundingClientRect() { return { height: this.height }; } };
}

test("writes the bar's measured height as a px --bulk-h, rounded up", () => {
  const h = harness();
  const w = createBulkHeightWatcher({ rootStyle: h.rootStyle, ResizeObserverImpl: h.FakeRO });
  const el = bar(131);
  w.watch(el);
  h.observers[0].fire();
  assert.deepEqual(h.writes, [["--bulk-h", "131px"]]);
  // getBoundingClientRect is fractional (offsetHeight would already be the
  // integer 169 here and under-reserve by 0.4px): rounded UP.
  el.height = 169.4;
  h.observers[0].fire();
  assert.deepEqual(h.writes.at(-1), ["--bulk-h", "170px"]);
});

test("one observer for all mounts: watch() drops the previous bar before observing the new one", () => {
  const h = harness();
  const w = createBulkHeightWatcher({ rootStyle: h.rootStyle, ResizeObserverImpl: h.FakeRO });
  const first = bar(120);
  const second = bar(150);
  w.watch(first);
  w.watch(second);
  assert.equal(h.observers.length, 1);
  assert.deepEqual(h.observers[0].targets, [second]);
  h.observers[0].fire();
  assert.deepEqual(h.writes, [["--bulk-h", "150px"]]);
});

test("a detached or zero-height bar never overwrites the last real height", () => {
  const h = harness();
  const w = createBulkHeightWatcher({ rootStyle: h.rootStyle, ResizeObserverImpl: h.FakeRO });
  const el = bar(140);
  w.watch(el);
  h.observers[0].fire();
  el.height = 0; // what a detached element reports
  h.observers[0].fire();
  assert.deepEqual(h.writes, [["--bulk-h", "140px"]]);
});

test("unwatch() (leaving the Library) disconnects and drops the bar; a later watch() re-arms it", () => {
  const h = harness();
  const w = createBulkHeightWatcher({ rootStyle: h.rootStyle, ResizeObserverImpl: h.FakeRO });
  const first = bar(130);
  w.watch(first);
  w.unwatch();
  assert.deepEqual(h.observers[0].targets, []);
  // A late callback after unwatch() writes nothing (no bar is held).
  h.observers[0].fire();
  assert.deepEqual(h.writes, []);
  const second = bar(145);
  w.watch(second);
  assert.deepEqual(h.observers[0].targets, [second]);
  h.observers[0].fire();
  assert.deepEqual(h.writes, [["--bulk-h", "145px"]]);
});

test("no ResizeObserver (fake DOM, old browsers) or no root style: a no-op, theme.css's fallback applies", () => {
  const h = harness();
  const noRO = createBulkHeightWatcher({ rootStyle: h.rootStyle, ResizeObserverImpl: undefined });
  assert.doesNotThrow(() => noRO.watch(bar(100)));
  const noRoot = createBulkHeightWatcher({ rootStyle: undefined, ResizeObserverImpl: h.FakeRO });
  assert.doesNotThrow(() => noRoot.watch(bar(100)));
  assert.doesNotThrow(() => noRO.unwatch());
  assert.equal(h.observers.length, 0);
  assert.deepEqual(h.writes, []);
});
