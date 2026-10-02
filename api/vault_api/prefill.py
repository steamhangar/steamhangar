"""SteamPrefill runner: run one prefill, observe which depots it filled.

Two independent jobs live here:

1. ``run_prefill`` — invoke the SteamPrefill CLI for exactly one appid as a
   subprocess and report the outcome (never raises for a *prefill* failure;
   failures are data, so the job queue can record them).
2. ``scan_depots`` / ``diff_depots`` / ``apply_observed_mapping`` — attribute
   the depots that changed during the run to that appid and write the mapping
   with replace-semantics (ADR-0003 decision 3).

The verified SteamPrefill CLI contract (v3.7.1)
-----------------------------------------------
Checked empirically against ``poc/steamprefill/bin/SteamPrefill.exe`` while
writing this module — NOT assumed from docs:

- ``SteamPrefill.exe prefill --help`` offers ``--all``, ``--recent``,
  ``--recently-purchased``, ``--top``, ``-f|--force``, ``--os``, ``--verbose``,
  ``--unit``, ``--no-ansi``. **There is no ``--app-ids`` / ``--appid`` option
  and no positional app-id parameter**: ``SteamPrefill.exe prefill 480`` is
  rejected with "Unexpected parameter(s): <zzz>"-style output, and the binary
  contains no ``app-ids`` string at all.
- The app selection ``prefill`` consumes is a **state file**:
  ``<exe dir>/Config/selectedAppsToPrefill.json``, a plain JSON array of app
  ids (observed content: ``[3419430]``). ``select-apps`` is only the
  interactive TUI that writes it; ``select-apps status`` reads it back and
  listed exactly that one app, confirming the file is the selection store.
- So the non-interactive way to prefill a specific app id is: **write
  ``Config/selectedAppsToPrefill.json`` = ``[appid]``, then run
  ``SteamPrefill.exe prefill [--force] --no-ansi``.** That is what this module
  does. Consequence, documented in api/README.md: vault-api OWNS that file —
  a manual ``select-apps`` selection on the same SteamPrefill installation
  gets overwritten on the next job.
- ``--force`` is deliberate, but (WP 3.4, ADR-0006 decision 2) no longer
  unconditional. Without it SteamPrefill skips apps its own
  ``Config/successfullyDownloadedDepots.json`` considers up to date — state
  that knows nothing about vault-api deleting an app from the cache
  (``DELETE /v1/cache/{appid}``, WP 1.6), so a non-forced run would silently
  refuse to re-fill a game we just deleted. Chunks still present on disk are
  re-requested and served by vault-core as local HITs, so the cost of
  ``--force`` is disk speed, not internet bandwidth (Phase 0 measured
  HIT ~120x faster than MISS, ADR-0001) — which is why it remains the right
  tool for first fills and post-deletion refills specifically, rather than
  something to avoid altogether. What changed is *when* it is applied: a
  per-app ``apps.needs_force`` flag (schema v5) now reserves it for exactly
  those two cases, so a routine "is this app still current?" run (the
  staleness check ADR-0006 decision 1 is built around) is a genuinely cheap
  non-forced no-op instead of always re-touching every chunk on disk. See
  ``worker.py`` for who reads/clears the flag and ``deletion.py`` for who sets
  it, and api/README.md's "needs_force lifecycle" table for the full state
  machine.
- ``--no-ansi`` is passed but is **not sufficient**: Spectre.Console's
  exception renderer still emits SGR escapes (observed). Captured output is
  therefore stripped of ANSI escapes here before being stored.
- ``--max-threads N`` (WP CORE-FIX-2) is a HIDDEN flag: absent from
  ``--help``, pre-parsed and stripped from the argv in ``Program.cs``
  (``ParseHiddenFlags``) before CliFx sees it, and it sets the cap on
  concurrent chunk requests (default 30). Measured against the pinned linux-x64
  binary: ``prefill --force --no-ansi --max-threads 8`` logs "Using
  --max-threads flag. Will download using at most 8 threads" and then parses
  normally; an option CliFx does not know (``--bogus 8``) exits 1 with
  "Unrecognized option(s)". ``--help`` ignores unknown options (exit 0), so a
  help run alone does not prove the flag exists; the build probe in
  ``api/Dockerfile`` checks for the log line instead.
- Not logged in, stdin closed: verified against a fresh copy of the binary
  with an empty ``Config/``. It does **not** hang — it prints "A Steam account
  is required in order to prefill apps!" / "Please enter your Steam account
  name :" and dies with
  ``InvalidOperationException: Failed to read input in non-interactive mode.``,
  exit code 1, in about a second. Login happens *before* cache detection, so
  this is the first thing that fails on an unconfigured install. The timeout
  below is still enforced as a backstop (a Steam Guard prompt or a wedged
  network connection is not something this was able to prove fast-fails).
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable

from vault_api.config import (
    DEFAULT_PREFILL_MAX_THREADS,
    MAX_PREFILL_MAX_THREADS,
    MIN_PREFILL_MAX_THREADS,
)
from vault_api.mapping import delete_mapping, upsert_mapping
from vault_api.sizes import DepotSignature, scan_depot_signatures

logger = logging.getLogger(__name__)

#: Name of SteamPrefill's own selected-apps state file, relative to the
#: directory holding the executable.
SELECTED_APPS_RELPATH = os.path.join("Config", "selectedAppsToPrefill.json")

#: Substrings that identify the "no Steam session, cannot prompt" failure.
#: Taken verbatim from the observed run described in the module docstring.
NOT_LOGGED_IN_MARKERS = (
    "a steam account is required",
    "failed to read input in non-interactive mode",
    "please enter your steam account name",
)

NOT_LOGGED_IN_HINT = (
    "SteamPrefill has no usable Steam session, and vault-api runs it "
    "non-interactively (stdin closed), so it cannot prompt for credentials. "
    "Log in once by hand on the server: run 'SteamPrefill select-apps' in a "
    "terminal, enter your account name/password/Steam Guard code, then retry "
    "this job. The session is stored next to the executable in Config/ and is "
    "reused afterwards. vault-api never sees or stores Steam credentials."
)

#: How often the subprocess is polled for exit / timeout / abort.
_POLL_INTERVAL_SECONDS = 0.2

#: Grace period between terminate() and kill() when aborting a subprocess.
_TERMINATE_GRACE_SECONDS = 5.0

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (``--no-ansi`` does not cover all output)."""
    return _ANSI_ESCAPE_RE.sub("", text)


