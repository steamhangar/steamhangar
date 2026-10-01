<#
.SYNOPSIS
    Registers a per-user Windows Scheduled Task that runs vault-agent.exe
    `report` every N minutes (default 30)  -  WP 2.6.

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
    Full path to vault-agent.exe.

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
    Mutually exclusive with -ApiKey. The file itself is only read, never
    copied or referenced by the installed task.

.PARAMETER ClientId
    Optional VAULT_AGENT_CLIENT_ID value. Omitted -> vault-agent defaults to
    the sanitized local hostname (go/agentconfig). Either way, this script's
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
    Optional VAULT_AGENT_LIBRARY_ROOT value. Omitted -> vault-agent's own
    Windows default (`C:\Program Files (x86)\Steam`). When omitted, this
    script checks whether that default actually contains a steamapps\
    directory and prints a loud warning if it does not (WP AGENT-FIX-1
    S1) - the install still proceeds, but vault-agent will then refuse to
    post (exit 1, "report refused" in the log) until -LibraryRoot points
    at the directory that contains steamapps\. No registry lookup is done
    (v1 scope); see agent/README.md's "Windows Scheduled Task" section.

.PARAMETER ConfigDir
    Directory this script owns: the env file, the deployed copy of
    run-vault-agent.ps1, and (if -LogFile is not overridden) the log file.
    Default: $env:LOCALAPPDATA\VaultAgent.

.PARAMETER TaskName
    Scheduled Task name. Default: VaultAgentReport. Re-running install with
    the same name UPDATES the existing task in place (idempotent) instead of
    creating a duplicate.

.PARAMETER IntervalMinutes
    Repetition interval in minutes. Default: 30, matching
    go/agentconfig.DefaultReportInterval and the systemd timer's
    OnCalendar=*:0/30.

.PARAMETER LogFile
    Optional override for the log file run-vault-agent.ps1 appends to.
    Default: <ConfigDir>\vault-agent.log.

.EXAMPLE
    .\install-task.ps1 -AgentPath C:\Tools\vault-agent.exe `
        -ServerUrl http://100.64.0.5:8080 -ApiKeyFile C:\secrets\key.txt

.EXAMPLE
    .\install-task.ps1 -AgentPath C:\Tools\vault-agent.exe `
        -ServerUrl http://100.64.0.5:8080 -ApiKeyFile C:\secrets\key.txt -WhatIf
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
    [int]$IntervalMinutes = 30,

    [Parameter(Mandatory = $false)]
    [string]$LogFile
)

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

$haveApiKey = [bool]$PSBoundParameters.ContainsKey("ApiKey")
$haveApiKeyFile = [bool]$PSBoundParameters.ContainsKey("ApiKeyFile")
if ($haveApiKey -and $haveApiKeyFile) {
    Write-Error "Specify exactly one of -ApiKey or -ApiKeyFile, not both."
    exit 2
}
if (-not $haveApiKey -and -not $haveApiKeyFile) {
    Write-Error "Specify one of -ApiKey or -ApiKeyFile."
    exit 2
}

if ($haveApiKeyFile) {
    if (-not (Test-Path -LiteralPath $ApiKeyFile -PathType Leaf)) {
        Write-Error "ApiKeyFile '$ApiKeyFile' does not exist."
        exit 2
    }
    $resolvedApiKey = (Get-Content -LiteralPath $ApiKeyFile -Raw).Trim()
} else {
    $resolvedApiKey = $ApiKey
}

if ([string]::IsNullOrWhiteSpace($resolvedApiKey)) {
    Write-Error "Resolved API key is empty."
    exit 2
}

if ($IntervalMinutes -lt 1) {
    Write-Error "IntervalMinutes must be >= 1."
    exit 2
}

if (-not $LogFile) {
    $LogFile = Join-Path $ConfigDir "vault-agent.log"
}

$envFilePath = Join-Path $ConfigDir "env.txt"
$runnerDestPath = Join-Path $ConfigDir "run-vault-agent.ps1"
$runnerSourcePath = Join-Path $PSScriptRoot "run-vault-agent.ps1"

if (-not (Test-Path -LiteralPath $runnerSourcePath -PathType Leaf)) {
    Write-Error "run-vault-agent.ps1 not found next to this script ($runnerSourcePath)."
    exit 2
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
    if ($ClientId) { $envLines.Add("VAULT_AGENT_CLIENT_ID=$ClientId") }
    if ($LibraryRoot) { $envLines.Add("VAULT_AGENT_LIBRARY_ROOT=$LibraryRoot") }

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

    $startTime = (Get-Date).AddMinutes(1)
    $trigger = New-ScheduledTaskTrigger -Once -At $startTime `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $userId = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

    # -StartWhenAvailable is the closest Windows equivalent of the systemd
    # timer's Persistent=true (WP 2.5): a run missed while the machine was
    # off/asleep fires as soon as the task becomes available again, instead
    # of silently waiting for the next on-schedule slot.
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
}

# ---- summary --------------------------------------------------------------

Write-Host ""
Write-Host "vault-agent Scheduled Task installed/updated:"
Write-Host "  Task name       : $TaskName"
Write-Host "  Agent binary    : $AgentPath"
Write-Host "  Interval        : every $IntervalMinutes minute(s), starting ~1 minute from now"
Write-Host "  Config dir      : $ConfigDir"
Write-Host "  Secret env file : $envFilePath (owner-only ACL, contains VAULT_AGENT_API_KEY)"
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
# until the games vanish from the server. The literal below mirrors
# go/agentconfig's defaultLibraryRoot("windows") exactly - keep the two in
# sync. Warn only, never abort: Steam may be installed after the agent.
if ($LibraryRoot) {
    Write-Host "  Library root    : $LibraryRoot (explicit -LibraryRoot)"
} else {
    $defaultLibraryRoot = "C:\Program Files (x86)\Steam"
    $defaultSteamapps = Join-Path $defaultLibraryRoot "steamapps"
    if (Test-Path -LiteralPath $defaultSteamapps -PathType Container) {
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
