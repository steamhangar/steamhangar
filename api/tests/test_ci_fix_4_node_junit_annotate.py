"""WP CI-FIX-4: a failed web test job names the failing tests in annotations.

The token-less public API exposes a check run's annotations but not its log.
ci.yml's `web-tests` job therefore runs `node --test` with a second reporter
(`junit`, to a file) and, on failure, `.github/scripts/node-junit-annotate.py`
turns that file into `::error` workflow commands. This module pins:

- the parser against synthetic junit XML modeled on what Node's junit
  reporter actually writes (measured on Node v22.23.3: no file attribute,
  line breaks deleted from the `message` attribute, suites as nested
  `<testsuite>`, a whole-file failure as a test case named by its path),
  with the EXACT annotation lines expected,
- the escaping: test names and messages are data and can never start a
  workflow command of their own (checked with a model of the runner's line
  parser),
- the "nothing to show" cases (no failure, malformed, missing report) and
  the per-step cap,
- the workflow wiring: both reporters on the test step, its exit status
  untouched, the annotate step `if: failure()` reading the same file, no
  new action -- and, when a Node >= 22 is on PATH, the test step's real
  `run:` text executed against a failing sample.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = REPO_ROOT / ".github" / "scripts" / "node-junit-annotate.py"
JOB = "web-tests"
TEST_STEP = "node --test"
ANNOTATE_STEP = "annotate: failed web tests"
JUNIT_PATH = "${RUNNER_TEMP}/web-junit.xml"
PER_STEP_LIMIT = 10


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("node_junit_annotate", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["node_junit_annotate"] = mod
    spec.loader.exec_module(mod)
    return mod


nja = _load_module()


# ---------------------------------------------------------------------------
# A model of the runner's command parser (actions/runner ActionCommand).
# ---------------------------------------------------------------------------

_CMD = re.compile(r"^::([A-Za-z-]+)(?: ([^:]*))?::(.*)$")


def parse_commands(stdout: str) -> list[dict[str, Any]]:
    cmds = []
    for line in re.split(r"\r\n|\r|\n", stdout):
        m = _CMD.match(line.strip())
        if not m:
            continue
        props: dict[str, str] = {}
        if m.group(2):
            for pair in m.group(2).split(","):
                k, _, v = pair.partition("=")
                props[k] = (v.replace("%0D", "\r").replace("%0A", "\n")
                            .replace("%3A", ":").replace("%2C", ",").replace("%25", "%"))
        msg = m.group(3).replace("%0D", "\r").replace("%0A", "\n").replace("%25", "%")
        cmds.append({"cmd": m.group(1), "props": props, "message": msg})
    return cmds


def run_script(report: Path, workspace: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report), "--workspace", str(workspace)],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )


# ---------------------------------------------------------------------------
# Fixtures: junit XML in the shape Node's reporter writes
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "work" / "steamhangar"
    (w / "web" / "tests").mkdir(parents=True)
    return w


def _report(ws: Path) -> str:
    t = f"file://{ws}/web/tests/store.test.js"
    lib = f"file://{ws}/web/js/store.js"
    crash = f"{ws}/web/tests/broken.test.js"
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites>
        \t<testcase name="passes" time="0.0007" classname="test"/>
        \t<testcase name="sizes 100% &amp;quot;a,b&amp;quot; &lt;x> ::y" time="0.0009" classname="test" failure="line onefoo, bar">
        \t\t<failure type="testCodeFailure" message="line onefoo, bar">
        [Error [ERR_TEST_FAILURE]: line one
        ::error file=evil.js::pwned 100%

        1 !== 2
        ] {{
          code: 'ERR_TEST_FAILURE',
          failureType: 'testCodeFailure',
          cause: AssertionError [ERR_ASSERTION]: line one
              at load ({lib}:40:3)
              at TestContext.&lt;anonymous> ({t}:12:5)
              at Test.runInAsyncScope (node:async_hooks:214:14)
        }}
        \t\t</failure>
        \t</testcase>
        \t<testsuite name="poll" time="0.0005" disabled="0" errors="0" tests="2" failures="1" skipped="0" hostname="x">
        \t\t<testcase name="ok" time="0.0002" classname="test"/>
        \t\t<testcase name="times out" time="0.5" classname="test" failure="test timed out after 500ms">
        \t\t\t<failure type="testTimeoutFailure" message="test timed out after 500ms">
        [Error [ERR_TEST_FAILURE]: test timed out after 500ms] {{ code: 'ERR_TEST_FAILURE', failureType: 'testTimeoutFailure', cause: 'test timed out after 500ms' }}
        \t\t\t</failure>
        \t\t</testcase>
        \t</testsuite>
        \t<testcase name="{crash}" time="0.03" classname="test" failure="test failed">
        \t\t<failure type="testCodeFailure" message="test failed">
        [Error: test failed] {{ code: 'ERR_TEST_FAILURE', failureType: 'testCodeFailure', cause: 'test failed', exitCode: 1, signal: null }}
        \t\t</failure>
        \t</testcase>
        \t<!-- tests 5 -->
        </testsuites>
        """)