# --------------------------------------------------------------------------
# Running SteamPrefill
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PrefillResult:
    """Outcome of one SteamPrefill invocation. Failures are data, not exceptions."""

    success: bool
    #: 'not_logged_in' | 'timeout' | 'aborted' | 'cancelled' | 'paused' |
    #: 'exit_code' | 'setup' | None. ``'cancelled'``/``'paused'`` (WP 3.12) are
    #: operator-requested stops and are NOT failures in the ordinary sense —
    #: ``success`` is still ``False`` (the run did not complete), but
    #: ``worker.py`` routes them to their own terminal/parked outcomes instead
    #: of ``error``.
    failure_reason: str | None
    exit_code: int | None
    #: ANSI-stripped combined stdout+stderr, plus any diagnostic vault-api adds.
    output: str


def resolve_executable(steamprefill_path: str) -> tuple[str | None, str | None]:
    """Validate the configured SteamPrefill path. Returns ``(path, error)``.

    Returning an error instead of raising is the point: a box without
    SteamPrefill set up must still serve the rest of the API (plan §6), so this
    turns into a per-job failure, never a startup failure.
    """
    if not steamprefill_path:
        return None, (
            "VAULT_STEAMPREFILL_PATH is not set, so vault-api has no SteamPrefill "
            "executable to run. Set it to the full path of SteamPrefill(.exe) and "
            "restart vault-api (see api/README.md)."
        )
    if not os.path.isfile(steamprefill_path):
        return None, (
            f"VAULT_STEAMPREFILL_PATH points at {steamprefill_path!r}, which is not "
            "an existing file. Fix the path and restart vault-api."
        )
    return steamprefill_path, None


