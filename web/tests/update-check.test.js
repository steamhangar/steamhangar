/**
 * Headless tests for web/js/lib/update-check.js (WP UPD-2): the "Check
 * updates" decision logic and the bounded, detach-safe status poller.
 *
 * The runner gets fake timers (a manual queue), a gated fake POST and a
 * scripted fake status reader, so every guarantee is checked without a
 * browser and without waiting on real time:
 *   - POST outcomes started / already_running / disabled / 404 / error
 *   - polling stops on running=false and reports the finished status once
 *   - polling gives up after maxTicks
 *   - detach() stops polling, including a read already in flight
 *   - attach() on an older vault-api (404) marks the feature unsupported
 *   - a scheduled run seen on attach is polled but not announced
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CHECK_UPDATES_LABEL,
  UPDATE_PHASE,
  UPDATE_POLL_INTERVAL_MS,
  UPDATE_POLL_MAX_TICKS,
  countUpdates,
  createUpdateCheckRunner,
  describeUpdateOutcome,
  hasUpdateField,
  updateFlagsChanged,
  updateStatusLine,
} from "../js/lib/update-check.js";
import { ApiError, ERROR_KINDS } from "../js/errors.js";

const notFound = () => new ApiError(ERROR_KINDS.NOT_FOUND, "404", { status: 404, detail: "Not Found" });

function idleStatus(over = {}) {
  return {
    enabled: true,
    running: false,
    last_trigger: "manual",
    last_started_at: "2026-10-13T10:00:00Z",
    last_finished_at: "2026-10-13T10:01:00Z",
    last_outcome: "completed",
    last_apps: 12,
    last_ok: 12,
    last_failed: 0,
    last_stale: 3,
    ...over,
  };
}
function runningStatus(over = {}) {
  return idleStatus({
    running: true,
    last_finished_at: null,
    last_outcome: null,
    last_apps: null,
    last_ok: null,
    last_failed: null,
    last_stale: null,
    ...over,
  });
}

/** Manual timer queue: `flush()` fires every pending timer once. */
function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    setTimer(fn, ms) {
      const id = next++;
      pending.set(id, { fn, ms });
      return id;
    },
    clearTimer(id) {
      pending.delete(id);
    },
    count: () => pending.size,
    lastMs: () => [...pending.values()].at(-1)?.ms,
    async flush() {
      const due = [...pending.entries()];
      pending.clear();
      for (const [, t] of due) t.fn();
      // let the async tick (await fetchStatus) settle
      for (let i = 0; i < 5; i++) await Promise.resolve();
    },
  };
}

function makeRunner({ post, statuses, maxTicks } = {}) {
  const timers = fakeTimers();
  const done = [];
  const changes = [];
  const calls = { post: 0, status: 0 };
  const queue = [...(statuses || [])];
  const runner = createUpdateCheckRunner({
    startCheck: async () => {
      calls.post += 1;
      if (typeof post === "function") return post();
      return post;
    },
    fetchStatus: async () => {
      calls.status += 1;
      const s = queue.length > 1 ? queue.shift() : queue[0];
      if (typeof s === "function") return s();
      return s;
    },
    onChange: (snap) => changes.push(snap.phase),
    onDone: (d) => done.push(d),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    maxTicks,
  });
  return { runner, timers, done, changes, calls };
}

// ---------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------

test("labels: the button says 'Check updates' (no download, so a bare check is honest here)", () => {
  assert.equal(CHECK_UPDATES_LABEL, "Check updates");
  assert.doesNotMatch(CHECK_UPDATES_LABEL, /&|update all/i);
});

test("hasUpdateField: true only when a row carries a BOOLEAN update_available", () => {
  assert.equal(hasUpdateField([{ appid: 1 }]), false);
  assert.equal(hasUpdateField([{ appid: 1, update_available: null }]), false);
  assert.equal(hasUpdateField([{ appid: 1 }, { appid: 2, update_available: false }]), true);
  assert.equal(hasUpdateField(null), false);
});

test("countUpdates counts update_available === true only", () => {
  assert.equal(countUpdates([{ update_available: true }, { update_available: false }, { update_available: 1 }, {}]), 1);
});

test("updateFlagsChanged: a flip either way is a change; any other field drift is not", () => {
  const row = (u, size = 1) => ({ appid: 1, update_available: u, size_bytes: size });
  assert.equal(updateFlagsChanged({ updated: [{ prev: row(false), curr: row(true) }] }), true);
  assert.equal(updateFlagsChanged({ updated: [{ prev: row(true), curr: row(false) }] }), true);
  assert.equal(updateFlagsChanged({ updated: [{ prev: row(true, 1), curr: row(true, 2) }] }), false);
  assert.equal(updateFlagsChanged({ updated: [{ prev: { appid: 1 }, curr: row(false) }] }), false, "missing -> false is no visible change");
  assert.equal(updateFlagsChanged(null), false);
});

