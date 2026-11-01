#!/usr/bin/env python3
"""Per-component release versions (WP RELEASE-SPLIT-1a, ADR-0031).

One product version exists (ADR-0029: `v0.X.Y(-rcN)` tags only). Each
component (server, agent, Android app) carries the product version in
which it last changed; `.github/components.json` records that version and
the repo paths that define the component. At release time this script
compares the tree of the previous release tag with the release commit and
decides per component:

- changed   -> the component takes the new product version and is rebuilt;
- unchanged -> it keeps the version recorded at the previous release tag
               and its existing artifacts are reused (not rebuilt).

Subcommands (stdlib only, git on PATH):

  plan      print the plan (human table on stderr-free stdout, JSON with
            --json-out, step outputs with --github-output, a Markdown
            table with --summary)
  write     write the manifest with the planned versions (the release
            commit runs this before the tag is set)
  check     exit 1 when the committed manifest's versions differ from the
            plan (a tag run whose release commit skipped `write`)
  validate  check the manifest schema only
  history   per consecutive tag pair, which components changed (the
            source of ADR-0031's history table)

Path patterns in the manifest: `dir/` matches everything below dir,
a pattern with `*` is a glob (`*` stays inside one path segment, `**`
crosses segments), anything else is an exact file path. `exclude`
patterns use the same syntax and win over `paths`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
COMPONENT_ORDER = ("server", "agent", "app")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$")
# Build versions on non-tag runs (`dev-<sha7>`, publish.yml build-version).
DEV_VERSION_RE = re.compile(r"^dev-[0-9a-f]{7,40}$")
ARTIFACT_KEYS = ("images", "files")
# How publish.yml reuses an unchanged component's artifacts. "none" means
# the reuse path is not wired yet: the component is rebuilt even when
# unchanged and `enforce` is on (agent and app until RELEASE-SPLIT-1b,
# because vault-api still bakes this run's agent files and APK).
REUSE_MODES = ("retag", "release-assets", "none")


class ManifestError(ValueError):
    """The manifest does not follow schema 1."""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def validate_manifest(data: Any) -> dict[str, Any]:
    """Return `data` if it is a schema-1 manifest, else raise ManifestError."""
    if not isinstance(data, dict):
        raise ManifestError("manifest is not a JSON object")
    if data.get("schema") != SCHEMA:
        raise ManifestError(f"schema must be {SCHEMA}, got {data.get('schema')!r}")
    if not isinstance(data.get("enforce"), bool):
        raise ManifestError("enforce must be true or false")
    if data.get("tag_prefix") != "v":
        raise ManifestError("tag_prefix must be 'v' (ADR-0029: v0.X.Y(-rcN) tags)")
    comps = data.get("components")
    if not isinstance(comps, dict) or set(comps) != set(COMPONENT_ORDER):
        raise ManifestError(f"components must be exactly {list(COMPONENT_ORDER)}")
    for name in COMPONENT_ORDER:
        comp = comps[name]
        where = f"components.{name}"
        if not isinstance(comp, dict):
            raise ManifestError(f"{where} is not an object")
        allowed = {"description", "version", "reuse", "paths", "exclude", "artifacts"}
        extra = set(comp) - allowed
        if extra:
            raise ManifestError(f"{where} has unknown keys {sorted(extra)}")
        version = comp.get("version")
        if not isinstance(version, str) or not VERSION_RE.match(version):
            raise ManifestError(f"{where}.version {version!r} is not a product version")
        if comp.get("reuse") not in REUSE_MODES:
            raise ManifestError(f"{where}.reuse must be one of {list(REUSE_MODES)}")
        if comp["reuse"] == "retag" and "images" not in comp.get("artifacts", {}):
            raise ManifestError(f"{where}.reuse 'retag' needs artifacts.images")
        for key in ("paths", "exclude"):
            value = comp.get(key, [] if key == "exclude" else None)
            if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
                raise ManifestError(f"{where}.{key} must be a list of non-empty strings")
            for p in value:
                if p.startswith("/") or ".." in p.split("/"):
                    raise ManifestError(f"{where}.{key} entry {p!r} is not a repo-relative path")
        if not comp["paths"]:
            raise ManifestError(f"{where}.paths is empty")
        arts = comp.get("artifacts")
        if not isinstance(arts, dict) or not arts or set(arts) - set(ARTIFACT_KEYS):
            raise ManifestError(f"{where}.artifacts must hold only {list(ARTIFACT_KEYS)}")
        for key, value in arts.items():
            if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
                raise ManifestError(f"{where}.artifacts.{key} must be a non-empty list of strings")
        if not any("{version}" in v for vals in arts.values() for v in vals):
            raise ManifestError(f"{where}.artifacts name no {{version}}-bound artifact")
        for v in arts.get("images", []):
            if "{version}" not in v:
                raise ManifestError(f"{where}.artifacts.images entry {v!r} lacks {{version}}")
    return data


def load_manifest(path: Path) -> dict[str, Any]:
    return validate_manifest(json.loads(path.read_text(encoding="utf-8")))


def dump_manifest(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_artifacts(comp: dict[str, Any], version: str) -> dict[str, list[str]]:
    return {k: [v.replace("{version}", version) for v in vals]
            for k, vals in comp["artifacts"].items()}


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if "*" in pattern or "?" in pattern:
        return _glob_to_regex(pattern).match(path) is not None
    return path == pattern


def component_files(comp: dict[str, Any], files: list[str]) -> list[str]:
    """The files of `files` that belong to the component."""
    hits = []
    for f in files:
        if any(path_matches(f, p) for p in comp["paths"]) and not any(
            path_matches(f, p) for p in comp.get("exclude", [])
        ):
            hits.append(f)
    return hits


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def changed_files(repo: Path, base: str, ref: str) -> list[str]:
    """Paths that differ between the trees of `base` and `ref` (both sides
    of a rename count)."""
    out = _git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base}^{{tree}}",
               f"{ref}^{{tree}}")
    return sorted(p for p in out.split("\0") if p)


def manifest_at(repo: Path, ref: str, rel: str) -> dict[str, Any] | None:
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{rel}"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    return validate_manifest(json.loads(proc.stdout))


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def make_plan(repo: Path, manifest: dict[str, Any], manifest_rel: str, version: str,
              previous_tag: str, ref: str = "HEAD") -> dict[str, Any]:
    if not (VERSION_RE.match(version) or DEV_VERSION_RE.match(version)):
        raise ManifestError(f"new version {version!r} is neither a product nor a dev version")
    for name in COMPONENT_ORDER:
        if component_files(manifest["components"][name], [manifest_rel]):
            raise ManifestError(f"the manifest {manifest_rel} must not belong to {name}")
    files = changed_files(repo, previous_tag, ref)
    previous = manifest_at(repo, previous_tag, manifest_rel)
    enforce = manifest["enforce"]
    components: dict[str, Any] = {}
    for name in COMPONENT_ORDER:
        comp = manifest["components"][name]
        hits = component_files(comp, files)
        changed = bool(hits)
        if previous is not None:
            prior_version = previous["components"][name]["version"]
        else:
            # Bootstrap: the previous tag has no manifest yet; the committed
            # one carries the versions recorded from git history.
            prior_version = comp["version"]
        planned = version if changed else prior_version
        components[name] = {
            "changed": changed,
            "action": "build" if changed else "reuse",
            # The switch: in dry run, and while a component's reuse path is
            # not wired, every component is still built.
            "build": changed or not enforce or comp["reuse"] == "none",
            "reuse": comp["reuse"],
            "recorded_version": comp["version"],
            "previous_version": prior_version,
            "version": planned,
            "artifacts": render_artifacts(comp, planned),
            "changed_files": hits,
        }
    return {
        "schema": SCHEMA,
        "version": version,
        "previous_tag": previous_tag,
        "ref": ref,
        "enforce": enforce,
        "mode": "enforce" if enforce else "dry-run",
        "previous_manifest": previous is not None,
        "components": components,
    }


def manifest_mismatches(manifest: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    out = []
    for name in COMPONENT_ORDER:
        have = manifest["components"][name]["version"]
        want = plan["components"][name]["version"]
        if have != want:
            out.append(f"{name}: manifest records {have}, plan says {want}")
    return out


def human_table(plan: dict[str, Any]) -> str:
    rows = [("component", "action", "version", "previous", "built", "changed files")]
    for name in COMPONENT_ORDER:
        c = plan["components"][name]
        rows.append((name, c["action"], c["version"], c["previous_version"],
                     "yes" if c["build"] else "no", str(len(c["changed_files"]))))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = [f"component versions: {plan['previous_tag']} -> {plan['version']} "
             f"(mode: {plan['mode']})"]
    for r in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip())
    return "\n".join(lines) + "\n"


def summary_markdown(plan: dict[str, Any]) -> str:
    lines = [f"### Component versions ({plan['mode']})", "",
             f"Previous release tag `{plan['previous_tag']}`, new product version "
             f"`{plan['version']}`.", "",
             "| component | action | version | built in this run |",
             "| --- | --- | --- | --- |"]
    for name in COMPONENT_ORDER:
        c = plan["components"][name]
        if c["changed"]:
            action = "changed"
        else:
            action = f"unchanged since {c['version']}"
        if c["build"]:
            built = "yes"
            if not c["changed"] and c["reuse"] == "none":
                built = "yes (reuse not wired yet)"
        else:
            built = f"no (reused: {c['reuse']})"
        lines.append(f"| {name} | {action} | `{c['version']}` | {built} |")
    if not plan["enforce"]:
        lines += ["", "Dry run (`enforce: false` in `.github/components.json`): every "
                  "component is still built; the table shows what an enforcing run "
                  "would reuse (RELEASE-SPLIT-1b)."]
    return "\n".join(lines) + "\n"


def github_outputs(plan: dict[str, Any]) -> str:
    """Step outputs: literals and versions only (no secrets, ci.md rc18)."""
    lines = [f"mode={plan['mode']}"]
    for name in COMPONENT_ORDER:
        c = plan["components"][name]
        lines.append(f"{name}_changed={'true' if c['changed'] else 'false'}")
        lines.append(f"{name}_build={'true' if c['build'] else 'false'}")
        lines.append(f"{name}_version={c['version']}")
        lines.append(f"{name}_reuse={c['reuse']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def history(repo: Path, manifest: dict[str, Any], tags: list[str]) -> list[dict[str, Any]]:
    """For each consecutive tag pair: which components changed. The first
    tag counts as a change of everything."""
    rows = []
    last: dict[str, str] = {}
    for i, tag in enumerate(tags):
        if i == 0:
            changed = {n: True for n in COMPONENT_ORDER}
        else:
            files = changed_files(repo, tags[i - 1], tag)
            changed = {n: bool(component_files(manifest["components"][n], files))
                       for n in COMPONENT_ORDER}
        version = tag[len(manifest["tag_prefix"]):]
        for n in COMPONENT_ORDER:
            if changed[n]:
                last[n] = version
        rows.append({"tag": tag, "changed": changed, "versions": dict(last)})
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--manifest", default=".github/components.json",
                   help="manifest path relative to --repo")


def _plan_args(p: argparse.ArgumentParser) -> None:
    _common(p)
    p.add_argument("--version", required=True, help="new product version, no 'v'")
    p.add_argument("--previous-tag", required=True, help="git ref of the previous release tag")
    p.add_argument("--ref", default="HEAD")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan")
    _plan_args(p_plan)
    p_plan.add_argument("--json-out", type=Path)
    p_plan.add_argument("--github-output", type=Path)
    p_plan.add_argument("--summary", type=Path)
    _plan_args(sub.add_parser("write"))
    _plan_args(sub.add_parser("check"))
    _common(sub.add_parser("validate"))
    p_hist = sub.add_parser("history")
    _common(p_hist)
    p_hist.add_argument("tags", nargs="+")
    args = ap.parse_args(argv)

    repo: Path = args.repo
    if args.cmd in ("write", "check") and not VERSION_RE.match(args.version):
        print(f"::error::component-versions: {args.cmd} needs a product version, "
              f"got {args.version!r}", file=sys.stderr)
        return 2
    mpath = repo / args.manifest
    try:
        manifest = load_manifest(mpath)
        if args.cmd == "validate":
            print(f"{args.manifest}: schema {SCHEMA} OK")
            return 0
        if args.cmd == "history":
            for row in history(repo, manifest, args.tags):
                flags = " ".join(f"{n}={'changed' if row['changed'][n] else '-'}"
                                 for n in COMPONENT_ORDER)
                print(f"{row['tag']}: {flags}")
            return 0
        plan = make_plan(repo, manifest, args.manifest, args.version, args.previous_tag,
                         args.ref)
    except (ManifestError, RuntimeError, json.JSONDecodeError, OSError) as exc:
        print(f"::error::component-versions: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "plan":
        sys.stdout.write(human_table(plan))
        if args.json_out:
            args.json_out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as fh:
                fh.write(github_outputs(plan))
        if args.summary:
            with args.summary.open("a", encoding="utf-8") as fh:
                fh.write(summary_markdown(plan))
        return 0
    if args.cmd == "write":
        for name in COMPONENT_ORDER:
            manifest["components"][name]["version"] = plan["components"][name]["version"]
        mpath.write_text(dump_manifest(manifest), encoding="utf-8")
        sys.stdout.write(human_table(plan))
        return 0
    # check
    bad = manifest_mismatches(manifest, plan)
    for line in bad:
        print(f"{args.manifest}: {line}")
    if bad:
        print(f"run `component-versions.py write --version {plan['version']} "
              f"--previous-tag {plan['previous_tag']}` in the release commit")
        return 1
    print(f"{args.manifest}: versions match the plan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
