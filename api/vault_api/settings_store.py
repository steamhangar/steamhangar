"""Persisted settings — the DB-override half of ADR-0009.

``GET``/``PATCH /v1/settings`` (``vault_api/routers/settings.py``) are the
HTTP surface; this module is everything underneath it: the catalog of which
``Settings`` fields may be overridden, how a raw string PATCH value is
validated and turned into a typed value, how the ``settings`` table (schema
v13) is read and written, and how a DB override, an env value and a built-in
default combine into one "effective" answer.

Precedence (ADR-0009 decision 1): **DB override > env value > built-in
default.** ``effective_settings`` is the ONE accessor that resolves this —
every consumer that wants a live-reloadable value goes through it rather than
reading ``Settings`` fields directly. It is called at CALL TIME, not once at
startup, which is what makes a ``PATCH`` visible without a restart to
whichever caller re-invokes it. Two call sites do so on a recurring cadence
of their own (documented per key below as ``applies``):

* ``vault_api/scheduler.py``'s tick loop — the schedule window/interval/
  staleness keys, applies ``"next_sweep"``: the tick already opens its own
  connection every ~60s, so resolving here is free and exactly matches the
  scheduler's own "next sweep sees new config" cadence.
* ``vault_api/worker.py``'s ``_maybe_queue_auto_gc`` — the auto-GC key,
  applies ``"immediately"``: it already has an open connection for the job
  it is about to finish, so the very next completed prefill picks up a
  changed value.

``vault_name`` and the webhook keys (``webhook_url``, ``webhook_events``) are
overridable (PATCH persists them, GET reports them) but are honestly labelled
``"restart-required"``: ``WebhookNotifier`` is constructed once with a fixed
``Settings`` snapshot, and — more fundamentally — its delivery thread is only
ever started by the FastAPI lifespan if ``settings.webhook_enabled`` was true
at BOOT (``vault_api/main.py``). Turning the feature on from off via a DB
override cannot start a thread that decided not to exist minutes or days
earlier. Wiring live reload into that thread is real, scoped work (a second
long-lived SQLite connection, a refresh cadence, its own tests) deliberately
left for a follow-up rather than claimed here — see api/README.md's
"Persisted settings" section for the full statement of this limitation.

Env-only keys (ADR-0009 decision 5) are never in ``OVERRIDABLE_SPECS`` at
all — ``PATCH`` rejects them by name with a distinct ``422`` detail
(``ENV_ONLY_KEYS``), never the generic "unknown key" one, so an operator (or
the web UI) can tell "this key exists but is locked to the environment" from
"this key does not exist".

**WP 4h.0 addendum (ADR-0010).** ``relay_expose_playtime`` and
``relay_expose_last_played`` (the Steam relay's privacy gate,
``vault_api/routers/steam.py``) join ``ENV_ONLY_INFO_KEYS`` for a DIFFERENT
reason than the bootstrap/security keys ADR-0009 §5 already lists there:
they are not about how the process finds its world or who may talk to it.
ADR-0010 records the actual rationale — the ``settings`` table lives in a
Docker volume that can be lost (``docker compose down -v``, a rebuild), and a
setting whose loss silently RE-ENABLES a privacy-sensitive data flow with no
notification cannot be backed by that volume. Both are still ordinary
booleans reported by ``describe_settings`` exactly like every other
``ENV_ONLY_INFO_KEYS`` row (``source``/``effective`` from the environment,
``applies="restart-required"``) — the only thing missing, compared to
``sweep_include_cached``/``auto_gc``, is a ``PATCH`` path.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Callable

from vault_api import config
from vault_api import jobs
from vault_api import webhooks
from vault_api.config import Settings
from vault_api.schedule_window import ScheduleWindow, ScheduleWindowError, parse_window

logger = logging.getLogger(__name__)


class SettingValidationError(ValueError):
    """A ``PATCH /v1/settings`` value that fails its key's grammar.

    Always carries a human-readable message describing exactly what is wrong
    (the router turns it into the ``422`` response body) — never a bare
    ``ValueError`` from deep inside a parser, so every rejection reads the
    same regardless of which key produced it.
    """


# --------------------------------------------------------------------------
# Per-key specs: validation, typed<->JSON conversion, apply semantics.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingSpec:
    """Everything ``GET``/``PATCH /v1/settings`` needs to know about one
    overridable ``Settings`` field.

    ``key`` doubles as the JSON key in both the request and response bodies
    and as the ``Settings`` attribute name — one name, never translated,
    so there is nothing to keep in sync between the two.
    """

    key: str
    env_var: str
    #: "immediately" | "next_sweep" | "restart-required" — see the module
    #: docstring for what each means and which keys get which, honestly.
    applies: str
    #: True for a value that can carry credentials (only ``webhook_url``
    #: today) — redacted in every GET response (ADR-0009 decision 7).
    secret: bool
    #: The built-in default, in TYPED form (matches ``Settings``'s own
    #: dataclass default for this field). Compared against the resolved env
    #: value to report ``source: "env"`` vs. ``"default"`` — see
    #: ``_source_without_override``.
    default: Any
    #: Raw PATCH string -> typed value. Raises ``SettingValidationError`` on
    #: anything the field's grammar does not accept.
    parse: Callable[[str], Any]
    #: Typed value -> JSON-safe value for the GET response (redacts secrets).
    to_json: Callable[[Any], Any]


#: Re-exported for callers/tests; the rule itself lives in ``config`` so
#: ``VAULT_NAME`` at startup and ``PATCH /v1/settings`` share it (S2).
MAX_VAULT_NAME_LENGTH = config.MAX_VAULT_NAME_LENGTH


def _parse_vault_name(raw: str) -> str:
    try:
        return config.parse_vault_name(raw)
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


def _parse_schedule_window_override(raw: str) -> ScheduleWindow | None:
    """Mirrors ``Settings.from_env``'s own handling of ``VAULT_SCHEDULE_WINDOW``
    exactly: blank means "disabled" and is accepted without ever calling the
    parser, which is what makes "force the scheduler off via the API even
    though the env var still has a window in it" expressible at all — the
    same blank-is-the-off-switch convention ``VAULT_SCHEDULE_WINDOW`` and
    ``VAULT_WEBHOOK_URL`` both use at startup.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        return parse_window(text)
    except ScheduleWindowError as exc:
        raise SettingValidationError(str(exc)) from exc