EXPECTED = [
    "::error file=web/tests/store.test.js,line=12,"
    "title=Web test failed%3A sizes 100%25 \"a%2Cb\" <x> %3A%3Ay"
    "::line one%0A::error file=evil.js::pwned 100%25%0A%0A1 !== 2",
    "::error title=Web test failed%3A poll > times out"
    "::test timed out after 500ms%0A[testTimeoutFailure]",
    "::error file=web/tests/broken.test.js,title=Web test file failed%3A web/tests/broken.test.js"
    "::test failed%0A(the whole file failed: crash, load error or non-zero exit outside a "
    "test -- the details are only in the spec output of the job log)",
]


def test_failures_become_exact_annotation_lines(ws: Path, tmp_path: Path) -> None:
    report = tmp_path / "web-junit.xml"
    report.write_text(_report(ws), encoding="utf-8")
    p = run_script(report, ws)
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == EXPECTED


def test_test_data_never_becomes_a_command(ws: Path, tmp_path: Path) -> None:
    report = tmp_path / "web-junit.xml"
    report.write_text(_report(ws), encoding="utf-8")
    cmds = parse_commands(run_script(report, ws).stdout)
    # Exactly the three error commands; the `::error file=evil.js` text
    # inside a message stays message data.
    assert [c["cmd"] for c in cmds] == ["error"] * 3
    assert {c["props"].get("file") for c in cmds} == {
        "web/tests/store.test.js", None, "web/tests/broken.test.js"}
    first = cmds[0]
    assert first["props"]["title"] == 'Web test failed: sizes 100% "a,b" <x> ::y'
    assert first["message"] == "line one\n::error file=evil.js::pwned 100%\n\n1 !== 2"


def test_no_failure_still_says_so(ws: Path, tmp_path: Path) -> None:
    report = tmp_path / "web-junit.xml"
    report.write_text('<?xml version="1.0"?>\n<testsuites>\n'
                      '<testcase name="a" classname="test"/>\n</testsuites>\n', encoding="utf-8")
    p = run_script(report, ws)
    assert p.returncode == 0
    assert p.stdout.splitlines() == [
        "::error title=web tests::node --test failed, but its junit report lists "
        "no failing test case; see the job log."]


def test_malformed_report(ws: Path, tmp_path: Path) -> None:
    report = tmp_path / "web-junit.xml"
    report.write_text("<testsuites><testcase name='x'>", encoding="utf-8")
    p = run_script(report, ws)
    assert p.returncode == 0
    assert p.stdout.splitlines() == [
        "::error title=web tests::node --test failed and its junit report "
        "(web-junit.xml) is unreadable: ParseError."]


def test_missing_report(ws: Path, tmp_path: Path) -> None:
    p = run_script(tmp_path / "web-junit.xml", ws)
    assert p.returncode == 0
    assert p.stdout.splitlines() == [
        "::error title=web tests::node --test failed and wrote no junit report "
        "(web-junit.xml); see the job log."]


def test_subtests_failed_parent_is_dropped_when_the_child_reports(ws: Path) -> None:
    import xml.etree.ElementTree as ET
    t = f"{ws}/web/tests/a.test.js"
    root = ET.fromstring(f"""<testsuites>
      <testcase name="parent" classname="test"><failure type="testCodeFailure">
      [Error [ERR_TEST_FAILURE]: 1 subtest failed] {{ failureType: 'subtestsFailed' }}
      </failure></testcase>
      <testsuite name="parent"><testcase name="child" classname="test">
      <failure type="testCodeFailure">Error [ERR_TEST_FAILURE]: nope
          at TestContext.&lt;anonymous&gt; ({t}:3:9) {{
        failureType: 'testCodeFailure' }}</failure></testcase></testsuite>
    </testsuites>""")
    found = nja.parse(root, ws)
    assert [(f.title, f.message, f.file, f.line) for f in found] == [
        ("Web test failed: parent > child", "nope", "web/tests/a.test.js", 3)]


