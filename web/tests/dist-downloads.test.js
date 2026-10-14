/**
 * WP DIST-1b: web/js/lib/dist-downloads.js (where the "Add a device" sheet
 * downloads the app and the agent from) and the Linux download command in
 * web/js/lib/agent-install.js.
 *
 *  - app: baked -> page origin + the version-free alias; not baked on a
 *    release server -> the GitHub release asset; dev server -> none;
 *  - agent: every needed file baked -> <agent address>/dist; otherwise the
 *    GitHub release of the same version; dev -> none; a partial bake is
 *    NOT mixed with GitHub;
 *  - fail closed: a local path off /dist/, a foreign or other-version
 *    release URL, a renamed file or a malformed index count as missing;
 *  - the Linux command, RUN in sh with a fake curl: good sums pass and make
 *    the binary executable, a bad or missing sum stops it before chmod.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, writeFileSync, mkdirSync, statSync, existsSync, rmSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  agentDownloadPlan,
  appDownloadPlan,
  formatDownloadSize,
  linuxArtifactNames,
  noAppText,
  readDistIndex,
  windowsArtifactNames,
} from "../js/lib/dist-downloads.js";
import { linuxDownloadSnippet, shQuote, windowsInstallSnippet } from "../js/lib/agent-install.js";

const V = "0.1.0-rc18";
const GH = `https://github.com/steamhangar/steamhangar/releases/download/v${V}/`;
const NAMES = [
  ["app-android", `steamhangar-app-v${V}.apk`],
  ["agent-windows-amd64", `vault-agent-v${V}-windows-amd64.exe`],
  ["agent-linux-amd64", `vault-agent-v${V}-linux-amd64`],
  ["agent-linux-arm64", `vault-agent-v${V}-linux-arm64`],
  ["install-task", "install-task.ps1"],
  ["run-vault-agent", "run-vault-agent.ps1"],
  ["uninstall-task", "uninstall-task.ps1"],
  ["systemd-service", "vault-agent-report.service"],
  ["systemd-timer", "vault-agent-report.timer"],
  ["sha256sums", "SHA256SUMS"],
];

/** An index as DIST-1a's GET /v1/dist builds it. `localIds`: baked files. */
function index({ version = V, release = true, localIds = [] } = {}) {
  const base = release ? `https://github.com/steamhangar/steamhangar/releases/download/v${version}/` : null;
  const names = NAMES.map(([id, n]) => [id, n.replace(V, version)]);
  return {
    version,
    release,
    local_base: "/dist/",
    release_base: base,
    apk_alias_url: localIds.includes("app-android") ? "/dist/steamhangar-app.apk" : null,
    artifacts: names.map(([id, name]) => {
      const local = localIds.includes(id);
      const localUrl = local ? `/dist/${name}` : null;
      const releaseUrl = base ? base + name : null;
      return { id, name, local, size: local ? 1234 : null, sha256: local ? "ab".repeat(32) : null, local_url: localUrl, release_url: releaseUrl, url: localUrl || releaseUrl, source: local ? "local" : releaseUrl ? "release" : "none" };
    }),
  };
}
const ALL = NAMES.map(([id]) => id);
const PAGE = "http://hangar.lan:8080";
const AGENT = "http://192.0.2.10:8080";

test("MUTATION TARGET: app — baked: page origin + /dist/steamhangar-app.apk", () => {
  assert.deepEqual(appDownloadPlan(index({ localIds: ALL }), PAGE), {
    kind: "download",
    source: "local",
    url: "http://hangar.lan:8080/dist/steamhangar-app.apk",
    name: `steamhangar-app-v${V}.apk`,
    size: 1234,
    version: V,
  });
  // Without the alias the versioned local path still works.
  const noAlias = { ...index({ localIds: ALL }), apk_alias_url: null };
  assert.equal(appDownloadPlan(noAlias, PAGE).url, `http://hangar.lan:8080/dist/steamhangar-app-v${V}.apk`);
});

