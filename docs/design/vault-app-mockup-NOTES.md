# vault-app mockup — design notes

Companion to `vault-app-mockup.html` (same folder). Clickable design prototype for
Phase 4, **not** production code, **not** in the repo.

Open the file in any browser. On desktop it renders a 390 x 844 phone frame; below
480 px viewport width it drops the chrome and runs fullscreen on a real phone.
No external requests at all (audited: zero `src`/`href` attributes, zero remote URLs),
so it survives a strict CSP.

## How to drive it

| Path | What happens |
|---|---|
| open normally | first-launch flow: **Connect → Steam identity → Ready → Library** |
| `Skip` (header) or `Skip for now — browse in demo mode` | jumps to the Library, marks the context strip `DEMO` |
| `#library`, `#downloads`, `#settings`, `#demo` in the URL | deep-links past onboarding — handy for reviewing one screen on the phone |
| Settings → **Reconnect / switch account** | replays the first-launch flow |
| Settings → **Show API mapping** | labels every action with the vault-api endpoint behind it |
| the three icons in the Library header | switch layout: 2 columns · 3 columns · list |
| the **bell** in the Library header | notifications panel (finished downloads, updates, bypass warnings, job errors); unread badge — **tap any item to jump to what it is about** |
| onboarding **Back** (steps 2 & 3) | return a step without losing any entered value |
| the **pill on a cover** | status icon + size in one object — and a button: tap to download / update / pause |

Working interactions: **search** · filter chips · **layout switch (2 / 3 columns / list)** ·
long-press (or right-click, or the header select icon) → multi-select → bulk download ·
detail sheet · **tap a shared depot row to see its co-owners** · download → animated job in
Downloads → badge flips to green (a first-ever download also fills in the previously unknown
depot list) · **pause / resume a running download** · delete → confirm → game flips to
"not cached", free space grows, and every other game's "shared" label updates · job cancel ·
history log expanders · bypass banner → clients sheet · connectivity profile switching ·
vault rename · masked key reveal · Test connection · pull-to-refresh (touch) / refresh icon ·
Escape as a back gesture.

Worth trying in this order to see the round-3 fix: delete **Nebula Drift**, then open
**Ironwood Hollow** and tap depot 228990 — Nebula Drift is still listed as a co-owner,
dimmed, marked "not cached · mapping kept".

Worth trying to see the round-4 fixes:

1. Open **Tundra Protocol** → it has three shared depots. Tap 317410: it is still tagged
   `shared`, but the panel says **no cached co-owner** because Meridian Rally (its only
   other owner) has nothing on the cache.
2. Hit delete on Tundra Protocol: the dialog is a bullet list — 228990 and 442790 *kept*,
   317410 *freed*, with the totals underneath.
3. Switch the library to 3 columns and to list, then search and multi-select in each.
4. Download any not-cached game, go to **Downloads**, hit **Pause**, then **Resume**.

Worth trying to see the round-5 fixes:

1. **Status icons.** Scan any layout: every game carries a glyph, not a bare dot —
   check (current), download arrow (not cached), circular arrows (update ready), pause
   bars (paused), `!` (failed). Start a download and watch the arrow *slide*; update a
   stale game and watch the arrows *spin*; a library at rest is completely still.
2. **Multi-select semantics.** Select one not-cached game and one cached game: the button
   reads **Download 1 of 2** and the note says *1 already cached — not re-downloaded*.
   Select only cached games: the primary disables ("All cached") and a **Re-download**
   secondary appears. Select a cached + a stale game: the primary becomes **Update 1 game**.
3. **Notifications.** The bell shows an unread badge on launch; open it for the seeded
   items (DEMON bypass, three updates, one failed run). Finish a download and a new
   *Cached* item appears and the badge bumps.
4. **Pause from the detail sheet.** Start a download, open the game — the sheet now offers
   **Pause** (it only offered Resume before).
5. **Sole-holder depot.** Open **Tundra Protocol** → depot 317410: tagged
   *shared · sole holder*, panel leads with "You are the only cached game using this depot
   — deleting frees it." Then delete everything except **Nebula Drift** and open it: depot
   228990 flips to the same sole-holder state.
6. **Stale needs cache.** Delete a stale game (Ashfall / Voidbreaker): it becomes *Not
   cached* and the **Updating** chip count drops — no game is ever stale with an empty cache.
7. **Onboarding Back.** Fill in step 1, sign in on step 2, reach step 3, then press Back
   twice — every value (URL, key, vault name, sign-in) is still there; step 1 has no Back.

Worth trying to see the round-6 fixes:

