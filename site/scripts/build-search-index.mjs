// site/scripts/build-search-index.mjs
//
// Walk site/**/*.html, extract title, headings, lede paragraph, and emit
// a JSON index consumed by site/assets/js/search.js. Run from repo root:
//
//     node site/scripts/build-search-index.mjs > site/assets/js/index.json
//
// The output is a flat array of { url, title, section, text, headings: [...] }
// entries that search.js tokenizes at runtime. No external deps.

import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

const ROOT = new URL("../..", import.meta.url).pathname;
const SITE = join(ROOT, "site");

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (entry === "node_modules" || entry === "diagrams" || entry === "assets") continue;
      out.push(...walk(full));
    } else if (entry.endsWith(".html")) {
      out.push(full);
    }
  }
  return out;
}

function stripTags(s) {
  return s.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function extract(html, file) {
  const rel = relative(SITE, file).split(sep).join("/");
  const titleMatch = html.match(/<title>([^<]+)<\/title>/i);
  const title = titleMatch ? titleMatch[1].replace(/ — Cryo$/, "").trim() : rel;
  const sectionMatch = html.match(/data-page="([^"]+)"/);
  const section = sectionMatch ? sectionMatch[1] : "home";

  // Headings: <h1>, <h2>, <h3>
  const headings = [];
  for (const m of html.matchAll(/<h([1-3])[^>]*>([\s\S]*?)<\/h\1>/gi)) {
    headings.push({ level: Number(m[1]), text: stripTags(m[2]).slice(0, 120) });
  }

  // Lede: first <p class="lede"> or first <p>
  const ledeMatch = html.match(/<p class="lede"[^>]*>([\s\S]*?)<\/p>/i)
    || html.match(/<p>([\s\S]*?)<\/p>/i);
  const lede = ledeMatch ? stripTags(ledeMatch[1]).slice(0, 240) : "";

  return {
    url: rel.replace(/index\.html$/, ""),
    title,
    section,
    headings,
    lede,
  };
}

const files = walk(SITE);
const docs = files.map((f) => extract(readFileSync(f, "utf8"), f));

const out = { generatedAt: new Date().toISOString(), count: docs.length, docs };
process.stdout.write(JSON.stringify(out));
