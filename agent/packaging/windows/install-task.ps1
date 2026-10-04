<#
.SYNOPSIS
    Registers a per-user Windows Scheduled Task that runs vault-agent.exe
    `report` at logon and every N minutes (default 10)  -  WP 2.6,
    WP AGENT-FEAT-1.

.DESCRIPTION
    This is the Windows counterpart to agent/packaging/systemd's
    vault-agent-report.timer/.service (WP 2.5): the OS times a one-shot
    `vault-agent report` invocation, matching plan section 7 ("a Windows Scheduled
    Task provides the timing") and agent/README.md's "one-shot is the
    PRIMARY mode" stance. Everything this script creates lives under the
    current user  -  no admin elevation, no machine-wide state.

    ### Why -LogonType Interactive, not S4U

    Two logon types can run a Scheduled Task without ever storing a
    password: Interactive (the task only runs while this user has an
    interactive/RDP session, which is exactly the desktop-gaming-PC scenario
    vault-agent targets) and S4U (runs whether logged in or not, no stored
    password, but needs the "Log on as a batch job" user right).

    Empirically verified on a real, non-admin Windows 11 account during WP
    2.6 (`agent/packaging/windows/tests/`): registering with `-LogonType S4U`
    fails with "Access is denied" for a standard user lacking that logon
    right; the identical registration with `-LogonType Interactive
    -RunLevel Limited` succeeds and the task runs (`LastTaskResult=0`)
    without any elevation prompt. Interactive is therefore what this script
    uses. Trade-off, stated plainly: the task will NOT run while the user is
    fully logged off (e.g. at the Windows lock/login screen with no session
    at all)  -  acceptable for "report what's installed on my gaming PC",
    which is normally logged in whenever Steam itself is running.

    ### Where the API key lives (never on the task's command line)

    schtasks/Task Scheduler stores an action's command line in a place any
    process that can query the task (e.g. `schtasks /query /v`) can read  - 
    the same reason a Windows Scheduled Task's "Run" field must never hold
    a secret verbatim. Mirroring WP 2.5's systemd EnvironmentFile= pattern
    (agent/README.md, "Install" section) and go/agentconfig's own
    flags-with-env-fallback design (there is deliberately no vault-agent
    config-file format  -  see go/agentconfig/config.go's package doc), the
    key is written to a KEY=VALUE env file that run-vault-agent.ps1 (shipped
    alongside this script) reads and exports as process environment
    variables immediately before invoking vault-agent.exe. Only that
    wrapper script's path plus two non-secret filesystem paths ever appear
    in the Scheduled Task's action.

    The env file's ACL is locked down to the current user only BEFORE any
    content is written to it  -  the same "umask 077 before creating, not
    chmod 600 after" ordering docs/LEARNINGS.md's "systemd / packaging"
    section calls out (WP 2.5): an empty file is created, its ACL is
    replaced with an owner-only rule (inheritance disabled), and only then
    is the secret content written  -  never a window where a default,
    possibly-inherited ACL exposes real content.

    ### When the task runs, and how vault-api tells online from offline
    ### (WP AGENT-FEAT-1)

    The task has two triggers: one at logon of the installing user (the
    same user the Interactive principal below runs as), so the PC reports
    as soon as someone sits down at it, and a repetition trigger every
    -IntervalMinutes (default 10). -StartWhenAvailable catches up a run
    missed while the PC was asleep, and -MultipleInstances IgnoreNew keeps
    a logon run and a repetition run from overlapping.

    The interval is also written into the env file as
    VAULT_AGENT_REPORT_INTERVAL (e.g. "10m"), so every report states it.
    vault-api shows the PC offline once its last report is older than
    2 x interval + 5 minutes. An install from before AGENT-FEAT-1 (30
    minutes, no logon trigger, no interval in the env file) keeps working
    with a new vault-agent.exe: its reports state no interval and vault-api
    assumes 30 minutes. Re-run this script to switch it to the new
    schedule.

    ### What this prints about the client id (WP AG-0)

    vault-agent identifies this machine to vault-api with a "client id" that
    is either given explicitly (-ClientId here, or --client-id/
    VAULT_AGENT_CLIENT_ID on the agent itself) or, if nothing is given,
    derived from the local hostname (go/agentconfig's defaultClientID,
    sanitized and truncated to 64 characters). Before WP AG-0 this choice
    was invisible: an install with no -ClientId silently committed the
    machine to a hostname-derived identity with no install-time indication
    that a name was even being picked, or that it could have been chosen
    differently.

    This script's summary output (bottom) now always states which case
    applies:
      - -ClientId given: the exact value, that it was explicit, and that it
        is passed to the agent as VAULT_AGENT_CLIENT_ID (via the env file,
        not the task's command line -- see "Where the API key lives"
        above) -- so a look at vault-agent's own log afterward correctly
        shows client_id_source=env, never =flag, even though this script's
        own parameter is named -ClientId.
      - -ClientId omitted: this machine's hostname from
        [System.Net.Dns]::GetHostName() (NOT $env:COMPUTERNAME -- see the
        case-sensitivity note below) plus a plain statement that
        vault-agent will derive a (possibly sanitized) client id from it,
        and how to override that with -ClientId.

    This is a preview, not a guarantee of the final value: this script
    deliberately does NOT re-implement go/agentconfig's sanitizing rules in
    PowerShell (see -ClientId's own parameter doc for why -- a second,
    drifting implementation could confidently show the WRONG name). For
    almost every real hostname the shown value and the resolved one are
    identical (sanitizing only touches non-printable characters or names
    over 64 characters); vault-agent's own startup log line (captured by
    run-vault-agent.ps1 into the -LogFile this script configures --
    client_id=... client_id_source=... client_id_note=...) is the
    authoritative source of truth for the exact value in use, unlike this
    preview.

    ### Case-sensitivity note (review round 1, WP AG-0)

    This preview reads [System.Net.Dns]::GetHostName() deliberately, NOT
    $env:COMPUTERNAME: Windows uppercases the NetBIOS-style COMPUTERNAME
    variable (e.g. a machine actually named "Demon" reports COMPUTERNAME
    as "DEMON"), while go/agentconfig reads os.Hostname(), which on
    Windows resolves the DNS host name and preserves the real case exactly
    like hostname.exe and GetHostName() do. Since client_id is a
    CASE-SENSITIVE persisted identity key server-side (see "Client
    identity and renaming" in agent/README.md), a preview that showed the
    wrong case would not just look different -- an operator who read
    "DEMON" here and later pinned -ClientId DEMON explicitly would create
    a second identity and a ghost row, exactly the harm that section
    warns about. Verified directly: on the machine used to build this
    package, $env:COMPUTERNAME, hostname.exe, and
    [System.Net.Dns]::GetHostName() disagreed in exactly the way described
    above; only the last one matched a real cross-built vault-agent.exe's
    own resolved client_id.

.PARAMETER AgentPath
    Full path to vault-agent.exe. Recommended location:
    %LOCALAPPDATA%\VaultAgent\vault-agent.exe. A folder you create directly
    under C:\ (C:\Tools, ...) inherits C:\'s ACL, which lets every
    Authenticated User modify its contents -- any local account could then
    swap the binary the task runs as you. This script warns (does not
    abort) when the binary or its folder is writable by Users,
    Authenticated Users or Everyone.

.PARAMETER ServerUrl
    VAULT_AGENT_SERVER_URL value (e.g. http://100.x.y.z:8080).

.PARAMETER ApiKey
    VAULT_AGENT_API_KEY value, given directly. Mutually exclusive with
    -ApiKeyFile. NOTE: a value passed this way lands in this PowerShell
    session's command history like any other typed argument  -  prefer
    -ApiKeyFile (e.g. a one-line file created by a password manager or
    `Read-Host -AsSecureString` piped to a temp file you delete afterward)
    if that matters in your environment.

.PARAMETER ApiKeyFile
    Path to a file whose entire (trimmed) contents is the API key.
    Mutually exclusive with -ApiKey. Re-install (WP AGENT-FIX-2): when
    neither -ApiKey nor -ApiKeyFile is given and <ConfigDir>\env.txt
    already holds a non-empty VAULT_AGENT_API_KEY, that key is kept (never
    printed; the summary says "API key: kept from existing env.txt").
    Without such a key, one of the two is required. The file itself is only read, never
    copied or referenced by the installed task -- keep it under your user
    profile (e.g. $env:USERPROFILE\vault-key.txt) and delete it after the
    install. This script warns (does not abort) when the file is readable
    by Users, Authenticated Users or Everyone.

.PARAMETER ClientId
    Optional VAULT_AGENT_CLIENT_ID value. Omitted -> a non-empty
    VAULT_AGENT_CLIENT_ID in the existing <ConfigDir>\env.txt is kept
    (re-install, WP AGENT-FIX-2: the PC's identity stays stable); with no
    such value vault-agent defaults to the sanitized local hostname
    (go/agentconfig). Either way, this script's
    summary output (below) says plainly which one will happen and how to
    change it -- see "What this prints about the client id" below.

    NOTE (WP AG-0): when omitted, this script does NOT attempt to replicate
    go/agentconfig's hostname-sanitizing rules (rune replacement, 64-char
    truncation) here in PowerShell -- a second implementation of that logic
    would drift from the real one and could confidently print the WRONG
    sanitized name. It prints the raw, unsanitized hostname instead and
    points at vault-agent's own startup log line (which logs the value it
    actually resolved, plus its source) as the authoritative answer.

.PARAMETER LibraryRoot
    Optional VAULT_AGENT_LIBRARY_ROOT value. Omitted (WP AGENT-FIX-2) ->
    this script reads Steam's own install path from
    HKCU\Software\Valve\Steam\SteamPath (e.g. "c:/steam", normalized to
    "c:\steam") and, if that directory contains steamapps\, writes it to
    env.txt as VAULT_AGENT_LIBRARY_ROOT; the summary names the registry as
    the source. Without a usable registry value, a VAULT_AGENT_LIBRARY_ROOT
    already in <ConfigDir>\env.txt is kept if it contains steamapps\
    (re-install). Failing both, it falls back to
    vault-agent's own Windows default (`C:\Program Files (x86)\Steam`,
    nothing written to env.txt), checks that default for a steamapps\
    directory and prints a loud warning if there is none (WP AGENT-FIX-1
    S1) - the install still proceeds, but vault-agent will then refuse to
    post (exit 1, "report refused" in the log) until -LibraryRoot points
    at the directory that contains steamapps\. The lookup happens here, at
    install time, only; vault-agent itself does not read the registry.
    See agent/README.md's "Windows Scheduled Task" section.

.PARAMETER ConfigDir
    Directory this script owns: the env file, the deployed copy of
    run-vault-agent.ps1, and (if -LogFile is not overridden) the log file.
    Default: $env:LOCALAPPDATA\VaultAgent.

.PARAMETER TaskName
    Scheduled Task name. Default: VaultAgentReport. Re-running install with
    the same name UPDATES the existing task in place (idempotent) instead of
    creating a duplicate.

.PARAMETER IntervalMinutes
    Repetition interval in minutes, 1 to 1440. Default: 10, matching
    go/agentconfig.DefaultReportInterval and the systemd timer's
    OnCalendar=*:0/10. Also passed to the agent as
    VAULT_AGENT_REPORT_INTERVAL so its reports state it; intervals below
    1 minute are not possible here, and vault-api accepts 1 minute to
    1 day (24 hours = 1440 minutes).

.PARAMETER LogFile
    Optional override for the log file run-vault-agent.ps1 appends to.
    Default: <ConfigDir>\vault-agent.log.

.EXAMPLE
    .\install-task.ps1 -AgentPath $env:LOCALAPPDATA\VaultAgent\vault-agent.exe `
        -ServerUrl http://100.64.0.5:8080 -ApiKeyFile $env:USERPROFILE\vault-key.txt

.EXAMPLE
    .\install-task.ps1 -AgentPath $env:LOCALAPPDATA\VaultAgent\vault-agent.exe `
        -ServerUrl http://100.64.0.5:8080 -ApiKeyFile $env:USERPROFILE\vault-key.txt -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$AgentPath,

    [Parameter(Mandatory = $true)]
    [string]$ServerUrl,

    [Parameter(Mandatory = $false)]
    [string]$ApiKey,

    [Parameter(Mandatory = $false)]
    [string]$ApiKeyFile,

    [Parameter(Mandatory = $false)]
    [string]$ClientId,

    [Parameter(Mandatory = $false)]
    [string]$LibraryRoot,

    [Parameter(Mandatory = $false)]
    [string]$ConfigDir = (Join-Path $env:LOCALAPPDATA "VaultAgent"),

    [Parameter(Mandatory = $false)]
    [string]$TaskName = "VaultAgentReport",

    [Parameter(Mandatory = $false)]
    [int]$IntervalMinutes = 10,

    [Parameter(Mandatory = $false)]
    [string]$LogFile
)

