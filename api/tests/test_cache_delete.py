"""DELETE /v1/cache/{appid} — per-game deletion with shared-depot protection
(plan §4/§6, WP 1.6).

Split in three, deliberately: the **path guards** and the **plan** are unit
tests over pure functions (no HTTP, no database, no filesystem where possible),
the **link/removal mechanics** are tested against real symlinks/junctions on the
real filesystem, and the **endpoint** is tested end to end through HTTP. Nothing
here mocks the filesystem: this code deletes user data, so the tests exercise
what actually happens on disk.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY, make_dir_link, make_junction
from vault_api import deletion, jobs
from vault_api.config import Settings
from vault_api.deletion import (
    CoOwner,
    DeletedDepot,
    FailedDepot,
    RemnantDepot,
    SharedDepot,
    UnsafeCacheRootError,
    UnsafeDepotTargetError,
    coerce_positive_id,
    delete_app_depots,
    depot_dir_bytes,
    depot_dir_path,
    is_filesystem_root,
    plan_deletion,
    remove_depot_dir,
    resolve_depot_root,
)
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seed_depot(cache_root: Path, depotid: int, size: int) -> Path:
    """One depot directory holding a single chunk file of ``size`` bytes."""
    depot = cache_root / "depot" / str(depotid)
    _write(depot / "chunk" / "aa", b"1" * size)
    return depot


def _make_app(
    tmp_path: Path, *, cache_root: str | None = None
) -> tuple[TestClient, Path, Settings]:
    """A fresh app whose VAULT_CACHE_ROOT is a real directory under tmp_path.

    Not the shared ``client`` fixture: every test here cares about the exact
    cache root on disk. No lifespan is started, so no prefill worker runs and
    queued jobs stay queued (which is what the 409 test needs).

    ``cache_root`` is passed through **verbatim** (as a ``str``, never via
    ``Path``) so the guard tests can hand the app a literally empty value —
    ``Path("")`` silently becomes ``"."``, which would test a different thing.
    """
    root = str(tmp_path / "cache") if cache_root is None else cache_root
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=root,
        log_level="INFO",
    )
    return TestClient(create_app(settings)), Path(root or "."), settings


def _seed_mapping(client: TestClient, depotid: int, appid: int, name: str | None = None) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": name}, headers=AUTH
    )
    assert response.status_code == 200, response.text


def _app_row(settings: Settings, appid: int) -> sqlite3.Row:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT appid, status, last_prefill_at, needs_force FROM apps "
            "WHERE appid = ?",
            (appid,),
        ).fetchone()
    finally:
        conn.close()


def _clear_needs_force(settings: Settings, appid: int) -> None:
    """Drive an app to needs_force=0 so a test can prove deletion SETS it,
    rather than merely observing the schema's own DEFAULT 1."""
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO apps (appid, status, needs_force) VALUES (?, 'idle', 0) "
            "ON CONFLICT(appid) DO UPDATE SET needs_force = 0",
            (appid,),
        )
        conn.commit()
    finally:
        conn.close()


def _set_app_prefilled(settings: Settings, appid: int) -> None:
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "UPDATE apps SET status = 'done', last_prefill_at = ? WHERE appid = ?",
            ("2026-08-05T10:00:00Z", appid),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# path guards (pure functions)
# --------------------------------------------------------------------------


def test_is_filesystem_root_recognises_roots_and_normal_dirs() -> None:
    assert is_filesystem_root(os.path.realpath(os.sep)) is True
    assert is_filesystem_root(os.path.abspath(os.sep)) is True
    # A UNC share root has no parent either (Windows semantics; the check is
    # pure ntpath/posixpath string work, so it is meaningful on both).
    if os.name == "nt":
        assert is_filesystem_root("\\\\server\\share") is True
        assert is_filesystem_root("\\\\server\\share\\sub") is False
    assert is_filesystem_root(os.path.join(os.path.abspath(os.sep), "cache")) is False


def test_resolve_depot_root_refuses_an_empty_cache_root() -> None:
    # abspath("") is the current working directory — an unset VAULT_CACHE_ROOT
    # must never turn into "rm -rf $(pwd)/depot".
    for value in ["", "   ", None]:  # type: ignore[list-item]
        with pytest.raises(UnsafeCacheRootError) as excinfo:
            resolve_depot_root(value)  # type: ignore[arg-type]
        assert "empty" in str(excinfo.value).lower()


def test_resolve_depot_root_refuses_a_filesystem_root() -> None:
    # On Windows realpath("/") is "C:\\" — a Docker-style path pasted onto a
    # Windows host lands exactly here.
    for value in ["/", os.path.abspath(os.sep)]:
        with pytest.raises(UnsafeCacheRootError) as excinfo:
            resolve_depot_root(value)
        assert "filesystem root" in str(excinfo.value)


def test_resolve_depot_root_refuses_a_cache_root_without_a_depot_dir(
    tmp_path: Path,
) -> None:
    (tmp_path / "cache").mkdir()
    with pytest.raises(UnsafeCacheRootError) as excinfo:
        resolve_depot_root(str(tmp_path / "cache"))
    assert "depot" in str(excinfo.value)

    # ...and refuses a depot/ that is a FILE rather than a directory.
    _write(tmp_path / "cache" / "depot", b"not a directory")
    with pytest.raises(UnsafeCacheRootError):
        resolve_depot_root(str(tmp_path / "cache"))


def test_resolve_depot_root_refuses_an_unc_style_path() -> None:
    # No such share exists, so this must refuse rather than "succeed" against
    # something unresolvable (the concrete refusal reason differs per platform:
    # missing depot/ dir on Windows, missing path on POSIX).
    with pytest.raises(UnsafeCacheRootError):
        resolve_depot_root("\\\\no-such-server\\no-such-share")


def test_resolve_depot_root_accepts_a_relative_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The default VAULT_CACHE_ROOT ("./cache") is relative, so this is the
    # normal case, not an edge case.
    (tmp_path / "cache" / "depot").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert resolve_depot_root("cache") == os.path.realpath(
        str(tmp_path / "cache" / "depot")
    )


def test_resolve_depot_root_resolves_a_linked_cache_root(tmp_path: Path) -> None:
    # Putting the cache on another volume via a link is a legitimate homelab
    # setup: the RESOLVED directory becomes the deletion base, which is what
    # makes depot_dir_path's strict-child check meaningful.
    real = tmp_path / "real-cache"
    (real / "depot").mkdir(parents=True)
    make_dir_link(tmp_path / "linked-cache", real)

    assert resolve_depot_root(str(tmp_path / "linked-cache")) == os.path.realpath(
        str(real / "depot")
    )


@pytest.mark.parametrize(
    "poisoned",
    [
        "../../etc",
        "441/../..",
        "..",
        ".",
        "",
        "   ",
        "0",
        "-1",
        0,
        -5,
        None,
        True,
        1.5,
        "441; rm -rf /",
        "0x1c1",
        "C:\\Windows",
    ],
)
def test_depot_dir_path_refuses_anything_that_is_not_a_positive_int(
    tmp_path: Path, poisoned: object
) -> None:
    """SQLite's INTEGER affinity does not enforce integers, so a poisoned or
    hand-edited depot_app_map row really can contain these."""
    depot_root = os.path.realpath(str(tmp_path))
    with pytest.raises(UnsafeDepotTargetError):
        depot_dir_path(depot_root, poisoned)


def test_depot_dir_path_builds_a_direct_child_from_the_integer_only(
    tmp_path: Path,
) -> None:
    depot_root = os.path.realpath(str(tmp_path))
    assert depot_dir_path(depot_root, 441) == os.path.join(depot_root, "441")
    # A plain numeric string from the DB is accepted (INTEGER affinity normally
    # stores an int, but a TEXT row is possible).
    assert depot_dir_path(depot_root, "441") == os.path.join(depot_root, "441")


def test_coerce_positive_id_rejects_bool_and_non_positive() -> None:
    assert coerce_positive_id(441) == 441
    assert coerce_positive_id("441") == 441
    # True == 1 in Python; without the explicit bool check it would become
    # "delete depot 1".
    assert coerce_positive_id(True) is None
    assert coerce_positive_id(0) is None
    assert coerce_positive_id(-1) is None
    assert coerce_positive_id(None) is None
    assert coerce_positive_id(b"441") is None


def test_coerce_positive_id_accepts_only_exact_ascii_digit_strings() -> None:
    """WP 1.6 review nitpick: exactness beats what ``int()`` happens to accept.

    All three of these parse fine with ``int()`` but are not depot ids vault-api
    ever writes — on the path that decides which directory gets destroyed, a
    value this odd means the row is wrong and the operator should hear about it
    rather than have it silently normalised.
    """
    assert int(" 441 ") == 441 and coerce_positive_id(" 441 ") is None
    assert int("1_0") == 10 and coerce_positive_id("1_0") is None
    assert int("٤٤١") == 441  # Arabic-Indic digits
    assert coerce_positive_id("٤٤١") is None
    assert coerce_positive_id("+441") is None
    # isdigit() alone would accept a superscript; isascii() rules it out.
    assert "²".isdigit() and coerce_positive_id("²") is None


