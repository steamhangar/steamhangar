"""Agent report storage and the ADR-0002 server-side diff (WP 2.4).

The contract (plan §3 "vault-agent", ADR-0002 decision 2): the agent is
**stateless and dumb**. It reports the COMPLETE list of installed Steam app
ids every time; vault-api stores that as a full-list snapshot and derives
additions *and removals* by diffing consecutive snapshots per ``client_id``.
No delta logic on the device.

What this module deliberately does NOT do
-----------------------------------------
A removal is **surfaced**, not acted on:

* no cache content is deleted (plan A9 keeps deletion a human/API decision —
  ``DELETE /v1/cache/{appid}``),
* ``apps.status`` is not changed and no ``apps`` row is created,
* nothing is queued.

An agent report is an *observation about a client machine*, not a statement
about the server's cache. Phase 3's scheduler is the component that turns
these snapshots into a prefill set (plan §7 Phase 2, last bullet); until then
the data path just records and reports.

Ordering: the "previous" snapshot is the previous row by SQLite ``rowid``,
i.e. by **insertion order**, not by ``reported_at``. ``reported_at`` has
second precision and comes from the server clock, so two reports inside one
second tie and a clock that steps backwards would reorder the chain. rowid
cannot do either. (Pruning only ever removes the *oldest* rows, so the
maximum rowid is monotonic and is never recycled.)
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Literal, Sequence

from vault_api import is_valid_version, peer_hops
from vault_api.jobs import immediate_transaction, parse_utc_iso, to_utc_iso, utcnow_iso

logger = logging.getLogger(__name__)

#: Longest accepted ``client_id`` (characters). A client id is an operator-set
#: label (hostname, "steam-deck", ...), it lands in log lines and in a TEXT
#: primary lookup column — 64 characters is generous for that and keeps a
#: buggy agent from writing kilobyte-long ids.
MAX_CLIENT_ID_LENGTH = 64

#: Upper bound on app ids in one report. The largest real Steam libraries are
#: a few thousand titles; this only stops a broken agent from pushing a
#: multi-megabyte JSON blob into SQLite on every 30-minute tick.
MAX_APPIDS_PER_REPORT = 10_000

#: Hard floor for the retention setting: the diff needs the previous snapshot
#: *plus* the one being written, so fewer than 2 rows per client would make
#: every report look like a first report.
MIN_REPORTS_KEPT = 2

#: Longest accepted source address (schema v9, WP 3.11). An IPv6 address with a
#: zone id fits in 45 characters; 64 leaves room for other peer spellings an
#: ASGI server might report.
MAX_SOURCE_ADDR_LENGTH = 64

#: Most LAN addresses one report may state (schema v24, WP AGENT-ADDR-1). The
#: agent sends the source address it would use toward each address the vault
#: host resolves to -- normally one IPv4 and one IPv6. Eight leaves room for a
#: host name with several A/AAAA records; mirrored by ``agent/go/report``.
MAX_LOCAL_ADDRS = 8

#: How many removed/added ids a single log line prints before it summarizes.
_LOG_ID_SAMPLE = 50

#: Accepted range for a report's ``report_interval_seconds`` (schema v17,
#: WP AGENT-FEAT-1): one minute to one day. Below a minute is not a schedule
#: anybody runs a Steam library report on; above a day would keep a dead
#: machine "online" for more than two days. Mirrored by the agent
#: (``agent/go/report``), which leaves an out-of-range interval out rather
#: than have the whole report rejected.
MIN_REPORT_INTERVAL_SECONDS = 60
MAX_REPORT_INTERVAL_SECONDS = 24 * 60 * 60

#: The interval the presence rule assumes when a client did not state one
#: (user decision 2026-10-03, "Weg B"): every agent before AGENT-FEAT-1 ran
#: on the 30-minute schedule the old packaging installed.
ASSUMED_REPORT_INTERVAL_SECONDS = 30 * 60

#: Slack on top of two missed intervals before a client counts as offline
#: (same decision): a report that is late because the machine was busy
#: waking up or the network came up slowly does not flip it.
PRESENCE_GRACE_SECONDS = 5 * 60

Presence = Literal["online", "offline"]


@dataclass(frozen=True)
class StoredSnapshot:
    """One ``agent_reports`` row as read back.

    ``appids`` is ``None`` when the stored JSON could not be decoded — see
    ``_decode_appids``.
    """

    rowid: int
    reported_at: str
    appids: list[int] | None
    #: What the agent said about itself in this report (schema v17). ``None``
    #: when it did not say, or when the stored value is not one the API
    #: would have accepted (a hand-edited row) -- see ``_stored_version``.
    agent_version: str | None = None
    report_interval_seconds: int | None = None


@dataclass(frozen=True)
class ReportResult:
    """Outcome of one ``POST /v1/agent/installed``."""

    client_id: str
    reported_at: str
    #: Number of DISTINCT app ids stored (duplicates in the request collapse).
    received: int
    added: list[int]
    removed: list[int]
    #: True when there was no usable previous snapshot for this client.
    first_report: bool
    #: How many old snapshots retention removed in the same transaction.
    pruned: int


@dataclass(frozen=True)
class ClientSummary:
    """One row of ``GET /v1/clients`` (minimal v1 — see the router)."""

    client_id: str
    #: Oldest RETAINED report's timestamp, not necessarily the true first
    #: contact: retention prunes older snapshots away (see ``prune_reports``).
    first_seen: str
    last_reported_at: str
    #: Size of the latest snapshot; ``None`` if that row's JSON was unreadable.
    app_count: int | None
    #: The addresses this client is correlated with cache traffic on: the
    #: union of ``effective_addrs`` over its RETAINED reports (WP AGENT-ADDR-1;
    #: before that, every report's TCP peer, schema v9). Per report that is
    #: the LAN addresses the agent stated (``local_addrs``), else the TCP peer
    #: unless it is a forwarding hop (``vault_api/peer_hops.py``). Usually
    #: one or two; more when the machine changed address (DHCP lease, wifi vs.
    #: cable) within the retained window. Empty when no retained report gives
    #: a usable address -- every report written before schema v9, and an old
    #: agent behind Docker's published port whose only peer is the bridge
    #: gateway -- and that is the case that must never be read as "this
    #: client is bypassing the cache" (see ``routers/clients.py``).
    source_addrs: list[str] = field(default_factory=list)
    #: Raw TCP peers of the retained reports, hops included (diagnostics).
    peer_addrs: list[str] = field(default_factory=list)
    #: LAN addresses the agent itself stated in its retained reports
    #: (schema v24); empty for agents from before AGENT-ADDR-1.
    reported_addrs: list[str] = field(default_factory=list)
    #: From the LATEST snapshot (schema v17, WP AGENT-FEAT-1): the agent's
    #: build version and report interval, ``None`` when that report did not
    #: carry them. Deliberately not "the last value ever sent": an agent
    #: downgraded to a build without the fields reads as unknown again.
    agent_version: str | None = None
    report_interval_seconds: int | None = None


def normalize_source_addr(raw: str | None) -> str | None:
    """Validate the peer address of an agent report; ``None`` if unusable.

    This value is the join key between agent reports and event-log lines
    (ADR-0008's client identity story), it is written into log lines, and it is
    returned in an API response. It comes from the ASGI server rather than from
    the request body, so it is not attacker-chosen in any normal deployment —
    but "not normally attacker-chosen" is not a reason to store it unchecked,
    and a ``None`` here is perfectly serviceable (it means "cannot correlate",
    which every consumer already has to handle for pre-v9 rows).

    Bounded, ASCII, printable, whitespace-free — the same shape the event-log
    parser demands of field 3, because a value that cannot appear on that side
    can never correlate with anything on this one.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > MAX_SOURCE_ADDR_LENGTH:
        return None
    if not value.isascii() or not value.isprintable():
        return None
    if any(character.isspace() for character in value):
        return None
    return value


def normalize_local_addr(raw: str) -> str:
    """Validate one agent-stated LAN address (WP AGENT-ADDR-1); canonical form.

    Raises ``ValueError`` for anything but a plain unicast IP literal another
    host could reach this PC on: not an IP literal, a zone id (``%eth0``),
    surrounding whitespace, loopback, unspecified, link-local or multicast.
    The agent applies the same filter before sending (``agent/go/report``),
    so a rejection here means a broken or hostile agent, not a quirk.

    The canonical form is what vault-core's event log writes for
    ``$remote_addr``: Python's compressed spelling for IPv6, and the plain
    IPv4 address for an IPv4-mapped one (``::ffff:192.0.2.7``).
    """
    if not isinstance(raw, str) or raw != raw.strip() or not raw.isascii():
        raise ValueError("local_addrs entries must be plain IP address literals")
    if "%" in raw:
        raise ValueError("local_addrs entries must not carry a zone id")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        raise ValueError(f"local_addrs entry {raw!r} is not an IP address") from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if (
        address.is_loopback
        or address.is_unspecified
        or address.is_link_local
        or address.is_multicast
    ):
        raise ValueError(
            f"local_addrs entry {raw!r} is loopback, unspecified, link-local or "
            "multicast; only addresses other hosts can reach count"
        )
    return str(address)


def normalize_local_addrs(values: Sequence[str]) -> list[str]:
    """Validate a ``local_addrs`` list: at most ``MAX_LOCAL_ADDRS`` entries,
    each through ``normalize_local_addr``; returned de-duplicated and sorted.
    Raises ``ValueError`` on the first bad entry."""
    if len(values) > MAX_LOCAL_ADDRS:
        raise ValueError(f"local_addrs holds at most {MAX_LOCAL_ADDRS} addresses")
    return sorted({normalize_local_addr(value) for value in values})


def _decode_local_addrs(raw: object) -> list[str] | None:
    """A stored ``local_addrs`` column as a list, ``None`` when absent or not
    a list the API would have accepted (a hand-edited row reads as "the agent
    said nothing", the same degrade as ``_stored_version``)."""
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(decoded, list):
        return None
    try:
        addrs = normalize_local_addrs(decoded)
    except (ValueError, TypeError):
        return None
    return addrs or None


def effective_addrs(
    source_addr: object, local_addrs: object, is_hop: "peer_hops.HopFilter"
) -> list[str]:
    """The addresses ONE stored report correlates on (WP AGENT-ADDR-1).

    * The agent stated LAN addresses (``local_addrs``, schema v24): exactly
      those. The TCP peer is ignored then -- behind Docker's published port it
      is the bridge gateway, and every PC shares it.
    * It did not (an agent from before AGENT-ADDR-1, or one that found no
      usable address): the TCP peer, unless it is a forwarding hop
      (``vault_api/peer_hops.py``: loopback or a gateway of vault-api's own
      routing table).
    * Neither: nothing. Such a report cannot be correlated, so it can neither
      count traffic for the client nor make the bypass rule accuse it.

    The ONE place this rule lives: ``list_clients`` (``GET /v1/clients``, the
    bypass rule and its webhook) and ``address_owners`` (the D15b overview)
    both call it.
    """
    stated = _decode_local_addrs(local_addrs)
    if stated:
        return stated
    peer = normalize_source_addr(source_addr) if isinstance(source_addr, str) else None
    if peer is None or is_hop(peer):
        return []
    return [peer]


def valid_report_interval(value: object) -> bool:
    """True for an ``int`` (not a ``bool``) within
    ``MIN_REPORT_INTERVAL_SECONDS..MAX_REPORT_INTERVAL_SECONDS``."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_REPORT_INTERVAL_SECONDS <= value <= MAX_REPORT_INTERVAL_SECONDS
    )


def _stored_version(raw: object) -> str | None:
    """A stored ``agent_version`` as the API may show it, else ``None``.

    The request model already validates both v17 fields, so a bad value can
    only come from a hand-edited database. It reads as "unknown" rather than
    being echoed, the same way ``_decode_appids`` degrades a corrupt row.
    """
    return raw if isinstance(raw, str) and is_valid_version(raw) else None


def _stored_interval(raw: object) -> int | None:
    return raw if valid_report_interval(raw) else None


def presence(
    last_reported_at: str,
    report_interval_seconds: int | None,
    now: datetime,
) -> tuple[Presence, str | None]:
    """``(presence, offline_after)`` for a client (WP AGENT-FEAT-1).

    The rule (user decision 2026-10-03, "Weg B"): a client is **offline**
    once its last report is older than ``2 x interval + 5 minutes``, and
    **online** until then -- at exactly that age it is still online. The
    interval is the one the agent stated in its latest report, or
    ``ASSUMED_REPORT_INTERVAL_SECONDS`` (30 minutes) when it stated none.
    There is no third state.

    ``offline_after`` is the UTC timestamp at which the client turns
    offline if no further report arrives. An unreadable ``last_reported_at``
    (hand-edited row) gives ``("offline", None)``: online is a claim this
    function can only make from a timestamp it understands.

    The one place the rule lives: ``GET /v1/clients`` calls this per client,
    and anything else that ever needs presence must call it too.
    """
    last = parse_utc_iso(last_reported_at)
    if last is None:
        return "offline", None
    interval = (
        report_interval_seconds
        if valid_report_interval(report_interval_seconds)
        else ASSUMED_REPORT_INTERVAL_SECONDS
    )
    deadline = last + timedelta(seconds=2 * interval + PRESENCE_GRACE_SECONDS)
    state: Presence = "online" if now <= deadline else "offline"
    return state, to_utc_iso(deadline)


def normalize_appids(appids: Iterable[int]) -> list[int]:
    """Sorted, de-duplicated app id list — the canonical stored form.

    A snapshot is conceptually a *set* ("what is installed right now"), so the
    stored order carries no information and duplicates in the request body are
    collapsed rather than rejected: a library listing the same app twice is a
    quirk of the reporter, not something the operator can act on.
    """
    return sorted({int(appid) for appid in appids})


def _decode_appids(raw: object, client_id: str, rowid: int) -> list[int] | None:
    """Decode a stored ``appids`` JSON array; ``None`` if the row is unusable.

    A corrupt row (hand-edited database, truncated write) must not wedge the
    endpoint forever — it degrades to "no usable previous snapshot", which
    restarts the diff chain at the next report, and says so loudly in the log.
    """
    if not isinstance(raw, str):
        logger.warning(
            "agent-report client=%r snapshot rowid=%d has a non-text appids "
            "column (%s); ignoring it for the diff",
            client_id, rowid, type(raw).__name__,
        )
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "agent-report client=%r snapshot rowid=%d holds unparseable JSON; "
            "ignoring it for the diff (the next report restarts the chain)",
            client_id, rowid,
        )
        return None
    if not isinstance(decoded, list):
        logger.warning(
            "agent-report client=%r snapshot rowid=%d holds JSON %s, expected a "
            "list; ignoring it for the diff",
            client_id, rowid, type(decoded).__name__,
        )
        return None
    # bool is an int subclass — exclude it explicitly so a stray `true` does
    # not silently become app id 1.
    return sorted(
        {value for value in decoded if isinstance(value, int) and not isinstance(value, bool)}
    )


def latest_snapshot(conn: sqlite3.Connection, client_id: str) -> StoredSnapshot | None:
    """The client's most recently INSERTED snapshot, or None if it has none.

    Ordered by ``rowid`` (insertion order) rather than ``reported_at`` — see
    the module docstring. ``idx_agent_reports_client_time`` narrows the scan to
    this client's rows; retention keeps that at a handful, so the tiny sort by
    rowid on top of it is free.
    """
    row = conn.execute(
        """
        SELECT rowid AS rowid, reported_at, appids,
               agent_version, report_interval_seconds
        FROM agent_reports
        WHERE client_id = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (client_id,),
    ).fetchone()
    if row is None:
        return None
    rowid = int(row["rowid"])
    return StoredSnapshot(
        rowid=rowid,
        reported_at=str(row["reported_at"]),
        appids=_decode_appids(row["appids"], client_id, rowid),
        agent_version=_stored_version(row["agent_version"]),
        report_interval_seconds=_stored_interval(row["report_interval_seconds"]),
    )


