/**
 * WP RELEASE-SPLIT-1b (ADR-0031): the Clients page's agent version hint
 * compares with the BUNDLED agent the server states (`GET /v1/settings`
 * `clients.agent`: component version, build stamp, minimum, same_build with
 * the server's alias list), not with the server version; and the "Add a
 * device" download plans name each client's files by its own version
 * (`GET /v1/dist` `clients.<agent|app>.build`).
 *
 *  - version order: rc9 < rc17 < rc21 < rc21.1 < rc22 < 0.1.0 (natural
 *    pre-release order, the same as component-versions.py and the Kotlin twin);
 *  - same_build (aliases rc18..rc21.1 -> rc17) never warns; below the
 *    minimum says so; another build differs; a newer agent differs too;
 *  - no bundled agent (server before 1b, local build): the CLIENTS-3 rule
 *    against the server version, unchanged;
 *  - downloads: an agent reused from rc17 on an rc22 server gets rc17 names
 *    and the rc17 release URL, and a URL of another release is refused.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  agentVersionIssue,
  agentVersionRef,
  clientCardModel,
  compareReleaseVersions,
  isAgentOutdated,
  outdatedAgentInfo,
  outdatedAgentText,
  summaryTiles,
} from "../js/lib/clients-page.js";
import { agentDownloadPlan, appDownloadPlan, clientFilesVersion, linuxArtifactNames } from "../js/lib/dist-downloads.js";

const ALIASES = ["0.1.0-rc17", "0.1.0-rc18", "0.1.0-rc18.1", "0.1.0-rc19", "0.1.0-rc19.2", "0.1.0-rc19.3",
  "0.1.0-rc20", "0.1.0-rc20.1", "0.1.0-rc20.2", "0.1.0-rc21", "0.1.0-rc21.1", "0.1.0-rc22"];
/** `clients` as vault_api/client_versions.py states an rc22 dry-run server
 * whose agent (unchanged since rc17) was rebuilt with the rc22 stamp. */
const CLIENTS = {
  agent: { version: "0.1.0-rc17", build: "0.1.0-rc22", minimum: "0.1.0-rc17", same_build: ALIASES },
  app: { version: "0.1.0-rc22", build: "0.1.0-rc22", minimum: "0.1.0-rc17", same_build: ["0.1.0-rc22"] },
};
const REF = agentVersionRef("0.1.0-rc22", CLIENTS);

const card = (version, key = "agent:pc") => ({
  key,
  kind: "agent",
  client_id: key.slice(6),
  connected: true,
  agent: { client_id: key.slice(6), agent_version: version },
});

test("compareReleaseVersions: natural pre-release order, release after its rcs, junk is null", () => {
  const order = ["0.0.9", "0.1.0-rc9", "0.1.0-rc17", "0.1.0-rc21", "0.1.0-rc21.1", "0.1.0-rc22", "0.1.0", "0.1.1", "0.2.0", "1.0.0"];
  for (let i = 0; i < order.length; i++) {
    for (let j = 0; j < order.length; j++) {
      assert.equal(compareReleaseVersions(order[i], order[j]), Math.sign(i - j), `${order[i]} vs ${order[j]}`);
    }
  }
  assert.equal(compareReleaseVersions("0.1.0-beta", "0.1.0-rc1"), -1, "letters compare as text");
  for (const bad of ["dev", "", null, "0.1", "v0.1.0", " 0.1.0"]) {
    assert.equal(compareReleaseVersions(bad, "0.1.0"), null, String(bad));
    assert.equal(compareReleaseVersions("0.1.0", bad), null, String(bad));
  }
});

test("agentVersionRef reads clients.agent; garbage falls back to the server version", () => {
  assert.equal(REF.server, "0.1.0-rc22");
  assert.equal(REF.bundled, "0.1.0-rc22");
  assert.equal(REF.minimum, "0.1.0-rc17");
  assert.ok(REF.same.includes("0.1.0-rc18.1"));
  for (const clients of [null, {}, { agent: null }, { agent: { build: "dev" } }, "x"]) {
    const ref = agentVersionRef("0.1.0", clients);
    assert.deepEqual(ref, { server: "0.1.0", bundled: null, minimum: null, same: [] }, JSON.stringify(clients));
  }
  // A bad minimum or bad alias entries are dropped, never trusted.
  const ref = agentVersionRef("0.1.0", { agent: { build: "0.1.0", minimum: "old", same_build: ["x y", "0.0.9"] } });
  assert.equal(ref.minimum, null);
  assert.deepEqual(ref.same, ["0.0.9", "0.1.0"]);
  assert.equal(agentVersionRef("dev", null).server, null);
});