def test_plan_deletion_rejects_a_non_exact_depotid_string() -> None:
    # Same rule inside the plan, so a weird row is reported rather than
    # normalised into a deletion target.
    plan = plan_deletion([(441, 440), (" 442 ", 440)], appid=440)

    assert plan.exclusive == [441]
    assert len(plan.unusable) == 1
    assert "' 442 '" in plan.unusable[0].error


# --------------------------------------------------------------------------
# the plan: exclusive vs shared (plan §4)
# --------------------------------------------------------------------------


def test_plan_deletion_splits_exclusive_and_shared() -> None:
    rows = [
        (441, 440),  # only 440 -> exclusive
        (442, 440),  # only 440 -> exclusive
        (900, 440),  # also 730 and 570 -> shared, kept
        (900, 730),
        (900, 570),
    ]
    plan = plan_deletion(rows, appid=440)

    assert plan.exclusive == [441, 442]
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [570, 730])]
    assert plan.unusable == []


def test_plan_deletion_is_symmetric_for_the_other_app() -> None:
    rows = [(441, 440), (900, 440), (900, 730)]
    plan = plan_deletion(rows, appid=730)

    assert plan.exclusive == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [440])]


def test_plan_deletion_reports_an_unusable_depotid_row_instead_of_deleting() -> None:
    plan = plan_deletion([(441, 440), ("../../etc", 440)], appid=440)

    assert plan.exclusive == [441]
    assert len(plan.unusable) == 1
    assert plan.unusable[0].depotid == 0
    assert "../../etc" in plan.unusable[0].error


def test_plan_deletion_treats_an_unreadable_co_owner_as_shared() -> None:
    # Conservative direction: something else references this depot, and the
    # reference is unreadable -> never delete it.
    plan = plan_deletion([(900, 440), (900, None)], appid=440)

    assert plan.exclusive == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [])]


# --------------------------------------------------------------------------
# plan_deletion: last cached remnants (ADR-0003 addendum, WP 3.5)
# --------------------------------------------------------------------------


def test_plan_deletion_without_co_owner_states_reproduces_the_pre_addendum_behavior() -> None:
    """Omitting ``co_owner_states`` entirely (the default, ``None``) must
    behave EXACTLY like the pre-addendum "any other owner protects" rule —
    no caller that hasn't been updated to supply state regresses into
    deleting something it used to protect."""
    rows = [(900, 440), (900, 730)]
    plan = plan_deletion(rows, appid=440)

    assert plan.remnant == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [730])]


def test_plan_deletion_classifies_a_shared_depot_as_remnant_when_every_co_owner_is_uncached() -> None:
    rows = [(900, 440), (900, 730)]
    plan = plan_deletion(rows, appid=440, co_owner_states={730: False})

    assert plan.exclusive == []
    assert plan.shared == []
    assert [(r.depotid, r.shared_with_uncached) for r in plan.remnant] == [(900, [730])]


def test_plan_deletion_keeps_a_shared_depot_protected_when_any_co_owner_has_content() -> None:
    rows = [(900, 440), (900, 730), (900, 570)]
    plan = plan_deletion(rows, appid=440, co_owner_states={730: False, 570: True})

    assert plan.remnant == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [570, 730])]


def test_plan_deletion_treats_a_co_owner_missing_from_states_as_protected() -> None:
    # 730 is a co-owner in `rows` but absent from co_owner_states (e.g. a
    # caller that only looked up SOME of the ids, or the appid turned out to
    # have no `apps` row and load_co_owner_states simply omitted it).
    rows = [(900, 440), (900, 730)]
    plan = plan_deletion(rows, appid=440, co_owner_states={})

    assert plan.remnant == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [730])]


def test_plan_deletion_never_classifies_a_depot_with_an_unreadable_co_owner_as_remnant() -> None:
    # Even though the READABLE co-owner (730) is uncached, the unreadable
    # mapping row protects unconditionally -- it is never eligible to become
    # a remnant no matter what the other states say.
    rows = [(900, 440), (900, 730), (900, None)]
    plan = plan_deletion(rows, appid=440, co_owner_states={730: False})

    assert plan.remnant == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [730])]


def test_plan_deletion_three_way_share_needs_every_co_owner_uncached_to_become_remnant() -> None:
    rows = [(900, 440), (900, 730), (900, 570)]

    protected = plan_deletion(rows, appid=440, co_owner_states={730: False, 570: True})
    assert protected.remnant == []
    assert [(s.depotid, s.shared_with) for s in protected.shared] == [(900, [570, 730])]

    remnant = plan_deletion(rows, appid=440, co_owner_states={730: False, 570: False})
    assert remnant.shared == []
    assert [(r.depotid, r.shared_with_uncached) for r in remnant.remnant] == [
        (900, [570, 730])
    ]


# --------------------------------------------------------------------------
# link safety on the real filesystem
# --------------------------------------------------------------------------


def test_shutil_rmtree_refuses_a_linked_dir_which_is_why_we_unlink_it(
    tmp_path: Path,
) -> None:
    """Pins the measured platform behavior ``remove_depot_dir`` is built on.

    Windows 11 / CPython 3.12.10, junction created with ``mklink /J``:
    ``shutil.rmtree(<junction>)`` raises
    ``OSError("Cannot call rmtree on a symbolic link")`` and the junction's
    target survives. Same refusal for a POSIX/Windows directory symlink. So
    rmtree does not destroy foreign data — but it cannot delete a legitimately
    linked depot directory either, which is why ``remove_depot_dir`` handles
    the link case itself.
    """
    outside = tmp_path / "outside"
    _write(outside / "precious.bin", b"keep me")
    link = tmp_path / "441"
    make_dir_link(link, outside)

    with pytest.raises(OSError):
        shutil.rmtree(link)

    assert (outside / "precious.bin").read_bytes() == b"keep me"


def test_remove_depot_dir_unlinks_a_linked_depot_and_spares_the_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    _write(outside / "precious.bin", b"keep me")
    link = tmp_path / "depot" / "441"
    link.parent.mkdir(parents=True)
    kind = make_dir_link(link, outside)

    remove_depot_dir(str(link))

    assert not os.path.lexists(link), f"the {kind} itself must be gone"
    assert (outside / "precious.bin").read_bytes() == b"keep me"


def test_remove_depot_dir_does_not_follow_a_link_nested_in_the_tree(
    tmp_path: Path,
) -> None:
    """A link *inside* a depot tree must be removed as a link, not followed.

    Measured: ``shutil.rmtree`` on a real directory containing a junction
    completes, removes the junction as a link, and leaves the target intact
    (Windows 11 / CPython 3.12.10). This test is the regression guard — if a
    future CPython followed nested junctions, it would fail here instead of
    eating a user's data.
    """
    outside = tmp_path / "outside"
    _write(outside / "precious.bin", b"keep me")
    depot = tmp_path / "depot" / "441"
    _write(depot / "chunk" / "aa", b"1" * 10)
    make_dir_link(depot / "chunk" / "sneaky", outside)

    remove_depot_dir(str(depot))

    assert not os.path.lexists(depot)
    assert (outside / "precious.bin").read_bytes() == b"keep me"


def test_depot_dir_bytes_counts_real_files_but_not_a_link_target(
    tmp_path: Path,
) -> None:
    real = tmp_path / "depot" / "441"
    _write(real / "chunk" / "aa", b"1" * 12)
    assert depot_dir_bytes(str(real)) == 12

    outside = tmp_path / "outside"
    _write(outside / "big.bin", b"1" * 5000)
    link = tmp_path / "depot" / "442"
    make_dir_link(link, outside)
    # Only the link is removed, so no target byte is freed — reporting 5000
    # freed bytes here would be a lie.
    assert depot_dir_bytes(str(link)) == 0


def test_delete_app_depots_reports_an_absent_depot_as_deleted_with_zero_bytes(
    tmp_path: Path,
) -> None:
    depot_root = tmp_path / "depot"
    depot_root.mkdir()

    # Explicit no-op recheck: co_owners is a required keyword argument, so a
    # caller that means "no shared-depot recheck here" has to say so.
    deleted, failed, late_shared = delete_app_depots(
        str(depot_root), 440, [441], co_owners=lambda _depotid: []
    )

    assert deleted == [DeletedDepot(depotid=441, size_bytes_freed=0)]
    assert failed == []
    assert late_shared == []


def test_delete_app_depots_isolates_a_path_guard_failure(tmp_path: Path) -> None:
    depot_root = tmp_path / "depot"
    depot_root.mkdir()
    _write(depot_root / "441" / "chunk" / "aa", b"1" * 7)

    deleted, failed, _late = delete_app_depots(
        str(depot_root), 440, ["../../etc", 441], co_owners=lambda _depotid: []
    )

    assert deleted == [DeletedDepot(depotid=441, size_bytes_freed=7)]
    assert len(failed) == 1
    assert isinstance(failed[0], FailedDepot)
    assert not (depot_root / "441").exists()


