"""vault-core's settings, seen from vault-api (D16b).

Three things live here:

1. **Python twins of the vault-core hook grammars** (D16 audit 3.4, 3.6):
   ``27-vault-upstream-rate.sh`` (rate, window), ``28-vault-upstream-pool.sh``
   (edge, pool hosts, global cap), ``26-vault-tls-passthrough.sh`` (TLS caps),
   ``40-vault-preflight.sh`` section 2 (resolver) and
   ``45-vault-eventlog-rotate.sh`` (rotation size). ``PATCH /v1/settings``
   validates a core key with these, so vault-api refuses what vault-core would
   refuse at render time. Twins drift; both sides are pinned by ONE fixture
   table, ``core/tests/fixtures/settings-grammar.tsv``, which
   ``api/tests/test_d16b_core_grammar.py`` runs here and the core shell tests
   run against the hooks (D16c adds the ``off`` keyword to the hooks and the
   shell runner for the table).

   Each parser returns ``None`` for "not set" (an empty value) and a
   normalised string otherwise. It raises ``CoreGrammarError`` for anything
   the hook would refuse. The normalised forms are the table's last column.

2. **The effective core values** (Q3): ``core_file_values`` computes what
   vault-api hands vault-core, including ``VAULT_UPSTREAM_RATE_WINDOW``
   derived from the effective download window (``off`` when the window is
   off). There is no separate rate window any more; the env variable of that
   name is deprecated (still honoured by vault-core as an env pin).

3. **The file writer** (audit 4.2), off unless ``VAULT_CORE_SETTINGS_FILE``
   names a path. D16c mounts the shared volume, sets the path and teaches
   vault-core to read the file.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Callable, Mapping

from vault_api.schedule_window import WINDOW_OFF, ScheduleWindowError, parse_window

logger = logging.getLogger(__name__)

#: The keyword for "off" (Q7), shared with ``schedule_window``.
OFF = WINDOW_OFF


class CoreGrammarError(ValueError):
    """A value vault-core's hook would refuse."""


# --------------------------------------------------------------------------
# Hook constants (mirrored, pinned by the fixture table and by
# test_d16b_core_grammar.py's literal checks against the hook sources)
# --------------------------------------------------------------------------

#: 27: smallest total, so total/1024 buckets never rounds to 0 (= unlimited).
RATE_MIN_TOTAL = 1024
RATE_MAX_DIGITS = 9
#: 28: the global upstream cap.
UPSTREAM_CAP_DEFAULT = 16
UPSTREAM_CAP_MIN = 1
UPSTREAM_CAP_MAX = 64
#: 28: legacy pool ceiling (32 idle / 8 per group).
POOL_MAX_HOSTS = 4
POOL_FAMILIES = ("steamcontent.com", "steamserver.net")
POOL_MARKER = "lancache.steamcontent.com"
#: 26: HTTPS passthrough caps.
TLS_CLIENT_DEFAULT = 64
TLS_CLIENT_MAX = 256
TLS_TOTAL_DEFAULT = 256
TLS_TOTAL_MAX = 400
#: 45: rotation size.
ROTATE_DEFAULT_BYTES = 64 * 1024 * 1024
ROTATE_MIN_BYTES = 1048576
ROTATE_MAX_DIGITS = 15
#: The shipped defaults compose supplies today and D16c moves into the hooks.
UPSTREAM_EDGE_DEFAULT = "dist-fra1.discovery.steamserver.net"
RESOLVER_DEFAULT = "1.1.1.1"

#: 27's ``strip``: the ASCII whitespace set ``str.isspace()`` accepts.
_ASCII_WS = " \t\n\v\f\r\x1c\x1d\x1e\x1f"
#: POSIX word splitting on the default IFS (28 tokenises lists with it).
_IFS_SPLIT = re.compile(r"[ \t\n]+")
_DIGITS = frozenset("0123456789")


def _is_digits(text: str) -> bool:
    return bool(text) and all(ch in _DIGITS for ch in text)


# --------------------------------------------------------------------------
# 27: VAULT_UPSTREAM_RATE and the window
# --------------------------------------------------------------------------


