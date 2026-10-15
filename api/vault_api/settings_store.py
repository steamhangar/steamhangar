"""Persisted settings — ADR-0009 with the D16b addendum (env always wins).

``GET``/``PATCH /v1/settings`` (``vault_api/routers/settings.py``) are the
HTTP surface; this module is everything underneath it: the catalog of which
settings may be changed from Settings, how a raw string PATCH value is
validated and turned into a typed value, how the ``settings`` table (schema
v13) is read and written, and how an env value, a stored Settings value and a
built-in default combine into one "effective" answer.

Precedence (ADR-0009 addendum 2026-10-14, D16b, user decision Q1): **env
value > Settings (DB) value > built-in default.** A key whose ``VAULT_*``
variable is set to a non-blank value is "set via env": a stored value for it
is ignored (one startup warning per shadowed key, ``warn_at_startup``), GET
reports it with ``editable: false``, and PATCH refuses it. Blank or unset
means "not set" (Q7). ``Settings.env_pinned`` carries the set of pinned
variable names from ``Settings.from_env``.

``effective_settings`` is the ONE accessor that resolves this for the
vault-api side. It is called at CALL TIME: the scheduler tick (schedule
keys, the event-sweep keys), the worker and runner per job (prefill threads
and timeout, manifest keep, GC grace), the request dependencies in
``vault_api/deps.py`` (agent report keep, bypass window, oracle timeout, the
stats/traffic views). Each key's ``applies`` label says which.

Keys with ``target == "core"`` have no ``Settings`` field: vault-core reads
them (``vault_api/core_settings.py`` validates them with Python twins of the
hook grammars and computes the core settings file). vault-api cannot see
vault-core's environment, so for those keys GET reports ``env_visible:
false``; D16c adds vault-core's status file, which reports the env pins.

Env-only keys are never in ``OVERRIDABLE_SPECS``. ``PATCH`` rejects them by
name with a distinct ``422`` detail (``ENV_ONLY_KEYS``). Since D16b (Q2)
``webhook_url`` is one of them; GET shows it masked (``mask_url``).

**WP 4h.0 addendum (ADR-0010).** ``relay_expose_playtime`` and
``relay_expose_last_played`` are env-only for a privacy reason (a lost DB
volume must not silently re-enable a data flow); see ADR-0010.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from vault_api import config
from vault_api import core_settings
from vault_api import jobs
from vault_api.config import Settings
from vault_api.core_settings import CoreGrammarError
from vault_api.schedule_window import (
    ScheduleWindow,
    ScheduleWindowError,
    parse_window,
    parse_window_or_off,
)

logger = logging.getLogger(__name__)


class SettingValidationError(ValueError):
    """A ``PATCH /v1/settings`` value that fails its key's grammar."""


# --------------------------------------------------------------------------
# Per-key specs
# --------------------------------------------------------------------------

TARGET_API = "api"
TARGET_CORE = "core"
TARGET_BOTH = "api+core"


@dataclass(frozen=True)
class SettingSpec:
    """Everything ``GET``/``PATCH /v1/settings`` needs to know about one
    editable key. ``key`` is the JSON key and, for ``api``/``api+core``
    targets, the ``Settings`` attribute name."""

    key: str
    env_var: str
    #: "immediately" | "next_sweep" | "next_job" | "core" | "restart-required"
    applies: str
    secret: bool
    #: The built-in default. Typed for api keys (matches ``Settings``), the
    #: JSON form for core keys (``None`` = the hook's "nothing").
    default: Any
    #: Raw stored string -> typed value (api) or canonical string (core).
    #: Raises ``SettingValidationError``.
    parse: Callable[[str], Any]
    #: Typed value -> JSON-safe value.
    to_json: Callable[[Any], Any]
    target: str = TARGET_API
    #: PATCH needs ``confirm_risky=true`` (D16a table sign-off 2026-10-14:
    #: the network/load caps and the debug knobs except the log level).
    risky: bool = False
    #: Shown in the expert area of the Settings page (D16a sign-off):
    #: network/load keys, debug keys and housekeeping. ``False`` = normal.
    expert: bool = False
    #: Short grammar hint for the UI.
    grammar: str = ""


