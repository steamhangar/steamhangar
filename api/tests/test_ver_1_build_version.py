"""WP VER-1: the real release version and commit reach every built artifact.

Pinned here, one guarantee per test group:

1. Every Dockerfile (core, api, dns, deploy/proxy) takes the build args
   ``VAULT_VERSION`` (default ``dev``) and ``VAULT_COMMIT`` (default
   ``unknown``) in its final stage, bakes them as the runtime ENV
   ``VAULT_BUILD_VERSION``/``VAULT_BUILD_COMMIT`` and the OCI
   ``version``/``revision`` labels, and declares them after every
   RUN/COPY/ADD so a new release does not invalidate the expensive layers.
2. ``.github/workflows/publish.yml`` computes ONE version (tag without its
   ``v``, or ``dev-<short sha>`` off a tag) and commit in its
   ``build-version`` job, passes them as exactly those build args, checks
   the version against metadata-action's on a tag, and stamps the agent
   binaries with ``-X main.version``/``-X main.commit``. The job's shell
   runs here under bash for the tag, branch and invalid cases, and its
   grammar is compared with ``vault_api.build_info``'s on the same inputs.
3. vault-api reports the baked env as ``server_version`` and falls back to
   ``BASE_VERSION`` when it is absent, blank or outside the grammar.

ci.yml's mirror of the build args lives in ``test_ci_image_build.py``; the
agent's own ``--version`` tests live in ``agent/go/cmd/vault-agent``.
Pure file parsing plus one bash subprocess; nothing here needs Docker.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from vault_api import (
    BASE_VERSION,
    BUILD_COMMIT_ENV,
    BUILD_VERSION_ENV,
    BuildInfo,
    build_info,
)
from vault_api.config import Settings
from vault_api.main import create_app

from tests.conftest import TEST_API_KEY

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_PATH = REPO_ROOT / ".github" / "workflows" / "publish.yml"

#: Every Dockerfile publish.yml ships (test_ci_image_build.py pins ci.yml's
#: matrix to publish.yml's; this list is pinned to that matrix below).
DOCKERFILES = {
    "vault-core": REPO_ROOT / "core" / "Dockerfile",
    "vault-api": REPO_ROOT / "api" / "Dockerfile",
    "vault-dns": REPO_ROOT / "dns" / "Dockerfile",
    "vault-proxy": REPO_ROOT / "deploy" / "proxy" / "Dockerfile",
}

BUILD_ARGS = {"VAULT_VERSION": "dev", "VAULT_COMMIT": "unknown"}
AUTH = {"X-Api-Key": TEST_API_KEY}
SHA = "0123456789abcdef0123456789abcdef01234567"


# ==========================================================================
# 1. Dockerfiles
# ==========================================================================


def _instructions(path: Path) -> list[tuple[str, str]]:
    """``(KEYWORD, arguments)`` per Dockerfile instruction, continuation
    lines joined and comment lines dropped (Docker drops them too, even
    inside a continuation)."""
    result: list[tuple[str, str]] = []
    current: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#") or (not line and not current):
            continue
        continued = line.endswith("\\")
        current.append(line[:-1].strip() if continued else line)
        if not continued:
            text = " ".join(part for part in current if part)
            keyword, _, args = text.partition(" ")
            result.append((keyword.upper(), args.strip()))
            current = []
    return result


def _final_stage(path: Path) -> list[tuple[str, str]]:
    instructions = _instructions(path)
    last_from = max(i for i, (kw, _) in enumerate(instructions) if kw == "FROM")
    return instructions[last_from + 1 :]


def _key_values(args: str) -> dict[str, str]:
    """``KEY="value" KEY2=value`` (ENV/LABEL form) -> dict, quotes removed."""
    return {
        m.group(1): m.group(2) if m.group(2) is not None else m.group(3)
        for m in re.finditer(r'([A-Za-z0-9_.-]+)=(?:"([^"]*)"|(\S+))', args)
    }


@pytest.mark.parametrize("image", sorted(DOCKERFILES))
def test_dockerfile_declares_both_build_args_with_their_defaults_in_the_final_stage(
    image: str,
) -> None:
    args = [a for kw, a in _final_stage(DOCKERFILES[image]) if kw == "ARG"]
    declared = dict(a.partition("=")[::2] for a in args)
    for name, default in BUILD_ARGS.items():
        assert declared.get(name) == default, (
            f"{image}: the final stage must declare `ARG {name}={default}` "
            f"(found ARGs {args})"
        )


@pytest.mark.parametrize("image", sorted(DOCKERFILES))
def test_dockerfile_bakes_the_build_args_as_runtime_env(image: str) -> None:
    env: dict[str, str] = {}
    for kw, args in _final_stage(DOCKERFILES[image]):
        if kw == "ENV":
            env.update(_key_values(args))
    assert env.get(BUILD_VERSION_ENV) == "${VAULT_VERSION}", (image, env.get(BUILD_VERSION_ENV))
    assert env.get(BUILD_COMMIT_ENV) == "${VAULT_COMMIT}", (image, env.get(BUILD_COMMIT_ENV))


@pytest.mark.parametrize("image", sorted(DOCKERFILES))
def test_dockerfile_oci_labels_come_from_the_build_args(image: str) -> None:
    """No hand-maintained version literal is left in any Dockerfile."""
    labels: dict[str, str] = {}
    for kw, args in _instructions(DOCKERFILES[image]):
        if kw == "LABEL":
            labels.update(_key_values(args))
    assert labels.get("org.opencontainers.image.version") == "${VAULT_VERSION}", image
    assert labels.get("org.opencontainers.image.revision") == "${VAULT_COMMIT}", image


@pytest.mark.parametrize("image", sorted(DOCKERFILES))
def test_dockerfile_declares_the_build_args_after_every_layer_building_step(
    image: str,
) -> None:
    """Layer caching: a changed ARG value invalidates every later RUN, so
    no RUN/COPY/ADD may follow the ARG (and the ENV/LABEL that use it)."""
    stage = _final_stage(DOCKERFILES[image])
    arg_positions = [
        i
        for i, (kw, a) in enumerate(stage)
        if kw == "ARG" and a.partition("=")[0] in BUILD_ARGS
    ]
    assert arg_positions, f"{image}: no build-version ARG in the final stage"
    first_arg = min(arg_positions)
    later = [(kw, a[:60]) for kw, a in stage[first_arg:] if kw in {"RUN", "COPY", "ADD"}]
    assert not later, f"{image}: layer-building steps after the build-version ARG: {later}"


def test_dockerfile_list_is_publish_matrix() -> None:
    matrix = _job(_publish(), "publish")["strategy"]["matrix"]["include"]
    shipped = {row["image"]: REPO_ROOT / row["dockerfile"] for row in matrix}
    assert shipped == DOCKERFILES


# ==========================================================================
# 2. publish.yml
# ==========================================================================


def _publish() -> dict[str, Any]:
    data = yaml.safe_load(PUBLISH_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _job(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return workflow["jobs"][name]


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _step(job: dict[str, Any], *, name: str | None = None, uses: str | None = None) -> dict[str, Any]:
    found = [
        s
        for s in job["steps"]
        if (name is not None and s.get("name") == name)
        or (uses is not None and str(s.get("uses", "")).startswith(uses + "@"))
    ]
    assert len(found) == 1, f"expected one step {name or uses!r}, found {len(found)}"
    return found[0]


def _norm(expr: str) -> str:
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: "${{ " + " ".join(m.group(1).split()) + " }}", expr)


def parse_build_args(text: str) -> list[tuple[str, str]]:
    """build-push-action's newline-separated ``KEY=VALUE`` list."""
    pairs = []
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            key, sep, value = line.partition("=")
            assert sep, f"build-args line without '=': {line!r}"
            pairs.append((key, _norm(value)))
    return pairs


