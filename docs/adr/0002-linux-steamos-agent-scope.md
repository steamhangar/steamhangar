# ADR-0002: vault-agent supports Linux/SteamOS devices (Steam Deck, Steam Machine)

Date: 2026-08-05
Status: Accepted (user decision during Phase 0)

## Context

The project plan originally scoped vault-agent as a Windows-only listener
on the gaming PC. SteamOS-based devices (Steam Deck, Steam Machine) are a
natural part of a Steam-only cache's audience — and they are exactly the
clients that cannot use the Windows hosts-file mode (the Linux Steam client
does not perform the `lancache.steamcontent.com` lookup) and therefore rely
on the DNS-rewrite path (vault-dns). Without an agent on those devices,
their installed libraries would be invisible to the prefill scheduler.

## Decision

1. Phase 2 gains a Linux/SteamOS vault-agent variant. The ACF/VDF library
   format is identical to Windows; the variant differs only in library
   discovery paths (XDG, `~/.local/share/Steam`), packaging (systemd user
   service instead of a Windows scheduled task), and a SteamOS-friendly
   install that touches only the home directory (read-only rootfs).
2. Removed titles are part of the contract: the agent keeps reporting the
   FULL installed-app-ID list; vault-api derives additions AND removals by
   diffing consecutive reports per client. The agent stays stateless and
   dumb by design — no delta logic, no control logic on the device.

## Consequences

- `POST /v1/agent/installed` needs no schema change (full-list semantics
  already planned); vault-api gains a per-client diff step and surfaces
  removals (status update / optional cleanup hint).
- The hosts-file mode remains Windows-only and opt-in; SteamOS devices are
  served by vault-dns (validated in Phase 0, WP 0.6 Scenario B).
- No change to the v1 scope cut (Steam only, no multi-service support).

## Addendum (2026-09-30): the Windows-only premise did not survive Phase 0

The context above cites the upstream claim that the Linux Steam client
does not perform the `lancache.steamcontent.com` lookup. WP 0.6 (same day
as this ADR, `poc/linux-client-test/RESULTS-20260805-083353.md`) showed
the current client DOES perform lancache discovery, so the hosts-file mode
is usable on Linux/SteamOS as well, and Phase 2 shipped it that way
(`agent/README.md`, "The Linux-client finding": only the default hosts
path and the elevation wording differ per platform). The decision itself
stands unchanged — a Linux/SteamOS agent variant, full-list reporting —
only its motivation narrows: SteamOS devices need the agent so their
libraries are visible to the scheduler, not because hosts mode is closed
to them. Multi-device LANs still belong on the DNS path; hosts mode is the
single-PC answer on either platform.