def prune_reports(conn: sqlite3.Connection, client_id: str, keep: int) -> int:
    """Keep only this client's ``keep`` newest snapshots. Returns rows removed.

    Called inside the insert transaction, so a client that reports every 30
    minutes forever holds a bounded number of rows instead of growing the table
    without limit (the retention gap the WP 1.2 review left open).

    Only the *oldest* rows go, which is what keeps the newest rowid monotonic
    and the diff chain intact. ``keep`` is clamped to ``MIN_REPORTS_KEPT``
    defensively — the config layer already enforces it, and a caller passing 1
    would silently turn every report into a first report.
    """
    keep = max(MIN_REPORTS_KEPT, int(keep))
    cursor = conn.execute(
        """
        DELETE FROM agent_reports
        WHERE client_id = ?
          AND rowid NOT IN (
              SELECT rowid FROM agent_reports
              WHERE client_id = ?
              ORDER BY rowid DESC
              LIMIT ?
          )
        """,
        (client_id, client_id, keep),
    )
    return int(cursor.rowcount or 0)


def _sample(ids: Sequence[int]) -> str:
    """Render an id list for a log line without dumping thousands of numbers."""
    if len(ids) <= _LOG_ID_SAMPLE:
        return str(list(ids))
    return f"{list(ids[:_LOG_ID_SAMPLE])} (+{len(ids) - _LOG_ID_SAMPLE} more)"


