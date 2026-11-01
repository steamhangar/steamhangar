# Plan archive: Phase 4h decision support in the desktop width

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

#### Phase 4h — Decision support in the reclaimed desktop width (user decision 2026-08-18)

The desktop portering (Phase 4e) frees width that today carries nothing. The
question "what do we put there" was answered deliberately: **decision support
about the cache, not taste.** SteamHangar knows things Steam does not — what is
on the disk, how much of it sits in SHARED depots, when a game was last
confirmed current, what is installed on which machine, and from
`depot_manifests`, how often a game actually changes. Crossed with playtime
(already relayed and validated, see below) that yields statements nobody else
can make:

- "43 GB cached, 0 minutes played" — prefilled and never touched
- "Installed on your PC, not in the cache" — the real prefill list, not a guess
- "Not confirmed current for 40 days" (`last_manifest_check` already exists)
- "Deleting frees 12 of 43 GB; the other 31 GB sit in depots other games use"
  (pure ADR-0003 data)
- "Changes every three days" vs "unchanged for two years" — the actual input
  for the Phase 4d decision about which games the sweep should keep current

**Deliberately rejected: taste recommendations** ("what should I play next" in
the Steam-discovery sense). They need tags, similar titles and review scores
from the store API, they duplicate a Steam feature, and they turn a cache
manager into a storefront — against the scope discipline in §9. The honest
version of the same idea needs no foreign data and is uniquely ours: *"these
games are fully cached and you have never started them — playable now, no
wait."* The cache is the reason "playable now" is true.

**Privacy stance (user, 2026-08-18), binding on every item below:** playtime
makes the UI judgemental ("never played"), and a household vault has more than
one person in the living room. So: off by default or dismissible at any time,
no nagging, and no number that gets held up to somebody else. This is the same
posture §7 Phase 6 already takes toward client addresses in webhook payloads,
for the same reason. WP 5.3's threat model must cover the new personal-data
surface (playtime and last-played now flow through the API response).

- [x] **4h.0 (api)** — the API-level half of the privacy stance above:
      two independent, off-by-default settings
      (`VAULT_RELAY_EXPOSE_PLAYTIME`/`VAULT_RELAY_EXPOSE_LAST_PLAYED`) gate
      whether `playtime_forever`/`rtime_last_played` may leave the Steam
      relay's response AT ALL, enforced by omitting the JSON key entirely
      (not `0`/`null`) when off. Deliberately environment-only — no
      `PATCH /v1/settings` override — because the `settings` table lives in
      a Docker volume that can be lost independently of the environment,
      and a privacy opt-out must not have "silently starts collecting
      again" as its failure mode; see
      [ADR-0010](adr/0010-relay-privacy-gate-env-only.md) for the full
      argument, including the runtime-toggle design considered and
      rejected. This is the API-only half of the stance: the display-side
      "dismissible at any time, no nagging" half remains 4h.2/4h.3's to
      build. `docs/security/threat-model.md` §4 updated accordingly.
- [x] **4h.4 (app)** — the Android app fetches library + persona through
      vault-api's Steam relay; the device-local Steam Web API key is gone
      (entry UI, EncryptedSharedPreferences storage, direct-to-Valve client
      — deleted, with a construction-time + sign-out scrub for legacy
      installs), no fallback by design so the 4h.0 gate cannot be bypassed
      from a phone. Private-profile limit is a first-class UI state and an
      accepted, recorded cost (ADR-0004 addendum 2). Suite 592→578 both
      variants, ledger reconciled per file; Opus review rounds 1-3
      (FAIL/FAIL/PASS — threat-model truth, pinned-the-fake call-site gap,
      dual-source-set isolation blind spot all closed). 2026-08-19.
