# ADR-0005: vault-agent ships as a Go binary

Date: 2026-08-05
Status: Accepted (user decision)

## Context

The plan left the agent language open ("Small Python or Go binary"). The
agent runs on end-user gaming machines — Windows PCs, Linux desktops, and
SteamOS devices (Steam Deck / Steam Machine, ARM64) per ADR-0002. Python
means either a runtime install (adoption hurdle) or PyInstaller bundles
(large, and notorious for antivirus false positives on exactly the target
audience's machines). Go produces a single static binary per platform via
trivial cross-compilation, and Go competence enters the repo in Phase 4
anyway (tsnet gomobile module).

## Decision

1. vault-agent ships in Go: one static binary per target
   (windows/amd64, linux/amd64, linux/arm64), no runtime dependencies.
2. The Phase-2 WP 2.1 Python parser is kept as the executable
   specification: its synthetic fixture corpus and test semantics define
   the KeyValues/ACF/VDF behavior; WP 2.1b ports parser + tests to Go
   against the same fixtures, then Python agent code is removed. All
   subsequent agent packages (reporter, hosts mode, Linux variant) are
   built in Go directly.
3. Build tooling: Go toolchain in WSL2, cross-compiled artifacts; CI
   builds land with Phase 5.

## Addendum (2026-08-06, orchestrator)

Removal timing refined: the Python parser stays in the repo as the frozen
executable spec baseline until the Phase-2 close-out package, where it is
removed in one dedicated step. `agent/tests/fixtures/` is permanent — the
Go test suite consumes the same fixture corpus and it must survive the
Python removal. Known deliberate Go deviations from the Python spec
(stricter: invalid UTF-8 rejected, integer overflow rejected, ASCII-only
digit keys) are documented in agent/README.md's divergence list.

## Addendum (2026-09-30)

The tsnet gomobile module named in Context did not enter the repo in
Phase 4 — it is deliberately post-v1 (`docs/PROJECT_PLAN.md` §7 Phase 4b,
the unticked tsnet bullet; the System-VPN profile covers Tailscale via the
regular Tailscale app). The decision stands on its other grounds: the
agent is the project's only Go code today.
