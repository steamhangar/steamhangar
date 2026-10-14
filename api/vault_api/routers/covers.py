"""Stored cover images: GET /v1/covers/{appid}/{signature} (WP D10 COVER-CACHE-1).

**Not behind ``require_api_key``, on purpose, and not unauthenticated
either.** Browsers load covers with ``<img src>``, which cannot send the
``X-Api-Key`` header. The credential is in the path instead: ``signature``
is an HMAC keyed with ``VAULT_API_KEY`` over the appid and the stored file's
SHA-256 (``cover_cache.cover_signature``), and only the key-authenticated
``GET /v1/games[/{appid}]`` hands such URLs out (``cover_cached_url``).
``PreAuthBodyGuard`` skips its header check for exactly this path shape
(``body_guard.is_signed_cover_path``) and this handler checks the signature
instead. No route other than this one may be added to this router.

Every refusal is the same 404 (unknown appid, nothing stored, wrong
signature, file missing or altered), so the answer never tells an unkeyed
caller which apps the vault tracks. The handler only reads files
``cover_cache`` stored for known apps; it never fetches anything.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from vault_api import cover_cache
from vault_api.deps import DbOpener, db_opener

router = APIRouter(tags=["covers"])

_NOT_FOUND = "Not Found"
#: Signed against when nothing is stored, so a miss costs the same HMAC as a hit.
_NO_SHA = "0" * 64


@router.get("/v1/covers/{appid}/{signature}")
def get_cover(
    request: Request,
    appid: str,
    signature: str,
    open_db: DbOpener = Depends(db_opener),
    if_none_match: str | None = Header(default=None),
) -> Response:
    number = cover_cache.parse_appid(appid)
    if number is None or not cover_cache.valid_signature_text(signature):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)

    api_key = request.app.state.settings.vault_api_key
    with open_db() as conn:
        stored = cover_cache.stored_cover(conn, number)
    expected = cover_cache.cover_signature(
        api_key, number, stored.sha256 if stored is not None else _NO_SHA
    )
    if stored is None or not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)

    headers = {
        "ETag": cover_cache.etag_for(stored.sha256),
        "Cache-Control": cover_cache.CACHE_CONTROL,
    }
    if cover_cache.if_none_match_hits(if_none_match, stored.sha256):
        # The client already holds exactly these bytes (the ETag is their hash).
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    cache_dir = request.app.state.cover_file_fetcher.cache_dir
    data = cover_cache.read_verified(cache_dir, stored)
    if data is None:
        # Gone or altered on disk: clear the row so the fetcher downloads it
        # again; the client falls back to the CDN URL meanwhile.
        with open_db() as conn:
            cover_cache.forget(conn, number, stored.sha256)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return Response(content=data, media_type=stored.content_type, headers=headers)