def test_long_message_and_frame_preference(ws: Path) -> None:
    body = "Error [ERR_TEST_FAILURE]: " + "\n".join(f"l{i}" for i in range(9))
    assert nja.message_lines(body, "x") == ["l0", "l1", "l2", "l3", "l4", "l5", "…"]
    frames = (f"  at f (/elsewhere/x.js:1:1)\n  at g ({ws}/web/js/a.js:5:1)\n"
              f"  at h (file://{ws}/web/tests/a.test.js:9:2)\n")
    assert nja.location(frames, ws) == ("web/tests/a.test.js", 9)
    assert nja.location(f"  at g ({ws}/web/js/a.js:5:1)", ws) == ("web/js/a.js", 5)
    assert nja.location("  at f (/elsewhere/x.js:1:1)", ws) == (None, None)


def test_cap_at_the_per_step_limit(ws: Path, tmp_path: Path) -> None:
    cases = "".join(
        f'<testcase name="t{i}" classname="test"><failure>Error [ERR_TEST_FAILURE]: '
        f"bad {i}</failure></testcase>" for i in range(15))
    report = tmp_path / "web-junit.xml"
    report.write_text(f"<testsuites>{cases}</testsuites>", encoding="utf-8")
    cmds = parse_commands(run_script(report, ws).stdout)
    assert len(cmds) == PER_STEP_LIMIT
    assert cmds[0]["message"] == "bad 0"
    assert cmds[-1]["props"]["title"] == "web tests: 6 more"


# ---------------------------------------------------------------------------
# Workflow wiring
# ---------------------------------------------------------------------------


def _job() -> dict[str, Any]:
    wf = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    return wf["jobs"][JOB]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    steps = [s for s in job["steps"] if s.get("name") == name]
    assert len(steps) == 1, name
    return steps[0]


def test_test_step_wires_both_reporters_and_keeps_its_exit_status() -> None:
    step = _step(_job(), TEST_STEP)
    run = " ".join(step["run"].split())
    assert run == (
        "node --test --test-reporter=spec --test-reporter-destination=stdout "
        f'--test-reporter=junit --test-reporter-destination="{JUNIT_PATH}" '
        '"web/tests/*.test.js"')
    assert step.get("shell") == "bash"
    assert "if" not in step and "continue-on-error" not in step


def test_annotate_step_is_failure_only_and_reads_the_same_file() -> None:
    job = _job()
    steps = job["steps"]
    test_step, annotate = _step(job, TEST_STEP), _step(job, ANNOTATE_STEP)
    assert steps.index(annotate) == steps.index(test_step) + 1
    assert annotate.get("if") == "failure()"
    assert "uses" not in annotate and "continue-on-error" not in annotate
    assert annotate["run"] == f'python3 .github/scripts/node-junit-annotate.py "{JUNIT_PATH}"'


def test_job_gained_no_new_action() -> None:
    uses = [s["uses"].split("@")[0] for s in _job()["steps"] if "uses" in s]
    assert uses == ["actions/checkout", "actions/setup-node"]


def _node_major() -> int | None:
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    m = re.match(r"v(\d+)\.", out.stdout.strip())
    return int(m.group(1)) if m else None


@pytest.mark.skipif(shutil.which("bash") is None or (_node_major() or 0) < 22,
                    reason="needs bash and Node >= 22 on PATH")
def test_real_step_fails_and_its_report_is_annotated(ws: Path, tmp_path: Path) -> None:
    (ws / "web" / "tests" / "ok.test.js").write_text(
        "import test from 'node:test';\ntest('fine', () => {});\n", encoding="utf-8")
    (ws / "web" / "tests" / "bad.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "test('adds', () => {\n  assert.equal(1 + 1, 3);\n});\n", encoding="utf-8")
    (ws / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")},
           "RUNNER_TEMP": str(runner_temp)}
    step = _step(_job(), TEST_STEP)
    p = subprocess.run(["bash", "-eo", "pipefail", "-c", step["run"]], cwd=ws, env=env,
                       capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode != 0
    assert "✖ adds" in p.stdout  # the spec reporter still writes the log
    report = runner_temp / "web-junit.xml"
    assert report.is_file()
    cmds = parse_commands(run_script(report, ws).stdout)
    assert [(c["props"].get("file"), c["props"].get("line"), c["props"]["title"])
            for c in cmds] == [("web/tests/bad.test.js", "4", "Web test failed: adds")]
    assert "3" in cmds[0]["message"] and "2" in cmds[0]["message"]