def test_delete_app_depots_keeps_a_depot_that_became_shared_at_recheck_time(
    tmp_path: Path,
) -> None:
    depot_root = tmp_path / "depot"
    depot_root.mkdir()
    _write(depot_root / "441" / "chunk" / "aa", b"1" * 7)
    _write(depot_root / "442" / "chunk" / "aa", b"1" * 9)

    deleted, failed, late_shared = delete_app_depots(
        str(depot_root),
        440,
        [441, 442],
        co_owners=lambda d: [CoOwner(appid=730, has_content=True)] if d == 441 else [],
    )

    assert late_shared == [SharedDepot(depotid=441, shared_with=[730])]
    assert deleted == [DeletedDepot(depotid=442, size_bytes_freed=9)]
    assert failed == []
    assert (depot_root / "441").exists(), "a newly-shared depot was deleted"


def test_delete_app_depots_never_deletes_when_the_recheck_itself_fails(
    tmp_path: Path,
) -> None:
    """"Unknown ownership" must resolve to "keep", not to "delete"."""
    depot_root = tmp_path / "depot"
    depot_root.mkdir()
    _write(depot_root / "441" / "chunk" / "aa", b"1" * 7)

    def broken(depotid: int) -> list[int]:
        raise sqlite3.OperationalError("database is locked")

    deleted, failed, late_shared = delete_app_depots(
        str(depot_root), 440, [441], co_owners=broken
    )

    assert deleted == []
    assert late_shared == []
    assert len(failed) == 1 and failed[0].depotid == 441
    assert "re-check" in failed[0].error
    assert (depot_root / "441").exists()


def test_delete_app_depots_fails_closed_on_an_explicit_none_co_owners(
    tmp_path: Path,
) -> None:
    """``co_owners=None`` is NOT a second, quieter spelling of "skip the
    recheck" (WP 3.5 review) — calling ``None(depotid)`` raises ``TypeError``,
    which is caught by the same "unknown ownership never resolves to delete"
    path as any other recheck failure. The only supported opt-out remains
    ``co_owners=lambda _depotid: []``, spelled out at the call site."""
    depot_root = tmp_path / "depot"
    depot_root.mkdir()
    _write(depot_root / "441" / "chunk" / "aa", b"1" * 7)

    deleted, failed, late_shared = delete_app_depots(
        str(depot_root), 440, [441], co_owners=None  # type: ignore[arg-type]
    )

    assert deleted == []
    assert late_shared == []
    assert len(failed) == 1 and failed[0].depotid == 441
    assert "re-check" in failed[0].error
    assert (depot_root / "441").exists()


# --------------------------------------------------------------------------
# needs_force (WP 3.4, ADR-0006 decision 2) -- unit level
# --------------------------------------------------------------------------


def test_reset_app_after_deletion_requires_the_set_needs_force_kwarg(
    tmp_path: Path,
) -> None:
    """Same rule as delete_app_depots's required `co_owners`: a future caller
    must not be able to silently skip deciding this."""
    from vault_api.db import get_connection, init_db

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        with pytest.raises(TypeError):
            deletion.reset_app_after_deletion(conn, 440, "idle")  # type: ignore[call-arg]
    finally:
        conn.close()


def test_reset_app_after_deletion_sets_needs_force_when_requested(
    tmp_path: Path,
) -> None:
    from vault_api.db import get_connection, init_db

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO apps (appid, status, needs_force) VALUES (440, 'done', 0)"
        )
        conn.commit()

        deletion.reset_app_after_deletion(conn, 440, "idle", set_needs_force=True)

        row = conn.execute(
            "SELECT status, last_prefill_at, needs_force FROM apps WHERE appid = 440"
        ).fetchone()
        assert row["status"] == "idle"
        assert row["last_prefill_at"] is None
        assert row["needs_force"] == 1
    finally:
        conn.close()


def test_reset_app_after_deletion_leaves_needs_force_when_not_requested(
    tmp_path: Path,
) -> None:
    from vault_api.db import get_connection, init_db

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO apps (appid, status, needs_force) VALUES (440, 'done', 0)"
        )
        conn.commit()

        deletion.reset_app_after_deletion(conn, 440, "idle", set_needs_force=False)

        row = conn.execute("SELECT needs_force FROM apps WHERE appid = 440").fetchone()
        assert row["needs_force"] == 0
    finally:
        conn.close()


def test_load_co_owners_returns_only_the_other_apps(tmp_path: Path) -> None:
    """``upsert_mapping`` creates fresh ``apps`` rows at ``status='idle'`` with
    no ``last_prefill_at`` and no job, so every co-owner here reads as
    currently uncached (``has_content=False``) — the ADR-0003 addendum's
    default state for a brand-new app, not yet the "protects" case tested
    separately below."""
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)
        upsert_mapping(conn, depotid=900, appid=730, name=None)
        upsert_mapping(conn, depotid=900, appid=570, name=None)
        upsert_mapping(conn, depotid=441, appid=440, name=None)

        assert deletion.load_co_owners(conn, 900, 440) == [
            CoOwner(appid=570, has_content=False),
            CoOwner(appid=730, has_content=False),
        ]
        assert deletion.load_co_owners(conn, 441, 440) == []
    finally:
        conn.close()


def test_load_co_owners_protects_for_a_co_owner_with_no_apps_row_at_all(
    tmp_path: Path,
) -> None:
    """Pins the EXECUTE-time half of "no apps row -> protected" (WP 3.5
    review, should-fix 1): ``load_co_owner_states``/``plan_deletion`` already
    had this covered at plan time, but nothing exercised
    ``load_co_owners``/``_has_cache_content`` itself — flipping the ``status
    is None -> True`` default to ``False`` passed the whole suite before this
    test existed. ``depot_app_map`` has no foreign key to ``apps``, so a bare
    mapping row naming an appid that was never inserted into ``apps`` is a
    real, reachable state, not a contrived one."""
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)
        # 730 maps depot 900 but never gets an `apps` row of its own.
        conn.execute("INSERT INTO depot_app_map (depotid, appid) VALUES (900, 730)")
        conn.commit()
        assert conn.execute(
            "SELECT 1 FROM apps WHERE appid = 730"
        ).fetchone() is None, "precondition: 730 has no apps row"

        assert deletion.load_co_owners(conn, 900, 440) == [
            CoOwner(appid=730, has_content=True)
        ]
    finally:
        conn.close()


def test_load_co_owners_reports_has_content_per_adr0003_addendum_rule(
    tmp_path: Path,
) -> None:
    """Each of the three protecting signals — non-idle status, a set
    ``last_prefill_at``, an active job — makes ``has_content=True``
    independently; a co-owner with none of them is ``has_content=False``."""
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)
        for appid in (100, 200, 300, 400):
            upsert_mapping(conn, depotid=900, appid=appid, name=None)

        conn.execute("UPDATE apps SET status = 'error' WHERE appid = 100")
        conn.execute(
            "UPDATE apps SET last_prefill_at = ? WHERE appid = 200",
            ("2026-08-05T10:00:00Z",),
        )
        conn.execute(
            "INSERT INTO jobs (appid, type, status, created_at) VALUES (300, 'prefill', 'queued', ?)",
            (jobs.utcnow_iso(),),
        )
        # 400 stays idle/never-prefilled/job-free -> the only uncached one.
        conn.commit()

        owners = {co.appid: co.has_content for co in deletion.load_co_owners(conn, 900, 440)}
        assert owners == {100: True, 200: True, 300: True, 400: False}
    finally:
        conn.close()


def test_load_co_owner_states_matches_load_co_owners_conservative_default(
    tmp_path: Path,
) -> None:
    """The plan-time bulk lookup and the execute-time per-depot lookup share
    the same ``_has_cache_content`` rule — proven here by comparing them
    directly on the same fixture, and by checking a missing ``apps`` row is
    simply absent (not a KeyError, not a crash)."""
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)
        upsert_mapping(conn, depotid=900, appid=730, name=None)  # idle, uncached
        conn.execute("UPDATE apps SET status = 'done' WHERE appid = 730")
        conn.commit()

        states = deletion.load_co_owner_states(conn, {730, 999999})
        assert states == {730: True}  # 999999 has no apps row -> absent, not False

        owners = deletion.load_co_owners(conn, 900, 440)
        assert owners == [CoOwner(appid=730, has_content=True)]
    finally:
        conn.close()


@pytest.mark.parametrize("blocking_status", ["done", "stale", "error", "running"])
def test_load_co_owners_protects_for_every_non_idle_status(
    tmp_path: Path, blocking_status: str
) -> None:
    """Every non-'idle' status protects, including 'error' — a failed last
    run does not mean "no content", just "last run failed" (ADR-0003
    addendum: "error and unknown states protect")."""
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)
        upsert_mapping(conn, depotid=900, appid=730, name=None)
        conn.execute("UPDATE apps SET status = ? WHERE appid = 730", (blocking_status,))
        conn.commit()

        assert deletion.load_co_owners(conn, 900, 440) == [
            CoOwner(appid=730, has_content=True)
        ]
        assert deletion.load_co_owner_states(conn, {730}) == {730: True}
    finally:
        conn.close()


