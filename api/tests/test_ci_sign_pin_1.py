"""WP CI-SIGN-PIN-1: the release APK's signing certificate is pinned in CI.

A swapped or wrong release key still produces an APK that `apksigner`
calls valid, but Android refuses it as an update over the installed app.
publish.yml's "Verify APK signature (apksigner)" step therefore compares
the certificate SHA-256 with the pin in `app/signing-cert.sha256` (the one
committed place; public value, not a secret) via
`.github/scripts/apk-cert-digest.sh pin`. This module pins:

- the pin file itself: exactly one 64-hex value, equal to the release
  certificate measured on v0.1.0-rc10 and documented in app/README.md;
- the `pin` mode: match, mismatch, upper-case and CRLF tolerance, malformed
  pin files, and "pin not set" (fails with APK_PIN_STRICT=true, warns
  otherwise);
- the workflow wiring: the step reads the pin from that one file only,
  gets APK_PIN_STRICT from the same tag expression as the job's
  `environment:`, and runs the comparison before `cert_sha256` is written;
  the real `run:` text fails on a mismatch and on an unset pin in a tag run;
- debug builds: ci.yml (debug-key `assembleDebug`) never references the pin.

Local `sh`/`bash` subprocesses only; no Android SDK, Docker or network.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_ci_fix_3_apk_cert_digest import (
    BASH,
    JOB,
    OTHER_DIGEST,
    PIN_PATH,
    PUBLISH_PATH,
    RC10_DIGEST,
    RC10_PRINT_CERTS,
    RC10_VERIFY,
    REPO_ROOT,
    SCRIPT,
    SH,
    STEP,
    _notices,
    _run_step,
)

pytestmark = pytest.mark.skipif(SH is None or BASH is None, reason="sh/bash not available")

CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
README_PATH = REPO_ROOT / "app" / "README.md"
PIN_REL = "app/signing-cert.sha256"
UNSET_PIN = "# comment only, no value yet\n\n"


def _pin(pin_text: str, digest: str, tmp_path: Path, *, strict: bool | None = None
         ) -> subprocess.CompletedProcess[str]:
    f = tmp_path / "signing-cert.sha256"
    f.write_bytes(pin_text.encode("utf-8"))
    env = {"PATH": "/usr/bin:/bin"}
    if strict is not None:
        env["APK_PIN_STRICT"] = "true" if strict else "false"
    assert SH is not None
    return subprocess.run([SH, str(SCRIPT), "pin", str(f), digest], capture_output=True,
                          text=True, env=env, timeout=30, check=False)


# ---------------------------------------------------------------------------
# The pin file
# ---------------------------------------------------------------------------


def test_pin_file_holds_exactly_the_release_certificate() -> None:
    lines = PIN_PATH.read_text(encoding="utf-8").splitlines()
    values = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    assert values == [RC10_DIGEST]
    # The same value app/README.md tells users to compare a download with.
    assert RC10_DIGEST in README_PATH.read_text(encoding="utf-8")


def test_readme_documents_the_operator_steps() -> None:
    text = README_PATH.read_text(encoding="utf-8")
    assert "Pinned release certificate" in text
    assert PIN_REL in text


# ---------------------------------------------------------------------------
# `pin` mode
# ---------------------------------------------------------------------------


def test_pin_match(tmp_path: Path) -> None:
    p = _pin(PIN_PATH.read_text(encoding="utf-8"), RC10_DIGEST, tmp_path, strict=True)
    assert (p.returncode, p.stdout, p.stderr) == (0, "match\n", "")


def test_pin_match_tolerates_case_crlf_and_whitespace(tmp_path: Path) -> None:
    p = _pin(f"# c\r\n  {RC10_DIGEST.upper()}  \r\n", RC10_DIGEST, tmp_path, strict=True)
    assert (p.returncode, p.stdout) == (0, "match\n")
    p = _pin(f"{RC10_DIGEST}\n", RC10_DIGEST.upper(), tmp_path, strict=True)
    assert (p.returncode, p.stdout) == (0, "match\n")


@pytest.mark.parametrize("strict", [True, False, None])
def test_pin_mismatch_fails_on_every_run(tmp_path: Path, strict: bool | None) -> None:
    p = _pin(f"{RC10_DIGEST}\n", OTHER_DIGEST, tmp_path, strict=strict)
    assert p.returncode == 1
    assert p.stdout == ""
    assert p.stderr.startswith("::error::apk-cert-digest: signing certificate SHA-256 "
                               f"{OTHER_DIGEST} does not match the pinned release "
                               f"certificate {RC10_DIGEST}")


def test_pin_not_set_fails_on_tag_runs(tmp_path: Path) -> None:
    p = _pin(UNSET_PIN, RC10_DIGEST, tmp_path, strict=True)
    assert p.returncode == 1
    assert p.stdout == ""
    assert p.stderr.startswith("::error::apk-cert-digest: pin not set")


@pytest.mark.parametrize("strict", [False, None])
def test_pin_not_set_only_warns_elsewhere(tmp_path: Path, strict: bool | None) -> None:
    p = _pin(UNSET_PIN, RC10_DIGEST, tmp_path, strict=strict)
    assert p.returncode == 0
    assert p.stdout == "unset\n"
    assert p.stderr.startswith("::warning::apk-cert-digest: pin not set")


def test_pin_empty_file_counts_as_not_set(tmp_path: Path) -> None:
    assert _pin("", RC10_DIGEST, tmp_path, strict=True).returncode == 1
    assert _pin("", RC10_DIGEST, tmp_path, strict=False).stdout == "unset\n"


@pytest.mark.parametrize("pin_text", [
    "not-a-digest\n",
    RC10_DIGEST[:-1] + "\n",
    RC10_DIGEST + "0\n",
    f"{RC10_DIGEST}\n{OTHER_DIGEST}\n",
    f"{RC10_DIGEST}\n{RC10_DIGEST}\n",
    f"sha256:{RC10_DIGEST}\n",
])
@pytest.mark.parametrize("strict", [True, False])
def test_pin_malformed_file_fails_on_every_run(tmp_path: Path, pin_text: str, strict: bool) -> None:
    p = _pin(pin_text, RC10_DIGEST, tmp_path, strict=strict)
    assert p.returncode == 1
    assert "::error::apk-cert-digest: pin file" in p.stderr
    assert "malformed" in p.stderr


def test_pin_missing_file_fails(tmp_path: Path) -> None:
    assert SH is not None
    p = subprocess.run([SH, str(SCRIPT), "pin", str(tmp_path / "nope"), RC10_DIGEST],
                       capture_output=True, text=True, timeout=30, check=False)
    assert p.returncode == 1
    assert "missing or unreadable" in p.stderr


def test_pin_rejects_a_non_digest_argument(tmp_path: Path) -> None:
    p = _pin(f"{RC10_DIGEST}\n", "", tmp_path, strict=False)
    assert p.returncode == 1
    assert "is not a certificate SHA-256" in p.stderr


# ---------------------------------------------------------------------------
# Workflow wiring (lint)
# ---------------------------------------------------------------------------


def _wf(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _verify_step() -> dict:
    steps = [s for s in _wf(PUBLISH_PATH)["jobs"][JOB]["steps"] if s.get("name") == STEP]
    assert len(steps) == 1
    return steps[0]


def test_step_reads_the_pin_from_the_one_file() -> None:
    step = _verify_step()
    run = step["run"]
    call = f'sh .github/scripts/apk-cert-digest.sh pin {PIN_REL} "$CERT_SHA256"'
    assert run.count(call) == 1
    # The comparison gates the output: no cert_sha256 for an unpinned key.
    assert run.index(call) < run.index('echo "cert_sha256=${CERT_SHA256}" >> "$GITHUB_OUTPUT"')
    assert f'if ! PIN_STATE="$({call})"; then' in run
    # No second source for the pin: no repo variable, no inline digest.
    assert "vars." not in str(step)
    assert not re.search(r"\b[0-9a-f]{64}\b", run)


def test_strict_mode_follows_the_release_environment_expression() -> None:
    job = _wf(PUBLISH_PATH)["jobs"][JOB]
    strict_expr = "startsWith(github.ref, 'refs/tags/v')"
    assert strict_expr in job["environment"]
    assert _verify_step()["env"] == {"APK_PIN_STRICT": "${{ " + strict_expr + " }}"}


def test_pin_is_referenced_only_by_the_release_verify_step() -> None:
    # No other step in publish.yml reads the pin file (comments may name it).
    for job_name, job in _wf(PUBLISH_PATH)["jobs"].items():
        for step in job.get("steps", []):
            if job_name == JOB and step.get("name") == STEP:
                continue
            assert PIN_REL not in str(step.get("run", "")), (job_name, step.get("name"))
            assert PIN_REL not in str(step.get("with", "")), (job_name, step.get("name"))
    # Debug builds (ci.yml's assembleDebug, debug key) are never compared.
    ci = CI_PATH.read_text(encoding="utf-8")
    assert PIN_REL not in ci
    assert "apk-cert-digest.sh pin" not in ci


# ---------------------------------------------------------------------------
# The step's real `run:` text
# ---------------------------------------------------------------------------


def test_step_matching_pin_publishes(tmp_path: Path) -> None:
    p, output, summary = _run_step(tmp_path, RC10_VERIFY, RC10_PRINT_CERTS, strict=True)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert output.read_text(encoding="utf-8") == f"cert_sha256={RC10_DIGEST}\n"
    assert "matches the pinned release certificate" in summary.read_text(encoding="utf-8")


@pytest.mark.parametrize("strict", [True, False])
def test_step_foreign_key_is_refused(tmp_path: Path, strict: bool) -> None:
    certs = RC10_PRINT_CERTS.replace(RC10_DIGEST, OTHER_DIGEST)
    p, output, _ = _run_step(tmp_path, RC10_VERIFY, certs, strict=strict)
    assert p.returncode == 1
    assert not output.exists() or "cert_sha256" not in output.read_text(encoding="utf-8")
    assert "does not match the pinned release certificate" in p.stderr
    assert "refusing to publish" in p.stderr
    # The trap annotates the transcripts, so the foreign DN is visible token-free.
    assert [n.splitlines()[0] for n in _notices(p)] == ["apksigner-verify.txt:", "apksigner-certs.txt:"]


def test_step_unset_pin_fails_a_tag_run(tmp_path: Path) -> None:
    p, output, _ = _run_step(tmp_path, RC10_VERIFY, RC10_PRINT_CERTS, pin=UNSET_PIN, strict=True)
    assert p.returncode == 1
    assert not output.exists() or "cert_sha256" not in output.read_text(encoding="utf-8")
    assert "::error::apk-cert-digest: pin not set" in p.stderr


def test_step_unset_pin_warns_on_other_runs(tmp_path: Path) -> None:
    p, output, summary = _run_step(tmp_path, RC10_VERIFY, RC10_PRINT_CERTS, pin=UNSET_PIN,
                                   strict=False)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert output.read_text(encoding="utf-8") == f"cert_sha256={RC10_DIGEST}\n"
    assert "::warning::apk-cert-digest: pin not set" in p.stderr
    assert "pin not set" in summary.read_text(encoding="utf-8")