test("MUTATION TARGET: app — not baked on a release server: the GitHub release asset; dev: none", () => {
  const r = appDownloadPlan(index(), PAGE);
  assert.equal(r.kind, "download");
  assert.equal(r.source, "release");
  assert.equal(r.url, `${GH}steamhangar-app-v${V}.apk`);
  assert.deepEqual(appDownloadPlan(index({ version: "dev-1a2b3c4", release: false }), PAGE), { kind: "none", version: "dev-1a2b3c4" });
  assert.deepEqual(appDownloadPlan(null, PAGE), { kind: "none", version: null });
  assert.match(noAppText("dev-1a2b3c4"), /version "dev-1a2b3c4" and has no app file to offer/);
  assert.match(noAppText(null), /a version this page cannot read/);
});

test("MUTATION TARGET: app — fail closed on a path off /dist/, an absolute URL, or a foreign/other-version release URL", () => {
  for (const bad of ["//evil.example/x.apk", "http://evil.example/x.apk", "/v1/x.apk", "/dist/../v1/x", "/dist//x.apk", "dist/x.apk"]) {
    const ix = index({ localIds: ALL });
    ix.apk_alias_url = bad;
    ix.artifacts[0].local_url = bad;
    const r = appDownloadPlan(ix, PAGE);
    assert.notEqual(r.source, "local", `refused local ${bad}`);
  }
  for (const bad of ["https://evil.example/steamhangar-app.apk", `https://github.com/steamhangar/steamhangar/releases/download/v0.0.1/steamhangar-app-v${V}.apk`, `http://github.com/steamhangar/steamhangar/releases/download/v${V}/steamhangar-app-v${V}.apk`]) {
    const ix = index();
    ix.artifacts[0].release_url = bad;
    assert.deepEqual(appDownloadPlan(ix, PAGE), { kind: "none", version: V }, `refused ${bad}`);
  }
  const notRelease = index();
  notRelease.release = false;
  assert.equal(appDownloadPlan(notRelease, PAGE).kind, "none", "release:false never yields a release URL");
});

test("readDistIndex refuses malformed answers", () => {
  assert.equal(readDistIndex({ detail: "Not Found" }), null);
  assert.equal(readDistIndex({ ...index(), version: "" }), null);
  assert.equal(readDistIndex({ ...index(), release: "yes" }), null);
  assert.equal(readDistIndex({ ...index(), local_base: "/" }), null, "a root base would accept any path");
  assert.equal(readDistIndex({ ...index(), artifacts: {} }), null);
  assert.notEqual(readDistIndex(index()), null);
});

test("MUTATION TARGET: agent — every file baked: <agent address>/dist; otherwise the release; dev: none", () => {
  assert.deepEqual(agentDownloadPlan(index({ localIds: ALL }), AGENT, windowsArtifactNames), { ok: true, source: "local", base: "http://192.0.2.10:8080/dist", version: V });
  assert.deepEqual(agentDownloadPlan(index({ localIds: ALL }), AGENT, linuxArtifactNames), { ok: true, source: "local", base: "http://192.0.2.10:8080/dist", version: V });
  assert.deepEqual(agentDownloadPlan(index(), AGENT, windowsArtifactNames), { ok: true, source: "release", base: `https://github.com/steamhangar/steamhangar/releases/download/v${V}`, version: V });
  assert.deepEqual(agentDownloadPlan(index({ version: "dev-1a2b3c4", release: false }), AGENT, windowsArtifactNames), { ok: false, version: "dev-1a2b3c4" });
  assert.deepEqual(agentDownloadPlan(undefined, AGENT, windowsArtifactNames), { ok: false, version: null });
});