@pytest.mark.parametrize("job_status", [jobs.STATUS_QUEUED, jobs.STATUS_RUNNING])
def test_load_co_owners_protects_for_a_queued_or_running_job(
    tmp_path: Path, job_status: str
) -> None:
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)
        upsert_mapping(conn, depotid=900, appid=730, name=None)
        conn.execute(
            "INSERT INTO jobs (appid, type, status, created_at) VALUES (730, ?, ?, ?)",
            (jobs.JOB_TYPE_PREFILL, job_status, jobs.utcnow_iso()),
        )
        conn.commit()

        assert deletion.load_co_owners(conn, 900, 440) == [
            CoOwner(appid=730, has_content=True)
        ]
        assert deletion.load_co_owner_states(conn, {730}) == {730: True}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# appids_with_cache_content (WP 4f): the one shared "which apps hold cache
# content" definition, reused by scheduler.cached_appids and
# routers/jobs.py's POST /v1/prefill/cached selection.
# --------------------------------------------------------------------------


def test_appids_with_cache_content_includes_an_exclusive_owner(tmp_path: Path) -> None:
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name=None)

        assert deletion.appids_with_cache_content(conn, {441: 100}) == {440}
        # A depot with no bytes on disk right now contributes nothing, even
        # though the mapping row exists.
        assert deletion.appids_with_cache_content(conn, {}) == set()
        assert deletion.appids_with_cache_content(conn, {999: 100}) == set()
    finally:
        conn.close()


def test_appids_with_cache_content_mutual_sharing_pair_becomes_visible(
    tmp_path: Path,
) -> None:
    """WP 4f: two apps sharing ALL their depots with each other and NOTHING
    else, with neither one recorded as having content anywhere, are the hole
    ``exclusive``-alone (WP 4d's original rule) left open FOREVER — neither
    app ever becomes the sole owner of anything, so under that narrower rule
    this depot's bytes could never make either one a sweep/check-and-update
    target again, no matter how long they sit on disk. ADR-0003's remnant
    rule closes it: since every OTHER co-owner (there is exactly one) is
    verifiably uncached, the shared depot is a ``remnant`` for BOTH apps
    symmetrically, and both become visible.
    """
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=10, name=None)
        upsert_mapping(conn, depotid=900, appid=20, name=None)
        # Both rows were just created by upsert_mapping -> status='idle', no
        # last_prefill_at, no job -> has_content=False for both (ADR-0003
        # addendum's default state for a brand-new app).

        assert deletion.appids_with_cache_content(conn, {900: 4096}) == {10, 20}
    finally:
        conn.close()


def test_appids_with_cache_content_excludes_when_a_co_owner_has_content(
    tmp_path: Path,
) -> None:
    """The B1 direction, at the unit level (the end-to-end real-``DELETE``
    fixture lives in ``tests/test_scheduler.py``): a shared depot with at
    least one co-owner that currently HAS content protects it for every
    OTHER owner — that other owner is never counted as holding cache content
    through this depot, matching ``plan_deletion``'s ``shared`` outcome."""
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=300, appid=440, name=None)
        upsert_mapping(conn, depotid=300, appid=730, name=None)
        conn.execute(
            "UPDATE apps SET status = 'done', last_prefill_at = ? WHERE appid = 730",
            ("2026-08-05T10:00:00Z",),
        )
        conn.commit()

        result = deletion.appids_with_cache_content(conn, {300: 4096})

        assert result == {730}
        assert 440 not in result
    finally:
        conn.close()


def test_appids_with_cache_content_is_one_statement_regardless_of_app_count(
    tmp_path: Path,
) -> None:
    """Same guarantee as ``tests/test_prefill_cached.py``'s route-level pin,
    exercised directly against the shared function itself (the route is now
    a thin wrapper around this one) — mutating either the join query or the
    per-app reconstruction loop back into a per-app query fails this by
    name.

    N5 (reviewer nitpick, 2026-08-18 review round): includes ONE small shared
    cluster (two apps mutually sharing one depot) alongside the 200 exclusive
    apps, so this pin also exercises the per-app reconstruction loop it
    claims to protect — a fixture with zero sharing would stay at one
    statement even if that loop were deleted entirely and replaced with
    something that only ever produced ``exclusive`` results.
    """
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        depot_bytes: dict[int, int] = {}
        for i in range(200):
            appid = 5000 + i
            depotid = 700_000 + i
            upsert_mapping(conn, depotid=depotid, appid=appid, name=None)
            depot_bytes[depotid] = 4096

        # A shared cluster: apps 5900/5901 mutually share depot 800000, both
        # freshly idle/uncached -> both remnant-visible (WP 4f widening).
        upsert_mapping(conn, depotid=800_000, appid=5900, name=None)
        upsert_mapping(conn, depotid=800_000, appid=5901, name=None)
        depot_bytes[800_000] = 4096

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            result = deletion.appids_with_cache_content(conn, depot_bytes)
        finally:
            conn.set_trace_callback(None)

        assert result == {5000 + i for i in range(200)} | {5900, 5901}
        assert len(statements) == 1, statements
    finally:
        conn.close()


# --------------------------------------------------------------------------
# WP 4f, B1 (reviewer blocker, 2026-08-18 review round): two fail-closed arms
# of the shared predicate were unpinned -- flipping either one left the
# WHOLE suite green. docs/LEARNINGS.md's standing rule (WP 3.6): "fail-closed
# defaults need tests that pin the DEFAULT direction". Both mutations are
# re-applied and confirmed to fail these tests, then reverted, as part of
# this package's verification.
# --------------------------------------------------------------------------


def test_appids_with_cache_content_a_co_owner_with_no_apps_row_protects_the_depot(
    tmp_path: Path,
) -> None:
    """Mutation pin: ``LEFT JOIN apps`` -> ``JOIN apps`` in
    ``load_all_mapping_rows_with_owner_state``. Under a plain ``JOIN``, a
    mapping row whose appid has NO ``apps`` row at all is dropped from the
    result set ENTIRELY, not merely reported with ``status IS NULL`` -- so
    the depot's OTHER owner never sees it as shared at all and is wrongly
    classified as exclusive.

    Fixture: depot 900 is mapped to app 440 (a real, idle/uncached ``apps``
    row) and to app 730, which maps the depot but has NO ``apps`` row
    (``depot_app_map`` has no foreign key to ``apps`` -- a real, reachable
    state, same fixture shape as
    ``test_load_co_owners_protects_for_a_co_owner_with_no_apps_row_at_all``
    above). ``_has_cache_content(status=None, ...)`` already returns
    ``True`` (protected) for exactly this case, which is what makes 440
    correctly EXCLUDED here -- but that rule only fires if the row survives
    the join in the first place. 730 itself IS a valid candidate (its own
    appid coerces fine; only its ``apps`` row is missing) and is correctly
    INCLUDED via the remnant rule (its only co-owner, 440, is uncached).

    Measured effect of the mutation (reviewer, real rig): selection flips
    from ``{730}`` to ``{440}`` -- the exact re-download-after-delete
    divergence this package exists to make impossible, reintroduced.
    """
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)  # idle/uncached
        # 730 maps depot 900 but never gets an `apps` row of its own.
        conn.execute("INSERT INTO depot_app_map (depotid, appid) VALUES (900, 730)")
        conn.commit()
        assert conn.execute(
            "SELECT 1 FROM apps WHERE appid = 730"
        ).fetchone() is None, "precondition: 730 has no apps row"

        result = deletion.appids_with_cache_content(conn, {900: 4096})

        assert result == {730}
        assert 440 not in result
    finally:
        conn.close()


def test_appids_with_cache_content_a_poisoned_co_owner_appid_protects_the_depot(
    tmp_path: Path,
) -> None:
    """Mutation pin: moving ``depot_groups.setdefault(...).append(...)``
    below the ``if owner is None: continue`` guard in
    ``appids_with_cache_content``. Today a poisoned (non-coercible) co-owner
    appid still gets its row added to ``depot_groups`` BEFORE the coercion
    check short-circuits the rest of that iteration -- which is what lets
    ``plan_deletion`` see the row and classify the depot as protected
    (``unreadable_owner``). Move the append below the guard and the poisoned
    row never reaches ``depot_groups`` at all, so the depot's readable owner
    is wrongly reconstructed as exclusive.

    Fixture: depot 900 mapped to app 440 (real, idle/uncached) and to a
    poisoned appid ``'abc'`` (``depot_app_map`` has no type-enforcing
    constraint on ``appid`` — SQLite affinity does not reject it, same class
    of fixture ``tests/test_cache_delete.py``'s
    ``test_plan_deletion_treats_an_unreadable_co_owner_as_shared`` already
    uses one level down). The correct answer is ``set()``: 440 is protected
    by the unreadable co-owner, and the poisoned appid names no candidate of
    its own.
    """
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)  # idle/uncached
        conn.execute("INSERT INTO depot_app_map (depotid, appid) VALUES (900, 'abc')")
        conn.commit()

        result = deletion.appids_with_cache_content(conn, {900: 4096})

        assert result == set()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# the endpoint
# --------------------------------------------------------------------------


def test_delete_without_key_is_rejected(client: TestClient) -> None:
    response = client.delete("/v1/cache/440")
    assert response.status_code == 401