#: Re-exported for callers/tests; the rule itself lives in ``config``.
MAX_VAULT_NAME_LENGTH = config.MAX_VAULT_NAME_LENGTH

_BLANK_DETAIL = "must not be blank; use null to clear the stored value instead of an empty string"


def _wrap(parser: Callable[[str], Any]) -> Callable[[str], Any]:
    """Turn a config grammar's ``ValueError`` into ``SettingValidationError``."""

    def parse(raw: str) -> Any:
        try:
            return parser(raw)
        except SettingValidationError:
            raise
        except ValueError as exc:
            raise SettingValidationError(str(exc)) from exc

    return parse


def _non_blank(parser: Callable[[str], Any]) -> Callable[[str], Any]:
    def parse(raw: str) -> Any:
        if not raw.strip():
            raise SettingValidationError(_BLANK_DETAIL)
        return parser(raw)

    return _wrap(parse)


def _int(minimum: int = 1, maximum: int | None = None) -> Callable[[str], int]:
    return _non_blank(
        lambda raw: config.parse_strict_int(raw, minimum=minimum, maximum=maximum)
    )


_float = _non_blank(config.parse_strict_float)


def _parse_schedule_window_override(raw: str) -> ScheduleWindow | None:
    """``off`` -> ``None`` (no window, Q7), else a window.

    A blank STORED value is still read as ``None``: before rc18 that was how
    a Settings change spelled "scheduler off", and such rows exist. A blank
    PATCH value is stored as ``off`` by the router (review decision B2).
    """
    text = raw.strip()
    if not text:
        return None
    try:
        return parse_window_or_off(text)
    except ScheduleWindowError as exc:
        raise SettingValidationError(str(exc)) from exc


def _window_to_json(window: ScheduleWindow | None) -> str:
    """``off`` for no window (D16b): the JSON value is always a string the
    PATCH grammar accepts back."""
    return core_settings.OFF if window is None else window.raw


def _events_to_json(events: frozenset[str]) -> list[str]:
    return sorted(events)


def _core(twin: Callable[[str], str | None]) -> Callable[[str], str]:
    """A core key's PATCH grammar: the twin, blank refused, canonical value
    returned (the twin's normalised form, except the rate, whose raw
    ``800k`` spelling is kept because it is what the operator wrote)."""

    def parse(raw: str) -> str:
        try:
            normalised = twin(raw)
        except CoreGrammarError as exc:
            raise SettingValidationError(str(exc)) from exc
        if normalised is None:
            raise SettingValidationError(_BLANK_DETAIL)
        if twin is core_settings.parse_upstream_rate:
            return raw
        return normalised

    return parse


def _identity(value: Any) -> Any:
    return value


_INT_HINT = "whole number, ASCII digits"

