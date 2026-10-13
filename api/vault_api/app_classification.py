"""Which tracked apps are content packages of another game (WP TOOL-APPS-2a,
ADR-0022 §1).

## What this stores

Table ``app_classification`` (schema v22): one row per tracked app that a
source could classify, ``(appid, app_type, parent_appid, source,
checked_at)``. It is display data: ``GET /v1/games`` and ``GET
/v1/games/{appid}`` derive ``app_type`` and ``parent_appid`` from it, and the
UIs group the flat list (ADR-0022 decision Q2). Nothing here changes what is
prefilled, collected or deleted (decision Q5; the grouped delete is
TOOL-APPS-2b).

## Two sources, store first (decision Q1, Weg A)

* ``store`` -- Steam's store item lookup, the same ``GetItems`` request the
  cover refresher already sends (``cover_art.py``), now with
  ``include_basic_info`` and ``include_related_items``. Its numeric store type
  and ``related_items.parent_appid`` are written by
  ``record_store_results``, on the cover refresher's thread.
* ``steamcmd_api`` -- the manifest oracle (``oracle.py``), only for ids the
  store cannot answer and only while the oracle is on. No extra oracle
  request is ever made for this (decision Q8, Weg B): ``fill_from_oracle``
  reads what the oracle already stored for CACHED apps -- an app's own
  ``common.type``/``common.parent``, and the ``extended.listofdlc`` and
  depot ``dlcappid`` links of a cached parent.

A row comes from exactly one source (its ``source`` column): the store row
when the store answered the id with a type or a parent; otherwise the
oracle-derived row. "The store cannot answer" is ``success != 1`` (P1,
2026-10-13: the HD2 packages and 228980 answer ``success: 15``) or an
answered item that carries neither a type nor a parent.

Both sources are written only by the background cover refresher (one
daemon thread), never on a request path.

## Vocabulary

``app_type`` is one lowercase set whatever the source: ``APP_TYPES``. The
store's numeric ``EStoreAppType`` and PICS' ``common.type`` string are each
mapped onto it in one function (``app_type_from_store``,
``app_type_from_pics``); a value outside the table is ``None`` (unknown).

## Reading (``resolve_classifications``)

* Store rows always count; ``steamcmd_api`` rows only while the oracle is on
  (the same "every read path checks the flag itself" rule as ``oracle.py``).
* Grouping is ONE level: a package whose parent is itself a package is
  attached to the top-level parent, following ``parent_appid`` over at most
  ``MAX_NESTING_DEPTH`` links. A cycle, a self reference, a longer chain, or
  a top-level parent with no ``apps`` row (not tracked) leaves the row
  ungrouped (``parent_appid`` null).
* Apps on the fixed tool list (``tool_apps.TOOL_APPS``, e.g. 228980) are
  never children and never parents: a stored parent for them is ignored, and
  a row whose chain reaches one stays ungrouped.
* Every value read back is re-validated (the database is a file an operator
  can edit): ids through ``deletion.coerce_positive_id``, types against
  ``APP_TYPES``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from vault_api import deletion, tool_apps
from vault_api.config import MANIFEST_ORACLE_STEAMCMD_API
from vault_api.jobs import TIMESTAMP_FORMAT

#: ``source`` values. ``steamcmd_api`` deliberately equals
#: ``oracle.SOURCE_STEAMCMD_API`` (the oracle's own provenance tag). Agent-side
#: classification (S4) is not built (decision Q1); should it ever be, its
#: value is ``agent``.
SOURCE_STORE = "store"
SOURCE_STEAMCMD_API = MANIFEST_ORACLE_STEAMCMD_API

#: The one normalised ``app_type`` vocabulary (ADR-0022 §1).
APP_TYPES = frozenset(
    {"game", "dlc", "tool", "application", "config", "music", "video", "demo"}
)

#: PICS ``common.type`` (case-insensitive) -> vocabulary. PICS spells these
#: "Game", "DLC", "Tool", "Application", "Config", "Music", "Video", "Demo";
#: other PICS types ("Beta", "Media", "Guide", "Series", ...) are unknown.
_PICS_TYPES: Mapping[str, str] = {
    "game": "game",
    "dlc": "dlc",
    "tool": "tool",
    "application": "application",
    "config": "config",
    "music": "music",
    "video": "video",
    "demo": "demo",
}

#: Store ``EStoreAppType`` (``StoreItem.type``) -> vocabulary (Steam,
#: unverified beyond P1: the numbers follow the public protobuf
#: ``steammessages_storebrowse``; 2 Mod, 3 Movie, 5 Guide, 8 Series,
#: 9 Episode, 10 Hardware, 12 Beta, 14 Advertising are left unknown).
_STORE_TYPES: Mapping[int, str] = {
    0: "game",
    1: "demo",
    4: "dlc",
    6: "application",
    7: "video",
    11: "music",
    13: "tool",
}

#: How many ``parent_appid`` links a package may be away from its top-level
#: parent (ADR-0022 §1, "bounded depth (4)").
MAX_NESTING_DEPTH = 4


def app_type_from_pics(value: object) -> str | None:
    """PICS ``common.type`` -> vocabulary, ``None`` when unknown."""
    if not isinstance(value, str) or not value.isascii():
        return None
    return _PICS_TYPES.get(value.strip().lower())


def app_type_from_store(value: object) -> str | None:
    """Store ``StoreItem.type`` (an integer) -> vocabulary, ``None`` when
    unknown. ``bool`` and strings are rejected."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return _STORE_TYPES.get(value)