def test_delete_unknown_appid_is_404(client: TestClient) -> None:
    response = client.delete("/v1/cache/999999", headers=AUTH)
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_delete_non_positive_appid_is_422(client: TestClient) -> None:
    assert client.delete("/v1/cache/0", headers=AUTH).status_code == 422
    assert client.delete("/v1/cache/-3", headers=AUTH).status_code == 422


def test_delete_app_without_any_mapping_is_404(client: TestClient) -> None:
    # POST /v1/prefill creates the apps row without any mapping.
    assert client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).status_code == 202

    response = client.delete("/v1/cache/440", headers=AUTH)
    assert response.status_code == 404
    assert "no depot mappings" in response.json()["detail"]


def test_delete_removes_exclusive_depots_and_reports_freed_bytes(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440)
    depot441 = _seed_depot(cache_root, 441, 10)
    depot442 = _seed_depot(cache_root, 442, 25)
    _seed_depot(cache_root, 555, 99)  # unrelated, unmapped -> must survive

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "appid": 440,
        "deleted_depots": [
            {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []},
            {"depotid": 442, "size_bytes_freed": 25, "shared_with_uncached": []},
        ],
        "skipped_shared": [],
        "failed": [],
        "total_bytes_freed": 35,
    }
    assert not depot441.exists()
    assert not depot442.exists()
    assert (cache_root / "depot" / "555").exists(), "an unrelated depot was deleted"
    assert (cache_root / "depot").is_dir(), "the depot root itself must survive"


def test_delete_keeps_a_shared_depot_and_reports_the_other_appids(
    tmp_path: Path,
) -> None:
    """Plan §4: "2 depots shared with game Y, not deleted" — in both directions.

    730 and 570 are given real cache content (``status='done'`` with a
    ``last_prefill_at``) precisely so depot 900 stays PROTECTED throughout —
    this is the "at least one co-owner still has content" regression case
    (ADR-0003 addendum); see
    ``test_delete_deletes_a_shared_depot_when_every_co_owner_is_uncached``
    below for the addendum's new "all co-owners uncached" outcome on
    otherwise-identical data.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730, name="Counter-Strike 2")
    _seed_mapping(client, depotid=900, appid=570, name="Dota 2")
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 900, 50)
    _set_app_prefilled(settings, 730)
    _set_app_prefilled(settings, 570)

    first = client.delete("/v1/cache/440", headers=AUTH).json()
    assert first["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}
    ]
    assert first["skipped_shared"] == [{"depotid": 900, "shared_with": [570, 730]}]
    assert first["total_bytes_freed"] == 10
    assert (cache_root / "depot" / "900").exists(), "shared depot was deleted!"

    # Other direction: 730 still shares 900 with 440 and 570 (the mapping rows
    # of the just-deleted app are kept on purpose). 440 is uncached again by
    # now (just reset to idle above), but 570 still has content, so 900 stays
    # protected.
    second = client.delete("/v1/cache/730", headers=AUTH).json()
    assert second["deleted_depots"] == []
    assert second["skipped_shared"] == [{"depotid": 900, "shared_with": [440, 570]}]
    assert second["total_bytes_freed"] == 0
    assert (cache_root / "depot" / "900" / "chunk" / "aa").read_bytes() == b"1" * 50


def test_delete_deletes_a_shared_depot_when_every_co_owner_is_uncached(
    tmp_path: Path,
) -> None:
    """ADR-0003 addendum, both-uncached two-way share: A and B share a depot,
    both idle/never-prefilled/job-free -> DELETE A removes it, flags it with
    B's appid, counts its bytes, and sets B.needs_force=1."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 900, 50)
    _clear_needs_force(settings, 730)
    assert _app_row(settings, 730)["needs_force"] == 0

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []},
        {"depotid": 900, "size_bytes_freed": 50, "shared_with_uncached": [730]},
    ]
    assert body["skipped_shared"] == []
    assert body["failed"] == []
    assert body["total_bytes_freed"] == 60
    assert not (cache_root / "depot" / "900").exists(), "last remnant must be deleted"
    assert _app_row(settings, 440)["status"] == "idle"
    # B's own status/last_prefill_at are untouched -- only needs_force moves.
    b_row = _app_row(settings, 730)
    assert b_row["status"] == "idle"
    assert b_row["last_prefill_at"] is None
    assert b_row["needs_force"] == 1


def test_delete_keeps_a_shared_depot_protected_when_one_co_owner_is_cached_and_leaves_its_needs_force_untouched(
    tmp_path: Path,
) -> None:
    """One-cached regression: a single content-having co-owner is enough to
    protect (reported in skipped_shared, never a remnant), and that
    co-owner's OWN needs_force is never touched by another app's DELETE."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 900, 50)
    _set_app_prefilled(settings, 730)
    _clear_needs_force(settings, 730)
    assert _app_row(settings, 730)["needs_force"] == 0

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == []
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": [730]}]
    assert (cache_root / "depot" / "900").exists(), "protected depot must survive"
    assert _app_row(settings, 730)["needs_force"] == 0, "protected co-owner untouched"


@pytest.mark.parametrize("blocking_status", ["error", "stale", "running"])
def test_delete_protects_a_remnant_candidate_when_its_only_co_owner_has_a_non_idle_status(
    tmp_path: Path, blocking_status: str
) -> None:
    """Status variants: 'error', 'stale' and 'running' each protect, exactly
    like 'done' — only 'idle' with no last_prefill_at and no job counts as
    uncached."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 900, 50)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("UPDATE apps SET status = ? WHERE appid = 730", (blocking_status,))
        conn.commit()
    finally:
        conn.close()

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == []
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": [730]}]
    assert (cache_root / "depot" / "900").exists()


def test_delete_three_way_share_deletes_only_once_every_co_owner_is_uncached(
    tmp_path: Path,
) -> None:
    """Three-way share: B (uncached) + C (cached) -> protected. Once C also
    becomes uncached, B + C both uncached -> deleted, both flagged, both get
    needs_force=1."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)  # B
    _seed_mapping(client, depotid=900, appid=570)  # C
    _seed_depot(cache_root, 900, 50)
    _set_app_prefilled(settings, 570)  # C is cached
    _clear_needs_force(settings, 730)
    _clear_needs_force(settings, 570)

    first = client.delete("/v1/cache/440", headers=AUTH)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["deleted_depots"] == []
    # shared_with names EVERY other tracked owner (unchanged semantics), even
    # though only 570 is the one actually keeping the depot protected here.
    assert first_body["skipped_shared"] == [{"depotid": 900, "shared_with": [570, 730]}]
    assert (cache_root / "depot" / "900").exists()
    assert _app_row(settings, 730)["needs_force"] == 0
    assert _app_row(settings, 570)["needs_force"] == 0

    # C now becomes uncached too -> both co-owners uncached -> deletable.
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "UPDATE apps SET status = 'idle', last_prefill_at = NULL WHERE appid = 570"
        )
        conn.commit()
    finally:
        conn.close()

    second = client.delete("/v1/cache/440", headers=AUTH)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["deleted_depots"] == [
        {"depotid": 900, "size_bytes_freed": 50, "shared_with_uncached": [570, 730]}
    ]
    assert body["skipped_shared"] == []
    assert not (cache_root / "depot" / "900").exists()
    assert _app_row(settings, 730)["needs_force"] == 1
    assert _app_row(settings, 570)["needs_force"] == 1


def test_delete_protects_a_depot_whose_co_owner_has_no_apps_row_at_all(
    tmp_path: Path,
) -> None:
    """The mapping table has no foreign key to ``apps``, so a
    ``depot_app_map`` row can legitimately name an appid with no ``apps`` row
    at all -- ADR-0003 addendum: protects, conservatively, same as an
    unreadable owner."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_depot(cache_root, 900, 50)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("INSERT INTO depot_app_map (depotid, appid) VALUES (900, 730)")
        conn.commit()
        assert conn.execute(
            "SELECT 1 FROM apps WHERE appid = 730"
        ).fetchone() is None, "precondition: 730 has no apps row"
    finally:
        conn.close()

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == []
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": [730]}]
    assert (cache_root / "depot" / "900").exists()


def test_delete_protects_a_depot_with_a_poisoned_co_owner_row_end_to_end(
    tmp_path: Path,
) -> None:
    """A poisoned (non-integer) ``depot_app_map.appid`` -- SQLite's INTEGER
    affinity does not enforce the column type -- protects unconditionally,
    reported at plan time exactly like an unreadable co-owner always has
    been (``shared_with`` empty, since the poisoned value cannot be named as
    an owner id)."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_depot(cache_root, 900, 50)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO depot_app_map (depotid, appid) VALUES (900, ?)",
            ("not-an-appid",),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == []
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": []}]
    assert (cache_root / "depot" / "900").exists()


def test_delete_deletes_the_only_depot_when_it_is_an_all_shared_remnant(
    tmp_path: Path,
) -> None:
    """An app whose ONLY mapped depot is a shared last-cached remnant: the
    depot IS deleted, the requester still gets needs_force=1 and ends at
    status='idle' -- this is NOT the pre-addendum "everything shared,
    nothing to do" edge case, because "shared" alone no longer means
    "untouched"."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 900, 50)
    _clear_needs_force(settings, 440)
    assert _app_row(settings, 440)["needs_force"] == 0

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 900, "size_bytes_freed": 50, "shared_with_uncached": [730]}
    ]
    assert body["skipped_shared"] == []
    assert not (cache_root / "depot" / "900").exists()
    row = _app_row(settings, 440)
    assert row["status"] == "idle"
    assert row["needs_force"] == 1