def _parse_schedule_interval_override(raw: str) -> int:
    if not raw.strip():
        raise SettingValidationError(
            "must not be blank; use null to clear the override instead of "
            "an empty string"
        )
    try:
        # Same ceiling Settings.from_env applies (config.MAX_INTERVAL_MINUTES,
        # WP API-FIX-2 S2): this value goes straight into a timedelta on the
        # scheduler tick and in GET /v1/schedule, GET /v1/games.
        return config.parse_strict_int(
            raw, minimum=1, maximum=config.MAX_INTERVAL_MINUTES
        )
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


def _parse_schedule_client_stale_days_override(raw: str) -> int:
    if not raw.strip():
        raise SettingValidationError(
            "must not be blank; use null to clear the override instead of "
            "an empty string"
        )
    try:
        return config.parse_strict_int(raw, minimum=1, maximum=config.MAX_DAYS)
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


def _parse_auto_gc_override(raw: str) -> str:
    if not raw.strip():
        raise SettingValidationError(
            f"must be one of {', '.join(config.AUTO_GC_MODES)}; use null to "
            "clear the override instead of an empty string"
        )
    try:
        return config.parse_auto_gc(raw)
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


def _parse_sweep_include_cached_override(raw: str) -> bool:
    """WP 4d. Same "blank is not a value" rule as every other non-blank enum
    key here (``auto_gc``, the two ``schedule_*`` ints): a settings override
    that clears to blank is meaningless — clearing is spelled ``null``.
    """
    if not raw.strip():
        raise SettingValidationError(
            "must be one of true/false/yes/no/on/off/1/0; use null to clear "
            "the override instead of an empty string"
        )
    try:
        return config.parse_strict_bool(raw)
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


def _parse_webhook_url_override(raw: str) -> str:
    try:
        return config.validate_webhook_url(raw)
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


def _parse_webhook_events_override(raw: str) -> frozenset[str]:
    try:
        return config.parse_webhook_events(raw)
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


def _window_to_json(window: ScheduleWindow | None) -> str | None:
    return None if window is None else window.raw


def _events_to_json(events: frozenset[str]) -> list[str]:
    return sorted(events)


