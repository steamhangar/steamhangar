"""WP WEB-GOLDENS-1: screenshot goldens for the web UI in ci.yml.

Three jobs, the web twin of the Android ones pinned in
test_app_test_2_golden_refresh_ci.py:

- `web-goldens` (verify): after `web-tests`, serves web/ with
  tools/web-serve.mjs, shoots with tools/web-shot.mjs, compares with
  tools/png-diff.mjs against web/tests/goldens (missing golden = notice).
  Skipped on wp/*-goldens pushes, exactly when the refresh jobs start.
- `web-goldens-record` (read token): re-records the set, renders a second
  time and compares, uploads only the PNGs.
- `web-goldens-push` (write token, no build code): validates the artifact
  against the page/width lists in tools/web-shot.mjs, commits as
  github-actions[bot], rebases over android-goldens-push's commit if that
  is all that moved, and pushes fast-forward to the same branch.

Chromium cannot run here; pinned is the contract (triggers, permissions,
pins, the Chromium/font install the two render jobs share, step order) and
-- executed for real in temporary directories and repositories -- the guard
step, the artifact validation, the commit step and the push step's
branch-moved logic against a local bare remote.
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
TOOLS_README = REPO_ROOT / "tools" / "README.md"
SHOT_TOOL = REPO_ROOT / "tools" / "web-shot.mjs"
VERIFY = "web-goldens"
RECORD = "web-goldens-record"
PUSH = "web-goldens-push"
ANDROID_RECORD = "android-goldens-record"
ANDROID_PUSH = "android-goldens-push"
GOLDENS = "web/tests/goldens"
ARTIFACT = "web-screenshot-goldens"
CHROMIUM_INSTALL = "npx --yes playwright-core@1.63.0 install --with-deps chromium"
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
# Triggers, permissions, wiring
# ---------------------------------------------------------------------------


def test_verify_job_runs_after_web_tests_and_is_skipped_exactly_when_the_refresh_jobs_start() -> None:
    job = _job(VERIFY)
    assert job["needs"] in ("web-tests", ["web-tests"])
    assert _norm(job["if"]) == _norm(_job("android-tests")["if"])
    assert _norm(job["if"]) == (
        "(github.event_name == 'push' || github.event_name == 'pull_request' "
        "|| github.event_name == 'workflow_dispatch') "
        f"&& !({REFRESH_PREDICATE})"
    )
    assert "permissions" not in job, "read-only like every other job"


def test_record_job_runs_only_on_pushes_to_wp_goldens_branches() -> None:
    guard = _norm(_job(RECORD)["if"])
    assert guard == REFRESH_PREDICATE
    assert _job(RECORD)["permissions"] == {"contents": "read"}


def test_push_job_runs_only_on_pushes_to_wp_goldens_branches_after_both_refreshes() -> None:
    job = _job(PUSH)
    guard = _norm(job["if"])
    assert guard == f"!cancelled() && needs.{RECORD}.result == 'success' && {REFRESH_PREDICATE}"
    for forbidden in ("||", "pull_request", "refs/heads/main", "refs/tags", "workflow_dispatch", "schedule"):
        assert forbidden not in guard, forbidden
    assert sorted(job["needs"]) == sorted([RECORD, ANDROID_PUSH])
    assert job["permissions"] == {"contents": "write"}


@pytest.mark.parametrize("job", [RECORD, PUSH])
def test_concurrency_is_per_branch(job: str) -> None:
    assert _job(job)["concurrency"]["group"] == f"{job}-${{{{ github.ref }}}}"


def test_push_job_runs_no_node_npx_or_repo_script() -> None:
    for s in _steps(PUSH):
        text = _dump(s)
        for forbidden in ("node ", "npx", "setup-node", "actions/cache", ".github/scripts", "python", "apt-get"):
            assert forbidden not in text, f"{s.get('name', s.get('uses'))}: {forbidden}"


def test_only_the_push_step_references_the_token() -> None:
    hits = [
        s.get("name")
        for s in _steps(PUSH)
        if "GITHUB_TOKEN" in _dump(s) or re.search(r"\$\{\{[^}]*secrets\.", _dump(s))
    ]
    assert hits == ["goldens: push to the same branch"]
    for job in (VERIFY, RECORD):
        for s in _steps(job):
            assert "secrets." not in _dump(s) and "GITHUB_TOKEN" not in _dump(s), (job, s.get("name"))


def test_actions_are_the_same_sha_pins_the_android_jobs_use() -> None:
    android_uses = {
        s["uses"] for job in ("web-tests", ANDROID_RECORD, ANDROID_PUSH) for s in _steps(job) if "uses" in s
    }
    allowed = {"actions/checkout", "actions/setup-node", "actions/upload-artifact", "actions/download-artifact"}
    for job in (VERIFY, RECORD, PUSH):
        for s in _steps(job):
            if "uses" not in s:
                continue
            name, _, ref = s["uses"].partition("@")
            assert name in allowed, f"{job}: unexpected action {name}"
            assert re.fullmatch(r"[0-9a-f]{40}", ref), "actions must be pinned by full commit SHA"
            assert s["uses"] in android_uses, f"{job}: {s['uses']} differs from the pin the other jobs use"
    assert not _uses(PUSH, "actions/setup-node") and not _uses(PUSH, "actions/upload-artifact")
    (download,) = _uses(PUSH, "actions/download-artifact")
    assert download["with"]["name"] == ARTIFACT


@pytest.mark.parametrize("job", [RECORD, PUSH])
def test_checkout_keeps_no_credentials(job: str) -> None:
    (checkout,) = _uses(job, "actions/checkout")
    assert checkout["with"]["persist-credentials"] is False


def test_push_job_checks_out_the_recorded_commit() -> None:
    (checkout,) = _uses(PUSH, "actions/checkout")
    assert checkout["with"]["ref"] == "${{ github.sha }}"


def test_guard_step_is_the_android_jobs_guard_step() -> None:
    android = _step(ANDROID_PUSH, "goldens: guard branch")
    assert _step(RECORD, "goldens: guard branch") == android
    assert _step(PUSH, "goldens: guard branch") == android


# ---------------------------------------------------------------------------
# The render jobs: same Chromium, same fonts, same server
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", [VERIFY, RECORD])
def test_render_jobs_install_the_pinned_chromium_and_liberation_fonts(job: str) -> None:
    run = _step(job, "goldens: install fonts and Chromium")["run"]
    assert CHROMIUM_INSTALL in run
    assert "fonts-liberation" in run
    assert "--no-install-recommends" in run
    assert "@latest" not in run and re.search(r"playwright-core@\d+\.\d+\.\d+ ", run)
    (node,) = _uses(job, "actions/setup-node")
    assert node["with"]["node-version"] == _uses("web-tests", "actions/setup-node")[0]["with"]["node-version"]


def test_render_jobs_share_identical_install_and_serve_steps() -> None:
    for name in ("goldens: install fonts and Chromium", "goldens: serve web/"):
        assert _step(VERIFY, name) == _step(RECORD, name)
    serve = _step(VERIFY, "goldens: serve web/")["run"]
    assert "node tools/web-serve.mjs --port 8787" in serve
    assert "curl -fsS -o /dev/null http://127.0.0.1:8787/library" in serve


def test_chromium_pin_is_documented_once_in_tools_readme() -> None:
    text = TOOLS_README.read_text(encoding="utf-8")
    version = re.search(r"playwright-core@(\d+\.\d+\.\d+)", CHROMIUM_INSTALL).group(1)
    assert f"playwright-core@{version}" in text
    assert "fonts-liberation" in text
    for needle in ("wp/<name>-goldens", RECORD, PUSH, "web-shot-fonts.conf"):
        assert needle in text, needle
    others = set(re.findall(r"playwright-core@(\d+\.\d+\.\d+)", text)) - {version}
    assert not others, f"tools/README.md names a different playwright-core version: {others}"


def test_verify_job_steps() -> None:
    shoot = _step(VERIFY, "goldens: shoot")["run"]
    assert 'node tools/web-shot.mjs --base http://127.0.0.1:8787 --out "${RUNNER_TEMP}/web-shots"' in shoot
    verify = _norm(_step(VERIFY, "goldens: verify")["run"])
    assert verify.startswith("node tools/png-diff.mjs --expected web/tests/goldens --actual")
    assert "--missing warn" in verify and "--github" in verify
    assert "if" not in _step(VERIFY, "goldens: verify")
    shots = _step(VERIFY, "goldens: upload screenshots")
    assert shots["if"] == "${{ !cancelled() }}" and shots["with"]["name"] == "web-screenshots"
    diffs = _step(VERIFY, "goldens: upload diffs")
    assert diffs["if"] == "failure()" and diffs["with"]["name"] == "web-screenshots-diff"
    assert diffs["with"]["path"] == "${{ runner.temp }}/web-shots-diff/*.png"


def test_record_job_rerecords_in_place_then_verifies_a_second_render() -> None:
    record = _step(RECORD, "goldens: record")["run"]
    assert f"rm -f {GOLDENS}/*.png" in record
    assert f"node tools/web-shot.mjs --base http://127.0.0.1:8787 --out {GOLDENS}" in record
    verify = _norm(_step(RECORD, "goldens: verify (second render)")["run"])
    assert 'node tools/web-shot.mjs --base http://127.0.0.1:8787 --out "${RUNNER_TEMP}/web-shots"' in verify
    assert f"node tools/png-diff.mjs --expected {GOLDENS} --actual" in verify
    assert "--missing warn" not in verify, "a missing golden in the second render is a failure here"
    assert "if" not in _step(RECORD, "goldens: verify (second render)")
    up = _step(RECORD, "goldens: upload recorded goldens")
    assert up["with"]["name"] == ARTIFACT
    assert up["with"]["path"] == f"{GOLDENS}/*.png"
    assert up["with"]["if-no-files-found"] == "error"
    assert "if" not in up, "only after a green verify"


def test_record_job_step_order() -> None:
    (checkout,) = _uses(RECORD, "actions/checkout")
    order = [
        _step(RECORD, "goldens: guard branch"), checkout,
        _step(RECORD, "goldens: install fonts and Chromium"), _step(RECORD, "goldens: serve web/"),
        _step(RECORD, "goldens: record"), _step(RECORD, "goldens: verify (second render)"),
        _step(RECORD, "goldens: upload recorded goldens"),
    ]
    idx = [_steps(RECORD).index(s) for s in order]
    assert idx == sorted(idx), idx


def test_push_job_step_order() -> None:
    (checkout,) = _uses(PUSH, "actions/checkout")
    (download,) = _uses(PUSH, "actions/download-artifact")
    order = [
        _step(PUSH, "goldens: guard branch"), checkout, download,
        _step(PUSH, "goldens: validate and copy"), _step(PUSH, "goldens: commit locally"),
        _step(PUSH, "goldens: push to the same branch"),
    ]
    idx = [_steps(PUSH).index(s) for s in order]
    assert idx == sorted(idx), idx


def test_push_is_fast_forward_to_the_same_branch_after_an_android_only_rebase() -> None:
    push = _step(PUSH, "goldens: push to the same branch")
    assert push["if"] == "steps.commit.outputs.changed == 'true'"
    assert push["env"] == {"PUSH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    run = push["run"]
    assert 'push origin "HEAD:${GITHUB_REF}"' in run
    assert "core.hooksPath=/dev/null" in run
    assert "add-mask" in run
    assert "merge-base --is-ancestor" in run
    assert "app/app/src/testDebug/screenshots/" in run
    assert not re.search(r"(--force|--force-with-lease|\s-f\b|HEAD:\+|\+HEAD)", run), "never force-push"


def test_shot_tool_declares_pages_and_widths_on_single_greppable_lines() -> None:
    source = SHOT_TOOL.read_text(encoding="utf-8")
    pages = re.search(r'^export const DEFAULT_PAGES = \[([^\]]+)\];$', source, re.M)
    widths = re.search(r"^export const DEFAULT_WIDTHS = \[([^\]]+)\];$", source, re.M)
    assert pages and widths
    assert set(re.findall(r'"([a-z-]+)"', pages.group(1))) >= {"library", "clients", "downloads", "settings", "kit"}
    assert [int(w) for w in re.findall(r"\d+", widths.group(1))] == [1400, 1000, 400]


# ---------------------------------------------------------------------------
# Executed steps
# ---------------------------------------------------------------------------


def _clean_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GIT_", "RUNNER_"))}
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


PAGES = ["library", "kit"]
WIDTHS = [1400, 400]
ANDROID_GOLDENS = "app/app/src/testDebug/screenshots"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    tool = repo / "tools" / "web-shot.mjs"
    tool.parent.mkdir(parents=True)
    pages = ", ".join(f'"{p}"' for p in PAGES)
    widths = ", ".join(str(w) for w in WIDTHS)
    tool.write_text(
        f"// stub\nexport const DEFAULT_PAGES = [{pages}];\nexport const DEFAULT_WIDTHS = [{widths}];\n",
        encoding="utf-8",
    )
    (repo / GOLDENS).mkdir(parents=True)
    (repo / GOLDENS / "library-1400.png").write_bytes(PNG + b"old")
    (repo / GOLDENS / "gone-1400.png").write_bytes(PNG + b"gone")
    (repo / GOLDENS / "README.md").write_text("kept\n", encoding="utf-8")
    (repo / ANDROID_GOLDENS).mkdir(parents=True)
    (repo / ANDROID_GOLDENS / "library_a.png").write_bytes(PNG + b"android")
    (repo / "other.txt").write_text("tracked\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "wp/x-goldens")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return repo


def _run_step(tmp_path: Path, repo: Path, name: str, extra_env: dict[str, str] | None = None):
    out_file = tmp_path / "output"
    summary_file = tmp_path / "summary.md"
    env = _clean_env(tmp_path)
    env.update(
        GITHUB_OUTPUT=str(out_file), GITHUB_STEP_SUMMARY=str(summary_file),
        GITHUB_RUN_ID="4242", GITHUB_SHA=_git(repo, "rev-parse", "HEAD") if (repo / ".git").exists() else "0" * 40,
        RUNNER_TEMP=str(tmp_path / "runner"), GITHUB_REF="refs/heads/wp/x-goldens",
        GITHUB_SERVER_URL="https://github.invalid", PUSH_TOKEN="not-a-real-token",
    )
    env.update(extra_env or {})
    p = subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", _step(PUSH, name)["run"]],
        cwd=repo, capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    outputs = dict(
        ln.split("=", 1) for ln in out_file.read_text(encoding="utf-8").splitlines() if "=" in ln
    ) if out_file.exists() else {}
    return p, outputs, summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""


def _download(tmp_path: Path, files: dict[str, bytes]) -> Path:
    dl = tmp_path / "runner" / "web-goldens"
    dl.mkdir(parents=True)
    for name, data in files.items():
        (dl / name).parent.mkdir(parents=True, exist_ok=True)
        (dl / name).write_bytes(data)
    return dl


VALIDATE = "goldens: validate and copy"
COMMIT = "goldens: commit locally"
PUSH_STEP = "goldens: push to the same branch"
FULL_SET = {f"{p}-{w}.png": PNG + f"{p}{w}".encode() for p in PAGES for w in WIDTHS}

needs_tools = pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")


@needs_tools
def test_validation_accepts_the_declared_names_and_mirrors_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _download(tmp_path, FULL_SET)
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode == 0, p.stdout + p.stderr
    assert sorted(f.name for f in (repo / GOLDENS).iterdir()) == sorted([*FULL_SET, "README.md"])
    assert (repo / GOLDENS / "library-1400.png").read_bytes() == FULL_SET["library-1400.png"]


@needs_tools
def test_validation_accepts_a_subset_of_pages_when_every_width_is_present(tmp_path: Path) -> None:
    # tools/web-shot.mjs skips a page the server does not serve (kit before
    # UI-KIT-1 lands); the artifact then simply lacks that page.
    repo = _repo(tmp_path)
    _download(tmp_path, {k: v for k, v in FULL_SET.items() if k.startswith("library-")})
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode == 0, p.stdout + p.stderr
    assert sorted(f.name for f in (repo / GOLDENS).iterdir()) == ["README.md", "library-1400.png", "library-400.png"]


@needs_tools
@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ({}, "empty"),
        ({"library-1400.png": PNG + b"x"}, "of 2 widths"),
        ({**FULL_SET, "library-1000.png": PNG + b"x"}, "not a page/width"),
        ({**FULL_SET, "about-1400.png": PNG + b"x"}, "not a page/width"),
        ({**FULL_SET, "Library-1400.png": PNG + b"x"}, "unexpected name"),
        ({**FULL_SET, "library-1400.png": b"not a png"}, "not a PNG"),
        ({**FULL_SET, "sub/library-1400.png": PNG + b"x"}, "unexpected name"),
    ],
)
def test_validation_rejects_everything_else_and_leaves_the_tree_alone(
    tmp_path: Path, files: dict[str, bytes], reason: str
) -> None:
    repo = _repo(tmp_path)
    _download(tmp_path, files)
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode != 0
    assert "::error title=Golden refresh rejected::" in p.stdout
    assert reason in p.stdout, p.stdout
    assert sorted(f.name for f in (repo / GOLDENS).iterdir()) == ["README.md", "gone-1400.png", "library-1400.png"]
    assert (repo / GOLDENS / "library-1400.png").read_bytes() == PNG + b"old"


@needs_tools
def test_validation_fails_when_the_tool_does_not_declare_its_lists(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "tools" / "web-shot.mjs").write_text("export const DEFAULT_PAGES = [\n  'library',\n];\n", encoding="utf-8")
    _download(tmp_path, FULL_SET)
    p, _, _ = _run_step(tmp_path, repo, VALIDATE)
    assert p.returncode != 0
    assert "cannot read DEFAULT_PAGES" in p.stdout


@needs_tools
def test_commit_step_commits_only_the_goldens_as_the_bot(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _download(tmp_path, FULL_SET)
    assert _run_step(tmp_path, repo, VALIDATE)[0].returncode == 0
    (repo / "other.txt").write_text("modified, must not be committed\n", encoding="utf-8")
    p, outputs, summary = _run_step(tmp_path, repo, COMMIT)
    assert p.returncode == 0, p.stdout + p.stderr
    assert outputs == {"changed": "true"}
    assert "Web screenshot goldens: refreshed" in summary
    assert _git(repo, "log", "-1", "--format=%an <%ae>") == (
        "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"
    )
    assert _git(repo, "log", "-1", "--format=%s") == "test(web): refresh screenshot goldens from CI"
    changed = set(_git(repo, "show", "--name-status", "--format=", "HEAD").splitlines())
    assert changed == {"D\tweb/tests/goldens/gone-1400.png", "M\tweb/tests/goldens/library-1400.png",
                       "A\tweb/tests/goldens/library-400.png", "A\tweb/tests/goldens/kit-1400.png",
                       "A\tweb/tests/goldens/kit-400.png"}
    assert "other.txt" in _git(repo, "status", "--porcelain")


@needs_tools
def test_commit_step_reports_unchanged_when_the_goldens_are_identical(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _download(tmp_path, {"library-1400.png": PNG + b"old", "library-400.png": PNG + b"x"})
    assert _run_step(tmp_path, repo, VALIDATE)[0].returncode == 0
    # gone-1400.png was deleted by the copy, so this IS a change; make it a no-op:
    (repo / GOLDENS / "gone-1400.png").write_bytes(PNG + b"gone")
    (repo / GOLDENS / "library-400.png").unlink()
    p, outputs, summary = _run_step(tmp_path, repo, COMMIT)
    assert p.returncode == 0, p.stdout + p.stderr
    assert outputs == {"changed": "false"}
    assert "unchanged" in summary
    assert _git(repo, "log", "--oneline").count("\n") == 0


def _with_remote(tmp_path: Path) -> tuple[Path, Path, str]:
    """A repo with a bare origin at the recorded commit, plus the bot commit."""
    repo = _repo(tmp_path)
    recorded = _git(repo, "rev-parse", "HEAD")
    remote = tmp_path / "origin.git"
    subprocess.run([GIT, "init", "-q", "--bare", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/wp/x-goldens")
    _download(tmp_path, FULL_SET)
    assert _run_step(tmp_path, repo, VALIDATE)[0].returncode == 0
    assert _run_step(tmp_path, repo, COMMIT)[1] == {"changed": "true"}
    return repo, remote, recorded


def _move_remote(tmp_path: Path, remote: Path, recorded: str, files: dict[str, bytes]) -> str:
    """Advance origin's branch by one commit on top of `recorded`."""
    other = tmp_path / "other"
    subprocess.run([GIT, "clone", "-q", "-b", "wp/x-goldens", str(remote), str(other)], check=True)
    assert _git(other, "rev-parse", "HEAD") == recorded
    for name, data in files.items():
        (other / name).parent.mkdir(parents=True, exist_ok=True)
        (other / name).write_bytes(data)
    _git(other, "-c", "user.name=a", "-c", "user.email=a@a", "add", "-A")
    _git(other, "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-q", "-m", "moved")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/wp/x-goldens")
    return _git(other, "rev-parse", "HEAD")


