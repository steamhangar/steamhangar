"""WP CI-FIX-2: a failed Android CI job explains itself through annotations.

The token-less public API exposes a check run's annotations but not its
log. ci.yml's `android-tests` job therefore tees the Gradle output to a file
and runs `.github/scripts/android-annotate.py` from one `if: failure()` step
per category (Kotlin compiler errors, JUnit failures, lint issues, Gradle's
"What went wrong" block). This module pins:

- the escaping: log text is data; `%`/CR/LF are escaped in messages and
  `:`/`,` additionally in properties, so no message can end its line and
  start a new `::` workflow command (checked against a re-implementation of
  the runner's own line parser, not just by substring),
- the parsers against synthetic fixtures modeled on the real formats
  (Kotlin 2.x `e: file:///...:L:C msg`, Surefire-style JUnit XML, AGP lint
  XML/TXT),
- the per-step cap (GitHub keeps 10 error annotations per step, 50 per job),
- fail-closed: the Gradle step's real `run:` text, executed with a fake
  gradlew, still exits with gradlew's status even though its output is
  piped through `tee`, and the step suspends workflow-command parsing while
  that output is printed,
- the workflow wiring: every annotate step is `if: failure()`, uses only
  python3 + this script, and the job gained no new action.

Pure file parsing plus local bash/python3 subprocesses; nothing needs
Gradle, Docker or network.
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
SCRIPT = REPO_ROOT / ".github" / "scripts" / "android-annotate.py"
JOB = "android-tests"
GRADLE_STEP_PREFIX = "gradlew "
ANNOTATE_PREFIX = "annotate: "
#: GitHub's documented limits.
PER_STEP_LIMIT = 10
PER_JOB_LIMIT = 50

BASH = shutil.which("bash")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("android_annotate", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["android_annotate"] = mod
    spec.loader.exec_module(mod)
    return mod


aa = _load_module()


# ---------------------------------------------------------------------------
# A model of the runner's command parser (actions/runner ActionCommand),
# used to prove what the runner would actually see.
# ---------------------------------------------------------------------------

_CMD = re.compile(r"^::([A-Za-z-]+)(?: ([^:]*))?::(.*)$")


def _unescape_data(s: str) -> str:
    return s.replace("%0D", "\r").replace("%0A", "\n").replace("%25", "%")


def _unescape_prop(s: str) -> str:
    return (s.replace("%0D", "\r").replace("%0A", "\n")
             .replace("%3A", ":").replace("%2C", ",").replace("%25", "%"))


def parse_commands(stdout: str) -> list[dict[str, Any]]:
    """Split stdout into lines exactly like the runner (CR, LF, CRLF) and
    return every line that the runner would treat as a workflow command."""
    cmds = []
    for line in re.split(r"\r\n|\r|\n", stdout):
        m = _CMD.match(line.strip())
        if not m:
            continue
        props: dict[str, str] = {}
        if m.group(2):
            for pair in m.group(2).split(","):
                k, _, v = pair.partition("=")
                props[k] = _unescape_prop(v)
        cmds.append({"cmd": m.group(1), "props": props,
                     "message": _unescape_data(m.group(3)), "raw": line})
    return cmds


def run_script(*args: str, workspace: Path, summary: Path | None = None
               ) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    if summary is not None:
        env["GITHUB_STEP_SUMMARY"] = str(summary)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--workspace", str(workspace)],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )


# ---------------------------------------------------------------------------
# Fixtures (synthetic, modeled on Kotlin 2.0 / Gradle 8 / AGP 8.7 output)
# ---------------------------------------------------------------------------

INJECTION = "boom\n::error file=evil.kt,line=1::pwned\r::set-output name=x::y %0A ,: done"


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "work" / "steamhangar"
    src = w / "app/app/src/test/java/com/steamhangar/app"
    src.mkdir(parents=True)
    (src / "ParserTest.kt").write_text("// fixture\n", encoding="utf-8")
    (w / "app/app/src/main/res/values").mkdir(parents=True)
    return w


def _gradle_log(ws: Path) -> str:
    main = ws / "app/app/src/main/java/com/steamhangar/app/Main.kt"
    return textwrap.dedent(f"""\
        > Task :app:compileDebugKotlin FAILED
        e: file://{main}:12:5 Unresolved reference 'foo'.
        e: file:///opt/elsewhere/Other.kt:3:1 Outside the workspace, a: b, c
        e: file://{main}:40:9 {INJECTION.splitlines()[0]}::error::still data, 100%
        e: {main}: (7, 2): Old-style location
        w: file://{main}:1:1 a warning, not annotated

        FAILURE: Build failed with an exception.

        * What went wrong:
        Execution failed for task ':app:compileDebugKotlin'.
        > A failure occurred while executing org.jetbrains.kotlin.compilerRunner.GradleCompilerRunnerWithWorkers$GradleKotlinCompilerWorkAction
           > Compilation error. See log for more details

        * Try:
        > Run with --stacktrace option to get the stack trace.

        BUILD FAILED in 41s
        """)


def _junit_xml(message: str, *, line: int = 27) -> str:
    def attr(s: str) -> str:
        return (s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
                 .replace("\n", "&#10;").replace("\r", "&#13;"))
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <testsuite name="com.steamhangar.app.ParserTest" tests="3" failures="2" errors="0">
          <testcase name="parsesEmpty" classname="com.steamhangar.app.ParserTest" time="0.01"/>
          <testcase name="rejectsGarbage" classname="com.steamhangar.app.ParserTest" time="0.02">
            <failure message="{attr(message)}" type="java.lang.AssertionError">java.lang.AssertionError: x
        \tat org.junit.Assert.fail(Assert.java:89)
        \tat com.steamhangar.app.ParserTest.rejectsGarbage(ParserTest.kt:{line})
        </failure>
          </testcase>
          <testcase name="noMessage" classname="com.steamhangar.app.ParserTest" time="0.02">
            <failure type="java.lang.IllegalStateException">java.lang.IllegalStateException: first line
        \tat com.steamhangar.app.ParserTest.noMessage(ParserTest.kt:50)
        </failure>
          </testcase>
        </testsuite>
        """)


