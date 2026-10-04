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
 *  - the key is embedded EXACTLY ONCE, single-quoted with every quote
 *    character doubled, after a line that switches off the history file;
 *  - PowerShell 5.1 only: no `&&`, `||`, `??`, `?.` or ternary outside
 *    string literals;
 *  - the agent server URL check (http/https + host, nothing else).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  AGENT_SERVER_URL_NOTE,
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

test("MUTATION TARGET: the key appears exactly once, quoted, after the history-off line", () => {
  const quoted = psQuote(KEY);
  assert.equal(quoted, "'k3y''with\u2019\u2019quotes&$dollar`tick\"dq'", "every single/typographic quote doubled; $ and ` are literal in single quotes");
  assert.equal(SNIPPET.split(quoted).length - 1, 1, "embedded once");
  assert.equal(SNIPPET.split("k3y").length - 1, 1, "no second copy in any other form");
  const keyLine = lines.findIndex((l) => l === `$apiKey = ${quoted}`);
  const historyOff = lines.findIndex((l) => l.includes("Set-PSReadLineOption -HistorySaveStyle SaveNothing"));
  const block = lines.indexOf("& {");
  assert.ok(historyOff >= 0 && historyOff < block && block < keyLine, "history off runs as its own line before the block");
  assert.equal(lines[lines.length - 1], "}", "the block closes at the end");
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

test("the server-URL note names the reverse-proxy pitfall", () => {
  assert.match(AGENT_SERVER_URL_NOTE, /direct LAN address/);
  assert.match(AGENT_SERVER_URL_NOTE, /not a reverse-proxy hostname/);
  assert.match(AGENT_SERVER_URL_NOTE, /bypass detection/);
});
