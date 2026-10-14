"""Persisted settings endpoints (ADR-0009 + D16b addendum): ``GET``/``PATCH /v1/settings``.

**D16b (rc18).** Env always wins (Q1): a key whose ``VAULT_*`` variable is
set in vault-api's env is reported ``source: "env"``, ``editable: false``, and
PATCH answers ``409`` for it. Every row now also carries ``env_var``,
``value`` (the stored Settings value or ``null``), ``editable``, ``risky``,
``expert`` (expert area of the page), ``grammar``, ``target`` (``api`` | ``core`` | ``api+core``), ``env_visible``
(false for core-only keys, whose env vault-api cannot see) and ``shadowed``.
A PATCH that sets or clears a risky key needs the query parameter
``confirm_risky=true``, else ``428`` with ``detail.risky_keys``. A blank
``schedule_window`` value is a reset (like ``null``); "no window" is ``off``.
``webhook_url`` is env-only (Q2) and shown masked. ``source`` keeps the wire
value ``"db"`` for "set in Settings" (the shipped web UI and Android app
read it).

**D16c.** Core keys (``target: "core"``) read vault-core's status file
(``<cache volume>/logs/settings-applied.json``): a key vault-core's own env
pins is ``source: "env"``, ``editable: false``, and every core key is
``env_visible``. The top-level ``core_apply`` (``null`` until vault-core wrote
the file) carries ``state`` (``applied`` | ``rejected`` | ``none``),
``generation``, ``applied_generation``, ``reason``, ``updated_at``,
``written_generation`` (vault-api's own file) and ``pending``.

``GET`` reports, per key, the effective value, where it came from (``db`` |
``env`` | ``default``), the value clearing the override would revert to, when
a change takes effect (``immediately`` | ``next_sweep`` | ``restart-required``
— see ``vault_api/settings_store.py``'s module docstring for exactly why each
key gets the label it does), and whether the key is environment-only at all.

``PATCH`` accepts a partial JSON object, one entry per key to change:

* a string (or a number, coerced to its string form) sets/replaces the
  override, validated with the SAME grammar function ``vault_api/config.py``
  applies to the corresponding env var at startup (ADR-0009 decision 4) —
  ``webhook_events`` additionally accepts a JSON list of strings, joined with
  commas before validation, since a list is the more natural shape for a
  multi-value field over JSON;
* ``null`` deletes the override row, reverting that key to its env value or
  built-in default immediately (ADR-0009 decision 2).

The WHOLE body is validated before anything is written — one bad value in a
multi-key PATCH fails the request with ``422`` and persists NOTHING, not just
the key that was bad (ADR-0009 decision 4: "impossible to persist a value
that would later fail"). An unrecognised key is ``422`` ("unknown setting"); a
recognised but environment-only key (``ENV_ONLY_KEYS`` — bootstrap/security
settings, see ``settings_store.py``) is also ``422`` but with a DISTINCT
detail, so a caller (or the web UI) can tell "this key does not exist" from
"this key exists but is locked to the environment" without string-matching
the message.

``VAULT_SETTINGS_READONLY=1`` disables ``PATCH`` entirely — ``403``, checked
BEFORE the body is even parsed, so a locked-down/GitOps deployment gets the
same answer regardless of what the request contains.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth") — both routes are authenticated like every other
endpoint in this codebase.

**``server_version`` (WP 4e.7).** ``GET /v1/settings`` also reports the
running server's own version, as its own top-level field, sibling to
``readonly`` — NOT as a row in the ``settings`` list. This is a deliberate
shape decision, not an oversight: a version is not settable, has no
db/env/default precedence, and no ``applies`` timing, so shoehorning it into
``SettingInfoOut`` would either fake those fields or special-case a row that
does not behave like the others. ``PATCH`` treats ``server_version`` exactly
like any other name it does not recognise — ``422`` "not a recognised
setting" — because it is not in ``OVERRIDABLE_SPECS`` and not in
``ENV_ONLY_KEYS`` either; it needs no entry in either catalog to be rejected
correctly, and adding one would wrongly imply it is either overridable or
env-controlled.

This endpoint carries it (instead of a new route, or ``GET /v1/health``)
because it is already authenticated and already polled by every frontend
that has settings UI, so exposing it here costs zero new routes and zero
new requests. **Deliberately not on ``GET /v1/health``:** that route is the
one intentionally unauthenticated endpoint in this API (api/README.md
"Auth"), with a fixed ``{"status": "ok"}`` body by design — anything reachable
without a key should tell an unauthenticated caller as little as possible.

The precise reason this matters (corrected, review round 1 S2 — the plan
does NOT say vault-api itself must never face the internet; it says the
opposite of vault-core: `docs/PROJECT_PLAN.md` §10 lists a "Public domain"
remote-access profile that fronts **vault-api** with a reverse proxy,
explicitly TLS-terminated but with `/v1/health` still meant to answer an
external monitor, per that same section — "never expose vault-core/port
80" is the vault-core rule, not a vault-api one): `/v1/health` can
legitimately be internet-reachable in that shipped profile, which makes
the minimalism argument STRONGER, not weaker — free fingerprinting matters
more, not less, on a route that may sit on the open internet with no key
in front of it. Where the value comes from (WP VER-1): the image's baked
``VAULT_BUILD_VERSION`` (the release tag without its ``v`` on a published
image, ``dev`` on a local build), falling back to
``vault_api.BASE_VERSION`` when the env is absent or unusable. It is
resolved once per app in ``create_app`` (``app.state.build_info``) via
``vault_api.build_info()``; see ``vault_api/__init__.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from vault_api import core_settings, settings_store
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener
from vault_api.settings_store import (
    ENV_ONLY_KEYS,
    OVERRIDABLE_SPECS,
    SettingValidationError,
)

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["settings"])

_READONLY_DETAIL = (
    "The settings API is read-only (VAULT_SETTINGS_READONLY is set). Change "
    "values via the environment and restart, or unset that variable to "
    "re-enable PATCH /v1/settings."
)
_ENV_ONLY_DETAIL_TEMPLATE = (
    "{key!r} is environment-only and cannot be changed via the API; set its "
    "environment variable and restart instead."
)
_UNKNOWN_KEY_DETAIL_TEMPLATE = "{key!r} is not a recognised setting."
_ENV_PINNED_DETAIL_TEMPLATE = (
    "{key!r} is set via env ({env_var}); env always wins. Remove the "
    "environment variable and restart to change it here."
)
_RISKY_DETAIL = (
    "This change includes debug settings that can break downloads. Repeat the "
    "request with confirm_risky=true to apply it."
)


class SettingInfoOut(BaseModel):
    key: str
    effective: Any
    source: str
    fallback: Any
    applies: str
    env_only: bool
    env_var: str
    value: Any
    editable: bool
    risky: bool
    expert: bool
    grammar: str
    target: str
    env_visible: bool
    shadowed: bool


class CoreApplyOut(BaseModel):
    """D16c: vault-core's report on the core settings (``settings-applied.json``).
    ``pending`` is true while vault-api's settings file carries a generation
    other than the one vault-core last applied or rejected (not equal, not
    greater: the generation restarts at 1 after a lost settings volume)."""

    state: str
    generation: int
    applied_generation: int
    reason: str
    updated_at: str
    written_generation: int | None
    pending: bool


class SettingsOut(BaseModel):
    #: Mirrors ``Settings.settings_readonly`` — true means PATCH answers 403.
    readonly: bool
    #: WP 4e.7: the running server's own version. Its own top-level field,
    #: not a row in ``settings`` — see this module's docstring for why.
    #: Since WP VER-1 it is the release version baked into the image
    #: (``VAULT_BUILD_VERSION``), ``dev`` for a locally built image, and
    #: ``vault_api.BASE_VERSION`` for a native run without that env.
    server_version: str
    settings: list[SettingInfoOut]
    #: D16c: ``None`` until vault-core has written its status file.
    core_apply: CoreApplyOut | None = None


def _core_apply(core_status: core_settings.CoreStatus | None, base: Settings) -> CoreApplyOut | None:
    if core_status is None:
        return None
    written = core_settings.written_generation(base.core_settings_file)
    return CoreApplyOut(
        state=core_status.state,
        generation=core_status.generation,
        applied_generation=core_status.applied_generation,
        reason=core_status.reason,
        updated_at=core_status.updated_at,
        written_generation=written,
        # "not equal", not "greater": vault-api restarts at 1 when the
        # settings volume is lost.
        pending=written is not None and written != core_status.generation,
    )


def _build_response(conn, base: Settings, server_version: str) -> SettingsOut:
    core_status = core_settings.read_core_status(core_settings.core_status_path(base.cache_root))
    infos = settings_store.describe_settings(conn, base, core_status)
    return SettingsOut(
        readonly=base.settings_readonly,
        server_version=server_version,
        settings=[SettingInfoOut(**vars(info)) for info in infos],
        core_apply=_core_apply(core_status, base),
    )


@router.get("/v1/settings", response_model=SettingsOut)
def get_settings(request: Request, open_db: DbOpener = Depends(db_opener)) -> SettingsOut:
    base: Settings = request.app.state.settings
    with open_db() as conn:
        return _build_response(conn, base, request.app.state.build_info.version)


def _coerce_patch_value(key: str, value: Any) -> str:
    """One PATCH body entry -> the raw string a ``SettingSpec.parse`` expects.

    Booleans are rejected explicitly and BEFORE the int/float branch — a JSON
    boolean is an ``int`` subclass in Python, so without this a stray
    ``true``/``false`` would silently become the string ``"True"``/``"False"``
    instead of a clear error (docs/LEARNINGS.md "Parsers": "Pydantic lax mode
    coerces true->1 on int fields"; the same trap applies to a hand-rolled
    isinstance chain, not just Pydantic).
    """
    if isinstance(value, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{key!r} must be a string, not a boolean.",
        )
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and key == "steam_library_steamid":
        # WP API-FEAT-1 S1: a JSON number for a 17-digit id is already
        # rounded by any JavaScript sender, so refuse it instead of storing a
        # possibly different account.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{key!r}: send the SteamID64 as a JSON string (17 digits "
                "exceed JavaScript's safe integer range)."
            ),
        )
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list) and key == "webhook_events":
        if not all(isinstance(item, str) for item in value):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{key!r}: every list item must be a string event name.",
            )
        return ",".join(value)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"{key!r} must be a string"
            + (" or a list of strings" if key == "webhook_events" else "")
            + ", or null to clear the override."
        ),
    )


def _apply_live_log_level(effective: Settings) -> None:
    """``log_level`` applies immediately in vault-api (the runner picks it up
    at its next start)."""
    level = logging.getLevelNamesMapping().get(effective.log_level.strip().upper())
    if level is not None:
        logging.getLogger().setLevel(level)


@router.patch("/v1/settings", response_model=SettingsOut)
def patch_settings(
    payload: dict[str, Any],
    request: Request,
    open_db: DbOpener = Depends(db_opener),
    confirm_risky: bool = Query(False),
) -> SettingsOut:
    base: Settings = request.app.state.settings

    # ADR-0009 decision 3: checked BEFORE the body is even inspected, so a
    # locked deployment gets the same 403 regardless of what was sent.
    if base.settings_readonly:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_READONLY_DETAIL)

    # Pass 1: validate EVERYTHING, write NOTHING. A single bad value in a
    # multi-key PATCH must not partially apply the good ones.
    to_set: list[tuple[str, str]] = []
    to_clear: list[str] = []
    for key, value in payload.items():
        if key in ENV_ONLY_KEYS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=_ENV_ONLY_DETAIL_TEMPLATE.format(key=key),
            )
        spec = OVERRIDABLE_SPECS.get(key)
        if spec is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=_UNKNOWN_KEY_DETAIL_TEMPLATE.format(key=key),
            )
        if settings_store.is_env_pinned(spec, base):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_ENV_PINNED_DETAIL_TEMPLATE.format(key=key, env_var=spec.env_var),
            )
        if value is None:
            to_clear.append(key)
            continue
        raw = _coerce_patch_value(key, value)
        if key == "schedule_window" and not raw.strip():
            # Q7: blank = not set; "no window" is spelled off.
            to_clear.append(key)
            continue
        try:
            parsed = spec.parse(raw)
        except SettingValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{key!r}: {exc}",
            ) from exc
        # Core keys are stored in their canonical form (the twin's), which
        # is what the core settings file carries.
        to_set.append((key, parsed if spec.target == settings_store.TARGET_CORE else raw))

    risky = sorted(
        key for key in [k for k, _ in to_set] + to_clear if OVERRIDABLE_SPECS[key].risky
    )
    if risky and not confirm_risky:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={"message": _RISKY_DETAIL, "risky_keys": risky},
        )

    # Pass 2: everything validated — persist it all as ONE transaction
    # (reviewer should-fix S2: looping set_override/delete_override here
    # used to commit each key separately, so a mid-batch sqlite3.Error could
    # leave a partial write despite the response claiming nothing persisted).
    with open_db() as conn:
        # Audit 3.4: cross-variable rules on the state the batch leaves.
        try:
            settings_store.check_cross_rules(conn, base, to_set, to_clear)
        except SettingValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        settings_store.apply_updates(conn, to_set, to_clear)
        touched = {key for key, _ in to_set} | set(to_clear)
        if "log_level" in touched:
            _apply_live_log_level(settings_store.effective_settings(conn, base))
        settings_store.write_core_file(conn, base)
        return _build_response(conn, base, request.app.state.build_info.version)
