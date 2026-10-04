/**
 * Settings → "Add a device" → Windows PC: the vault-agent install command
 * (WP PAIR-1).
 *
 * One PowerShell block the operator pastes into a normal (non-admin)
 * PowerShell 5.1 window on the gaming PC. THE BLOCK CONTAINS NO KEY (user
 * decision "Weg A" on review finding 1, 2026-10-04). It:
 *
 *  1. asks for the key with `Read-Host -AsSecureString` (the window echoes
 *     only asterisks; the user pastes it from the sheet's separate
 *     "Copy key" button), converts it in memory (`SecureStringToBSTR`,
 *     `PtrToStringBSTR`, `ZeroFreeBSTR` in a `finally`) and refuses an
 *     empty or non-printable-ASCII answer before anything is downloaded;
 *  2. downloads `vault-agent-v<version>-windows-amd64.exe`,
 *     `install-task.ps1`, `run-vault-agent.ps1`, `uninstall-task.ps1` and
 *     `SHA256SUMS` from the GitHub Release `v<version>` (asset names as
 *     .github/workflows/publish.yml's agent-binaries job writes them: the
 *     tag is "v" + the version vault-api reports, the exe carries the tag)
 *     into `%LOCALAPPDATA%\VaultAgent\release-v<version>` (under the user
 *     profile; agent/README.md: a folder under C:\ would inherit an ACL that
 *     lets every local user swap the binary);
 *  3. checks all four files against `SHA256SUMS` with `Get-FileHash` and
 *     stops before installing anything on a mismatch. Honest limit: the sums
 *     come from the same release, so this catches a broken or swapped
 *     download, not a compromised release;
 *  4. `Unblock-File`s them, copies the exe to the VERSIONED path
 *     `%LOCALAPPDATA%\VaultAgent\vault-agent-v<version>.exe` (review
 *     finding 4: a re-install of another version never overwrites an exe
 *     the task may be running right now; install-task.ps1 re-points the
 *     task through -AgentPath; a same-version re-install skips the copy when
 *     the file is already byte-identical). Older versioned exes are left in
 *     place;
 *  5. writes the key to a temp file locked to the current user with icacls
 *     BEFORE the key is written (install-task.ps1's own, measured-on-
 *     Windows approach), runs `install-task.ps1 -AgentPath ... -ServerUrl
 *     ... -ApiKeyFile <temp>` in a child `powershell.exe -ExecutionPolicy
 *     Bypass` (agent/README.md "Running the scripts"), and deletes the temp
 *     file in a `finally`;
 *  6. copies `uninstall-task.ps1` next to the exe (it needs nothing beside
 *     it) and removes the download folder (review finding 9), then starts
 *     the task `VaultAgentReport` once, so the PC reports now. On a failure
 *     the folder stays, so the downloaded files can be inspected.
 *
 * What this guarantees, and what it does not (review findings 1 and 5):
 * the key is never part of the pasted text. So the PSReadLine history file
 * (ConsoleHost_history.txt, written as one item when a block is pasted),
 * a PowerShell transcript of the command, and a script-block log record
 * (event 4104, Microsoft-Windows-PowerShell/Operational, by policy or by
 * 5.1's automatic logging of "suspicious" blocks) hold the command but not
 * the key. Read-Host input is not added to the history. Residuals: the
 * "Copy key" copy sits in the clipboard (and in clipboard history or cloud
 * clipboard sync where those are on) until something else is copied; the
 * key exists as a plain string in this PowerShell process while it runs
 * (`$apiKey` is cleared at the end, the .NET string itself cannot be
 * wiped); and it is on disk in the owner-only temp file for the seconds
 * install-task.ps1 needs, then in install-task.ps1's own owner-only
 * env.txt, as with any install. The command no longer touches the history
 * setting: with no secret in it there is nothing to keep out.
 *
 * install-task.ps1 finds the Steam library itself (registry SteamPath,
 * AGENT-FIX-2), so no library path is asked here.
 *
 * Server URL (coordinator note, 2026-10-04, from the real install): the
 * agent must reach vault-api DIRECTLY, not through a reverse proxy.
 * vault-api records the TCP peer address of each agent report (uvicorn runs
 * with --no-proxy-headers on purpose) and matches it against cache traffic;
 * through a proxy every PC shows the proxy's address and per-PC stats and
 * bypass detection cannot match it. The web UI is often served through a
 * proxy, so the page origin is only the PREFILL of an editable field, and
 * the field's value is remembered per browser (not a secret).
 *
 * PowerShell 5.1 only constructs are used: no `&&`/`||`, no ternary, no
 * `??`. Values are embedded as single-quoted literals ({@link psQuote}).
 *
 * Pure: no DOM, no storage, no fetch.
 */