def write_selected_apps(executable: str, appid: int) -> str | None:
    """Point SteamPrefill's selection at exactly ``[appid]``. Returns an error string or None.

    This is the verified non-interactive selection mechanism (see module
    docstring). vault-api overwrites the file wholesale on every job — one
    appid per job, one job at a time, so the file always describes the job
    that is about to run.

    **Atomic write (WP 1.5 carry-over fix from the WP 1.4 review):** the file
    is written to a tempfile in the SAME directory, then moved into place with
    ``os.replace``. A same-directory tempfile guarantees the replace is a
    same-filesystem rename (atomic on both POSIX and Windows), so nothing that
    reads ``selectedAppsToPrefill.json`` — SteamPrefill itself, or a future
    concurrent inspection — can ever observe a partially-written file. Plain
    ``open(path, "w")`` truncates in place first, which has no such guarantee.
    """
    config_dir = os.path.join(os.path.dirname(os.path.abspath(executable)), "Config")
    path = os.path.join(config_dir, os.path.basename(SELECTED_APPS_RELPATH))
    try:
        os.makedirs(config_dir, exist_ok=True)
    except OSError as exc:
        return (
            f"Could not create {config_dir!r}: {exc}. vault-api needs write access "
            "to the Config/ directory next to the SteamPrefill executable."
        )

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=config_dir, prefix="selectedAppsToPrefill-", suffix=".tmp"
        )
    except OSError as exc:
        return (
            f"Could not create a temporary file in {config_dir!r}: {exc}. "
            "vault-api needs write access to the Config/ directory next to the "
            "SteamPrefill executable — that file is how a specific app id is "
            "selected non-interactively."
        )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump([appid], handle)
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:  # pragma: no cover - best effort cleanup
            pass
        return (
            f"Could not write SteamPrefill's app selection to {path!r}: {exc}. "
            "vault-api needs write access to the Config/ directory next to the "
            "SteamPrefill executable — that file is how a specific app id is "
            "selected non-interactively."
        )
    return None


#: WP 3.12: ``PrefillResult.failure_reason`` values that mean "an operator
#: stopped this", as opposed to "this went wrong". ``worker.py`` matches on
#: these constants rather than on string literals.
FAILURE_CANCELLED = "cancelled"
FAILURE_PAUSED = "paused"
STOP_FAILURE_REASONS = (FAILURE_CANCELLED, FAILURE_PAUSED)

#: The ``jobs.stop_request`` values this runner understands, mapped to the
#: ``failure_reason`` each produces. Anything else a callback returns is
#: ignored — an unrecognized request must not silently kill a download.
_STOP_REASONS = {"cancel": FAILURE_CANCELLED, "pause": FAILURE_PAUSED}

#: Default wording appended to an ``'aborted'`` result — true for the
#: in-process (subprocess-mode) caller, whose ``should_abort`` is vault-api's
#: own stop event. Queue mode passes its own (WP API-FIX-1, P4): there the
#: event belongs to the separate ``prefill_runner`` process, and a log line
#: blaming vault-api's shutdown for a runner SIGTERM sent operators to the
#: wrong container.
ABORT_REASON_VAULT_API = "vault-api is shutting down"


