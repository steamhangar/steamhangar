"""Which tracked apps are content packages of another game, and what Steam
calls apps vault-api has no name for (WP TOOL-APPS-2a, ADR-0022 §1).

## What this stores

Table ``app_classification`` (schema v22): at most one row per (app,
source), ``(appid, source, app_type, parent_appid, linked, name,
checked_at)``. Each row holds exactly what ONE source said, so provenance is
kept per field; ``resolve_classifications`` merges the sources on read. It is
display data: ``GET /v1/games`` and ``GET /v1/games/{appid}`` derive
``app_type``, ``parent_appid`` and a fallback ``name`` from it, and the UIs
group the flat list (ADR-0022 decision Q2). Nothing here changes what is
prefilled, collected or deleted (decision Q5; the grouped delete is
TOOL-APPS-2b).

## Two sources, store first (decision Q1, Weg A)

* ``store`` -- Steam's store item lookup, the same ``GetItems`` request the
  cover refresher already sends (``cover_art.py``), now with
  ``include_basic_info`` and ``include_related_items``: the item's numeric
  store type, ``related_items.parent_appid`` and ``name``. Written by
  ``record_store_results``.
* ``steamcmd_api`` -- the manifest oracle (``oracle.py``), only while the
  oracle is on. No extra oracle request is ever made for this (decision Q8,
  Weg B): ``fill_from_oracle`` reads what the oracle already stored for
  CACHED apps -- an app's own ``common.type``/``common.parent``/
  ``common.name``, and the ``extended.listofdlc`` and depot ``dlcappid``
  links of a cached parent (``linked``).

**Merge on read, per field, store first:** each of type, parent and name is
the store's value when the store gave one, else the oracle's. So a store
answer with a type but no parent keeps the store's type and takes the
oracle's parent (the "oracle for gaps" of Q1). A store parent always wins
over an oracle parent. The oracle row's ``app_type`` is only the app's OWN
``common.type``; a ``dlc`` inferred from a parent's link list is kept apart
(``linked``) and used for the displayed type only when no source named one,
so TOOL-APPS-2b can tell an app's own type from an inferred one
(``Classification.own_app_type``).

Both sources are written only by the background cover refresher (one daemon
thread), never on a request path.

## Names

``name`` is a fallback for ``apps.name``: the game endpoints show it only
when ``apps.name`` is empty, and it is never written into ``apps`` (owned
library, agent and mapping names keep winning, and a third-party name never
becomes vault-api's own record). It is plain text from a third party:
``valid_app_name`` drops control and invisible format characters, trims, and
caps it at ``MAX_NAME_LEN`` characters, on write AND on read. Steam's own
spelling is kept (no parent-prefix trimming; UIs may shorten later).

## Vocabulary

``app_type`` is one lowercase set whatever the source: ``APP_TYPES``. The
store's numeric ``EStoreAppType`` and PICS' ``common.type`` string are each
mapped onto it in one function (``app_type_from_store``,
``app_type_from_pics``); a value outside the table is ``None`` (unknown).

## Reading (``resolve_classifications``)

* Store rows always count; ``steamcmd_api`` rows only while the oracle is on
  (the same "every read path checks the flag itself" rule as ``oracle.py``).
* Grouping is ONE level: a package whose parent is itself a package is
  attached to the top-level parent, following the merged ``parent_appid``
  over at most ``MAX_NESTING_DEPTH`` links. A cycle, a self reference, a
  longer chain, or a top-level parent with no ``apps`` row (not tracked)
  leaves the row ungrouped (``parent_appid`` null).
* Apps on the fixed tool list (``tool_apps.TOOL_APPS``, e.g. 228980) are
  never children and never parents: a stored parent for them is ignored, and
  a row whose chain reaches one stays ungrouped.
* Every value read back is re-validated (the database is a file an operator
  can edit): ids through ``deletion.coerce_positive_id``, types against
  ``APP_TYPES``, names through ``valid_app_name``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

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

#: Longest name kept, in characters (same bound as the Steam relay's
#: owned-games names, ``steam_relay.MAX_NAME_LEN``).
MAX_NAME_LEN = 256

#: Invisible format characters a third-party name must not carry (category
#: Cf/Zl/Zp, not Cc): bidi embedding/override/isolate controls and the
#: implicit marks LRM/RLM/ALM (they reorder or disguise the text around
#: them), zero-width space/joiners, word joiner and BOM (invisible padding
#: that makes two names look alike), and the line/paragraph separators.
_INVISIBLE_CHARS = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    "\u200e\u200f\u061c"
    "\u200b\u200c\u200d\u2060\ufeff"
    "\u2028\u2029"
)


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


def valid_app_name(value: object) -> str | None:
    """A third-party app name as plain display text, or ``None``.

    Control characters (C0, DEL, C1) and ``_INVISIBLE_CHARS`` are removed, the
    result is trimmed and capped at ``MAX_NAME_LEN`` characters; an empty
    result is ``None``. Markup is NOT stripped: the UIs render names as text
    (``textContent`` on the web, ``Text`` on Android), never as HTML.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        ch
        for ch in value
        if not (ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in _INVISIBLE_CHARS)
    ).strip()
    cleaned = cleaned[:MAX_NAME_LEN].rstrip()
    return cleaned or None


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


