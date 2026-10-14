/**
 * DOM-wiring pins for WP D15-NAMES "Delete history" in
 * `web/js/views/downloads.js`:
 *
 *   - a "Delete history…" button under the History filter;
 *   - it opens an alertdialog (aria-modal, labelled and described) with
 *     focus on "Keep" and "Older than 30 days" preselected;
 *   - the confirm button names the chosen variant; confirming sends
 *     `DELETE /v1/history?scope=...`, closes the dialog and refetches the
 *     history list (`GET /v1/jobs?range=`) and the statistics;
 *   - "Keep" sends nothing; a 404 (old server) keeps the dialog open with
 *     the update text in a role=alert line.
 *
 * Harness: fake-dom.js, a routing `fetch` fake. Text assertions only.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", "test-key"]]);
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test", pathname: "/downloads", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const job = (id, status) => ({
  id, appid: 10 + id, type: "prefill", status, stop_request: null,
  created_at: iso(120_000), started_at: iso(90_000), finished_at: status === "running" ? null : iso(60_000),
});
const server = { deleteStatus: 200, deletes: [], historyFetches: 0, statsFetches: 0, jobs: [job(1, "running"), job(2, "done")] };

function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const p = u.pathname;
  const method = init.method || "GET";
  if (p === "/v1/history" && method === "DELETE") {
    const scope = u.searchParams.get("scope");
    server.deletes.push(scope);
    if (server.deleteStatus !== 200) return respond(server.deleteStatus, { detail: "Not Found" });
    server.jobs = server.jobs.filter((j) => j.status === "running");
    return respond(200, { scope, cutoff: null, jobs_deleted: 1, traffic_rows_deleted: { client_traffic: 0, client_depot_traffic: 0, server_traffic: 0 } });
  }
  if (p === "/v1/throughput" || p === "/v1/throughput/series") {
    server.statsFetches += 1;
    return respond(404, { detail: "Not Found" }); // statistics are not under test here
  }
  if (p === "/v1/jobs") {
    if (u.searchParams.get("range")) server.historyFetches += 1;
    return respond(200, server.jobs);
  }
  if (p === "/v1/games" || p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");
const { navigateTo } = await import("../js/router.js");

after(() => {
  store.stop();
  navigateTo("library");
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 3000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const body = dom.document.body;
const role = (root, r) => root.querySelector(`[data-role="${r}"]`);
const dialog = () => role(body, "history-delete-confirm");
const open = () => dialog().parentNode.classList.contains("on");
const radio = (value) => dialog().querySelectorAll("input").find((i) => i.value === value);

async function mount() {
  navigateTo("library");
  store.start();
  const s = renderDownloads();
  dom.appRoot.replaceChildren(s);
  await until(() => s.querySelector('.jobcard[data-jid="1"]') !== null, "jobs painted");
  return s;
}

beforeEach(() => {
  server.deleteStatus = 200;
  server.deletes = [];
  server.jobs = [job(1, "running"), job(2, "done")];
});

test("MUTATION TARGET: the button opens an alertdialog with focus on Keep and the 30-day variant preselected", async () => {
  const s = await mount();
  const btn = role(s, "history-delete");
  assert.equal(btn.textContent, "Delete history…");
  btn.focus();
  btn.dispatchEvent({ type: "click" });
  assert.equal(open(), true);
  const d = dialog();
  assert.equal(d.getAttribute("role"), "alertdialog");
  assert.equal(d.getAttribute("aria-modal"), "true");
  assert.equal(d.getAttribute("aria-labelledby"), "hist-delete-title");
  assert.equal(d.getAttribute("aria-describedby"), "hist-delete-what hist-delete-keeps");
  assert.equal(d.querySelector("h3").textContent, "Delete history?");
  assert.equal(dom.document.activeElement, role(d, "history-delete-no"), "focus on the non-destructive button");
  assert.equal(radio("older-than-30d").checked, true);
  assert.equal(radio("all").checked, false);
  assert.equal(role(d, "history-delete-yes").textContent, "Delete older than 30 days");
  role(d, "history-delete-no").dispatchEvent({ type: "click" });
  assert.equal(open(), false);
  assert.deepEqual(server.deletes, [], "Keep sends nothing");
  assert.equal(dom.document.activeElement, btn, "focus returns to the button");
});

test("MUTATION TARGET: choosing Everything renames the button and confirming sends scope=all, then refetches", async () => {
  const s = await mount();
  await until(() => s.querySelectorAll(".hrow").length === 1, "history row");
  role(s, "history-delete").dispatchEvent({ type: "click" });
  radio("older-than-30d").checked = false;
  radio("all").checked = true;
  radio("all").dispatchEvent({ type: "change" });
  assert.equal(role(dialog(), "history-delete-yes").textContent, "Delete everything");
  const fetchesBefore = server.historyFetches;
  const statsBefore = server.statsFetches;
  role(dialog(), "history-delete-yes").dispatchEvent({ type: "click" });
  await until(() => !open(), "dialog closed");
  assert.deepEqual(server.deletes, ["all"]);
  await until(() => server.historyFetches > fetchesBefore, "history refetched");
  await until(() => server.statsFetches > statsBefore, "statistics refetched");
  await until(() => s.querySelectorAll(".hrow").length === 0, "deleted rows gone");
});

test("MUTATION TARGET: the default variant sends scope=older-than-30d", async () => {
  const s = await mount();
  role(s, "history-delete").dispatchEvent({ type: "click" });
  role(dialog(), "history-delete-yes").dispatchEvent({ type: "click" });
  await until(() => !open(), "dialog closed");
  assert.deepEqual(server.deletes, ["older-than-30d"]);
});

test("MUTATION TARGET: an old server (404) keeps the dialog open with the update text in a role=alert line", async () => {
  server.deleteStatus = 404;
  const s = await mount();
  role(s, "history-delete").dispatchEvent({ type: "click" });
  role(dialog(), "history-delete-yes").dispatchEvent({ type: "click" });
  await until(() => role(dialog(), "history-delete-error").hidden === false, "error shown");
  assert.equal(role(dialog(), "history-delete-error").getAttribute("role"), "alert");
  assert.equal(
    role(dialog(), "history-delete-error").textContent,
    "This server cannot delete history yet. Update vault-api to use this.",
  );
  assert.equal(open(), true);
  role(dialog(), "history-delete-no").dispatchEvent({ type: "click" });
  assert.equal(open(), false);
});
