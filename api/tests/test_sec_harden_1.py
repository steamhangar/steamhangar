"""SEC-HARDEN-1: the security nits of the 2026-11-20 audit (S-3, S-4, S-6,
S-7, S-8, S-9; docs/handover/post-0.1.0-roadmap.md "Low findings").

Pins:

- S-3: api/Dockerfile chowns only the data paths (never /app or the
  SteamPrefill binary) and bakes ``VAULT_DOTENV=off``; config.py's ``.env``
  loader runs only without that marker; deploy/compose.yaml runs vault-api,
  vault-runner, vault-proxy and vault-dns with a read-only root filesystem
  and exactly the tmpfs paths each one writes, and the proxy's tmpfs owner
  equals the uid/gid deploy/proxy/Dockerfile asserts. The live half (the
  stack still starts and works) is deploy/tests/verify-stack.sh 4e and 7h.
- S-4: one startup WARNING for a VAULT_API_KEY below 24 characters, never
  for a longer one, and never carrying the key.
- S-6: both nginx configs carry the catch-all ``location / { return 404; }``
  exactly once. The live half is .github/scripts/verify-core-nginx.sh.
- S-7: the Android catalog pins security-crypto at the GA 1.1.0.
- S-8: both requirements files are fully pinned and hashed, the runtime set
  has no uvicorn[standard] extras, pytest is >= 9.0.3, PyYAML is a test
  dependency, and the image installs with --require-hashes.
- S-9: .gitignore ignores env variants and key files, keeps the
  .env.example templates, and ignores nothing that is tracked.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.conftest import TEST_API_KEY
from vault_api import config
from vault_api.config import Settings
from vault_api.main import create_app, log_api_key_strength

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DOCKERFILE = REPO_ROOT / "api" / "Dockerfile"
PROXY_DOCKERFILE = REPO_ROOT / "deploy" / "proxy" / "Dockerfile"
COMPOSE = REPO_ROOT / "deploy" / "compose.yaml"


def _run_steps(dockerfile: str) -> list[str]:
    """Every `;`-separated shell step of the RUN instructions, comments
    dropped and continuations joined (same parser idea as test_eg1)."""
    joined = re.sub(r"\\\n", " ", "\n".join(
        ln for ln in dockerfile.splitlines() if not ln.lstrip().startswith("#")
    ))
    steps: list[str] = []
    for line in joined.splitlines():
        if line.startswith("RUN "):
            steps.extend(p.strip() for p in line[4:].split(";") if p.strip())
    return steps


# --- S-3: image ownership, dotenv, read-only root filesystems ---------------


def test_s3_dockerfile_chowns_only_the_data_paths() -> None:
    steps = _run_steps(API_DOCKERFILE.read_text(encoding="utf-8"))
    chowns = [s for s in steps if s.startswith("chown")]
    assert chowns == [
        "chown -R 101:101 /data /opt/steamprefill/Config /opt/steamprefill/home "
        "/vault /var/lib/vault-settings"
    ], chowns
    targets = chowns[0].split()[3:]
    assert "/app" not in targets and "/opt/steamprefill" not in targets
    # The build-time guard that keeps it that way is present.
    assert any(s.startswith('test -z "$(find /app /opt/steamprefill') for s in steps)
    assert 'test "$(stat -c \'%u:%g %a\' /opt/steamprefill/SteamPrefill)" = "0:0 755"' in steps


def test_s3_steamprefill_app_log_has_a_writable_target_in_both_cwds() -> None:
    """SteamPrefill writes `app.log` into its current directory. With /app
    and /opt/steamprefill root-owned and the root filesystem read-only, both
    cwds it really runs in need a link onto the /tmp tmpfs: the image WORKDIR
    (the documented `compose exec` login) and the binary's directory
    (prefill.run_prefill's cwd)."""
    text = API_DOCKERFILE.read_text(encoding="utf-8")
    steps = _run_steps(text)
    workdir = re.findall(r"^WORKDIR\s+(\S+)\s*$", text, re.MULTILINE)
    assert workdir == ["/app"], workdir
    exe = re.search(r"^\s*VAULT_STEAMPREFILL_PATH=(\S+) \\$", text, re.MULTILINE)
    assert exe, "VAULT_STEAMPREFILL_PATH not baked"
    exe_dir = exe.group(1).rsplit("/", 1)[0]
    for cwd in (workdir[0], exe_dir):
        assert f"ln -s /tmp/steamprefill-app.log {cwd}/app.log" in steps, cwd
    # prefill.run_prefill still runs the binary in its own directory.
    prefill_src = (REPO_ROOT / "api" / "vault_api" / "prefill.py").read_text(encoding="utf-8")
    assert "workdir = os.path.dirname(os.path.abspath(executable))" in prefill_src
    assert "cwd=workdir," in prefill_src
    tmpfs = _compose_services()["vault-runner"]["tmpfs"]
    assert any(m == "/tmp" or m.startswith("/tmp:") for m in tmpfs)


def test_s3_image_bakes_the_dotenv_marker() -> None:
    text = API_DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^\s*VAULT_DOTENV=off \\$", text, re.MULTILINE)
    assert config.DOTENV_SWITCH_ENV == "VAULT_DOTENV"


@pytest.mark.parametrize(
    ("value", "enabled"),
    [
        (None, True),
        ("", True),
        ("on", True),
        ("0", True),
        ("offf", True),
        ("off", False),
        ("OFF", False),
        ("  Off\t", False),
    ],
)
def test_s3_dotenv_enabled_grammar(value: str | None, enabled: bool) -> None:
    environ = {} if value is None else {"VAULT_DOTENV": value}
    assert config.dotenv_enabled(environ) is enabled


def test_s3_loader_skips_dotenv_under_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    import dotenv

    calls: list[object] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("VAULT_DOTENV", "off")
    assert config._load_dotenv_unless_disabled() is False
    assert calls == []


def test_s3_loader_runs_dotenv_without_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    import dotenv

    calls: list[object] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: calls.append(a))
    monkeypatch.delenv("VAULT_DOTENV", raising=False)
    assert config._load_dotenv_unless_disabled() is True
    assert calls == [()]


