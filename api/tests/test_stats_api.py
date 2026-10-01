"""WP 3.11: GET /v1/stats — the cache-event sweep's observability surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import event_line, write_log
from vault_api import event_sweep
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
TZ = timezone(timedelta(hours=2))


def make_client(
    tmp_path: Path, *, feed_on: bool = True, cooldown: int = 60
) -> tuple[TestClient, Settings]:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        event_log_path=str(tmp_path / "event.log") if feed_on else "",
        miss_trigger_cooldown_minutes=cooldown,
        event_log_max_bytes=0,
    )
    return TestClient(create_app(settings)), settings


def test_stats_requires_api_key(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    assert client.get("/v1/stats").status_code == 401


def test_stats_reports_the_configuration_when_the_feed_is_off(
    tmp_path: Path,
) -> None:
    client, _ = make_client(tmp_path, feed_on=False)

    body = client.get("/v1/stats", headers=AUTH).json()

    assert body["event_feed_enabled"] is False
    assert body["miss_trigger_enabled"] is False
    assert body["cursor_offset"] == 0
    assert body["first_sweep_at"] is None
    assert body["lines_read_total"] == 0
    assert body["top_unmapped_depots"] == []


def test_a_zero_cooldown_shows_the_trigger_as_off_with_the_feed_on(
    tmp_path: Path,
) -> None:
    client, _ = make_client(tmp_path, cooldown=0)

    body = client.get("/v1/stats", headers=AUTH).json()

    assert body["event_feed_enabled"] is True
    assert body["miss_trigger_enabled"] is False


def test_stats_reports_what_the_last_sweep_did(tmp_path: Path) -> None:
    client, settings = make_client(tmp_path)
    write_log(
        Path(settings.event_log_path),
        event_line(cache_status="HIT"),
        event_line(depot="99001"),  # a MISS on an unmapped depot
        event_line(version="v2"),  # skipped: unknown version
    )

    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        event_sweep.sweep_once(
            conn, settings, datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
        )
    finally:
        conn.close()

    body = client.get("/v1/stats", headers=AUTH).json()

    assert body["event_feed_enabled"] is True
    assert body["miss_trigger_enabled"] is True
    assert body["lines_read_total"] == 2
    assert body["lines_skipped_total"] == 1
    assert body["last_lines"] == 2
    assert body["last_skipped"] == 1
    assert body["cursor_offset"] > 0
    assert body["first_sweep_at"] is not None
    assert body["last_sweep_at"] is not None
    assert body["truncate_denied_count"] == 0
    assert [row["depotid"] for row in body["top_unmapped_depots"]] == [99001]
    assert body["top_unmapped_depots"][0]["miss_count"] == 1


def test_stats_surfaces_a_denied_rotation(tmp_path: Path, monkeypatch) -> None:
    """The container permission case has to be visible without reading logs."""
    client, settings = make_client(tmp_path)
    settings = Settings(**{**settings.__dict__, "event_log_max_bytes": 100})
    write_log(
        Path(settings.event_log_path),
        *[event_line(cache_status="HIT") for _ in range(4)],
    )

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(event_sweep.os, "ftruncate", denied)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        event_sweep.sweep_once(
            conn, settings, datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
        )
    finally:
        conn.close()

    body = client.get("/v1/stats", headers=AUTH).json()

    assert body["truncate_denied_count"] == 1
    assert body["last_truncate_denied_at"] is not None
    assert body["lines_read_total"] == 4, "sweeping was unaffected"


def test_only_unmapped_depots_are_listed(tmp_path: Path) -> None:
    """The list is the actionable one: depots with no mapping to act on."""
    from vault_api import mapping

    client, settings = make_client(tmp_path)
    write_log(
        Path(settings.event_log_path),
        event_line(depot="70403"),
        event_line(depot="99001"),
    )

    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        mapping.upsert_mapping(conn, depotid=70403, appid=440, name=None)
        event_sweep.sweep_once(
            conn, settings, datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
        )
    finally:
        conn.close()

    body = client.get("/v1/stats", headers=AUTH).json()

    assert [row["depotid"] for row in body["top_unmapped_depots"]] == [99001]
