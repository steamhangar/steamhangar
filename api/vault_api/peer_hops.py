"""Which TCP peer addresses are forwarding hops, not clients (WP AGENT-ADDR-1).

vault-api records the TCP peer of every agent report
(``agent_reports.source_addr``, schema v9) and used to treat it as the
reporting PC's LAN address. Behind Docker's published ports that is false: the
userland proxy (``docker-proxy``) opens the connection to the container from
the host side of the bridge, so the peer is the bridge's **gateway** address
for every PC alike (measured on a real install 2026-10-14). The bypass rule
then accused every agent PC, and the D15b clients overview mapped one gateway
address to whichever agent reported last.

The rule this module defines
----------------------------
A peer address is a **forwarding hop** when it is

1. a loopback or unspecified address (``127.0.0.0/8``, ``::1``, ``0.0.0.0``,
   ``::``): a process on the vault host itself, typically a proxy; or
2. a next-hop **gateway** in vault-api's own routing table -- every route in
   ``/proc/net/route`` (IPv4) and ``/proc/net/ipv6_route`` (IPv6) whose
   ``RTF_GATEWAY`` flag is set. Inside a Docker bridge network that is the
   bridge's gateway address, which is exactly the address the published port
   connects from; with ``network_mode: host`` it is the LAN router, which is
   never a gaming PC either.

Nothing else is a hop. In particular no address range is hard-coded: Docker's
address pools are configurable, and a range rule (``172.16.0.0/12``) would
also swallow real LANs that happen to use it.

A hop is never used as a client's address: it is not correlated with cache
traffic, not assigned to an agent in the clients overview, and so can never
make the bypass rule accuse a client (``agent_reports.effective_addrs``).

Limits, stated rather than hidden: a reverse proxy in its own container on
the same Docker network connects from that container's address, which is
neither loopback nor a gateway, so it is not detected. Agents since
AGENT-ADDR-1 report their own LAN addresses and do not depend on this rule;
it only decides what an old agent's ``source_addr`` is worth.

The route files are read on every call. They are a few hundred bytes, the
callers run once per request or sweep, and a network re-attach changes the
gateway without a vault-api restart.
"""

from __future__ import annotations

import ipaddress
from typing import Sequence

#: IPv4 and IPv6 routing tables of this network namespace (Linux procfs).
#: Missing files (another OS, a sandbox) mean "no gateways known", which only
#: ever makes rule 2 match nothing. Tests point these at fixture files.
IPV4_ROUTE_FILE = "/proc/net/route"
IPV6_ROUTE_FILE = "/proc/net/ipv6_route"

#: ``RTF_GATEWAY`` from ``linux/route.h``: the route goes through a next hop.
RTF_GATEWAY = 0x2

#: Upper bound on route lines read per file. A container has a handful; this
#: only stops a pathological table from making every request slow.
MAX_ROUTE_LINES = 4096


def _canonical(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IP literal into its canonical form; ``None`` if it is not one.

    An IPv4-mapped IPv6 address (``::ffff:192.0.2.1``, what a dual-stack
    socket reports for an IPv4 peer) is unmapped to the IPv4 address, so both
    spellings compare equal. A zone id (``fe80::1%eth0``) is dropped for the
    comparison: gateways in the route table carry none.
    """
    value = raw.strip()
    if "%" in value:
        value = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            lines: list[str] = []
            for index, line in enumerate(handle):
                if index >= MAX_ROUTE_LINES:
                    break
                lines.append(line)
            return lines
    except OSError:
        return []


def _ipv4_gateways(lines: Sequence[str]) -> set[str]:
    """Gateways from ``/proc/net/route``: header line, then whitespace-split
    columns ``Iface Destination Gateway Flags ...`` with the addresses as
    8 hex digits in host (little-endian) byte order."""
    gateways: set[str] = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            flags = int(fields[3], 16)
            raw = bytes.fromhex(fields[2])
        except ValueError:
            continue
        if not flags & RTF_GATEWAY or len(raw) != 4:
            continue
        address = ipaddress.IPv4Address(raw[::-1])
        if not address.is_unspecified:
            gateways.add(str(address))
    return gateways


def _ipv6_gateways(lines: Sequence[str]) -> set[str]:
    """Gateways from ``/proc/net/ipv6_route``: no header, columns
    ``dest plen src plen next_hop metric refcnt use flags iface`` with the
    addresses as 32 hex digits in network byte order."""
    gateways: set[str] = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 9:
            continue
        try:
            flags = int(fields[8], 16)
            raw = bytes.fromhex(fields[4])
        except ValueError:
            continue
        if not flags & RTF_GATEWAY or len(raw) != 16:
            continue
        address = ipaddress.IPv6Address(raw)
        if address.is_unspecified:
            continue
        canonical = _canonical(str(address))
        if canonical is not None:
            gateways.add(str(canonical))
    return gateways


def gateway_addrs(
    ipv4_file: str | None = None, ipv6_file: str | None = None
) -> frozenset[str]:
    """Every next-hop gateway in this namespace's routing tables, canonical."""
    v4 = IPV4_ROUTE_FILE if ipv4_file is None else ipv4_file
    v6 = IPV6_ROUTE_FILE if ipv6_file is None else ipv6_file
    return frozenset(_ipv4_gateways(_read_lines(v4)) | _ipv6_gateways(_read_lines(v6)))


def is_forwarding_hop(addr: str, gateways: frozenset[str]) -> bool:
    """True when ``addr`` is a forwarding hop under the module's rule.

    A value that is not an IP literal (``"testclient"`` from Starlette's test
    client, a Unix-socket peer) is not a hop: the rule only speaks about
    addresses it can parse.
    """
    address = _canonical(addr)
    if address is None:
        return False
    if address.is_loopback or address.is_unspecified:
        return True
    return str(address) in gateways


def forwarding_hop_filter() -> "HopFilter":
    """A filter over the routing tables as they are right now."""
    return HopFilter(gateway_addrs())


class HopFilter:
    """``is_forwarding_hop`` bound to one snapshot of the gateway set, so one
    request or sweep judges every address against the same table."""

    def __init__(self, gateways: frozenset[str]) -> None:
        self.gateways = gateways

    def __call__(self, addr: str) -> bool:
        return is_forwarding_hop(addr, self.gateways)