def _compose_services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


#: The writable paths each hardened service gets; everything else is the
#: read-only image or a volume. Changing one is a reviewed decision.
EXPECTED_TMPFS = {
    "vault-api": ["/tmp:size=256m"],
    "vault-runner": ["/tmp:size=256m"],
    "vault-proxy": ["/tmp", "/run/tinyproxy:uid=100,gid=101,mode=0755"],
    "vault-dns": ["/tmp", "/run:mode=0755"],
}


@pytest.mark.parametrize("service", sorted(EXPECTED_TMPFS))
def test_s3_compose_read_only_root_and_tmpfs(service: str) -> None:
    svc = _compose_services()[service]
    assert svc.get("read_only") is True, f"{service} lost read_only: true"
    assert svc.get("tmpfs") == EXPECTED_TMPFS[service]


def test_s3_proxy_tmpfs_owner_matches_the_dockerfile_assertion() -> None:
    steps = _run_steps(PROXY_DOCKERFILE.read_text(encoding="utf-8"))
    asserted = [s for s in steps if s.startswith('test "$(id -u tinyproxy):$(id -g tinyproxy)"')]
    assert len(asserted) == 1, asserted
    uid_gid = re.search(r'= "(\d+):(\d+)"$', asserted[0])
    assert uid_gid, asserted[0]
    tmpfs = [t for t in _compose_services()["vault-proxy"]["tmpfs"] if t.startswith("/run/tinyproxy:")]
    assert tmpfs == [f"/run/tinyproxy:uid={uid_gid.group(1)},gid={uid_gid.group(2)},mode=0755"]