def valid_app_type(value: object) -> str | None:
    """``value`` if it is one of ``APP_TYPES`` exactly, else ``None``."""
    return value if isinstance(value, str) and value in APP_TYPES else None


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


# --------------------------------------------------------------------------
# Writing: the store (S1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreFacts:
    """What one GetItems item said about an app's place in the catalogue."""

    #: ``success == 1`` and the item was about the app asked for.
    answered: bool
    app_type: str | None = None
    parent_appid: int | None = None

    @property
    def usable(self) -> bool:
        """The store answered this id (see the module docstring)."""
        return self.answered and (self.app_type is not None or self.parent_appid is not None)


def record_store_results(
    conn: sqlite3.Connection, facts: Mapping[int, StoreFacts], now: datetime
) -> None:
    """Store the store's answers. Commits.

    A usable answer replaces the app's row (whatever its source: the store
    comes first). An unusable one removes a previous STORE row, so the gap
    goes back to the oracle; an oracle-derived row is left alone. Apps the
    answer did not mention are not in ``facts`` and are not touched.
    """
    checked = _ts(now)
    for appid, fact in facts.items():
        if deletion.coerce_positive_id(appid) is None:
            continue
        if fact.usable:
            parent = deletion.coerce_positive_id(fact.parent_appid)
            if parent == appid:
                parent = None
            conn.execute(
                """
                INSERT INTO app_classification
                    (appid, app_type, parent_appid, source, checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (appid) DO UPDATE SET
                    app_type     = excluded.app_type,
                    parent_appid = excluded.parent_appid,
                    source       = excluded.source,
                    checked_at   = excluded.checked_at
                """,
                (appid, valid_app_type(fact.app_type), parent, SOURCE_STORE, checked),
            )
        else:
            conn.execute(
                "DELETE FROM app_classification WHERE appid = ? AND source = ?",
                (appid, SOURCE_STORE),
            )
    conn.commit()


# --------------------------------------------------------------------------
# Writing: the oracle's gap fill (S2/S3, decision Q8 Weg B)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FillOutcome:
    written: int = 0
    removed: int = 0


