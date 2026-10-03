<#
.SYNOPSIS
    Real-machine verification harness for install-task.ps1 / uninstall-task.ps1
    (WP 2.6). Not `go test` - run by hand, same convention as
    agent/tests/sandbox's hosts-mode scripts.

.DESCRIPTION
    Runs entirely under a throwaway TaskName/ConfigDir so it cannot collide
    with (or be confused with) a real install, and cleans up everything it
    creates, including on failure. No administrator rights are required -
    Scheduled Task registration for the current user with -LogonType
    Interactive does not need elevation (verified empirically; see
    install-task.ps1's own doc comment for the S4U-vs-Interactive finding).

    Covers, in order:
      1. Syntax-check install-task.ps1, uninstall-task.ps1, run-vault-agent.ps1
         ([scriptblock]::Create).
      2. -WhatIf leaves no trace (no task, no files).
      3. install creates: a Scheduled Task with the expected trigger
         (repetition interval / long duration), an action whose command line
         does NOT contain the API key anywhere, a secret env file with an
         owner-only ACL, and the deployed wrapper script.
      4. Starting the task actually runs vault-agent (via the wrapper) - the
         log file gets the expected "starting" / "finished" lines.
      5. Re-running install with different settings (idempotent update, not a
         duplicate): still exactly one task with that name, and its
         properties reflect the new settings.
      6. uninstall removes the task and the config files it owns.
      7. Re-running uninstall on an already-clean state exits gracefully
         (no throw), still exit 0.

    Requires a real windows/amd64 vault-agent.exe (cross-compiled per
    agent/README.md's "Cross-compile matrix" - a WSL2 build host was used
    during WP 2.6, since this repo's dev environment has no native Windows Go
    toolchain). It is never called against a real vault-api - ServerUrl below
    points at a closed local port so the one HTTP attempt fails fast; this
    harness only cares that the wrapper reaches the point of invoking it.

.PARAMETER AgentExe
    Path to a real windows/amd64 vault-agent.exe used only to prove the
    installed task actually launches vault-agent through the wrapper script.

.EXAMPLE
    .\test-install-uninstall.ps1 -AgentExe C:\path\to\vault-agent.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AgentExe
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$packagingDir = Split-Path -Parent $scriptDir
$installScript = Join-Path $packagingDir "install-task.ps1"
$uninstallScript = Join-Path $packagingDir "uninstall-task.ps1"
$runnerScript = Join-Path $packagingDir "run-vault-agent.ps1"

$TaskName = "SteamHangar-WP26-Harness-Test"
$ConfigDir = Join-Path $env:TEMP "vault-agent-wp26-harness"
$ApiKeyValue = "test-secret-api-key-do-not-leak-12345"
$ApiKeyFilePath = Join-Path $env:TEMP "vault-agent-wp26-harness-apikey.txt"
$ServerUrl = "http://127.0.0.1:1"   # closed port: fails fast, never a real server

$script:fails = 0
function Pass($m) { Write-Output "PASS  $m" }
function Fail($m) { Write-Output "FAIL  $m"; $script:fails++ }
function Check($label, $actual, $expected) {
    if ($actual -eq $expected) { Pass $label } else { Fail "$label -- got [$actual], want [$expected]" }
}
function CheckTrue($label, $condition) {
    if ($condition) { Pass $label } else { Fail $label }
}

function Get-TestTask {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Remove-TestArtifacts {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $ConfigDir) {
        # ACL may deny even the owner delete-without-take-ownership in edge
        # cases; reset before removing so cleanup cannot itself get stuck.
        Get-ChildItem -LiteralPath $ConfigDir -Force -ErrorAction SilentlyContinue | ForEach-Object {
            icacls $_.FullName /reset /Q | Out-Null
        }
        Remove-Item -LiteralPath $ConfigDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $ApiKeyFilePath) {
        Remove-Item -LiteralPath $ApiKeyFilePath -Force -ErrorAction SilentlyContinue
    }
}

# Always start clean, even if a previous run of this harness died mid-way.
Remove-TestArtifacts

try {
    # ---- 1. syntax check ----------------------------------------------
    foreach ($f in @($installScript, $uninstallScript, $runnerScript)) {
        try {
            [void][scriptblock]::Create((Get-Content -Raw $f))
            Pass "syntax OK: $(Split-Path -Leaf $f)"
        } catch {
            Fail "syntax check FAILED: $(Split-Path -Leaf $f) -- $($_.Exception.Message)"
        }
    }

    if (-not (Test-Path -LiteralPath $AgentExe -PathType Leaf)) {
        throw "AgentExe '$AgentExe' does not exist -- cross-compile it first (see agent/README.md)."
    }

    Set-Content -LiteralPath $ApiKeyFilePath -Value $ApiKeyValue -Encoding utf8 -NoNewline

    # ---- 1b. a usage error genuinely exits 2, not 1 -----------------------
    #
    # install-task.ps1 has an `exit 2` statement on every validation-failure
    # path. Calling the script in-process (`& $installScript ...`, as the
    # rest of this harness does below) would be the wrong way to check this:
    # `exit` inside a script invoked with `&` terminates the CURRENT
    # PowerShell process, not just the called script -- it would kill this
    # harness itself the moment install-task.ps1 hit its first `exit 2`.
    # A real child process is required so $LASTEXITCODE reflects only that
    # process. This is also the exact shape of the review finding this
    # check pins: an earlier version set $ErrorActionPreference = "Stop"
    # BEFORE input validation, which turned every Write-Error into a
    # terminating error -- the script unwound before ever reaching its own
    # `exit 2` lines, and PowerShell reported exit code 1 (its generic
    # uncaught-error code) for all four usage-error paths instead of 2.
    $usageErrOut = [System.IO.Path]::GetTempFileName()
    $usageErrErr = [System.IO.Path]::GetTempFileName()
    $usageErrorProc = Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installScript,
        "-AgentPath", "$env:TEMP\definitely-does-not-exist-vault-agent.exe",
        "-ServerUrl", $ServerUrl, "-ApiKey", "irrelevant-for-this-check"
    ) -NoNewWindow -Wait -PassThru -RedirectStandardOutput $usageErrOut -RedirectStandardError $usageErrErr
    Check "usage error (missing AgentPath) exits with code 2" $usageErrorProc.ExitCode 2
    Remove-Item $usageErrOut, $usageErrErr -Force -ErrorAction SilentlyContinue

    # ---- 2. -WhatIf leaves no trace -------------------------------------
    #
    # *>&1 merges Write-Host's Information-stream output into the success
    # stream so it can be captured as text -- this is a PowerShell SCRIPT
    # being invoked, not a native executable, so this does not hit the
    # "2>&1 wraps native stderr in NativeCommandError" pitfall
    # docs/LEARNINGS.md warns about (that is specific to native commands'
    # own stderr; install-task.ps1's internal icacls call handles its own
    # exit code, it does not rely on stream redirection).
    $whatIfOutput = & $installScript -AgentPath $AgentExe -ServerUrl $ServerUrl -ApiKeyFile $ApiKeyFilePath `
        -ConfigDir $ConfigDir -TaskName $TaskName -IntervalMinutes 30 -WhatIf *>&1 | Out-String

    CheckTrue "-WhatIf: no task registered" ((Get-TestTask) -eq $null)
    CheckTrue "-WhatIf: no env file written" (-not (Test-Path -LiteralPath (Join-Path $ConfigDir "env.txt")))

    # ---- 2b. client-id preview when -ClientId is OMITTED (WP AG-0) --------
    #
    # No -ClientId was passed above -- the summary must plainly say a
    # hostname-derived id will be used, name the real hostname, and point at
    # -ClientId / the agent's own log as the ways to get the exact value.
    # -WhatIf is enough for this: the summary block always prints,
    # regardless of ShouldProcess (nothing here depends on a filesystem
    # mutation), so this is $0 extra cost against the harness's TaskName.
    #
    # S2 (review round 1): the preview must read [System.Net.Dns]::
    # GetHostName(), NOT $env:COMPUTERNAME -- Windows uppercases
    # COMPUTERNAME, but go/agentconfig's os.Hostname() preserves real case,
    # so a COMPUTERNAME-based preview can show a DIFFERENT string than the
    # id vault-agent actually resolves. -like/-notlike are CASE-INSENSITIVE
    # in PowerShell, so they cannot tell "DEMON" from "Demon" -- this
    # regression needs -clike/-cnotlike (case-sensitive) or it silently
    # passes either way, which is exactly how this defect shipped unnoticed
    # in round 1.
    $dnsHostName = [System.Net.Dns]::GetHostName()
    CheckTrue "client-id preview shows the real hostname, EXACT case (S2 regression pin)" (
        $whatIfOutput -clike "*$dnsHostName*"
    )
    CheckTrue "client-id preview says a name will be derived" ($whatIfOutput -like "*derive*")
    CheckTrue "client-id preview names -ClientId as the override" ($whatIfOutput -like "*-ClientId*")
    if ($env:COMPUTERNAME -cne $dnsHostName) {
        # This host's COMPUTERNAME happens to differ in case from its DNS
        # hostname right now -- a case-SENSITIVE check that the wrong
        # (uppercased) form is absent is meaningful here. On a host where
        # they already match, this assertion would prove nothing either
        # way, so it is skipped rather than faked.
        CheckTrue "client-id preview does NOT show the case-mismatched `$env:COMPUTERNAME form" (
            $whatIfOutput -cnotlike "*$env:COMPUTERNAME*"
        )
    } else {
        Pass "client-id preview case-mismatch pin skipped (COMPUTERNAME already matches DNS hostname case on this host)"
    }

    # ---- 3. real install -------------------------------------------------
    $installOutput = & $installScript -AgentPath $AgentExe -ServerUrl $ServerUrl -ApiKeyFile $ApiKeyFilePath `
        -ClientId "wp26-harness" -ConfigDir $ConfigDir -TaskName $TaskName -IntervalMinutes 30 *>&1 | Out-String

    # ---- 3b. client-id preview when -ClientId is GIVEN (WP AG-0) ----------
    CheckTrue "client-id preview shows the explicit value" ($installOutput -like "*wp26-harness*")
    CheckTrue "client-id preview marks it explicit" ($installOutput -like "*explicit*")
    # N2 (review round 1): the preview must say the explicit value becomes
    # VAULT_AGENT_CLIENT_ID and that vault-agent's own log will therefore
    # show client_id_source=env (never =flag), so the -ClientId parameter
    # name and the agent's own log line don't read as contradicting each
    # other.
    CheckTrue "client-id preview says it becomes VAULT_AGENT_CLIENT_ID" ($installOutput -like "*VAULT_AGENT_CLIENT_ID*")
    CheckTrue "client-id preview flags the resulting client_id_source=env" ($installOutput -like "*client_id_source=env*")

    $task = Get-TestTask
    CheckTrue "task exists after install" ($task -ne $null)

    $action = $task.Actions[0]
    CheckTrue "action command line does not contain the API key" ($action.Arguments -notlike "*$ApiKeyValue*")
    CheckTrue "action command line references the wrapper script" ($action.Arguments -like "*run-vault-agent.ps1*")
    CheckTrue "action command line does not embed ServerUrl (paths only)" ($action.Arguments -notlike "*$ServerUrl*")

    $trigger = $task.Triggers[0]
    Check "trigger repetition interval" $trigger.Repetition.Interval "PT30M"
    CheckTrue "trigger repetition duration is long (catch-up window)" ($trigger.Repetition.Duration -like "P*")

    # WP AGENT-FEAT-1: a second trigger at logon of the installing user.
    Check "task has two triggers (repetition + logon)" @($task.Triggers).Count 2
    $logonTrigger = $task.Triggers[1]
    Check "second trigger is a logon trigger" $logonTrigger.CimClass.CimClassName "MSFT_TaskLogonTrigger"
    CheckTrue "logon trigger is for the installing user" ($logonTrigger.UserId -like "*$env:USERNAME")
    Check "MultipleInstances is IgnoreNew (logon and repetition never overlap)" "$($task.Settings.MultipleInstances)" "IgnoreNew"

    Check "principal logon type" $task.Principal.LogonType "Interactive"

    $settings = $task.Settings
    CheckTrue "StartWhenAvailable is enabled (Persistent= equivalent)" ($settings.StartWhenAvailable -eq $true)

    $envFilePath = Join-Path $ConfigDir "env.txt"
    CheckTrue "env file exists" (Test-Path -LiteralPath $envFilePath)
    $envContent = Get-Content -Raw -LiteralPath $envFilePath
    CheckTrue "env file contains the API key" ($envContent -like "*VAULT_AGENT_API_KEY=$ApiKeyValue*")
    CheckTrue "env file contains the server URL" ($envContent -like "*VAULT_AGENT_SERVER_URL=$ServerUrl*")
    CheckTrue "env file passes the interval to the agent (WP AGENT-FEAT-1)" ($envContent -like "*VAULT_AGENT_REPORT_INTERVAL=30m*")

    $acl = Get-Acl -LiteralPath $envFilePath
    CheckTrue "env file ACL: inheritance disabled" ($acl.AreAccessRulesProtected -eq $true)
    $badPrincipals = @("Everyone", "BUILTIN\Users", "NT AUTHORITY\Authenticated Users", "Jeder")
    $leaked = $acl.Access | Where-Object { $badPrincipals -contains $_.IdentityReference.Value }
    CheckTrue "env file ACL: no broad-group grant" (@($leaked).Count -eq 0)
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $ownerRule = $acl.Access | Where-Object { $_.IdentityReference.Value -eq $currentUser }
    CheckTrue "env file ACL: current user has an explicit rule" (@($ownerRule).Count -ge 1)

    $wrapperDestPath = Join-Path $ConfigDir "run-vault-agent.ps1"
    CheckTrue "wrapper script deployed" (Test-Path -LiteralPath $wrapperDestPath)

    # ---- 4. the task actually runs vault-agent via the wrapper ------------
    Start-ScheduledTask -TaskName $TaskName
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
    } while ((Get-TestTask).State -eq "Running" -and (Get-Date) -lt $deadline)

    $logFilePath = Join-Path $ConfigDir "vault-agent.log"
    CheckTrue "log file was written by the wrapper" (Test-Path -LiteralPath $logFilePath)
    if (Test-Path -LiteralPath $logFilePath) {
        $logContent = Get-Content -Raw -LiteralPath $logFilePath
        CheckTrue "log shows the wrapper invoked vault-agent report" ($logContent -like "*starting:*report*")
        CheckTrue "log shows the wrapper recorded a finish" ($logContent -like "*finished: exit=*")
        CheckTrue "log file does not contain the raw API key" ($logContent -notlike "*$ApiKeyValue*")
    }

    # ---- 5. idempotent re-install (update, not duplicate) ------------------
    & $installScript -AgentPath $AgentExe -ServerUrl "http://127.0.0.1:2" -ApiKeyFile $ApiKeyFilePath `
        -ClientId "wp26-harness-v2" -ConfigDir $ConfigDir -TaskName $TaskName -IntervalMinutes 15 | Out-Null

    $allTasks = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
    Check "exactly one task after re-install" $allTasks.Count 1

    $task2 = Get-TestTask
    Check "re-install updated trigger interval" $task2.Triggers[0].Repetition.Interval "PT15M"
    Check "re-install still has exactly two triggers" @($task2.Triggers).Count 2
    $envContent2 = Get-Content -Raw -LiteralPath $envFilePath
    CheckTrue "re-install updated the server URL" ($envContent2 -like "*VAULT_AGENT_SERVER_URL=http://127.0.0.1:2*")
    CheckTrue "re-install updated the client id" ($envContent2 -like "*VAULT_AGENT_CLIENT_ID=wp26-harness-v2*")
    CheckTrue "re-install updated the interval passed to the agent" ($envContent2 -like "*VAULT_AGENT_REPORT_INTERVAL=15m*")

    # ---- 6. uninstall removes exactly what install created ------------------
    & $uninstallScript -TaskName $TaskName -ConfigDir $ConfigDir | Out-Null

    CheckTrue "task removed after uninstall" ((Get-TestTask) -eq $null)
    CheckTrue "env file removed" (-not (Test-Path -LiteralPath $envFilePath))
    CheckTrue "wrapper script removed" (-not (Test-Path -LiteralPath $wrapperDestPath))
    CheckTrue "log file removed" (-not (Test-Path -LiteralPath $logFilePath))
    CheckTrue "empty config dir removed" (-not (Test-Path -LiteralPath $ConfigDir))

    # ---- 7. uninstall on an already-clean state is graceful -----------------
    $secondUninstallFailed = $false
    try {
        & $uninstallScript -TaskName $TaskName -ConfigDir $ConfigDir | Out-Null
    } catch {
        $secondUninstallFailed = $true
    }
    CheckTrue "second uninstall does not throw" (-not $secondUninstallFailed)
    Check "second uninstall exit code" $LASTEXITCODE 0

} finally {
    Remove-TestArtifacts
}

Write-Output ""
if ($script:fails -eq 0) {
    Write-Output "ALL CHECKS PASSED"
    exit 0
} else {
    Write-Output "$($script:fails) CHECK(S) FAILED"
    exit 1
}
