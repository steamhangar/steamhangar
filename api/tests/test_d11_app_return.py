"""WP D11 (APP-FIX-LOGIN): the Steam OpenID return page for the Android app.

Pins, each by literal (docs/LEARNINGS.md 4a.6r rule, never the module's own
constants): the public path, the fixed deep-link target (no open redirect),
the byte-for-byte pass-through of Steam's query, the refusal of anything
that is not a plausible Steam answer, HTML escaping, the no-store and
security headers, and that the /v1 auth carve-out did not grow.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api.routers import app_return

# A realistic, synthetic Steam id_res answer as it reaches the page: the
# app's own state first (it is part of return_to, which Steam appends to),
# then Steam's openid.* fields, all percent-encoded the way Steam sends them.
STEAM_QUERY = (
    "state=Zm9vYmFyYmF6cXV4LXRlc3Qtc3RhdGUtMDAx"
    "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
    "&openid.mode=id_res"
    "&openid.op_endpoint=https%3A%2F%2Fsteamcommunity.com%2Fopenid%2Flogin"
    "&openid.claimed_id=https%3A%2F%2Fsteamcommunity.com%2Fopenid%2Fid%2F76561198042117903"
    "&openid.identity=https%3A%2F%2Fsteamcommunity.com%2Fopenid%2Fid%2F76561198042117903"
    "&openid.return_to=http%3A%2F%2F192.0.2.10%3A8080%2Fauth%2Fsteam%2Fapp-return"
    "%3Fstate%3DZm9vYmFyYmF6cXV4LXRlc3Qtc3RhdGUtMDAx"
    "&openid.response_nonce=2026-10-13T10%3A00%3A00Zabc"
    "&openid.assoc_handle=1234567890"
    "&openid.signed=signed%2Cop_endpoint%2Cclaimed_id%2Cidentity%2Creturn_to%2Cresponse_nonce%2Cassoc_handle"
    "&openid.sig=Zm9vYmFy%2Bc2lnPQ%3D%3D"
)

EXPECTED_LINK = "steamvault://auth/openid-return?" + STEAM_QUERY


def _href(link: str) -> str:
    return link.replace("&", "&amp;")


def test_path_and_target_literals() -> None:
    assert app_return.APP_RETURN_PATH == "/auth/steam/app-return"
    assert app_return.APP_LINK == "steamvault://auth/openid-return"


def test_steam_answer_is_handed_to_the_app_unchanged(client: TestClient) -> None:
    response = client.get("/auth/steam/app-return?" + STEAM_QUERY)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # The link the user can tap, and the automatic no-JS hand-off, both
    # carry the query exactly as it arrived (only HTML-escaped).
    assert f'<a href="{_href(EXPECTED_LINK)}">' in body
    assert f'<meta http-equiv="refresh" content="0;url={_href(EXPECTED_LINK)}">' in body
    assert "<script" not in body


def test_no_api_key_is_needed_and_none_is_echoed(client: TestClient) -> None:
    response = client.get("/auth/steam/app-return?" + STEAM_QUERY)
    assert response.status_code == 200
    assert TEST_API_KEY not in response.text


def test_page_is_not_cached_and_carries_the_security_headers(client: TestClient) -> None:
    response = client.get("/auth/steam/app-return?" + STEAM_QUERY)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_head_is_answered(client: TestClient) -> None:
    response = client.head("/auth/steam/app-return?" + STEAM_QUERY)
    assert response.status_code == 200
    assert response.content == b""


def test_post_is_not_allowed(client: TestClient) -> None:
    response = client.post("/auth/steam/app-return?" + STEAM_QUERY)
    assert response.status_code == 405


def test_request_without_a_query_gets_no_link(client: TestClient) -> None:
    response = client.get("/auth/steam/app-return")
    assert response.status_code == 400
    assert "steamvault:" not in response.text
    assert "http-equiv" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_query_without_openid_mode_gets_no_link(client: TestClient) -> None:
    response = client.get("/auth/steam/app-return?state=abc&openid.ns=x")
    assert response.status_code == 400
    assert "steamvault:" not in response.text


def test_oversized_query_gets_no_link(client: TestClient) -> None:
    query = "openid.mode=id_res&pad=" + "a" * (8 * 1024)
    response = client.get("/auth/steam/app-return?" + query)
    assert response.status_code == 400
    assert "steamvault:" not in response.text


def test_query_at_the_length_bound_is_accepted() -> None:
    query = "openid.mode=id_res&pad="
    query += "a" * (8 * 1024 - len(query))
    assert len(query) == 8 * 1024
    assert app_return.deep_link_for(query) == "steamvault://auth/openid-return?" + query
    assert app_return.deep_link_for(query + "a") is None


def test_markup_and_quote_characters_are_refused() -> None:
    for bad in ('"', "'", "<", ">", " ", "\\", "`", "{", "}", "|", "^", "#", "\n", "\t"):
        query = f"openid.mode=id_res&x=a{bad}b"
        assert app_return.deep_link_for(query) is None, repr(bad)


def test_non_ascii_query_is_refused() -> None:
    assert app_return.deep_link_for("openid.mode=id_res&x=é") is None
    # A Unicode digit/letter must not slip through a non-ASCII regex class.
    assert app_return.deep_link_for("openid.mode=id_res&x=٣") is None


def test_openid_mode_must_be_a_parameter_name_not_a_substring() -> None:
    assert app_return.deep_link_for("xopenid.mode=id_res") is None
    assert app_return.deep_link_for("a=openid.mode") is None
    assert app_return.deep_link_for("openid.mode=cancel") == (
        "steamvault://auth/openid-return?openid.mode=cancel"
    )


def test_target_is_fixed_whatever_the_query_says() -> None:
    # No open redirect: the scheme, host and path of the target never come
    # from the request, even when the query tries to name another target.
    for query in (
        "openid.mode=id_res&url=https://attacker.example/",
        "openid.mode=id_res&next=steamhangar://pair?key=x",
        "openid.mode=id_res/../../evil",
        "openid.mode=id_res&@attacker.example",
    ):
        link = app_return.deep_link_for(query)
        assert link is not None
        assert link.startswith("steamvault://auth/openid-return?")
        assert link[len("steamvault://auth/openid-return?"):] == query


def test_escaping_is_applied_where_the_link_is_written() -> None:
    page = app_return._page("t", "", refresh_to='steamvault://auth/openid-return?a="b"&c')
    assert 'content="0;url=steamvault://auth/openid-return?a=&quot;b&quot;&amp;c"' in page


def test_v1_auth_carve_out_did_not_grow(client: TestClient) -> None:
    # The page lives outside /v1 on purpose; the same path under /v1 is not
    # public (pre-auth guard answers 401 without a key).
    response = client.get("/v1/auth/steam/app-return?" + STEAM_QUERY)
    assert response.status_code == 401