1. **Tap a notification.** The bell → tap an *Update ready* row (opens that game), the
   *DEMON* warning (opens the client list), the *Failed* row (jumps to Downloads with the
   job's log already expanded). The panel closes on the way.
2. **The pill.** Every cover now carries one pill: status icon + size. Tap it on a
   not-cached game to start the download without opening anything; tap it on a stale game
   to update; tap a running one to pause, a paused one to resume. A *current* game's pill
   is deliberately inert.
3. **Animations.** Start a download — the arrow drifts slowly and has no line under it.
   Update a stale game — two arrows turn a full, even circle. Nothing else moves.
4. **Filter chips.** A first-time download now sits under **Downloading**, never under
   **Update ready**.
5. **Multi-select.** Select Nebula Drift + Ironwood Hollow + Tundra Protocol and hit
   **Delete 3 games** — the dialog frees depot 228990 *because all three holders are in the
   batch*; deleting only two of them keeps it. The bar itself no longer overflows.

Worth trying to see the round-7 fixes:

1. **Live animation.** Start a download and watch the pill on the card: the arrow now runs
   its full slow loop instead of stuttering, and it matches the legend in the detail sheet
   exactly. Same for an update's turning arrows, on the card, in Downloads and in the
   detail sheet.
2. **Toast.** Trigger any toast — it now sits just above the bottom nav. Enter multi-select
   while it is on screen: it lifts to sit on top of the bulk bar instead of behind it.

**Sample-data note.** Tundra Protocol was given five depots (three of them shared) so the
bullet-list dialog has a realistic worst case, and Meridian Rally is seeded as a
*previously deleted* game — mapping rows intact, nothing on disk — so the "shared depot
with no cached co-owner" state exists on first load instead of having to be constructed.

## Design decisions

**Palette.** Ground `#0A1016` with surfaces at `#111C24`/`#17252F` — a blue-grey
biased slightly toward cyan so the neutrals and the accent belong to the same family.
Accent is a vault aqua `#2ED9CE`, used only for primary actions, active nav, focus
and progress. Status colors are a separate semantic set and never double as accent.
**Retuned in round 5 for CVD separation** (see "Status icons" below): green `#57DD8A`
current · yellow `#F2CE5B` running · orange `#F07B2E` update ready · grey `#8296A6` not
cached · red `#FF6F6F` destructive. The three coloured states now differ in *lightness*
as well as hue, because red-green colour-blindness collapses them onto a light-dark axis;
grey is fully desaturated so it reads as neutral. Colour is only the **third** cue —
shape (a status-icon glyph) is first, the status word second where there is room.

**Committed to dark, on purpose.** The app surface is single-theme (your decision);
only the desk backdrop behind the phone follows the viewer's light/dark preference,
so the page never looks broken in a light context.

**Type.** No webfonts — a CDN font would be blocked by CSP and fail silently. Stack
is Roboto first (the authentic Android system face), Roboto Condensed for capsule
art titles (uppercase, tight tracking, the way cover typography actually behaves),
Roboto Mono for every number, id, path and endpoint, with `tabular-nums` so sizes
and percentages line up in columns.

**Covers.** 600x900 portrait ratio, built from two per-title hues plus one of six
overlay patterns (diagonal band, bottom glow, horizon, corner burst, scanlines,
concentric rings) and a shared top-left light. The grid reads as artwork rather than
16 identical rectangles, and it costs nothing to swap in real Steam artwork later.

**Search (a 400-game library needs it up front).** A persistent field above the filter
chips, not an icon that expands: with 412 owned titles, search is the primary way into
the grid, so hiding it behind a tap would be the wrong trade. Case-insensitive
substring match on the title, live on every keystroke. Search and chips are **ANDed**,
and the chip counts recompute against the current query — so "Cached 5" always means
"5 things this chip would show you right now", never a number the grid can't produce.
The empty state names the query and offers one button that clears it. Escape clears the
field without disturbing the rest of the app. **Selections survive a search:** the
selected set is held by app id, not by what's on screen, and the bulk bar says
"3 selected · 1 out of view" when a selection is filtered out — so nobody downloads a
game they can no longer see without being told.

**Wording: "download", not "prefill".** Every user-facing control now says
**Download to cache** / **Update cached copy** / **Download again** / **Download
selected**; job states read "Downloading", the detail sheet says "Last download".
"Prefill" is server vocabulary — it describes filling a cache *before* anyone asks for
it, which is a maintainer's mental model, not the phone user's. The user's intent is
simply "put this game on my server". The API-mapping chips deliberately keep saying
`POST /v1/prefill`: that is the real endpoint name, and the toggle exists precisely to
show the technical layer underneath the friendly label. The Downloads screen title
stays "Downloads".

> **Footnote for the repo:** UI language is *download to cache*; the API, the job
> types, the docs and the SteamPrefill integration keep the *prefill* terminology.
> One concept, two vocabularies, on purpose — do not rename the endpoints.

**Vault name.** Optional field in onboarding step 1 (placeholder `e.g. vault-01`,
defaulting to the server hostname) and editable later under Settings → Server. It
appears in the library context strip, the Downloads subtitle and the step-3 summary.
Both fields edit one value and mirror each other live. Rationale: a homelab often has
more than one box, and "vault-01 online" is a much better anchor than a bare URL.

**Login explainer (onboarding step 2).** Restructured to the information architecture
you asked for: a plain-language headline ("Signing in with Steam lets the app show
your library"), what happens without it, an explicit list of what gets fetched
(SteamID64, persona name, avatar, owned games — all public profile data, fetched by
*this phone* directly from Valve), the not-affiliated disclaimer in its own box, then
a prominent full-width **Sign in through Steam** button, and finally a
"How can I verify this is legitimate?" paragraph telling the user to check the address
bar for `https://steamcommunity.com`, that an already-signed-in browser only needs one
click, and that SteamHangar never receives a username or password (ADR-0004). The
information architecture is borrowed; none of the visual design is — our palette, our
type, our layout, and a neutral external-link glyph rather than any Steam iconography.

**Depot sharing is computed, never stored (bug fix, round 3).** The first version wrote
the co-owner's name into each depot row as a string, so deleting Nebula Drift left two
other games still claiming "shared with Nebula Drift". The data model is now the same
shape the API actually has: a game's depot list **is** its mapping-row set
(`{id, gb}`), and ownership is derived on every render —
`depotOwners(depotid) = games whose mapping rows contain it`. Sharing therefore cannot
go stale: delete a game, download a new one, and every "shared" label recomputes.

The nuance worth keeping: **deletion removes cache content, not mapping rows.** A
deleted game stays a co-owner of its shared depots, so the co-owner list shows it
dimmed as "not cached · mapping kept" rather than dropping it. Same reason a deleted
game with a shared depot still reports those bytes as its size — that content really is
still on disk, and vault-api reports it the same way.

**Depot rows say "shared", and nothing else (round 3).** The row is now id · `shared` ·
size; who it is shared with is one tap away in an expander listing each co-owner with
its current status, plus the line that explains the consequence: "this depot is only
removed once no mapped game needs it". Scanning a depot list is the common case;
"which other game holds this" is the occasional question, and it no longer costs
horizontal room in every row. *(Refined in round 4 — see "Shared is a mapping fact" below:
the panel now also says when none of those co-owners is actually cached.)*

**Uncached games show no depot list (round 3).** v1 learns a game's depots by watching
what a download writes, so before the first run there is nothing to list and no size to
quote. Inventing either would be the app lying about server knowledge. The detail sheet
shows an explicit empty state — "Depots unknown until the first download" — with the
reason and a note that an optional manifest oracle on the server (Phase 3 setting) could
supply it earlier. The delete action doesn't appear for these games at all, so the
delete dialog never has to handle the unknown case. A *deleted* game is deliberately
different: its mapping rows survive, so its depot list is still shown, with sizes at "—".

**"Shared" is a mapping fact; "kept" is a cache-state fact (round 4).** Until now a depot
tagged `shared` was always described as *kept* on delete — even when every other game
mapping it was uncached. That is wrong in the way that matters: the bytes would sit on the
server with nobody able to use them, and the freed-space number the user was shown would be
too small. The tag stays (the mapping row really is shared), but the app now distinguishes
two cases everywhere it matters:

- the co-owner panel adds a muted **"No cached co-owner"** note and flips its closing line
  from "only removed once no mapped game needs it" to "deleting this game frees the depot
  too";
- the delete dialog counts those depots as **freed**, not kept, so the headline number is
  exclusive depots **plus** orphaned shared depots;
- deleting really does clear those bytes in the prototype's model, including in the
  *other* games' mapping rows, so an uncached co-owner stops reporting size it no longer has.

> **Backend implication (already filed as a task):** the real deletion path must consult the
> **cache state** of a depot's co-owners, not just the existence of mapping rows. "Some other
> app maps this depot" is not a reason to keep bytes; "some other app that is *cached* maps
> this depot" is. Whether that check lives in `DELETE /v1/cache/{appid}` or in a separate
> orphan sweep is a server decision — but the app is now showing the user a number that
> assumes it happens.

**The delete dialog is a bullet list, not a sentence (round 4).** With three shared depots
the prose version ("depots A, B, C are also mapped to X, Y, Z and will be kept…") was
unparseable, and it flattened per-depot reasons into one clause. The dialog now reads:
one lead line (*Deleting X frees about N GB of the M GB it occupies*), one bullet per
**shared** depot (id · KEPT/FREED · size · the reason, naming the co-owners), then a total
line (*N GB freed · M GB stays on disk*). Exclusive depots are not listed — they are always
freed and the totals already cover them, so listing them would add rows that carry no
decision. The one- and zero-shared-depot cases stay a single short line. KEPT/FREED is
encoded twice, as a word and as the row's left border (grey / red), same double-encoding
rule as the status badges.

**Library layout is configurable (round 4).** Three layouts off one render path and one
`STATE.layout` value: **2 columns** (default, artwork does the recognising), **3 columns**
(~50 % more titles per screen — the case for a 400-title library), and a **list** (small
capsule, title, size, status per row; the only layout that never truncates a title).
Switchable from a segmented control in the Library header *and* from Settings → Library
layout; both controls edit the same value and stay in sync. Search, the filter chips,
multi-select and the "out of view" counter are layout-agnostic because they operate on app
ids, not on grid cells. Two details that mattered: in 3 columns the status **word** moves to
its own line rather than being clipped (dropping it would break the dot-and-word double
encoding), and in the list the row is the natural DOM order already — capsule, checkbox,
title, meta — so the checkbox becomes a leading control instead of an overlay.

*The real app stores this locally* (DataStore / SharedPreferences), not on the server: it
is a per-device preference, and the same vault viewed from a phone and a tablet should be
allowed to disagree about it. No new API surface.

**Pause and resume (round 4).** The Downloads screen can now hold a running job: **Pause**
on the active card, a *paused* state with a muted frozen bar and no ETA, **Resume** as the
primary action, and a short **"verifying cached chunks…"** phase before bytes move again.
The library card and the detail sheet follow along — a paused job shows a static yellow dot
and the word *Paused*, not the blinking *Running* it used to claim.

> **What pause actually means on the server.** There is no protocol-level way to suspend a
> Steam download. Pause = vault-api **terminates the SteamPrefill subprocess**; resume =
> vault-api **starts a fresh run for the same app**. Nothing is lost because **the cache
> itself is the progress store** — chunks already written are found again on the next run,
> which is exactly what the verify phase is doing and why it is worth showing rather than
> hiding behind a spinner. Consequences the UI states out loud: a paused job **keeps the
> single worker slot**, so the queue waits until it resumes or is cancelled, and the bytes
> already fetched **stay on the cache** while paused.

Endpoints the app now assumes: `POST /v1/jobs/{id}/pause`, `POST /v1/jobs/{id}/resume`,
`DELETE /v1/jobs/{id}` (cancel/remove). **None of these exist in the API yet** — job control
is a decided plan feature, not a shipped one.

**Information design over decoration.** The library opens with what an operator
actually needs first: server online, free space, then any warning, then search, then
the grid. Sizes are floors, not guesses — the delete dialog states occupied bytes and
the smaller number that will actually be freed once shared depots are kept.

**Copy.** Every action says what it does and the confirmation says what happened
("Freed 42.8 GB · 1 shared depot kept"). Errors in the job history quote the kind of
line SteamPrefill and vault-api really emit, including
`[vault-api] Mapping untouched — a failed run is not evidence about depot ownership.`

**Endpoint mapping.** Every action maps 1:1 to a real endpoint. Turn on
Settings → Show API mapping to see them in place: `GET /v1/games`,
`GET /v1/games/{appid}`, `POST /v1/prefill`, `GET /v1/jobs`, `GET /v1/jobs/{id}`,
`DELETE /v1/cache/{appid}`, `GET /v1/cache/summary`, `GET /v1/clients`,
`GET /v1/health`, plus the job-control trio that does **not** exist yet —
`POST /v1/jobs/{id}/pause`, `POST /v1/jobs/{id}/resume`, `DELETE /v1/jobs/{id}`.
The queue mirrors the server's real semantics: one job at a time, FIFO, dedupe instead of
stacking a second job for the same app — and a paused job still occupies that one slot.

**ADR-0004 is visible, not just respected.** Sign-in opens a mocked browser tab that
is deliberately *not* a Valve look-alike: a dashed placeholder that says the real page
loads there, states that SteamHangar only ever receives the SteamID64, and is labelled
MOCKUP. There is no password field anywhere in the prototype — a realistic fake login
screen would be an impersonation risk, and the honest placeholder communicates the
trust story better anyway.

## Behavior rule for the real app: navigation dismisses transient surfaces

A bottom-nav tap used to leave the detail sheet painted over the new screen. The fix
is a rule, not a patch: **any event that changes what the user is looking at first
dismisses every transient surface** — detail sheet, clients sheet, notifications sheet
(round 5), confirm dialog and scrim. In the mockup a single `closeAll()` is called from:

- `go()` — every bottom-nav switch
- `doRefresh()` — pull-to-refresh and the refresh icon (it reloads what the sheet showed)
- `enterSelect()` — entering multi-select is a mode change
- `onbOpen()` — replaying the first-launch flow from Settings
- `Escape` — the desktop stand-in for Android's back gesture: dismiss the top surface,
  then leave multi-select, and only then do nothing

In Compose this belongs in the navigation layer, not in each screen: transient surfaces
(`ModalBottomSheet`, dialogs) should be state owned *above* the destination, cleared on
every destination change, with the system back handler consuming them in the same
order. Worth an explicit test case per surface — this class of bug reappears every time
a new sheet is added.

## Deliberate deviations from Steam's trade dress

- No Valve/Steam logos, wordmarks, icons or button styles. The wordmark is plain type;
  the app mark is a generic vault shield; the sign-in button uses a neutral
  external-link glyph.
- Accent is aqua `#2ED9CE`, not Steam's sky blue `#66c0f4`; ground is a colder, darker
  blue-grey than Steam's `#1b2838`.
- Portrait capsules follow the 2:3 ratio because that is the shape of the artwork the
  app will display — the frame, badge placement, typography and chrome are ours.
- The login explainer copies SteamDB's *information architecture* only; the visual
  design is ours.
- The mocked sign-in interstitial is an explicit placeholder, never a facsimile.
- Footer on every viewport: "MOCKUP — not affiliated with Valve".
- Fake titles throughout (Nebula Drift, Tundra Protocol, …) — no real game names.

## Open questions

1. ~~**Cancel has no endpoint.**~~ *(answered by the job-control decision.)* The app now
   shows Pause, Resume, Cancel and Remove, mapped to `POST /v1/jobs/{id}/pause`,
   `POST /v1/jobs/{id}/resume` and `DELETE /v1/jobs/{id}`. What is left is not a design
   question but a build order one: **all three endpoints are still unimplemented**, and
   the pause semantics (terminate the subprocess, keep the partial cache, hold the worker
   slot) need to be the server's behaviour, not just the mockup's story.
2. **Manifest oracle: build it or not?** *(narrowed in round 3.)* The UI side is settled
   — a never-downloaded game now says "unknown" for both its size and its depot list
   instead of faking either. What is left is a server question: should vault-api gain an
   optional manifest lookup (PICS, Phase 3 setting) that fills in depot list and download
   size *before* the first run, so the user can see what a download will cost? Without it
   "how much space will this need?" is unanswerable until after the fact.
3. **The orange "stale" badge depends on Phase 3.** Manifest comparison isn't built yet.
   Does Phase 4 ship with three badges and gain orange later, or does the app wait?
4. **The delete dialog previews the freed bytes client-side — and now needs more data to do
   it.** *(sharpened in round 4.)* The split is no longer "exclusive vs. shared": it is
   "exclusive + shared-with-no-cached-co-owner vs. shared-with-a-cached-co-owner", which
   means the phone has to know the **cache state of every co-owning app** to draw the
   dialog. It can do that today only because the mockup holds the whole library in memory.
   Proposal: `GET /v1/games/{appid}` returns the per-depot breakdown the dialog needs —
   depot id, size, co-owner app ids **and whether any of them is currently cached** — so
   the number the user sees is the server's own arithmetic and matches what the delete
   will actually do.
5. **Is demo mode in scope for v1?** The prototype lets you browse without a server or a
   Steam account. Real value for first-run and screenshots — or should the app refuse to
   open the library until `/v1/health` answers?

### Answered by feedback rounds 2 and 3

- ~~Search~~ — persistent field, ANDed with the chips, selections survive it.
- ~~Prefill vs. download wording~~ — UI says download, API keeps prefill.
- ~~How much to explain about Steam sign-in~~ — full explainer before the button.
- ~~Overlay left painted over a new screen~~ — answered as a general rule above.
- ~~Stale "shared with X" labels~~ — ownership is derived from mapping rows now, so it
  cannot go stale; the co-owner list moved into a per-depot expander.
- ~~What to show for a game with no depot knowledge~~ — an honest empty state, not a
  fabricated list. Half of open question 2; the server-side half is restated above.

### Answered by feedback round 4

- ~~"shared" implied "kept" even with no cached co-owner~~ — the tag stays, the consequence
  is now computed from co-owner cache state, in the panel and in the freed-bytes total.
- ~~The delete dialog was unreadable prose at 3+ shared depots~~ — bullet list, one row per
  shared depot, totals underneath.
- ~~One fixed grid density~~ — 2 columns / 3 columns / list, switchable from the header and
  from Settings, stored per device.
- ~~A download could only be cancelled, never paused~~ — pause/resume with an explicit
  verify phase, and the queue consequence stated on screen.

### Two robustness fixes found while verifying round 4

- **The file declared no character encoding.** With no `<meta charset>` and no BOM, a
  browser falls back to its locale default, and every `·`, `—` and `…` in the prototype
  renders as mojibake on a non-UTF-8 default (reproduced in a headless render). Added
  `<meta charset="utf-8">`.
- **The "real phone = fullscreen" behaviour never actually fired.** The `max-width:480px`
  rules that drop the phone frame need a `<meta name="viewport">` to be evaluated against
  the device width; without one a mobile browser lays out at ~980 px, so a phone got the
  shrunken desk-and-frame view instead. Added
  `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`,
  which is what the "How to drive it" section above claimed all along.

### New backlog item this round produces (server side)

**Vault name is not in the API yet.** The app currently only knows a name the user
typed on the phone, which means two devices can disagree about what the same server is
called. Proposal for the Phase 4 backlog: a `VAULT_NAME` environment variable on
vault-api (defaulting to the container hostname), surfaced as a field on
`GET /v1/health` — the endpoint the app already calls before anything else, and a place
where a self-chosen label is harmless in an unauthenticated liveness answer — with a
per-device override in Settings for people who prefer their own label. **Not in the
current API; do not assume it exists.**

## Design decisions — round 5

**Status icons replace status dots (findings 3c + 8, per the agreed addendum).** The
compact status channel is now a filled circle in the status hue carrying a pixel-centred
inline-SVG glyph: **check** = current, **download arrow** = not cached, **circular
arrows** = update ready, **pause bars** = paused, **`!`** = failed/warning. Shape is the
primary, colour-blind-safe channel — it survives when the hue is unreadable (CVD) *and*
when the element is tiny (3-column grid), which neither colour nor a shrunk-to-illegible
word does. Rationale over the old dot+word rule: a bare coloured dot fails CVD outright,
and a word is the first thing to get clipped in a dense grid. The glyph is drawn on a
24-unit grid with stroke widths that survive down to ~10 px and is centred with CSS
`grid`/`place-items:center`, so it renders crisp on the whole-pixel circle (verified: 17 px
circle in 2-col, 16 px in 3-col, svg centre offset 0.0 px in every layout).

*Where words stay.* Every roomier surface keeps **icon + word**: the list row, the
detail-sheet badge, the legend (icon + word + explanation), the co-owner rows and the client
list. The item-8 audit therefore became a *bare-icon* audit, and the two remaining wordless
dots (the client rows) gained both an icon and a word ("Bypassing" / "Healthy").
*(Superseded in round 6: the grids no longer lean on the glyph alone — moving icon and size
onto the capsule pill freed the meta row, so **all three layouts now show the status word**.
The word was always in the DOM for screen readers; now it is visible everywhere too.)*

**Motion = "activity right now", never decoration (round-5 addendum).** *(Timings and both
glyphs were reworked in round 6 — see "Motion, calmed down" below; the principle here is
unchanged.)* Only the two active states animate: a **running** download's arrow drifts down
and fades; an **update / re-verify** run's arrows turn. Every static state
(check, idle download arrow, idle update arrows, pause bars, `!`) is completely still, so a
library at rest never flickers. Paused is *deliberately* motionless — a frozen pause glyph
is the whole signal. All of it is GPU-cheap transforms only (`translateY`/`rotate`) and is
switched off by the existing `prefers-reduced-motion` block. **Design principle for the
real app:** reserve motion for in-flight state; a moving icon must always mean the server
is doing something for that app *this second*, and the "updating vs. fresh download"
distinction (rotate vs. slide) is worth carrying because it answers "is this a new game or
a patch?" at a glance. In the mockup this rides on a per-job `upd` flag set at enqueue
time (`g.st` was stale/cached → update); the real app gets the same signal free from
whether the app already had cached content when the job started.
*(Real app, WP WEB-FEAT-6: the signal is the games row of the app with the running job —
`last_prefill_at` set means a previous run completed, so the run is an update; with
`needs_force` also set it is a forced re-run, shown as "Verifying". Both fields hold still
for the whole run, unlike the cached byte count. The short "verifying cached chunks" phase
after a resume is not shown: no API field marks its end.)*

**Bulk actions never silently re-download a cached game (finding 2).** Multi-select now
classifies the picked set by real cache state and targets only what needs bytes: fresh
(not-cached) first — the button spells out *Download 3 of 5* with a note *2 already cached
— not re-downloaded*; if nothing is fresh but something is stale, the primary becomes
*Update N*; if everything is current, the primary disables ("All cached — nothing to
download") and an explicit **Re-download N** secondary is offered. The count and the skip
are mirrored in the toast. **Backend implication:** this is a *client-side filter applied
before* `POST /v1/prefill` — the phone decides which app ids to send, so the server never
receives a redundant re-download it has to dedupe or a wasteful refetch the user didn't ask
for. The endpoint and its one-job-FIFO/dedupe semantics are unchanged.

**Notifications are a poll, not a push (finding 7).** A bell in the Library header opens a
panel of what changed — finished downloads (*Cached*), *Update ready* items, cache-bypass
*Warnings*, and job *Failures* — with an unread badge that clears when the panel is opened.
Every row is icon + word (never colour alone). **Backend implication:** v1 has no push
channel; the real app derives these by **polling `GET /v1/jobs`, `GET /v1/games` and
`GET /v1/clients`** and diffing against the previous poll (a finished job → "cached", a
game that turned stale → "update available", a client that reports games but never hits the
cache → bypass warning). No new endpoint, no websocket, no server-held notification store —
the notification list is a client-side view over three existing reads. If a server-side
"seen" cursor is ever wanted (to sync read-state across devices) that is a *later* decision,
explicitly out of scope for v1.

**"Stale" (and "cached") require cache content — a status rule, not a cosmetic (finding
6).** A game can only be *Update ready* if it actually has bytes on the cache to update;
emptying its cache makes it *Not cached*. The mockup enforces this invariant on every
repaint (`enforceStatusInvariant`), so the filter counts and the **Updating** chip always
match what is really on disk after a deletion (verified: deleting a stale game drops the
Updating count and the game flips to Not cached). **Backend implication:** this is a real
**vault-api status rule** — `GET /v1/games` must never report `st=stale` (or `cached`) for
an app whose cached depot content is zero. Whatever computes the manifest-comparison
"stale" flag (Phase 3) has to gate it on "content exists" first; a stale flag on an empty
app is a server bug, not a display glitch.

**"Shared · sole holder": cache-state truth leads over mapping truth (finding 5).** When
the game you are looking at is the *only cached* game mapping a shared depot, the row now
softens the tag to **shared · sole holder** (grey, not orange) and the co-owner panel
*leads* with the consequence — "You are the only cached game using this depot — deleting
frees it." The mapping fact is still true and still shown (the uncached co-owners remain
listed, *mapping kept*), but the thing the user needs first is that deleting this game
reclaims the bytes. This reuses the existing `holders()` derivation (co-owners filtered to
those actually cached); no new data. It composes with the round-4 rule that the delete
dialog counts such depots as *freed*, not kept.

**Pause from the detail sheet (finding 4).** The detail sheet's running-job action is now
a **Pause** button (same `POST /v1/jobs/{id}/pause` mapping), symmetric with the Resume it
already offered when paused — you no longer have to go to the Downloads screen to pause.

**Onboarding gains Back (finding 1).** Steps 2 and 3 show a Back control that returns to
the previous step; step 1 has none. Entered values survive back/forward because they live
in the DOM inputs (URL, key, vault name) and the onboarding object (profile, sign-in,
test-passed), neither of which a step change touches — verified by filling everything,
walking forward to step 3 and back to step 1 with all values intact. The progress track
reflects the current step in both directions.

## Design decisions — round 7

**The live animation was being restarted ~2.4× a second (item 1).** The user noticed the
status icon animated *better* in the detail-sheet legend than on a card that was actually
downloading. That was real, and the cause was architectural, not cosmetic: a progress tick
called `renderGrid()` / `renderDownloads()` / `renderDetail()`, each of which reassigns
`innerHTML`. That destroys and recreates the animated `<svg>` nodes every **420 ms**, and a
CSS animation on a brand-new node starts at 0 % — so a live download never got past the
first ~16 % of its 2.6 s loop and juddered in place, while the legend (rendered once)
played the whole loop as designed.

*Measured before the fix:* the animated `<svg>` was a different node on **every** tick
(`NODE REPLACED EVERY TICK`). *After:* the same `<svg>`, the same `.dla` group **and the
same `Animation` object** across 8 consecutive ticks, while the percentage advanced
20 → 38 %.

The rule now is explicit, and it is the same rule the real Compose app will need:

> **Rebuild DOM only when a card's STATE changes** (status transition, filter, layout,
> selection). While a job is merely progressing, **patch the volatile values in place** —
> the % text, the bar widths, speed/ETA — and touch no animated node.

`repaintAll()` (full rebuild) is now called only at genuine transitions — job start, pause,
resume, verify→running, finish, cancel — and `tickProgress()` patches the three surfaces
that can show a live job (`patchGridProgress`, `patchDownloadsProgress`,
`patchDetailProgress`). Each patcher first checks a **structural signature** (`data-dk` on
the card, `data-jid` + state on the job card) and falls back to a full render if it no
longer matches, so the patch path can never paint a stale shape. Verified that transitions
still rebuild correctly and that the pill flips actionable↔inert across
`none → running → paused → verify → running → cached`, with the click handler still firing
on the rebuilt node afterwards.

> **Note for the real app.** This is not a mockup-only concern. Any implementation that
> re-emits a list item on every progress update — `notifyDataSetChanged`, a naive
> `LazyColumn` key change, recreating a composable subtree — restarts its animations the
> same way. Progress belongs in state that updates the *leaf* (the text, the bar), not in
> a key that reconstructs the row.

**A status icon must never be blank (found while rendering).** With the baseline removed
from the animated download glyph (round 6 item 4), the arrow's fade to `opacity: 0` left the
pill as an **empty coloured disc** for part of every cycle — caught in a real screenshot.
That is unacceptable for a glyph that is simultaneously the status indicator and the button.
The fade now bottoms out at **.35** and the travel is small enough (~3.4 user units ≈ 1.5 px
at icon size) that the loop's reset is imperceptible even though the arrow is still faintly
visible when it happens. Verified across five sampled animation phases: legible at every one.

**Toast sits where the bulk bar sits (item 2).** It was floating 78 px above the bottom
edge; it is now pinned **12 px above the nav**, the same inset as the round-6 bulk bar.
Because both would occupy that space, `positionToast()` stacks the toast **above** the bar
whenever the bar is up (`bar height + 20 px`, i.e. the bar's 12 px inset + its height + an
8 px gap), and drops it back to 12 px when the bar goes away. It is recomputed on every
toast *and* at the end of `syncBulk()`, so entering or leaving multi-select while a toast is
on screen re-stacks it rather than letting it collide. Verified arithmetically (8 px
clearance, collision-free) and confirmed in a render.

**The Library topbar had started wrapping (found while rendering).** Adding the
notifications bell in round 5 pushed the header to five controls, and the refresh icon
silently dropped onto a second row. The control cluster is now `flex-wrap: nowrap` and
`flex: none`; the title block takes the pressure instead, with the secondary stat line
truncating (`412 owned · 12 on the c…`) so the header stays one row tall. Icon buttons went
32 px and the layout segments 28 px to buy the room back.

> **Verification note.** The Browser pane used for scripted checks does not composite
> (viewport reports 0×0, `document.timeline` is frozen, and `getComputedStyle` serves stale
> values), so *geometry and animation clocks cannot be measured there* — an early "the toast
> overlaps the bar" reading was an artefact of exactly that. DOM/logic assertions (node
> identity, text, classes, attributes) are reliable in the pane; everything visual was
> verified by rendering the file in real headless Chrome instead.

