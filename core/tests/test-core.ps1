<#
.SYNOPSIS
    vault-core (core/nginx/nginx.conf) automated test suite (WP 1.1).

.DESCRIPTION
    Runs core/nginx/nginx.conf natively on Windows (reusing the nginx.exe
    binary already fetched for the Phase-0 PoC at poc/nginx/nginx.exe -- see
    core/README.md for why no separate download/binary lives under core/
    yet) against the REAL Steam CDN, and asserts every ADR-0001 production
    requirement this work package implements:

      1. smoke            - MISS -> store -> HIT, byte-identical (SHA256)
      2. heartbeat         - /lancache-heartbeat: 200 + X-LanCache-Processed-By
      3. range-strip guard - cold object + Range header -> STORED file is
                             still the complete object; documents what the
                             CLIENT received (see console/PASS output)
      4. nocache bypass    - warm object + ?nocache=1 -> upstream re-contacted
                             (log evidence), stored file remains valid
      5. host allowlist    - off-list Host -> rejected (4xx), no open proxy
      6. host pass-through - on-list Host -> proxied to that real edge (200)

    Plus three regressions added after a reviewer-found FAIL on this WP:
      7. B1 retry regression   - a local throwaway two-backend rig (never
                                  the real CDN, which can't be made to fail
                                  on demand) reproduces upstream_status
                                  becoming a comma list ("502, 200") when
                                  proxy_next_upstream retries, and asserts
                                  the eventually-successful body still gets
                                  stored (core/tests/fixtures/retry-regression.conf)
      8. S2 gzip regression    - Accept-Encoding: gzip MISS then HIT, both
                                  byte-identical to ground truth (proves
                                  upstream never sends a raw gzip body that
                                  would land on disk with no Content-Encoding
                                  to say so)
      9. S3 temp-path regression - GET /tmp/proxy/... returns 404, not an
                                  in-flight (or leftover) temp file's bytes

    Plus five test groups for the WP 3.10 (ADR-0008) cache-event log
    (core/README.md "Cache-event log" has the full field spec/reasoning):
      10. MISS-then-HIT          - a MISS writes a tab-separated v1 line
                                  with the depot id and a positive byte
                                  count; the follow-up warm request writes
                                  a HIT line
      11. nocache=1 BYPASS       - a ?nocache=1 request is typed BYPASS,
                                  not MISS, and its logged uri excludes the
                                  query string
      12. heartbeat exclusion    - /lancache-heartbeat produces ZERO
                                  cache-event log lines (the location never
                                  declares the directive)
      13. hostile URI            - a path containing percent-encoded
                                  tab/quote/newline/non-ASCII produces
                                  exactly one well-formed 9-field line with
                                  the control characters escaped as \xXX,
                                  not raw
      14. feature off            - a fixture config with the directive
                                  entirely absent (fixtures/eventlog-off.conf)
                                  stays `nginx -t` green and creates no
                                  event.log file

    Plus one group for the WP TH-1a upstream rate cap (core/README.md
    "Upstream rate cap"); the cap itself is container-only, this checks the
    native build can carry it:
      15. cap prerequisites      - `nginx -V` lists the stub_status module
                                  ($connections_writing, the divisor) and
                                  the version is >= 1.27.0 (variables in
                                  proxy_limit_rate); the static native
                                  include is present and uncapped

    IMPORTANT -- port 80 contention: a live Steam client may be using the
    Phase-0 PoC's nginx on port 80. This script:
      1. Stops whatever nginx is currently running (poc/stop.ps1 -- safe/
         idempotent if nothing is running).
      2. Starts core/nginx/nginx.conf on port 80 for the duration of the
         test run only.
      3. In a `finally` block (runs even on failure/Ctrl+C): stops the core
         nginx instance, then restarts the PoC's own nginx via
         poc/start.ps1, so the machine is left exactly as this script found
         it -- PoC nginx serving port 80 with poc/conf/nginx.conf, as
         required by the WP 1.1 scope note. poc/ itself is never modified.

    Every /depot/ request in this suite deliberately sends an explicit Host
    header naming a real Steam CDN hostname (see $ValidHost/$PassThroughHost
    below). This mirrors real usage: a DNS- or hosts-file-redirected Steam
    client always sends a genuine *.steamcontent.com/*.steamserver.net Host
    -- curl/Invoke-WebRequest's own default Host (127.0.0.1) is exactly the
    kind of Host the allowlist guard (ADR req 4) is designed to reject, so
    it cannot be used for the positive-path tests here.

.PARAMETER DepotId / ChunkHash
    Known-good live Steam CDN test object, same one used throughout poc/
    (999,232-byte chunk, confirmed reachable since WP 0.1).

.PARAMETER ValidHost
    Host header used for tests that just need *some* legitimate Steam CDN
    Host. "lancache.steamcontent.com" is deliberately chosen here (not just
    for variety): it exercises the ADR req 4 fallback-edge path (this exact
    hostname has no public A record, so vault-core must redirect it to a
    concrete edge internally -- see core/nginx/nginx.conf) on every general
    test in this suite, not just a dedicated one.

.PARAMETER PassThroughHost
    A second, different real Steam CDN edge hostname used specifically to
    prove per-request Host forwarding (ADR req 4) reaches a DIFFERENT edge
    than the fallback, not just the fallback edge every time.

    Exit code 0 = PASS, 1 = FAIL.
#>

[CmdletBinding()]
param(
    [string]$DepotId          = "70403",
    [string]$ChunkHash        = "773d10050d99b2544665873ec2125b3bf273e8b2",
    [string]$BaseUrl          = "http://127.0.0.1",
    [string]$ValidHost        = "lancache.steamcontent.com",
    [string]$PassThroughHost  = "cache2-ams1.steamcontent.com",
    [string]$RejectHost       = "evil.example.com"
)

$ErrorActionPreference = "Stop"

$CoreRoot   = Split-Path $PSScriptRoot -Parent            # .../core
$RepoRoot   = Split-Path $CoreRoot -Parent                # repo root
$PocRoot    = Join-Path $RepoRoot "poc"
$NginxExe   = Join-Path $PocRoot "nginx\nginx.exe"
$CoreConfig = "nginx/nginx.conf"                          # relative to -p $CoreRoot

$LogFile     = Join-Path $CoreRoot "logs\access.log"
$CacheFile   = Join-Path $CoreRoot "cache\depot\$DepotId\chunk\$ChunkHash"
$RequestPath = "/depot/$DepotId/chunk/$ChunkHash"
# Built via concatenation, not string interpolation: PowerShell's expandable
# ("...") string parser treats "$Var?literal" as an attempt to resolve a
# variable literally named "Var?literal" (which doesn't exist -> empty),
# silently swallowing everything up to the next "=" -- confirmed during this
# WP's test-writing. Concatenation (or ${Var} + literal) sidesteps it.
$NocacheRequestPath = $RequestPath + "?nocache=1"
$TestTmpDir  = Join-Path $CoreRoot "_testcore_tmp"

# Synthetic path guaranteed to never be legitimately cached -- used for the
# Host-allowlist rejection test so it can't accidentally be a HIT.
$SyntheticPath = "/depot/1/chunk/0000000000000000000000000000000000000000"
$SyntheticCacheFile = Join-Path $CoreRoot "cache\depot\1\chunk\0000000000000000000000000000000000000000"

$script:failures = @()

function Fail($msg) {
    $script:failures += $msg
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}

function Pass($msg) {
    Write-Host "  [ OK ] $msg" -ForegroundColor Green
}

function Info($msg) {
    Write-Host "  [INFO] $msg" -ForegroundColor Cyan
}

# --- helpers -----------------------------------------------------------------

# Nitpick fixed in review: earlier revisions fell back to
# `Get-Process -Name nginx | Stop-Process -Force`, which force-kills EVERY
# nginx process on the whole host, not just the two instances this script
# manages -- collateral damage for anyone running an unrelated nginx on the
# same machine. These helpers instead read the master PID from each
# instance's own pid file (poc/logs/nginx.pid, core/logs/nginx.pid) and only
# ever touch that specific process tree.
function Get-PidFromFile([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = (Get-Content $PidFile -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $parsed = 0
    if ([int]::TryParse($raw.Trim(), [ref]$parsed)) { return $parsed }
    return $null
}

function Stop-NginxInstance([string]$Prefix, [string]$ConfigRelPath, [string]$PidFile) {
    $masterPid = Get-PidFromFile $PidFile
    if (-not $masterPid) { return }
    if (-not (Get-Process -Id $masterPid -ErrorAction SilentlyContinue)) { return }

    # Graceful stop first -- nginx's own -s stop, scoped to exactly this
    # prefix/config, signals the master to shut down its worker(s) cleanly.
    try { & $NginxExe -p "$Prefix" -c $ConfigRelPath -s stop 2>$null } catch {}
    Start-Sleep -Milliseconds 500

    # On Windows, killing just the master does not necessarily take its
    # worker child process(es) down too (no default job-object grouping),
    # so if the master is still alive after a graceful stop, force-kill it
    # AND any of its direct children -- still scoped to this one process
    # tree, never to unrelated nginx processes elsewhere on the host.
    if (Get-Process -Id $masterPid -ErrorAction SilentlyContinue) {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$masterPid" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $masterPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
}

function Stop-AnyNginx {
    Stop-NginxInstance $PocRoot "conf/nginx.conf" (Join-Path $PocRoot "logs\nginx.pid")
    Stop-NginxInstance $CoreRoot $CoreConfig (Join-Path $CoreRoot "logs\nginx.pid")
}

function Start-CoreNginx {
    foreach ($dir in @(
        (Join-Path $CoreRoot "cache\depot"),
        (Join-Path $CoreRoot "tmp\client_body"),
        (Join-Path $CoreRoot "tmp\proxy"),
        (Join-Path $CoreRoot "tmp\fastcgi"),
        (Join-Path $CoreRoot "tmp\uwsgi"),
        (Join-Path $CoreRoot "tmp\scgi"),
        (Join-Path $CoreRoot "logs")
    )) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }

    Stop-AnyNginx

    Write-Host "Starting vault-core nginx (prefix=$CoreRoot, config=$CoreConfig) on port 80 ..."
    Start-Process -FilePath $NginxExe `
        -ArgumentList @("-p", "`"$CoreRoot`"", "-c", $CoreConfig) `
        -WorkingDirectory $CoreRoot `
        -WindowStyle Hidden
    Start-Sleep -Milliseconds 800

    $corePidFile = Join-Path $CoreRoot "logs\nginx.pid"
    $corePid = Get-PidFromFile $corePidFile
    if (-not $corePid -or -not (Get-Process -Id $corePid -ErrorAction SilentlyContinue)) {
        throw "vault-core nginx did not start -- check core/logs/error.log"
    }
    Write-Host "vault-core nginx started (PID $corePid)." -ForegroundColor Green
}

function Stop-CoreNginx {
    Stop-NginxInstance $CoreRoot $CoreConfig (Join-Path $CoreRoot "logs\nginx.pid")
}

# Minimal, dependency-free fake HTTP backend for the B1 retry regression
# (test 7 below): a raw TcpListener answering every connection with a fixed
# status/body, run as a background PowerShell job. Avoids relying on
# System.Net.HttpListener (which can require a URL ACL reservation / admin
# rights for some binding forms) and avoids any external dependency (e.g.
# Python) for a test that ships in the repo.
function Start-FakeHttpBackend([int]$Port, [int]$StatusCode, [string]$Body) {
    $statusText = @{200 = "OK"; 502 = "Bad Gateway"}[$StatusCode]
    if (-not $statusText) { $statusText = "Status" }
    return Start-Job -ScriptBlock {
        param($Port, $StatusCode, $StatusText, $Body)
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $bodyBytes = [System.Text.Encoding]::ASCII.GetBytes($Body)
        $headerText = "HTTP/1.1 $StatusCode $StatusText`r`nContent-Length: $($bodyBytes.Length)`r`nConnection: close`r`n`r`n"
        $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($headerText)
        while ($true) {
            $client = $listener.AcceptTcpClient()
            try {
                $stream = $client.GetStream()
                $stream.ReadTimeout = 2000
                $buffer = New-Object byte[] 4096
                try { [void]$stream.Read($buffer, 0, $buffer.Length) } catch {}
                $stream.Write($headerBytes, 0, $headerBytes.Length)
                $stream.Write($bodyBytes, 0, $bodyBytes.Length)
                $stream.Flush()
            }
            finally { $client.Close() }
        }
    } -ArgumentList $Port, $StatusCode, $statusText, $Body
}

function Get-NewLogLines([int]$sinceCount) {
    if (-not (Test-Path $LogFile)) { return @() }
    return Get-Content $LogFile | Select-Object -Skip $sinceCount
}

function Get-LogLineCount {
    if (-not (Test-Path $LogFile)) { return 0 }
    return (Get-Content $LogFile | Measure-Object -Line).Lines
}

# --- WP 3.10 (ADR-0008) cache-event log helpers -------------------------------
# core/nginx/nginx.conf writes this SECOND, machine-readable log
# (core/README.md "Cache-event log") to logs/event.log, tab-separated,
# version-prefixed ("v1"). buffer=64k flush=5s means a line can sit
# unflushed in nginx's memory for up to 5 seconds after the request that
# produced it (see the config comment at the access_log directive) -- so
# every test below that expects new event-log lines POLLS for them instead
# of reading immediately, exactly the flakiness trap that comment warns
# against.
$EventLogFile = Join-Path $CoreRoot "logs\event.log"

function Get-EventLineCount {
    if (-not (Test-Path $EventLogFile)) { return 0 }
    return (Get-Content $EventLogFile | Measure-Object -Line).Lines
}

function Get-EventNewLines([int]$sinceCount) {
    if (-not (Test-Path $EventLogFile)) { return @() }
    return Get-Content $EventLogFile | Select-Object -Skip $sinceCount
}

# Polls until at least $MinNewLines new lines appear (or $TimeoutSec
# elapses), tolerating the buffer=64k flush=5s latency documented above
# instead of racing it.
function Wait-EventNewLines([int]$SinceCount, [int]$MinNewLines = 1, [int]$TimeoutSec = 8) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lines = @()
    do {
        $lines = @(Get-EventNewLines $SinceCount)
        if ($lines.Count -ge $MinNewLines) { return $lines }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    return $lines
}

# ==============================================================================
Write-Host "== SteamHangar vault-core (WP 1.1) test suite ==" -ForegroundColor Cyan
Write-Host "Target object: depot $DepotId chunk $ChunkHash"

try {
    Start-CoreNginx

    # --- /health -------------------------------------------------------------
    try {
        $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
        if ($health.StatusCode -eq 200) { Pass "/health returns 200" }
        else { Fail "/health returned HTTP $($health.StatusCode), expected 200" }
    }
    catch { Fail "/health request failed: $_" }

    # --- 1. lancache-heartbeat (ADR req 1) ------------------------------------
    Write-Host ""
    Write-Host "-- Test: lancache-heartbeat (ADR req 1) --"
    try {
        $hb = Invoke-WebRequest -Uri "$BaseUrl/lancache-heartbeat" -UseBasicParsing -TimeoutSec 10
        if ($hb.StatusCode -ne 200) {
            Fail "/lancache-heartbeat returned HTTP $($hb.StatusCode), expected 200"
        }
        elseif ($hb.Headers['X-LanCache-Processed-By'] -ne "steamhangar") {
            Fail "/lancache-heartbeat X-LanCache-Processed-By was '$($hb.Headers['X-LanCache-Processed-By'])', expected 'steamhangar'"
        }
        else {
            Pass "/lancache-heartbeat: 200 + X-LanCache-Processed-By: steamhangar"
        }
    }
    catch { Fail "/lancache-heartbeat request failed: $_" }

    # --- 2. smoke: MISS -> store -> HIT, byte-identical -----------------------
    Write-Host ""
    Write-Host "-- Test: smoke (MISS -> store -> HIT) --"
    if (Test-Path $CacheFile) { Remove-Item -Force $CacheFile }
    if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
    New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null

    $groundTruthHash = $null
    $logBaseline = Get-LogLineCount
    $firstOut = Join-Path $TestTmpDir "first.bin"
    try {
        $r1 = Invoke-WebRequest -Uri "$BaseUrl$RequestPath" -Headers @{Host = $ValidHost} `
            -UseBasicParsing -TimeoutSec 30 -OutFile $firstOut -PassThru
        if ($r1.StatusCode -eq 200) { Pass "first request (cold) returned HTTP 200" }
        else { Fail "first request returned HTTP $($r1.StatusCode), expected 200" }
    }
    catch { Fail "first request threw: $_" }

    if (Test-Path $CacheFile) {
        $groundTruthHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
        Pass "response stored path-faithfully at core/cache/depot/$DepotId/chunk/$ChunkHash (SHA256 $groundTruthHash)"
    }
    else {
        Fail "expected cache file was not created: $CacheFile"
    }

    $secondOut = Join-Path $TestTmpDir "second.bin"
    try {
        $r2 = Invoke-WebRequest -Uri "$BaseUrl$RequestPath" -Headers @{Host = $ValidHost} `
            -UseBasicParsing -TimeoutSec 30 -OutFile $secondOut -PassThru
        if ($r2.StatusCode -eq 200) { Pass "second request (warm) returned HTTP 200" }
        else { Fail "second request returned HTTP $($r2.StatusCode), expected 200" }
    }
    catch { Fail "second request threw: $_" }

    if ((Test-Path $firstOut) -and (Test-Path $secondOut)) {
        $h1 = (Get-FileHash -Algorithm SHA256 -Path $firstOut).Hash
        $h2 = (Get-FileHash -Algorithm SHA256 -Path $secondOut).Hash
        if ($h1 -eq $h2) { Pass "response bodies are byte-identical (SHA256 $h1)" }
        else { Fail "response bodies differ: first=$h1 second=$h2" }
    }
    else {
        Fail "could not compare response bodies - one or both downloads missing"
    }

    # Wrapped in @(...): PowerShell unwraps a single-element pipeline result
    # to a bare scalar string, and $scalar[0] then indexes the string's
    # CHARACTERS, not "element 0 of a one-line array" -- confirmed during
    # this WP's test-writing (a single match silently became the log line's
    # first character, "0", from its leading timestamp digit). @(...) forces
    # array semantics regardless of match count (0, 1, or many).
    $newLines = @((Get-NewLogLines $logBaseline) | Where-Object { $_ -like "*uri=`"$RequestPath`"*" })
    if ($newLines.Count -lt 2) {
        Fail "expected 2 new access log entries for $RequestPath, found $($newLines.Count)"
    }
    else {
        if ($newLines[0] -match "cache=MISS" -and $newLines[0] -match "upstream_status=200") {
            Pass "log: first request cache=MISS, upstream_status=200"
        } else { Fail "log: first request line unexpected: $($newLines[0])" }

        if ($newLines[1] -match "cache=HIT" -and $newLines[1] -match "upstream_status=-") {
            Pass "log: second request cache=HIT, upstream_status=- (no upstream contacted)"
        } else { Fail "log: second request line unexpected: $($newLines[1])" }
    }

    if (-not $groundTruthHash) {
        Fail "no ground-truth hash available -- skipping range/nocache/host tests that depend on it"
    }

    # --- 3. Range-strip guard (ADR req 2) -------------------------------------
    Write-Host ""
    Write-Host "-- Test: Range-strip guard (ADR req 2) --"
    if ($groundTruthHash) {
        if (Test-Path $CacheFile) { Remove-Item -Force $CacheFile }
        Info "cache file removed - object is cold for this test"

        # Invoke-WebRequest cannot set a Range header at all (.NET's
        # WebHeaderCollection treats it as a restricted header, throwing
        # "The 'Range' header must be modified using the appropriate
        # property or method" -- there is no such property exposed by
        # Invoke-WebRequest in Windows PowerShell 5.1). curl.exe has no such
        # restriction and is available on this environment (verified
        # during this WP), so it is used for this one request only.
        $rangeOut = Join-Path $TestTmpDir "range.bin"
        $curlOut = & curl.exe -s -o $rangeOut -w "%{http_code} %{size_download} %{header_json}" `
            -H "Host: $ValidHost" -H "Range: bytes=0-1023" "$BaseUrl$RequestPath"
        $curlParts = $curlOut -split " ", 3
        $statusCode3 = [int]$curlParts[0]
        $clientLen = if (Test-Path $rangeOut) { (Get-Item $rangeOut).Length } else { -1 }
        Info "client received: HTTP $statusCode3, body bytes on disk=$clientLen"
        if ($statusCode3 -eq 200) {
            Pass "client received HTTP 200 (full body) for a Range request on a cold object -- documented consequence of stripping Range upstream (see core/README.md); NOT a 206"
        }
        elseif ($statusCode3 -eq 206) {
            Fail "client received HTTP 206 for a cold-object Range request -- Range was NOT stripped upstream as intended"
        }
        else {
            Fail "client received unexpected HTTP $statusCode3 for the Range request"
        }

        if (Test-Path $CacheFile) {
            $storedHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($storedHash -eq $groundTruthHash) {
                Pass "stored file after a Range request on a cold object is still the COMPLETE object (SHA256 $storedHash matches ground truth)"
            }
            else {
                Fail "stored file after Range request does NOT match ground truth: got $storedHash, expected $groundTruthHash"
            }
        }
        else {
            Fail "no cache file exists after the Range request -- storage guard may have over-blocked a legitimate 200"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 4. nocache=1 bypass (ADR req 3) ---------------------------------------
    Write-Host ""
    Write-Host "-- Test: nocache=1 bypass (ADR req 3) --"
    if ($groundTruthHash) {
        # Object is warm again after test 3 re-stored it. Confirm that, then
        # bypass it with ?nocache=1 and prove upstream was re-contacted.
        if (-not (Test-Path $CacheFile)) {
            Fail "precondition failed: object should be warm before the nocache test"
        }
        $logBaseline2 = Get-LogLineCount
        $nocacheOut = Join-Path $TestTmpDir "nocache.bin"
        try {
            $r4 = Invoke-WebRequest -Uri "$BaseUrl$NocacheRequestPath" -Headers @{Host = $ValidHost} `
                -UseBasicParsing -TimeoutSec 30 -OutFile $nocacheOut -PassThru
            if ($r4.StatusCode -eq 200) { Pass "?nocache=1 request returned HTTP 200" }
            else { Fail "?nocache=1 request returned HTTP $($r4.StatusCode), expected 200" }
        }
        catch { Fail "?nocache=1 request threw: $_" }

        $nocacheLogPattern = '*uri="' + $NocacheRequestPath + '"*'
        # @(...): see the array-wrapping note on $newLines above -- this is
        # exactly the single-match case that bug bites.
        $nocacheLogLines = @((Get-NewLogLines $logBaseline2) | Where-Object { $_ -like $nocacheLogPattern })
        if ($nocacheLogLines.Count -lt 1) {
            Fail "no access log entry found for the ?nocache=1 request (expected log evidence of upstream contact)"
        }
        elseif ($nocacheLogLines[0] -match "cache=MISS" -and $nocacheLogLines[0] -match "upstream_status=200") {
            Pass "log evidence: ?nocache=1 request shows cache=MISS, upstream_status=200 (upstream was re-contacted, bypassing the stored copy)"
        }
        else {
            Fail "?nocache=1 request log line did not show upstream contact: $($nocacheLogLines[0])"
        }

        if (Test-Path $CacheFile) {
            $postNocacheHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($postNocacheHash -eq $groundTruthHash) {
                Pass "stored file remains valid after the nocache=1 bypass (SHA256 matches ground truth)"
            }
            else {
                Fail "stored file after nocache=1 bypass does not match ground truth: got $postNocacheHash"
            }
        }
        else {
            Fail "cache file missing after nocache=1 bypass"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 5. Host-header allowlist: reject off-list Host (ADR req 4) ----------
    Write-Host ""
    Write-Host "-- Test: Host-header allowlist rejection (ADR req 4) --"
    if (Test-Path $SyntheticCacheFile) { Remove-Item -Force $SyntheticCacheFile }
    # Windows PowerShell 5.1 (this environment's primary shell) has no
    # -SkipHttpErrorCheck; Invoke-WebRequest throws on any non-2xx status
    # instead, so the expected 4xx here is recovered from the exception.
    try {
        $r5 = Invoke-WebRequest -Uri "$BaseUrl$SyntheticPath" -Headers @{Host = $RejectHost} `
            -UseBasicParsing -TimeoutSec 10
        $code5 = $r5.StatusCode
    }
    catch {
        if ($_.Exception.Response) { $code5 = [int]$_.Exception.Response.StatusCode }
        else { $code5 = $null }
    }
    if ($code5 -ge 400 -and $code5 -lt 500) {
        Pass "off-list Host '$RejectHost' rejected with HTTP $code5 (not proxied -- no open proxy)"
    }
    else {
        Fail "off-list Host '$RejectHost' returned HTTP $code5, expected 4xx"
    }
    if (Test-Path $SyntheticCacheFile) {
        Fail "a file was created for the rejected off-list-Host request -- this must never happen: $SyntheticCacheFile"
    }
    else {
        Pass "no file was stored for the rejected request"
    }

    # --- 6. Host-header pass-through: on-list Host reaches its own edge -------
    Write-Host ""
    Write-Host "-- Test: Host-header pass-through (ADR req 4) --"
    if ($groundTruthHash) {
        $logBaseline3 = Get-LogLineCount
        $passOut = Join-Path $TestTmpDir "passthrough.bin"
        try {
            $r6 = Invoke-WebRequest -Uri "$BaseUrl$NocacheRequestPath" -Headers @{Host = $PassThroughHost} `
                -UseBasicParsing -TimeoutSec 30 -OutFile $passOut -PassThru
            if ($r6.StatusCode -eq 200) { Pass "Host: $PassThroughHost request returned HTTP 200" }
            else { Fail "Host: $PassThroughHost request returned HTTP $($r6.StatusCode), expected 200" }
        }
        catch { Fail "Host: $PassThroughHost request threw: $_" }

        $passLogPattern = '*uri="' + $NocacheRequestPath + '"*'
        # @(...): see the array-wrapping note on $newLines above.
        $passLogLines = @((Get-NewLogLines $logBaseline3) | Where-Object { $_ -like $passLogPattern })
        if ($passLogLines.Count -ge 1 -and $passLogLines[0] -match "upstream_status=200") {
            Pass "log evidence: Host: $PassThroughHost fetch reached its upstream with upstream_status=200"
        }
        else {
            Fail "no log evidence of a successful upstream fetch for Host: $PassThroughHost"
        }

        if (Test-Path $CacheFile) {
            $passHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($passHash -eq $groundTruthHash) {
                Pass "object re-stored via Host: $PassThroughHost still matches ground truth (SHA256 $passHash)"
            }
            else {
                Fail "object stored via Host: $PassThroughHost does not match ground truth: got $passHash"
            }
        }
        else {
            Fail "cache file missing after Host: $PassThroughHost fetch"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 7. B1 regression: retried 502->200 must still be stored --------------
    Write-Host ""
    Write-Host "-- Test: retry regression, upstream_status='502, 200' must be stored (B1 fix) --"
    $retryFixtureConf = Join-Path $PSScriptRoot "fixtures\retry-regression.conf"
    $retryRigRoot = Join-Path $CoreRoot "_testcore_tmp\retry-rig"
    $badBackendJob = $null
    $okBackendJob = $null
    try {
        foreach ($dir in @(
            (Join-Path $retryRigRoot "cache\depot\t"),
            (Join-Path $retryRigRoot "tmp\client_body"),
            (Join-Path $retryRigRoot "tmp\proxy"),
            (Join-Path $retryRigRoot "tmp\fastcgi"),
            (Join-Path $retryRigRoot "tmp\uwsgi"),
            (Join-Path $retryRigRoot "tmp\scgi"),
            (Join-Path $retryRigRoot "logs")
        )) {
            if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        }

        $badBackendJob = Start-FakeHttpBackend -Port 18101 -StatusCode 502 -Body "BACKEND-1-ALWAYS-502"
        $okBackendJob  = Start-FakeHttpBackend -Port 18102 -StatusCode 200 -Body "BACKEND-2-ALWAYS-200-RETRY-BODY"
        Start-Sleep -Milliseconds 500

        Start-Process -FilePath $NginxExe `
            -ArgumentList @("-p", "`"$retryRigRoot`"", "-c", "`"$retryFixtureConf`"") `
            -WorkingDirectory $retryRigRoot `
            -WindowStyle Hidden
        Start-Sleep -Milliseconds 600

        $retryStoredFile = Join-Path $retryRigRoot "cache\depot\t\retrytest"
        try {
            $r7 = Invoke-WebRequest -Uri "http://127.0.0.1:18096/depot/t/retrytest" -UseBasicParsing -TimeoutSec 15
            if ($r7.StatusCode -eq 200) { Pass "retry-rig request returned HTTP 200 (client got the eventually-successful body)" }
            else { Fail "retry-rig request returned HTTP $($r7.StatusCode), expected 200" }
        }
        catch { Fail "retry-rig request threw: $_" }

        $retryLogFile = Join-Path $retryRigRoot "logs\access.log"
        $retryLogLine = if (Test-Path $retryLogFile) { Get-Content $retryLogFile | Select-Object -Last 1 } else { $null }
        if ($retryLogLine -match "502, 200") {
            Pass "log evidence: upstream_status shows the retry list '502, 200' (reproduces the exact Phase-0 pattern, poc/logs/access.log:3391-3392)"
        }
        else {
            Fail "retry-rig access log did not show the expected '502, 200' retry pattern: $retryLogLine"
        }

        if (Test-Path $retryStoredFile) {
            Pass "B1 FIXED: the retried-then-successful (502, 200) response WAS stored to disk"
        }
        else {
            Fail "B1 REGRESSION: a request that succeeded after a retry (upstream_status='502, 200') was NOT stored -- the map's regex guard is missing or broken"
        }
    }
    finally {
        # Scoped stop: this fixture's own pid file only, never a blanket
        # nginx-process kill (same reasoning as Stop-NginxInstance above).
        Stop-NginxInstance $retryRigRoot "`"$retryFixtureConf`"" (Join-Path $retryRigRoot "logs\nginx.pid")
        if ($badBackendJob) { Stop-Job $badBackendJob -ErrorAction SilentlyContinue; Remove-Job $badBackendJob -Force -ErrorAction SilentlyContinue }
        if ($okBackendJob)  { Stop-Job $okBackendJob  -ErrorAction SilentlyContinue; Remove-Job $okBackendJob  -Force -ErrorAction SilentlyContinue }
    }

    # --- 8. S2 regression: Accept-Encoding: gzip must not corrupt storage ----
    Write-Host ""
    Write-Host "-- Test: Accept-Encoding: gzip MISS then HIT, byte-identical (S2 fix) --"
    if ($groundTruthHash) {
        if (Test-Path $CacheFile) { Remove-Item -Force $CacheFile }
        Info "cache file removed - object is cold for this test"

        # curl.exe, not Invoke-WebRequest: Invoke-WebRequest negotiates and
        # transparently decompresses Accept-Encoding itself, which would
        # mask exactly the bug this test targets (a raw gzip body landing
        # on disk with no Content-Encoding header to say so). curl with an
        # explicit header and no --compressed flag sends the header but
        # does not touch the response body.
        $gzipMissOut = Join-Path $TestTmpDir "gzip_miss.bin"
        $gzipMissCode = & curl.exe -s -o $gzipMissOut -w "%{http_code}" `
            -H "Host: $ValidHost" -H "Accept-Encoding: gzip" "$BaseUrl$RequestPath"
        if ([int]$gzipMissCode -eq 200) { Pass "Accept-Encoding: gzip MISS request returned HTTP 200" }
        else { Fail "Accept-Encoding: gzip MISS request returned HTTP $gzipMissCode, expected 200" }

        $gzipHitOut = Join-Path $TestTmpDir "gzip_hit.bin"
        $gzipHitCode = & curl.exe -s -o $gzipHitOut -w "%{http_code}" `
            -H "Host: $ValidHost" -H "Accept-Encoding: gzip" "$BaseUrl$RequestPath"
        if ([int]$gzipHitCode -eq 200) { Pass "Accept-Encoding: gzip HIT request returned HTTP 200" }
        else { Fail "Accept-Encoding: gzip HIT request returned HTTP $gzipHitCode, expected 200" }

        if ((Test-Path $gzipMissOut) -and (Test-Path $gzipHitOut)) {
            $gzipMissHash = (Get-FileHash -Algorithm SHA256 -Path $gzipMissOut).Hash
            $gzipHitHash  = (Get-FileHash -Algorithm SHA256 -Path $gzipHitOut).Hash
            if ($gzipMissHash -eq $groundTruthHash -and $gzipHitHash -eq $groundTruthHash) {
                Pass "MISS and HIT bodies with Accept-Encoding: gzip both match ground truth (SHA256 $gzipMissHash) -- upstream served identity, not raw gzip"
            }
            else {
                Fail "gzip MISS/HIT bodies do not match ground truth: miss=$gzipMissHash hit=$gzipHitHash expected=$groundTruthHash"
            }
        }
        else {
            Fail "could not compare gzip MISS/HIT bodies - one or both downloads missing"
        }

        if (Test-Path $CacheFile) {
            $gzipStoredHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($gzipStoredHash -eq $groundTruthHash) {
                Pass "stored file after Accept-Encoding: gzip request matches ground truth (not a raw gzip body)"
            }
            else {
                Fail "stored file after Accept-Encoding: gzip request does NOT match ground truth: got $gzipStoredHash"
            }
        }
        else {
            Fail "no cache file exists after the Accept-Encoding: gzip request"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 9. S3 regression: temp files must not be web-reachable ---------------
    Write-Host ""
    Write-Host "-- Test: GET /tmp/proxy/... is not served (S3 fix) --"
    try {
        $r9 = Invoke-WebRequest -Uri "$BaseUrl/tmp/proxy/whatever" -UseBasicParsing -TimeoutSec 10
        $code9 = $r9.StatusCode
    }
    catch {
        if ($_.Exception.Response) { $code9 = [int]$_.Exception.Response.StatusCode }
        else { $code9 = $null }
    }
    if ($code9 -eq 404) {
        Pass "GET /tmp/proxy/whatever correctly returns 404 (temp files are not web-reachable)"
    }
    else {
        Fail "GET /tmp/proxy/whatever returned HTTP $code9, expected 404"
    }

    # --- 10. WP 3.10 cache-event log: MISS-then-store, HIT ---------------------
    Write-Host ""
    Write-Host "-- Test: cache-event log (WP 3.10 / ADR-0008) -- MISS then HIT --"
    if (Test-Path $CacheFile) { Remove-Item -Force $CacheFile }
    Info "cache file removed - object is cold for the event-log test"

    $eventBaseline10 = Get-EventLineCount
    try {
        $r10a = Invoke-WebRequest -Uri "$BaseUrl$RequestPath" -Headers @{Host = $ValidHost} `
            -UseBasicParsing -TimeoutSec 30
        if ($r10a.StatusCode -ne 200) { Fail "event-log MISS request returned HTTP $($r10a.StatusCode), expected 200" }
    }
    catch { Fail "event-log MISS request threw: $_" }

    # Wrapped in @(...) at the CALL SITE, not just inside the helper: a
    # PowerShell function's `return` re-flattens a single-element array back
    # to a bare scalar on its way out through the pipeline, same trap the
    # $newLines/$nocacheLogLines/$passLogLines helpers above already guard
    # against -- confirmed by this test failing with "1 fields" (indexing
    # the STRING's first character, "v") instead of the real 8-tab-field
    # line before this wrap was added here.
    $missLines = @(Wait-EventNewLines -SinceCount $eventBaseline10 -MinNewLines 1)
    if ($missLines.Count -lt 1) {
        Fail "no new cache-event log line appeared for the MISS request within the poll window (logs/event.log missing or empty: $(Test-Path $EventLogFile))"
    }
    else {
        $missFields = $missLines[0] -split "`t"
        if ($missFields.Count -ne 9) {
            Fail "MISS event-log line has $($missFields.Count) fields, expected 9 (review finding N3 added `$status as field 9): $($missLines[0])"
        }
        elseif ($missFields[0] -ne "v1") {
            Fail "MISS event-log line field 1 (version) was '$($missFields[0])', expected 'v1'"
        }
        elseif ($missFields[3] -ne "MISS") {
            Fail "MISS event-log line field 4 (cache status) was '$($missFields[3])', expected 'MISS'"
        }
        elseif ($missFields[4] -ne $DepotId) {
            Fail "MISS event-log line field 5 (depot id) was '$($missFields[4])', expected '$DepotId'"
        }
        elseif ([int]$missFields[6] -le 0) {
            Fail "MISS event-log line field 7 (bytes sent) was '$($missFields[6])', expected a positive number"
        }
        elseif ($missFields[8] -ne "200") {
            Fail "MISS event-log line field 9 (HTTP status) was '$($missFields[8])', expected '200'"
        }
        else {
            Pass "MISS event-log line: v1, depot=$($missFields[4]), bytes=$($missFields[6]), status=MISS, http_status=$($missFields[8]) ($($missLines[0]))"
        }
    }

    $eventBaseline10b = Get-EventLineCount
    try {
        $r10b = Invoke-WebRequest -Uri "$BaseUrl$RequestPath" -Headers @{Host = $ValidHost} `
            -UseBasicParsing -TimeoutSec 30
        if ($r10b.StatusCode -ne 200) { Fail "event-log HIT request returned HTTP $($r10b.StatusCode), expected 200" }
    }
    catch { Fail "event-log HIT request threw: $_" }

    $hitLines = @(Wait-EventNewLines -SinceCount $eventBaseline10b -MinNewLines 1)
    if ($hitLines.Count -lt 1) {
        Fail "no new cache-event log line appeared for the follow-up HIT request within the poll window"
    }
    else {
        $hitFields = $hitLines[0] -split "`t"
        if ($hitFields.Count -ne 9) {
            Fail "HIT event-log line has $($hitFields.Count) fields, expected 9 (review finding N3 added `$status as field 9): $($hitLines[0])"
        }
        elseif ($hitFields[3] -ne "HIT") {
            Fail "HIT event-log line field 4 (cache status) was '$($hitFields[3])', expected 'HIT'"
        }
        elseif ($hitFields[4] -ne $DepotId) {
            Fail "HIT event-log line field 5 (depot id) was '$($hitFields[4])', expected '$DepotId'"
        }
        elseif ($hitFields[8] -ne "200") {
            Fail "HIT event-log line field 9 (HTTP status) was '$($hitFields[8])', expected '200'"
        }
        else {
            Pass "HIT event-log line: v1, depot=$($hitFields[4]), bytes=$($hitFields[6]), status=HIT, http_status=$($hitFields[8]) ($($hitLines[0]))"
        }
    }

    # --- 11. WP 3.10 cache-event log: ?nocache=1 is BYPASS-typed ---------------
    Write-Host ""
    Write-Host "-- Test: cache-event log -- nocache=1 identifiable as BYPASS --"
    $eventBaseline11 = Get-EventLineCount
    try {
        $r11 = Invoke-WebRequest -Uri "$BaseUrl$NocacheRequestPath" -Headers @{Host = $ValidHost} `
            -UseBasicParsing -TimeoutSec 30
        if ($r11.StatusCode -ne 200) { Fail "event-log nocache=1 request returned HTTP $($r11.StatusCode), expected 200" }
    }
    catch { Fail "event-log nocache=1 request threw: $_" }

    $bypassLines = @(Wait-EventNewLines -SinceCount $eventBaseline11 -MinNewLines 1)
    if ($bypassLines.Count -lt 1) {
        Fail "no new cache-event log line appeared for the ?nocache=1 request within the poll window"
    }
    else {
        $bypassFields = $bypassLines[0] -split "`t"
        if ($bypassFields.Count -ne 9) {
            Fail "nocache=1 event-log line has $($bypassFields.Count) fields, expected 9 (review finding N3 added `$status as field 9): $($bypassLines[0])"
        }
        elseif ($bypassFields[3] -ne "BYPASS") {
            Fail "nocache=1 event-log line field 4 (cache status) was '$($bypassFields[3])', expected 'BYPASS': $($bypassLines[0])"
        }
        elseif ($bypassFields[5] -ne $RequestPath) {
            Fail "nocache=1 event-log line field 6 (uri) was '$($bypassFields[5])', expected '$RequestPath' (query string must not appear -- `$uri already strips it)"
        }
        elseif ($bypassFields[8] -ne "200") {
            Fail "nocache=1 event-log line field 9 (HTTP status) was '$($bypassFields[8])', expected '200'"
        }
        else {
            Pass "nocache=1 event-log line correctly typed BYPASS, uri=$($bypassFields[5]) has no query string, http_status=$($bypassFields[8]) ($($bypassLines[0]))"
        }
    }

    # --- 12. WP 3.10 cache-event log: heartbeat produces no line ---------------
    Write-Host ""
    Write-Host "-- Test: cache-event log -- /lancache-heartbeat produces NO line --"
    $eventBaseline12 = Get-EventLineCount
    try {
        $r12 = Invoke-WebRequest -Uri "$BaseUrl/lancache-heartbeat" -UseBasicParsing -TimeoutSec 10
        if ($r12.StatusCode -ne 200) { Fail "heartbeat request (event-log test) returned HTTP $($r12.StatusCode), expected 200" }
    }
    catch { Fail "heartbeat request (event-log test) threw: $_" }
    Start-Sleep -Seconds 1
    $heartbeatLines = @(Get-EventNewLines $eventBaseline12)
    if ($heartbeatLines.Count -eq 0) {
        Pass "/lancache-heartbeat produced zero cache-event log lines (location never declares the directive)"
    }
    else {
        Fail "/lancache-heartbeat produced $($heartbeatLines.Count) unexpected cache-event log line(s): $heartbeatLines"
    }

    # --- 13. WP 3.10 cache-event log: hostile URI keeps the line format stable -
    Write-Host ""
    Write-Host "-- Test: cache-event log -- hostile URI (tab/quote/newline/non-ASCII) --"
    # %09=tab, %22=quote, %0A=newline, %C3%A9=UTF-8 'e' -- all percent-encoded
    # so they travel as a syntactically valid HTTP request line, then nginx
    # decodes them INTO $uri before this log format ever sees them. curl.exe
    # is used (not Invoke-WebRequest) for the same reason test 3/8 use it:
    # no risk of .NET's Uri class silently re-normalizing/rejecting the
    # crafted path before it reaches the wire.
    $hostileUri = "/depot/1/chunk/AAAA%09%22%0A%C3%A9BBBB"
    $eventBaseline13 = Get-EventLineCount
    $hostileOut = Join-Path $TestTmpDir "hostile.bin"
    $hostileCode = & curl.exe -s -o $hostileOut -w "%{http_code}" -H "Host: $ValidHost" "$BaseUrl$hostileUri"
    Info "hostile-URI request completed with client-visible HTTP $hostileCode (any status is fine -- this test targets log-line integrity, not a successful fetch)"

    $hostileLines = @(Wait-EventNewLines -SinceCount $eventBaseline13 -MinNewLines 1)
    if ($hostileLines.Count -lt 1) {
        Fail "no cache-event log line appeared for the hostile-URI request within the poll window"
    }
    else {
        # Exactly ONE new line is required, not just "at least one": a raw,
        # unescaped tab/newline reaching the file would SPLIT what should be
        # one logical event into extra physical lines -- which is precisely
        # the corruption escape=default exists to prevent (core/README.md
        # "Escaping"). Asserting the count itself, not just field-splitting
        # the first line, is what actually pins that guarantee.
        if ($hostileLines.Count -ne 1) {
            Fail "hostile-URI request produced $($hostileLines.Count) new event-log lines, expected exactly 1 -- an embedded control character likely broke the line format: $hostileLines"
        }
        else {
            $hostileFields = $hostileLines[0] -split "`t"
            if ($hostileFields.Count -ne 9) {
                Fail "hostile-URI event-log line has $($hostileFields.Count) fields, expected 9 (embedded tab was not escaped away, or review finding N3's `$status field is missing): $($hostileLines[0])"
            }
            elseif ($hostileFields[0] -ne "v1") {
                Fail "hostile-URI event-log line field 1 (version) was '$($hostileFields[0])', expected 'v1' -- format/version field did not survive"
            }
            elseif ($hostileFields[5] -notmatch '\\x09' -or $hostileFields[5] -notmatch '\\x0A') {
                Fail "hostile-URI event-log line's uri field does not show escaped \x09/\x0A -- escape=default may not be escaping control characters as expected: $($hostileFields[5])"
            }
            elseif ($hostileFields[8] -ne $hostileCode) {
                Fail "hostile-URI event-log line field 9 (HTTP status) was '$($hostileFields[8])', expected '$hostileCode' (the client-visible status curl reported) -- this is exactly the field N3 exists for: a non-2xx response must be identifiable, not counted as served traffic"
            }
            else {
                Pass "hostile URI produced exactly 1 well-formed line: 9 fields, version=v1, control characters escaped as \xXX, not raw, http_status=$($hostileFields[8]) correctly non-2xx: uri=$($hostileFields[5])"
            }
        }
    }

    # --- 14. WP 3.10 cache-event log: feature-off produces no file, config stays valid -
    Write-Host ""
    Write-Host "-- Test: cache-event log -- feature OFF (no directive) stays nginx -t green, no file --"
    $offFixtureConf = Join-Path $PSScriptRoot "fixtures\eventlog-off.conf"
    $offRigRoot = Join-Path $CoreRoot "_testcore_tmp\eventlog-off-rig"
    try {
        foreach ($dir in @(
            (Join-Path $offRigRoot "cache"),
            (Join-Path $offRigRoot "tmp\client_body"),
            (Join-Path $offRigRoot "tmp\proxy"),
            (Join-Path $offRigRoot "tmp\fastcgi"),
            (Join-Path $offRigRoot "tmp\uwsgi"),
            (Join-Path $offRigRoot "tmp\scgi"),
            (Join-Path $offRigRoot "logs")
        )) {
            if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        }

        # LEARNINGS (PowerShell 5.1) says "2>&1 on native commands wraps
        # stderr lines in NativeCommandError and kills the script under
        # $ErrorActionPreference=Stop" -- but empirically (measured while
        # fixing this test, not assumed from that wording) the trap is
        # BROADER than the "&1" merge case it names: `2>$null` and even
        # `2> file` ALSO raise a terminating NativeCommandError under Stop
        # the moment the native process writes anything to stderr, no
        # matter where that stream is redirected TO -- only NOT redirecting
        # stderr at all avoids it. nginx -t writes its "syntax is
        # ok"/"test is successful" result to stderr by design (confirmed:
        # a bare, unredirected call prints it and returns cleanly with
        # $LASTEXITCODE readable), so this call needs a LOCAL, restored
        # override to a non-terminating ErrorActionPreference around it
        # rather than a redirect -- redirecting is what breaks it here, not
        # what fixes it.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $NginxExe -p "`"$offRigRoot`"" -c "`"$offFixtureConf`"" -t 2>$null
            $offTestExit = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
        if ($offTestExit -eq 0) {
            Pass "nginx -t is green (exit 0) for a config with the cache-event access_log directive absent (feature off)"
        }
        else {
            Fail "nginx -t failed (exit $offTestExit) for the feature-off fixture"
        }

        Start-Process -FilePath $NginxExe `
            -ArgumentList @("-p", "`"$offRigRoot`"", "-c", "`"$offFixtureConf`"") `
            -WorkingDirectory $offRigRoot `
            -WindowStyle Hidden
        Start-Sleep -Milliseconds 600

        try {
            $r14 = Invoke-WebRequest -Uri "http://127.0.0.1:18103/" -UseBasicParsing -TimeoutSec 10
            if ($r14.StatusCode -eq 200) { Pass "feature-off fixture served a request normally (HTTP 200)" }
            else { Fail "feature-off fixture returned HTTP $($r14.StatusCode), expected 200" }
        }
        catch { Fail "feature-off fixture request threw: $_" }

        $offEventLog = Join-Path $offRigRoot "logs\event.log"
        if (Test-Path $offEventLog) {
            Fail "a logs/event.log file was created even though the fixture has no cache-event access_log directive: $offEventLog"
        }
        else {
            Pass "no logs/event.log file exists under the feature-off fixture root (no directive -> no file, ADR-0008 optional-at-runtime)"
        }
    }
    finally {
        Stop-NginxInstance $offRigRoot "`"$offFixtureConf`"" (Join-Path $offRigRoot "logs\nginx.pid")
    }

    # --- 15. WP TH-1a upstream rate cap: native build prerequisites ----------
    # The container renders a map on $connections_writing (a stub_status
    # variable) feeding `proxy_limit_rate $vault_upstream_rate` (variables
    # there need nginx >= 1.27.0). Verified for the container image
    # (poc/throttle/RESULTS-TH1-20261001.md); for THIS native binary it is
    # verified only when this suite runs. The native config itself carries
    # the identical directives with an uncapped static include, so the
    # Start-CoreNginx above already proves the include resolves.
    Write-Host ""
    Write-Host "-- Test: upstream rate cap prerequisites (WP TH-1a) -- stub_status, version floor, static include --"
    # nginx -V writes to stderr; see test 14 for why only a locally restored
    # "Continue" (not a redirect under Stop) is safe on PowerShell 5.1.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $nginxV = (& $NginxExe -V 2>&1 | ForEach-Object { "$_" }) -join "`n"
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($nginxV -match '--with-http_stub_status_module') {
        Pass "nginx -V lists --with-http_stub_status_module (`$connections_writing is available for the cap's divisor map)"
    }
    else {
        Fail "nginx -V does not list --with-http_stub_status_module -- a capped vault-upstream-rate.conf (map on `$connections_writing) would fail to load on this build"
    }
    if ($nginxV -match 'nginx/(\d+)\.(\d+)\.(\d+)') {
        $nginxVer = [System.Version]("{0}.{1}.{2}" -f $Matches[1], $Matches[2], $Matches[3])
        if ($nginxVer -ge [System.Version]"1.27.0") {
            Pass "nginx $nginxVer >= 1.27.0 (variables in proxy_limit_rate)"
        }
        else {
            Fail "nginx $nginxVer is below 1.27.0 -- proxy_limit_rate does not accept variables before that"
        }
    }
    else {
        Fail "could not read the nginx version from nginx -V output: $nginxV"
    }
    $nativeRateConf = Join-Path $CoreRoot "nginx\vault-upstream-rate.conf"
    if (-not (Test-Path $nativeRateConf)) {
        Fail "missing $nativeRateConf (included by core/nginx/nginx.conf)"
    }
    elseif ((Get-Content $nativeRateConf -Raw) -notmatch '(?m)^\s*default 0;\s*$') {
        Fail "$nativeRateConf is not the uncapped form ('default 0;') -- the native rig must not cap"
    }
    else {
        Pass "static native include present and uncapped (default 0)"
    }
}
finally {
    # --- teardown: always restore the machine to its pre-test state ----------
    Write-Host ""
    Write-Host "-- Teardown: stopping vault-core nginx, restoring PoC nginx on port 80 --" -ForegroundColor Cyan
    Stop-CoreNginx
    Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue
    & (Join-Path $PocRoot "start.ps1")
}

# --- verdict ------------------------------------------------------------------
Write-Host ""
if ($script:failures.Count -eq 0) {
    Write-Host "PASS - vault-core (WP 1.1) verified end-to-end against the real Steam CDN." -ForegroundColor Green
    exit 0
}
else {
    Write-Host "FAIL - $($script:failures.Count) check(s) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