def run_prefill(
    appid: int,
    steamprefill_path: str,
    timeout_seconds: int,
    should_abort: Callable[[], bool] | None = None,
    use_force: bool = True,
    stop_request: Callable[[], str | None] | None = None,
    abort_reason: str = ABORT_REASON_VAULT_API,
    max_threads: int = DEFAULT_PREFILL_MAX_THREADS,
) -> PrefillResult:
    """Run SteamPrefill for one appid. Never raises for a prefill failure.

    ``should_abort`` is polled while waiting; when it returns True (the
    calling process is shutting down) the subprocess is terminated and the
    result is a failure with reason ``'aborted'``. Without that, ``docker
    stop`` would hang until the prefill finished or the runtime SIGKILLed the
    container. ``abort_reason`` is the human sentence recorded with that
    outcome — it names WHOSE shutdown it was, which differs between the two
    processes that call this function (see ``ABORT_REASON_VAULT_API``).

    **The callbacks may raise, and the child is reaped anyway (WP API-FIX-1,
    P2).** ``stop_request`` in queue mode reads the shared database on
    every tick; a ``sqlite3.Error`` escaping it used to unwind
    ``_wait_for_process`` past the terminate/wait step, leaving SteamPrefill
    running unreaped behind a runner that had already given up on it.
    ``_wait_for_process`` now stops the child before re-raising ANY exception
    from the wait loop — the exception still propagates (it is a bug or an
    infrastructure failure, not a prefill outcome), but never with a live
    orphan behind it.

    ``stop_request`` (WP 3.12) is polled on the same tick and answers "did an
    operator ask for something?" — ``'cancel'``, ``'pause'`` or ``None``. Both
    outcomes terminate the subprocess exactly the same way ``should_abort``
    does; only the reported ``failure_reason`` differs, because what the JOB
    then becomes differs (``cancelled`` vs ``paused`` vs ``error``).

    **Two callbacks rather than one, and shutdown wins.** They answer different
    questions from different owners — process lifetime (the worker's stop
    event) versus operator intent (a database flag) — and merging them would
    mean encoding one into the other's return value. When both fire in the same
    tick, ``should_abort`` is checked first: a container that is going down must
    stop the child regardless, and an interrupted shutdown leaves a ``running``
    row that ``jobs.recover_stale_jobs`` cleans up, whereas parking a job as
    ``paused`` during shutdown would promise a resume the process is not around
    to honor.

    **There is no pause signal in this design because SteamPrefill has none.**
    Pause terminates the child; resume runs it again from the start. That is
    affordable only because of what vault-core does with the bytes already on
    disk: the re-run's requests for cached chunks are local HITs (ADR-0001
    measured ~120x faster than a miss), so the cache is the progress store.

    ``use_force`` (WP 3.4, ADR-0006 decision 2) controls whether ``--force`` is
    included in the argv at all. Defaults to ``True`` — the pre-WP-3.4
    behavior this module always had — for any direct caller that doesn't care;
    ``worker.py`` always passes it explicitly, computed from the app's
    ``apps.needs_force`` flag (``jobs.get_app_needs_force``). See the module
    docstring's "``--force`` is deliberate" note for *why* forcing exists at
    all — that reasoning is unchanged, only *when* it is applied is now
    conditional instead of unconditional.

    ``max_threads`` (WP CORE-FIX-2) is passed as SteamPrefill's hidden
    ``--max-threads N`` flag on every run, after the documented options --
    the argv shape measured against the pinned binary (it logs "Will download
    using at most N threads" and parses the rest normally). Both production
    callers pass ``Settings.prefill_max_threads`` explicitly; the default only
    serves direct callers such as tests. An out-of-range value or a bool is a
    caller bug (``Settings.from_env`` already refused it at boot), so it
    raises ``ValueError`` instead of becoming a job outcome.
    """
    if (
        isinstance(max_threads, bool)
        or not isinstance(max_threads, int)
        or not MIN_PREFILL_MAX_THREADS <= max_threads <= MAX_PREFILL_MAX_THREADS
    ):
        raise ValueError(
            f"max_threads must be an int in {MIN_PREFILL_MAX_THREADS}.."
            f"{MAX_PREFILL_MAX_THREADS}, got {max_threads!r}"
        )
    executable, error = resolve_executable(steamprefill_path)
    if executable is None:
        return PrefillResult(False, "setup", None, error or "")

    error = write_selected_apps(executable, appid)
    if error is not None:
        return PrefillResult(False, "setup", None, error)

    command = [executable, "prefill"]
    if use_force:
        command.append("--force")
    command.append("--no-ansi")
    # WP CORE-FIX-2: bound SteamPrefill's concurrency (default 30 upstream)
    # so a prefill cannot exhaust a carrier-grade NAT's port mappings. The
    # flag is hidden (not in --help); api/Dockerfile's build probe fails the
    # image build if the pinned binary stops recognising it.
    # Keep it LAST: ParseHiddenFlags reads the element after "--max-threads"
    # as the count, then removes the first argv string equal to that count,
    # so an earlier token with the same text would be the one removed.
    command.extend(["--max-threads", str(max_threads)])
    workdir = os.path.dirname(os.path.abspath(executable))

    # Output goes to a temp FILE rather than a pipe on purpose: a prefill run
    # can emit a lot of progress output, and a PIPE that nobody drains while we
    # poll for the timeout/abort conditions would deadlock on a full OS pipe
    # buffer. A file also makes reading just the tail trivial.
    #
    # Opened in BINARY mode deliberately (WP 3.3 cleanup): this handle is
    # handed to Popen as ``stdout=``, which redirects the CHILD PROCESS's raw
    # bytes straight into the underlying OS file descriptor (``handle.fileno()``)
    # -- the parent never goes through this Python file object's own text
    # encoding/decoding machinery, so an ``encoding=``/``errors=`` kwarg here
    # (the previous version passed ``encoding="utf-8", errors="replace"``) is a
    # complete no-op for what actually lands on disk and was misleading about
    # where the real decode happens. That is ``_read_text`` below, which is
    # also where WP 3.3's actual decode fix lives (see its docstring).
    handle = tempfile.NamedTemporaryFile(
        mode="w+b", suffix=".log",
        prefix=f"vault-prefill-{appid}-", delete=False,
    )
    log_path = handle.name
    try:
        with handle:
            try:
                process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    command,
                    cwd=workdir,
                    # stdin closed: SteamPrefill prompts interactively when it
                    # has no session; DEVNULL makes that fail fast instead of
                    # blocking forever on a prompt nobody can answer.
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
            except OSError as exc:
                return PrefillResult(
                    False, "setup", None,
                    f"Could not start {command[0]!r}: {exc}",
                )

            outcome, exit_code = _wait_for_process(
                process, timeout_seconds, should_abort, stop_request
            )

        output = strip_ansi(_read_text(log_path))

        if outcome == "timeout":
            return PrefillResult(
                False, "timeout", exit_code,
                output
                + f"\n[vault-api] SteamPrefill exceeded the {timeout_seconds}s time "
                "budget (VAULT_PREFILL_TIMEOUT_SECONDS) and was killed.",
            )
        if outcome == "aborted":
            return PrefillResult(
                False, "aborted", exit_code,
                output + f"\n[vault-api] Aborted: {abort_reason}.",
            )
        if outcome == "cancelled":
            return PrefillResult(
                False, "cancelled", exit_code,
                output
                + "\n[vault-api] Cancelled on request: SteamPrefill was "
                "terminated. Everything it had already written stays in the "
                "cache and is served as local HITs by any later run.",
            )
        if outcome == "paused":
            return PrefillResult(
                False, "paused", exit_code,
                output
                + "\n[vault-api] Paused on request: SteamPrefill was "
                "terminated (it has no pause signal). Resuming re-runs it; "
                "every chunk already on disk is a local HIT, so the cache is "
                "the progress store.",
            )

        if _looks_not_logged_in(output):
            return PrefillResult(
                False, "not_logged_in", exit_code,
                output + "\n[vault-api] " + NOT_LOGGED_IN_HINT,
            )
        if exit_code != 0:
            return PrefillResult(
                False, "exit_code", exit_code,
                output + f"\n[vault-api] SteamPrefill exited with code {exit_code}.",
            )
        return PrefillResult(True, None, exit_code, output)
    finally:
        try:
            os.unlink(log_path)
        except OSError:  # pragma: no cover - best effort cleanup
            logger.warning("Could not remove temporary prefill log %s", log_path)


