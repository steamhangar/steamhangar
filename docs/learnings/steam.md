# Learnings: Steam ecosystem

Scope: Steam CDN, depots and manifests, SteamPrefill behaviour. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## Steam ecosystem facts
- SteamPrefill v3.7.1 has no app-id CLI — selection via
  Config/selectedAppsToPrefill.json; exits 0 with "Prefilled 0 apps" for
  unowned apps (job-outcome trap, Phase 3 item); requires the
  /lancache-heartbeat + X-LanCache-Processed-By contract; sends
  ?nocache=1 + Range bytes=0-0 speed probes (WP 0.4/1.4/1.7).
- Real Steam clients (Windows AND current Linux) send ZERO Range headers
  and use lancache discovery; manifest URLs carry per-request codes (no URL
  dedupe); Steam LAN P2P transfers can legitimately replace cache traffic
  (WP 0.3/0.6).
- SteamPrefill's cache discovery probes **four** candidates in order —
  `lancache.steamcontent.com`, `localhost`, the Docker gateway
  `172.17.0.1`, then the local hostname — and accepts one only if it is a
  private/loopback **IPv4** AND answers `/lancache-heartbeat` with
  `X-LanCache-Processed-By` (`poc/steamprefill/PROTOCOL.md`, source-read).
  Two consequences that invert each other: with vault-core on the default
  `0.0.0.0` bind, the **gateway** candidate carries detection inside a
  container with no DNS involved — so a DNS lookup is the wrong
  diagnostic and reports a bypass that isn't happening; but with core
  bound to a dedicated address (the port-80-conflict/NAS recipe), gateway
  and loopback both refuse and detection hangs entirely on candidate 1,
  i.e. on container DNS. Probe the heartbeat, never the name. Pinning that
  ONE hostname is sufficient: after detection SteamPrefill talks to the
  resolved IP for all depot traffic (WP 0.4: 1272 chunks through a single
  hosts entry), so depot hostnames need no rewrite — and the pin value
  must be a private IPv4 or it is rejected before any probe
  (P1 review, measured in both layouts).

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- A hidden CLI flag needs a positive probe: SteamPrefill's `--help`
  exits 0 even with unknown options, so only the flag's own log line
  proves the parser took it. Make the image build fail without that
  line (WP CORE-FIX-2).
- See also [api.md](api.md), this section: "Parsing a third-party tool's output for a hint: match the ..."

## 2026-10-02/03 — upstream keepalive pool (ADR-0017, WP CORE-FEAT-1a..1d)
- SteamPrefill exits 0 when an app fails; its summary table only grows a
  "Failed" column (and an "Unowned" one) when the count is non-zero
  (v3.7.1 `PrefillSummaryResult.cs`). Read optional columns by header,
  fail closed when a present Failed column cannot be read, and never
  match a word boundary before a header word that may carry an SGR
  remnant (WP API-FIX-3).