def parse_upstream_rate(raw: str) -> str | None:
    """27 ``parse_rate``. No whitespace strip (the hook does none).

    ``""`` -> not set; ``off`` -> ``"0"`` (no cap); ``800k`` -> ``"819200"``
    (bytes per second). Refused: anything outside digits plus ONE trailing
    k/K/m/M, a leading zero (``0`` included: write ``off``), more than 9
    digits, a total below 1024 B/s.
    """
    if raw == "":
        return None
    if raw == OFF:
        return "0"
    if any(ch not in "0123456789kKmM" for ch in raw):
        raise CoreGrammarError(
            f"{raw!r} is not a byte rate. Use digits with an optional k (x1024) "
            "or m (x1048576) suffix, e.g. 800k, or 'off' for no cap."
        )
    if raw[-1] in "kKmM":
        num, unit = raw[:-1], raw[-1].lower()
    else:
        num, unit = raw, ""
    if not _is_digits(num):
        raise CoreGrammarError(
            f"{raw!r}: the unit suffix must come last and only once (e.g. 800k or 2m)."
        )
    if num.startswith("0"):
        raise CoreGrammarError(
            f"{raw!r} starts with 0. Write 'off' for no cap; a leading zero is refused."
        )
    if len(num) > RATE_MAX_DIGITS:
        raise CoreGrammarError(f"{raw!r} has more than 9 digits. Use a k or m suffix.")
    total = int(num) * {"k": 1024, "m": 1048576, "": 1}[unit]
    if total < RATE_MIN_TOTAL:
        raise CoreGrammarError(
            f"{raw!r} = {total} bytes/s is below the minimum of {RATE_MIN_TOTAL}."
        )
    return str(total)


def parse_rate_window(raw: str) -> str | None:
    """27 ``parse_window`` (the file key ``VAULT_UPSTREAM_RATE_WINDOW``).

    Strips 27's ASCII whitespace set only; whitespace-only -> not set.
    ``off`` -> ``"off"`` (cap around the clock); a window -> its normalised
    ``HH:MM-HH:MM``. The window grammar is ``schedule_window.parse_window``,
    the one 27 already mirrors. Fail-closed divergence kept from 27: any
    non-ASCII character is refused (27 would not strip U+00A0, Python would).
    """
    text = raw.strip(_ASCII_WS)
    if text == "":
        return None
    if text == OFF:
        return OFF
    if not text.isascii():
        raise CoreGrammarError(f"{raw!r} contains non-ASCII characters.")
    try:
        return parse_window(text).normalized
    except ScheduleWindowError as exc:
        raise CoreGrammarError(str(exc)) from exc


# --------------------------------------------------------------------------
# 28: edge, pool hosts, global cap
# --------------------------------------------------------------------------


def check_host(host: str) -> str:
    """28 ``check_host``: one Steam edge host name, or ``CoreGrammarError``."""
    if ":" in host:
        raise CoreGrammarError(f"{host!r} carries a scheme or port; use the bare host name.")
    if any("A" <= ch <= "Z" for ch in host):
        raise CoreGrammarError(f"{host!r} contains uppercase; write it in lowercase.")
    if any(not (ch.isascii() and (ch.islower() or ch.isdigit() or ch in ".-")) for ch in host):
        raise CoreGrammarError(
            f"{host!r} is not a host name (allowed: labels of a-z 0-9 '-' joined by '.')."
        )
    if host.startswith(".") or host.endswith(".") or ".." in host:
        raise CoreGrammarError(f"{host!r} has an empty label.")
    if len(host) > 253:
        raise CoreGrammarError(f"{host!r} is longer than 253 characters.")
    for label in host.split("."):
        if len(label) > 63:
            raise CoreGrammarError(f"{host!r} has a label longer than 63 characters.")
    if host in POOL_FAMILIES:
        raise CoreGrammarError(f"{host!r} is a bare family name, not an edge.")
    if not any(host.endswith("." + family) for family in POOL_FAMILIES):
        raise CoreGrammarError(
            f"{host!r} is outside the allowed families (*.steamcontent.com, *.steamserver.net)."
        )
    if host == POOL_MARKER:
        raise CoreGrammarError(
            f"{host!r} is the client discovery marker, not an edge; use "
            "dist-fra1.discovery.steamserver.net."
        )
    return host


def _ifs_tokens(raw: str) -> list[str]:
    return [token for token in _IFS_SPLIT.split(raw) if token]


def parse_upstream_edge(raw: str) -> str | None:
    """28 ``EDGE_RAW``: no token -> not set (default edge); ``off`` ->
    ``"off"`` (legacy per-name pool, the ADR-0021 rollback switch); one host
    -> that host. More than one token is refused."""
    tokens = _ifs_tokens(raw)
    if not tokens:
        return None
    if len(tokens) > 1:
        raise CoreGrammarError(f"{raw!r} names {len(tokens)} hosts; it takes exactly one.")
    if tokens[0] == OFF:
        return OFF
    return check_host(tokens[0])


def parse_upstream_pool_hosts(raw: str) -> str | None:
    """28 ``HOSTS_RAW``: no token -> not set; exactly ``off`` -> ``"off"``
    (no pool hosts); else up to 4 distinct hosts, joined by one space."""
    tokens = _ifs_tokens(raw)
    if not tokens:
        return None
    if tokens == [OFF]:
        return OFF
    seen: list[str] = []
    for token in tokens:
        check_host(token)
        if token in seen:
            raise CoreGrammarError(f"{token!r} is listed twice.")
        seen.append(token)
    if len(seen) > POOL_MAX_HOSTS:
        raise CoreGrammarError(f"at most {POOL_MAX_HOSTS} hosts, got {len(seen)}.")
    return " ".join(seen)