#: The eight keys overridable today (ADR-0009 named the first seven; WP 4d
#: added ``sweep_include_cached`` as "one more overridable key when it
#: lands", per the ADR's own consequences section), in the order they are
#: documented in api/README.md and returned by GET /v1/settings. Keyed by
#: ``Settings`` attribute name, which is also the JSON key both endpoints use.
OVERRIDABLE_SPECS: dict[str, SettingSpec] = {
    "vault_name": SettingSpec(
        key="vault_name",
        env_var="VAULT_NAME",
        applies="restart-required",
        secret=False,
        default="",
        parse=_parse_vault_name,
        to_json=lambda v: v,
    ),
    "schedule_window": SettingSpec(
        key="schedule_window",
        env_var="VAULT_SCHEDULE_WINDOW",
        applies="next_sweep",
        secret=False,
        default=None,
        parse=_parse_schedule_window_override,
        to_json=_window_to_json,
    ),
    "schedule_interval_minutes": SettingSpec(
        key="schedule_interval_minutes",
        env_var="VAULT_SCHEDULE_INTERVAL_MINUTES",
        applies="next_sweep",
        secret=False,
        default=config.DEFAULT_SCHEDULE_INTERVAL_MINUTES,
        parse=_parse_schedule_interval_override,
        to_json=lambda v: v,
    ),
    "schedule_client_stale_days": SettingSpec(
        key="schedule_client_stale_days",
        env_var="VAULT_SCHEDULE_CLIENT_STALE_DAYS",
        applies="next_sweep",
        secret=False,
        default=config.DEFAULT_SCHEDULE_CLIENT_STALE_DAYS,
        parse=_parse_schedule_client_stale_days_override,
        to_json=lambda v: v,
    ),
    "auto_gc": SettingSpec(
        key="auto_gc",
        env_var="VAULT_AUTO_GC",
        applies="immediately",
        secret=False,
        default=config.DEFAULT_AUTO_GC,
        parse=_parse_auto_gc_override,
        to_json=lambda v: v,
    ),
    "sweep_include_cached": SettingSpec(
        key="sweep_include_cached",
        env_var="VAULT_SWEEP_INCLUDE_CACHED",
        # Same tick-loop resolution as the other scheduler keys above:
        # scheduler.PrefillScheduler._tick resolves effective_settings fresh
        # every ~60s tick and hands the result to maybe_sweep/compute_targets,
        # so a PATCH is visible to the very next sweep, not only after a
        # restart.
        applies="next_sweep",
        secret=False,
        default=config.DEFAULT_SWEEP_INCLUDE_CACHED,
        parse=_parse_sweep_include_cached_override,
        to_json=lambda v: v,
    ),
    "webhook_url": SettingSpec(
        key="webhook_url",
        env_var="VAULT_WEBHOOK_URL",
        applies="restart-required",
        secret=True,
        default="",
        parse=_parse_webhook_url_override,
        to_json=webhooks.redact_url,
    ),
    "webhook_events": SettingSpec(
        key="webhook_events",
        env_var="VAULT_WEBHOOK_EVENTS",
        applies="restart-required",
        secret=False,
        default=frozenset(config.WEBHOOK_EVENTS_ALL),
        parse=_parse_webhook_events_override,
        to_json=_events_to_json,
    ),
}


@dataclass(frozen=True)
class EnvOnlySpec:
    """Informational entry for a key ``PATCH`` refuses by name (ADR-0009
    decision 5). ``VAULT_API_KEY`` is deliberately NOT one of these — it is
    rejected by ``PATCH`` the same way, but never listed anywhere a ``GET``
    response could echo it, redacted or not (see ``ENV_ONLY_KEYS`` below).
    """

    key: str
    env_var: str


#: Shown in GET /v1/settings as read-only, informational rows (env_only=true)
#: — a settings screen can render "this exists but only the environment
#: controls it" instead of silently omitting these keys. ``VAULT_API_KEY``
#: is excluded on purpose (see ``EnvOnlySpec``'s docstring).
ENV_ONLY_INFO_KEYS: tuple[EnvOnlySpec, ...] = (
    EnvOnlySpec("db_path", "VAULT_DB_PATH"),
    EnvOnlySpec("cache_root", "VAULT_CACHE_ROOT"),
    EnvOnlySpec("steamprefill_path", "VAULT_STEAMPREFILL_PATH"),
    EnvOnlySpec("steamprefill_cache_dir", "VAULT_STEAMPREFILL_CACHE_DIR"),
    EnvOnlySpec("manifest_archive_dir", "VAULT_MANIFEST_ARCHIVE_DIR"),
    EnvOnlySpec("web_dir", "VAULT_WEB_DIR"),
    EnvOnlySpec("settings_readonly", "VAULT_SETTINGS_READONLY"),
    # WP 4h.0 (ADR-0010) — see this module's own docstring addendum above
    # for why these two are env-only for a DIFFERENT reason than every
    # other row in this tuple.
    EnvOnlySpec("relay_expose_playtime", "VAULT_RELAY_EXPOSE_PLAYTIME"),
    EnvOnlySpec("relay_expose_last_played", "VAULT_RELAY_EXPOSE_LAST_PLAYED"),
)

