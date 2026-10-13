# ADR-0022: Group DLC/content packages under their parent game (TOOL-APPS-2, roadmap D9)

Date: 2026-10-13
Status: Accepted 2026-10-13. Decided by the user: Q1 A, Q3 B variant, Q4 A,
Q5 A, Q6 (user variant), Q7 A, Q8 B (after P1, roadmap D9); by the
orchestrator: Q2 A and the grouped-delete `members` confirmation. Server
side implemented in TOOL-APPS-2a/2b and documented in TOOL-APPS-2e
(api/README.md "Content packages" and "Grouped delete"; threat model §5
items 2 and 6; ADR-0006/0007 addenda and an ADR-0011 note of 2026-10-13).
UIs: TOOL-APPS-2c/2d. (Original status: Proposed, Q3/Q7/Q8 open until P1.) Roadmap item D9 (TOOL-APPS-2),
`docs/handover/post-0.1.0-roadmap.md`, wave plan item 11. Extends the
API-FIX-4 tool-app rule (`api/vault_api/tool_apps.py`, api/README.md
"Steam tool apps"). Touches ADR-0003 (deletion, decision Q6), and, only
if Q8 is answered with Weg A, ADR-0006 addendum 2026-10-13 point 3 and
ADR-0011 (see Q8).

ADR numbers 0018 and 0019 are taken by the unmerged D2/D3 drafts on
`wp/docs-adr-post-0.1.0-d1-d3`; this is the next free number.

Evidence labels: **(repo)** read in this repository at `main` c547e90;
**(prod)** observed on the operator's rc14 production library, as given
in the roadmap, not re-measured here; **(Steam, unverified)** a fact about
Steam's PICS data or the store/Web API taken from general knowledge of the
public protobufs and SteamDB, NOT fetched in this session (the devbox has
no reason to call Steam, and the dev instance is offline);
**(inference)** reasoned, not measured.

## Context

**What the user sees (prod).** The rc14 library shows many bare
"APP 25062xx" cards, mostly Helldivers 2 DLC/content packages. User
decision 2026-10-13: group them under the parent game, do not merely
filter them out.

**Where these rows come from (repo).**

1. An agent reports every `appmanifest_<appid>.acf` it finds
   (`agent/go/acf/appmanifest.go`). The report carries only app ids
   (`agent/go/report/report.go`: `client_id`, `appids`); the ACF name is
   not sent. If Steam keeps a content package in its own appmanifest, its
   app id is reported as installed like a game (inference: that is the
   only path by which an unnamed seven-digit id with an installed client
   reaches the library).
2. `scheduler.compute_targets` turns every freshly reported app id into a
   sweep target; `jobs.ensure_app_row` creates an `apps` row with
   `name = NULL` on enqueue (`api/vault_api/jobs.py`).
