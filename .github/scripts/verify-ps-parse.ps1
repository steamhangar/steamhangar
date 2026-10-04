#Requires -Version 5.1
<#
.SYNOPSIS
    PowerShell 5.1 parser + pure-ASCII gate for the agent packaging scripts
    (WP 5.1, CI). Runs under real Windows PowerShell 5.1 (`shell: powershell`,
    never `pwsh`) -- see verify-ps-analyze.ps1 for the PSScriptAnalyzer half
    of the WP 5.1 brief's "PS 5.1 syntax checks", split out (review round 2,
    S5) to run under `pwsh` instead. Why the split, not a fallback install:
    this script's OWN check (the parser pass) has to run under 5.1's real
    parser to mean anything -- there is no substitute host for it. The
    analyzer check has no such constraint (PSUseCompatibleSyntax analyzes
    against stored version-profile data, not the host's own edition), so it
    moved to the host most likely to have the module already visible.

.DESCRIPTION
    Two independent, both-mandatory checks against every .ps1 file under
    agent/packaging/windows:

      1. A full parser pass ([System.Management.Automation.Language.Parser]::
         ParseFile) -- catches anything that is not even syntactically valid
         PowerShell.

      2. A byte-level pure-ASCII assertion (review round 2, S6). Pins the
         docs/LEARNINGS.md invariant verbatim: "packaging scripts are pure
         ASCII" -- because "em dash in BOM-less UTF-8 breaks the PS 5.1
         parser under the system codepage" (WP 2.6). Checking this by
         DECODING the file and hoping a bad byte raises is exactly the trap
         that invariant warns about: whether a stray multi-byte UTF-8
         sequence actually breaks anything depends on the reading process's
         codepage, so a run on a UTF-8-defaulting host (pwsh, or a future
         CI image) could decode it "successfully" into the wrong text and
         pass silently. Reading the raw bytes and rejecting anything > 0x7F
         has no such luck-of-the-codepage dependency.

    Also parse-only (no ASCII requirement -- that invariant is documented
    for the packaging scripts specifically, not these) over two harnesses
    outside agent/packaging/windows that are PowerShell but must NEVER be
    executed in CI (review round 2, N3): core/tests/test-core.ps1 (runs
    against the real Steam CDN) and dns/tests/test-dnsmasq-config.ps1. Parse-
    checking them here catches a broken edit without ever running them.

    Also deliberately does NOT execute anything it checks: the packaging
    scripts mutate real machine state (Scheduled Tasks, secret env files
    with owner-only ACLs -- see agent/packaging/windows/install-task.ps1),
    and agent/packaging/windows/tests/test-install-uninstall.ps1's own doc
    comment already says "Not `go test` - run by hand". core/tests/
    test-core.ps1 and dns/tests/test-dnsmasq-config.ps1 need the real
    network. This gate is pure static analysis: no execution, no network,
    no container.
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Get-ParseErrors {
    param([string]$Path)
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$parseErrors)
    return $parseErrors
}

function Test-PureAscii {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        if ($bytes[$i] -gt 127) {
            return [PSCustomObject]@{ Offset = $i; Byte = $bytes[$i] }
        }
    }
    return $null
}

Write-Output "Running under PSVersion $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"

$anyFailed = $false

# --- Group 1: agent/packaging/windows -- parser + pure-ASCII (both mandatory) ---
$packagingDir = Join-Path $repoRoot "agent\packaging\windows"
if (-not (Test-Path -LiteralPath $packagingDir)) {
    throw "Target directory does not exist: $packagingDir"
}
$packagingFiles = @(Get-ChildItem -Path $packagingDir -Filter *.ps1 -Recurse)
if ($packagingFiles.Count -eq 0) {
    throw "No .ps1 files found under $packagingDir -- nothing was actually checked."
}

Write-Output ""
Write-Output "=== agent/packaging/windows: parser + pure-ASCII ($($packagingFiles.Count) file(s)) ==="
foreach ($f in $packagingFiles) {
    $rel = $f.FullName.Substring($repoRoot.Length + 1)

    $parseErrors = Get-ParseErrors -Path $f.FullName
    if ($parseErrors.Count -gt 0) {
        $anyFailed = $true
        Write-Output "PARSE ERROR: $rel"
        foreach ($e in $parseErrors) {
            Write-Output "  line $($e.Extent.StartLineNumber): $($e.Message)"
        }
    } else {
        Write-Output "  parse OK: $rel"
    }

    $badByte = Test-PureAscii -Path $f.FullName
    if ($null -ne $badByte) {
        $anyFailed = $true
        Write-Output ("NON-ASCII BYTE: {0} -- offset {1}, byte 0x{2:X2}" -f $rel, $badByte.Offset, $badByte.Byte)
    } else {
        Write-Output "  pure ASCII: $rel"
    }
}

# --- Group 2: parse-only, never executed (N3) -------------------------------
# WP PAIR-1 (review finding 6): the vault-agent install command the web UI
# generates (Settings -> Add a device -> Windows PC), with a dummy key. It
# is written by web/tests/agent-install.test.js, which fails when the
# committed copy drifts from what the page generates; parsed here with the
# real 5.1 parser, never executed (it downloads and installs).
$parseOnlyTargets = @(
    (Join-Path $repoRoot "core\tests\test-core.ps1"),
    (Join-Path $repoRoot "dns\tests\test-dnsmasq-config.ps1"),
    (Join-Path $repoRoot "web\tests\fixtures\windows-install-command.ps1")
)

Write-Output ""
Write-Output "=== parse-only (never executed): $($parseOnlyTargets.Count) file(s) ==="
foreach ($path in $parseOnlyTargets) {
    if (-not (Test-Path -LiteralPath $path)) {
        $anyFailed = $true
        Write-Output "MISSING: $path"
        continue
    }
    $rel = $path.Substring($repoRoot.Length + 1)
    $parseErrors = Get-ParseErrors -Path $path
    if ($parseErrors.Count -gt 0) {
        $anyFailed = $true
        Write-Output "PARSE ERROR: $rel"
        foreach ($e in $parseErrors) {
            Write-Output "  line $($e.Extent.StartLineNumber): $($e.Message)"
        }
    } else {
        Write-Output "  parse OK: $rel"
    }
}

if ($anyFailed) {
    throw "One or more PowerShell 5.1 parse/ASCII checks failed -- see output above."
}

Write-Output ""
Write-Output "All checked scripts parse cleanly; agent/packaging/windows is pure ASCII."
