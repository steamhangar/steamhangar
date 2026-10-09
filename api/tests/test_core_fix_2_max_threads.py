"""WP CORE-FIX-2 stage 1: SteamPrefill runs with ``--max-threads N``.

Production evidence (2026-10-02, a DS-Lite line): SteamPrefill's default of
30 concurrent chunk requests, each one a new upstream connection from
vault-core, exhausted the carrier-grade NAT's port mappings; vault-core
logged ``113: Host is unreachable`` 502s by the tens of thousands. The fix
passes SteamPrefill's hidden ``--max-threads`` flag on every prefill, with N
from ``VAULT_PREFILL_MAX_THREADS`` (default 8, 1..64, env-only).

Guarantees pinned here, one test (group) each:

G1  the default is 8 and reaches ``Settings`` with the variable unset;
G2  1..64 is accepted, everything outside (and every sloppy integer, see
    ``test_config.py``'s INTEGER_SETTINGS) refuses to boot, in vault-api AND
    in vault-runner's ``from_env(require_api_key=False)``;
G3  ``run_prefill`` puts ``--max-threads <N>`` on the argv and refuses an
    out-of-range or bool value;
G4  subprocess mode (vault-api's worker) passes ``Settings.prefill_max_threads``
    -- a non-default value, so a caller that forgot it (and fell back to the
    function default) fails;
G5  queue mode (vault-runner) does the same;
G6  the hidden flag is guarded: api/Dockerfile's pinned SteamPrefill version
    is one verified to carry it, and the Dockerfile still runs the build
    probe that fails the image build when the flag stops being recognised;
G7  deploy/.env.example documents the default (api/.env.example's twin is in
    ``test_config.py``'s ENV_EXAMPLE_DEFAULT_PINS; the compose rows are in
    ``test_p1_compose_env_defaults.py``).

The nginx half (tries 2; ``error`` was dropped here and restored by WP
CORE-FEAT-1b2 for the keepalive pool, ADR-0017 decision 6A) is pinned by
``core/docker/check-config-drift.sh`` step 2c, run by ``dev.sh test-core`` /
CI's core job.
"""

from __future__ import annotations

import dataclasses
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.test_prefill_runner_process import _queue_settings, _wait_for_job
from tests.test_worker import enqueue, make_settings, wait_for_job
from vault_api import config, jobs, prefill
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill_runner import PrefillRunner
from vault_api.worker import PrefillWorker

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DOCKERFILE = REPO_ROOT / "api" / "Dockerfile"
DEPLOY_ENV_EXAMPLE = REPO_ROOT / "deploy" / ".env.example"

#: SteamPrefill releases checked to carry the hidden ``--max-threads`` flag.
#: 3.7.1: measured against the linux-x64 release binary (sha256 as pinned in
#: api/Dockerfile): ``prefill --force --no-ansi --max-threads 8`` logs "Using
#: --max-threads flag.  Will download using at most 8 threads" and then parses
#: normally. 3.7.2 (WP DEPS-1, 2026-10-09): Program.cs is untouched by the
#: v3.7.1...v3.7.2 diff, and the linux-x64 release binary (sha256 as pinned in
#: api/Dockerfile) run as the build probe does, ``prefill --max-threads 3
#: --help``, logs "Will download using at most 3 threads". Add a version here
#: only after running it -- the image build probe does that for you.
VERIFIED_MAX_THREADS_VERSIONS = ("3.7.1", "3.7.2")


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_PREFILL_MAX_THREADS", raising=False)


# -- G1 / G2: the setting --------------------------------------------------