### Answered by feedback round 7

- ~~The live download/update animation looked hectic compared to the legend~~ — progress
  ticks patch in place; animated nodes are never recreated.
- ~~The toast floated too high~~ — pinned 12 px above the nav, and stacked above the bulk bar
  when both are visible.

## Design decisions — round 6

**The capsule pill: one object instead of a colour dot plus a duplicate line (item 2).**
The old cover carried a bare coloured tick (colour-only, the very thing round 5 set out to
remove) while the size sat on a second line below the card. Both facts now ride in a single
pill on the artwork — **status icon + the number** — over a dark translucent ground with a
hairline, so it stays readable on any cover. The number is context-sensitive: cached size,
live percentage while a job runs, and nothing at all when there is no size to quote (a
never-downloaded game shows the icon alone rather than a fake "—"). The meta row under the
card was reworked so nothing is said twice: in **2 and 3 columns** it now carries the status
**word** (which the grids previously had to drop for space), and in the **list** the pill is
hidden entirely and the row keeps its icon + word + size — the list row was already the
honest, roomy version and did not need a pill on a 30 px thumbnail. Net effect: the grids
gained the word back *and* lost the duplication.

**The status icon is the button (item 7).** Tapping the pill starts the obvious action
without opening the detail sheet: **not cached → download**, **stale → update**,
**running → pause**, **paused → resume**. Two states are deliberately inert and render as a
plain span rather than a button, so nothing looks tappable that isn't: **current** (silently
re-downloading a current game is exactly what round 5 removed from the bulk bar — the rule
has to hold here too, re-download stays an explicit choice in the detail sheet) and
**verifying** (a transient handshake with nothing sane to offer). `running → pause` was
chosen for symmetry with the detail sheet's Pause/Resume pair. The pill stops click,
mousedown and touchstart from reaching the card, so the card still opens the detail sheet
everywhere else and the long-press gesture is unaffected; in multi-select the pill goes
inert so a tap toggles selection instead. Feedback is immediate because the action
re-renders the grid and the icon flips to the active state on the spot. The list row's icon
gets the same behaviour through a padded ~30 px hit target around a 17 px glyph.

