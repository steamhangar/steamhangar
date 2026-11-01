# Plan archive: Phases 0-3 (PoC, server MVP, agent, scheduler)

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

### Phase 0 — Feasibility PoC (CRITICAL, before anything else)
**Goal: verify the central assumption before writing product code.**
- [x] Test setup: nginx with `proxy_store` + hosts-file redirect
      *(run natively on Windows instead of a container — deliberate decision:
      develop natively first, containerize at end of Phase 1; see `poc/`)*
- [x] Route a real Steam download through it and verify:
  - [x] Does Steam consistently use the `/depot/<id>/chunk/<hash>` scheme?
        *(2026-08-04: 187 real-client requests, 100% chunk/manifest
        conformance, zero non-conforming URIs — see
        `poc/steam-client-test/RESULTS-*.md`)*
  - [x] Do range requests work cleanly with `proxy_store`?
    (known risk: `proxy_store` only stores complete responses —
    may need the `slice` module, or chunks may be small enough)
    *(2026-08-04: the real Windows client sent ZERO Range headers
    (~1 MiB chunks, full-body GETs); the CDN edge tested ignores Range
    on miss and always returns full 200; warm-cache ranges served
    natively. Production follow-up: strip client Range upstream +
    store-only-200 guard — see `poc/RANGE-FINDINGS.md`)*
  - [x] Cache hit on second download? Speed LAN-limited?
        *(2026-08-04: 93/93 HIT after uninstall/reinstall, zero upstream
        contact, disk-limited — 81.8 MiB served in ~1 s)*
  - [x] **Miss-handling decision:** synchronous store vs. transparent
    passthrough + async prefill (see pain-points section) — measure both
    *(measured in `poc/MISS-HANDLING-FINDINGS.md`: perf difference within
    noise. 2026-08-05 gate decision: HYBRID — store-on-miss (Phase 1)
    plus miss-triggered prefill completion (Phase 3). See ADR-0001)*
- [x] Run SteamPrefill against the PoC cache — does it fill correctly?
      *(2026-08-04: SteamPrefill v3.7.1 auto-detected the cache via the
      lancache-heartbeat contract and prefilled path-faithfully — layout
      cross-check PASS, repeat requests served as HITs. Notable: 0 manifest
      requests through the cache, and `?nocache=1` speed probes that
      production vault-core should honor as a cache bypass — see
      `poc/steamprefill/RESULTS-STEAMPREFILL-*.md`)*
- [x] Verify behavior of the Linux/Steam Deck client (known upstream quirk:
      does not perform the `lancache.steamcontent.com` lookup like Windows)
      *(2026-08-05, Ubuntu 26.04/WSL2, current stable client: the quirk is
      OUTDATED — the Linux client DOES perform lancache discovery and sent
      3574 requests through the cache with real CDN Host headers, zero
      Range headers. Cross-client sharing confirmed: chunks cached by the
      Windows client served to the Linux client as HITs. A transient
      upstream-IP outage also produced 502/stall evidence motivating
      production upstream design (honor client Host header, short
      connect timeouts, retry) — see
      `poc/linux-client-test/RESULTS-20260805-083353.md`)*
- **Abort criterion:** If `proxy_store` fails on range requests with no clean
  workaround → fall back to Plan A (unmodified LanCache + a manager layer in
  the spirit of lancache-manager; the rest of the project stays usable as-is)

### Phase 1 — vault-core + vault-api (server MVP)
- [x] Production-ready nginx config (log rotation, healthcheck) —
      implements the Phase-0 requirements from ADR-0001: lancache
      heartbeat, Range-strip + 200-only store guards (incl. retried-200
      handling), nocache=1 bypass, client-Host upstream with short
      timeouts/retry, store-on-miss
      *(caveat: log rotation is documented, implemented with the
      container in WP 1.9; tmp/ and cache/ must share one volume)*
- [x] FastAPI skeleton, SQLite schema, depot mapping import
      *(WP 1.2/1.3: skeleton with secure-by-default auth, schema v1,
      manual mapping endpoints per ADR-0003; the prefill-driven mapping
      import lands with the orchestration in the next package)*
