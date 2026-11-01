"""WP RELEASE-SPLIT-1a: per-component versions under one product version.

ADR-0031. `.github/components.json` records per component (server, agent,
app) the repo paths that define it and the product version in which it
last changed; `.github/scripts/component-versions.py` decides at release
time which components changed since the previous release tag. This module
pins:

- the plan logic against a temporary git repo fixture: changed/unchanged
  per component, excludes, globs, the previous tag's manifest as the
  source of an unchanged component's version, the bootstrap case, the
  `enforce` switch and the `reuse: none` override, `write` + `check`;
- the manifest schema: the committed manifest is valid, every Dockerfile
  `COPY` source and the agent/app build inputs belong to their component,
  and (when the release tags are present) the recorded versions match the
  git history up to v0.1.0-rc21.1;
- the workflow wiring: `component-plan` runs before every build job, the
  builds are gated by its outputs, the reuse step retags instead of
  building, the manifest check runs on tag runs only and fails hard only
  with `enforce: true` (dry run: warning with the `write` command); the
  real `run:` text of the "Decide build or reuse" and manifest-check steps
  is executed with bash.

Local `git`/`bash` subprocesses only; no Docker, no network.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "component-versions.py"
MANIFEST = REPO_ROOT / ".github" / "components.json"
PUBLISH = REPO_ROOT / ".github" / "workflows" / "publish.yml"
GIT = shutil.which("git")
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(GIT is None, reason="git not available")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("component_versions", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["component_versions"] = mod
    spec.loader.exec_module(mod)
    return mod


cv = _load()


# ---------------------------------------------------------------------------
# Fixture repo
# ---------------------------------------------------------------------------


def _fixture_manifest(*, enforce: bool = False, agent_reuse: str = "release-assets"
                      ) -> dict[str, Any]:
    return {
        "schema": 1,
        "tag_prefix": "v",
        "enforce": enforce,
        "components": {
            "server": {
                "version": "1.0.0",
                "reuse": "retag",
                "paths": ["core/", "api/Dockerfile", "web/"],
                "exclude": ["web/tests/", "**/README.md"],
                "artifacts": {"images": ["ghcr.io/x/vault-core:{version}"]},
            },
            "agent": {
                "version": "1.0.0",
                "reuse": agent_reuse,
                "paths": ["agent/go/"],
                "exclude": ["agent/go/**/*_test.go"],
                "artifacts": {"files": ["vault-agent-v{version}-linux-amd64", "SHA256SUMS"]},
            },
            "app": {
                "version": "1.0.0",
                "reuse": "release-assets",
                "paths": ["app/app/src/main/", "app/build.gradle.kts"],
                "artifacts": {"files": ["steamhangar-app-v{version}.apk"]},
            },
        },
    }


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        assert GIT is not None
        return subprocess.run([GIT, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                               *args], cwd=self.root, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def write(self, rel: str, text: str = "x\n") -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def manifest(self, data: dict[str, Any]) -> None:
        self.write(".github/components.json", json.dumps(data, indent=2) + "\n")

    def commit(self, msg: str = "c") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)

    def tag(self, name: str) -> None:
        self.git("tag", "-a", name, "-m", name)


@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path / "repo")
    for rel in ("core/nginx.conf", "api/Dockerfile", "web/index.html", "web/tests/a.test.js",
                "agent/go/main.go", "agent/go/main_test.go", "app/app/src/main/A.kt",
                "app/build.gradle.kts", "docs/readme.md"):
        r.write(rel)
    r.manifest(_fixture_manifest())
    r.commit("v1")
    r.tag("v1.0.0")
    return r


def _plan(r: Repo, version: str = "1.1.0", prev: str = "v1.0.0",
          manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    m = manifest if manifest is not None else cv.load_manifest(r.root / ".github/components.json")
    return cv.make_plan(r.root, m, ".github/components.json", version, prev)


def _run(r: Repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args, "--repo", str(r.root)],
                          capture_output=True, text=True, check=False, env=r.env)


# ---------------------------------------------------------------------------
# Plan logic
# ---------------------------------------------------------------------------


def test_nothing_changed_reuses_every_component(repo: Repo) -> None:
    repo.write("docs/readme.md", "other\n")
    repo.commit()
    plan = _plan(repo)
    for name in cv.COMPONENT_ORDER:
        c = plan["components"][name]
        assert (c["changed"], c["action"], c["version"]) == (False, "reuse", "1.0.0"), name
        assert c["changed_files"] == []


def test_agent_only_change_bumps_only_the_agent(repo: Repo) -> None:
    repo.write("agent/go/main.go", "changed\n")
    repo.commit()
    plan = _plan(repo, "1.0.1")
    comps = plan["components"]
    assert comps["agent"]["changed"] is True
    assert comps["agent"]["version"] == "1.0.1"
    assert comps["agent"]["changed_files"] == ["agent/go/main.go"]
    assert comps["agent"]["artifacts"]["files"][0] == "vault-agent-v1.0.1-linux-amd64"
    assert comps["server"]["version"] == comps["app"]["version"] == "1.0.0"
    assert comps["server"]["artifacts"]["images"] == ["ghcr.io/x/vault-core:1.0.0"]


@pytest.mark.parametrize("rel", ["web/tests/a.test.js", "agent/go/main_test.go",
                                 "core/README.md", "web/README.md"])
def test_excluded_paths_do_not_count_as_a_change(repo: Repo, rel: str) -> None:
    repo.write(rel, "touched\n")
    repo.commit()
    plan = _plan(repo)
    assert not any(c["changed"] for c in plan["components"].values())


@pytest.mark.parametrize("rel,component", [
    ("core/sub/deep.conf", "server"),
    ("api/Dockerfile", "server"),
    ("web/js/new.js", "server"),
    ("app/build.gradle.kts", "app"),
    ("app/app/src/main/res/x.xml", "app"),
])
def test_paths_and_exact_files_count(repo: Repo, rel: str, component: str) -> None:
    repo.write(rel, "new\n")
    repo.commit()
    plan = _plan(repo)
    assert [n for n, c in plan["components"].items() if c["changed"]] == [component]


def test_deleted_and_renamed_files_count(repo: Repo) -> None:
    repo.git("mv", "agent/go/main.go", "docs/moved.go")
    repo.commit()
    plan = _plan(repo)
    assert plan["components"]["agent"]["changed_files"] == ["agent/go/main.go"]


def test_unchanged_version_comes_from_the_previous_tags_manifest(repo: Repo) -> None:
    # Release 1.1.0 changed the agent; 1.2.0 changes only the server.
    repo.write("agent/go/main.go", "v11\n")
    m = _fixture_manifest()
    m["components"]["agent"]["version"] = "1.1.0"
    repo.manifest(m)
    repo.commit()
    repo.tag("v1.1.0")
    repo.write("core/nginx.conf", "v12\n")
    # A stale committed manifest must not decide the unchanged versions.
    m2 = _fixture_manifest()
    m2["components"]["agent"]["version"] = "0.9.0"
    repo.manifest(m2)
    repo.commit()
    plan = _plan(repo, "1.2.0", "v1.1.0")
    assert plan["previous_manifest"] is True
    assert plan["components"]["agent"]["version"] == "1.1.0"
    assert plan["components"]["server"]["version"] == "1.2.0"
    assert plan["components"]["app"]["version"] == "1.0.0"
    assert cv.manifest_mismatches(cv.load_manifest(repo.root / ".github/components.json"),
                                  plan) == ["server: manifest records 1.0.0, plan says 1.2.0",
                                            "agent: manifest records 0.9.0, plan says 1.1.0"]


def test_bootstrap_without_a_manifest_at_the_previous_tag(tmp_path: Path) -> None:
    r = Repo(tmp_path / "boot")
    r.write("agent/go/main.go")
    r.write("core/nginx.conf")
    r.commit()
    r.tag("v0.9.0")
    m = _fixture_manifest()
    m["components"]["agent"]["version"] = "0.5.0"
    r.manifest(m)
    r.write("core/nginx.conf", "new\n")
    r.commit()
    plan = _plan(r, "1.0.0", "v0.9.0")
    assert plan["previous_manifest"] is False
    assert plan["components"]["agent"]["version"] == "0.5.0"
    assert plan["components"]["server"]["version"] == "1.0.0"


def test_dry_run_builds_everything(repo: Repo) -> None:
    repo.commit()
    plan = _plan(repo)
    assert plan["mode"] == "dry-run"
    assert all(c["build"] for c in plan["components"].values())
    assert all(c["action"] == "reuse" for c in plan["components"].values())


def test_enforce_skips_unchanged_components_with_a_wired_reuse(repo: Repo) -> None:
    repo.write("app/app/src/main/A.kt", "new\n")
    repo.commit()
    plan = _plan(repo, manifest=_fixture_manifest(enforce=True))
    assert plan["mode"] == "enforce"
    builds = {n: c["build"] for n, c in plan["components"].items()}
    assert builds == {"server": False, "agent": False, "app": True}


def test_enforce_still_builds_a_component_whose_reuse_is_not_wired(repo: Repo) -> None:
    repo.commit()
    plan = _plan(repo, manifest=_fixture_manifest(enforce=True, agent_reuse="none"))
    assert plan["components"]["agent"]["build"] is True
    assert plan["components"]["agent"]["changed"] is False
    assert plan["components"]["server"]["build"] is False
    md = cv.summary_markdown(plan)
    assert "| agent | unchanged since 1.0.0 | `1.0.0` | yes (reuse not wired yet) |" in md
    assert "| server | unchanged since 1.0.0 | `1.0.0` | no (reused: retag) |" in md


def test_manifest_inside_a_component_is_refused(repo: Repo) -> None:
    m = _fixture_manifest()
    m["components"]["server"]["paths"].append(".github/")
    with pytest.raises(cv.ManifestError, match="must not belong to server"):
        _plan(repo, manifest=m)


@pytest.mark.parametrize("version", ["1.1", "v1.1.0", "latest", "1.1.0-rc 1", ""])
def test_bad_new_versions_are_refused(repo: Repo, version: str) -> None:
    with pytest.raises(cv.ManifestError):
        _plan(repo, version)


def test_dev_versions_plan_but_never_write(repo: Repo) -> None:
    plan = _plan(repo, "dev-abc1234")
    assert plan["version"] == "dev-abc1234"
    proc = _run(repo, "write", "--version", "dev-abc1234", "--previous-tag", "v1.0.0")
    assert proc.returncode == 2
    assert "needs a product version" in proc.stderr


def test_github_outputs_and_summary(repo: Repo) -> None:
    repo.write("core/nginx.conf", "new\n")
    repo.commit()
    plan = _plan(repo, "1.1.0-rc1")
    out = cv.github_outputs(plan).splitlines()
    assert out == [
        "mode=dry-run",
        "server_changed=true", "server_build=true", "server_version=1.1.0-rc1",
        "server_reuse=retag", "server_artifact_version=1.1.0-rc1",
        "agent_changed=false", "agent_build=true", "agent_version=1.0.0",
        "agent_reuse=release-assets",
        # RELEASE-SPLIT-1b: a rebuilt unchanged component (dry run) ships
        # artifacts stamped with the new version.
        "agent_artifact_version=1.1.0-rc1", "agent_minimum=",
        "app_changed=false", "app_build=true", "app_version=1.0.0",
        "app_reuse=release-assets", "app_artifact_version=1.1.0-rc1", "app_minimum=",
        "previous_check=first-tag",
    ]
    md = cv.summary_markdown(plan)
    assert "| server | changed | `1.1.0-rc1` | yes |" in md
    assert "| agent | unchanged since 1.0.0 | `1.0.0` | yes |" in md
    assert "Dry run" in md


def test_cli_plan_write_check_roundtrip(repo: Repo, tmp_path: Path) -> None:
    repo.write("agent/go/main.go", "new\n")
    repo.commit()
    args = ["--version", "1.0.1", "--previous-tag", "v1.0.0"]
    check = _run(repo, "check", *args)
    assert check.returncode == 1
    assert "agent: manifest records 1.0.0, plan says 1.0.1" in check.stdout
    gh_out = tmp_path / "out.txt"
    summary = tmp_path / "summary.md"
    js = tmp_path / "plan.json"
    plan = _run(repo, "plan", *args, "--github-output", str(gh_out), "--summary",
                str(summary), "--json-out", str(js))
    assert plan.returncode == 0, plan.stderr
    assert re.search(r"^agent\s+build\s+1\.0\.1\s+1\.0\.0\s+yes\s+1$", plan.stdout, re.M)
    assert "agent_version=1.0.1" in gh_out.read_text()
    assert json.loads(js.read_text())["components"]["agent"]["changed"] is True
    assert "### Component versions (dry-run)" in summary.read_text()
    write = _run(repo, "write", *args)
    assert write.returncode == 0, write.stderr
    data = json.loads((repo.root / ".github/components.json").read_text())
    assert {n: c["version"] for n, c in data["components"].items()} == {
        "server": "1.0.0", "agent": "1.0.1", "app": "1.0.0"}
    repo.commit("release commit")
    check2 = _run(repo, "check", *args)
    assert check2.returncode == 0, check2.stdout
    # The manifest change of the release commit is no component change.
    again = _plan(repo, "1.0.1")
    assert [n for n, c in again["components"].items() if c["changed"]] == ["agent"]


def test_cli_reports_a_missing_tag_as_an_error(repo: Repo) -> None:
    proc = _run(repo, "plan", "--version", "1.1.0", "--previous-tag", "v9.9.9")
    assert proc.returncode == 2
    assert proc.stderr.startswith("::error::component-versions: git diff")


def test_history_marks_changes_per_tag(repo: Repo) -> None:
    repo.write("agent/go/main.go", "2\n")
    repo.commit()
    repo.tag("v1.1.0")
    repo.write("docs/readme.md", "3\n")
    repo.commit()
    repo.tag("v1.1.1")
    m = cv.load_manifest(repo.root / ".github/components.json")
    rows = cv.history(repo.root, m, ["v1.0.0", "v1.1.0", "v1.1.1"])
    assert [r["changed"] for r in rows] == [
        {"server": True, "agent": True, "app": True},
        {"server": False, "agent": True, "app": False},
        {"server": False, "agent": False, "app": False},
    ]
    assert rows[-1]["versions"] == {"server": "1.0.0", "agent": "1.1.0", "app": "1.0.0"}


@pytest.mark.parametrize("path,pattern,hit", [
    ("web/a.js", "web/", True),
    ("webx/a.js", "web/", False),
    ("web", "web/", False),
    ("api/Dockerfile", "api/Dockerfile", True),
    ("api/Dockerfile.bak", "api/Dockerfile", False),
    ("agent/go/x/y_test.go", "agent/go/**/*_test.go", True),
    ("agent/go/y_test.go", "agent/go/**/*_test.go", True),
    ("agent/go/y_test.go.txt", "agent/go/**/*_test.go", False),
    ("core/README.md", "**/README.md", True),
    ("README.md", "**/README.md", True),
    ("core/README.mdx", "**/README.md", False),
    ("a/b.txt", "a/*.txt", True),
    ("a/c/b.txt", "a/*.txt", False),
])
def test_path_patterns(path: str, pattern: str, hit: bool) -> None:
    assert cv.path_matches(path, pattern) is hit


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


def _committed() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_committed_manifest_is_valid_and_enforcing() -> None:
    data = cv.validate_manifest(_committed())
    # Enforced from rc22 on (ENFORCE-RC22 Weg B, ADR-0031).
    assert data["enforce"] is True
    assert list(data["components"]) == list(cv.COMPONENT_ORDER)
    # Reuse: images are retagged, agent files and APK attached from the
    # recorded release (wired in RELEASE-SPLIT-1b).
    assert {n: c["reuse"] for n, c in data["components"].items()} == {
        "server": "retag", "agent": "release-assets", "app": "release-assets"}
    assert MANIFEST.read_text(encoding="utf-8") == cv.dump_manifest(data)


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.update(schema=2), "schema"),
    (lambda d: d.update(enforce="no"), "enforce"),
    (lambda d: d.update(tag_prefix="release-"), "tag_prefix"),
    (lambda d: d["components"].pop("app"), "components must be"),
    (lambda d: d["components"].update(runner={}), "components must be"),
    (lambda d: d["components"]["agent"].update(version="v0.1.0"), "version"),
    (lambda d: d["components"]["agent"].update(version="dev-abc1234"), "version"),
    (lambda d: d["components"]["agent"].update(reuse="copy"), "reuse"),
    (lambda d: d["components"]["agent"].update(paths=[]), "paths is empty"),
    (lambda d: d["components"]["agent"].update(paths=["../x"]), "repo-relative"),
    (lambda d: d["components"]["agent"].update(paths=["/abs"]), "repo-relative"),
    (lambda d: d["components"]["agent"].update(exclude="x"), "exclude"),
    (lambda d: d["components"]["agent"].update(extra=1), "unknown keys"),
    (lambda d: d["components"]["app"].update(artifacts={"files": ["fixed.apk"]}), "{version}"),
    (lambda d: d["components"]["server"]["artifacts"].update(
        images=["ghcr.io/x/vault-core:{version}", "ghcr.io/x/fixed:1"]), "lacks {version}"),
    (lambda d: d["components"]["server"].update(artifacts={"files": ["x-{version}"]}),
     "retag"),
    (lambda d: d["components"]["app"].update(artifacts={"apk": ["x-{version}"]}), "artifacts"),
])
def test_schema_violations_are_refused(mutate: Any, message: str) -> None:
    data = copy.deepcopy(_committed())
    mutate(data)
    with pytest.raises(cv.ManifestError, match=re.escape(message)):
        cv.validate_manifest(data)


def _component_of(rel: str) -> list[str]:
    data = _committed()
    return [n for n in cv.COMPONENT_ORDER if cv.component_files(data["components"][n], [rel])]


def _copy_sources(dockerfile: Path) -> list[str]:
    out = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^COPY\s+(?!--from)(?:--\S+\s+)*(\S+)\s+\S+\s*$", line)
        if m:
            out.append(m.group(1))
    return out


def _dockerignored_dirs(context: str) -> list[str]:
    """Directory entries of the root .dockerignore (vault-api's context)."""
    if context != ".":
        return []
    lines = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip().endswith("/") and "*" not in ln]