def store_report(
    conn: sqlite3.Connection,
    client_id: str,
    appids: Sequence[int],
    keep: int,
    source_addr: str | None = None,
    agent_version: str | None = None,
    report_interval_seconds: int | None = None,
    local_addrs: Sequence[str] | None = None,
) -> ReportResult:
    """Store one full-list snapshot and diff it against the previous one.

    Everything that must not interleave happens inside ONE
    ``BEGIN IMMEDIATE`` transaction (``jobs.immediate_transaction``): read the
    previous snapshot, insert the new one, prune. Without that write lock two
    reports from the same client arriving together would both read the same
    "previous" row and both report the same additions — the diff chain would
    fork. With it they serialize: the loser sees the winner's snapshot as its
    predecessor, so every snapshot is diffed against exactly one other,
    whichever order the two land in.

    The removals are logged (audit) and returned. Nothing else happens to
    them — see the module docstring.
    """
    stored = normalize_appids(appids)
    payload = json.dumps(stored, separators=(",", ":"))
    reported_at = utcnow_iso()
    # Schema v9 (WP 3.11): recorded, never required. An unusable or absent peer
    # address stores NULL and the report itself is completely unaffected —
    # correlation is a nice-to-have on top of the report, not a precondition
    # for accepting one.
    stored_addr = normalize_source_addr(source_addr)
    # Schema v17 (WP AGENT-FEAT-1): the router's request model has already
    # validated both; re-checked here so no caller can store a value the API
    # would refuse to show.
    if agent_version is not None and not is_valid_version(agent_version):
        raise ValueError("agent_version must match the VER-1 version grammar")
    if report_interval_seconds is not None and not valid_report_interval(
        report_interval_seconds
    ):
        raise ValueError(
            f"report_interval_seconds must be an int in "
            f"{MIN_REPORT_INTERVAL_SECONDS}..{MAX_REPORT_INTERVAL_SECONDS}"
        )

    # Schema v24 (WP AGENT-ADDR-1): validated by the request model already;
    # re-checked for the same reason. An empty list stores NULL ("the agent
    # stated nothing"), so correlation falls back to the TCP peer.
    stated_addrs = normalize_local_addrs(local_addrs) if local_addrs is not None else []
    stored_local = (
        json.dumps(stated_addrs, separators=(",", ":")) if stated_addrs else None
    )

    with immediate_transaction(conn):
        previous = latest_snapshot(conn, client_id)
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr, "
            "agent_version, report_interval_seconds, local_addrs) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                client_id,
                reported_at,
                payload,
                stored_addr,
                agent_version,
                report_interval_seconds,
                stored_local,
            ),
        )
        pruned = prune_reports(conn, client_id, keep)

    previous_ids = None if previous is None else previous.appids
    first_report = previous_ids is None
    if first_report:
        added = list(stored)
        removed: list[int] = []
    else:
        current_set = set(stored)
        previous_set = set(previous_ids or ())
        added = sorted(current_set - previous_set)
        removed = sorted(previous_set - current_set)

    logger.info(
        "agent-report client=%r stored snapshot: reported_at=%s apps=%d "
        "first_report=%s added=%d removed=%d pruned=%d agent_version=%s "
        "report_interval_seconds=%s source_addr=%s local_addrs=%s",
        client_id, reported_at, len(stored), first_report,
        len(added), len(removed), pruned,
        agent_version if agent_version is not None else "-",
        report_interval_seconds if report_interval_seconds is not None else "-",
        stored_addr if stored_addr is not None else "-",
        ",".join(stated_addrs) if stated_addrs else "-",
    )
    if removed:
        # Audit line, deliberately separate and explicit about the boundary:
        # this is the one event an operator may want to grep for, and the one
        # place where somebody might expect vault-api to "clean up" by itself.
        # ASCII only, deliberately: this line is meant to be grepped out of a
        # console/json-file log, and a Windows console at codepage 850 renders
        # an em dash as a replacement character (observed during verification).
        logger.info(
            "agent-report client=%r REMOVED %d app(s) from the client library: "
            "%s - cache content is NOT deleted and apps.status is NOT changed "
            "(ADR-0002: removals are surfaced; deletion stays a human/API "
            "decision, plan A9)",
            client_id, len(removed), _sample(removed),
        )

    return ReportResult(
        client_id=client_id,
        reported_at=reported_at,
        received=len(stored),
        added=added,
        removed=removed,
        first_report=first_report,
        pruned=pruned,
    )