@needs_tools
def test_push_step_pushes_fast_forward_when_the_branch_did_not_move(tmp_path: Path) -> None:
    repo, remote, recorded = _with_remote(tmp_path)
    bot = _git(repo, "rev-parse", "HEAD")
    p, _, _ = _run_step(tmp_path, repo, PUSH_STEP, {"GITHUB_SHA": recorded})
    assert p.returncode == 0, p.stdout + p.stderr
    assert _git(remote, "rev-parse", "refs/heads/wp/x-goldens") == bot


@needs_tools
def test_push_step_rebases_over_an_android_golden_commit(tmp_path: Path) -> None:
    repo, remote, recorded = _with_remote(tmp_path)
    android = _move_remote(tmp_path, remote, recorded, {f"{ANDROID_GOLDENS}/library_b.png": PNG + b"b",
                                                         f"{ANDROID_GOLDENS}/library_a.png": PNG + b"a2"})
    p, _, _ = _run_step(tmp_path, repo, PUSH_STEP, {"GITHUB_SHA": recorded})
    assert p.returncode == 0, p.stdout + p.stderr
    tip = _git(remote, "rev-parse", "refs/heads/wp/x-goldens")
    assert _git(remote, "rev-parse", f"{tip}^") == android, "web commit sits on top of the Android one"
    assert _git(remote, "log", "-1", "--format=%s", tip) == "test(web): refresh screenshot goldens from CI"
    tree = set(_git(remote, "ls-tree", "-r", "--name-only", tip).splitlines())
    assert {f"{ANDROID_GOLDENS}/library_b.png", f"{GOLDENS}/kit-400.png"} <= tree
    assert f"{GOLDENS}/gone-1400.png" not in tree