@pytest.mark.parametrize("dockerfile,context", [
    ("core/Dockerfile", "core"),
    ("dns/Dockerfile", "dns"),
    ("deploy/proxy/Dockerfile", "deploy/proxy"),
    ("api/Dockerfile", "."),
])
def test_every_dockerfile_copy_source_belongs_to_the_server(dockerfile: str, context: str) -> None:
    sources = _copy_sources(REPO_ROOT / dockerfile)
    assert sources, f"no COPY lines parsed in {dockerfile}"
    assert _component_of(dockerfile) == ["server"]
    for src in sources:
        rel = os.path.normpath(os.path.join(context, src))
        path = REPO_ROOT / rel
        files = ([str(p.relative_to(REPO_ROOT)) for p in path.rglob("*") if p.is_file()]
                 if path.is_dir() else [rel])
        files = [f for f in files if "__pycache__" not in f and not f.endswith(".pyc")
                 and not any(f.startswith(d) for d in _dockerignored_dirs(context))]
        for f in files:
            if f.endswith("README.md"):
                continue
            assert _component_of(f) == ["server"], f"{dockerfile} COPY {src}: {f}"


@pytest.mark.parametrize("rel,component", [
    ("api/requirements.txt", "server"),
    (".dockerignore", "server"),
    ("web/index.html", "server"),
    ("agent/go/go.mod", "agent"),
    ("agent/go/cmd/vault-agent/main.go", "agent"),
    ("agent/packaging/windows/install-task.ps1", "agent"),
    ("agent/packaging/systemd/vault-agent-report.timer", "agent"),
    ("app/build.gradle.kts", "app"),
    ("app/app/build.gradle.kts", "app"),
    ("app/gradle/libs.versions.toml", "app"),
    ("app/gradle/wrapper/gradle-wrapper.properties", "app"),
    ("app/app/src/main/AndroidManifest.xml", "app"),
])
def test_shared_build_inputs_belong_to_their_component(rel: str, component: str) -> None:
    assert (REPO_ROOT / rel).is_file(), rel
    assert _component_of(rel) == [component]


