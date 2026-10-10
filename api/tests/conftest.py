from __future__ import annotations

import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from vault_api.config import Settings
from vault_api.main import create_app

TEST_API_KEY = "test-api-key-do-not-use-in-prod"


def _mklink_junction(
    link: os.PathLike[str] | str, target: os.PathLike[str] | str
) -> "subprocess.CompletedProcess[bytes]":
    """``mklink /J`` with BYTE capture.

    Deliberately not ``text=True``: ``cmd`` writes its messages in the console
    OEM codepage (850 on a German Windows), and letting subprocess decode that
    with the locale codec raised ``UnicodeDecodeError`` inside subprocess's own
    stderr-reader thread — observed as a ``PytestUnhandledThreadExceptionWarning``
    while writing these tests. Bytes cannot fail; ``_mklink_output`` decodes
    lossily only if a message is actually needed.
    """
    return subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )


def _mklink_output(result: "subprocess.CompletedProcess[bytes]") -> str:
    return (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()


def make_dir_link(link: os.PathLike[str] | str, target: os.PathLike[str] | str) -> str:
    """Create a real directory link at ``link`` pointing at ``target``.

    Returns which kind was created: ``"symlink"`` or ``"junction"``. Used by the
    size-scan and deletion tests, which must exercise *real* links rather than
    mocks — the whole point is that Python treats the two kinds differently
    (see ``sizes.is_link_like``).

    Creating a directory symlink on Windows requires either Developer Mode or
    admin rights, so this falls back to ``mklink /J`` (a junction, which needs
    neither). A caller that only cares "it's a link" can ignore the return
    value; tests whose *assertion* depends on the kind read it. Skips the test
    if neither mechanism is available (e.g. a locked-down Windows CI runner
    without Developer Mode — the fallback covers that in practice).
    """
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError, AttributeError):
        pass

    if os.name == "nt":
        result = _mklink_junction(link, target)
        if result.returncode == 0:
            return "junction"
        pytest.skip(f"cannot create a junction here: {_mklink_output(result)}")

    pytest.skip("cannot create a directory symlink in this environment")


def make_junction(link: os.PathLike[str] | str, target: os.PathLike[str] | str) -> None:
    """Create a Windows **junction** specifically (skips the test elsewhere).

    ``make_dir_link`` prefers a symlink when it can create one, but a junction
    behaves differently in the two places that matter and therefore needs its
    own tests: measured on Windows 11 / CPython 3.12.10, a junction is
    ``os.path.islink() == False`` and — the dangerous one —
    ``DirEntry.is_dir(follow_symlinks=False) == True``, i.e. it looks like an
    ordinary directory to a walk that only guards against symlinks.
    """
    if os.name != "nt":
        pytest.skip("junctions exist on Windows only")
    result = _mklink_junction(link, target)
    if result.returncode != 0:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot create a junction here: {_mklink_output(result)}")


@pytest.fixture(autouse=True)
def _no_cover_art_lookup_in_tests(monkeypatch) -> None:
    """WP API-FIX-5: a test that runs the lifespan starts the cover-art
    refresher thread. Push its first tick an hour out, so no test ever calls
    Steam for real; tests of the lookup drive ``refresh_once``/``tick``
    directly with a fake fetch."""
    from vault_api import cover_art

    monkeypatch.setattr(cover_art, "INITIAL_DELAY_SECONDS", 3600.0)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    return TestClient(app)