- [x] Prefill orchestration (SteamPrefill subprocess, job queue, one job at a time)
      *(WP 1.4: selection-file driven — SteamPrefill has no app-id CLI;
      hybrid miss-trigger itself remains Phase 3 per ADR-0001)*
- [x] Size calculation + deletion incl. shared-depot protection
      *(WP 1.5/1.6: TTL-cached sizes; deletion with path guards,
      link-safe removal, execute-time shared recheck closing the
      TOCTOU, settle-and-recheck against racing deletes; mapping rows
      survive deletion by design. Opus + Fable double review)*
- [x] Docker Compose (2 services + volume), .env convention, pinned image tags
      *(WP 1.9: tag+digest-pinned images, preflight guards, json-file log
      rotation, SteamPrefill v3.7.1 with TOFU checksum and a dedicated
      HOME volume, 62-check container verification incl. real CDN
      MISS/HIT through the Linux container. Opus + Fable double review)*
- [x] Optional vault-dns container behind a Compose profile (`--profile dns`)
      *(WP 1.9: envsubst entrypoint with IPv4 validation and address=/
      local= re-assertion, fail-closed loopback default binding)*
- [x] **MVP test: prefill a game via curl, query its size, delete it — no app**
      *(2026-08-05, WP 1.7: full HTTP-only cycle against the real Steam
      CDN through the resident vault-core — prefill 79.7 MiB via job
      queue, size and summary internally consistent to the byte,
      deletion freed exactly the app's size, disk verified. Evidence:
      `core/tests/mvp/RESULTS-*.md`. Note: unattended real-account
      prefills are blocked by the local permission classifier — the
      run is operator-executed by design)*

### Phase 2 — vault-agent (PC listener) — COMPLETE 2026-08-09
- [x] ACF/VDF parser (appmanifest + libraryfolders, multiple drives)
      *(WP 2.1 Python reference with spec-parity pins, ported to Go in
      WP 2.1b per ADR-0005; reference implementation removed at close-out
      (WP 2.6), embedded EAppState bit table preserved in go/acf/acf.go)*
- [x] HTTP reporter with retry (tolerate VPN/network outages)
      *(WP 2.2: 46/46 validation parity, ctx-cancellable backoff, 429
      retryable, no secret in flag defaults)*
- [x] Optional hosts-file mode (opt-in, admin rights, clean uninstall path)
      *(WP 2.3: marker-block management with byte-exact preservation,
      fail-closed backup, measured Windows ACL write strategy, UTF-16/
      symlink refusal, SIGKILL/ENOSPC fault-injection evidence. Note:
      Phase 0 proved CURRENT Linux clients also perform lancache
      discovery, so hosts mode works beyond Windows; Opus + Fable
      double review. Real-Windows verification done 2026-08-06: hosts
      status on the live machine correctly reported the user's manual
      Phase-0 entry as a conflict without touching anything)*
- [x] Document Windows scheduled-task setup, optional installer script
      *(WP 2.6: per-user task via install-task.ps1/uninstall-task.ps1,
      API key in an owner-only env file, never on the command line
      (verified with a canary key against schtasks + task XML),
      icacls-before-write ACL, idempotent re-install, 36-assertion
      real-machine harness; Python reference implementation removed per
      ADR-0005 addendum, fixtures kept, EAppState bit table preserved
      in go/acf/acf.go)*
- [x] Linux/SteamOS agent variant (Steam Deck / Steam Machine): library
      discovery under `~/.local/share/Steam`, systemd user service
      packaging, SteamOS read-only-rootfs-friendly install (home dir only)
      — see ADR-0002
      *(WP 2.5: OnCalendar=*:0/30 + Persistent=true (monotonic timers
      proved a silent no-op for catch-up), umask-077-first secret env
      files)*
- [x] vault-api: scheduler uses the installed list as the prefill set;
      server-side diff of consecutive agent reports surfaces removed
      titles (status update / optional cleanup hint, see ADR-0002)
      *(WP 2.4 report chains ordered by rowid + WP 3.5 window sweeps)*

### Phase 3 — Scheduler & Update Logic — COMPLETE 2026-08-09

