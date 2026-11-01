"""Storage balance: where the bytes on disk come from (WP STORAGE-BALANCE-1).

Operator decision 2026-11-01 (Weg A): the product itself explains how the
cache's disk usage splits, instead of a one-off dev measurement. On a cache
that has seen several game updates, a large part of the disk can sit outside
the current builds of the measured games; this module says how large and in
which class.

## The figures (all bytes; nothing here walks the disk)

Inputs are the depot byte totals of the existing cached size walk
(``sizes.SizeCache`` snapshot), ``depot_app_map``, and the stored completeness
rows read through ``cache_completeness.load_index`` (current targets
included). No extra disk walk, no oracle traffic. The two inputs have
different ages: the snapshot is as old as its walk, the present bytes as old
as the last measurement (the last update check, up to one
``VAULT_SCHEDULE_INTERVAL_MINUTES``, or the last prefill of that game).

- ``on_disk``: total of the size walk (``SizeSnapshot.total_bytes``).
- ``planned``: sum of ``expected_bytes`` of the current target manifest over
  every *measured* (app, depot) pair (``CompletenessIndex.measured``: an
  ``ok`` row for the pair's CURRENT target). Each (depot, manifest) counts
  once, so a depot shared by two apps adds its manifest once.
- ``present``: the same sum over ``present_bytes`` (capped at the pair's
  expected bytes, exactly like ``CompletenessIndex.figure``).
- ``unmeasured``: on-disk bytes of mapped depots with no measured pair at
  all (tool/config packages, DLC/content packages, not-owned apps, a target
  the oracle announced but nobody fetched yet ...), with the depot count.
- ``unmapped``: on-disk bytes of depot directories with no ``depot_app_map``
  row (the same set as the summary's ``unmapped_depots``), with the count.
- ``old_builds``: per measured depot, its on-disk bytes minus the present
  bytes of its current manifest(s), floored at 0, summed. These are bytes
  in measured depots that the current build does not use: older builds'
  chunks (what GC would reclaim), chunk files with the wrong size, and
  stored manifest copies -- and also chunks of the CURRENT build that a
  client or prefill downloaded after the last measurement (present bytes stay
  frozen until the next one, up to one check interval). An **estimate**:
  GC's own plan (``gc.plan_gc``) also keeps the oracle's beta-branch chunk
  sets, so it can reclaim less.
- ``unexplained``: ``on_disk - present - old_builds - unmeasured -
  unmapped``. By construction every depot directory lands in exactly one of
  the three depot classes, so this is 0 when the size count and the stored
  rows agree. It turns **negative** when present bytes exceed the depot's
  on-disk bytes: the size count and the last check disagree (files changed
  in between, e.g. a delete or GC), or one depot is measured against two
  current manifests that share chunks. Never hidden: the log line and the
  API show it as it is.

## Warning

``warning`` is true when ``old_builds + |unexplained|`` is above
``WARN_FRACTION`` of ``on_disk`` or above ``WARN_BYTES`` (constants, not
settings). ``warning_reason`` says how much and what to do: garbage
collection reclaims old builds (Auto-GC only cleans a game after its own
prefill, so a cache with many updated games keeps the warning until GC runs).
The update check logs the balance as one INFO line after every run and logs
at WARNING when the flag first turns on (``BalanceLogger``).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping

from vault_api import cache_completeness
from vault_api.config import Settings

logger = logging.getLogger(__name__)

#: Warn when old builds + unexplained exceed this share of ``on_disk`` ...
WARN_FRACTION = 0.15
#: ... or this many bytes. "GB" here is 2**30 bytes, the unit ``zfs list``
#: and both UIs print as "G"/"GB", so the log line compares directly.
GB = 2**30
WARN_BYTES = 50 * GB


#: The action the warning points at.
GC_HINT = (
    "Garbage collection reclaims old builds; Auto-GC only cleans a game "
    "after its own prefill."
)
#: Said when the residual is part of the warning.
UNEXPLAINED_HINT = (
    "Unexplained bytes mean the size count and the last check disagree "
    "(files changed in between, or a depot measured against two builds)."
)


@dataclass(frozen=True)
class StorageBalance:
    on_disk_bytes: int
    planned_bytes: int
    present_bytes: int
    #: Depots with at least one measured pair (each depot once).
    measured_depots: int
    old_builds_bytes: int
    unmeasured_bytes: int
    unmeasured_depots: int
    unmapped_bytes: int
    unmapped_depots: int
    unexplained_bytes: int
    warning: bool
    warning_reason: str | None


def format_gb(value: int) -> str:
    """``GB`` (2**30 bytes) with one decimal, sign kept (``-1.2 GB``); a value
    that rounds to zero prints ``0.0 GB``, never ``-0.0 GB``."""
    text = f"{value / GB:.1f}"
    if text == "-0.0":
        text = "0.0"
    return f"{text} GB"


def _warning(on_disk: int, old_builds: int, unexplained: int) -> tuple[bool, str | None]:
    concern = old_builds + abs(unexplained)
    if on_disk <= 0 or concern <= 0:
        return False, None
    share = concern / on_disk
    if concern <= WARN_BYTES and share <= WARN_FRACTION:
        return False, None
    what = "Old builds and unexplained bytes" if unexplained else "Old builds"
    reason = (
        f"{what} take {format_gb(concern)} ({share * 100:.0f} % of "
        f"{format_gb(on_disk)} on disk), above the "
        f"{WARN_FRACTION * 100:.0f} % / {format_gb(WARN_BYTES)} threshold. "
        f"{GC_HINT}"
    )
    if unexplained:
        reason += f" {UNEXPLAINED_HINT}"
    return True, reason


def compute(
    depot_bytes: Mapping[int, int],
    mapped_depots: Iterable[int],
    index: cache_completeness.CompletenessIndex,
) -> StorageBalance:
    """The balance from a size-walk snapshot, the mapped depot ids and the
    completeness index. Pure: no I/O."""
    disk = {d: max(0, int(b)) for d, b in depot_bytes.items()}
    on_disk = sum(disk.values())
    mapped = set(mapped_depots)

    seen: set[tuple[int, str]] = set()
    planned = 0
    present = 0
    present_by_depot: dict[int, int] = {}
    for pair in sorted(index.targets):
        if not index.measured(pair):
            continue
        row = index.rows[pair]
        target = index.targets[pair]
        key = (row.depotid, str(target))
        if key in seen:
            continue
        seen.add(key)
        expected = max(0, row.expected_bytes or 0)
        here = max(0, min(row.present_bytes or 0, expected))
        planned += expected
        present += here
        present_by_depot[row.depotid] = present_by_depot.get(row.depotid, 0) + here

    old_builds = sum(
        max(0, disk.get(depotid, 0) - here) for depotid, here in present_by_depot.items()
    )
    unmeasured_ids = [d for d in disk if d in mapped and d not in present_by_depot]
    unmapped_ids = [d for d in disk if d not in mapped]
    unmeasured = sum(disk[d] for d in unmeasured_ids)
    unmapped = sum(disk[d] for d in unmapped_ids)
    unexplained = on_disk - present - old_builds - unmeasured - unmapped
    warning, reason = _warning(on_disk, old_builds, unexplained)
    return StorageBalance(
        on_disk_bytes=on_disk,
        planned_bytes=planned,
        present_bytes=present,
        measured_depots=len(present_by_depot),
        old_builds_bytes=old_builds,
        unmeasured_bytes=unmeasured,
        unmeasured_depots=len(unmeasured_ids),
        unmapped_bytes=unmapped,
        unmapped_depots=len(unmapped_ids),
        unexplained_bytes=unexplained,
        warning=warning,
        warning_reason=reason,
    )


def compute_from_db(
    conn: sqlite3.Connection, settings: Settings, depot_bytes: Mapping[int, int]
) -> StorageBalance:
    """Read ``depot_app_map`` and the completeness index, then ``compute``."""
    mapped = [int(r["depotid"]) for r in conn.execute("SELECT DISTINCT depotid FROM depot_app_map")]
    index = cache_completeness.load_index(conn, settings)
    return compute(depot_bytes, mapped, index)


def describe(balance: StorageBalance, sizes_age_seconds: float | None = None) -> str:
    """The INFO line the update check logs after every run; with the size
    snapshot's age when known (its on-disk figures can be older than the
    present bytes)."""
    age = (
        ""
        if sizes_age_seconds is None
        else f", sizes from a walk {int(max(0.0, sizes_age_seconds) // 60)} min old"
    )
    return (
        f"storage balance: on disk {format_gb(balance.on_disk_bytes)}, "
        f"planned {format_gb(balance.planned_bytes)}, "
        f"present {format_gb(balance.present_bytes)}, "
        f"old builds {format_gb(balance.old_builds_bytes)}, "
        f"unmeasured {format_gb(balance.unmeasured_bytes)} "
        f"({balance.unmeasured_depots} depots), "
        f"unmapped {format_gb(balance.unmapped_bytes)} "
        f"({balance.unmapped_depots} depots), "
        f"unexplained {format_gb(balance.unexplained_bytes)}{age}"
    )


class BalanceLogger:
    """Logs one balance per call; WARNING only when the flag first turns on
    (and INFO once when it clears again). Per process: after a restart the
    first warning is logged again."""

    def __init__(self) -> None:
        self._warned = False

    def log(self, balance: StorageBalance, *, sizes_age_seconds: float | None = None) -> None:
        logger.info("%s", describe(balance, sizes_age_seconds))
        if balance.warning and not self._warned:
            logger.warning("storage balance warning: %s", balance.warning_reason)
        elif not balance.warning and self._warned:
            logger.info("storage balance: back below the warning threshold")
        self._warned = balance.warning
