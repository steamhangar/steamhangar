#!/usr/bin/env node
// Static server for web/ with the SPA fallback vault-api provides in
// production (api/vault_api/webui.py): the known view paths and `/` serve
// index.html, files under web/ are served as-is, everything else -- /v1/*
// included -- is a 404. Demo mode never calls the API, so a 404 there is the
// correct answer for a screenshot run (WP WEB-GOLDENS-1).
//
// The view list is read from web/js/router.js (`export const VIEWS = [...]`)
// at startup so this server cannot drift from the app: a view the router
// does not know is a 404 here too, which lets tools/web-shot.mjs skip a page
// that does not exist yet instead of photographing the default view under
// the wrong name.
//
// No dependencies. Usage: node tools/web-serve.mjs [--root web] [--host
// 127.0.0.1] [--port 8787]. Prints `listening http://host:port` once ready.
import { createServer } from "node:http";
import { readFileSync, statSync, realpathSync } from "node:fs";
import { extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

/** The `VIEWS` array of web/js/router.js, or a thrown Error. */
export function readViews(webRoot) {
  const source = readFileSync(join(webRoot, "js", "router.js"), "utf8");
  const match = /export const VIEWS = \[([^\]]*)\]/.exec(source);
  if (match === null) throw new Error(`no \`export const VIEWS = [...]\` in ${join(webRoot, "js", "router.js")}`);
  const views = [...match[1].matchAll(/"([a-z][a-z0-9-]*)"/g)].map((m) => m[1]);
  if (views.length === 0) throw new Error("VIEWS in router.js is empty");
  return views;
}

/**
 * Build the request handler. Exported for the test; `main()` below wires it
 * to a listening socket.
 */
export function createHandler(webRoot, views = readViews(webRoot)) {
  const root = realpathSync(resolve(webRoot));
  const indexPath = join(root, "index.html");
  statSync(indexPath);

  const sendIndex = (res, method) => {
    const body = readFileSync(indexPath);
    res.writeHead(200, {
      "content-type": CONTENT_TYPES[".html"],
      "content-length": body.length,
      "cache-control": "no-cache",
    });
    res.end(method === "HEAD" ? undefined : body);
  };

  const sendStatus = (res, status) => {
    res.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
    res.end(`${status}\n`);
  };

  return (req, res) => {
    const method = req.method ?? "GET";
    if (method !== "GET" && method !== "HEAD") return sendStatus(res, 405);
    let pathname;
    try {
      pathname = decodeURIComponent(new URL(req.url ?? "/", "http://localhost").pathname);
    } catch {
      return sendStatus(res, 400);
    }
    if (pathname === "/" || pathname === "/index.html") return sendIndex(res, method);
    const firstSegment = pathname.split("/")[1];
    if (views.includes(firstSegment)) return sendIndex(res, method);

    // A real file under web/: resolve, then require the resolved path to stay
    // inside the root (path.resolve already collapses `..`; the prefix check
    // is the guard, realpath catches symlinks pointing out).
    const candidate = resolve(root, "." + pathname);
    if (candidate !== root && !candidate.startsWith(root + sep)) return sendStatus(res, 404);
    let real;
    try {
      real = realpathSync(candidate);
      if (!statSync(real).isFile()) return sendStatus(res, 404);
    } catch {
      return sendStatus(res, 404);
    }
    if (real !== root && !real.startsWith(root + sep)) return sendStatus(res, 404);
    const body = readFileSync(real);
    res.writeHead(200, {
      "content-type": CONTENT_TYPES[extname(real).toLowerCase()] ?? "application/octet-stream",
      "content-length": body.length,
      "cache-control": "no-cache",
    });
    res.end(method === "HEAD" ? undefined : body);
  };
}

function parseArgs(argv) {
  const opts = { root: "web", host: "127.0.0.1", port: 8787 };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const value = argv[i + 1];
    if (arg === "--root" && value !== undefined) { opts.root = value; i += 1; }
    else if (arg === "--host" && value !== undefined) { opts.host = value; i += 1; }
    else if (arg === "--port" && value !== undefined) { opts.port = Number(value); i += 1; }
    else if (arg === "--help" || arg === "-h") { opts.help = true; }
    else throw new Error(`unknown argument: ${arg}`);
  }
  if (!Number.isInteger(opts.port) || opts.port < 0 || opts.port > 65535) throw new Error("--port must be 0..65535");
  return opts;
}

export function main(argv = process.argv.slice(2)) {
  const opts = parseArgs(argv);
  if (opts.help) {
    console.log("usage: node tools/web-serve.mjs [--root web] [--host 127.0.0.1] [--port 8787]");
    return;
  }
  const server = createServer(createHandler(opts.root));
  server.listen(opts.port, opts.host, () => {
    const address = server.address();
    console.log(`listening http://${opts.host}:${address.port}`);
  });
  const stop = () => server.close(() => process.exit(0));
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (err) {
    console.error(`web-serve: ${err.message}`);
    process.exit(2);
  }
}