def _wait_for_process(
    process: "subprocess.Popen[str]",
    timeout_seconds: int,
    should_abort: Callable[[], bool] | None,
    stop_request: Callable[[], str | None] | None = None,
) -> tuple[str, int | None]:
    """Poll until exit / timeout / abort / operator stop.

    Returns ``(outcome, exit_code)`` where outcome is ``'exited'``,
    ``'timeout'``, ``'aborted'``, ``'cancelled'`` or ``'paused'``.

    **``process.poll()`` is checked first on every tick, and that ordering is
    the answer to the "cancel raced the finish line" question (WP 3.12):** a
    subprocess that already exited yields ``'exited'`` with its real exit code
    even if a cancel or pause request is pending. The run genuinely completed,
    so the job keeps its real outcome and the worker only notes in the log that
    the request arrived too late. Rewriting a finished download as ``cancelled``
    would throw away the mapping/manifest work it earned.

    **Any exception out of the loop stops the child first (WP API-FIX-1,
    P2).** ``should_abort``/``stop_request`` are caller-supplied and may
    raise (queue mode's ``stop_request`` talks to SQLite); an exception is
    re-raised unchanged, but only after ``_stop_process`` has terminated and
    reaped the subprocess — the same "never an orphan SteamPrefill" promise
    the pause path already makes. A child that already exited is left alone
    (checked with ``poll()`` first, so nothing signals a reaped pid).
    """
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                return "exited", exit_code
            if should_abort is not None and should_abort():
                return "aborted", _stop_process(process)
            if stop_request is not None:
                reason = _STOP_REASONS.get(stop_request() or "")
                if reason is not None:
                    # _stop_process() terminates and WAITS (then kills and
                    # waits again), so by the time this returns the child is
                    # reaped — which is what makes "a paused job never has an
                    # orphan SteamPrefill behind it" true rather than hopeful.
                    return reason, _stop_process(process)
            if time.monotonic() >= deadline:
                return "timeout", _stop_process(process)
            time.sleep(_POLL_INTERVAL_SECONDS)
    except BaseException:
        if process.poll() is None:
            _stop_process(process)
        raise


