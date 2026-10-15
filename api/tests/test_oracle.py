"""The manifest oracle (``vault_api/oracle.py``, WP 3.9).

**No test in this file touches the network.** Every response is a *recorded
fixture*: a synthetic document built by ``app_info`` below, modeled on the
structure ``api.steamcmd.net``'s ``/v1/info/{appid}`` endpoint returns (which
is in turn a JSON rendering of Steam's PICS app info). Synthetic on purpose —
``docs/LEARNINGS.md``: fixtures are modeled on real structure and never
contain anyone's real data — and injected through ``refresh_app``'s ``fetch``
parameter, so the HTTP path is exercised only by its own guards.

The three properties these tests exist to pin, in order of how much damage
getting them wrong would do:

1. **Off by default.** ``Settings.from_env()`` with a clean environment yields
   an oracle that is off, and every read path answers "nothing" in that state.
   Flip the default in ``config.py`` and
   ``test_the_oracle_is_off_by_default`` dies.
2. **Additive only.** Oracle data can grow a GC keep set and nothing else.
   The set-relation proof lives next to the planner in
   ``tests/test_gc.py::test_the_oracle_can_only_shrink_the_orphan_set``; here
   the pin is that a passworded branch never reaches the database at all.
3. **Fail-soft.** Every hostile document below produces either a clean
   ``OracleError`` or a partial-but-valid record — never an exception the
   caller was not told to expect, and never a stored value that skipped
   validation.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import oracle
from vault_api.config import (
    MANIFEST_ORACLE_OFF,
    MANIFEST_ORACLE_STEAMCMD_API,
    Settings,
)
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

# --------------------------------------------------------------------------
# Recorded fixtures
# --------------------------------------------------------------------------

#: A full, realistic answer for one app: two depots, a public branch, an open
#: beta branch and a password-protected one. The shapes here (string ids as
#: object keys, ``manifests.<branch>.gid`` as a decimal STRING, ``pwdrequired``
#: as the string ``"1"``, ``branches`` sitting inside ``depots``) are the ones
#: the real endpoint uses.
RECORDED_TF2 = {
    "status": "success",
    "data": {
        "440": {
            "appid": "440",
            "common": {"name": "Team Fortress 2", "type": "Game"},
            "depots": {
                "441": {
                    "name": "Team Fortress 2 Content",
                    "manifests": {
                        "public": {
                            "gid": "900",
                            "size": "12345678",
                            "download": "4321000",
                        },
                        "beta": {
                            "gid": "901",
                            "size": "12445678",
                            "download": "4421000",
                        },
                        "private_test": {"gid": "902", "size": "1", "download": "1"},
                    },
                },
                "442": {
                    "name": "Team Fortress 2 Materials",
                    "manifests": {"public": {"gid": "910", "size": "22", "download": "2"}},
                },
                "branches": {
                    "public": {"buildid": "17400000", "timeupdated": "1754000000"},
                    "beta": {
                        "buildid": "17410000",
                        "description": "Public beta",
                        "timeupdated": "1754100000",
                    },
                    "private_test": {
                        "buildid": "17420000",
                        "description": "Internal",
                        "pwdrequired": "1",
                        "timeupdated": "1754200000",
                    },
                },
                "baselanguages": "english",
                "hasdepotsindlc": "1",
            },
        }
    },
}


def encode(document: Any) -> bytes:
    """A recorded fixture as the bytes a fetcher would hand back."""
    return json.dumps(document).encode("utf-8")


def app_info(
    appid: int = 440,
    *,
    depots: dict[str, Any] | None = None,
    branches: dict[str, Any] | None = None,
    include_appid: bool = True,
) -> bytes:
    """Build a minimal but structurally faithful answer.

    ``depots``/``branches`` are spliced into the same places the real document
    puts them, so a test that varies one field varies nothing else.
    """
    app: dict[str, Any] = {}
    if include_appid:
        app["appid"] = str(appid)
    inner: dict[str, Any] = dict(depots or {})
    if branches is not None:
        inner["branches"] = branches
    app["depots"] = inner
    return encode({"status": "success", "data": {str(appid): app}})


def one_depot(
    manifests: dict[str, Any], *, depotid: str = "441"
) -> dict[str, Any]:
    return {depotid: {"name": "d", "manifests": manifests}}


OPEN_PUBLIC_AND_BETA = {
    "public": {"buildid": "100"},
    "beta": {"buildid": "101"},
}


def enabled_settings(tmp_path, **overrides: Any) -> Settings:
    """Settings with the oracle ON. Constructed directly (never from the
    environment) so enabling it in a test cannot leak into another one."""
    values: dict[str, Any] = dict(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        manifest_oracle=MANIFEST_ORACLE_STEAMCMD_API,
    )
    values.update(overrides)
    return Settings(**values)


def off_settings(tmp_path) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )


@pytest.fixture
def conn(tmp_path):
    """An initialised schema-v8 database."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def fetcher(payload: bytes):
    """A ``Fetcher`` that always answers with one recorded body."""

    def fetch(url: str) -> bytes:
        return payload

    return fetch