#: Every key editable from Settings (D16 audit 3.1/3.2), in display order.
OVERRIDABLE_SPECS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            "vault_name", "VAULT_NAME", "restart-required", False, "",
            _wrap(config.parse_vault_name), _identity,
            grammar=f"text, at most {config.MAX_VAULT_NAME_LENGTH} printable characters",
        ),
        SettingSpec(
            "schedule_window", "VAULT_SCHEDULE_WINDOW", "next_sweep", False,
            parse_window(config.DEFAULT_SCHEDULE_WINDOW),
            _parse_schedule_window_override, _window_to_json,
            target=TARGET_BOTH,
            grammar="HH:MM-HH:MM (overnight allowed, 24:00 as end), or off",
        ),
        SettingSpec(
            "schedule_interval_minutes", "VAULT_SCHEDULE_INTERVAL_MINUTES", "next_sweep",
            False, config.DEFAULT_SCHEDULE_INTERVAL_MINUTES,
            _int(1, config.MAX_INTERVAL_MINUTES), _identity,
            grammar=f"{_INT_HINT}, 1..{config.MAX_INTERVAL_MINUTES}",
            expert=True,
        ),
        SettingSpec(
            "schedule_client_stale_days", "VAULT_SCHEDULE_CLIENT_STALE_DAYS", "next_sweep",
            False, config.DEFAULT_SCHEDULE_CLIENT_STALE_DAYS,
            _int(1, config.MAX_DAYS), _identity,
            grammar=f"{_INT_HINT}, 1..{config.MAX_DAYS}",
            expert=True,
        ),
        SettingSpec(
            "sweep_include_cached", "VAULT_SWEEP_INCLUDE_CACHED", "next_sweep", False,
            config.DEFAULT_SWEEP_INCLUDE_CACHED,
            _non_blank(config.parse_strict_bool), _identity,
            grammar="true/false (also yes/no, on/off, 1/0)",
            expert=True,
        ),
        SettingSpec(
            "auto_gc", "VAULT_AUTO_GC", "immediately", False, config.DEFAULT_AUTO_GC,
            _non_blank(config.parse_auto_gc), _identity,
            grammar="off, dry-run or execute",
        ),
        SettingSpec(
            "gc_grace_days", "VAULT_GC_GRACE_DAYS", "next_job", False,
            config.DEFAULT_GC_GRACE_DAYS, _int(0, config.MAX_DAYS), _identity,
            grammar=f"{_INT_HINT}, 0..{config.MAX_DAYS} (0 = no grace)",
            expert=True,
        ),
        SettingSpec(
            "manifest_keep", "VAULT_MANIFEST_KEEP", "next_job", False,
            config.DEFAULT_MANIFEST_KEEP, _int(config.MIN_MANIFEST_KEEP), _identity,
            grammar=f"{_INT_HINT}, at least {config.MIN_MANIFEST_KEEP}",
            expert=True,
        ),
        SettingSpec(
            "agent_report_keep", "VAULT_AGENT_REPORT_KEEP", "immediately", False,
            config.DEFAULT_AGENT_REPORT_KEEP, _int(config.MIN_AGENT_REPORT_KEEP), _identity,
            grammar=f"{_INT_HINT}, at least {config.MIN_AGENT_REPORT_KEEP}",
            expert=True,
        ),
        SettingSpec(
            "client_stats_keep", "VAULT_CLIENT_STATS_KEEP", "next_sweep", False,
            config.DEFAULT_CLIENT_STATS_KEEP, _int(1), _identity,
            grammar=f"{_INT_HINT}, at least 1",
            expert=True,
        ),
        SettingSpec(
            "event_sweep_interval_minutes", "VAULT_EVENT_SWEEP_INTERVAL_MINUTES", "next_sweep",
            False, config.DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES,
            _int(1, config.MAX_INTERVAL_MINUTES), _identity,
            grammar=f"{_INT_HINT}, 1..{config.MAX_INTERVAL_MINUTES}",
            expert=True,
        ),
        SettingSpec(
            "miss_trigger_cooldown_minutes", "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES",
            "next_sweep", False, config.DEFAULT_MISS_TRIGGER_COOLDOWN_MINUTES,
            _int(0, config.MAX_COOLDOWN_MINUTES), _identity,
            grammar=f"{_INT_HINT}, 0..{config.MAX_COOLDOWN_MINUTES} (0 = trigger off)",
            expert=True,
        ),
        SettingSpec(
            "miss_trigger_max_per_sweep", "VAULT_MISS_TRIGGER_MAX_PER_SWEEP", "next_sweep",
            False, config.DEFAULT_MISS_TRIGGER_MAX_PER_SWEEP, _int(1), _identity,
            grammar=f"{_INT_HINT}, at least 1",
            expert=True,
        ),
        SettingSpec(
            "bypass_window_days", "VAULT_BYPASS_WINDOW_DAYS", "immediately", False,
            config.DEFAULT_BYPASS_WINDOW_DAYS, _int(1, config.MAX_DAYS), _identity,
            grammar=f"{_INT_HINT}, 1..{config.MAX_DAYS}",
        ),
        SettingSpec(
            "prefill_max_threads", "VAULT_PREFILL_MAX_THREADS", "next_job", False,
            config.DEFAULT_PREFILL_MAX_THREADS,
            _int(config.MIN_PREFILL_MAX_THREADS, config.MAX_PREFILL_MAX_THREADS), _identity,
            target=TARGET_BOTH, risky=True, expert=True,
            grammar=(
                f"{_INT_HINT}, {config.MIN_PREFILL_MAX_THREADS}.."
                f"{config.MAX_PREFILL_MAX_THREADS}, at most upstream_max_conns"
            ),
        ),
        SettingSpec(
            "prefill_timeout_seconds", "VAULT_PREFILL_TIMEOUT_SECONDS", "next_job", False,
            config.DEFAULT_PREFILL_TIMEOUT_SECONDS, _int(1), _identity,
            grammar=f"{_INT_HINT}, seconds, at least 1",
            expert=True,
        ),
        SettingSpec(
            "manifest_oracle_timeout", "VAULT_MANIFEST_ORACLE_TIMEOUT", "immediately", False,
            config.DEFAULT_MANIFEST_ORACLE_TIMEOUT, _float, _identity,
            grammar="positive number of seconds, e.g. 10 or 2.5",
            expert=True,
        ),
        SettingSpec(
            "webhook_events", "VAULT_WEBHOOK_EVENTS", "restart-required", False,
            frozenset(config.WEBHOOK_EVENTS_ALL),
            _wrap(config.parse_webhook_events), _events_to_json,
            grammar="comma-separated event names (or a JSON list); blank = all",
        ),
        SettingSpec(
            "webhook_timeout_seconds", "VAULT_WEBHOOK_TIMEOUT_SECONDS", "restart-required",
            False, config.DEFAULT_WEBHOOK_TIMEOUT_SECONDS, _float, _identity,
            grammar="positive number of seconds, e.g. 5",
        ),
        SettingSpec(
            "steam_library_steamid", "VAULT_STEAM_LIBRARY_STEAMID", "immediately", False, "",
            _wrap(config.parse_steam_library_steamid), _identity,
            grammar="SteamID64 (17 digits) as a string; blank = not set",
        ),
        # --- vault-core (applied through the core settings file, D16c) ----
        SettingSpec(
            "upstream_rate", "VAULT_UPSTREAM_RATE", "core", False, None,
            _core(core_settings.parse_upstream_rate), _identity, target=TARGET_CORE,
            expert=True,
            grammar="bytes/s with optional k or m suffix (800k, 2m), min 1024; off = no cap",
        ),
        SettingSpec(
            "upstream_max_conns", "VAULT_UPSTREAM_MAX_CONNS", "core", False,
            str(core_settings.UPSTREAM_CAP_DEFAULT),
            _core(core_settings.parse_upstream_max_conns), _identity, target=TARGET_CORE,
            risky=True, expert=True,
            grammar="whole number 1..64, no leading zero, at least prefill_max_threads",
        ),
        SettingSpec(
            "tls_client_max_conns", "VAULT_TLS_CLIENT_MAX_CONNS", "core", False,
            str(core_settings.TLS_CLIENT_DEFAULT),
            _core(core_settings.parse_tls_client_max_conns), _identity, target=TARGET_CORE,
            risky=True, expert=True,
            grammar="whole number 1..256, no leading zero, at most tls_max_conns",
        ),
        SettingSpec(
            "tls_max_conns", "VAULT_TLS_MAX_CONNS", "core", False,
            str(core_settings.TLS_TOTAL_DEFAULT),
            _core(core_settings.parse_tls_max_conns), _identity, target=TARGET_CORE,
            risky=True, expert=True,
            grammar="whole number 1..400, no leading zero",
        ),
        SettingSpec(
            "event_log_rotate_bytes", "VAULT_EVENT_LOG_ROTATE_BYTES", "core", False,
            str(core_settings.ROTATE_DEFAULT_BYTES),
            _core(core_settings.parse_event_log_rotate_bytes), _identity, target=TARGET_CORE,
            grammar="bytes, digits only, at least 1048576; 0 = rotation off",
        ),
        # --- risky (debug knobs, confirm dialog) ---------------------------
        SettingSpec(
            "upstream_edge", "VAULT_UPSTREAM_EDGE", "core", False,
            core_settings.UPSTREAM_EDGE_DEFAULT,
            _core(core_settings.parse_upstream_edge), _identity, target=TARGET_CORE,
            risky=True, expert=True,
            grammar="one *.steamcontent.com or *.steamserver.net host; off = legacy per-name pool",
        ),
        SettingSpec(
            "upstream_pool_hosts", "VAULT_UPSTREAM_POOL_HOSTS", "core", False, None,
            _core(core_settings.parse_upstream_pool_hosts), _identity, target=TARGET_CORE,
            risky=True, expert=True,
            grammar="up to 4 space-separated Steam edge hosts, used only while upstream_edge is off; off = none",
        ),
        SettingSpec(
            "resolver", "VAULT_RESOLVER", "core", False, core_settings.RESOLVER_DEFAULT,
            _core(core_settings.parse_resolver), _identity, target=TARGET_CORE, risky=True,
            expert=True,
            grammar="IP address list, e.g. 1.1.1.1 or [2606:4700::1111]:53; never a resolver that rewrites Steam names",
        ),
        SettingSpec(
            "log_level", "VAULT_LOG_LEVEL", "immediately", False, config.DEFAULT_LOG_LEVEL,
            _non_blank(config.parse_log_level), _identity, expert=True,
            grammar="DEBUG, INFO, WARNING, ERROR or CRITICAL",
        ),
    )
}

