# Learnings: Process and docs

Scope: orchestration, delegation, review loop, agent limits, branches/merges, Antraege, docs and community release. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## Docs / community release
- Entry-point docs describe SHIPPED behavior, not ADR designs: an ADR
  records a decision that may be unimplemented — grep the code for the
  mechanism (and check PLAN checkboxes) before claiming it works
  (WP 5.2 blocker: staleness FAQ described the unshipped manifest oracle
  as the live mechanism).
- Quote container-real paths in docs: the cache lives at
  `/vault/cache/depot/...` (nginx `-p /vault`, `VAULT_CACHE_ROOT`), not
  `/cache/...` (WP 5.2 blocker).
- Never present a planned license as granted — a `## License: Apache-2.0`
  heading without a LICENSE file reads as an effective grant that does not
  exist; word it "planned" until the file lands (WP 5.2 blocker).

## Process (2026-08-18 decision audit, Fable)
- A control that protects a person other than the operator (privacy
  switches, data-collection gates) gets a written failure-mode analysis
  BEFORE any coder brief: for each store the setting can live in (env,
  db volume, image default), state what happens when that store is lost,
  and which direction the failure points. The 4h.0 ceiling design would
  have shipped a switch whose volume-loss failure mode was "collection
  silently resumes"; it was caught by the operator's question, not by
  the pipeline — coder and reviewer both had ADR-0009 in front of them
  and neither flagged it. Named options go to the operator first.
- A package is committable only when the reviewer's round-2 REPORT is in
  hand — orchestrator self-verification of the fixes is a supplement,
  never a substitute for the verdict. WP 5.3 was committed on
  self-verification after a round-1 FAIL; the retroactive round 2 had to
  be commissioned by the audit. "Review complete" means report received,
  not verdict word received (the 4e.6 post-commit report carried the
  finding that became WP 4e.8).
- The reviewer's read-only mandate now includes git explicitly, enforced
  in .claude/agents/reviewer.md after the WP 4e.6 incident (a reviewer's
  `git checkout` destroyed uncommitted CSS in a foreign worktree;
  recovery was byte-exact from the served stylesheets, luck that should
  never be load-bearing). Mutations happen in a scratch copy, never in
  any working tree.
- A mutation report's STATED MECHANISM is a claim of its own and drifts
  independently of the result: WP 4h.4 shipped four in one package (a
  compile failure described as a parse error; a JsonDecodingException
  described as a timeout; a fixture pin described as behaviour-testing
  production; a Gradle-input theory the review measured and refuted).
  All four errors ran in different directions — two overstated
  protection, two over-warned — so the fix is procedural, not
  directional: when documenting a mutation, quote the actual failure
  output, name the failing assertion, and let the reviewer re-run the
  exact form documented. "Pin the mechanism that actually kills the
  mutation" (WP 4a.6) extends to the prose describing it (WP 4h.4
  rounds 1-3, 2026-08-19).
- A viewport-width column-count claim is meaningless without naming the
  ELEMENT and the SCROLLBAR STATE: .view-root border-box, .grid content-box
  and viewport-minus-tokens differ by 47px (32 padding + 15 classic
  scrollbar) — enough to straddle an auto-fill track floor and produce two
  irreconcilable, individually-correct measurements (WP 4h.2, three review
  rounds). State the box, state the page state, and record the decisive
  intermediate (grid content-box width) next to the count so the next
  dispute checks one number.
- A DOM-last child of a display:grid container with named areas has NO
  defined visual position: auto-placement drops it into whatever cell is
  vacant, and which cell is vacant can depend on a SIBLING's runtime state
  (.banner-wrap's [hidden] toggled the failure between above-the-content
  and a 148px rail-column sliver). Text-only CSS pins cannot see it; a
  vertical-only live pin catches one of the two states. New grid children
  get an explicit grid-area in EVERY breakpoint block that defines a
  template (WP 4h.2 blocker, browser-measured both states).
- A Docker per-container egress lock (masquerade-disabled bridge +
  internal net + filtering proxy) does NOT close two channels, both
  measured live in WP EG-1 by actually exfiltrating from the locked
  container: (1) DNS — the embedded resolver forwards from the HOST
  namespace, so a unique label resolves against the public authoritative
  NS regardless of the container's missing route; a 32-char secret fits in
  one label. (2) the Docker HOST's own addresses — replies to a container
  bridge address need no SNAT, so host-bound listeners incl. every
  0.0.0.0-published port stay directly reachable. Only a container on an
  internal:true net ALONE loses DNS too. State both as accepted open gaps;
  a security doc that claims "no packet can leave" is the worse failure.
