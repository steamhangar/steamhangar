"""WP CI-FIX-1: ci.yml builds every release image on every push and PR.

Until this pin existed, only `.github/workflows/publish.yml` (tag-gated) ever
ran `docker build` on the release Dockerfiles, so a `.dockerignore` that
excluded a file core/Dockerfile COPYs passed CI and would first have failed
at tag time (docs/LEARNINGS.md, WP RC7-INT).

ci.yml's `image-build` job carries its own copy of publish.yml's image
matrix (see the job's comment for why it is duplicated rather than shared).
This module is what makes that copy safe: it fails when

- ci.yml has no job that builds the images on push/pull_request,
- the job's matrix drifts from publish.yml's (an image added, dropped, or
  built with a different context, Dockerfile, or platform list),
- the build step's `with:` drifts from publish.yml's (anything beyond
  `push`/`tags`/`labels`: target, contexts, cache, secrets...), or pushes,
  or logs in, or is skipped or allowed to fail,
- the build-args KEYS drift from publish.yml's (WP VER-1: the values differ
  on purpose -- publish passes the release version, ci passes ci-<sha> --
  but a key added, dropped or renamed on one side fails here),
- the job's docker actions are pinned to different SHAs than publish.yml's,
- a matrix `context`/`dockerfile` path does not exist in the repo.

Pure file parsing (PyYAML, pinned in api/requirements.txt);
nothing here needs Docker.
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
PUBLISH_PATH = REPO_ROOT / ".github" / "workflows" / "publish.yml"

CI_JOB = "image-build"
PUBLISH_JOB = "publish"
MATRIX_KEYS = ("image", "context", "dockerfile", "platforms")
BUILD_ACTION = "docker/build-push-action"
SHARED_ACTIONS = (
    "actions/checkout",
    "docker/setup-qemu-action",
    "docker/setup-buildx-action",
    BUILD_ACTION,
)


#: `with:` keys only the publishing side may set: what to push and how to name it.
PUBLISH_ONLY_WITH_KEYS = frozenset({"push", "tags", "labels"})
#: WP VER-1: compared by KEY only (see test_image_build_build_args_keys_match_publish).
BUILD_ARGS_KEY = "build-args"

_EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def _norm(value: Any) -> Any:
    """Normalise whitespace inside every `${{ ... }}` so `${{matrix.x}}` and
    `${{ matrix.x }}` compare equal."""
    if isinstance(value, str):
        return _EXPR.sub(lambda m: "${{ " + " ".join(m.group(1).split()) + " }}", value)
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path}: not a YAML mapping"
    return _norm(data)


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # YAML 1.1 (PyYAML) reads the bare key `on` as boolean True.
    on = workflow.get("on", workflow.get(True))
    assert isinstance(on, dict), "workflow has no `on:` mapping"
    return on


def _job(workflow: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    jobs = workflow.get("jobs") or {}
    assert name in jobs, f"{path.name}: no `{name}` job (found {sorted(jobs)})"
    return jobs[name]


def _matrix(job: dict[str, Any]) -> list[dict[str, str]]:
    include = job["strategy"]["matrix"]["include"]
    rows = [{k: entry.get(k) for k in MATRIX_KEYS} for entry in include]
    return sorted(rows, key=lambda row: str(row["image"]))


def _uses(job: dict[str, Any]) -> dict[str, str]:
    """Action name -> pinned ref for every `uses:` step of a job."""
    pins: dict[str, str] = {}
    for step in job["steps"]:
        uses = step.get("uses")
        if uses:
            name, _, ref = uses.partition("@")
            pins[name] = ref
    return pins


def _build_step(job: dict[str, Any]) -> dict[str, Any]:
    steps = [s for s in job["steps"] if str(s.get("uses", "")).startswith(BUILD_ACTION + "@")]
    assert len(steps) == 1, f"expected exactly one {BUILD_ACTION} step, found {len(steps)}"
    return steps[0]


def test_ci_runs_on_push_to_main_and_wp_branches_and_on_pull_request() -> None:
    on = _triggers(_load(CI_PATH))
    assert "pull_request" in on
    branches = (on.get("push") or {}).get("branches") or []
    assert "main" in branches and "wp/**" in branches, branches


def test_image_build_job_runs_on_push_and_pull_request() -> None:
    job = _job(_load(CI_PATH), CI_JOB, CI_PATH)
    guard = job.get("if", "")
    for event in ("push", "pull_request"):
        assert f"github.event_name == '{event}'" in guard, (
            f"ci.yml `{CI_JOB}` must run on {event}; its if: is {guard!r}"
        )


def test_image_build_matrix_matches_publish_matrix() -> None:
    ci = _matrix(_job(_load(CI_PATH), CI_JOB, CI_PATH))
    publish = _matrix(_job(_load(PUBLISH_PATH), PUBLISH_JOB, PUBLISH_PATH))
    assert len(publish) >= 4, f"publish.yml matrix looks wrong: {publish}"
    assert ci == publish, (
        "ci.yml `image-build` must build exactly the images publish.yml ships, "
        f"with the same context/dockerfile/platforms.\n ci.yml:      {ci}\n "
        f"publish.yml: {publish}"
    )


def test_image_build_wires_the_matrix_into_build_push_action_without_pushing() -> None:
    job = _job(_load(CI_PATH), CI_JOB, CI_PATH)
    step = _build_step(job)
    with_ = step.get("with") or {}
    assert with_.get("context") == "${{ matrix.context }}"
    assert with_.get("file") == "${{ matrix.dockerfile }}"
    assert with_.get("platforms") == "${{ matrix.platforms }}"
    assert with_.get("push") is False, f"push must be literally false, got {with_.get('push')!r}"


def test_image_build_with_matches_publish_with_except_push_tags_labels() -> None:
    """Everything that shapes the build (target, build-args, build-contexts,
    provenance, cache, secrets, ...) must be identical to publish.yml's;
    only the push/naming keys may differ, and ci.yml must not set the
    naming keys at all."""
    ci_with = dict(_build_step(_job(_load(CI_PATH), CI_JOB, CI_PATH)).get("with") or {})
    publish_with = dict(
        _build_step(_job(_load(PUBLISH_PATH), PUBLISH_JOB, PUBLISH_PATH)).get("with") or {}
    )
    ci_with.pop("push", None)
    for key in PUBLISH_ONLY_WITH_KEYS:
        publish_with.pop(key, None)
    # WP VER-1: build-args values differ by design; their KEYS are pinned by
    # test_image_build_build_args_keys_match_publish below.
    ci_with.pop(BUILD_ARGS_KEY, None)
    publish_with.pop(BUILD_ARGS_KEY, None)
    assert ci_with == publish_with, (
        "ci.yml image-build `with:` (minus push and build-args) must equal "
        f"publish.yml's (minus {sorted(PUBLISH_ONLY_WITH_KEYS)} and build-args)."
        f"\n ci.yml:      {ci_with}\n publish.yml: {publish_with}"
    )


def _build_args(with_: dict[str, Any]) -> list[tuple[str, str]]:
    """build-push-action's newline-separated ``KEY=VALUE`` list, in order."""
    pairs = []
    for line in str(with_.get(BUILD_ARGS_KEY) or "").splitlines():
        line = line.strip()
        if line:
            key, sep, value = line.partition("=")
            assert sep, f"build-args line without '=': {line!r}"
            pairs.append((key, value))
    return pairs


