/**
 * web/js/lib/agent-install.js (WP PAIR-1): the Windows vault-agent install
 * command shown in Settings → "Add a device".
 *
 *  - release guard: only a release version WITH a commit (a v* tag build)
 *    gets a command; dev-<sha>, a native run (base version, no commit),
 *    "invalid", a missing vault-api row or a non-About answer get the note;
 *  - asset names and URLs equal publish.yml's agent-binaries naming (tag =
 *    "v" + version; the exe carries the tag), pinned as LITERALS;
 *  - the command checks every downloaded file against SHA256SUMS before it
 *    installs, unblocks them, passes -AgentPath/-ServerUrl/-ApiKeyFile to
 *    install-task.ps1 (whose param block is read from the real script),
 *    deletes the temp key file in a finally, starts the task once;
 *  - the command contains NO key (user decision "Weg A"): it asks with
 *    `Read-Host -AsSecureString`, frees the BSTR in a finally, refuses an
 *    empty or non-printable answer before downloading; even a key passed by
 *    mistake never reaches the text; `psQuote` doubles every quote kind;
 *  - PowerShell 5.1 only: no `&&`, `||`, `??`, `?.` or ternary outside
 *    string literals;
 *  - the agent server URL check (http/https + host, nothing else).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  AGENT_SERVER_URL_NOTE,
  installKey,
  isInstallableKey,
  agentReleaseFromAbout,
  noReleaseText,
  psQuote,
  releaseAssets,
  validateAgentServerUrl,
  windowsInstallSnippet,
} from "../js/lib/agent-install.js";

const here = dirname(fileURLToPath(import.meta.url));
const COMMIT = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0";
const about = (version, commit = COMMIT) => ({
  components: [
    { name: "vault-core", version: "9.9.9", commit: COMMIT, status: "unknown" },
    { name: "vault-api", version, commit, status: "ok" },
  ],
});

test("MUTATION TARGET: release guard — only a tag build (release version + commit) gets a command", () => {
  assert.deepEqual(agentReleaseFromAbout(about("0.1.0")), { ok: true, version: "0.1.0" });
  assert.deepEqual(agentReleaseFromAbout(about("0.1.0-rc10")), { ok: true, version: "0.1.0-rc10" });
  assert.deepEqual(agentReleaseFromAbout(about("1.2.3-beta.2")), { ok: true, version: "1.2.3-beta.2" });
  assert.deepEqual(agentReleaseFromAbout(about("dev-1a2b3c4")), { ok: false, version: "dev-1a2b3c4" });
  assert.deepEqual(agentReleaseFromAbout(about("0.1.0", null)), { ok: false, version: "0.1.0" }, "native run: no commit");
  assert.deepEqual(agentReleaseFromAbout(about("0.1.0", "invalid")), { ok: false, version: "0.1.0" });
  assert.deepEqual(agentReleaseFromAbout(about("invalid")), { ok: false, version: "invalid" });
  assert.deepEqual(agentReleaseFromAbout(about(null)), { ok: false, version: null });
  assert.deepEqual(agentReleaseFromAbout({ components: [] }), { ok: false, version: null });
  assert.deepEqual(agentReleaseFromAbout(null), { ok: false, version: null });
  assert.deepEqual(agentReleaseFromAbout({ detail: "Not Found" }), { ok: false, version: null });
});

test("no-release note names the version and points at agent/README.md", () => {
  assert.match(noReleaseText("dev-1a2b3c4"), /version "dev-1a2b3c4", which is not a published release/);
  assert.match(noReleaseText(null), /a version this page cannot read/);
  assert.match(noReleaseText("x"), /agent\/README\.md/);
});

test("MUTATION TARGET: asset names follow publish.yml (tag v<version>, exe carries the tag)", () => {
  assert.deepEqual(releaseAssets("0.1.0-rc10"), {
    tag: "v0.1.0-rc10",
    exe: "vault-agent-v0.1.0-rc10-windows-amd64.exe",
    scripts: ["install-task.ps1", "run-vault-agent.ps1", "uninstall-task.ps1"],
    sums: "SHA256SUMS",
  });
  // The workflow really names them so (read, not assumed).
  const wf = readFileSync(join(here, "..", "..", ".github", "workflows", "publish.yml"), "utf8");
  assert.match(wf, /name="vault-agent-\$\{VERSION\}-\$\{goos\}-\$\{goarch\}\$\{ext\}"/);
  assert.match(wf, /version="\$\{GITHUB_REF_NAME\/\/\\\/\/-\}"/);
  assert.match(wf, /cp "\$GITHUB_WORKSPACE"\/agent\/packaging\/windows\/\*\.ps1 \./);
  assert.match(wf, /> SHA256SUMS/);
});

const KEY = "k3y'with\u2019quotes&$dollar`tick\"dq";
// `apiKey` is passed on purpose: the function must ignore it (Weg A).
const SNIPPET = windowsInstallSnippet({ version: "0.1.0-rc10", serverUrl: "http://192.0.2.10:8080", apiKey: KEY });
const lines = SNIPPET.split("\n");

test("MUTATION TARGET: the command downloads the release assets from the project's GitHub release", () => {
  assert.ok(lines.includes("$version = '0.1.0-rc10'"));
  assert.ok(lines.includes("$base = 'https://github.com/steamhangar/steamhangar/releases/download/v0.1.0-rc10'"));
  assert.ok(lines.includes("$exeName = 'vault-agent-v0.1.0-rc10-windows-amd64.exe'"));
  assert.ok(lines.includes("$files = @($exeName, 'install-task.ps1', 'run-vault-agent.ps1', 'uninstall-task.ps1')"));
  assert.match(SNIPPET, /foreach \(\$n in \(\$files \+ 'SHA256SUMS'\)\) \{ Invoke-WebRequest -UseBasicParsing -Uri \(\$base \+ '\/' \+ \$n\) -OutFile \(Join-Path \$kit \$n\) \}/);
  assert.ok(lines.includes("$dir = Join-Path $env:LOCALAPPDATA 'VaultAgent'"), "under the user profile");
});

test("MUTATION TARGET: every downloaded file is checked against SHA256SUMS before anything is installed", () => {
  const check = lines.findIndex((l) => l.includes("Get-FileHash -Algorithm SHA256"));
  const fail = lines.findIndex((l) => l.includes("if ($want[$n] -ne $got) { throw"));
  const unblock = lines.findIndex((l) => l.includes("Unblock-File"));
  const install = lines.findIndex((l) => l.includes("install-task.ps1') -AgentPath"));
  assert.ok(check > 0 && fail > check, "hash computed, then compared with a throw");
  assert.ok(lines[check - 1] === "foreach ($n in $files) {", "for every file (exe and scripts)");
  assert.ok(unblock > fail && install > unblock, "unblock and install only after the check");
  assert.ok(lines.includes("$ErrorActionPreference = 'Stop'"), "a throw stops the block");
});

test("MUTATION TARGET: install-task.ps1 gets -AgentPath, -ServerUrl, -ApiKeyFile; the temp key file goes in a finally", () => {
  const install = lines.find((l) => l.includes("-File (Join-Path $kit 'install-task.ps1')"));
  assert.equal(
    install.trim(),
    "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit 'install-task.ps1') -AgentPath $agentPath -ServerUrl $serverUrl -ApiKeyFile $keyFile",
  );
  const params = readFileSync(join(here, "..", "..", "agent", "packaging", "windows", "install-task.ps1"), "utf8");
  for (const p of ["AgentPath", "ServerUrl", "ApiKeyFile"]) assert.match(params, new RegExp(`\\[string\\]\\$${p},?\\n`), `install-task.ps1 has -${p}`);
  const lock = lines.findIndex((l) => l.includes("icacls $keyFile /inheritance:r"));
  const write = lines.findIndex((l) => l.includes("Set-Content -LiteralPath $keyFile -Value $apiKey"));
  const fin = lines.findIndex((l) => l === "} finally {");
  assert.ok(lock > 0 && write > lock, "the ACL is locked before the key is written");
  assert.equal(lines[fin + 1].trim(), "Remove-Item -LiteralPath $keyFile -Force -ErrorAction SilentlyContinue");
  assert.ok(lines.includes("Start-ScheduledTask -TaskName 'VaultAgentReport'"), "the task runs once");
  assert.ok(lines.includes("$serverUrl = 'http://192.0.2.10:8080'"));
});

test("MUTATION TARGET: the exe goes to a VERSIONED path, never over a running exe of another version", () => {
  assert.ok(lines.includes("$agentPath = Join-Path $dir ('vault-agent-v' + $version + '.exe')"));
  assert.equal(SNIPPET.includes("'vault-agent.exe'"), false, "no fixed, overwritable path");
  const copy = lines.findIndex((l) => l.includes("Copy-Item -LiteralPath $kitExe -Destination $agentPath"));
  assert.ok(lines[copy].startsWith("if (-not $sameExe) {"), "a byte-identical exe (same-version re-install) is not copied again");
  assert.ok(lines[copy - 1].includes("$sameExe = (Get-FileHash -Algorithm SHA256 -LiteralPath $agentPath).Hash -eq (Get-FileHash -Algorithm SHA256 -LiteralPath $kitExe).Hash"));
});

test("MUTATION TARGET: after a successful install the download folder is removed; uninstall-task.ps1 is kept", () => {
  const install = lines.findIndex((l) => l.includes("-File (Join-Path $kit 'install-task.ps1')"));
  const keep = lines.indexOf("Copy-Item -LiteralPath (Join-Path $kit 'uninstall-task.ps1') -Destination (Join-Path $dir 'uninstall-task.ps1') -Force");
  const remove = lines.findIndex((l) => l.includes("Remove-Item -LiteralPath $kit -Recurse -Force"));
  const start = lines.indexOf("Start-ScheduledTask -TaskName 'VaultAgentReport'");
  assert.ok(install > 0 && keep > install && remove > keep && start > remove, "install, keep the uninstaller, remove the kit, start");
  assert.equal(lines[remove].startsWith(" "), false, "outside the try/finally: a failed install keeps the folder for inspection");
});

test("MUTATION TARGET: WEB-FIX-10 — a kit folder that cannot be removed warns and still starts the task", () => {
  const remove = lines.findIndex((l) => l.includes("Remove-Item -LiteralPath $kit"));
  const start = lines.indexOf("Start-ScheduledTask -TaskName 'VaultAgentReport'");
  assert.equal(
    lines[remove],
    "try { Remove-Item -LiteralPath $kit -Recurse -Force -ErrorAction Stop } catch { Write-Warning ('Could not remove the download folder ' + $kit + ' (' + $_.Exception.Message + '). The agent is installed; delete the folder by hand later.') }",
    "the cleanup's error is caught (ErrorActionPreference is Stop for the whole block) and reported as a warning",
  );
  assert.equal(start, remove + 1, "the task start follows the guarded cleanup directly");
  assert.equal(lines.filter((l) => l.includes("Remove-Item -LiteralPath $kit")).length, 1, "no second, unguarded cleanup");
});

test("psQuote doubles every quote character PowerShell accepts", () => {
  assert.equal(psQuote(KEY), "'k3y''with\u2019\u2019quotes&$dollar`tick\"dq'", "$ and ` are literal in single quotes");
  assert.equal(psQuote("a\u2018b\u2019c\u201ad\u201be'f"), "'a\u2018\u2018b\u2019\u2019c\u201a\u201ad\u201b\u201be''f'");
  assert.equal(psQuote("\u201a"), "'\u201a\u201a'");
  assert.equal(psQuote("\u201b"), "'\u201b\u201b'");
});

test("MUTATION TARGET: Weg A — the command contains no key in any form", () => {
  for (const form of [KEY, psQuote(KEY), "k3y", encodeURIComponent(KEY)]) {
    assert.equal(SNIPPET.includes(form), false, `no ${JSON.stringify(form)}`);
  }
  assert.equal(windowsInstallSnippet({ version: "0.1.0-rc10", serverUrl: "http://192.0.2.10:8080" }), SNIPPET, "the key argument changes nothing");
  assert.equal(lines.some((l) => /^\$apiKey = '/.test(l)), false, "no key literal");
  assert.equal(SNIPPET.includes("Set-PSReadLineOption"), false, "no history claim: nothing secret to keep out");
});

test("MUTATION TARGET: Weg A — the key is asked for as a SecureString and checked before anything is downloaded", () => {
  const ask = lines.indexOf("$secureKey = Read-Host 'Hangar API key (paste it, then press Enter)' -AsSecureString");
  const bstr = lines.indexOf("$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)");
  const plain = lines.indexOf("try { $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }");
  const trim = lines.indexOf("$apiKey = ([string]$apiKey).Trim()");
  const check = lines.indexOf("if ([string]::IsNullOrEmpty($apiKey) -or $apiKey -cnotmatch '^[\\x20-\\x7E]+$') { throw 'No usable API key was entered (empty or not printable ASCII). Nothing was installed.' }");
  const download = lines.findIndex((l) => l.includes("Invoke-WebRequest"));
  assert.ok(ask > lines.indexOf("& {"), "inside the block, so the paste is complete before it asks");
  assert.ok(bstr === ask + 1 && plain === bstr + 1, "converted in memory, BSTR zero-freed in a finally");
  assert.ok(trim > plain && check > trim && check < download, "edge-trimmed, then refused before any download");
  const fin = lines.findIndex((l) => l === "} finally {");
  assert.equal(lines[fin + 2].trim(), "$apiKey = $null", "the variable is cleared at the end");
  assert.equal(lines[lines.length - 1], "}", "the block closes at the end");
  // The command's check and the sheet's isInstallableKey are one rule.
  for (const k of ["abc", "a b!~", " abc ", "\tabc\r\n"]) assert.equal(isInstallableKey(k), true, JSON.stringify(k));
  for (const k of ["", " ", "\t\n", "ü", "a\tb", "a\u007fb", null]) assert.equal(isInstallableKey(k), false, JSON.stringify(k));
});

test("MUTATION TARGET: WEB-FIX-10 — the key's edge whitespace is handled like install-task.ps1 (trim edges, keep inner spaces)", () => {
  // install-task.ps1: `$resolvedApiKey = (Get-Content -LiteralPath $ApiKeyFile -Raw).Trim()`,
  // inner spaces kept. The command trims what was pasted; Copy key copies the trimmed key.
  const ps = readFileSync(join(here, "..", "..", "agent", "packaging", "windows", "install-task.ps1"), "utf8");
  assert.match(ps, /\$resolvedApiKey = \(Get-Content -LiteralPath \$ApiKeyFile -Raw\)\.Trim\(\)/, "install-task.ps1 still trims the key file");
  assert.equal(installKey("  ab cd \t\r\n"), "ab cd");
  assert.equal(installKey("abc"), "abc");
  assert.equal(installKey(null), null);
  assert.equal(installKey(42), null);
  // The command's check runs on the trimmed value.
  const trim = lines.indexOf("$apiKey = ([string]$apiKey).Trim()");
  assert.ok(trim > 0 && lines[trim + 1].includes("$apiKey -cnotmatch '^[\\x20-\\x7E]+$'"), "trim directly before the check");
});

test("MUTATION TARGET: the command is ONE top-level statement, `& { ... }`, so a line-by-line paste runs nothing before the end", () => {
  const nonEmpty = lines.filter((l) => l.trim() !== "");
  assert.equal(nonEmpty[0], "& {", "first non-empty line opens the block");
  assert.equal(nonEmpty[nonEmpty.length - 1], "}", "last non-empty line closes it");
  // Parse-level: with string literals and comments removed, the brace depth
  // never returns to 0 before the last line, i.e. no second top-level
  // statement (a comment line, a Read-Host) sits outside the block.
  let depth = 0;
  nonEmpty.forEach((line, i) => {
    const code = line.trim().startsWith("#") ? "" : line.replace(/'(?:[^']|'')*'/g, "''").replace(/"[^"]*"/g, '""');
    if (i > 0) assert.ok(depth > 0, `line ${i + 1} is outside the block: ${line}`);
    for (const ch of code) {
      if (ch === "{") depth++;
      else if (ch === "}") depth--;
    }
    if (i < nonEmpty.length - 1) assert.ok(depth > 0, `the block closes early at line ${i + 1}: ${line}`);
  });
  assert.equal(depth, 0, "balanced at the end");
  const ask = lines.findIndex((l) => l.includes("Read-Host"));
  assert.ok(ask > 0 && lines[ask].startsWith("$secureKey"), "the prompt is inside the block");
});

test("PowerShell 5.1: no &&, ||, ??, ?. or ternary outside string literals", () => {
  for (const line of lines) {
    if (line.startsWith("#")) continue;
    const code = line.replace(/'(?:[^']|'')*'/g, "''").replace(/"[^"]*"/g, '""');
    assert.doesNotMatch(code, /&&|\|\||\?\?|\?\.|\s\?\s/, line);
  }
  // Braces balance (a paste of an unbalanced block hangs at ">>").
  const code = lines
    .filter((l) => !l.startsWith("#"))
    .map((l) => l.replace(/'(?:[^']|'')*'/g, "''").replace(/"[^"]*"/g, '""'))
    .join("\n");
  assert.ok((code.match(/\{/g) || []).length > 5, "the stripped code still has its blocks");
  assert.equal((code.match(/\{/g) || []).length, (code.match(/\}/g) || []).length);
});

test("MUTATION TARGET: agent server URL — http/https with a host, nothing else", () => {
  assert.deepEqual(validateAgentServerUrl("http://192.0.2.10:8080"), { ok: true, url: "http://192.0.2.10:8080" });
  assert.deepEqual(validateAgentServerUrl("  https://hangar.lan/  "), { ok: true, url: "https://hangar.lan" });
  assert.deepEqual(validateAgentServerUrl("http://[2001:db8::1]:8080"), { ok: true, url: "http://[2001:db8::1]:8080" });
  for (const bad of ["", "   ", "hangar.lan:8080", "ftp://hangar.lan", "file:///C:/x", "http://", "http://u:p@hangar.lan", "http://hangar.lan/api", "http://hangar.lan/?x=1", "http://hangar.lan/#k"]) {
    const r = validateAgentServerUrl(bad);
    assert.equal(r.ok, false, `refused: ${JSON.stringify(bad)}`);
    assert.equal(typeof r.message, "string");
  }
  assert.equal(validateAgentServerUrl(undefined).ok, false);
});

// Review finding 6: the generated command, with a dummy key, as a committed
// fixture that CI's powershell-syntax job parses with Windows PowerShell
// 5.1's own parser (.github/scripts/verify-ps-parse.ps1, never executed).
// This test regenerates it and fails on any drift; refresh it with
//   UPDATE_PS_FIXTURE=1 node --test web/tests/agent-install.test.js
const FIXTURE = join(here, "fixtures", "windows-install-command.ps1");
const FIXTURE_ARGS = Object.freeze({
  version: "0.1.0-rc10",
  serverUrl: "http://192.0.2.10:8080",
});

test("MUTATION TARGET: the committed Windows command fixture equals what the page generates", () => {
  const generated = windowsInstallSnippet(FIXTURE_ARGS) + "\n";
  if (process.env.UPDATE_PS_FIXTURE === "1") writeFileSync(FIXTURE, generated);
  const committed = readFileSync(FIXTURE, "utf8");
  assert.equal(committed, generated, "fixture drifted: regenerate it (see the comment above) and commit it");
  assert.equal(/[^\x00-\x7f]/.test(committed), false, "pure ASCII, so 5.1 parses it the same under any codepage");
  assert.equal(/\$apiKey = '/.test(committed), false, "no key literal in the fixture");
  assert.match(committed, /Read-Host 'Hangar API key \(paste it, then press Enter\)' -AsSecureString/);
  const parseStep = readFileSync(join(here, "..", "..", ".github", "scripts", "verify-ps-parse.ps1"), "utf8");
  assert.match(parseStep, /web\\tests\\fixtures\\windows-install-command\.ps1/, "CI parses the fixture");
});

test("the server-URL note names the reverse-proxy pitfall", () => {
  assert.match(AGENT_SERVER_URL_NOTE, /direct LAN address/);
  assert.match(AGENT_SERVER_URL_NOTE, /not a reverse-proxy hostname/);
  assert.match(AGENT_SERVER_URL_NOTE, /bypass detection/);
});