def _write_junit(ws: Path, message: str) -> Path:
    results = ws / "app/app/build/test-results"
    for variant in ("testDebugUnitTest", "testReleaseUnitTest"):
        d = results / variant
        d.mkdir(parents=True)
        (d / "TEST-com.steamhangar.app.ParserTest.xml").write_text(
            _junit_xml(message), encoding="utf-8")
    return results


def _lint_xml(ws: Path, message: str) -> str:
    res = ws / "app/app/src/main/res/values/strings.xml"
    msg = (message.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
           .replace("\n", "&#10;").replace("\r", "&#13;"))
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <issues format="6" by="lint 8.7.3">
            <issue id="UnusedResources" severity="Warning" message="{msg}"
                category="Performance" priority="3" summary="Unused resources">
                <location file="{res}" line="5" column="13"/>
            </issue>
            <issue id="MissingTranslation" severity="Fatal" message="Not translated"
                category="Correctness:Messages" priority="8" summary="x">
                <location file="{res}" line="9" column="5"/>
            </issue>
            <issue id="ObsoleteSdkInt" severity="Information" message="info only"
                category="Performance" priority="6" summary="x">
                <location file="{res}" line="1"/>
            </issue>
        </issues>
        """)


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_escape_data_follows_github_rules() -> None:
    assert aa.escape_data("a%b\r\nc") == "a%25b%0D%0Ac"
    # `%` first: a literal "%0A" in the log must stay literal text.
    assert aa.escape_data("%0A") == "%250A"
    # `:` and `,` are fine in a message (only properties need them escaped).
    assert aa.escape_data("a: b, c") == "a: b, c"


def test_escape_property_also_escapes_colon_and_comma() -> None:
    assert aa.escape_property("a:b,c%\n") == "a%3Ab%2Cc%25%0A"


def test_other_line_breaks_and_controls_are_neutralized() -> None:
    out = aa.escape_data("a\x1b[31mb c\x85d\x00e\tf")
    assert "\x1b" not in out and " " not in out and "\x85" not in out
    assert "\x00" not in out and "\t" in out


def test_injected_message_stays_one_command() -> None:
    line = aa._command(aa.Finding(INJECTION, INJECTION, "app/x,y:z.kt", 3, 4))
    cmds = parse_commands(line + "\n")
    assert len(cmds) == 1, cmds
    c = cmds[0]
    assert c["cmd"] == "error"
    assert c["props"] == {"file": "app/x,y:z.kt", "line": "3", "col": "4",
                          "title": INJECTION.strip()}
    assert c["message"] == INJECTION.strip()
    assert "\n" not in line and "\r" not in line


# ---------------------------------------------------------------------------
# Parsers, end to end through the CLI
# ---------------------------------------------------------------------------


def test_kotlin_errors_become_file_line_annotations(ws: Path, tmp_path: Path) -> None:
    log = tmp_path / "gradle.log"
    log.write_text(_gradle_log(ws), encoding="utf-8")
    summary = tmp_path / "summary.md"
    p = run_script("kotlin", "--log", str(log), workspace=ws, summary=summary)
    assert p.returncode == 0, p.stderr
    cmds = parse_commands(p.stdout)
    assert len(cmds) == 4, p.stdout
    assert all(c["cmd"] == "error" for c in cmds)
    rel = "app/app/src/main/java/com/steamhangar/app/Main.kt"
    first, outside, injected, old = cmds
    assert first["props"]["file"] == rel
    assert first["props"]["line"] == "12" and first["props"]["col"] == "5"
    assert first["message"] == "Unresolved reference 'foo'."
    # Outside the workspace: no file=, the path is kept in the message.
    assert "file" not in outside["props"]
    assert outside["message"].startswith("/opt/elsewhere/Other.kt:3:1 ")
    assert injected["props"]["line"] == "40"
    assert injected["message"] == "boom::error::still data, 100%"
    assert old["props"]["file"] == rel and old["props"]["line"] == "7"
    # Every stdout line is ours: nothing but `::error` lines.
    assert all(ln.startswith("::error ") for ln in p.stdout.splitlines())
    text = summary.read_text(encoding="utf-8")
    assert "Kotlin compile errors (4)" in text
    assert "Unresolved reference" in text


def test_gradle_what_went_wrong_block(ws: Path, tmp_path: Path) -> None:
    log = tmp_path / "gradle.log"
    log.write_text(_gradle_log(ws), encoding="utf-8")
    p = run_script("gradle", "--log", str(log), workspace=ws)
    cmds = parse_commands(p.stdout)
    assert len(cmds) == 1
    assert cmds[0]["message"].startswith("Execution failed for task ':app:compileDebugKotlin'.\n")
    assert "Compilation error" in cmds[0]["message"]
    assert "--stacktrace" not in cmds[0]["message"]


def test_junit_failures_annotated_once_with_source_location(ws: Path, tmp_path: Path) -> None:
    results = _write_junit(ws, INJECTION)
    summary = tmp_path / "summary.md"
    p = run_script("junit", "--results-dir", str(results),
                   "--test-root", str(ws / "app/app/src/test/java"),
                   workspace=ws, summary=summary)
    assert p.returncode == 0, p.stderr
    cmds = parse_commands(p.stdout)
    # 2 failures x 2 variants, merged per failure.
    assert len(cmds) == 2, p.stdout
    assert len(p.stdout.splitlines()) == 2
    by_title = {c["props"]["title"]: c for c in cmds}
    inj = by_title["Unit test failed: ParserTest > rejectsGarbage"]
    assert inj["props"]["file"] == "app/app/src/test/java/com/steamhangar/app/ParserTest.kt"
    assert inj["props"]["line"] == "27"
    assert inj["message"] == (f"ParserTest > rejectsGarbage: {INJECTION} "
                              "[testDebugUnitTest, testReleaseUnitTest]")
    nomsg = by_title["Unit test failed: ParserTest > noMessage"]
    assert "java.lang.IllegalStateException: first line" in nomsg["message"]
    assert nomsg["props"]["line"] == "50"
    # The summary is markdown-inert and one line per item.
    text = summary.read_text(encoding="utf-8")
    items = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(items) == 2
    assert not any(ln.startswith("::") for ln in text.splitlines())


def test_lint_errors_and_warnings_annotated(ws: Path, tmp_path: Path) -> None:
    xml = tmp_path / "lint-results-debug.xml"
    xml.write_text(_lint_xml(ws, INJECTION), encoding="utf-8")
    p = run_script("lint", "--lint-xml", str(xml), workspace=ws)
    cmds = parse_commands(p.stdout)
    assert len(cmds) == 2, p.stdout  # Information is skipped
    warn, fatal = cmds
    assert warn["props"]["file"] == "app/app/src/main/res/values/strings.xml"
    assert warn["props"]["line"] == "5" and warn["props"]["col"] == "13"
    assert warn["props"]["title"] == "Lint Warning: UnusedResources"
    assert warn["message"] == f"[UnusedResources] {INJECTION}"
    assert fatal["props"]["title"] == "Lint Fatal: MissingTranslation"


def test_lint_text_report_fallback(ws: Path, tmp_path: Path) -> None:
    txt = tmp_path / "lint-results-debug.txt"
    txt.write_text(
        "src/main/res/values/strings.xml:5: Warning: The resource R.string.x "
        "appears to be unused [UnusedResources]\n"
        "    <string name=\"x\">x</string>\n"
        "0 errors, 1 warnings\n", encoding="utf-8")
    p = run_script("lint", "--lint-xml", str(tmp_path / "missing.xml"),
                   "--lint-txt", str(txt), "--project-dir", str(ws / "app/app"),
                   workspace=ws)
    cmds = parse_commands(p.stdout)
    assert len(cmds) == 1
    assert cmds[0]["props"]["file"] == "app/app/src/main/res/values/strings.xml"
    assert cmds[0]["props"]["line"] == "5"


def test_missing_inputs_emit_nothing_and_exit_zero(ws: Path, tmp_path: Path) -> None:
    for args in (("kotlin", "--log", str(tmp_path / "nope.log")),
                 ("gradle", "--log", str(tmp_path / "nope.log")),
                 ("junit", "--results-dir", str(tmp_path / "nope")),
                 ("lint", "--lint-xml", str(tmp_path / "nope.xml"))):
        p = run_script(*args, workspace=ws)
        assert p.returncode == 0, (args, p.stderr)
        assert p.stdout == ""


def test_broken_xml_is_skipped_not_fatal(ws: Path, tmp_path: Path) -> None:
    xml = tmp_path / "lint.xml"
    xml.write_text("<issues><issue", encoding="utf-8")
    p = run_script("lint", "--lint-xml", str(xml), workspace=ws)
    assert p.returncode == 0 and p.stdout == ""


def test_annotations_capped_per_step(ws: Path, tmp_path: Path) -> None:
    main = ws / "app/app/Main.kt"
    log = tmp_path / "gradle.log"
    log.write_text("".join(f"e: file://{main}:{i}:1 error {i}\n" for i in range(1, 26)),
                   encoding="utf-8")
    summary = tmp_path / "summary.md"
    p = run_script("kotlin", "--log", str(log), workspace=ws, summary=summary)
    cmds = parse_commands(p.stdout)
    assert len(cmds) == PER_STEP_LIMIT
    assert cmds[-1]["message"].startswith("16 more kotlin compile errors")
    assert "Kotlin compile errors (25)" in summary.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Workflow wiring
# ---------------------------------------------------------------------------


def _job() -> dict[str, Any]:
    wf = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    return wf["jobs"][JOB]


def _gradle_step(job: dict[str, Any]) -> dict[str, Any]:
    steps = [s for s in job["steps"] if str(s.get("name", "")).startswith(GRADLE_STEP_PREFIX)]
    assert len(steps) == 1
    return steps[0]


def test_gradle_step_tees_and_is_fail_closed_statically() -> None:
    step = _gradle_step(_job())
    assert step.get("shell") == "bash"
    assert "if" not in step and "continue-on-error" not in step
    run = step["run"]
    assert "set -o pipefail" in run
    assert re.search(r"bash gradlew [^\n]*\\\n\s*\| tee \"\$\{RUNNER_TEMP\}/android-gradle.log\"", run)
    assert "|| true" not in run


def test_annotate_steps_are_failure_only_and_within_job_limit() -> None:
    job = _job()
    steps = job["steps"]
    gradle_idx = steps.index(_gradle_step(job))
    annotate = [s for s in steps if str(s.get("name", "")).startswith(ANNOTATE_PREFIX)]
    assert {s["run"].split()[2] for s in annotate} == {"kotlin", "junit", "lint", "gradle"}
    assert len(annotate) * PER_STEP_LIMIT <= PER_JOB_LIMIT
    for s in annotate:
        assert steps.index(s) > gradle_idx
        assert s.get("if") == "failure()"
        assert "uses" not in s
        assert s["run"].startswith("python3 .github/scripts/android-annotate.py ")


def test_job_gained_no_new_action() -> None:
    uses = [s["uses"].split("@")[0] for s in _job()["steps"] if "uses" in s]
    assert uses == ["actions/checkout", "actions/setup-java"]


# ---------------------------------------------------------------------------
# Fail-closed, dynamically: run the step's real `run:` text with a fake
# gradlew and a fake runner temp dir.
# ---------------------------------------------------------------------------


def _run_gradle_step(tmp_path: Path, exit_code: int, shell: list[str]
                     ) -> subprocess.CompletedProcess[str]:
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    (app / "gradlew").write_text(textwrap.dedent(f"""\
        echo "> Task :app:compileDebugKotlin FAILED"
        echo "::error::injected by test output"
        echo "e: file:///w/app/Main.kt:1:1 nope" >&2
        exit {exit_code}
        """), encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)
    script = tmp_path / "step.sh"
    script.write_text(_gradle_step(_job())["run"], encoding="utf-8")
    env = dict(os.environ, RUNNER_TEMP=str(runner_temp))
    return subprocess.run([*shell, str(script)], cwd=app, capture_output=True,
                          text=True, env=env, timeout=60, check=False)


# GitHub's `shell: bash` template, and the default (no `shell:`) one, which
# lacks pipefail -- the step's own `set -o pipefail` must carry it alone.
SHELLS = [["--noprofile", "--norc", "-eo", "pipefail"], ["-e"]]


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("flags", SHELLS, ids=["shell-bash", "default-shell"])
def test_gradle_exit_code_propagates_through_tee(tmp_path: Path, flags: list[str]) -> None:
    p = _run_gradle_step(tmp_path, 7, [BASH, *flags])
    assert p.returncode == 7, (p.stdout, p.stderr)
    log = (tmp_path / "runner_temp/android-gradle.log").read_text(encoding="utf-8")
    assert "e: file:///w/app/Main.kt:1:1 nope" in log  # stderr is teed too
    assert "::error::injected by test output" in log


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_gradle_success_stays_success(tmp_path: Path) -> None:
    p = _run_gradle_step(tmp_path, 0, [BASH, *SHELLS[0]])
    assert p.returncode == 0, (p.stdout, p.stderr)


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_gradle_output_is_fenced_by_stop_commands(tmp_path: Path) -> None:
    p = _run_gradle_step(tmp_path, 1, [BASH, *SHELLS[0]])
    lines = p.stdout.splitlines()
    stop = [i for i, ln in enumerate(lines) if ln.startswith("::stop-commands::")]
    assert len(stop) == 1
    token = lines[stop[0]].split("::")[2]
    assert re.fullmatch(r"[0-9a-f]{32}", token)
    injected = lines.index("::error::injected by test output")
    resume = lines.index(f"::{token}::")  # printed by the EXIT trap despite exit 1
    assert stop[0] < injected < resume
    # A second run gets a different token (not guessable by the build).
    p2 = _run_gradle_step(tmp_path, 1, [BASH, *SHELLS[0]])
    assert token not in p2.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_annotate_steps_run_from_workspace_root(ws: Path, tmp_path: Path) -> None:
    """Execute each annotate step's real `run:` text in a fake workspace."""
    (ws / ".github/scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, ws / ".github/scripts/android-annotate.py")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    (runner_temp / "android-gradle.log").write_text(_gradle_log(ws), encoding="utf-8")
    _write_junit(ws, "expected:<1> but was:<2>")
    reports = ws / "app/app/build/reports"
    reports.mkdir(parents=True)
    (reports / "lint-results-debug.xml").write_text(_lint_xml(ws, "unused"), encoding="utf-8")
    summary = tmp_path / "summary.md"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env.update(RUNNER_TEMP=str(runner_temp), GITHUB_WORKSPACE=str(ws),
               GITHUB_STEP_SUMMARY=str(summary))
    expected = {"kotlin": 4, "junit": 2, "lint": 2, "gradle": 1}
    total = 0
    for s in _job()["steps"]:
        if not str(s.get("name", "")).startswith(ANNOTATE_PREFIX):
            continue
        p = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", s["run"]],
                           cwd=ws, capture_output=True, text=True, env=env,
                           timeout=60, check=False)
        assert p.returncode == 0, (s["name"], p.stderr)
        n = len(parse_commands(p.stdout))
        assert n == expected[s["run"].split()[2]], (s["name"], p.stdout)
        total += n
    assert total == sum(expected.values())
    assert summary.read_text(encoding="utf-8").count("### Android CI:") == 4
