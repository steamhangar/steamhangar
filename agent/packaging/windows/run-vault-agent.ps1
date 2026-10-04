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
    section): no && chains, no ternary operator, and vault-agent's output
    never passes through a PowerShell stream redirection at all - see "How
    the agent's output reaches the log (WP AGENT-FIX-2)" below.

    ### How the agent's output reaches the log (WP AGENT-FIX-2)

    Up to rc9 this script ran `& $AgentPath report *>> $LogFile`. On
    Windows PowerShell 5.1 that had two defects, both seen in a real
    operator install (2026-10-04):
      - every stderr line of the native agent (and vault-agent logs to
        stderr) was turned into an ErrorRecord, so the log showed
        "+ CategoryInfo : NotSpecified: (...:String) [], RemoteException"
        and "+ FullyQualifiedErrorId : NativeCommandError" around each line;
      - `*>>` writes UTF-16LE ("Unicode") while Write-LogLine below appends
        UTF-8, so the file mixed encodings (NUL bytes, read back as blank
        lines between every line).
    `2>&1` had been avoided for the reason docs/LEARNINGS.md records: ANY
    redirection of a native command's stderr inside PowerShell creates
    those ErrorRecords, and under a Stop error-action preference the first
    one terminates the script.

    The fix takes PowerShell's stream machinery out of the path instead of
    working around it: Start-Process with -RedirectStandardOutput and
    -RedirectStandardError hands two temp files to the new process as its
    OS-level stdout/stderr handles. vault-agent writes its bytes straight
    into them; PowerShell never sees a stderr line, so no ErrorRecord can
    exist and the error-action preference does not matter for the call.
    The two files are then appended to the log byte for byte, so the log
    holds exactly what vault-agent printed (UTF-8, as Go writes it - game
    names with non-ASCII characters included, which a pipeline-based fix
    would have decoded through the console codepage). The exit code comes
    from the Process object (-Wait -PassThru), the same pattern the WP 2.6
    harness already relies on for install-task.ps1's exit code.

    Accepted trade-off: stdout and stderr are appended one after the other
    (stdout first), not interleaved. `vault-agent report` logs to stderr
    only (cmd/vault-agent main.go: log.New(stderr, ...)), so in practice
    the order is the order the agent printed. When -LogFile is omitted,
    the agent still runs as a plain `& $AgentPath report` (no redirection,
    exit code from $LASTEXITCODE).

.PARAMETER AgentPath
    Full path to vault-agent.exe.

.PARAMETER EnvFile
    Full path to the KEY=VALUE secret env file written by install-task.ps1
    (VAULT_AGENT_SERVER_URL, VAULT_AGENT_API_KEY,
    VAULT_AGENT_REPORT_INTERVAL (WP AGENT-FEAT-1), and optionally
    VAULT_AGENT_CLIENT_ID / VAULT_AGENT_LIBRARY_ROOT). Blank lines and lines
    starting with '#' are skipped.

.PARAMETER LogFile
    Optional path to append vault-agent's stdout and stderr to, as UTF-8
    (see above; a log written by rc9 or older may already hold UTF-16
    content, which stays as it is - new lines are appended in UTF-8). Windows
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

# Resolve a relative -LogFile against the PowerShell location once, so the
# .NET file calls below (which resolve against the process working
# directory instead) and Add-Content agree on the same file.
if ($LogFile) {
    $LogFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LogFile)
}

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

function Add-FileBytesToLog {
    # Appends a file's raw bytes to $LogFile unchanged (no decoding, no
    # re-encoding). Adds CRLF when the output does not end in a newline, so
    # the next "[timestamp] ..." line always starts on a line of its own.
    param([string]$SourcePath)
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) { return }
    $bytes = [System.IO.File]::ReadAllBytes($SourcePath)
    if ($bytes.Length -eq 0) { return }
    $stream = New-Object System.IO.FileStream($LogFile, [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        if ($bytes[$bytes.Length - 1] -ne 10) {
            $stream.Write([byte[]](13, 10), 0, 2)
        }
    } finally {
        $stream.Dispose()
    }
}

if ($LogFile) {
    # See "How the agent's output reaches the log (WP AGENT-FIX-2)" above.
    # The temp files live in the user's own %TEMP% (owner-only by default)
    # and are removed on every path.
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    $exitCode = $null
    try {
        try {
            $proc = Start-Process -FilePath $AgentPath -ArgumentList "report" -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -ErrorAction Stop
            $exitCode = $proc.ExitCode
        } catch {
            Write-LogLine "ERROR: could not start '$AgentPath': $($_.Exception.Message)"
            $exitCode = 1
        }
        Add-FileBytesToLog -SourcePath $stdoutFile
        Add-FileBytesToLog -SourcePath $stderrFile
    } finally {
        Remove-Item -LiteralPath $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $exitCode) {
        Write-LogLine "ERROR: vault-agent's exit code could not be read; reporting failure"
        $exitCode = 1
    }
} else {
    & $AgentPath report
    $exitCode = $LASTEXITCODE
}

Write-LogLine "finished: exit=$exitCode"
exit $exitCode
