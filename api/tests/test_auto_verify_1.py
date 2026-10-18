"""WP AUTO-VERIFY-1: a cached game holding only an update delta is caught.

Operator decision 2026-10-18 (Weg A). Guarantees pinned here, by name:

1. **Delta-only reads "Partially cached".** A game whose recorded manifest is
   N+1 but whose disk holds only the chunks the update changed is measured
   against EVERY chunk of N+1, in EVERY mapped depot (also a depot the update
   did not touch and that has nothing on disk). Old-build chunks that N+1 does
   not list count for nothing. ``GET /v1/games`` reports pct < 100 and
   ``cache_missing_chunks`` > 0, and ``?incomplete=true`` lists it. Holds
   whether N+1 came from a prefill (archived ``.bin``) or a Steam client
   update through the cache (oracle announcement + cache-stored manifest).
2. **The sweep completes it only while the switch is on.**
   ``auto_complete_cached`` (default on): the sweep flags it
   ``needs_force = 1`` and enqueues it. Off: nothing is forced and nothing is
   enqueued for completeness; manual ``POST /v1/prefill/verify`` still forces.
3. **Settings plumbing.** Default on (env unset), env pin wins and is
   read-only in ``GET /v1/settings``, a stored Settings value reaches the
   scheduler through ``effective_settings``, ``GET /v1/schedule`` reports
   the effective value.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_cache_complete_1a import chunks, oracle_says, record, settings_for
from tests.test_gc import _cid, write_archive_bin, write_cache_manifest, write_chunks
from tests.test_scheduler import local
from vault_api import cache_completeness, config, jobs, scheduler, settings_store
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.schedule_window import parse_window

AUTH = {"X-Api-Key": TEST_API_KEY}

APPID = 730
CHANGED_DEPOT = 731  # the update touched this depot
UNTOUCHED_DEPOT = 732  # the update did not; nothing of it is on disk
OLD_GID = "1000"  # build N of the changed depot
NEW_GID = "2000"  # build N+1 of the changed depot
UNTOUCHED_GID = "1500"

#: Build N: chunks 0..9. Build N+1 keeps 0..4 and replaces 5..9 by 100..104.
OLD_BUILD = chunks(0, 10)
NEW_BUILD = {**chunks(0, 5), **chunks(100, 5)}
DELTA = chunks(100, 5)
OLD_ONLY = chunks(5, 5)
UNTOUCHED_BUILD = chunks(200, 4)


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def window_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "schedule_window": parse_window("09:00-17:00"),
        # No sweep targets from the cached mode: only the completion step can
        # enqueue the game, so "enqueued" proves completion.
        "sweep_include_cached": False,
        **overrides,
    }
    return dataclasses.replace(settings_for(tmp_path), **values)


def seed_delta_only(conn: sqlite3.Connection, settings: Settings, *, via: str) -> None:
    """The game was prefilled at build N once (both depots recorded and
    archived), its chunks are gone, and only the update delta to N+1 reached
    the cache. ``via``: ``prefill`` = a prefill recorded and archived N+1;
    ``client`` = the oracle announced N+1 and a Steam client fetched its
    manifest and the changed chunks through the cache."""
    cache = Path(settings.cache_root)
    archive = Path(settings.manifest_archive_dir)
    record(conn, APPID, CHANGED_DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(archive, depotid=CHANGED_DEPOT, manifestid=OLD_GID, chunks=OLD_BUILD)
    record(conn, APPID, UNTOUCHED_DEPOT, UNTOUCHED_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(
        archive, depotid=UNTOUCHED_DEPOT, manifestid=UNTOUCHED_GID, chunks=UNTOUCHED_BUILD
    )
    if via == "prefill":
        record(conn, APPID, CHANGED_DEPOT, NEW_GID, "2026-10-17T00:00:00Z")
        write_archive_bin(archive, depotid=CHANGED_DEPOT, manifestid=NEW_GID, chunks=NEW_BUILD)
    else:
        oracle_says(conn, APPID, CHANGED_DEPOT, NEW_GID, "2026-10-17T00:00:00Z")
        oracle_says(conn, APPID, UNTOUCHED_DEPOT, UNTOUCHED_GID, "2026-10-17T00:00:00Z")
        write_cache_manifest(cache, depotid=CHANGED_DEPOT, manifestid=NEW_GID, chunks=NEW_BUILD)
    write_chunks(cache, CHANGED_DEPOT, DELTA)
    # Leftovers of build N that N+1 no longer lists: never counted for N+1.
    write_chunks(cache, CHANGED_DEPOT, OLD_ONLY)
    jobs.ensure_app_row(conn, APPID)
    conn.execute("UPDATE apps SET needs_force = 0")
    conn.commit()
    # The update check's disk measurement (it only reads the disk).
    cache_completeness.evaluate_apps(conn, settings, [APPID])


def needs_force(conn: sqlite3.Connection, appid: int = APPID) -> int:
    return int(conn.execute("SELECT needs_force FROM apps WHERE appid = ?", (appid,)).fetchone()[0])


# --------------------------------------------------------------------------
# 1. delta-only reads "Partially cached"
# --------------------------------------------------------------------------


@pytest.mark.parametrize("via", ["prefill", "client"])
def test_a_delta_only_game_reads_partially_cached(
    tmp_path: Path, conn: sqlite3.Connection, via: str
) -> None:
    settings = window_settings(tmp_path)
    seed_delta_only(conn, settings, via=via)

    rows = {
        int(r["depotid"]): r
        for r in conn.execute("SELECT * FROM cache_completeness WHERE appid = ?", (APPID,))
    }
    # Every mapped depot is measured, not only the one the update changed.
    assert set(rows) == {CHANGED_DEPOT, UNTOUCHED_DEPOT}
    changed, untouched = rows[CHANGED_DEPOT], rows[UNTOUCHED_DEPOT]
    # Against N+1, never N; the old-only leftovers are not "present".
    assert (changed["manifestid"], changed["status"]) == (NEW_GID, "ok")
    assert (changed["expected_bytes"], changed["present_bytes"]) == (1000, 500)
    assert (changed["missing_chunks"], changed["mismatched_chunks"]) == (5, 0)
    assert (untouched["manifestid"], untouched["status"]) == (UNTOUCHED_GID, "ok")
    assert (untouched["expected_bytes"], untouched["present_bytes"]) == (400, 0)
    assert untouched["missing_chunks"] == 4

    client = TestClient(create_app(settings))  # no lifespan: no worker, no tick
    game = client.get(f"/v1/games/{APPID}", headers=AUTH).json()
    # 500 of 1400 bytes: "Partially cached 35%" in the web UI.
    assert game["cache_complete_pct"] == 35
    assert (game["cache_expected_bytes"], game["cache_present_bytes"]) == (1400, 500)
    assert game["cache_missing_chunks"] == 9
    assert game["cache_complete_checked_at"]  # the "verified" signal
    listed = client.get("/v1/games?incomplete=true", headers=AUTH).json()
    assert [g["appid"] for g in listed] == [APPID]


def test_old_build_chunks_alone_never_make_the_new_build_complete(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """The mirror image: the whole OLD build on disk, N+1 recorded, none of
    the delta. Bytes on disk exceed the delta, the figure still says 50 %."""
    settings = window_settings(tmp_path)
    cache = Path(settings.cache_root)
    archive = Path(settings.manifest_archive_dir)
    record(conn, APPID, CHANGED_DEPOT, NEW_GID, "2026-10-17T00:00:00Z")
    write_archive_bin(archive, depotid=CHANGED_DEPOT, manifestid=NEW_GID, chunks=NEW_BUILD)
    write_chunks(cache, CHANGED_DEPOT, OLD_BUILD)
    cache_completeness.evaluate_apps(conn, settings, [APPID])

    index = cache_completeness.load_index(conn, settings)
    figure = index.figure([(APPID, CHANGED_DEPOT)])
    assert (figure.pct, figure.present_bytes, figure.missing_chunks) == (50, 500, 5)


# --------------------------------------------------------------------------
# 2. the sweep completes it only while the switch is on
# --------------------------------------------------------------------------


@pytest.mark.parametrize("via", ["prefill", "client"])
def test_the_sweep_completes_a_delta_only_game_when_the_switch_is_on(
    tmp_path: Path, conn: sqlite3.Connection, via: str
) -> None:
    settings = window_settings(tmp_path)
    assert settings.auto_complete_cached is True  # the default
    seed_delta_only(conn, settings, via=via)

    result = scheduler.maybe_sweep(conn, settings, local(10))

    assert result.swept and result.targets == ()
    assert result.completing == (APPID,)
    assert result.enqueued == (APPID,)
    assert needs_force(conn) == 1


@pytest.mark.parametrize("include_cached", [False, True])
def test_the_sweep_never_forces_it_when_the_switch_is_off(
    tmp_path: Path, conn: sqlite3.Connection, include_cached: bool
) -> None:
    settings = window_settings(
        tmp_path, auto_complete_cached=False, sweep_include_cached=include_cached
    )
    seed_delta_only(conn, settings, via="prefill")

    result = scheduler.maybe_sweep(conn, settings, local(10))

    assert result.swept
    assert result.completing == ()
    assert needs_force(conn) == 0
    queued = [int(r[0]) for r in conn.execute("SELECT appid FROM jobs")]
    # With the cached mode on the game is still a normal (non-forced) sweep
    # target; with it off nothing is queued at all.
    assert queued == ([APPID] if include_cached else [])


def test_manual_verify_still_forces_when_the_switch_is_off(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = window_settings(tmp_path, auto_complete_cached=False)
    seed_delta_only(conn, settings, via="prefill")
    client = TestClient(create_app(settings))

    response = client.post("/v1/prefill/verify", json={"appids": [APPID]}, headers=AUTH)

    assert response.status_code == 202
    assert needs_force(conn) == 1


def test_switch_off_skips_the_selection_entirely(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means the completion selection is not even computed (no depot walk
    on its behalf)."""
    settings = window_settings(tmp_path, auto_complete_cached=False)
    seed_delta_only(conn, settings, via="prefill")

    def boom(*_args: Any, **_kwargs: Any) -> tuple[int, ...]:
        raise AssertionError("completion_appids called while the switch is off")

    monkeypatch.setattr(scheduler, "completion_appids", boom)
    assert scheduler.maybe_sweep(conn, settings, local(10)).completing == ()


