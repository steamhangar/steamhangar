/**
 * Settings → "Add a device" → Windows PC: the vault-agent install command
 * (WP PAIR-1).
 *
 * One PowerShell block the operator pastes into a normal (non-admin)
 * PowerShell 5.1 window on the gaming PC. It:
 *
 *  1. switches off PSReadLine's history FILE for that window (the block
 *     carries the API key once; without this it would be saved to
 *     ConsoleHost_history.txt) — a separate first line, so it runs before
 *     the block that holds the key is accepted;
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
 *  4. `Unblock-File`s them, copies the exe to
 *     `%LOCALAPPDATA%\VaultAgent\vault-agent.exe` (a stable path the task
 *     keeps across upgrades);
 *  5. writes the key to a temp file locked to the current user with icacls
 *     BEFORE the key is written (install-task.ps1's own, measured-on-
 *     Windows approach), runs `install-task.ps1 -AgentPath ... -ServerUrl
 *     ... -ApiKeyFile <temp>` in a child `powershell.exe -ExecutionPolicy
 *     Bypass` (agent/README.md "Running the scripts"), and deletes the temp
 *     file in a `finally`;
 *  6. starts the task `VaultAgentReport` once, so the PC reports now.
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

/**
 * The install command. `apiKey` is embedded exactly once (the `$apiKey`
 * line); see the module header for how it is kept out of the history file
 * and off the disk.
 *
 * @param {{version: string, serverUrl: string, apiKey: string}} args
 * @returns {string}
 */
export function windowsInstallSnippet({ version, serverUrl, apiKey }) {
  const assets = releaseAssets(version);
  const lines = [
    `# SteamHangar: install vault-agent ${version} for this Windows user (no admin rights needed).`,
    "# Paste into a normal PowerShell window. It contains the hangar API key: the first line",
    "# switches off this window's history file. Close the window when it is done.",
    "if (Get-Command Set-PSReadLineOption -ErrorAction SilentlyContinue) { Set-PSReadLineOption -HistorySaveStyle SaveNothing }",
    "& {",
    "$ErrorActionPreference = 'Stop'",
    "$ProgressPreference = 'SilentlyContinue'",
    "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12",
    `$version = ${psQuote(version)}`,
    `$serverUrl = ${psQuote(serverUrl)}`,
    `$apiKey = ${psQuote(apiKey)}`,
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
    "$agentPath = Join-Path $dir 'vault-agent.exe'",
    "Copy-Item -LiteralPath (Join-Path $kit $exeName) -Destination $agentPath -Force",
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
    "}",
    `Start-ScheduledTask -TaskName ${psQuote(AGENT_TASK_NAME)}`,
    "Write-Host 'vault-agent is installed and its first report has started. You can close this window.'",
    "}",
  ];
  return lines.join("\n");
}