- The reviewer of a security package must ATTEMPT the violation, not read
  the claim: EG-1's two overstatements passed every static test and were
  caught only by the reviewer standing the stack up and exfiltrating. The
  mechanism was sound in both directions it targets; the FAIL was pure
  claim-honesty, which for a doc strangers use to decide whether to run
  this on their network is exactly the defect that matters most.
- Docker attaches a service's networks in REVERSE of the `networks:` list
  order, so a hardcoded `eth0` inside a multi-network container may be the
  wrong interface (WP EG-1: picked the internal net with no gateway); find
  the gateway-bearing interface via /proc/net/route instead.
- A model name hard-coded in an agent definition (`model: sonnet`/`opus`)
  or in the working agreement pins a TIER by name regardless of the
  session's model, and nothing fails loudly when that tier is weaker or
  outdated — the coder ran one tier below the session model and no file
  showed it. (Family aliases do track the newest model of their family;
  the defect is the fixed tier, not a stale generation.) Fix: `model:
  inherit` in every agent frontmatter so delegation rides the session's
  current model, plus call-time selection ("strongest available")
  wherever the working agreement asks for a stronger second pass — never
  a model name in a file or brief. Basis: user decision 2026-09-29, not a
  failed review round (WP R-0).

## 2026-08-22 — the defaults-flip wave (SWEEP-1, APP-DEMO, 4d-web, AG-0, CI-3/AGENT-BIN)
- A guarantee stated one notch stronger than its mechanism is this
  project's most-repeated defect, and WP APP-DEMO hit it three times in
  ONE package at increasing depth: a seven-literal denylist whose KDoc
  claimed type-level impossibility (defeated through the project's own
  net.steam package), its allowlist replacement claiming "fails closed BY
  CONSTRUCTION" (defeated via the unwatched javax. prefix family), and
  the structural ceiling itself — Kotlin's implicit imports make
  ProcessBuilder/Runtime.getRuntime().exec usable with NO dotted name, so
  no fully-qualified-name scan can ever see them. Rule: document the
  ceiling instead of denying it; an identifier denylist to "close" it
  would be the same defect a fourth time. Corollary proven the same day:
  a test-driven production refactor can move a property from code the
  file owns onto framework structure one level out (the banner into
  ModalBottomSheet's own non-scrolling Column) — the review that
  confirmed it disassembled the pinned material3 AAR rather than reading
  docs, and the resulting guarantee is VERSION-COUPLED and must be
  written down as such.
- A defaults flip must verify its ACTIVATION precondition ships: SWEEP-1
  flipped "keep cached games current" on while no schedule window
  shipped, so the growing half was inert out of the box while the
  deleting half (auto-GC, queued by every prefill) went live — inverting
  the pairing that justified the flip. The fix was the operator's call
  (ship a window), not the packager's.

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- Process: delegating to more than ~3 parallel agents hit the session
  limit repeatedly during this review cycle. Queue packages instead of
  fanning out wider; the throughput gained by a fourth parallel agent was
  lost to restarts (WP pre-freeze review, cross-package).
- Process: review-then-fix with mutation checks caught six tests that
  pinned nothing — the webhook redirect test (API-FIX-2, above), the
  AGENT Windows `%q` assertion (above), three APP wiring pins, and the WEB
  OOM-ing node assertion. Each was green against the code it claimed to
  guard. A test that has never been seen failing for the right reason is
  not yet evidence; mutate its subject once before counting it (WP
  pre-freeze review, cross-package).

## 2026-10-09/10 — rc13 night run (CORE-FIX-4d, WEB-FEAT-6, DEPS-1, HYG-1, WEB-FIX-10, AGENT-FIX-3)
- Parallel coders share one docker host: `deploy/tests/verify-stack.sh`
  runs under a fixed compose project, so two concurrent `verify` runs
  clobber each other. Every agent wraps every dev.sh call in one shared
  `flock` lock file, and nobody edits worktree files while their verify
  runs (a mid-run edit produced a bash syntax error and two false FAILs).
- Steam's cover art for new apps needs a server-side
  `IStoreBrowseService/GetItems` lookup (no CORS); that would send the
  vault's appid list to Valve without a relay key, which the threat model
  rules out. Such privacy questions go to the operator before the brief
  (API-FIX-5 parked).

## 2026-10-13/14 — rc15/rc16 (D9, D10, UPD-5, UPD-6, D15a-c)
- `dev.sh verify` tears down the dev instance as a side effect; run
  `dev.sh up` right after it. Only the orchestrator runs verify — coders
  and reviewers never do, so a review cannot silently stop the instance
  another agent is testing against.
- Edits that widen egress (allowlists, relay hosts) are blocked by the
  auto-mode safety classifier even after the operator agreed in chat. The
  operator makes that one-line edit himself; the brief says so up front
  instead of letting an agent retry.
- A package branched from an older main must merge current main before it
  joins an integration branch (D9 vs D10 conflicted only at integration).
  Merge main into long-lived `wp/` branches before review, not after.
- Overnight, four parallel agents already hit the session limit; keep to
  three and continue an interrupted agent with SendMessage instead of
  spawning a fresh one.

## 2026-10-14 afternoon — rc17 integration (D15c/d, NAMES, RUNNER, AGENT-ADDR-1, CACHE-COMPLETE-1a-c)
- After every merge into an integration branch, run test-api, test-web
  (and test-core when core changed) on the merged tree and
  `git grep -nE '^(<<<<<<<|>>>>>>>)'` before pushing. Skipping this once
  committed conflict markers into api/README.md and left a deterministic
  api failure (golden guard vs a new screenshot class) on the branch.

## 2026-10-31 — rc21 (GRAPH-RECENT-1, LOADING-NOW-1)
- **`pkill -f <pattern>` in the same Bash call can kill that call's own
  shell** (its command line contains the pattern, exit 144); kill by PID.
- **Sample addresses in tests: 10.0.0.x.** The operator's server VLAN and
  his management subnet 192.168.1.0/24 are both ZEUS data; grep before
  committing.

## 2026-11-01 — rc21.1 (prod findings after the rc21 rollout)
- **Prod data questions: use `dev.sh diag db '<select>'`** (A-20261101-02;
  read-only, settings/steam_relay_key/runner_marks blocked) instead of
  asking the operator to paste queries.
- **A clean textual merge can still turn main red**: a parallel package
  added a "prefill runner" literal that RUNNER-NAME-1's source-scan test
  forbids. Run the full suite on the merged tree (or `git merge-tree` +
  tests) before merging packages that touched the same files.
- **Check which release a prod run used before blaming current code**
  (rollout Antrag dates against tags): MAP-LOSS-1's removals came from
  rc20.2, before GROUP-VERIFIED-1.
- **`diag db` takes plain SELECT only (no WITH) and cuts long cells**: read
  a `log_excerpt` with `substr(log_excerpt, i, 180)` and
  `replace(..., char(10), ' | ')`.

## 2026-11-01 — rc22 (DEV-MODS-1a, Claude Code mods, CC 2.1.316)
- **`claude plugin validate` refuses a mod that passes `$` to a closure**:
  `$` may only go to a function declared at the top of the module (or a
  const bound to one, or an import). Helpers defined inside `register`
  fail validation; hoist them and keep option values in module variables
  set by `register`.
- **Reading a `turn.step` stream in a test: iterate by hand.** A
  `for await` over `$.turn.step(...)` drops the generator's return value
  and `stream.result` is then undefined; loop on `stream.next()` and take
  `value` of the `done` step.
- **A mod test that pushes a multi-MiB string through a mocked `fs.write`
  kills the test child** ("the file did not load ... ended without a
  report"), so a size limit cannot be tested at its real value; make it an
  option and test with a small one.
- **In the test kit a plugin's `{ deny }` comes back from `$.tool.call` as
  `{ deny }`** (not `isError`), and relative tool paths reach the `fs.stat`
  hook already resolved against the session directory (the mod folder in
  a test).

### 2026-11-01 NITS-APP-1

- A carried nit that survives only as a label ("D19WiringTest pin") cannot
  be fixed with certainty later: when a nit is carried into the plan, keep
  at least one sentence that says what is wrong and where.