def _parse_cap(raw: str, *, maximum: int, max_len: int) -> str | None:
    if raw == "":
        return None
    if not _is_digits(raw):
        raise CoreGrammarError(f"{raw!r} is not a whole number (digits only, 1..{maximum}).")
    if raw.startswith("0"):
        raise CoreGrammarError(f"{raw!r}: 0 and leading zeros are not accepted (1..{maximum}).")
    if len(raw) > max_len or int(raw) > maximum:
        raise CoreGrammarError(f"{raw} is outside 1..{maximum}.")
    return raw


def parse_upstream_max_conns(raw: str) -> str | None:
    """28 ``CAP_RAW``: 1..64, no leading zero, no off switch."""
    return _parse_cap(raw, maximum=UPSTREAM_CAP_MAX, max_len=2)


def parse_tls_client_max_conns(raw: str) -> str | None:
    """26 ``tls_cap`` for the per-client cap: 1..256."""
    return _parse_cap(raw, maximum=TLS_CLIENT_MAX, max_len=3)


def parse_tls_max_conns(raw: str) -> str | None:
    """26 ``tls_cap`` for the total cap: 1..400."""
    return _parse_cap(raw, maximum=TLS_TOTAL_MAX, max_len=3)


# --------------------------------------------------------------------------
# 40 section 2: VAULT_RESOLVER; 45: VAULT_EVENT_LOG_ROTATE_BYTES
# --------------------------------------------------------------------------

_RESOLVER_CHARS = frozenset("0123456789abcdefABCDEF.:[] -")


def parse_resolver(raw: str) -> str | None:
    """40 section 2: an IP address list (hex digits, ``.``, ``:``, ``[``,
    ``]``, space, ``-``). ``""`` -> not set (D16c's ``15-`` hook supplies
    1.1.1.1). Fail-closed divergence: a whitespace-only value is refused
    here (40 lets it through to an empty ``resolver`` directive)."""
    if raw == "":
        return None
    if any(ch not in _RESOLVER_CHARS for ch in raw):
        raise CoreGrammarError(
            f"{raw!r} contains characters that are not part of an IP address list "
            "(e.g. '1.1.1.1', '10.0.0.53 10.0.0.54' or '[2606:4700::1111]:53')."
        )
    if raw.strip(" ") == "":
        raise CoreGrammarError("the resolver list is empty.")
    return raw


def parse_event_log_rotate_bytes(raw: str) -> str | None:
    """45: 1..15 digits, leading zeros allowed; ``0`` = rotation off; else
    at least 1 MiB. Normalised without leading zeros."""
    if raw == "":
        return None
    if not _is_digits(raw) or len(raw) > ROTATE_MAX_DIGITS:
        raise CoreGrammarError(
            f"{raw!r} is not a whole number of bytes (digits only, at most 15; 0 = off)."
        )
    value = int(raw)
    if value != 0 and value < ROTATE_MIN_BYTES:
        raise CoreGrammarError(f"{raw!r} is below the minimum {ROTATE_MIN_BYTES} (1 MiB); 0 = off.")
    return str(value)


#: The fixture table's key column -> the twin. ``VAULT_SCHEDULE_WINDOW`` is
#: in the table too, but its parser lives in vault-api (``config``), see
#: test_d16b_core_grammar.py.
TWINS: dict[str, Callable[[str], str | None]] = {
    "VAULT_UPSTREAM_RATE": parse_upstream_rate,
    "VAULT_UPSTREAM_RATE_WINDOW": parse_rate_window,
    "VAULT_UPSTREAM_EDGE": parse_upstream_edge,
    "VAULT_UPSTREAM_POOL_HOSTS": parse_upstream_pool_hosts,
    "VAULT_UPSTREAM_MAX_CONNS": parse_upstream_max_conns,
    "VAULT_TLS_CLIENT_MAX_CONNS": parse_tls_client_max_conns,
    "VAULT_TLS_MAX_CONNS": parse_tls_max_conns,
    "VAULT_RESOLVER": parse_resolver,
    "VAULT_EVENT_LOG_ROTATE_BYTES": parse_event_log_rotate_bytes,
}


# --------------------------------------------------------------------------
# Cross-variable rules (audit 3.4), on effective values
# --------------------------------------------------------------------------