def failing_fetcher(message: str = "boom"):
    def fetch(url: str) -> bytes:
        raise oracle.OracleError(message)

    return fetch


# ==========================================================================
# 1. On by default since WP UPD-1 (user decision 2026-10-13, superseding
#    ADR-0006 decision 4's "default off"); opt-out and the egress rule are
#    pinned in tests/test_upd_1_oracle_egress.py
# ==========================================================================


def test_the_oracle_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test that dies if the operator-facing default in ``config.py`` is
    flipped back: unset means the default oracle, ``off`` is the opt-out."""
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.delenv("VAULT_MANIFEST_ORACLE", raising=False)
    monkeypatch.delenv("VAULT_MANIFEST_ORACLE_URL", raising=False)

    settings = Settings.from_env()
    assert settings.manifest_oracle == MANIFEST_ORACLE_STEAMCMD_API
    assert settings.manifest_oracle_enabled is True

    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "off")
    assert Settings.from_env().manifest_oracle == MANIFEST_ORACLE_OFF
    # A directly constructed Settings (every other test fixture in this suite)
    # stays OFF on purpose: no code path that bypasses from_env may reach the
    # network by accident (config.py, MANIFEST_ORACLE_OFF).
    assert Settings(
        vault_api_key="k", db_path=":memory:", cache_root="c", log_level="INFO"
    ).manifest_oracle_enabled is False


def test_a_blank_oracle_value_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "   ")
    assert Settings.from_env().manifest_oracle_enabled is True


def test_the_oracle_can_be_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "  STEAMCMD_API ")
    # An allowlist entry for the default host is harmless (no longer needed
    # since WP UPD-1, see tests/test_upd_1_oracle_egress.py).
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", "api.steamcmd.net")
    settings = Settings.from_env()
    assert settings.manifest_oracle == MANIFEST_ORACLE_STEAMCMD_API
    assert settings.manifest_oracle_enabled is True


def test_an_unknown_oracle_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo must not look like "off": leaving the variable unset is how an
    operator asks for off, so a non-empty value is an explicit request for a
    feature and gets a loud answer when it does not exist. (The oracle's own
    runtime failures stay soft — that is a different thing.)"""
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE", "steamcmd-api")
    with pytest.raises(RuntimeError, match="not a supported manifest oracle"):
        Settings.from_env()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "api.steamcmd.net"])
def test_a_non_http_oracle_url_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE_URL", url)
    with pytest.raises(RuntimeError, match="http"):
        Settings.from_env()