def _stop_process(process: "subprocess.Popen[str]") -> int | None:
    """terminate(), then kill() if it doesn't go away. Returns the exit code."""
    process.terminate()
    try:
        return process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            return process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable process
            return None


def _windows_oem_encoding() -> str:
    """Best-effort guess at the encoding a redirected-to-file Windows console
    process actually wrote in.

    A console application that never explicitly reconfigures
    ``Console.OutputEncoding`` writes bytes in the process's OEM codepage, NOT
    UTF-8 -- this is codepage 850 on a German Windows install, verified
    elsewhere in this codebase (``api/README.md``'s agent-report note:
    "a Windows console at codepage 850 renders an em dash as a replacement
    character"; ``api/tests/conftest.py``'s ``mklink`` capture comment). It is
    a *different* codepage on other locales (437 on US Windows, for example),
    so this asks the OS for the real value (``GetOEMCP``) instead of hardcoding
    one -- decoding SteamPrefill's Spectre.Console box-drawing table with the
    wrong single-byte codepage would just trade one mojibake for another.
    """
    if os.name == "nt":
        try:
            codepage = ctypes.windll.kernel32.GetOEMCP()  # type: ignore[attr-defined]
            if codepage:
                return f"cp{codepage}"
        except Exception:  # pragma: no cover - defensive, non-Windows/odd host
            pass
        return "cp437"
    return "utf-8"