def list_clients(
    conn: sqlite3.Connection, is_hop: "peer_hops.HopFilter | None" = None
) -> list[ClientSummary]:
    """Every client that has ever reported (and still has a retained row).

    Two steps rather than one clever query: a GROUP BY for ``first_seen``, then
    ``latest_snapshot`` per client for the current app count and timestamp.
    The follow-up query runs once per *gaming machine* — a homelab has a
    handful — and it keeps ``last_reported_at`` and ``app_count`` derived from
    the same row, which a ``MAX(reported_at)`` aggregate would not guarantee
    (see the rowid-vs-timestamp note in the module docstring).

    ``is_hop`` defaults to the routing tables as they are now
    (``peer_hops.forwarding_hop_filter``); a caller passes its own only to
    share one snapshot across several lookups.
    """
    hop = is_hop if is_hop is not None else peer_hops.forwarding_hop_filter()
    rows = conn.execute(
        """
        SELECT client_id, MIN(reported_at) AS first_seen
        FROM agent_reports
        GROUP BY client_id
        ORDER BY client_id
        """
    ).fetchall()

    summaries: list[ClientSummary] = []
    for row in rows:
        client_id = str(row["client_id"])
        latest = latest_snapshot(conn, client_id)
        # WP AG-1: no longer provably unreachable, same reasoning as
        # ``scheduler.fresh_client_snapshots``'s identical guard —
        # ``delete_client`` committing between the GROUP BY above and this
        # lookup now makes ``latest_snapshot`` return None for a client_id
        # this loop just read. Exercised directly by
        # ``tests/test_clients_api.py::
        # test_a_client_deleted_between_the_group_by_and_the_lookup_is_silently_skipped``.
        if latest is None:
            continue
        addrs = _client_addrs(conn, client_id, hop)
        summaries.append(
            ClientSummary(
                client_id=client_id,
                first_seen=str(row["first_seen"]),
                last_reported_at=latest.reported_at,
                app_count=None if latest.appids is None else len(latest.appids),
                source_addrs=addrs.effective,
                peer_addrs=addrs.peers,
                reported_addrs=addrs.reported,
                agent_version=latest.agent_version,
                report_interval_seconds=latest.report_interval_seconds,
            )
        )
    return summaries