**"Downloading" and "Updating" are different things (item 5).** A first-time download was
being reported as *Updating* — in the animation and in the filter chip. Two causes, both
fixed:
- the chip `Updating` was defined as `running || stale`, so any in-flight job landed under
  it. It is now split into **Downloading** (a job is in flight — fresh or refresh) and
  **Update ready** (cached content exists, a newer manifest is upstream, nothing running).
  The two are mutually exclusive and neither can absorb the other. Splitting beat renaming
  to a vaguer "Active": two precise words cost one extra chip in a horizontally scrolling
  row and answer two genuinely different questions.
- the fresh-vs-refresh decision is now taken **once, at enqueue time**, from whether the app
  actually had cache content then, and stored on the job (`upd`). It must never be
  re-derived later, because `pump()` overwrites the game's status with `running` for the
  duration of the job and thereby destroys the evidence — that was the underlying bug.
`LABEL.running` is now "Downloading", so card, badge, legend, Downloads subtitle and chip
all use one vocabulary.

**Motion, calmed down (items 3 + 4).** The update glyph is now **two opposing curved arrows
forming one circle**, turning at a constant rate in complete 360° revolutions — `linear`,
2.2 s, no easing wobble and no part-swing. Because the glyph has 180° rotational symmetry
every half turn lands back on itself, so the loop is seamless; `transform-box:fill-box`
puts the origin on the glyph's own centre (verified: exactly 12,12 in the 24-unit grid).
The download arrow was "sehr unruhig" for two reasons, both addressed: the animation was
moving the **whole glyph including its baseline**, and it was fast. Now only the arrow group
drifts, the travel is shorter, the cadence is 2.6 s, and — as asked — the **animated variant
has no line under it at all**; the static "not cached" glyph keeps its baseline, where it
reads as a floor to download onto rather than a jittering artefact.

