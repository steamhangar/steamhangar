# Security Policy

## Supported versions

SteamHangar has no tagged releases yet (see `docs/PROJECT_PLAN.md` §7 Phase 5 —
publishing to a registry with version tags is still gated on the user's
go-ahead, `docs/PROJECT_PLAN.md` §11, WP 5.5). Until the first tagged release,
security reports are accepted against the current state of the `main`
branch, and fixes land there. Once tagged releases exist, only the most
recent tag will receive security fixes — this is a single-maintainer
project (see
`docs/PROJECT_PLAN.md` §9 "Risks & Open Questions") and cannot commit to
backporting.

## Reporting a vulnerability

Please report security vulnerabilities privately through **GitHub's private
vulnerability reporting** for this repository: open the repository's
**Security** tab and select **"Report a vulnerability"** to start a private
advisory. This opens a private conversation with the maintainer that is not
visible to the public until a fix is ready.

This channel depends on GitHub's private vulnerability reporting being
**enabled** for this repository (a repository setting, not a GitHub-wide
guarantee). If the **Security** tab has no **"Report a vulnerability"**
option when you look, please open a minimal public GitHub issue that asks
only for a private contact channel — **no vulnerability details and no
email address** in that issue — and the maintainer will follow up privately
from there.

Do not open a public GitHub issue for a security report — see
`docs/security/threat-model.md` for the design assumptions and known,
by-design limitations that are *not* new findings (in particular:
`vault-core` has no authentication and is not meant to have any; that is a
documented trust-boundary decision, not a bug to report).

**Where your Steam credentials go.** ADR-0004
(`docs/adr/0004-steam-credentials-never-touch-steamvault.md`) promised that
the community release would document, in this file, both credential flows
and every path data takes out of your LAN. That material lives in the
threat model instead, where it can be checked against the code in one
place: `docs/security/threat-model.md` §3 ("Credentials — ADR-0004's claim,
checked against the code") for where Steam credentials and the relay's Web
API key do and do not live, and §5 ("Outbound data flows — what leaves the
LAN") for every outbound connection the stack makes. Read those two
sections before reporting a credential-handling finding — they also name
the claims the document could not substantiate from code alone. One
correction to ADR-0004 itself: SteamPrefill has no QR login, so the Steam
password IS typed once on the server, into SteamPrefill's own prompt, with
Steam Guard confirmed by app approval or code (ADR-0004 addendum 4,
2026-10-03). It never passes through SteamHangar code.

Please include:

- A description of the issue and its potential impact.
- Steps to reproduce, or a minimal proof of concept.
- Which component is affected (`vault-core`, `vault-api`, `vault-agent`,
  the web UI, or the Android app).

## What to expect

This is a community project with a single maintainer and no dedicated
security team, so please read this as an honest best effort rather than an
SLA:

- Acknowledgement of a new report: best effort, typically within a week.
- After that, expect updates as the investigation progresses rather than on
  a fixed schedule. Severity and available time both affect how quickly a
  fix can land.
- Credit in the eventual advisory if you would like it, or anonymity if you
  would prefer that instead — say which when you report.
- Coordinated disclosure: please give the maintainer a chance to ship a fix
  (or to document a by-design limitation, if that is what the report turns
  out to be) before any public disclosure.

## Scope

This policy covers the code in this repository: `core/` (vault-core),
`api/` (vault-api), `agent/` (vault-agent), `app/` (the Android app), `web/`
(the bundled web UI), and `deploy/` (the shipping Compose configuration).
It does not cover Valve's own Steam infrastructure, or third-party software
this project shells out to (SteamPrefill) or depends on — please report
those upstream.