@pytest.mark.parametrize("rel", [
    "api/tests/test_release_split_1a.py", "web/tests/about-view.test.js",
    "agent/tests/README.md", "app/app/src/test/java/dev/steamvault/app/x/YTest.kt",
    "app/README.md", "app/signing-cert.sha256", "deploy/compose.yaml",
    ".github/workflows/publish.yml", ".github/components.json", "docs/adr/0031-component-versions.md",
])
def test_non_inputs_belong_to_no_component(rel: str) -> None:
    assert _component_of(rel) == []


def test_agent_artifacts_match_what_agent_build_releases() -> None:
    files = _committed()["components"]["agent"]["artifacts"]["files"]
    wf = PUBLISH.read_text(encoding="utf-8")
    for arch in ("windows-amd64.exe", "linux-amd64", "linux-arm64"):
        assert f"vault-agent-v{{version}}-{arch}" in files
    assert 'name="vault-agent-${VERSION}-${goos}-${goarch}${ext}"' in wf
    for script in sorted((REPO_ROOT / "agent/packaging/windows").glob("*.ps1")):
        assert script.name in files
    for unit in sorted((REPO_ROOT / "agent/packaging/systemd").iterdir()):
        assert unit.name in files
    assert "SHA256SUMS" in files
    apk = _committed()["components"]["app"]["artifacts"]["files"]
    assert apk == ["steamhangar-app-v{version}.apk"]
    assert 'steamhangar-app-v${BUILD_VERSION}.apk' in wf


