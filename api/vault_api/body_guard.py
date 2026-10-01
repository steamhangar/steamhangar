"""Pre-routing request guard: auth and a body cap before any body is read.

WP SEC-FIX-4 (S-3). FastAPI reads and parses a JSON body while it resolves a
route's parameters, and the router-level ``require_api_key`` dependency does
not run first: measured on fastapi 0.133 / starlette 1.3, a 40 MB body
without a key cost ~1.7 GB RSS before the 401, and malformed JSON without a
key answered 422 (a pre-auth parser oracle) instead of 401.

This pure-ASGI middleware runs before routing:

1. **Auth first** for ``/v1`` and ``/v1/*`` except exactly ``/v1/health``
   (the one documented unauthenticated route, ``auth.py``): the same
   constant-time comparison as the dependency (``auth.api_key_matches``),
   so a request without a valid key is answered 401 without one body byte
   being read. The dependency stays on every router as defence in depth.
2. **Body cap** for every request: a ``Content-Length`` over
   ``MAX_BODY_BYTES`` is 413 up front; a body without one (chunked) is
   counted as it arrives and answered 413 the moment it passes the cap,
   whatever the app had started to do with it.

The cap is 1 MiB. The largest legitimate body is an agent report at the
10 000-appid bound (``agent_reports.MAX_APPIDS_PER_REPORT``): ~110 KB.
It is a module constant, not an env variable: nothing legitimate comes
within a factor of nine of it.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from vault_api.auth import UNAUTHORIZED_DETAIL, api_key_matches

MAX_BODY_BYTES = 1024 * 1024

_PUBLIC_PATHS = frozenset({"/v1/health"})

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class _BodyTooLarge(Exception):
    """Raised out of the wrapped ``receive`` once the cap is passed."""


def needs_api_key(path: str) -> bool:
    """Every ``/v1`` path except the public health check."""
    return (path == "/v1" or path.startswith("/v1/")) and path not in _PUBLIC_PATHS


async def _send_json(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PreAuthBodyGuard:
    """ASGI middleware; ``expected_key`` is read per request from app state."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        expected_key: Callable[[], str],
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        self.app = app
        self.expected_key = expected_key
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        if needs_api_key(scope.get("path", "")):
            # Starlette decodes header values as latin-1; doing the same keeps
            # this comparison byte-for-byte identical to the dependency's.
            provided = next(
                (v.decode("latin-1") for k, v in headers if k == b"x-api-key"), None
            )
            if not api_key_matches(provided, self.expected_key()):
                await _send_json(send, 401, UNAUTHORIZED_DETAIL)
                return

        for key, value in headers:
            if key == b"content-length":
                text = value.decode("latin-1").strip()
                if not (text.isascii() and text.isdigit()):
                    await _send_json(send, 400, "invalid Content-Length header")
                    return
                if int(text) > self.max_body_bytes:
                    await _send_json(send, 413, self._too_large())
                    return

        received = 0
        overflow = False
        started = False

        async def counting_receive() -> Message:
            nonlocal received, overflow
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    overflow = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if overflow:
                # Whatever the app answers after the overflow (FastAPI turns
                # the exception into a 400) is replaced by the 413 below.
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _BodyTooLarge:
            pass
        except Exception:
            if not overflow:
                raise
        if overflow and not started:
            await _send_json(send, 413, self._too_large())

    def _too_large(self) -> str:
        return f"request body exceeds the {self.max_body_bytes}-byte limit"
