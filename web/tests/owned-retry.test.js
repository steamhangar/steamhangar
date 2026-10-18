/**
 * WP WEB-FIX-20.2: the bounded owned-list retry (`lib/owned-library.js`
 * `createOwnedRetry`, `isTransientOwnedError`). A transient failure (no
 * answer, a 5xx: vault-api restarting) is retried after 2 s, 5 s, 10 s and
 * then left alone (the relay hits Steam; the Library's Reload is the retry
 * after that); a configuration answer (409/422/403) is never retried.
 *
 * Harness: fake timers (an array of armed callbacks), no DOM, no fetch.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { OWNED_RETRY_DELAYS_MS, OWNED_STATUS, createOwnedRetry, isTransientOwnedError } from "../js/lib/owned-library.js";

const err = (errorStatus) => ({ status: OWNED_STATUS.ERROR, errorStatus, loading: false, games: [] });
const ready = { status: OWNED_STATUS.READY, errorStatus: null, loading: false, games: [] };

function fakeTimers() {
  const armed = [];
  return {
    armed,
    setTimer: (fn, ms) => {
      const t = { fn, ms, cleared: false };
      armed.push(t);
      return t;
    },
    clearTimer: (t) => {
      t.cleared = true;
    },
    fire() {
      const t = armed.find((x) => !x.cleared && !x.fired);
      t.fired = true;
      t.fn();
    },
  };
}

test("isTransientOwnedError: network errors and 5xx only", () => {
  assert.equal(isTransientOwnedError(err(null)), true);
  assert.equal(isTransientOwnedError(err(502)), true);
  assert.equal(isTransientOwnedError(err(503)), true);
  assert.equal(isTransientOwnedError(err(409)), false, "no Steam key: configuration");
  assert.equal(isTransientOwnedError(err(422)), false);
  assert.equal(isTransientOwnedError(err(403)), false);
  assert.equal(isTransientOwnedError(ready), false);
  assert.equal(isTransientOwnedError({ status: OWNED_STATUS.UNSET, errorStatus: null }), false);
});

test("MUTATION PIN: a transient failure is retried after 2 s, 5 s, 10 s, then never again", () => {
  assert.deepEqual([...OWNED_RETRY_DELAYS_MS], [2000, 5000, 10000]);
  const timers = fakeTimers();
  let loads = 0;
  const retry = createOwnedRetry({ load: () => loads++, setTimer: timers.setTimer, clearTimer: timers.clearTimer });
  retry.settled(err(null));
  assert.equal(retry.pending, true, "a retry is armed");
  retry.settled(err(null)); // a second notification of the same state arms nothing more
  assert.deepEqual(timers.armed.map((t) => t.ms), [2000]);
  for (let i = 0; i < 3; i++) {
    timers.fire();
    assert.equal(loads, i + 1);
    retry.settled({ ...err(503), loading: true }); // the load's own "loading" state is ignored
    retry.settled(err(503));
  }
  assert.deepEqual(timers.armed.map((t) => t.ms), [2000, 5000, 10000], "three retries, no fourth");
  assert.equal(retry.pending, false, "settled: no retry armed any more");
});

test("success, a configuration answer or a manual load resets the schedule", () => {
  const timers = fakeTimers();
  let loads = 0;
  const retry = createOwnedRetry({ load: () => loads++, setTimer: timers.setTimer, clearTimer: timers.clearTimer });
  retry.settled(err(null));
  timers.fire();
  retry.settled(ready);
  assert.equal(retry.pending, false);
  retry.settled(err(null));
  assert.equal(timers.armed.at(-1).ms, 2000, "a later failure starts over at 2 s");
  retry.settled(err(409));
  assert.equal(timers.armed.at(-1).cleared, true, "a configuration answer cancels the armed retry");
  retry.settled(err(null));
  retry.reset();
  assert.equal(retry.pending, false);
  assert.equal(timers.armed.at(-1).cleared, true, "a manual load (Reload) cancels the armed retry");
  assert.equal(loads, 1);
});
