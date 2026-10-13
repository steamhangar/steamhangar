"""WP APP-TEST-1: the screenshot tests' CI flow in ci.yml's `android-tests` job.

The Roborazzi/Robolectric tests themselves only run in GitHub CI (Gradle
plus an Android SDK; nothing of that exists in the devbox). What can be
pinned here, without Gradle, is the contract between the three files that
make the flow work:

- the Kotlin test (`LibraryScreenshotTest.kt`) writes missing goldens to
  `build/outputs/roborazzi/new-goldens/` and diff images to
  `build/outputs/roborazzi/diffs/`, and reads goldens from
  `src/testDebug/screenshots/`,
- `app/app/build.gradle.kts` forwards the record switch under the same
  property name and declares the same golden directory as a task input,
- ci.yml uploads exactly that output directory (pinned action, never on
  cancel, nothing-found is not an error) and its summary step reports the
  two counts without ever failing the job itself,
- every `shoot("<name>")` in the Kotlin test has a checked-in golden
  `<name>.png` and no golden is left without a shot (WP APP-TEST-1b): a
  missing golden only records and passes in Gradle, so without this check a
  lost PNG would silently turn a verify into a record.

The summary step's real `run:` text is executed with fake output trees.
Pure file parsing plus local bash subprocesses; no Gradle, Docker or network.
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
GRADLE_FILE = REPO_ROOT / "app" / "app" / "build.gradle.kts"
TEST_FILE = (REPO_ROOT / "app" / "app" / "src" / "testDebug" / "java" / "dev" / "steamvault"
             / "app" / "ui" / "library" / "LibraryScreenshotTest.kt")
GOLDEN_DIR = REPO_ROOT / "app" / "app" / "src" / "testDebug" / "screenshots"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JOB = "android-tests"
UPLOAD_ACTION = "actions/upload-artifact"
OUTPUT_DIR = "app/app/build/outputs/roborazzi"
BASH = shutil.which("bash")


def _job() -> dict[str, Any]:
    wf = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    return wf["jobs"][JOB]


def _step(name: str) -> dict[str, Any]:
    steps = [s for s in _job()["steps"] if s.get("name") == name]
    assert len(steps) == 1, name
    return steps[0]


def _index(step: dict[str, Any]) -> int:
    return _job()["steps"].index(step)


def _gradle_step() -> dict[str, Any]:
    steps = [s for s in _job()["steps"] if str(s.get("name", "")).startswith("gradlew ")]
    assert len(steps) == 1
    return steps[0]


# ---------------------------------------------------------------------------
# Static wiring
# ---------------------------------------------------------------------------


def test_screenshot_tests_run_inside_the_existing_gradle_step() -> None:
    # testDebug sources run under testDebugUnitTest, which the gradle step
    # already calls -- no second Gradle invocation was added.
    assert "testDebugUnitTest" in _gradle_step()["run"]
    assert TEST_FILE.is_file()


def test_upload_step_is_pinned_and_publishes_the_output_dir() -> None:
    step = _step("screenshots: upload artifact")
    name, _, ref = step["uses"].partition("@")
    assert name == UPLOAD_ACTION
    assert re.fullmatch(r"[0-9a-f]{40}", ref), "actions must be pinned by full commit SHA"
    assert step["if"] == "${{ !cancelled() }}"
    w = step["with"]
    assert w["name"] == "app-screenshots"
    assert w["path"].rstrip("/") == OUTPUT_DIR
    # Missing images are the normal green case, never a warning or failure.
    assert w["if-no-files-found"] == "ignore"
    assert "continue-on-error" not in step
    assert _index(step) > _index(_gradle_step())


def test_summary_step_runs_after_gradle_and_before_upload() -> None:
    summary = _step("screenshots: summary")
    assert summary["if"] == "${{ !cancelled() }}"
    assert summary.get("shell") == "bash"
    assert "uses" not in summary
    assert _index(_gradle_step()) < _index(summary) < _index(_step("screenshots: upload artifact"))
    assert f"out={OUTPUT_DIR}" in summary["run"]


def test_junit_annotations_know_the_testdebug_source_root() -> None:
    step = _step("annotate: failed unit tests")
    assert "--test-root app/app/src/testDebug/java" in step["run"]


def test_kotlin_test_writes_where_ci_uploads_from() -> None:
    src = TEST_FILE.read_text(encoding="utf-8")
    # Module-relative paths in the test == repo paths minus "app/app/".
    assert 'File("build/outputs/roborazzi")' in src
    assert 'File(outputDir, "new-goldens")' in src
    assert 'File(outputDir, "diffs")' in src
    assert 'File("src/testDebug/screenshots")' in src
    summary = _step("screenshots: summary")["run"]
    assert '"$out/new-goldens"' in summary
    assert '"$out/diffs"' in summary


def test_gradle_forwards_the_record_switch_and_declares_goldens_as_input() -> None:
    gradle = GRADLE_FILE.read_text(encoding="utf-8")
    src = TEST_FILE.read_text(encoding="utf-8")
    prop = "steamhangar.screenshots.record"
    assert f'RECORD_PROPERTY = "{prop}"' in src
    assert f'val screenshotRecordProperty = "{prop}"' in gradle
    assert 'providers.gradleProperty(screenshotRecordProperty).getOrElse("false")' in gradle
    assert 'inputs.files(fileTree("src/testDebug/screenshots")' in gradle
    # inputs.dir on a not-yet-existing golden directory fails task
    # validation even with .optional() (first CI run, 8d12ee4).
    code = [ln for ln in gradle.splitlines() if not ln.lstrip().startswith("//")]
    assert not [ln for ln in code if "inputs.dir(" in ln]
    # Robolectric needs the merged resources to render real Composables.
    assert "isIncludeAndroidResources = true" in gradle


# ---------------------------------------------------------------------------
# The summary step's real run: text, executed
# ---------------------------------------------------------------------------


def _run_summary(tmp_path: Path, new: int, changed: int) -> tuple[subprocess.CompletedProcess[str], str]:
    ws = tmp_path / "ws"
    out = ws / OUTPUT_DIR
    if new:
        (out / "new-goldens").mkdir(parents=True)
        for i in range(new):
            (out / "new-goldens" / f"shot_{i}.png").write_bytes(b"png")
    if changed:
        (out / "diffs").mkdir(parents=True)
        for i in range(changed):
            (out / "diffs" / f"shot_{i}_compare.png").write_bytes(b"png")
            (out / "diffs" / f"shot_{i}_actual.png").write_bytes(b"png")
    ws.mkdir(parents=True, exist_ok=True)
    summary_file = tmp_path / "summary.md"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env["GITHUB_STEP_SUMMARY"] = str(summary_file)
    p = subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", _step("screenshots: summary")["run"]],
        cwd=ws, capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    return p, summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_summary_with_no_output_dir_is_quiet_and_green(tmp_path: Path) -> None:
    p, summary = _run_summary(tmp_path, new=0, changed=0)
    assert p.returncode == 0, p.stderr
    assert "::notice" not in p.stdout and "::error" not in p.stdout
    assert "(new): 0" in summary and "(failed): 0" in summary


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_summary_reports_missing_goldens_as_notice_without_failing(tmp_path: Path) -> None:
    p, summary = _run_summary(tmp_path, new=6, changed=0)
    assert p.returncode == 0, p.stderr
    notices = [ln for ln in p.stdout.splitlines() if ln.startswith("::notice ")]
    assert len(notices) == 1 and "::6 screenshot(s) have no golden yet" in notices[0]
    assert "::error" not in p.stdout
    assert "(new): 6" in summary


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_summary_counts_compare_images_only_and_stays_exit_zero(tmp_path: Path) -> None:
    # The gradle step already failed the job for a diff; this step only
    # explains it. _actual.png files must not double the count.
    p, summary = _run_summary(tmp_path, new=1, changed=2)
    assert p.returncode == 0, p.stderr
    errors = [ln for ln in p.stdout.splitlines() if ln.startswith("::error ")]
    assert len(errors) == 1 and "::2 screenshot(s) differ" in errors[0]
    assert "(failed): 2" in summary and "(new): 1" in summary


# ---------------------------------------------------------------------------
# Goldens checked in (WP APP-TEST-1b)
# ---------------------------------------------------------------------------


def _shot_names() -> list[str]:
    src = TEST_FILE.read_text(encoding="utf-8")
    # Calls only: the definition is `shoot(\n    name: String, ...`.
    return re.findall(r'\bshoot\(\s*"([^"]+)"', src)


def test_every_shot_has_a_checked_in_golden() -> None:
    names = _shot_names()
    assert len(names) >= 6, names
    assert len(names) == len(set(names)), "duplicate shot names overwrite each other's golden"
    missing = [n for n in names if not (GOLDEN_DIR / f"{n}.png").is_file()]
    assert not missing, f"no golden in {GOLDEN_DIR.relative_to(REPO_ROOT)} for: {missing}"
    for n in names:
        with (GOLDEN_DIR / f"{n}.png").open("rb") as fh:
            assert fh.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE, f"{n}.png is not a PNG"


def test_no_golden_without_a_shot() -> None:
    names = set(_shot_names())
    orphans = sorted(p.name for p in GOLDEN_DIR.iterdir() if p.stem not in names or p.suffix != ".png")
    assert not orphans, f"files in the golden directory no test shoots: {orphans}"