def _real_tags() -> list[str]:
    assert GIT is not None
    proc = subprocess.run([GIT, "-C", str(REPO_ROOT), "tag", "-l", "v*", "--sort=creatordate"],
                          capture_output=True, text=True, check=False)
    return proc.stdout.split() if proc.returncode == 0 else []


def test_recorded_versions_match_the_git_history() -> None:
    tags = _real_tags()
    if "v0.1.0-rc21.1" not in tags or "v0.1.0-rc1" not in tags:
        pytest.skip("release tags not in this checkout (shallow CI clone)")
    tags = tags[: tags.index("v0.1.0-rc21.1") + 1]
    data = cv.validate_manifest(_committed())
    rows = cv.history(REPO_ROOT, data, tags)
    # ADR-0031 history table: agent unchanged since rc17, app unchanged in
    # rc11, rc18, rc18.1, rc20.1, rc20.2.
    agent_changed = [r["tag"] for r in rows if r["changed"]["agent"]]
    assert agent_changed[-1] == "v0.1.0-rc17"
    app_unchanged = [r["tag"] for r in rows[1:] if not r["changed"]["app"]]
    assert {"v0.1.0-rc11", "v0.1.0-rc18", "v0.1.0-rc18.1", "v0.1.0-rc20.1",
            "v0.1.0-rc20.2"} <= set(app_unchanged)
    final = rows[-1]["versions"]
    recorded = {n: data["components"][n]["version"] for n in cv.COMPONENT_ORDER}
    # rc22 packages may already have bumped server/app in the committed
    # manifest via `write`; until then the history state must match.
    if recorded["server"] == "0.1.0-rc21.1":
        assert recorded == final
    assert final["agent"] == "0.1.0-rc17"
    # The first release after an agent change records a newer version via
    # `write`; it must then be a version that no tag up to rc21.1 carries.
    if recorded["agent"] != final["agent"]:
        assert f"v{recorded['agent']}" not in tags, recorded["agent"]


