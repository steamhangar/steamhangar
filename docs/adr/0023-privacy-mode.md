# ADR-0023: One privacy mode instead of many switches

Status: Proposed (draft, D22; not implemented)

Date: 2026-10-15

Related: ADR-0009 (persisted settings, "env always wins" addendum), ADR-0010
(relay privacy gate env-only), ADR-0011 (egress lock),
`docs/security/privacy-review.md` (inventory and findings F1 to F8),
`docs/LEARNINGS.md:557-565` (failure-mode analysis per store).

## Context

The privacy review found that the controls over what leaves the household are
scattered and uneven: `VAULT_MANIFEST_ORACLE` (env, default on,
`api/vault_api/config.py:155`), `VAULT_RELAY_EXPOSE_PLAYTIME` and
`VAULT_RELAY_EXPOSE_LAST_PLAYED` (env only, `config.py:1148,1153`), the relay key
and `VAULT_STEAM_LIBRARY_STEAMID` (db), `VAULT_WEBHOOK_URL` (env only),
`VAULT_EVENT_LOG` (env), `VAULT_EGRESS_ALLOW` (env), and two flows with no
switch at all (cover lookup and cover images, review O3 to O5). Planned work
adds more (D19-1a on-open oracle query, D19-1c `VAULT_STORE_DETAILS`). A user
cannot answer "what leaves my house" from one place, and every new feature
risks inventing another switch with its own failure mode.

## Decision (proposed)

Introduce one setting, `VAULT_PRIVACY_MODE`, with three values. A mode is a
named bundle of the individual switches; individual switches stay as expert
overrides.

| Switch | `strict` | `balanced` (default) | `open` |
|--------|----------|----------------------|--------|
| Manifest oracle, timer-driven update check (O1) | off | on | on |
| Oracle on card open (O2, D19-1a) | off | on, TTL + per-appid dedupe | on |
| Store details (O9, D19-1c, `VAULT_STORE_DETAILS`) | off | off | on |
| Cover lookup and server-side cover fetch (O3, O4) | off | on | on |
| Client-side cover fallback to Valve CDN (O5) | off (placeholders, `img-src 'self'`) | on | on |
| Steam relay (O6) | refused even with a key | allowed once a key is entered | allowed |
| Playtime and last played (E1) | off | off | off unless the existing env flags are set |
| Webhook payloads (O7) | not sent | sent, client id and addresses omitted | sent in full |
| Event log and Clients traffic stats (E4) | off | follows `VAULT_EVENT_LOG` | follows `VAULT_EVENT_LOG` |
| Agent install reports and Clients page (E3) | off | on, raw peer addresses omitted | on |

`strict` is only true if the egress lock is also tightened: the proxy entry
point derives its baked hosts (`deploy/proxy/docker-entrypoint.sh:114`) from the
mode, so a bug in vault-api cannot undo it. The Valve hosts therefore stop being
unconditional.

`open` never enables the playtime flags by itself: ADR-0010 stands, those two
stay explicit env flags.

## Interaction with expert settings and overrides

Resolution order, most specific first:

1. An explicit env override of an individual switch (as today).
2. `VAULT_PRIVACY_MODE` (env), which sets every switch it covers.
3. A db-stored mode (ADR-0009) set from the UI.
4. The code default `balanced`.

Rules:

- **The env floor can only be tightened by the UI.** If `VAULT_PRIVACY_MODE` is
  set in env, the UI shows it read-only. The UI may set a stricter mode than the
  code default but never relax an env value (same shape as ADR-0010).
- **An explicit expert switch relaxing a mode is allowed but visible.** Env
  `VAULT_MANIFEST_ORACLE=on` under `strict` wins, and `GET /v1/settings` lists it
  under `overrides` with the mode value it contradicts; the UI shows a banner.
  Reason: operators with their own mirror or a self-hosted oracle must not be
  blocked.
- **An explicit switch tightening a mode is always honoured.**
- Switches that carry a third party's data or protect another household member
  (playtime flags, webhook URL, relay expose) keep the ADR-0010 env-only floor;
  the mode does not move them toward collection.
- Unknown mode value: refuse to start, not fall back to a default, because a
  typo in a privacy setting must not silently pick a collecting mode.

## Failure-mode analysis (per store)

| Store | Lost or unreadable | Result |
|-------|--------------------|--------|
| env | compose file reverted | falls to db value, then `balanced` |
| db | volume wiped | env value if set, else `balanced`; relaxed overrides that lived only in the db are gone, which is toward protection |
| image default | new image changes the default | the default is a code constant and is changed only by an ADR amendment plus a migration note |

Direction: a lost env with a `strict` operator choice stored only in env moves
to `balanced`, i.e. toward collection. Mitigation: recommend `strict` is set in
env (compose), and write the chosen mode to the db as well so a later env loss
still resolves to the db value, not the code default. The mode is stored in both;
the UI may only raise it.

## Migration of existing settings

- No stored mode and no env mode: resolve to `balanced`. This reproduces current
  behaviour for every flow that exists today (oracle on, covers on, relay off
  until a key is set, playtime off), so an upgrade changes nothing.
- Existing explicit switches are kept and become overrides (shown in the
  overrides list). Nothing is rewritten.
- `VAULT_MANIFEST_ORACLE` unset keeps meaning "on" only while the mode is
  `balanced` or `open`.
- Cover lookup and cover fetch gain a switch (`VAULT_COVER_LOOKUP`, default on)
  so `strict` has something to set; an unset value follows the mode.
- Downgrade: older images ignore `VAULT_PRIVACY_MODE`, so a downgrade from a
  `strict` setup silently collects again. State this in the release notes of the
  first version that ships the mode.
- Fix the doc drift found in the review: `deploy/compose.yaml:615` calls
  `VAULT_WEBHOOK_URL` "DB-overridable" while ADR-0009/0010 and
  `settings_store.py:449,718` make it env only.

## Consequences

- One answer to "what leaves my house": the mode plus a list of overrides.
- New features must declare which mode enables them; the PR checklist asks for
  an entry in the mode table and a row in the failure-mode table.
- `strict` needs real work beyond a flag: a cover switch, proxy allow list
  derived from the mode, placeholders in web and Android, and an off switch for
  `client=` in access logs (review F6). Until those ship, the mode must not be
  advertised as `strict`.
- Risk: three modes hide detail. The expert overrides and the review document
  keep the detail reachable.

## Alternatives considered

- **Keep individual switches, add a new one per feature.** What exists; fails
  review F8.
- **Four modes (off/light/secure/strict, roadmap sketch).** More granularity than
  the flows justify; `light` and `secure` differ only by the store-details flow,
  which a single override covers.
- **A mode that can be changed from the UI only.** Rejected: a lost db volume
  would drop it (ADR-0010 reasoning).

## Open questions for the decision

1. Is it acceptable that `strict` removes covers (placeholders only)?
2. Should `balanced` keep the oracle on by default, or move it to opt-in?
3. Does the Clients page remain in `strict`, or does it need D5 (scoped keys)
   first?