def test_the_oracle_url_is_validated_even_while_the_oracle_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same rule as the VAULT_SCHEDULE_* numbers: a typo surfaces the day it is
    made, not the day the feature is switched on."""
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.delenv("VAULT_MANIFEST_ORACLE", raising=False)
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE_URL", "file:///etc/passwd")
    with pytest.raises(RuntimeError):
        Settings.from_env()


def test_a_bad_oracle_timeout_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_MANIFEST_ORACLE_TIMEOUT", "0")
    with pytest.raises(RuntimeError, match="must be > 0"):
        Settings.from_env()


# ==========================================================================
# 2. Validators
# ==========================================================================


@pytest.mark.parametrize("name", ["public", "beta", "pre-release", "dev_2.0", "x" * 64])
def test_valid_branch_name_accepts_real_shapes(name: str) -> None:
    assert oracle.valid_branch_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "x" * 65,
        ".",
        "..",
        "../../etc",
        "beta/../public",
        "beta\\public",
        "beta branch",
        "bêta",
        "beta\x00",
        "beta\n",
        123,
        True,
        None,
    ],
)
def test_valid_branch_name_rejects_everything_else(name: object) -> None:
    assert oracle.valid_branch_name(name) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("901", "901"),
        ({"gid": "901", "size": "1"}, "901"),
        ({"gid": "18446744073709551615"}, "18446744073709551615"),
    ],
)
def test_extract_gid_accepts_both_spellings(raw: object, expected: str) -> None:
    assert oracle._extract_gid(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        901,  # a JSON number would already have lost u64 precision
        {"gid": 901},
        {"gid": " 901 "},
        {"gid": "+901"},
        {"gid": "9_01"},
        {"gid": "0901"},
        {"gid": "0"},
        {"gid": "٩٠١"},  # Arabic-Indic digits: isdigit() is True, isascii() is not
        {"gid": "../../etc"},
        {"gid": "9" * 21},
        {"gid": True},
        {"size": "1"},
        [],
        None,
    ],
)
def test_extract_gid_rejects_everything_else(raw: object) -> None:
    assert oracle._extract_gid(raw) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ({}, False),
        ({"pwdrequired": "0"}, False),
        ({"pwdrequired": ""}, False),
        ({"pwdrequired": 0}, False),
        ({"pwdrequired": False}, False),
        ({"pwdrequired": "1"}, True),
        ({"pwdrequired": 1}, True),
        ({"pwdrequired": True}, True),
        ({"pwdrequired": "yes"}, True),
        ({"pwdrequired": []}, True),
    ],
)
def test_password_state_is_never_guessed_optimistically(
    value: dict, expected: bool
) -> None:
    """Anything not recognisably "no" counts as "yes": mistaking a private
    branch for an open one would store a gid this project has no business
    storing."""
    assert oracle._is_password_required(value) is expected


def test_app_info_url_builds_from_a_positive_int_only() -> None:
    assert oracle.app_info_url("https://api.steamcmd.net/v1/info", 440) == (
        "https://api.steamcmd.net/v1/info/440"
    )
    assert oracle.app_info_url("https://x/v1/info/", 440) == "https://x/v1/info/440"
    with pytest.raises(oracle.OracleError):
        oracle.app_info_url("https://x/v1/info", 0)


@pytest.mark.real_http_fetch
def test_http_fetch_refuses_a_non_http_scheme() -> None:
    """The guard that means a misconfigured URL can never make vault-api read
    a local file. (No network is used: the scheme check runs first.)"""
    with pytest.raises(oracle.OracleError, match="only http/https"):
        oracle.http_fetch("file:///etc/passwd")


def test_http_fetch_never_follows_a_redirect() -> None:
    """Structural: the opener is built with a handler that turns a redirect
    into an error, so the request's destination stays the URL the operator
    configured."""
    assert any(
        isinstance(handler, oracle._RefuseRedirects)
        for handler in oracle._OPENER.handlers
    )
    assert (
        oracle._RefuseRedirects().redirect_request(
            None, None, 302, "Found", {}, "https://elsewhere.example/"
        )
        is None
    )


# ==========================================================================
# 3. Parsing a recorded answer
# ==========================================================================


def test_parses_a_recorded_answer() -> None:
    info = oracle.parse_app_info(440, encode(RECORDED_TF2))

    assert info.appid == 440
    assert info.buildid == "17400000"
    assert info.open_branches == ("beta", "public")
    assert info.skipped_password_branches == 1
    assert info.depotids == (441, 442)
    assert [(bm.depotid, bm.branch, bm.manifestid) for bm in info.branch_manifests] == [
        (441, "beta", "901"),
        (441, "public", "900"),
        (442, "public", "910"),
    ]


def test_a_passworded_branchs_gid_is_never_even_parsed() -> None:
    """Decision B covers OPEN branches only: a passworded branch's manifest is
    encrypted and uncoverable, so its gid is dropped here rather than stored
    with a flag some future query might forget to filter on."""
    info = oracle.parse_app_info(440, encode(RECORDED_TF2))
    assert "902" not in {bm.manifestid for bm in info.branch_manifests}
    assert "private_test" not in info.open_branches


def test_the_bare_string_manifest_spelling_is_accepted() -> None:
    """The older PICS rendering writes ``manifests.public`` as a plain gid
    string instead of an object."""
    payload = app_info(
        depots=one_depot({"public": "900", "beta": "901"}),
        branches=OPEN_PUBLIC_AND_BETA,
    )
    info = oracle.parse_app_info(440, payload)
    assert [(bm.branch, bm.manifestid) for bm in info.branch_manifests] == [
        ("beta", "901"),
        ("public", "900"),
    ]


def test_a_branch_the_branches_object_never_declared_is_skipped() -> None:
    """Password state unknown ⇒ treated as protected. A ``manifests`` entry
    with no matching ``branches`` entry tells us nothing about whether that
    branch is open."""
    payload = app_info(
        depots=one_depot({"public": "900", "ghost": "901"}),
        branches={"public": {"buildid": "1"}},
    )
    info = oracle.parse_app_info(440, payload)
    assert [bm.branch for bm in info.branch_manifests] == ["public"]


def test_no_branches_object_means_no_branch_is_open() -> None:
    """Fail-soft *and* fail-closed at once: with the branch list unreadable the
    oracle knows nothing about any branch's password state, so nothing is
    recorded and GC is back at its pre-oracle behaviour."""
    payload = app_info(depots=one_depot({"public": "900", "beta": "901"}))
    info = oracle.parse_app_info(440, payload)
    assert info.branch_manifests == ()
    assert info.open_branches == ()
    assert any("branches" in w for w in info.warnings)


def test_a_depot_without_manifests_is_not_a_warning() -> None:
    """DLC-only and shared-install depots legitimately carry no manifests."""
    payload = app_info(
        depots={"441": {"name": "dlc"}, "442": {"name": "d", "manifests": {"public": "1"}}},
        branches={"public": {"buildid": "1"}},
    )
    info = oracle.parse_app_info(440, payload)
    assert info.depotids == (442,)
    assert info.warnings == ()


def test_sibling_keys_of_depots_are_not_mistaken_for_depots() -> None:
    payload = app_info(
        depots={
            "441": {"manifests": {"public": "900"}},
            "baselanguages": "english",
            "hasdepotsindlc": "1",
            "overridescddb": "0",
            "some_future_key": {"anything": 1},
        },
        branches={"public": {"buildid": "1"}},
    )
    info = oracle.parse_app_info(440, payload)
    assert info.depotids == (441,)


# ==========================================================================
# 4. Poisoned and hostile answers
# ==========================================================================


@pytest.mark.parametrize(
    "payload,match",
    [
        (b"", "not valid JSON"),
        (b"<html>503 Service Unavailable</html>", "not valid JSON"),
        (b"[1, 2, 3]", "not a JSON object"),
        (b'"a string"', "not a JSON object"),
        (b"\xff\xfe\x00garbage", "not valid UTF-8"),
        (encode({"status": "error", "data": {}}), "status 'error'"),
        (encode({"status": "success"}), "no 'data' object"),
        (encode({"status": "success", "data": []}), "no 'data' object"),
        (encode({"status": "success", "data": {}}), "no data for app 440"),
        (encode({"status": "success", "data": {"440": "nope"}}), "is not an object"),
    ],
)
def test_hostile_documents_raise_exactly_one_exception_type(
    payload: bytes, match: str
) -> None:
    with pytest.raises(oracle.OracleError, match=match):
        oracle.parse_app_info(440, payload)


def test_an_answer_about_another_app_is_refused() -> None:
    """The same corruption cross-check the manifest parsers apply between a
    filename and its payload: attributing another app's depots to this one is
    how a keep set ends up describing the wrong game."""
    document = json.loads(encode(RECORDED_TF2))
    document["data"]["440"]["appid"] = "730"
    with pytest.raises(oracle.OracleError, match="claims appid"):
        oracle.parse_app_info(440, encode(document))


def test_deeply_nested_json_becomes_an_oracle_error_not_a_recursion_error() -> None:
    """docs/LEARNINGS.md (WP 2.1): a ``RecursionError`` escaping a parser's
    documented exception contract crashes the caller on an exception it was
    never told to catch. CPython's JSON scanner recurses per nesting level, so
    this is a real reachable path for a hostile body — it is caught by name and
    converted."""
    payload = b"[" * 200_000
    with pytest.raises(oracle.OracleError, match="nests too deeply"):
        oracle.parse_app_info(440, payload)


def test_an_oversized_body_is_refused_before_it_is_parsed() -> None:
    payload = b"x" * (oracle.MAX_RESPONSE_BYTES + 1)
    with pytest.raises(oracle.OracleError, match="exceeds"):
        oracle.parse_app_info(440, payload)


@pytest.mark.parametrize(
    "depot_key", ["../../etc", "0", "-1", " 441 ", "441_0", "٤٤١", "not-a-depot"]
)
def test_a_poisoned_depot_key_is_skipped(depot_key: str) -> None:
    payload = app_info(
        depots={depot_key: {"manifests": {"public": "900"}}},
        branches={"public": {"buildid": "1"}},
    )
    info = oracle.parse_app_info(440, payload)
    assert info.branch_manifests == ()


def test_a_poisoned_branch_name_is_skipped_with_a_warning() -> None:
    payload = app_info(
        depots=one_depot({"public": "900", "../../etc": "901"}),
        branches={"public": {"buildid": "1"}, "../../etc": {"buildid": "2"}},
    )
    info = oracle.parse_app_info(440, payload)
    assert [bm.branch for bm in info.branch_manifests] == ["public"]
    assert any("unusable branch name" in w for w in info.warnings)


def test_a_poisoned_gid_is_skipped_and_the_rest_survives() -> None:
    """Partial answers stay useful: one unreadable branch must not throw away
    the protection the readable ones provide."""
    payload = app_info(
        depots=one_depot({"public": "900", "beta": {"gid": "../../etc"}}),
        branches=OPEN_PUBLIC_AND_BETA,
    )
    info = oracle.parse_app_info(440, payload)
    assert [(bm.branch, bm.manifestid) for bm in info.branch_manifests] == [
        ("public", "900")
    ]
    assert any("no usable manifest gid" in w for w in info.warnings)


def test_a_poisoned_buildid_becomes_null_not_a_stored_string() -> None:
    payload = app_info(
        depots=one_depot({"public": "900"}),
        branches={"public": {"buildid": "../../etc"}},
    )
    assert oracle.parse_app_info(440, payload).buildid is None


def test_the_depot_branch_and_row_counts_are_bounded() -> None:
    depots = {
        str(1000 + i): {"manifests": {"public": str(9000 + i)}}
        for i in range(oracle.MAX_DEPOTS + 25)
    }
    payload = app_info(depots=depots, branches={"public": {"buildid": "1"}})
    info = oracle.parse_app_info(440, payload)
    assert len(info.depotids) <= oracle.MAX_DEPOTS
    assert any("more than" in w for w in info.warnings)


def test_warnings_are_bounded() -> None:
    payload = app_info(
        depots=one_depot({f"branch{i}": {"gid": "nope"} for i in range(40)}),
        branches={f"branch{i}": {"buildid": "1"} for i in range(40)},
    )
    info = oracle.parse_app_info(440, payload)
    assert len(info.warnings) <= oracle.MAX_WARNINGS + 1
    assert info.warnings[-1] == "... further warnings suppressed"


# ==========================================================================
# 5. Storage — its own tables, provenance-tagged, snapshot semantics
# ==========================================================================


def test_storing_never_touches_depot_manifests(conn: sqlite3.Connection) -> None:
    """Rule 3 of the module docstring, asserted rather than trusted: a
    third-party claim must never become indistinguishable from a manifest
    vault-api parsed itself (ADR-0006 decision 3)."""
    info = oracle.parse_app_info(440, encode(RECORDED_TF2))
    oracle.store_app_info(conn, info, checked_at="2026-08-09T10:00:00Z")

    assert conn.execute("SELECT COUNT(*) FROM depot_manifests").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM oracle_branch_manifests").fetchone()[0] == 3


def test_every_stored_row_carries_its_provenance(conn: sqlite3.Connection) -> None:
    info = oracle.parse_app_info(440, encode(RECORDED_TF2))
    oracle.store_app_info(conn, info, checked_at="2026-08-09T10:00:00Z")

    sources = {
        row[0]
        for row in conn.execute("SELECT DISTINCT source FROM oracle_branch_manifests")
    }
    assert sources == {oracle.SOURCE_STEAMCMD_API}
    state = conn.execute("SELECT * FROM oracle_app_state WHERE appid = 440").fetchone()
    assert state["source"] == oracle.SOURCE_STEAMCMD_API
    assert state["checked_at"] == "2026-08-09T10:00:00Z"
    assert state["buildid"] == "17400000"
    assert (state["depot_count"], state["branch_count"]) == (2, 2)


def test_a_refresh_replaces_the_previous_snapshot(conn: sqlite3.Connection) -> None:
    """Snapshot semantics, not upsert: a branch that disappeared upstream must
    stop protecting chunks rather than linger forever as a row nothing
    refreshes."""
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )
    smaller = app_info(
        depots=one_depot({"public": "999"}), branches={"public": {"buildid": "2"}}
    )
    oracle.store_app_info(
        conn, oracle.parse_app_info(440, smaller), checked_at="2026-08-09T11:00:00Z"
    )

    rows = conn.execute(
        "SELECT depotid, branch, manifestid FROM oracle_branch_manifests"
    ).fetchall()
    assert [tuple(row) for row in rows] == [(441, "public", "999")]


def test_clear_app_forgets_everything(conn: sqlite3.Connection) -> None:
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )
    oracle.clear_app(conn, 440)

    assert conn.execute("SELECT COUNT(*) FROM oracle_branch_manifests").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM oracle_app_state").fetchone()[0] == 0


# ==========================================================================
# 6. refresh_app — never raises, never half-writes
# ==========================================================================


def test_refresh_does_nothing_at_all_while_the_oracle_is_off(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """The whole point of the default: no request is even attempted. The
    fetcher below would fail the test loudly if it were called."""

    def must_not_be_called(url: str) -> bytes:  # pragma: no cover - asserted below
        raise AssertionError(f"the oracle is off but something fetched {url}")

    result = oracle.refresh_app(
        conn, 440, settings=off_settings(tmp_path), fetch=must_not_be_called
    )

    assert result.enabled is False
    assert result.ok is False
    assert result.error == ""
    assert conn.execute("SELECT COUNT(*) FROM oracle_app_state").fetchone()[0] == 0


def test_refresh_stores_a_good_answer(conn: sqlite3.Connection, tmp_path) -> None:
    result = oracle.refresh_app(
        conn,
        440,
        settings=enabled_settings(tmp_path),
        fetch=fetcher(encode(RECORDED_TF2)),
    )

    assert (result.enabled, result.ok) == (True, True)
    assert result.depot_count == 2
    assert result.branch_manifest_count == 3
    assert result.open_branches == ("beta", "public")
    assert result.skipped_password_branches == 1
    assert result.checked_at is not None


def test_a_failing_oracle_is_reported_not_raised(
    conn: sqlite3.Connection, tmp_path
) -> None:
    result = oracle.refresh_app(
        conn, 440, settings=enabled_settings(tmp_path), fetch=failing_fetcher("down")
    )
    assert (result.enabled, result.ok) == (True, False)
    assert "down" in result.error


def test_a_failed_refresh_leaves_the_previous_snapshot_intact(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """A validated-but-old snapshot is strictly better than none: it can only
    add keep-set protection, and ``checked_at`` says how old it is."""
    settings = enabled_settings(tmp_path)
    oracle.refresh_app(conn, 440, settings=settings, fetch=fetcher(encode(RECORDED_TF2)))
    oracle.refresh_app(conn, 440, settings=settings, fetch=failing_fetcher())

    assert conn.execute("SELECT COUNT(*) FROM oracle_branch_manifests").fetchone()[0] == 3


def test_a_degraded_but_parseable_answer_replaces_the_snapshot(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """The distinction the README spells out, pinned: a failed *fetch* keeps
    the old snapshot (above), but an answer that PARSES and simply describes no
    open branches is a successful refresh saying "nothing" — and snapshot
    semantics then withdraw the protection. That is the same mechanism that
    stops a branch deleted upstream from protecting chunks forever."""
    settings = enabled_settings(tmp_path)
    oracle.refresh_app(conn, 440, settings=settings, fetch=fetcher(encode(RECORDED_TF2)))

    degraded = encode({"status": "success", "data": {"440": {"appid": "440"}}})
    result = oracle.refresh_app(conn, 440, settings=settings, fetch=fetcher(degraded))

    assert result.ok is True
    assert result.depot_count == 0
    assert conn.execute("SELECT COUNT(*) FROM oracle_branch_manifests").fetchone()[0] == 0


def test_depot_count_counts_depots_with_an_open_branch_manifest(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """An app whose every branch is password protected records depot_count 0 —
    correctly, since nothing about its depots was stored."""
    payload = app_info(
        depots=one_depot({"private_test": "902"}),
        branches={"private_test": {"buildid": "1", "pwdrequired": "1"}},
    )
    result = oracle.refresh_app(
        conn, 440, settings=enabled_settings(tmp_path), fetch=fetcher(payload)
    )

    assert (result.ok, result.depot_count, result.branch_manifest_count) == (True, 0, 0)
    assert result.skipped_password_branches == 1
    state = conn.execute("SELECT depot_count FROM oracle_app_state").fetchone()
    assert state["depot_count"] == 0


def test_a_fetcher_that_raises_something_unexpected_is_still_contained(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """Rule 2 has no exceptions: even a bug (or a future fetcher raising its
    own type) must not escape into a request handler."""

    def exploding(url: str) -> bytes:
        raise ZeroDivisionError("surprise")

    result = oracle.refresh_app(
        conn, 440, settings=enabled_settings(tmp_path), fetch=exploding
    )
    assert result.ok is False
    assert "surprise" in result.error


# ==========================================================================
# 7. The GC keep-set contribution (ADR-0007 decision B, database side)
# ==========================================================================


def _map(conn: sqlite3.Connection, depotid: int, appid: int) -> None:
    from vault_api.mapping import upsert_mapping

    upsert_mapping(conn, depotid=depotid, appid=appid, name=None)


def test_keepset_is_empty_while_the_oracle_is_off(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """Even with rows already in the table — disabling the oracle must take
    its influence on the deletion path away immediately, not "after the next
    refresh"."""
    _map(conn, 441, 440)
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )

    assert oracle.gc_keepset_gids(conn, 440, settings=off_settings(tmp_path)) == {}