**Bulk bar: a column that cannot overflow, plus delete (item 6).** The bar was a single
flex row with the buttons pushed right, which overflowed as soon as a third button or an
API chip appeared, and it floated 70 px above the nav. It is now a **column** — a head line
(count + Cancel), the plain-language note, then a wrapping button row — pinned 12 px above
the nav. Buttons flex and wrap instead of overflowing (verified at 390 px *and* 375 px with
API chips on and every button visible: zero overflow in all combinations).
**Multi-delete** joins it, and the shared-depot arithmetic is genuinely set-aware: the plan
is computed for the whole selection at once (`multiPlan`), so co-owners *inside the batch*
no longer count as reasons to keep a depot. Deleting Nebula Drift + Ironwood Hollow keeps
shared depot 228990 because Tundra Protocol still holds it; adding Tundra to the same
selection frees it — where three separate single-game dialogs would each have claimed
"kept". Depots are de-duplicated across the selection because the bytes exist on disk once.
Single-game delete is now literally `multiPlan([id])`, so the two paths cannot drift, and
its dialog copy is unchanged. Mixed selections are split as honestly as the download side:
only games with cache content are deletable, the button says **Delete 3 of 5**, and it
disappears when nothing in the selection is cached.

> **Backend implication (multi-delete).** The app issues **one `DELETE /v1/cache/{appid}`
> per selected app**; there is no batch endpoint and this mockup does not assume one. But it
> sharpens the round-4 note: the server's decision to keep or free a shared depot must be
> made against the **cache state after the whole batch is applied**, not per call. Deleting
> three co-owners one at a time, each checking "does another mapped app still have this
> cached?", would keep the depot on all three calls and strand the bytes. Either the server
> sweeps orphaned depots after a delete, or a batch delete endpoint is needed. **This is the
> single most important server-side consequence of round 6.**

