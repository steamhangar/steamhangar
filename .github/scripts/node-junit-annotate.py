#!/usr/bin/env python3
"""WP CI-FIX-4: turn a failed `node --test` run into GitHub annotations.

ci.yml's `web-tests` job runs Node's built-in test runner with two
reporters: `spec` to stdout (the human log) and `junit` to a file. When the
step fails, the job log needs a GitHub login, but a check run's annotations
are readable through the public, token-less API. This script, run from an
`if: failure()` step after the test step, reads the junit file and prints
one `::error` workflow command per failing test case.

What Node's junit reporter writes (measured on Node v22.23.3; ci.yml runs
Node 24 and is assumed to write the same shape):

- `<testsuites>` holding `<testcase>` and nested `<testsuite>` elements; a
  `describe()` block -- and a test that has subtests -- becomes a
  `<testsuite name=...>`, so the annotation title joins the suite names
  (`suite > test`).
- `classname` is always the literal "test"; there is NO file attribute.
  The file and line are therefore taken from the first stack frame of the
  failure body that points into the workspace (outside `node_modules/`),
  preferring a `*.test.js` frame -- ES-module frames are `file:///...` URLs.
- The `message` attribute has its line breaks DELETED ("line oneline two"),
  so the message is taken from the failure body instead: its text up to the
  first stack frame, minus the `Error [ERR_TEST_FAILURE]: ` wrapper.
- A test file that fails as a whole (crash, syntax error, non-zero exit
  outside a test) is a top-level `<testcase>` named by the file's path AS
  GIVEN ON THE COMMAND LINE -- relative (`web/tests/x.test.js`) for ci.yml's
  relative glob, absolute for an absolute one -- with the bare message
  "test failed"; the crash text itself only reaches the spec log. A
  top-level case whose name resolves to a test file inside the workspace is
  therefore annotated on that file, with a hint.
- `failureType: 'subtestsFailed'` marks a parent that failed only because a
  child did; it is dropped when the child carries its own failure.
- Node escapes `"` twice in attribute values (`&amp;quot;`); the leftover
  `&quot;` in a test name is turned back into `"`.

Security: everything read here is test output, i.e. DATA. All of it goes
through android-annotate.py's `_command()` (WP CI-FIX-2), which escapes it
per GitHub's workflow-command rules (`%`, CR, LF in the message; `:` and `,`
additionally in properties; other control characters replaced), so no test
name or message can end its line and start a command of its own. That
escaping is reused, not copied, so both scripts share one tested
implementation.

Limits: GitHub keeps at most 10 error annotations per step, so at most
MAX_ANNOTATIONS lines are printed; with more failures the 10th line is a
"N more" pointer. A missing, unreadable or failure-free report still yields
one annotation that says so -- this step only runs after the test step
failed, and "it failed, but the report shows nothing" is itself the useful
finding.

Exit status is always 0: the test step already decided the job's result.

Standard library only; no third-party action, no pip install.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType
from typing import Iterator, TextIO


def _load_android_annotate() -> ModuleType:
    """Import the sibling CI-FIX-2 script (its file name has a dash)."""
    path = Path(__file__).resolve().with_name("android-annotate.py")
    spec = importlib.util.spec_from_file_location("android_annotate", path)
    if spec is None or spec.loader is None:  # pragma: no cover - repo layout
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("android_annotate", mod)
    spec.loader.exec_module(mod)
    return mod


_aa = _load_android_annotate()
Finding = _aa.Finding
command = _aa._command
relativize = _aa.relativize

#: GitHub keeps 10 error annotations per step; never print more than that.
MAX_ANNOTATIONS = 10
#: Lines of a failure message kept per annotation ("first lines"); enough
#: for a short deepStrictEqual diff. `_command()` still clips at 1000 chars.
MAX_MESSAGE_LINES = 12
#: Title of the annotations that are not about one test case.
REPORT_TITLE = "web tests"

#: A stack frame: `at fn (/abs/path:L:C)`, `at /abs/path:L:C` or the same
#: with a `file:///` URL (ES modules).
_FRAME = re.compile(r"\bat (?:[^\n]*? \()?((?:file://)?/[^():\n]+):(\d+):(\d+)\)?")
_STACK_LINE = re.compile(r"^\s+at \S")
#: The ERR_TEST_FAILURE wrapper around every failure body, optionally inside
#: util.inspect's `[...]` brackets: `[Error [ERR_TEST_FAILURE]: msg`.
_WRAPPER = re.compile(r"^\[?(?:[\w.]*Error(?: \[[A-Z0-9_]+\])?: )?")
#: Where util.inspect starts the error's own properties when the error has
#: no stack (`[Error: msg] { code: ... }`); with a stack, the first `at`
#: line ends the message first. A bare `{` line is message text (an
#: assertion diff), not this.
_PROPS_START = re.compile(r"\]\s*\{")
_FAILURE_TYPE = re.compile(r"failureType: '(\w+)'")
_TEST_FILE = re.compile(r"\.test\.[cm]?js$")


def _frames(body: str, workspace: Path) -> Iterator[tuple[str, int]]:
    for m in _FRAME.finditer(body):
        rel = relativize(m.group(1), workspace)
        if rel is not None and "node_modules" not in rel.split("/"):
            yield rel, int(m.group(2))


def location(body: str, workspace: Path) -> tuple[str | None, int | None]:
    """File and line of the failure: the first frame in a test file, else
    the first frame anywhere in the workspace, else none."""
    frames = list(_frames(body, workspace))
    for rel, ln in frames:
        if _TEST_FILE.search(rel):
            return rel, ln
    return frames[0] if frames else (None, None)


def message_lines(body: str, fallback: str) -> list[str]:
    """The failure's own message: body text before the first stack frame or
    the error-properties block, without the ERR_TEST_FAILURE wrapper."""
    text = _WRAPPER.sub("", body.strip(), count=1)
    out: list[str] = []
    for line in text.splitlines():
        if _STACK_LINE.match(line):
            break
        cut = _PROPS_START.search(line)
        if cut:
            head = line[: cut.start()].rstrip()
            if head:
                out.append(head)
            break
        out.append(line.rstrip())
    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    if not out:
        out = [fallback] if fallback else []
    if len(out) > MAX_MESSAGE_LINES:
        out = out[:MAX_MESSAGE_LINES] + ["…"]
    return out


def as_test_file(name: str, workspace: Path) -> str | None:
    """Workspace-relative path if a top-level test case's name is a test
    file (absolute, `file:` URL, or relative to the workspace = node's cwd)."""
    if name.startswith(("/", "file:")):
        rel = relativize(name, workspace)
    else:
        rel = relativize(str(workspace / name), workspace)
    if rel is None:
        return None
    try:
        exists = (workspace / rel).is_file()
    except OSError:
        exists = False
    return rel if _TEST_FILE.search(rel) or exists else None


def _name(el: ET.Element) -> str:
    return (el.get("name") or "?").replace("&quot;", '"')


def _walk(el: ET.Element, path: tuple[str, ...]
          ) -> Iterator[tuple[tuple[str, ...], ET.Element]]:
    for child in el:
        if child.tag == "testsuite":
            yield from _walk(child, path + (_name(child),))
        elif child.tag == "testcase":
            yield path, child


def parse(root: ET.Element, workspace: Path) -> list[Finding]:
    """One Finding per failing test case, in report order."""
    real: list[Finding] = []
    parents: list[Finding] = []
    for suites, case in _walk(root, ()):
        for el in case.findall("failure"):
            body = el.text or ""
            ftype_m = _FAILURE_TYPE.search(body)
            ftype = ftype_m.group(1) if ftype_m else (el.get("type") or "")
            name = _name(case)
            file: str | None
            line: int | None
            # Only a TOP-LEVEL case can be a whole file; inside a suite a
            # path-like name is just a test name.
            as_file = None if suites else as_test_file(name, workspace)
            if as_file is not None:
                # The test FILE failed as a whole (see the docstring).
                file, line = as_file, None
                title = f"Web test file failed: {as_file}"
                lines = message_lines(body, el.get("message") or "failure")
                lines.append("(the whole file failed: crash, load error or non-zero "
                             "exit outside a test -- the details are only in the "
                             "spec output of the job log)")
            else:
                file, line = location(body, workspace)
                title = "Web test failed: " + " > ".join(suites + (name,))
                lines = message_lines(body, el.get("message") or "failure")
                if ftype and ftype != "testCodeFailure":
                    lines.append(f"[{ftype}]")
            f = Finding(title, "\n".join(lines), file, line)
            (parents if ftype == "subtestsFailed" else real).append(f)
    return real or parents


def findings_for(report: Path, workspace: Path) -> list[Finding]:
    """Findings for the report, or one finding explaining why there are none."""
    if not report.is_file():
        return [Finding(REPORT_TITLE, "the job failed before or without writing a junit "
                        f"report ({report.name}): checkout, setup-node or node --test; "
                        "see the job log.")]
    try:
        root = ET.parse(report).getroot()
    except (ET.ParseError, OSError) as exc:
        return [Finding(REPORT_TITLE, f"node --test failed and its junit report "
                        f"({report.name}) is unreadable: {type(exc).__name__}.")]
    found = parse(root, workspace)
    if not found:
        return [Finding(REPORT_TITLE, "node --test failed, but its junit report lists "
                        "no failing test case; see the job log.")]
    return found


def emit(findings: list[Finding], stdout: TextIO) -> None:
    total = len(findings)
    if total > MAX_ANNOTATIONS:
        shown = findings[: MAX_ANNOTATIONS - 1]
        rest = total - len(shown)
        tail = Finding(f"{REPORT_TITLE}: {rest} more",
                       f"{rest} more failing web tests not annotated (GitHub keeps "
                       f"{MAX_ANNOTATIONS} per step); see the job log.")
        findings = shown + [tail]
    for f in findings:
        stdout.write(command(f) + "\n")
    stdout.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", help="junit XML written by node --test")
    ap.add_argument("--workspace", default=os.environ.get("GITHUB_WORKSPACE", os.getcwd()))
    args = ap.parse_args(argv)
    emit(findings_for(Path(args.report), Path(args.workspace).resolve()), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
