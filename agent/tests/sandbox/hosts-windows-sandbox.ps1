<#
.SYNOPSIS
WP 2.3 - Windows verification of `vault-agent hosts` against SANDBOX hosts
fixtures.

.DESCRIPTION
Exercises the parts of hosts mode that only real Windows can answer: CRLF
preservation on a genuine Windows-shaped hosts file, and what os.Rename does
against ACL-restricted files (the evidence behind the in-place write fallback
documented in agent/go/hostsfile/write.go).

THE REAL SYSTEM HOSTS FILE IS NEVER WRITTEN. It is only hashed, at the start
and at the end, and the run fails if those hashes differ. Every fixture lives
under -LabDir.

No administrator rights are required: the ACL cases are built on files this
user owns, using icacls DENY entries against the user's own account.

.PARAMETER Exe
Path to a windows/amd64 vault-agent.exe. Cross-compile it with:
  CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -o vault-agent.exe ./cmd/vault-agent

.PARAMETER LabDir
Throwaway directory for the fixtures. Created if missing, and REMOVED again
when the run ends (including on failure) unless -KeepLab is given. Sections 2
and 3 leave DENY ACEs behind, so the cleanup resets the ACLs first -- without
that, the directory cannot be deleted from Explorer without a fight.

.PARAMETER KeepLab
Keep the lab directory for inspection. You will need
`icacls <LabDir> /reset /T /C /Q` before you can delete it yourself.

.EXAMPLE
.\hosts-windows-sandbox.ps1 -Exe C:\tmp\vault-agent.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$LabDir = (Join-Path $env:TEMP "vault-agent-hosts-sandbox"),
    [switch]$KeepLab
)

# docs/LEARNINGS.md (WP 1.8/0.6): `2>&1` on a native command wraps stderr in a
# NativeCommandError and kills the script under $ErrorActionPreference=Stop.
# Every agent invocation below therefore goes through Start-Process with
# stderr redirected to a FILE.
$ErrorActionPreference = "Continue"

$me   = "$env:USERDOMAIN\$env:USERNAME"
$real = "$env:SystemRoot\System32\drivers\etc\hosts"
$CacheIP = "10.0.0.50"
$CacheHost = "lancache.steamcontent.com"

$script:fails = 0
function Pass($m) { Write-Output "PASS  $m" }
function Fail($m) { Write-Output "FAIL  $m"; $script:fails++ }
function Check($label, $actual, $expected) {
    if ($actual -eq $expected) { Pass $label } else { Fail "$label -- got [$actual], want [$expected]" }
}
function Has($label, $text, $needle) {
    if ($text -like "*$needle*") { Pass $label } else { Fail "$label -- [$needle] not in output:`n$text" }
}
function Sha($p) { (Get-FileHash -Algorithm SHA256 $p).Hash }
function WriteFixture($path, $content) {
    [System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))
}

function RunAgent {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AgentArgs)
    $outF = [System.IO.Path]::GetTempFileName()
    $errF = [System.IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $Exe -ArgumentList $AgentArgs -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $outF -RedirectStandardError $errF
    $text = (Get-Content -Raw $outF) + (Get-Content -Raw $errF)
    Remove-Item $outF, $errF -Force
    return [pscustomobject]@{ Code = $p.ExitCode; Text = [string]$text }
}

if (-not (Test-Path $Exe)) { throw "vault-agent.exe not found at $Exe" }
New-Item -ItemType Directory -Force $LabDir | Out-Null

Write-Output "############################################################"
Write-Output "# vault-agent hosts - Windows sandbox (WP 2.3)"
Write-Output "# exe : $Exe"
Write-Output "# lab : $LabDir"
Write-Output "############################################################"

$realBefore = Sha $real
Write-Output "REAL system hosts file: $real"
Write-Output "REAL sha256 before    : $realBefore   (must be unchanged at the end)"

