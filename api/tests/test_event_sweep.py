"""The cache-event sweep (WP 3.11, ADR-0008): parsing, cursor, trigger, rotation.

Every timing decision is driven by an INJECTED aware datetime, so nothing here
waits for real time and nothing depends on the timezone of the machine running
the tests (same discipline as ``test_scheduler.py``).

Log fixtures are synthetic but modelled byte-for-byte on the format
``core/README.md`` pins — tab-separated, ``escape=default``, nine fields,
version-prefixed. No real client addresses or real request paths (LEARNINGS:
"fixtures: synthetic only, modelled on real structure").
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import TEST_API_KEY
from vault_api import event_sweep, jobs as jobs_queue, mapping
from vault_api.config import Settings
from vault_api.db import get_connection, init_db

#: Fixed +02:00, so a machine in UTC and a machine in Berlin agree.
TZ = timezone(timedelta(hours=2))


def moment(hour: int = 12, minute: int = 0, day: int = 9) -> datetime:
    """An aware server-local datetime on 2026-08-``day``."""
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def event_line(
    *,
    version: str = "v1",
    time: str = "2026-08-09T14:03:11+02:00",
    addr: str = "192.168.1.42",
    cache_status: str = "MISS",
    depot: str = "70403",
    uri: str | None = None,
    bytes_sent: str = "999232",
    host: str = "lancache.steamcontent.com",
    http_status: str = "200",
) -> str:
    """One event-log line. Defaults to a served chunk MISS on depot 70403."""
    if uri is None:
        uri = (
            f"/depot/{depot}/chunk/773d10050d99b2544665873ec2125b3bf273e8b2"
            if depot != "-"
            else "/lancache-heartbeat"
        )
    return "\t".join(
        [version, time, addr, cache_status, depot, uri, bytes_sent, host, http_status]
    )


def write_log(path: Path, *lines: str, terminated: bool = True) -> None:
    """(Re)write the log. ``terminated=False`` leaves the last line partial."""
    text = "".join(f"{line}\n" for line in lines)
    if not terminated and lines:
        text = text[:-1]
    path.write_bytes(text.encode("utf-8"))


def append_log(path: Path, *lines: str, terminated: bool = True) -> None:
    text = "".join(f"{line}\n" for line in lines)
    if not terminated and lines:
        text = text[:-1]
    with open(path, "ab") as handle:
        handle.write(text.encode("utf-8"))


def make_settings(
    tmp_path: Path,
    *,
    log_path: str | None = None,
    cooldown: int = 60,
    cap: int = 5,
    stats_keep: int = 48,
    max_bytes: int = 0,
    interval: int = 5,
    bypass_days: int = 3,
) -> Settings:
    """Settings with the event sweep ON and truncation OFF unless asked for."""
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        event_log_path=(
            str(tmp_path / "event.log") if log_path is None else log_path
        ),
        event_sweep_interval_minutes=interval,
        miss_trigger_cooldown_minutes=cooldown,
        miss_trigger_max_per_sweep=cap,
        client_stats_keep=stats_keep,
        event_log_max_bytes=max_bytes,
        bypass_window_days=bypass_days,
    )


@pytest.fixture
def db(tmp_path: Path):
    """An initialized database connection, closed afterwards."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def map_depot(conn: sqlite3.Connection, depotid: int, appid: int) -> None:
    mapping.upsert_mapping(conn, depotid=depotid, appid=appid, name=None)


def queued_appids(conn: sqlite3.Connection) -> list[int]:
    return [
        int(row["appid"])
        for row in conn.execute(
            "SELECT appid FROM jobs WHERE type = 'prefill' ORDER BY id"
        )
    ]