test("describeUpdateOutcome: completed with updates", () => {
  assert.deepEqual(describeUpdateOutcome(idleStatus()), {
    message: "Checked 12 games · 3 updates available",
    warn: false,
  });
  assert.equal(describeUpdateOutcome(idleStatus({ last_stale: 1, last_apps: 1 })).message, "Checked 1 game · 1 update available");
});

test("describeUpdateOutcome: completed, nothing stale, every game answered -> all up to date", () => {
  assert.deepEqual(describeUpdateOutcome(idleStatus({ last_stale: 0 })), {
    message: "Checked 12 games · all up to date",
    warn: false,
  });
});

test("MUTATION PIN: a failed game is unknown, never reported as 'up to date'", () => {
  const d = describeUpdateOutcome(idleStatus({ last_stale: 0, last_ok: 10, last_failed: 2 }));
  assert.doesNotMatch(d.message, /up to date/);
  assert.equal(d.message, "Checked 12 games · no updates found · 2 could not be checked");
  assert.equal(d.warn, true);
});

test("describeUpdateOutcome: zero cached games is a normal outcome, not a failure", () => {
  assert.deepEqual(describeUpdateOutcome(idleStatus({ last_apps: 0, last_stale: 0 })), {
    message: "No cached games to check.",
    warn: false,
  });
});

test("describeUpdateOutcome: bailed_out / error / aborted / interrupted are non-blocking warnings", () => {
  for (const outcome of ["bailed_out", "error", "aborted", "interrupted"]) {
    const d = describeUpdateOutcome(idleStatus({ last_outcome: outcome }));
    assert.equal(d.warn, true, outcome);
    assert.doesNotMatch(d.message, /up to date|available/, outcome);
  }
  assert.match(describeUpdateOutcome(idleStatus({ last_outcome: "bailed_out" })).message, /oracle did not answer/);
});

test("updateStatusLine: `scheduled` adds a short automatic/manual hint, only when the field exists", () => {
  const now = Date.parse("2026-10-13T10:06:00Z");
  assert.equal(
    updateStatusLine(UPDATE_PHASE.IDLE, idleStatus({ scheduled: true }), now),
    "Updates last checked 5 min ago · automatic checks on",
  );
  assert.equal(
    updateStatusLine(UPDATE_PHASE.IDLE, idleStatus({ scheduled: false }), now),
    "Updates last checked 5 min ago · manual checks only",
  );
  assert.equal(updateStatusLine(UPDATE_PHASE.IDLE, idleStatus(), now), "Updates last checked 5 min ago");
});

test("updateStatusLine: hidden while unknown/unsupported; explains the oracle when disabled; 'last checked' from last_finished_at", () => {
  const now = Date.parse("2026-10-13T10:06:00Z");
  assert.equal(updateStatusLine(UPDATE_PHASE.UNKNOWN, null, now), "");
  assert.equal(updateStatusLine(UPDATE_PHASE.UNSUPPORTED, null, now), "");
  assert.match(updateStatusLine(UPDATE_PHASE.DISABLED, idleStatus({ enabled: false }), now), /manifest oracle is disabled/);
  assert.match(updateStatusLine(UPDATE_PHASE.RUNNING, runningStatus(), now), /Checking/);
  assert.equal(updateStatusLine(UPDATE_PHASE.IDLE, idleStatus(), now), "Updates last checked 5 min ago");
  assert.equal(
    updateStatusLine(UPDATE_PHASE.IDLE, idleStatus({ last_finished_at: null, last_outcome: "interrupted" }), now),
    "Last update check started 6 min ago and did not finish",
  );
  assert.equal(
    updateStatusLine(UPDATE_PHASE.IDLE, idleStatus({ last_started_at: null, last_finished_at: null, last_outcome: null }), now),
    "Updates not checked yet",
  );
});

// ---------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------

test("attach: a 404 from GET /v1/updates/status (older vault-api) marks the feature UNSUPPORTED", async () => {
  const { runner } = makeRunner({ statuses: [() => Promise.reject(notFound())] });
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.UNKNOWN);
  await runner.attach();
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.UNSUPPORTED);
});

test("attach: a non-404 read failure still offers the button (IDLE), it never claims 'unsupported'", async () => {
  const { runner } = makeRunner({
    statuses: [() => Promise.reject(new ApiError(ERROR_KINDS.NETWORK, "down"))],
  });
  await runner.attach();
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.IDLE);
});