test("MUTATION TARGET: bundled agent — same build never warns, below minimum and other builds do", () => {
  for (const v of ALIASES) assert.equal(agentVersionIssue(v, REF), null, v);
  assert.equal(agentVersionIssue(" 0.1.0-rc19.2 ", REF), null, "whitespace ignored");
  assert.equal(agentVersionIssue("0.1.0-rc16", REF), "minimum");
  assert.equal(agentVersionIssue("0.1.0-rc9", REF), "minimum", "natural order: rc9 is older than rc17");
  assert.equal(agentVersionIssue("0.1.0-rc23", REF), "bundled", "newer differs too");
  assert.equal(agentVersionIssue("0.1.0", REF), "bundled");
  for (const unknown of [null, undefined, "", "dev", "0.1"]) assert.equal(agentVersionIssue(unknown, REF), null, String(unknown));
  // Without a minimum an older agent only differs.
  const noMin = agentVersionRef("0.1.0-rc22", { agent: { ...CLIENTS.agent, minimum: null } });
  assert.equal(agentVersionIssue("0.1.0-rc16", noMin), "bundled");
  assert.equal(isAgentOutdated("0.1.0-rc16", REF), true);
  assert.equal(isAgentOutdated("0.1.0-rc18", REF), false, "the alias list: an rc18 agent is the rc17 build");
});

test("no bundled agent: the CLIENTS-3 rule against the server version is unchanged", () => {
  assert.equal(agentVersionIssue("0.1.0-rc18", "0.1.0-rc22"), "server");
  assert.equal(agentVersionIssue("0.1.0-rc22", "0.1.0-rc22"), null);
  const legacy = agentVersionRef("0.1.0-rc22", null);
  assert.equal(agentVersionIssue("0.1.0-rc18", legacy), "server");
  assert.deepEqual(outdatedAgentInfo(card("0.1.0-rc18"), "0.1.0-rc22"), { agent: "0.1.0-rc18", server: "0.1.0-rc22" });
});

test("card texts name the bundled build or the minimum", () => {
  const below = outdatedAgentInfo(card("0.1.0-rc16"), REF);
  assert.deepEqual(below, { agent: "0.1.0-rc16", server: "0.1.0-rc22", bundled: "0.1.0-rc22", minimum: "0.1.0-rc17", issue: "minimum" });
  assert.equal(outdatedAgentText(below), "Agent 0.1.0-rc16 is older than the minimum 0.1.0-rc17. Update vault-agent on this PC to match the server.");
  const other = outdatedAgentInfo(card("0.1.0-rc23"), REF);
  assert.equal(outdatedAgentText(other), "Agent 0.1.0-rc23 · server's agent 0.1.0-rc22. Update vault-agent on this PC to match the server.");
  assert.equal(outdatedAgentInfo(card("0.1.0-rc20.2"), REF), null);
  const model = clientCardModel(card("0.1.0-rc16"), { range: "7d", serverVersion: REF, nowMs: 0 });
  assert.equal(model.outdated.text, outdatedAgentText(below));
  assert.equal(clientCardModel(card("0.1.0-rc21.1"), { range: "7d", serverVersion: REF, nowMs: 0 }).outdated, null);
});

test("summaryTiles counts against the bundled agent", () => {
  const tile = (list, ref) => summaryTiles(list, "7d", ref).find((t) => t.key === "agents");
  const t = tile([card("0.1.0-rc17", "agent:a"), card("0.1.0-rc21.1", "agent:b"), card("0.1.0-rc16", "agent:c")], REF);
  assert.equal(t.outdated, 1);
  assert.equal(t.detail, "3 paired");
  assert.equal(tile([card("0.1.0-rc19.3")], REF).detail, "1 paired · all match the server");
  // A bundled agent is known even when the server version is not a release.
  assert.equal(tile([card("0.1.0-rc16")], agentVersionRef("dev", CLIENTS)).outdated, 1);
  assert.equal(tile([card("0.1.0-rc16")], agentVersionRef("dev", null)).outdated, null);
});