# ---- helpers (WP AGENT-FIX-2) ------------------------------------------
#
# Pure functions with no side effects beyond reading, so
# tests/test-packaging-unit.ps1 can lift them out of this file by name
# (PowerShell AST) and test them in CI without running the installer.

function Get-EnvFileValue {
    # Returns the value of $Key in a KEY=VALUE env file written by this
    # script, or $null when the file or the key is missing. Uses the same
    # line rules as run-vault-agent.ps1 (blank and '#' lines skipped, key
    # trimmed, value kept as-is; a later line wins). Encoding differs
    # slightly: this reads UTF-8 explicitly, the wrapper uses PS 5.1's
    # default (UTF-8 only when a BOM is present, else the ANSI codepage).
    # env.txt as written by this script always carries a UTF-8 BOM
    # (Set-Content -Encoding utf8 on 5.1), so both agree on it; only a
    # hand-edited, BOM-less file with non-ASCII values could be read
    # differently by the two.
    param([string]$Path, [string]$Key)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $found = $null
    foreach ($rawLine in @(Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $trimmed = $rawLine.Trim()
        if ($trimmed.Length -eq 0) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        $eqIndex = $rawLine.IndexOf("=")
        if ($eqIndex -lt 1) { continue }
        if ($rawLine.Substring(0, $eqIndex).Trim() -eq $Key) {
            $found = $rawLine.Substring($eqIndex + 1)
        }
    }
    return $found
}

function Get-RegistrySteamPath {
    # Raw HKCU\Software\Valve\Steam SteamPath value (Steam writes it with
    # forward slashes and often lowercase, e.g. "c:/steam"), or $null.
    # Never throws: a missing key or value is the normal "Steam is not
    # installed for this user" case.
    try {
        $item = Get-ItemProperty -LiteralPath "HKCU:\Software\Valve\Steam" -Name "SteamPath" -ErrorAction Stop
        return [string]$item.SteamPath
    } catch {
        return $null
    }
}

function ConvertTo-SteamLibraryRoot {
    # Normalizes a raw SteamPath into a Windows path: forward slashes to
    # backslashes, surrounding whitespace and trailing separators removed
    # (a bare drive keeps its root backslash, "c:/" -> "c:\"). Case is
    # kept as given; Windows paths are case-insensitive. $null for empty.
    param([string]$SteamPath)
    if ([string]::IsNullOrWhiteSpace($SteamPath)) { return $null }
    $p = $SteamPath.Trim().Replace("/", "\").TrimEnd([char]92)
    if ($p.Length -eq 0) { return $null }
    if ($p -match '^[A-Za-z]:$') { $p = $p + "\" }
    return $p
}

function Resolve-LibraryRoot {
    # Picks the library root, in this order:
    #   1. explicit -LibraryRoot                      (Source "explicit")
    #   2. registry SteamPath, if it has steamapps\   (Source "registry")
    #   3. VAULT_AGENT_LIBRARY_ROOT from the existing env.txt, if it has
    #      steamapps\ (re-install, review S1)          (Source "kept")
    #   4. vault-agent's own Windows default          (Source "default")
    # WriteToEnv says whether the value goes into env.txt as
    # VAULT_AGENT_LIBRARY_ROOT: for "default" it does not, the agent's own
    # default applies exactly as before. RegistryCandidate is the
    # normalized registry path even when it was rejected, for the summary.
    param([string]$ExplicitRoot, [string]$RegistrySteamPath, [string]$DefaultRoot, [string]$ExistingRoot)
    $candidate = ConvertTo-SteamLibraryRoot -SteamPath $RegistrySteamPath
    if ($ExplicitRoot) {
        return [PSCustomObject]@{ Root = $ExplicitRoot; Source = "explicit"; WriteToEnv = $true
            SteamappsFound = $null; RegistryCandidate = $candidate }
    }
    if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate "steamapps") -PathType Container)) {
        return [PSCustomObject]@{ Root = $candidate; Source = "registry"; WriteToEnv = $true
            SteamappsFound = $true; RegistryCandidate = $candidate }
    }
    if ($ExistingRoot -and (Test-Path -LiteralPath (Join-Path $ExistingRoot "steamapps") -PathType Container)) {
        return [PSCustomObject]@{ Root = $ExistingRoot; Source = "kept"; WriteToEnv = $true
            SteamappsFound = $true; RegistryCandidate = $candidate }
    }
    $defaultFound = [bool](Test-Path -LiteralPath (Join-Path $DefaultRoot "steamapps") -PathType Container)
    return [PSCustomObject]@{ Root = $DefaultRoot; Source = "default"; WriteToEnv = $false
        SteamappsFound = $defaultFound; RegistryCandidate = $candidate }
}