#: The core keys by their env name, for ``core_settings``.
CORE_SPECS_BY_ENV: dict[str, SettingSpec] = {
    spec.env_var: spec for spec in OVERRIDABLE_SPECS.values() if spec.target == TARGET_CORE
}


def mask_url(url: str) -> str:
    """Q2: the webhook URL as GET shows it: scheme and host only.

    Credentials can sit in the userinfo AND in the path or query (Discord
    and Slack put the token in the path), so everything after the host is
    replaced. ``https://u:p@hooks.example/x?t=1`` -> ``https://***@hooks.example/***``.
    Any ``@`` after the scheme counts as userinfo, the same over-redaction
    rule as ``webhooks.redact_url`` (review S3): an unencoded ``/``, ``?`` or
    ``#`` in a password moves the userinfo out of what ``urlsplit`` calls
    the netloc. Never raises.
    """
    if not url:
        return ""
    if "://" not in url:
        return "***"
    scheme, after = url.split("://", 1)
    userinfo = "@" in after
    if userinfo:
        after = after.rsplit("@", 1)[1]
    try:
        parts = urlsplit(f"{scheme}://{after}")
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "***"
    if not scheme or not host:
        return "***"
    netloc = ("***@" if userinfo else "") + host + (f":{port}" if port else "")
    rest = "/***" if (parts.path not in ("", "/") or parts.query or parts.fragment) else ""
    return f"{scheme}://{netloc}{rest}"