def test_retroactive_repair_of_a_pre_fix_orphaned_remnant(tmp_path: Path) -> None:
    """Simulates the exact leak ADR-0003's addendum closes: A's own exclusive
    depot is already absent on disk (as if a pre-fix DELETE already ran for
    A), a shared depot D survived as "shared with the other", and both A and
    B are uncached. Re-issuing DELETE for A must report the absent depot
    (0 bytes) AND remove D -- no new endpoint is needed because mapping rows
    survive and this DELETE re-reads current co-owner state every time."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)  # A's own depot
    _seed_mapping(client, depotid=900, appid=440)  # shared with B
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 900, 50)
    # 441 is deliberately NOT seeded on disk: simulates the pre-fix DELETE
    # having already removed it, while 900 survived as an orphaned remnant.
    assert not (cache_root / "depot" / "441").exists()
    # Cleared explicitly (WP 3.5 review nit): needs_force's schema DEFAULT is
    # already 1, so leaving this out would make the needs_force==1 assertion
    # below pass whether or not this request actually set it.
    _clear_needs_force(settings, 730)
    assert _app_row(settings, 730)["needs_force"] == 0

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 0, "shared_with_uncached": []},
        {"depotid": 900, "size_bytes_freed": 50, "shared_with_uncached": [730]},
    ]
    assert body["failed"] == []
    assert not (cache_root / "depot" / "900").exists(), (
        "the orphaned remnant must be reclaimed"
    )
    assert _app_row(settings, 730)["needs_force"] == 1


def test_an_already_absent_remnant_depot_still_reports_and_flags_its_co_owners(
    tmp_path: Path,
) -> None:
    """The ALREADY-ABSENT branch (the mapping said this depot was here, and
    it demonstrably wasn't) must carry ``shared_with_uncached`` exactly like
    a real removal does, and its co-owner still gets ``needs_force=1`` (WP
    3.5 review, should-fix 2) — dropping ``shared_with_uncached`` here passed
    the whole suite before this test existed."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    # depot 900's directory is deliberately NEVER created on disk -- the
    # mapping row exists, but there is nothing to remove.
    (cache_root / "depot").mkdir(parents=True)
    assert not (cache_root / "depot" / "900").exists()
    _clear_needs_force(settings, 730)
    assert _app_row(settings, 730)["needs_force"] == 0

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 900, "size_bytes_freed": 0, "shared_with_uncached": [730]}
    ]
    assert body["failed"] == []
    assert _app_row(settings, 440)["status"] == "idle"
    assert _app_row(settings, 730)["needs_force"] == 1


def test_a_remnant_depot_whose_removal_fails_still_flags_its_co_owners_with_needs_force(
    tmp_path: Path,
) -> None:
    """A remnant depot's removal FAILING still leaves its co-owners' on-disk
    assumption uncertain -- they still get needs_force=1 -- and the
    requesting app's own status still ends at 'error' (WP 1.6 rule: 'failed'
    does not mean 'untouched')."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    depot900 = _seed_depot(cache_root, 900, 25)
    _clear_needs_force(settings, 730)
    assert _app_row(settings, 730)["needs_force"] == 0

    if os.name == "nt":
        blocker = open(depot900 / "chunk" / "aa", "rb")
        restore = None
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: directory mode bits do not block unlink")
        blocker = None
        chunk_dir = depot900 / "chunk"
        os.chmod(chunk_dir, 0o500)
        restore = chunk_dir

    try:
        response = client.delete("/v1/cache/440", headers=AUTH)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["deleted_depots"] == []
        assert len(body["failed"]) == 1
        assert body["failed"][0]["depotid"] == 900
        assert _app_row(settings, 440)["status"] == "error"
        assert _app_row(settings, 730)["needs_force"] == 1, (
            "a remnant depot's co-owner still needs needs_force after a failed removal"
        )
    finally:
        if blocker is not None:
            blocker.close()
        if restore is not None:
            os.chmod(restore, 0o700)


def test_a_remnant_depot_that_gains_a_content_having_co_owner_before_removal_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0003 addendum's own TOCTOU variant: ``plan_deletion`` classifies
    depot 900 as a deletable remnant (co-owner 730 uncached at plan time),
    and then -- before the directory is removed -- 730's prefill finishes (a
    real concurrent job completing). The execute-time recheck must protect
    it, exactly like a newly-mapped co-owner does for the pre-addendum
    TOCTOU (see ``test_a_depot_that_becomes_shared_between_plan_and_removal_survives``).
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 900, 50)

    real_plan_deletion = deletion.plan_deletion

    def plan_then_730_gets_prefilled(rows, appid, co_owner_states=None):  # type: ignore[no-untyped-def]
        plan = real_plan_deletion(rows, appid, co_owner_states)
        assert any(r.depotid == 900 for r in plan.remnant), (
            "precondition: 900 is a deletable remnant at plan time"
        )
        # The racing write lands here: after the plan, before any removal.
        _set_app_prefilled(settings, 730)
        return plan

    monkeypatch.setattr(deletion, "plan_deletion", plan_then_730_gets_prefilled)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}
    ]
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": [730]}]
    assert (cache_root / "depot" / "900" / "chunk" / "aa").read_bytes() == b"1" * 50, (
        "another game's content was deleted through a stale remnant plan"
    )
    assert _app_row(settings, 440)["status"] == "idle"


def test_delete_is_refused_with_409_while_a_prefill_job_is_queued(
    tmp_path: Path,
) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)

    assert client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).status_code == 202

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "queued" in detail
    assert (cache_root / "depot" / "441").exists(), "409 must not delete anything"

    # A job for a DIFFERENT app does not block this one.
    _seed_mapping(client, depotid=800, appid=570)
    _seed_depot(cache_root, 800, 5)
    assert client.delete("/v1/cache/570", headers=AUTH).status_code == 200


def test_delete_resets_status_to_idle_and_clears_last_prefill_at(
    tmp_path: Path,
) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)
    _set_app_prefilled(settings, 440)
    assert _app_row(settings, 440)["status"] == "done"

    assert client.delete("/v1/cache/440", headers=AUTH).status_code == 200

    row = _app_row(settings, 440)
    assert row["status"] == "idle"
    assert row["last_prefill_at"] is None


# --------------------------------------------------------------------------
# needs_force (WP 3.4, ADR-0006 decision 2) -- through the endpoint
# --------------------------------------------------------------------------


def test_delete_sets_needs_force_after_a_clean_deletion(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)
    _clear_needs_force(settings, 440)
    assert _app_row(settings, 440)["needs_force"] == 0

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    assert _app_row(settings, 440)["needs_force"] == 1


def test_delete_sets_needs_force_on_partial_failure(tmp_path: Path) -> None:
    """Cache state is unknown after a partial failure, so the next fill must
    not trust SteamPrefill's own (now possibly stale) bookkeeping — the
    partial-failure error path sets needs_force even though ONE depot really
    was fully removed here."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=442, appid=440)
    _seed_depot(cache_root, 441, 10)
    depot442 = _seed_depot(cache_root, 442, 25)
    _clear_needs_force(settings, 440)

    if os.name == "nt":
        blocker = open(depot442 / "chunk" / "aa", "rb")
        restore = None
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: directory mode bits do not block unlink")
        blocker = None
        chunk_dir = depot442 / "chunk"
        os.chmod(chunk_dir, 0o500)
        restore = chunk_dir

    try:
        response = client.delete("/v1/cache/440", headers=AUTH)
        assert response.status_code == 200, response.text
        assert len(response.json()["failed"]) == 1
        assert _app_row(settings, 440)["needs_force"] == 1
    finally:
        if blocker is not None:
            blocker.close()
        if restore is not None:
            os.chmod(restore, 0o700)


def test_delete_sets_needs_force_when_a_depot_is_already_absent(tmp_path: Path) -> None:
    """The already-absent race: the mapping row is kept after a first delete
    (documented decision), so a second DELETE of the same app targets a depot
    that is demonstrably no longer there. That is new information about cache
    state (not "nothing happened") and must set needs_force just like an
    actual removal would — proven independently of the first delete's own
    (also-correct) needs_force=1 by clearing the flag back to 0 in between."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)

    first = client.delete("/v1/cache/440", headers=AUTH)
    assert first.status_code == 200, first.text
    assert first.json()["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}
    ]

    _clear_needs_force(settings, 440)
    assert _app_row(settings, 440)["needs_force"] == 0

    second = client.delete("/v1/cache/440", headers=AUTH)

    assert second.status_code == 200, second.text
    assert second.json()["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 0, "shared_with_uncached": []}
    ]
    assert second.json()["failed"] == []
    assert _app_row(settings, 440)["needs_force"] == 1


def test_delete_does_not_touch_needs_force_when_everything_is_shared_and_protected(
    tmp_path: Path,
) -> None:
    """Nothing exclusive/remnant existed to delete -> nothing on disk changed
    for this app -> needs_force is left exactly as it was, not reset to 1.

    730 is given real content (ADR-0003 addendum) so depot 900 stays
    PROTECTED rather than becoming a deletable remnant — see
    ``test_delete_deletes_the_only_depot_when_it_is_an_all_shared_remnant``
    for the addendum's own "all shared, but every co-owner uncached" case,
    which this test is deliberately NOT testing.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 900, 50)
    _set_app_prefilled(settings, 730)
    _clear_needs_force(settings, 440)
    assert _app_row(settings, 440)["needs_force"] == 0

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == []
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": [730]}]
    assert body["failed"] == []
    assert _app_row(settings, 440)["needs_force"] == 0