# ---- validate inputs -------------------------------------------------
#
# NOTE: $ErrorActionPreference is deliberately left at its default
# ("Continue") through this whole validation block, and only set to
# "Stop" afterwards (see below). Write-Error under "Continue" writes to
# the error stream and returns control to the next statement, so the
# `exit 2` right after it actually runs. Under "Stop" (the original
# version of this script set it at the very top), Write-Error becomes a
# TERMINATING error instead -- the script unwinds immediately and NONE of
# these `exit 2` lines are ever reached; PowerShell then reports exit
# code 1 (its generic "script terminated by an uncaught error" code) for
# every one of the four usage errors below instead of the documented 2.
# Measured directly during a WP 2.6 review round: all four paths returned
# exit 1, not 2, with $ErrorActionPreference = "Stop" set up front.
# Pinned by the harness's "usage error exits with code 2" check.

$resolvedAgentPath = Resolve-Path -LiteralPath $AgentPath -ErrorAction SilentlyContinue
if (-not $resolvedAgentPath) {
    Write-Error "AgentPath '$AgentPath' does not exist."
    exit 2
}
$AgentPath = $resolvedAgentPath.Path

$envFilePath = Join-Path $ConfigDir "env.txt"

# WP AGENT-FIX-2: a re-install (e.g. only to change -LibraryRoot) may omit
# both key parameters when the existing env.txt already holds a non-empty
# VAULT_AGENT_API_KEY; that value is reused as-is and never printed. With
# neither parameter and no such key, this is the same usage error as before.
$haveApiKey = [bool]$PSBoundParameters.ContainsKey("ApiKey")
$haveApiKeyFile = [bool]$PSBoundParameters.ContainsKey("ApiKeyFile")
$apiKeySource = $null
if ($haveApiKey -and $haveApiKeyFile) {
    Write-Error "Specify exactly one of -ApiKey or -ApiKeyFile, not both."
    exit 2
}
if (-not $haveApiKey -and -not $haveApiKeyFile) {
    $existingApiKey = Get-EnvFileValue -Path $envFilePath -Key "VAULT_AGENT_API_KEY"
    if ([string]::IsNullOrWhiteSpace($existingApiKey)) {
        Write-Error "Specify one of -ApiKey or -ApiKeyFile."
        exit 2
    }
    $resolvedApiKey = $existingApiKey
    $apiKeySource = "kept from existing env.txt"
} elseif ($haveApiKeyFile) {
    if (-not (Test-Path -LiteralPath $ApiKeyFile -PathType Leaf)) {
        Write-Error "ApiKeyFile '$ApiKeyFile' does not exist."
        exit 2
    }
    $resolvedApiKey = (Get-Content -LiteralPath $ApiKeyFile -Raw).Trim()
    $apiKeySource = "from -ApiKeyFile"
} else {
    $resolvedApiKey = $ApiKey
    $apiKeySource = "from -ApiKey"
}

