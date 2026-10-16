# ADR-0026: The cover refresher stays anonymous

Status: Accepted

Date: 2026-10-16

Related: ADR-0010 (relay privacy gate), ADR-0011 (egress lock),
ADR-0023 (privacy mode, draft), `docs/security/privacy-review.md` (O3 to O5,
F2), `api/vault_api/cover_art.py`.

## Context

The cover refresher looks up names and cover art for the app ids the vault
tracks through the public `IStoreBrowseService/GetItems` call. The privacy
review (F2) asked whether this should require the relay key, be switchable,
or wait for the privacy modes (D22). Options were: A keep and document, B
gate names and covers on the relay key, C defer to D22.

## Decision

Option A (user decision 2026-10-16): keep the refresher anonymous and
document it.

- Names and covers are fetched through `IStoreBrowseService` **without a
  key**. No key, no SteamID, no client id is sent.
- What the operator must know: Valve sees the vault's egress address and the
  list of library app ids (one batch per request). This is stated in the
  privacy review and the threat model.
- The relay key is used only for account data (owned games, playtime), as
  before; ADR-0010 and ADR-0011 are unchanged.
- The flow stays on by default and is only stoppable through the egress
  lock until D22 folds it into a mode.

## Consequences

- Without a relay key the UI still shows names and covers; it never shows a
  bare id where a name could exist.
- Revisit with D22: the modes must decide whether `strict` turns O3/O4 off
  and drops the legacy cover URL fallback (review F2, F3).