def test_delete_keeps_the_mapping_rows(tmp_path: Path) -> None:
    """Documented decision: the mapping is knowledge, not cache state."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, name="Team Fortress 2")
    _seed_depot(cache_root, 441, 10)

    assert client.delete("/v1/cache/440", headers=AUTH).status_code == 200

    assert client.get("/v1/mapping", headers=AUTH).json() == [
        {"depotid": 441, "appid": 440}
    ]
    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["depots"] == [{"depotid": 441, "shared": False, "size_bytes": None}]
    assert detail["size_bytes"] is None
    assert detail["name"] == "Team Fortress 2"


def test_delete_keeps_the_mapping_row_of_a_deleted_remnant(tmp_path: Path) -> None:
    """ADR-0003's main decision ("mapping rows are still kept") applies to a
    remnant deletion exactly like an ordinary one — only the directory is
    removed. The co-owner (730) keeps mapping the now-deleted depot too,
    which is precisely what makes the retroactive-repair story work."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730, name="Counter-Strike 2")
    _seed_depot(cache_root, 900, 50)

    response = client.delete("/v1/cache/440", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["deleted_depots"] == [
        {"depotid": 900, "size_bytes_freed": 50, "shared_with_uncached": [730]}
    ]

    assert client.get("/v1/mapping", headers=AUTH).json() == [
        {"depotid": 900, "appid": 440},
        {"depotid": 900, "appid": 730},
    ]
    b_detail = client.get("/v1/games/730", headers=AUTH).json()
    assert b_detail["depots"] == [{"depotid": 900, "shared": True, "size_bytes": None}]
    assert b_detail["name"] == "Counter-Strike 2"


def test_delete_invalidates_the_size_cache_immediately(tmp_path: Path) -> None:
    """The DEFAULT 60s TTL is used on purpose: only an explicit
    ``SizeCache.invalidate()`` can make the size drop inside the test's runtime.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)

    assert settings.size_cache_ttl_seconds == 60.0
    before = client.get("/v1/games", headers=AUTH).json()
    assert before[0]["size_bytes"] == 10
    assert client.get("/v1/cache/summary", headers=AUTH).json()["total_bytes"] == 10

    assert client.delete("/v1/cache/440", headers=AUTH).status_code == 200

    after = client.get("/v1/games", headers=AUTH).json()
    assert after[0]["size_bytes"] is None, "stale pre-deletion size served"
    assert after[0]["depot_count"] == 1, "the mapping row must still be there"
    assert client.get("/v1/cache/summary", headers=AUTH).json()["total_bytes"] == 0


def test_deleting_twice_is_a_clean_no_op(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)

    first = client.delete("/v1/cache/440", headers=AUTH).json()
    assert first["total_bytes_freed"] == 10

    second = client.delete("/v1/cache/440", headers=AUTH)
    assert second.status_code == 200
    body = second.json()
    # The mapping row is kept, so the second call still targets depot 441 —
    # it is simply already gone: reported as deleted with 0 bytes freed, no
    # error, no exception.
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 0, "shared_with_uncached": []}
    ]
    assert body["failed"] == []
    assert body["total_bytes_freed"] == 0


def test_two_racing_deletes_do_not_5xx_or_double_delete(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=442, appid=440)
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 442, 25)
    app = client.app

    async def run() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            return await asyncio.gather(
                *[async_client.delete("/v1/cache/440", headers=AUTH) for _ in range(4)]
            )

    responses = asyncio.run(run())

    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    # No double count: across all racing requests each depot's bytes are
    # claimed at most once. NOT "exactly once": total_bytes_freed is a
    # documented floor (CacheDeletionOut, deletion.delete_app_depots), and a
    # race can legitimately report a depot's bytes as 0 everywhere. Measured
    # (~1% of runs, identical at eea667d and with SEC-FIX-4): request A sizes
    # the depot (10 bytes) and unlinks its chunk file, request B sizes it
    # after the unlink (0 bytes), then B's rmtree wins the final rmdir of the
    # now-empty depot directory -> B reports removed_here with 0 bytes, A's
    # rmdir hits FileNotFoundError -> A reports "removed concurrently", 0
    # bytes. The exact sequential count is pinned by the test above.
    for depotid, size in ((441, 10), (442, 25)):
        claimed = sum(
            depot["size_bytes_freed"]
            for r in responses
            for depot in r.json()["deleted_depots"]
            if depot["depotid"] == depotid
        )
        assert claimed in (0, size), (depotid, claimed)
    for response in responses:
        body = response.json()
        assert body["failed"] == []
        assert sorted(d["depotid"] for d in body["deleted_depots"]) == [441, 442]
    assert not (cache_root / "depot" / "441").exists()
    assert not (cache_root / "depot" / "442").exists()


def test_cross_app_remnant_race_does_not_5xx_and_mutually_flags_needs_force(
    tmp_path: Path,
) -> None:
    """The cross-app version of the racing-deletes test above: depot 900 is
    NOT one app racing itself, it is a last cached remnant SHARED by two
    different apps (440 and 730), and both apps' own DELETE requests race
    each other (4 requests, appids alternating). Fable second-pass rig
    (WP 3.5): no 5xx, bytes freed at most once across ALL racing requests —
    not just requests for the same app — and, the thing the same-app race
    above cannot exercise at all, BOTH apps mutually end up needs_force=1,
    whichever request actually removed the depot."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730)
    _seed_depot(cache_root, 900, 100)
    _clear_needs_force(settings, 440)
    _clear_needs_force(settings, 730)
    app = client.app

    async def run() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            return await asyncio.gather(
                *[
                    async_client.delete(f"/v1/cache/{appid}", headers=AUTH)
                    for appid in (440, 730, 440, 730)
                ]
            )

    responses = asyncio.run(run())

    assert [r.status_code for r in responses] == [200, 200, 200, 200], [
        r.text for r in responses
    ]
    bodies = [r.json() for r in responses]
    for body in bodies:
        assert body["failed"] == [], body

    # Bytes claimed AT MOST once across all four racing requests, whether the
    # winner was a request for 440 or for 730 -- the whole point of the
    # cross-app variant is that "raced against itself" is not the only race.
    total_bytes_freed = sum(body["total_bytes_freed"] for body in bodies)
    assert total_bytes_freed <= 100, bodies
    depot_gone = not (cache_root / "depot" / "900").exists()
    assert depot_gone or any(body["skipped_shared"] for body in bodies), (
        "depot survived although nobody protected it and nobody reported it kept"
    )

    row_440 = _app_row(settings, 440)
    row_730 = _app_row(settings, 730)
    assert row_440["status"] == "idle", row_440
    assert row_730["status"] == "idle", row_730
    if depot_gone:
        # Whichever app's OWN request actually touched depot 900 flags
        # itself via the ordinary requester reset; the OTHER app is flagged
        # via shared_with_uncached (deletion.set_needs_force_for_remnant_co_owners)
        # by whoever's request performed the removal. Either way, once the
        # depot is gone both apps must know their disk state changed --
        # this is the assertion the same-app race test cannot make at all,
        # since it only ever has one app in play.
        assert row_440["needs_force"] == 1, row_440
        assert row_730["needs_force"] == 1, row_730


def test_delete_reports_a_partial_failure_without_claiming_success(
    tmp_path: Path,
) -> None:
    """One depot is made undeletable; the other must still be deleted and the
    failure must be reported per depot (never a half-reported success).

    Windows: an open file handle inside the depot makes removal fail with a
    sharing violation. POSIX: a read-only+execute directory mode makes the
    unlink inside it fail (skipped as root, where mode bits don't apply).
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=442, appid=440)
    _seed_depot(cache_root, 441, 10)
    depot442 = _seed_depot(cache_root, 442, 25)

    if os.name == "nt":
        blocker = open(depot442 / "chunk" / "aa", "rb")
        restore = None
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: directory mode bits do not block unlink")
        blocker = None
        chunk_dir = depot442 / "chunk"
        os.chmod(chunk_dir, 0o500)
        restore = chunk_dir

    try:
        response = client.delete("/v1/cache/440", headers=AUTH)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["deleted_depots"] == [
            {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}
        ]
        assert body["total_bytes_freed"] == 10, "failed depot must not be counted"
        assert len(body["failed"]) == 1
        assert body["failed"][0]["depotid"] == 442
        assert body["failed"][0]["error"]
        assert depot442.exists(), "the failing depot must still be reported as present"
    finally:
        if blocker is not None:
            blocker.close()
        if restore is not None:
            os.chmod(restore, 0o700)


def test_a_partial_failure_leaves_the_app_in_error_not_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP 1.6 review blocker: "failed" does not mean "untouched".

    ``shutil.rmtree`` deletes files as it walks and only then raises, so a
    failed depot is typically *half* deleted. Leaving the app at ``done`` with
    its ``last_prefill_at`` intact would put a green badge on a half-destroyed
    game, and Phase 3's staleness check (manifest ids, not file counts) would
    never re-fill it. Any failure therefore ends at ``status='error'`` with
    ``last_prefill_at`` cleared.

    The block is applied to a file in the MIDDLE of the only mapped depot, so
    rmtree really does remove part of the tree before failing.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    depot = cache_root / "depot" / "441"
    for name in ["aa", "bb", "cc", "dd", "ee"]:
        _write(depot / "chunk" / name, b"1" * 100)
    _set_app_prefilled(settings, 440)
    assert _app_row(settings, 440)["status"] == "done"

    if os.name == "nt":
        blocker = open(depot / "chunk" / "cc", "rb")
        restore = None
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: directory mode bits do not block unlink")
        blocker = None
        restore = depot / "chunk"
        os.chmod(restore, 0o500)

    try:
        response = client.delete("/v1/cache/440", headers=AUTH)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["failed"]) == 1 and body["failed"][0]["depotid"] == 441
        assert body["total_bytes_freed"] == 0

        row = _app_row(settings, 440)
        assert row["status"] == "error", "half-deleted game must not stay 'done'"
        assert row["last_prefill_at"] is None
    finally:
        if blocker is not None:
            blocker.close()
        if restore is not None:
            os.chmod(restore, 0o700)


def test_a_depot_that_becomes_shared_between_plan_and_removal_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP 1.6 review should-fix: the shared-depot TOCTOU window.

    The interleaving under test: ``plan_deletion`` classifies depot 900 as
    exclusive, and *then* — before the directory is removed — another app maps
    it (a `PUT /v1/mapping/900` in the real world). Without the execute-time
    recheck, deletion would proceed on the stale plan and destroy content that
    now belongs to a second game, breaking plan §4's guarantee.

    The mapping write is injected by wrapping ``deletion.plan_deletion`` — the
    plan is computed by the real function first, so what the endpoint carries
    into the deletion loop is a genuinely stale plan, and nothing inside that
    loop is patched.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 900, 50)

    real_plan_deletion = deletion.plan_deletion

    def plan_then_someone_else_maps_900(rows, appid, co_owner_states=None):  # type: ignore[no-untyped-def]
        plan = real_plan_deletion(rows, appid, co_owner_states)
        assert 900 in plan.exclusive, "precondition: 900 is exclusive at plan time"
        # The racing write lands here: after the plan, before any removal.
        # 730 is given real content (status='done' + last_prefill_at) so this
        # test stays about the ORIGINAL TOCTOU (a co-owner appearing at all) —
        # see test_a_remnant_depot_that_gains_a_content_having_co_owner_survives
        # for the addendum's own "co-owner state changed" TOCTOU variant.
        conn = sqlite3.connect(settings.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO depot_app_map (depotid, appid) VALUES (900, 730)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO apps (appid, name, status, last_prefill_at) "
                "VALUES (730, 'Counter-Strike 2', 'done', '2026-08-05T10:00:00Z')"
            )
            conn.commit()
        finally:
            conn.close()
        return plan

    monkeypatch.setattr(deletion, "plan_deletion", plan_then_someone_else_maps_900)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}
    ]
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": [730]}], (
        "the depot that became shared must be reported as skipped, with the "
        "fresh owner list"
    )
    assert body["total_bytes_freed"] == 10
    assert (cache_root / "depot" / "900" / "chunk" / "aa").read_bytes() == b"1" * 50, (
        "another game's content was deleted through a stale plan"
    )
    # A clean run despite the late skip -> idle, not error.
    assert _app_row(settings, 440)["status"] == "idle"


def test_delete_unlinks_a_linked_depot_dir_and_the_outside_target_survives(
    tmp_path: Path,
) -> None:
    """End to end: a depot directory that is a symlink/junction pointing OUTSIDE
    the cache root. The link must go, the foreign data must stay, and the freed
    bytes must not be overstated."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 4096)
    (cache_root / "depot").mkdir(parents=True)
    kind = make_dir_link(cache_root / "depot" / "441", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 0, "shared_with_uncached": []}
    ], f"a {kind} depot dir must report 0 freed bytes — its target is not freed"
    assert body["failed"] == []
    assert not os.path.lexists(cache_root / "depot" / "441"), "the link must be gone"
    assert (outside / "precious.bin").read_bytes() == b"1" * 4096, (
        "data OUTSIDE the cache root was deleted through the link"
    )


