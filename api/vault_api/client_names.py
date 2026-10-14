"""Operator-set client names (WP D15-NAMES, roadmap D15, user decision
2026-10-14 morning).

The operator can give a client a name. The vault itself only ever sees
**addresses** (the event log's ``$remote_addr``, an agent's stated LAN
addresses), so a name is keyed by address: ``(key_type, key)`` =
``("ip", "<canonical IP literal>")`` in ``client_names`` (schema v26).
``key_type = "mac"`` is reserved in the schema for the day an agent reports
its MAC address; no source reports MACs today and the API refuses it.

Which name a client shows -- the ONE rule
-----------------------------------------
``resolve_agent`` and ``resolve_address`` are the only places the rule lives;
``GET /v1/clients`` and ``GET /v1/clients/overview`` both call them, so the
two answers cannot disagree about a client's name.

* **Address client** (``kind: "address"`` in the overview): the name stored
  for its address, if any.
* **Agent client**: its addresses are the ones it OWNS
  (``agent_reports.address_owners``: the agent whose newest report
  correlates on the address, via ``effective_addrs`` since WP
  AGENT-ADDR-1). The first of those, in sorted order, that has a stored name
  gives the name. Owned, not "every retained address": a DHCP address that
  moved to another PC takes its name with it, so the old PC does not keep
  showing a name that now belongs to someone else.
* **An explicit name wins.** ``display_name`` is the stored name when there
  is one, else the agent's ``client_id`` (the PC name the agent reports),
  else the address.
* ``name_addr`` is the address a rename writes to: the address the current
  name is stored under, else the client's first owned address. ``None`` =
  the client cannot be named (an agent with no correlated address: an agent
  from before AGENT-ADDR-1 behind Docker's published port).

Deleting a client (``DELETE /v1/clients/{client_id}``) or the history
(``DELETE /v1/history``) never deletes names; a name is removed only by
``DELETE /v1/client-names/ip/{addr}``.
"""

from __future__ import annotations

import ipaddress
import sqlite3
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from vault_api.jobs import utcnow_iso

#: Key types the schema knows. ``mac`` is reserved (see the module docstring).
KEY_TYPE_IP = "ip"
KEY_TYPE_MAC = "mac"
KEY_TYPES = (KEY_TYPE_IP, KEY_TYPE_MAC)
#: Key types the API accepts today.
SUPPORTED_KEY_TYPES = (KEY_TYPE_IP,)

#: Longest name, in characters (code points), after trimming.
MAX_NAME_LENGTH = 40

#: Unicode categories a name must not contain: control characters (Cc, incl.
#: tab and newline), format characters (Cf: zero-width and bidi controls),
#: surrogates, private use, unassigned, and the line/paragraph separators.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

#: Major Unicode categories that count as visible: Letter, Number, Symbol,
#: Punctuation. A name needs at least one such character.
_VISIBLE_MAJOR = frozenset("LNSP")

#: Longest address text ``canonical_ip`` looks at (a full IPv6 literal is 39;
#: the rest is slack). Anything longer is refused before parsing.
_MAX_ADDR_TEXT = 64


def normalize_name(raw: object) -> str:
    """Validate a client name; return it trimmed. Raises ``ValueError``.

    Strict: a ``str`` that, after stripping surrounding whitespace, is 1 to
    ``MAX_NAME_LENGTH`` characters, contains no control, format,
    separator, private-use or unassigned character, and has at least one
    letter, number, symbol or punctuation character. Inner spaces are kept.
    """
    if not isinstance(raw, str):
        raise ValueError("name must be a string")
    name = raw.strip()
    if not name:
        raise ValueError("name must not be empty")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"name must be at most {MAX_NAME_LENGTH} characters")
    for character in name:
        if unicodedata.category(character) in _FORBIDDEN_CATEGORIES:
            raise ValueError(
                "name must not contain control or invisible formatting characters"
            )
    # Something visible must remain: a name of combining marks or odd
    # spaces alone would render as nothing. Same rule as the web dialog
    # (web/js/lib/client-names.js).
    if not any(unicodedata.category(character)[0] in _VISIBLE_MAJOR for character in name):
        raise ValueError(
            "name must contain at least one letter, number, symbol or punctuation character"
        )
    return name


