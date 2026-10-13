"""Which apps a grouped delete removes together (WP TOOL-APPS-2b, ADR-0022 §4).

``DELETE /v1/cache/{appid}?with_packages=true`` (and its preview, ``GET
/v1/cache/{appid}/delete-plan?with_packages=true``) deletes a game together
with its content packages. The server builds the set itself from the stored
classification (``app_classification.resolve_classifications``); it never
trusts a client-sent list.

## Who is a package

A tracked app joins the delete set as a package only when ALL of these hold:

* its resolved top-level parent (``Classification.parent_appid``, the same
  value ``GET /v1/games`` returns) is the requested app;
* its OWN type (``Classification.own_app_type``: the store's type, else the
  app's own PICS ``common.type``) is one of ``PACKAGE_TYPES``. A ``dlc``
  inferred only because a cached parent's ``extended.listofdlc`` / depot
  ``dlcappid`` names the app is display data and is NOT enough;
* it is not on the fixed tool list (``tool_apps``). The classification read
  already never groups those; the check is repeated here because this is the
  path that removes data.

Every other app grouped under the parent (typed ``game``, ``application``,
``tool``, unknown, or only linked) is reported as ``kept_children`` and stays
an ordinary outside app: it protects its depots like any other co-owner
(ADR-0003 remnant rule) and is never deleted by this request.

## What this widens, stated openly

Store or oracle classification decides who is a package, so it can widen
what a user-confirmed delete removes; see ``oracle.py``'s safety-invariant
section for the bounds.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from vault_api import app_classification, deletion, tool_apps

#: The own types that make a grouped child deletable with its parent
#: (ADR-0022 §4). ``game``, ``application``, ``tool`` and unknown are not.
PACKAGE_TYPES = frozenset({"dlc", "config", "music", "video", "demo"})

ROLE_PARENT = "parent"
ROLE_PACKAGE = "package"

REASON_NOT_A_PACKAGE_TYPE = "not_a_package_type"
REASON_TYPE_ONLY_INFERRED = "type_only_inferred"
REASON_TYPE_UNKNOWN = "type_unknown"


@dataclass(frozen=True)
class Member:
    appid: int
    role: str
    name: str | None
    app_type: str | None


@dataclass(frozen=True)
class KeptChild:
    appid: int
    name: str | None
    app_type: str | None
    #: ``not_a_package_type`` (own type game/application/tool),
    #: ``type_only_inferred`` (only a parent's DLC list names it) or
    #: ``type_unknown``.
    reason: str


@dataclass(frozen=True)
class GroupSelection:
    delete_set: deletion.DeleteSet
    #: Parent first, then packages by appid.
    members: tuple[Member, ...]
    kept_children: tuple[KeptChild, ...]


def _names(conn: sqlite3.Connection) -> dict[int, str | None]:
    return {
        int(row["appid"]): row["name"]
        for row in conn.execute("SELECT appid, name FROM apps")
        if deletion.coerce_positive_id(row["appid"]) is not None
    }


def select_group(
    conn: sqlite3.Connection,
    parent: int,
    *,
    with_packages: bool,
    oracle_enabled: bool,
) -> GroupSelection:
    """The delete set for ``parent``.

    Without ``with_packages``, or for a fixed-list tool app, it is the
    one-member set, returned BEFORE any classification is read: the plain
    delete never touches ``app_classification`` (its member carries only the
    ``apps.name``, one ``SELECT``, and no type).
    """
    if not with_packages or tool_apps.is_tool_app(parent):
        row = conn.execute("SELECT name FROM apps WHERE appid = ?", (parent,)).fetchone()
        own = None if row is None else row["name"]
        name = own if own is not None and own.strip() else None
        return GroupSelection(
            deletion.as_delete_set(parent), (Member(parent, ROLE_PARENT, name, None),), ()
        )

    classes = app_classification.resolve_classifications(
        conn, oracle_enabled=oracle_enabled
    )
    names = _names(conn)

    def name_of(appid: int) -> str | None:
        return app_classification.display_name(names.get(appid), classes.get(appid))

    parent_class = classes.get(parent, app_classification.UNCLASSIFIED)
    parent_member = Member(parent, ROLE_PARENT, name_of(parent), parent_class.app_type)

    packages: list[Member] = []
    kept: list[KeptChild] = []
    for appid in sorted(classes):
        cls = classes[appid]
        if appid == parent or cls.parent_appid != parent:
            continue
        if tool_apps.is_tool_app(appid):  # pragma: no cover - never grouped on read
            continue
        if cls.own_app_type in PACKAGE_TYPES:
            packages.append(Member(appid, ROLE_PACKAGE, name_of(appid), cls.app_type))
            continue
        if cls.own_app_type is not None:
            reason = REASON_NOT_A_PACKAGE_TYPE
        elif cls.app_type is not None:
            reason = REASON_TYPE_ONLY_INFERRED
        else:
            reason = REASON_TYPE_UNKNOWN
        kept.append(KeptChild(appid, name_of(appid), cls.app_type, reason))

    delete_set = deletion.DeleteSet(
        parent, frozenset({parent, *(member.appid for member in packages)})
    )
    return GroupSelection(delete_set, (parent_member, *packages), tuple(kept))