def test_delete_unlinks_a_junction_depot_dir_and_the_outside_target_survives(
    tmp_path: Path,
) -> None:
    """The junction-specific version of the test above (Windows only).

    Required as its own test because ``make_dir_link`` prefers a symlink where
    it can create one, and the two differ exactly where it matters:
    ``os.path.islink()`` is ``False`` for a junction and
    ``shutil.rmtree`` refuses it. This is the case the work package asked to be
    verified against a target OUTSIDE the cache root.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 4096)
    _write(outside / "nested" / "more.bin", b"1" * 128)
    (cache_root / "depot").mkdir(parents=True)
    make_junction(cache_root / "depot" / "441", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 0, "shared_with_uncached": []}
    ]
    assert body["failed"] == []
    assert not os.path.lexists(cache_root / "depot" / "441"), "the junction must be gone"
    assert (outside / "precious.bin").read_bytes() == b"1" * 4096
    assert (outside / "nested" / "more.bin").read_bytes() == b"1" * 128


def test_delete_does_not_follow_a_junction_nested_inside_the_depot_tree(
    tmp_path: Path,
) -> None:
    """Junction-specific: ``shutil.rmtree`` must not walk *into* it.

    Measured on Windows 11 / CPython 3.12.10: rmtree removes the junction as a
    link and the target survives. Pinned here so a future CPython change is
    caught by this suite rather than by a user losing data.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    depot = _seed_depot(cache_root, 441, 10)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 2048)
    make_junction(depot / "chunk" / "sneaky", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json()["failed"] == []
    assert not depot.exists()
    assert (outside / "precious.bin").read_bytes() == b"1" * 2048


def test_delete_does_not_follow_a_link_nested_inside_the_depot_tree(
    tmp_path: Path,
) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    depot = _seed_depot(cache_root, 441, 10)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 2048)
    make_dir_link(depot / "chunk" / "sneaky", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json()["failed"] == []
    assert not depot.exists()
    assert (outside / "precious.bin").read_bytes() == b"1" * 2048


def test_delete_reports_a_poisoned_mapping_row_and_still_deletes_the_good_depot(
    tmp_path: Path,
) -> None:
    """End to end through a *real* poisoned database row.

    SQLite's INTEGER affinity keeps a non-numeric string as TEXT, so this row
    can genuinely exist in ``depot_app_map.depotid`` (hand-edited database,
    corruption, a future buggy writer). It must be reported, never turned into
    a path, and must not stop the healthy depot from being deleted.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)
    outside_marker = tmp_path / "outside-the-cache.txt"
    outside_marker.write_bytes(b"must survive")

    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO depot_app_map (depotid, appid) VALUES (?, ?)",
            ("../../outside-the-cache.txt", 440),
        )
        conn.commit()
        stored = conn.execute(
            "SELECT typeof(depotid) FROM depot_app_map WHERE appid = 440 "
            "AND typeof(depotid) = 'text'"
        ).fetchone()
        assert stored is not None, "sqlite coerced the poisoned value away"
    finally:
        conn.close()

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}
    ]
    assert len(body["failed"]) == 1
    assert body["failed"][0]["depotid"] == 0
    assert "outside-the-cache.txt" in body["failed"][0]["error"]
    assert outside_marker.read_bytes() == b"must survive"


def test_delete_refuses_when_the_cache_root_has_no_depot_dir(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    assert not cache_root.exists()

    response = client.delete("/v1/cache/440", headers=AUTH, follow_redirects=False)

    assert response.status_code == 500
    assert "depot" in response.json()["detail"]


def test_delete_refuses_an_empty_cache_root(tmp_path: Path) -> None:
    # Nothing must be deleted relative to the current working directory.
    client, _cache_root, settings = _make_app(tmp_path, cache_root="")
    _seed_mapping(client, depotid=441, appid=440)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 500
    assert "VAULT_CACHE_ROOT is empty" in response.json()["detail"]


def test_delete_route_is_registered_under_the_authenticated_cache_router() -> None:
    # Belt to tests/test_security.py's route walk: the DELETE route must live on
    # the router that carries require_api_key, not be added ad hoc elsewhere.
    from vault_api.routers import cache as cache_router

    paths = {
        route.path  # type: ignore[attr-defined]
        for route in cache_router.router.routes
    }
    assert "/v1/cache/{appid}" in paths
    assert deletion.DEPOT_DIRNAME == "depot"