def check_cap_floor(core: Mapping[str, str | None], prefill_max_threads: int) -> None:
    """28: ``VAULT_UPSTREAM_MAX_CONNS >= VAULT_PREFILL_MAX_THREADS``.

    ``core`` maps env names to normalised values (absent or ``None`` = not
    set, the hook default 16 applies). ``prefill_max_threads`` is vault-api's
    effective value. Raises ``CoreGrammarError``.
    """
    cap_raw = core.get("VAULT_UPSTREAM_MAX_CONNS")
    cap = int(cap_raw) if cap_raw else UPSTREAM_CAP_DEFAULT
    if cap < prefill_max_threads:
        raise CoreGrammarError(
            f"upstream_max_conns ({cap}) must be at least prefill_max_threads "
            f"({prefill_max_threads}); a prefill alone would otherwise run into the cap."
        )


def check_tls_order(core: Mapping[str, str | None]) -> None:
    """26: ``VAULT_TLS_CLIENT_MAX_CONNS <= VAULT_TLS_MAX_CONNS`` (defaults
    64/256 when not set). Raises ``CoreGrammarError``."""
    client_raw = core.get("VAULT_TLS_CLIENT_MAX_CONNS")
    total_raw = core.get("VAULT_TLS_MAX_CONNS")
    client = int(client_raw) if client_raw else TLS_CLIENT_DEFAULT
    total = int(total_raw) if total_raw else TLS_TOTAL_DEFAULT
    if client > total:
        raise CoreGrammarError(
            f"tls_client_max_conns ({client}) must not be above tls_max_conns ({total})."
        )


# --------------------------------------------------------------------------
# The file (audit 4.2)
# --------------------------------------------------------------------------

#: Allowlist and order of the keys the file may carry.
FILE_KEYS = (
    "VAULT_UPSTREAM_RATE",
    "VAULT_UPSTREAM_RATE_WINDOW",
    "VAULT_UPSTREAM_EDGE",
    "VAULT_UPSTREAM_POOL_HOSTS",
    "VAULT_UPSTREAM_MAX_CONNS",
    "VAULT_PREFILL_MAX_THREADS",
    "VAULT_TLS_CLIENT_MAX_CONNS",
    "VAULT_TLS_MAX_CONNS",
    "VAULT_RESOLVER",
    "VAULT_EVENT_LOG_ROTATE_BYTES",
)
FILE_HEADER = "# generated by vault-api -- do not edit"


def rate_window_value(schedule_window_raw: str | None) -> str:
    """Q3: the core rate window IS the effective download window.

    ``schedule_window_raw`` is the effective window's normalised
    ``HH:MM-HH:MM``, or ``None`` when the download window is off; vault-core
    then caps around the clock (``off``).
    """
    return OFF if schedule_window_raw is None else schedule_window_raw


def core_file_values(
    stored_core: Mapping[str, str],
    schedule_window_raw: str | None,
    prefill_max_threads: int | None,
) -> dict[str, str]:
    """The key/value lines of the core settings file, in ``FILE_KEYS`` order.

    ``stored_core``: env name -> stored raw value for each core key with a
    Settings value that is not shadowed by vault-api's env (the caller
    decides; vault-core's own env still wins inside vault-core, audit 4.2).
    ``schedule_window_raw``: the EFFECTIVE download window (env, Settings or
    default), ``None`` = off. ``prefill_max_threads``: written only when it
    comes from Settings (``None`` otherwise), because vault-core reads the
    same env variable for its floor check.

    Every value is run through its twin again, so a value the hook would
    refuse can never reach the file.
    """
    values: dict[str, str] = {}
    window = rate_window_value(schedule_window_raw)
    for key in FILE_KEYS:
        if key == "VAULT_UPSTREAM_RATE_WINDOW":
            parse_rate_window(window)
            values[key] = window
        elif key == "VAULT_PREFILL_MAX_THREADS":
            if prefill_max_threads is not None:
                values[key] = str(prefill_max_threads)
        elif key in stored_core:
            raw = stored_core[key]
            if TWINS[key](raw) is None:
                continue
            values[key] = raw
    return values


def render_file(values: Mapping[str, str], generation: int) -> str:
    lines = [FILE_HEADER, f"GENERATION={generation}"]
    for key, value in values.items():
        if key not in FILE_KEYS or "\n" in value or "\r" in value:
            raise CoreGrammarError(f"refusing to write {key!r} into the core settings file")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _read_file(path: str) -> tuple[int, dict[str, str]]:
    """(generation, values) of an existing file; (0, {}) if absent or unusable."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return 0, {}
    generation = 0
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "GENERATION":
            generation = int(value) if _is_digits(value) else 0
        else:
            values[key] = value
    return generation, values


def write_core_settings_file(path: str, values: Mapping[str, str]) -> int | None:
    """Write ``values`` atomically (temp file in the same directory, then
    rename). Returns the new generation, or ``None`` when the content did not
    change (no write, so vault-core is not asked to reload for nothing).
    """
    generation, current = _read_file(path)
    if generation > 0 and current == dict(values):
        return None
    generation += 1
    content = render_file(values, generation)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".core-settings.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return generation