def test_keepset_carries_beta_gids_but_never_the_public_one(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """Public reaches the keep set through vault-api's OWN depot_manifests
    record; decision B is specifically about beta branches."""
    _map(conn, 441, 440)
    _map(conn, 442, 440)
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )

    assert oracle.gc_keepset_gids(conn, 440, settings=enabled_settings(tmp_path)) == {
        441: ["901"]
    }


def test_keepset_covers_a_shared_depot_recorded_under_another_app(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """A shared depot's beta chunks belong to the DEPOT, not to whichever app
    happened to be refreshed — same scoping rule as ADR-0007's keep-set
    union."""
    _map(conn, 441, 440)
    _map(conn, 441, 730)
    payload = app_info(
        730, depots=one_depot({"beta": "777"}), branches={"beta": {"buildid": "1"}}
    )
    oracle.store_app_info(
        conn, oracle.parse_app_info(730, payload), checked_at="2026-08-09T10:00:00Z"
    )

    assert oracle.gc_keepset_gids(conn, 440, settings=enabled_settings(tmp_path)) == {
        441: ["777"]
    }


def test_keepset_skips_a_depot_that_is_not_mapped_to_this_app(
    conn: sqlite3.Connection, tmp_path
) -> None:
    _map(conn, 999, 440)
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )
    assert oracle.gc_keepset_gids(conn, 440, settings=enabled_settings(tmp_path)) == {}


