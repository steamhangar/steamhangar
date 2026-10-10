"""Tests for the built-in web UI's static serving (WP 4a.1).

Covers: the app-shell routes and asset mounts exist and serve the real
repo `web/` directory by default, `index.html` is reachable with no API
key and never caches, `/v1/*` routing is BYTE-FOR-BYTE unchanged from a
build with no web UI at all (trailing-slash redirects, HEAD/405 semantics,
404 shape — the exact deltas the WP 4a.1 review round found and this
rewrite fixes), the SPA fallback only fires for the three scaffolded views
(never for `/v1/*`, `/docs`, or a made-up path), path-traversal attempts
404 safely, a missing web directory degrades to "API only" rather than a
crash, and the web-ui routes/mounts are registered after every `/v1/*`
router.
"""

from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import webui
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}

# The real repo web/ directory (created by this work package) — used to
# prove the DEFAULT wiring actually works end-to-end, the way
# `uvicorn vault_api.main:create_app --factory` would see it.
REPO_WEB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "web")
)
REPO_ROUTER_JS = os.path.join(REPO_WEB_DIR, "js", "router.js")


def _settings_with_web_dir(tmp_path, web_dir: str) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        web_dir=web_dir,
    )


# ---------------------------------------------------------------------------
# Default wiring: Settings() with no override resolves to the real web/ dir.
# ---------------------------------------------------------------------------


def test_default_settings_resolve_to_the_real_web_dir(tmp_path) -> None:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    assert os.path.normpath(settings.web_dir) == REPO_WEB_DIR
    assert os.path.isdir(settings.web_dir), (
        "the repo web/ directory must exist for WP 4a.1's DoD "
        "('served page loads against a running vault-api') to hold"
    )


def test_index_served_unauthenticated(client: TestClient) -> None:
    # `client` (conftest.py) builds Settings with no web_dir override, so
    # this exercises the real repo web/ directory end-to-end.
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "SteamHangar" in response.text


def test_index_has_no_cache_header(client: TestClient) -> None:
    response = client.get("/")
    assert response.headers.get("cache-control") == "no-cache"