def fill_from_oracle(conn: sqlite3.Connection, *, oracle_enabled: bool) -> FillOutcome:
    """Derive ``steamcmd_api`` rows for every tracked app the store did not
    answer, from what the oracle ALREADY stored. Makes no request. Commits.

    Per app, first its own stored answer (``oracle_app_state.app_type`` /
    ``parent_appid``, present only for apps the update check asked about,
    i.e. cached ones), then the links of cached parents
    (``oracle_dlc_links``: ``extended.listofdlc`` and depot ``dlcappid``).
    A link makes the app a ``dlc``; it names the parent only when exactly
    one cached parent claims the app (two claimants: no parent, rather than
    a guess). An app nothing describes loses a previous oracle-derived row.

    With the oracle off this does nothing (stored rows stay, and
    ``resolve_classifications`` ignores them).
    """
    if not oracle_enabled:
        return FillOutcome()

    tracked = {
        appid
        for appid in (
            deletion.coerce_positive_id(row["appid"])
            for row in conn.execute("SELECT appid FROM apps")
        )
        if appid is not None
    }
    store_answered = {
        int(row["appid"])
        for row in conn.execute(
            "SELECT appid FROM app_classification WHERE source = ?", (SOURCE_STORE,)
        )
    }
    existing = {
        int(row["appid"]): (row["app_type"], row["parent_appid"], row["checked_at"])
        for row in conn.execute(
            "SELECT appid, app_type, parent_appid, checked_at FROM app_classification "
            "WHERE source = ?",
            (SOURCE_STEAMCMD_API,),
        )
    }
    own: dict[int, tuple[str | None, int | None, str]] = {}
    for row in conn.execute(
        "SELECT appid, app_type, parent_appid, checked_at FROM oracle_app_state"
    ):
        appid = deletion.coerce_positive_id(row["appid"])
        if appid is None:
            continue
        own[appid] = (
            valid_app_type(row["app_type"]),
            deletion.coerce_positive_id(row["parent_appid"]),
            row["checked_at"],
        )
    claimants: dict[int, dict[int, str]] = {}
    for row in conn.execute(
        """
        SELECT l.appid AS parent, l.dlc_appid AS child, s.checked_at AS checked_at
        FROM oracle_dlc_links l
        JOIN oracle_app_state s ON s.appid = l.appid
        """
    ):
        parent = deletion.coerce_positive_id(row["parent"])
        child = deletion.coerce_positive_id(row["child"])
        if parent is None or child is None or parent == child:
            continue
        claimants.setdefault(child, {})[parent] = row["checked_at"]

    written = removed = 0
    for appid in sorted(tracked - store_answered):
        own_type, own_parent, own_checked = own.get(appid, (None, None, None))
        if own_parent == appid:
            own_parent = None
        claims = claimants.get(appid, {})
        link_parent = next(iter(claims)) if len(claims) == 1 else None

        app_type = own_type or ("dlc" if claims else None)
        parent = own_parent or link_parent
        if own_type is not None or own_parent is not None:
            checked = own_checked
        else:
            checked = max(claims.values()) if claims else None

        if app_type is None and parent is None:
            if appid in existing:
                conn.execute(
                    "DELETE FROM app_classification WHERE appid = ? AND source = ?",
                    (appid, SOURCE_STEAMCMD_API),
                )
                removed += 1
            continue
        if existing.get(appid) == (app_type, parent, checked):
            continue
        conn.execute(
            """
            INSERT INTO app_classification
                (appid, app_type, parent_appid, source, checked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (appid) DO UPDATE SET
                app_type     = excluded.app_type,
                parent_appid = excluded.parent_appid,
                source       = excluded.source,
                checked_at   = excluded.checked_at
            WHERE app_classification.source = excluded.source
            """,
            (appid, app_type, parent, SOURCE_STEAMCMD_API, checked),
        )
        written += 1
    conn.commit()
    return FillOutcome(written=written, removed=removed)


# --------------------------------------------------------------------------
# Reading: what GET /v1/games returns
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """The two response fields for one tracked app."""

    app_type: str | None = None
    #: The resolved TOP-LEVEL parent, or ``None`` when the app is not grouped.
    parent_appid: int | None = None


UNCLASSIFIED = Classification()


def _top_parent(
    appid: int, raw_parent: Mapping[int, int | None], tracked: set[int]
) -> int | None:
    """Follow ``parent_appid`` up to the top-level parent (see module
    docstring for every case that answers ``None``)."""
    if tool_apps.is_tool_app(appid):
        return None
    current = raw_parent.get(appid)
    if current is None:
        return None
    seen = {appid}
    for _ in range(MAX_NESTING_DEPTH):
        if current in seen or tool_apps.is_tool_app(current):
            return None
        above = raw_parent.get(current)
        if above is None:
            return current if current in tracked else None
        seen.add(current)
        current = above
    return None


def resolve_classifications(
    conn: sqlite3.Connection, *, oracle_enabled: bool
) -> dict[int, Classification]:
    """``{appid: Classification}`` for every tracked app that has one.

    Read-only, two statements regardless of the library size. An app absent
    from the result is unclassified (``UNCLASSIFIED``).
    """
    sources = {SOURCE_STORE}
    if oracle_enabled:
        sources.add(SOURCE_STEAMCMD_API)

    tracked = {
        appid
        for appid in (
            deletion.coerce_positive_id(row["appid"])
            for row in conn.execute("SELECT appid FROM apps")
        )
        if appid is not None
    }
    raw_type: dict[int, str | None] = {}
    raw_parent: dict[int, int | None] = {}
    for row in conn.execute(
        "SELECT appid, app_type, parent_appid, source FROM app_classification"
    ):
        if row["source"] not in sources:
            continue
        appid = deletion.coerce_positive_id(row["appid"])
        if appid is None:
            continue
        raw_type[appid] = valid_app_type(row["app_type"])
        raw_parent[appid] = deletion.coerce_positive_id(row["parent_appid"])

    result: dict[int, Classification] = {}
    for appid in tracked:
        app_type = raw_type.get(appid)
        parent = _top_parent(appid, raw_parent, tracked)
        if app_type is None and parent is None:
            continue
        result[appid] = Classification(app_type=app_type, parent_appid=parent)
    return result