export const RELEASE_DOWNLOAD_BASE = "https://github.com/steamhangar/steamhangar/releases/download";
export const AGENT_TASK_NAME = "VaultAgentReport";
export const AGENT_README_URL = "https://github.com/steamhangar/steamhangar/blob/main/agent/README.md";
/** localStorage key of the remembered agent server URL (not a secret). */
export const AGENT_SERVER_URL_STORAGE_KEY = "steamvault.agentServerUrl";

export const AGENT_SERVER_URL_NOTE =
  "Must be vault-api's direct LAN address (e.g. http://<host-ip>:8080), not a reverse-proxy hostname. Otherwise per-PC cache stats and bypass detection cannot match this PC.";

// A release version as publish.yml derives it from a v* tag: digits-dot
// triple, optional pre-release (e.g. "0.1.0", "0.1.0-rc10").
const RELEASE_VERSION = /^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$/;
const COMMIT = /^[0-9a-f]{7,40}$/;

/**
 * The vault-api release version from a `GET /v1/about` answer, if it names a
 * downloadable release.
 *
 * A build from a v* tag reports the tag without its "v" plus the commit
 * (WP VER-1). Anything else has no release to download from: a CI build of
 * a branch reports `dev-<sha>`, a native run reports the source tree's
 * base version WITHOUT a commit (indistinguishable from a release by the
 * version alone, so the commit is required too), an unusable baked value
 * reports `"invalid"`, and a server older than `/v1/about` has no answer.
 *
 * @param {unknown} response
 * @returns {{ok: true, version: string} | {ok: false, version: string | null}}
 */
export function agentReleaseFromAbout(response) {
  const components = response && typeof response === "object" && Array.isArray(response.components) ? response.components : [];
  const api = components.find((c) => c && c.name === "vault-api");
  const version = api && typeof api.version === "string" ? api.version : null;
  const commit = api && typeof api.commit === "string" ? api.commit : null;
  if (version && RELEASE_VERSION.test(version) && commit && COMMIT.test(commit)) return { ok: true, version };
  return { ok: false, version };
}

/** Text shown instead of the command when there is no release to use. */
export function noReleaseText(version) {
  const what = version ? `version "${version}"` : "a version this page cannot read";
  return (
    `This server reports ${what}, which is not a published release, so there is no matching vault-agent download. ` +
    "Install the agent by hand as agent/README.md describes, or use a released server image."
  );
}

/** Release asset names for a version (publish.yml agent-binaries). */
export function releaseAssets(version) {
  return {
    tag: `v${version}`,
    exe: `vault-agent-v${version}-windows-amd64.exe`,
    scripts: ["install-task.ps1", "run-vault-agent.ps1", "uninstall-task.ps1"],
    sums: "SHA256SUMS",
  };
}

/**
 * Check an agent server URL typed by the operator: http or https, a host,
 * no credentials, query or fragment, no path beyond "/". Returns the
 * normalized origin (no trailing slash).
 * @param {unknown} text
 * @returns {{ok: true, url: string} | {ok: false, message: string}}
 */
export function validateAgentServerUrl(text) {
  const raw = typeof text === "string" ? text.trim() : "";
  if (!raw) return { ok: false, message: "Enter the vault-api address, e.g. http://192.168.1.20:8080." };
  let url;
  try {
    url = new URL(raw);
  } catch {
    return { ok: false, message: "Not a valid address. Use the form http://<host>:<port>." };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, message: "The address must start with http:// or https://." };
  }
  if (!url.hostname) return { ok: false, message: "The address needs a host name or IP address." };
  if (url.username || url.password) return { ok: false, message: "The address must not contain a user name or password." };
  if ((url.pathname && url.pathname !== "/") || url.search || url.hash) {
    return { ok: false, message: "Use the server address only, without a path, query or #fragment." };
  }
  return { ok: true, url: url.origin };
}

/**
 * A PowerShell single-quoted string literal. Inside one only the quote
 * itself is special, and PowerShell treats the typographic quotes
 * U+2018..U+201B as quotes too, so every one of them is doubled.
 * @param {string} value
 */
export function psQuote(value) {
  return `'${String(value).replace(/['\u2018\u2019\u201A\u201B]/g, (q) => q + q)}'`;
}

/** The prompt the command shows; the sheet's text names it too. */
export const KEY_PROMPT = "Hangar API key (paste it, then press Enter)";

/**
 * The key rule of the install command: printable ASCII (what an HTTP header
 * value can carry unchanged). The sheet shows a note instead of the command
 * for a stored key outside it. Same rule as the command's own check.
 * @param {unknown} key
 */
export function isInstallableKey(key) {
  return typeof key === "string" && /^[\x20-\x7e]+$/.test(key);
}

/**
 * How the command gets the key into `$apiKey` (user decision "Weg A"): it
 * asks for it. Nothing about the key is in the command text.
 * @returns {string[]}
 */