if ([string]::IsNullOrWhiteSpace($resolvedApiKey)) {
    Write-Error "Resolved API key is empty."
    exit 2
}

if ($IntervalMinutes -lt 1 -or $IntervalMinutes -gt 1440) {
    Write-Error "IntervalMinutes must be between 1 and 1440 (one day)."
    exit 2
}

if (-not $LogFile) {
    $LogFile = Join-Path $ConfigDir "vault-agent.log"
}

# WP AGENT-FIX-2 (review S1): a re-install must not silently change this
# PC's identity. Explicit -ClientId wins; otherwise a non-empty
# VAULT_AGENT_CLIENT_ID from the existing env.txt is carried over (a
# dropped id would make vault-agent fall back to the hostname-derived one
# and leave a ghost row, see agent/README.md "Client identity and
# renaming"); otherwise none, as before.
$effectiveClientId = $null
$clientIdSource = $null
if ($ClientId) {
    $effectiveClientId = $ClientId
    $clientIdSource = "explicit"
} else {
    $existingClientId = Get-EnvFileValue -Path $envFilePath -Key "VAULT_AGENT_CLIENT_ID"
    if (-not [string]::IsNullOrWhiteSpace($existingClientId)) {
        $effectiveClientId = $existingClientId
        $clientIdSource = "kept"
    }
}

