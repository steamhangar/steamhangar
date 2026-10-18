"""A fake SteamPrefill executable for the tests. No Steam login, no network.

The real binary cannot be used in tests: it needs a Steam account, a running
vault-core, and real bandwidth. This module generates a small executable that
behaves like it in the ways the runner depends on:

- it reads its app selection from ``Config/selectedAppsToPrefill.json`` next to
  itself — the verified non-interactive selection mechanism (see
  ``vault_api/prefill.py``), so the tests prove the runner actually uses it;
- it writes fake depot chunk files into a cache root, so the depot-diff
  attribution has something real to observe;
- it can optionally drop a synthetic SteamPrefill manifest ``.bin`` file into
  a configured directory (WP 3.2's worker-ingestion end-to-end test), using
  caller-supplied raw bytes (hex-encoded in the control file) rather than a
  second protobuf encoder — the one in ``tests/test_manifests.py`` is reused;
- it can reproduce the failure modes that matter: a non-zero exit, the verbatim
  not-logged-in output, and a hang.

``make_stub`` writes a platform-appropriate launcher (a ``.cmd`` shim on
Windows, a ``sh`` shim elsewhere) that runs the stub with the *current*
interpreter, so nothing has to be on PATH. Both were verified to pass argv
through, hand the child an EOF stdin, and propagate the exit code.

Known shim artifact: on Windows, ``terminate()`` on a ``.cmd`` kills cmd.exe but
not the python grandchild (measured). The real SteamPrefill is a single-process
executable, so this only affects the stub — hence the bounded hang below.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

#: The stub reads this file (next to itself) to learn what to do.
CONTROL_FILENAME = "stub_control.json"

#: Verbatim from a real SteamPrefill v3.7.1 run with an empty Config/ and
#: stdin closed (see vault_api/prefill.py's module docstring).
NOT_LOGGED_IN_OUTPUT = """[7:31:52 PM] Starting login!
A Steam account is required in order to prefill apps!
Please enter your Steam account name :
[7:31:52 PM] Already disconnected from Steam
System.InvalidOperationException: Failed to read input in non-interactive mode.
  at async Task<ConsoleKeyInfo?> Spectre.Console.DefaultInput.ReadKeyAsync(bool
  at async Task SteamPrefill.Handlers.Steam.Steam3Session.LoginToSteamAsync()
"""

_STUB_SOURCE = r'''
"""Fake SteamPrefill. Behavior is driven by stub_control.json next to this file."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

LANCACHE_NOT_DETECTED_OUTPUT = (
    "LancacheNotFoundException: Unable to detect Lancache server!\n"
    "  at async Task<IPAddress> LancachePrefill.Common.LancacheIpResolver"
    ".ResolveLancacheIpAsync(...)"
)


def log(message):
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def main():
    with open(os.path.join(HERE, "stub_control.json"), encoding="utf-8") as handle:
        control = json.load(handle)

    # Record the argv the runner used, so a test can assert the exact CLI
    # invocation (prefill --force --no-ansi) instead of trusting a comment.
    with open(os.path.join(HERE, "argv.json"), "w", encoding="utf-8") as handle:
        json.dump(sys.argv[1:], handle)

    # stdin must be closed/null: read() returning "" immediately proves the
    # runner did not hand us an inheritable console.
    try:
        stdin_data = sys.stdin.read()
        stdin_state = "eof" if stdin_data == "" else "data"
    except Exception as exc:  # pragma: no cover - stdin fully detached
        stdin_state = "error:%s" % type(exc).__name__

    selection_path = os.path.join(HERE, "Config", "selectedAppsToPrefill.json")
    with open(selection_path, encoding="utf-8") as handle:
        selected = json.load(handle)

    # Non-overlap evidence for the one-job-at-a-time test.
    runs_path = os.path.join(HERE, "runs.jsonl")
    started = time.time()
    log("[stub] selected=%s stdin=%s" % (selected, stdin_state))

    # WP RUNNER-RETRY-1: the first N invocations fail cache detection the
    # way SteamPrefill does when its /lancache-heartbeat probe gets no valid
    # answer in time (exception, non-zero exit); later ones run the mode.
    lancache_fail_runs = control.get("lancache_fail_runs", 0)
    if lancache_fail_runs:
        counter_path = os.path.join(HERE, "lancache_failures.txt")
        failures = 0
        if os.path.exists(counter_path):
            with open(counter_path, encoding="utf-8") as handle:
                failures = int(handle.read() or "0")
        if failures < lancache_fail_runs:
            with open(counter_path, "w", encoding="utf-8") as handle:
                handle.write(str(failures + 1))
            log("[stub] Detecting Lancache server...")
            log(LANCACHE_NOT_DETECTED_OUTPUT)
            _record(runs_path, selected, started, 1)
            return 1

    mode = control.get("mode", "success")
    sleep_seconds = control.get("sleep_seconds", 0)
    if sleep_seconds:
        time.sleep(sleep_seconds)

    if mode == "hang":
        # Bounded on purpose. On Windows the launcher is a .cmd shim, and
        # terminating it kills cmd.exe but NOT this grandchild (verified), so an
        # unbounded loop would leave an orphan behind for the whole test
        # session. The bound is far longer than any test's timeout, so the
        # runner's timeout/abort path is still what ends the job.
        log("[stub] hanging")
        deadline = time.time() + control.get("max_hang_seconds", 10)
        while time.time() < deadline:
            time.sleep(0.2)
        return 0

    if mode == "not_logged_in":
        sys.stdout.write(control["not_logged_in_output"])
        sys.stdout.flush()
        _record(runs_path, selected, started, 1)
        return 1

    if mode == "fail":
        log("[stub] Retrieving latest App metadata...")
        sys.stderr.write("[stub] simulated depot download failure\n")
        sys.stderr.flush()
        _record(runs_path, selected, started, control.get("exit_code", 3))
        return control.get("exit_code", 3)

    if mode == "chatty":
        for index in range(400):
            log("[stub] progress line %04d %s" % (index, "x" * 40))

    # success / noop: optionally write fake chunks for the selected app(s).
    cache_root = control.get("cache_root")
    depots_by_app = control.get("depots_by_app", {})
    written = []
    if mode != "noop" and cache_root:
        for appid in selected:
            for depotid in depots_by_app.get(str(appid), []):
                chunk_dir = os.path.join(cache_root, "depot", str(depotid), "chunk")
                os.makedirs(chunk_dir, exist_ok=True)
                name = "%040x" % (int(depotid) * 1000 + len(os.listdir(chunk_dir)))
                with open(os.path.join(chunk_dir, name), "wb") as handle:
                    handle.write(b"fake-chunk" * 16)
                written.append(depotid)

    # WP 3.2: optionally drop synthetic SteamPrefill manifest .bin files, so
    # the worker-ingestion end-to-end test can observe real ingestion
    # (parse -> depot_manifests row -> archive) through the full stack. Bytes
    # travel through the JSON control file as hex (JSON has no bytes type);
    # tests/test_manifests.py's protobuf encoder builds them, this stub just
    # writes them out verbatim -- it has no opinion on their contents.
    for spec in control.get("manifest_bins", []):
        target_dir = spec["dir"]
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, spec["filename"]), "wb") as handle:
            handle.write(bytes.fromhex(spec["hex"]))

    log("[stub] wrote depots=%s" % written)
    log("[stub] Prefill complete!")

    # WP 3.3: optionally emit SteamPrefill's own end-of-run summary table
    # (or a corrupted/absent stand-in for it) verbatim, so the worker
    # end-to-end tests can drive prefill_summary.parse_summary + the
    # job-outcome wiring through the real subprocess pipe rather than only
    # unit-testing the parser in isolation. Written via sys.stdout.buffer
    # (raw bytes), not text-mode sys.stdout.write, so the exact bytes a test
    # asks for survive unchanged regardless of this interpreter's own stdout
    # encoding -- "summary_text" is a normal unicode string, UTF-8 encoded;
    # "summary_bytes_hex" is raw bytes (hex-encoded for the JSON control
    # file, same convention as manifest_bins above) for tests that need
    # deliberately-not-valid-UTF-8 bytes, e.g. real single-byte OEM-codepage
    # bytes exercising vault_api.prefill's decode fallback.
    summary_text = control.get("summary_text")
    summary_bytes_hex = control.get("summary_bytes_hex")
    if summary_text or summary_bytes_hex:
        sys.stdout.flush()
        data = bytes.fromhex(summary_bytes_hex) if summary_bytes_hex else summary_text.encode("utf-8")
        if not data.endswith(b"\n"):
            data += b"\n"
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    _record(runs_path, selected, started, 0)
    return 0