def test_a_broken_keepset_read_never_reaches_the_gc_job(tmp_path) -> None:
    """``gc_keepset_gids`` is called from inside ``run_gc_job``'s try block,
    where anything that escapes ends the GC job as 'error'. It therefore
    catches ``Exception``, not just ``sqlite3.Error``: "the optional oracle had
    a problem" must never become "garbage collection failed"."""

    class Exploding:
        def execute(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("not a sqlite3.Error")

    assert oracle.gc_keepset_gids(
        Exploding(), 440, settings=enabled_settings(tmp_path)
    ) == {}


def test_keepset_revalidates_hand_edited_rows(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """Only validated values are ever written — but the database is a file an
    operator can edit, and this value ends up in a filename."""
    _map(conn, 441, 440)
    conn.executemany(
        "INSERT INTO oracle_branch_manifests "
        "(appid, depotid, branch, manifestid, recorded_at, source) VALUES (?,?,?,?,?,?)",
        [
            (440, 441, "beta", "../../etc", "t", "x"),
            (440, 441, "beta2", " 901 ", "t", "x"),
            (440, "../../etc", "beta3", "901", "t", "x"),
            (440, 441, "beta4", "902", "t", "x"),
        ],
    )
    conn.commit()

    assert oracle.gc_keepset_gids(conn, 440, settings=enabled_settings(tmp_path)) == {
        441: ["902"]
    }


# ==========================================================================
# 8. The pre-emptive stale badge
# ==========================================================================


def _record_manifest(conn: sqlite3.Connection, *, depotid: int, manifestid: str) -> None:
    from vault_api.depot_manifests import upsert_depot_manifest

    upsert_depot_manifest(
        conn,
        appid=440,
        containing_appid=None,
        depotid=depotid,
        manifestid=manifestid,
        chunk_count=1,
        total_bytes=1,
        recorded_at="2026-08-09T09:00:00Z",
        source="steamprefill_bin",
    )


def _view(conn: sqlite3.Connection, tmp_path) -> oracle.AppOracleView:
    return oracle.app_view(conn, 440, settings=enabled_settings(tmp_path))


def test_a_newer_public_gid_makes_the_app_stale(
    conn: sqlite3.Connection, tmp_path
) -> None:
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )
    _record_manifest(conn, depotid=441, manifestid="899")  # an older manifest
    _record_manifest(conn, depotid=442, manifestid="910")

    view = _view(conn, tmp_path)
    assert view.verdict == oracle.VERDICT_STALE
    by_depot = {d.depotid: d for d in view.depots}
    assert by_depot[441].verdict == oracle.VERDICT_STALE
    assert by_depot[441].beta_branches == ("beta",)
    assert by_depot[442].verdict == oracle.VERDICT_CURRENT


def test_matching_gids_read_as_current(conn: sqlite3.Connection, tmp_path) -> None:
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )
    _record_manifest(conn, depotid=441, manifestid="900")
    _record_manifest(conn, depotid=442, manifestid="910")

    assert _view(conn, tmp_path).verdict == oracle.VERDICT_CURRENT


