# Learnings: vault-agent and Windows

Scope: Go agent and CLI, Windows filesystem, PowerShell 5.1, Scheduled Task, systemd packaging. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## Windows filesystem semantics
- `shutil.rmtree` deletes part of the tree BEFORE raising — "every attempt
  failed" never implies "nothing changed" (WP 1.6 blocker).
- Deleted-while-handle-open files report PermissionError (delete pending),
  not FileNotFoundError; racing removals need settle-and-recheck with
  `lexists` as the last word (WP 1.6 Fable blocker).
- Junction detection: `os.path.islink()` is False for junctions,
  `DirEntry.is_dir(follow_symlinks=False)` is True — use an
  islink-OR-isjunction helper; rmtree refuses links (target survives) but
  removal code must unlink links itself, never traverse (WP 1.6).
- `os.path.lexists` swallows EVERY `OSError` into False ("gone") — a
  protective branch keyed on it collapses "unreadable" into "deletable".
  Decide on the error TYPE: only `FileNotFoundError` means gone
  (WP 3.8b).
- Attribute Windows errors per SYSCALL: a delete-pending name answers
  `lstat` with `FileNotFoundError [WinError 2]` but `unlink` with
  `PermissionError [WinError 5]` — WP 1.6's finding is about unlink only;
  measured with a FILE_SHARE_DELETE handle rig (WP 3.8b).
- A path-shape guard measured only on the dev OS is not a guard:
  `ntpath.basename` splits on `\` (and strips `X:` drives), `posixpath`
  does neither — the Windows measurement hid a POSIX hole (`a\b`, `C:x`
  accepted as direct children) for two work packages until CI ran pytest
  on the production OS for the first time. Reject separator-ish
  characters (`\`, `:`) by LITERAL check, never via the host's `os.path`;
  pin each rejection arm with its own mutation-tested case (WP 3.8 →
  caught by WP 5.1 CI run #1).
- **Never round-trip a file through Python text mode on Windows.**
  `read_text()` + `write_text()` rewrites EVERY line ending to CRLF, which
  bites three different ways — all three hit in one session: (a) `dash`
  refuses to parse a shell script with CRLF, so a patched `verify-stack.sh`
  died with `Syntax error: word unexpected`; (b) CRLF silently defeated a
  brand-new `verify-stack.sh` guard whose regex ended in `$` — the guard
  asserts ABSENCE, so it passed for the wrong reason on every run (P1
  review); (c) it corrupts embedded `
` literals elsewhere in the file
  (WP 4e.7, caught only by `git diff --stat` showing an unexpected hunk).
  Read and write **bytes**, or `open(..., newline="")`, and remember that
  `read_text()` also normalises on the way IN — a search string written with
  `
