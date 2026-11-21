"""WP PROXY-VERSION-1: ``GET /v1/about`` shows vault-proxy's real version.

The proxy's entrypoint renders tinyproxy's ``ErrorFile 403`` page with one
marker line naming the image's ``VAULT_BUILD_VERSION`` /
``VAULT_BUILD_COMMIT``; the about probe, which asks the proxy for an
off-list host anyway, reads it from the refusal. No host is added to the
egress filter.

Guarantees, each pinned by name below:

P1  the REAL entrypoint renders the marker with the baked values, and the
    template's ``ErrorFile 403`` names the path it writes.
P2  for every baked value, the proxy's row shows what vault-api's own row
    shows for the same value (one grammar on both sides, ``invalid`` for a
    bad value, ``null`` for the commit ``unknown``).
P3  the page never contains a tinyproxy ``{variable}`` or a raw bad value.
P4  a page that cannot be written does not stop the proxy.
P5  the probe reads the marker from a real socket, also when it arrives in
    pieces, and never reads more than ``PROXY_ANSWER_MAX_BYTES``.
P6  fallback: no marker, a late body, a proxy that does not answer, or a
    non-403 answer all give ``null`` version and commit, never an error.
P7  the route returns the proxy's version and commit.
"""

from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_eg1_egress_lock import (
    PROXY_ENTRYPOINT_PATH,
    PROXY_TINYPROXY_CONF_PATH,
    _EXPECTED_VAULT_EGRESS_SUBNET,
    _SH,
    requires_sh,
)
from vault_api import INVALID_VALUE, about, reported_identity
from vault_api.about import AboutService, ProxyAnswer
from vault_api.config import Settings
from vault_api.main import create_app

SHA = "0123456789abcdef0123456789abcdef01234567"
HEADERS = {"X-Api-Key": TEST_API_KEY}
MARKER = re.compile(r"<!-- vault-build component=vault-proxy version=(\S*) commit=(\S*) -->")
REFUSAL_HEAD = b"HTTP/1.0 403 Filtered\r\nServer: tinyproxy/1.11.3\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"


# --- helpers -------------------------------------------------------------------


