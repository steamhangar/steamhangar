"""Steam OpenID return page for the Android app (WP D11 APP-FIX-LOGIN).

GET/HEAD ``/auth/steam/app-return`` — public, no auth, by design.

Why it exists: Steam's OpenID provider only redirects to an ``http(s)``
``openid.return_to``; the app's own ``steamvault://auth/openid-return`` deep
link is refused with "Invalid return protocol". The app therefore asks Steam
to return to THIS page on its own vault-api, and the page hands the answer
on to the app's deep link.

What the page does, and nothing more:

- It copies the request's raw query string, byte for byte, behind the ONE
  fixed prefix :data:`APP_LINK` (fixed scheme, host and path — no part of
  the target is taken from the request, so this is no open redirect).
- It answers an HTML page that points at that deep link twice: a
  ``<meta http-equiv="refresh">`` (automatic, no JavaScript) and a plain
  link the user can tap if the browser blocks the automatic hand-off.
- It never verifies, stores or logs-on-purpose anything. The app does the
  whole trust decision itself: per-login ``state`` bound into the signed
  ``return_to``, the signed-fields check and ``check_authentication`` with
  steamcommunity.com from the phone (``SteamIdentityRepository``). vault-api
  gains no egress: it does not talk to Steam for this (ADR-0011).

Why outside ``/v1``: the pre-auth guard (``body_guard.py``) requires the
API key on every ``/v1`` path except ``/v1/health``; the browser that lands
here has no key and must never be given one. Living beside the web UI's
public static files keeps the ``/v1`` auth carve-out at exactly one path.

The query is checked before it is echoed: a bounded length, and only
the ASCII characters a percent-encoded query consists of — no quotes, angle
brackets, spaces or backslashes — and it must carry an ``openid.mode``
parameter. Anything else gets a 400 page without a link. The checks are
belt and braces: the value is also HTML-escaped wherever it is written.
"""

from __future__ import annotations

import html
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["steam-app-return"])

#: The path Steam returns to. The app builds ``openid.return_to`` from the
#: vault base URL plus this path (``SteamOpenIdConfig.VAULT_RETURN_PATH``);
#: both sides pin the literal.
APP_RETURN_PATH = "/auth/steam/app-return"

#: The app's deep link (``AndroidManifest.xml`` intent filter, scheme
#: ``steamvault``, host ``auth``, path ``/openid-return``). Fixed: the
#: request decides only the query that follows the ``?``.
APP_LINK = "steamvault://auth/openid-return"

#: A real Steam answer is about 1 KB (eight ``openid.*`` fields plus the
#: app's ``state``); 8 KiB leaves ample room and keeps a junk query from
#: being echoed into a page at any size.
MAX_QUERY_LENGTH = 8 * 1024

# RFC 3986 query characters minus the apostrophe: unreserved, percent
# escapes, and the sub-delims/separators a form-encoded query uses.
# Spelled out as explicit ASCII ranges (no \w or \d, which match
# Unicode); fullmatch in the caller.
_QUERY_CHARS = re.compile(r"[A-Za-z0-9\-._~%!$&()*+,;=:@/?]+")

_NO_STORE = {"Cache-Control": "no-store"}

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
{refresh}<title>{title}</title>
</head>
<body>
<main>
<h1>{title}</h1>
{body}
</main>
</body>
</html>
"""


def deep_link_for(raw_query: str) -> str | None:
    """The app deep link for ``raw_query``, or ``None`` if it is refused.

    ``raw_query`` is the undecoded query string as it arrived (no leading
    ``?``). It is passed through unchanged; this function only decides
    whether it is acceptable.
    """
    if not raw_query or len(raw_query) > MAX_QUERY_LENGTH:
        return None
    # The character class is explicit ASCII, so it also refuses every
    # non-ASCII character; no separate isascii() layer that no test could
    # tell apart from this one.
    if _QUERY_CHARS.fullmatch(raw_query) is None:
        return None
    names = {pair.partition("=")[0] for pair in raw_query.split("&")}
    if "openid.mode" not in names:
        return None
    return f"{APP_LINK}?{raw_query}"


def _page(title: str, body: str, refresh_to: str | None = None) -> str:
    refresh = ""
    if refresh_to is not None:
        refresh = (
            '<meta http-equiv="refresh" content="0;url='
            + html.escape(refresh_to, quote=True)
            + '">\n'
        )
    return _PAGE.format(refresh=refresh, title=html.escape(title), body=body)


@router.api_route(APP_RETURN_PATH, methods=["GET", "HEAD"], include_in_schema=False)
def steam_app_return(request: Request) -> HTMLResponse:
    """Hand Steam's OpenID answer on to the app's deep link."""
    raw_query = request.scope.get("query_string", b"").decode("latin-1")
    link = deep_link_for(raw_query)
    if link is None:
        body = (
            "<p>This address only forwards a Steam sign-in answer to the "
            "SteamHangar app, and this request does not carry one.</p>\n"
            "<p>Start the sign-in again from the app.</p>"
        )
        return HTMLResponse(
            _page("Not a Steam sign-in answer", body),
            status_code=400,
            headers=_NO_STORE,
        )
    escaped = html.escape(link, quote=True)
    body = (
        f'<p><a href="{escaped}">Open SteamHangar</a> to finish signing in.</p>\n'
        "<p>If the app does not open by itself, tap the link. The app checks "
        "the answer with Steam before it signs you in.</p>"
    )
    return HTMLResponse(
        _page("Back to SteamHangar", body, refresh_to=link),
        headers=_NO_STORE,
    )