# ---------------------------------------------------------------------------
# Workflow wiring
# ---------------------------------------------------------------------------


def _wf() -> dict[str, Any]:
    return yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))


def _needs(job: dict[str, Any]) -> list[str]:
    n = job.get("needs", [])
    return [n] if isinstance(n, str) else list(n)


def _step(job: dict[str, Any], name_prefix: str) -> tuple[int, dict[str, Any]]:
    for i, s in enumerate(job["steps"]):
        if str(s.get("name", "")).startswith(name_prefix):
            return i, s
    raise AssertionError(f"no step {name_prefix!r}")


def test_component_plan_job_runs_the_script_with_full_history() -> None:
    job = _wf()["jobs"]["component-plan"]
    assert _needs(job) == ["build-version"]
    assert job["permissions"] == {"contents": "read"}
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    _, prev = _step(job, "Find the previous release tag")
    assert "git describe --tags --abbrev=0 --match 'v[0-9]*' \"${GITHUB_SHA}^\"" in prev["run"]
    i_plan, plan = _step(job, "Plan component builds")
    assert plan["id"] == "plan"
    assert ".github/scripts/component-versions.py plan" in plan["run"]
    assert '--github-output "$GITHUB_OUTPUT"' in plan["run"]
    assert '--summary "$GITHUB_STEP_SUMMARY"' in plan["run"]
    assert plan["env"]["BUILD_VERSION"] == "${{ needs.build-version.outputs.version }}"
    i_check, check = _step(job, "Check the manifest records the planned versions")
    assert i_check > i_plan
    assert check["env"]["STRICT"] == "${{ startsWith(github.ref, 'refs/tags/v') }}"
    assert check["env"]["MODE"] == "${{ steps.plan.outputs.mode }}"
    assert "component-versions.py check" in check["run"]
    assert check["run"].lstrip().startswith("set -eu")
    # Every output the jobs below read is produced by the script.
    for key, value in job["outputs"].items():
        assert value == f"${{{{ steps.plan.outputs.{key} }}}}"
        assert re.search(rf"^{key}=", _sample_outputs(), re.M), key