` will not match a CRLF file, which is a separate failure that looks
  like "anchor not found" (WP 4f). After any scripted edit on Windows, check
  `git diff --stat` for hunks you did not intend and `git ls-files --eol`
  for the file's declared ending.

## PowerShell 5.1 (dev/test harnesses)
- `2>&1` on native commands wraps stderr lines in NativeCommandError and
  kills the script under `$ErrorActionPreference=Stop` (WP 1.8/0.6) — and
  so does `2>$null` (measured, WP 3.10): ANY stderr redirection triggers
  it; the only fix around stderr-writing natives like `nginx -t` is a
  locally restored `$ErrorActionPreference = "Continue"`.
- `return $arrayVar` re-flattens a single-element array even when the
  helper wrapped it — the CALL SITE needs `@(...)` too (WP 3.10).
- `"\n"` in double quotes is a literal backslash-n; locale decimal commas
  corrupt reports — force InvariantCulture (WP 0.3).
- `$null` numeric comparisons pass silently (`$x -ge $null` is true) — gate
  assertions on preconditions or they become false positives (WP 1.7).
- `$ErrorActionPreference = "Stop"` makes every later `Write-Error`
  terminating: `exit 2` lines after it are unreachable and the script
  reports exit 1. Set Stop only after input validation (WP 2.6, measured
  on all four usage paths).
- `Set-Acl` on an already-protected ACL fails with SeSecurityPrivilege for
  non-admin accounts on the SECOND call — use `icacls /inheritance:r
  /grant:r` (idempotent, measured 3x); Task Scheduler XML rejects
  `[TimeSpan]::MaxValue` repetition durations; em dashes in BOM-less UTF-8
  break the PS 5.1 parser under the system codepage — packaging scripts
  are pure ASCII (WP 2.6).
- `*>>` on a native command is no safe log redirect either: PS 5.1 still
  wraps stderr in NativeCommandError text and writes UTF-16LE, so a log
  that `Add-Content -Encoding utf8` also appends to ends up mixed (blank
  lines, NULs). Seen on a real install (rc9). `Start-Process -NoNewWindow
  -Wait -PassThru -RedirectStandardOutput/-RedirectStandardError` to temp
  files, then append the bytes, avoids both; take the exit code from the
  process object (WP AGENT-FIX-2).

## systemd / packaging
- Persistent=true only works with OnCalendar= timers — on monotonic
  triggers (OnBootSec/OnUnitActiveSec) it is a silent no-op, so
  "catch up after suspend" claims need OnCalendar (WP 2.5).
- network-online.target does not exist in the systemd USER scope —
  Wants=/After= on it are no-ops there (WP 2.5).
- Secret env files: umask 077 BEFORE creating, not chmod 600 after
  (world-readable window) (WP 2.5).

## Go / CLI
- Never register a secret env value as a flag DEFAULT — Go's
  flag.PrintDefaults() prints non-empty defaults verbatim on -h and every
  parse error, leaking the secret to stderr on the recommended
  operational path. Register empty, apply env fallback after parsing
  (WP 2.2 blocker).
- Python str.isprintable() rejects Cf/Zs/Co/Cn — Go parity needs
  !unicode.IsPrint, not IsControl+Zl/Zp (WP 2.2).
- time.Sleep in retry loops is not ctx-cancellable — select on
  ctx.Done() vs time.After (WP 2.2). CGO_ENABLED=0 explicitly or
  "static binary" claims are false on the native target (WP 2.2).
- With core.autocrlf=true and no `*.go eol=lf` in .gitattributes,
  `gofmt -l .` lists EVERY Go file — a checkout property, not a style
  violation. Judge gofmt only from an LF checkout (measured, WP 5.4).
- On this dev host the Go toolchain lives only in WSL (no go.exe) —
  Go verification claims must state where they ran, and reviewers should
  check WSL before treating a Go claim as unverifiable (WP 5.4).
- Removal reviews audit CONTENT, not just references: a deleted reference
  implementation can be the only home of documentation living code still
  cites (the EAppState bit table existed nowhere else) — port embedded
  knowledge before deleting its host (WP 2.6 blocker).

## 2026-08-22 — the defaults-flip wave (SWEEP-1, APP-DEMO, 4d-web, AG-0, CI-3/AGENT-BIN)
- Windows: $env:COMPUTERNAME is the uppercased NetBIOS name; Go's
  os.Hostname() returns the case-preserving DNS name. With a
  case-sensitive persisted identity key, a preview printing the former
  mints ghost identities (WP AG-0, measured DEMON vs Demon). Preview
  from [System.Net.Dns]::GetHostName(), and pin with -clike/-ceq —
  PowerShell's default comparisons are case-insensitive, so a
  case-defect pin using -like passes either way.
- Go vcs.revision stamping inside a linked worktree NESTED IN another
  repo records the ENCLOSING repo's HEAD (root detection needs a .git
  DIRECTORY, so the worktree's redirect file is skipped and the walk
  lands above); a non-nested worktree yields NO stamp at all. The
  orchestrator's first confident root cause here was wrong and survived
  into a README draft — the round-2 report rule (no self-verified
  commits after a fix round) is what caught it.

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- Asserting a raw path against a `%q`-formatted log line passes on Linux
  and fails on Windows (`%q` escapes the backslashes). Format the
  expectation the same way the code does (WP AGENT-FIX-1).
- windows-latest failed `go test` on a commit with no Go change. Fixed
  sleeps and tight wall-clock bounds (80ms, <100ms, a 5ms client
  timeout) do not survive Windows' ~15.6ms timer tick: poll for the
  condition with a generous deadline, and keep bounds at a fraction of
  the forced delay. In Go, a handler's `r.Context()` does not see a
  client disconnect before the body is read; block a test handler on an
  explicit release channel closed before `srv.Close()` (WP
  AGENT-TEST-FIX-1).

## 2026-10-02/03 — upstream keepalive pool (ADR-0017, WP CORE-FEAT-1a..1d)
- Adding an optional request field is a breaking change against servers
  that forbid extra fields: rc1-rc8 answer 422 for the whole report. The
  agent resends once without the new fields only when the 422 names
  nothing but `extra_forbidden` on exactly those fields, and states an
  interval only when its scheduler was told one, so old 30-minute tasks
  running a new binary are not judged on a 10-minute window (WP
  AGENT-FEAT-1).

## 2026-10-09/10 — rc13 night run (CORE-FIX-4d, WEB-FEAT-6, DEPS-1, HYG-1, WEB-FIX-10, AGENT-FIX-3)
- `powershell.exe -WindowStyle Hidden` in a Scheduled Task with an Interactive
  principal still flashes a console; `conhost.exe --headless` (Win10
  1809+, undocumented) is the launcher. Exit-code pass-through to
  LastTaskResult and ExecutionTimeLimit reaching the child chain are not
  verified on a real PC yet (WP AGENT-FIX-3).