def delete_client(conn: sqlite3.Connection, client_id: str) -> bool:
    """Remove every row keyed on this ``client_id`` (WP AG-1). Returns whether
    the client existed.

    **This is not a ban.** It clears the rename-cleanup ghost row
    ``agent/README.md``'s "Client identity and renaming" section named as
    AG-1's planned fix: a client that reports again after being deleted
    simply starts a fresh diff chain, exactly like a brand-new machine — see
    ``routers/clients.py`` / ``api/README.md`` for the operator-facing
    wording.

    **Tables touched — the full set keyed on ``client_id``, established by
    reading ``db.py``'s DDL rather than assumed:**

    * ``agent_reports`` — every retained snapshot for this client. This is
      the table that makes a client "exist" at all (``list_clients`` groups
      by it); deleting all of it is what makes the ghost row disappear from
      ``GET /v1/clients``.
    * ``client_bypass_state`` — the webhook feature's last-computed
      ``bypass_suspected`` verdict for this client (WP 3.13). Deleted too, on
      purpose: if this client_id starts reporting again, the webhook
      transition-detector should establish a fresh baseline rather than
      compare against a verdict computed for what is, semantically, a
      different machine now.

    **Tables deliberately NOT touched, and why nothing dangles:**

    * ``client_cache_stats`` is keyed on ``client_addr`` (a network address),
      not ``client_id`` — schema v9's bridge between the two is
      ``agent_reports.source_addr``, a column on the table THIS function
      does clear. Deleting the client's agent reports simply severs its
      contribution to ``GET /v1/clients``' per-client totals (computed via
      ``event_sweep.totals_for_addrs`` over ``ClientSummary.source_addrs``, which now
      returns nothing for this client_id); the address rows themselves are
      cache-traffic history, not client identity, and outlive the client
      exactly like they outlive an agent that simply stops reporting.
    * ``depot_miss_stats`` / ``miss_trigger_state`` are keyed on ``depotid``/
      ``appid`` — cache content, not client identity (plan §4: "cache
      content is keyed by app, not client"). Nothing here references a
      client_id at all, so deleting one leaves these completely untouched,
      verified by reading their DDL rather than asserted.
    * ``jobs`` / ``depot_app_map`` / ``apps`` / manifests / oracle tables:
      none of these have a client_id column. A prefill job this client's
      installed-list once caused to be enqueued is an app-keyed row with no
      back-reference to the client that triggered it — there is nothing to
      cascade.

    **Concurrency (this project's established ``BEGIN IMMEDIATE`` idiom,
    same as ``store_report``'s read-then-write above):** the existence check
    and both deletes run inside ONE write-locked transaction, so no other
    writer can insert a row for this client_id between "does it exist" and
    "delete it". Two interleavings with a concurrent
    ``POST /v1/agent/installed`` for the SAME client_id are both possible and
    both acceptable (see ``api/README.md``'s "Deleting a client" section for
    the full write-up):

    1. **This DELETE's transaction commits first.** The report that was
       waiting on the write lock then runs ``store_report`` against an empty
       history: ``latest_snapshot`` returns ``None``, so it is treated as a
       genuine first report (``first_report=True``, ``added`` = every
       reported appid, nothing "removed"). Exactly the documented
       reappearance behaviour — a fresh diff chain, as if the machine had
       never reported before.
    2. **The report's transaction commits first.** Its new snapshot (and any
       pruned old rows) land, then this DELETE's transaction runs and removes
       EVERYTHING for that client_id, including the report that just landed.
       The operator's delete therefore wins the race and the just-arrived
       install is gone too — a report that lands microseconds before an
       explicit "remove this client" request is swept up in the same
       operator intent, the same class of accepted race
       ``DELETE /v1/cache/{appid}`` already documents against the scheduler
       (a job enqueued microseconds after a guard check "refills what was
       deleted" — here the operator can simply have the agent report again).

    A read that is not inside either transaction (``GET /v1/clients``,
    ``scheduler.fresh_client_snapshots``/``compute_targets`` mid-sweep) can
    observe the client_id from a ``DISTINCT``/``GROUP BY`` query and then find
    ``latest_snapshot`` return ``None`` for it a moment later, if this DELETE
    's transaction commits in between the two reads. Both call sites already
    handle that shape defensively (``if snapshot/latest is None: continue``)
    — before this endpoint existed the branch was unreachable in practice
    (nothing else could remove a client's LAST row), so this package makes it
    reachable for the first time; the existing degrade-gracefully behaviour
    (silently skip that client for this one read) is exactly correct, not a
    new gap.
    """
    with immediate_transaction(conn):
        exists = (
            conn.execute(
                "SELECT 1 FROM agent_reports WHERE client_id = ? LIMIT 1",
                (client_id,),
            ).fetchone()
            is not None
        )
        if not exists:
            return False
        conn.execute("DELETE FROM agent_reports WHERE client_id = ?", (client_id,))
        conn.execute(
            "DELETE FROM client_bypass_state WHERE client_id = ?", (client_id,)
        )
    return True