def stats_for(conn: sqlite3.Connection, addr: str) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(requests), 0)     AS requests,
               COALESCE(SUM(hits), 0)         AS hits,
               COALESCE(SUM(misses), 0)       AS misses,
               COALESCE(SUM(bypasses), 0)     AS bypasses,
               COALESCE(SUM(errors), 0)       AS errors,
               COALESCE(SUM(bytes_served), 0) AS bytes_served
        FROM client_cache_stats WHERE client_addr = ?
        """,
        (addr,),
    ).fetchone()
    return {key: int(row[key]) for key in row.keys()}


# ===========================================================================
# Line parsing — the format contract from core/README.md
# ===========================================================================


def test_a_well_formed_v1_line_parses_every_field() -> None:
    line, reason = event_sweep.parse_line(event_line())

    assert reason == ""
    assert line is not None
    assert line.addr == "192.168.1.42"
    assert line.cache_status == "MISS"
    assert line.depotid == 70403
    assert line.bytes_sent == 999232
    assert line.http_status == 200
    assert line.host == "lancache.steamcontent.com"
    assert line.served is True
    assert line.is_chunk is True
    # $time_iso8601 carries an offset; storage is always UTC.
    assert line.time_utc == "2026-08-09T12:03:11Z"


@pytest.mark.parametrize("field_count", [8, 10])
def test_a_line_without_exactly_nine_fields_is_skipped(field_count: int) -> None:
    fields = event_line().split("\t")
    raw = "\t".join(fields[:8] if field_count == 8 else fields + ["extra"])

    line, reason = event_sweep.parse_line(raw)

    assert line is None
    assert reason == "field-count"


def test_an_unknown_format_version_is_skipped_with_its_own_reason() -> None:
    """The whole point of field 1: a v2 layout must never be read as v1."""
    line, reason = event_sweep.parse_line(event_line(version="v2"))

    assert line is None
    # Not "field-count": a v2 line can have nine fields in a different ORDER,
    # which is exactly the case that would otherwise be silently misparsed into
    # wrong depot ids and wrong byte counts.
    assert reason == "unknown-version"


def test_an_unknown_version_is_counted_and_warned_about_not_misparsed(
    db: sqlite3.Connection, tmp_path: Path, caplog
) -> None:
    settings = make_settings(tmp_path)
    write_log(
        Path(settings.event_log_path),
        event_line(),
        event_line(version="v2", depot="999"),
    )

    with caplog.at_level(logging.WARNING):
        outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.lines == 1
    assert outcome.skipped_lines == 1
    assert outcome.skip_reasons == {"unknown-version": 1}
    assert "v1" in caplog.text
    # The v2 line's depot must not have reached any table.
    assert db.execute(
        "SELECT COUNT(*) FROM depot_miss_stats WHERE depotid = 999"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "depot",
    [
        "70a03",  # not a number
        "٧٠٤٠٣",  # Arabic-Indic digits: int() would accept these
        "７０４０３",  # fullwidth digits: str.isdigit() is True, isascii() False
        "1_0",  # int("1_0") == 10
        "+70403",
        " 70403",
        "70403 ",
        "99999999999999999999",  # longer than any real (uint32) depot id
        "",
    ],
)
def test_a_depot_id_that_is_not_plain_ascii_digits_is_refused(depot: str) -> None:
    """docs/LEARNINGS.md: this value feeds SQL and an app-id lookup."""
    line, reason = event_sweep.parse_line(event_line(depot=depot))

    assert line is None
    assert reason == "bad-depot"


def test_the_depot_placeholder_parses_as_no_depot() -> None:
    line, reason = event_sweep.parse_line(event_line(depot="-", uri="/health"))

    assert reason == ""
    assert line is not None and line.depotid is None


@pytest.mark.parametrize("value", ["20", "2000", "abc", "２００", ""])
def test_an_http_status_that_is_not_three_ascii_digits_is_refused(value: str) -> None:
    line, reason = event_sweep.parse_line(event_line(http_status=value))

    assert line is None
    assert reason == "bad-http-status"


@pytest.mark.parametrize("value", ["-1", "1_0", "12x", "٧"])
def test_a_bytes_sent_field_that_is_not_ascii_digits_is_refused(value: str) -> None:
    line, reason = event_sweep.parse_line(event_line(bytes_sent=value))

    assert line is None
    assert reason == "bad-bytes"


@pytest.mark.parametrize(
    "addr",
    ["", "192.168.1.42 ", "a" * 65, "192.168.1.42\x07", "192 168"],
)
def test_a_malformed_client_address_is_refused(addr: str) -> None:
    line, reason = event_sweep.parse_line(event_line(addr=addr))

    assert line is None
    assert reason == "bad-address"


def test_an_unknown_cache_status_is_refused() -> None:
    line, reason = event_sweep.parse_line(event_line(cache_status="STALE"))

    assert line is None
    assert reason == "bad-cache-status"


def test_a_huge_line_is_skipped_rather_than_processed() -> None:
    line, reason = event_sweep.parse_line(event_line(uri="/depot/1/chunk/" + "a" * 9000))

    assert line is None
    assert reason == "too-long"


def test_an_escaped_control_sequence_stays_one_line_and_parses() -> None:
    """core/README.md: escape=default renders %09/%0A as literal \\x09/\\x0A.

    So a hostile path cannot inject a real tab (a tenth field) or a real
    newline (a forged second line) — the text arrives as printable ASCII.
    """
    raw = event_line(uri="/depot/70403/chunk/\\x09\\x22\\x0A")

    line, reason = event_sweep.parse_line(raw)

    assert reason == ""
    assert line is not None and line.depotid == 70403


def test_a_non_2xx_response_is_not_served_traffic() -> None:
    """Field 9 exists exactly so a 403/404/502 is not counted as a hit/miss."""
    for status in ("403", "404", "500", "502", "301"):
        line, _ = event_sweep.parse_line(event_line(http_status=status))
        assert line is not None and line.served is False

    for status in ("200", "206", "299"):
        line, _ = event_sweep.parse_line(event_line(http_status=status))
        assert line is not None and line.served is True


def test_an_unparseable_timestamp_does_not_discard_the_line() -> None:
    line, reason = event_sweep.parse_line(event_line(time="not-a-time"))

    assert reason == ""
    assert line is not None and line.time_utc is None


# ===========================================================================
# Statistics
# ===========================================================================


def test_statistics_count_hits_misses_bypasses_and_errors_separately(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    write_log(
        Path(settings.event_log_path),
        event_line(cache_status="HIT", bytes_sent="100"),
        event_line(cache_status="HIT", bytes_sent="200"),
        event_line(cache_status="MISS", bytes_sent="300"),
        event_line(cache_status="BYPASS", bytes_sent="400"),
        # Not served: counted as a request and an error, nothing else.
        event_line(cache_status="MISS", bytes_sent="9999", http_status="502"),
    )

    event_sweep.sweep_once(db, settings, moment())

    totals = stats_for(db, "192.168.1.42")
    assert totals["requests"] == 5
    assert totals["hits"] == 2
    assert totals["misses"] == 1
    assert totals["bypasses"] == 1
    assert totals["errors"] == 1
    # The 502's 9999 bytes must NOT be in bytes_served.
    assert totals["bytes_served"] == 100 + 200 + 300 + 400
    # requests == hits + misses + bypasses + errors, by construction.
    assert totals["requests"] == sum(
        totals[key] for key in ("hits", "misses", "bypasses", "errors")
    )


def test_statistics_are_kept_per_client_address(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    write_log(
        Path(settings.event_log_path),
        event_line(addr="192.168.1.10", cache_status="HIT"),
        event_line(addr="192.168.1.11", cache_status="HIT"),
        event_line(addr="192.168.1.11", cache_status="HIT"),
    )

    event_sweep.sweep_once(db, settings, moment())

    assert stats_for(db, "192.168.1.10")["hits"] == 1
    assert stats_for(db, "192.168.1.11")["hits"] == 2


def test_client_statistics_retention_keeps_only_the_newest_windows(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0, stats_keep=2)
    log = Path(settings.event_log_path)

    for index in range(4):
        append_log(log, event_line(cache_status="HIT"))
        event_sweep.sweep_once(db, settings, moment(minute=index * 10))

    rows = db.execute(
        "SELECT COUNT(*) FROM client_cache_stats WHERE client_addr = '192.168.1.42'"
    ).fetchone()[0]
    assert rows == 2, "retention must bound the per-address window history"


def test_two_sweeps_in_the_same_second_merge_instead_of_colliding(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """The ON CONFLICT upsert is what makes the (addr, window_at) PK safe."""
    settings = make_settings(tmp_path, cooldown=0)
    log = Path(settings.event_log_path)

    write_log(log, event_line(cache_status="HIT"))
    event_sweep.sweep_once(db, settings, moment())
    append_log(log, event_line(cache_status="HIT"))
    event_sweep.sweep_once(db, settings, moment())  # identical timestamp

    assert stats_for(db, "192.168.1.42")["hits"] == 2


# ===========================================================================
# The cursor contract
# ===========================================================================


def test_a_partial_line_at_eof_is_not_consumed(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """nginx buffers (flush=5s), so a read at EOF routinely lands mid-line."""
    settings = make_settings(tmp_path, cooldown=0)
    log = Path(settings.event_log_path)
    complete = event_line(cache_status="HIT")
    write_log(log, complete, event_line(cache_status="HIT"), terminated=False)

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.lines == 1, "only the newline-terminated line may be consumed"
    assert outcome.cursor == len(complete.encode()) + 1


def test_the_partial_line_is_read_whole_once_its_newline_arrives(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    log = Path(settings.event_log_path)
    first = event_line(cache_status="HIT")
    second = event_line(cache_status="MISS", depot="-", uri="/x")

    # Write the second line WITHOUT its newline, sweep, then finish it.
    write_log(log, first, second, terminated=False)
    event_sweep.sweep_once(db, settings, moment())
    with open(log, "ab") as handle:
        handle.write(b"\n")
    outcome = event_sweep.sweep_once(db, settings, moment(minute=5))

    assert outcome.lines == 1
    # Exactly once in total — not zero (lost) and not twice (duplicated).
    totals = stats_for(db, "192.168.1.42")
    assert totals["requests"] == 2
    assert totals["hits"] == 1
    assert totals["misses"] == 1


def test_a_genuine_partial_tail_is_not_treated_as_an_oversized_line(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    """Direction 1 of the S1 distinction: a short unterminated tail WAITS.

    MUTATION TARGET: drop the ``truncated_batch`` condition (skip whenever
    there is no newline) and this test dies — the tail would be discarded
    instead of being re-read whole once nginx flushes its newline.
    """
    settings = make_settings(tmp_path, cooldown=0)
    monkeypatch.setattr(event_sweep, "MAX_BATCH_BYTES", 4096)
    log = Path(settings.event_log_path)
    write_log(log, event_line(cache_status="HIT"), terminated=False)

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.lines == 0
    assert outcome.cursor == 0, "nothing consumed"
    assert outcome.oversized_skipped_bytes == 0, "NOT an oversized line"
    assert outcome.oversized_stalled is False
    assert event_sweep.read_state(db).oversized_skips_total == 0

    # …and once the newline arrives the line is read exactly once.
    with open(log, "ab") as handle:
        handle.write(b"\n")
    second = event_sweep.sweep_once(db, settings, moment(minute=5))
    assert second.lines == 1
    assert stats_for(db, "192.168.1.42")["hits"] == 1


def test_an_oversized_line_is_skipped_once_and_the_sweep_continues(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch, caplog
) -> None:
    """Direction 2: a newline-free region longer than a batch must not stall.

    MUTATION TARGET: revert the distinction (treat a full newline-free batch as
    a partial tail) and this test dies — the sweep re-reads the same bytes
    forever and the following lines are never seen.
    """
    settings = make_settings(tmp_path, cooldown=0)
    monkeypatch.setattr(event_sweep, "MAX_BATCH_BYTES", 1024)
    log = Path(settings.event_log_path)
    # A 4 KiB newline-free blob (4x the batch), then two normal lines.
    with open(log, "wb") as handle:
        handle.write(b"X" * 4096 + b"\n")
        handle.write((event_line(cache_status="HIT") + "\n").encode())
        handle.write((event_line(cache_status="HIT") + "\n").encode())

    with caplog.at_level(logging.WARNING):
        first = event_sweep.sweep_once(db, settings, moment())

    assert first.oversized_skipped_bytes == 4097, "the blob plus its newline"
    assert first.oversized_stalled is False
    assert first.cursor == 4097
    assert "SKIPPED" in caplog.text
    # The counter is persisted, so an operator sees it without reading logs.
    assert event_sweep.read_state(db).oversized_skips_total == 1
    assert event_sweep.read_state(db).last_oversized_at is not None

    # The following lines are processed on the next sweep — progress resumed.
    second = event_sweep.sweep_once(db, settings, moment(minute=5))
    assert second.lines == 2
    assert second.oversized_skipped_bytes == 0, "skipped exactly once"
    assert stats_for(db, "192.168.1.42")["hits"] == 2
    assert event_sweep.read_state(db).oversized_skips_total == 1


def test_an_oversized_region_running_to_eof_stalls_LOUDLY(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch, caplog
) -> None:
    """The residual case: no newline anywhere, so the cursor cannot advance.

    It must be a WARNING about being stalled, never the old INFO line that
    claimed "a backlog remains and is consumed by the following sweeps".
    """
    settings = make_settings(tmp_path, cooldown=0)
    monkeypatch.setattr(event_sweep, "MAX_BATCH_BYTES", 1024)
    log = Path(settings.event_log_path)
    log.write_bytes(b"X" * 4096)  # no newline at all

    with caplog.at_level(logging.INFO):
        outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.oversized_stalled is True
    assert outcome.oversized_skipped_bytes == 0
    assert outcome.cursor == 0, "never consume an unterminated line"
    assert "STALLED" in caplog.text
    assert "backlog remains" not in caplog.text, "the misleading INFO is gone"

    # It resolves by itself the moment the region is terminated.
    with open(log, "ab") as handle:
        handle.write(b"\n" + (event_line(cache_status="HIT") + "\n").encode())
    recovered = event_sweep.sweep_once(db, settings, moment(minute=5))

    assert recovered.oversized_skipped_bytes == 4097
    assert event_sweep.sweep_once(db, settings, moment(minute=10)).lines == 1


def test_a_file_that_shrank_below_the_cursor_is_treated_as_rotated(
    db: sqlite3.Connection, tmp_path: Path, caplog
) -> None:
    """Somebody else rotated it (operator, logrotate copytruncate, redeploy)."""
    settings = make_settings(tmp_path, cooldown=0)
    log = Path(settings.event_log_path)
    write_log(log, *[event_line(cache_status="HIT") for _ in range(5)])
    first = event_sweep.sweep_once(db, settings, moment())
    assert first.cursor > 0

    # Externally truncated, then one fresh line written.
    write_log(log, event_line(cache_status="MISS", depot="-", uri="/x"))
    with caplog.at_level(logging.WARNING):
        outcome = event_sweep.sweep_once(db, settings, moment(minute=5))

    assert outcome.rotated is True
    assert outcome.lines == 1, "the fresh line must be read, not skipped over"
    assert "shrank" in caplog.text


def test_a_missing_log_leaves_the_cursor_untouched(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    log = Path(settings.event_log_path)
    write_log(log, event_line(cache_status="HIT"))
    swept = event_sweep.sweep_once(db, settings, moment())
    os.remove(log)

    outcome = event_sweep.sweep_once(db, settings, moment(minute=5))

    assert outcome.swept is False
    assert outcome.skipped_reason == "log-missing"
    assert event_sweep.read_state(db).cursor_offset == swept.cursor


def test_an_unreadable_log_does_not_move_the_cursor(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    write_log(Path(settings.event_log_path), event_line(cache_status="HIT"))

    def boom(*args, **kwargs):
        raise OSError("device on fire")

    monkeypatch.setattr(event_sweep.os.path, "getsize", boom)
    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.swept is False
    assert outcome.skipped_reason == "read-error"
    assert event_sweep.read_state(db).cursor_offset == 0


def test_a_crash_before_the_commit_does_not_double_count_statistics(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    """The idempotence pin: statistics and the cursor are ONE transaction.

    Kill the sweep between reading the batch and committing it; the whole batch
    is re-read next time, and the counters must reflect it exactly once.
    """
    settings = make_settings(tmp_path, cooldown=0)
    write_log(
        Path(settings.event_log_path),
        *[event_line(cache_status="HIT") for _ in range(3)],
    )

    real_commit = event_sweep.commit_batch
    monkeypatch.setattr(
        event_sweep,
        "commit_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed mid-batch")),
    )
    with pytest.raises(RuntimeError):
        event_sweep.sweep_once(db, settings, moment())

    # Nothing was committed: no counters, and the cursor never moved.
    assert stats_for(db, "192.168.1.42")["hits"] == 0
    assert event_sweep.read_state(db).cursor_offset == 0

    monkeypatch.setattr(event_sweep, "commit_batch", real_commit)
    outcome = event_sweep.sweep_once(db, settings, moment(minute=5))

    assert outcome.lines == 3
    assert stats_for(db, "192.168.1.42")["hits"] == 3, "exactly once, not twice"


def test_a_failure_inside_the_commit_rolls_the_statistics_back(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    """The stronger atomicity pin: the failure happens AFTER the counters are
    written, inside the same transaction as the cursor.

    MUTATION TARGET: commit the per-address rows in their own transactions
    instead of one shared ``BEGIN IMMEDIATE`` and this test dies — the counters
    survive the crash while the cursor does not, and the re-read doubles them.
    """
    settings = make_settings(tmp_path, cooldown=0)
    write_log(
        Path(settings.event_log_path),
        *[event_line(cache_status="HIT") for _ in range(3)],
    )

    real_prune = event_sweep._prune_depot_misses
    monkeypatch.setattr(
        event_sweep,
        "_prune_depot_misses",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed inside commit")),
    )
    with pytest.raises(RuntimeError):
        event_sweep.sweep_once(db, settings, moment())

    assert stats_for(db, "192.168.1.42")["hits"] == 0, "rolled back with the cursor"
    assert event_sweep.read_state(db).cursor_offset == 0

    monkeypatch.setattr(event_sweep, "_prune_depot_misses", real_prune)
    event_sweep.sweep_once(db, settings, moment(minute=5))

    assert stats_for(db, "192.168.1.42")["hits"] == 3, "exactly once, not twice"


def test_a_re_read_batch_does_not_enqueue_a_second_job(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    """Crash-recovery, trigger side: dedupe + cooldown absorb the repeat."""
    settings = make_settings(tmp_path)
    map_depot(db, 70403, 440)
    write_log(Path(settings.event_log_path), event_line())

    real_commit = event_sweep.commit_batch
    monkeypatch.setattr(
        event_sweep,
        "commit_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed after enqueue")),
    )
    with pytest.raises(RuntimeError):
        event_sweep.sweep_once(db, settings, moment())

    assert queued_appids(db) == [440], "the enqueue happened before the crash"

    monkeypatch.setattr(event_sweep, "commit_batch", real_commit)
    outcome = event_sweep.sweep_once(db, settings, moment(minute=1))

    assert queued_appids(db) == [440], "no second job for the same app"
    assert outcome.enqueued == ()


def test_a_backlog_larger_than_one_batch_is_consumed_across_sweeps(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    monkeypatch.setattr(event_sweep, "MAX_BATCH_BYTES", 400)
    write_log(
        Path(settings.event_log_path),
        *[event_line(cache_status="HIT") for _ in range(6)],
    )

    first = event_sweep.sweep_once(db, settings, moment())
    second = event_sweep.sweep_once(db, settings, moment(minute=5))
    third = event_sweep.sweep_once(db, settings, moment(minute=10))

    assert first.lines + second.lines + third.lines == 6
    assert stats_for(db, "192.168.1.42")["hits"] == 6


# ===========================================================================
# The miss trigger (ADR-0001 hybrid, ADR-0008 rules)
# ===========================================================================


def test_a_chunk_miss_on_a_mapped_uncached_app_enqueues_a_prefill(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    map_depot(db, 70403, 440)
    write_log(Path(settings.event_log_path), event_line())

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == (440,)
    assert queued_appids(db) == [440]
    job = db.execute("SELECT * FROM jobs WHERE appid = 440").fetchone()
    assert job["type"] == "prefill"
    assert job["status"] == "queued"


def test_the_cooldown_suppresses_a_second_trigger_for_the_same_app(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """MUTATION TARGET: remove the cooldown guard and this test dies."""
    settings = make_settings(tmp_path, cooldown=60)
    map_depot(db, 70403, 440)
    log = Path(settings.event_log_path)

    write_log(log, event_line())
    first = event_sweep.sweep_once(db, settings, moment())
    # Let the first job finish, so the queue's own dedupe can NOT be what
    # suppresses the second trigger — only the cooldown can.
    db.execute("UPDATE jobs SET status = 'done' WHERE appid = 440")
    db.commit()

    append_log(log, event_line())
    second = event_sweep.sweep_once(db, settings, moment(minute=30))

    assert first.enqueued == (440,)
    assert second.enqueued == ()
    assert second.skipped_cooldown == (440,)
    assert queued_appids(db) == [440], "still exactly one job"


def test_the_trigger_fires_again_once_the_cooldown_has_expired(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=60)
    map_depot(db, 70403, 440)
    log = Path(settings.event_log_path)

    write_log(log, event_line())
    event_sweep.sweep_once(db, settings, moment(hour=12))
    db.execute("UPDATE jobs SET status = 'done' WHERE appid = 440")
    db.commit()

    append_log(log, event_line())
    later = event_sweep.sweep_once(db, settings, moment(hour=14))

    assert later.enqueued == (440,), "a cooldown is a delay, not a permanent ban"


def test_a_zero_cooldown_disables_the_trigger_entirely(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """0 means OFF, deliberately — never "no cooldown"."""
    settings = make_settings(tmp_path, cooldown=0)
    map_depot(db, 70403, 440)
    write_log(Path(settings.event_log_path), event_line())

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert settings.miss_trigger_enabled is False
    assert outcome.enqueued == ()
    assert queued_appids(db) == []
    # Statistics still work — that is the point of having an off switch here.
    assert stats_for(db, "192.168.1.42")["misses"] == 1


def test_the_per_sweep_cap_drops_further_candidates_and_says_so(
    db: sqlite3.Connection, tmp_path: Path, caplog
) -> None:
    """MUTATION TARGET: remove the cap and this test dies."""
    settings = make_settings(tmp_path, cap=2)
    depots = [70401, 70402, 70403, 70404, 70405]
    for index, depotid in enumerate(depots):
        map_depot(db, depotid, 440 + index)
    write_log(
        Path(settings.event_log_path),
        *[event_line(depot=str(depotid)) for depotid in depots],
    )

    with caplog.at_level(logging.WARNING):
        outcome = event_sweep.sweep_once(db, settings, moment())

    assert len(outcome.enqueued) == 2
    assert outcome.dropped_by_cap == (442, 443, 444)
    assert len(queued_appids(db)) == 2
    # LEARNINGS: no silent caps.
    assert "cap" in caplog.text and "442" in caplog.text


def test_dropped_candidates_are_reconsidered_on_the_next_sweep(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cap=1)
    map_depot(db, 70401, 440)
    map_depot(db, 70402, 441)
    log = Path(settings.event_log_path)
    write_log(log, event_line(depot="70401"), event_line(depot="70402"))
    first = event_sweep.sweep_once(db, settings, moment())

    append_log(log, event_line(depot="70402"))
    second = event_sweep.sweep_once(db, settings, moment(minute=5))

    assert first.enqueued == (440,)
    assert first.dropped_by_cap == (441,)
    assert second.enqueued == (441,), "the cap delays, it does not discard"


def test_an_unmapped_depot_miss_is_counted_but_never_triggers(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """MUTATION TARGET: trigger on unmapped depots and this test dies.

    ADR-0008: "no mapping = no honest target" — the readiness thinking of
    ADR-0007 applied to job creation.
    """
    settings = make_settings(tmp_path)
    write_log(Path(settings.event_log_path), event_line(depot="99001"))

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == ()
    assert queued_appids(db) == []
    row = db.execute(
        "SELECT miss_count, mapped FROM depot_miss_stats WHERE depotid = 99001"
    ).fetchone()
    assert row is not None, "the miss must still be COUNTED"
    assert row["miss_count"] == 1
    assert row["mapped"] == 0


def test_a_depot_shared_by_several_apps_never_triggers(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """One chunk miss cannot say WHICH of several apps is being downloaded."""
    settings = make_settings(tmp_path)
    map_depot(db, 228990, 440)
    map_depot(db, 228990, 730)  # a redistributables-style shared depot
    write_log(Path(settings.event_log_path), event_line(depot="228990"))

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == ()
    assert queued_appids(db) == [], "must not fan one miss out into N jobs"
    assert db.execute(
        "SELECT mapped FROM depot_miss_stats WHERE depotid = 228990"
    ).fetchone()["mapped"] == 1


def test_a_cached_and_current_app_is_not_triggered(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=60)
    map_depot(db, 70403, 440)
    db.execute(
        "UPDATE apps SET status = 'done', needs_force = 0, last_manifest_check = ? "
        "WHERE appid = 440",
        (jobs_queue.to_utc_iso(moment()),),
    )
    db.commit()
    write_log(Path(settings.event_log_path), event_line())

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == ()
    assert outcome.skipped_current == (440,)


@pytest.mark.parametrize("state", ["needs-force", "stale-check", "not-done"])
def test_an_app_that_is_not_cached_and_current_still_triggers(
    db: sqlite3.Connection, tmp_path: Path, state: str
) -> None:
    """Each of the three conditions is load-bearing on its own."""
    settings = make_settings(tmp_path, cooldown=60)
    map_depot(db, 70403, 440)
    fresh = jobs_queue.to_utc_iso(moment())
    stale = jobs_queue.to_utc_iso(moment(hour=6))
    values = {
        "needs-force": ("done", 1, fresh),
        "stale-check": ("done", 0, stale),
        "not-done": ("error", 0, fresh),
    }[state]
    db.execute(
        "UPDATE apps SET status = ?, needs_force = ?, last_manifest_check = ? "
        "WHERE appid = 440",
        values,
    )
    db.commit()
    write_log(Path(settings.event_log_path), event_line())

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == (440,)


@pytest.mark.parametrize("status", ["queued", "running", "paused"])
def test_an_app_with_an_active_job_is_not_triggered_again(
    db: sqlite3.Connection, tmp_path: Path, status: str
) -> None:
    """ACTIVE_STATUSES from WP 3.12 — including `paused`, deliberately."""
    settings = make_settings(tmp_path)
    map_depot(db, 70403, 440)
    db.execute(
        "INSERT INTO jobs (appid, type, status, created_at) VALUES (440, 'prefill', ?, ?)",
        (status, jobs_queue.utcnow_iso()),
    )
    db.commit()
    write_log(Path(settings.event_log_path), event_line())

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == ()
    assert outcome.skipped_active == (440,)
    assert len(queued_appids(db)) == 1


def test_an_app_with_an_active_job_does_not_consume_a_cap_slot(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """MUTATION TARGET: remove the active-job guard and this test dies.

    ``jobs.enqueue_prefill`` dedupes internally, so removing the guard would
    still never create a second job — which is why the job-count test alone
    cannot prove the guard does anything. Its *unique* effect is this: an app
    that already has a job must not burn one of the sweep's scarce enqueue
    slots (nor start a cooldown), because that slot belongs to an app nothing
    is filling yet.
    """
    settings = make_settings(tmp_path, cap=1)
    map_depot(db, 70401, 440)
    map_depot(db, 70402, 441)
    db.execute(
        "INSERT INTO jobs (appid, type, status, created_at) "
        "VALUES (440, 'prefill', 'running', ?)",
        (jobs_queue.utcnow_iso(),),
    )
    db.commit()
    # 440 (already running) is seen FIRST, so with no guard it takes the slot.
    write_log(
        Path(settings.event_log_path),
        event_line(depot="70401"),
        event_line(depot="70402"),
    )

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.skipped_active == (440,)
    assert outcome.enqueued == (441,), "the slot must go to the app with no job"
    assert outcome.dropped_by_cap == ()
    # And no cooldown was started for the app that was merely skipped.
    assert db.execute(
        "SELECT COUNT(*) FROM miss_trigger_state WHERE appid = 440"
    ).fetchone()[0] == 0


def test_a_manifest_miss_does_not_trigger(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """ADR-0001 finding 5: manifest URLs never dedupe, so they always MISS."""
    settings = make_settings(tmp_path)
    map_depot(db, 70403, 440)
    write_log(
        Path(settings.event_log_path),
        event_line(uri="/depot/70403/manifest/1234567890/5/abcdef"),
    )

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == ()
    # Still counted, in both the client stats and the depot stats.
    assert stats_for(db, "192.168.1.42")["misses"] == 1
    assert db.execute(
        "SELECT miss_count FROM depot_miss_stats WHERE depotid = 70403"
    ).fetchone()["miss_count"] == 1


@pytest.mark.parametrize("cache_status", ["HIT", "BYPASS"])
def test_only_a_miss_can_trigger(
    db: sqlite3.Connection, tmp_path: Path, cache_status: str
) -> None:
    settings = make_settings(tmp_path)
    map_depot(db, 70403, 440)
    write_log(Path(settings.event_log_path), event_line(cache_status=cache_status))

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == ()
    assert queued_appids(db) == []


def test_a_miss_that_did_not_succeed_never_triggers(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """A 502 MISS fetched nothing; it is no evidence the app is uncached."""
    settings = make_settings(tmp_path)
    map_depot(db, 70403, 440)
    write_log(Path(settings.event_log_path), event_line(http_status="502"))

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == ()
    assert queued_appids(db) == []
    assert stats_for(db, "192.168.1.42")["errors"] == 1


def test_many_misses_for_one_app_in_one_batch_enqueue_exactly_one_job(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """A real download is tens of thousands of chunk requests."""
    settings = make_settings(tmp_path)
    map_depot(db, 70403, 440)
    write_log(Path(settings.event_log_path), *[event_line() for _ in range(500)])

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.enqueued == (440,)
    assert queued_appids(db) == [440]
    assert stats_for(db, "192.168.1.42")["misses"] == 500


# ===========================================================================
# Rotation / truncation
# ===========================================================================


def test_the_log_is_truncated_once_fully_swept_and_over_the_limit(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0, max_bytes=200)
    log = Path(settings.event_log_path)
    write_log(log, *[event_line(cache_status="HIT") for _ in range(4)])
    assert log.stat().st_size >= 200

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.truncated is True
    assert log.stat().st_size == 0
    assert outcome.cursor == 0
    state = event_sweep.read_state(db)
    assert state.cursor_offset == 0
    assert state.last_rotated_at is not None


def test_a_log_below_the_limit_is_left_alone(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0, max_bytes=10_000_000)
    log = Path(settings.event_log_path)
    write_log(log, event_line(cache_status="HIT"))

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.truncated is False
    assert log.stat().st_size > 0


def test_truncation_is_refused_when_the_file_grew_after_the_read(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Truncating a file with unswept bytes in it would DESTROY those lines."""
    settings = make_settings(tmp_path, cooldown=0, max_bytes=100)
    log = Path(settings.event_log_path)
    write_log(log, *[event_line(cache_status="HIT") for _ in range(3)])
    state_before = event_sweep.read_state(db)
    batch = event_sweep.read_batch(str(log), state_before.cursor_offset)
    # nginx appends between our read and the truncate decision.
    append_log(log, event_line(cache_status="HIT"))

    result = event_sweep.maybe_truncate(
        db, settings, batch.new_cursor, jobs_queue.to_utc_iso(moment())
    )

    assert result.truncated is False
    assert result.reason == event_sweep.TRUNCATE_INCOMPLETE
    assert log.stat().st_size > 0


