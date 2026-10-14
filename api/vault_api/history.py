"""Delete history (WP D15-NAMES, roadmap D15, user decision 2026-10-14
morning: "Delete history" in two variants, everything and older than 30
days).

What "history" is -- exactly
----------------------------
History is what the Downloads page shows about the PAST and nothing else:

* **Finished jobs**: ``jobs`` rows whose status is terminal
  (``jobs.TERMINAL_STATUSES``: done, error, cancelled) -- prefill and GC
  jobs alike, with their log excerpts. The Downloads "History" list.
* **Traffic statistics**: every tier of the three D15b tables
  ``client_traffic``, ``client_depot_traffic`` and ``server_traffic``
  (``vault_api/traffic.py``) -- the Downloads header, its graph, and the
  per-client numbers and "loaded recently" on the Clients page.

Deliberately NOT history, never touched here:

* queued, running and paused jobs (``jobs.ACTIVE_STATUSES``) -- work, not
  history; a paused job's partial download is its progress;
* the cache content on disk, ``apps`` (incl. ``last_prefill_at`` and
  ``last_manifest_check``), depot mappings and manifests;
* agent reports and client names -- who the clients are, not what they did;
* ``client_cache_stats``, ``depot_miss_stats`` and ``client_bypass_state``:
  short-lived operational state (about four hours) that the bypass rule and
  the miss trigger decide on. Emptying them would make every agent PC look
  absent from the cache log and could raise a false "bypassing the cache"
  warning; they age out on their own;
* the event-sweep cursor and its counters (diagnostics).

The two variants
----------------
* ``all``: every finished job and every traffic row. ``traffic_since`` is
  reset to ``NULL`` ("nothing recorded"); the next sweep that writes sets it
  again.
* ``older-than-30d``: finished jobs whose finish time (else start, else
  creation -- the same ``jobs.HISTORY_TIME_SQL`` the history filter of
  ``GET /v1/jobs`` uses) is before ``now - 30 days``, and traffic buckets
  that END at or before that cutoff (``bucket_at`` before the cutoff's own
  bucket in that tier). A bucket that straddles the cutoff is kept whole: it
  also holds newer data. ``traffic_since`` moves forward to the start of the
  cutoff's day bucket when it was earlier, so "nothing before it was
  recorded" stays true.

Everything happens in one ``BEGIN IMMEDIATE`` transaction, so a concurrent
event sweep (which folds its batch in its own immediate transaction) either
lands entirely before or entirely after the delete.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from vault_api import jobs, traffic

SCOPE_ALL = "all"
SCOPE_OLDER_THAN_30D = "older-than-30d"
SCOPES = (SCOPE_ALL, SCOPE_OLDER_THAN_30D)

#: The "older than" variant's age.
KEEP_DAYS = 30

TRAFFIC_TABLES = ("client_traffic", "client_depot_traffic", "server_traffic")

_TERMINAL_PLACEHOLDERS = ", ".join("?" for _ in jobs.TERMINAL_STATUSES)


@dataclass(frozen=True)
class HistoryDeleteResult:
    scope: str
    #: UTC cutoff for ``older-than-30d``; ``None`` for ``all``.
    cutoff: str | None
    jobs_deleted: int
    #: Rows deleted per traffic table.
    traffic_rows_deleted: dict[str, int]


def delete_history(
    conn: sqlite3.Connection, scope: str, now: datetime
) -> HistoryDeleteResult:
    """Delete finished jobs and traffic statistics; see the module docstring."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    cutoff_moment = now - timedelta(days=KEEP_DAYS) if scope == SCOPE_OLDER_THAN_30D else None
    cutoff = jobs.to_utc_iso(cutoff_moment) if cutoff_moment is not None else None
    traffic_deleted: dict[str, int] = {}
    with jobs.immediate_transaction(conn):
        if cutoff is None:
            cursor = conn.execute(
                f"DELETE FROM jobs WHERE status IN ({_TERMINAL_PLACEHOLDERS})",
                jobs.TERMINAL_STATUSES,
            )
        else:
            cursor = conn.execute(
                f"""
                DELETE FROM jobs
                WHERE status IN ({_TERMINAL_PLACEHOLDERS})
                  AND {jobs.HISTORY_TIME_SQL} < ?
                """,
                (*jobs.TERMINAL_STATUSES, cutoff),
            )
        jobs_deleted = int(cursor.rowcount or 0)

        for table in TRAFFIC_TABLES:
            if cutoff_moment is None:
                cursor = conn.execute(f"DELETE FROM {table}")
                traffic_deleted[table] = int(cursor.rowcount or 0)
                continue
            removed = 0
            for tier in traffic.TIERS:
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE tier = ? AND bucket_at < ?",
                    (tier, traffic.bucket_iso(cutoff_moment, tier)),
                )
                removed += int(cursor.rowcount or 0)
            traffic_deleted[table] = removed

        if cutoff_moment is None:
            conn.execute("UPDATE event_sweep_state SET traffic_since = NULL")
        else:
            boundary = traffic.bucket_iso(cutoff_moment, traffic.TIER_1D)
            conn.execute(
                """
                UPDATE event_sweep_state SET traffic_since = ?
                WHERE traffic_since IS NOT NULL AND traffic_since < ?
                """,
                (boundary, boundary),
            )
    return HistoryDeleteResult(
        scope=scope,
        cutoff=cutoff,
        jobs_deleted=jobs_deleted,
        traffic_rows_deleted=traffic_deleted,
    )