def test_image_build_build_args_keys_match_publish() -> None:
    """WP VER-1: ci builds with the same build args publish does, so a key
    publish adds (and a Dockerfile starts depending on) is exercised on
    every push -- only the values may differ."""
    ci = _build_args(_build_step(_job(_load(CI_PATH), CI_JOB, CI_PATH)).get("with") or {})
    publish = _build_args(
        _build_step(_job(_load(PUBLISH_PATH), PUBLISH_JOB, PUBLISH_PATH)).get("with") or {}
    )
    ci_keys = [key for key, _ in ci]
    publish_keys = [key for key, _ in publish]
    assert publish_keys, "publish.yml passes no build-args at all (WP VER-1 expects two)"
    assert len(set(ci_keys)) == len(ci_keys), f"duplicate build-arg key in ci.yml: {ci_keys}"
    assert sorted(ci_keys) == sorted(publish_keys), (
        "ci.yml image-build must pass the same build-arg KEYS as publish.yml "
        f"(values may differ).\n ci.yml:      {ci_keys}\n publish.yml: {publish_keys}"
    )


def test_image_build_build_args_carry_the_ci_version_and_commit() -> None:
    job = _job(_load(CI_PATH), CI_JOB, CI_PATH)
    assert dict(_build_args(_build_step(job).get("with") or {})) == {
        "VAULT_VERSION": "${{ steps.version.outputs.version }}",
        "VAULT_COMMIT": "${{ steps.version.outputs.commit }}",
    }


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_image_build_version_step_says_ci_and_short_sha(tmp_path: Path) -> None:
    """Executed, not grepped: the value can never look like a release."""
    job = _job(_load(CI_PATH), CI_JOB, CI_PATH)
    steps = [s for s in job["steps"] if s.get("id") == "version"]
    assert len(steps) == 1
    assert steps[0].get("shell") == "bash"
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    sha = "0123456789abcdef0123456789abcdef01234567"
    subprocess.run(
        [shutil.which("bash"), "-c", steps[0]["run"]],
        env={"PATH": os.environ.get("PATH", ""), "GITHUB_SHA": sha, "GITHUB_OUTPUT": str(out)},
        check=True,
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines == [f"version=ci-{sha[:7]}", f"commit={sha}"]


def test_image_build_cannot_be_skipped_or_soft_failed() -> None:
    job = _job(_load(CI_PATH), CI_JOB, CI_PATH)
    assert "continue-on-error" not in job, "image-build must be able to fail the run"
    assert "schedule" not in job.get("if", ""), "image-build is a push/PR gate, not nightly"
    step = _build_step(job)
    assert "if" not in step, f"the build step must always run, found if: {step['if']!r}"
    assert "continue-on-error" not in step, "the build step must be able to fail the job"


def test_image_build_never_logs_in_or_asks_for_write_permissions() -> None:
    workflow = _load(CI_PATH)
    job = _job(workflow, CI_JOB, CI_PATH)
    assert "docker/login-action" not in _uses(job)
    assert "permissions" not in job, "image-build needs only the workflow's contents: read"
    assert workflow.get("permissions") == {"contents": "read"}, (
        f"ci.yml workflow permissions must be exactly contents: read, got {workflow.get('permissions')!r}"
    )
    checkout = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkout) == 1
    assert (checkout[0].get("with") or {}).get("persist-credentials") is False


