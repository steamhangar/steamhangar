# ADR-0024: Depot preview counts Windows depots only

Status: Accepted

Date: 2026-10-15

Related: D19-1a (expected depots and size for never-cached games),
`api/vault_api/depot_preview.py` (`_other_os_only`)

## Context

The depot preview asks the manifest oracle which depots a game has on the
public branch and sums their sizes, so a never-cached game card can show an
expected download size. Steam app info lists depots for every operating
system; a depot with `config.oslist` naming only macOS and/or Linux would
inflate the sum if the prefill tool does not fetch it.

## Decision

A depot whose `oslist` is non-empty and does not contain `windows` is left out
of the preview. An absent or empty `oslist` means every OS and is kept.

This is an assumption: the prefill tool is believed to fetch the Windows
depots. It is not verified against the tool's behaviour for every game.

## Consequences

- The shown size is an expectation, not a measurement. If the assumption is
  wrong for a game, the size is too low (a needed non-Windows depot is
  missing) or, where the tool fetches more than Windows, too high.
- Observed depots from real downloads always win over the preview and are
  never overwritten by it.
- If the tool's OS selection becomes configurable, the filter must follow it;
  that would supersede this ADR.
