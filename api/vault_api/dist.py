"""Release downloads served from the vault itself (WP DIST-1a).

The release pipeline (``.github/workflows/publish.yml``) bakes the signed
Android APK and the vault-agent release files into the vault-api image at
``VAULT_DIST_DIR`` (``/app/dist`` in the image), under exactly the names the
GitHub Release carries for the same tag:

- ``steamhangar-app-v<version>.apk`` (only when the release was signed)
- ``vault-agent-v<version>-windows-amd64.exe``, ``...-linux-amd64``,
  ``...-linux-arm64``
- ``install-task.ps1``, ``run-vault-agent.ps1``, ``uninstall-task.ps1``
- ``vault-agent-report.service``, ``vault-agent-report.timer``
- ``SHA256SUMS`` (the release's own checksum file, byte for byte)

This module decides which of those exist locally and describes them for
``GET /v1/dist``; ``routers/dist.py`` serves them under ``/dist/<name>``.

Which names can ever be served is decided from THIS server's build version,
never from the request or the directory listing: the expected set is
computed from the version, a request name must be one of those names
exactly, and only then is the file looked up (``lstat``, regular file, no
symlink). A request can therefore never name a path, and a stray file in
the directory (the placeholder README of a dev build, a leftover from
another version) is never served.

A dev build (``dev.sh build``, ``docker compose build``) bakes nothing: the
directory holds only its placeholder README. Then every artifact is
``local: false`` and, when the server's version is a published release,
points at the GitHub Release asset of the same version instead. A server
whose version is not a published release (``dev``, ``dev-<sha>``, a native
run without a commit) serves nothing locally even if files are present, and
has no release to fall back to (see :meth:`DistCatalog.local_path`).
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
from dataclasses import dataclass

from vault_api import UNKNOWN_COMMIT, BuildInfo

#: Public URL prefix the files are served under (``routers/dist.py``).
DIST_URL_PREFIX = "/dist/"

#: Stable, version-free alias for the APK: typeable on a phone and stable
#: across releases. Answers only while the versioned APK is present.
APK_ALIAS = "steamhangar-app.apk"

#: Where the GitHub Release assets of a tag live. A literal, pinned by
#: ``tests/test_dist_1a.py`` as a string literal (web/js/lib/agent-install.js
#: carries the same one).
RELEASE_DOWNLOAD_BASE = "https://github.com/steamhangar/steamhangar/releases/download"

#: A published release version, as publish.yml derives it from a ``v*`` tag
#: (the same grammar as ``web/js/lib/agent-install.js``'s RELEASE_VERSION):
#: a digits-dot triple with an optional pre-release, e.g. ``0.1.0-rc18``.
_RELEASE_VERSION = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?", re.ASCII
)


@dataclass(frozen=True)
class ArtifactSpec:
    """One release file: a stable id, its file name and its media type."""

    id: str
    name: str
    media_type: str
    #: True for names that carry the version (immutable per name).
    versioned: bool


@dataclass(frozen=True)
class Artifact:
    spec: ArtifactSpec
    #: Absolute path of the local copy, or None when it is not baked in.
    path: str | None
    size: int | None
    sha256: str | None


def is_release_version(build: BuildInfo) -> bool:
    """True when ``build`` names a published release: a release-shaped
    version AND a real commit (a native run reports the base version with
    commit ``unknown``, which is indistinguishable from a release by the
    version alone; ``agentReleaseFromAbout`` in the web applies the same
    rule)."""
    return (
        _RELEASE_VERSION.fullmatch(build.version) is not None
        and build.commit != UNKNOWN_COMMIT
    )


def artifact_specs(version: str) -> tuple[ArtifactSpec, ...]:
    """The release file set for ``version`` (without its ``v``), in a fixed
    order. Names match publish.yml's ``android-build``/``agent-build`` jobs."""
    tag = f"v{version}"
    octet = "application/octet-stream"
    text = "text/plain; charset=utf-8"
    return (
        ArtifactSpec("app-android", f"steamhangar-app-{tag}.apk", "application/vnd.android.package-archive", True),
        ArtifactSpec("agent-windows-amd64", f"vault-agent-{tag}-windows-amd64.exe", octet, True),
        ArtifactSpec("agent-linux-amd64", f"vault-agent-{tag}-linux-amd64", octet, True),
        ArtifactSpec("agent-linux-arm64", f"vault-agent-{tag}-linux-arm64", octet, True),
        ArtifactSpec("install-task", "install-task.ps1", text, False),
        ArtifactSpec("run-vault-agent", "run-vault-agent.ps1", text, False),
        ArtifactSpec("uninstall-task", "uninstall-task.ps1", text, False),
        ArtifactSpec("systemd-service", "vault-agent-report.service", text, False),
        ArtifactSpec("systemd-timer", "vault-agent-report.timer", text, False),
        ArtifactSpec("sha256sums", "SHA256SUMS", text, False),
    )


