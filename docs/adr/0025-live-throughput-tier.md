# ADR-0025: Live throughput tier in memory, stitched with the persisted 1-minute tier

Status: Accepted

Date: 2026-10-16

Related: GRAPH-ZOOM (1-minute tier, schema 29), GRAPH-LIVE
(`api/vault_api/live_tier.py`, `GET /v1/throughput/live`),
`docs/handover/rc19-run-plan.md`.

## Context

The Downloads graph should feel live. The source of traffic numbers is the
vault-core access log, which gets a line when a request ends (chunks are
small, so a 5 s bucket is accurate). The sweep that reads it
(`event_sweep`) runs on the scheduler cadence, which was the real limit on
freshness. Reading faster all the time would add idle load, and persisting
5 s points would grow the database for a view nobody watches.

## Decision

- A **live tier** is held in memory only: a ring of 5 s cells per address
  covering the last 15 minutes. It is never persisted and is lost on restart;
  there is no schema change.
- The log is **tailed only while a watcher is active**: a client polled
  `GET /v1/throughput/live` within the last ~30 s. While active the tail runs
  at most every 2 s however many clients poll. Otherwise nothing reads the
  log beyond the normal sweep, so idle load is unchanged. There is no
  background thread; the poll itself drives the tail.
- The endpoint returns **one stitched series**: 5 s points for the last
  minute, 15 s points up to 5 minutes back, then points from the persisted
  1-minute tier. A minute comes from the ring only when the ring has covered
  that whole minute since the tailer started; otherwise the persisted row is
  used. The "Current speed" tile reads the same live tier.
- Gaps are explicit: after an idle gap or any reset the tailer restarts from
  the file's end window, stale cells are dropped and the "covered from" mark
  moves to the next minute, so the persisted tier fills the hole. A shrunk
  file or a changed inode restarts at offset 0; a read starting mid-file aligns
  to the next newline. Reads are bounded (bytes per read, reads per refresh).
- The web graph polls only while the Downloads page is visible; the 1 h / 6 h /
  24 h ranges keep the persisted tiers.

## Consequences

- A restart loses at most the last minutes of fine detail; the persisted
  1-minute tier still holds them.
- Lines skipped because of an oversized backlog do not reach the ring (the
  persisted tiers have them).
- Per-cell address count is bounded (overflow folds into one anonymous
  bucket), so memory is bounded.
- Android gets the same series after the web part; the contract is the
  endpoint, not the ring.