def test_a_never_cached_game_gets_depot_info_not_a_verdict(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """The second thing this feature exists for: an app nothing has ever
    prefilled still has a depot list and gid to show."""
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )

    view = _view(conn, tmp_path)
    assert view.verdict == oracle.VERDICT_UNKNOWN
    assert [d.verdict for d in view.depots] == [oracle.VERDICT_NOT_CACHED] * 2
    assert [d.oracle_manifestid for d in view.depots] == ["900", "910"]
    assert [d.recorded_manifestid for d in view.depots] == [None, None]


def test_an_unknown_depot_never_softens_a_stale_verdict(
    conn: sqlite3.Connection, tmp_path
) -> None:
    """One stale depot is enough. A depot the oracle knows nothing about must
    not turn a real "stale" into a comfortable "current"."""
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )
    _record_manifest(conn, depotid=441, manifestid="899")
    _record_manifest(conn, depotid=442, manifestid="910")
    _record_manifest(conn, depotid=999, manifestid="1")

    assert _view(conn, tmp_path).verdict == oracle.VERDICT_STALE


def test_the_view_is_empty_and_disabled_while_the_oracle_is_off(
    conn: sqlite3.Connection, tmp_path
) -> None:
    oracle.store_app_info(
        conn,
        oracle.parse_app_info(440, encode(RECORDED_TF2)),
        checked_at="2026-08-09T10:00:00Z",
    )
    view = oracle.app_view(conn, 440, settings=off_settings(tmp_path))

    assert view.enabled is False
    assert view.depots == ()
    assert view.verdict == oracle.VERDICT_UNKNOWN