_GOOD_PLAN_ENV = {
    "SERVER_BUILD": "true", "SERVER_VERSION": "0.1.0-rc22",
    "AGENT_BUILD": "false", "AGENT_VERSION": "0.1.0-rc17",
    "APP_BUILD": "true", "APP_VERSION": "dev-abc1234",
    # RELEASE-SPLIT-1b outputs (tested in test_release_split_1b.py).
    "AGENT_ARTIFACT_VERSION": "0.1.0-rc17", "APP_ARTIFACT_VERSION": "dev-abc1234",
    "AGENT_MINIMUM": "0.1.0-rc17", "APP_MINIMUM": "", "PREVIOUS_CHECK": "match",
}


def _run_output_check(overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    job = _wf()["jobs"]["component-plan"]
    i_plan, _ = _step(job, "Plan component builds")
    i_out, step = _step(job, "Check the plan outputs")
    assert i_out == i_plan + 1
    for key, value in step["env"].items():
        assert value == f"${{{{ steps.plan.outputs.{key.lower()} }}}}", key
    env = {"PATH": "/usr/bin:/bin", **_GOOD_PLAN_ENV, **overrides}
    return subprocess.run([BASH, "-c", step["run"]], env=env, capture_output=True, text=True,
                          check=False, timeout=30)


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_plan_output_check_accepts_good_outputs() -> None:
    proc = _run_output_check({})
    assert proc.returncode == 0, proc.stderr
    assert "plan outputs OK" in proc.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("key,value", [
    ("SERVER_BUILD", ""), ("AGENT_BUILD", "True"), ("APP_BUILD", "yes"),
    ("SERVER_VERSION", ""), ("AGENT_VERSION", "v0.1.0"), ("APP_VERSION", "0.1"),
    ("SERVER_VERSION", "0.1.0;rm"), ("AGENT_VERSION", "dev-xyz"),
])
def test_plan_output_check_fails_loudly_on_bad_outputs(key: str, value: str) -> None:
    proc = _run_output_check({key: value})
    assert proc.returncode != 0
    assert "::error::" in proc.stderr
    assert key.lower() in proc.stderr
    assert "plan outputs OK" not in proc.stdout


def _run_check(tmp_path: Path, mode: str, check_rc: int, strict: str = "true"
               ) -> subprocess.CompletedProcess[str]:
    """Run the real manifest-check `run:` text with a stub `python3`."""
    assert BASH is not None
    _, check = _step(_wf()["jobs"]["component-plan"],
                     "Check the manifest records the planned versions")
    stub_dir = tmp_path / f"bin-{mode}-{check_rc}-{strict}"
    stub_dir.mkdir()
    stub = stub_dir / "python3"
    stub.write_text(f"#!/bin/sh\necho stub-check \"$@\"\nexit {check_rc}\n")
    stub.chmod(0o755)
    env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "BUILD_VERSION": "0.1.0-rc22",
           "PREV_TAG": "v0.1.0-rc21.1", "STRICT": strict, "MODE": mode}
    return subprocess.run([BASH, "-c", check["run"]], env=env, capture_output=True, text=True,
                          check=False, timeout=30)


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("mode,check_rc,want_rc,want_warning", [
    ("dry-run", 0, 0, False),
    ("dry-run", 1, 0, True),   # stale manifest in dry run: warn, continue
    ("dry-run", 2, 2, False),  # script error is never downgraded
    ("enforce", 0, 0, False),
    ("enforce", 1, 1, False),  # stale manifest with enforce: hard failure
    ("enforce", 2, 2, False),
])
def test_manifest_check_is_hard_only_when_enforced(tmp_path: Path, mode: str, check_rc: int,
                                                   want_rc: int, want_warning: bool) -> None:
    proc = _run_check(tmp_path, mode, check_rc)
    assert proc.returncode == want_rc, proc.stdout + proc.stderr
    assert "stub-check" in proc.stdout
    write_cmd = ("python3 .github/scripts/component-versions.py write "
                 "--version 0.1.0-rc22 --previous-tag v0.1.0-rc21.1")
    if want_warning:
        assert "::warning::" in proc.stdout
        assert write_cmd in proc.stdout
    else:
        assert "::warning::" not in proc.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_manifest_check_skipped_off_tag_and_fails_closed_on_bad_mode(tmp_path: Path) -> None:
    proc = _run_check(tmp_path, "enforce", 1, strict="false")
    assert proc.returncode == 0
    assert "stub-check" not in proc.stdout
    assert "::notice::" in proc.stdout
    proc = _run_check(tmp_path, "", 0)
    assert proc.returncode != 0
    assert "::error::" in proc.stderr
    assert "stub-check" not in proc.stdout