- [x] **4h.1 (api)** — two small additions that make the panel sharp:
      `rtime_last_played` carried through the Steam relay (Steam returns it,
      we simply never asked; `playtime_forever` is ALREADY relayed and
      validated — `steam_relay.py`, unused by any frontend today), and a
      per-app change frequency derived from `depot_manifests`. Two honesty
      pins are the point of this package, not the plumbing: **an absent
      upstream field must surface as UNKNOWN, never as "never played"** (0 is
      a claim, absence is not), and **a short observation window must surface
      as "not enough data", never as "changes rarely"** — the manifest history
      starts when WE started watching, so on a young vault every game looks
      stable.
      *(WP 4h.1 done, Opus FAIL→fix→re-verified: `rtime_last_played` added to
      the Steam relay's `OwnedGame`/`OwnedGameOut` — no extra request
      parameter needed (verified against the Steamworks Web API docs, not a
      live account); an absent OR an implausible-sentinel (`<=0`) upstream
      value both degrade to `null`, never a manufactured `0` (pin 1) —
      wording fixed in the fix round: Steam's own convention IS that `0`
      means never-played (real information), discarded here only because
      `playtime_forever` already carries that fact and a 1970-01-01 render
      would be worse; `playtime_forever`'s existing type is untouched
      (additive-only). Change frequency: schema v14 adds
      `depot_manifests.first_seen_at`/`manifest_changed_at`/
      `observation_count` (ALTER-migration with a conservative recorded_at
      backfill, guarded against the table not existing yet); `GameSummary`/
      `GameDetail` gain `manifest_change_frequency`
      (`null`/`"insufficient_data"`/`"stable"`/`"changed"`),
      `manifest_observation_days`, and — fix round, reviewer-authorised
      semantic correction — `manifest_days_since_last_change`: the category
      was originally `"changing"`, which the review correctly called not a
      rate (an app that changed once 3 years ago and one that changes weekly
      were indistinguishable); renamed to `"changed"` (has-changed-at-least-
      once, past tense) and the new field carries the one rate-adjacent fact
      the table can actually support (days since the MOST RECENT observed
      change). Computed by
      `depot_manifests.change_frequency_for_app`/`.change_frequency_by_app`
      (bulk, no N+1, statement-count pinned) with a weakest-observed-depot
      rule: `null` only when an app has NO manifest rows at all (never
      conflated with `"insufficient_data"`, pin 2); fewer than 2 observations
      on any one depot, or an observation window under 14 days (matching
      `VAULT_GC_GRACE_DAYS`'s established scale), both force
      `"insufficient_data"` rather than a confident claim. Fix round also
      closed two review blockers: B1 (the deliverable WP 4h.2 consumes had
      zero endpoint tests — added to `test_games.py`, all four states over
      real HTTP for both routes, plus the null-out mutation re-run and
      confirmed dead by name) and B2 (`change_frequency_by_app` 500'd `GET
      /v1/games` on a poisoned `depot_manifests.appid` row that `gc.py`
      already defends against for the same table — fixed with the same
      `deletion.coerce_positive_id` skip, regression-pinned unit + HTTP).
      Both original honesty-pin mutations re-verified dead by name after the
      fix round. 1603 tests green, 1 skipped, on a tree rebased onto WP 4f:
      1554 baseline + 12 (WP 4f) + 37 across this package's three rounds
      (26 initial, 10 for the untested endpoint surface, 1 real pin for the
      most-recent-change guarantee). An earlier draft of this note claimed
      1580 and "net count unchanged by the fix round" — both wrong, and
      caught by the reviewer in the same diff that shipped them, which is
      why the numbers here are the measured ones. api/README.md documents both fields' exact semantics,
      absence/unknown handling, the observation-window caveat, the NOT-a-rate
      correction, the poisoned-row degrade, and that these fields survive
      cache deletion (unlike `size_bytes`). Not verified against a live Steam
      account/key — recorded on the real-world/device verification list,
      incl. the reviewer's own addition: every migrated row starts at one
      observation, so on a freshly upgraded vault the panel legitimately
      shows `insufficient_data` for every game until
      two post-upgrade observations plus a 14-day window exist.)*
- [x] **4h.2 (web)** — DONE 2026-08-19: both modes shipped (BP-XL right
      column + collapsible card below, one statement module), privacy stance
      structurally pinned (negative pin survived adversarial probing), demo
      fixtures corrected to the shipped default gate. Opus review, FOUR
      rounds: grid auto-placement blocker (browser-measured), a
      measurement dispute settled by full-chain measurement (both rigs
      right, different elements/page states — see LEARNINGS), the column
      released when there is nothing to say. 587→665 web tests. D-16.
      Original bullet: **4h.2 (web)** — the panel itself: a right-hand column at BP-XL
      (>=1800px), a collapsible card below that width. Serves the statements
      above. Depends on 4h.1's fields and on Phase 4e's breakpoints.
      **Panel removed 2026-10-13 (user decision), statements moved, see
      `docs/handover/post-0.1.0-roadmap.md` WEB-FEAT-SUGGEST-2:** "last
      changed" to the detail sheet ("Last update downloaded N days ago"),
      "fully cached, never played" to a card pill ("Never played"), "not
      confirmed current" to one bell notification at 14 days, "unchanged
      for N days" dropped.
- [x] **4h.3 (web)** — DONE 2026-08-19: header art in the detail card
      (aspect-ratio as the only reservation, whole-wrapper collapse on 404
      — verified against the real CDN both ways), 665→676 web tests, Opus
      PASS round 1 incl. four reviewer-own mutations and a hostile-appid
      host-escape probe (impossible by construction). D-17. PHASE 4h
      COMPLETE. Original bullet: **4h.3 (web)** — header art in the detail drawer. Nearly free: a
      predictable `header.jpg` URL on the CDN host the CSP already allows for
      cover art, no relay endpoint, no new external call.
- Screenshots (the `appdetails` store API) are **not** in this phase:
  undocumented, rate-limited, no CORS, so they need their own relay endpoint
  and privacy note — and a screenshot does not help anyone manage a cache.
  If wanted at all, they belong to Phase 6 with the other external
  integrations. **Dropped (user decision 2026-10-16):** Steam-like
  screenshots on the detail card are rejected and will not be built in
  any phase.
