"""WP ID-AUDIT: traffic reads name an app the same way ``GET /v1/games`` does.

Before, ``traffic.app_names`` read ``apps.name`` only, so an app whose name
sat in ``app_classification`` (Steam's store/oracle answer) or on the fixed
tool list showed up as ``name: null`` on the Clients and Downloads pages and
the UIs printed "App <id>". Reading stored rows adds no lookup and no egress.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from vault_api import app_classification, traffic
from vault_api.db import get_connection, init_db

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    c = get_connection(db_path)
    try:
        yield c
    finally:
        c.close()


def _app(conn, appid: int, name: str | None) -> None:
    conn.execute("INSERT INTO apps (appid, name, status) VALUES (?, ?, 'idle')", (appid, name))
    conn.commit()


def _store_name(conn, appid: int, name: str) -> None:
    app_classification.record_store_results(
        conn, {appid: app_classification.StoreFacts(answered=True, name=name)}, NOW
    )


def _oracle_name(conn, appid: int, name: str) -> None:
    conn.execute(
        "INSERT INTO app_classification (appid, source, linked, name, checked_at) "
        "VALUES (?, ?, 0, ?, 'x')",
        (appid, app_classification.SOURCE_STEAMCMD_API, name),
    )
    conn.commit()


def test_own_name_wins_then_stored_steam_name_then_tool_name_then_none(conn) -> None:
    _app(conn, 1465470, None)
    _app(conn, 440, "Team Fortress 2")
    _app(conn, 228980, None)
    _app(conn, 3419430, None)
    _store_name(conn, 1465470, "Steam Name")
    _store_name(conn, 440, "Store Spelling")  # must not override apps.name

    names = traffic.app_names(conn, [1465470, 440, 228980, 3419430, 999])

    assert names == {
        1465470: "Steam Name",
        440: "Team Fortress 2",
        228980: "Steamworks Common Redistributables",
        3419430: None,  # no source knows it: the UI shows a neutral fallback
        999: None,  # not even an apps row
    }


def test_blank_apps_name_counts_as_unnamed(conn) -> None:
    _app(conn, 10, "   ")
    _store_name(conn, 10, "Counter-Strike")
    assert traffic.app_names(conn, [10]) == {10: "Counter-Strike"}


def test_oracle_name_is_used_only_while_the_oracle_is_on(conn) -> None:
    _app(conn, 20, None)
    _oracle_name(conn, 20, "Oracle Name")

    assert traffic.app_names(conn, [20]) == {20: None}
    assert traffic.app_names(conn, [20], oracle_enabled=True) == {20: "Oracle Name"}


def test_store_name_beats_oracle_name_and_names_are_sanitized(conn) -> None:
    _app(conn, 30, None)
    _store_name(conn, 30, "Store​ Name\x07")
    _oracle_name(conn, 30, "Oracle Name")

    assert traffic.app_names(conn, [30], oracle_enabled=True) == {30: "Store Name"}


def test_reading_names_makes_no_request(conn, monkeypatch) -> None:
    from vault_api import steam_relay

    def boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("app_names must not touch the network")

    monkeypatch.setattr(steam_relay, "http_fetch", boom)
    _app(conn, 40, None)
    assert traffic.app_names(conn, [40]) == {40: None}
