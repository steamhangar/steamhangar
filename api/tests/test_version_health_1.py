"""VERSION-HEALTH-1: the schema version is readable without the API key.

The build version was already logged at startup (WP VER-1); this package adds
the database schema version to the same startup output. ``/v1/health`` stays
a fixed body (pinned in ``test_health.py``).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app, log_schema_version


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "vault_api.main"]


def test_startup_logs_the_stored_schema_version(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="vault_api.main"):
        create_app(_settings(tmp_path))
    assert f"vault-api database schema version {SCHEMA_VERSION}" in _messages(caplog)


def test_schema_log_reads_the_stored_value_not_the_constant(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = str(tmp_path / "vault.db")
    init_db(db)
    conn = get_connection(db)
    try:
        conn.execute("UPDATE schema_version SET version = 7")
        conn.commit()
    finally:
        conn.close()
    with caplog.at_level(logging.INFO, logger="vault_api.main"):
        log_schema_version(db)
    assert "vault-api database schema version 7" in _messages(caplog)


def test_schema_log_is_fail_soft(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # An empty file has no schema_version table: warn, never raise.
    with caplog.at_level(logging.INFO, logger="vault_api.main"):
        log_schema_version(str(tmp_path / "empty.db"))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