# WP AGENT-FIX-2: explicit -LibraryRoot > HKCU\Software\Valve\Steam
# SteamPath (only if it contains steamapps\) > VAULT_AGENT_LIBRARY_ROOT
# kept from the existing env.txt (only if it contains steamapps\) >
# vault-agent's own Windows default. The default literal mirrors
# go/agentconfig's defaultLibraryRoot("windows") exactly - keep the two in
# sync.
$defaultLibraryRoot = "C:\Program Files (x86)\Steam"
$registrySteamPath = $null
$existingLibraryRoot = $null
if (-not $LibraryRoot) {
    $registrySteamPath = Get-RegistrySteamPath
    $existingLibraryRoot = Get-EnvFileValue -Path $envFilePath -Key "VAULT_AGENT_LIBRARY_ROOT"
}
$libraryRootChoice = Resolve-LibraryRoot -ExplicitRoot $LibraryRoot -RegistrySteamPath $registrySteamPath `
    -DefaultRoot $defaultLibraryRoot -ExistingRoot $existingLibraryRoot

$runnerDestPath = Join-Path $ConfigDir "run-vault-agent.ps1"
$runnerSourcePath = Join-Path $PSScriptRoot "run-vault-agent.ps1"

if (-not (Test-Path -LiteralPath $runnerSourcePath -PathType Leaf)) {
    Write-Error "run-vault-agent.ps1 not found next to this script ($runnerSourcePath)."
    exit 2
}

# ---- ACL advisories (warn, never abort) --------------------------------
# WP SEC-FIX-2 (S6): the task runs AgentPath as this user, so anyone who can
# replace that binary runs code as this user; and the key file holds the API
# key. Both are checked against the broad well-known groups by SID (locale-
# independent): Everyone, Authenticated Users, BUILTIN\Users. Only Allow
# rules that apply to the object itself count (inherit-only rules do not).
# Generic-rights bits are not decoded; this is an advisory, not a proof.

function Get-BroadGroupRights {
    param([string]$Path, [int]$RightsMask)
    $broadSids = @("S-1-1-0", "S-1-5-11", "S-1-5-32-545")
    $hits = @()
    try {
        $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
        $rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
    } catch {
        Write-Warning "Could not read the ACL of '$Path' ($($_.Exception.Message)); skipping its permission check."
        return @()
    }
    foreach ($rule in $rules) {
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { continue }
        if (($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
        if ($broadSids -notcontains $rule.IdentityReference.Value) { continue }
        if (([int]$rule.FileSystemRights -band $RightsMask) -eq 0) { continue }
        $name = $rule.IdentityReference.Value
        try { $name = $rule.IdentityReference.Translate([System.Security.Principal.NTAccount]).Value } catch { }
        $hits += $name
    }
    return @($hits | Select-Object -Unique)
}

# WriteData(2) | AppendData(4) | DeleteSubdirectoriesAndFiles(64) |
# Delete(65536) | WriteDAC(262144) | WriteOwner(524288)
$modifyMask = 2 + 4 + 64 + 65536 + 262144 + 524288
# ReadData(1)
$readMask = 1

$agentDir = Split-Path -Parent $AgentPath
foreach ($target in @($AgentPath, $agentDir)) {
    $who = @(Get-BroadGroupRights -Path $target -RightsMask $modifyMask)
    if ($who.Count -gt 0) {
        Write-Warning ("'$target' can be modified by: " + ($who -join ", ") + ". Any local account " +
            "could replace the binary this task runs as you. Move vault-agent.exe to " +
            "$env:LOCALAPPDATA\VaultAgent\ (folders created directly under C:\ inherit write " +
            "access for Authenticated Users).")
    }
}
if ($haveApiKeyFile) {
    $who = @(Get-BroadGroupRights -Path $ApiKeyFile -RightsMask $readMask)
    if ($who.Count -gt 0) {
        Write-Warning ("ApiKeyFile '$ApiKeyFile' is readable by: " + ($who -join ", ") + ". Keep the " +
            "key file under your user profile and delete it after this install.")
    }
}

# All inputs validated -- from here on, an unexpected failure (a mutating
# filesystem/registry/Task Scheduler operation going wrong) SHOULD stop
# the script hard rather than limping forward with a half-applied state.
$ErrorActionPreference = "Stop"

# ---- ConfigDir ---------------------------------------------------------

if (-not (Test-Path -LiteralPath $ConfigDir)) {
    if ($PSCmdlet.ShouldProcess($ConfigDir, "Create config directory")) {
        New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    }
}

# ---- secret env file: ACL BEFORE content, never after ------------------
#
# See docs/LEARNINGS.md "systemd / packaging": "Secret env files: umask 077
# BEFORE creating, not chmod 600 after (world-readable window)." Windows has
# no umask; the equivalent is creating the file empty, replacing its ACL
# with an owner-only rule (inheritance disabled) FIRST, and only then
# writing the real content with Set-Content.

if ($PSCmdlet.ShouldProcess($envFilePath, "Create/lock down secret env file")) {
    if (-not (Test-Path -LiteralPath $envFilePath)) {
        New-Item -ItemType File -Path $envFilePath -Force | Out-Null
    }

    # icacls, not the Set-Acl cmdlet: both `New-Object FileSecurity` from
    # scratch AND a `Get-Acl` -> modify -> `Set-Acl` round trip fail with
    # "SeSecurityPrivilege" for a standard (non-admin) user THE SECOND TIME
    # they run against a file whose ACL is already protected (inheritance
    # already disabled) -- verified empirically during WP 2.6's own harness
    # run (idempotent-reinstall step): install works once, re-install then
    # fails on this exact line. This is a documented .NET FileSystemSecurity
    # quirk (Persist() decides to write SACL information once the DACL is
    # already protected, and writing SACL info needs a privilege a standard
    # user does not hold) with no reliable workaround inside
    # System.Security.AccessControl itself. icacls.exe does not go through
    # that .NET path and was re-verified idempotent (3 repeated calls, same
    # result each time) during the same harness run.
    #   /inheritance:r  -- strip inherited ACEs, mark the ACL protected
    #   /grant:r <user>:(F) -- grant that user FullControl, REPLACING (:r)
    #                          any existing explicit grant for them, so
    #                          repeated calls converge instead of stacking
    # Net result after this line: exactly one explicit ACE (current user,
    # FullControl), inheritance disabled -- equivalent to Unix mode 600.
    $currentUser = "$env:USERDOMAIN\$env:USERNAME"
    icacls $envFilePath /inheritance:r /grant:r "${currentUser}:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "icacls failed to lock down '$envFilePath' (exit $LASTEXITCODE)"
    }

    $envLines = New-Object System.Collections.Generic.List[string]
    $envLines.Add("VAULT_AGENT_SERVER_URL=$ServerUrl")
    $envLines.Add("VAULT_AGENT_API_KEY=$resolvedApiKey")
    # WP AGENT-FEAT-1: the agent states this interval in every report, so
    # vault-api can tell online from offline. Must match the repetition
    # trigger below.
    $envLines.Add("VAULT_AGENT_REPORT_INTERVAL=${IntervalMinutes}m")
    if ($effectiveClientId) { $envLines.Add("VAULT_AGENT_CLIENT_ID=$effectiveClientId") }
    if ($libraryRootChoice.WriteToEnv) { $envLines.Add("VAULT_AGENT_LIBRARY_ROOT=$($libraryRootChoice.Root)") }

    # -Encoding utf8 explicitly: Set-Content otherwise defaults to the
    # system ANSI codepage on PowerShell 5.1 (docs/LEARNINGS.md).
    Set-Content -LiteralPath $envFilePath -Value $envLines -Encoding utf8
}

# ---- deploy the wrapper script ------------------------------------------

if ($PSCmdlet.ShouldProcess($runnerDestPath, "Deploy run-vault-agent.ps1")) {
    Copy-Item -LiteralPath $runnerSourcePath -Destination $runnerDestPath -Force
}

# ---- Scheduled Task ------------------------------------------------------

$powershellExe = Join-Path $PSHOME "powershell.exe"
$taskArgument = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden " +
    "-File `"$runnerDestPath`" -AgentPath `"$AgentPath`" -EnvFile `"$envFilePath`" -LogFile `"$LogFile`""