def test_static_asset_served_with_asset_cache_control(client: TestClient) -> None:
    response = client.get("/css/theme.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    cache_control = response.headers.get("cache-control", "")
    assert "max-age" in cache_control
    assert cache_control != "no-cache"


def test_js_module_served_with_correct_mime_type(client: TestClient) -> None:
    response = client.get("/js/app.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_direct_index_html_request_also_gets_no_cache(client: TestClient) -> None:
    # Reached via the same handler as "/", registered separately by exact
    # path — both must agree on the header.
    response = client.get("/index.html")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-cache"


# ---------------------------------------------------------------------------
# SPA fallback: only the three scaffolded views, never /v1/*, /docs, or junk.
# Review fix (blocker B2): the frozen mockup has three nav items, not four —
# "clients" is NOT a view here (WP 4a.7 adds it as a sheet, not a nav item).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", ["library", "downloads", "settings"])
def test_spa_view_routes_serve_index(client: TestClient, view: str) -> None:
    response = client.get(f"/{view}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "SteamHangar" in response.text


def test_clients_is_not_a_registered_view(client: TestClient) -> None:
    # Review fix (blocker B2): "clients" was a fourth nav item/view in the
    # rejected version; it must not exist as a route until WP 4a.7.
    assert "clients" not in webui._SPA_ROUTES
    response = client.get("/clients")
    assert response.status_code == 404


def test_spa_fallback_serves_index_for_nested_view_paths(client: TestClient) -> None:
    # Later work packages (e.g. a game detail deep link) add paths UNDER an
    # existing top-level view; the route registered for it must already
    # cover that shape.
    response = client.get("/library/440")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.parametrize("path", ["/", "/index.html", "/library", "/downloads", "/settings"])
def test_head_on_app_shell_routes_matches_get(client: TestClient, path: str) -> None:
    # Review fix: HEAD must behave like GET on every app-shell route (200,
    # same content-type, empty body) — not 404 (the rejected version's
    # exception-handler design only special-cased GET) and not 405.
    get_response = client.get(path)
    head_response = client.head(path)
    assert head_response.status_code == 200 == get_response.status_code
    assert head_response.headers["content-type"] == get_response.headers["content-type"]
    assert head_response.headers.get("cache-control") == get_response.headers.get("cache-control")
    assert head_response.content == b""


# ---------------------------------------------------------------------------
# /v1/* routing parity — the exact deltas the review round measured and
# this rewrite exists to eliminate. Baseline values were captured empirically
# against the pre-WP-4a.1 app (git stash) before writing these assertions.
# ---------------------------------------------------------------------------


def test_v1_trailing_slash_still_redirects_307(client: TestClient) -> None:
    # Baseline (measured, web UI absent entirely): GET /v1/games/ -> 307.
    # The old catch-all Mount("/") intercepted this and turned it into a
    # 404 instead of letting Starlette's own redirect_slashes handle it.
    response = client.get("/v1/games/", headers=AUTH, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/v1/games")


def test_v1_health_head_still_returns_405(client: TestClient) -> None:
    # Baseline (measured): HEAD /v1/health -> 405 (FastAPI's APIRoute does
    # NOT auto-add HEAD to a GET-only route, unlike plain Starlette's
    # Route — the old catch-all Mount silently changed this to 404 by
    # intercepting the request instead of letting the router 405 it).
    response = client.head("/v1/health")
    assert response.status_code == 405


def test_v1_unknown_path_wrong_method_still_returns_404(client: TestClient) -> None:
    # Baseline (measured): POST to a /v1/ path that matches NO route at all
    # (not even by path) -> 404, same as GET. The old catch-all Mount
    # turned this into a 405 (its own "wrong method for a static path"
    # check) because the request reached StaticFiles at all.
    response = client.post("/v1/does-not-exist", headers=AUTH)
    assert response.status_code == 404


def test_unknown_v1_route_returns_404_json_not_html(client: TestClient) -> None:
    # The WP 4a.1 pin, verbatim: "an unknown /v1/ path must still 404/401
    # as today, never return HTML."
    response = client.get("/v1/does-not-exist", headers=AUTH)
    assert response.status_code == 404
    assert "html" not in response.headers.get("content-type", "").lower()
    assert "<html" not in response.text.lower()


def test_v1_auth_is_unchanged_with_web_ui_mounted(client: TestClient) -> None:
    # Same three cases as tests/test_auth.py, re-asserted here so a
    # regression in the web-ui wiring (e.g. auth accidentally attached to,
    # or bypassed by, one of its routes) fails THIS file too.
    assert client.get("/v1/games").status_code == 401
    assert client.get("/v1/games", headers={"X-Api-Key": "wrong-key"}).status_code == 401
    assert client.get("/v1/games", headers=AUTH).status_code == 200


def test_random_unmapped_path_returns_plain_404_not_html(client: TestClient) -> None:
    # A path that is neither a real asset, a known view, nor an API route
    # must 404 like a normal FastAPI app with no web UI at all.
    response = client.get("/this-path-does-not-exist-anywhere")
    assert response.status_code == 404
    assert "<html" not in response.text.lower()


def test_docs_and_openapi_stay_disabled_with_web_ui_mounted(client: TestClient) -> None:
    # Regression guard: FastAPI's deliberately-disabled docs routes
    # (main.py: openapi_url=None) must stay a plain 404, matching
    # tests/test_security.py, never a 200 HTML response.
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "<html" not in response.text.lower(), path


# ---------------------------------------------------------------------------
# Path traversal — StaticFiles' own commonpath guard (starlette) protects
# the /css and /js mounts; everything else has no matching route at all, so
# no filesystem access can happen regardless of the path shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/../api/vault_api/config.py",
        "/../../api/vault_api/config.py",
        "/css/../../vault_api/config.py",
        "/js/../../vault_api/config.py",
        "/css/%2e%2e/%2e%2e/vault_api/config.py",
        "/js/%2e%2e%2f%2e%2e%2fvault_api%2fconfig.py",
        "/.git/config",
        "/css/.git/config",
    ],
)
def test_path_traversal_attempts_return_404(client: TestClient, path: str) -> None:
    response = client.get(path, headers=AUTH)
    assert response.status_code == 404, path
    assert "VAULT_API_KEY" not in response.text
    assert "vault_api_key" not in response.text


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_present_on_api_and_ui_responses(client: TestClient) -> None:
    for path, headers in (("/v1/health", {}), ("/", {})):
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert "Content-Security-Policy" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


def test_csp_has_no_unsafe_inline(client: TestClient) -> None:
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp
    assert "'self'" in csp


def test_csp_img_src_allows_self_data_and_exactly_the_two_steam_asset_hosts(client: TestClient) -> None:
    # WP 4a.3: the Library view's cover art is the one deliberate CSP
    # extension over WP 4a.1's self-only baseline (web/js/lib/cover-art.js,
    # api/README.md "Security headers, including CSP"). Pin the exact
    # directive value, not just "contains the host" — a stray wildcard
    # (`*.steamstatic.com`) or a second host added later without updating
    # this test would silently widen what the browser will load images
    # from.
    csp = client.get("/").headers["Content-Security-Policy"]
    directives = dict(
        part.strip().split(" ", 1) for part in csp.split(";") if part.strip()
    )
    # WP API-FIX-5 adds exactly one more literal host for the hashed
    # store-item cover art (vault_api/cover_art.py).
    assert directives["img-src"] == (
        "'self' data: https://cdn.akamai.steamstatic.com "
        "https://shared.akamai.steamstatic.com"
    )


# ---------------------------------------------------------------------------
# Graceful degradation: no web/ directory -> API-only, no crash.
# ---------------------------------------------------------------------------


def test_missing_web_dir_does_not_crash_startup(tmp_path) -> None:
    settings = _settings_with_web_dir(tmp_path, str(tmp_path / "no-such-web-dir"))
    app = create_app(settings)
    missing_client = TestClient(app)

    # The API still works fully.
    assert missing_client.get("/v1/health").status_code == 200
    assert missing_client.get("/v1/games", headers=AUTH).status_code == 200

    # No UI to fall back to: a plain 404, not a crash, not HTML.
    response = missing_client.get("/")
    assert response.status_code == 404
    assert "<html" not in response.text.lower()


def test_missing_web_dir_still_keeps_v1_auth_and_404_semantics(tmp_path) -> None:
    settings = _settings_with_web_dir(tmp_path, str(tmp_path / "no-such-web-dir"))
    client = TestClient(create_app(settings))

    assert client.get("/v1/games").status_code == 401
    assert client.get("/v1/does-not-exist", headers=AUTH).status_code == 404
    assert client.get("/v1/games/", headers=AUTH, follow_redirects=False).status_code == 307
    assert client.head("/v1/health").status_code == 405


# ---------------------------------------------------------------------------
# Route registration order: API routes must always win (SHOULD-FIX 1).
# ---------------------------------------------------------------------------


def _webui_route_indices(app) -> list[int]:
    """Indices in app.routes contributed by mount_web_ui (view/index routes
    and the /css, /js mounts)."""
    webui_paths = {"/", "/index.html", "/css", "/js"}
    for view in webui._SPA_ROUTES:
        webui_paths.add(f"/{view}")
        webui_paths.add(f"/{view}/{{rest:path}}")
    return [i for i, route in enumerate(app.routes) if getattr(route, "path", None) in webui_paths]


def _v1_route_indices(app) -> list[int]:
    return [
        i
        for i, route in enumerate(app.routes)
        if isinstance(getattr(route, "path", None), str) and route.path.startswith("/v1/")
    ]


def test_web_ui_routes_are_registered_after_v1_routers(client: TestClient) -> None:
    app = client.app
    webui_indices = _webui_route_indices(app)
    v1_indices = _v1_route_indices(app)
    assert webui_indices, "expected at least one web-ui route to be registered"
    assert v1_indices, "expected at least one /v1/* route to be registered"
    assert min(webui_indices) > max(v1_indices), (
        "a web-ui route/mount is registered before a /v1/* route — this no "
        "longer breaks routing correctness (webui routes are exact paths, "
        "not a catch-all), but the invariant 'API routes always win' should "
        "still hold by construction"
    )


# ---------------------------------------------------------------------------
# Sync check: web/js/router.js's VIEWS must match webui.py's _SPA_ROUTES
# (SHOULD-FIX 2) — a mismatch means either a nav button with no server
# route, or a server route nothing links to.
# ---------------------------------------------------------------------------


def test_router_js_views_match_webui_spa_routes() -> None:
    source = open(REPO_ROUTER_JS, encoding="utf-8").read()
    match = re.search(r"export const VIEWS\s*=\s*\[([^\]]*)\]", source)
    assert match, "could not find 'export const VIEWS = [...]' in web/js/router.js"
    js_views = {v.strip().strip("'\"") for v in match.group(1).split(",") if v.strip()}
    assert js_views == set(webui._SPA_ROUTES)


# ---------------------------------------------------------------------------
# Isolated fixture: pin the exact routing rules against a minimal synthetic
# web/ directory, independent of the real one's contents.
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_web_client(tmp_path) -> TestClient:
    web_dir = tmp_path / "web"
    (web_dir / "js").mkdir(parents=True)
    (web_dir / "index.html").write_text("<html><body>synthetic shell</body></html>", encoding="utf-8")
    (web_dir / "js" / "app.js").write_text("// marker\n", encoding="utf-8")
    settings = _settings_with_web_dir(tmp_path, str(web_dir))
    return TestClient(create_app(settings))


def test_synthetic_root_serves_synthetic_index(synthetic_web_client: TestClient) -> None:
    response = synthetic_web_client.get("/")
    assert response.status_code == 200
    assert "synthetic shell" in response.text


def test_synthetic_asset_served_directly(synthetic_web_client: TestClient) -> None:
    response = synthetic_web_client.get("/js/app.js")
    assert response.status_code == 200
    assert response.text.strip() == "// marker"


def test_synthetic_unknown_v1_path_never_html(synthetic_web_client: TestClient) -> None:
    response = synthetic_web_client.get("/v1/nope", headers=AUTH)
    assert response.status_code == 404
    assert "synthetic shell" not in response.text


def test_synthetic_missing_asset_under_spa_route_falls_back_to_index(
    synthetic_web_client: TestClient,
) -> None:
    # /library/missing-icon.png isn't a real file, but its first segment IS
    # a known view, so it is treated as a client-side route.
    response = synthetic_web_client.get("/library/missing-icon.png")
    assert response.status_code == 200
    assert "synthetic shell" in response.text


def test_synthetic_missing_index_serves_assets_only(tmp_path) -> None:
    web_dir = tmp_path / "web"
    (web_dir / "js").mkdir(parents=True)
    (web_dir / "js" / "app.js").write_text("// marker\n", encoding="utf-8")
    settings = _settings_with_web_dir(tmp_path, str(web_dir))
    no_index_client = TestClient(create_app(settings))

    assert no_index_client.get("/js/app.js").status_code == 200
    assert no_index_client.get("/").status_code == 404
