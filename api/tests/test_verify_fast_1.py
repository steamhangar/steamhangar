"""WP VERIFY-FAST-1: section selection and timing in deploy/tests/verify-stack.sh.

No Docker anywhere: the selection library (deploy/tests/verify-sections.sh)
runs under ``sh``, verify-stack.sh itself runs with ``VERIFY_PLAN=1`` or with
a ``docker`` stub on PATH that only records its arguments.

Guarantees, one named test (or group) each:

V1  default = full: VERIFY_SECTIONS unset or empty selects every id, skips
    nothing, needs the image builds and the stack start.
V2  preset ``lean`` expands to exactly 1, 3, 8, 5a-5d and 6a-6e, holds no
    CDN/internet step, and still needs the builds and one stack start.
V3  parsing: single ids, step ranges in script order (5a2 inside 5a-5d),
    section ranges, unions with presets, blanks around tokens, ids that
    contain "-" (4-settings, 3.5-runner, 6i-core, also as range ends).
V4  dependencies are added and reported (5f/5g -> 5e, 9 -> 3); section 7
    plans the stack start (its 7h check needs vault-api).
V5  unknown ids, step ids of sections without step selection, backwards
    ranges, ranges across step groups and empty lists fail with exit 2 and a
    message naming the token, BEFORE any Docker call or output on stdout.
V6  timing format: ``[time] start|end <id> <UTC ISO>``, seconds per part,
    the summary and the total (deterministic with a fake clock).
V7  end to end with a docker stub: VERIFY_SECTIONS=1 runs section 1 only,
    prints one ``skip <id>`` line per other section, never builds or starts
    anything, still runs Cleanup, ends with the timing summary.
V8  structural: every step id of sections 4-6 in the selection library is
    guarded by ``if vs_step <id>; then`` directly above its ``step`` line and
    every step of those sections is in the library (both directions); every
    plain section is guarded by ``vs_section``.
V9  CI keeps running everything: the verify-stack job sets VERIFY_SECTIONS
    to ``full``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY = REPO_ROOT / "deploy" / "tests" / "verify-stack.sh"
LIB = REPO_ROOT / "deploy" / "tests" / "verify-sections.sh"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")

GROUP_4 = "4-settings 3.5-runner 4a 4b 4c 4d".split()
GROUP_5 = "5a 5a2 5b 5c 5d 5e 5f 5g 5h 5i 5j".split()
GROUP_6 = "6a 6b 6c 6d 6e 6f 6g 6h 6i 6i-core 6s 6v 6w 6x 6j 6k 6l".split()
ALL = ["1", "2", "3", *GROUP_4, *GROUP_5, *GROUP_6, "7", "8", "9", "10", "11"]
LEAN = ["1", "3", "5a", "5a2", "5b", "5c", "5d", "6a", "6b", "6c", "6d", "6e", "8"]
#: steps and sections that reach the Steam CDN or another internet host
INTERNET = ["5e", "5f", "5g", "5i", "5j", "6l", "7"]


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VERIFY_")}
    env.update(extra)
    return env


def _resolve(spec: str) -> subprocess.CompletedProcess[str]:
    script = (
        '. "$1"; vs_resolve "$2" || exit $?; '
        'printf "sel=%s\\nadded=%s\\nimages=%s\\nstack=%s\\n" '
        '"$VS_SELECTED" "$VS_ADDED" "$VS_NEED_IMAGES" "$VS_NEED_STACK"'
    )
    return subprocess.run(
        ["sh", "-c", script, "sh", str(LIB), spec],
        capture_output=True, text=True, env=_clean_env(), timeout=30,
    )


def _ok(spec: str) -> dict[str, str]:
    res = _resolve(spec)
    assert res.returncode == 0, res.stderr
    return dict(line.split("=", 1) for line in res.stdout.splitlines())


def _plan(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(VERIFY)], capture_output=True, text=True,
        env=_clean_env(VERIFY_PLAN="1", **env), timeout=30,
    )


# --- V1 ---------------------------------------------------------------------
@pytest.mark.parametrize("env", [{}, {"VERIFY_SECTIONS": ""}, {"VERIFY_SECTIONS": "full"}])
def test_v1_default_is_full(env: dict[str, str]) -> None:
    res = _plan(env)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert "VERIFY_SECTIONS=full" in lines
    assert f"run: {' '.join(ALL)}" in lines
    assert not [line for line in lines if line.startswith("skip ")]
    assert "image builds: yes" in lines
    assert "stack start: yes" in lines


# --- V2 ---------------------------------------------------------------------
def test_v2_lean_preset() -> None:
    out = _ok("lean")
    assert out["sel"].split() == LEAN
    assert not set(out["sel"].split()) & set(INTERNET)
    assert out["images"] == "yes"
    assert out["stack"] == "yes"
    assert out["added"] == ""


def test_v2_lean_plan_skips_everything_else() -> None:
    res = _plan({"VERIFY_SECTIONS": "lean"})
    assert res.returncode == 0, res.stderr
    skips = [line.split()[1] for line in res.stdout.splitlines() if line.startswith("skip ")]
    assert skips == [x for x in ALL if x not in LEAN]


# --- V3 ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("5a-5d", ["5a", "5a2", "5b", "5c", "5d"]),
        ("1-3", ["1", "2", "3"]),
        ("4-6", [*GROUP_4, *GROUP_5, *GROUP_6]),
        ("5", GROUP_5),
        ("4-settings", ["4-settings"]),
        ("3.5-runner-4b", ["3.5-runner", "4a", "4b"]),
        ("6i-core-6s", ["6i-core", "6s"]),
        ("6i-6i-core", ["6i", "6i-core"]),
        (" 8 , 1 ", ["1", "8"]),
        ("1,,3", ["1", "3"]),
        ("lean,5e,lean", [*LEAN[:7], "5e", *LEAN[7:]]),
        ("11,full", ALL),
    ],
)
def test_v3_parsing(spec: str, expected: list[str]) -> None:
    assert _ok(spec)["sel"].split() == expected


@pytest.mark.parametrize(
    ("spec", "images", "stack"),
    [("1", "no", "no"), ("3", "no", "no"), ("8", "yes", "no"), ("7", "yes", "yes"),
     ("11", "yes", "no"), ("10", "yes", "yes"), ("6e", "yes", "yes"), ("4a", "yes", "yes")],
)
def test_v3_derived_needs(spec: str, images: str, stack: str) -> None:
    out = _ok(spec)
    assert (out["images"], out["stack"]) == (images, stack)


# --- V4 ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("spec", "sel", "added"),
    [("5f", "5e 5f", "5e:5f"), ("5g", "5e 5g", "5e:5g"), ("5f,5g", "5e 5f 5g", "5e:5f"),
     ("9", "3 9", "3:9"), ("5e,5f", "5e 5f", "")],
)
def test_v4_dependencies_are_added(spec: str, sel: str, added: str) -> None:
    out = _ok(spec)
    assert out["sel"] == sel
    assert out["added"] == added


def test_v4_plan_reports_the_added_dependency() -> None:
    res = _plan({"VERIFY_SECTIONS": "9"})
    assert "add 3  (needed by 9)" in res.stdout.splitlines()


def test_v4_section_7_plans_the_stack_start() -> None:
    """7h curls $API_URL/v1/about, so VERIFY_SECTIONS=7 must start vault-api."""
    res = _plan({"VERIFY_SECTIONS": "7"})
    assert res.returncode == 0, res.stderr
    assert "stack start: yes" in res.stdout.splitlines()


# --- V5 ---------------------------------------------------------------------
BAD = [
    ("bogus", "unknown section id or preset 'bogus'"),
    ("12", "unknown section id or preset '12'"),
    ("3e", "unknown section id '3e' (step ids exist for sections 4, 5 and 6 only"),
    ("2.web", "unknown section id '2.web'"),
    ("5z", "unknown step id '5z' in section 5"),
    ("5d-5a", "range '5d-5a' runs backwards"),
    ("3-1", "range '3-1' runs backwards"),
    ("4a-5b", "range '4a-5b' crosses sections"),
    ("3-5a", "range '3-5a' mixes a section number and a step id"),
    (",", "selects nothing"),
    ("lean,nope", "unknown section id or preset 'nope'"),
]


@pytest.mark.parametrize(("spec", "message"), BAD)
def test_v5_bad_selection_fails_in_the_library(spec: str, message: str) -> None:
    res = _resolve(spec)
    assert res.returncode == 2
    assert message in res.stderr
    assert f"VERIFY_SECTIONS={spec}:" in res.stderr


@pytest.mark.parametrize(("spec", "message"), BAD)
def test_v5_bad_selection_stops_verify_stack_before_docker(
    tmp_path: Path, spec: str, message: str
) -> None:
    log = _docker_stub(tmp_path)
    res = subprocess.run(
        ["sh", str(VERIFY)], capture_output=True, text=True, timeout=30,
        env=_clean_env(VERIFY_SECTIONS=spec, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}"),
    )
    assert res.returncode == 2
    assert message in res.stderr
    assert res.stdout == ""
    assert not log.exists(), "docker was called before the selection was validated"


# --- V6 ---------------------------------------------------------------------
ISO = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


def test_v6_timing_lines_with_a_fake_clock(tmp_path: Path) -> None:
    # vs_epoch is replaced by a counter file (+5 s per call): the library
    # calls it in command substitutions, so a shell variable would not count.
    clock = tmp_path / "clock"
    clock.write_text("1000\n")
    script = (
        '. "$1"; clock=$2; '
        'vs_epoch() { vs_n=$(($(cat "$clock") + 5)); echo "$vs_n" > "$clock"; echo "$vs_n"; }; '
        "VS_T_START=1000; vs_begin 1; vs_begin 2; vs_end; vs_end; vs_begin cleanup; vs_report"
    )
    res = subprocess.run(
        ["sh", "-c", script, "sh", str(LIB), str(clock)], capture_output=True, text=True,
        env=_clean_env(), timeout=30,
    )
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    pattern = [
        rf"\[time\] start 1 {ISO}",
        rf"\[time\] end 1 {ISO} 5s",  # vs_begin 2 closes the open part first
        rf"\[time\] start 2 {ISO}",
        rf"\[time\] end 2 {ISO} 5s",
        rf"\[time\] start cleanup {ISO}",
        rf"\[time\] end cleanup {ISO} 5s",
        r"\[time\] section 1 5s",
        r"\[time\] section 2 5s",
        r"\[time\] section cleanup 5s",
        r"\[time\] total 35s",
    ]
    assert len(lines) == len(pattern), lines
    for line, pat in zip(lines, pattern):
        assert re.fullmatch(pat, line), (line, pat)


def test_v6_skip_and_step_helpers() -> None:
    script = (
        '. "$1"; vs_resolve 5a >/dev/null; '
        "vs_step 5a && echo run-5a; vs_step 5b || echo no-5b; "
        "vs_section 7 || echo no-7; vs_skip 2 'a note'"
    )
    res = subprocess.run(
        ["sh", "-c", script, "sh", str(LIB)], capture_output=True, text=True,
        env=_clean_env(), timeout=30,
    )
    assert res.stdout.splitlines() == [
        "run-5a", "skip 5b", "no-5b", "skip 7", "no-7", "skip 2  (a note)",
    ]


# --- V7 ---------------------------------------------------------------------
def _docker_stub(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    stub = bindir / "docker"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\nexit 0\n")
    stub.chmod(0o755)
    return log


def test_v7_section_1_only_end_to_end(tmp_path: Path) -> None:
    log = _docker_stub(tmp_path)
    res = subprocess.run(
        ["sh", str(VERIFY)], capture_output=True, text=True, timeout=120,
        env=_clean_env(VERIFY_SECTIONS="1", PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}"),
    )
    out = res.stdout.splitlines()
    assert res.returncode == 0, res.stdout[-2000:]
    assert [line for line in out if line.startswith("skip ")] == [
        f"skip {n}" for n in range(2, 12)
    ]
    assert "## 1. Config drift: the container nginx.conf is the reviewed one" in out
    assert not [line for line in out if re.match(r"## ([2-9]|1[01])\. ", line)]
    assert "## Cleanup" in out
    times = [line for line in out if line.startswith("[time] ")]
    assert re.fullmatch(rf"\[time\] start 1 {ISO}", times[0])
    assert re.fullmatch(rf"\[time\] end 1 {ISO} \d+s", times[1])
    assert [t.rsplit(" ", 1)[0] for t in times[-3:]] == [
        "[time] section 1", "[time] section cleanup", "[time] total",
    ]
    calls = log.read_text().splitlines()
    assert not [c for c in calls if c.startswith(("build", "run", "compose --env-file")) and " down " not in c]
    assert any(" down -v --remove-orphans" in c for c in calls), "Cleanup must still run"


# --- V8 ---------------------------------------------------------------------
def _script_lines() -> list[str]:
    return VERIFY.read_text().splitlines()


def test_v8_library_lists_match_the_script() -> None:
    lib = LIB.read_text()
    for name, group in (("VS_GROUP_4", GROUP_4), ("VS_GROUP_5", GROUP_5), ("VS_GROUP_6", GROUP_6)):
        assert f'{name}="{" ".join(group)}"' in lib, f"{name} drifted from this test's copy"
    lines = _script_lines()
    starts = {n: next(i for i, l in enumerate(lines) if l.startswith(f'section "{n}. ')) for n in (4, 7)}
    steps = [
        m.group(1)
        for l in lines[starts[4]:starts[7]]
        if (m := re.match(r'step "([^ "]+?)\. ', l))
    ]
    assert steps == [*GROUP_4, *GROUP_5, *GROUP_6], (
        "a step was added to or removed from sections 4-6: update the group in "
        "deploy/tests/verify-sections.sh (and this test) so it can be selected and skipped"
    )


def test_v8_every_step_is_guarded() -> None:
    lines = _script_lines()
    for sid in [*GROUP_4, *GROUP_5, *GROUP_6]:
        idx = [i for i, l in enumerate(lines) if l.startswith(f'step "{sid}. ')]
        assert len(idx) == 1, sid
        above = idx[0] - 1
        while lines[above].startswith("#"):  # a step's own comment block (5i)
            above -= 1
        assert lines[above] == f"if vs_step {sid}; then", f"step {sid} has no vs_step guard"


def test_v8_every_plain_section_is_guarded() -> None:
    lines = _script_lines()
    for n in (1, 3, 5, 6, 7, 8, 9, 10, 11):
        idx = next(i for i, l in enumerate(lines) if l.startswith(f'section "{n}. '))
        assert lines[idx - 2] == f"if vs_section {n}; then", f"section {n} has no vs_section guard"
    for n, flag in ((2, "VS_NEED_IMAGES"), (4, "VS_NEED_STACK")):
        idx = next(i for i, l in enumerate(lines) if l.startswith(f'section "{n}. '))
        assert lines[idx - 3] == f'if [ "${flag}" = yes ]; then', f"section {n} is not gated on {flag}"


def test_v8_selection_is_resolved_before_any_side_effect() -> None:
    text = VERIFY.read_text()
    resolve = text.index('vs_resolve "${VERIFY_SECTIONS:-full}" || exit 2')
    assert resolve < text.index("work=$(mktemp -d)")
    assert resolve < text.index("trap cleanup EXIT")


# --- V9 ---------------------------------------------------------------------
def test_v9_ci_runs_full() -> None:
    ci = CI.read_text()
    job = ci[ci.index("\n  verify-stack:\n"):]
    step = job[: job.index("run: bash deploy/tests/verify-stack.sh")]
    assert re.search(r"\n\s+env:\n\s+VERIFY_SECTIONS: full\n\s+$", step), (
        "the nightly verify-stack job must set VERIFY_SECTIONS: full"
    )