def _read_text(path: str) -> str:
    """Read the captured subprocess output, decoded correctly (WP 3.3 fix).

    **The bug this replaces:** the previous version always decoded as
    ``utf-8`` with ``errors="replace"``. SteamPrefill's Spectre.Console summary
    table uses Unicode box-drawing glyphs, but the child process actually wrote
    them in the OS's single-byte OEM codepage (see ``_windows_oem_encoding``)
    when its console output was redirected to a file rather than a real
    terminal -- decoding those bytes as UTF-8 fails on nearly every one of
    them (a lone high-bit byte is essentially never a valid UTF-8 continuation
    sequence), so ``errors="replace"`` silently turned every glyph into
    U+FFFD. That is exactly the corruption captured for real in
    ``core/tests/mvp/RESULTS-20260805-222046.md`` and
    ``RESULTS-20260805-223328.md``, which is why ``vault_api/prefill_summary``
    still has to parse around it defensively even after this fix (a stored job
    row from before this fix, or a host whose OEM codepage detection fails,
    still needs to be tolerated).

    **The fix:** try strict UTF-8 first (the common case for ASCII-only login
    lines, and correct as-is if SteamPrefill ever runs somewhere that really
    does write UTF-8, e.g. a Linux container). Only on a decode failure fall
    back to the OS's actual OEM codepage, which -- unlike UTF-8 -- has no
    invalid byte sequences on Windows, so it recovers the real glyphs instead
    of replacing them there.

    **This function must NEVER raise (review blocker).** On POSIX,
    ``_windows_oem_encoding()`` returns ``"utf-8"`` -- so the "fallback" is
    the exact same strict decode attempted again, which raises the exact
    same ``UnicodeDecodeError`` a second time on any real-world invalid UTF-8
    byte sequence. That is not a hypothetical: ``run_prefill``'s
    timeout/abort path ``terminate()``s (then ``kill()``s) the subprocess
    while it may be mid-write to this file, which can truncate a multi-byte
    UTF-8 sequence at the very end of the captured bytes -- turning an
    otherwise-successful download into a job that "crashes" reading back its
    own output, on Linux specifically (Windows OEM codepages are single-byte
    and have no invalid sequences, so this never bit there). The final
    ``errors="replace"`` decode is therefore not just a defensive fallback
    for an unknown codepage name (``LookupError``) -- it is the mandatory
    last resort for a *second* failed decode too (``UnicodeDecodeError``),
    which is why both exception types are caught around the second attempt.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:  # pragma: no cover - defensive
        return f"[vault-api] Could not read captured output: {exc}"

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    try:
        return raw.decode(_windows_oem_encoding())
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace")


def _looks_not_logged_in(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in NOT_LOGGED_IN_MARKERS)


# --------------------------------------------------------------------------
# Depot attribution (ADR-0003 decision 3)
# --------------------------------------------------------------------------

def scan_depots(cache_root: str) -> dict[int, DepotSignature]:
    """Signature per depot directory under ``<cache_root>/depot/<depotid>/``.

    Thin wrapper: the actual walk lives in ``vault_api.sizes.scan_depot_signatures``
    (WP 1.5 carry-over fix #1). It used to be duplicated here with
    ``os.walk`` + a per-file ``os.stat()`` call — replaced with a shared
    ``os.scandir`` + ``DirEntry.stat()`` walk (17x faster, measured) so the
    per-app size calculation (``vault_api/sizes.py``) and this module's
    before/after attribution diff use exactly one implementation, not two.
    Kept as a module-level function here (rather than importing
    ``scan_depot_signatures`` directly at call sites) so existing callers and
    tests that reference ``prefill.scan_depots`` keep working unchanged.
    """
    return scan_depot_signatures(cache_root)


def diff_depots(
    before: dict[int, DepotSignature], after: dict[int, DepotSignature]
) -> set[int]:
    """Depot ids that are new or changed between the two snapshots.

    Disappearing depots are ignored on purpose: this diff exists to attribute
    *filled* content to an app, and a depot removed during a prefill (only
    possible via a concurrent ``DELETE /v1/cache/...``, WP 1.6) was not filled
    by it.
    """
    return {depotid for depotid, signature in after.items() if before.get(depotid) != signature}


def apply_observed_mapping(
    conn: sqlite3.Connection, appid: int, observed: set[int]
) -> "MappingChange":
    """Replace ``appid``'s depot mapping with ``observed`` (ADR-0003 decision 3).

    Semantics, precisely:

    - **Replace within an app.** Depot rows currently mapped to ``appid`` that
      are NOT in ``observed`` are deleted, so a depot Steam reassigned away
      from this app stops being reported as its content (and stops blocking
      WP 1.6's deletion as a false "shared" depot).
    - **Additive across apps.** Only rows for *this* ``appid`` are touched. A
      depot in ``observed`` that another app also maps keeps that other
      mapping — that is plan §4's shared-depot case (redistributables), and
      both apps continue to report it as ``shared``.
    - **Empty observation changes nothing.** If the run filled no new bytes,
      ``observed`` is empty and the existing mapping is left completely alone.
      This is the common case for an already-fully-cached app: every chunk is
      served from disk as a HIT, nothing is written, so there is nothing to
      observe. Wiping the mapping there would delete correct data on the
      strength of no evidence at all, so it is explicitly not done. The
      trade-off is honest: a stale depot row only disappears once a prefill
      actually writes something for that app again (a game update), or via
      ``DELETE /v1/mapping/{depotid}/{appid}``.
    """
    if not observed:
        return MappingChange(added=set(), removed=set(), kept=set(), skipped_empty=True)

    existing = {
        int(row["depotid"])
        for row in conn.execute(
            "SELECT depotid FROM depot_app_map WHERE appid = ?", (appid,)
        ).fetchall()
    }

    stale = existing - observed
    for depotid in sorted(stale):
        delete_mapping(conn, depotid=depotid, appid=appid)

    for depotid in sorted(observed):
        upsert_mapping(conn, depotid=depotid, appid=appid, name=None)

    return MappingChange(
        added=observed - existing,
        removed=stale,
        kept=observed & existing,
        skipped_empty=False,
    )


@dataclass(frozen=True)
class MappingChange:
    """What ``apply_observed_mapping`` did, for the job log."""

    added: set[int] = field(default_factory=set)
    removed: set[int] = field(default_factory=set)
    kept: set[int] = field(default_factory=set)
    #: True when nothing was observed and the mapping was left untouched.
    skipped_empty: bool = False

    def summary(self) -> str:
        if self.skipped_empty:
            return (
                "[vault-api] No new or changed depot directories were observed during "
                "this run (everything requested was already cached), so the existing "
                "depot mapping for this app was left unchanged."
            )
        return (
            "[vault-api] Depot mapping updated (replace-semantics for this app): "
            f"added={sorted(self.added)} removed={sorted(self.removed)} "
            f"unchanged={sorted(self.kept)}"
        )