def test_image_build_qemu_is_set_up_for_arm64_legs() -> None:
    job = _job(_load(CI_PATH), CI_JOB, CI_PATH)
    qemu = [s for s in job["steps"] if str(s.get("uses", "")).startswith("docker/setup-qemu-action@")]
    assert len(qemu) == 1, "image-build needs QEMU for the arm64 legs"
    assert (qemu[0].get("with") or {}).get("platforms") == "arm64"


def test_image_build_pins_the_same_action_shas_as_publish() -> None:
    ci = _uses(_job(_load(CI_PATH), CI_JOB, CI_PATH))
    publish = _uses(_job(_load(PUBLISH_PATH), PUBLISH_JOB, PUBLISH_PATH))
    for action in SHARED_ACTIONS:
        assert action in ci, f"image-build does not use {action}"
        assert ci[action] == publish[action], (
            f"{action}: ci.yml pins {ci[action]}, publish.yml pins {publish[action]}"
        )
        assert len(ci[action]) == 40, f"{action} must be pinned by full commit SHA"


def test_every_matrix_context_and_dockerfile_exists() -> None:
    for entry in _matrix(_job(_load(CI_PATH), CI_JOB, CI_PATH)):
        assert (REPO_ROOT / entry["context"]).is_dir(), f"missing context: {entry}"
        assert (REPO_ROOT / entry["dockerfile"]).is_file(), f"missing Dockerfile: {entry}"
