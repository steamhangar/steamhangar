"""WP SEC-FIX-4 S-3: no body is read or parsed before the API key is checked."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import body_guard
from vault_api.agent_reports import MAX_APPIDS_PER_REPORT
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
JSON = {"content-type": "application/json"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="WARNING",
    )
    return TestClient(create_app(settings))


def test_malformed_json_without_a_key_is_401_not_422(client: TestClient) -> None:
    response = client.post("/v1/agent/installed", content=b"{not json", headers=JSON)
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"


def test_an_over_cap_body_without_a_key_is_refused_unread(client: TestClient) -> None:
    big = b"1" * (body_guard.MAX_BODY_BYTES + 1)
    response = client.post("/v1/agent/installed", content=big, headers=JSON)
    assert response.status_code in (401, 413)
    assert response.status_code != 422


def test_an_over_cap_body_with_a_key_is_413(client: TestClient) -> None:
    big = b"1" * (body_guard.MAX_BODY_BYTES + 1)
    response = client.post(
        "/v1/agent/installed", content=big, headers={**JSON, **AUTH}
    )
    assert response.status_code == 413


def test_an_over_cap_chunked_body_with_a_key_is_413(client: TestClient) -> None:
    """No Content-Length: the guard counts what arrives."""

    def chunks():
        for _ in range(body_guard.MAX_BODY_BYTES // 65536 + 2):
            yield b"1" * 65536

    response = client.post(
        "/v1/agent/installed", content=chunks(), headers={**JSON, **AUTH}
    )
    assert response.status_code == 413


def test_a_full_size_agent_report_still_works(client: TestClient) -> None:
    appids = list(range(1_000_000_000, 1_000_000_000 + MAX_APPIDS_PER_REPORT))
    response = client.post(
        "/v1/agent/installed",
        json={"client_id": "pc-1", "appids": appids},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text


def test_health_stays_public(client: TestClient) -> None:
    assert client.get("/v1/health").status_code == 200


def test_the_guard_uses_the_same_key_check(client: TestClient) -> None:
    response = client.post(
        "/v1/agent/installed",
        content=b"{not json",
        headers={**JSON, "X-Api-Key": TEST_API_KEY + "x"},
    )
    assert response.status_code == 401


def _raw_status(client: TestClient, method: str, path: str, headers=()) -> int:
    """Drive the ASGI app directly: TestClient would normalise the path."""
    import asyncio

    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in headers],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
    }
    asyncio.run(client.app(scope, receive, send))
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


@pytest.mark.parametrize("path", ["/v1/", "/v1//health", "/v1/health/", "/v1/./health"])
def test_health_lookalike_paths_without_a_key_are_401(
    client: TestClient, path: str
) -> None:
    """Only exactly /v1/health is public; variants never reach a redirect."""
    assert _raw_status(client, "GET", path) == 401


def test_a_double_slash_prefix_is_not_a_v1_route(client: TestClient) -> None:
    assert _raw_status(client, "GET", "//v1/stats") == 404


def test_a_non_digit_content_length_is_400(client: TestClient) -> None:
    status = _raw_status(
        client,
        "POST",
        "/v1/agent/installed",
        [("x-api-key", TEST_API_KEY), ("content-length", "1e3")],
    )
    assert status == 400