3. SteamPrefill is run for that one id (`prefill.write_selected_apps`).
   For an id it does not consider (not an owned standalone app) it exits 0
   with `Updated == 0 AND Up To Date == 0`; the worker's "unowned" branch
   ends the job `error`, sets `apps.status = 'error'` and touches no
   mapping (`api/vault_api/worker.py`, "SteamPrefill did not consider this
   app"). Whether the HD2 packages end exactly here, or are prefilled as
   apps of their own, is **not measured** (step P1 below).
4. `GET /v1/games` (`api/vault_api/routers/games.py`) returns one flat row
   per `apps` row. With no name, no entry in the relay's owned-games list
   (`GetOwnedGames` lists games, not DLC: Steam, unverified) and usually no
   cover, both UIs fall back to `App {appid}` (`web/js/lib/game-status.js`,
   `web/js/lib/owned-library.js` `appTitle`).

This is the same mechanism API-FIX-4 met with app 228980 (tool package
reported everywhere, never prefillable, "Failed / Retry download"), one
step wider: API-FIX-4 answered it with a fixed list of one id. A fixed list
cannot follow a game that ships dozens of content packages.

**Depots of a DLC are usually already attributed to the parent (inference,
repo).** Depot attribution is "depots that changed during this app's
prefill run" (`prefill.apply_observed_mapping`, ADR-0003 decision 3). When
SteamPrefill prefills the parent it also downloads the depots of DLC the
account owns, because PICS lists those depots inside the parent's
`depots` object with a `dlcappid` (Steam, unverified; consistent with
`oracle.KNOWN_NON_DEPOT_KEYS` containing `hasdepotsindlc`, repo). So the
bytes of a content package are normally counted in the parent's
`size_bytes` today, and the child row has `depot_count = 0`,
`size_bytes = null`. Step P1 confirms this before any code relies on it.

## What could name the parent

| Source | Field | Host / egress | Covers | Cost |
|---|---|---|---|---|
| S1. Store item lookup `IStoreBrowseService/GetItems` (already called by `cover_art.py`) | `related_items.parent_appid` with `data_request.include_related_items`, `type` and `name` with `include_basic_info` (Steam, unverified) | `api.steampowered.com`, baked into vault-proxy, always allowed (ADR-0011 §3 and its 2026-10-10 addendum) | Store-visible DLC. **Not** non-store items: 228980 answers `success: 15` today (repo, `cover_art.py` docstring), and hidden content packages may do the same | No new request: two more flags in the request the cover refresher already sends for every tracked app. Timing: see Design §1 |
| S2. Manifest oracle, PICS mirror `api.steamcmd.net` (`oracle.py`) | `common.type` (`Game`, `DLC`, `Tool`, `Application`, `Config`, `Music`, ...) and `common.parent` on the child; `extended.listofdlc` and per-depot `dlcappid` on the parent (Steam, unverified) | `api.steamcmd.net`, allowed only while the oracle is on (ADR-0011 2026-10-13 addendum); oracle on by default since UPD-1, `off` opts out | Every app PICS knows, incl. non-store packages and tool apps | The oracle already downloads the whole app-info document; `parse_app_info` reads only `depots` today (repo). The automatic update check asks only about **cached** apps (`update_check.py`, api/README.md `POST /v1/updates/check`), so a child with no mapped depots is never sent by the automatic check (a manual `POST /v1/oracle/{appid}/refresh` can still ask about any id). Classifying uncached children means sending more app ids to a third party (Q8) |
| S3. The parent's own oracle answer | `depots.<id>.dlcappid` of an already-checked parent | as S2 | Only children whose depots the parent lists, only for cached parents | No extra app ids leave the LAN; misses content packages that are separate apps without a `dlcappid` link |
| S4. Agent-side, from the client's Steam install | `appinfo.vdf` (binary PICS cache on each PC), same `common.parent` | none (LAN only) | Everything installed on a client with an agent | A binary, versioned format parser in Go on three OSes plus a report-format change (LEARNINGS: optional request fields break old servers, AGENT-FEAT-1); largest effort. Not chosen (Q1) |

**When no source knows the parent** (oracle off, GetItems has no item,
offline vault, a brand-new package): the row stays a top-level card exactly
as today. Grouping is a display improvement that must degrade to the
current behaviour, never hide a row. A row whose parent appid has no
`apps` row of its own (parent not tracked) also stays top-level, named
"<parent name> content" if the parent's name is known, else `App {appid}`.

## Decisions

1. **Q1, parent source (user, 2026-10-13, Weg A).** Store lookup (S1)
   first; the oracle (S2/S3) only for ids S1 cannot answer, and only while
   the oracle is on. Agent-side S4 is not built.
2. **Q2, where grouping happens (orchestrator, 2026-10-13, Weg A).** The
   server returns the flat list as today plus `parent_appid`; the UIs
   group. The server never nests or drops rows.
3. **Q4, display (user, 2026-10-13, Weg A).** A "+N content packages" line
   on the parent's card; the package list in the detail sheet. No
   expandable card in the grid.
4. **Q5, prefill (user, 2026-10-13, Weg A).** What is prefilled stays
   unchanged until P1 has measured what happens to the packages today.
   Grouping must not change the target set of any download path (§4).
5. **Q6, delete (user, 2026-10-13, user variant).** Deleting a grouped game
   ALSO deletes its packages' own depots when they would be orphaned;
   depots shared with another cached game are kept, as today. Exact
   semantics in §4.

## Design (proposed)

### 1. Classification is stored, provenance-tagged, refreshed in the background

- New table `app_classification(appid PRIMARY KEY, app_type, parent_appid,
  source, checked_at)`, written only by background refreshers, never on the
  request path (same rule as `cover_art.py` and the update check).
- `source` names where the row came from: `store` (S1) or `steamcmd_api`
  (S2/S3). S4 is not built (Q1); should it ever be, its value is `agent`.
  This follows oracle rule 3 ("oracle data never becomes vault-api's own
  record"): third-party facts are labelled as such and live in their own
  table.
- `app_type` is one normalised lowercase vocabulary, whatever the source:
  `game`, `dlc`, `tool`, `application`, `config`, `music`, `video`,
  `demo`, or null (unknown or a value outside this set). The S1 numeric
  store type and the S2 PICS `common.type` string are each mapped onto it
  in one function with a literal-table test.
- Ids are validated with `deletion.coerce_positive_id`.
- Apps on the fixed tool list (`tool_apps.TOOL_APPS`, e.g. 228980) are
  never classified as children: a `parent_appid` stored for them is
  ignored, they stay top-level tool cards and never join a group or a
  group delete (§4).
- **Nesting depth (decided here).** Grouping is one level. A package whose
  parent is itself a package is attached to the top-level parent, found by
  following `parent_appid` up to a bounded depth (4). A cycle, a self
  reference, a chain longer than the bound, or a parent unknown to the
  table leaves the row ungrouped (`parent_appid` null in the response).
- **S1 timing.** The cover refresher re-asks an app only when it is due:
  30 days after a found cover, 7 days after "none" (`REFRESH_FOUND_DAYS`,
  `RETRY_NONE_DAYS`, `cover_art.due_appids`). Without help, existing
  installs would get parents only over the next month. The migration that
  creates `app_classification` therefore sets `next_check_at` to "now" once
  for every `app_cover_art` row, so the next ticks (50 apps per 60 s,
  `BATCH_SIZE`/`TICK_SECONDS`) re-ask every tracked app with the new
  flags. A stored cover is kept on that re-check (existing rule).
- `GET /v1/games` / `GET /v1/games/{appid}` gain `parent_appid: int | null`
  and `app_type: str | null`. Every other field stays the raw per-app
  truth, as API-FIX-4 did with `tool_app`.
- **Compatibility, both directions.**
  - New server, old client: the Android client decodes with
    `ignoreUnknownKeys = true` (`app/.../net/VaultJson.kt`, repo) and the
    web ignores unknown fields, so old clients keep the flat list.
  - New client, old server: the Kotlin and JS models default both fields
    to null; missing means ungrouped. Each platform gets a test that feeds
    a pre-TOOL-APPS-2 `GET /v1/games` response (no new fields) and asserts
    the flat list renders unchanged.
  - The web UI ships inside vault-api's image, so web and API always match;
    the Android app does not, hence the second direction matters there.

### 2. What rolls up to the grouped card, and how

The UIs group the flat list (decision Q2). The group card shows the
parent's own row plus an aggregate:

- **Size:** the union of the depots of parent and children, each depot
  counted once (ADR-0003 shared-depot semantics). Never the sum of the
  per-app `size_bytes`, which would double-count a depot mapped to both. If
  P1 confirms children have no own depots this equals the parent's size,
  and the UI may simply show it. Needs depot ids per app in the list
  response or a server-side `group_size_bytes`; decided in the API package.
- **Update available (UPD-1):** the group shows the badge when the parent
  or any child has `update_available`. Children are rarely cached, so in
  practice this is the parent's own verdict. The card's update action
  enqueues the **parent's** prefill only (the same single job it enqueues
  today); it does not add child jobs.
- **Installed on:** union of `installed_on` across the group.
- **Status / failed jobs:** the parent's status is the card's status.
  Whether a child's `error` shows on the card is open question 3. What the
  "Failed" chip counts under each option: Weg A counts a group only when
  the parent failed; Weg B also counts a group whose parent is fine but a
  child failed. Either way a group counts once, never once per package.
- **Downloads view:** job rows of a child are labelled
  "<parent name> · content <appid>" instead of `App <appid>`.

### 3. Rendering (decision Q4)

- Web (`web/js/components/game-card.js`) and Android (`GameCard.kt`,
  `GameListRow.kt`): one card per parent, with a small "+N content
  packages" line. The detail sheet (web `game-detail-sheet.js`, Android
  `GameDetailSheet.kt`) lists the packages: app id, name if known, size,
  status, installed on.