@dataclass(frozen=True)
class EnvOnlySpec:
    """Informational GET row for a key ``PATCH`` refuses by name.
    ``VAULT_API_KEY`` is never one of these (never echoed)."""

    key: str
    env_var: str
    display: Callable[[Any], Any] = _identity


#: Shown in GET as read-only rows (``env_only: true``).
ENV_ONLY_INFO_KEYS: tuple[EnvOnlySpec, ...] = (
    EnvOnlySpec("db_path", "VAULT_DB_PATH"),
    EnvOnlySpec("cache_root", "VAULT_CACHE_ROOT"),
    EnvOnlySpec("steamprefill_path", "VAULT_STEAMPREFILL_PATH"),
    EnvOnlySpec("steamprefill_cache_dir", "VAULT_STEAMPREFILL_CACHE_DIR"),
    EnvOnlySpec("manifest_archive_dir", "VAULT_MANIFEST_ARCHIVE_DIR"),
    EnvOnlySpec("web_dir", "VAULT_WEB_DIR"),
    EnvOnlySpec("settings_readonly", "VAULT_SETTINGS_READONLY"),
    # WP 4h.0 (ADR-0010): env-only for a privacy reason, see the docstring.
    EnvOnlySpec("relay_expose_playtime", "VAULT_RELAY_EXPOSE_PLAYTIME"),
    EnvOnlySpec("relay_expose_last_played", "VAULT_RELAY_EXPOSE_LAST_PLAYED"),
    # D16b (Q2): can carry credentials; shown masked, never editable.
    EnvOnlySpec("webhook_url", "VAULT_WEBHOOK_URL", mask_url),
)

#: Every key PATCH refuses with the distinct "environment-only" 422.
ENV_ONLY_KEYS: frozenset[str] = frozenset(
    {"vault_api_key"} | {spec.key for spec in ENV_ONLY_INFO_KEYS}
)

