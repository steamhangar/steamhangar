<#
.SYNOPSIS
    Wrapper invoked by the Scheduled Task created by install-task.ps1 (WP 2.6).

.DESCRIPTION
    This is the Windows equivalent of vault-agent-report.service's
    `EnvironmentFile=%h/.config/vault-agent/env` line (agent/packaging/systemd,
    WP 2.5): Windows Scheduled Tasks have no built-in "load an env file before
    running the action" feature, so this script does it by hand  -  read
    KEY=VALUE lines from -EnvFile, set them as process environment variables,
    then exec vault-agent.exe. VAULT_AGENT_API_KEY therefore never appears on
    the Scheduled Task's own command line (visible to any process that can
    list `schtasks /query`)  -  only this script's path and two plain
    filesystem paths (-AgentPath, -EnvFile) do.

    PowerShell 5.1 compatible per docs/LEARNINGS.md ("PowerShell 5.1"
    section): no && chains, no ternary operator, and native-command output is
    redirected with `*>>` (a direct stream-to-file redirect) rather than
    `2>&1` (which merges native stderr into the success pipeline as
    ErrorRecord objects and misbehaves under a Stop error-action preference).

.PARAMETER AgentPath
    Full path to vault-agent.exe.

.PARAMETER EnvFile
    Full path to the KEY=VALUE secret env file written by install-task.ps1
    (VAULT_AGENT_SERVER_URL, VAULT_AGENT_API_KEY,
    VAULT_AGENT_REPORT_INTERVAL (WP AGENT-FEAT-1), and optionally
    VAULT_AGENT_CLIENT_ID / VAULT_AGENT_LIBRARY_ROOT). Blank lines and lines
    starting with '#' are skipped.

.PARAMETER LogFile
    Optional path to append vault-agent's combined stdout/stderr to. Windows
    Scheduled Tasks have no equivalent of `journalctl --user -u ...`
    (systemd's built-in log), so this is how an operator gets the same
    "what did the last run print" story WP 2.5 gets for free. When omitted,
    output goes wherever the Task Scheduler action sends it (nowhere, by
    default).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AgentPath,

    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [Parameter(Mandatory = $false)]
    [string]$LogFile
)

function Write-LogLine {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    if ($LogFile) {
        Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
    }
}

if (-not (Test-Path -LiteralPath $AgentPath -PathType Leaf)) {
    Write-LogLine "ERROR: agent binary not found at '$AgentPath'"
    exit 1
}

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Write-LogLine "ERROR: env file not found at '$EnvFile'"
    exit 1
}

# Mirrors go/agentconfig's own stance exactly (config.go: "deliberately
# NOT trimmed beyond this: a key is opaque data, not text", applied there
# to VAULT_AGENT_API_KEY read from the process environment): the VALUE
# half of each KEY=VALUE line is opaque data and must survive byte-exact,
# never trimmed. Only the KEY half (an identifier, not a secret/opaque
# value) is trimmed. The blank-line/comment check below inspects a
# separately-trimmed copy for that purpose ONLY -- it is never used to
# derive $value, so trailing/leading whitespace that is genuinely part of
# a value (unusual, but not this script's call to silently discard) is
# preserved. An earlier version of this script trimmed the whole raw line
# BEFORE splitting on "=", which silently stripped trailing whitespace
# from the value -- inconsistent with the rule above.
$lines = Get-Content -LiteralPath $EnvFile
foreach ($rawLine in $lines) {
    $trimmedForBlankCommentCheck = $rawLine.Trim()
    if ($trimmedForBlankCommentCheck.Length -eq 0) { continue }
    if ($trimmedForBlankCommentCheck.StartsWith("#")) { continue }
    $eqIndex = $rawLine.IndexOf("=")
    if ($eqIndex -lt 1) { continue }
    $key = $rawLine.Substring(0, $eqIndex).Trim()
    $value = $rawLine.Substring($eqIndex + 1)
    Set-Item -Path "Env:$key" -Value $value
}

Write-LogLine "starting: $AgentPath report"

if ($LogFile) {
    & $AgentPath report *>> $LogFile
} else {
    & $AgentPath report
}
$exitCode = $LASTEXITCODE

Write-LogLine "finished: exit=$exitCode"
exit $exitCode