def test_truncation_can_be_disabled_entirely(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0, max_bytes=0)
    log = Path(settings.event_log_path)
    write_log(log, *[event_line(cache_status="HIT") for _ in range(4)])

    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.truncated is False
    assert log.stat().st_size > 0


def test_a_denied_truncation_keeps_sweeping_correctly_and_is_recorded(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch, caplog
) -> None:
    """The shipped-container case: vault-api may READ the log, not write it.

    vault-api runs as uid 101 while /vault/logs and the nginx-created event.log
    belong to vault-core's nginx user, so os.truncate raises EPERM. Sweeping
    must be completely unaffected — correctness is cursor-based — and the
    condition must be VISIBLE rather than silently skipped.
    """
    settings = make_settings(tmp_path, cooldown=0, max_bytes=200)
    log = Path(settings.event_log_path)
    write_log(log, *[event_line(cache_status="HIT") for _ in range(4)])

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(event_sweep.os, "truncate", denied)
    with caplog.at_level(logging.WARNING):
        outcome = event_sweep.sweep_once(db, settings, moment())

    # The sweep itself succeeded completely.
    assert outcome.swept is True
    assert outcome.lines == 4
    assert stats_for(db, "192.168.1.42")["hits"] == 4
    assert outcome.truncated is False
    assert outcome.truncate_denied is True
    # The cursor advanced past everything read — nothing is lost or re-read.
    assert outcome.cursor == log.stat().st_size
    assert event_sweep.read_state(db).cursor_offset == outcome.cursor
    # And it is visible, in the log AND as a persisted counter.
    assert "NOT PERMITTED" in caplog.text
    assert "chown" in caplog.text
    state = event_sweep.read_state(db)
    assert state.truncate_denied_count == 1
    assert state.last_truncate_denied_at is not None


