# SteamHangar: install vault-agent 0.1.0-rc10 for this Windows user (no admin rights needed).
# Paste into a normal PowerShell window. It contains the hangar API key: the first line
# switches off this window's history file. Close the window when it is done.
if (Get-Command Set-PSReadLineOption -ErrorAction SilentlyContinue) { Set-PSReadLineOption -HistorySaveStyle SaveNothing }
& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$version = '0.1.0-rc10'
$serverUrl = 'http://192.0.2.10:8080'
$apiKey = 'DUMMY-not-a-real-key_0123''quote'
$base = 'https://github.com/steamhangar/steamhangar/releases/download/v0.1.0-rc10'
$exeName = 'vault-agent-v0.1.0-rc10-windows-amd64.exe'
$dir = Join-Path $env:LOCALAPPDATA 'VaultAgent'
$kit = Join-Path $dir ('release-v' + $version)
New-Item -ItemType Directory -Force -Path $kit | Out-Null
$files = @($exeName, 'install-task.ps1', 'run-vault-agent.ps1', 'uninstall-task.ps1')
foreach ($n in ($files + 'SHA256SUMS')) { Invoke-WebRequest -UseBasicParsing -Uri ($base + '/' + $n) -OutFile (Join-Path $kit $n) }
$want = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $kit 'SHA256SUMS')) {
  $parts = $line.Trim() -split '\s+', 2
  if ($parts.Count -eq 2) { $want[$parts[1].TrimStart('*')] = $parts[0].ToLowerInvariant() }
}
foreach ($n in $files) {
  $got = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $kit $n)).Hash.ToLowerInvariant()
  if ($want[$n] -ne $got) { throw ('SHA256 check failed for ' + $n + '. Nothing was installed.') }
}
Get-ChildItem -LiteralPath $kit | Unblock-File
$kitExe = Join-Path $kit $exeName
$agentPath = Join-Path $dir ('vault-agent-v' + $version + '.exe')
$sameExe = $false
if (Test-Path -LiteralPath $agentPath) { $sameExe = (Get-FileHash -Algorithm SHA256 -LiteralPath $agentPath).Hash -eq (Get-FileHash -Algorithm SHA256 -LiteralPath $kitExe).Hash }
if (-not $sameExe) { Copy-Item -LiteralPath $kitExe -Destination $agentPath -Force }
$keyFile = Join-Path $kit ('key-' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
  New-Item -ItemType File -Path $keyFile | Out-Null
  icacls $keyFile /inheritance:r /grant:r "${env:USERDOMAIN}\${env:USERNAME}:(F)" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'icacls could not lock the temporary key file.' }
  Set-Content -LiteralPath $keyFile -Value $apiKey -Encoding utf8
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install-task.ps1') -AgentPath $agentPath -ServerUrl $serverUrl -ApiKeyFile $keyFile
  if ($LASTEXITCODE -ne 0) { throw ('install-task.ps1 failed with exit code ' + $LASTEXITCODE + '.') }
} finally {
  Remove-Item -LiteralPath $keyFile -Force -ErrorAction SilentlyContinue
}
Copy-Item -LiteralPath (Join-Path $kit 'uninstall-task.ps1') -Destination (Join-Path $dir 'uninstall-task.ps1') -Force
Remove-Item -LiteralPath $kit -Recurse -Force
Start-ScheduledTask -TaskName 'VaultAgentReport'
Write-Host 'vault-agent is installed and its first report has started. You can close this window.'
}
