# vault-agent

The PC listener for SteamHangar. Deliberately dumb by design (see
`docs/PROJECT_PLAN.md` section 3): read the local Steam library, report
what's installed, no control logic on the device. All prefill/scheduling
decisions live in vault-api.

Status: Phase 2 complete. The ACF/VDF parser (WP 2.1b, `go/acf`), the HTTP
reporter and `vault-agent` CLI (WP 2.2, `go/report`, `go/client`,
`go/agentconfig`, `go/cmd/vault-agent`), the optional hosts-file mode
(WP 2.3, `go/hostsfile` + `go/cmd/vault-agent/hosts.go`), the Linux/SteamOS
variant (WP 2.5, see ADR-0002: real-install library discovery plus systemd
user-service packaging under `packaging/systemd/`), and the Windows
Scheduled Task installer (WP 2.6, `packaging/windows/`) all exist — see
their respective sections below.

**Executable specification (ADR-0005), now history:** vault-agent shipped
its Phase-2 parser design phase as a small Python reference implementation
(`vault_agent/acf.py` + `tests/test_*.py`) whose test corpus pinned every
KeyValues/ACF/VDF parsing decision — including the ones that are
conventions rather than the one obviously correct answer (escape handling,
nesting limits, conditional tags) — before the Go port (`go/acf`, WP 2.1b)
was built and tested against it. Per the ADR-0005 addendum, that Python
package was removed at the Phase-2 close-out (WP 2.6): it is no longer in
this tree, but its full source and test suite remain available in git
history (see the commit that removed it, and everything before it) for
anyone who wants to see the original reference. `tests/fixtures/` is
**permanent** and was never Python-specific data — it's a synthetic
fixture corpus (see "Fixture policy" below) that `go/acf/*_test.go`
consumes directly and continues to consume unchanged.

## Go port (production)

`go/acf` is the Go package vault-agent actually ships (ADR-0005): a
straight port of this Python package's parsing behavior, tested against
the SAME fixture corpus under `tests/fixtures/` (referenced relatively,
never copied).

**Layout:**

```
agent/go/
├── go.mod                    # module github.com/Riviera822/steamhangar/agent
├── acf/
│   ├── acf.go                 # KeyValues (ordered map), ParseError, getCI
│   ├── tokenizer.go            # rune-based tokenizer
│   ├── parser.go                # recursive-descent parser, depth cap, conditionals, BOM
│   ├── strictuint.go             # parseStrictUint (ASCII-digit-only grammar)
│   ├── appmanifest.go              # InstalledApp, ParseAppManifest(File)
│   ├── libraryfolders.go            # ParseLibraryFolders(File), modern + old flat
│   ├── discover.go                   # DiscoverInstalled, Warning
│   ├── testhelpers_test.go            # fixture path resolution, KeyValues-vs-map equality helper
│   ├── acf_test.go                     # tokenizer/parser tests (ported from test_tokenizer.py)
│   ├── appmanifest_test.go              # ported from test_appmanifest.py
│   ├── libraryfolders_test.go            # ported from test_libraryfolders.py
│   └── discover_test.go                   # ported from test_discover.py
├── report/
│   ├── report.go               # BuildReport, ValidateClientID (mirrors the server's rules)
│   └── report_test.go
├── client/
│   ├── client.go                # HTTP POST /v1/agent/installed, retry + backoff + jitter
│   └── client_test.go            # httptest.Server: success/401/500-then-ok/timeout/
│                                  # malformed-JSON/connection-refused/retry-cap cases
├── agentconfig/
│   ├── config.go                 # flags+env parsing, defaults, validation (WP 2.2)
│   └── config_test.go
├── hostsfile/                     # WP 2.3: the managed hosts-file block
│   ├── hostsfile.go                # parse/Verify/Apply/Remove, states, IPv4 validation
│   ├── write.go                     # backup + atomic rename with in-place fallback
│   ├── paths.go                      # per-GOOS default path, elevation hint
│   └── hostsfile_test.go              # fixture corpus: CRLF/LF, corruption, byte-exactness
├── cmd/
│   └── vault-agent/            # THE production CLI
│       ├── main.go              # subcommand dispatch + `report` [--loop] (WP 2.2)
│       ├── hosts.go              # `hosts apply|remove|status` (WP 2.3)
│       ├── main_test.go           # exit codes, one-shot success/failure, API-key redaction
│       └── hosts_test.go           # hosts CLI: exit codes, refusals, elevation hint
```

(`cmd/probe` — a throwaway CLI used for manual real-machine validation during
WP 2.1b/2.2/2.5, never the production reporter — was removed at the Phase-2
close-out (WP 2.6) once it had served its purpose; the validation runs it
produced are still cited below as historical evidence, and its source is
still in git history if anyone wants to re-run that style of check.)

Windows Scheduled Task installer scripts (WP 2.6) live outside `go/`
entirely, next to the systemd packaging:

```
agent/packaging/windows/
├── install-task.ps1          # registers the Scheduled Task (idempotent, -WhatIf)
├── uninstall-task.ps1        # removes exactly what install created (idempotent, -WhatIf)
├── run-vault-agent.ps1       # wrapper the Task Action runs: loads the secret
│                               env file into process env vars, then execs
│                               vault-agent.exe (Windows has no systemd
│                               EnvironmentFile= equivalent)
└── tests/
    └── test-install-uninstall.ps1   # real-machine harness, run by hand (WP 2.6)
```

See "Windows Scheduled Task (WP 2.6)" below for the full install/uninstall
story and the real-machine harness evidence.

Sandbox verification scripts for hosts mode live in `agent/tests/sandbox/`
(see "Hosts-file mode" below) — they are not `go test` and are run by hand.

Systemd packaging for the Linux/SteamOS variant (WP 2.5) lives outside
`go/` entirely, next to it:

```
agent/packaging/systemd/
├── vault-agent-report.service   # Type=oneshot: runs `vault-agent report` once
└── vault-agent-report.timer     # OnCalendar=*:0/10 + OnStartupSec=30s + jitter, Persistent=true (real catch-up)
```

See "Linux/SteamOS variant (WP 2.5)" below for the full install and
verification story.

**Building and testing (WSL2 — no Windows Go toolchain in this repo's dev
environment; Go 1.26 in WSL):**

```bash
wsl bash -c "cd /mnt/c/path/to/your/checkout/agent/go && go build ./... && go vet ./... && gofmt -l . && go test ./..."
```

**Cross-compile matrix** (static binaries, no CGO, matching the three
ADR-0005 targets plus a Linux/arm64 SteamOS check). `CGO_ENABLED=0` is
REQUIRED on every line, not a cosmetic default: building `linux/amd64`
*from* the linux/amd64 WSL host without it silently produces a
dynamically-linked binary (Go enables cgo automatically whenever
GOOS/GOARCH match the build host and a C toolchain is present) - `file`
reported it as "dynamically linked" against `libc.so.6`, directly
contradicting ADR-0005's "no runtime dependencies" static-binary premise
(WP 2.2 review finding S3). The other two targets (`windows/amd64`,
`linux/arm64`) don't have a host C toolchain configured for them so they
came out static either way, but `CGO_ENABLED=0` is set explicitly on all
three so this doesn't depend on which machine happens to run the build:

```bash
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -o /tmp/vault-agent-windows-amd64.exe ./cmd/vault-agent
CGO_ENABLED=0 GOOS=linux   GOARCH=amd64 go build -o /tmp/vault-agent-linux-amd64      ./cmd/vault-agent
CGO_ENABLED=0 GOOS=linux   GOARCH=arm64 go build -o /tmp/vault-agent-linux-arm64      ./cmd/vault-agent
```

All three build clean for `cmd/vault-agent` (and, at the time, the now-
removed `cmd/probe` validation CLI — verified during WP 2.1b and WP 2.2).
**Verified static, not just assumed** (WP 2.2): `file` reports the
`linux/amd64` output as "statically linked" and `ldd` refuses it ("not a
dynamic executable") only with `CGO_ENABLED=0` set - the same build
without it links against `libc.so.6` per the paragraph above. Build output
is never committed — `agent/go/*.exe`, `agent/go/vault-agent(.exe)` are
gitignored.

**You don't have to run these commands yourself.** Starting with WP
AGENT-BIN, `.github/workflows/publish.yml`'s `agent-binaries` job runs
exactly this cross-compile matrix on every `v*` tag and attaches the three
binaries — named `vault-agent-<tag>-<os>-<arch>` (`.exe` on Windows), e.g.
`vault-agent-v1.2.3-windows-amd64.exe` — to that tag's GitHub Release,
together with the packaging files (`install-task.ps1`,
`uninstall-task.ps1`, `run-vault-agent.ps1`, `vault-agent-report.service`,
`vault-agent-report.timer`) and one `SHA256SUMS` file covering all of
them (`sha256sum -c --ignore-missing SHA256SUMS`, or `Get-FileHash -Algorithm SHA256` on
Windows). Building it yourself from this section is only
needed for a version that hasn't been tagged yet, or if you'd rather not
trust a prebuilt binary. See "Windows Scheduled Task (WP 2.6)" below for
what to do with the downloaded `.exe` (including the SmartScreen note).

**No `--version` flag, but the binary is not entirely mute about where it
came from (a real gap plus a real, if partial, mitigation):**
`cmd/vault-agent` has no `-ldflags`-injected version variable, and running
e.g. `vault-agent --version` or `-version` just prints the usual "unknown
command" usage text and exits 2, the same as any other unrecognized
subcommand — a `version` subcommand is a real follow-up package, not this
one. **A follow-up implementing it must not register the flag without the
variable it targets:** `-ldflags "-X main.version=…"` against a
`main.version` that doesn't exist builds green and silently embeds
nothing, so the flag and the variable have to land in the same change.
What already exists, for free, from Go's own toolchain: `go build` run
from inside an ordinary git checkout (a real `.git` **directory**) embeds
a `vcs.revision` build-info stamp, readable with
`strings -a <binary> | grep -o 'vcs\.revision=[0-9a-f]*'` (no Go toolchain
needed to read it back) or `go version -m <binary>` (if one is
available). This gives you the **commit** the checkout was at when the
binary was built, not the tag. In an ordinary single checkout — which is
what the release workflow uses (`actions/checkout` produces a real `.git`
directory, not a linked worktree) — that commit is the one being built,
so for a tag-triggered release build it is the commit the tag points to,
and matching it against the release page's own commit reference closes
the loop. This is the reasoned expectation for the actual release path,
not yet a measurement: nobody has checked a real release binary's stamp
against its tag's commit, because no tag has been pushed yet.

**Measured, and root-caused, for a git worktree nested inside another
repository:** Go's repository-root detection requires `.git` to be a
**directory**; a linked worktree's `.git` is a **file** (a redirect,
`gitdir: <path>/.git/worktrees/<name>`), so Go doesn't follow it at all —
it simply doesn't recognize the worktree as a repository root and keeps
walking up the filesystem looking for a real `.git` directory. Because
this project's worktrees sit physically *inside* the main checkout (at
`<mainrepo>/.claude/worktrees/<name>`), that walk lands on the **main
repository's** `.git` directory and stamps *its* `HEAD` — not the
worktree's checked-out commit, and not nothing. Confirmed directly
against this exact package's own worktree: `git rev-parse HEAD` there
reported one commit while the binary's stamped `vcs.revision` reported a
different, earlier commit — the main repository's `HEAD`, byte-for-byte.
The corroborating detail that pins the mechanism: the stamp also carried
`vcs.modified=true`, which describes the *main repository's* working
tree, not the worktree's — consistent with Go having stamped the
enclosing repo the whole time, not the worktree at all. The **nesting is
the precondition**, confirmed by the control case: a linked worktree
created *outside* any enclosing repository (the ordinary
`git worktree add ../feature-x` layout) produces **no `vcs` stamp at
all** — the upward walk finds no `.git` directory anywhere and Go embeds
nothing. So the one-command reproduction only shows a *disagreement* when
the worktree is nested inside another repo that has since moved on
(`git -C <worktree> rev-parse HEAD` vs.
`strings -a <binary> | grep -o 'vcs\.revision=[0-9a-f]*'`); for a
non-nested worktree, expect the `grep` to come back **empty**, not a
mismatched value — that is the documented behavior in that layout, not a
broken command. None of this applies to the release workflow's own
checkout (`actions/checkout` produces an ordinary, non-worktree clone),
but it does mean a binary built locally from a nested worktree may
silently carry the wrong repository's revision, or a non-nested one may
carry none at all. Until a real tag build's stamp has actually been
checked against its commit, matching a binary's `SHA256SUMS` entry
against the release page it was downloaded from remains the most direct
check available today.

**Live integration check (WP 2.2):** the real vault-api (loopback,
throwaway SQLite DB) was started and the built Windows `vault-agent.exe`
was run against the real `C:\steam` library (read-only): first run —
`200`, `first_report=true`, `received=15` (matching the same 15 apps
`cmd/probe`/WP 2.1b found, back when that validation CLI still existed);
second run — `added=[]`, `removed=[]`,
`first_report=false`; a run with a deliberately wrong `--api-key` returned
`401` in under 100ms (no retry storm — 4xx is never retried) with exit
code 1. Server-side logs confirmed the matching
`vault_api.agent_reports` audit lines. No real app names/sizes are
reproduced here (fixture/privacy policy, same as WP 2.1b).

**Key porting decisions:**

- **Ordered `KeyValues`, not a plain `map[string]any`.** Go map iteration
  order is randomized; Python dicts are insertion-ordered, and
  `ParseLibraryFolders`' returned path list is order-sensitive (the
  modern-format fixture asserts library 0 before library 1). `KeyValues`
  is a small struct (ordered key slice + lookup map) instead — a bare Go
  map here would make that test flaky rather than deterministically
  wrong, which is worse.
- **Runes, not bytes, in the tokenizer.** Python strings are indexed by
  code point; a byte-indexed Go tokenizer would split multi-byte UTF-8
  content (e.g. a non-ASCII game name in a quoted value) mid-character.
  `tokenize` converts to `[]rune` up front so offsets and slicing behave
  the same way.