- Filter chips and header counts count groups, not packages (a package
  must not make "Failed" or "Updates" count a game twice).
- Demo fixtures on both platforms get a grouped example and the twin
  drift pin from LEARNINGS ("Demo fixtures drift back the moment a twin
  lacks the twin pin").

### 4. Downloads, deletion, GC

**Prefill (decision Q5): the target set stays exactly today's.**

- The scheduler, the miss trigger and `POST /v1/prefill/cached` are
  server-side and do not read `app_classification`; unchanged.
- **Bulk actions must be made to keep today's set; grouping alone would
  change it.** Today `classifyBulkSelection` (`web/js/lib/bulk-plan.js`)
  puts an errored child into `needsDownload`, so selecting it downloads
  it; once children are hidden inside a group card they cannot be selected.
  Rule: selecting a group card in bulk mode selects the parent **and every
  child**, and the bulk plan classifies each member exactly as it would
  classify the ungrouped row today (busy / needsDownload / current /
  notOnVault / toolApps). Same rule on Android (`BulkPlan.kt`). A test per
  platform asserts that the bulk plan's targets for a grouped selection
  equal those for the same rows selected ungrouped.
- The single-card download/retry and update actions act on the parent
  only (§2), as they do today for the parent's own card.
- Whether children should stop being prefilled at all is a separate
  decision after P1 (Q5 follow-up), because it changes what is downloaded.

**Delete (decision Q6).**

- **Endpoint.** `DELETE /v1/cache/{appid}` on a **parent** gains the
  optional query parameter `with_packages=true`. Without it the endpoint
  behaves exactly as today (old clients unchanged). The server builds the
  group itself from `app_classification` (it never trusts a client-sent
  list), and the response reports per member app.
- **Who is a deletable package (orchestrator, matching the user's
  "packages" intent: a real game is not a package).** Only a child whose
  `app_type` is one of `dlc`, `config`, `music`, `video`, `demo` is a
  *package* for the delete. A child typed `game`, `application`, `tool` or
  null (unknown) may still be shown in the group, but for the delete it is
  an ordinary outside app: it protects its depots like any other co-owner
  and is never deleted by `with_packages=true`. Apps on the fixed tool
  list (`tool_apps.TOOL_APPS`, e.g. 228980) are never classified as
  children at all (§1), so they never join a group delete.
- **What is deleted.** The candidate depots are the union of the depots
  mapped to the parent and to each package. A candidate is deleted when
  no app **outside the delete set** (the parent plus its packages) protects
  it under today's rule (ADR-0003 addendum, api/README.md "Last cached
  remnants": a co-owner with `status != 'idle'`, a non-null
  `last_prefill_at`, an active job, an unreadable mapping, or no `apps`
  row protects). Members of the delete set do not protect each other's
  depots, otherwise a package left at `error` by an "unowned" run would
  keep its parent's shared depot forever. That is what "would be
  orphaned" means here.
