# SteamHangar — Working Agreement

Canonical source for scope, architecture, phases, and definition of done:
`docs/PROJECT_PLAN.md`. Read it before doing anything else.

## Roles

The main session (orchestrator) plans, decomposes, and delegates. It writes
no production code and performs no reviews itself. Two subagents exist in
`.claude/agents/` and must be used consistently:

- **coder**: implements one clearly scoped work package at a time
- **reviewer**: reviews every completed work package before it counts
  as done; read-only

Agent definitions use `model: inherit` so delegation always rides the
session's current model instead of one pinned by name; where the working
agreement asks for a stronger second pass (the `[Fable]` marker in
`docs/WORKPACKAGES.md`), the orchestrator picks the strongest available
model at call time — never by name in a file or brief.

## Shared learnings (mandatory)

`docs/LEARNINGS.md` is the living list of project-proven findings. Every
coder and reviewer reads it before their first task of a session; work
package briefs reference it instead of repeating its content. The
orchestrator appends newly distilled findings after each review cycle.

## Work mode (applies to the whole project)

- Work strictly phase by phase per `docs/PROJECT_PLAN.md`. Phase 0
  (feasibility PoC) comes first — NO production code before Phase 0 has
  answered the proxy_store question (Plan B viable vs. fallback Plan A).
- Split each phase into work packages of at most ~1-2 hours of effort.
  Show the package list of a phase to the user BEFORE delegating.
- Every package goes through: coder implements → reviewer reviews →
  on FAIL back to coder with the findings → only on PASS does it count
  as done and gets committed.
- One commit per passed work package, Conventional Commits format.
- Gate principle: after each completed package, a short status report to
  the user (done / open / blockers). For architecture decisions that would
  deviate from the plan: stop and ask the user, with clearly named options
  (Plan A / Plan B) — never decide alone.
- Keep `docs/PROJECT_PLAN.md` up to date: tick completed checkboxes,
  record decisions as short ADR notes in `docs/adr/`.
- Language: communication with the user in German; code, comments, commits,
  and everything in the repo in English.