test("MUTATION TARGET: agent — a partial bake is not mixed: all from GitHub (release) or nothing (dev)", () => {
  const partial = ALL.filter((id) => id !== "sha256sums");
  assert.equal(agentDownloadPlan(index({ localIds: partial }), AGENT, windowsArtifactNames).source, "release");
  assert.equal(agentDownloadPlan(index({ release: false, localIds: partial }), AGENT, windowsArtifactNames).ok, false);
  // Only the APK baked: the agent still comes from the release.
  assert.equal(agentDownloadPlan(index({ localIds: ["app-android"] }), AGENT, linuxArtifactNames).source, "release");
});

test("MUTATION TARGET: agent — a file under another name than the command uses counts as missing", () => {
  const ix = index({ localIds: ALL });
  ix.artifacts[1].name = "vault-agent.exe";
  ix.artifacts[1].local_url = "/dist/vault-agent.exe";
  assert.equal(agentDownloadPlan(ix, AGENT, windowsArtifactNames).ok, false);
  assert.equal(agentDownloadPlan(ix, AGENT, linuxArtifactNames).source, "local", "the Linux files are unaffected");
  const missing = index({ localIds: ALL });
  missing.artifacts = missing.artifacts.filter((a) => a.id !== "systemd-timer");
  assert.equal(agentDownloadPlan(missing, AGENT, linuxArtifactNames).ok, false);
});

test("the Windows command takes the chosen base; without one it keeps the GitHub release", () => {
  const local = windowsInstallSnippet({ version: V, serverUrl: AGENT, downloadBase: "http://192.0.2.10:8080/dist" });
  assert.ok(local.split("\n").includes("$base = 'http://192.0.2.10:8080/dist'"));
  const dflt = windowsInstallSnippet({ version: V, serverUrl: AGENT });
  assert.ok(dflt.split("\n").includes(`$base = 'https://github.com/steamhangar/steamhangar/releases/download/v${V}'`));
});

test("formatDownloadSize", () => {
  assert.equal(formatDownloadSize(9_300_000), "9.3 MB");
  assert.equal(formatDownloadSize(21_000), "21 kB");
  assert.equal(formatDownloadSize(400), "1 kB");
  assert.equal(formatDownloadSize(null), null);
  assert.equal(formatDownloadSize(-1), null);
});

test("shQuote closes, escapes and reopens a single quote", () => {
  assert.equal(shQuote("a'b"), "'a'\\''b'");
  assert.equal(shQuote("$HOME `x`"), "'$HOME `x`'");
});

test("Linux command: a subshell with set -eu, no key, architecture switch, sums checked before chmod", () => {
  const s = linuxDownloadSnippet({ version: V, downloadBase: "http://192.0.2.10:8080/dist" });
  const lines = s.split("\n");
  assert.equal(lines[0], "(");
  assert.equal(lines[lines.length - 1], ")");
  assert.ok(lines.includes("set -eu"));
  assert.ok(lines.includes("base='http://192.0.2.10:8080/dist'"));
  assert.ok(lines.includes(`  x86_64|amd64) bin='vault-agent-v${V}-linux-amd64' ;;`));
  assert.ok(lines.includes(`  aarch64|arm64) bin='vault-agent-v${V}-linux-arm64' ;;`));
  const check = lines.findIndex((l) => l.includes('[ "$want" != "$got" ]'));
  const chmod = lines.indexOf('chmod +x "$bin"');
  assert.ok(check > 0 && chmod > check);
  assert.equal(/key/i.test(s), false, "nothing about a key");
  assert.ok(linuxDownloadSnippet({ version: V }).includes(`base='https://github.com/steamhangar/steamhangar/releases/download/v${V}'`));
});

// The command, executed: sh with a fake `curl` on PATH that copies from a
// fixture directory instead of the network. Linux/macOS only.
const canRun = process.platform !== "win32" && spawnSync("sh", ["-c", "command -v sha256sum && command -v awk"]).status === 0;