if ($PSCmdlet.ShouldProcess($TaskName, "Register/update Scheduled Task")) {
    $action = New-ScheduledTaskAction -Execute $powershellExe -Argument $taskArgument

    $userId = "$env:USERDOMAIN\$env:USERNAME"

    # Trigger 1 (keep it first: the harness and the README read
    # Triggers[0] as the repetition trigger): every -IntervalMinutes.
    $startTime = (Get-Date).AddMinutes(1)
    $trigger = New-ScheduledTaskTrigger -Once -At $startTime `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    # Trigger 2 (WP AGENT-FEAT-1): at logon of the installing user, the same
    # account the Interactive principal runs as, so the PC shows online
    # right after logon instead of up to one interval later. No delay: the
    # agent retries for up to about 105 s while the network comes up.
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userId

    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

    # -StartWhenAvailable is the closest Windows equivalent of the systemd
    # timer's Persistent=true (WP 2.5): a run missed while the machine was
    # off/asleep fires as soon as the task becomes available again, instead
    # of silently waiting for the next on-schedule slot.
    # -MultipleInstances IgnoreNew: a logon run and a repetition run that
    # fall together do not overlap; the second one is skipped.
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($trigger, $logonTrigger) `
        -Principal $principal -Settings $settings -Force | Out-Null
}