def test_build_version_job_exposes_version_and_commit_from_its_step() -> None:
    job = _job(_publish(), "build-version")
    assert job["outputs"] == {
        "version": "${{ steps.version.outputs.version }}",
        "commit": "${{ steps.version.outputs.commit }}",
    }
    step = _step(job, name="Compute build version and commit")
    assert step["id"] == "version"
    assert step["shell"] == "bash"
    assert job.get("permissions") == {}


def test_publish_passes_exactly_the_two_build_args_from_build_version() -> None:
    job = _job(_publish(), "publish")
    assert "build-version" in _needs(job)
    step = _step(job, uses="docker/build-push-action")
    assert parse_build_args(step["with"]["build-args"]) == [
        ("VAULT_VERSION", "${{ needs.build-version.outputs.version }}"),
        ("VAULT_COMMIT", "${{ needs.build-version.outputs.commit }}"),
    ]


def test_publish_checks_the_baked_version_against_metadata_action_on_tags() -> None:
    job = _job(_publish(), "publish")
    names = [s.get("name") for s in job["steps"]]
    check_name = "Check the baked version matches the image tag"
    step = _step(job, name=check_name)
    assert step["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert _norm(step["env"]["META_VERSION"]) == "${{ steps.meta.outputs.version }}"
    assert _norm(step["env"]["BUILD_VERSION"]) == "${{ needs.build-version.outputs.version }}"
    assert '[ "$META_VERSION" != "$BUILD_VERSION" ]' in step["run"]
    assert "exit 1" in step["run"]
    build = next(i for i, s in enumerate(job["steps"]) if str(s.get("uses", "")).startswith("docker/build-push-action@"))
    assert names.index(check_name) < build, "the check must run before anything is built or pushed"


def test_agent_binaries_are_stamped_with_the_build_version() -> None:
    job = _job(_publish(), "agent-binaries")
    assert "build-version" in _needs(job)
    step = _step(job, name="Cross-compile vault-agent (CGO_ENABLED=0, static binaries)")
    assert _norm(step["env"]["BUILD_VERSION"]) == "${{ needs.build-version.outputs.version }}"
    assert _norm(step["env"]["BUILD_COMMIT"]) == "${{ needs.build-version.outputs.commit }}"
    # The literal symbol names: agent/go/cmd/vault-agent/version.go's
    # `version`/`commit` in package main (TestLdflagsSetVersionAndCommit
    # pins the same names from the Go side).
    assert '-ldflags "-X main.version=${BUILD_VERSION} -X main.commit=${BUILD_COMMIT}"' in step["run"]
    builds = re.findall(r"^\s*go build\b", step["run"], re.MULTILINE)
    assert len(builds) == 1, "one go build line, inside build(), carries the flags"


def test_agent_binaries_run_the_stamped_binary() -> None:
    job = _job(_publish(), "agent-binaries")
    step = _step(job, name="Verify the stamped version (vault-agent --version)")
    assert '--version' in step["run"]
    assert 'want="vault-agent ${BUILD_VERSION} (commit ${BUILD_COMMIT})"' in step["run"]


# --- the build-version shell, executed ------------------------------------

_BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(_BASH is None, reason="bash not available")


def run_version_script(
    tmp_path: Path, script: str, *, ref: str, ref_name: str, sha: str = SHA
) -> tuple[int, dict[str, str], str]:
    out = tmp_path / f"github_output_{len(list(tmp_path.iterdir()))}"
    out.write_text("", encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "GITHUB_REF": ref,
        "GITHUB_REF_NAME": ref_name,
        "GITHUB_SHA": sha,
        "GITHUB_OUTPUT": str(out),
    }
    proc = subprocess.run(
        [_BASH, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    outputs = dict(
        line.partition("=")[::2] for line in out.read_text(encoding="utf-8").splitlines() if line
    )
    return proc.returncode, outputs, proc.stderr


def _publish_version_script() -> str:
    return _step(_job(_publish(), "build-version"), name="Compute build version and commit")["run"]


@needs_bash
def test_version_script_strips_the_v_on_a_tag(tmp_path: Path) -> None:
    rc, out, err = run_version_script(
        tmp_path, _publish_version_script(), ref="refs/tags/v0.1.0-rc8", ref_name="v0.1.0-rc8"
    )
    assert rc == 0, err
    assert out == {"version": "0.1.0-rc8", "commit": SHA}


@needs_bash
def test_version_script_says_dev_and_short_sha_off_a_tag(tmp_path: Path) -> None:
    rc, out, err = run_version_script(
        tmp_path, _publish_version_script(), ref="refs/heads/wp/ver-1", ref_name="wp/ver-1"
    )
    assert rc == 0, err
    assert out == {"version": "dev-0123456", "commit": SHA}


@needs_bash
@pytest.mark.parametrize("tag", ["v", "v0.1.0 rc8", 'v0.1"x', "v-1.0", "v" + "1" * 65])
def test_version_script_refuses_a_tag_outside_the_grammar(tmp_path: Path, tag: str) -> None:
    rc, out, err = run_version_script(
        tmp_path, _publish_version_script(), ref=f"refs/tags/{tag}", ref_name=tag
    )
    assert rc == 1
    assert out == {}, "nothing may reach GITHUB_OUTPUT on refusal"
    assert "::error::build version" in err


@needs_bash
def test_version_script_refuses_a_malformed_sha(tmp_path: Path) -> None:
    rc, out, _ = run_version_script(
        tmp_path, _publish_version_script(), ref="refs/heads/main", ref_name="main", sha="xyz"
    )
    assert rc == 1
    assert out == {}


@needs_bash
@pytest.mark.parametrize(
    "candidate",
    [
        "0.1.0",
        "0.1.0-rc8",
        "1.2.3+build.5",
        "dev",
        "dev-0123456",
        "A_b",
        "a" * 64,
        "a" * 65,
        "",
        "-1.0",
        ".1",
        "_1",
        "1.0 beta",
        "1.0\tbeta",
        '1.0"',
        "1/2",
        "1.0;x",
        "1.0$x",
        "1.0\\x",
        "١.0",  # Arabic-Indic digit one
        "1.0é",
    ],
)
def test_publish_grammar_and_vault_api_grammar_agree(tmp_path: Path, candidate: str) -> None:
    """A version publish.yml accepts is served by vault-api verbatim; one it
    refuses would also be refused at runtime. Same inputs through both."""
    rc, out, _ = run_version_script(
        tmp_path, _publish_version_script(), ref=f"refs/tags/v{candidate}", ref_name=f"v{candidate}"
    )
    info = build_info({BUILD_VERSION_ENV: candidate})
    publish_accepts = rc == 0
    api_accepts = info.rejected == ()
    assert publish_accepts == api_accepts, (candidate, rc, info)
    if publish_accepts:
        assert out["version"] == info.version == candidate


# ==========================================================================
# 3. vault-api: build_info, server_version, startup log
# ==========================================================================


def test_build_info_falls_back_to_base_version_without_the_env() -> None:
    assert build_info({}) == BuildInfo(version=BASE_VERSION, commit="unknown", rejected=())
    assert BASE_VERSION == "0.1.0"


def test_build_info_reads_the_baked_env() -> None:
    info = build_info({BUILD_VERSION_ENV: "0.1.0-rc8", BUILD_COMMIT_ENV: SHA})
    assert info == BuildInfo(version="0.1.0-rc8", commit=SHA, rejected=())


def test_build_info_accepts_the_dockerfile_defaults_of_a_local_build() -> None:
    info = build_info({BUILD_VERSION_ENV: "dev", BUILD_COMMIT_ENV: "unknown"})
    assert info == BuildInfo(version="dev", commit="unknown", rejected=())


def test_build_info_accepts_a_short_commit() -> None:
    assert build_info({BUILD_COMMIT_ENV: "0123456"}).commit == "0123456"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "0.1.0\n", "0.1.0 rc8", "<script>", "١.0", "v" * 65, "-dev"],
)
def test_build_info_rejects_an_unusable_version_and_falls_back(value: str) -> None:
    info = build_info({BUILD_VERSION_ENV: value})
    assert info.version == BASE_VERSION
    assert info.rejected == (BUILD_VERSION_ENV,)


@pytest.mark.parametrize(
    "value", ["", "0123456789ABCDEF0123456789ABCDEF01234567", "012345", "a" * 41, "g123456", SHA + "\n"]
)
def test_build_info_rejects_an_unusable_commit_and_falls_back(value: str) -> None:
    info = build_info({BUILD_COMMIT_ENV: value})
    assert info.commit == "unknown"
    assert info.rejected == (BUILD_COMMIT_ENV,)


def _app_settings(tmp_path: Path) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )


