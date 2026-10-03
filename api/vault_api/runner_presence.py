"""vault-runner presence (WP VER-2): who is running, which build, last seen.

Before WP VER-2 the queue-mode runner (``prefill_runner``) left a trace in
the shared database only while it owned a job (``jobs.run_heartbeat_at``).
An idle runner was invisible, so nothing could say whether one was running
at all, let alone which build or which SteamPrefill it carried.

The runner now upserts one ``runner_presence`` row (schema v16) about every
:data:`PRESENCE_INTERVAL_SECONDS`, from its idle poll loop and from the
heartbeat callback while it runs a job. ``GET /v1/about`` reads the freshest
row and calls the runner ``ok`` while that row is younger than
:data:`PRESENCE_STALE_SECONDS`.

**Why 30 s and 90 s.** The runner polls the job table every second
(``VAULT_RUNNER_POLL_SECONDS``); writing presence on every poll would put a
write on the shared SQLite file every second for a fact that changes only
when the runner starts or stops. 30 s keeps that at two writes a minute. The
stale threshold is three intervals: one write can be delayed by up to the
5 s ``busy_timeout`` (``db.get_connection``) and one can fail outright
(logged, retried at the next interval), so a healthy runner that misses two
writes in a row is still ``ok``, and a stopped runner shows as unreachable
within 90 s. The values are module constants, not settings: nothing an
operator tunes depends on them, and an env knob would need compose
forwarding on two services (docs/LEARNINGS.md, "Containers").
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from vault_api.jobs import parse_utc_iso, to_utc_iso

#: How often a running runner refreshes its row. See the module docstring.
PRESENCE_INTERVAL_SECONDS = 30.0
#: How old the freshest row may be before the runner counts as unreachable:
#: three intervals. See the module docstring.
PRESENCE_STALE_SECONDS = 90.0
#: Rows older than this are deleted by the next write. Every runner restart
#: mints a new ``runner_id`` (hostname:pid:random), so without pruning the
#: table would grow by one row per restart forever.
PRUNE_AFTER_SECONDS = 24 * 60 * 60.0


@dataclass(frozen=True)
class PresenceRow:
    """One ``runner_presence`` row as stored (unvalidated text)."""

    runner_id: str
    build_version: str | None
    build_commit: str | None
    steamprefill_version: str | None
    started_at: str
    last_seen: str


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def record_presence(
    conn: sqlite3.Connection,
    *,
    runner_id: str,
    started_at: str,
    build_version: str | None,
    build_commit: str | None,
    steamprefill_version: str | None,
    now: datetime | None = None,
) -> None:
    """Upsert this runner's row and prune rows not seen for a day.

    One transaction, committed here. Raises ``sqlite3.Error`` (including
    "no such table" against a database vault-api has not migrated yet); the
    caller logs it and retries at the next interval.
    """
    moment = _now(now)
    last_seen = to_utc_iso(moment)
    cutoff = to_utc_iso(moment - timedelta(seconds=PRUNE_AFTER_SECONDS))
    try:
        conn.execute(
            """
            INSERT INTO runner_presence
                (runner_id, build_version, build_commit, steamprefill_version,
                 started_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (runner_id) DO UPDATE SET
                build_version = excluded.build_version,
                build_commit = excluded.build_commit,
                steamprefill_version = excluded.steamprefill_version,
                last_seen = excluded.last_seen
            """,
            (
                runner_id,
                build_version,
                build_commit,
                steamprefill_version,
                started_at,
                last_seen,
            ),
        )
        # Cannot hit this runner's own row: it was just stamped ``now``.
        conn.execute("DELETE FROM runner_presence WHERE last_seen < ?", (cutoff,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def fresh_and_latest(
    conn: sqlite3.Connection, now: datetime | None = None
) -> tuple[PresenceRow | None, int, float | None]:
    """``(latest row, fresh row count, latest age in seconds)``.

    ``latest`` is the row with the greatest ``last_seen`` (string order is
    time order in this format). A row whose ``last_seen`` does not parse
    counts as never seen: it is skipped for ``latest`` and never fresh.
    ``age`` is ``None`` when there is no usable row; a negative age (a clock
    step between the two containers) is clamped to 0.
    """
    moment = _now(now)
    rows = conn.execute(
        """
        SELECT runner_id, build_version, build_commit, steamprefill_version,
               started_at, last_seen
        FROM runner_presence
        ORDER BY last_seen DESC
        """
    ).fetchall()
    latest: PresenceRow | None = None
    latest_age: float | None = None
    fresh = 0
    for row in rows:
        seen = parse_utc_iso(row["last_seen"])
        if seen is None or to_utc_iso(seen) != row["last_seen"]:
            continue
        age = max(0.0, (moment - seen).total_seconds())
        if age < PRESENCE_STALE_SECONDS:
            fresh += 1
        if latest is None:
            latest = PresenceRow(
                runner_id=row["runner_id"],
                build_version=row["build_version"],
                build_commit=row["build_commit"],
                steamprefill_version=row["steamprefill_version"],
                started_at=row["started_at"],
                last_seen=row["last_seen"],
            )
            latest_age = age
    return latest, fresh, latest_age


__all__ = [
    "PRESENCE_INTERVAL_SECONDS",
    "PRESENCE_STALE_SECONDS",
    "PRUNE_AFTER_SECONDS",
    "PresenceRow",
    "fresh_and_latest",
    "record_presence",
]