def _render(tmp_path: Path, env_extra: dict[str, str], page: Path | None = None) -> tuple[int, str, Path]:
    """Run the REAL entrypoint with the test-only path overrides; returns
    ``(exit code, output, page path)``."""
    page = tmp_path / "403.html" if page is None else page
    env = {
        "VAULT_EGRESS_ALLOW": "",
        "VAULT_EGRESS_SUBNET": _EXPECTED_VAULT_EGRESS_SUBNET,
        "VAULT_MANIFEST_ORACLE": "off",
        "VAULT_PROXY_FILTER_FILE_FOR_TESTS": str(tmp_path / "filter"),
        "VAULT_PROXY_CONF_TEMPLATE_FOR_TESTS": str(PROXY_TINYPROXY_CONF_PATH),
        "VAULT_PROXY_CONF_FILE_FOR_TESTS": str(tmp_path / "tinyproxy.conf"),
        "VAULT_PROXY_ERROR_PAGE_FILE_FOR_TESTS": str(page),
        "PATH": "/usr/bin:/bin",
        **env_extra,
    }
    assert _SH is not None
    proc = subprocess.run(
        [_SH, str(PROXY_ENTRYPOINT_PATH), "echo", "TINYPROXY-WOULD-START"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    return proc.returncode, proc.stdout + proc.stderr, page


def _page_build(page: Path) -> tuple[str, str]:
    found = MARKER.findall(page.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected exactly one marker line, found {found!r}"
    return found[0]


class _Server:
    """A loopback TCP server that reads the request, then sends ``chunks``
    with ``pause`` seconds between them, then holds the connection open
    until closed (``hold``) or closes it."""

    def __init__(self, chunks: list[bytes], pause: float = 0.0, hold: bool = False) -> None:
        self.chunks = chunks
        self.pause = pause
        self.hold = hold
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.release = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(5)
            request = b""
            try:
                while b"\r\n\r\n" not in request:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    request += chunk
                for i, chunk in enumerate(self.chunks):
                    if i and self.pause:
                        time.sleep(self.pause)
                    conn.sendall(chunk)
            except OSError:
                return
            if self.hold:
                self.release.wait(5)

    def close(self) -> None:
        self.release.set()
        self.thread.join(timeout=5)
        self.sock.close()


def _probe(chunks: list[bytes], timeout: float = 2.0, **kw) -> ProxyAnswer:
    server = _Server(chunks, **kw)
    try:
        return about.probe_proxy(("127.0.0.1", server.port), timeout)
    finally:
        server.close()


def _proxy_row(settings: Settings, answer: ProxyAnswer | Exception) -> about.Component:
    def prober(addr, timeout):
        if isinstance(answer, Exception):
            raise answer
        return answer

    svc = AboutService(settings, environ={"HTTP_PROXY": "http://vault-proxy:8888"}, proxy_prober=prober)
    return {c.name: c for c in svc.components()}["vault-proxy"]


# --- P1: the entrypoint renders the marker ---------------------------------------


@requires_sh
def test_entrypoint_renders_the_baked_version_and_commit(tmp_path: Path) -> None:
    rc, out, page = _render(tmp_path, {"VAULT_BUILD_VERSION": "0.1.0-rc23", "VAULT_BUILD_COMMIT": SHA})
    assert rc == 0, out
    assert "TINYPROXY-WOULD-START" in out
    assert _page_build(page) == ("0.1.0-rc23", SHA)
    assert f"refusal page rendered: version=0.1.0-rc23 commit={SHA}" in out
    # A page tinyproxy can serve: HTML, the status in the title.
    text = page.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>") and "<title>403 Refused by vault-proxy</title>" in text


@requires_sh
def test_entrypoint_keeps_the_dockerfile_defaults(tmp_path: Path) -> None:
    rc, out, page = _render(tmp_path, {"VAULT_BUILD_VERSION": "dev", "VAULT_BUILD_COMMIT": "unknown"})
    assert rc == 0, out
    assert _page_build(page) == ("dev", "unknown")
    assert "WARNING" not in out


def test_template_error_file_names_the_page_the_entrypoint_writes() -> None:
    conf = PROXY_TINYPROXY_CONF_PATH.read_text(encoding="utf-8")
    directives = [
        line.strip() for line in conf.splitlines()
        if re.match(r"\s*(errorfile|defaulterrorfile)\s", line, re.IGNORECASE)
    ]
    assert directives == ['ErrorFile 403 "/run/tinyproxy/403.html"']
    entrypoint = PROXY_ENTRYPOINT_PATH.read_text(encoding="utf-8")
    assert 'ERROR_PAGE="${VAULT_PROXY_ERROR_PAGE_FILE_FOR_TESTS:-/run/tinyproxy/403.html}"' in entrypoint


# --- P2: one grammar on both sides ------------------------------------------------

_VERSIONS = [
    "0.1.0-rc23", "dev", "1.2.3+build.4", "a" * 64, "a" * 65, "", "-x", ".x", "x y",
    "x<y", "x-->y", "{x}", "é1", "invalid",
]
_COMMITS = [SHA, "abcdef0", "ABCDEF0", "abc", "unknown", "", "g" * 7, "a" * 41, "invalid", "{detail}"]


@requires_sh
@pytest.mark.parametrize("version", _VERSIONS)
def test_proxy_row_matches_the_api_row_for_every_version(tmp_path: Path, settings: Settings, version: str) -> None:
    env = {"VAULT_BUILD_VERSION": version, "VAULT_BUILD_COMMIT": SHA}
    rc, out, page = _render(tmp_path, env)
    assert rc == 0, out
    row = _proxy_row(settings, ProxyAnswer(403, _page_build(page)))
    assert (row.version, row.commit) == reported_identity(env), version


@requires_sh
@pytest.mark.parametrize("commit", _COMMITS)
def test_proxy_row_matches_the_api_row_for_every_commit(tmp_path: Path, settings: Settings, commit: str) -> None:
    env = {"VAULT_BUILD_VERSION": "0.1.0-rc23", "VAULT_BUILD_COMMIT": commit}
    rc, out, page = _render(tmp_path, env)
    assert rc == 0, out
    row = _proxy_row(settings, ProxyAnswer(403, _page_build(page)))
    assert (row.version, row.commit) == reported_identity(env), commit


@requires_sh
def test_unset_build_env_is_invalid_on_the_page(tmp_path: Path, settings: Settings) -> None:
    # The image always bakes both; a container started without them is a
    # broken override, so the page says "invalid" and the commit "unknown".
    rc, out, page = _render(tmp_path, {})
    assert rc == 0, out
    assert _page_build(page) == ("invalid", "unknown")
    row = _proxy_row(settings, ProxyAnswer(403, _page_build(page)))
    assert (row.version, row.commit, row.status) == (INVALID_VALUE, None, "ok")
    assert "names a version that is not valid" in row.detail


# --- P3: nothing tinyproxy would substitute, nothing raw ---------------------------


@requires_sh
@pytest.mark.parametrize("version, commit", [("{url}", "{clientip}"), ("x</title><script>", "<!--x"), ("a~b", "c~d")])
def test_page_never_carries_a_variable_or_a_raw_bad_value(tmp_path: Path, version: str, commit: str) -> None:
    rc, out, page = _render(tmp_path, {"VAULT_BUILD_VERSION": version, "VAULT_BUILD_COMMIT": commit})
    assert rc == 0, out
    text = page.read_text(encoding="utf-8")
    assert "{" not in text and "}" not in text
    assert version not in text and commit not in text
    assert _page_build(page) == ("invalid", "invalid")


# --- P4: a failed write does not stop the proxy -------------------------------------


@requires_sh
def test_unwritable_page_warns_and_the_proxy_still_starts(tmp_path: Path) -> None:
    page = tmp_path / "missing-dir" / "403.html"
    rc, out, _ = _render(tmp_path, {"VAULT_BUILD_VERSION": "dev", "VAULT_BUILD_COMMIT": "unknown"}, page=page)
    assert rc == 0, out
    assert "TINYPROXY-WOULD-START" in out
    assert "WARNING: could not write the refusal page" in out
    assert not page.exists()
    assert (tmp_path / "tinyproxy.conf").exists() and (tmp_path / "filter").exists()


# --- P5: the probe reads the marker ---------------------------------------------------


@requires_sh
def test_probe_reads_the_real_page_over_a_socket(tmp_path: Path) -> None:
    rc, out, page = _render(tmp_path, {"VAULT_BUILD_VERSION": "0.1.0-rc23", "VAULT_BUILD_COMMIT": SHA})
    assert rc == 0, out
    answer = _probe([REFUSAL_HEAD + page.read_bytes()])
    assert answer == ProxyAnswer(403, ("0.1.0-rc23", SHA))


def test_probe_reads_a_marker_that_arrives_in_pieces() -> None:
    line = f"<!-- vault-build component=vault-proxy version=0.1.0-rc23 commit={SHA} -->\n".encode()
    pieces = [REFUSAL_HEAD[:9], REFUSAL_HEAD[9:] + b"<html>", line[:20], line[20:50], line[50:], b"</html>"]
    answer = _probe(pieces, pause=0.05)
    assert answer == ProxyAnswer(403, ("0.1.0-rc23", SHA))


def test_probe_stops_at_the_marker_without_waiting_for_the_close() -> None:
    line = f"<!-- vault-build component=vault-proxy version=dev commit={SHA} -->\n".encode()
    started = time.monotonic()
    answer = _probe([REFUSAL_HEAD + line], timeout=2.0, hold=True)
    assert time.monotonic() - started < 1.5
    assert answer == ProxyAnswer(403, ("dev", SHA))


def test_probe_reads_at_most_the_limit() -> None:
    line = b"<!-- vault-build component=vault-proxy version=dev commit=unknown -->"
    filler = b"x" * (about.PROXY_ANSWER_MAX_BYTES - len(REFUSAL_HEAD))
    answer = _probe([REFUSAL_HEAD + filler + line + b"y" * 100_000])
    assert answer == ProxyAnswer(403, None)
    assert about.PROXY_ANSWER_MAX_BYTES == 4096


# --- P6: fallbacks ------------------------------------------------------------------


def test_status_line_then_silence_is_ok_without_a_version(settings: Settings) -> None:
    server = _Server([REFUSAL_HEAD + b"<html>"], hold=True)
    try:
        answer = about.probe_proxy(("127.0.0.1", server.port), 0.3)
    finally:
        server.close()
    assert answer == ProxyAnswer(403, None)


def test_old_proxy_without_a_marker_shows_dashes(settings: Settings) -> None:
    builtin = b"<html><body><h1>Filtered</h1><p><em>Generated by tinyproxy version 1.11.3.</em></p></body></html>"
    answer = _probe([REFUSAL_HEAD + builtin])
    assert answer == ProxyAnswer(403, None)
    row = _proxy_row(settings, answer)
    assert (row.status, row.version, row.commit) == ("ok", None, None)
    assert row.detail == (
        "Answers, and refuses a host that is not on the egress allowlist. Its refusal page names no "
        "version: a vault-proxy image from before rc23, or the page could not be written (see the proxy's log)."
    )
    assert "1.11.3" not in (row.version or "")


@pytest.mark.parametrize("error", [ConnectionRefusedError(), socket.timeout(), OSError("unreachable")])
def test_a_proxy_that_does_not_answer_shows_dashes_and_no_error(settings: Settings, error: Exception) -> None:
    row = _proxy_row(settings, error)
    assert (row.status, row.version, row.commit) == ("unreachable", None, None)


def test_a_silent_proxy_over_a_real_socket_shows_dashes(settings: Settings) -> None:
    server = _Server([], hold=True)
    try:
        svc = AboutService(
            settings, environ={"HTTP_PROXY": f"http://127.0.0.1:{server.port}"},
            probe_timeout_seconds=0.3,
        )
        row = {c.name: c for c in svc.components()}["vault-proxy"]
    finally:
        server.close()
    assert (row.status, row.version, row.commit) == ("unreachable", None, None)


def test_a_marker_on_a_non_403_answer_is_ignored(settings: Settings) -> None:
    row = _proxy_row(settings, ProxyAnswer(200, ("0.1.0-rc23", SHA)))
    assert (row.status, row.version, row.commit) == ("unknown", None, None)
    assert "Check the egress filter" in row.detail


@pytest.mark.parametrize(
    "build, expected",
    [
        (("0.1.0-rc23", SHA), ("0.1.0-rc23", SHA)),
        (("0.1.0-rc23", "unknown"), ("0.1.0-rc23", None)),
        (("invalid", "invalid"), ("invalid", "invalid")),
        (("", SHA), (INVALID_VALUE, SHA)),
        (("a" * 65, SHA), (INVALID_VALUE, SHA)),
        (("0.1.0", "ABCDEF0"), ("0.1.0", INVALID_VALUE)),
        (("0.1.0", "�" * 7), ("0.1.0", INVALID_VALUE)),
        (None, (None, None)),
    ],
)
def test_marker_values_are_checked_on_read(build, expected) -> None:
    assert about.proxy_build_from(build) == expected


def test_non_ascii_bytes_in_the_marker_become_invalid(settings: Settings) -> None:
    line = "<!-- vault-build component=vault-proxy version=1.0é commit=abcdef0 -->".encode("utf-8")
    answer = _probe([REFUSAL_HEAD + line])
    row = _proxy_row(settings, answer)
    assert (row.version, row.commit) == (INVALID_VALUE, "abcdef0")


# --- P7: the route --------------------------------------------------------------------


def test_route_returns_the_proxy_version_and_commit(settings: Settings) -> None:
    app = create_app(settings)
    app.state.about = AboutService(
        settings, environ={"HTTP_PROXY": "http://vault-proxy:8888"},
        proxy_prober=lambda a, t: ProxyAnswer(403, ("0.1.0-rc23", SHA)),
    )
    body = TestClient(app).get("/v1/about", headers=HEADERS).json()
    proxy = {c["name"]: c for c in body["components"]}["vault-proxy"]
    assert (proxy["status"], proxy["version"], proxy["commit"]) == ("ok", "0.1.0-rc23", SHA)


def test_route_still_answers_when_the_proxy_does_not(settings: Settings) -> None:
    def refused(addr, timeout):
        raise ConnectionRefusedError()

    app = create_app(settings)
    app.state.about = AboutService(
        settings, environ={"HTTP_PROXY": "http://vault-proxy:8888"}, proxy_prober=refused,
    )
    response = TestClient(app).get("/v1/about", headers=HEADERS)
    assert response.status_code == 200
    proxy = {c["name"]: c for c in response.json()["components"]}["vault-proxy"]
    assert (proxy["status"], proxy["version"], proxy["commit"]) == ("unreachable", None, None)