def test_server_version_is_the_baked_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BUILD_VERSION_ENV, "0.1.0-rc8")
    monkeypatch.setenv(BUILD_COMMIT_ENV, SHA)
    client = TestClient(create_app(_app_settings(tmp_path)))
    body = client.get("/v1/settings", headers=AUTH).json()
    assert body["server_version"] == "0.1.0-rc8"
    # PATCH answers with the same builder: same version.
    patched = client.patch("/v1/settings", json={"vault_name": "x"}, headers=AUTH).json()
    assert patched["server_version"] == "0.1.0-rc8"


def test_server_version_falls_back_without_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(BUILD_VERSION_ENV, raising=False)
    client = TestClient(create_app(_app_settings(tmp_path)))
    assert client.get("/v1/settings", headers=AUTH).json()["server_version"] == "0.1.0"


def test_server_version_falls_back_on_an_invalid_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BUILD_VERSION_ENV, "0.1.0 <b>")
    client = TestClient(create_app(_app_settings(tmp_path)))
    assert client.get("/v1/settings", headers=AUTH).json()["server_version"] == "0.1.0"


def test_health_stays_free_of_the_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BUILD_VERSION_ENV, "0.1.0-rc8")
    client = TestClient(create_app(_app_settings(tmp_path)))
    assert client.get("/v1/health").text == '{"status":"ok"}'


def test_startup_logs_the_build_and_warns_about_a_rejected_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(BUILD_VERSION_ENV, "0.1.0-rc8")
    monkeypatch.setenv(BUILD_COMMIT_ENV, "not-a-sha")
    with caplog.at_level(logging.INFO, logger="vault_api.main"):
        create_app(_app_settings(tmp_path))
    messages = [r.getMessage() for r in caplog.records if r.name == "vault_api.main"]
    assert "vault-api version 0.1.0-rc8 (commit unknown)" in messages
    warnings = [
        r.getMessage() for r in caplog.records if r.name == "vault_api.main" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1 and warnings[0].startswith(f"{BUILD_COMMIT_ENV} is set but")
    assert "not-a-sha" not in warnings[0], "the raw value is not echoed into the log"