def test_an_app_nobody_asked_about_is_unknown_not_an_error(
    conn: sqlite3.Connection, tmp_path
) -> None:
    view = _view(conn, tmp_path)
    assert view.known is False
    assert view.verdict == oracle.VERDICT_UNKNOWN
    assert view.depots == ()


# ==========================================================================
# 9. The HTTP surface
# ==========================================================================


@pytest.fixture
def off_client(tmp_path) -> TestClient:
    return TestClient(create_app(off_settings(tmp_path)))


@pytest.fixture
def on_client(tmp_path) -> TestClient:
    return TestClient(create_app(enabled_settings(tmp_path)))


HEADERS = {"X-Api-Key": TEST_API_KEY}


def test_the_oracle_routes_require_the_api_key(off_client: TestClient) -> None:
    assert off_client.get("/v1/oracle/440").status_code == 401
    assert off_client.post("/v1/oracle/440/refresh").status_code == 401
    assert off_client.delete("/v1/oracle/440").status_code == 401


def test_the_routes_answer_disabled_rather_than_404(off_client: TestClient) -> None:
    """A client must be able to tell "this vault-api has no oracle" from "this
    operator chose not to enable it"."""
    view = off_client.get("/v1/oracle/440", headers=HEADERS)
    assert view.status_code == 200
    assert view.json()["enabled"] is False
    assert view.json()["verdict"] == oracle.VERDICT_UNKNOWN

    refresh = off_client.post("/v1/oracle/440/refresh", headers=HEADERS)
    assert refresh.status_code == 200
    assert refresh.json() == {
        "appid": 440,
        "enabled": False,
        "ok": False,
        "error": "",
        "checked_at": None,
        "depot_count": 0,
        "branch_manifest_count": 0,
        "open_branches": [],
        "skipped_password_branches": 0,
        "warnings": [],
    }


