"""WP CI-FIX-3: the release APK's signing-certificate digest is parsed robustly.

Tag v0.1.0-rc10 signed and verified its APK, then refused to publish it:
current build-tools print `V2 Signer: certificate SHA-256 digest: <hex>`,
and the inline sed in publish.yml only accepted lines starting with
`Signer`. The parser now lives in `.github/scripts/apk-cert-digest.sh`;
this module pins:

- the digest parser against fixture transcripts of every known shape
  (the verbatim rc10 output, the old `Signer #1` form, a v3.1
  `Signer (minSdkVersion=...)` block repeating the same certificate, CRLF,
  upper-case hex) and its fail-closed cases (zero certificates, two
  different ones, SHA-1/MD5-only lines, a source-stamp signer only),
- the `annotate` mode: apksigner's verdict and signer lines become one
  escaped `::notice::` per transcript, so a refused release explains
  itself through token-less check-run annotations,
- the workflow wiring, by executing the publish.yml step's real `run:`
  text with a fake apksigner: success writes `cert_sha256`, any failure
  exits non-zero AND leaves the annotations behind.

Local `sh`/`bash` subprocesses only; no Android SDK, Docker or network.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "apk-cert-digest.sh"
PUBLISH_PATH = REPO_ROOT / ".github" / "workflows" / "publish.yml"
JOB = "android-release"
STEP = "Verify APK signature (apksigner)"

SH = shutil.which("sh")
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(SH is None or BASH is None, reason="sh/bash not available")

RC10_DIGEST = "f095d5abda5acb23f3f07c9fa68a5526a514f1f4e600fca17162aa8d17c66172"
OTHER_DIGEST = "0123456789abcdef" * 4

# Verbatim from the v0.1.0-rc10 android-release job log (public values only).
RC10_PRINT_CERTS = """\
Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): false
Verified using v3.1 scheme (APK Signature Scheme v3.1): false
Verified using v3.2 scheme (APK Signature Scheme v3.2): false
Verified using v4 scheme (APK Signature Scheme v4): false
Verified for SourceStamp: false
Number of signers: 1
V2 Signer: certificate DN: CN=Jan Niesel, O=SteamHangar
V2 Signer: certificate SHA-256 digest: f095d5abda5acb23f3f07c9fa68a5526a514f1f4e600fca17162aa8d17c66172
V2 Signer: certificate SHA-1 digest: a3d146832125d2ae1b22ce81882b4522d0d21193
V2 Signer: certificate MD5 digest: 6b39b965ff80cab6ee32a2c66c3d17a6
"""

# Older build-tools shape (synthetic DN and digests).
OLD_SIGNER_1 = f"""\
Signer #1 certificate DN: CN=Example, O=Example
Signer #1 certificate SHA-256 digest: {RC10_DIGEST}
Signer #1 certificate SHA-1 digest: {"ab" * 20}
Signer #1 certificate MD5 digest: {"cd" * 16}
"""

# v3.1 rotation-targeted block: the same certificate listed per block.
V31_SAME_CERT_TWICE = f"""\
Signer (minSdkVersion=24, maxSdkVersion=32) certificate DN: CN=Example
Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: {RC10_DIGEST}
Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate DN: CN=Example
Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: {RC10_DIGEST}
V3.1 Signer: certificate SHA-256 digest: {RC10_DIGEST.upper()}
V3 Signer: certificate SHA-256 digest: {RC10_DIGEST}
"""

TWO_CERTS = f"""\
Number of signers: 2
Signer #1 certificate SHA-256 digest: {RC10_DIGEST}
Signer #2 certificate SHA-256 digest: {OTHER_DIGEST}
"""

ZERO_CERTS = """\
Verifies
Verified using v2 scheme (APK Signature Scheme v2): true
Number of signers: 1
"""

ONLY_SHA1_MD5 = f"""\
V2 Signer: certificate SHA-1 digest: {"ab" * 20}
V2 Signer: certificate MD5 digest: {"cd" * 16}
"""

# A source stamp is not the signing certificate and must not be counted.
ONLY_SOURCE_STAMP = f"""\
Source Stamp Signer: certificate SHA-256 digest: {RC10_DIGEST}
"""

# A digest that is one hex digit short must not be truncated into a match.
SHORT_DIGEST = f"V2 Signer: certificate SHA-256 digest: {RC10_DIGEST[:-1]}\n"


def _digest(tmp_path: Path, text: str, *, newline: str = "\n"
            ) -> subprocess.CompletedProcess[str]:
    f = tmp_path / "certs.txt"
    f.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return subprocess.run([SH, str(SCRIPT), "digest", str(f)], capture_output=True,
                          text=True, timeout=30, check=False)


@pytest.mark.parametrize("text", [RC10_PRINT_CERTS, OLD_SIGNER_1, V31_SAME_CERT_TWICE],
                         ids=["rc10-verbatim-v2-signer", "old-signer-1", "v31-same-cert"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_digest_accepts_every_known_shape(tmp_path: Path, text: str, newline: str) -> None:
    p = _digest(tmp_path, text, newline=newline)
    assert p.returncode == 0, p.stderr
    assert p.stdout == RC10_DIGEST + "\n"
    assert p.stderr == ""


def test_digest_lowercases_upper_case_hex(tmp_path: Path) -> None:
    p = _digest(tmp_path, f"V2 Signer: certificate SHA-256 digest: {RC10_DIGEST.upper()}\n")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == RC10_DIGEST


@pytest.mark.parametrize(("text", "found"), [
    (TWO_CERTS, 2),
    (ZERO_CERTS, 0),
    ("", 0),
    (ONLY_SHA1_MD5, 0),
    (ONLY_SOURCE_STAMP, 0),
    (SHORT_DIGEST, 0),
], ids=["two-certs", "zero", "empty", "sha1-md5-only", "source-stamp-only", "short-digest"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_digest_fails_closed(tmp_path: Path, text: str, found: int, newline: str) -> None:
    p = _digest(tmp_path, text, newline=newline)
    assert p.returncode == 1
    assert p.stdout == ""
    assert p.stderr.startswith("::error::")
    assert f"found {found}" in p.stderr


def test_digest_fails_closed_on_missing_transcript(tmp_path: Path) -> None:
    p = subprocess.run([SH, str(SCRIPT), "digest", str(tmp_path / "nope.txt")],
                       capture_output=True, text=True, timeout=30, check=False)
    assert p.returncode == 1
    assert p.stdout == ""
    assert "missing or unreadable" in p.stderr


@pytest.mark.parametrize("argv", [[], ["digest"], ["digest", "a", "b"], ["bogus", "a"]])
def test_usage_errors_fail(argv: list[str]) -> None:
    p = subprocess.run([SH, str(SCRIPT), *argv], capture_output=True, text=True,
                       timeout=30, check=False)
    assert p.returncode == 2
    assert p.stdout == ""


# ---------------------------------------------------------------------------
# annotate mode
# ---------------------------------------------------------------------------

_CMD = re.compile(r"^::([A-Za-z-]+)(?: ([^:]*))?::(.*)$")


def _commands(out: str) -> list[tuple[str, str, str]]:
    """Model of the runner's line parser: one workflow command per line."""
    cmds = []
    for line in out.splitlines():
        m = _CMD.match(line)
        if m:
            cmds.append((m.group(1), m.group(2) or "", m.group(3)))
    return cmds