All packages merged: 3.1–3.6 (manifests, ingestion, summary, needs_force,
scheduler, last-remnant), 3.7/3.8/3.8b (GC plan/execute/grace window),
3.9 (oracle, branch session), 3.10 (event log), 3.11 (event sweep),
3.12 (job control), 3.13 (webhooks). Suite at close: 1239 green.

Closing plan (2026-08-09, user decision "implement everything"): remaining
packages are 3.8b grace window (merged) · 3.9 oracle incl. open-beta
branch manifests (ADR-0007 addendum B) · 3.10 vault-core cache-event log
(ADR-0008) · 3.11 event sweep = miss-trigger + client hit stats/bypass ·
3.12 job control + optional auto-GC · 3.13 webhooks. api/-packages run
strictly serially; 3.10 (core/) runs in parallel.

Branch-dispatch structure for ALL remaining phases (package briefs,
parallel/serial decisions, merge discipline, open user decisions):
`docs/WORKPACKAGES.md` (2026-08-09).

- [x] Miss-triggered prefill completion: a cache miss on an unknown/partial
      app queues a prefill job for that app (hybrid decision, ADR-0001;
      feed design decided in ADR-0008 → WP 3.10 core log + WP 3.11 sweep)
      *(WP 3.11: non-forced enqueues under cap → active-job → cooldown →
      cached-and-current guards; unmapped/shared/manifest misses counted,
      never triggered)*
- [x] Job outcome honesty: a prefill run that observed zero depots and has
      zero cached bytes must not end 'done' (WP 1.7 finding: SteamPrefill
      exits 0 for unowned apps → green badge for a never-cached game)
      *(WP 3.3: summary parsing with digits-and-separators row detection,
      cp850-safe decode chain, 0/0 ⇒ error)*
- [x] Job control: pause/resume/cancel (`DELETE /v1/jobs/{id}`,
      pause = terminate the SteamPrefill subprocess, resume = re-run —
      already-cached chunks replay as disk-speed HITs, so the cache
      itself is the progress store; user feature decision 2026-08-06)
      *(WP 3.12: distinct 'cancelled' terminal + 'paused' in
      ACTIVE_STATUSES (a paused prefill keeps protecting its shared
      depots), stop_request as a DB column cleared at every
      terminal/parking transition, pause RELEASES the worker slot with
      resume priority via the original job id (documented mockup
      divergence — UI follows backend), cooperative GC cancellation,
      VAULT_AUTO_GC hook, strict numeric env grammar (6 int + 2 float
      settings, nan/inf rejected); schema v8; suite 895 green; Opus
      PASS + Fable PASS)*
- [x] Manifest comparison (stale detection)
      *(ADR-0006: staleness via non-forced prefill (Tier 1), depot_manifests
      latest-per-(app,depot) from WP 3.2 ingestion, needs_force lifecycle
      with CAS-protected clear from WP 3.4)*
- [x] Configurable cron window (e.g. 09:00–17:00, every 3 h)
      *(WP 3.5: schedule_window parsing incl. overnight windows + 24:00 end,
      OnCalendar-style sweeps from agent reports)*
- [x] Manifest-based garbage collection (`/v1/cache/{appid}/gc`, optional
      auto-GC after successful update prefill)
      *(auto-GC half CLOSED — the earlier "still open" note below is
      superseded: WP 3.12 shipped `VAULT_AUTO_GC=off|dry-run|execute`
      (`worker.py::_maybe_queue_auto_gc`, re-read per job so a
      `PATCH /v1/settings` applies without restart, key exposed by
      ADR-0009 and wired through `deploy/compose.yaml` in WP D1). This
      is NOT a Phase-4d blocker any more)*
      *(WP 3.7 done: read-only GC core — `plan_gc()` with exact on-disk
      orphan bytes, shared-depot UNION keep sets, ADR-0007 readiness gate,
      uncached-app exclusion per ADR-0007 addendum, stored-manifest dedupe
      candidates; 86 tests, mutation-tested fail-closed directions,
      verified against the real PoC cache (0 orphans, research-doc parity).
      Endpoint + execution = WP 3.8, Opus + Fable mandatory)*
      *(WP 3.8 done: POST /v1/cache/{appid}/gc as queued job, dry-run by
      default at every layer (StrictBool opt-in, NULL job rows read as
      dry-run), execute-time re-plan by construction (run_gc takes no plan),
      WP 1.6 guard path reused (safe_child_path, remove_file_settling),
      keep-newest dedupe with byte-identity check, needs_force for touched
      depots' owners, partial-failure honesty; schema v7; 687 tests green;
      Opus PASS + Fable PASS. Optional auto-GC after update prefills still
      open)*
