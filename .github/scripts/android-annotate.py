#!/usr/bin/env python3
"""WP CI-FIX-2: turn a failed Android CI run into GitHub annotations.

ci.yml's `android-tests` job runs one Gradle invocation (assemble + unit
tests + lint). When it fails, the only thing the public, token-less check-run
API exposes is the check run's annotations -- job logs need a token. So this
script, run from `if: failure()` steps AFTER the Gradle step, reads what that
step left behind and re-emits the interesting parts as `::error` workflow
commands, one category per invocation (= per step, see "Limits" below):

  kotlin  `e: file:///abs/path/File.kt:LINE:COL message` lines of the teed
          Gradle log (Kotlin 2.x format; the 1.x `e: /path: (L, C): msg`
          form is accepted too).
  gradle  Gradle's own `* What went wrong:` block(s) -- the fallback that
          still says something when the failure is none of the other
          categories (dependency resolution, a manifest merge, a crash).
  junit   `<failure>`/`<error>` elements of the JUnit XML reports under a
          test-results directory (more robust than scraping
          `XyzTest > name FAILED` from the console).
  lint    `<issue>` elements of AGP's lint XML report (severity Fatal,
          Error AND Warning: the app sets `warningsAsErrors = true`, so a
          warning fails the build exactly like an error). Falls back to the
          plain-text report when the XML one is absent.

Each invocation also appends a markdown section to $GITHUB_STEP_SUMMARY.

Security, and its limit: everything read here is DATA produced by the
build, and this script never lets it act as a workflow command. That
protects against command-like text the trusted code base happens to print
(a test message, a file name, a lint message containing `::`). It is NOT a
defence against a malicious pull request: that PR's build code runs in the
same job and can write workflow commands, $GITHUB_ENV or the step summary
itself, without going through this script. The only way log text
reaches stdout is through `_command()`, which escapes it per GitHub's
workflow-command rules: `%`, CR and LF in the message (so the message can
never end the line and start a fresh `::command`), plus `:` and `,` in
property values (so a value cannot close its property or the property
list). Other line-breaking or control characters are replaced first. A `::`
that survives inside a message is harmless: the runner only parses a
command at the START of a line, and every line this script prints starts
with its own `::error`.

Limits: GitHub keeps at most 10 error annotations per step and 50 per job.
Each category therefore runs as its own step and prints at most
MAX_ANNOTATIONS (10) lines; when there are more findings, the 10th is a
"N more" pointer to the step summary, which lists up to MAX_SUMMARY_ITEMS.
4 categories x 10 = 40 <= 50.

Exit status is always 0 for a parsed (possibly empty) input: these steps
only ever run after the Gradle step already failed the job, so they must
add information, never decide the result.

Standard library only; no third-party action, no pip install.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, TextIO
from urllib.parse import unquote, urlparse

#: GitHub keeps 10 error annotations per step; never print more than that.
MAX_ANNOTATIONS = 10
#: Upper bound on the step-summary list per category.
MAX_SUMMARY_ITEMS = 50
#: Annotation messages are clipped to this many characters.
MAX_MESSAGE_CHARS = 1000
#: Summary entries are clipped harder (one line each).
MAX_SUMMARY_CHARS = 300
#: Lines of a Gradle `* What went wrong:` block kept per block.
MAX_GRADLE_BLOCK_LINES = 8

LINT_SEVERITIES = frozenset({"fatal", "error", "warning"})


@dataclass(frozen=True)
class Finding:
    """One annotation-to-be. `file` is repo-relative or None."""

    title: str
    message: str
    file: str | None = None
    line: int | None = None
    col: int | None = None


# --------------------------------------------------------------------------
# Escaping (the security-relevant part)
# --------------------------------------------------------------------------

# Characters other than CR/LF that some consumer might treat as a line or
# record break, plus the remaining C0/C1 controls (ESC sequences, NUL...).
# Tab is kept. CR and LF are NOT in here: they are escaped, not replaced.
_UNSAFE_CHARS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f  ]"
)


def _sanitize(text: str) -> str:
    return _UNSAFE_CHARS.sub("�", text)


def escape_data(text: str) -> str:
    """Escape a workflow-command MESSAGE (GitHub's escapeData)."""
    return (
        _sanitize(text)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def escape_property(text: str) -> str:
    """Escape a workflow-command PROPERTY value (GitHub's escapeProperty)."""
    return (
        escape_data(text)
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _command(f: Finding) -> str:
    props: list[str] = []
    if f.file:
        props.append(f"file={escape_property(f.file)}")
        if f.line is not None and f.line > 0:
            props.append(f"line={f.line}")
            if f.col is not None and f.col > 0:
                props.append(f"col={f.col}")
    props.append(f"title={escape_property(_clip(f.title, 200))}")
    message = escape_data(_clip(f.message, MAX_MESSAGE_CHARS)) or "(no message)"
    return f"::error {','.join(props)}::{message}"


def _md(text: str) -> str:
    """Make one log-derived line inert as markdown (one list item)."""
    text = _clip(" ".join(_sanitize(text).split()), MAX_SUMMARY_CHARS)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]{}()#+!|~])", r"\\\1", text)


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def relativize(path: str, workspace: Path) -> str | None:
    """Repo-relative POSIX path if `path` is inside `workspace`, else None."""
    if path.startswith("file:"):
        path = unquote(urlparse(path).path)
    try:
        resolved = Path(os.path.normpath(path))
        ws = Path(os.path.normpath(str(workspace)))
        rel = resolved.relative_to(ws)
    except ValueError:
        return None
    if not resolved.is_absolute() or ".." in rel.parts or str(rel) == ".":
        return None
    return rel.as_posix()


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------

_KOTLIN_V2 = re.compile(r"^e: (file://\S+?):(\d+):(\d+) (.*)$")
_KOTLIN_V1 = re.compile(r"^e: (/\S.*?): \((\d+), (\d+)\): (.*)$")
_KOTLIN_NOLOC = re.compile(r"^e: (.*)$")


def parse_kotlin(lines: Iterable[str], workspace: Path) -> list[Finding]:
    out: list[Finding] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        m = _KOTLIN_V2.match(line) or _KOTLIN_V1.match(line)
        if m:
            path, ln, col, msg = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            rel = relativize(path, workspace)
            if rel is None:
                shown = unquote(urlparse(path).path) if path.startswith("file:") else path
                msg = f"{shown}:{ln}:{col} {msg}"
                out.append(Finding("Kotlin compile error", msg))
            else:
                out.append(Finding("Kotlin compile error", msg, rel, ln, col))
            continue
        m = _KOTLIN_NOLOC.match(line)
        if m:
            out.append(Finding("Kotlin compile error", m.group(1)))
    return _dedupe(out)


_GRADLE_WRONG = re.compile(r"^\* What went wrong:\s*$")
_GRADLE_BLOCK_END = re.compile(r"^\* (Try|Get more help|Exception is):")


def parse_gradle(lines: Iterable[str]) -> list[Finding]:
    out: list[Finding] = []
    block: list[str] | None = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if _GRADLE_WRONG.match(line):
            if block:
                out.append(_gradle_finding(block))
            block = []
            continue
        if block is None:
            continue
        if _GRADLE_BLOCK_END.match(line) or (not line.strip() and block):
            out.append(_gradle_finding(block))
            block = None
            continue
        if line.strip() and len(block) < MAX_GRADLE_BLOCK_LINES:
            block.append(line.strip())
    if block:
        out.append(_gradle_finding(block))
    return _dedupe(out)


def _gradle_finding(block: list[str]) -> Finding:
    return Finding("Gradle build failed", "\n".join(block))


def _iter_xml(paths: Iterable[Path]) -> Iterator[tuple[Path, ET.Element]]:
    for p in sorted(paths):
        try:
            # The XML is build output of the same (untrusted) code this job
            # already executes; expat as bundled with CPython 3.13 refuses
            # entity-expansion bombs, and no external entities are fetched.
            yield p, ET.parse(p).getroot()
        except (ET.ParseError, OSError) as exc:
            # repr(): the file name is build output too. A name such as
            # "x\n::warning::y.xml" must not reach the log as its own line
            # (stderr is part of the step log the runner parses).
            print(f"android-annotate: skipping unreadable {p.name!r}: {type(exc).__name__}",
                  file=sys.stderr)


# The method part is anything but parentheses and a line break: Kotlin
# backtick test names contain spaces, commas and dashes
# (`at pkg.FooTest.MUTATION PIN -- x(FooTest.kt:12)`). The class part cannot
# contain a space, so the lazy method part still splits at the last `.`
# before the name.
_FRAME = re.compile(r"\bat ([\w$.]+)\.[^()\n]+?\(([\w$.-]+\.(?:kt|java)):(\d+)\)")


def _junit_location(classname: str, body: str, workspace: Path,
                    test_roots: list[Path]) -> tuple[str | None, int | None]:
    """Map the first stack frame inside the test class to a source file."""
    outer = classname.split("$", 1)[0]
    pkg = outer.rpartition(".")[0]
    for m in _FRAME.finditer(body):
        frame_cls, fname, ln = m.group(1), m.group(2), int(m.group(3))
        if frame_cls.split("$", 1)[0] != outer:
            continue
        for root in test_roots:
            cand = root.joinpath(*pkg.split("."), fname) if pkg else root / fname
            try:
                if cand.is_file():
                    return relativize(str(cand.resolve()), workspace), ln
            except OSError:
                continue  # e.g. a name too long for the file system
        return None, None
    return None, None


def parse_junit(results_dir: Path, workspace: Path,
                test_roots: list[Path]) -> list[Finding]:
    if not results_dir.is_dir():
        return []
    # Debug and release unit tests run the same test classes: merge identical
    # failures and name the variants instead of annotating twice.
    merged: dict[tuple[str, str, str], tuple[Finding, list[str]]] = {}
    for path, root in _iter_xml(results_dir.rglob("*.xml")):
        variant = path.parent.name  # e.g. testDebugUnitTest
        for case in root.iter("testcase"):
            for kind in ("failure", "error"):
                for el in case.findall(kind):
                    cls = case.get("classname", "?")
                    name = case.get("name", "?")
                    msg = el.get("message")
                    if not msg:
                        body_lines = (el.text or "").strip().splitlines()
                        msg = body_lines[0] if body_lines else el.get("type", kind)
                    rel, ln = _junit_location(cls, el.text or "", workspace, test_roots)
                    short = cls.rpartition(".")[2]
                    f = Finding(f"Unit test failed: {short} > {name}",
                                f"{short} > {name}: {msg}", rel, ln)
                    key = (cls, name, msg)
                    if key in merged:
                        merged[key][1].append(variant)
                    else:
                        merged[key] = (f, [variant])
    out: list[Finding] = []
    for f, variants in merged.values():
        out.append(Finding(f.title, f"{f.message} [{', '.join(sorted(set(variants)))}]",
                           f.file, f.line))
    return out


def parse_lint_xml(xml_path: Path, workspace: Path) -> list[Finding]:
    out: list[Finding] = []
    for _, root in _iter_xml([xml_path]):
        for issue in root.iter("issue"):
            sev = (issue.get("severity") or "").strip()
            if sev.lower() not in LINT_SEVERITIES:
                continue
            issue_id = issue.get("id", "?")
            msg = issue.get("message", "")
            loc = issue.find("location")
            rel = ln = col = None
            shown = ""
            if loc is not None and loc.get("file"):
                rel = relativize(loc.get("file", ""), workspace)
                ln = _int(loc.get("line"))
                col = _int(loc.get("column"))
                if rel is None:
                    shown = f" ({loc.get('file')})"
            out.append(Finding(f"Lint {sev}: {issue_id}",
                               f"[{issue_id}] {msg}{shown}", rel, ln, col))
    return _dedupe(out)


_LINT_TXT = re.compile(r"^(.+?):(?:(\d+):)? (Fatal|Error|Warning): (.*?) \[([\w.-]+)\]\s*$")


def parse_lint_txt(lines: Iterable[str], workspace: Path,
                   project_dir: Path) -> list[Finding]:
    out: list[Finding] = []
    for raw in lines:
        m = _LINT_TXT.match(raw.rstrip("\r\n"))
        if not m:
            continue
        path, ln, sev, msg, issue_id = m.groups()
        full = path if os.path.isabs(path) else str(project_dir / path)
        rel = relativize(full, workspace)
        out.append(Finding(f"Lint {sev}: {issue_id}", f"[{issue_id}] {msg}",
                           rel, _int(ln)))
    return _dedupe(out)


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _dedupe(items: list[Finding]) -> list[Finding]:
    seen: set[Finding] = set()
    out: list[Finding] = []
    for f in items:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def emit(findings: list[Finding], label: str, stdout: TextIO,
         summary_path: str | None) -> None:
    total = len(findings)
    if total > MAX_ANNOTATIONS:
        shown = findings[: MAX_ANNOTATIONS - 1]
        rest = total - len(shown)
        tail = Finding(f"{label}: {rest} more",
                       f"{rest} more {label.lower()} not annotated (GitHub keeps "
                       f"{MAX_ANNOTATIONS} per step); see the job summary.")
        lines = [_command(f) for f in shown] + [_command(tail)]
    else:
        lines = [_command(f) for f in findings]
    for line in lines:
        stdout.write(line + "\n")
    stdout.flush()

    if not summary_path:
        return
    md = [f"### Android CI: {label} ({total})", ""]
    if not findings:
        md.append("_none found_")
    for f in findings[:MAX_SUMMARY_ITEMS]:
        loc = f"{f.file}:{f.line}" if f.file and f.line else (f.file or "")
        prefix = f"{_md(loc)}: " if loc else ""
        md.append(f"- {prefix}{_md(f.message)}")
    if total > MAX_SUMMARY_ITEMS:
        md.append(f"- ... and {total - MAX_SUMMARY_ITEMS} more")
    md.append("")
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("category", choices=("kotlin", "gradle", "junit", "lint"))
    ap.add_argument("--workspace", default=os.environ.get("GITHUB_WORKSPACE", os.getcwd()))
    ap.add_argument("--log", help="teed Gradle console log (kotlin, gradle)")
    ap.add_argument("--results-dir", help="JUnit XML directory (junit)")
    ap.add_argument("--test-root", action="append", default=[],
                    help="test source root for file/line mapping (junit; repeatable)")
    ap.add_argument("--lint-xml", help="lint XML report (lint)")
    ap.add_argument("--lint-txt", help="lint text report, used if the XML is absent (lint)")
    ap.add_argument("--project-dir", help="Gradle module dir lint-txt paths are relative to")
    args = ap.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    summary = os.environ.get("GITHUB_STEP_SUMMARY") or None

    if args.category in ("kotlin", "gradle"):
        if not args.log:
            ap.error("--log is required")
        lines = _read_lines(Path(args.log))
        if args.category == "kotlin":
            emit(parse_kotlin(lines, workspace), "Kotlin compile errors", sys.stdout, summary)
        else:
            emit(parse_gradle(lines), "Gradle failure", sys.stdout, summary)
    elif args.category == "junit":
        if not args.results_dir:
            ap.error("--results-dir is required")
        roots = [Path(r).resolve() for r in args.test_root]
        emit(parse_junit(Path(args.results_dir), workspace, roots),
             "Failed unit tests", sys.stdout, summary)
    else:
        findings: list[Finding] = []
        if args.lint_xml and Path(args.lint_xml).is_file():
            findings = parse_lint_xml(Path(args.lint_xml), workspace)
        elif args.lint_txt and Path(args.lint_txt).is_file():
            project = Path(args.project_dir).resolve() if args.project_dir else workspace
            findings = parse_lint_txt(_read_lines(Path(args.lint_txt)), workspace, project)
        emit(findings, "Lint issues", sys.stdout, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