def test_repeated_denials_keep_counting_and_never_break_the_sweep(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, cooldown=0, max_bytes=200)
    log = Path(settings.event_log_path)

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(event_sweep.os, "truncate", denied)
    for index in range(3):
        append_log(log, *[event_line(cache_status="HIT") for _ in range(4)])
        outcome = event_sweep.sweep_once(db, settings, moment(minute=index * 5))
        assert outcome.swept is True

    assert event_sweep.read_state(db).truncate_denied_count == 3
    assert stats_for(db, "192.168.1.42")["hits"] == 12


def test_another_oserror_during_truncation_is_not_reported_as_denied(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, cooldown=0, max_bytes=200)
    write_log(
        Path(settings.event_log_path),
        *[event_line(cache_status="HIT") for _ in range(4)],
    )

    def boom(*args, **kwargs):
        raise OSError("I/O error")

    monkeypatch.setattr(event_sweep.os, "truncate", boom)
    outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.swept is True
    assert outcome.truncated is False
    assert outcome.truncate_denied is False
    assert event_sweep.read_state(db).truncate_denied_count == 0


def test_writing_continues_correctly_after_this_sweeper_truncated(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Post-truncation the cursor is 0, so the next lines are read from 0."""
    settings = make_settings(tmp_path, cooldown=0, max_bytes=200)
    log = Path(settings.event_log_path)
    write_log(log, *[event_line(cache_status="HIT") for _ in range(4)])
    event_sweep.sweep_once(db, settings, moment())

    append_log(log, event_line(cache_status="MISS", depot="-", uri="/x"))
    outcome = event_sweep.sweep_once(db, settings, moment(minute=5))

    assert outcome.lines == 1
    assert stats_for(db, "192.168.1.42")["misses"] == 1


# ===========================================================================
# Scheduling gates
# ===========================================================================


def test_the_sweep_is_disabled_without_a_log_path(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, log_path="")

    outcome = event_sweep.maybe_sweep(db, settings, moment())

    assert settings.event_sweep_enabled is False
    assert outcome.swept is False
    assert outcome.skipped_reason == "disabled"


def test_the_interval_gate_allows_one_sweep_per_interval(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0, interval=5)
    log = Path(settings.event_log_path)
    write_log(log, event_line(cache_status="HIT"))

    first = event_sweep.maybe_sweep(db, settings, moment(hour=12, minute=0))
    append_log(log, event_line(cache_status="HIT"))
    too_soon = event_sweep.maybe_sweep(db, settings, moment(hour=12, minute=3))
    due = event_sweep.maybe_sweep(db, settings, moment(hour=12, minute=6))

    assert first.swept is True
    assert too_soon.swept is False
    assert too_soon.skipped_reason == "interval-not-elapsed"
    assert due.swept is True
    assert stats_for(db, "192.168.1.42")["hits"] == 2


def test_the_sweep_runs_regardless_of_the_prefill_schedule_window(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """PINNED DECISION (WP 3.11): the event sweep ignores VAULT_SCHEDULE_WINDOW.

    It owns the event log's rotation and feeds bypass detection; both break if
    the log goes unread for the hours the window is shut. Statistics collection
    is time-window-agnostic by nature.
    """
    from vault_api.schedule_window import parse_window

    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        schedule_window=parse_window("09:00-17:00"),
        event_log_path=str(tmp_path / "event.log"),
        miss_trigger_cooldown_minutes=0,
        event_log_max_bytes=0,
    )
    write_log(Path(settings.event_log_path), event_line(cache_status="HIT"))

    # 03:00 — the middle of the night, far outside the 09:00-17:00 window.
    outcome = event_sweep.maybe_sweep(db, settings, moment(hour=3))

    assert outcome.swept is True
    assert outcome.lines == 1


def test_a_future_last_sweep_timestamp_pauses_rather_than_storms(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Same fail direction as the prefill scheduler (clock stepped backwards)."""
    settings = make_settings(tmp_path, cooldown=0)
    write_log(Path(settings.event_log_path), event_line(cache_status="HIT"))
    event_sweep.maybe_sweep(db, settings, moment(hour=20))

    outcome = event_sweep.maybe_sweep(db, settings, moment(hour=12))

    assert outcome.swept is False
    assert outcome.skipped_reason == "interval-not-elapsed"


def test_an_unreadable_last_sweep_timestamp_does_not_disable_the_sweep(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    write_log(Path(settings.event_log_path), event_line(cache_status="HIT"))
    event_sweep.maybe_sweep(db, settings, moment())
    db.execute("UPDATE event_sweep_state SET last_sweep_at = 'garbage' WHERE id = 1")
    db.commit()

    assert event_sweep.interval_elapsed(
        event_sweep.read_state(db), moment(), 5
    ) is True


def test_the_first_sweep_timestamp_is_set_once_and_never_moves(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Bypass detection reads it as "how long have we been watching"."""
    settings = make_settings(tmp_path, cooldown=0, interval=5)
    write_log(Path(settings.event_log_path), event_line(cache_status="HIT"))

    event_sweep.maybe_sweep(db, settings, moment(hour=12, minute=0))
    first = event_sweep.read_state(db).first_sweep_at
    event_sweep.maybe_sweep(db, settings, moment(hour=13, minute=0))
    state = event_sweep.read_state(db)

    assert state.first_sweep_at == first
    assert state.last_sweep_at != first


# ===========================================================================
# Thread wiring — the sweep must run even with the prefill scheduler OFF
# ===========================================================================


def test_the_scheduler_thread_starts_for_the_event_sweep_alone(
    tmp_path: Path,
) -> None:
    """WP 3.5's thread is started by ``thread_needed``, not by ``enabled``.

    "Collect statistics, never download on a schedule" is an ordinary setup, so
    an unset VAULT_SCHEDULE_WINDOW must not take the event sweep down with it.
    """
    import time

    from fastapi.testclient import TestClient

    from vault_api.main import create_app
    from vault_api.scheduler import PrefillScheduler

    settings = make_settings(tmp_path, cooldown=0)
    write_log(
        Path(settings.event_log_path),
        *[event_line(cache_status="HIT") for _ in range(3)],
    )
    app = create_app(settings)
    scheduler = PrefillScheduler(settings, clock=moment, tick_seconds=0.05)
    app.state.scheduler = scheduler

    assert scheduler.enabled is False, "no prefill window is configured"
    assert scheduler.thread_needed is True, "but the event sweep needs ticks"

    with TestClient(app) as client:
        deadline = time.monotonic() + 10
        body = {"lines_read_total": 0}
        while time.monotonic() < deadline:
            body = client.get("/v1/stats", headers={"X-Api-Key": TEST_API_KEY}).json()
            if body["lines_read_total"]:
                break
            time.sleep(0.05)

    assert body["lines_read_total"] == 3
    assert body["cursor_offset"] > 0


def test_a_failing_event_sweep_does_not_stop_the_prefill_sweep(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """Each job rides the tick inside its OWN try (WP 3.11)."""
    import sqlite3 as sqlite

    from vault_api import scheduler as scheduler_module
    from vault_api.schedule_window import parse_window

    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        schedule_window=parse_window("00:00-24:00"),
        event_log_path=str(tmp_path / "event.log"),
        miss_trigger_cooldown_minutes=0,
        event_log_max_bytes=0,
    )
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('pc', ?, '[440]')",
            (jobs_queue.to_utc_iso(moment()),),
        )
        conn.commit()

        monkeypatch.setattr(
            scheduler_module.event_sweep,
            "maybe_sweep",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sweep exploded")),
        )
        scheduler = scheduler_module.PrefillScheduler(settings, clock=moment)
        with caplog.at_level(logging.ERROR):
            scheduler._tick(conn)

        assert "sweep exploded" in caplog.text
        # The prefill sweep still ran and enqueued its target.
        assert queued_appids(conn) == [440]
    finally:
        conn.close()
    assert isinstance(conn, sqlite.Connection)


# ===========================================================================
# depot_miss_stats retention
# ===========================================================================


def test_depot_miss_statistics_are_bounded(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    monkeypatch.setattr(event_sweep, "MAX_DEPOT_MISS_ROWS", 3)
    write_log(
        Path(settings.event_log_path),
        *[event_line(depot=str(90000 + index)) for index in range(6)],
    )

    event_sweep.sweep_once(db, settings, moment())

    assert db.execute("SELECT COUNT(*) FROM depot_miss_stats").fetchone()[0] == 3


# ---------------------------------------------------------------------------
# WP API-FIX-2, data-path P1: an unusable depot_app_map row must not stall
# the sweep forever
# ---------------------------------------------------------------------------


def test_a_non_numeric_mapping_row_does_not_stall_the_sweep(
    db: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``_appids_for_depot`` used to ``int()`` the column; SQLite's INTEGER
    affinity lets a string sit there, ``ValueError`` escaped ``aggregate_lines``
    BEFORE ``commit_batch``, and the cursor never advanced -- every later sweep
    re-read the same batch and failed the same way."""
    settings = make_settings(tmp_path)
    log_path = Path(settings.event_log_path)
    write_log(log_path, event_line(depot="70403"), event_line(depot="70403"))
    # Raw SQL on purpose: mapping.upsert_mapping validates its input, and the
    # whole point is a row that bypassed validation (hand edit, corruption).
    db.execute(
        "INSERT INTO depot_app_map (depotid, appid) VALUES (?, ?)", (70403, "abc")
    )
    map_depot(db, 70403, 440)
    db.commit()

    with caplog.at_level("WARNING", logger="vault_api.event_sweep"):
        outcome = event_sweep.sweep_once(db, settings, moment())

    assert outcome.swept is True
    assert outcome.cursor == log_path.stat().st_size, "the cursor must advance"
    assert any("unusable appid 'abc'" in r.getMessage() for r in caplog.records)
    # The usable co-mapping still yields the honest single target.
    assert queued_appids(db) == [440]


def test_appids_for_depot_skips_every_unusable_shape(db: sqlite3.Connection) -> None:
    for bad in ("abc", "", "1_0", "-5", 0, -3):
        db.execute("INSERT OR IGNORE INTO depot_app_map (depotid, appid) VALUES (?, ?)", (9, bad))
    db.execute("INSERT INTO depot_app_map (depotid, appid) VALUES (?, ?)", (9, 730))
    db.commit()

    assert event_sweep._appids_for_depot(db, 9) == [730]