test("attach: enabled=false -> DISABLED; idle -> IDLE with the status kept for the line", async () => {
  const a = makeRunner({ statuses: [idleStatus({ enabled: false })] });
  await a.runner.attach();
  assert.equal(a.runner.snapshot().phase, UPDATE_PHASE.DISABLED);
  const b = makeRunner({ statuses: [idleStatus()] });
  await b.runner.attach();
  assert.equal(b.runner.snapshot().phase, UPDATE_PHASE.IDLE);
  assert.equal(b.runner.snapshot().status.last_stale, 3);
  assert.equal(b.timers.count(), 0, "idle: no polling timer");
});

test("run: started -> RUNNING, polls at the interval, stops on running=false and reports ONCE with announce=true", async () => {
  const { runner, timers, done, calls } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [idleStatus({ last_stale: 0 }), runningStatus(), runningStatus(), idleStatus()],
  });
  await runner.attach();
  const res = await runner.run();
  assert.deepEqual(res, { result: "started" });
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.RUNNING);
  assert.equal(runner.isBusy(), true);
  assert.equal(timers.lastMs(), UPDATE_POLL_INTERVAL_MS);
  await timers.flush(); // running
  await timers.flush(); // running
  assert.equal(done.length, 0);
  await timers.flush(); // finished
  assert.equal(done.length, 1);
  assert.equal(done[0].kind, "finished");
  assert.equal(done[0].announce, true);
  assert.equal(done[0].status.last_stale, 3);
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.IDLE);
  assert.equal(timers.count(), 0, "MUTATION PIN: polling stopped once running=false");
  assert.equal(calls.post, 1);
});

test("run: already_running also polls (the result of the run already going is shown)", async () => {
  const { runner, timers, done } = makeRunner({
    post: { status: "already_running", enabled: true },
    statuses: [idleStatus()],
  });
  await runner.attach();
  assert.deepEqual(await runner.run(), { result: "already_running" });
  await timers.flush();
  assert.equal(done.length, 1);
  assert.equal(done[0].announce, true);
});

test("run: disabled (200, enabled=false) -> DISABLED, no polling", async () => {
  const { runner, timers } = makeRunner({ post: { status: "disabled", enabled: false }, statuses: [idleStatus()] });
  await runner.attach();
  assert.deepEqual(await runner.run(), { result: "disabled" });
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.DISABLED);
  assert.equal(timers.count(), 0);
});

test("run: 404 on POST -> UNSUPPORTED; another error -> message, phase unchanged, button free again", async () => {
  const a = makeRunner({ post: () => Promise.reject(notFound()), statuses: [idleStatus()] });
  await a.runner.attach();
  assert.deepEqual(await a.runner.run(), { result: "unsupported" });
  assert.equal(a.runner.snapshot().phase, UPDATE_PHASE.UNSUPPORTED);

  const b = makeRunner({
    post: () => Promise.reject(new ApiError(ERROR_KINDS.AUTH, "401", { status: 401, detail: "Invalid API key" })),
    statuses: [idleStatus()],
  });
  await b.runner.attach();
  assert.deepEqual(await b.runner.run(), { result: "error", message: "Invalid API key" });
  assert.equal(b.runner.snapshot().phase, UPDATE_PHASE.IDLE);
  assert.equal(b.runner.isBusy(), false);
});

test("run: 503 while vault-api shuts down is a transient error (try again), never 'unsupported'", async () => {
  const { runner } = makeRunner({
    post: () => Promise.reject(new ApiError(ERROR_KINDS.SERVER, "503", { status: 503, detail: "shutting down" })),
    statuses: [idleStatus()],
  });
  await runner.attach();
  const res = await runner.run();
  assert.equal(res.result, "error");
  assert.match(res.message, /Try again shortly/);
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.IDLE);
  assert.equal(runner.isBusy(), false);
});

test("MUTATION PIN (in-flight guard): a second run() while the POST is pending or the check runs sends no second POST", async () => {
  let release;
  const gate = new Promise((r) => (release = r));
  const { runner, calls } = makeRunner({
    post: () => gate.then(() => ({ status: "started", enabled: true })),
    statuses: [idleStatus()],
  });
  await runner.attach();
  const first = runner.run();
  assert.equal(runner.isBusy(), true, "busy while the POST is out");
  assert.deepEqual(await runner.run(), { skipped: true });
  release();
  await first;
  assert.deepEqual(await runner.run(), { skipped: true }, "still running on the vault");
  assert.equal(calls.post, 1);
});

test("MUTATION PIN (bound): polling gives up after maxTicks and frees the button", async () => {
  const { runner, timers, done, calls } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [idleStatus(), runningStatus()],
    maxTicks: 3,
  });
  await runner.attach();
  await runner.run();
  const before = calls.status;
  for (let i = 0; i < 3; i++) await timers.flush();
  assert.equal(calls.status - before, 3);
  assert.equal(timers.count(), 0, "no further timer after the bound");
  assert.deepEqual(done, [{ kind: "gave_up", announce: true }]);
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.IDLE);
  assert.equal(UPDATE_POLL_MAX_TICKS * UPDATE_POLL_INTERVAL_MS, 300_000, "default bound: 5 minutes");
});

