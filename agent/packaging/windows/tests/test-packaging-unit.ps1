<#
.SYNOPSIS
    Hermetic unit tests for the Windows packaging scripts (WP AGENT-FIX-2).
    Executed in CI (ci.yml, powershell-syntax job, Windows PowerShell 5.1).

.DESCRIPTION
    Unlike tests/test-install-uninstall.ps1 (real-machine harness, run by
    hand: it registers a real Scheduled Task), this script changes nothing
    outside one throwaway directory under %TEMP%, which it removes on every
    exit path. It never registers a task, never writes to the registry and
    never talks to a network. That is what makes it safe to execute on a
    CI runner and on a developer's own machine alike.

    Covers:
      1. install-task.ps1's helper functions, lifted out of the script by
         name through the PowerShell AST (the installer itself is NOT run
         for these):
           - ConvertTo-SteamLibraryRoot: the raw HKCU SteamPath ("c:/steam",
             forward slashes, any case) becomes a Windows path.
           - Resolve-LibraryRoot: explicit > registry SteamPath (only with
             steamapps\) > default, and only explicit/registry go to env.txt.
           - Get-RegistrySteamPath: never throws, whatever this host has.
           - Get-EnvFileValue: parses env.txt like run-vault-agent.ps1.
         The registry itself is deliberately NOT faked: writing a temp HKCU
         subkey on a shared machine is not acceptable, so the lookup is
         split into "read the raw value" (only checked for not throwing)
         and "decide from the raw string" (tested with temp directories).
      2. install-task.ps1 -WhatIf in a child powershell.exe (it calls
         `exit`, which would end this script if run in-process):
           - no key parameter + env.txt with VAULT_AGENT_API_KEY -> exit 0,
             summary says "kept from existing env.txt", key never printed;
             the client id and (without a usable registry value) the
             library root from env.txt are kept too (review S1);
           - no key parameter + no env.txt -> exit 2 (unchanged usage error).
         -WhatIf skips every mutation (config dir, env file, wrapper copy,
         task registration); only the summary prints.
      3. run-vault-agent.ps1 against a fake agent .exe (compiled here with
         Add-Type) that writes to stdout AND stderr, including a non-ASCII
         UTF-8 character and a last line without a newline: the log holds
         the agent's bytes unchanged, no NativeCommandError/RemoteException
         wrapping, no NUL bytes (no UTF-16), and the agent's exit code.

    Pure ASCII like every script under agent/packaging/windows
    (docs/LEARNINGS.md, PowerShell 5.1); the one non-ASCII test character
    is built with [char] at runtime.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$packagingDir = Split-Path -Parent $scriptDir
$installScript = Join-Path $packagingDir "install-task.ps1"
$runnerScript = Join-Path $packagingDir "run-vault-agent.ps1"

$script:fails = 0
$script:passes = 0
function Pass($m) { Write-Output "PASS  $m"; $script:passes++ }
function Fail($m) { Write-Output "FAIL  $m"; $script:fails++ }
function Check($label, $actual, $expected) {
    if ($actual -ceq $expected) { Pass $label } else { Fail "$label -- got [$actual], want [$expected]" }
}
function CheckTrue($label, $condition) {
    if ($condition) { Pass $label } else { Fail $label }
}

# Runs a .ps1 in a child Windows PowerShell and returns exit code plus the
# combined stdout/stderr text. Same pattern as the WP 2.6 harness's 1b check.
function Invoke-ChildScript {
    param([string]$ScriptPath, [string[]]$Arguments)
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $argList = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "`"$ScriptPath`"")
        foreach ($a in $Arguments) {
            if ($a.StartsWith("-")) { $argList += $a } else { $argList += "`"$a`"" }
        }
        $proc = Start-Process -FilePath (Join-Path $PSHOME "powershell.exe") -ArgumentList $argList `
            -NoNewWindow -Wait -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $text = [System.IO.File]::ReadAllText($outFile) + "`n" + [System.IO.File]::ReadAllText($errFile)
        return [PSCustomObject]@{ ExitCode = $proc.ExitCode; Output = $text }
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