def _sample_outputs() -> str:
    data = cv.validate_manifest(_committed())
    plan = {"mode": "dry-run", "components": {
        n: {"changed": False, "build": True, "version": "0.1.0", "reuse": data["components"][n]["reuse"]}
        for n in cv.COMPONENT_ORDER}}
    return cv.github_outputs(plan)


@pytest.mark.parametrize("job_name,output", [
    ("agent-build", "agent_build"),
    ("android-build", "app_build"),
])
def test_client_builds_are_gated_by_the_plan(job_name: str, output: str) -> None:
    job = _wf()["jobs"][job_name]
    assert "component-plan" in _needs(job)
    assert f"needs.component-plan.outputs.{output} == 'true'" in job["if"]


def test_publish_waits_for_the_plan_and_gates_each_leg() -> None:
    job = _wf()["jobs"]["publish"]
    assert "component-plan" in _needs(job)
    i_mode, mode = _step(job, "Decide build or reuse")
    assert mode["id"] == "mode"
    assert mode["env"]["SERVER_BUILD"] == "${{ needs.component-plan.outputs.server_build }}"
    i_build, build = _step(job, "Build and push")
    assert i_mode < i_build
    assert build["if"] == "steps.mode.outputs.build == 'true'"
    _, baked = _step(job, "Check the baked version matches the image tag")
    assert "steps.mode.outputs.build == 'true'" in baked["if"]
    i_reuse, reuse = _step(job, "Reuse the recorded image")
    assert reuse["if"] == "steps.mode.outputs.build == 'false'"
    assert "docker buildx imagetools create" in reuse["run"]
    assert "*:sha-*) continue" in reuse["run"]
    assert reuse["env"]["PUSH"] == "${{ startsWith(github.ref, 'refs/tags/v') }}"
    assert "steps.mode.outputs.recorded_version" in reuse["env"]["SRC"]
    i_verify, verify = _step(job, "Verify pushed manifest")
    assert i_reuse < i_verify
    assert "steps.build.outputs.digest || steps.reuse.outputs.digest" in verify["run"]
    _, pin = _step(job, "Export digest pin")
    assert "steps.build.outputs.digest || steps.reuse.outputs.digest" in pin["env"]["PIN"]
    # vault-api-only steps rely on vault-api always building in 1a.
    assert 'if [ "$IMAGE" = "vault-api" ]; then\n  build=true' in mode["run"]