#: Every key name PATCH refuses with the distinct "environment-only" 422 —
#: ``ENV_ONLY_INFO_KEYS`` above, PLUS ``vault_api_key``, which never appears
#: in a GET response (redacted or not) but must still be named, not silently
#: folded into the generic "unknown key" error, if someone tries to PATCH it.
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
    """The UPSERT itself, with no transaction/commit management of its own —
    the two things that call this (``set_override`` and ``apply_updates``)
    each own that decision, one commit per call or one commit for a whole
    batch respectively.
    """
    now = jobs.utcnow_iso()
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, raw_value, now),
    )


def _delete_override_row(conn: sqlite3.Connection, key: str) -> None:
    """The DELETE itself — see ``_write_override``'s note on why this has no
    transaction/commit management of its own."""
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def set_override(conn: sqlite3.Connection, key: str, raw_value: str) -> None:
    """Persist a validated override, as its own committed transaction.

    Caller must have already validated ``raw_value`` with the matching
    ``SettingSpec.parse`` — this function does not re-validate, it only
    stores. For a multi-key ``PATCH`` where several writes must succeed or
    fail TOGETHER, use ``apply_updates`` instead — calling this in a loop is
    exactly the bug reviewer blocker S2 caught (each call is its own
    transaction, so a failure partway through a batch leaves a partial
    write despite the ``422``/`500` response claiming nothing was persisted).
    """
    with jobs.immediate_transaction(conn):
        _write_override(conn, key, raw_value)


def delete_override(conn: sqlite3.Connection, key: str) -> None:
    """Idempotent: deleting a key with no override row is a silent no-op.
    See ``set_override``'s note on ``apply_updates`` for the batch case.
    """
    with jobs.immediate_transaction(conn):
        _delete_override_row(conn, key)


def apply_updates(
    conn: sqlite3.Connection, to_set: list[tuple[str, str]], to_clear: list[str]
) -> None:
    """Apply a whole ``PATCH``'s worth of writes as ONE committed transaction
    (reviewer should-fix S2).

    ``PATCH /v1/settings`` validates every key in the request body before
    writing anything (``routers/settings.py``), which only holds the promise
    ADR-0009 makes ("a bad value... must be impossible to persist") for
    VALIDATION failures. A ``sqlite3.Error`` partway through persisting an
    already-validated batch (disk full, a locked database past
    ``busy_timeout``) is a different failure mode, and looping
    ``set_override``/``delete_override`` — each its OWN committed
    transaction — would leave keys ``1..N-1`` durably written and key ``N``
    missing while the ``500`` response tells the caller nothing was
    persisted. ``jobs.immediate_transaction`` (the same ``BEGIN IMMEDIATE``
    pattern every other check-then-act write in this codebase uses) makes
    the whole batch all-or-nothing: any exception rolls back everything
    written so far in this call, including keys that individually would have
    validated and written fine.
    """
    with jobs.immediate_transaction(conn):
        for key, raw_value in to_set:
            _write_override(conn, key, raw_value)
        for key in to_clear:
            _delete_override_row(conn, key)


# --------------------------------------------------------------------------
# The one accessor: effective_settings (ADR-0009 decision 6)
# --------------------------------------------------------------------------


