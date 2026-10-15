"""WP DIST-1a: release downloads served by vault-api, and their index.

Pins:

- ``/dist/<name>`` serves exactly the release names computed for the
  server's own version (plus the APK alias), public (no key), with the right
  media type, download name and caching; HEAD works;
- everything else under ``/dist/`` is 404: other versions' names, the
  placeholder README, symlinks, directories, encoded traversal shapes, and
  raw un-normalised paths;
- ``GET /v1/dist`` is behind the key and reports present files with size
  and SHA-256, absent ones with the GitHub Release URL of the same version,
  and no URL at all for a server version that is not a published release;
- the wiring: api/Dockerfile copies api/dist root-owned after the chown and
  sets VAULT_DIST_DIR; publish.yml builds the APK and agent files BEFORE the
  images and stages them for the vault-api leg; the release jobs still
  attach the same asset names.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import BuildInfo
from vault_api.config import Settings, _default_dist_dir
from vault_api.dist import artifact_specs, is_release_version, release_base_url
from vault_api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH = {"X-Api-Key": TEST_API_KEY}
VERSION = "0.1.0-rc18"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
TAG = f"v{VERSION}"

ALL_NAMES = [
    f"steamhangar-app-{TAG}.apk",
    f"vault-agent-{TAG}-windows-amd64.exe",
    f"vault-agent-{TAG}-linux-amd64",
    f"vault-agent-{TAG}-linux-arm64",
    "install-task.ps1",
    "run-vault-agent.ps1",
    "uninstall-task.ps1",
    "vault-agent-report.service",
    "vault-agent-report.timer",
    "SHA256SUMS",
]


def _content(name: str) -> bytes:
    return f"synthetic content of {name}\n".encode("ascii")


def _bake(dist: Path, names: list[str] | None = None) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "README.md").write_text("placeholder\n", encoding="utf-8")
    for name in ALL_NAMES if names is None else names:
        (dist / name).write_bytes(_content(name))


def _client(tmp_path: Path, monkeypatch, *, version: str = VERSION, commit: str = COMMIT) -> TestClient:
    monkeypatch.setenv("VAULT_BUILD_VERSION", version)
    monkeypatch.setenv("VAULT_BUILD_COMMIT", commit)
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        dist_dir=str(tmp_path / "dist"),
    )
    return TestClient(create_app(settings))


# --- the release-version rule and the name set -----------------------------


def test_artifact_names_are_the_release_asset_names_literally() -> None:
    # Literal list (docs/LEARNINGS.md: pin contracts with literals, never
    # derived from the code under test): publish.yml's asset names.
    assert [s.name for s in artifact_specs(VERSION)] == ALL_NAMES
    assert [s.id for s in artifact_specs(VERSION)] == [
        "app-android",
        "agent-windows-amd64",
        "agent-linux-amd64",
        "agent-linux-arm64",
        "install-task",
        "run-vault-agent",
        "uninstall-task",
        "systemd-service",
        "systemd-timer",
        "sha256sums",
    ]


@pytest.mark.parametrize(
    ("version", "commit", "release"),
    [
        ("0.1.0-rc18", COMMIT, True),
        ("0.1.0", COMMIT, True),
        ("1.2.3-beta.2", "abcdef0", True),
        ("0.1.0", "unknown", False),  # native run: base version, no commit
        ("dev", COMMIT, False),
        ("dev-1a2b3c4", COMMIT, False),
        ("ci-1a2b3c4", COMMIT, False),
        ("0.1", COMMIT, False),
    ],
)
def test_release_version_rule(version: str, commit: str, release: bool) -> None:
    build = BuildInfo(version=version, commit=commit)
    assert is_release_version(build) is release
    base = release_base_url(build)
    if release:
        assert base == f"https://github.com/steamhangar/steamhangar/releases/download/v{version}/"
    else:
        assert base is None


def test_native_default_dist_dir_is_api_dist() -> None:
    assert Path(_default_dist_dir()) == REPO_ROOT / "api" / "dist"


def test_blank_env_falls_back_to_the_default(monkeypatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("VAULT_DIST_DIR", "   ")
    assert Settings.from_env().dist_dir == _default_dist_dir()
    monkeypatch.setenv("VAULT_DIST_DIR", "/somewhere/dist")
    assert Settings.from_env().dist_dir == "/somewhere/dist"


# --- serving ------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_NAMES)
def test_every_baked_file_is_served_without_a_key(tmp_path: Path, monkeypatch, name: str) -> None:
    _bake(tmp_path / "dist")
    client = _client(tmp_path, monkeypatch)
    response = client.get(f"/dist/{name}")  # no X-Api-Key on purpose
    assert response.status_code == 200
    assert response.content == _content(name)
    assert f'filename="{name}"' in response.headers["content-disposition"]
    assert response.headers["content-disposition"].startswith("attachment;")
    # The global security headers ride along on downloads too.
    assert response.headers["x-content-type-options"] == "nosniff"


def test_media_types_and_caching(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist")
    client = _client(tmp_path, monkeypatch)
    apk = client.get(f"/dist/steamhangar-app-{TAG}.apk")
    assert apk.headers["content-type"] == "application/vnd.android.package-archive"
    assert apk.headers["cache-control"] == "public, max-age=31536000, immutable"
    exe = client.get(f"/dist/vault-agent-{TAG}-windows-amd64.exe")
    assert exe.headers["content-type"] == "application/octet-stream"
    assert exe.headers["cache-control"] == "public, max-age=31536000, immutable"
    sums = client.get("/dist/SHA256SUMS")
    assert sums.headers["content-type"] == "text/plain; charset=utf-8"
    assert sums.headers["cache-control"] == "no-cache"
    script = client.get("/dist/install-task.ps1")
    assert script.headers["cache-control"] == "no-cache"


def test_apk_alias_serves_the_versioned_apk_and_revalidates(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist")
    client = _client(tmp_path, monkeypatch)
    response = client.get("/dist/steamhangar-app.apk")
    assert response.status_code == 200
    assert response.content == _content(f"steamhangar-app-{TAG}.apk")
    assert f'filename="steamhangar-app-{TAG}.apk"' in response.headers["content-disposition"]
    # The alias changes content with every release: never "immutable".
    assert response.headers["cache-control"] == "no-cache"


def test_head_answers_without_a_body(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist")
    client = _client(tmp_path, monkeypatch)
    name = f"vault-agent-{TAG}-linux-arm64"
    response = client.head(f"/dist/{name}")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(_content(name)))


def test_single_range_request_gets_206(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist")
    client = _client(tmp_path, monkeypatch)
    name = f"vault-agent-{TAG}-linux-amd64"
    data = _content(name)
    response = client.get(f"/dist/{name}", headers={"Range": "bytes=2-9"})
    assert response.status_code == 206
    assert response.content == data[2:10]
    assert response.headers["content-range"] == f"bytes 2-9/{len(data)}"


def test_absent_files_and_alias_are_404(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist", names=["SHA256SUMS"])  # no APK baked
    client = _client(tmp_path, monkeypatch)
    assert client.get("/dist/SHA256SUMS").status_code == 200
    assert client.get(f"/dist/steamhangar-app-{TAG}.apk").status_code == 404
    assert client.get("/dist/steamhangar-app.apk").status_code == 404


def test_missing_dist_dir_is_not_an_error(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)  # tmp_path/dist never created
    assert client.get("/dist/SHA256SUMS").status_code == 404
    body = client.get("/v1/dist", headers=AUTH).json()
    assert all(a["local"] is False for a in body["artifacts"])


@pytest.mark.parametrize(
    "name",
    [
        "README.md",  # the placeholder is in the directory but never served
        "vault-agent-v0.1.0-rc17-linux-amd64",  # another version's name
        "steamhangar-app-v0.1.0-rc17.apk",
        "vault-agent-dev-linux-amd64",
        "sha256sums",  # case matters
        "SHA256SUMS ",
        "stray.bin",
    ],
)
def test_names_outside_the_computed_set_are_404_even_when_present(
    tmp_path: Path, monkeypatch, name: str
) -> None:
    dist = tmp_path / "dist"
    _bake(dist)
    for extra in ("vault-agent-v0.1.0-rc17-linux-amd64", "steamhangar-app-v0.1.0-rc17.apk", "stray.bin"):
        (dist / extra).write_bytes(b"must not be served\n")
    client = _client(tmp_path, monkeypatch)
    response = client.get(f"/dist/{name}")
    assert response.status_code == 404
    assert b"must not be served" not in response.content
    assert b"placeholder" not in response.content


def test_symlink_and_directory_with_a_served_name_are_404(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    _bake(dist, names=[])
    secret = tmp_path / "secret.txt"
    secret.write_text("VAULT_API_KEY=leak\n", encoding="utf-8")
    try:
        os.symlink(secret, dist / "SHA256SUMS")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    (dist / "install-task.ps1").mkdir()
    client = _client(tmp_path, monkeypatch)
    assert client.get("/dist/SHA256SUMS").status_code == 404
    assert client.get("/dist/install-task.ps1").status_code == 404
    body = client.get("/v1/dist", headers=AUTH).json()
    by_id = {a["id"]: a for a in body["artifacts"]}
    assert by_id["sha256sums"]["local"] is False
    assert by_id["install-task"]["local"] is False


@pytest.mark.parametrize(
    "path",
    [
        "/dist/%2e%2e/vault_api/config.py",
        "/dist/..%2fvault_api%2fconfig.py",
        "/dist/%2e%2e%2f%2e%2e%2fapi%2fvault_api%2fconfig.py",
        "/dist/..%5cSHA256SUMS",
        "/dist/",
        "/dist",
    ],
)
def test_encoded_traversal_shapes_are_404(tmp_path: Path, monkeypatch, path: str) -> None:
    _bake(tmp_path / "dist")
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    response = client.get(path)
    assert response.status_code == 404, path
    assert b"outside" not in response.content


@pytest.mark.parametrize(
    "raw",
    [b"/dist/../outside.txt", b"/dist/../../outside.txt", b"/dist/./SHA256SUMS/../../outside.txt"],
)
def test_raw_unnormalised_paths_are_404(tmp_path: Path, monkeypatch, raw: bytes) -> None:
    # httpx normalises ../ client-side (docs/LEARNINGS.md WP 4a.1), so drive
    # the app with a raw ASGI scope carrying the un-normalised path.
    _bake(tmp_path / "dist")
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    app = client.app
    path = raw.decode("ascii")
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": raw,
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    assert start["status"] == 404
    assert b"outside" not in body


# --- the index ------------------------------------------------------------------


def test_index_requires_the_key(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist")
    client = _client(tmp_path, monkeypatch)
    assert client.get("/v1/dist").status_code == 401
    assert client.get("/v1/dist", headers={"X-Api-Key": "wrong"}).status_code == 401
    assert client.get("/v1/dist", headers=AUTH).status_code == 200


def test_index_reports_baked_files_with_size_and_sha256(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist")
    client = _client(tmp_path, monkeypatch)
    body = client.get("/v1/dist", headers=AUTH).json()
    assert body["version"] == VERSION
    assert body["release"] is True
    assert body["local_base"] == "/dist/"
    assert body["release_base"] == f"https://github.com/steamhangar/steamhangar/releases/download/{TAG}/"
    assert body["apk_alias_url"] == "/dist/steamhangar-app.apk"
    assert [a["name"] for a in body["artifacts"]] == ALL_NAMES
    for art in body["artifacts"]:
        data = _content(art["name"])
        assert art["local"] is True
        assert art["source"] == "local"
        assert art["size"] == len(data)
        assert art["sha256"] == hashlib.sha256(data).hexdigest()
        assert art["local_url"] == f"/dist/{art['name']}"
        assert art["url"] == art["local_url"]
        assert art["release_url"] == (
            f"https://github.com/steamhangar/steamhangar/releases/download/{TAG}/{art['name']}"
        )
        # Every advertised local URL actually answers.
        assert client.get(art["local_url"]).content == data


def test_index_falls_back_to_the_release_when_nothing_is_baked(tmp_path: Path, monkeypatch) -> None:
    _bake(tmp_path / "dist", names=[])  # a dev-style image: README only
    client = _client(tmp_path, monkeypatch)
    body = client.get("/v1/dist", headers=AUTH).json()
    assert body["release"] is True
    assert body["apk_alias_url"] is None
    for art in body["artifacts"]:
        assert art["local"] is False
        assert art["size"] is None and art["sha256"] is None and art["local_url"] is None
        assert art["source"] == "release"
        assert art["url"] == art["release_url"]
        assert art["url"] == (
            f"https://github.com/steamhangar/steamhangar/releases/download/{TAG}/{art['name']}"
        )


def test_index_mixes_local_and_release_per_file(tmp_path: Path, monkeypatch) -> None:
    # A release built without the signing keystore: agent files baked, no APK.
    _bake(tmp_path / "dist", names=ALL_NAMES[1:])
    client = _client(tmp_path, monkeypatch)
    body = client.get("/v1/dist", headers=AUTH).json()
    by_id = {a["id"]: a for a in body["artifacts"]}
    assert by_id["app-android"]["source"] == "release"
    assert by_id["agent-windows-amd64"]["source"] == "local"
    assert body["apk_alias_url"] is None


@pytest.mark.parametrize(("version", "commit"), [("dev", "unknown"), ("dev-1a2b3c4", COMMIT), ("0.1.0", "unknown")])
def test_index_has_no_urls_for_a_non_release_server(
    tmp_path: Path, monkeypatch, version: str, commit: str
) -> None:
    _bake(tmp_path / "dist")  # files of v0.1.0-rc18 lying around
    client = _client(tmp_path, monkeypatch, version=version, commit=commit)
    body = client.get("/v1/dist", headers=AUTH).json()
    assert body["version"] == version
    assert body["release"] is False
    assert body["release_base"] is None
    for art in body["artifacts"]:
        # A non-release server serves nothing locally: the version-free
        # names (scripts, SHA256SUMS) could belong to any release.
        assert art["local"] is False
        assert art["url"] is None and art["release_url"] is None
        assert art["source"] == "none"
    assert client.get(f"/dist/vault-agent-{TAG}-linux-amd64").status_code == 404
    assert client.get("/dist/SHA256SUMS").status_code == 404
    assert client.get("/dist/install-task.ps1").status_code == 404


def test_sha256_is_cached_but_follows_a_changed_file(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    _bake(dist)
    client = _client(tmp_path, monkeypatch)
    first = {a["id"]: a["sha256"] for a in client.get("/v1/dist", headers=AUTH).json()["artifacts"]}
    target = dist / "SHA256SUMS"
    target.write_bytes(b"changed and longer than before\n")
    os.utime(target, ns=(1, 1))  # mtimes are tick-coarse (LEARNINGS API-FIX-2)
    second = {a["id"]: a["sha256"] for a in client.get("/v1/dist", headers=AUTH).json()["artifacts"]}
    assert second["sha256sums"] == hashlib.sha256(b"changed and longer than before\n").hexdigest()
    assert second["sha256sums"] != first["sha256sums"]
    assert second["install-task"] == first["install-task"]


# --- wiring: Dockerfile, .gitignore, publish.yml ----------------------------


def _dockerfile() -> str:
    return (REPO_ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_copies_api_dist_after_the_chown_and_sets_the_env() -> None:
    text = _dockerfile()
    lines = [ln.strip() for ln in text.splitlines()]
    copy = lines.index("COPY api/dist /app/dist")
    chown = next(i for i, ln in enumerate(lines) if ln.startswith("chown -R 101:101"))
    assert chown < copy, "COPY after the chown keeps the files root-owned"
    assert "--chown" not in lines[copy]
    assert re.search(r"^\s*VAULT_DIST_DIR=/app/dist \\$", text, re.MULTILINE)


def test_placeholder_keeps_the_copy_source_present_in_every_checkout() -> None:
    assert (REPO_ROOT / "api" / "dist" / "README.md").is_file()
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "api/dist/*" in gitignore
    assert "!api/dist/README.md" in gitignore
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert not any(ln.strip().rstrip("/") in ("api/dist", "api/dist/*", "api") for ln in dockerignore)


def _publish() -> dict[str, Any]:
    data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8"))
    return data["jobs"]


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in job["steps"] if s.get("name") == name)


def test_publish_builds_apk_and_agent_before_any_image() -> None:
    jobs = _publish()
    assert {"android-build", "agent-build"} <= set(_needs(jobs["publish"]))
    assert "publish" not in _needs(jobs["android-build"])
    assert "publish" not in _needs(jobs["agent-build"])
    # The builders upload exactly the artifacts publish downloads.
    up_apk = _step(jobs["android-build"], "Upload signed APK (workflow artifact)")
    up_agent = _step(jobs["agent-build"], "Upload vault-agent release files (workflow artifact)")
    assert up_apk["with"]["name"] == "release-apk"
    assert up_agent["with"]["name"] == "release-agent"
    assert up_apk["with"]["if-no-files-found"] == "error"
    assert up_agent["with"]["if-no-files-found"] == "error"


def test_publish_stages_both_artifacts_into_api_dist_before_the_build() -> None:
    job = _publish()["publish"]
    names = [s.get("name") for s in job["steps"]]
    build = names.index("Build and push ${{ matrix.image }}")
    order = [
        "Check api/dist holds only its placeholder (vault-api only)",
        "Download vault-agent release files (vault-api only)",
        "Download signed APK (vault-api only)",
        "Stage release downloads into api/dist (vault-api only)",
    ]
    idx = [names.index(n) for n in order]
    assert idx == sorted(idx) and idx[-1] < build
    for n in order:
        assert _step(job, n)["if"].startswith("matrix.image == 'vault-api'")
    assert _step(job, order[1])["with"] == {"name": "release-agent", "path": "api/dist"}
    assert _step(job, order[2])["with"] == {"name": "release-apk", "path": "api/dist"}
    assert "needs.android-build.outputs.configured == 'true'" in _step(job, order[2])["if"]
    stage = _step(job, order[3])["run"]
    assert "sha256sum --check --strict SHA256SUMS" in stage
    assert 'vault-agent-${tag}-windows-amd64.exe' in stage


def _run_stage(tmp_path: Path, names: list[str], *, apk: str | None = None, **overrides: str):
    """Run publish.yml's staging shell for real in a baked temp dir.

    `apk` is the APK file name to checksum for APK_SHA256 (default: the
    tag's); the step itself derives the name from BUILD_VERSION.
    """
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None or shutil.which("sha256sum") is None:
        pytest.skip("bash/sha256sum not available")
    script = _step(_publish()["publish"], "Stage release downloads into api/dist (vault-api only)")["run"]
    dist = tmp_path / f"d{len(list(tmp_path.iterdir()))}"
    _bake(dist, names=[n for n in names if n != "SHA256SUMS"])
    sums = "".join(
        f"{hashlib.sha256(_content(n)).hexdigest()}  {n}\n"
        for n in names
        if n != "SHA256SUMS" and not n.endswith(".apk")
    )
    (dist / "SHA256SUMS").write_text(sums, encoding="utf-8")
    apk = apk or f"steamhangar-app-{TAG}.apk"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "BUILD_VERSION": VERSION,
        "APK_CONFIGURED": "true",
        "APK_SHA256": hashlib.sha256(_content(apk)).hexdigest(),
        "IS_TAG": "true",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    env.update(overrides)
    return subprocess.run([bash, "-e", "-c", script], cwd=dist, env=env, capture_output=True, text=True)


def test_stage_step_passes_with_the_full_set(tmp_path: Path) -> None:
    ok = _run_stage(tmp_path, ALL_NAMES)
    assert ok.returncode == 0, ok.stderr


def test_stage_step_refuses_a_missing_file_on_a_tag(tmp_path: Path) -> None:
    missing = _run_stage(tmp_path, [n for n in ALL_NAMES if n != "uninstall-task.ps1"])
    assert missing.returncode != 0
    assert "uninstall-task.ps1 missing" in missing.stderr


def test_stage_step_refuses_an_apk_that_is_not_the_verified_one(tmp_path: Path) -> None:
    bad = _run_stage(tmp_path, ALL_NAMES, APK_SHA256="0" * 64)
    assert bad.returncode != 0
    assert "differs from android-build's" in bad.stderr


@pytest.mark.parametrize("version", ["0.1.0+meta", "0.1", "dev-1a2b3c4", "0.1.0-rc18_x"])
def test_stage_step_refuses_a_non_release_version_on_a_tag(tmp_path: Path, version: str) -> None:
    # Same grammar as is_release_version: such an image would serve nothing.
    assert is_release_version(BuildInfo(version=version, commit=COMMIT)) is False
    bad = _run_stage(tmp_path, ALL_NAMES, BUILD_VERSION=version)
    assert bad.returncode != 0
    assert "is not a release version" in bad.stderr
    # It must stop THERE, not later on a missing-file check.
    assert "missing" not in bad.stderr


def test_in_image_check_runs_on_tags_for_vault_api_and_checks_the_sums() -> None:
    job = _publish()["publish"]
    step = _step(job, "Check the baked release downloads inside the pushed image (vault-api only)")
    assert step["if"] == "matrix.image == 'vault-api' && startsWith(github.ref, 'refs/tags/v')"
    assert "cd /app/dist && sha256sum --check --strict SHA256SUMS" in step["run"]
    names = [s.get("name") for s in job["steps"]]
    build = names.index("Build and push ${{ matrix.image }}")
    assert names.index(step["name"]) > build


def test_release_jobs_attach_the_same_asset_names_as_before() -> None:
    jobs = _publish()
    rel = jobs["android-release"]
    assert set(_needs(rel)) == {"build-version", "android-build", "publish"}
    attach = _step(rel, "Attach signed APK to the GitHub Release")
    assert attach["with"]["files"] == f"release-apk/{APK_FROM_BUILD_VERSION}"
    rename = _step(jobs["android-build"], "Rename APK to its release-asset name")["run"]
    assert 'steamhangar-app-v${BUILD_VERSION}.apk' in rename
    agent = jobs["agent-binaries"]
    assert {"publish", "android-release", "agent-build"} <= set(_needs(agent))
    files = _step(agent, "Attach vault-agent binaries to the GitHub Release")["with"]["files"]
    assert files.split() == ["dist/vault-agent-*", "dist/*.ps1", "dist/SHA256SUMS"]
    dl = _step(agent, "Download vault-agent release files")
    assert dl["with"] == {"name": "release-agent", "path": "dist"}
    # android-release is skipped when no APK was signed; agent-binaries
    # must still run then.
    assert "needs.android-release.result == 'skipped'" in agent["if"]


def test_signing_secrets_and_write_token_are_in_different_jobs() -> None:
    jobs = _publish()
    assert jobs["android-build"]["permissions"] == {"contents": "read"}
    assert "environment" not in jobs["android-release"]
    assert jobs["android-release"]["permissions"] == {"contents": "write"}
    text = yaml.safe_dump(jobs["android-release"])
    assert "ANDROID_" not in text


# --- WP CI-FIX-DIST: no job output may carry the APK name ---------------------
# v0.1.0-rc18's publish run lost its release: GitHub dropped android-build's
# `apk_name` output ("Skip output 'apk_name' since it may contain secret")
# because the name contained a `release` Environment secret's value, and the
# staging step then ran `sha256sum ""`. Every consumer now derives the name
# from build-version; the secret-holding job only outputs a literal and hex
# digests.

APK_FROM_BUILD_VERSION = "steamhangar-app-v${{ needs.build-version.outputs.version }}.apk"


def test_no_job_output_or_reference_carries_the_apk_name() -> None:
    jobs = _publish()
    # yaml drops comments, so this sees only live keys and expressions.
    assert "apk_name" not in yaml.safe_dump(jobs)
    for job in jobs.values():
        for value in (job.get("outputs") or {}).values():
            assert "steamhangar-app" not in value


def test_secret_holding_jobs_output_only_literals_and_digests() -> None:
    jobs = _publish()
    holders = {name for name, job in jobs.items() if "environment" in job}
    assert holders == {"android-build"}
    assert jobs["android-build"]["outputs"] == {
        "configured": "${{ steps.signing_check.outputs.configured }}",
        "cert_sha256": "${{ steps.verify.outputs.cert_sha256 }}",
        "apk_sha256": "${{ steps.apk.outputs.sha256 }}",
    }


def test_every_apk_consumer_derives_the_name_from_build_version() -> None:
    jobs = _publish()
    assert "build-version" in _needs(jobs["android-build"])
    record = _step(jobs["android-build"], "Record APK checksum")
    assert record["env"] == {"BUILD_VERSION": "${{ needs.build-version.outputs.version }}"}
    assert 'name="steamhangar-app-v${BUILD_VERSION}.apk"' in record["run"]
    in_image = _step(
        jobs["publish"], "Check the baked release downloads inside the pushed image (vault-api only)"
    )
    assert in_image["env"]["APK_NAME"] == APK_FROM_BUILD_VERSION
    stage = _step(jobs["publish"], "Stage release downloads into api/dist (vault-api only)")
    assert "APK_NAME" not in stage["env"]
    assert 'apk="steamhangar-app-v${BUILD_VERSION}.apk"' in stage["run"]
    check = _step(jobs["android-release"], "Check the APK is the one android-build verified")
    assert check["env"]["BUILD_VERSION"] == "${{ needs.build-version.outputs.version }}"
    assert "steamhangar-app-v${BUILD_VERSION}.apk" in check["run"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"APK_SHA256": ""}, "apk_sha256 output is '', not a SHA-256"),
        ({"APK_CONFIGURED": ""}, "configured output is '', not true/false"),
        ({"BUILD_VERSION": ""}, "version output is empty"),
    ],
)
def test_stage_step_names_an_empty_output_instead_of_hashing_nothing(
    tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    bad = _run_stage(tmp_path, ALL_NAMES, **overrides)
    assert bad.returncode != 0
    assert message in bad.stderr
    assert "sha256sum: ''" not in bad.stderr
    assert "differs from" not in bad.stderr


def test_stage_step_refuses_an_artifact_whose_name_is_not_the_derived_one(tmp_path: Path) -> None:
    other = "steamhangar-app-v0.1.0-rc17.apk"
    names = [other if n.endswith(".apk") else n for n in ALL_NAMES]
    bad = _run_stage(tmp_path, names, apk=other)
    assert bad.returncode != 0
    assert f"steamhangar-app-{TAG}.apk missing" in bad.stderr


def test_stage_step_accepts_a_dispatch_rehearsal_apk(tmp_path: Path) -> None:
    # Off a tag the version is dev-<sha7>; android-build names the APK by the
    # same rule, so the derived name still matches.
    apk = "steamhangar-app-vdev-1a2b3c4.apk"
    names = [apk if n.endswith(".apk") else n for n in ALL_NAMES]
    ok = _run_stage(tmp_path, names, apk=apk, BUILD_VERSION="dev-1a2b3c4", IS_TAG="false")
    assert ok.returncode == 0, ok.stderr


def test_release_body_step_refuses_a_signed_apk_without_its_certificate() -> None:
    step = _step(_publish()["agent-binaries"], "Append image digests and APK certificate to the release body")
    assert step["env"]["APK_CONFIGURED"] == "${{ needs.android-build.outputs.configured }}"
    assert "cert_sha256 output is" in step["run"]
