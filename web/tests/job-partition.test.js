/**
 * Headless tests for web/js/lib/job-partition.js (WP 4a.5).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  partitionJobs,
  countPending,
  queuePosition,
  jobIconKind,
  jobStatusWord,
} from "../js/lib/job-partition.js";

const job = (id, status, extra = {}) => ({
  id,
  appid: 1000 + id,
  type: "prefill",
  status,
  stop_request: null,
  paused_at: null,
  finished_at: null,
  ...extra,
});

test("partitionJobs: buckets by status, ignoring input order", () => {
  const jobs = [job(5, "done"), job(1, "queued"), job(2, "running"), job(3, "error")];
  const p = partitionJobs(jobs);
  assert.deepEqual(p.running.map((j) => j.id), [2]);
  assert.deepEqual(p.queued.map((j) => j.id), [1]);
  assert.deepEqual(
    p.history.map((j) => j.id).sort(),
    [3, 5],
  );
});

test("partitionJobs: null/undefined/non-array input is treated as no jobs", () => {
  for (const input of [null, undefined, "not an array"]) {
    assert.deepEqual(partitionJobs(input), { running: [], paused: [], queued: [], history: [] });
  }
});

test("partitionJobs: queued is sorted FIFO (oldest job id first), not snapshot order", () => {
  // GET /v1/jobs is newest-first, so a naive "keep input order" would show
  // the queue backwards (last-enqueued job first).
  const jobs = [job(30, "queued"), job(10, "queued"), job(20, "queued")];
  const p = partitionJobs(jobs);
  assert.deepEqual(
    p.queued.map((j) => j.id),
    [10, 20, 30],
  );
});

test("partitionJobs: history keeps the snapshot's own (newest-first) order", () => {
  const jobs = [job(3, "error"), job(1, "done"), job(2, "cancelled")];
  const p = partitionJobs(jobs);
  assert.deepEqual(
    p.history.map((j) => j.id),
    [3, 1, 2],
  );
});

// ---------------------------------------------------------------------
// The slot-release divergence (job-partition.js's module header,
// api/README.md "The worker slot"): running and paused are INDEPENDENT
// buckets, not a single mutually-exclusive "active slot" like the mockup.
// A paused job for one app coexisting with a different app's running job
// is the normal, expected shape once the worker slot is released on
// pause — this is the presentation data the Paused-section-is-honest
// requirement in the WP brief is built on.
// ---------------------------------------------------------------------
test("partitionJobs: a paused job and a DIFFERENT app's running job coexist in separate buckets", () => {
  const jobs = [job(1, "paused", { appid: 440 }), job(2, "running", { appid: 730 })];
  const p = partitionJobs(jobs);
  assert.deepEqual(
    p.running.map((j) => j.id),
    [2],
  );
  assert.deepEqual(
    p.paused.map((j) => j.id),
    [1],
  );
  // Neither bucket claims the other's job — proves they are independent,
  // not a single "active slot" the mockup assumed.
  assert.equal(p.running.some((j) => j.id === 1), false);
  assert.equal(p.paused.some((j) => j.id === 2), false);
});

test("countPending: counts queued + running + paused, not done/error/cancelled", () => {
  const jobs = [
    job(1, "queued"),
    job(2, "running"),
    job(3, "paused"),
    job(4, "done"),
    job(5, "error"),
    job(6, "cancelled"),
  ];
  assert.equal(countPending(jobs), 3);
});

test("countPending: zero for an empty/absent snapshot", () => {
  assert.equal(countPending([]), 0);
  assert.equal(countPending(null), 0);
  assert.equal(countPending(undefined), 0);
});

test("queuePosition: 1-based position within the FIFO-sorted queue", () => {
  const jobs = [job(30, "queued"), job(10, "queued"), job(20, "queued")];
  const queued = partitionJobs(jobs).queued;
  assert.equal(queuePosition(queued, 10), 1);
  assert.equal(queuePosition(queued, 20), 2);
  assert.equal(queuePosition(queued, 30), 3);
});

test("queuePosition: null for a job id not present", () => {
  assert.equal(queuePosition([job(1, "queued")], 999), null);
});

test("jobIconKind: maps every real status to a status-icon kind", () => {
  assert.equal(jobIconKind(job(1, "running")), "running");
  assert.equal(jobIconKind(job(1, "paused")), "paused");
  assert.equal(jobIconKind(job(1, "done")), "cached");
  assert.equal(jobIconKind(job(1, "error")), "error");
  assert.equal(jobIconKind(job(1, "cancelled")), "cancelled");
  assert.equal(jobIconKind(job(1, "queued")), "none");
});

test("jobStatusWord: prefill wording", () => {
  assert.equal(jobStatusWord(job(1, "running")), "Downloading");
  assert.equal(jobStatusWord(job(1, "paused")), "Paused");
  assert.equal(jobStatusWord(job(1, "done")), "Done");
  assert.equal(jobStatusWord(job(1, "error")), "Failed");
  assert.equal(jobStatusWord(job(1, "cancelled")), "Cancelled");
});

test("jobStatusWord: cancelled is worded distinctly from error (job outcome honesty)", () => {
  const cancelled = jobStatusWord(job(1, "cancelled"));
  const failed = jobStatusWord(job(1, "error"));
  assert.notEqual(cancelled, failed);
  assert.equal(cancelled, "Cancelled");
});

test("jobStatusWord: GC jobs get GC-specific wording, never the download vocabulary", () => {
  const gc = (id, status) => job(id, status, { type: "gc" });
  assert.equal(jobStatusWord(gc(1, "running")), "Collecting garbage");
  assert.equal(jobStatusWord(gc(1, "done")), "Garbage collected");
  assert.equal(jobStatusWord(gc(1, "error")), "Garbage collection failed");
  assert.equal(jobStatusWord(gc(1, "cancelled")), "Garbage collection cancelled");
});

test("WP PAUSE-ALL-GC-1: a paused GC job reads as GC, not as a paused download", () => {
  assert.equal(jobStatusWord(job(1, "paused", { type: "gc" })), "Garbage collection paused");
  assert.equal(jobStatusWord(job(1, "paused")), "Paused", "prefill wording unchanged");
});

test("WP PAUSE-ALL-GC-1: a finished dry-run GC job does not claim it collected anything", () => {
  assert.equal(jobStatusWord(job(1, "done", { type: "gc", gc_execute: false })), "GC dry run (nothing deleted)");
  assert.equal(jobStatusWord(job(1, "done", { type: "gc", gc_execute: true })), "Garbage collected");
  assert.equal(jobStatusWord(job(1, "done", { type: "gc", gc_execute: null })), "Garbage collected", "unknown mode keeps the old word");
  assert.equal(jobStatusWord(job(1, "done", { type: "gc" })), "Garbage collected", "older server without the field");
  // Only "done" changes: the other outcomes of a dry run keep their words.
  assert.equal(jobStatusWord(job(1, "error", { type: "gc", gc_execute: false })), "Garbage collection failed");
  assert.equal(jobStatusWord(job(1, "running", { type: "gc", gc_execute: false })), "Collecting garbage");
});

// ---------------------------------------------------------------------
// WP 4a.8 backport of the WP 4b.5 divergence (job-partition.js's module
// header): an unrecognized status must land in history with a NEUTRAL
// presentation, never vanish from every bucket silently.
// ---------------------------------------------------------------------
test("partitionJobs: an unrecognized status lands in history, not nowhere", () => {
  const jobs = [job(1, "queued"), job(2, "quarantined")];
  const p = partitionJobs(jobs);
  assert.deepEqual(p.running, []);
  assert.deepEqual(p.paused, []);
  assert.deepEqual(
    p.queued.map((j) => j.id),
    [1],
  );
  // Mutation target: dropping the `|| !KNOWN_STATUSES.has(j.status)` half of
  // the history filter makes this assertion fail (job 2 would match no
  // bucket at all).
  assert.deepEqual(
    p.history.map((j) => j.id),
    [2],
  );
});

test("countPending: an unrecognized status does NOT count toward the nav pip", () => {
  const jobs = [job(1, "queued"), job(2, "quarantined")];
  assert.equal(countPending(jobs), 1);
});

test("jobIconKind: an unrecognized status falls back to the neutral 'none' glyph", () => {
  assert.equal(jobIconKind(job(1, "quarantined")), "none");
});

test("jobStatusWord: an unrecognized status falls back to the raw status string, never a fabricated word", () => {
  assert.equal(jobStatusWord(job(1, "quarantined")), "quarantined");
});