**Notifications lead somewhere (item 1).** A notification that cannot be acted on is a dead
end, so every row is now a button that navigates: a game item opens that game's detail
sheet, the bypass warning opens the clients sheet, a job error opens **Downloads with that
job's history row already expanded**. The panel closes on the way, obeying the existing
"navigation dismisses transient surfaces" rule. Each row's `aria-label` states its
destination ("Opens this game", "Opens the client list", "Opens this job in Downloads").
*Implementation note worth keeping:* the jump to a history row scrolls the **list**, never
`scrollIntoView()` — that also scrolls the `overflow:hidden` app shell, which silently
shifts the whole screen and every absolutely positioned surface with it (caught while
verifying: it moved the bulk bar 66 px). No new API surface; the targets are all data the
polled endpoints already carry.

**A clean gear (item 8).** The bottom-nav settings glyph was a single hand-built path that
rendered visibly lopsided. Replaced with a composed, symmetric gear: one body circle, one
hub, and eight teeth at exact 45° intervals of identical length (verified: bounding box is
square and centred on the icon's centre).

### Answered by feedback round 6

- ~~Notifications were a dead end~~ — every row navigates to the game, the client list or
  the job it is about.
- ~~A colour-only tick on the cover, and the size repeated below~~ — one pill carrying icon
  + size; the grids got the status word back.
- ~~The update spin wobbled~~ — a symmetric two-arrow circle turning in full linear revolutions.
- ~~The download arrow was restless and had a line under it~~ — only the arrow drifts, slower
  and shorter, and the animated variant drops the baseline.
- ~~Fresh downloads were bucketed as "Updating"~~ — the split is decided at enqueue time and
  the chip is split into Downloading / Update ready.
- ~~The bulk bar overflowed and floated~~ — a wrapping column pinned above the nav, plus
  multi-delete with batch-aware shared-depot arithmetic.
- ~~Downloads could only be started from the detail sheet~~ — the status icon is the button.
- ~~The gear looked deformed~~ — redrawn symmetrically.

### Answered by feedback round 5

- ~~Bare status dots fail colour-blind readers~~ — a shape-first **status-icon** system;
  colour is now the third cue, words ride along in every roomy layout.
- ~~"Download" bulk-action silently re-downloaded cached games~~ — the primary targets only
  what needs bytes, spells out the skip, and offers re-download only as an explicit secondary.
- ~~No way to know what the server noticed since you last looked~~ — a polled notifications
  panel with an unread badge.
- ~~A game could read "Updating" with nothing on the cache~~ — a status invariant, stated as
  a vault-api rule.
- ~~"Shared" read as "kept" even when this was the last cached holder~~ — sole-holder framing
  leads with the cache-state consequence.
- ~~Pause was only reachable from the Downloads screen~~ — it is on the detail sheet too.
- ~~Onboarding had no way back without losing entered values~~ — Back on steps 2 & 3, values
  preserved.
- ~~Icons at rest could be mistaken for "busy"~~ — motion is reserved for active jobs only,
  and disabled under reduced-motion.
