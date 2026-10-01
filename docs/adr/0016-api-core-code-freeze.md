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