test("a transient read error while polling keeps polling (counts toward the bound)", async () => {
  const { runner, timers, done } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [idleStatus(), () => Promise.reject(new ApiError(ERROR_KINDS.NETWORK, "x")), idleStatus()],
  });
  await runner.attach();
  await runner.run();
  await timers.flush(); // error
  assert.equal(done.length, 0);
  assert.equal(timers.count(), 1, "re-armed");
  await timers.flush();
  assert.equal(done.length, 1);
});

test("MUTATION PIN (detach): leaving the view stops polling at once, and a read in flight is ignored", async () => {
  let releaseRead;
  let gated = false;
  const { runner, timers, done } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [
      idleStatus(),
      () => {
        gated = true;
        return new Promise((r) => (releaseRead = () => r(idleStatus())));
      },
    ],
  });
  await runner.attach();
  await runner.run();
  await timers.flush(); // read goes out and hangs
  assert.equal(gated, true);
  runner.detach();
  releaseRead();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(done.length, 0, "a detached view gets no result");
  assert.equal(timers.count(), 0, "no timer after detach");
});

test("detach, then attach while the check still runs: polling resumes and the press is still announced", async () => {
  const { runner, timers, done } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [idleStatus(), runningStatus(), runningStatus(), idleStatus()],
  });
  await runner.attach(); // idle
  await runner.run();
  runner.detach();
  assert.equal(timers.count(), 0);
  await runner.attach(); // running -> resume
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.RUNNING);
  assert.equal(timers.count(), 1);
  await timers.flush(); // running
  await timers.flush(); // idle
  assert.equal(done.length, 1);
  assert.equal(done[0].announce, true);
});

test("a scheduled run seen on attach is polled to the end but NOT announced (nobody pressed the button)", async () => {
  const { runner, timers, done } = makeRunner({
    statuses: [runningStatus({ last_trigger: "scheduled" }), idleStatus({ last_trigger: "scheduled" })],
  });
  await runner.attach();
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.RUNNING);
  await timers.flush();
  assert.equal(done.length, 1);
  assert.equal(done[0].kind, "finished");
  assert.equal(done[0].announce, false);
});

test("a 404 while polling (vault-api downgraded mid-run) -> UNSUPPORTED, polling stops", async () => {
  const { runner, timers } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [idleStatus(), () => Promise.reject(notFound())],
  });
  await runner.attach();
  await runner.run();
  await timers.flush();
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.UNSUPPORTED);
  assert.equal(timers.count(), 0);
});

test("MUTATION PIN: leave and return mid-run, the status read on return FAILS -> polling resumes (never busy forever)", async () => {
  const { runner, timers, done } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [idleStatus(), () => Promise.reject(new ApiError(ERROR_KINDS.NETWORK, "x")), idleStatus()],
  });
  await runner.attach();
  await runner.run();
  runner.detach();
  await runner.attach(); // read fails while phase is RUNNING
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.RUNNING);
  assert.equal(timers.count(), 1, "polling re-armed");
  await timers.flush();
  assert.equal(done.length, 1);
  assert.equal(runner.isBusy(), false);
});

test("MUTATION PIN: a run that finished while the view was closed clears `announce` — a later scheduled run is not toasted", async () => {
  const { runner, timers, done } = makeRunner({
    post: { status: "started", enabled: true },
    statuses: [idleStatus(), idleStatus(), runningStatus({ last_trigger: "scheduled" }), idleStatus({ last_trigger: "scheduled" })],
  });
  await runner.attach();
  await runner.run();
  runner.detach(); // the pressed run ends while away
  await runner.attach(); // idle: the press's result is not waited for any more
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.IDLE);
  runner.detach();
  await runner.attach(); // a scheduled run is going
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.RUNNING);
  await timers.flush();
  assert.equal(done.length, 1);
  assert.equal(done[0].announce, false, "the scheduled run's result is not toasted as if pressed");
});

test("attach: a status read that lands after detach() changes nothing (stale guard on the success path)", async () => {
  let release;
  const { runner, timers, changes } = makeRunner({
    statuses: [() => new Promise((r) => (release = () => r(runningStatus())))],
  });
  const pending = runner.attach();
  runner.detach();
  release();
  await pending;
  assert.equal(runner.snapshot().phase, UPDATE_PHASE.UNKNOWN);
  assert.equal(timers.count(), 0, "no polling started for a closed view");
  assert.deepEqual(changes, []);
});
