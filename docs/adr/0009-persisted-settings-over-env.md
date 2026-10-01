# ADR-0009: Persisted settings override env defaults

Date: 2026-08-10
Status: Accepted (user decision "Plan B" 2026-08-10, PLAN Phase 4d; design
details below drafted by the orchestrator and validated in the settings
work-package review)

## Context

Every runtime setting is env-only today (`VAULT_NAME`, `VAULT_SCHEDULE_*`,
`VAULT_WEBHOOK_*`, ...) and there is no settings write endpoint. A settings
screen (Phase 4a) that can toggle anything — and the Phase 4d sweep-mode
switch — need a persistence layer. User decision: a settings table whose
values override the env defaults, plus `GET`/`PATCH /v1/settings` (Plan B,
chosen over env-only).

## Decision

1. **Precedence: DB override > env value > built-in default.** The env
   value acts as the *default* that a UI-set override shadows. Rationale:
   this is what the user decided ("Settings-Tabelle überschreibt die
   Env-Defaults"); a UI toggle that can lose against a forgotten Compose
   line is not a toggle.
2. **Forcing a value back is explicit and first-class:** `PATCH` with
   `null` for a key deletes the override row — the env value (or built-in
   default) applies again immediately. `GET /v1/settings` therefore
   reports, per key: `effective` value, `source` (`db` | `env` |
   `default`), and the env default it would fall back to, so the operator
   can see *why* a value is what it is before clearing it.
3. **Operator hard-lock:** `VAULT_SETTINGS_READONLY=1` (env-only by
   definition) disables `PATCH` entirely (`403`, distinct detail). For a
   headless/GitOps deployment this restores pure env semantics without
   touching the schema. Emergency escape hatch (documented, not an API):
   delete rows from the settings table with sqlite3 while the service is
   stopped.
4. **PATCH-time validation with the startup grammars.** The exact
   validation functions `config.py` applies at startup are applied at
   `PATCH` time — same strict int/float grammars (nan/inf rejected, WP
   3.12), same `schedule_window` parser (overnight + 24:00 forms, WP 3.5).
   A bad value fails the PATCH with `422`; it must be impossible to
   persist a value that would later fail in the scheduler thread.
   Implementation consequence: the grammar functions live in one
   importable place and are used by BOTH config startup and the PATCH
   path — no duplicated parsing. Exception: `VAULT_WEBHOOK_URL` has no
   startup grammar — WP 3.13 treats it as trusted operator config and
   never refuses to boot over it. The PATCH path adds a scheme-only check
   (`config.validate_webhook_url`) used ONLY by the API;
   `Settings.from_env()` stays unchanged.
5. **Env-only allowlist (not persistable, PATCH rejects with `422`):**
   bootstrap and security settings — the shipped fields `vault_api_key`,
   `db_path`, `cache_root`, `steamprefill_path`, `steamprefill_cache_dir`,
   `manifest_archive_dir`, `web_dir`, `settings_readonly` (the process
   bind address/port are uvicorn CLI arguments, not Settings fields).
   Rationale: these configure how the process finds its world or who may
   talk to it; changing them from inside the API is either meaningless
   before a restart or a privilege escalation. Everything else (vault name,
   schedule window/cadence and numeric tunables, webhook URL + event
   filter, auto-GC flag, future sweep-mode flag) becomes overridable.
   **Update (WP 4h.0):** `relay_expose_playtime` and
   `relay_expose_last_played` (the Steam relay's privacy gate) joined this
   allowlist too, for a DIFFERENT rationale than the bootstrap/security one
   above — see ADR-0010, which is the actual amendment to this decision.
6. **Read model:** settings are read through one accessor that resolves
   precedence at call time. Components that cache a value (scheduler
   cadence) re-read on their existing wake-up cadence — documented per
   key in the API response (`applies` = `immediately` | `next_sweep` |
   `restart-required`). `applies` describes when a RUNNING component
   re-reads the value; a component whose thread only starts at boot when
   its feature was enabled is `restart-required` regardless (shipped:
   webhook delivery; the scheduler thread starts unconditionally so the
   schedule keys are genuinely `next_sweep`).
7. **Secrets in responses:** values that can carry credentials (webhook
   URL userinfo) are redacted in `GET` exactly like the WP 3.13 logging
   redaction; `PATCH` accepts the full value, `GET` never returns it.

## Consequences

- Schema bump: one `settings` table (key TEXT PRIMARY KEY, value TEXT,
  updated_at) — values stored in their env string grammar, so one
  validation path covers both sources.
- `GET /v1/schedule` stays read-only and unchanged in shape, but now
  reports the EFFECTIVE (override-resolved) configuration; the settings
  endpoint is the write path.
- The Phase 4a settings screen builds on `GET`/`PATCH /v1/settings`
  (WP 4a.6) instead of displaying "set this env var" hints.
- Phase 4d's sweep-mode switch is one more overridable key when it lands.

## Addendum 2026-09-30 — what decision 3's lock does not cover (WP API-FIX-2)

Decision 3 says `VAULT_SETTINGS_READONLY=1` "disables `PATCH` entirely" and
"restores pure env semantics". The pre-freeze project review found one
settings-shaped write path outside it: `PUT /v1/steam/key` and
`DELETE /v1/steam/key` (`api/vault_api/routers/steam.py`, ADR-0010's relay)
persist the Steam Web API key in the database and are not gated by the
flag.

Decision, taken as a documentation fix rather than a code change: the
routes stay outside the lock for the release. There is no environment
source for the relay key, so "pure env semantics" has nothing to fall back
to; gating the routes without one would make the relay unconfigurable in
every readonly deployment. The honest statement is therefore recorded in
three places — this addendum, `docs/security/threat-model.md` §7 and
`api/README.md` "`VAULT_SETTINGS_READONLY`" — instead of rewriting decision
3 above. Gating the key routes together with a `VAULT_STEAM_RELAY_KEY` env
source is post-release work; when it lands, decision 3's wording becomes
true without qualification.