def test_s3_dns_entrypoint_creates_its_run_directory() -> None:
    """/run is an empty tmpfs on vault-dns, so the image's /run/vault-dns is
    hidden; the entrypoint must create it before rendering into it."""
    text = (REPO_ROOT / "dns" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    mkdir = text.index('mkdir -p "$(dirname "$RENDERED")"')
    render = text.index('envsubst < "$TEMPLATE" > "$RENDERED"')
    assert mkdir < render
    assert 'RENDERED="/run/vault-dns/dnsmasq.conf"' in text


# --- S-4: short API key warning --------------------------------------------


def _short_key_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "VAULT_API_KEY is shorter than" in r.getMessage()
    ]


@pytest.mark.parametrize(
    ("length", "warns"),
    [(1, True), (23, True), (24, False), (48, False)],
)
def test_s4_warns_below_24_characters(
    caplog: pytest.LogCaptureFixture, length: int, warns: bool
) -> None:
    # Letters the message itself never contains, so "not in" is meaningful.
    key = ("Zq" * 30)[:length]
    with caplog.at_level(logging.WARNING, logger="vault_api.main"):
        log_api_key_strength(key)
    found = _short_key_warnings(caplog)
    assert bool(found) is warns
    for record in found:
        assert key not in record.getMessage()
        assert "24" in record.getMessage()


def test_s4_threshold_is_24() -> None:
    assert config.MIN_RECOMMENDED_API_KEY_LENGTH == 24
    assert len(TEST_API_KEY) >= 24, "the shared test key must not trigger the warning"