def canonical_ip(raw: object) -> str:
    """The canonical spelling of an IP literal; raises ``ValueError``.

    Same canonical form ``agent_reports.normalize_local_addr`` produces (and
    vault-core's event log writes): Python's compressed IPv6 spelling, and
    the plain IPv4 address for an IPv4-mapped one. Unlike that function it
    accepts any address class (a loopback or link-local address can still
    appear in the cache log), but no zone id and no surrounding whitespace.
    """
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > _MAX_ADDR_TEXT
        or raw != raw.strip()
        or not raw.isascii()
    ):
        raise ValueError("address must be a plain IP address literal")
    if "%" in raw:
        raise ValueError("address must not carry a zone id")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is not an IP address") from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def lookup_ip(raw: object) -> str | None:
    """``canonical_ip`` for matching: ``None`` instead of raising."""
    try:
        return canonical_ip(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class ClientName:
    key_type: str
    key: str
    name: str
    updated_at: str


def _row(row: sqlite3.Row) -> ClientName:
    return ClientName(
        key_type=str(row["key_type"]),
        key=str(row["key"]),
        name=str(row["name"]),
        updated_at=str(row["updated_at"]),
    )


def list_names(conn: sqlite3.Connection) -> list[ClientName]:
    """Every stored name, ordered by key type and key."""
    rows = conn.execute(
        "SELECT key_type, key, name, updated_at FROM client_names ORDER BY key_type, key"
    ).fetchall()
    return [_row(row) for row in rows]


def ip_names(conn: sqlite3.Connection) -> dict[str, str]:
    """``{canonical ip: name}`` for every IP-keyed name."""
    rows = conn.execute(
        "SELECT key, name FROM client_names WHERE key_type = ?", (KEY_TYPE_IP,)
    ).fetchall()
    return {str(row["key"]): str(row["name"]) for row in rows}


def set_name(conn: sqlite3.Connection, key_type: str, key: str, name: str) -> ClientName:
    """Insert or replace one name. ``key``/``name`` must already be
    validated (``canonical_ip``/``normalize_name``). Commits."""
    updated_at = utcnow_iso()
    conn.execute(
        """
        INSERT INTO client_names (key_type, key, name, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (key_type, key) DO UPDATE SET
            name = excluded.name, updated_at = excluded.updated_at
        """,
        (key_type, key, name, updated_at),
    )
    conn.commit()
    return ClientName(key_type=key_type, key=key, name=name, updated_at=updated_at)


def clear_name(conn: sqlite3.Connection, key_type: str, key: str) -> bool:
    """Delete one name; ``False`` when there was none. Commits."""
    cursor = conn.execute(
        "DELETE FROM client_names WHERE key_type = ? AND key = ?", (key_type, key)
    )
    conn.commit()
    return int(cursor.rowcount or 0) > 0


@dataclass(frozen=True)
class ResolvedName:
    """What a client row carries (see the module docstring)."""

    #: The operator-set name, ``None`` when there is none.
    name: str | None
    #: The address a rename writes to; ``None`` = the client cannot be named.
    name_addr: str | None
    #: ``name`` if set, else the agent's client_id, else the address.
    display_name: str


def _first_named(names: Mapping[str, str], addrs: Iterable[str]) -> tuple[str | None, str | None]:
    canonical = sorted({c for c in (lookup_ip(a) for a in addrs) if c is not None})
    for addr in canonical:
        if addr in names:
            return names[addr], addr
    return None, (canonical[0] if canonical else None)


def owned_addrs(owners: Mapping[str, str], client_id: str) -> list[str]:
    """The addresses ``agent_reports.address_owners`` assigns to ``client_id``."""
    return sorted(addr for addr, owner in owners.items() if owner == client_id)


def resolve_agent(
    names: Mapping[str, str], owners: Mapping[str, str], client_id: str
) -> ResolvedName:
    """The name of an agent client: its OWNED addresses (``owners`` is
    ``agent_reports.address_owners``), first named one in sorted order."""
    name, addr = _first_named(names, owned_addrs(owners, client_id))
    return ResolvedName(name=name, name_addr=addr, display_name=name or client_id)


def resolve_address(names: Mapping[str, str], addr: str) -> ResolvedName:
    """The name of an agentless (address) client."""
    canonical = lookup_ip(addr)
    name = names.get(canonical) if canonical is not None else None
    return ResolvedName(name=name, name_addr=canonical, display_name=name or addr)
