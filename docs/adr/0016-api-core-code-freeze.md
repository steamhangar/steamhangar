# ADR-0016: api/ and core/ code freeze before the first release candidate

Date: 2026-10-01
Status: Accepted (user decision 2026-09-29)

## Context

The pre-release security review (WP 5.3 review half, `docs/PROJECT_PLAN.md`
§7 Phase 5 and §11 item 6) waits on an api/core code freeze, so the
reviewed code is the code that ships. On 2026-09-29 the user decided to
freeze after the pre-freeze project review and its fix packages (§11
item 12). Before the freeze the user pulled in two more items: download
throttling (WP TH-1a/TH-1b/TH-1-FIX, ADR-0015) and the web
connection-lost indicator (WP WEB-FIX-2). All of them have passed review.

## Decision

- **Frozen:** `api/`, `core/` and `dns/`. The user decision named api/ and
  core/; dns/ is the orchestrator's extension (it ships in the same image
  set and was reviewed together with core), not part of that decision.
- **Allowed in frozen code:** fixes from the WP 5.3 security review, fixes
  from the first real-world rollout, fixes for red CI, and docs.
- **Not frozen:** `web/`, `app/`, `agent/` and `deploy/`. They stay open
  for fixes; no new features before the release candidate without a user
  decision.
- **Freeze commit:** `92e95be` on branch `wp/pre-freeze-review`. It
  becomes `main` once the user merges the PR.
- **Exit:** the tag `v0.1.0-rc1`, pushed after the security review's fixes
  have landed.
- **Model rule:** the WP 5.3 second review pass uses the strongest
  available model (the "[Fable]" marker names a boundary, not a model,
  since WP R-0). On 2026-10-01 that is Opus 5.5, because Fable was
  unavailable and the user decided not to wait.

## Consequences

- The security review can start; it reviews `92e95be` (or `main` after the
  merge, if nothing in the frozen paths changed in between).
- Any change to a frozen path that is not one of the allowed classes needs
  a user decision and a note here.
- Features for web/app/agent/deploy wait until after `v0.1.0-rc1` unless
  the user decides otherwise.

## Addendum 2026-10-01 — freeze exception: `steam_library_steamid` (WP API-FEAT-1)

User decision, 2026-10-01: the api/ freeze opens for exactly one new
setting, `steam_library_steamid`, and nothing else. Why: there should be one
SteamID per vault, not one per device. The web library (WP WEB-FEAT-1) reads
it now, and the app reads it later, both from `GET /v1/settings`, instead of
each device storing its own.

Scope of the exception, all in WP API-FEAT-1:

- one `OVERRIDABLE_SPECS` entry (`api/vault_api/settings_store.py`) and one
  `Settings` field with its env source `VAULT_STEAM_LIBRARY_STEAMID`
  (`api/vault_api/config.py`);
- no new grammar: the value must pass `steam_relay.valid_steamid64`, the
  relay's existing check (17 ASCII digits, individual-account range);
- the forwarding line in `deploy/compose.yaml` and the stanza in
  `deploy/.env.example` (deploy/ is not frozen; listed for completeness).

`PATCH` accepts the value only as a JSON string: a JSON number gets `422`,
because a JavaScript sender has already rounded a 17-digit id to a
different account.

No route, schema or relay behaviour changed. Every other frozen-path change
still needs its own user decision and note here.

## Addendum 2026-10-02 — freeze exception: CGNAT port exhaustion during prefill (WP CORE-FIX-2, stage 1)

(Named CORE-FIX-2 because CORE-FIX-1 is the pre-freeze core + dns review
package, commit 30151f8.)

User decision, 2026-10-02: "RC 6 erst wenn der Bug hier mit dem Download
gefixt ist" (no rc6 until the download bug is fixed). The api/ and core/
freeze opens for this one bug fix.

The bug, from the production rollout on a DS-Lite line: vault-core logged
57845 upstream 502s (`connect() failed (113: Host is unreachable) while
connecting to upstream`, to Valve CDN addresses) against 4029 200s in about
two minutes. A single request always worked; from another container on the
same host 50 parallel new connections worked and 200 failed about half the
time; the router logged an "ICMP Flood" from the DS-Lite AFTR. The
carrier-grade NAT had run out of port mappings and answered new connections
with ICMP host-unreachable (RFC 6888 REQ-11). Every miss is a new upstream
connection (variable `proxy_pass`, no keepalive pool), SteamPrefill 3.7.1
keeps 30 requests in flight and re-requests failures at once, and
vault-core retried each connect error (`proxy_next_upstream_tries 3`: up to
three attempts, i.e. two retries).

Scope of the exception (stage 1), all in WP CORE-FIX-2:

- api/: SteamPrefill gets its hidden `--max-threads N` flag on every
  prefill, in subprocess and queue mode. N comes from the new env-only
  setting `VAULT_PREFILL_MAX_THREADS` (`api/vault_api/config.py`, default 8,
  strict whole number 1..64, invalid values refuse to boot). Not a
  `PATCH /v1/settings` key, so no route or schema changes. `api/Dockerfile`
  probes the pinned binary for the flag at build time.
- core/: `@miss` no longer retries on `error` and allows one retry
  (`proxy_next_upstream timeout http_502 http_503 http_504`,
  `proxy_next_upstream_tries 2`), pinned by
  `core/docker/check-config-drift.sh` step 2c.
- deploy/ (not frozen, listed for completeness): forwarding on vault-api and
  vault-runner, `.env.example`, README troubleshooting row, verify-stack
  checks.

Not in this exception: an upstream keepalive pool for vault-core (the root
cause, since it removes the new connection per chunk). That is a separate
decision for the user. Every other frozen-path change still needs its own
user decision and note here.