$workDir = Join-Path $env:TEMP ("vault-agent-unit-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $workDir | Out-Null

try {
    # ---- 1. helper functions lifted from install-task.ps1 ---------------
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($installScript, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { throw "install-task.ps1 does not parse: $($parseErrors[0].Message)" }
    $wanted = @("Get-EnvFileValue", "Get-RegistrySteamPath", "ConvertTo-SteamLibraryRoot", "Resolve-LibraryRoot")
    $defs = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
    }, $true) | Where-Object { $wanted -contains $_.Name })
    foreach ($name in $wanted) {
        $def = @($defs | Where-Object { $_.Name -eq $name })
        if ($def.Count -ne 1) { throw "install-task.ps1 must define exactly one function '$name' (found $($def.Count))" }
        . ([scriptblock]::Create($def[0].Extent.Text))
    }
    Pass "helper functions found in install-task.ps1: $($wanted -join ', ')"

    # 1a. ConvertTo-SteamLibraryRoot
    Check "SteamPath 'c:/steam' (operator's real value) -> 'c:\steam'" (ConvertTo-SteamLibraryRoot -SteamPath "c:/steam") "c:\steam"
    Check "SteamPath with trailing slash" (ConvertTo-SteamLibraryRoot -SteamPath "C:/Program Files (x86)/Steam/") "C:\Program Files (x86)\Steam"
    Check "SteamPath with surrounding whitespace" (ConvertTo-SteamLibraryRoot -SteamPath "  d:/games/steam  ") "d:\games\steam"
    Check "SteamPath already with backslashes" (ConvertTo-SteamLibraryRoot -SteamPath "E:\Steam") "E:\Steam"
    Check "SteamPath bare drive keeps its root" (ConvertTo-SteamLibraryRoot -SteamPath "c:/") "c:\"
    CheckTrue "empty SteamPath -> null" ($null -eq (ConvertTo-SteamLibraryRoot -SteamPath ""))
    CheckTrue "null SteamPath -> null" ($null -eq (ConvertTo-SteamLibraryRoot -SteamPath $null))

    # 1b. Resolve-LibraryRoot with real temp directories
    $withSteamapps = Join-Path $workDir "SteamWith"
    New-Item -ItemType Directory -Path (Join-Path $withSteamapps "steamapps") | Out-Null
    $withoutSteamapps = Join-Path $workDir "SteamWithout"
    New-Item -ItemType Directory -Path $withoutSteamapps | Out-Null
    $missingDefault = Join-Path $workDir "NoDefault"
    # Raw registry form: forward slashes, lowercase - exactly how Steam
    # stores it ("c:/steam" on the operator's machine).
    $rawWith = $withSteamapps.Replace("\", "/").ToLowerInvariant()
    $rawWithout = $withoutSteamapps.Replace("\", "/").ToLowerInvariant()

    $r = Resolve-LibraryRoot -ExplicitRoot "" -RegistrySteamPath $rawWith -DefaultRoot $missingDefault
    Check "registry SteamPath with steamapps -> source registry" $r.Source "registry"
    Check "registry SteamPath is normalized to backslashes" $r.Root $withSteamapps.ToLowerInvariant()
    Check "registry SteamPath is written to env.txt" $r.WriteToEnv $true

    $r = Resolve-LibraryRoot -ExplicitRoot "X:\Explicit" -RegistrySteamPath $rawWith -DefaultRoot $missingDefault
    Check "explicit -LibraryRoot beats the registry" $r.Source "explicit"
    Check "explicit -LibraryRoot value is kept verbatim" $r.Root "X:\Explicit"
    Check "explicit -LibraryRoot is written to env.txt" $r.WriteToEnv $true

    $r = Resolve-LibraryRoot -ExplicitRoot "" -RegistrySteamPath $rawWithout -DefaultRoot $missingDefault
    Check "registry SteamPath without steamapps -> default" $r.Source "default"
    Check "default is not written to env.txt" $r.WriteToEnv $false
    Check "default without steamapps is reported as not found" $r.SteamappsFound $false
    Check "rejected registry path is kept for the summary" $r.RegistryCandidate $withoutSteamapps.ToLowerInvariant()

    $r = Resolve-LibraryRoot -ExplicitRoot "" -RegistrySteamPath $null -DefaultRoot $withSteamapps
    Check "no registry value -> default" $r.Source "default"
    Check "default with steamapps is reported as found" $r.SteamappsFound $true
    CheckTrue "no registry value -> no registry candidate" ($null -eq $r.RegistryCandidate)

    # Review S1: an existing env.txt root is kept only below the registry
    # and only if it has steamapps\.
    $r = Resolve-LibraryRoot -ExplicitRoot "" -RegistrySteamPath $null -DefaultRoot $missingDefault -ExistingRoot $withSteamapps
    Check "existing env.txt root with steamapps -> source kept" $r.Source "kept"
    Check "kept root value" $r.Root $withSteamapps
    Check "kept root is written to env.txt again" $r.WriteToEnv $true
    $r = Resolve-LibraryRoot -ExplicitRoot "" -RegistrySteamPath $rawWith -DefaultRoot $missingDefault -ExistingRoot $withoutSteamapps
    Check "registry beats a kept env.txt root" $r.Source "registry"
    $r = Resolve-LibraryRoot -ExplicitRoot "" -RegistrySteamPath $null -DefaultRoot $missingDefault -ExistingRoot $withoutSteamapps
    Check "kept env.txt root without steamapps -> default" $r.Source "default"
    $r = Resolve-LibraryRoot -ExplicitRoot "X:\Explicit" -RegistrySteamPath $null -DefaultRoot $missingDefault -ExistingRoot $withSteamapps
    Check "explicit beats a kept env.txt root" $r.Source "explicit"

    # 1c. Get-RegistrySteamPath never throws (value depends on this host)
    $threw = $false
    try { $raw = Get-RegistrySteamPath } catch { $threw = $true }
    CheckTrue "Get-RegistrySteamPath does not throw on this host" (-not $threw)
    CheckTrue "Get-RegistrySteamPath returns null or a string" (($null -eq $raw) -or ($raw -is [string]))

    # 1d. Get-EnvFileValue
    $envProbe = Join-Path $workDir "probe-env.txt"
    Set-Content -LiteralPath $envProbe -Encoding utf8 -Value @(
        "# comment",
        "",
        "VAULT_AGENT_SERVER_URL=http://127.0.0.1:1",
        "  VAULT_AGENT_API_KEY =first",
        "VAULT_AGENT_API_KEY=second value ",
        "BROKEN LINE WITHOUT EQUALS"
    )
    Check "env value: later line wins, value kept byte-exact" (Get-EnvFileValue -Path $envProbe -Key "VAULT_AGENT_API_KEY") "second value "
    Check "env value: plain key" (Get-EnvFileValue -Path $envProbe -Key "VAULT_AGENT_SERVER_URL") "http://127.0.0.1:1"
    CheckTrue "env value: missing key -> null" ($null -eq (Get-EnvFileValue -Path $envProbe -Key "VAULT_AGENT_CLIENT_ID"))
    CheckTrue "env value: missing file -> null" ($null -eq (Get-EnvFileValue -Path (Join-Path $workDir "nope.txt") -Key "VAULT_AGENT_API_KEY"))

    # ---- fake agent .exe (used by 2 and 3) ------------------------------
    # A folder name with a space (review N4): the real default lives under
    # paths like this, and every quoting step must survive it.
    $spaceDir = Join-Path $workDir "dir with space"
    New-Item -ItemType Directory -Path $spaceDir | Out-Null
    $fakeAgent = Join-Path $spaceDir "fake-agent.exe"
    $fakeSource = @'
using System;
using System.IO;
using System.Text;
public static class VaultFakeAgentAgentFix2 {
    static void W(Stream s, string text) {
        byte[] b = new UTF8Encoding(false).GetBytes(text);
        s.Write(b, 0, b.Length);
        s.Flush();
    }
    public static int Main(string[] args) {
        Stream o = Console.OpenStandardOutput();
        Stream e = Console.OpenStandardError();
        W(o, "fake-agent stdout args=" + string.Join(",", args) + "\n");
        W(e, "fake-agent stderr line 1\n");
        W(e, "fake-agent stderr caf\u00e9 line 2\n");
        W(e, "fake-agent server=" + Environment.GetEnvironmentVariable("VAULT_AGENT_SERVER_URL") + "\n");
        W(e, "fake-agent tail-without-newline");
        return 3;
    }
}
'@
    Add-Type -TypeDefinition $fakeSource -Language CSharp -OutputAssembly $fakeAgent -OutputType ConsoleApplication -IgnoreWarnings
    CheckTrue "fake agent compiled" (Test-Path -LiteralPath $fakeAgent -PathType Leaf)

    # ---- 2. install-task.ps1 -WhatIf: API key reuse ----------------------
    $secret = "unit-secret-agent-fix-2-do-not-print-4711"
    $reuseConfigDir = Join-Path $workDir "cfg-reuse"
    New-Item -ItemType Directory -Path $reuseConfigDir | Out-Null
    $reuseEnv = Join-Path $reuseConfigDir "env.txt"
    Set-Content -LiteralPath $reuseEnv -Encoding utf8 -Value @(
        "VAULT_AGENT_SERVER_URL=http://127.0.0.1:1",
        "VAULT_AGENT_API_KEY=$secret",
        "VAULT_AGENT_CLIENT_ID=unit-pc-kept-id",
        "VAULT_AGENT_LIBRARY_ROOT=$withSteamapps"
    )
    $envBefore = [System.IO.File]::ReadAllBytes($reuseEnv)

    # No -LibraryRoot / -ClientId on purpose: the registry lookup runs for
    # real on this host and must not break the install, whatever it finds;
    # the client id and (without a usable registry value) the library root
    # must be carried over from env.txt (review S1).
    $res = Invoke-ChildScript -ScriptPath $installScript -Arguments @(
        "-AgentPath", $fakeAgent, "-ServerUrl", "http://127.0.0.1:1",
        "-ConfigDir", $reuseConfigDir, "-TaskName", "SteamHangar-Unit-Never-Registered", "-WhatIf")
    Check "re-install without key parameters exits 0 when env.txt has a key" $res.ExitCode 0
    CheckTrue "summary says the API key was kept from env.txt" ($res.Output -like "*API key*: kept from existing env.txt*")
    CheckTrue "the reused API key is never printed" ($res.Output -notlike "*$secret*")
    CheckTrue "summary shows the kept client id (S1)" ($res.Output.Contains("Client id       : unit-pc-kept-id (kept from existing env.txt"))
    $hostRegistryRoot = ConvertTo-SteamLibraryRoot -SteamPath (Get-RegistrySteamPath)
    if ($hostRegistryRoot -and (Test-Path -LiteralPath (Join-Path $hostRegistryRoot "steamapps") -PathType Container)) {
        CheckTrue "registry beats the kept library root (this host has Steam)" ($res.Output.Contains("(from HKCU\Software\Valve\Steam\SteamPath"))
    } else {
        CheckTrue "summary shows the kept library root (S1)" ($res.Output.Contains("Library root    : $withSteamapps (kept from existing env.txt"))
    }
    $envAfter = [System.IO.File]::ReadAllBytes($reuseEnv)
    CheckTrue "-WhatIf left env.txt untouched" ([Convert]::ToBase64String($envBefore) -eq [Convert]::ToBase64String($envAfter))
    CheckTrue "-WhatIf registered no task" ($null -eq (Get-ScheduledTask -TaskName "SteamHangar-Unit-Never-Registered" -ErrorAction SilentlyContinue))

    $emptyConfigDir = Join-Path $workDir "cfg-empty"
    $res = Invoke-ChildScript -ScriptPath $installScript -Arguments @(
        "-AgentPath", $fakeAgent, "-ServerUrl", "http://127.0.0.1:1",
        "-ConfigDir", $emptyConfigDir, "-TaskName", "SteamHangar-Unit-Never-Registered", "-WhatIf")
    Check "no key parameter and no env.txt is still usage error 2" $res.ExitCode 2

    $res = Invoke-ChildScript -ScriptPath $installScript -Arguments @(
        "-AgentPath", $fakeAgent, "-ServerUrl", "http://127.0.0.1:1", "-ApiKey", "a", "-ApiKeyFile", $reuseEnv,
        "-ConfigDir", $reuseConfigDir, "-TaskName", "SteamHangar-Unit-Never-Registered", "-WhatIf")
    Check "both -ApiKey and -ApiKeyFile is still usage error 2" $res.ExitCode 2

    # ---- 3. run-vault-agent.ps1: plain UTF-8 log, agent exit code -------
    $runEnv = Join-Path $workDir "run-env.txt"
    Set-Content -LiteralPath $runEnv -Encoding utf8 -Value @(
        "VAULT_AGENT_SERVER_URL=http://127.0.0.1:1",
        "VAULT_AGENT_API_KEY=$secret"
    )
    $logFile = Join-Path $spaceDir "vault-agent.log"
    $res = Invoke-ChildScript -ScriptPath $runnerScript -Arguments @(
        "-AgentPath", $fakeAgent, "-EnvFile", $runEnv, "-LogFile", $logFile)
    Check "wrapper exits with the agent's exit code" $res.ExitCode 3
    CheckTrue "log file written" (Test-Path -LiteralPath $logFile -PathType Leaf)

    $logBytes = [System.IO.File]::ReadAllBytes($logFile)
    CheckTrue "log contains no NUL byte (no UTF-16 content)" (-not ($logBytes -contains [byte]0))
    $logText = (New-Object System.Text.UTF8Encoding($false)).GetString($logBytes)
    CheckTrue "log has no NativeCommandError wrapping" ($logText -notlike "*NativeCommandError*")
    CheckTrue "log has no RemoteException wrapping" ($logText -notlike "*RemoteException*")
    CheckTrue "log has no CategoryInfo wrapping" ($logText -notlike "*CategoryInfo*")
    CheckTrue "log has the wrapper's starting line" ($logText -like "*starting:*report*")
    CheckTrue "log has the agent's stdout, args passed" ($logText.Contains("fake-agent stdout args=report`n"))
    $expectedStderr = "fake-agent stderr line 1`nfake-agent stderr caf" + [char]0x00E9 + " line 2`n"
    CheckTrue "log has the agent's stderr lines unchanged and adjacent (UTF-8, no blank lines)" ($logText.Contains($expectedStderr))
    CheckTrue "agent saw the env file's variables" ($logText -like "*fake-agent server=http://127.0.0.1:1*")
    CheckTrue "output without a final newline is terminated before the next log line" ($logText.Contains("tail-without-newline`r`n["))
    CheckTrue "log has the finish line with the agent's exit code" ($logText -like "*finished: exit=3*")
    CheckTrue "log does not contain the API key" ($logText -notlike "*$secret*")
} finally {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output ""
if ($script:fails -eq 0) {
    Write-Output "ALL $($script:passes) CHECKS PASSED"
    exit 0
} else {
    Write-Output "$($script:fails) CHECK(S) FAILED ($($script:passes) passed)"
    exit 1
}