try {

# --- 1. CRLF fixture shaped like a genuine Windows hosts file ---------------
Write-Output "`n=== 1. CRLF fixture: apply / status / remove ==="
$f1 = Join-Path $LabDir "hosts-crlf"
WriteFixture $f1 ("# Copyright (c) 1993-2009 Microsoft Corp.`r`n#`r`n" +
    "#`t127.0.0.1       localhost`r`n#`t::1             localhost`r`n10.0.0.5`tnas.lan`r`n")
$sha1 = Sha $f1

$r = RunAgent hosts apply --cache-ip $CacheIP --hosts-path $f1
Check "apply exits 0" $r.Code 0
Write-Output $r.Text
Has "apply reports the transition" $r.Text "absent -> present-correct"
Has "apply used the atomic rename" $r.Text "write:      rename"

$text = [System.IO.File]::ReadAllText($f1)
Check "every line ending is CRLF (no lone LF introduced)" `
    ([regex]::Matches($text, "`r`n")).Count ([regex]::Matches($text, "`n")).Count
Has "the block is present" $text "$CacheIP $CacheHost"
Has "unrelated content survived verbatim" $text "10.0.0.5`tnas.lan"

$r = RunAgent hosts status --cache-ip $CacheIP --hosts-path $f1
Check "status exits 0" $r.Code 0
Has "status reports present-correct" $r.Text "state:      present-correct"
Has "status warns the resolver line describes the SYSTEM file" $r.Text "SYSTEM hosts file"

$r = RunAgent hosts remove --hosts-path $f1
Check "remove exits 0" $r.Code 0
Check "CRLF file is byte-identical after the round trip" (Sha $f1) $sha1

# --- 2. ACL-hardened: DELETE denied -> the in-place fallback must engage ----
# This is the shape security software gives the hosts file: an administrator
# may edit it, but nobody may replace or delete it. os.Rename cannot work
# here; the in-place write can.
Write-Output "`n=== 2. ACL-hardened file (DELETE denied, write allowed) ==="
$d2 = Join-Path $LabDir "acl"
New-Item -ItemType Directory -Force $d2 | Out-Null
$f2 = Join-Path $d2 "hosts"
WriteFixture $f2 "127.0.0.1`tlocalhost`r`n"
icacls $d2 /deny "${me}:(DC)" | Out-Null       # no delete-child on the parent
icacls $f2 /inheritance:d | Out-Null
icacls $f2 /remove:d "$me" | Out-Null          # drop the inherited deny from the file
icacls $f2 /deny "${me}:(DE)" | Out-Null       # ... but deny DELETE on the file
Write-Output ((icacls $f2) -join " ; ")

$r = RunAgent hosts apply --cache-ip $CacheIP --hosts-path $f2
Check "apply on an undeletable file still succeeds" $r.Code 0
Has "the in-place fallback was used" $r.Text "write:      in-place"
Has "the block was written" ([System.IO.File]::ReadAllText($f2)) "$CacheIP $CacheHost"
$r = RunAgent hosts remove --hosts-path $f2
Check "remove on an undeletable file succeeds" $r.Code 0
Check "content restored" ([System.IO.File]::ReadAllText($f2)) "127.0.0.1`tlocalhost`r`n"

# --- 3. fully write-denied -> permission error + Administrator hint ---------
Write-Output "`n=== 3. fully write-denied file ==="
$d3 = Join-Path $LabDir "denied"
New-Item -ItemType Directory -Force $d3 | Out-Null
$f3 = Join-Path $d3 "hosts"
WriteFixture $f3 "127.0.0.1`tlocalhost`r`n"
$sha3 = Sha $f3
icacls $d3 /deny "${me}:(WD,AD,DC)" | Out-Null
icacls $f3 /inheritance:d | Out-Null
icacls $f3 /deny "${me}:(WD,DE)" | Out-Null

$r = RunAgent hosts apply --cache-ip $CacheIP --hosts-path $f3
Check "apply exits 1" $r.Code 1
Write-Output $r.Text
Has "it names the permission problem" $r.Text "Access is denied"
Has "it gives the Administrator hint" $r.Text "Administrator"
Has "it repeats the exact command" $r.Text "hosts apply --cache-ip $CacheIP"
Has "it shows the elevated-terminal keystroke (not sudo)" $r.Text "Ctrl+Shift+Enter"
Check "the file was not modified" (Sha $f3) $sha3

# --- 4. corrupt markers are refused ----------------------------------------
Write-Output "`n=== 4. corrupt markers ==="
$f4 = Join-Path $LabDir "hosts-corrupt"
WriteFixture $f4 ("127.0.0.1 localhost`r`n" +
    "# BEGIN steamhangar-agent (managed block - do not edit inside)`r`n1.2.3.4 $CacheHost`r`n")
$sha4 = Sha $f4
$r = RunAgent hosts apply --cache-ip $CacheIP --hosts-path $f4
Check "apply on corrupt markers exits 1" $r.Code 1
Has "it refuses explicitly" $r.Text "Refusing to modify"
Check "the corrupt file was not touched" (Sha $f4) $sha4

# --- 5. a conflicting entry outside the block is refused --------------------
Write-Output "`n=== 5. conflicting entry outside the managed block ==="
$f5 = Join-Path $LabDir "hosts-conflict"
WriteFixture $f5 "127.0.0.1 localhost`r`n10.0.0.7 $CacheHost`r`n"
$sha5 = Sha $f5
$r = RunAgent hosts apply --cache-ip $CacheIP --hosts-path $f5
Check "apply with a conflicting entry exits 1" $r.Code 1
Has "it points at the conflicting line" $r.Text "line 2"
Check "the file was not touched" (Sha $f5) $sha5

# --- 6. the real system hosts file must be untouched ------------------------
Write-Output "`n=== 6. the real system hosts file ==="
Check "REAL hosts file is byte-identical (never written by this run)" (Sha $real) $realBefore

}
finally {
    # Sections 2 and 3 deliberately leave DENY ACEs on lab files and folders.
    # Reset them before deleting, otherwise Remove-Item cannot traverse or
    # delete what it just built (and the leftovers are awkward to clear by
    # hand afterwards). Runs even if the body above threw.
    Write-Output "`n=== cleanup ==="
    if (Test-Path $LabDir) {
        if ($KeepLab) {
            Write-Output "lab kept at $LabDir"
            Write-Output "  clear its DENY ACEs with: icacls `"$LabDir`" /reset /T /C /Q"
        } else {
            icacls $LabDir /reset /T /C /Q | Out-Null
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $LabDir
            if (Test-Path $LabDir) {
                Write-Output "WARN  could not fully remove $LabDir - clear it by hand"
            } else {
                Write-Output "lab directory removed: $LabDir"
            }
        }
    }
}

Write-Output "`n############################################################"
if ($script:fails -eq 0) {
    Write-Output "# ALL WINDOWS CHECKS PASSED"
} else {
    Write-Output "# $($script:fails) CHECK(S) FAILED"
}
Write-Output "############################################################"
exit $script:fails