# ---- summary --------------------------------------------------------------

Write-Host ""
Write-Host "vault-agent Scheduled Task installed/updated:"
Write-Host "  Task name       : $TaskName"
Write-Host "  Agent binary    : $AgentPath"
Write-Host "  Runs            : at logon, and every $IntervalMinutes minute(s) starting ~1 minute from now"
Write-Host "  Online/offline  : reports state VAULT_AGENT_REPORT_INTERVAL=${IntervalMinutes}m; vault-api shows"
Write-Host "                    this PC offline after 2 x $IntervalMinutes + 5 minutes without a report"
Write-Host "  Config dir      : $ConfigDir"
Write-Host "  Secret env file : $envFilePath (owner-only ACL, contains VAULT_AGENT_API_KEY)"
# Only where the key came from, never the key itself.
Write-Host "  API key         : $apiKeySource"
Write-Host "  Wrapper script  : $runnerDestPath"
Write-Host "  Log file        : $LogFile"

# ---- client id visibility (WP AG-0) --------------------------------------
#
# Deliberately does NOT re-derive vault-agent's sanitized hostname here --
# see -ClientId's parameter doc for why a second, drifting implementation of
# go/agentconfig's rules would be worse than not previewing at all. This
# either states the explicit value given, or shows the raw hostname plus an
# honest "this is a preview, the agent's own log is authoritative" caveat.
#
# [System.Net.Dns]::GetHostName() is used here deliberately, NOT
# $env:COMPUTERNAME (review round 1, WP AG-0 S2): COMPUTERNAME is uppercased
# by Windows (e.g. a machine named "Demon" reports "DEMON"), while
# go/agentconfig reads os.Hostname(), which preserves real case on Windows
# just like this call and hostname.exe do -- see -ClientId's parameter doc
# for the measured proof and why case matters here (client_id is a
# case-sensitive persisted identity key).
if ($ClientId) {
    Write-Host "  Client id       : $ClientId (explicit -ClientId, passed to the agent as"
    Write-Host "                    VAULT_AGENT_CLIENT_ID -- so vault-agent's own log will show"
    Write-Host "                    client_id_source=env, not =flag)"
} elseif ($clientIdSource -eq "kept") {
    Write-Host "  Client id       : $effectiveClientId (kept from existing env.txt; pass -ClientId"
    Write-Host "                    to change it -- see 'Client identity and renaming' in"
    Write-Host "                    agent/README.md before you do)"
} else {
    $hostnamePreview = [System.Net.Dns]::GetHostName()
    Write-Host "  Client id       : not given -> vault-agent will derive one from this machine's"
    Write-Host "                    hostname ('$hostnamePreview'), sanitized to fit its rules"
    Write-Host "                    (non-printable characters replaced, truncated to 64 chars)."
    Write-Host "                    Pass -ClientId to choose a different one explicitly, or check"
    Write-Host "                    $LogFile after the first run for the exact value vault-agent"
    Write-Host "                    resolved (it logs client_id / client_id_source / client_id_note)."
}

