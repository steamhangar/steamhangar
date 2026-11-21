"""Client priority over the prefill runner (WP CAP-503-FIX-1a, ADR-0016 addendum).

The problem (measured 2026-11-01, CAP-503-LOOP-1)
-------------------------------------------------
A Steam client and the prefill runner (SteamPrefill, 8 threads) share
vault-core's global MISS cap (``limit_conn``, 16). Under contention the
runner retried every rejected chunk at once: ~37,000 503s per minute, and the
PC got 6,688 503s in the same minute and 39 Mbit/s instead of ~150 alone.

The decision (operator 2026-11-01, Weg A: client priority)
-----------------------------------------------------------
vault-api holds the runner while a client loads through the cache and lets
it carry on after an idle gap. Nothing in vault-core changes.

How
---
A daemon thread ticks every ``TICK_SECONDS`` while there is something to
look after (``jobs.has_client_priority_work``: a queued or running job, or a
job this controller paused). An idle server with an empty queue does not
read the event log at all. Each tick:

1. arms and tails the live tier (``live_tier.LiveTier``, the ring the
   Downloads page reads, on its own cursor of vault-core's event log);
2. reads its "now" window (``CURRENT_SECONDS`` plus the running cell) and
   adds up the MISS bytes served to addresses that are NOT the runner. The
   runner is every address of a current runner window
   (``runner_identity.windows``) plus every address that sent a runner
   marker line inside the ring (``LiveTier.runner_marks``) whose token is an
   issued one (``runner_identity.live_mark_valid``, the sweep's rules), so a
   new runner address counts as the runner from its first marker on and a
   LAN host requesting the marker path changes nothing;
3. "a client is loading" = that MISS rate is at least
   ``MIN_CLIENT_MISS_BYTES_PER_SECOND``.

The hold:

* **Start** when a client loads: the running prefill job gets a pause
  request tagged ``pause_reason = 'client'`` (``jobs.request_client_pause``),
  and the worker stops claiming queued jobs (``holding``, the gate in
  ``worker.PrefillWorker._run``), so the next queued job does not start
  SteamPrefill just to be paused again.
* **Operator override** (operator decision 2026-11-21, Weg B): a manual
  Resume during a hold marks the job ``client_override``; the worker claims
  such a job despite the hold and this controller never pauses it. The
  override ends with the job or with a manual pause.
* **While holding** each tick repeats the request (a job the worker claimed
  just before the hold started is paused too) and every tick that sees a
  client loading moves ``last_client_at``.
* **Release** once no client has loaded for ``IDLE_GAP_SECONDS``: a client
  pause the worker has not honoured yet is withdrawn (the tag stays, so a
  job the worker parks in that instant is resumed on the next tick), every
  job this
  controller paused goes back to ``queued`` with its original id (it runs
  first again, ``jobs.resume_client_paused``), and the gate opens.

The pause is the existing manual pause, unchanged (WP 3.12): the worker
terminates SteamPrefill and parks the job at ``paused``; resume re-runs it,
and every chunk the first attempt stored is served from disk as a local HIT
(ADR-0001), SteamPrefill's own ``successfullyDownloadedDepots.json`` skips the
depots it finished. The cache is the progress store, so no progress is lost.

What it never does
------------------
* It never resumes a manual pause. Only rows tagged ``client`` are resumed;
  a NULL tag (every row paused before schema v34) reads as manual. Pressing
  Pause on a client-paused job turns it into a manual pause
  (``jobs.request_pause``), and a manual pause or cancel pending on a running
  job is never overwritten by this controller.
* It never touches a GC job's run (no internet traffic; a running GC job
  cannot be paused). Queued GC jobs wait behind the gate like everything
  else in the queue.

Fail-safe directions
--------------------
* **Event log off** (``VAULT_EVENT_LOG_PATH`` unset): nothing can be seen,
  so no hold ever starts, and a job left tagged ``client`` from an earlier
  run is resumed rather than parked forever.
* **Runner address unknown** while a prefill runs (no current runner window
  and no valid marker in the ring): the runner's own MISS traffic would look
  like a client's, so no hold starts on that tick and
  ``state().runner_address_unknown`` says why. The check is global, not per
  address (see the limits below).
* **Restart**: the hold lives in memory. On its first tick with work the
  controller adopts every job still tagged ``client`` as a hold that started
  now (``state().restored``; ``last_client_at`` is then the restart, not a
  sighting), so they resume one idle gap after the restart unless a client
  loads.
* **A tick that fails** is logged and leaves the hold as it was; the next
  tick retries.

Known limits (honest, not bugs): an address shared by the runner and a real
client (both behind one NAT) is the runner's, so that client gets no
priority; a client that gets no MISS byte through at all (every request
503) is invisible, in practice it gets a part of the cap (39 Mbit/s
measured); a job queued while no tick was reading (empty queue) can start
and is then paused on the next tick. The unknown-runner guard is global:
when ANY runner address is known (an older window, or a valid marker) but
the running runner's own marker did not reach the log (marker request
failed, a new address before its first marker is read), its MISS counts as
a client's and it pauses itself, then resumes after the idle gap, about
every two minutes until a marker for its address is seen.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from vault_api import jobs, live_tier, runner_identity
from vault_api.config import Settings
from vault_api.db import get_connection

logger = logging.getLogger(__name__)

#: One tick per live-tier cell (``live_tier.CELL_SECONDS``, 5 s): the ring
#: cannot change faster than that, and nginx flushes the event log every 5 s.
TICK_SECONDS = float(live_tier.CELL_SECONDS)

#: How long no client may have loaded before the runner carries on.
#:
#: Why 120 s: a client's last MISS leaves the "now" window ~20 s later
#: (``CURRENT_SECONDS`` 15 s plus the running 5 s cell) and reaches the log up
#: to 5 s late (nginx ``flush=5s``), so ~25 s of "idle" is only sampling
#: delay. A Steam client also stops loading for a while between depots and
#: between games in its download queue (disk allocation, verify), and every
#: resume restarts SteamPrefill (login, manifests, HIT replay of what it
#: already has). 120 s is 24 ticks, about four times the sampling delay and
#: twice the live tier's watcher window (60 s): long enough not to restart
#: the runner in each short client pause, short against a prefill job
#: (minutes to hours). A constant on purpose (D16: fewer env values).
IDLE_GAP_SECONDS = 120

#: The client MISS rate in the "now" window that counts as "a client is
#: loading": 512 KiB/s (~4 Mbit/s). A contended client download measured
#: 39 Mbit/s (~4.9 MB/s), far above it; a small patch of a few MB spread over
#: the 15-20 s window stays below it and does not stop the runner.
MIN_CLIENT_MISS_BYTES_PER_SECOND = 512 * 1024


@dataclass(frozen=True)
class ClientLoad:
    """One client address loading from the internet through the cache."""

    addr: str
    #: Its MISS bytes in the "now" window per second of that window.
    miss_bytes_per_second: float


@dataclass(frozen=True)
class PriorityState:
    """What the controller decided last (``ClientPriority.state``)."""

    #: The event log is configured, so clients can be seen at all.
    enabled: bool = False
    #: The runner is held for clients right now.
    holding: bool = False
    since: datetime | None = None
    #: The last tick that saw a client loading.
    last_client_at: datetime | None = None
    #: The clients of that tick, highest rate first (kept while holding).
    clients: tuple[ClientLoad, ...] = ()
    #: The last tick could not tell the runner from clients (no hold started).
    runner_address_unknown: bool = False
    #: The hold was adopted from jobs tagged ``client`` after a restart and
    #: no client has been seen since (``last_client_at`` = the restart).
    restored: bool = False
    idle_gap_seconds: int = IDLE_GAP_SECONDS

    @property
    def resume_at(self) -> datetime | None:
        """When the hold ends if no client loads again (``None`` unless holding)."""
        if not self.holding or self.last_client_at is None:
            return None
        return self.last_client_at + timedelta(seconds=self.idle_gap_seconds)


class ClientPriority:
    """The controller. One per app; ``tick`` is the whole decision."""

    def __init__(
        self,
        settings: Settings,
        tier: Callable[[], live_tier.LiveTier],
        *,
        idle_gap_seconds: int = IDLE_GAP_SECONDS,
        tick_seconds: float = TICK_SECONDS,
        min_miss_bytes_per_second: float = MIN_CLIENT_MISS_BYTES_PER_SECOND,
    ) -> None:
        self._settings = settings
        #: A callable, not the tier itself: tests swap ``app.state.live_tier``
        #: after ``create_app`` (CLOCK-SEAM-1), and the controller must read
        #: the same tier and clock as the endpoints.
        self._tier = tier
        self._gap = timedelta(seconds=idle_gap_seconds)
        self._tick_seconds = tick_seconds
        self._min_rate = min_miss_bytes_per_second
        self._lock = threading.Lock()
        self._state = PriorityState(
            enabled=bool(settings.event_log_path), idle_gap_seconds=idle_gap_seconds
        )
        #: Whether the first tick with work has adopted client-paused rows.
        self._adopted = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- reading -----------------------------------------------------------

    def holding(self) -> bool:
        """The worker's gate: ``True`` = do not claim a queued job now."""
        with self._lock:
            return self._state.holding

    def state(self) -> PriorityState:
        with self._lock:
            return self._state

    # -- thread ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:  # pragma: no cover - guarded by lifespan
            raise RuntimeError("client priority already started")
        self._thread = threading.Thread(
            target=self._run, name="vault-client-priority", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        conn = get_connection(self._settings.db_path)
        try:
            # First tick one interval after start: the worker's own startup
            # (stale-job recovery, reattach) settles first.
            while not self._stop.wait(self._tick_seconds):
                try:
                    self.tick(conn)
                except Exception:  # noqa: BLE001 - never end the thread
                    logger.exception("Client priority tick failed; retried on the next tick.")
        finally:
            conn.close()

    # -- the decision ------------------------------------------------------

    def tick(self, conn: sqlite3.Connection, now: datetime | None = None) -> PriorityState:
        """One step. ``now`` defaults to the live tier's clock (CLOCK-SEAM-1),
        the clock the ring's cells are judged by. Returns the new state."""
        tier = self._tier()
        moment = now if now is not None else tier.clock()
        path = self._settings.event_log_path

        if not path:
            with self._lock:
                self._state = replace(
                    self._state, enabled=False, holding=False, since=None,
                    clients=(), runner_address_unknown=False, restored=False,
                )
            self._release(conn)
            return self.state()

        if not jobs.has_client_priority_work(conn):
            # Nothing to protect: no log read. An expired hold still ends
            # (time only), so the state never shows a stale hold.
            with self._lock:
                self._adopted = True
                if self._expired(moment):
                    self._state = replace(
                        self._state, holding=False, since=None, clients=(), restored=False
                    )
                return self._state

        with self._lock:
            adopt = not self._adopted
            self._adopted = True
        if adopt and jobs.client_paused_ids(conn):
            with self._lock:
                self._state = replace(
                    self._state,
                    holding=True,
                    since=moment,
                    last_client_at=moment,
                    restored=True,
                )
            logger.info(
                "Client priority: jobs paused for a client before this start "
                "resume after %d s without client downloads.",
                int(self._gap.total_seconds()),
            )

        loads, unknown = self._client_loads(conn, tier, path, moment)
        loading = not unknown and sum(c.miss_bytes_per_second for c in loads) >= self._min_rate

        with self._lock:
            state = replace(self._state, enabled=True, runner_address_unknown=unknown)
            started = released = False
            if loading:
                started = not state.holding
                state = replace(
                    state,
                    holding=True,
                    since=moment if started else state.since,
                    last_client_at=moment,
                    clients=loads,
                    restored=False,
                )
            elif state.holding and self._expired_state(state, moment):
                released = True
                state = replace(
                    state, holding=False, since=None, clients=(), restored=False
                )
            self._state = state
            holding = state.holding

        if started:
            logger.info(
                "Client priority: %s loading from the internet; holding the "
                "prefill runner until %d s after the last client download.",
                ", ".join(c.addr for c in loads),
                int(self._gap.total_seconds()),
            )
        if holding:
            paused = jobs.request_client_pause(conn)
            if paused:
                logger.info(
                    "Client priority: pause requested for prefill job(s) %s.", paused
                )
        else:
            resumed = self._release(conn)
            if released or resumed:
                logger.info(
                    "Client priority: no client download for %d s; the prefill "
                    "runner carries on (resumed job(s) %s).",
                    int(self._gap.total_seconds()),
                    resumed or "none",
                )
        return self.state()

    def _client_loads(
        self,
        conn: sqlite3.Connection,
        tier: live_tier.LiveTier,
        path: str,
        now: datetime,
    ) -> tuple[tuple[ClientLoad, ...], bool]:
        """The clients loading now (highest rate first) and whether the
        runner's address is unknown while a prefill runs."""
        tier.touch(now)
        tier.refresh(path, now)
        windows = runner_identity.windows(conn)
        marks = {
            addr
            for addr, token, seen in tier.runner_marks(now)
            if runner_identity.live_mark_valid(conn, token, addr, seen)
        }

        def is_runner(addr: str, bucket_iso: str) -> bool:
            # The rule of ``routers.traffic._runner_test`` plus the valid
            # markers in the ring (``LiveTier.runner_marks``).
            return addr in marks or any(
                w.addr == addr and w.start <= bucket_iso < w.end for w in windows
            )

        known = bool(marks) or bool(runner_identity.current_addrs(windows, now))
        unknown = not known and bool(jobs.running_prefill_ids(conn))
        view = tier.now_view(now, is_runner)
        loads = sorted(
            (
                ClientLoad(addr=addr, miss_bytes_per_second=entry.miss_bytes / view.seconds)
                for (addr, runner), entry in view.activity.items()
                if not runner and entry.miss_bytes > 0
            ),
            key=lambda c: (-c.miss_bytes_per_second, c.addr),
        )
        return tuple(loads), unknown

    def _expired(self, now: datetime) -> bool:
        return self._expired_state(self._state, now)

    def _expired_state(self, state: PriorityState, now: datetime) -> bool:
        return state.last_client_at is None or now - state.last_client_at >= self._gap

    @staticmethod
    def _release(conn: sqlite3.Connection) -> list[int]:
        """Withdraw a pending client pause and resume every client-paused job."""
        jobs.withdraw_client_pause(conn)
        return jobs.resume_client_paused(conn)