def _record(runs_path, selected, started, exit_code):
    entry = {
        "selected": selected,
        "started": started,
        "finished": time.time(),
        "exit_code": exit_code,
    }
    with open(runs_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    sys.exit(main())
'''


def _encode_manifest_bins(
    manifest_bins: list[dict[str, object]] | None,
) -> list[dict[str, str]]:
    """``[{"dir": str, "filename": str, "data": bytes}, ...]`` -> JSON-safe hex."""
    encoded = []
    for spec in manifest_bins or []:
        encoded.append(
            {
                "dir": str(spec["dir"]),
                "filename": str(spec["filename"]),
                "hex": bytes(spec["data"]).hex(),  # type: ignore[arg-type]
            }
        )
    return encoded


def make_stub(
    directory: Path,
    *,
    mode: str = "success",
    cache_root: str | None = None,
    depots_by_app: dict[int, list[int]] | None = None,
    exit_code: int = 3,
    sleep_seconds: float = 0.0,
    manifest_bins: list[dict[str, object]] | None = None,
    summary_text: str | None = None,
    summary_bytes: bytes | None = None,
    lancache_fail_runs: int = 0,
) -> str:
    """Create a fake SteamPrefill in ``directory``; returns the launcher path.

    ``directory`` plays the role of the real ``bin/`` folder: the runner writes
    ``Config/selectedAppsToPrefill.json`` into it and the stub reads it back.

    ``manifest_bins`` (WP 3.2): a list of ``{"dir": str, "filename": str,
    "data": bytes}`` — the stub writes ``data`` verbatim to
    ``os.path.join(dir, filename)`` on a successful run, so a test can point
    ``dir`` at a fake SteamPrefill temp-cache directory and observe
    ``ingest_after_prefill`` pick it up through the real worker.

    ``summary_text``/``summary_bytes`` (WP 3.3, mutually exclusive — pass at
    most one): appended verbatim to stdout right after "Prefill complete!" on
    a successful run, so a worker end-to-end test can exercise
    ``vault_api.prefill_summary.parse_summary`` and the job-outcome wiring
    through the real subprocess pipe. ``summary_text`` is UTF-8 encoded;
    ``summary_bytes`` goes through unencoded (hex-encoded only for transport
    in the JSON control file) — use it for bytes that are deliberately not
    valid UTF-8, e.g. real single-byte OEM-codepage bytes for the decode-fix
    test. Neither given (both ``None``, the default) reproduces the exact
    pre-WP-3.3 stub output every other test in this module depends on.

    ``lancache_fail_runs`` (WP RUNNER-RETRY-1): the first that many
    invocations print SteamPrefill's "Unable to detect Lancache server!"
    exception and exit 1; later invocations behave as ``mode`` says. The
    count survives between runs in ``lancache_failures.txt`` next to the stub.
    """
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "steamprefill_stub.py"
    script.write_text(_STUB_SOURCE, encoding="utf-8")

    control = {
        "mode": mode,
        "cache_root": cache_root,
        "depots_by_app": {str(key): value for key, value in (depots_by_app or {}).items()},
        "exit_code": exit_code,
        "sleep_seconds": sleep_seconds,
        "not_logged_in_output": NOT_LOGGED_IN_OUTPUT,
        "manifest_bins": _encode_manifest_bins(manifest_bins),
        "summary_text": summary_text,
        "summary_bytes_hex": summary_bytes.hex() if summary_bytes is not None else None,
        "lancache_fail_runs": lancache_fail_runs,
    }
    (directory / CONTROL_FILENAME).write_text(
        json.dumps(control, indent=2), encoding="utf-8"
    )

    if os.name == "nt":
        launcher = directory / "SteamPrefill.cmd"
        launcher.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{script}" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
    else:
        launcher = directory / "SteamPrefill"
        launcher.write_text(
            "#!/bin/sh\n" f'exec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    return str(launcher)


def set_mode(directory: Path, **updates: object) -> None:
    """Patch the stub's control file in place (between runs)."""
    path = directory / CONTROL_FILENAME
    control = json.loads(path.read_text(encoding="utf-8"))
    for key, value in updates.items():
        if key == "depots_by_app":
            value = {str(k): v for k, v in value.items()}  # type: ignore[union-attr]
        elif key == "manifest_bins":
            value = _encode_manifest_bins(value)  # type: ignore[arg-type]
        control[key] = value
    path.write_text(json.dumps(control, indent=2), encoding="utf-8")


def read_runs(directory: Path) -> list[dict[str, object]]:
    """Every invocation the stub recorded (start/finish timestamps, selection)."""
    path = directory / "runs.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_argv(directory: Path) -> list[str]:
    """The argv (without argv[0]) of the stub's most recent invocation."""
    path = directory / "argv.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def read_selection(directory: Path) -> list[int]:
    """Contents of the selected-apps state file the runner wrote."""
    path = directory / "Config" / "selectedAppsToPrefill.json"
    return json.loads(path.read_text(encoding="utf-8"))