- [x] Beta-branch protection for GC (user decision 2026-08-09, ADR-0007
      addendum): (A) recently-stored grace window via st_ctime as a
      ChunkExclusion predicate; (B) open-beta branch manifests join the
      keep set when the oracle (WP 3.9) is enabled
      *(A: WP 3.8b merged. B: WP 3.9 done — opt-in manifest oracle
      (`VAULT_MANIFEST_ORACLE=steamcmd_api`, default OFF, mutation-pinned),
      own tables `oracle_app_state`/`oracle_branch_manifests` (schema v10,
      `depot_manifests` never written — test-pinned), passworded/unknown
      branches never stored, `public` excluded from the keep set, additive
      post-gate union in `gc.py` via generic `extra_manifest_ids` (no oracle
      import; orphans(on) ⊆ orphans(off)), fail-soft everywhere incl. broad
      catch so an oracle error can never fail a GC job; stdlib urllib with
      redirects refused + bounded read; three authed `/v1/oracle/*` routes.
      Renumbered twice by rebases: WP 3.12 took v8, WP 3.11 took v9, so the
      oracle tables are v10 — harmless, they add no column to either. 1193
      tests green, 17 mutations killed by named tests, reviewer re-verified
      with its own mutation set; Opus PASS ×3. Note: response shape modeled on
      api.steamcmd.net, not yet verified against the live service — mismatch
      degrades to oracle-off, documented in api/README)*
      *(WP 4a.6 done 2026-08-11: web settings view over GET/PATCH
      /v1/settings — per-key source/applies captions, only-changed-keys
      PATCH bodies (mutation-pinned), per-field reset, readonly banner;
      Steam identity per A+C — key entry/removal with masked last4,
      typed key cleared from DOM on every path (pinned), SteamID64
      validation with literal boundary fixtures incl. the BigInt
      0x-hex kill, library preview with 409 setup guidance, ADR privacy
      note; 3-step onboarding overlay with dialog semantics (focus trap
      deferred to 4a.8, recorded); serverUrl setting removed (same-origin
      by design); demo parity programmatically diffed against live
      responses; 242 headless tests; Opus PASS + should-fix round)*
- [x] Per-client hit statistics + bypass detection (`/v1/clients`)
      *(WP 3.11, ADR-0008: event sweep with a persisted byte-offset cursor,
      strict 9-field v1 parsing, miss-triggered prefill (cooldown + per-sweep
      cap + unmapped/shared/manifest never trigger), per-address hit stats,
      bypass detection failing toward NOT accusing; schema v9; new
      `GET /v1/stats`. Rotation is best-effort — in the shipped containers
      vault-api may read the log but not truncate it, which is handled,
      counted and surfaced rather than fatal)*
- [x] Optional generic webhook notifications (built as a generic webhook
      feature, not vendor-specific. NOTE: the original wording claimed
      "Discord/Slack/ntfy-compatible" — that overstates what shipped.
      Generic JSON receivers and n8n consume the envelope directly;
      Discord requires `{"content": …}`/`embeds` and needs a relay until
      the Phase 6 format adapters land)
      *(WP 3.13: five events — job.done/error/cancelled +
      client.bypass_suspected/resolved (transition-only, state tracked in
      both directions regardless of the delivery filter) — one generic
      JSON schema, single delivery daemon thread with bounded drop-oldest
      queue, measured zero worker latency against hanging receivers,
      userinfo→Authorization-header conversion with redacted logging;
      schema v10; suite 1102 green; Opus PASS + fix round)*