@dataclass(frozen=True)
class _ClientAddrs:
    effective: list[str]
    peers: list[str]
    reported: list[str]


def _client_addrs(
    conn: sqlite3.Connection, client_id: str, is_hop: "peer_hops.HopFilter"
) -> _ClientAddrs:
    """A client's addresses across its RETAINED reports.

    Every retained report, not only the newest: a laptop that moved from wifi
    to cable an hour ago still has cache-log traffic under the old address, and
    dropping it would make the machine look half-silent. Retention bounds the
    set naturally -- ``VAULT_AGENT_REPORT_KEEP`` rows, each with at most
    ``MAX_LOCAL_ADDRS`` stated addresses plus one peer.
    """
    rows = conn.execute(
        "SELECT source_addr, local_addrs FROM agent_reports WHERE client_id = ?",
        (client_id,),
    ).fetchall()
    effective: set[str] = set()
    peers: set[str] = set()
    reported: set[str] = set()
    for row in rows:
        effective.update(effective_addrs(row["source_addr"], row["local_addrs"], is_hop))
        if isinstance(row["source_addr"], str):
            peers.add(row["source_addr"])
        reported.update(_decode_local_addrs(row["local_addrs"]) or ())
    return _ClientAddrs(sorted(effective), sorted(peers), sorted(reported))


