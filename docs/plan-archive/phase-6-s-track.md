# Plan archive: Phase 6 external integrations, runner split and egress enforcement (S-track)

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

### Phase 6 — External Integrations (post-release, user decision 2026-08-10)

Deliberately NOT a release blocker: WP 3.13 already ships working generic
webhooks, and everything below is additive on top of them. Scheduled after
the community release so no integration work delays it.

Motivation: the WP 3.13 envelope is consumed happily by n8n and any generic
JSON receiver, but the Phase 3 checkbox claiming "Discord/Slack/ntfy-
compatible" overstates it — a Discord webhook accepts only `{"content": …}`
or `embeds` and rejects our envelope with 400. Today Discord needs a relay
in between. Phase 6 closes that gap and adds the events an automation
platform actually wants to react to.

- [ ] Multi-target delivery: `VAULT_WEBHOOK_TARGETS` — N receivers, each
      with its own URL, event filter, format and headers, so n8n and Discord
      can be fed at the same time (today: exactly one URL)
- [ ] Vendor format adapters `generic|discord|slack` (user decision
      2026-08-10: adapters in the backend, NOT a free-form body template —
      Discord must work without a relay; the cost is that we maintain two
      foreign formats). Includes honouring `Retry-After` on 429, which
      Discord does send and the current 3-attempt/0.2-0.5 s backoff ignores
- [ ] `app.updated` — the update notification. **No oracle dependency**
      (user decision 2026-08-10, superseding the earlier "either source"
      answer): the trigger is the existing non-forced scheduler sweep, which
      per `docs/research/phase3-manifests.md` costs ~3 s and zero bytes for
      an up-to-date app and is therefore already the manifest check. The
      changed manifest ids come from WP 3.2 ingestion, the byte/`Updated`
      counters from the WP 3.3 summary parser — so this is a notification
      hook on machinery that already runs, not new detection.
      The honest semantics are "the vault HAS this update" rather than
      "an update is available"; for a homelab that is the more actionable
      message. `VAULT_MANIFEST_ORACLE` (ADR-0006 tier 3) stays exactly what
      it is — an opt-in pre-emptive badge — and never becomes a
      precondition for notifications
- [ ] Outbound auth beyond Basic-in-URL: custom headers (n8n commonly wants
      a header token) + optional HMAC-SHA256 signature, plus a per-event
      `event_id` so a receiver can deduplicate across retries
- [ ] `POST /v1/webhooks/test` — fire a synthetic event at a target. Needed
      by any settings UI (Phase 4) and turns webhook setup from guesswork
      into one click
- [ ] Integration docs: n8n in BOTH directions (receiving events; calling
      `/v1/jobs` to trigger a prefill), Discord, ntfy
- [ ] Structured log export — opt-in `VAULT_EVENT_LOG_STDOUT` mirroring the
      cache-event TSV (WP 3.10) to vault-core's stdout, so Loki/Promtail/
      Vector-style collectors can ingest the machine-readable events without
      mounting the volume (user request 2026-08-17: keep it open for the
      community even though the requester uses Dozzle). Deliberately NOT a
      Dozzle gap: Dozzle reads container stdout, where vault-core's
      human-readable per-request log with HIT/MISS/BYPASS already is — a
      file-tailing log viewer is what the TSV would need, and no popular
      Docker log viewer does that. Cost side: doubles the line volume inside
      the json-file ring buffer, and the config-drift check's pinned
      `access_log` counts must be updated in the same change. Off by default
- [ ] Named, scoped API keys — the direct consequence of inviting external
      systems in (user question 2026-08-10). Today there is exactly ONE
      key: `auth.require_api_key` compares against `VAULT_API_KEY` for every
      router, so the key an n8n flow needs in order to enqueue a prefill is
      the same key that may `DELETE /v1/cache/{appid}`. Wanted: several
      named keys (web UI, agent, Android app, n8n) with a coarse scope —
      read / enqueue / destructive — each revocable on its own, so a leaked
      automation token does not mean rotating every agent in the house.
      Note this is about BLAST RADIUS, not about identity: it does not make
      the vault multi-user
- [ ] Payload scoping per target: a Discord channel is a room full of
      people, and `client.bypass_*` payloads carry `client_id` plus every
      IP address a device reported from, while job/update events reveal
      which games the household owns. A `full|minimal` payload mode per
      target (minimal drops device and address fields) is the cheap,
      correct fix — authentication cannot solve a receiver-side visibility
      problem
- Open, deliberately deferred: PER-USER webhooks ("tell ME when MY games
  update") require a real user identity, which only arrives with Phase 4a's
  Sign in with Steam (ADR-0004). Until then a vault has one operator's view
  and the webhook config belongs to that operator. Revisit after Phase 4a
- Explicitly rejected: persisting the delivery queue across restarts.
      At-most-once is the right hardness for homelab notifications
      (`webhooks.py` module docstring)

### Runner split & egress enforcement (S-track, decided 2026-08-19)

Operator decision after WP EG-1's first attempt STOPPED pre-implementation
(the coder proved the egress lock's promise unkeepable while SteamPrefill
runs as a subprocess of vault-api — network namespaces are per container;
ADR-0011 is reserved for the egress lock's ADR when EG-1 lands, which is
why 0012 precedes it in time): split first, then lock.

- [x] **S-1 (api)** — prefill runner split: VAULT_PREFILL_MODE
      subprocess|queue, schema v15 lease columns, atomic claim (two
      independent sufficient mechanisms, measured as 8 OS processes),
      no-lease-stealing crash semantics, bounded stale-lease tests after a
      mutation class was shown to wedge CI silently. ADR-0012. Opus review
      rounds 1-2 (FAIL: queue-mode pause→resume was an inescapable dead
      end, found by live probe; fixed + integration-pinned → PASS).
      1672→1729 api tests. 2026-08-19.
- [x] **S-2 (deploy)** — DONE 2026-08-19: vault-runner service (same
      image, queue mode on both sides, health-gated against a
      fresh-volume race found empirically), credential volume moved off
      vault-api, HOME volume shared (CI-pinned — the silent-forever
      ingestion case), unused cache mount dropped from the broad-egress
      container on review. verify-stack 112→149 checks, api tests
      1729→1747. Opus rounds 1-3 (version-pin guard silently disabled by
      the duplicate image line — re-keyed by service; a tuned-setup
      recipe asserting a mechanism the code lacks — rewritten to move the
      volume, not the variable).
- [x] **EG-1 (deploy, resumed)** — DONE 2026-08-19: the egress lock.
      vault-api on vault-lan (no-masquerade) + vault-egress (internal),
      allowlist tinyproxy as shipped default, VAULT_EGRESS_ALLOW, the
      five-minute tcpdump/router verify recipe, completeness pins. ADR-0011.
      Two channels the lock does NOT close are named as accepted gaps in the
      ADR and threat-model, both measured live: DNS resolution (resolver
      forwards from the host namespace) and the Docker host's own address
      (replies need no masquerade). verify-stack 149->185, api 1747->1809.
      Opus rounds 1-2 (FAIL: two security-doc absolutes overstated the lock,
      caught by the reviewer actually exfiltrating; a CRLF gitattributes gap
      that would crash-loop the proxy on a Windows checkout; the proxy image
      was never built by verify-stack -> all fixed). HARDENING CHAIN
      COMPLETE.