// --- downloads ---------------------------------------------------------------

const GH = "https://github.com/steamhangar/steamhangar/releases/download";

/** GET /v1/dist of an rc22 server with the agent reused from rc17 (enforce
 * on): agent files carry rc17, the APK rc22. */
function index({ local = false, agentUrlVersion = "0.1.0-rc17", clients = true } = {}) {
  const agent = "0.1.0-rc17";
  const app = "0.1.0-rc22";
  const files = [
    ["app-android", `steamhangar-app-v${app}.apk`, app],
    ["agent-windows-amd64", `vault-agent-v${agent}-windows-amd64.exe`, agent],
    ["agent-linux-amd64", `vault-agent-v${agent}-linux-amd64`, agent],
    ["agent-linux-arm64", `vault-agent-v${agent}-linux-arm64`, agent],
    ["install-task", "install-task.ps1", agent],
    ["run-vault-agent", "run-vault-agent.ps1", agent],
    ["uninstall-task", "uninstall-task.ps1", agent],
    ["systemd-service", "vault-agent-report.service", agent],
    ["systemd-timer", "vault-agent-report.timer", agent],
    ["sha256sums", "SHA256SUMS", agent],
  ];
  return {
    version: "0.1.0-rc22",
    release: true,
    local_base: "/dist/",
    release_base: `${GH}/v0.1.0-rc22/`,
    apk_alias_url: local ? "/dist/steamhangar-app.apk" : null,
    artifacts: files.map(([id, name, version]) => {
      const relVersion = id.startsWith("app") ? version : agentUrlVersion;
      const releaseUrl = `${GH}/v${relVersion}/${name}`;
      const localUrl = local ? `/dist/${name}` : null;
      return { id, name, version, local, size: local ? 10 : null, sha256: null, local_url: localUrl, release_url: releaseUrl, url: localUrl || releaseUrl, source: local ? "local" : "release" };
    }),
    clients: clients
      ? {
          agent: { version: agent, build: agent, minimum: agent, same_build: [agent] },
          app: { version: app, build: app, minimum: agent, same_build: [app] },
        }
      : undefined,
  };
}

test("clientFilesVersion: clients.<c>.build, else the server version", () => {
  assert.equal(clientFilesVersion(index(), "agent"), "0.1.0-rc17");
  assert.equal(clientFilesVersion(index(), "app"), "0.1.0-rc22");
  assert.equal(clientFilesVersion(index({ clients: false }), "agent"), "0.1.0-rc22");
  assert.equal(clientFilesVersion({ version: "0.1.0", clients: { agent: { build: "dev-1" } } }, "agent"), "0.1.0");
});

test("MUTATION TARGET: a reused agent downloads from its own release with its own names", () => {
  const plan = agentDownloadPlan(index(), "http://192.0.2.10:8080", linuxArtifactNames);
  assert.deepEqual(plan, { ok: true, source: "release", base: `${GH}/v0.1.0-rc17`, version: "0.1.0-rc17" });
  const local = agentDownloadPlan(index({ local: true }), "http://192.0.2.10:8080", linuxArtifactNames);
  assert.deepEqual(local, { ok: true, source: "local", base: "http://192.0.2.10:8080/dist", version: "0.1.0-rc17" });
  // A release URL of another version than the file's is refused (fail closed).
  const foreign = agentDownloadPlan(index({ agentUrlVersion: "0.1.0-rc22" }), "http://192.0.2.10:8080", linuxArtifactNames);
  assert.equal(foreign.ok, false);
  // Without `clients` the names would be the server's: the rc17 files do not match.
  assert.equal(agentDownloadPlan(index({ clients: false }), "http://192.0.2.10:8080", linuxArtifactNames).ok, false);
});

test("the app keeps its own version's release URL", () => {
  const plan = appDownloadPlan(index(), "http://hangar.lan:8080");
  assert.equal(plan.kind, "download");
  assert.equal(plan.url, `${GH}/v0.1.0-rc22/steamhangar-app-v0.1.0-rc22.apk`);
});
