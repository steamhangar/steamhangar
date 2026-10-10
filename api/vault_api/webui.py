"""Static serving for the built-in web UI (WP 4a.1, Phase 4a stack decision:
a no-build vanilla ES-module SPA — see docs/WORKPACKAGES.md).

**Review fix (WP 4a.1, blocker B1).** The first version of this module
mounted the whole ``web_dir`` at ``"/"`` with ``StaticFiles`` and used a
global ``StarletteHTTPException`` handler to fall back to ``index.html``
for unmatched GET paths. That is a catch-all: it intercepts EVERY path with
no other matching route, which silently changed behaviour that has nothing
to do with the web UI at all — measured deltas against the pre-WP app:
``GET /v1/games/`` (trailing slash) went from a 307 redirect to a 404,
``HEAD /v1/health`` went from 405 to 404, and a wrong-method request to a
made-up ``/v1/...`` path went from 404 to 405. None of that is acceptable
for a work package scoped to "serve some static files" — vault-api's
existing ``/v1/*`` routing (owned entirely by Starlette's router, including
its ``redirect_slashes`` and partial-match/405 behaviour) must stay
byte-for-byte what it was.

The fix is to stop being a catch-all: register REAL, EXACT routes for the
app shell (``/``, ``/index.html``, and the scaffolded view paths) and mount
``StaticFiles`` only on the actual asset subtrees (``/css``, ``/js``) —
never on ``"/"``. A request that matches none of these (every ``/v1/*``
path, ``/docs``, a typo'd top-level path, ...) now never reaches ANY code
in this module; it falls straight through to Starlette's own default
routing, identical to a vault-api build with no web UI mounted at all. No
custom exception handler is needed any more.

**Review fix (WP 4a.1, blocker B2).** The frozen round-7 mockup
(``docs/design/vault-app-mockup.html``), the design source of truth, has
exactly THREE bottom-nav destinations: Library, Downloads, Settings.
"Clients" is reached from the bypass banner as a sheet, not a nav item, and
belongs to WP 4a.7 — dropped from ``_SPA_ROUTES`` here and from the nav
markup / router in ``web/``.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

#: The scaffolded top-level views (WP 4a.1 brief: "nav, views scaffold").
#: Keep this in sync with `VIEWS` in web/js/router.js — a sync test
#: (tests/test_webui.py) parses both and asserts equality, so adding a view
#: on only one side fails the suite instead of silently deep-linking to a
#: 404 or leaving a nav button with no server-side route.
_SPA_ROUTES = ("library", "downloads", "settings")

#: The app shell itself always revalidates: a stale cached copy after a
#: deploy is the one caching mistake worth actively avoiding here.
_INDEX_CACHE_CONTROL = "no-cache"

#: Every other static asset. Short and revalidate-friendly rather than long:
#: WP 4a.1 ships a no-build, no-hashing SPA (Phase 4a stack decision — no
#: bundler, ever), so a ``.js``/``.css`` file has no content-hashed filename
#: to bust a long-lived cache with after a deploy. ``must-revalidate`` means
#: a client holding a copy still within the 5-minute window skips the round
#: trip, but never serves a silently outdated file once it expires.
_ASSET_CACHE_CONTROL = "public, max-age=300, must-revalidate"

#: Content-Security-Policy for the whole app (API responses carry it too —
#: a JSON body ignores it, and there is no reason to special-case the
#: router just to omit it there). Self-only throughout: the shipped app
#: shell is plain ES modules and stylesheets, no CDN, no inline ``<script>``
#: or ``<style>`` tags and no inline ``style="..."`` attributes in the HTML
#: this work package ships. Dynamic styling from the app's own JS uses the
#: CSSOM (``element.style.prop = value``), which CSP's ``style-src`` does
#: NOT restrict — only literal inline markup is covered — so
#: ``'unsafe-inline'`` is never needed here. If a later 4a.x package
#: genuinely needs an inline style or script, it must extend this policy
#: explicitly and say why in a comment, not add a blanket
#: ``'unsafe-inline'``.
#:
#: **Deliberate extension (WP 4a.3): ``img-src`` additionally allows
#: exactly one external host, ``cdn.akamai.steamstatic.com``.** The Library
#: view (``web/js/lib/cover-art.js``) fetches real Steam capsule artwork
#: (``https://cdn.akamai.steamstatic.com/steam/apps/{appid}/library_600x900.jpg``)
#: by appid — the same 2:3 portrait asset the frozen design mockup's fake
#: covers were already modeled on (docs/design/vault-app-mockup-NOTES.md,
#: "Covers": "costs nothing to swap in real Steam artwork later"). This is
#: the ONE host that serves that path; no wildcard (``*.steamstatic.com``)
#: and no second CDN alias are added. A missing/blocked image (offline LAN,
#: no route to the host — a real homelab deployment) degrades to a styled
#: fallback tile in JS, never a broken layout — the CSP addition only ever
#: WIDENS what CAN load, it never becomes a hard dependency.
#:
#: **Second host (WP API-FIX-5): ``shared.akamai.steamstatic.com``.** Valve
#: serves newer games' covers only under a hashed path on this host
#: (``/store_item_assets/steam/apps/{appid}/{hash}/library_600x900.jpg``);
#: the legacy path above answers 404 for them. vault-api looks the real URL
#: up (``vault_api/cover_art.py``) and serves it as ``cover_url`` in
#: ``GET /v1/games``; it only ever stores and serves https URLs on these two
#: hosts, and ``web/js/lib/cover-art.js`` checks the same two hosts again
#: before using one. Still no wildcard; exactly two literal hosts.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data: https://cdn.akamai.steamstatic.com "
    "https://shared.akamai.steamstatic.com; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)


def install_security_headers(app: FastAPI) -> None:
    """Attach security headers to every response — API and UI alike.

    Deliberately unconditional (installed regardless of whether a web UI is
    mounted): a homelab API surface benefits from these headers on its own,
    and there is no reason the JSON-only deployment shape should miss them.
    """

    @app.middleware("http")
    async def _security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        return response


class _AssetStaticFiles(StaticFiles):
    """``StaticFiles`` for one asset subtree (``css/`` or ``js/``) with a
    uniform, revalidate-friendly ``Cache-Control`` — see the module-level
    constants for why it is short rather than long. Never pointed at the
    whole ``web_dir`` (that would include ``index.html``, which needs the
    opposite policy — applied separately, directly by ``_serve_index``
    below), only at the two real asset subtrees.
    """

    def file_response(self, full_path, stat_result, scope, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = _ASSET_CACHE_CONTROL
        return response


def mount_web_ui(app: FastAPI, web_dir: str) -> bool:
    """Serve ``web_dir`` as the app shell + static assets, if it exists.

    Returns True if the directory was found and at least the app shell or
    an asset subtree was wired up, False if there is nothing to serve (e.g.
    the shipped Docker image, which does not yet copy ``web/`` in — see
    ``config._default_web_dir``). A missing directory is NOT a startup
    error — vault-api is a fully working headless API without a UI.

    No auth dependency is attached to any of this (WP 4a.1 brief): the
    static assets and the app shell need no key, since the SPA itself sends
    ``X-Api-Key`` on its own ``/v1/*`` calls once the user enters one in
    Settings.

    Call this AFTER every ``/v1/*`` router is registered (``main.py``
    enforces the order). Unlike the original catch-all-Mount design, this
    no longer strictly matters for correctness — every route registered
    here is either an exact path or a narrow, real asset-subtree prefix, so
    it cannot shadow an unrelated ``/v1/...`` route by construction — but
    the order is still asserted by
    ``tests/test_webui.py::test_web_ui_routes_are_registered_after_v1_routers``
    as a regression guard.
    """
    if not os.path.isdir(web_dir):
        logger.info(
            "Web UI directory %r not found; serving the API only (no static "
            "UI). Set VAULT_WEB_DIR to point at a populated web/ directory "
            "to enable it.",
            web_dir,
        )
        return False

    mounted_anything = False
    index_path = os.path.join(web_dir, "index.html")

    if os.path.isfile(index_path):

        def _serve_index(request: Request) -> Response:
            return FileResponse(index_path, headers={"Cache-Control": _INDEX_CACHE_CONTROL})

        # Real, exact routes — GET AND HEAD, both explicitly. FastAPI's
        # `APIRoute` does NOT auto-add HEAD for a GET-only route the way
        # plain Starlette's `Route` does (measured against this exact
        # FastAPI version — see api/README.md "Web UI static serving" for
        # the empirical note). `FileResponse` already special-cases HEAD
        # itself (headers only, no body, at the ASGI layer), so listing
        # both methods against the same handler is sufficient.
        app.add_api_route("/", _serve_index, methods=["GET", "HEAD"], include_in_schema=False)
        app.add_api_route(
            "/index.html", _serve_index, methods=["GET", "HEAD"], include_in_schema=False
        )
        for view in _SPA_ROUTES:
            app.add_api_route(
                f"/{view}", _serve_index, methods=["GET", "HEAD"], include_in_schema=False
            )
            # A path segment past the view name (e.g. a future per-game
            # deep link like /library/440) belongs to that view too — the
            # client-side router owns everything past the first segment.
            # `{rest:path}` matches zero or more characters including "/",
            # so this also covers the bare `/library/` case.
            app.add_api_route(
                f"/{view}/{{rest:path}}",
                _serve_index,
                methods=["GET", "HEAD"],
                include_in_schema=False,
            )
        mounted_anything = True
    else:
        logger.warning(
            "Web UI directory %r has no index.html; serving its static "
            "assets only, with no app shell to fall back to.",
            web_dir,
        )

    for subdir in ("css", "js"):
        asset_dir = os.path.join(web_dir, subdir)
        if os.path.isdir(asset_dir):
            app.mount(
                f"/{subdir}",
                _AssetStaticFiles(directory=asset_dir, html=False),
                name=f"web-{subdir}",
            )
            mounted_anything = True

    if mounted_anything:
        logger.info("Serving web UI from %s", web_dir)
    return mounted_anything
