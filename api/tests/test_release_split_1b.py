"""WP RELEASE-SPLIT-1b: reuse of unchanged clients, job gating, guards, and
the bundled client versions the server states (ADR-0031).

Pins:

- `.github/scripts/component-versions.py`: natural version order; the
  optional `minimum_version`; the previous tag's manifest read tolerantly
  (only `components.*.version`, any schema); the stale-manifest guard
  (enforce fails on a previous tag whose manifest a replayed plan
  contradicts; dry run only reports); `*_artifact_version` outputs;
- `.github/scripts/release-assets.py`: a reused download is checked
  against GitHub's asset digests, the release body's recorded SHA-256 and
  (agent) SHA256SUMS, failing closed on anything missing or different;
- publish.yml: the reuse jobs (read-only, no Environment, same artifact
  names), `!cancelled()` gating that accepts a builder skipped BY THE PLAN
  only together with a successful reuse job, the staging step with
  per-client versions, the release body's SHA-256 lines, the APK pin
  replaced by a summary line for a reused APK; enforce is on from rc22;
- vault-api: `clients` in GET /v1/dist and GET /v1/settings, per-client
  file names and release URLs, the alias list (checked against the git
  history when the tags are present), the Dockerfile ARG/ENV wiring.

Local git/bash subprocesses only; no Docker, no network.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_release_split_1a import Repo, _fixture_manifest
from vault_api.client_versions import AGENT_BUILD_ALIASES, client_version, client_versions, env_name
from vault_api.config import Settings
from vault_api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".github" / "scripts"
PUBLISH = REPO_ROOT / ".github" / "workflows" / "publish.yml"
MANIFEST = REPO_ROOT / ".github" / "components.json"
GIT = shutil.which("git")
BASH = shutil.which("bash")
AUTH = {"X-Api-Key": TEST_API_KEY}
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cv = _load("component_versions_1b", "component-versions.py")
ra = _load("release_assets", "release-assets.py")


# ---------------------------------------------------------------------------
# Version order and the minimum
# ---------------------------------------------------------------------------

ORDER = ["0.0.9", "0.1.0-rc9", "0.1.0-rc17", "0.1.0-rc21", "0.1.0-rc21.1", "0.1.0-rc22",
         "0.1.0", "0.1.1", "0.2.0", "1.0.0"]


def test_compare_versions_natural_order() -> None:
    for i, a in enumerate(ORDER):
        for j, b in enumerate(ORDER):
            assert cv.compare_versions(a, b) == (i > j) - (i < j), (a, b)
    assert cv.compare_versions("0.1.0-beta", "0.1.0-rc1") == -1
    with pytest.raises(cv.ManifestError):
        cv.compare_versions("dev", "0.1.0")


def test_minimum_version_is_validated() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert cv.validate_manifest(copy.deepcopy(data))
    for mutate, message in [
        (lambda d: d["components"]["agent"].update(minimum_version="0.9.0"), "newer than"),
        (lambda d: d["components"]["agent"].update(minimum_version="rc17"), "not a product version"),
        (lambda d: d["components"]["server"].update(minimum_version="0.1.0"), "only clients"),
    ]:
        bad = copy.deepcopy(data)
        mutate(bad)
        with pytest.raises(cv.ManifestError, match=message):
            cv.validate_manifest(bad)


def test_committed_manifest_enforces_from_rc22_with_minimums() -> None:
    data = cv.load_manifest(MANIFEST)
    # Operator decision 2026-11-01 ENFORCE-RC22 Weg B: reuse is enforced
    # from the rc22 tag on (ADR-0031).
    assert data["enforce"] is True, "rc22 is the first enforcing release (ADR-0031)"
    assert data["components"]["agent"]["reuse"] == "release-assets"
    assert data["components"]["app"]["reuse"] == "release-assets"
    for name in ("agent", "app"):
        assert cv.VERSION_RE.match(data["components"][name]["minimum_version"])


# ---------------------------------------------------------------------------
# Plan: tolerant previous manifest, artifact versions, stale-manifest guard
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    if GIT is None:
        pytest.skip("git not available")
    r = Repo(tmp_path / "repo")
    for rel in ("core/nginx.conf", "api/Dockerfile", "web/index.html", "agent/go/main.go",
                "app/app/src/main/A.kt", "app/build.gradle.kts"):
        r.write(rel)
    r.manifest(_fixture_manifest())
    r.commit("v1")
    r.tag("v1.0.0")
    return r


def _plan(r: Repo, manifest: dict[str, Any], version: str, prev: str) -> dict[str, Any]:
    return cv.make_plan(r.root, manifest, ".github/components.json", version, prev)


def _write(r: Repo, version: str, prev: str) -> None:
    m = cv.load_manifest(r.root / ".github/components.json")
    plan = _plan(r, m, version, prev)
    for n in cv.COMPONENT_ORDER:
        m["components"][n]["version"] = plan["components"][n]["version"]
    r.manifest(m)


def test_previous_manifest_is_read_tolerantly(repo: Repo) -> None:
    # The previous tag's manifest uses a future schema with unknown keys:
    # only components.*.version is read from it.
    future = _fixture_manifest()
    future["schema"] = 7
    future["new_top_level"] = {"x": 1}
    future["components"]["agent"]["version"] = "1.0.5"
    future["components"]["agent"]["whatever"] = True
    repo.manifest(future)
    repo.commit("future schema")
    repo.tag("v1.0.5")
    repo.manifest(_fixture_manifest())
    repo.commit("back")
    plan = _plan(repo, cv.load_manifest(repo.root / ".github/components.json"), "1.1.0", "v1.0.5")
    assert plan["components"]["agent"]["version"] == "1.0.5"
    # A missing or malformed version is an error, never a guess.
    for broken in ({"components": {}}, {"components": {"server": {"version": "1"}}}, []):
        with pytest.raises(cv.ManifestError):
            cv.recorded_versions(broken, "x")


def test_artifact_versions_follow_build_or_reuse(repo: Repo) -> None:
    repo.write("core/nginx.conf", "changed\n")
    repo.commit()
    dry = _plan(repo, _fixture_manifest(), "1.1.0", "v1.0.0")
    enforce = _plan(repo, _fixture_manifest(enforce=True), "1.1.0", "v1.0.0")
    # Dry run: the unchanged agent is rebuilt and stamped with the new version.
    assert dry["components"]["agent"]["artifact_version"] == "1.1.0"
    assert dry["components"]["agent"]["version"] == "1.0.0"
    # Enforce: reused, so its files keep the recorded version.
    assert enforce["components"]["agent"]["build"] is False
    assert enforce["components"]["agent"]["artifact_version"] == "1.0.0"
    assert enforce["components"]["server"]["artifact_version"] == "1.1.0"
    out = cv.github_outputs(enforce)
    assert "agent_artifact_version=1.0.0\n" in out
    assert "app_artifact_version=1.0.0\n" in out
    assert "previous_check=first-tag\n" in out


def test_stale_manifest_guard(repo: Repo) -> None:
    # v1.1.0 changes the server but its release commit skipped `write`
    # (dry-run tag): its manifest still says server 1.0.0.
    repo.write("core/nginx.conf", "changed\n")
    repo.commit()
    repo.tag("v1.1.0")
    repo.write("docs/x.md", "y\n")
    repo.commit()
    dry = _plan(repo, _fixture_manifest(), "1.2.0", "v1.1.0")
    assert dry["previous_check"]["status"] == "mismatch"
    assert "server: v1.1.0 records 1.0.0" in dry["previous_check"]["mismatches"][0]
    with pytest.raises(cv.ManifestError, match="stale-manifest guard"):
        _plan(repo, _fixture_manifest(enforce=True), "1.2.0", "v1.1.0")
    # The CLI fails the plan job (exit 2) with the message.
    repo.manifest(_fixture_manifest(enforce=True))
    repo.commit()
    proc = subprocess.run([sys.executable, str(SCRIPTS / "component-versions.py"), "plan",
                           "--version", "1.2.0", "--previous-tag", "v1.1.0",
                           "--repo", str(repo.root)], capture_output=True, text=True, env=repo.env)
    assert proc.returncode == 2
    assert "::error::" in proc.stderr and "stale-manifest guard" in proc.stderr
    assert "versions match the plan" in proc.stderr


def test_guard_passes_after_a_written_release(repo: Repo) -> None:
    repo.write("core/nginx.conf", "changed\n")
    repo.commit()
    _write(repo, "1.1.0", "v1.0.0")
    repo.commit("release 1.1.0")
    repo.tag("v1.1.0")
    repo.write("docs/x.md", "y\n")
    repo.commit()
    plan = _plan(repo, _fixture_manifest(enforce=True), "1.2.0", "v1.1.0")
    assert plan["previous_check"]["status"] == "match"
    assert plan["components"]["server"]["version"] == "1.1.0"
    assert plan["components"]["agent"]["build"] is False
    assert "previous tag check: match" in cv.human_table(plan)


def test_guard_allows_the_bootstrap_without_a_manifest(tmp_path: Path) -> None:
    if GIT is None:
        pytest.skip("git not available")
    r = Repo(tmp_path / "boot")
    r.write("core/nginx.conf")
    r.commit()
    r.tag("v0.9.0")
    r.write("core/nginx.conf", "b\n")
    r.commit()
    r.tag("v1.0.0")
    plan = _plan(r, _fixture_manifest(enforce=True), "1.1.0", "v1.0.0")
    assert plan["previous_check"]["status"] == "no-manifest"


# ---------------------------------------------------------------------------
# release-assets.py
# ---------------------------------------------------------------------------

AGENT_V = "0.1.0-rc17"


def _agent_names() -> list[str]:
    return ra.component_files(json.loads(MANIFEST.read_text(encoding="utf-8")), "agent", AGENT_V)


def _download(tmp_path: Path, names: list[str]) -> tuple[Path, dict[str, Any]]:
    d = tmp_path / "dl"
    d.mkdir()
    sums = []
    for n in names:
        if n == "SHA256SUMS":
            continue
        data = f"bytes of {n}\n".encode()
        (d / n).write_bytes(data)
        sums.append(f"{hashlib.sha256(data).hexdigest()}  {n}\n")
    if "SHA256SUMS" in names:
        (d / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")
    release = {
        "tag_name": f"v{AGENT_V}",
        "draft": False,
        "body": "notes\n",
        "assets": [{"name": n, "digest": "sha256:" + hashlib.sha256((d / n).read_bytes()).hexdigest()}
                   for n in names],
    }
    return d, release


def test_agent_file_names_render_from_the_manifest() -> None:
    names = _agent_names()
    assert f"vault-agent-v{AGENT_V}-windows-amd64.exe" in names
    assert "SHA256SUMS" in names
    with pytest.raises(ra.VerifyError):
        ra.component_files({"components": {}}, "agent", AGENT_V)
    with pytest.raises(ra.VerifyError):
        ra.component_files(json.loads(MANIFEST.read_text(encoding="utf-8")), "agent", "dev-1234567")


def test_verify_accepts_a_matching_download(tmp_path: Path) -> None:
    names = _agent_names()
    d, release = _download(tmp_path, names)
    got = ra.verify(d, release, names, f"v{AGENT_V}", sha256sums=True)
    assert set(got) == set(names)


@pytest.mark.parametrize("mutate,message", [
    (lambda d, r: r["assets"][0].update(digest="sha256:" + "0" * 64), "differs from the release asset digest"),
    (lambda d, r: r["assets"][0].pop("digest"), "carries no sha256 digest"),
    (lambda d, r: r["assets"][0].update(digest="md5:abc"), "carries no sha256 digest"),
    (lambda d, r: r["assets"].pop(0), "has no release asset"),
    (lambda d, r: r.update(tag_name="v9.9.9"), "not v0.1.0-rc17"),
    (lambda d, r: r.update(draft=True), "draft"),
    (lambda d, r: (d / "stray.txt").write_text("x"), "unexpected files"),
    (lambda d, r: (d / "install-task.ps1").unlink(), "not downloaded"),
])
def test_verify_fails_closed(tmp_path: Path, mutate: Any, message: str) -> None:
    names = _agent_names()
    d, release = _download(tmp_path, names)
    mutate(d, release)
    with pytest.raises(ra.VerifyError, match=re.escape(message)):
        ra.verify(d, release, names, f"v{AGENT_V}", sha256sums=True)


def test_verify_checks_the_release_body_and_sha256sums(tmp_path: Path) -> None:
    names = _agent_names()
    d, release = _download(tmp_path, names)
    sums_sha = hashlib.sha256((d / "SHA256SUMS").read_bytes()).hexdigest()
    block = f"{ra.VERIFY_START}\n```\n{sums_sha}  SHA256SUMS\n```\n{ra.VERIFY_END}\n"
    release["body"] = "notes\n" + block
    assert ra.verify(d, release, names, f"v{AGENT_V}", sha256sums=True)
    release["body"] = "notes\n" + block.replace(sums_sha, "f" * 64)
    with pytest.raises(ra.VerifyError, match="release body"):
        ra.verify(d, release, names, f"v{AGENT_V}", sha256sums=True)
    # A line outside the verify block is not a record.
    release["body"] = f"{'f' * 64}  SHA256SUMS\n"
    assert ra.verify(d, release, names, f"v{AGENT_V}", sha256sums=True)
    # A SHA256SUMS (matching its own asset digest) that misses a file or
    # lists one twice is refused.
    release["body"] = ""
    sums = (d / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for text in ("\n".join(sums[:-1]) + "\n", "\n".join(sums + [sums[0]]) + "\n"):
        (d / "SHA256SUMS").write_text(text, encoding="utf-8")
        for a in release["assets"]:
            if a["name"] == "SHA256SUMS":
                a["digest"] = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        with pytest.raises(ra.VerifyError, match="SHA256SUMS"):
            ra.verify(d, release, names, f"v{AGENT_V}", sha256sums=True)


def test_verify_cli_prints_sha256sum_lines_for_the_apk(tmp_path: Path) -> None:
    apk = "steamhangar-app-v0.1.0-rc21.1.apk"
    d = tmp_path / "dl"
    d.mkdir()
    (d / apk).write_bytes(b"apk")
    sha = hashlib.sha256(b"apk").hexdigest()
    rel = tmp_path / "release.json"
    rel.write_text(json.dumps({"tag_name": "v0.1.0-rc21.1", "assets": [{"name": apk, "digest": f"sha256:{sha}"}]}))
    proc = subprocess.run([sys.executable, str(SCRIPTS / "release-assets.py"), "verify",
                           "--manifest", str(MANIFEST), "--component", "app", "--version", "0.1.0-rc21.1",
                           "--dir", str(d), "--release-json", str(rel)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{sha}  {apk}\n"
    rel.write_text(json.dumps({"tag_name": "v0.1.0-rc21.1", "assets": [{"name": apk}]}))
    proc = subprocess.run([sys.executable, str(SCRIPTS / "release-assets.py"), "verify",
                           "--manifest", str(MANIFEST), "--component", "app", "--version", "0.1.0-rc21.1",
                           "--dir", str(d), "--release-json", str(rel)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "::error::" in proc.stderr


# ---------------------------------------------------------------------------
# publish.yml wiring
# ---------------------------------------------------------------------------


def _wf() -> dict[str, Any]:
    return yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))


def _step(job: dict[str, Any], prefix: str) -> dict[str, Any]:
    for s in job["steps"]:
        if str(s.get("name", "")).startswith(prefix):
            return s
    raise AssertionError(prefix)


def _norm(text: str) -> str:
    return " ".join(str(text).split())


@pytest.mark.parametrize("job_name,client,artifact", [
    ("agent-reuse", "agent", "release-agent"),
    ("android-reuse", "app", "release-apk"),
])
def test_reuse_jobs(job_name: str, client: str, artifact: str) -> None:
    job = _wf()["jobs"][job_name]
    output = "agent_build" if client == "agent" else "app_build"
    assert job["needs"] == ["component-plan"]
    assert f"needs.component-plan.outputs.{output} == 'false'" in job["if"]
    # Read-only token, no signing Environment.
    assert job["permissions"] == {"contents": "read"}
    assert "environment" not in job
    text = yaml.safe_dump(job)
    assert "ANDROID_" not in text
    dl = _step(job, "Download the recorded")
    assert dl["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert f"needs.component-plan.outputs.{client}_version" in yaml.safe_dump(dl["env"])
    assert 'gh api "repos/${GITHUB_REPOSITORY}/releases/tags/v${' in dl["run"]
    assert "gh release download" in dl["run"]
    verify = _step(job, "Verify the")
    assert "release-assets.py verify" in verify["run"]
    if client == "agent":
        assert "--sha256sums" in verify["run"]
    else:
        # The CI-SIGN-PIN-1 pin is not re-checked; the summary says so.
        assert "not re-checked: it was checked when this APK was built" in verify["run"]
        assert job["outputs"]["apk_sha256"] == "${{ steps.verify.outputs.apk_sha256 }}"
    upload = _step(job, "Upload the reused")
    assert upload["with"]["name"] == artifact
    assert upload["with"]["if-no-files-found"] == "error"


SKIPPED_BY_PLAN = {
    "agent": ("(needs.agent-build.result == 'success' || (needs.agent-build.result == 'skipped' "
              "&& needs.component-plan.outputs.agent_build == 'false' "
              "&& needs.agent-reuse.result == 'success'))"),
    "app": ("(needs.android-build.result == 'success' || (needs.android-build.result == 'skipped' "
            "&& needs.component-plan.outputs.app_build == 'false' "
            "&& needs.android-reuse.result == 'success'))"),
}


def test_publish_runs_when_a_builder_is_skipped_by_the_plan() -> None:
    job = _wf()["jobs"]["publish"]
    cond = _norm(job["if"])
    assert cond.startswith("${{ !cancelled()")
    assert "needs.component-plan.result == 'success'" in cond
    assert "needs.build-version.result == 'success'" in cond
    assert _norm(SKIPPED_BY_PLAN["agent"]) in cond
    assert _norm(SKIPPED_BY_PLAN["app"]) in cond
    assert {"agent-reuse", "android-reuse"} <= set(job["needs"])


def test_release_jobs_run_when_a_builder_is_skipped_by_the_plan() -> None:
    jobs = _wf()["jobs"]
    rel = _norm(jobs["android-release"]["if"])
    assert rel.startswith("${{ !cancelled()")
    assert "needs.publish.result == 'success'" in rel
    assert "|| needs.android-reuse.result == 'success'" in rel
    agent = _norm(jobs["agent-binaries"]["if"])
    assert agent.startswith("${{ !cancelled()")
    assert _norm(SKIPPED_BY_PLAN["agent"]) in agent
    attach = _step(jobs["agent-binaries"], "Attach vault-agent binaries")
    assert "needs.android-release.result != 'success'" in attach["with"]["generate_release_notes"]


def _eval_gate(job: str, results: dict[str, str], plan: dict[str, str]) -> bool:
    """Evaluate a job's real `if:` text with the given needs results (the
    rest succeeded) and plan outputs: `&&`/`||`/`!cancelled()` mapped to
    Python, every operand substituted, nothing else allowed."""
    expr = _norm(_wf()["jobs"][job]["if"])
    assert expr.startswith("${{") and expr.endswith("}}")
    expr = expr[3:-2].replace("!cancelled()", "True")
    expr = re.sub(r"needs\.([a-z-]+)\.result", lambda m: repr(results.get(m.group(1), "success")), expr)
    expr = re.sub(r"needs\.component-plan\.outputs\.([a-z_]+)", lambda m: repr(plan[m.group(1)]), expr)
    expr = expr.replace("needs.android-build.outputs.configured", repr(plan.get("configured", "true")))
    expr = expr.replace("github.event_name", "'push'").replace("&&", " and ").replace("||", " or ")
    assert re.fullmatch(r"[\sA-Za-z_'()=!.,-]*", expr), expr
    return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 -- operands substituted above


@pytest.mark.parametrize("results,plan,want", [
    ({"agent-build": "success", "agent-reuse": "skipped", "android-build": "success",
      "android-reuse": "skipped"}, {"agent_build": "true", "app_build": "true"}, True),
    ({"agent-build": "skipped", "agent-reuse": "success", "android-build": "success",
      "android-reuse": "skipped"}, {"agent_build": "false", "app_build": "true"}, True),
    # A failed builder or reuse job stops the release.
    ({"agent-build": "failure", "agent-reuse": "skipped", "android-build": "success",
      "android-reuse": "skipped"}, {"agent_build": "true", "app_build": "true"}, False),
    ({"agent-build": "skipped", "agent-reuse": "failure", "android-build": "success",
      "android-reuse": "skipped"}, {"agent_build": "false", "app_build": "true"}, False),
    # A builder skipped while the plan said "build" (e.g. the plan failed) stops it.
    ({"agent-build": "skipped", "agent-reuse": "skipped", "android-build": "success",
      "android-reuse": "skipped"}, {"agent_build": "true", "app_build": "true"}, False),
])
def test_gate_semantics(results: dict[str, str], plan: dict[str, str], want: bool) -> None:
    assert _eval_gate("publish", results, plan) is want
    agent_ok = results["agent-build"] == "success" or (
        results["agent-build"] == "skipped" and plan["agent_build"] == "false"
        and results["agent-reuse"] == "success")
    # agent-binaries: publish and android-release succeeded (defaults here),
    # the agent files came from either job.
    assert _eval_gate("agent-binaries", results, plan) is agent_ok
    # android-release: publish succeeded, the APK came from either job.
    assert _eval_gate("android-release", {**results, "publish": "success"}, plan) is (
        results["android-build"] == "success" or results["android-reuse"] == "success")


def test_component_plan_exposes_the_1b_outputs() -> None:
    job = _wf()["jobs"]["component-plan"]
    for key in ("agent_artifact_version", "app_artifact_version", "agent_minimum", "app_minimum",
                "previous_check"):
        assert job["outputs"][key] == f"${{{{ steps.plan.outputs.{key} }}}}"


def _run_output_check(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    step = _step(_wf()["jobs"]["component-plan"], "Check the plan outputs")
    base = {k: v.split("outputs.")[1].rstrip(" }") for k, v in step["env"].items()}
    good = {"server_build": "true", "server_version": "0.1.0-rc22", "agent_build": "false",
            "agent_version": "0.1.0-rc17", "app_build": "true", "app_version": "0.1.0-rc22",
            "agent_artifact_version": "0.1.0-rc17", "app_artifact_version": "0.1.0-rc22",
            "agent_minimum": "0.1.0-rc17", "app_minimum": "", "previous_check": "match"}
    full = {"PATH": "/usr/bin:/bin", **{k: good[v] for k, v in base.items()}, **env}
    return subprocess.run([BASH, "-c", step["run"]], env=full, capture_output=True, text=True,
                          check=False, timeout=30)


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("env,ok", [
    ({}, True),
    ({"AGENT_ARTIFACT_VERSION": ""}, False),
    ({"APP_ARTIFACT_VERSION": "v1"}, False),
    ({"AGENT_MINIMUM": "rc17"}, False),
    ({"APP_MINIMUM": "0.1.0-rc17"}, True),
    ({"PREVIOUS_CHECK": ""}, False),
    ({"PREVIOUS_CHECK": "stale"}, False),
])
def test_plan_output_check_covers_the_1b_outputs(env: dict[str, str], ok: bool) -> None:
    proc = _run_output_check(env)
    assert (proc.returncode == 0) is ok, proc.stderr


def test_vault_api_bakes_the_client_versions() -> None:
    job = _wf()["jobs"]["publish"]
    args = _step(job, "Build and push")["with"]["build-args"]
    for key, out in [("VAULT_AGENT_VERSION", "agent_version"), ("VAULT_AGENT_BUILD", "agent_artifact_version"),
                     ("VAULT_AGENT_MINIMUM", "agent_minimum"), ("VAULT_APP_VERSION", "app_version"),
                     ("VAULT_APP_BUILD", "app_artifact_version"), ("VAULT_APP_MINIMUM", "app_minimum")]:
        assert f"{key}=${{{{ needs.component-plan.outputs.{out} }}}}" in args
    dockerfile = (REPO_ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    for client in ("AGENT", "APP"):
        for field in ("VERSION", "BUILD", "MINIMUM"):
            assert f"ARG VAULT_{client}_{field}=\n" in dockerfile
            assert f'{env_name(client.lower(), field.lower())}="${{VAULT_{client}_{field}}}"' in dockerfile
    stage = _step(job, "Stage release downloads")
    assert stage["env"]["AGENT_VERSION"] == "${{ needs.component-plan.outputs.agent_artifact_version }}"
    assert stage["env"]["APK_SHA256"] == (
        "${{ needs.android-build.outputs.apk_sha256 || needs.android-reuse.outputs.apk_sha256 }}")
    assert 'tag="v${AGENT_VERSION}"' in stage["run"]


@pytest.mark.skipif(BASH is None or shutil.which("sha256sum") is None, reason="bash/sha256sum")
def test_stage_step_bakes_a_reused_agent_next_to_a_new_apk(tmp_path: Path) -> None:
    script = _step(_wf()["jobs"]["publish"], "Stage release downloads")["run"]
    d = tmp_path / "dist"
    d.mkdir()
    (d / "README.md").write_text("x")
    agent = [n for n in _agent_names() if n != "SHA256SUMS"]
    for n in agent:
        (d / n).write_bytes(n.encode())
    (d / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(n.encode()).hexdigest()}  {n}\n" for n in agent))
    apk = "steamhangar-app-v0.1.0-rc22.apk"
    (d / apk).write_bytes(b"apk")
    env = {"PATH": os.environ.get("PATH", ""), "BUILD_VERSION": "0.1.0-rc22", "AGENT_VERSION": AGENT_V,
           "APP_VERSION": "0.1.0-rc22", "APK_CONFIGURED": "true",
           "APK_SHA256": hashlib.sha256(b"apk").hexdigest(), "IS_TAG": "true",
           "GITHUB_STEP_SUMMARY": str(tmp_path / "s.md")}
    ok = subprocess.run([BASH, "-e", "-c", script], cwd=d, env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    # The same files under the server's version would not be the ones baked.
    bad = subprocess.run([BASH, "-e", "-c", script], cwd=d, env={**env, "AGENT_VERSION": "0.1.0-rc22"},
                         capture_output=True, text=True)
    assert bad.returncode != 0
    assert "missing" in bad.stderr


def test_release_body_records_the_sha256_lines_reuse_checks() -> None:
    step = _step(_wf()["jobs"]["agent-binaries"], "Append image digests")
    run = step["run"]
    assert 'echo "${sums_sha}  SHA256SUMS"' in run
    assert 'apk_line="${APK_SHA256}  steamhangar-app-v${APP_VERSION}.apk"' in run
    assert "reused unchanged from" in run
    assert step["env"]["APK_REUSED"] == "${{ needs.android-reuse.result == 'success' }}"
    # The lines land inside the verify block release-assets.py reads.
    assert run.index('echo "${sums_sha}  SHA256SUMS"') < run.index('echo "<!-- steamhangar:verify:end -->"')
    assert ra.VERIFY_START in run and ra.VERIFY_END in run


def test_the_apk_pin_runs_only_for_a_built_apk() -> None:
    jobs = _wf()["jobs"]
    assert "apk-cert-digest.sh pin" in _step(jobs["android-build"], "Verify APK signature")["run"]
    assert "apk-cert-digest.sh" not in yaml.safe_dump(jobs["android-reuse"])


# ---------------------------------------------------------------------------
# vault-api: client versions
# ---------------------------------------------------------------------------

RC22_ENV = {
    "VAULT_CLIENT_AGENT_VERSION": "0.1.0-rc17",
    "VAULT_CLIENT_AGENT_BUILD": "0.1.0-rc17",
    "VAULT_CLIENT_AGENT_MINIMUM": "0.1.0-rc17",
    "VAULT_CLIENT_APP_VERSION": "0.1.0-rc22",
    "VAULT_CLIENT_APP_BUILD": "0.1.0-rc22",
    "VAULT_CLIENT_APP_MINIMUM": "0.1.0-rc17",
}


def test_client_versions_from_env() -> None:
    agent = client_version("agent", RC22_ENV)
    assert agent is not None
    assert (agent.version, agent.build, agent.minimum) == ("0.1.0-rc17", "0.1.0-rc17", "0.1.0-rc17")
    assert set(agent.same_build) == {"0.1.0-rc17", *AGENT_BUILD_ALIASES}
    app = client_version("app", RC22_ENV)
    assert app is not None and app.same_build == ("0.1.0-rc22",)
    # A local build (empty args) states nothing; junk is never shown.
    assert client_versions({}) == {"agent": None, "app": None}
    assert client_version("agent", {k: "" for k in RC22_ENV}) is None
    junk = {**RC22_ENV, "VAULT_CLIENT_AGENT_BUILD": "dev-1234567", "VAULT_CLIENT_AGENT_MINIMUM": "x y"}
    agent = client_version("agent", junk)
    assert agent is not None and agent.build == "0.1.0-rc17" and agent.minimum is None
    # A dry-run rebuild of the unchanged agent: the new stamp is the same build.
    dry = client_version("agent", {**RC22_ENV, "VAULT_CLIENT_AGENT_BUILD": "0.1.0-rc22"})
    assert dry is not None and "0.1.0-rc22" in dry.same_build and "0.1.0-rc18" in dry.same_build


def test_aliases_are_the_identical_rc18_to_rc21_1_builds() -> None:
    # Literal (ADR-0031 section 7): every existing tag from rc18 to rc21.1.
    assert sorted(AGENT_BUILD_ALIASES) == sorted([
        "0.1.0-rc18", "0.1.0-rc18.1", "0.1.0-rc19", "0.1.0-rc19.2", "0.1.0-rc19.3",
        "0.1.0-rc20", "0.1.0-rc20.1", "0.1.0-rc20.2", "0.1.0-rc21", "0.1.0-rc21.1"])
    assert set(AGENT_BUILD_ALIASES.values()) == {"0.1.0-rc17"}
    if GIT is None:
        return
    proc = subprocess.run([GIT, "-C", str(REPO_ROOT), "tag", "-l", "v*"], capture_output=True, text=True)
    tags = set(proc.stdout.split())
    if "v0.1.0-rc17" not in tags:
        pytest.skip("release tags not fetched")
    agent = cv.load_manifest(MANIFEST)["components"]["agent"]
    for alias in AGENT_BUILD_ALIASES:
        assert f"v{alias}" in tags, alias
        files = cv.changed_files(REPO_ROOT, "v0.1.0-rc17", f"v{alias}")
        assert cv.component_files(agent, files) == [], f"agent changed between rc17 and {alias}"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> TestClient:
    monkeypatch.setenv("VAULT_BUILD_VERSION", "0.1.0-rc22")
    monkeypatch.setenv("VAULT_BUILD_COMMIT", COMMIT)
    for key in RC22_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings(vault_api_key=TEST_API_KEY, db_path=str(tmp_path / "vault.db"),
                        cache_root=str(tmp_path / "cache"), log_level="INFO",
                        dist_dir=str(tmp_path / "dist"))
    return TestClient(create_app(settings))


def test_dist_names_and_urls_follow_each_clients_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    exe = "vault-agent-v0.1.0-rc17-windows-amd64.exe"
    (dist / exe).write_bytes(b"exe")
    (dist / "vault-agent-v0.1.0-rc22-windows-amd64.exe").write_bytes(b"wrong version")
    with _client(tmp_path, monkeypatch, RC22_ENV) as c:
        body = c.get("/v1/dist", headers=AUTH).json()
        arts = {a["id"]: a for a in body["artifacts"]}
        assert arts["agent-windows-amd64"]["name"] == exe
        assert arts["agent-windows-amd64"]["version"] == "0.1.0-rc17"
        assert arts["agent-windows-amd64"]["local"] is True
        assert arts["sha256sums"]["release_url"] == (
            "https://github.com/steamhangar/steamhangar/releases/download/v0.1.0-rc17/SHA256SUMS")
        assert arts["app-android"]["name"] == "steamhangar-app-v0.1.0-rc22.apk"
        assert arts["app-android"]["release_url"].endswith("/v0.1.0-rc22/steamhangar-app-v0.1.0-rc22.apk")
        assert body["version"] == "0.1.0-rc22"
        assert body["clients"]["agent"]["version"] == "0.1.0-rc17"
        assert body["clients"]["agent"]["minimum"] == "0.1.0-rc17"
        assert "0.1.0-rc21.1" in body["clients"]["agent"]["same_build"]
        assert body["clients"]["app"]["build"] == "0.1.0-rc22"
        assert c.get(f"/dist/{exe}").content == b"exe"
        assert c.get("/dist/vault-agent-v0.1.0-rc22-windows-amd64.exe").status_code == 404
        settings = c.get("/v1/settings", headers=AUTH).json()
        assert settings["clients"] == body["clients"]
        assert settings["server_version"] == "0.1.0-rc22"


def test_dist_without_client_env_keeps_the_server_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(tmp_path, monkeypatch, {}) as c:
        body = c.get("/v1/dist", headers=AUTH).json()
        assert body["clients"] == {"agent": None, "app": None}
        names = {a["id"]: a["name"] for a in body["artifacts"]}
        assert names["agent-linux-amd64"] == "vault-agent-v0.1.0-rc22-linux-amd64"
        assert names["app-android"] == "steamhangar-app-v0.1.0-rc22.apk"
        assert c.get("/v1/settings", headers=AUTH).json()["clients"] == {"agent": None, "app": None}
        # Read-only: PATCH refuses it like server_version.
        r = c.patch("/v1/settings", headers=AUTH, json={"clients": {}})
        assert r.status_code == 422