@needs_tools
@pytest.mark.parametrize(
    "files",
    [
        {"other.txt": b"someone pushed code\n"},
        {f"{ANDROID_GOLDENS}/library_b.png": PNG + b"b", "web/js/app.js": b"// new"},
        {f"{ANDROID_GOLDENS}/Evil.PNG": PNG + b"b"},
    ],
)
def test_push_step_gives_up_when_the_branch_moved_beyond_a_golden_refresh(tmp_path: Path, files: dict[str, bytes]) -> None:
    repo, remote, recorded = _with_remote(tmp_path)
    moved = _move_remote(tmp_path, remote, recorded, files)
    p, _, _ = _run_step(tmp_path, repo, PUSH_STEP, {"GITHUB_SHA": recorded})
    assert p.returncode != 0
    assert "::error::" in p.stdout and "newer run takes over" in p.stdout
    assert _git(remote, "rev-parse", "refs/heads/wp/x-goldens") == moved, "nothing was pushed"


@needs_tools
def test_push_step_gives_up_when_the_recorded_commit_is_gone_from_the_branch(tmp_path: Path) -> None:
    repo, remote, recorded = _with_remote(tmp_path)
    # Force the remote branch to an unrelated commit (a human force-pushed).
    other = tmp_path / "unrelated"
    other.mkdir()
    _git(other, "init", "-q", "-b", "wp/x-goldens")
    (other / "x.txt").write_text("x\n", encoding="utf-8")
    _git(other, "-c", "user.name=a", "-c", "user.email=a@a", "add", "-A")
    _git(other, "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-q", "-m", "unrelated")
    _git(other, "push", "-q", "--force", str(remote), "HEAD:refs/heads/wp/x-goldens")
    unrelated = _git(other, "rev-parse", "HEAD")
    p, _, _ = _run_step(tmp_path, repo, PUSH_STEP, {"GITHUB_SHA": recorded})
    assert p.returncode != 0
    assert "no longer contains" in p.stdout
    assert _git(remote, "rev-parse", "refs/heads/wp/x-goldens") == unrelated


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize(
    ("event", "ref", "ref_type", "ok"),
    [
        ("push", "refs/heads/wp/rc20-web-goldens-1-goldens", "branch", True),
        ("push", "refs/heads/main", "branch", False),
        ("push", "refs/heads/wp/a/b-goldens", "branch", False),
        ("pull_request", "refs/pull/7/merge", "", False),
    ],
)
def test_guard_step_executed(tmp_path: Path, event: str, ref: str, ref_type: str, ok: bool) -> None:
    env = _clean_env(tmp_path)
    env.update(GITHUB_EVENT_NAME=event, GITHUB_REF=ref, GITHUB_REF_TYPE=ref_type)
    p = subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", _step(PUSH, "goldens: guard branch")["run"]],
        capture_output=True, text=True, env=env, timeout=30, check=False,
    )
    assert (p.returncode == 0) is ok, p.stdout + p.stderr
