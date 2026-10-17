"""WP APP-TEST-2: CI refreshes the screenshot goldens on wp/*-goldens branches.

A push to a `wp/<name>-goldens` branch runs two ci.yml jobs:

- `android-goldens-record` (read-only token) records every
  `*ScreenshotTest` class (APP-TEST-2: Library, Clients, ...), runs android-tests' task list in verify mode on that tree and
  uploads only the PNGs as an artifact;
- `android-goldens-push` (contents: write, no build code at all) downloads
  them, accepts exactly the union of the shot names of every
  `*ScreenshotTest.kt` under app/app/src/testDebug as real PNGs, commits changed ones as github-actions[bot] and pushes fast-forward
  to the same branch.

Gradle cannot run here, so what is pinned is the jobs' contract: trigger
guards, permissions, which job may hold the token, pinned actions, step
order, the no-force push, and -- executed for real against temporary
directories and git repositories -- the branch guard, the artifact
validation and the local commit step.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
README = REPO_ROOT / "app" / "README.md"
TEST_FILE_REL = "app/app/src/testDebug/java/dev/steamvault/app/ui/library/LibraryScreenshotTest.kt"
SECOND_TEST_REL = "app/app/src/testDebug/java/dev/steamvault/app/ui/clients/ClientsScreenshotTest.kt"
SCREENSHOT_ROOT_REL = "app/app/src/testDebug/java"
RECORD = "android-goldens-record"
PUSH = "android-goldens-push"
WEB_PUSH = "web-goldens-push"  # WP WEB-GOLDENS-1; its own pins live in test_web_goldens_1_ci.py
GOLDENS = "app/app/src/testDebug/screenshots"
ARTIFACT = "app-screenshot-goldens"
RECORD_ACTIONS = {"actions/checkout", "actions/setup-java", "actions/upload-artifact"}
PUSH_ACTIONS = {"actions/checkout", "actions/download-artifact"}
DOWNLOAD_PIN = "actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131"
REFRESH_PREDICATE = (
    "github.event_name == 'push' && github.ref_type == 'branch' "
    "&& startsWith(github.ref, 'refs/heads/wp/') && endsWith(github.ref, '-goldens') "
    "&& !github.event.repository.fork"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"rest"
BASH = shutil.which("bash")
GIT = shutil.which("git")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


def _job(name: str) -> dict[str, Any]:
    return _workflow()["jobs"][name]


def _steps(job: str) -> list[dict[str, Any]]:
    return _job(job)["steps"]


def _step(job: str, name: str) -> dict[str, Any]:
    found = [s for s in _steps(job) if s.get("name") == name]
    assert len(found) == 1, f"{job}: {name}"
    return found[0]


def _uses(job: str, prefix: str) -> list[dict[str, Any]]:
    return [s for s in _steps(job) if str(s.get("uses", "")).startswith(prefix + "@")]


def _norm(expr: str) -> str:
    return " ".join(expr.split())


def _dump(step: dict[str, Any]) -> str:
    return yaml.safe_dump(step)


# ---------------------------------------------------------------------------
# Trigger and permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", [RECORD, PUSH])
def test_refresh_jobs_run_only_on_pushes_to_wp_goldens_branches(job: str) -> None:
    guard = _norm(_job(job)["if"])
    assert guard == REFRESH_PREDICATE
    for forbidden in ("||", "pull_request", "refs/heads/main", "refs/tags", "workflow_dispatch", "schedule"):
        assert forbidden not in guard, forbidden


def test_android_tests_is_skipped_exactly_when_the_refresh_jobs_start() -> None:
    # No regex in expressions: the complement must be the SAME predicate,
    # so a branch can never fall between android-tests and the refresh.
    guard = _norm(_job("android-tests")["if"])
    assert guard == (
        "(github.event_name == 'push' || github.event_name == 'pull_request' "
        "|| github.event_name == 'workflow_dispatch') "
        f"&& !({REFRESH_PREDICATE})"
    )


def test_workflow_push_trigger_covers_the_goldens_branches() -> None:
    wf = _workflow()
    on = wf.get("on", wf.get(True))  # PyYAML reads a bare `on:` as True
    assert "wp/**" in on["push"]["branches"]


def test_only_the_push_jobs_may_write_and_only_contents() -> None:
    # WP WEB-GOLDENS-1 added the web twin (web-goldens-push, pinned in
    # test_web_goldens_1_ci.py); it is the only other job allowed to write.
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read"}
    assert _job(PUSH)["permissions"] == {"contents": "write"}
    assert _job(RECORD)["permissions"] == {"contents": "read"}
    for name, job in wf["jobs"].items():
        if name not in (PUSH, WEB_PUSH):
            assert "write" not in str(job.get("permissions", "")), f"{name} must not gain write permissions"


def test_push_job_needs_the_record_job() -> None:
    assert _job(PUSH)["needs"] in (RECORD, [RECORD])


@pytest.mark.parametrize("job", [RECORD, PUSH])
def test_concurrency_is_per_branch(job: str) -> None:
    conc = _job(job)["concurrency"]
    assert conc["group"] == f"{job}-${{{{ github.ref }}}}"


# ---------------------------------------------------------------------------
# The push job runs no build code; the token lives there only
# ---------------------------------------------------------------------------


def test_push_job_runs_no_gradle_java_cache_or_repo_script() -> None:
    for s in _steps(PUSH):
        text = _dump(s)
        for forbidden in ("gradlew", "gradle", "setup-java", "actions/cache", ".github/scripts", "python"):
            assert forbidden not in text, f"{s.get('name', s.get('uses'))}: {forbidden}"
        assert "cache" not in s.get("with", {}), s


def test_only_the_push_step_references_the_token() -> None:
    wf = _workflow()
    hits = []
    for job_name, job in wf["jobs"].items():
        for s in job.get("steps", []):
            text = _dump(s)
            if "GITHUB_TOKEN" in text or re.search(r"\$\{\{[^}]*secrets\.", text):
                hits.append((job_name, s.get("name")))
    assert hits == [(PUSH, "goldens: push to the same branch"), (WEB_PUSH, "goldens: push to the same branch")]
    assert _step(PUSH, "goldens: push to the same branch")["env"] == {"PUSH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}


def test_actions_are_sha_pinned_and_only_the_expected_ones() -> None:
    other_uses = {
        s["uses"]
        for name, job in _workflow()["jobs"].items() if name not in (RECORD, PUSH)
        for s in job.get("steps", []) if "uses" in s
    }
    for job, allowed in ((RECORD, RECORD_ACTIONS), (PUSH, PUSH_ACTIONS)):
        for s in _steps(job):
            if "uses" not in s:
                continue
            name, _, ref = s["uses"].partition("@")
            assert name in allowed, f"{job}: unexpected action {name}"
            assert re.fullmatch(r"[0-9a-f]{40}", ref), "actions must be pinned by full commit SHA"
            if name != "actions/download-artifact":
                assert s["uses"] in other_uses, f"{s['uses']} differs from the pin the other jobs use"
    # The one new action: the official one, v7.0.0 (upload-artifact is v7.0.1).
    (download,) = _uses(PUSH, "actions/download-artifact")
    assert download["uses"] == DOWNLOAD_PIN
    assert download["with"]["name"] == ARTIFACT


@pytest.mark.parametrize("job", [RECORD, PUSH])
def test_checkout_keeps_no_credentials(job: str) -> None:
    (checkout,) = _uses(job, "actions/checkout")
    assert checkout["with"]["persist-credentials"] is False


def test_push_job_checks_out_the_recorded_commit() -> None:
    (checkout,) = _uses(PUSH, "actions/checkout")
    assert checkout["with"]["ref"] == "${{ github.sha }}"


# ---------------------------------------------------------------------------
# Step wiring
# ---------------------------------------------------------------------------


def test_record_job_step_order() -> None:
    (checkout,) = _uses(RECORD, "actions/checkout")
    order = [
        _step(RECORD, "goldens: guard branch"),
        checkout,
        _step(RECORD, "goldens: record"),
        _step(RECORD, "goldens: verify (android-tests task list)"),
        _step(RECORD, "goldens: upload recorded goldens"),
    ]
    idx = [_steps(RECORD).index(s) for s in order]
    assert idx == sorted(idx), idx


def test_push_job_step_order() -> None:
    (checkout,) = _uses(PUSH, "actions/checkout")
    (download,) = _uses(PUSH, "actions/download-artifact")
    order = [
        _step(PUSH, "goldens: guard branch"),
        checkout,
        download,
        _step(PUSH, "goldens: validate and copy"),
        _step(PUSH, "goldens: commit locally"),
        _step(PUSH, "goldens: push to the same branch"),
    ]
    idx = [_steps(PUSH).index(s) for s in order]
    assert idx == sorted(idx), idx


def test_both_jobs_share_the_same_guard_step() -> None:
    assert _step(RECORD, "goldens: guard branch") == _step(PUSH, "goldens: guard branch")


def test_record_step_rerecords_every_golden_in_place() -> None:
    step = _step(RECORD, "goldens: record")
    assert step["working-directory"] == "app"
    run = step["run"]
    assert "rm -f app/src/testDebug/screenshots/*.png" in run
    assert "-Psteamhangar.screenshots.record=true" in run
    assert "testDebugUnitTest" in run
    # APP-TEST-2: every screenshot class, not the Library one only.
    assert "--tests '*ScreenshotTest'" in run
    assert "LibraryScreenshotTest" not in run
    assert "set -o pipefail" in run
    assert '| tee "${RUNNER_TEMP}/android-gradle.log"' in run


def test_verify_step_runs_the_android_tests_task_list_without_record() -> None:
    verify = _step(RECORD, "goldens: verify (android-tests task list)")
    tasks = "assembleDebug testDebugUnitTest testReleaseUnitTest lintDebug"
    assert f"bash gradlew {tasks}" in verify["run"]
    assert "screenshots.record" not in verify["run"]
    assert "set -o pipefail" in verify["run"]
    assert '| tee -a "${RUNNER_TEMP}/android-gradle.log"' in verify["run"]
    assert "if" not in verify, "the verify pass must always run"
    android = [s for s in _steps("android-tests") if str(s.get("name", "")).startswith("gradlew ")]
    assert len(android) == 1 and f"bash gradlew {tasks}" in android[0]["run"]


def test_record_job_annotates_failures_like_android_tests() -> None:
    names = [s["name"] for s in _steps("android-tests") if str(s.get("name", "")).startswith("annotate: ")]
    assert len(names) == 4
    for name in names:
        ours = _step(RECORD, name)
        assert ours == _step("android-tests", name)
        assert ours["if"] == "failure()"


def test_upload_carries_only_the_goldens() -> None:
    up = _step(RECORD, "goldens: upload recorded goldens")
    assert up["with"]["name"] == ARTIFACT
    assert up["with"]["path"] == f"{GOLDENS}/*.png"
    assert up["with"]["if-no-files-found"] == "error"
    assert "if" not in up, "only after a green verify"


def test_push_is_fast_forward_to_the_same_branch() -> None:
    push = _step(PUSH, "goldens: push to the same branch")
    assert push["if"] == "steps.commit.outputs.changed == 'true'"
    run = push["run"]
    assert 'push origin "HEAD:${GITHUB_REF}"' in run
    assert "core.hooksPath=/dev/null" in run
    assert "add-mask" in run
    assert not re.search(r"(--force|--force-with-lease|\s-f\b|HEAD:\+|\+HEAD)", run), "never force-push"


def test_readme_documents_the_refresh_flow_without_an_isolation_claim() -> None:
    text = README.read_text(encoding="utf-8")
    for needle in ("wp/<name>-goldens", RECORD, PUSH, "git fetch", "accepts **every**"):
        assert needle in text, needle


# ---------------------------------------------------------------------------
# The guard step, executed
# ---------------------------------------------------------------------------


def _clean_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GIT_", "RUNNER_"))}
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    return env


def _run_guard(tmp_path: Path, event: str, ref: str, ref_type: str) -> subprocess.CompletedProcess[str]:
    env = _clean_env(tmp_path)
    env.update(GITHUB_EVENT_NAME=event, GITHUB_REF=ref, GITHUB_REF_TYPE=ref_type)
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", _step(PUSH, "goldens: guard branch")["run"]],
        capture_output=True, text=True, env=env, timeout=30, check=False,
    )


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("ref", ["refs/heads/wp/app-test-1b-goldens", "refs/heads/wp/d12-goldens"])
def test_guard_accepts_wp_goldens_branches(tmp_path: Path, ref: str) -> None:
    p = _run_guard(tmp_path, "push", ref, "branch")
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize(
    ("event", "ref", "ref_type"),
    [
        ("push", "refs/heads/main", "branch"),
        ("push", "refs/tags/v0.2.0-goldens", "tag"),
        ("push", "refs/heads/wp/app-test-1b", "branch"),
        ("push", "refs/heads/wp/a/b-goldens", "branch"),
        ("push", "refs/heads/feature/x-goldens", "branch"),
        ("push", "refs/heads/wp/-goldens", "branch"),
        ("pull_request", "refs/pull/7/merge", ""),
        ("workflow_dispatch", "refs/heads/wp/x-goldens", "branch"),
    ],
)
def test_guard_rejects_everything_else(tmp_path: Path, event: str, ref: str, ref_type: str) -> None:
    p = _run_guard(tmp_path, event, ref, ref_type)
    assert p.returncode != 0
    assert "::error::" in p.stdout


# ---------------------------------------------------------------------------
# Validation and commit steps, executed against a temporary repository
# ---------------------------------------------------------------------------

SHOTS = ["library_a", "library_b_night"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path, shots: list[str] = SHOTS) -> Path:
    repo = tmp_path / "repo"
    test_file = repo / TEST_FILE_REL
    test_file.parent.mkdir(parents=True)
    calls = "\n".join(f'    fun t{i}() = shoot("{n}", PIXEL_411)' for i, n in enumerate(shots))
    test_file.write_text(f"class T {{\n{calls}\n    private fun shoot(\n        name: String,\n    ) {{}}\n}}\n",
                         encoding="utf-8")
    (repo / GOLDENS).mkdir(parents=True)
    (repo / GOLDENS / "library_a.png").write_bytes(PNG + b"old-a")
    (repo / GOLDENS / "gone.png").write_bytes(PNG + b"gone")
    (repo / "other.txt").write_text("tracked\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "wp/x-goldens")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return repo


def _run_step(tmp_path: Path, repo: Path, name: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
    out_file = tmp_path / "output"
    summary_file = tmp_path / "summary.md"
    env = _clean_env(tmp_path)
    env.update(
        GITHUB_OUTPUT=str(out_file), GITHUB_STEP_SUMMARY=str(summary_file),
        GITHUB_RUN_ID="4242", GITHUB_SHA="0" * 40, RUNNER_TEMP=str(tmp_path / "runner"),
    )
    p = subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", _step(PUSH, name)["run"]],
        cwd=repo, capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    outputs = dict(
        ln.split("=", 1) for ln in out_file.read_text(encoding="utf-8").splitlines() if "=" in ln
    ) if out_file.exists() else {}
    return p, outputs, summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""


def _download(tmp_path: Path, files: dict[str, bytes]) -> Path:
    dl = tmp_path / "runner" / "goldens"
    dl.mkdir(parents=True)
    for name, data in files.items():
        (dl / name).parent.mkdir(parents=True, exist_ok=True)
        (dl / name).write_bytes(data)
    return dl


VALIDATE = "goldens: validate and copy"
COMMIT = "goldens: commit locally"


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_validation_accepts_exactly_the_shots_and_mirrors_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _download(tmp_path, {"library_a.png": PNG + b"new-a", "library_b_night.png": PNG + b"new-b"})
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode == 0, p.stdout + p.stderr
    assert sorted(f.name for f in (repo / GOLDENS).iterdir()) == ["library_a.png", "library_b_night.png"]
    assert (repo / GOLDENS / "library_a.png").read_bytes() == PNG + b"new-a"


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ({"library_a.png": b"GIF89a-not-png", "library_b_night.png": PNG}, "not a PNG"),
        ({"library_a.png": PNG, "library_b_night.png": PNG, "evil.png": PNG}, "do not match"),
        ({"library_a.png": PNG}, "do not match"),
        ({"library_a.png": PNG, "library_b_night.png": PNG, "x.sh": b"#!/bin/sh"}, "unexpected name"),
        ({"library_a.png": PNG, "Library_B_Night.png": PNG}, "unexpected name"),
        ({"library_a.png": PNG, "sub/library_b_night.png": PNG}, "unexpected name"),
        ({"library_a.png": PNG, "../library_b_night.png": PNG}, "do not match"),
        ({}, "empty"),
    ],
)
def test_validation_rejects_anything_else(tmp_path: Path, files: dict[str, bytes], reason: str) -> None:
    repo = _repo(tmp_path)
    before = {f.name: f.read_bytes() for f in (repo / GOLDENS).iterdir()}
    _download(tmp_path, files)
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode != 0
    assert "::error title=Golden refresh rejected::" in p.stdout and reason in p.stdout, p.stdout
    # Nothing was copied before the verdict.
    assert {f.name: f.read_bytes() for f in (repo / GOLDENS).iterdir()} == before


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_validation_rejects_a_symlink(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dl = _download(tmp_path, {"library_a.png": PNG})
    (dl / "library_b_night.png").symlink_to(dl / "library_a.png")
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode != 0 and "not a regular file" in p.stdout


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_validation_rejects_duplicate_shot_names(tmp_path: Path) -> None:
    repo = _repo(tmp_path, shots=["library_a", "library_a"])
    _download(tmp_path, {"library_a.png": PNG})
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode != 0 and "duplicate shot names" in p.stdout


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_validation_parses_the_real_kotlin_tests(tmp_path: Path) -> None:
    # The step's grep must find the same names the APP-TEST-1b pytest does,
    # across EVERY real *ScreenshotTest.kt (APP-TEST-2).
    real = sorted(p for p in (REPO_ROOT / SCREENSHOT_ROOT_REL).rglob("*ScreenshotTest.kt") if p.is_file())
    assert len(real) >= 2, real
    repo = _repo(tmp_path)
    names: list[str] = []
    for f in real:
        src = f.read_text(encoding="utf-8")
        names += re.findall(r'\bshoot\(\s*"([^"]+)"', src)
        target = repo / f.relative_to(REPO_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src, encoding="utf-8")
    _download(tmp_path, {f"{n}.png": PNG for n in names})
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode == 0, p.stdout + p.stderr
    assert sorted(f.stem for f in (repo / GOLDENS).iterdir()) == sorted(names)


def _add_second_class(repo: Path, shots: list[str]) -> None:
    f = repo / SECOND_TEST_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    calls = "\n".join(f'    fun s{i}() = shoot("{n}", 411)' for i, n in enumerate(shots))
    f.write_text(f"class C {{\n{calls}\n}}\n", encoding="utf-8")


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_validation_takes_the_union_of_every_screenshot_class(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add_second_class(repo, ["clients_a"])
    _download(tmp_path, {"library_a.png": PNG, "library_b_night.png": PNG, "clients_a.png": PNG + b"c"})
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / GOLDENS / "clients_a.png").read_bytes() == PNG + b"c"


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_validation_rejects_a_missing_shot_of_a_second_class(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add_second_class(repo, ["clients_a"])
    _download(tmp_path, {"library_a.png": PNG, "library_b_night.png": PNG})
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode != 0 and "do not match" in p.stdout, p.stdout


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_validation_rejects_a_shot_name_used_by_two_classes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add_second_class(repo, ["library_a"])
    _download(tmp_path, {"library_a.png": PNG, "library_b_night.png": PNG})
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode != 0 and "duplicate shot names" in p.stdout, p.stdout


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_commit_step_makes_no_commit_when_nothing_changed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    p, outputs, summary = _run_step(tmp_path, repo, COMMIT)
    assert p.returncode == 0, p.stderr
    assert outputs == {"changed": "false"}
    assert _git(repo, "rev-parse", "HEAD") == head
    assert "unchanged" in summary


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")
def test_commit_step_commits_only_the_goldens_as_the_bot(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / GOLDENS / "library_a.png").write_bytes(PNG + b"new-a")
    (repo / GOLDENS / "library_b_night.png").write_bytes(PNG + b"new-b")
    (repo / GOLDENS / "gone.png").unlink()
    (repo / "other.txt").write_text("touched\n", encoding="utf-8")
    p, outputs, summary = _run_step(tmp_path, repo, COMMIT)
    assert p.returncode == 0, p.stderr
    assert outputs == {"changed": "true"}
    assert _git(repo, "rev-parse", "HEAD~1").strip() == base
    files = _git(repo, "show", "--name-status", "--format=", "HEAD").split()
    assert files == ["D", f"{GOLDENS}/gone.png", "M", f"{GOLDENS}/library_a.png",
                     "A", f"{GOLDENS}/library_b_night.png"]
    author = _git(repo, "log", "-1", "--format=%an <%ae>|%s")
    assert author.strip() == (
        "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"
        "|test(app): refresh screenshot goldens from CI"
    )
    assert _git(repo, "status", "--porcelain").strip() == "M other.txt"
    assert "library_b_night.png" in summary and "gone.png" in summary