def test_s4_create_app_logs_the_warning_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    short = "short-key-1"
    settings = Settings(
        vault_api_key=short,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    with caplog.at_level(logging.WARNING):
        create_app(settings)
    found = _short_key_warnings(caplog)
    assert len(found) == 1
    assert short not in caplog.text


def test_s4_create_app_is_quiet_for_a_long_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(
        vault_api_key="x" * 48,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    with caplog.at_level(logging.WARNING):
        create_app(settings)
    assert _short_key_warnings(caplog) == []


# --- S-6: nginx catch-all ----------------------------------------------------


def _server_80_block(text: str) -> str:
    start = text.index("        listen       80;")
    end = text.index("\n    }\n", start)
    return text[start:end]


@pytest.mark.parametrize(
    "path", ["core/docker/nginx.conf.template", "core/nginx/nginx.conf"]
)
def test_s6_catch_all_location_in_both_configs(path: str) -> None:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    block = _server_80_block(text)
    code = "\n".join(ln.split("#", 1)[0].rstrip() for ln in block.splitlines())
    assert len(re.findall(r"^\s*location / \{\n\s*return 404;\n\s*\}", code, re.MULTILINE)) == 1
    # Only one `location /` of any kind, and no regex location that could
    # outrank a prefix one for the named paths.
    assert len(re.findall(r"^\s*location\s+/\s*\{", code, re.MULTILINE)) == 1
    assert not re.findall(r"^\s*location\s+~", code, re.MULTILINE)
    # The locations it must not shadow are all still there.
    for loc in (
        "location /depot/ {",
        "location @miss {",
        "location ^~ /tmp/ {",
        "location ^~ /steamhangar/runner-mark/ {",
        "location = /health {",
        "location = /lancache-heartbeat {",
    ):
        assert loc in code, loc


# --- S-7: Android security-crypto -------------------------------------------


def test_s7_security_crypto_is_the_ga_release() -> None:
    text = (REPO_ROOT / "app" / "gradle" / "libs.versions.toml").read_text(encoding="utf-8")
    assert re.findall(r'^securityCrypto = "([^"]+)"$', text, re.MULTILINE) == ["1.1.0"]


# --- S-8: requirements -------------------------------------------------------


def _requirements(path: Path) -> dict[str, tuple[str, list[str]]]:
    """name -> (version, hashes) for every requirement line of one file."""
    out: dict[str, tuple[str, list[str]]] = {}
    joined = re.sub(r"\\\n", " ", path.read_text(encoding="utf-8"))
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        m = re.match(r"^([a-z0-9][a-z0-9._-]*)(\[[^\]]*\])?==(\S+)", line)
        assert m, f"{path.name}: not an exact pin: {line[:80]}"
        assert m.group(2) is None, f"{path.name}: extras are not allowed: {line[:80]}"
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", line)
        out[m.group(1)] = (m.group(3), hashes)
    return out


def test_s8_every_requirement_is_pinned_and_hashed() -> None:
    for name in ("requirements.txt", "requirements-dev.txt"):
        reqs = _requirements(REPO_ROOT / "api" / name)
        assert reqs, name
        unhashed = sorted(k for k, (_, hashes) in reqs.items() if not hashes)
        assert not unhashed, f"{name}: no --hash for {unhashed} (pip hash mode needs all)"


def test_s8_runtime_set_has_no_uvicorn_standard_extras() -> None:
    runtime = _requirements(REPO_ROOT / "api" / "requirements.txt")
    assert "uvicorn" in runtime
    # (`_requirements` already refuses any `[extra]` on a requirement line.)
    for extra in ("websockets", "watchfiles", "pyyaml", "uvloop", "httptools"):
        assert extra not in runtime, extra


def test_s8_dev_set_pins_a_fixed_pytest_and_pyyaml() -> None:
    runtime = _requirements(REPO_ROOT / "api" / "requirements.txt")
    dev = _requirements(REPO_ROOT / "api" / "requirements-dev.txt")
    assert "-r requirements.txt" in (REPO_ROOT / "api" / "requirements-dev.txt").read_text(encoding="utf-8")
    major, minor, patch = (int(p) for p in dev["pytest"][0].split(".")[:3])
    assert (major, minor, patch) >= (9, 0, 3), "pytest below 9.0.3 (CVE-2025-71176)"
    assert "pyyaml" in dev, "the workflow tests import yaml"
    assert not set(runtime) & set(dev), "a package is pinned in both files"


def test_s8_image_installs_with_require_hashes() -> None:
    steps = _run_steps(API_DOCKERFILE.read_text(encoding="utf-8"))
    assert "pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt" in steps


# --- S-9: .gitignore ---------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=False
    )


def _in_git_work_tree() -> bool:
    if shutil.which("git") is None:
        return False
    return _git("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


needs_git = pytest.mark.skipif(not _in_git_work_tree(), reason="needs git and a work tree")


@needs_git
@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("api/.env", True),
        ("deploy/.env.local", True),
        ("deploy/prod.env", True),
        ("x/server.pem", True),
        ("x/signing.key", True),
        ("api/.env.example", False),
        ("deploy/.env.example", False),
    ],
)
def test_s9_gitignore_patterns(path: str, ignored: bool) -> None:
    result = _git("check-ignore", "-q", "--no-index", path)
    assert (result.returncode == 0) is ignored, (path, result.returncode, result.stderr)


@needs_git
def test_s9_nothing_tracked_is_ignored() -> None:
    result = _git("ls-files", "-ci", "--exclude-standard")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


# --- S-3 follow-up (review): SteamPrefill's app.log on the tmpfs -----------


