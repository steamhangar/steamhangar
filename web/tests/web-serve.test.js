// WP WEB-GOLDENS-1: tools/web-serve.mjs serves web/ the way vault-api does
// (api/vault_api/webui.py): index.html for `/` and the router's views,
// files as files, a 404 for everything else -- /v1/* included.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { createHandler, readViews } from "../../tools/web-serve.mjs";

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// router.js registers a popstate listener at import time; a minimal window
// stub (same approach as the *-wiring tests) lets its VIEWS be imported.
globalThis.window = { addEventListener() {}, location: { pathname: "/" }, history: {} };
const { VIEWS } = await import("../js/router.js");

async function withServer(fn) {
  const server = createServer(createHandler(WEB_ROOT));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await fn(base);
  } finally {
    await new Promise((r) => server.close(r));
  }
}

test("readViews mirrors router.js VIEWS exactly", () => {
  assert.deepEqual(readViews(WEB_ROOT), VIEWS);
});

test("index.html for / and every view path, with and without a deep link", async () => {
  const index = readFileSync(join(WEB_ROOT, "index.html"), "utf8");
  await withServer(async (base) => {
    for (const path of ["/", "/index.html", ...VIEWS.map((v) => `/${v}`), `/${VIEWS[0]}/440`, `/${VIEWS[0]}/`]) {
      const res = await fetch(base + path);
      assert.equal(res.status, 200, path);
      assert.match(res.headers.get("content-type"), /text\/html/);
      assert.equal(await res.text(), index, path);
    }
    const head = await fetch(base + "/" + VIEWS[0], { method: "HEAD" });
    assert.equal(head.status, 200);
    assert.equal((await head.text()).length, 0);
  });
});

test("unknown routes and the API are 404, so an unshipped page is never photographed as the default view", async () => {
  await withServer(async (base) => {
    for (const path of ["/kit-does-not-exist", "/v1/health", "/v1/games", "/js/does-not-exist.js", "/library.html"]) {
      const res = await fetch(base + path);
      assert.equal(res.status, 404, path);
    }
    assert.equal((await fetch(base + "/", { method: "POST" })).status, 405);
  });
});

test("static files are served with their type; traversal stays inside web/", async () => {
  await withServer(async (base) => {
    const js = await fetch(base + "/js/router.js");
    assert.equal(js.status, 200);
    assert.match(js.headers.get("content-type"), /text\/javascript/);
    assert.equal(await js.text(), readFileSync(join(WEB_ROOT, "js", "router.js"), "utf8"));
    const css = await fetch(base + "/css/theme.css");
    assert.equal(css.status, 200);
    assert.match(css.headers.get("content-type"), /text\/css/);
    // Encoded traversal reaches the handler un-normalized (fetch normalizes
    // a bare `..` client-side, so only the encoded form tests the guard).
    for (const path of ["/js/%2e%2e/%2e%2e/api/README.md", "/%2e%2e/CLAUDE.md", "/js"]) {
      assert.equal((await fetch(base + path)).status, 404, path);
    }
  });
});