- **What is kept.** Every depot that another app outside the delete set
  still protects, i.e. one shared with another cached game or with a
  group member that is not a package; reported in `skipped_shared` as
  today. Mapping rows are kept (ADR-0003).
- **Plan and execution agree.** Today `deletion.load_co_owners` and
  `deletion.delete_app_depots` exclude only the one appid being deleted.
  For a group delete both must exclude the whole delete set, through one
  shared definition of it, so the dry plan the dialog shows and what is
  actually removed cannot diverge (LEARNINGS "two call sites computing the
  same domain predicate WILL diverge").
- **Preconditions.** `409` if any member of the delete set has a queued,
  running or paused prefill or GC job (today's rule, applied to every
  member). Each member that lost depots is reset like a single deletion
  (`deletion.reset_app_after_deletion`).
- **Confirmation dialog** (web and Android): lists the parent and every
  package by name or app id, the bytes that will be freed, and the depots
  kept because another cached game uses them. Bulk delete across several
  group cards uses the same per-group semantics, and its confirmation
  lists all packages of every selected group, not only the parents.
- **Classification now widens a delete, stated openly.** Store or oracle
  classification data (S1, S2/S3) decides which apps are packages, and so
  can now widen what a user-confirmed delete removes. Until now the oracle
  promised that its data can only ADD protection, never remove it
  (`api/vault_api/oracle.py` module docstring, "The one safety invariant";
  ADR-0007 decision B, ADR-0006 decision 4). That promise no longer holds
  in full: a wrong `parent_appid` or `dlc` type can put another app's
  depots into the delete set. Bounds: the delete is user-started and
  opt-in per request, every affected app is listed in the dialog, only
  package-typed apps lose protection, and a depot any app outside the
  delete set still protects is kept. TOOL-APPS-2b updates the oracle.py
  docstring to say exactly this; TOOL-APPS-2e records it as an addendum
  to that promise in ADR-0006 and ADR-0007.

**GC.** Per app, unchanged. Classification data never enters the GC keep
set or the automatic (GC) deletion path; the only deletion it affects is
the user-confirmed group delete above.

**Tool apps.** The fixed list stays the only thing that stops a prefill
until open question 7 is answered.

### 5. Tool packages: fixed list vs. dynamic classification (API-FIX-4)

With S2 in place, `app_type == "tool"` (from PICS `common.type`; Steam,
unverified for 228980; P1 measures it) could mark tool packages without a
code change. Two separate effects must not be conflated:

- **Display** (`tool_app: true`, no download/retry action, no "Failed"):
  safe to drive from classification; the worst wrong answer is a card shown
  as a tool.
- **Never prefill** (scheduler, miss trigger, `POST /v1/prefill` 422,
  `enqueue_prefill` refusal): a third-party answer would then decide what
  the vault does NOT download. A wrong or poisoned "Tool" answer for a real
  game silently stops its updates. API-FIX-4 made the list a code change
  "on purpose: it changes what the scheduler downloads".

Proposed default: dynamic classification drives display only; the
prefill skip stays on the reviewed fixed list (open question 7).

## Prove before building (P1, read-only, on prod with the user)

1. `GET /v1/games` on prod: for three HD2 package ids, record `status`,
   `depot_count`, `size_bytes`, `installed_on`, and the jobs they have
   (`GET /v1/jobs`): unowned `error` every sweep, or real prefills?
2. Does HD2's (553850) depot mapping already contain the packages' depots
   (`GET /v1/games/553850` depot list vs. the oracle's `dlcappid` data)?
3. One `GetItems` call with `include_basic_info` + `include_related_items`
   for 553850, two package ids and 228980; one oracle `GET` for the same
   ids. Record which fields exist, `common.type` of 228980, and
   `common.parent` of the packages, and whether GetItems answers the
   packages at all. No code depends on an unverified field name before
   this is done.

## Open questions for the user (after P1)

3. **A package's failed job on the group card.** Weg A: ignore a child's
   `error` on the card (shown only in the package list of the detail
   sheet); the "Failed" chip counts the group only when the parent failed.
   Weg B: show a small warning on the card when any child failed; the chip
   also counts such groups. Recommendation: A, if P1 shows these are
   "unowned" runs; B if they are real download failures.
7. **Tool packages (API-FIX-4).** Weg A: dynamic classification
   (`app_type == "tool"`) for display only; the never-prefill rule stays on
   the fixed list. Weg B: dynamic classification also stops prefills.
   Weg C: keep the fixed list for both, no dynamic classification.
   Recommendation: A; B lets a third-party answer stop a real game's
   updates.
8. **Oracle for packages the store cannot name.** Only relevant for the
   ids S1 leaves unanswered (Q1 = A). Weg A: for those ids, also ask the
   oracle when the app is tracked but uncached. Weg B: no extra oracle
   requests; use only what the oracle already returns for cached apps,
   including the parent's `dlcappid` links (S3). Recommendation: B if P1
   shows S1 names the HD2 packages; A only if it does not.
   **Weg A partly supersedes two recorded decisions** and needs addenda
   before code: ADR-0006 addendum 2026-10-13 point 3 ("cached games only
   ... never widens the set to owned-but-uncached apps") and ADR-0011's
   2026-10-13 addendum ("it receives the cached app ids"). It sends more
   app ids (uncached, tracked packages) to a third party, so it also needs
   a threat-model entry (`docs/security/threat-model.md`, outbound flows)
   and one more sentence in the api/README.md and `.env.example` privacy
   text.

## Consequences and honest limits

- With the oracle off and no store item, nothing groups; the library
  looks exactly as it does today.
- Grouping depends on Steam data that can change; a package can move
  between grouped and top-level after a refresh.
- Field names in S1/S2 are unverified until P1; the design is written so
  that a missing field means "no parent", never an error.
- Downloads are unchanged (Q5); GC is unchanged. The one behaviour change
  outside display is the grouped delete (Q6), which is opt-in per request,
  confirmed in a dialog listing every package, limited to package-typed
  members, and still bound by the shared-depot rule against every app
  outside the delete set. It is also the one place where store or oracle
  data can widen a deletion (§4).

## Work package split (after P1 and the open answers, each at most 1-2 h)

1. **TOOL-APPS-2a (api)** classification table and migration (incl. the
   one-time `next_check_at` reset), S1 flags in the cover refresher, S2/S3
   parsing for gaps, `parent_appid`/`app_type` in the game endpoints,
   tests incl. "missing field = no parent", nesting/cycle rejection and the
   type vocabulary table.
2. **TOOL-APPS-2b (api)** grouped delete (`with_packages=true`): delete
   set built server-side (parent plus children typed `dlc`, `config`,
   `music`, `video`, `demo`; never a fixed-list tool app);
   `deletion.load_co_owners` and `deletion.delete_app_depots` exclude the
   whole delete set through one shared definition, not just the one
   appid; everything else under today's remnant rule; update the
   `oracle.py` safety-invariant docstring (§4). Mutation-tested pins for
   "another cached game keeps its depot", "a member wrongly classified as
   a game keeps its depots", "a fixed-list tool app never joins the
   delete", "plan and execution delete the same depots" and "old call
   without the parameter unchanged". **Recorded after the 2a review
   (2026-10-13):** only a child whose OWN type is a package type is
   deletable: the store's type or the app's own PICS `common.type`
   (`app_classification.Classification.own_app_type`). A `dlc` inferred
   only because a cached parent's `extended.listofdlc` / depot `dlcappid`
   names the app (the `linked` flag) is display data and not enough.
3. **TOOL-APPS-2c (web)** grouped card, package list in the detail sheet,
   counts per group, bulk selection expands to children (same targets as
   ungrouped), delete dialog listing packages, Downloads labels,
   old-response test, demo fixture + drift pin.
4. **TOOL-APPS-2d (app)** the same on Android, plus screenshot goldens
   (APP-TEST-1 harness).
5. **TOOL-APPS-2e (docs)** api/README.md, the threat model
   (`docs/security/threat-model.md`: the store lookup's new flags; and, if
   Q8 = A, the new oracle flow plus the ADR-0006/ADR-0011 addenda), the
   addendum to the oracle's "can only add protection" promise in ADR-0006
   and ADR-0007 (§4), this ADR to Accepted. **Recorded after the 2a
   review (2026-10-13):** document that fixed-list tool apps are never
   parents either (a chain reaching one stays ungrouped); the new GetItems
   flags (`include_basic_info`, `include_related_items`) and what they
   return in api/README.md and the threat model; the new `parent_appid` /
   `app_type` fields; the per-field merge (store first, oracle parent for
   a store answer without one); and the name fill (`name` falls back to
   Steam's store name, then the oracle's stored `common.name`, only when
   vault-api has no name, never written into `apps`, plain text capped at
   256 characters). **Recorded after the 2b review (2026-10-13):** the
   grouped-delete contract (`with_packages=true` plus the required
   `members=<csv>` confirmed from `GET /v1/cache/{appid}/delete-plan`,
   `409` with both lists when they differ), and one README sentence on
   nesting: in a chain Y (dlc) -> X (game) -> Z, deleting Z with packages
   deletes Y and keeps X (Y resolves to the top-level parent Z; X is a
   game-typed child and stays an outside owner).