def effective_settings(conn: sqlite3.Connection, base: Settings) -> Settings:
    """Resolve DB override > env value > built-in default into one snapshot.

    ``base`` is always the pure env/default resolution (``app.state.settings``
    — the object ``Settings.from_env()`` produced once at startup, or a
    test's hand-built ``Settings``). This function never mutates it; it
    returns a NEW ``Settings`` via ``dataclasses.replace`` with only the
    overridden fields swapped in, so every field this module does not know
    about (and every overridable field with no DB row) passes through from
    ``base`` unchanged.

    A row whose stored value no longer parses (an operator hand-edited
    ``vault.db`` with sqlite3 while the service was stopped, per the
    documented emergency escape hatch, and typed something the current
    grammar rejects) is logged and IGNORED for that one key rather than
    raising — this function runs inside live request handling and the
    scheduler tick, and a single bad row must not take either down. The
    field falls back to ``base``'s own value for that key, exactly as if no
    override existed.
    """
    overrides = get_all_overrides(conn)
    if not overrides:
        return base

    updates: dict[str, Any] = {}
    for key, spec in OVERRIDABLE_SPECS.items():
        raw = overrides.get(key)
        if raw is None:
            continue
        try:
            updates[key] = spec.parse(raw)
        except SettingValidationError:
            # A secret-carrying key (webhook_url) is never logged, not even
            # through redact_url: a corrupt stored value need not parse as a
            # URL (scheme-less ``admin:s3cr3t@host/x`` passes redact_url
            # unchanged), and this line lands in `docker logs`
            # (WP API-FIX-2, P2/S1).
            shown = "<redacted>" if spec.secret else raw
            logger.error(
                "settings: stored override for %r is %r, which no longer "
                "passes validation; falling back to the env/default value "
                "for this key. Clear it with PATCH /v1/settings (null) or "
                "the sqlite3 escape hatch documented in api/README.md.",
                key, shown,
            )
    return replace(base, **updates) if updates else base


# --------------------------------------------------------------------------
# GET /v1/settings read model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingInfo:
    key: str
    effective: Any
    source: str  # "db" | "env" | "default"
    fallback: Any
    applies: str
    env_only: bool


def _source_without_override(current: Any, default: Any) -> str:
    """"env" if the resolved (non-override) value differs from the built-in
    default, else "default".

    Deliberately compares TYPED VALUES rather than re-inspecting
    ``os.environ`` — ``base`` (a plain ``Settings.from_env()`` result, or a
    hand-built one in tests) is already the complete answer to "what did the
    environment produce", so re-deriving it from ``os.environ`` a second time
    here would be a second source of truth for the exact same fact. The
    trade-off, stated plainly: an operator who explicitly sets an env var to
    the SAME value the default already has is reported as "default" rather
    than "env" — cosmetically imprecise, but the effective value is identical
    either way, so nothing an operator does differs from what this reports.
    """
    return "default" if current == default else "env"


def describe_settings(conn: sqlite3.Connection, base: Settings) -> list[SettingInfo]:
    """Build the full ``GET /v1/settings`` read model: every overridable key,
    plus the informational env-only ones.
    """
    overrides = get_all_overrides(conn)
    infos: list[SettingInfo] = []

    for key, spec in OVERRIDABLE_SPECS.items():
        base_value = getattr(base, key)
        fallback_json = spec.to_json(base_value)
        raw = overrides.get(key)
        if raw is not None:
            try:
                effective_value = spec.parse(raw)
                source = "db"
            except SettingValidationError:
                # Same fail-soft rule as effective_settings: a corrupt stored
                # value is reported as if it were absent, not surfaced as a
                # crash in the settings screen itself.
                effective_value = base_value
                # spec.env_var: which VAULT_* env var this "env" vs.
                # "default" call is deciding between (see the exhaustive
                # OVERRIDABLE_SPECS table above for the mapping).
                source = _source_without_override(base_value, spec.default)
        else:
            effective_value = base_value
            # spec.env_var: same note as above.
            source = _source_without_override(base_value, spec.default)

        infos.append(
            SettingInfo(
                key=key,
                effective=spec.to_json(effective_value),
                source=source,
                fallback=fallback_json,
                applies=spec.applies,
                env_only=False,
            )
        )

    for env_spec in ENV_ONLY_INFO_KEYS:
        value = getattr(base, env_spec.key)
        infos.append(
            SettingInfo(
                key=env_spec.key,
                effective=value,
                source="env" if _env_var_is_set(env_spec.env_var) else "default",
                fallback=value,
                applies="restart-required",
                env_only=True,
            )
        )

    return infos


def _env_var_is_set(name: str) -> bool:
    """Purely informational (the ``ENV_ONLY_INFO_KEYS`` rows only, never the
    overridable/mutation-tested set above, which uses
    ``_source_without_override`` instead) — these fields have no DB-override
    precedence to get wrong, so asking the environment directly is fine here.
    """
    return bool(os.environ.get(name, "").strip())