def test_default_is_eight_literally(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    assert config.DEFAULT_PREFILL_MAX_THREADS == 8
    assert Settings.from_env().prefill_max_threads == 8
    # The dataclass default too: tests and direct constructions get the same.
    assert Settings(vault_api_key="k", db_path="x", cache_root="y", log_level="INFO").prefill_max_threads == 8


@pytest.mark.parametrize("value", ["1", "8", "30", "64"])
def test_in_range_values_are_accepted(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("VAULT_PREFILL_MAX_THREADS", value)
    assert Settings.from_env().prefill_max_threads == int(value)


@pytest.mark.parametrize("value", ["0", "65", "1000", "-1", "eight", "8.0"])
@pytest.mark.parametrize("require_api_key", [True, False], ids=["vault-api", "vault-runner"])
def test_out_of_range_or_garbage_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch, value: str, require_api_key: bool
) -> None:
    """Loud at startup in BOTH processes: vault-runner loads the same
    ``Settings.from_env`` with ``require_api_key=False``."""
    _base_env(monkeypatch)
    if not require_api_key:
        monkeypatch.delenv("VAULT_API_KEY", raising=False)
    monkeypatch.setenv("VAULT_PREFILL_MAX_THREADS", value)
    with pytest.raises(RuntimeError, match="VAULT_PREFILL_MAX_THREADS"):
        Settings.from_env(require_api_key=require_api_key)


def test_bounds_are_one_and_sixty_four() -> None:
    assert (config.MIN_PREFILL_MAX_THREADS, config.MAX_PREFILL_MAX_THREADS) == (1, 64)


def test_the_setting_is_env_only() -> None:
    """Deliberately not a PATCH /v1/settings key: vault-runner (the shipped
    consumer) never reads the settings table, so a stored value would be a
    setting the process running SteamPrefill silently ignores."""
    from vault_api import settings_store

    assert "prefill_max_threads" not in settings_store.OVERRIDABLE_SPECS
    assert not any(
        spec.env_var == "VAULT_PREFILL_MAX_THREADS"
        for spec in settings_store.OVERRIDABLE_SPECS.values()
    )


# -- G3: run_prefill's argv ------------------------------------------------


def test_run_prefill_appends_max_threads_after_the_documented_options(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"), depots_by_app={440: [441]})
    result = prefill.run_prefill(440, executable, timeout_seconds=30, use_force=True, max_threads=5)
    assert result.success, result
    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi", "--max-threads", "5"]

    prefill.run_prefill(440, executable, timeout_seconds=30, use_force=False, max_threads=64)
    assert stub_prefill.read_argv(bindir) == ["prefill", "--no-ansi", "--max-threads", "64"]


def test_run_prefill_default_is_the_config_default(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"), depots_by_app={440: [441]})
    prefill.run_prefill(440, executable, timeout_seconds=30)
    assert stub_prefill.read_argv(bindir)[-2:] == ["--max-threads", "8"]


@pytest.mark.parametrize("bad", [0, 65, -1, True, "8", 8.0])
def test_run_prefill_refuses_a_bad_max_threads(tmp_path: Path, bad: object) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"), depots_by_app={440: [441]})
    with pytest.raises(ValueError, match="max_threads"):
        prefill.run_prefill(440, executable, timeout_seconds=30, max_threads=bad)  # type: ignore[arg-type]
    # Refused before anything ran.
    assert not (bindir / "argv.json").exists()


# -- G4: subprocess mode ---------------------------------------------------


def test_subprocess_mode_passes_the_configured_value(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), depots_by_app={440: [441]})
    settings = dataclasses.replace(make_settings(tmp_path, cache_root, executable), prefill_max_threads=5)
    assert settings.prefill_mode == config.PREFILL_MODE_SUBPROCESS

    with TestClient(create_app(settings)) as client:
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"

    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi", "--max-threads", "5"]


# -- G5: queue mode --------------------------------------------------------


def test_queue_mode_runner_passes_the_configured_value(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), depots_by_app={440: [441]})
    settings = _queue_settings(
        tmp_path, steamprefill_path=executable, cache_root=str(cache_root), prefill_max_threads=5
    )
    init_db(settings.db_path)

    worker = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="core-fix-1-runner")
    worker.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
        finally:
            conn.close()
        finished = _wait_for_job(settings.db_path, job_id)
    finally:
        worker.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)

    assert finished["status"] == jobs.STATUS_DONE, finished
    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi", "--max-threads", "5"]