- **`os.ReadDir` + manual `appmanifest_*.acf` prefix/suffix matching in
  `DiscoverInstalled`, not `filepath.Glob`.** Found during real-machine
  validation: Go's `filepath.Glob` runs `Match`-style pattern parsing
  over EVERY segment of the full joined path — on non-Windows GOOS it
  treats `\` as an escape metacharacter (a library path containing a
  literal backslash, e.g. a Windows-native `libraryfolders.vdf` path fed
  through a non-Windows-built `acf` binary while cross-testing under
  WSL, makes `Glob` silently match nothing), and `[`/`]` are ALWAYS
  character-class syntax on every platform (a library folder legitimately
  named e.g. `lib [beta] one` hits the same silent-nothing-matched trap,
  no OS-specific quirk needed). Python's `pathlib.Path.glob` has no
  equivalent trap — it only pattern-matches the final path component,
  never earlier directory segments. Plain directory listing sidesteps the
  whole class of bug and doubles as the "is this a directory" check.
  Regression-tested for both trigger cases (`go/acf/discover_test.go`);
  mutation-tested once during WP 2.1b's review round by reverting to
  `filepath.Glob` and confirming both new tests fail, then restoring the
  fix and confirming green again.
- **Manifest filename matching is case-insensitive.** `strings.ToLower`
  before the prefix/suffix check, so e.g. `AppManifest_100.ACF` is still
  found. Windows is the primary ADR-0005 target and Windows filesystems
  are case-insensitive; Python's `Path.glob` gets this for free there,
  a Go string comparison does not without the explicit lowering.
- **`Warning` slice instead of `logging.warning`.** Go has no ambient
  logging convention; `DiscoverInstalled` returns `(apps []InstalledApp,
  warnings []Warning)` and leaves it to the caller (WP 2.2's reporter) to
  decide how to surface them. The warning text distinguishes "no
  steamapps directory" (missing/not-a-directory) from "permission
  denied" — different operator actions, so collapsing them into one
  message would lose actionable information the Python spec's per-
  exception-type warning also preserves implicitly (a `PermissionError`
  vs `FileNotFoundError` read differently in a log).
- **Plain `string` for library/manifest paths, not a `Path` type.** Go
  has no cross-platform path-object abstraction equivalent to
  `pathlib.Path`; paths are plain strings throughout, joined with
  `path/filepath` where the code itself constructs a path (e.g.
  `steamapps` under a library root).
- **Invalid UTF-8 in a file is rejected with a `*ParseError`, not
  silently decoded.** Python's `read_text(encoding="utf-8-sig",
  errors="strict")` raises on a byte sequence that isn't valid UTF-8
  (e.g. a stray Latin-1/cp1252 byte like `0xE9` for an accented
  character in a manifest name), which the spec wraps into
  `VdfParseError` — corrupt file, skip it, warn. Go's `string(data)` has
  no equivalent guard: it silently substitutes the U+FFFD replacement
  character for every invalid byte, which would let a mojibake-mangled
  name flow all the way into `InstalledApp` (and from there, WP 2.2's
  reporter → vault-api) with no error at all. `readFileStripBOM` now
  calls `utf8.Valid` explicitly before returning.
- **`ParseError` carries a `Cause error` field and implements
  `Unwrap()`.** So callers can use `errors.Is`/`errors.As` — and
  `Discover` itself does: `errors.Is(err, fs.ErrNotExist)` on a manifest
  read tells "the manifest file vanished between listing and reading it"
  (Steam mid-uninstall/move, a dangling link) apart from "the manifest is
  corrupt", and the two get distinct warning texts (`... vanished between
  listing and reading it ...` vs `skipping corrupt manifest ...`) without
  string-matching the error message (WP AGENT-FIX-1 N2, pinned by
  `TestDiscoverWarnsVanishedManifestDistinctlyFromCorrupt`).

**Real-machine validation (WP 2.1b):** `DiscoverInstalled` was run from
WSL against the real `/mnt/c/steam` install (read-only — the install
itself was never modified) and found the same 15 installed apps with
zero warnings that the Python spec's empirical verification documents
(see "StateFlags" section below). The real `libraryfolders.vdf` on that
machine lists a Windows-native path (`C:\Steam`); resolving it from a
Linux-built binary needed a throwaway symlink in a scratch `/tmp`
directory (`ln -s /mnt/c/steam 'C:\Steam'`, deleted afterward) — a WSL-
vs-Windows path-syntax bridge for this one manual validation run, not
anything `go/acf` itself does or needs to do in real (single-OS)
production use. Real app names/sizes from that run are not reproduced
here or anywhere in the repo (fixture policy below).

**Parity with the Python spec:** every Python test in `tests/test_*.py`
(69 cases across 4 files, `pytest`-parametrized cases counted
individually) has a Go counterpart in `go/acf/*_test.go` using
`t.Run` subtests where Python used `@pytest.mark.parametrize`, so the
per-case count matches 1:1 (`go test ./acf -v` reports 88 leaf test
cases: the 69 ported plus 19 Go-only — measured with `go test -json`,
counting every pass/skip/fail event that has no sub-test beneath it). The
19 Go-only tests pin behavior the Python corpus implies but doesn't
separately exercise:
- 3 depth-cap boundary tests (nesting depth exactly at the 100-level
  cap, one over it, and 1500);
- 2 `filepath.Glob`-vs-`os.ReadDir` regression tests (a library path
  containing `[` `]` glob-metacharacters, and — non-Windows only — a
  literal backslash), mutation-tested once by reverting to `filepath.Glob`
  and confirming both fail;
- 1 case-insensitive manifest filename match test;
- 2 invalid-UTF-8-rejection tests (one per `readFileStripBOM` caller)
  plus 1 discover-level test proving the invalid-UTF-8 file is skipped
  with a warning, not silently mangled into the result;
- 3 integer-overflow divergence tests (one per affected field — see
  below);
- 7 WP AGENT-FIX-1 tests: 3 for `Discover`'s library counters
  (`LibrariesRead`/`LibrariesProbed`, see "vault-agent CLI" below), 2 for
  `libraryKey` (path normalisation for duplicate-library detection, both
  GOOS branches), 1 proving a library listed twice with a trailing
  separator is read once, and 1 for the vanished-vs-corrupt manifest
  warning distinction above.

### Known divergences from the Python spec

The Go port is judged against the Python spec's test corpus and matches
it for every case that corpus (or any real Steam-written file) exercises.
Two divergences exist by deliberate choice rather than oversight —
named here instead of buried in a "matches bit-for-bit" claim that
wouldn't survive contact with either of them:

1. **Integer overflow** (`parseStrictUint`, `go/acf/strictuint.go`): Go's
   `int` is a 64-bit machine integer on every ADR-0005 build target;
   Python's `int` is arbitrary-precision. A digit string too large to fit
   (~19+ digits) is grammatically valid in both, but:
   - `SizeOnDisk` silently becomes `nil` in Go where Python would return
     the actual (huge) value — shaped like the existing "tolerated
     field" contract, but the number is lost, not just defaulted.
   - `StateFlags` makes Go reject the WHOLE appmanifest as corrupt
     (`*ParseError`) where Python would accept it with an enormous
     `StateFlags` value — a file Python treats as fine, Go skips.
   - `appid` is rejected the same way, even though appid is stored as a
     plain string and never used arithmetically anywhere in this
     package — kept anyway, for consistency with the ONE house rule
     this grammar check shares with `api/vault_api/deletion.py`'s
     `coerce_positive_id` (see "Integer field grammar" below), not
     because appid needs the numeric bound.

   No real Steam-written file has ever been observed anywhere near this
   range (StateFlags is a small bitmask, appid and SizeOnDisk are
   ordinary Steam-scale numbers) — this is a corruption/hostile-input
   edge case, not a real-world compatibility gap. Pinned by three tests
   in `go/acf/appmanifest_test.go`.

2. **`isAllASCIIDigits`** (`go/acf/libraryfolders.go`), used to recognize
   numbered library keys in `libraryfolders.vdf`: stricter than Python's
   `str.isdigit()`, which also accepts some non-ASCII Unicode digit-like
   characters that this ASCII-only check rejects. Every real
   libraryfolders.vdf uses plain ASCII numeric keys, so this has no
   observed real-world impact; documented so the divergence is a named,
   deliberate fact rather than an implicit (and wrong) "identical
   behavior" assumption.

## vault-agent CLI (WP 2.2)

The production entrypoint: `go/cmd/vault-agent` discovers the local Steam
library (`go/acf`) and reports the FULL installed-app-id list to vault-api
(`go/report` builds and locally validates the payload, `go/client` sends
it with retry). Deliberately dumb (plan §3): no control logic runs here —
scheduling, prefill decisions, and removal handling all live in vault-api.

Out of scope for `report` (see the Status line above): a Windows Scheduled
Task installer script (WP 2.6). The hosts-file mode landed separately as
the `hosts` subcommand — see "Hosts-file mode (WP 2.3)" below; `report`
itself never touches the hosts file. The Linux/SteamOS-specific library
discovery order and systemd packaging landed as WP 2.5 — see "Linux/
SteamOS variant (WP 2.5)" below.

### Usage

```
vault-agent report                one-shot: discover -> report -> print result -> exit
vault-agent report --loop         keep running, reporting every --interval
                                    (jittered ±10%) until SIGTERM/CTRL-C
vault-agent --version             print the build version and commit, exit 0
```

**Where the version comes from (WP VER-1).** `--version` prints
`vault-agent <version> (commit <sha>)`. The release binaries attached to a
GitHub Release are linked with
`-ldflags "-X main.version=<tag without v> -X main.commit=<full sha>"` by
`publish.yml`'s `agent-binaries` job (from its `build-version` job, the same
version the images report), e.g. `vault-agent 0.1.0-rc8 (commit 1a2b...)`;
that job runs the linux/amd64 binary's `--version` before attaching it. A
plain `go build` from source prints `vault-agent dev (commit unknown)`. The
variables live in `go/cmd/vault-agent/version.go`. Since WP AGENT-FEAT-1
every report also carries it as `agent_version` (see "Presence: version and
report interval in every report" below).

One-shot is the PRIMARY mode (plan §7: a Windows Scheduled Task provides
the timing — see WP 2.6 for the installer). `--loop` exists for a systemd
user service (Phase 2.5's Linux/SteamOS packaging), where the service
itself needs to stay resident and do its own timing.

### Configuration: flags with an environment-variable fallback

No config FILE format (no TOML, no hand-rolled `KEY=VALUE` parser) — the
brief asked for "the simplest option that satisfies: one URL + API key",
and flags-with-env-fallback needs no parser at all. A flag, if given, wins
over its env var equivalent.

| Flag             | Env var                     | Required | Default                                              |
|------------------|------------------------------|----------|-------------------------------------------------------|
| `--server-url`   | `VAULT_AGENT_SERVER_URL`     | yes      | —                                                       |
| `--api-key`      | `VAULT_AGENT_API_KEY`        | yes      | — (prefer the env var: a flag value is visible in process listings / Task Manager) |
| `--client-id`    | `VAULT_AGENT_CLIENT_ID`      | no       | sanitized local hostname (see below)                    |
| `--library-root` | `VAULT_AGENT_LIBRARY_ROOT`   | no       | `C:\Program Files (x86)\Steam` (Windows) / `~/.local/share/Steam` (else) |
| `--interval`     | `VAULT_AGENT_REPORT_INTERVAL`| no       | `10m` — `--loop`'s sleep, and the interval every report states (one-shot: only when set, see below) |
| `--loop`         | —                            | no       | off (one-shot)                                          |
| `--allow-empty`  | —                            | no       | off — post an empty list even when NO library under `--library-root` was readable (see below) |

Why no config file: a file holding `VAULT_AGENT_API_KEY` needs its own
permission story and its own "never commit this" warning that this project
would then have to invent and document — whatever LAUNCHES the agent
(a Windows Scheduled Task's own environment, a systemd unit's
`Environment=`/`EnvironmentFile=`) already has that story solved. An env
var also never appears in a process listing the way a CLI flag would.

**`--server-url` validation:** must parse as an absolute `http://` or
`https://` URL with a host; a trailing slash is stripped. **`--client-id`**
is validated with the exact same rules vault-api enforces server-side
(`go/report.ValidateClientID`, mirroring
`vault_api.routers.agent.InstalledReportRequest`'s validator) — 1-64
characters, no surrounding whitespace, no control characters, not `.` or
`..` — so a bad value is rejected locally with a clear message instead of
spending a round trip on the 422 the server would return anyway.
**`--interval`** must be a positive Go duration (`"10m"`, `"1h"`, ...).

### Presence: version and report interval in every report (WP AGENT-FEAT-1)

Every report carries two optional fields next to `client_id`/`appids`:
`agent_version` (the `--version` value) and `report_interval_seconds`.
vault-api (schema v17) shows the machine **online** until its last report is
older than `2 × interval + 5 minutes`, **offline** after that, and "version
unknown" when no version was sent (`GET /v1/clients`, see api/README.md
"Agent presence").

- **`--loop`** always states its interval: it times itself (default 10m).
- **One-shot** (`vault-agent report`, started by the Windows task or the
  systemd timer) states an interval **only when `--interval` or
  `VAULT_AGENT_REPORT_INTERVAL` is set**. The agent cannot know how often its
  scheduler runs it, and a scheduled task installed before AGENT-FEAT-1 runs
  a new binary every 30 minutes without saying so — claiming the 10m default
  would make that PC flap offline for 5 of every 30 minutes. Without a stated
  interval vault-api assumes 30 minutes. The shipped packaging passes the
  interval (`--interval 10m` in the systemd service,
  `VAULT_AGENT_REPORT_INTERVAL=10m` in the Windows env file).
- A value vault-api would refuse is **left out, not sent**: a version outside
  the VER-1 grammar (only an odd `-ldflags` build), or an interval outside
  1 minute to 24 hours. A refused field would reject the whole report. The
  log line `report presence note=...` says what was left out and why.

**Against an older vault-api (up to v0.1.0-rc8).** Those servers refuse
unknown fields (`422`, `extra_forbidden`) and would reject the whole report.
The agent sends the new fields first; when the `422` names *nothing but*
`agent_version`/`report_interval_seconds` as unknown, it resends the same
report once without them and logs that the server is too old. Any other
`422` (for example a bad `client_id` reported in the same answer) is shown
as before and not resent. Cost on an old server: one extra request per
report; nothing is remembered, so the first report after the server upgrade
carries the fields again. Upgrade order therefore does not matter. (An old
agent against a new server needs nothing: the fields are optional.)

**Default `--client-id`:** the local hostname (`os.Hostname()`), trimmed,
with any non-printable character replaced by `-`, truncated to 64
characters. If
`os.Hostname()` fails, or the sanitized result is empty / `.` / `..`,
config parsing fails loudly with an actionable message rather than
silently falling back to some placeholder value — set `--client-id`
explicitly in that case.

**The choice is visible, not silent (WP AG-0):** every `report` run logs
one startup line naming the resolved `client_id` **and where it came
from** — `client_id_source=flag`, `env`, or `derived-from-hostname` — plus
a `client_id_note` field: for an explicit flag/env value, which one; for a
derived value, a plain "override with `--client-id` or
`VAULT_AGENT_CLIENT_ID`" hint; and if sanitizing actually changed the
hostname (a non-printable character replaced, or truncation past 64
characters), the original hostname and what it became. Before this, an
install with no explicit client id silently committed a machine to a
hostname-derived identity with no indication — at install time or in the
log — that a name was even being picked, or that it could differ from the
raw hostname. See "Windows Scheduled Task" and "systemd packaging" below
for where each install path surfaces this same choice.

**Missing/invalid configuration fails loudly** (`go/agentconfig`): every
problem found (not just the first one) is collected into one error report,
printed to stderr, and the process exits **2** without attempting any
discovery or network call.

**A library root where Steam is not found fails loudly too (WP
AGENT-FIX-1 S1).** `go/acf.Discover` reports how many libraries it could
actually list (`LibrariesRead`) alongside the app list. If that is
**zero** — no `steamapps/` directory readable under `--library-root`, nor
under any path `libraryfolders.vdf` names — `report` logs
`report refused ... probed="..." hint="..."` naming every path it checked
and the `--library-root`/`VAULT_AGENT_LIBRARY_ROOT` fix, posts **nothing**,
and exits **1**. Before this, a wrong library root (a typo, Steam installed
on `D:`, a Linux box where none of the probed paths exist) produced a
legitimate-looking `{"appids": []}` snapshot: vault-api diffed every
previously reported game as removed (`REMOVED N apps` in its log), the
client's badges vanished and its titles left the prefill set — silently,
with exit code 0. A library that IS readable but has nothing installed is
**not** this case: `LibrariesRead` is 1, and the empty report is posted as
always (ADR-0002 full-list semantics). `--allow-empty` restores the old
behaviour explicitly for the rare case where posting "nothing" from a
machine with no Steam library is what you mean (Steam uninstalled, agent
deliberately left running). Pinned by
`TestRun_OneShot_ZeroReadableLibrariesRefusesToPost` (no request reaches
the server, exit 1, the probed path and both hints in the log) and
`TestRun_OneShot_AllowEmptyPostsDespiteZeroReadableLibraries`. The Windows
default (`C:\Program Files (x86)\Steam`) is still a literal with no
existence check and no registry lookup in the agent itself. Since WP
AGENT-FIX-2, `install-task.ps1` reads Steam's `SteamPath` value from
`HKCU\Software\Valve\Steam` at install time and writes it to `env.txt`
when it contains `steamapps\`; otherwise it checks the default for a
`steamapps\` directory and warns loudly (see "Windows Scheduled Task"
below).

### Client identity and renaming (WP AG-0)

`client_id` is not a cosmetic label — vault-api treats it as a **persisted
identity key**. `agent_reports` rows are keyed on it, and every later
report is diffed against that same client's previous one (see
`api/README.md`'s "Agent reports" section) to derive `added`/`removed`.
That has a direct, honest consequence for renaming:

- **Changing `client_id`** (via `--client-id`/`VAULT_AGENT_CLIENT_ID`, or
  by letting the derived hostname change — e.g. renaming the PC) makes
  vault-api see a **brand-new client**, not a renamed one. The new id
  starts its own report history from scratch; it does not inherit
  anything from the old one.
- **The old id's row does not go away by itself.** There is no rename
  endpoint, so the old client id keeps appearing in `GET /v1/clients`,
  showing whatever its last report was, until an operator removes it with
  `DELETE /v1/clients/{client_id}` (WP AG-1, see `api/README.md`'s
  "Deleting a client"). That delete is **not a ban**: a client that
  reports again under the deleted id simply reappears with a fresh diff
  chain, as if it were a brand-new machine.
- **It does stop being acted on**, though: vault-api's scheduler excludes
  a client from its prefill-target sweep once that client's newest report
  is older than the configured staleness window
  (`compute_targets` in `api/vault_api/scheduler.py`) — so a renamed-away
  client's old identity stops influencing what gets prefilled. It simply
  keeps being **listed** as a client that once existed, not acted on.

**What this means in practice:** treat `client_id` as a name you set once
and keep — a bare-metal reinstall of Windows/Steam is fine (games are
rediscovered under the same library, same client id, same history), but
renaming the client id on a whim leaves a ghost row behind that has to be
cleaned up by hand: `curl -X DELETE -H "X-Api-Key: ..."
https://<vault-api>/v1/clients/<old-id>` (`204` on success, `404` if the
id has no rows; shipped in **AG-1**). The agent itself has no delete
command — it stays write-only towards `/v1/agent/installed` by design.

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | the report was sent and accepted (one-shot); or `--loop` exited cleanly on SIGTERM/CTRL-C; or `-h`/`--help` or `--version` was requested |
| `1`  | a runtime failure: no readable Steam library under `--library-root` (refused without `--allow-empty`, see "Configuration" above), local report validation failed (should be unreachable in practice — acf's own parser already enforces the appid grammar — but checked, not assumed), or the HTTP client gave up (network error after retries, `401`, `422`, a `3xx` redirect, malformed response, ...) |
| `2`  | a configuration/usage error (missing/invalid flag or env var, no subcommand given) |

In `--loop` mode, a failed report is logged and the loop keeps going —
exit codes 0/1 only describe one-shot runs and how `--loop` itself
terminates, never an individual iteration inside the loop (plan §7:
"tolerate VPN/network outages" means staying up through a bad interval,
not exiting on one).

### What gets sent (ADR-0002) and the privacy boundary

Every `report` run sends exactly two things to **your own vault-api, over
your own network** (Tailscale/VPN/your reverse proxy — see plan §10; never
to any third party, never to Valve): the **complete** list of currently
installed Steam app ids (`go/acf.DiscoverInstalled` → `go/report.BuildReport`
— filtered to `StateFlags & 4`, de-duplicated, sorted), and the configured
`client_id`. Nothing else about the machine — no game names, no file
paths, no usernames, no Steam account/session identifiers — leaves it;
`InstalledApp.Name` is used only for local log/error messages
(`go/report`'s validation errors), never included in the wire payload
(`report.Payload` has exactly two JSON fields: `client_id`, `appids`).

The agent is **stateless and dumb by design**: it does not remember what
it reported last time, does not compute what changed, and does not decide
anything. vault-api stores each report as a snapshot and diffs it against
that client's previous one to derive `added`/`removed` — see
`api/README.md`'s "Agent reports" section for the full server-side
contract, and ADR-0002 for why removals are surfaced there but never acted
on automatically.

**Removable / unmounted libraries (known behaviour, not a bug fix):** a
library that `libraryfolders.vdf` lists but that is absent on disk right
now — an external drive unplugged, a network share not mounted — is
skipped with a warning (`library path X has no steamapps directory,
skipping`), and its games are therefore **absent from that report**.
vault-api reads that as those titles having been uninstalled and lists
them under `removed` until the drive is back and the next report includes
them again. Because removals are only ever *surfaced* (ADR-0002), nothing
is deleted from the cache by this; the titles may drop out of the prefill
target set for as long as the drive is away. As long as at least one
library was readable the report is still posted (the machine has Steam,
that library just is not here right now); only the all-libraries-
unreadable case is refused (see "Configuration" above). Whether an
absent-but-listed library should instead be carried over from the last
report is a product decision deliberately deferred past the release.

### Retry behavior (`go/client`)

Per-attempt timeout defaults to 15s; connection errors (refused, reset,
DNS failure, timeout), `5xx` responses, **and `429` (Too Many Requests)**
are retried with capped exponential backoff + jitter (default: up to 5
retries, 500ms base, 30s cap). `429` is the one `4xx` that heals by
waiting and resending — plan §9 recommends operators put rate limiting in
front of vault-api via their reverse proxy, so a real deployment can
plausibly return this. Any `Retry-After` header on a `429` is **deliberately
ignored** rather than parsed and honored precisely (plan §9's simplicity
principle: the backoff already waits between attempts, and this is a small
periodic status report, not high-volume traffic a precise wait would
meaningfully protect a server from) — pinned by
`TestReportInstalled_RetryAfterHeaderIsIgnored`, which sets a 3600s
`Retry-After` and asserts the retry still happens almost immediately.
Every OTHER `4xx` response (`401` bad key, `422` rejected body) is
**never** retried — resending the identical request cannot fix either, and
hammering the server on a genuine auth/validation failure would be
actively harmful. The backoff sleep itself is cancellable mid-wait (not
just checked before it starts) — canceling the context passed to
`ReportInstalled` (e.g. on SIGTERM in `--loop` mode) interrupts a pending
backoff sleep immediately rather than sitting through it.

**Redirects are never followed** (WP AGENT-FIX-1 B1): the client sets
`CheckRedirect` to `http.ErrUseLastResponse`, so a `3xx` from a reverse
proxy (an http→https upgrade, a moved hostname) surfaces as a non-retried
error naming the status **and** the `Location` — `server returned HTTP 301
redirected to https://…/v1/agent/installed; … set --server-url to that
address` — instead of net/http's default behaviour, which would have
forwarded `X-Api-Key` to whatever host the `Location` named and, on
`301`/`302`/`303`, rewritten the `POST` into a body-less `GET` (the bare
`HTTP 405` the pre-fix agent showed). Pinned by
`TestReportInstalled_NeverFollowsRedirectOrForwardsKey`, a two-server rig
asserting the redirect target receives zero requests for each of
`301`/`302`/`303`/`307`/`308`.

**Worst-case retry wall time** with the defaults above: 6 total attempts
(1 initial + 5 retries), each up to the 15s per-attempt timeout, plus the
5 backoff sleeps between them at their upper bound
(500ms+1s+2s+4s+8s = 15.5s) ⇒ 6×15s + 15.5s ≈ **105.5s**. `cmd/vault-agent`
budgets **2 minutes** per report specifically to comfortably clear this
worst case without an operator needing to reason about the arithmetic
themselves.

TLS uses the OS/system root CA pool; `http_proxy`/`https_proxy`/`no_proxy`
environment variables are honored by default (`http.ProxyFromEnvironment`)
— set them in the environment vault-agent runs under if reaching the
server needs a proxy.

## Hosts-file mode (WP 2.3)

The optional, **opt-in** DNS-free deployment mode from `docs/PROJECT_PLAN.md`
§10 (mode 3) and §3. It automates — auditably and reversibly — exactly what
`poc/steam-client-test/PROTOCOL.md` §1 and §4 ask you to do by hand in an
elevated Notepad.

```
vault-agent hosts apply  --cache-ip 192.168.1.50   # add/update the entry
vault-agent hosts status [--cache-ip 192.168.1.50] # what's there, and is it live?
vault-agent hosts remove                           # clean uninstall
```

Nothing here runs automatically. `report` never touches the hosts file; the
only way this code writes anything is you typing `hosts apply`.

### What it writes — exactly one block, exactly one line

```
# BEGIN steamhangar-agent (managed block - do not edit inside)
192.168.1.50 lancache.steamcontent.com
# END steamhangar-agent
```

That's the whole change. `lancache.steamcontent.com` is **not** a
LanCache-project name and not ours: it is hardcoded by Valve in the Steam
client, on Valve's own `steamcontent.com` domain, as the client's built-in
cache-discovery interface (plan §3). The client looks it up at startup, and
if it resolves, uses that host as its download cache — which is why this
mode needs no DNS server at all.

**Everything outside the two markers is preserved byte for byte.** The
splice is done on byte offsets, not by re-parsing and re-joining lines, so
line endings, tabs, trailing whitespace and unrelated entries come through
untouched. The block itself is written with the file's own dominant line
ending (CRLF on a Windows hosts file, LF on Linux) — detected per file, not
assumed from the platform.

### Guarantees and refusals

| Situation | Behavior |
|---|---|
| No block yet | Appended at the end of the file |
| Block present, different IP | Replaced **in place** — its position in the file is kept |
| Block already exactly right | **Nothing is written at all** (no backup, no touch) |
| Block hand-edited inside the markers | Rewritten (the boundaries are ours, the contents are ours) |
| Markers damaged (BEGIN without END, two BEGINs, END before BEGIN, …) | **Refuses to touch the file** — `apply` *and* `remove`. The block cannot be identified, so any edit would be a guess. The error names the offending line numbers so you can fix it by hand |
| An entry for `lancache.steamcontent.com` exists **outside** the block | `apply` refuses and names the line. The resolver answers with the FIRST match, so appending a second entry would look correct and do nothing. `remove` is deliberately **not** blocked by this — uninstall must always work |
| The file is UTF-16/UTF-32 (BOM, or NUL bytes anywhere) | **Refuses everything**, including `status`, with a conversion hint. This package is byte-oriented: in UTF-16 every ASCII character is followed by a NUL, so conflict detection and marker detection are blind, and `apply` would append a UTF-8 block to a UTF-16 file — a mixed-encoding file the resolver ignores while vault-agent cheerfully reports `present-correct`. A UTF-8 BOM is fine and is *not* rejected |
| The path is a **symlink** | Refuses, naming the link target. The atomic write replaces the path, so it would silently turn the link into a regular file — `/etc/hosts` is a symlink on NixOS and in some container images, and neither `remove` nor the `.bak` can put a link back. Add the entry wherever that file is generated from instead. (Following the link with `EvalSymlinks` was the alternative; refusal was chosen for v1 because it cannot surprise anyone) |
| The path is a directory | Refuses with an actionable message instead of the platform's raw error (Windows reports a misleading "Access is denied." here) |
| Not running elevated | Fails with exit 1, changes nothing, and prints the exact command to re-run in an elevated shell |

**Before every mutation** the pre-change bytes are written to
`<hosts file>.steamhangar.bak`. If that backup cannot be written, the
mutation does not happen at all.

`--cache-ip` must be a plain IPv4 address (`netip.ParseAddr` + `Is4`, so
leading zeros, IPv6, IPv4-mapped IPv6, ports, CIDR, `0.0.0.0`, multicast and
`255.255.255.255` are all rejected). This is the same IPv4-only stance
vault-dns takes for `CACHE_IP`, for the same two reasons: an AAAA answer
would let IPv6-capable clients bypass the cache, and an unvalidated value
substituted into a line-oriented config file is a line-injection vector —
here, the ability to redirect any hostname on the machine.

`hosts status` additionally resolves `lancache.steamcontent.com` through the
system resolver and prints what it got. That is the real "is it live?"
check. Note the honesty caveat it prints for you: the resolver always
answers from the **system** hosts file, so with `--hosts-path` pointed
elsewhere, the state line and the resolver line describe two different files.

### Uninstall

```
vault-agent hosts remove
```

Deletes exactly the block and leaves the rest of the file byte-identical to
what it was before `apply` — with one documented exception: **a hosts file
that did not end in a newline gains one.** `apply` has to insert a line
terminator before the block (otherwise the file's last line would be glued
to the BEGIN marker), and on removal that byte cannot be told apart from one
the file always had. It is a single byte, and it makes the file more
POSIX-correct, not less. Every other round trip is hash-identical, which the
test suite and both sandboxes assert.

The `.steamhangar.bak` file is left in place on purpose — it is your undo
copy. Delete it yourself when you're satisfied. **It is a single slot, not a
history:** every mutation overwrites it with the state from immediately
before that mutation, so after `apply` → `remove` it holds the *applied*
file, not your original. If you want to keep a particular version, copy it
somewhere else before running the next command.

### Administrator / root rights, and why there is no auto-elevation

Writing the hosts file needs Administrator on Windows and root on Linux.
vault-agent detects the permission failure, changes nothing, and prints the
platform's way of getting an elevated shell **plus the exact command you
just ran**, e.g.:

```
The hosts file is only writable by an Administrator.
Open a new terminal as Administrator (press Start, type "Terminal",
then press Ctrl+Shift+Enter) and run exactly:

    vault-agent.exe hosts apply --cache-ip 192.168.1.50
```

There is deliberately **no self-elevation** (`runas` / re-exec under sudo) in
v1. A binary that can silently re-launch itself with administrator rights is
a materially larger attack surface — the elevated child inherits arguments
and environment from a context that may not be trustworthy, and it trains
users to click through UAC prompts raised by a background task. It also
needs platform-specific code plus a way to get output back out of the
elevated process. One copy-paste keeps the privileged step visible and in
your own shell.

### How the write happens (and what was measured to decide it)

Primary path: write a temp file in the same directory, fsync it, `rename` it
over the target (atomic on Windows and Linux, so a crash can never leave a
truncated hosts file). Fallback: truncate and rewrite the file in place.

**The fallback is not atomic, and that matters:** it truncates the real hosts
file and then writes into it, so a crash, power cut or kill signal in that
window can leave the file empty or half-written — which would break name
resolution for the whole machine until it is repaired. That is exactly why it
is the fallback and not the default, and why the backup is written first and
is mandatory. **Recovery is a single copy:** put
`<hosts file>.steamhangar.bak` back over the hosts file (from an elevated
shell, e.g. `copy C:\Windows\System32\drivers\etc\hosts.steamhangar.bak
C:\Windows\System32\drivers\etc\hosts` or
`sudo cp /etc/hosts.steamhangar.bak /etc/hosts`). Which path was used is
printed on every mutation as `write: rename` or `write: in-place`. If a write
does fail after the fallback already truncated the file, the error says so
explicitly ("may be partially written or empty right now") rather than
claiming the file is untouched — the two cases are distinguished, not guessed.

**Concurrent writers.** Nothing is locked, deliberately: holding a lock on the
file the whole machine resolves through would be more disruptive than the
narrow race it closes, and the realistic other writer is a human in Notepad,
which no lock of ours would exclude anyway. The consequence differs by path —
on the rename path the loser is simply overwritten wholesale; on the in-place
path the result can be genuinely **corrupt** (interleaved or truncated
content), not merely missing a line. That corruption then **fails closed**:
a damaged block trips the markers-corrupt check, so every later `apply` and
`remove` refuses to touch the file until a human fixes it, and the `.bak`
holds the pre-mutation bytes.

The fallback is not hypothetical. Measured on real Windows 11 with a
cross-compiled probe against ACL-restricted files (no admin needed to
reproduce; the real hosts file was never involved):

| file / directory ACL | `os.Rename` | in-place write |
|---|---|---|
| unrestricted | ok | ok |
| file denies DELETE, parent still grants delete-child | ok | ok |
| file denies DELETE **and** parent denies DELETE_CHILD | **denied** | ok |
| file denies FILE_WRITE_DATA | ok | **denied** |
| both denied | denied | denied |

Neither strategy dominates. Row 3 is the shape a hardened hosts file
actually takes — security software commonly blocks replacing or deleting it
while still letting an administrator edit it — so without the fallback,
hosts mode would simply be unavailable on those machines. Every failing case
surfaced as `fs.ErrPermission`, which is what makes the elevation hint fire
reliably on Windows and not just on Unix.

Two consequences worth knowing:

- **Permission bits are preserved** (the original file's mode is applied to
  the temp file before the rename). Without that, `os.CreateTemp`'s `0600`
  would land on `/etc/hosts` and make it unreadable to every non-root
  process on the machine.
- **A rename replaces the file object**, so an *explicit* (non-inherited)
  ACE on the Windows hosts file is not carried over; the new file inherits
  its ACL from `%SystemRoot%\System32\drivers\etc`. On a default Windows
  install the hosts file's ACL is entirely inherited from that directory
  anyway, so the result is identical — and the ACLs that would be worth
  preserving are exactly the ones that deny DELETE, which push the write
  onto the in-place path that preserves them by construction. Preserving an
  explicit ACL across a rename needs `golang.org/x/sys/windows`; this module
  is dependency-free by ADR-0005, so this is documented rather than
  implemented.

### The Linux-client finding

Plan §3 originally scoped this mode Windows-only, on the grounds that "the
Linux/Steam Deck client does not perform this lookup". **Phase 0 WP 0.6
disproved that**: the current stable Linux client *does* perform lancache
discovery (3574 requests through the PoC cache, real CDN Host headers, zero
Range headers — `poc/linux-client-test/RESULTS-20260805-083353.md`, and the
Linux checkbox in plan §7). Hosts mode is therefore useful on Linux and
SteamOS too, and `go/hostsfile` is implemented platform-neutrally: only the
default path (`/etc/hosts` vs `%SystemRoot%\System32\drivers\etc\hosts`) and
the elevation wording differ.

Windows remains the **documented primary target**, because that is where the
"single gaming PC, no local DNS server" scenario mode 3 exists for is most
common. On SteamOS specifically, note that the rootfs is read-only by
default (`steamos-readonly status`/`disable`/`enable`, SteamOS 3's
dm-verity-protected A/B root partition) — **and this claim now has a
verification note attached, per WP 2.5's review of it:** it is accurate
for THIS package's hosts-file mode specifically, because `/etc/hosts`
lives on that read-only root, not under the home directory — writing it
on an unmodified SteamOS install needs `steamos-readonly disable` first,
which this package does not do and does not recommend automating (the
same "no self-elevation" reasoning as the section above extends naturally
to "no self-disabling-of-OS-protections" — a background tool silently
punching a hole in the read-only rootfs is a worse idea than a hosts-file
UAC-style prompt ever was). **It is NOT accurate for WP 2.5's systemd
install** (see "Linux/SteamOS variant (WP 2.5)" below): that install is
deliberately entirely under `$HOME` (`~/.local/bin`,
`~/.config/systemd/user/`, `~/.config/vault-agent/`), which lives on
SteamOS's separate, writable home partition and needs no rootfs mutation
at all — `steamos-readonly disable` is neither required nor mentioned
anywhere in that section. The two Linux-targeting features in this
package have opposite read-only-rootfs stories; naming which one a given
piece of documentation is about is the fix, not picking one blanket
answer for both.

### Verifying it

Three layers, all reproducible:

**1. Fixture tests** (`go test ./hostsfile/`): CRLF and LF variants, mixed
endings, no trailing newline, empty file, file containing only our block,
block in the middle of a file, block at EOF without a terminator, IP-change
re-apply, idempotent no-op re-apply, all six marker-corruption shapes,
conflict detection (including the fully-qualified
`lancache.steamcontent.com.` spelling), backup contents, the backup-failure
refusal (backup impossible while the target is still writable ⇒ refuse and
change nothing), UTF-16/32 and symlink refusals, the half-written-file
warning (a real ENOSPC via `/dev/full`), permission-mode preservation, the
in-place fallback, and hash-compare round trips.

Those tests are **mutation-tested**: eleven separate revert-the-guarantee
mutations (line-ending detection, conflict refusal, corrupt-marker refusal,
END-terminator reuse, mode preservation, backup written, backup-failure
abort, encoding refusal, symlink refusal, the truncated flag, and the
write-failure wording) each make at least one named test fail. The two that
matter most — "a failed backup must abort the mutation" and "a half-written
file must be reported as such" — are pinned by dedicated tests rather than
caught incidentally.

**2. Linux container sandbox** — the end-to-end proof against a REAL
`/etc/hosts` that a real resolver reads:

```bash
wsl -u root bash /mnt/c/path/to/your/checkout/agent/tests/sandbox/run-hosts-sandbox.sh
```

It cross-builds the linux/amd64 binary, then in a throwaway
`alpine:3.23.5` container (`--network none`, so only the hosts file can
answer any lookup) runs: apply → `getent hosts lancache.steamcontent.com`
resolves to the cache IP → status agrees → IP change → remove → resolution
gone and the file byte-identical (sha256) to the pre-apply state → then the
same operations as an unprivileged user, expecting a clean permission-denied
message with the sudo hint and an untouched file. 44 assertions.

*(A container's `/etc/hosts` is a Docker-managed bind mount, and a
bind-mounted file cannot be renamed over — so this sandbox exercises the
in-place fallback. The rename path is covered by the fixture tests and by
the Windows sandbox below.)*

**3. Windows sandbox** — for CRLF behavior and ACL reality on the real OS:

```powershell
agent\tests\sandbox\hosts-windows-sandbox.ps1 -Exe <path to vault-agent.exe>
```

No admin rights needed. It builds fixtures under `-LabDir` only, hashes the
real system hosts file before and after, and **fails if that hash changed**.
Covers: CRLF preservation and hash-identical round trip, the
ACL-hardened-file case (asserting the in-place fallback engages), a fully
write-denied file (asserting exit 1, the Administrator hint, and an
unmodified file), corrupt-marker refusal, and conflict refusal.

### Optional: verify it on YOUR real machine (2 minutes, needs Administrator)

The automated suites deliberately never touch your real hosts file. If you
want to confirm the real thing end to end, this is the whole procedure:

1. **Look before you leap** (no admin needed):
   ```
   vault-agent hosts status
   ```
   Expect `state: absent`. If it reports a **conflict**, you have a manual
   `lancache.steamcontent.com` entry from an earlier experiment (e.g. the
   Phase 0 PoC protocol) — remove that line first, or `apply` will refuse.
2. Open Windows Terminal **as Administrator** (Start → type "Terminal" →
   `Ctrl+Shift+Enter`).
3. Apply, using your cache server's LAN IP:
   ```
   vault-agent hosts apply --cache-ip 192.168.1.50
   ```
   Expect `absent -> present-correct`, a `backup:` path, and the three-line
   block echoed back.
4. Confirm it is live:
   ```
   vault-agent hosts status --cache-ip 192.168.1.50
   ```
   Expect `state: present-correct` and
   `resolver: lancache.steamcontent.com -> 192.168.1.50`.
   (`Resolve-DnsName lancache.steamcontent.com` is the independent
   second opinion.)
5. **Fully quit and restart Steam** — tray icon → Exit, not just closing the
   window. The client only runs cache discovery at startup, so a running
   client ignores the change. This is the single most common reason the
   whole thing appears to "do nothing"
   (`poc/steam-client-test/PROTOCOL.md` §5).
6. Roll back whenever you like:
   ```
   vault-agent hosts remove
   ```
   Then `vault-agent hosts status` should report `absent`, and
   `Resolve-DnsName lancache.steamcontent.com` should stop returning your
   cache IP. Restart Steam again. Delete the `.steamhangar.bak` file if you
   don't want the undo copy.

### Exit codes

| Code | `hosts` meaning |
|------|-----------------|
| `0` | the operation succeeded, or `status` produced a report (whatever state it found — the state is the output, not an error), or `-h` |
| `1` | a runtime failure: permission denied, corrupt markers, a conflicting entry, an unreadable file |
| `2` | a usage error: unknown subcommand, missing/invalid `--cache-ip`, an unknown flag, a stray positional argument |

## Linux/SteamOS variant (WP 2.5)

Everything in "vault-agent CLI (WP 2.2)" above already runs unchanged on
Linux and SteamOS — `go/acf`, `go/report`, `go/client`, and the `report`
subcommand have no Windows-specific code anywhere in them. This package
is the other two things ADR-0002 asked for: a **library-discovery default
that actually matches how Steam installs itself on Linux** (not just one
guessed path), and **systemd user-service packaging** so `report` runs on
a schedule the same way a Windows Scheduled Task will (WP 2.6) — without
needing root, without touching anything outside `$HOME`, and without
`--loop` staying resident.

### Library discovery: probe order (`go/agentconfig`)

`--library-root` / `VAULT_AGENT_LIBRARY_ROOT` always wins when given —
nothing below ever runs if either is set. When neither is set, non-Windows
`defaultLibraryRoot` (`go/agentconfig/config.go`) probes three real
locations, **in this order**, and uses the first one that exists as a
directory:

1. **`~/.local/share/Steam`** — the modern (2019+) default. This is where
   the official Debian/Ubuntu package AND the official installer script
   put it.
2. **`~/.steam/steam`** — the legacy path. On every modern install this is
   itself a **symlink into #1** (confirmed on the real install below —
   `~/.steam/steam -> ~/.local/share/Steam`); kept as a distinct candidate
   for an older or hand-built install where it is a real directory
   instead of a symlink. `os.Stat` follows symlinks, so on a modern
   install this candidate simply resolves to the same place as #1 and is
   redundant with it, never wrong or conflicting.
3. **`~/.var/app/com.valvesoftware.Steam/.local/share/Steam`** — the
   Flatpak sandbox location (`~/.var/app/<app-id>/...` is Flatpak's
   standard per-app data directory convention, applied to Steam's
   Flatpak app ID). Checked last: it's the least common of the three on
   a gaming box, which normally has Steam preinstalled by the OS image or
   installed natively.

If **none** of the three exist yet (a fresh machine, Steam not installed
anywhere known), the probe falls back to #1 rather than failing config
parsing — the same "reasonable starting point, not a guarantee" stance
the pre-existing Windows default already took (see that default's own
doc comment). This is a deliberate choice, not an oversight: making the
Linux default *harder-failing* than the Windows default for the exact
same kind of guess would be an inconsistency with no real benefit,
because the guess is caught one step later either way: `report` logs the
`library root note=...` line at startup, and since WP AGENT-FIX-1 S1
**refuses to post** when that guessed root (or any root) yields zero
readable libraries — exit 1 with the probed paths and the
`--library-root` hint, never a silent empty snapshot (see "vault-agent
CLI" → "Configuration"). The probing primitive
itself (`probeLinuxLibraryRoot`) is unit-tested in isolation for all four
shapes: first-candidate-wins, second/third-wins-when-earlier-missing, and
the none-exist case, which DOES return a descriptive error naming every
path it checked (`go/agentconfig/config_test.go`'s
`TestProbeLinuxLibraryRoot_*` — used for a clear log message, not
propagated as a hard config-parse failure, for the reason just given).
Every one of those tests stubs the package-level `dirExists` var instead
of touching a real filesystem, so they're deterministic regardless of
what happens to be installed on whatever machine runs `go test`.

**Verified against a real Steam-on-Linux install** (WSL2 Ubuntu, the same
box used for Phase 0's Linux-client PoC — native `.deb` install, not
Flatpak):

```
$ ls -la ~/.steam/steam
lrwxrwxrwx 1 jan jan 28 ... /home/jan/.steam/steam -> /home/jan/.local/share/Steam

$ go run ./cmd/probe $HOME/.local/share/Steam   # cmd/probe existed at the time (WP 2.5); removed since, WP 2.6
discovered 3 installed app(s) under /home/jan/.local/share/Steam:
  appid=1070560  installed=true  stateflags=4  size=222685392  name="Steam Linux Runtime 1.0 (scout)"
  appid=1391110  installed=true  stateflags=4  size=676189670  name="Steam Linux Runtime 2.0 (soldier)"
  appid=480      installed=true  stateflags=4  size=1906055    name="Spacewar"
0 warning(s)
```

Candidate #1 exists (it's the real install location) and candidate #2 is
confirmed to be exactly the symlink-into-#1 case documented above — so
probing correctly stops at #1 without needing to fall through. Running
the production binary's `report` with **no `--library-root` at all**
resolves to the same path automatically:

```
$ vault-agent report --server-url http://127.0.0.1:9 --api-key x --client-id probe-host
... library_root="/home/jan/.local/share/Steam" ...
... report built installed_count=3 client_id="probe-host"
```

matching `cmd/probe`'s 3 apps exactly. No real app ownership/library data
beyond what's already synthesized/reproduced above is stored anywhere in
this repo (fixture policy, same as WP 2.1b/2.2).

### systemd packaging (`agent/packaging/systemd/`)

Two unit files, both **systemd USER units** (never `/etc/systemd/system/`
— see the read-only-rootfs note below for exactly why that split matters
on SteamOS specifically, and why it's the right call even on a regular
desktop Linux box):

- **`vault-agent-report.service`** — `Type=oneshot`,
  `ExecStart=%h/.local/bin/vault-agent report --interval 10m` (the flag
  states the timer's cadence in every report, WP AGENT-FEAT-1 — change it
  together with the timer; a flag rather than `Environment=` so the env file
  cannot silently override it),
  `EnvironmentFile=%h/.config/vault-agent/env` (no leading `-`: a
  missing/unreadable env file is a **hard** failure — the unit refuses to
  start at all rather than silently running vault-agent with no
  `VAULT_AGENT_SERVER_URL`/`VAULT_AGENT_API_KEY` — fail-closed by
  construction, on top of `agentconfig`'s own validation as a second
  line of defense). No `After=`/`Wants=network-online.target`: that
  target is a system-manager concept a user unit can't meaningfully
  order against, and `go/client`'s own retry/backoff (up to ~105s worst
  case — see "Retry behavior" above) already covers "network isn't up
  yet" for the one HTTP POST this unit makes. Runs one report and exits;
  no loop, no scheduling logic in the unit itself.
- **`vault-agent-report.timer`** — `OnCalendar=*:0/10` (every 10 minutes
  on the clock, matching `agentconfig.DefaultReportInterval`; 30 before WP
  AGENT-FEAT-1), `OnStartupSec=30s` (WP AGENT-FEAT-1: one report 30 s plus
  up to 5 min of `RandomizedDelaySec=` jitter after the user's service
  manager starts — at login, or at boot with
  `loginctl enable-linger` — so the machine shows online right away;
  `OnStartupSec=` rather than `OnBootSec=` because in a user unit it counts
  from the user manager's start, and `systemd.timer(5)` lets a point already
  in the past elapse immediately on activation; `Persistent=` still applies
  to the `OnCalendar=` trigger only, which is unchanged),
  `RandomizedDelaySec=5min` (jitter, same purpose as `--loop`'s own
  ±10% jitter: many agents on one network shouldn't all hit vault-api in
  lockstep), `Persistent=true` (a missed run while suspended/off is
  caught up on the next activation instead of silently skipped — **this
  is why the timer uses `OnCalendar=` and not `OnBootSec=`/
  `OnUnitActiveSec=`**: `systemd.timer(5)` (systemd 259) is explicit that
  "this setting \[`Persistent=`\] only has an effect on timers configured
  with `OnCalendar=`" — an earlier draft of this unit used the monotonic
  `OnBootSec=`/`OnUnitActiveSec=` pair instead, which have no on-disk
  last-trigger stamp for `Persistent=` to compare against, so it was
  silently a no-op there. That earlier draft, and this README, both
  incorrectly promised catch-up-after-suspend behavior the timer didn't
  actually have — exactly the scenario that matters most on a Steam Deck,
  which spends most of its life suspended, not running).

`%h` is a systemd user-unit specifier for the invoking user's home
directory — **not hardcoded**, so the same two files work unmodified for
any user on any machine (a desktop Linux user, a Steam Deck's `deck`
user, ...). Neither file contains a secret: the API key lives only in the
`EnvironmentFile`, which is data, not part of the unit — see below.

### Install (Steam Deck / any systemd-user Linux machine)

Everything here lives under `$HOME`. Nothing needs root, and nothing
needs `steamos-readonly disable` (see the corrected claim in "Hosts-file
mode"'s "The Linux-client finding" above — that requirement is about
`/etc/hosts`, a completely different file this section never touches).

```bash
# 1. Binary
mkdir -p ~/.local/bin
cp vault-agent ~/.local/bin/          # the linux/amd64 or linux/arm64 cross-build
chmod +x ~/.local/bin/vault-agent

# 2. Units
mkdir -p ~/.config/systemd/user
cp agent/packaging/systemd/vault-agent-report.service ~/.config/systemd/user/
cp agent/packaging/systemd/vault-agent-report.timer   ~/.config/systemd/user/
#    vault-agent-report.service ships a commented-out
#    "#Environment=VAULT_AGENT_CLIENT_ID=..." line (WP AG-0) right above
#    ExecStart=, documenting this same choice at the same place as the
#    example below: leave it commented to let vault-agent derive an id
#    from this machine's hostname, or set VAULT_AGENT_CLIENT_ID in EXACTLY
#    ONE place - either uncomment that line, or set it in step 3's env
#    file below, not both: if both set it, the env file always wins
#    (systemd.exec(5): EnvironmentFile= settings override Environment=,
#    regardless of which appears first in the unit).
#    See "Client identity and renaming" above for what changing it later
#    does and does not do.

# 3. Secret: the env file EnvironmentFile= points at, mode 600 (this is
#    where VAULT_AGENT_API_KEY actually lives - NEVER in the unit file,
#    NEVER in this repo). `umask 077` FIRST so the file is created 0600
#    from the moment it exists - without it, `cat >` creates the file at
#    the shell's default mode (typically 644, world-readable) and ONLY
#    THEN narrows it when chmod runs a moment later, leaving a real
#    (if brief) window where the key is world-readable. The chmod below
#    is kept anyway as a defensive backstop (e.g. if this snippet is
#    copy-pasted without the umask line, or run in a shell with a
#    stranger startup umask already set).
mkdir -p ~/.config/vault-agent
umask 077
cat > ~/.config/vault-agent/env <<'EOF'
VAULT_AGENT_SERVER_URL=http://100.x.y.z:8080
VAULT_AGENT_API_KEY=your-real-api-key-here
VAULT_AGENT_CLIENT_ID=steam-deck-01
EOF
chmod 600 ~/.config/vault-agent/env

# 4. Enable + start the timer (the .service is never enabled directly -
#    the timer activates it on schedule; `systemctl --user start
#    vault-agent-report.service` still works standalone for a manual
#    one-off report, e.g. to verify step 3 before waiting for the timer)
systemctl --user daemon-reload
systemctl --user enable --now vault-agent-report.timer
```

**Headless boxes (no active graphical/SSH login session): `loginctl
enable-linger`.** A systemd **user** manager instance normally only runs
while that user has an active login session — on a Steam Deck sitting at
the Gaming Mode UI with no desktop session open, or any Linux box you SSH
into once to set this up and then log out of, the user manager (and every
timer/service under it) stops the moment the last session closes, so the
10-minute timer would never fire again after that. Fix it once, as root
(or the same account via `sudo`):

```bash
sudo loginctl enable-linger $USER
```

This is a one-time, per-user setting (`Linger=yes` in `loginctl
show-user $USER`, backed by the actual presence of the file
`/var/lib/systemd/linger/<username>` — `enable-linger` creates it,
`disable-linger` removes it; verified directly, by checking for that
exact file, during WP 2.5's own E2E run). It does **not** need
`steamos-readonly disable`: `/var` is one of SteamOS's writable
partitions (alongside `/home`) precisely so ordinary system state like
this can be updated without touching the protected, dm-verity'd root
partition `steamos-readonly` guards — unlike `/etc/hosts`, which lives
*on* that protected partition (see the corrected claim in "Hosts-file
mode" above for the contrast).

**Verify it landed:**

```bash
systemctl --user list-timers vault-agent-report.timer   # shows NEXT/LAST run
journalctl --user -u vault-agent-report.service --no-pager   # report log lines
```

### `timer` vs `--loop`: which one to use

**`systemctl --user enable --now vault-agent-report.timer` is the Linux
answer** — use it on every real Linux/SteamOS install, including the
Steam Deck. It integrates with `journalctl` (no separate log file to
manage), survives a reboot on its own once enabled, and needs no process
of vault-agent's own to stay resident between reports (`Type=oneshot`
exits after every run — nothing idles in memory for 10 minutes doing
nothing).

**`report --loop` still exists and still works on Linux** — it's for the
cases that DON'T have a systemd user session to hook into: a container
(`docker run vault-agent report --loop`, no systemd PID 1 at all), or
under a process supervisor (s6, runit, supervisord) that expects a
long-running foreground process rather than a unit it schedules itself.
Neither mode is "more correct" in the abstract; the systemd timer is
simply the native fit for a real systemd user session, which is what a
Steam Deck, a SteamOS desktop, and most desktop Linux distros actually
run.

### Steam Deck walkthrough (concrete)

1. Switch to Desktop Mode (Steam button → Power → Switch to Desktop).
2. Open Konsole (or any terminal). The default user is `deck`, so every
   `~` above is `/home/deck`.
3. Copy the **`linux/amd64`** cross-build to the device (`scp`, a USB
   stick, or built directly on-device if you have a Go toolchain there)
   and follow "Install" above verbatim.
4. **Note on architecture:** the Steam Deck (LCD and OLED models) is
   **x86_64**, not ARM64 — `linux/amd64` is the correct cross-build. The
   `linux/arm64` target in ADR-0005's build matrix is for a Linux ARM64
   SteamOS/Steam-Machine-class device in general (the ADR's own wording:
   "SteamOS devices ... ARM64"), not the Deck specifically; verify with
   `uname -m` on the device (`x86_64` on every Deck shipped to date)
   before picking a cross-build.
5. `loginctl enable-linger deck` if you want reports to continue while
   sitting in Gaming Mode with no desktop session open (the common case
   for a Deck — most of its life is spent in Gaming Mode, not Desktop
   Mode) — see the linger note above; this is the one step that needs
   `sudo` (a session/login setting, not a rootfs write).
6. Point `VAULT_AGENT_SERVER_URL` at your vault-api's Tailscale/VPN
   address in the env file, same as any other client (plan §10).
7. This install never touches `/etc/hosts`, `/etc/systemd/system/`, or
   any other rootfs path, so **it survives a SteamOS update** the same
   way any of your other `$HOME` files and settings do — no
   `steamos-readonly disable`/`enable` dance needed before or after an OS
   update, unlike the (unrelated) hosts-file mode.

### Cross-builds and the DNS-free hosts-mode note

The `linux/amd64`/`linux/arm64` cross-build commands are unchanged from
the "Go port (production)" section above — this package adds no new
build target, only a new default and new packaging for the targets that
already existed. Recall from that section (and from Phase 0, WP 0.6):
**the current stable Linux/SteamOS Steam client DOES perform lancache
discovery** (the old "Linux clients ignore `lancache.steamcontent.com`"
assumption plan §3 originally shipped with was disproved), so a Linux/
SteamOS box benefits from vault-dns (the DNS-rewrite path) exactly like a
Windows one, and can *also* use hosts mode (`vault-agent hosts apply`) if
you'd rather skip running a DNS server at all — see "Hosts-file mode
(WP 2.3)" above for that mode's own (different) read-only-rootfs story.
The agent (`report`) and hosts mode are independent features; installing
one implies nothing about the other.

### Validation (WP 2.5)

**`systemd-analyze verify`**, both units, real WSL2 systemd (259.5):
clean, zero errors or warnings, once the two files are readable as normal
(non-executable, non-world-writable) regular files at their real install
path with the real `vault-agent` binary present at `%h/.local/bin/` —
`systemd-analyze` itself flags an executable/world-writable unit file as
a warning, which is a `/mnt/c` DrvFs mount artifact (Windows has no Unix
permission bits), not a real issue on an actual Linux filesystem;
confirmed by re-running the same command against copies placed on the
WSL2 instance's own native filesystem, which have ordinary `644`
permissions with no warning.

**End-to-end**, entirely in WSL2 against a throwaway user, throwaway
vault-api (loopback `127.0.0.1`, throwaway SQLite DB, `api/.venv`-pattern
install — a separate WSL-native venv, since the Windows `api/.venv` isn't
runnable from Linux), throwaway API key, cleaned up afterward:

1. Built the `linux/amd64` binary, installed it + both units + a
   `~/.config/vault-agent/env` (mode 600) pointing at the throwaway
   vault-api, exactly per "Install" above.
2. `systemctl --user daemon-reload` — clean.
3. `systemctl --user start vault-agent-report.service` — the unit ran to
   completion: `Result=success`, `ExecMainStatus=0`. Journal:
   `report built installed_count=3` → `reported 3 installed app(s) ...
   added=[480 1070560 1391110] removed=[] first_report=true` → `report
   accepted received=3 added=3 removed=0 first_report=true`.
4. Confirmed server-side landing directly in the throwaway SQLite file:
   `SELECT * FROM agent_reports` returned exactly one row —
   `client_id='wp25-e2e-steamdeck'`, `appids='[480,1070560,1391110]'` —
   matching the discovery output byte for byte.
5. `systemctl --user enable --now vault-agent-report.timer` →
   `systemctl --user list-timers` showed a scheduled next run aligned to
   the next `:00`/`:30` clock boundary plus jitter (per `OnCalendar=*:0/30`
   + `RandomizedDelaySec=5min` — see "Review round 2" below for the
   corrected timer design and the re-verification that replaced this
   step's original, since-fixed `OnBootSec=5min` evidence) with
   `enabled`/`active` status confirmed via `systemctl --user
   is-enabled`/`is-active`.
6. **Found and fixed while validating, not a code bug but an install-doc
   gap:** the very first `systemctl --user start` attempt failed with
   `Failed to connect to user scope bus via local transport` — the WSL2
   user manager instance is not kept alive between separate non-login
   shell invocations without `loginctl enable-linger` (exactly the
   headless-box scenario the brief called out). `sudo loginctl
   enable-linger jan` fixed it immediately; this is why the linger step
   above is written as load-bearing ("Fix it once"), not an optional
   nice-to-have, for any box that doesn't keep a session open 24/7 —
   which describes a Steam Deck in Gaming Mode as much as it describes a
   WSL2 test box.
7. **Cleanup:** timer stopped + disabled, both unit files removed from
   `~/.config/systemd/user/`, the env file and binary removed, the
   throwaway vault-api process killed, its SQLite DB and venv deleted,
   and `loginctl disable-linger` reverted the one durable WSL-wide
   setting step 6 needed — the WSL2 instance was left exactly as found
   (verified: `~/.local/share/Steam`, the real Steam install used for the
   discovery probe above, was read-only accessed and never modified by
   any of this).

### Review round 2: timer semantics fix + `LibraryRootProbeNote` (WP 2.5)

Two findings from review, fixed and re-verified in the same real WSL2
environment as the first pass above:

**S1 — `Persistent=true` was a no-op.** The original timer used
`OnBootSec=5min` + `OnUnitActiveSec=30min` (monotonic triggers) with
`Persistent=true`, on the mistaken assumption that `Persistent=` alone
guarantees catch-up-after-suspend regardless of trigger type. It doesn't:
`systemd.timer(5)` (systemd 259, `man systemd.timer`) states plainly
that `Persistent=` "only has an effect on timers configured with
`OnCalendar=`" — monotonic timers have no on-disk last-trigger stamp for
it to compare against. That's the exact scenario the unit's own comment
and this README both promised worked, and the one that matters most for
a Steam Deck, which spends most of its life suspended. Fixed by
switching to `OnCalendar=*:0/30` (every 30 minutes on the clock) —
`Persistent=true` is now real:

```
$ systemctl --user enable --now vault-agent-report.timer
$ systemctl --user list-timers vault-agent-report.timer
NEXT                          LEFT LAST PASSED UNIT                     ACTIVATES
Sun 2026-08-09 09:01:05 CEST 22min -    -      vault-agent-report.timer vault-agent-report.service
```

(`09:01:05` is the `09:00` calendar boundary plus `RandomizedDelaySec`
jitter — confirms `OnCalendar=*:0/30` is being evaluated correctly, not
just accepted by `systemd-analyze verify`.) And, unlike the monotonic
version, this one actually writes the persistence stamp `Persistent=`
depends on:

```
$ find ~/.local -iname '*stamp*vault-agent*'
/home/jan/.local/share/systemd/timers/stamp-vault-agent-report.timer
```

`systemd-analyze verify` was re-run against both corrected unit files
(same real WSL2 systemd 259.5) — clean, zero errors or warnings. The
full one-shot `start` → journal → `agent_reports` row chain (steps 2-4
above) was also re-run end to end against the corrected units, with
identical results to the first pass.

**S2 — the fallback-guess error was built and discarded.**
`probeLinuxLibraryRoot`'s descriptive "none of the candidates exist"
error (naming every path it checked) was constructed and then thrown
away — no caller ever surfaced it, so an operator whose Steam install
wasn't at any of the three probed locations got a silent wrong guess
with no way to find out why `report` kept sending zero apps. Fixed:
`defaultLibraryRoot` now returns `(path, note)`, `Config` carries it as
`LibraryRootProbeNote` (empty unless the fallback guess was actually
used), and `cmd/vault-agent`'s `runReport` logs it once at startup right
after the existing "vault-agent starting ..." line. Verified live with a
throwaway empty `$HOME`:

```
$ HOME=/tmp/fake-empty-home vault-agent report --server-url http://127.0.0.1:9 --api-key x --client-id probe-host
vault-agent starting ... library_root="/tmp/fake-empty-home/.local/share/Steam" ...
library root note="defaulted library-root to \"/tmp/fake-empty-home/.local/share/Steam\" without confirming it exists (no Steam installation found at any of the known Linux locations (checked in order: /tmp/fake-empty-home/.local/share/Steam, /tmp/fake-empty-home/.steam/steam, /tmp/fake-empty-home/.var/app/com.valvesoftware.Steam/.local/share/Steam); pass --library-root or set VAULT_AGENT_LIBRARY_ROOT explicitly if Steam is installed elsewhere)"
discover warning="could not read/parse .../libraryfolders.vdf (...); falling back to treating .../.local/share/Steam as the only library"
```

— the note now fires exactly once, before discovery even runs, instead
of being unreachable. Regression-tested in
`go/agentconfig/config_test.go` (`TestParse_LibraryRootProbeNote_*`):
set when the fallback guess is used, empty when a probe candidate is
confirmed to exist, empty when `--library-root` is given explicitly (the
probe must not even run in that case).

**Cheap items fixed in the same pass:**
`After=`/`Wants=network-online.target` removed from the `.service` (not
meaningful from a systemd user unit, and `go/client`'s own retry covers
the same gap); the env-file install snippet now runs `umask 077` before
the `cat > ... <<'EOF'` heredoc so the file is `0600` from the moment it
exists, with the `chmod 600` kept as a defensive backstop rather than
the only line of defense; `EnvironmentFile=` (no leading `-`) is now
documented as a deliberate fail-closed choice; the
`linuxLibraryRootCandidates` empty-`$HOME` fallback was fixed to return
the byte-identical pre-WP-2.5 string (`".local/share/Steam"`, no `./`
prefix) instead of a merely-equivalent one, and its doc comment
corrected to match; the `loginctl enable-linger` explanation now names
and empirically confirms the real backing file
(`/var/lib/systemd/linger/<user>`, checked for presence/absence directly
around `enable`/`disable-linger` in WSL2); and the `stamp-vault-agent-
report.timer` file this round's own timer-enable check left under
`~/.local/share/systemd/timers/` in the WSL2 home was deleted during
cleanup, alongside everything the first E2E pass already cleaned up.

## Windows Scheduled Task (WP 2.6)

The Windows counterpart to "Linux/SteamOS variant (WP 2.5)" above: the OS
times a one-shot `vault-agent report` (plan §7's "a Windows Scheduled Task
provides the timing" — see "vault-agent CLI (WP 2.2)"'s "One-shot is the
PRIMARY mode" note), matching the systemd timer's role exactly. Everything
this creates is per-user; no admin elevation is used or required anywhere
in it.

### Getting `vault-agent.exe`, and the SmartScreen warning you should expect

Download `vault-agent-<tag>-windows-amd64.exe` from the release's assets
(see "Cross-compile matrix" above), verify it against the release's
`SHA256SUMS`, then rename it to `vault-agent.exe` (or point `-AgentPath` at
it under its downloaded name — `install-task.ps1` doesn't care what you
call it). **This binary is not code-signed.** There is no Authenticode
certificate on it, so **the first time you run it yourself** — double-
clicking it in File Explorer, or launching it from a terminal you typed
the command into — Windows SmartScreen is expected to show an
"unrecognized app" / "Windows protected your PC" warning tied to the
downloaded file's mark-of-the-web. This is expected, not a sign anything
is wrong, and it will keep happening on every machine you install it on
until this project gets a code-signing certificate. It's called out here
in advance specifically because this is a binary that goes on to request
Administrator elevation for `hosts apply` (see "Hosts-file mode" above) —
an *unexplained* SmartScreen prompt right before an elevation prompt is
exactly the combination phishing malware tries to imitate, so knowing to
expect it, and having verified the checksum first, is the whole mitigation
available for now.

**The scheduled run is a different launch path, and is *not* expected to
show this warning:** `install-task.ps1`'s installed task runs
`vault-agent.exe` via `run-vault-agent.ps1`'s `Start-Process` with
redirected output (WP AGENT-FIX-2; before that, the call operator
`& $AgentPath report`) — either way a direct process launch, not a
shell/Explorer invocation (output redirection rules out `ShellExecute`). SmartScreen's app-reputation dialog is triggered by the
shell-launch path (`ShellExecute` / Explorer's "Open File" verb acting on
the mark-of-the-web), which a scheduled task's own process creation does
not go through. This claim is stated as an **expectation from how the two
launch paths differ, not something this package measured against a live
prompt** — doing that would mean deliberately reproducing the exact
warning this note exists to explain, on a real machine, which wasn't done
here. The practical upshot either way: don't sit and wait for a
SmartScreen prompt from the scheduled run that may simply never come —
if a scheduled report silently isn't happening, check `vault-agent.log`
and the Task Scheduler run history (see "Real-machine harness" below)
instead of assuming a warning dialog is blocking it unattended.

### Running the scripts: execution policy and mark-of-the-web

The three `.ps1` files are not signed either, and the default
`ExecutionPolicy` is `Restricted` on Windows client editions, `RemoteSigned`
on Server editions — under either, an unsigned script carrying the download
mark-of-the-web is refused, so double-clicking or `.\install-task.ps1`
from a plain prompt is expected to fail with "running scripts is disabled
on this system" / "is not digitally signed". Run the installer through an
explicit, **per-process** bypass instead — this changes nothing machine-
wide and needs no admin rights:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-task.ps1 `
    -AgentPath $env:LOCALAPPDATA\VaultAgent\vault-agent.exe `
    -ServerUrl http://100.64.0.5:8080 -ApiKeyFile $env:USERPROFILE\vault-key.txt
Remove-Item $env:USERPROFILE\vault-key.txt
```

**Where to put the binary and the key file.** Put `vault-agent.exe` in
`%LOCALAPPDATA%\VaultAgent\` (the same folder `install-task.ps1` uses
as its default `-ConfigDir`), and the key file somewhere under your user
profile, deleted once the install has run (the task never reads it again —
the key is copied into the owner-only `env.txt`). The reason is the ACL a
folder inherits: a folder you create directly under `C:\` (`C:\Tools`,
`C:\secrets`) inherits `C:\`'s rules, which give every **Authenticated
User** modify rights on its contents. Any other local account could then
replace `vault-agent.exe`, which the scheduled task runs as you, or read
the key file. Folders under your profile inherit an ACL that only you,
SYSTEM and Administrators can use. `install-task.ps1` checks this with
`Get-Acl` and prints a warning (it does not abort) when the binary or its
folder is modifiable, or the key file readable, by Everyone,
Authenticated Users or BUILTIN\Users.

The scripts downloaded from the release carry the mark-of-the-web too;
`Unblock-File .\install-task.ps1, .\uninstall-task.ps1,
.\run-vault-agent.ps1` removes it once, after you have checked them
against the release's `SHA256SUMS`, same as for the `.exe` above. **The installed task
keeps working regardless of either setting:** `install-task.ps1`
registers the action as `powershell.exe -NoProfile -NonInteractive
-ExecutionPolicy Bypass -WindowStyle Hidden -File <ConfigDir>\run-vault-
agent.ps1 ...` (see `$taskArgument` in the script), so the deployed copy
of the wrapper runs under the same per-process bypass on every schedule,
without you having to relax the user's or machine's policy. Use the same
`-ExecutionPolicy Bypass -File` form for `uninstall-task.ps1`.

### Why `-LogonType Interactive`, not S4U

Two Scheduled Task logon types need no stored password: **S4U** (runs
whether the user is logged on or not) and **Interactive** (runs only while
the user has an active session). S4U looked like the better default —
until it was tried on the real, non-admin Windows 11 account used to build
this package:

```
PS> Register-ScheduledTask ... -Principal (New-ScheduledTaskPrincipal `
      -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited) ...
Register-ScheduledTask : Access is denied.
```

— a standard account without the "Log on as a batch job" user right cannot
register an S4U task for itself, full stop. The identical registration
with `-LogonType Interactive -RunLevel Limited` succeeded immediately and
the task ran (`LastTaskResult=0`) with no elevation prompt, so that is what
`install-task.ps1` uses. The trade-off is real and worth stating plainly:
**the task will not run while the account is fully logged off** (locked is
fine — locked still counts as a session; logged off does not). For "report
what's installed on my gaming PC", that's an acceptable match: the machine
is normally logged in whenever Steam itself is running to generate
anything worth reporting.

### Where the API key lives — never on the task's command line

A Scheduled Task's action command line is not a secret store: any process
that can enumerate the task (`schtasks /query /v`, Task Scheduler's own
GUI, `Get-ScheduledTask`) can read it verbatim. That rules out an
`-ApiKey` argument baked into the task's Action the same way a bare
command-line flag was already rejected for `vault-agent` itself (see
"Configuration: flags with an environment-variable fallback" above: "a
flag value is visible in process listings / Task Manager").

Windows Scheduled Tasks also have no equivalent of systemd's
`EnvironmentFile=` — there is no built-in way to say "load these
environment variables before running the action." `run-vault-agent.ps1`
exists to fill exactly that gap: it is the thing the Task Action actually
runs, and it does three things before ever touching vault-agent.exe:

1. Read `KEY=VALUE` lines from `-EnvFile` (blank lines and `#` comments
   skipped).
2. `Set-Item Env:$key $value` for each one, so they become real process
   environment variables — the same `VAULT_AGENT_SERVER_URL`/
   `VAULT_AGENT_API_KEY`/etc. names `go/agentconfig` already reads.
3. Run `vault-agent report` and forward its exit code. With `-LogFile`
   (what the installed task uses) that is `Start-Process -Wait -PassThru`
   with stdout and stderr redirected to two temp files, which are then
   appended to the log byte for byte (WP AGENT-FIX-2, see "The log file"
   below); without `-LogFile` it is a plain `& $AgentPath report`.

### The log file: plain UTF-8, exactly what vault-agent printed (WP AGENT-FIX-2)

Up to rc9 the wrapper ran `& $AgentPath report *>> $LogFile`. A real
operator install on Windows 11 / PowerShell 5.1 (2026-10-04) showed what
that does: vault-agent logs to stderr, PowerShell 5.1 turns every native
stderr line under a stream redirection into an ErrorRecord, so each line
came wrapped in `+ CategoryInfo : NotSpecified: (...:String) [],
RemoteException` / `+ FullyQualifiedErrorId : NativeCommandError`; and
`*>>` writes UTF-16LE while the wrapper's own `[timestamp] starting/
finished` lines are UTF-8, so the file mixed encodings and showed a blank
line between every line. Since AGENT-FIX-2 the agent's stdout and stderr
go to temp files as OS-level handles (no PowerShell stream involved, so no
ErrorRecords and no dependence on `$ErrorActionPreference`), and those
bytes are appended unchanged: the log is plain UTF-8, line for line what
the agent printed, game names with non-ASCII characters included. Stdout
is appended before stderr rather than interleaved; `report` writes to
stderr only, so the order is the agent's. **An existing `vault-agent.log`
from rc9 or older keeps its old UTF-16 part**; new runs append UTF-8
after it, so an editor may show the old part garbled. Delete or rename the
old log once if that bothers you; the wrapper creates a new one on the
next run.

The Task Action's own command line therefore contains exactly three
things: the path to `powershell.exe`, the path to the deployed
`run-vault-agent.ps1`, and two **non-secret filesystem paths**
(`-AgentPath`, `-EnvFile`) — never the key value itself. Verified directly
in the real-machine harness run below (`Get-ScheduledTask`'s
`.Actions[0].Arguments`, checked with a `-notlike "*<the real key
value>*"` assertion, not just "looks right" by eye).

### The secret env file's ACL: locked down BEFORE content is written

Mirrors WP 2.5's systemd `EnvironmentFile=` secret-handling rule
(docs/LEARNINGS.md, "systemd / packaging": *"Secret env files: umask 077
BEFORE creating, not chmod 600 after (world-readable window)"*). Windows
has no umask; `install-task.ps1`'s equivalent sequence is: create the file
empty, lock its ACL down to the current user only, and only then write the
real `VAULT_AGENT_API_KEY` content with `Set-Content` — never a window
where a default/inherited ACL exposes a real secret, because the secret
isn't in the file yet when the ACL changes.

**How the lock-down is actually implemented, and why not the obvious way:**
the first version of this script used the `Set-Acl` cmdlet (`Get-Acl` →
`SetAccessRuleProtection` → `SetAccessRule` → `Set-Acl`), which is the
textbook PowerShell pattern for this and which the sandbox scripts under
`agent/tests/sandbox/` also lean on for read-only ACL *inspection*. It
works — once. **Verified empirically in this package's own harness (the
idempotent-reinstall step):** installing once succeeds; running
`install-task.ps1` a *second* time against the same (already
inheritance-disabled) file fails on that exact `Set-Acl` line with

```
Set-Acl : The process does not possess the required privilege to perform this operation.
(SeSecurityPrivilege)
```

This is a real, reproducible .NET `FileSystemSecurity` quirk, not an
environment fluke — isolated to a 6-line repro outside this package
entirely: `Get-Acl` → modify → `Set-Acl` on a file whose ACL is *already*
protected fails this way for a standard (non-admin) account every time;
the identical sequence on a freshly-inherited ACL (nothing protected yet)
succeeds. `install-task.ps1` therefore uses `icacls.exe` instead —
`icacls <file> /inheritance:r /grant:r "<user>:(F)"` — which does not go
through the same .NET code path and was re-verified idempotent (three
repeated calls against the same file in a standalone repro, then the full
install → re-install → uninstall cycle in the real harness) with identical
results every time. Net effect either way: exactly one explicit ACE
(current user, FullControl), inheritance disabled — the Windows analogue
of Unix mode `600`.

### Layout and what install creates

```
agent/packaging/windows/
├── install-task.ps1
├── uninstall-task.ps1
├── run-vault-agent.ps1
└── tests/
    ├── test-install-uninstall.ps1   # real-machine harness, run by hand
    └── test-packaging-unit.ps1      # hermetic, executed in CI (WP AGENT-FIX-2)
```

`install-task.ps1 -AgentPath <exe> -ServerUrl <url> -ApiKeyFile <path>
[-ClientId ...] [-LibraryRoot ...] [-ConfigDir ...] [-TaskName ...]
[-IntervalMinutes 10]` creates, under `-ConfigDir` (default
`%LOCALAPPDATA%\VaultAgent`):

| Path | Contents |
|---|---|
| `env.txt` | `VAULT_AGENT_SERVER_URL`/`VAULT_AGENT_API_KEY`/`VAULT_AGENT_REPORT_INTERVAL` (`<IntervalMinutes>m`, WP AGENT-FEAT-1)/etc., owner-only ACL (see above) |
| `run-vault-agent.ps1` | a deployed copy of the wrapper script, so the installed task does not depend on this repo checkout still existing at its original path |
| `vault-agent.log` (created on first run) | appended stdout+stderr from every `report` invocation, plain UTF-8 since WP AGENT-FIX-2 (see "The log file" above) — Windows Scheduled Tasks have no built-in per-run log the way `journalctl --user -u ...` gives WP 2.5 for free |

`install-task.ps1`'s summary output also always states the client id
situation (WP AG-0): given `-ClientId`, it echoes that value back marked
`(explicit -ClientId)`; omitted, it prints this machine's raw hostname and
says plainly that vault-agent will derive an id from it, plus how to
override that with `-ClientId`. This is a preview only — it deliberately
does not re-implement `go/agentconfig`'s sanitizing rules in PowerShell
(a second, drifting implementation could show a confidently wrong name);
`vault-agent`'s own startup log line (captured into `-LogFile`, see
"Configuration" above) is the authoritative source for the id actually in
use. See "Client identity and renaming" above for what changing it later
does and does not do.

The same summary states the **library root** situation (WP AGENT-FIX-1
S1, WP AGENT-FIX-2), resolved in this order:

1. `-LibraryRoot` given: that value, marked `(explicit -LibraryRoot)`.
2. Otherwise Steam's own install path from the registry,
   `HKCU\Software\Valve\Steam` value `SteamPath`. Steam stores it with
   forward slashes and often lowercase (`c:/steam` on the operator's
   machine); the script turns it into `c:\steam` and uses it only if it
   contains a `steamapps\` directory. It is then written to `env.txt` as
   `VAULT_AGENT_LIBRARY_ROOT`, and the summary says
   `(from HKCU\Software\Valve\Steam\SteamPath, ...)`. A missing key or
   value is not an error.
3. Otherwise (re-install) the `VAULT_AGENT_LIBRARY_ROOT` already in
   `env.txt`, if it contains `steamapps\`; summary
   `(kept from existing env.txt, ...)`.
4. Otherwise vault-agent's Windows default `C:\Program Files (x86)\Steam`
   (nothing written to `env.txt`). The script checks it for a
   `steamapps\` directory and, if there is none, prints a `WARNING`
   naming `-LibraryRoot` — the install still completes (Steam may be
   installed afterwards), but until the root is right every scheduled run
   will log `report refused` and exit 1 instead of posting an empty list
   (see "Configuration" above for why that refusal exists). A registry
   path that exists but has no `steamapps\` is named in the summary as
   ignored.

The lookup happens once, at install time. If Steam moves later, re-run
`install-task.ps1` (the key can be left out, see below). vault-agent
itself still does not read the registry; doing that in the Go agent is a
possible follow-up, not part of AGENT-FIX-2.

plus the Scheduled Task itself (`VaultAgentReport` by default) with two
triggers. **Trigger 1:** `-Once` with `-RepetitionInterval` =
`-IntervalMinutes` (default 10 since WP AGENT-FEAT-1, was 30; 1 to 1440,
matching `go/agentconfig.DefaultReportInterval` and the systemd timer's
`OnCalendar=*:0/10`) and a 10-year `-RepetitionDuration` (Task
Scheduler has no literal "forever," and `[TimeSpan]::MaxValue` itself is
**out of range** and fails registration — `P99999999DT23H59M59S` is
rejected outright; 10 years is comfortably "never needs manual renewal" in
practice), `-LogonType Interactive -RunLevel Limited` (see above),
`-StartWhenAvailable` (the closest Windows equivalent of the systemd
timer's `Persistent=true`: a run missed while the machine was off/asleep
fires as soon as the task becomes available again instead of waiting for
the next on-schedule slot), and `-MultipleInstances IgnoreNew` (a slow
report — e.g. mid-retry-backoff against an unreachable server, up to the
~105s worst case documented in "Retry behavior" above — must not stack a
second overlapping run at the next interval mark, and a logon run and a
repetition run that fall together run once). **Trigger 2 (WP
AGENT-FEAT-1):** `-AtLogOn -User <installing user>` — the same account the
Interactive principal runs as — so the PC reports right after logon instead
of up to one interval later. The interval is also written into `env.txt`
as `VAULT_AGENT_REPORT_INTERVAL`, so every report states it (see "Presence"
above).

**Upgrading an existing install (WP AGENT-FEAT-1): re-run
`install-task.ps1`** with the same arguments (minus `-IntervalMinutes 30`
if you passed it). A task installed before AGENT-FEAT-1 keeps working with a
new `vault-agent.exe` — still every 30 minutes, no logon trigger, and its
reports state no interval, so vault-api assumes 30 minutes and shows it
correctly — but it only gets the 10-minute schedule, the logon report and
the stated interval from a re-install. On Linux the same applies to the two
unit files: copy the new ones over and run `systemctl --user daemon-reload`
and `systemctl --user restart vault-agent-report.timer`.

**Two secrets that are NOT secrets:** `-ApiKey` takes the key directly as
a string argument, which — like typing any password into a terminal —
lands in this PowerShell session's own command history. `-ApiKeyFile`
(read once, trimmed, then only its resolved value is used) avoids that
entirely and is the recommended form; the two are mutually exclusive and
exactly one is required on a first install.

**Re-install without the key (WP AGENT-FIX-2):** when you re-run
`install-task.ps1` to change something else (`-LibraryRoot`,
`-IntervalMinutes`, ...) you may leave out both `-ApiKey` and
`-ApiKeyFile`: if `<ConfigDir>\env.txt` already holds a non-empty
`VAULT_AGENT_API_KEY`, that key is reused. It is never printed; the
summary only says `API key : kept from existing env.txt`. The env file is
rewritten through the same ACL-before-content path as a first install.
Without such a key, leaving out both is the same usage error (exit 2) as
before. Two more values are carried over so a re-install cannot silently
change what the PC is or where it looks (review S1):

- **Client id:** without `-ClientId`, a non-empty `VAULT_AGENT_CLIENT_ID`
  in the existing `env.txt` is kept, and the summary says
  `Client id : <value> (kept from existing env.txt ...)`. Dropping it
  would make vault-agent fall back to the hostname-derived id — a new
  identity and a ghost row (see "Client identity and renaming" above).
  An explicit `-ClientId` still wins.
- **Library root:** see the order below — a kept `VAULT_AGENT_LIBRARY_ROOT`
  ranks after the registry and is used only if it still contains
  `steamapps\`.

Everything else (`-ServerUrl`, `-IntervalMinutes`, ...) comes from this
run's parameters or their defaults, as before.

### Idempotent re-install

Re-running `install-task.ps1` with the same `-TaskName` **updates the
existing task and env file in place** — `Register-ScheduledTask -Force`
overwrites a same-named task rather than erroring or duplicating it, and
the env file/icacls steps are unconditional (not "only if missing").
Verified in the harness: install once, then re-install with a different
`-ServerUrl`/`-ClientId`/`-IntervalMinutes` — exactly one task still
exists afterward, and its trigger interval plus the env file's content
both reflect the *new* values, not stale leftovers from the first install.

### Uninstall — removes exactly what install created

```
uninstall-task.ps1 [-TaskName ...] [-ConfigDir ...]
```

Unregisters the task, then deletes only the three specific files
`install-task.ps1` is documented above to write (`env.txt`,
`run-vault-agent.ps1`, `vault-agent.log`) — never a blanket directory
wipe, so anything else an operator put in `-ConfigDir` survives. The
directory itself is removed only if that leaves it empty. `-AgentPath`
(the binary) is never touched: it was never copied or owned by
`install-task.ps1` in the first place, exactly like the Linux install
never deletes a hand-placed `~/.local/bin/vault-agent`.

**Refuses gracefully, not loudly, when already absent** — running
`uninstall-task.ps1` against a clean state (no task installed, e.g. it was
already removed, or install never completed) prints "nothing to do" /
"already clean" and exits **0**, never throws. Verified in the harness by
calling it twice in a row and asserting the second call neither throws nor
returns a non-zero exit code.

### Real-machine harness (`agent/packaging/windows/tests/`)

Not `go test` — a PowerShell script run by hand, same convention as
`agent/tests/sandbox`'s hosts-mode scripts. Needs a real
`windows/amd64 vault-agent.exe` (cross-compiled per "Cross-compile
matrix" above); it is never pointed at a real vault-api — the harness uses
a closed local port (`http://127.0.0.1:1`) so the one HTTP attempt the
installed task makes fails fast, and the harness only cares that the
wiring reaches the point of actually invoking vault-agent, not that the
report succeeds.

```powershell
agent\packaging\windows\tests\test-install-uninstall.ps1 -AgentExe <path to vault-agent.exe>
```

Runs entirely under a throwaway `TaskName`
(`SteamHangar-WP26-Harness-Test`) and `ConfigDir` (under `%TEMP%`) so it
cannot collide with, or be mistaken for, a real install; cleans up on
every exit path (including failure) via a `finally` block, and resets any
ACL it touched before deleting (`icacls ... /reset`) so cleanup itself can
never get stuck behind its own lock-down. No administrator rights needed —
same `-LogonType Interactive` per-user registration the real installer
uses.

**What it checks, run for real on a non-admin Windows 11 account (see
"Why `-LogonType Interactive`, not S4U" above for the S4U finding this run
produced):**

1. Syntax ([`scriptblock]::Create`) on all three `.ps1` files.
1b. A usage error (missing `-AgentPath`) genuinely exits with code 2 —
    checked via a real child `powershell.exe -File` process (`&`-invoking
    `install-task.ps1` in-process would have made its own `exit 2`
    terminate the harness itself, not just the called script). Pins the
    S1 review fix below.
2. `-WhatIf` on `install-task.ps1` registers no task and writes no file.
3. A real install: the task exists with the expected trigger repetition
   interval/duration, `LogonType=Interactive`, `StartWhenAvailable=true`;
   the action's command line does **not** contain the API key or the
   server URL (paths only); the env file exists with the right content;
   the env file's ACL has inheritance disabled, no broad-group grant
   (`Everyone`/`BUILTIN\Users`/`Authenticated Users`), and an explicit
   rule for the current user; the wrapper script is deployed.
4. `Start-ScheduledTask` actually runs it end to end: the log file gets a
   `starting: ... report` line and a `finished: exit=...` line, and never
   contains the raw API key.
5. Re-install with different settings updates the *same* task (still
   exactly one `Get-ScheduledTask` result) with the new trigger interval
   and the new env file content.
6. Uninstall removes the task and every file it owns.
7. A second uninstall on the now-clean state doesn't throw and exits 0.

WP AGENT-FIX-2 added to this harness (not yet run on a real machine at
the time of writing): step 4 also asserts the log has no
`NativeCommandError` text and no NUL byte, and a step 5b re-installs
without `-ApiKey`/`-ApiKeyFile` and `-ClientId` and asserts the key and
the client id are kept in `env.txt`, the key is never printed, and the
ACL is still protected.

**Hermetic unit tests, executed in CI (WP AGENT-FIX-2):**
`agent/packaging/windows/tests/test-packaging-unit.ps1` runs in the
`powershell-syntax` job under Windows PowerShell 5.1. It touches only a
throwaway `%TEMP%` directory: no task registration, no registry write, no
network. It lifts `install-task.ps1`'s helper functions out of the script
by name (PowerShell AST) and tests the SteamPath normalization and the
explicit > registry > kept from env.txt > default choice against temp
directories (the registry itself is not faked — a temp HKCU key on a
shared machine is not acceptable — so only "reading it never throws" is
checked live); runs `install-task.ps1 -WhatIf` in a child process for the
key reuse (exit 0, "kept from existing env.txt", key never in the output,
client id and library root carried over) and the unchanged
usage errors (exit 2); and runs `run-vault-agent.ps1` against a fake
agent `.exe` it compiles with `Add-Type`, which writes to stdout and
stderr (including a non-ASCII character and a last line without newline)
and exits 3 — the log must hold those bytes unchanged, no ErrorRecord
wrapping, no NUL byte, and the wrapper must exit 3.

All checks passed (`agent/packaging/windows/tests/test-install-uninstall.ps1`,
36 assertions — counted directly from the harness's own PASS-line output,
not estimated) in the run that produced this section, and the machine was
confirmed clean afterward (`Get-ScheduledTask` for the throwaway name
returns nothing, the throwaway `%TEMP%` config dir does not exist).

**Found and fixed by this same harness run, not assumed correct up
front:** the `Set-Acl`-vs-`icacls` finding above, a
`[TimeSpan]::MaxValue`-is-out-of-range finding for the repetition
duration (`New-ScheduledTaskTrigger` rejects it with a malformed-XML
error naming the literal, out-of-range duration string it tried to
serialize), and — caught in review rather than the first harness pass,
then re-verified by the harness afterward — a
`$ErrorActionPreference = "Stop"`-vs-usage-error-exit-code bug: an
earlier version of `install-task.ps1` set that preference at the very top
of the script, before any input validation. Under `"Stop"`, `Write-Error`
is a TERMINATING error, so every one of the four validation failures
(missing `-AgentPath`, both/neither of `-ApiKey`/`-ApiKeyFile`, a
nonexistent `-ApiKeyFile`, `-IntervalMinutes` `< 1`) unwound the script
before ever reaching its own documented `exit 2` line, and PowerShell
reported exit code **1** (its generic uncaught-error code) for all four
instead. Fixed by leaving `$ErrorActionPreference` at its default
(`"Continue"`, under which `Write-Error` does not terminate the script)
through the whole validation block, and setting it to `"Stop"` only
afterward, for the mutating filesystem/Task-Scheduler operations that
follow. Pinned by harness check 1b above (a real child-process invocation
asserting the missing-`-AgentPath` case now exits 2) so this cannot
regress silently. All three findings were caught by actually running the
install twice / actually registering a trigger / actually checking a
child process's exit code, not by reading the cmdlet reference and
assuming the obvious call would work.

### PowerShell 5.1 encoding note (all three scripts)

All three scripts (`install-task.ps1`, `uninstall-task.ps1`,
`run-vault-agent.ps1`) are plain ASCII on purpose, not merely by
coincidence: an earlier draft used em dashes and curly quotes in comments
and in a couple of live `Write-Host` strings, saved as UTF-8 **without a
BOM**. `[scriptblock]::Create` on that draft's `uninstall-task.ps1` failed
with a spurious "missing closing brace" — reading the same bytes back with
an explicit `-Encoding UTF8` parsed cleanly, and `[System.IO.File]::
ReadAllBytes` confirmed no BOM was present. Windows PowerShell 5.1 decodes
a BOM-less script file under the process's default codepage, not UTF-8;
the em dash's 3-byte UTF-8 sequence gets split into unrelated
single-byte characters under that codepage, and — worse than a cosmetic
mangling — running the file directly (`powershell.exe -File
uninstall-task.ps1`) hit the identical parse failure, so this was never
just a test-tooling artifact. Fix applied: every non-ASCII character was
replaced with a plain-ASCII equivalent (` - ` for em dashes, `'`/`"` for
curly quotes) rather than adding a BOM, since a BOM only helps whichever
tool happens to read it correctly and the failure mode when it doesn't is
exactly what was just observed.

## What's here

`go/acf` — a small, dependency-free parser for Valve's KeyValues text
format (VDF/ACF), used for:

- `steamapps/appmanifest_<appid>.acf` — one installed app's metadata
  (appid, name, StateFlags, SizeOnDisk).
- `steamapps/libraryfolders.vdf` — the list of library folders (drives)
  Steam knows about.

and `DiscoverInstalled(libraryRoot)` (`go/acf/discover.go`), which walks
every library listed in `libraryfolders.vdf` and returns the full list of
installed apps.

The parser is a real tokenizer + recursive-descent parser (quoted keys/
values with escaped quotes and backslashes, nested `{ }` blocks, unquoted
bareword tokens, `//` line comments) — not regex line-picking. KeyValues
is a small format, so this stays a handful of small files, stdlib only (no
third-party parser dependency — PROJECT_PLAN.md section 9). Every parsing
decision below was originally pinned by a Python reference implementation
built for exactly that purpose (ADR-0005) before being ported; that
package is gone from this tree now (removed at the Phase-2 close-out, WP
2.6) but is still in git history, and its behavior lives on unchanged
here.

## StateFlags: what "installed" means

`StateFlags` is a bitmask. Bit `4` means "fully installed". Other bits can
be set alongside it — e.g. an app mid-update still has bit 4 set (it's
still installed and playable, just stale) — so `InstalledApp.Installed()`
checks `StateFlags & 4`, not equality against a specific value.

This was **empirically verified** against every real appmanifest file on
a real Steam install (`c:\steam` on the dev machine): all 15 currently
installed apps report `StateFlags == 4`. That machine has nothing
currently mid-update or partially downloaded, so the "update required"
(bit 2 alongside bit 4) and "partial/not yet installed" (bit 2 without
bit 4) cases are modeled from Valve's publicly documented StateFlags bit
combinations in synthetic test fixtures, not copied from a real file —
see Fixture Policy below.

The full documented bit table (SteamKit's `EAppState` enum, widely
mirrored across community Steam tooling — only bit 4 was independently
verified here) is reproduced next to `StateFlagFullyInstalled` in
`go/acf/acf.go` (not `appmanifest.go`, where the constant is only
*used*, not defined).

`appid` and `StateFlags` are validated against a strict ASCII-digit
grammar (see "Integer field grammar" below) — a value like `" 480 "` or
`"notanumber"` is rejected rather than being silently coerced.

## libraryfolders.vdf: two formats

- **Modern** (Steam client 2019+): numbered blocks, each with a `path`
  key (and an `apps` sub-block, not needed here). This is what the real
  file on the dev machine looks like — a single library, block `"0"`,
  with `path` pointing at the Steam install root itself.
- **Old flat format**: numbered keys mapping directly to a path string,
  alongside unrelated bookkeeping keys (`TimeNextStatsReport`,
  `ContentStatsID`, ...) that must be skipped. No file in this format
  exists on the dev machine (this is a pre-2019 Steam client format); the
  test fixture is synthetic, modeled on the documented format.

`ParseLibraryFolders`/`DiscoverInstalled` treat `libraryRoot` (the
argument you pass in — the Steam install directory containing
`steamapps/libraryfolders.vdf`) as the source of truth for every library
path, including the main one itself, matching how the real file lists it.
Windows vs. Linux/SteamOS discovery differs only in which directory this
argument defaults to when not given explicitly — see "Linux/SteamOS
variant (WP 2.5)" above for the full probe order; nothing in `go/acf`
itself is platform-specific.

## Integer field grammar

`appid`, `StateFlags`, and `SizeOnDisk` are validated with a strict
grammar (`parseStrictUint` in `go/acf/strictuint.go`):

**Accepted:** one or more ASCII digit characters (`0`-`9`), nothing else.
Leading zeros are tolerated (`"004"` -> `4`).

**Rejected:** surrounding whitespace (`" 4 "`), a leading `+`/`-` sign
(`"+4"`), underscore digit-group separators (`"1_0"`), non-ASCII Unicode
digit characters (e.g. Arabic-Indic `"٤"`). This mirrors Go's
`strconv.Atoi` (base 10) — see the "Go port (production)" section's
"Known divergences from the Python spec" above for the one deliberate
exception (integer-overflow handling on very large digit strings) and
what it means for `SizeOnDisk`/`StateFlags`/`appid` specifically.

`appid` stays a `string` (it's an identifier, not a quantity), but is
validated with the same grammar; a value that fails it makes the whole
manifest return a `*ParseError` (`DiscoverInstalled` then warns and skips
that file, per the resilience contract below). `StateFlags` is likewise
required and errors. `SizeOnDisk` is the one field that is tolerated —
missing or ungrammatical, it becomes `nil` rather than failing the whole
record.

This grammar mirrors `api/vault_api/deletion.py`'s `coerce_positive_id`
(`value.isascii() and value.isdigit()`) — this project's one house rule
for "does this string look like an integer field", kept consistent
between `api/` and `agent/`.

## Other pinned parsing decisions

- **Unknown backslash escapes preserve the backslash.** `"a\qb"` parses
  to `a\qb` (backslash kept), not `aqb` (backslash dropped). A lone
  backslash before an unescaped character in a real-world file is far
  more likely to be an under-escaped Windows path
  (`"C:\Steam\common"` written without doubling the backslashes) than a
  deliberate unknown escape — dropping it would silently turn that into
  a *different, wrong* library path with no error raised. Recognized
  escapes remain `\"`, `\\`, `\n`, `\t`, `\r`.
- **Nesting depth is capped at 100 levels**, returning a `*ParseError`
  beyond that rather than recursing further. Real appmanifest/
  libraryfolders files nest 3 levels deep at most; the cap exists so a
  corrupt or hostile file with thousands of nested `{` cannot escape
  `DiscoverInstalled`'s single-error-type contract with an uncaught
  stack-overflow/recursion failure.
- **Platform conditional tags** (`[$WIN32]`, `[$LINUX]`, `[!$WIN32]`,
  ...) immediately after a key's value are tolerated and stripped, not
  treated as the start of the next pair — real Valve KeyValues files use
  this suffix (controller configs, localization files) to mark a pair as
  platform-specific, and failing the WHOLE file on encountering one would
  be needlessly fragile for a format-compliant construct. Deliberate
  simplification: the tag's *condition* is never evaluated — the pair is
  always kept regardless of which platform it names, since vault-agent
  only ever reads the manifest of the platform it runs on and has no
  reason to filter by platform. Only recognized directly after a value
  (the common, real-world placement); a conditional between a key and its
  value is not handled (not observed in practice in the two file types
  this module parses).
- **A leading UTF-8 BOM is stripped**, both when reading a file and
  defensively inside the parser itself for a string handed in directly.
  Without this, the BOM character is neither whitespace, a brace, nor a
  quote, so the unquoted-bareword reader swallows it together with the
  following quoted key — corrupting the very first token of the file.

## Resilience contract

`DiscoverInstalled` never crashes on bad local data. It returns a
`Warning` alongside the result (see the "Go port (production)" section's
"`Warning` slice instead of `logging.warning`" bullet above for why) and
degrades instead:

| Situation | Behavior |
|---|---|
| Missing/corrupt `libraryfolders.vdf` | Warn, fall back to treating `libraryRoot` as the only library |
| Missing/corrupt `appmanifest_*.acf` (incl. non-grammatical `appid`/`StateFlags`) | Warn, skip that file |
| Library path listed but missing on disk | Warn, skip that library (`Discover` counts it as probed, not read; `report` refuses to post when **no** library was readable unless `--allow-empty` is given — see "vault-agent CLI" → "Configuration") |
| Manifest listed by the directory scan but gone by the time it is read (dangling link, Steam mid-uninstall) | Warn with distinct "vanished between listing and reading it" wording (`errors.Is(err, fs.ErrNotExist)`), skip that file |
| Duplicate appid across libraries | Warn, first occurrence wins |
| Duplicate key at the same KeyValues nesting level (incl. a duplicated numbered library index) | Last occurrence silently wins (no warning — this is map-level parsing, not file discovery) |
| Missing/non-grammatical `SizeOnDisk` | `SizeOnDisk` is `nil`, record still returned |
| Nesting deeper than 100 levels | Whole file treated as corrupt (`*ParseError` -> warn + skip) |
| Leading UTF-8 BOM | Stripped transparently, no warning |
| Invalid UTF-8 in the file | `*ParseError` -> warn + skip (Go-only stricter-than-spec divergence, see above) |

## Fixture policy

Test fixtures under `agent/tests/fixtures/` are **synthetic** — modeled
on the structure of real files (verified against `c:\steam` on the dev
machine during development) but with fabricated app IDs, names, and
sizes. No real personal library data (owned game list, real appids/
sizes/build IDs) is committed to this repository. This corpus is
**permanent** (ADR-0005 addendum): it survived the Phase-2 close-out
Python removal unchanged and is consumed directly by `go/acf/*_test.go` —
see "Building and testing" in the "Go port (production)" section above
for how to run the Go suite against it.