def _linked_bindir(tmp_path: Path) -> tuple[Path, Path]:
    """A bin dir whose app.log is a link onto a separate "tmpfs" dir, the
    image's layout (/opt/steamprefill/app.log -> /tmp/steamprefill-app.log)."""
    bindir = tmp_path / "bin"
    scratch = tmp_path / "tmpfs"
    bindir.mkdir()
    scratch.mkdir()
    target = scratch / "steamprefill-app.log"
    (bindir / "app.log").symlink_to(target)
    return bindir, target


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="POSIX links")
def test_app_log_rotation_keeps_this_run_plus_one_old(tmp_path: Path) -> None:
    from vault_api import prefill

    bindir, target = _linked_bindir(tmp_path)
    old = Path(str(target) + ".old")

    # Nothing there yet: a fresh empty log is created, nothing rotated.
    prefill.rotate_steamprefill_app_log(str(bindir))
    assert target.read_bytes() == b"" and not old.exists()
    assert (bindir / "app.log").is_symlink(), "the image's link must stay a link"

    target.write_text("run 1\n")
    prefill.rotate_steamprefill_app_log(str(bindir))
    assert old.read_text() == "run 1\n"
    assert target.read_bytes() == b""

    target.write_text("run 2\n")
    prefill.rotate_steamprefill_app_log(str(bindir))
    assert old.read_text() == "run 2\n", "the older .old is replaced, never kept"
    assert target.read_bytes() == b""
    assert sorted(p.name for p in target.parent.iterdir()) == [
        "steamprefill-app.log", "steamprefill-app.log.old",
    ]
    assert target.stat().st_uid == os.getuid()


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_app_log_rotation_leaves_a_non_file_alone(tmp_path: Path) -> None:
    from vault_api import prefill

    bindir, target = _linked_bindir(tmp_path)
    target.mkdir()
    prefill.rotate_steamprefill_app_log(str(bindir))  # must not raise
    assert target.is_dir() and not Path(str(target) + ".old").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_run_prefill_cuts_the_app_log_before_starting(tmp_path: Path) -> None:
    """The runner's code path: prefill.run_prefill (used by vault-runner and by
    subprocess mode) rotates app.log in the binary's directory first."""
    from tests import stub_prefill
    from vault_api import prefill

    bindir, target = _linked_bindir(tmp_path)
    target.write_text("an earlier run's log\n")
    executable = stub_prefill.make_stub(bindir)
    result = prefill.run_prefill(440, executable, timeout_seconds=60)
    assert result.success, result.output
    assert Path(str(target) + ".old").read_text() == "an earlier run's log\n"
    assert target.read_bytes() == b"", "the stub writes no app.log; the fresh one stays empty"


def test_prefill_runner_creates_the_app_log_at_start() -> None:
    """vault-runner's main() creates the log as uid 101 before any login."""
    src = (REPO_ROOT / "api" / "vault_api" / "prefill_runner.py").read_text(encoding="utf-8")
    main = src[src.index("def main("):]
    rotate = main.index("prefill.rotate_steamprefill_app_log(")
    assert rotate < main.index("runner.run_forever()")


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_app_log_rotation_follows_one_link_hop_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A chained link (app.log -> hop -> elsewhere) is left alone with a
    warning: the rename never acts on a file reached through a second hop."""
    from vault_api import prefill

    bindir, hop = _linked_bindir(tmp_path)
    elsewhere = tmp_path / "elsewhere.log"
    elsewhere.write_text("not SteamPrefill's\n")
    hop.symlink_to(elsewhere)
    with caplog.at_level(logging.WARNING, logger="vault_api.prefill"):
        prefill.rotate_steamprefill_app_log(str(bindir))
    assert elsewhere.read_text() == "not SteamPrefill's\n"
    assert hop.is_symlink()
    assert not Path(str(hop) + ".old").exists()
    assert not Path(str(elsewhere) + ".old").exists()
    assert "not a regular file" in caplog.text


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_app_log_rotation_never_raises_when_the_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from vault_api import prefill

    bindir, target = _linked_bindir(tmp_path)
    target.write_text("run 1\n")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(prefill.os, "replace", refuse)
    with caplog.at_level(logging.WARNING, logger="vault_api.prefill"):
        prefill.rotate_steamprefill_app_log(str(bindir))  # must not raise
    assert "Could not rotate" in caplog.text
    assert target.read_text() == "run 1\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_app_log_rotation_never_raises_when_the_create_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from vault_api import prefill

    bindir, target = _linked_bindir(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(prefill.os, "open", refuse)
    with caplog.at_level(logging.WARNING, logger="vault_api.prefill"):
        prefill.rotate_steamprefill_app_log(str(bindir))  # must not raise
    assert "Could not create" in caplog.text
    assert not target.exists()