function runLinux({ corrupt = null, dropSum = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), "dist1b-"));
  const src = join(root, "src");
  const bin = join(root, "bin");
  const home = join(root, "home");
  mkdirSync(src);
  mkdirSync(bin);
  mkdirSync(home);
  const arch = spawnSync("uname", ["-m"]).stdout.toString().trim();
  const exe = arch === "aarch64" || arch === "arm64" ? `vault-agent-v${V}-linux-arm64` : `vault-agent-v${V}-linux-amd64`;
  const files = { [exe]: "#!/bin/sh\necho agent\n", "vault-agent-report.service": "[Service]\n", "vault-agent-report.timer": "[Timer]\n" };
  const sums = [];
  for (const [n, body] of Object.entries(files)) {
    writeFileSync(join(src, n), n === corrupt ? body + "tampered" : body);
    if (n !== dropSum) sums.push(`${createHash("sha256").update(body).digest("hex")}  ${n}`);
  }
  writeFileSync(join(src, "SHA256SUMS"), sums.join("\n") + "\n");
  // fake curl: curl -fsSL -o <out> <url>  ->  cp <src>/<basename url> <out>
  writeFileSync(join(bin, "curl"), `#!/bin/sh\nout="$3"; url="$4"; cp "${src}/\${url##*/}" "$out"\n`);
  chmodSync(join(bin, "curl"), 0o755);
  const script = linuxDownloadSnippet({ version: V, downloadBase: "http://192.0.2.10:8080/dist" });
  const r = spawnSync("sh", ["-c", script], { env: { PATH: `${bin}:${process.env.PATH}`, HOME: home } });
  const target = join(home, ".local", "share", "vault-agent", `release-v${V}`, exe);
  const out = { status: r.status, stderr: r.stderr.toString(), stdout: r.stdout.toString(), exists: existsSync(target), mode: existsSync(target) ? statSync(target).mode : 0 };
  rmSync(root, { recursive: true, force: true });
  return out;
}

test("MUTATION TARGET: Linux command, run: good sums pass and the binary becomes executable", { skip: !canRun }, () => {
  const r = runLinux();
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /downloaded and checked/);
  assert.ok(r.exists && (r.mode & 0o100) !== 0, "executable");
});

test("MUTATION TARGET: Linux command, run: a tampered file or a missing sum line stops it before chmod", { skip: !canRun }, () => {
  for (const bad of [{ corrupt: "vault-agent-report.timer" }, { dropSum: "vault-agent-report.service" }]) {
    const r = runLinux(bad);
    assert.notEqual(r.status, 0, JSON.stringify(bad));
    assert.match(r.stderr, /SHA256 check failed for vault-agent-report\./);
    assert.ok(r.exists && (r.mode & 0o100) === 0, "binary not made executable");
  }
});

test("MUTATION TARGET (review B1): a release index with a version outside the grammar yields nothing", () => {
  for (const evil of ["0.1.0\ntouch /tmp/PWNED #", "0.1.0'", "dev-1a2b3c4"]) {
    for (const localIds of [[], ALL]) {
      // Names and URLs built from the same bad version, release: true.
      const ix = index({ version: evil, release: true, localIds });
      assert.deepEqual(agentDownloadPlan(ix, AGENT, windowsArtifactNames), { ok: false, version: evil });
      assert.deepEqual(agentDownloadPlan(ix, AGENT, linuxArtifactNames), { ok: false, version: evil });
      assert.deepEqual(appDownloadPlan(ix, PAGE), { kind: "none", version: evil });
    }
  }
});

test("MUTATION TARGET (review N1): local:false with a local_url is not local", () => {
  const ix = index();
  for (const a of ix.artifacts) a.local_url = `/dist/${a.name}`;
  ix.apk_alias_url = "/dist/steamhangar-app.apk";
  assert.equal(appDownloadPlan(ix, PAGE).source, "release");
  assert.equal(agentDownloadPlan(ix, AGENT, windowsArtifactNames).source, "release");
  ix.release = false;
  assert.equal(appDownloadPlan(ix, PAGE).kind, "none");
  assert.equal(agentDownloadPlan(ix, AGENT, linuxArtifactNames).ok, false);
});
