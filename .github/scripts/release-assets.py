#!/usr/bin/env python3
"""Verify client files reused from an earlier release (WP RELEASE-SPLIT-1b).

ADR-0031: an unchanged agent or app is not rebuilt. publish.yml's
`agent-reuse` / `android-reuse` jobs download the files of the component's
recorded version from that version's GitHub Release and run this script
before anything is baked into vault-api or attached to the new release.

  names   print the file names of a component at a version, one per line
          (`.github/components.json` `artifacts.files`, `{version}` filled)
  verify  check a download directory against the release's metadata
          (`gh api repos/<repo>/releases/tags/v<version>` saved as JSON)

`verify` fails closed. For every expected file:

- it was downloaded (and nothing else is in the directory);
- GitHub's own asset digest (`assets[].digest`, `sha256:<hex>`, computed by
  GitHub when the asset was uploaded) exists and matches. A release asset
  without a digest is refused: there would be nothing independent of the
  downloaded bytes to compare with;
- when the release body records the file's SHA-256 in its verify block (a
  `<sha256>  <name>` line, written by publish.yml's agent-binaries job
  since RELEASE-SPLIT-1b), that value matches too.

With `--sha256sums`, the directory's `SHA256SUMS` must list exactly the
other expected files and every listed hash must match (the agent).

On success it prints `<sha256>  <name>` per file (sha256sum format), so a
job can read the APK's hash for its outputs. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"[0-9a-f]{64}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?")
VERIFY_START = "<!-- steamhangar:verify:start -->"
VERIFY_END = "<!-- steamhangar:verify:end -->"
BODY_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")


class VerifyError(Exception):
    """The download does not match what the release recorded."""


def component_files(manifest: dict[str, Any], component: str, version: str) -> list[str]:
    if not VERSION_RE.fullmatch(version):
        raise VerifyError(f"{version!r} is not a product version")
    try:
        files = manifest["components"][component]["artifacts"]["files"]
    except (KeyError, TypeError) as exc:
        raise VerifyError(f"manifest has no artifacts.files for {component!r}") from exc
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        raise VerifyError(f"artifacts.files of {component!r} is not a list of names")
    names = [f.replace("{version}", version) for f in files]
    for n in names:
        if "/" in n or n.startswith(".") or not n:
            raise VerifyError(f"artifact name {n!r} is not a plain file name")
    return names


def body_digests(body: str) -> dict[str, str]:
    """`<sha256>  <name>` lines inside the release body's verify block."""
    out: dict[str, str] = {}
    inside = False
    for line in body.splitlines():
        line = line.rstrip("\r")
        if line == VERIFY_START:
            inside = True
        elif line == VERIFY_END:
            inside = False
        elif inside:
            m = BODY_LINE.match(line)
            if m:
                if m.group(2) in out and out[m.group(2)] != m.group(1):
                    raise VerifyError(f"release body records two SHA-256 for {m.group(2)}")
                out[m.group(2)] = m.group(1)
    return out


def asset_digests(release: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            continue
        digest = asset.get("digest")
        value = None
        if isinstance(digest, str) and digest.startswith("sha256:") and HEX64.fullmatch(digest[7:]):
            value = digest[7:]
        out[asset["name"]] = value
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sha256sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", line)
        if not m:
            raise VerifyError(f"SHA256SUMS line is malformed: {line!r}")
        if m.group(2) in out:
            raise VerifyError(f"SHA256SUMS lists {m.group(2)} twice")
        out[m.group(2)] = m.group(1)
    return out


def verify(directory: Path, release: dict[str, Any], expected: list[str], tag: str,
           sha256sums: bool = False) -> dict[str, str]:
    if release.get("tag_name") != tag:
        raise VerifyError(f"release metadata is for {release.get('tag_name')!r}, not {tag}")
    if release.get("draft") is True:
        raise VerifyError(f"{tag} is a draft release")
    present = sorted(p.name for p in directory.iterdir())
    missing = [n for n in expected if n not in present]
    if missing:
        raise VerifyError(f"{tag}: not downloaded: {', '.join(missing)}")
    extra = [n for n in present if n not in expected]
    if extra:
        raise VerifyError(f"{tag}: unexpected files in the download: {', '.join(extra)}")
    assets = asset_digests(release)
    recorded = body_digests(release.get("body") or "")
    got: dict[str, str] = {}
    for name in expected:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise VerifyError(f"{name} is not a regular file")
        if name not in assets:
            raise VerifyError(f"{tag} has no release asset {name}")
        digest = assets[name]
        if digest is None:
            raise VerifyError(f"{tag}: asset {name} carries no sha256 digest; refusing to reuse it")
        actual = sha256_file(path)
        if actual != digest:
            raise VerifyError(f"{name}: sha256 {actual} differs from the release asset digest {digest}")
        if name in recorded and recorded[name] != actual:
            raise VerifyError(f"{name}: sha256 {actual} differs from the release body's {recorded[name]}")
        got[name] = actual
    if sha256sums:
        if "SHA256SUMS" not in expected:
            raise VerifyError("--sha256sums: SHA256SUMS is not an expected file")
        listed = parse_sha256sums((directory / "SHA256SUMS").read_text(encoding="utf-8"))
        want = sorted(n for n in expected if n != "SHA256SUMS")
        if sorted(listed) != want:
            raise VerifyError(f"SHA256SUMS lists {sorted(listed)}, expected {want}")
        for name, value in listed.items():
            if got[name] != value:
                raise VerifyError(f"{name}: sha256 {got[name]} differs from SHA256SUMS {value}")
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("names", "verify"):
        p = sub.add_parser(cmd)
        p.add_argument("--manifest", type=Path, default=Path(".github/components.json"))
        p.add_argument("--component", required=True, choices=("agent", "app"))
        p.add_argument("--version", required=True, help="recorded version, no 'v'")
        if cmd == "verify":
            p.add_argument("--dir", type=Path, required=True)
            p.add_argument("--release-json", type=Path, required=True)
            p.add_argument("--sha256sums", action="store_true")
    args = ap.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        names = component_files(manifest, args.component, args.version)
        if args.cmd == "names":
            print("\n".join(names))
            return 0
        release = json.loads(args.release_json.read_text(encoding="utf-8"))
        if not isinstance(release, dict):
            raise VerifyError("release metadata is not a JSON object")
        got = verify(args.dir, release, names, f"v{args.version}", args.sha256sums)
    except (VerifyError, OSError, json.JSONDecodeError) as exc:
        print(f"::error::release-assets: {exc}", file=sys.stderr)
        return 1
    for name in names:
        print(f"{got[name]}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