# --------------------------------------------------------------------------
# 3. settings plumbing
# --------------------------------------------------------------------------

KEY = "auto_complete_cached"
ENV = "VAULT_AUTO_COMPLETE_CACHED"


def test_default_is_on_and_env_parses_strict_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.DEFAULT_AUTO_COMPLETE_CACHED is True
    assert Settings(vault_api_key="k", db_path="x", cache_root="c", log_level="INFO").auto_complete_cached is True
    spec = settings_store.OVERRIDABLE_SPECS[KEY]
    assert (spec.env_var, spec.applies, spec.default, spec.expert) == (
        ENV, "next_sweep", True, False,
    )
    monkeypatch.setenv("VAULT_API_KEY", TEST_API_KEY)
    monkeypatch.delenv(ENV, raising=False)
    assert Settings.from_env().auto_complete_cached is True
    monkeypatch.setenv(ENV, "off")
    off = Settings.from_env()
    assert off.auto_complete_cached is False
    assert ENV in off.env_pinned
    monkeypatch.setenv(ENV, "maybe")
    with pytest.raises(RuntimeError, match=ENV):
        Settings.from_env()


def _row(body: dict[str, Any]) -> dict[str, Any]:
    return next(e for e in body["settings"] if e["key"] == KEY)


def test_a_stored_value_reaches_the_scheduler_and_the_schedule_endpoint(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = window_settings(tmp_path)
    client = TestClient(create_app(settings))
    assert client.get("/v1/schedule", headers=AUTH).json()["auto_complete_cached"] is True

    response = client.patch("/v1/settings", json={KEY: "false"}, headers=AUTH)
    assert response.status_code == 200
    entry = _row(client.get("/v1/settings", headers=AUTH).json())
    assert (entry["effective"], entry["source"], entry["editable"]) == (False, "db", True)
    assert client.get("/v1/schedule", headers=AUTH).json()["auto_complete_cached"] is False

    # The scheduler thread resolves effective settings per tick the same way.
    effective = settings_store.effective_settings(conn, settings)
    assert effective.auto_complete_cached is False
    seed_delta_only(conn, settings, via="prefill")
    assert scheduler.maybe_sweep(conn, effective, local(10)).completing == ()


def test_an_env_pin_wins_and_is_read_only(tmp_path: Path) -> None:
    settings = window_settings(
        tmp_path, auto_complete_cached=False, env_pinned=frozenset({ENV})
    )
    client = TestClient(create_app(settings))
    entry = _row(client.get("/v1/settings", headers=AUTH).json())
    assert (entry["effective"], entry["source"], entry["editable"]) == (False, "env", False)
    assert client.patch("/v1/settings", json={KEY: "true"}, headers=AUTH).status_code == 409
    assert client.get("/v1/schedule", headers=AUTH).json()["auto_complete_cached"] is False


def test_the_fixture_is_a_real_delta() -> None:
    """Guard for the fixture itself: the delta and the old-only leftovers are
    disjoint, and N+1 shares exactly half of N."""
    assert set(DELTA).isdisjoint(OLD_BUILD)
    assert set(OLD_ONLY) <= set(OLD_BUILD) and set(OLD_ONLY).isdisjoint(NEW_BUILD)
    assert len(set(OLD_BUILD) & set(NEW_BUILD)) == 5
    assert _cid(100) in NEW_BUILD
