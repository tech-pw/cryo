#!/usr/bin/env node
// Local docs preview. Rebuilds from site/_src on start and on change, then serves site/
// with caching disabled so a refresh always shows the latest pages, CSS and JS.
//
//   node site/scripts/preview.mjs
//   DOCS_PORT=4321 node site/scripts/preview.mjs

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync, watch } from "node:fs";
import { extname, join, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SITE = join(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = Number(process.env.DOCS_PORT || 4173);
const BUILD = join(SITE, "scripts", "build.mjs");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".xml": "application/xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".ico": "image/x-icon",
};

function build() {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [BUILD], { stdio: "inherit" });
    child.on("exit", (code) => resolve(code === 0));
  });
}

let building = false;
let queued = false;
async function rebuild(reason) {
  if (building) {
    queued = true;
    return;
  }
  building = true;
  do {
    queued = false;
    const ok = await build();
    console.log(ok ? `rebuilt (${reason})` : `build failed (${reason})`);
  } while (queued);
  building = false;
}

let timer = null;
function schedule(reason) {
  clearTimeout(timer);
  timer = setTimeout(() => rebuild(reason), 120);
}

await rebuild("startup");

createServer((req, res) => {
  // A path starting with "//" is a protocol-relative URL to new URL(path, base), which throws.
  const url = new URL(`http://localhost${req.url || "/"}`);
  let rel;
  try {
    rel = "/" + decodeURIComponent(url.pathname).replace(/^\/+/, "");
  } catch {
    res.writeHead(400).end();
    return;
  }
  const file = normalize(join(SITE, rel));
  if (!file.startsWith(SITE)) {
    res.writeHead(403).end();
    return;
  }
  const send = (status, body, type) => {
    res.writeHead(status, { "content-type": type, "cache-control": "no-store" });
    res.end(body);
  };
  if (existsSync(file) && statSync(file).isDirectory()) {
    if (!rel.endsWith("/")) {
      res.writeHead(302, { location: rel + "/" + url.search });
      res.end();
      return;
    }
    rel += "index.html";
  }
  const target = normalize(join(SITE, rel));
  if (!target.startsWith(SITE) || !existsSync(target) || statSync(target).isDirectory()) {
    send(404, readFileSync(join(SITE, "404.html")), MIME[".html"]);
    return;
  }
  send(200, readFileSync(target), MIME[extname(target)] || "application/octet-stream");
}).listen(PORT, () => {
  console.log(`docs preview  http://localhost:${PORT}/`);
  console.log("watching site/_src, site/scripts and site/assets — refresh after a save");
});

for (const dir of ["_src", "scripts", "assets"]) {
  watch(join(SITE, dir), { recursive: true }, (_event, name) => {
    if (!name || name.endsWith("search.json") || name.endsWith(".html")) return;
    schedule(name);
  });
}