def test_no_build_job_starts_before_the_plan() -> None:
    jobs = _wf()["jobs"]
    builders = {"publish", "agent-build", "android-build"}
    for name in builders:
        assert "component-plan" in _needs(jobs[name]), name
    # Release jobs depend on the builders, so transitively on the plan.
    for name in ("android-release", "agent-binaries"):
        assert set(_needs(jobs[name])) & builders, name


def _run_mode(tmp_path: Path, image: str, server_build: str, version: str = "0.1.0-rc21.1"
              ) -> tuple[subprocess.CompletedProcess[str], str]:
    assert BASH is not None
    _, mode = _step(_wf()["jobs"]["publish"], "Decide build or reuse")
    out = tmp_path / f"out-{image}-{server_build}"
    out.write_text("")
    env = {"PATH": "/usr/bin:/bin", "IMAGE": image, "SERVER_BUILD": server_build,
           "SERVER_VERSION": version, "GITHUB_OUTPUT": str(out)}
    proc = subprocess.run([BASH, "-c", mode["run"]], env=env, capture_output=True, text=True,
                          check=False, timeout=30)
    return proc, out.read_text()


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("image,server_build,want", [
    ("vault-core", "true", "true"),
    ("vault-core", "false", "false"),
    ("vault-dns", "false", "false"),
    ("vault-proxy", "false", "false"),
    ("vault-api", "false", "true"),
    ("vault-api", "true", "true"),
])
def test_mode_step_decides_per_leg(tmp_path: Path, image: str, server_build: str,
                                   want: str) -> None:
    proc, out = _run_mode(tmp_path, image, server_build)
    assert proc.returncode == 0, proc.stderr
    assert f"build={want}\n" in out
    assert "recorded_version=0.1.0-rc21.1\n" in out


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("server_build,version", [
    ("", "0.1.0"), ("yes", "0.1.0"), ("false", ""), ("false", "dev-abc1234"),
    ("false", "0.1.0;rm"),
])
def test_mode_step_fails_closed_on_bad_plan_outputs(tmp_path: Path, server_build: str,
                                                    version: str) -> None:
    proc, out = _run_mode(tmp_path, "vault-core", server_build, version)
    assert proc.returncode != 0
    assert "build=" not in out
    assert "::error::" in proc.stderr