def _unescape(s: str) -> str:
    return s.replace("%0D", "\r").replace("%0A", "\n").replace("%25", "%")


def _annotate(tmp_path: Path, *files: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([SH, str(SCRIPT), "annotate", *map(str, files)],
                          capture_output=True, text=True, timeout=30, check=False)


def test_annotate_carries_verdict_and_signer_lines(tmp_path: Path) -> None:
    f = tmp_path / "apksigner-certs.txt"
    f.write_bytes(RC10_PRINT_CERTS.replace("\n", "\r\n").encode("utf-8"))
    p = _annotate(tmp_path, f)
    assert p.returncode == 0, p.stderr
    cmds = _commands(p.stdout)
    assert len(cmds) == 1 == len(p.stdout.splitlines())
    name, props, data = cmds[0]
    assert name == "notice"
    assert props == "title=apksigner output"
    body = _unescape(data)
    assert "\r" not in body
    assert body.splitlines() == ["apksigner-certs.txt:", *RC10_PRINT_CERTS.splitlines()]


def test_annotate_escapes_percent_and_drops_unrelated_lines(tmp_path: Path) -> None:
    f = tmp_path / "t.txt"
    f.write_text("noise line\n"
                 "V2 Signer: certificate DN: CN=100%0A::error::forged\n"
                 "ERROR: APK Signature Scheme v2 signer #1: digest mismatch\n",
                 encoding="utf-8")
    p = _annotate(tmp_path, f)
    cmds = _commands(p.stdout)
    assert [c[0] for c in cmds] == ["notice"]
    body = _unescape(cmds[0][2])
    assert body.splitlines() == [
        "t.txt:",
        "V2 Signer: certificate DN: CN=100%0A::error::forged",
        "ERROR: APK Signature Scheme v2 signer #1: digest mismatch",
    ]


def test_annotate_reports_missing_and_lineless_transcripts(tmp_path: Path) -> None:
    noise = tmp_path / "noise.txt"
    noise.write_text("Picked up JAVA_TOOL_OPTIONS: -Dx=y\n", encoding="utf-8")
    p = _annotate(tmp_path, tmp_path / "missing.txt", noise)
    assert p.returncode == 0, p.stderr
    bodies = [_unescape(c[2]) for c in _commands(p.stdout)]
    assert len(bodies) == 2
    assert bodies[0] == "missing.txt: not produced or empty"
    assert bodies[1].splitlines() == ["noise.txt:", "(no apksigner verdict or signer lines in the transcript)"]


def test_annotate_caps_line_count(tmp_path: Path) -> None:
    f = tmp_path / "t.txt"
    f.write_text("".join(f"WARNING: entry {i}\n" for i in range(200)), encoding="utf-8")
    body = _unescape(_commands(_annotate(tmp_path, f).stdout)[0][2])
    assert len(body.splitlines()) == 1 + 40


# ---------------------------------------------------------------------------
# Workflow wiring: run the publish.yml step's real `run:` text.
# ---------------------------------------------------------------------------


def _step() -> dict[str, Any]:
    wf = yaml.safe_load(PUBLISH_PATH.read_text(encoding="utf-8"))
    steps = [s for s in wf["jobs"][JOB]["steps"] if s.get("name") == STEP]
    assert len(steps) == 1
    return steps[0]


def test_step_uses_the_script_and_no_inline_parser() -> None:
    step = _step()
    run = step["run"]
    assert "working-directory" not in step and "continue-on-error" not in step
    assert 'sh .github/scripts/apk-cert-digest.sh digest "$CERTS"' in run
    assert 'sh .github/scripts/apk-cert-digest.sh annotate "$TRANSCRIPT" "$CERTS"' in run
    # The parser lives in the tested script only, not inline.
    assert not re.search(r"\|\s*sed\b", run)
    assert "certificate SHA-256 digest:" not in run
    assert 'echo "cert_sha256=${CERT_SHA256}" >> "$GITHUB_OUTPUT"' in run
    assert "|| true; fi; exit \"$rc\"" in run


def _run_step(tmp_path: Path, verify_out: str, certs_out: str, *,
              verify_rc: int = 0, with_apk: bool = True
              ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    ws = tmp_path / "ws"
    (ws / ".github/scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, ws / ".github/scripts/apk-cert-digest.sh")
    if with_apk:
        apk = ws / "app/app/build/outputs/apk/release"
        apk.mkdir(parents=True)
        (apk / "app-release.apk").write_bytes(b"PK")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "verify.txt").write_text(verify_out, encoding="utf-8")
    (fixtures / "certs.txt").write_text(certs_out, encoding="utf-8")
    tools = tmp_path / "sdk/build-tools/99.0.0"
    tools.mkdir(parents=True)
    (tmp_path / "sdk/build-tools/34.0.0").mkdir()
    fake = tools / "apksigner"
    # Like the real tool, a refusal goes to stderr.
    verify_redirect = " >&2" if verify_rc else ""
    fake.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        case " $* " in
          *" --print-certs "*) cat '{fixtures / "certs.txt"}' ;;
          *) cat '{fixtures / "verify.txt"}'{verify_redirect}; exit {verify_rc} ;;
        esac
        """), encoding="utf-8")
    fake.chmod(0o755)
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    output = tmp_path / "github_output"
    summary = tmp_path / "summary.md"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GITHUB_", "ANDROID_"))}
    env.update(RUNNER_TEMP=str(runner_temp), ANDROID_SDK_ROOT=str(tmp_path / "sdk"),
               GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))
    script = tmp_path / "step.sh"
    script.write_text(_step()["run"], encoding="utf-8")
    # GitHub's default `run:` shell on Linux: `bash -e {0}`.
    p = subprocess.run([BASH, "-e", str(script)], cwd=ws, capture_output=True,
                       text=True, env=env, timeout=60, check=False)
    return p, output, summary


RC10_VERIFY = """\
Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Number of signers: 1
"""


def _notices(p: subprocess.CompletedProcess[str]) -> list[str]:
    return [_unescape(c[2]) for c in _commands(p.stdout) if c[0] == "notice"]


def test_step_publishes_the_rc10_digest(tmp_path: Path) -> None:
    p, output, summary = _run_step(tmp_path, RC10_VERIFY, RC10_PRINT_CERTS)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert output.read_text(encoding="utf-8") == f"cert_sha256={RC10_DIGEST}\n"
    assert RC10_DIGEST in summary.read_text(encoding="utf-8")
    assert _notices(p) == []  # success leaves no diagnostic annotations


def test_step_digest_failure_is_fail_closed_and_annotated(tmp_path: Path) -> None:
    p, output, _ = _run_step(tmp_path, RC10_VERIFY, TWO_CERTS)
    assert p.returncode == 1
    assert not output.exists() or "cert_sha256" not in output.read_text(encoding="utf-8")
    assert "::error::apk-cert-digest: expected exactly one signing certificate (SHA-256), found 2" in p.stderr
    assert "refusing to publish" in p.stderr
    notices = _notices(p)
    assert len(notices) == 2
    assert notices[0].splitlines()[0] == "apksigner-verify.txt:"
    assert notices[1].splitlines() == ["apksigner-certs.txt:", *TWO_CERTS.splitlines()]


def test_step_apksigner_refusal_is_annotated(tmp_path: Path) -> None:
    refused = "DOES NOT VERIFY\nERROR: APK Signature Scheme v2 signer #1: digest mismatch\n"
    p, output, _ = _run_step(tmp_path, refused, RC10_PRINT_CERTS, verify_rc=1)
    assert p.returncode == 1
    assert not output.exists()
    notices = _notices(p)
    assert notices[0].splitlines() == ["apksigner-verify.txt:", *refused.splitlines()]
    assert notices[1] == "apksigner-certs.txt: not produced or empty"


def test_step_missing_v2_is_annotated(tmp_path: Path) -> None:
    verify = RC10_VERIFY.replace("v2): true", "v2): false")
    p, output, _ = _run_step(tmp_path, verify, RC10_PRINT_CERTS)
    assert p.returncode == 1
    assert "did not confirm the v2 signing scheme" in p.stderr
    assert not output.exists()
    assert _notices(p)[0].splitlines() == ["apksigner-verify.txt:", *verify.splitlines()]


def test_step_missing_apk_is_annotated(tmp_path: Path) -> None:
    p, _, _ = _run_step(tmp_path, RC10_VERIFY, RC10_PRINT_CERTS, with_apk=False)
    assert p.returncode == 1
    assert "does not exist" in p.stderr
    assert _notices(p) == ["apksigner-verify.txt: not produced or empty",
                           "apksigner-certs.txt: not produced or empty"]