# ---- library root visibility (WP AGENT-FIX-1, S1) -------------------------
#
# A wrong library root is the one misconfiguration vault-agent could not
# tell from "nothing installed" until WP AGENT-FIX-1: the agent now
# refuses to post when no steamapps\ directory is readable, so surface the
# most likely cause HERE, at install time, instead of in a log nobody reads
# until the games vanish from the server. Since WP AGENT-FIX-2 the root is
# resolved during validation above (explicit > registry SteamPath > kept
# from env.txt > default, see Resolve-LibraryRoot); this block only reports
# the choice. Warn only, never abort: Steam may be installed after the
# agent.
if ($libraryRootChoice.Source -eq "explicit") {
    Write-Host "  Library root    : $($libraryRootChoice.Root) (explicit -LibraryRoot)"
} elseif ($libraryRootChoice.Source -eq "registry") {
    Write-Host "  Library root    : $($libraryRootChoice.Root) (from HKCU\Software\Valve\Steam\SteamPath,"
    Write-Host "                    steamapps\ found there; written to env.txt as VAULT_AGENT_LIBRARY_ROOT)"
} elseif ($libraryRootChoice.Source -eq "kept") {
    Write-Host "  Library root    : $($libraryRootChoice.Root) (kept from existing env.txt,"
    Write-Host "                    steamapps\ found there)"
} else {
    $defaultSteamapps = Join-Path $defaultLibraryRoot "steamapps"
    if ($libraryRootChoice.RegistryCandidate) {
        Write-Host "  Library root    : HKCU\Software\Valve\Steam\SteamPath points at"
        Write-Host "                    $($libraryRootChoice.RegistryCandidate), but it has no steamapps\ - ignored"
    }
    if ($libraryRootChoice.SteamappsFound) {
        Write-Host "  Library root    : not given -> vault-agent's Windows default"
        Write-Host "                    ($defaultLibraryRoot), steamapps\ found there."
    } else {
        Write-Host "  Library root    : not given -> vault-agent's Windows default"
        Write-Host "                    ($defaultLibraryRoot)"
        Write-Warning ("No steamapps directory at '$defaultSteamapps'. vault-agent will REFUSE to " +
            "report (exit 1) until the Steam install directory is known. Re-run this script " +
            "with -LibraryRoot <dir containing steamapps> (e.g. -LibraryRoot D:\Steam), or " +
            "install Steam there first.")
    }
}

Write-Host ""
Write-Host "Verify:  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Run now: Start-ScheduledTask -TaskName '$TaskName'"