# --------------------------------------------------------------------------
# The `settings` table (schema v13)
# --------------------------------------------------------------------------


def get_override(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def get_all_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def _write_override(conn: sqlite3.Connection, key: str, raw_value: str) -> None:
    now = jobs.utcnow_iso()
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, raw_value, now),
    )


def _delete_override_row(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def set_override(conn: sqlite3.Connection, key: str, raw_value: str) -> None:
    """Persist one already-validated value as its own transaction. For a
    multi-key PATCH use ``apply_updates`` (one transaction for the batch)."""
    with jobs.immediate_transaction(conn):
        _write_override(conn, key, raw_value)


def delete_override(conn: sqlite3.Connection, key: str) -> None:
    """Idempotent."""
    with jobs.immediate_transaction(conn):
        _delete_override_row(conn, key)


def apply_updates(
    conn: sqlite3.Connection, to_set: list[tuple[str, str]], to_clear: list[str]
) -> None:
    """A whole PATCH as ONE ``BEGIN IMMEDIATE`` transaction (all or nothing)."""
    with jobs.immediate_transaction(conn):
        for key, raw_value in to_set:
            _write_override(conn, key, raw_value)
        for key in to_clear:
            _delete_override_row(conn, key)


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


#: ``SettingInfo.reason`` values for a row that is not editable.
REASON_ENV = "env"
REASON_ENV_ONLY = "env_only"
REASON_CORE_FILE_DISABLED = "core_file_disabled"


def core_file_enabled(base: Settings) -> bool:
    """Review decision B3 (user night-run decision): core-only keys are
    editable only while vault-api actually hands them to vault-core, i.e.
    while ``VAULT_CORE_SETTINGS_FILE`` names a path. Otherwise a stored value
    would reach nothing."""
    return bool(base.core_settings_file)


def is_core_disabled(spec: SettingSpec, base: Settings) -> bool:
    return spec.target == TARGET_CORE and not core_file_enabled(base)


def env_core_values(base: Settings) -> dict[str, str]:
    """Core keys as vault-api's OWN environment sets them (non-blank and
    valid), env name -> normalised value. Used for the cross rules while the
    core file is disabled: then only defaults and env count, never stored
    values."""
    values: dict[str, str] = {}
    for env_var in CORE_SPECS_BY_ENV:
        if env_var not in base.env_pinned:
            continue
        try:
            normalised = core_settings.TWINS[env_var](os.environ.get(env_var, ""))
        except CoreGrammarError:
            continue
        if normalised is not None:
            values[env_var] = normalised
    return values


def is_env_pinned(spec: SettingSpec, base: Settings) -> bool:
    """Q1: the key's variable is set (non-blank) in vault-api's env."""
    return spec.env_var in base.env_pinned


def _shown(spec: SettingSpec, raw: str) -> str:
    return "<invalid>" if spec.key == "steam_library_steamid" else repr(raw)


def _parse_stored(spec: SettingSpec, raw: str) -> tuple[bool, Any]:
    """(ok, value) for a stored row; a row that no longer parses is logged
    and treated as absent (a bad row must not take the scheduler down)."""
    try:
        return True, spec.parse(raw)
    except SettingValidationError:
        logger.error(
            "settings: stored value for %r is %s, which no longer passes "
            "validation; using the env/default value for this key. Clear it "
            "with PATCH /v1/settings (null).",
            spec.key, _shown(spec, raw),
        )
        return False, None


def effective_settings(conn: sqlite3.Connection, base: Settings) -> Settings:
    """Resolve env > Settings > default into one ``Settings`` snapshot.

    ``base`` is the pure env/default resolution (``Settings.from_env()`` or
    a test's hand-built object). Returns a NEW object with only the stored,
    non-pinned api fields swapped in; core-only keys are not ``Settings``
    fields and never touched here.
    """
    overrides = get_all_overrides(conn)
    if not overrides:
        return base
    updates: dict[str, Any] = {}
    for key, spec in OVERRIDABLE_SPECS.items():
        if spec.target == TARGET_CORE:
            continue
        raw = overrides.get(key)
        if raw is None or is_env_pinned(spec, base):
            continue
        ok, value = _parse_stored(spec, raw)
        if ok:
            updates[key] = value
    return replace(base, **updates) if updates else base


def stored_core_values(
    overrides: Mapping[str, str], base: Settings
) -> dict[str, str]:
    """env name -> stored canonical value for each valid core-key row."""
    values: dict[str, str] = {}
    for env_var, spec in CORE_SPECS_BY_ENV.items():
        raw = overrides.get(spec.key)
        if raw is None or is_env_pinned(spec, base):
            continue
        ok, value = _parse_stored(spec, raw)
        if ok:
            values[env_var] = value
    return values


def core_values(conn: sqlite3.Connection, base: Settings) -> dict[str, str]:
    """Q3: the values vault-api hands vault-core (``core_settings``)."""
    overrides = get_all_overrides(conn)
    effective = effective_settings(conn, base)
    threads_spec = OVERRIDABLE_SPECS["prefill_max_threads"]
    threads_from_settings = (
        "prefill_max_threads" in overrides and not is_env_pinned(threads_spec, base)
    )
    window = effective.schedule_window
    return core_settings.core_file_values(
        stored_core_values(overrides, base),
        None if window is None else window.normalized,
        effective.prefill_max_threads if threads_from_settings else None,
    )


def write_core_file(conn: sqlite3.Connection, base: Settings) -> int | None:
    """Write the core settings file if ``VAULT_CORE_SETTINGS_FILE`` is set.
    Never raises: a failed write is logged (D16c reports the apply state)."""
    if not base.core_settings_file:
        return None
    try:
        return core_settings.write_core_settings_file(
            base.core_settings_file, core_values(conn, base)
        )
    except Exception:
        logger.exception(
            "Could not write the core settings file %s; vault-core keeps its "
            "last applied values.", base.core_settings_file,
        )
        return None


def check_cross_rules(
    conn: sqlite3.Connection,
    base: Settings,
    to_set: Iterable[tuple[str, str]],
    to_clear: Iterable[str],
) -> None:
    """Audit 3.4: refuse what vault-core would refuse, judged on the values
    the batch would leave behind. Only rules whose keys the batch touches are
    checked, so an unrelated PATCH is never blocked by an older state.
    Raises ``SettingValidationError``."""
    prospective = dict(get_all_overrides(conn))
    touched: set[str] = set()
    for key, raw in to_set:
        prospective[key] = raw
        touched.add(key)
    for key in to_clear:
        prospective.pop(key, None)
        touched.add(key)
    if core_file_enabled(base):
        core = {**env_core_values(base), **stored_core_values(prospective, base)}
    else:
        core = env_core_values(base)
    threads_spec = OVERRIDABLE_SPECS["prefill_max_threads"]
    threads = base.prefill_max_threads
    if "prefill_max_threads" in prospective and not is_env_pinned(threads_spec, base):
        ok, value = _parse_stored(threads_spec, prospective["prefill_max_threads"])
        if ok:
            threads = value
    try:
        if touched & {"upstream_max_conns", "prefill_max_threads"}:
            core_settings.check_cap_floor(core, threads)
        if touched & {"tls_client_max_conns", "tls_max_conns"}:
            core_settings.check_tls_order(core)
    except CoreGrammarError as exc:
        raise SettingValidationError(str(exc)) from exc


# --------------------------------------------------------------------------
# Startup warnings (Q1, Q2, rc18 window default)
# --------------------------------------------------------------------------


def warn_at_startup(conn: sqlite3.Connection, base: Settings) -> list[str]:
    """One WARNING per stored value an env pin now shadows (Q1), one for a
    stored webhook URL (Q2, ignored), and one when the download window comes
    from the code default (rc18 upgrade visibility). The deprecation warning
    for VAULT_UPSTREAM_RATE_WINDOW belongs to vault-core (D16c): compose
    does not forward that variable to vault-api. Returns the messages."""
    messages: list[str] = []
    overrides = get_all_overrides(conn)
    for key, spec in OVERRIDABLE_SPECS.items():
        if key in overrides and is_env_pinned(spec, base):
            messages.append(
                f"settings: {spec.env_var} is set in the environment and wins "
                f"over the value stored in Settings for {key!r} (env always "
                "wins since rc18, ADR-0009 addendum). Remove the env variable "
                "to use the Settings value, or clear it in Settings."
            )
    if "webhook_url" in overrides:
        messages.append(
            "settings: a webhook URL is stored in Settings; it is ignored since "
            "rc18 (VAULT_WEBHOOK_URL is env-only). Set VAULT_WEBHOOK_URL in the "
            "environment if webhooks should be delivered."
        )
    if (
        "VAULT_SCHEDULE_WINDOW" not in base.env_pinned
        and "schedule_window" not in overrides
        and base.schedule_window is not None
        and base.schedule_window.normalized == config.DEFAULT_SCHEDULE_WINDOW
    ):
        messages.append(
            f"settings: the download window {config.DEFAULT_SCHEDULE_WINDOW} is "
            "the rc18 default (neither VAULT_SCHEDULE_WINDOW nor Settings sets "
            "one): vault-api downloads updates automatically inside it. "
            "VAULT_SCHEDULE_WINDOW=off (or 'off' in Settings) disables it."
        )
    for message in messages:
        logger.warning("%s", message)
    return messages


# --------------------------------------------------------------------------
# GET /v1/settings read model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingInfo:
    key: str
    effective: Any
    #: "env" | "db" (= set in Settings) | "default"
    source: str
    #: What clearing the Settings value reverts to (env, else default).
    fallback: Any
    applies: str
    env_only: bool
    env_var: str
    #: The value stored in Settings (JSON form), ``None`` if none. Not echoed
    #: for env-only keys.
    value: Any
    #: False when the key is set via env (or env-only).
    editable: bool
    risky: bool
    grammar: str
    target: str
    #: Expert area of the Settings page (D16a table sign-off).
    expert: bool
    #: False for core-only keys: vault-api cannot see vault-core's env.
    env_visible: bool
    #: A stored value exists but env (or env-only status) overrides it.
    shadowed: bool
    #: Why the row is not editable: "env" (set via env), "env_only",
    #: "core_file_disabled" (core key while VAULT_CORE_SETTINGS_FILE is
    #: blank); null when editable.
    reason: str | None


def describe_settings(conn: sqlite3.Connection, base: Settings) -> list[SettingInfo]:
    overrides = get_all_overrides(conn)
    infos: list[SettingInfo] = []

    for key, spec in OVERRIDABLE_SPECS.items():
        pinned = is_env_pinned(spec, base)
        raw = overrides.get(key)
        stored_ok, stored_value = (False, None) if raw is None else _parse_stored(spec, raw)
        stored_json = spec.to_json(stored_value) if stored_ok else None

        if spec.target == TARGET_CORE:
            fallback = spec.default
        else:
            fallback = spec.to_json(getattr(base, key))

        core_disabled = is_core_disabled(spec, base)
        if pinned:
            source, effective = "env", fallback
        elif stored_ok and not core_disabled:
            source, effective = "db", stored_json
        else:
            source, effective = "default", fallback
        reason = REASON_ENV if pinned else (REASON_CORE_FILE_DISABLED if core_disabled else None)

        infos.append(
            SettingInfo(
                key=key,
                effective=effective,
                source=source,
                fallback=fallback,
                applies=spec.applies,
                env_only=False,
                env_var=spec.env_var,
                value=stored_json,
                editable=reason is None,
                risky=spec.risky,
                expert=spec.expert,
                grammar=spec.grammar,
                target=spec.target,
                env_visible=spec.target != TARGET_CORE,
                shadowed=(pinned or core_disabled) and stored_ok,
                reason=reason,
            )
        )

    for env_spec in ENV_ONLY_INFO_KEYS:
        value = env_spec.display(getattr(base, env_spec.key))
        infos.append(
            SettingInfo(
                key=env_spec.key,
                effective=value,
                source="env" if _env_var_is_set(env_spec.env_var) else "default",
                fallback=value,
                applies="restart-required",
                env_only=True,
                env_var=env_spec.env_var,
                value=None,
                editable=False,
                risky=False,
                expert=False,
                grammar="",
                target=TARGET_API,
                env_visible=True,
                shadowed=env_spec.key in overrides,
                reason=REASON_ENV_ONLY,
            )
        )

    return infos


def _env_var_is_set(name: str) -> bool:
    """Informational, for the env-only rows only."""
    return bool(os.environ.get(name, "").strip())