def release_base_url(build: BuildInfo) -> str | None:
    """The GitHub Release download base for this build, or None when the
    build is not a published release (there is nothing to fall back to)."""
    if not is_release_version(build):
        return None
    return f"{RELEASE_DOWNLOAD_BASE}/v{build.version}/"


def _regular_file(path: str) -> os.stat_result | None:
    """``lstat`` the path: a regular file (not a symlink, not a directory)
    or None. Every OSError (missing, unreadable) is "not present"."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


class DistCatalog:
    """The baked release files of one server build.

    Thread-safe; the SHA-256 of each file is computed once per
    (path, inode, size, mtime) and cached, because the files are immutable
    in the image and an APK is ~9 MB.
    """

    def __init__(self, dist_dir: str, build: BuildInfo) -> None:
        self.dist_dir = dist_dir
        self.build = build
        self.specs = artifact_specs(build.version)
        self._by_name = {s.name: s for s in self.specs}
        self.serves_local = is_release_version(build)
        self._lock = threading.Lock()
        self._hashes: dict[tuple[str, int, int, int], str] = {}

    def spec_for_request(self, name: str) -> ArtifactSpec | None:
        """The spec a request name refers to, by EXACT match against the
        computed set (plus the APK alias). Never derives a path from the
        request."""
        if name == APK_ALIAS:
            return self.specs[0]
        return self._by_name.get(name)

    def local_path(self, spec: ArtifactSpec) -> tuple[str, os.stat_result] | None:
        """The local copy of ``spec`` as (path, stat), or None. The path is
        built from the computed name only.

        Only a published-release build serves anything: half of the names
        (scripts, units, SHA256SUMS) carry no version, so on a dev or native
        build a file under such a name could belong to any release. The
        release pipeline stages exactly one version's files and checks the
        names against the image's version (publish.yml, "Stage release
        downloads"), which is what makes the version-free names trustworthy
        in a release image.
        """
        if not self.serves_local or not self.dist_dir:
            return None
        path = os.path.join(self.dist_dir, spec.name)
        st = _regular_file(path)
        return (path, st) if st is not None else None

    def _sha256(self, path: str, st: os.stat_result) -> str | None:
        key = (path, st.st_ino, st.st_size, st.st_mtime_ns)
        with self._lock:
            cached = self._hashes.get(key)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        value = digest.hexdigest()
        with self._lock:
            self._hashes[key] = value
        return value

    def artifacts(self) -> list[Artifact]:
        out: list[Artifact] = []
        for spec in self.specs:
            found = self.local_path(spec)
            if found is None:
                out.append(Artifact(spec, None, None, None))
                continue
            path, st = found
            sha = self._sha256(path, st)
            if sha is None:  # vanished or unreadable between lstat and read
                out.append(Artifact(spec, None, None, None))
                continue
            out.append(Artifact(spec, path, st.st_size, sha))
        return out
