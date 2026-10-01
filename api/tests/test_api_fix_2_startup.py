"""WP API-FIX-2, N3 + N4: startup-time claims that must be true, not merely
intended -- the log-level lookup and the "peer address is never
header-derived" promise in ``routers/agent.py``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

from tests.conftest import TEST_API_KEY
from vault_api import main as main_module
from vault_api.config import Settings
from vault_api.main import create_app, resolve_log_level

API_DIR = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# N3: VAULT_LOG_LEVEL
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [("debug", logging.DEBUG), ("INFO", logging.INFO), (" warning ", logging.WARNING),
     ("Error", logging.ERROR), ("critical", logging.CRITICAL)],
)
def test_resolve_log_level_accepts_every_stdlib_level_name(name: str, expected: int) -> None:
    assert resolve_log_level(name) == expected


@pytest.mark.parametrize("name", ["verbose", "", "disable", "config", "root", "getLogger"])
def test_resolve_log_level_warns_and_falls_back_to_info_on_a_non_level(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """``disable``/``config``/``root`` ARE ``logging`` attributes; the old
    ``getattr`` lookup handed them to ``basicConfig(level=...)``, which raised
    ``TypeError`` at startup. ``verbose`` silently became INFO with no hint."""
    with caplog.at_level("WARNING", logger="vault_api.main"):
        assert resolve_log_level(name) == logging.INFO
    messages = [r.getMessage() for r in caplog.records]
    assert any(f"VAULT_LOG_LEVEL={name!r} is not a logging level" in m for m in messages)


def test_create_app_no_longer_crashes_on_a_logging_attribute_that_is_not_a_level(
    tmp_path: Path,
) -> None:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="disable",
    )
    create_app(settings)  # used to raise TypeError from logging.basicConfig


def test_create_app_wires_basic_config_through_the_resolver() -> None:
    source = Path(main_module.__file__).read_text(encoding="utf-8")
    assert "logging.basicConfig(level=resolve_log_level(settings.log_level))" in source


# --------------------------------------------------------------------------
# N4: uvicorn --no-proxy-headers in the shipped image
# --------------------------------------------------------------------------


def _dockerfile_cmd() -> list[str]:
    text = (API_DIR / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^CMD\s+(\[.*?\])\s*$", text, re.MULTILINE | re.DOTALL)
    assert match, "api/Dockerfile must have exactly one exec-form CMD"
    # exec-form CMD is JSON; collapse the backslash-newline continuation first.
    return json.loads(match.group(1).replace("\\\n", ""))


def test_dockerfile_starts_uvicorn_with_no_proxy_headers() -> None:
    """``routers/agent.py`` records ``request.client`` as the agent's identity
    address and promises it is never taken from ``X-Forwarded-For``. uvicorn
    defaults to ``--proxy-headers`` ON (trusting 127.0.0.1), so the promise is
    only true with this flag."""
    cmd = _dockerfile_cmd()
    assert cmd[:3] == ["python", "-m", "uvicorn"]
    assert "--no-proxy-headers" in cmd
    assert "--proxy-headers" not in cmd
    assert "--workers" not in " ".join(cmd)


def test_agent_router_docstring_names_the_flag_its_claim_rests_on() -> None:
    from vault_api.routers import agent

    doc = agent.report_installed_apps.__doc__ or ""
    assert "--no-proxy-headers" in doc