def address_owners(
    conn: sqlite3.Connection, is_hop: "peer_hops.HopFilter | None" = None
) -> dict[str, str]:
    """``{address: client_id}`` for the D15b clients overview.

    Each address belongs to the ONE client whose newest report correlates on
    it (``effective_addrs``), so a DHCP address that moved from one PC to
    another counts for the PC that has it now; on an exact ``reported_at`` tie
    the smaller client_id wins, so the answer is deterministic. A forwarding
    hop (the Docker bridge gateway an old agent's report arrived from) is
    never anybody's address -- before WP AGENT-ADDR-1 it was assigned to
    whichever agent reported last, which then carried every other PC's
    traffic.
    """
    hop = is_hop if is_hop is not None else peer_hops.forwarding_hop_filter()
    rows = conn.execute(
        "SELECT client_id, reported_at, source_addr, local_addrs FROM agent_reports"
    ).fetchall()
    best: dict[str, tuple[str, str]] = {}
    for row in rows:
        client_id = str(row["client_id"])
        reported_at = str(row["reported_at"])
        for addr in effective_addrs(row["source_addr"], row["local_addrs"], hop):
            current = best.get(addr)
            if (
                current is None
                or reported_at > current[0]
                or (reported_at == current[0] and client_id < current[1])
            ):
                best[addr] = (reported_at, client_id)
    return {addr: owner for addr, (_at, owner) in best.items()}