function keySourceLines() {
  return [
    `$secureKey = Read-Host ${psQuote(KEY_PROMPT)} -AsSecureString`,
    "$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)",
    "try { $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }",
    "$secureKey.Dispose()",
    "if ([string]::IsNullOrEmpty($apiKey) -or $apiKey -cnotmatch '^[\\x20-\\x7E]+$') { throw 'No usable API key was entered (empty or not printable ASCII). Nothing was installed.' }",
  ];
}

/**
 * The install command. It takes no key: see the module header.
 *
 * @param {{version: string, serverUrl: string}} args
 * @returns {string}
 */
export function windowsInstallSnippet({ version, serverUrl }) {
  const assets = releaseAssets(version);
  const lines = [
    `# SteamHangar: install vault-agent ${version} for this Windows user (no admin rights needed).`,
    "# Paste into a normal PowerShell window. It contains no key: it asks for the hangar API key",
    "# (use the Copy key button in SteamHangar, then paste at the prompt).",
    "& {",
    "$ErrorActionPreference = 'Stop'",
    "$ProgressPreference = 'SilentlyContinue'",
    "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12",
    `$version = ${psQuote(version)}`,
    `$serverUrl = ${psQuote(serverUrl)}`,
    ...keySourceLines(),
    `$base = ${psQuote(`${RELEASE_DOWNLOAD_BASE}/${assets.tag}`)}`,
    `$exeName = ${psQuote(assets.exe)}`,
    "$dir = Join-Path $env:LOCALAPPDATA 'VaultAgent'",
    "$kit = Join-Path $dir ('release-v' + $version)",
    "New-Item -ItemType Directory -Force -Path $kit | Out-Null",
    `$files = @($exeName, ${assets.scripts.map(psQuote).join(", ")})`,
    `foreach ($n in ($files + ${psQuote(assets.sums)})) { Invoke-WebRequest -UseBasicParsing -Uri ($base + '/' + $n) -OutFile (Join-Path $kit $n) }`,
    "$want = @{}",
    `foreach ($line in Get-Content -LiteralPath (Join-Path $kit ${psQuote(assets.sums)})) {`,
    "  $parts = $line.Trim() -split '\\s+', 2",
    "  if ($parts.Count -eq 2) { $want[$parts[1].TrimStart('*')] = $parts[0].ToLowerInvariant() }",
    "}",
    "foreach ($n in $files) {",
    "  $got = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $kit $n)).Hash.ToLowerInvariant()",
    "  if ($want[$n] -ne $got) { throw ('SHA256 check failed for ' + $n + '. Nothing was installed.') }",
    "}",
    "Get-ChildItem -LiteralPath $kit | Unblock-File",
    "$kitExe = Join-Path $kit $exeName",
    "$agentPath = Join-Path $dir ('vault-agent-v' + $version + '.exe')",
    "$sameExe = $false",
    "if (Test-Path -LiteralPath $agentPath) { $sameExe = (Get-FileHash -Algorithm SHA256 -LiteralPath $agentPath).Hash -eq (Get-FileHash -Algorithm SHA256 -LiteralPath $kitExe).Hash }",
    "if (-not $sameExe) { Copy-Item -LiteralPath $kitExe -Destination $agentPath -Force }",
    "$keyFile = Join-Path $kit ('key-' + [guid]::NewGuid().ToString('N') + '.tmp')",
    "try {",
    "  New-Item -ItemType File -Path $keyFile | Out-Null",
    "  icacls $keyFile /inheritance:r /grant:r \"${env:USERDOMAIN}\\${env:USERNAME}:(F)\" | Out-Null",
    "  if ($LASTEXITCODE -ne 0) { throw 'icacls could not lock the temporary key file.' }",
    "  Set-Content -LiteralPath $keyFile -Value $apiKey -Encoding utf8",
    "  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install-task.ps1') -AgentPath $agentPath -ServerUrl $serverUrl -ApiKeyFile $keyFile",
    "  if ($LASTEXITCODE -ne 0) { throw ('install-task.ps1 failed with exit code ' + $LASTEXITCODE + '.') }",
    "} finally {",
    "  Remove-Item -LiteralPath $keyFile -Force -ErrorAction SilentlyContinue",
    "  $apiKey = $null",
    "}",
    "Copy-Item -LiteralPath (Join-Path $kit 'uninstall-task.ps1') -Destination (Join-Path $dir 'uninstall-task.ps1') -Force",
    "Remove-Item -LiteralPath $kit -Recurse -Force",
    `Start-ScheduledTask -TaskName ${psQuote(AGENT_TASK_NAME)}`,
    "Write-Host 'vault-agent is installed and its first report has started. You can close this window.'",
    "}",
  ];
  return lines.join("\n");
}