def test_queue_mode_value_comes_from_the_runner_settings_not_the_worker(tmp_path: Path) -> None:
    """In the shipped stack the two processes have separate environments;
    the runner's own value is the one SteamPrefill must get."""
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), depots_by_app={440: [441]})
    worker_settings = _queue_settings(
        tmp_path, steamprefill_path=executable, cache_root=str(cache_root), prefill_max_threads=30
    )
    runner_settings = dataclasses.replace(worker_settings, prefill_max_threads=3)
    init_db(worker_settings.db_path)

    worker = PrefillWorker(worker_settings)
    runner = PrefillRunner(runner_settings, runner_id="core-fix-1-runner-2")
    worker.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()
    try:
        conn = get_connection(worker_settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
        finally:
            conn.close()
        finished = _wait_for_job(worker_settings.db_path, job_id)
    finally:
        worker.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)

    assert finished["status"] == jobs.STATUS_DONE, finished
    assert stub_prefill.read_argv(bindir)[-2:] == ["--max-threads", "3"]


# -- G6: the hidden flag is guarded ----------------------------------------


def _dockerfile_text() -> str:
    return API_DOCKERFILE.read_text(encoding="utf-8")


def test_pinned_steamprefill_version_is_verified_to_carry_max_threads() -> None:
    match = re.search(r"^ARG STEAMPREFILL_VERSION=(\S+)$", _dockerfile_text(), re.MULTILINE)
    assert match, "api/Dockerfile no longer declares ARG STEAMPREFILL_VERSION=..."
    version = match.group(1)
    assert version in VERIFIED_MAX_THREADS_VERSIONS, (
        f"api/Dockerfile pins SteamPrefill {version}, which is not in "
        f"VERIFIED_MAX_THREADS_VERSIONS {VERIFIED_MAX_THREADS_VERSIONS}. vault-api "
        "passes the HIDDEN --max-threads flag on every prefill (WP CORE-FIX-2); "
        "if the new version dropped it, every prefill job fails with "
        "'Unrecognized option(s): --max-threads'. Build the image (its probe "
        "runs the binary), check the job output shows 'Will download using at "
        "most N threads', then add the version here."
    )


def test_dockerfile_runs_the_max_threads_build_probe_on_the_steamprefill_stage() -> None:
    """The probe must sit in the stage that holds the binary (before the
    runtime stage's FROM) and must check the flag's own log line -- an exit
    code check would pass even without the flag (``--help`` ignores unknown
    options and exits 0, measured)."""
    text = _dockerfile_text()
    from_lines = [m.start() for m in re.finditer(r"^FROM ", text, re.MULTILINE)]
    assert len(from_lines) == 2, "expected the two-stage api/Dockerfile layout"
    stage1 = text[from_lines[0]:from_lines[1]]
    run_blocks = re.findall(r"^RUN (?:.*\\\n)*.*$", stage1, re.MULTILINE)
    probes = [block for block in run_blocks if "--max-threads 3 --help" in block]
    assert len(probes) == 1, "api/Dockerfile's SteamPrefill stage lost the --max-threads build probe"
    probe = probes[0]
    assert "/opt/steamprefill/SteamPrefill prefill --max-threads 3 --help" in probe
    assert 'grep -F "Will download using at most 3 threads"' in probe
    assert "exit 1" in probe
    # Comment-only lines do not count as the probe.
    assert not probe.lstrip().startswith("#")


# -- G7: deploy/.env.example documents the default -------------------------


def test_deploy_env_example_documents_the_default() -> None:
    text = DEPLOY_ENV_EXAMPLE.read_text(encoding="utf-8")
    lines = re.findall(r"^#?VAULT_PREFILL_MAX_THREADS=(.*)$", text, re.MULTILINE)
    assert lines == [str(config.DEFAULT_PREFILL_MAX_THREADS)], (
        f"deploy/.env.example should carry exactly one VAULT_PREFILL_MAX_THREADS= "
        f"line with the config default {config.DEFAULT_PREFILL_MAX_THREADS}, found {lines!r}"
    )