@pytest.mark.parametrize("appid", ["0", "-1", "abc"])
def test_a_bad_appid_is_a_422(off_client: TestClient, appid: str) -> None:
    assert off_client.get(f"/v1/oracle/{appid}", headers=HEADERS).status_code == 422


def test_refresh_then_read_over_http(
    on_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one end-to-end path — with the HTTP fetch replaced by a recorded
    fixture, so the suite still never leaves the machine."""
    monkeypatch.setattr(
        oracle, "http_fetch", lambda url, timeout=None: encode(RECORDED_TF2)
    )

    refreshed = on_client.post("/v1/oracle/440/refresh", headers=HEADERS).json()
    assert refreshed["ok"] is True
    assert refreshed["open_branches"] == ["beta", "public"]

    view = on_client.get("/v1/oracle/440", headers=HEADERS).json()
    assert view["enabled"] is True
    assert view["source"] == oracle.SOURCE_STEAMCMD_API
    assert view["buildid"] == "17400000"
    assert [d["depotid"] for d in view["depots"]] == [441, 442]
    assert view["depots"][0]["beta_branches"] == ["beta"]

    assert on_client.delete("/v1/oracle/440", headers=HEADERS).status_code == 204
    assert on_client.get("/v1/oracle/440", headers=HEADERS).json()["depots"] == []


def test_an_unreachable_oracle_is_a_200_with_ok_false(
    on_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flaky third party must not become a 5xx on this API — ADR-0006's
    fail-soft rule, at the HTTP boundary."""

    def boom(url: str, timeout: float | None = None) -> bytes:
        raise oracle.OracleError("api.example is unreachable: timed out")

    monkeypatch.setattr(oracle, "http_fetch", boom)

    response = on_client.post("/v1/oracle/440/refresh", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "unreachable" in response.json()["error"]