_UPSERT = """
    INSERT INTO app_classification
        (appid, source, app_type, parent_appid, linked, name, checked_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (appid, source) DO UPDATE SET
        app_type     = excluded.app_type,
        parent_appid = excluded.parent_appid,
        linked       = excluded.linked,
        name         = excluded.name,
        checked_at   = excluded.checked_at
"""


# --------------------------------------------------------------------------
# Writing: the store (S1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreFacts:
    """What one GetItems item said about an app."""

    #: ``success == 1`` and the item was about the app asked for.
    answered: bool
    app_type: str | None = None
    parent_appid: int | None = None
    name: str | None = None

    @property
    def usable(self) -> bool:
        """The store said something about this id."""
        return self.answered and (
            self.app_type is not None
            or self.parent_appid is not None
            or self.name is not None
        )


def record_store_results(
    conn: sqlite3.Connection, facts: Mapping[int, StoreFacts], now: datetime
) -> None:
    """Store the store's answers in the app's ``store`` row. Commits.

    A usable answer replaces the store row; an unusable one removes it (the
    oracle row, if any, is a separate row and is never touched here). Apps
    the answer did not mention are not in ``facts`` and are not touched.
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
                _UPSERT,
                (
                    appid,
                    SOURCE_STORE,
                    valid_app_type(fact.app_type),
                    parent,
                    0,
                    valid_app_name(fact.name),
                    checked,
                ),
            )
        else:
            conn.execute(
                "DELETE FROM app_classification WHERE appid = ? AND source = ?",
                (appid, SOURCE_STORE),
            )
    conn.commit()


# --------------------------------------------------------------------------
# Writing: the oracle's rows (S2/S3, decision Q8 Weg B)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FillOutcome:
    written: int = 0
    removed: int = 0


def fill_from_oracle(conn: sqlite3.Connection, *, oracle_enabled: bool) -> FillOutcome:
    """Derive each tracked app's ``steamcmd_api`` row from what the oracle
    ALREADY stored. Makes no request. Commits.

    The row holds the app's own stored answer (``oracle_app_state``:
    ``app_type``, ``parent_appid``, ``name``; present only for apps the
    update check asked about, i.e. cached ones), plus ``linked`` when a
    cached parent's ``oracle_dlc_links`` name the app. Without an own parent,
    the parent is the linking parent when exactly one cached parent claims
    the app (two claimants: no parent, rather than a guess). The store row
    is a separate row: the merge happens on read. An app the oracle does
    not describe loses its previous oracle row.

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
    existing = {
        int(row["appid"]): (
            row["app_type"], row["parent_appid"], row["linked"], row["name"], row["checked_at"]
        )
        for row in conn.execute(
            "SELECT appid, app_type, parent_appid, linked, name, checked_at "
            "FROM app_classification WHERE source = ?",
            (SOURCE_STEAMCMD_API,),
        )
    }
    own: dict[int, tuple[str | None, int | None, str | None, str]] = {}
    for row in conn.execute(
        "SELECT appid, app_type, parent_appid, name, checked_at FROM oracle_app_state"
    ):
        appid = deletion.coerce_positive_id(row["appid"])
        if appid is None:
            continue
        own[appid] = (
            valid_app_type(row["app_type"]),
            deletion.coerce_positive_id(row["parent_appid"]),
            valid_app_name(row["name"]),
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
    for appid in sorted(tracked):
        own_type, own_parent, own_name, own_checked = own.get(
            appid, (None, None, None, None)
        )
        if own_parent == appid:
            own_parent = None
        claims = claimants.get(appid, {})
        link_parent = next(iter(claims)) if len(claims) == 1 else None
        linked = 1 if claims else 0
        parent = own_parent or link_parent
        stamps = [stamp for stamp in [own_checked, *claims.values()] if stamp]
        checked = max(stamps) if stamps else None

        if own_type is None and parent is None and own_name is None and not linked:
            if appid in existing:
                conn.execute(
                    "DELETE FROM app_classification WHERE appid = ? AND source = ?",
                    (appid, SOURCE_STEAMCMD_API),
                )
                removed += 1
            continue
        row_values = (own_type, parent, linked, own_name, checked)
        if existing.get(appid) == row_values:
            continue
        conn.execute(_UPSERT, (appid, SOURCE_STEAMCMD_API, *row_values[:4], checked))
        written += 1
    conn.commit()
    return FillOutcome(written=written, removed=removed)


# --------------------------------------------------------------------------
# Reading: what GET /v1/games returns
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """The merged view of one tracked app."""

    #: Displayed type: the store's, else the oracle's own ``common.type``,
    #: else ``dlc`` when a cached parent's link list names the app.
    app_type: str | None = None
    #: The resolved TOP-LEVEL parent, or ``None`` when the app is not grouped.
    parent_appid: int | None = None
    #: Fallback display name (store first, then the oracle's ``common.name``).
    name: str | None = None
    #: The app's OWN type (store or own ``common.type``), never inferred from
    #: a parent's link list. Not in the API; for TOOL-APPS-2b's delete set.
    own_app_type: str | None = None


UNCLASSIFIED = Classification()


def display_name(own: str | None, classification: Classification | None) -> str | None:
    """The name UIs show for an app: the ``apps.name`` vault-api recorded when
    it is non-blank, else Steam's name from ``classification`` (store, then
    the oracle's stored ``common.name``). Null when neither knows it. Shared
    by ``GET /v1/games`` and the grouped-delete plan."""
    if own is not None and own.strip():
        return own
    return None if classification is None else classification.name


def fallback_names(
    conn: sqlite3.Connection, appids: Iterable[int], *, oracle_enabled: bool
) -> dict[int, str]:
    """Steam's name for the given apps from the rows already stored (store
    first, then the oracle's while it is on). Read-only, no network, no
    lookup of its own: ids no source ever named are simply absent. Used by
    the traffic read paths, which only had ``apps.name`` before (ID-AUDIT)."""
    ids = sorted({a for a in appids if isinstance(a, int) and a > 0})
    names: dict[int, str] = {}
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        store: dict[int, str] = {}
        oracle: dict[int, str] = {}
        for row in conn.execute(
            "SELECT appid, source, name FROM app_classification "
            f"WHERE appid IN ({placeholders})",
            tuple(chunk),
        ):
            name = valid_app_name(row["name"])
            if name is None:
                continue
            if row["source"] == SOURCE_STORE:
                store[int(row["appid"])] = name
            elif row["source"] == SOURCE_STEAMCMD_API and oracle_enabled:
                oracle[int(row["appid"])] = name
        for appid in chunk:
            name = store.get(appid) or oracle.get(appid)
            if name:
                names[appid] = name
    return names


@dataclass(frozen=True)
class _Row:
    app_type: str | None
    parent_appid: int | None
    linked: bool
    name: str | None


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


def _first(*values):
    for value in values:
        if value is not None:
            return value
    return None


def resolve_classifications(
    conn: sqlite3.Connection, *, oracle_enabled: bool
) -> dict[int, Classification]:
    """``{appid: Classification}`` for every tracked app that has one.

    Read-only, two statements regardless of the library size. An app absent
    from the result is unclassified (``UNCLASSIFIED``).
    """
    tracked = {
        appid
        for appid in (
            deletion.coerce_positive_id(row["appid"])
            for row in conn.execute("SELECT appid FROM apps")
        )
        if appid is not None
    }
    store: dict[int, _Row] = {}
    from_oracle: dict[int, _Row] = {}
    for row in conn.execute(
        "SELECT appid, source, app_type, parent_appid, linked, name FROM app_classification"
    ):
        if row["source"] == SOURCE_STORE:
            target = store
        elif row["source"] == SOURCE_STEAMCMD_API and oracle_enabled:
            target = from_oracle
        else:
            continue
        appid = deletion.coerce_positive_id(row["appid"])
        if appid is None:
            continue
        parent = deletion.coerce_positive_id(row["parent_appid"])
        target[appid] = _Row(
            app_type=valid_app_type(row["app_type"]),
            parent_appid=None if parent == appid else parent,
            linked=row["linked"] == 1,
            name=valid_app_name(row["name"]),
        )

    none = _Row(None, None, False, None)
    raw_parent: dict[int, int | None] = {}
    for appid in store.keys() | from_oracle.keys():
        raw_parent[appid] = _first(
            store.get(appid, none).parent_appid, from_oracle.get(appid, none).parent_appid
        )

    result: dict[int, Classification] = {}
    for appid in tracked:
        s = store.get(appid, none)
        o = from_oracle.get(appid, none)
        own_type = _first(s.app_type, o.app_type)
        app_type = own_type if own_type is not None else ("dlc" if o.linked else None)
        parent = _top_parent(appid, raw_parent, tracked)
        name = _first(s.name, o.name)
        if app_type is None and parent is None and name is None:
            continue
        result[appid] = Classification(
            app_type=app_type, parent_appid=parent, name=name, own_app_type=own_type
        )
    return result
