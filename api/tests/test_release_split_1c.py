"""WP RELEASE-SPLIT-1c (ADR-0031 item 9, ABOUT-VERSION-1 Weg A).

- ``GET /v1/about`` carries ``release``: the product version and the
  server, agent and app component versions (baked by publish.yml from the
  component plan); a local build states none.
- ``api/Dockerfile`` / publish.yml / ci.yml wire the server component's
  version as its own build arg (never the product version).
- The release body of a tag run gets a "Components" block inside the
  verify markers: each component with its version and "rebuilt" or
  "unchanged since ..."; the step's own shell is run here with a fake
  ``gh`` (no network).
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api.client_versions import (
    SERVER_COMPONENT_VERSION_ENV,
    client_versions,
    release_out,
    server_component_version,
)
from vault_api.config import Settings
from vault_api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".github" / "scripts"
PUBLISH = REPO_ROOT / ".github" / "workflows" / "publish.yml"
BASH = shutil.which("bash")
AUTH = {"X-Api-Key": TEST_API_KEY}
COMMIT = "0123456789abcdef0123456789abcdef01234567"

RC23_ENV = {
    "VAULT_SERVER_COMPONENT_VERSION": "0.1.0-rc22",
    "VAULT_CLIENT_AGENT_VERSION": "0.1.0-rc17",
    "VAULT_CLIENT_AGENT_BUILD": "0.1.0-rc17",
    "VAULT_CLIENT_AGENT_MINIMUM": "0.1.0-rc17",
    "VAULT_CLIENT_APP_VERSION": "0.1.0-rc23",
    "VAULT_CLIENT_APP_BUILD": "0.1.0-rc23",
    "VAULT_CLIENT_APP_MINIMUM": "0.1.0-rc17",
}


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ra = _load("release_assets_1c", "release-assets.py")


# ---------------------------------------------------------------------------
# vault-api: release in GET /v1/about
# ---------------------------------------------------------------------------


def test_server_component_version_from_env() -> None:
    assert SERVER_COMPONENT_VERSION_ENV == "VAULT_SERVER_COMPONENT_VERSION"
    assert server_component_version({"VAULT_SERVER_COMPONENT_VERSION": "0.1.0-rc22"}) == "0.1.0-rc22"
    assert server_component_version({"VAULT_SERVER_COMPONENT_VERSION": " 0.1.0-rc22 "}) == "0.1.0-rc22"
    # Local build (empty arg), a dispatch plan's dev-<sha>, junk: none.
    for raw in ("", "dev", "dev-1234567", "v0.1.0", "0.1.0\nx"):
        assert server_component_version({"VAULT_SERVER_COMPONENT_VERSION": raw}) is None, raw
    assert server_component_version({}) is None


def test_release_out_shape() -> None:
    out = release_out("0.1.0-rc23", "0.1.0-rc22", client_versions(RC23_ENV))
    assert out == {
        "version": "0.1.0-rc23",
        "components": [
            {"name": "server", "version": "0.1.0-rc22"},
            {"name": "agent", "version": "0.1.0-rc17"},
            {"name": "app", "version": "0.1.0-rc23"},
        ],
    }
    # dev build: no product version, no component versions.
    assert release_out("dev", None, client_versions({})) == {
        "version": None,
        "components": [{"name": n, "version": None} for n in ("server", "agent", "app")],
    }
    # A bad server value never passes through.
    assert release_out("0.1.0", "dev-abc1234", {})["components"][0] == {"name": "server", "version": None}


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, env: dict[str, str]) -> TestClient:
    monkeypatch.setenv("VAULT_BUILD_VERSION", version)
    monkeypatch.setenv("VAULT_BUILD_COMMIT", COMMIT)
    for key in RC23_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings(vault_api_key=TEST_API_KEY, db_path=str(tmp_path / "vault.db"),
                        cache_root=str(tmp_path / "cache"), log_level="INFO",
                        dist_dir=str(tmp_path / "dist"))
    return TestClient(create_app(settings))


def test_about_states_the_product_and_component_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(tmp_path, monkeypatch, "0.1.0-rc23", RC23_ENV) as c:
        body = c.get("/v1/about", headers=AUTH).json()
    assert list(body) == ["components", "release"]
    assert body["release"] == {
        "version": "0.1.0-rc23",
        "components": [
            {"name": "server", "version": "0.1.0-rc22"},
            {"name": "agent", "version": "0.1.0-rc17"},
            {"name": "app", "version": "0.1.0-rc23"},
        ],
    }
    # The vault-api row keeps the product version (ABOUT-VERSION-1 Weg A).
    api = next(x for x in body["components"] if x["name"] == "vault-api")
    assert api["version"] == "0.1.0-rc23"


def test_about_of_a_local_build_states_no_components(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(tmp_path, monkeypatch, "dev", {}) as c:
        body = c.get("/v1/about", headers=AUTH).json()
    assert body["release"] == {
        "version": None,
        "components": [{"name": n, "version": None} for n in ("server", "agent", "app")],
    }


# ---------------------------------------------------------------------------
# Build wiring
# ---------------------------------------------------------------------------


def _wf() -> dict[str, Any]:
    return yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))


def _step(job: dict[str, Any], prefix: str) -> dict[str, Any]:
    for s in job["steps"]:
        if str(s.get("name", "")).startswith(prefix):
            return s
    raise AssertionError(prefix)


def test_vault_api_bakes_the_server_component_version() -> None:
    dockerfile = (REPO_ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG VAULT_SERVER_COMPONENT_VERSION=\n" in dockerfile
    assert 'ENV VAULT_SERVER_COMPONENT_VERSION="${VAULT_SERVER_COMPONENT_VERSION}"' in dockerfile
    # The product version stays VAULT_VERSION -> VAULT_BUILD_VERSION.
    assert 'VAULT_BUILD_VERSION="${VAULT_VERSION}"' in dockerfile
    args = _step(_wf()["jobs"]["publish"], "Build and push")["with"]["build-args"]
    assert "VAULT_SERVER_COMPONENT_VERSION=${{ needs.component-plan.outputs.server_version }}" in args
    assert "VAULT_VERSION=${{ needs.build-version.outputs.version }}" in args
    assert _wf()["jobs"]["component-plan"]["outputs"]["server_version"] == (
        "${{ steps.plan.outputs.server_version }}")


# ---------------------------------------------------------------------------
# Release body: the Components block
# ---------------------------------------------------------------------------

BODY_STEP = "Append image digests"
PINS = {f"PIN_{k}": f"ghcr.io/steamhangar/vault-{k.lower()}:0.1.0-rc23@sha256:{'ab' * 32}"
        for k in ("CORE", "API", "DNS", "PROXY")}


def _fake_gh(bin_dir: Path, body_file: Path, out_file: Path) -> None:
    """`gh release view ... --jq ...` prints `body_file`; `gh release edit
    ... --notes-file F` copies F to `out_file`."""
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'if [ "$1 $2" = "release view" ]; then cat "' + str(body_file) + '"; exit 0; fi\n'
        'if [ "$1 $2" = "release edit" ]; then\n'
        '  while [ $# -gt 0 ]; do if [ "$1" = "--notes-file" ]; then cp "$2" "' + str(out_file) + '"; exit 0; fi; shift; done\n'
        "fi\n"
        "echo unexpected gh call >&2; exit 9\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)


def _run_body(tmp_path: Path, plan: dict[str, str], body: str = "Generated notes.\n"
              ) -> tuple[subprocess.CompletedProcess[str], Path]:
    assert BASH is not None
    step = _step(_wf()["jobs"]["agent-binaries"], BODY_STEP)
    work = tmp_path / "work"
    (work / "dist").mkdir(parents=True)
    (work / "dist" / "SHA256SUMS").write_text("x\n")
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    out_file = tmp_path / "out.md"
    _fake_gh(tmp_path / "bin", body_file, out_file)
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    env = {
        "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_REF_NAME": "v0.1.0-rc23",
        "GITHUB_REPOSITORY": "steamhangar/steamhangar",
        "GH_TOKEN": "x",
        **PINS,
        "APK_CERT": "cd" * 32,
        "APK_CONFIGURED": "true",
        "APK_REUSED": "false",
        "APK_SHA256": "ef" * 32,
        "APP_VERSION": "0.1.0-rc23",
        "AGENT_VERSION": "0.1.0-rc17",
        "PRODUCT_VERSION": "0.1.0-rc23",
        "SERVER_COMPONENT": "0.1.0-rc22",
        "AGENT_COMPONENT": "0.1.0-rc17",
        "APP_COMPONENT": "0.1.0-rc23",
        "SERVER_BUILT": "false",
        "AGENT_BUILT": "false",
        "APP_BUILT": "true",
        **plan,
    }
    # The step's env keys are exactly what the test sets (no silent rename).
    assert set(step["env"]) - {"GH_TOKEN"} <= set(env), set(step["env"]) - set(env)
    proc = subprocess.run([BASH, "-e", "-c", step["run"]], cwd=work, env=env,
                          capture_output=True, text=True, check=False, timeout=60)
    return proc, out_file


def test_body_step_reads_the_component_plan() -> None:
    env = _step(_wf()["jobs"]["agent-binaries"], BODY_STEP)["env"]
    assert env["PRODUCT_VERSION"] == "${{ needs.build-version.outputs.version }}"
    for comp in ("server", "agent", "app"):
        assert env[f"{comp.upper()}_COMPONENT"] == f"${{{{ needs.component-plan.outputs.{comp}_version }}}}"
        assert env[f"{comp.upper()}_BUILT"] == f"${{{{ needs.component-plan.outputs.{comp}_build }}}}"
    needs = _wf()["jobs"]["agent-binaries"]["needs"]
    assert "build-version" in needs and "component-plan" in needs


@pytest.mark.skipif(BASH is None or shutil.which("sha256sum") is None, reason="bash/sha256sum")
def test_body_lists_each_component_with_rebuilt_or_unchanged_since(tmp_path: Path) -> None:
    proc, out = _run_body(tmp_path, {})
    assert proc.returncode == 0, proc.stderr
    notes = out.read_text(encoding="utf-8")
    assert notes.startswith("Generated notes.\n")
    assert "## Components\n" in notes
    assert "SteamHangar `0.1.0-rc23`." in notes
    assert "- Server `0.1.0-rc22` -- unchanged since 0.1.0-rc22\n" in notes
    assert "- Agent `0.1.0-rc17` -- unchanged since 0.1.0-rc17\n" in notes
    assert "- App `0.1.0-rc23` -- rebuilt\n" in notes
    # Inside the verify markers (re-run safe), before "Verify this release",
    # and the SHA lines later reuse jobs read still parse.
    assert notes.index(ra.VERIFY_START) < notes.index("## Components") < notes.index(
        "## Verify this release") < notes.index(ra.VERIFY_END)
    sums = hashlib.sha256(b"x\n").hexdigest()
    assert ra.body_digests(notes) == {"SHA256SUMS": sums, "steamhangar-app-v0.1.0-rc23.apk": "ef" * 32}


@pytest.mark.skipif(BASH is None or shutil.which("sha256sum") is None, reason="bash/sha256sum")
def test_body_rerun_replaces_the_block(tmp_path: Path) -> None:
    proc, out = _run_body(tmp_path, {})
    assert proc.returncode == 0, proc.stderr
    first = out.read_text(encoding="utf-8")
    proc, out = _run_body(tmp_path / "again", {"SERVER_COMPONENT": "0.1.0-rc23"}, body=first)
    assert proc.returncode == 0, proc.stderr
    notes = out.read_text(encoding="utf-8")
    assert notes.count("## Components") == 1
    assert "- Server `0.1.0-rc23` -- rebuilt\n" in notes
    assert "unchanged since 0.1.0-rc22" not in notes


@pytest.mark.skipif(BASH is None or shutil.which("sha256sum") is None, reason="bash/sha256sum")
def test_body_marks_a_dry_run_rebuild_of_an_unchanged_component(tmp_path: Path) -> None:
    proc, out = _run_body(tmp_path, {"AGENT_BUILT": "true"})
    assert proc.returncode == 0, proc.stderr
    assert "- Agent `0.1.0-rc17` -- unchanged since 0.1.0-rc17, rebuilt\n" in out.read_text(encoding="utf-8")


@pytest.mark.skipif(BASH is None or shutil.which("sha256sum") is None, reason="bash/sha256sum")
@pytest.mark.parametrize("plan,needle", [
    ({"SERVER_COMPONENT": ""}, "version for Server"),
    ({"AGENT_COMPONENT": "dev-1234567"}, "version for Agent"),
    ({"APP_BUILT": ""}, "build flag for App"),
    ({"PRODUCT_VERSION": "dev-1234567"}, "product version"),
])
def test_body_fails_closed_on_a_malformed_plan_output(tmp_path: Path, plan: dict[str, str], needle: str) -> None:
    proc, out = _run_body(tmp_path, plan)
    assert proc.returncode != 0
    assert needle in proc.stderr
    assert not out.exists(), "the release body must not be edited"
