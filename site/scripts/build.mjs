#!/usr/bin/env node
// Cryo docs site generator. Zero dependencies.
//
//   node site/scripts/build.mjs           write pages into site/
//   node site/scripts/build.mjs --check   exit 1 if committed output is stale (CI)
//
// Inputs:  site/_src/site.json (nav + metadata), site/_src/pages/*.html (content fragments)
// Outputs: site/<slug>/index.html, site/<slug>/index.md, site/index.html, site/index.md,
// 404.html, assets/search.json, sitemap.xml, robots.txt, llms.txt
//
// Fragments are plain HTML plus a few components (<callout>, <codeblock>, <diagram>, <tabs>, <steps>,
// <linkcards>, <contributors />). Internal links are written as href="@/path/" and rewritten relative to each page.
// <contributors /> expands site/_src/contributors.json. Refresh that file with site/scripts/contributors.mjs;
// the build itself does not call GitHub. Bots and AI-agent logins are dropped when the snapshot is written.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderChangelog } from "./changelog.mjs";
import { contributorItems, renderContributors } from "./contributors.mjs";
import { renderDiagram } from "./diagrams.mjs";
import { highlight } from "./highlight.mjs";

const SITE = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(SITE, "_src");
const CHECK = process.argv.includes("--check");
const cfg = JSON.parse(readFileSync(join(SRC, "site.json"), "utf8"));

const LOGO_MARK_INNER = readFileSync(join(SITE, "assets/svg/cryo-mark.svg"), "utf8")
  .replace(/^[\s\S]*?<svg[^>]*>/, "")
  .replace(/<\/svg>\s*$/, "")
  .replace(/<title[\s\S]*?<\/title>\s*/g, "")
  .trim();

const esc = (s) => {
  let out = "";
  for (const ch of String(s)) {
    if (ch === "&") out += "&amp;";
    else if (ch === "<") out += "&lt;";
    else if (ch === ">") out += "&gt;";
    else if (ch === '"') out += "&quot;";
    else out += ch;
  }
  return out;
};
const dropNamedElement = (html, name) => {
  const lower = html.toLowerCase();
  const open = `<${name}`;
  const close = `</${name}>`;
  let out = "";
  let cursor = 0;
  while (cursor < html.length) {
    const start = lower.indexOf(open, cursor);
    if (start < 0 || !isTagBoundary(lower, start + open.length)) return out + html.slice(cursor);
    const end = lower.indexOf(close, start + open.length);
    if (end < 0) return out + html.slice(cursor);
    out += html.slice(cursor, start);
    cursor = end + close.length;
  }
  return out;
};
const isTagBoundary = (lower, index) => {
  const next = lower[index];
  return next === ">" || next === " " || next === "/" || next === "\n" || next === "\t";
};
const dropHtmlTags = (html, replacement = "") => {
  let out = "";
  let cursor = 0;
  while (cursor < html.length) {
    const start = html.indexOf("<", cursor);
    if (start < 0) return out + html.slice(cursor);
    const next = html[start + 1];
    const named = next === "/" || (next >= "A" && next <= "Z") || (next >= "a" && next <= "z") || next === "!";
    const end = named ? html.indexOf(">", start + 1) : -1;
    if (end < 0) {
      out += html.slice(cursor, start + 1);
      cursor = start + 1;
      continue;
    }
    out += html.slice(cursor, start) + replacement;
    cursor = end + 1;
  }
  return out;
};
const stripTags = (s) => decodeEntities(dropHtmlTags(dropNamedElement(dropNamedElement(String(s), "script"), "style"), " ")).replace(/\s+/g, " ").trim();
const slugify = (s) => stripTags(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

// ---------- nav model ----------
const flat = [];
for (const g of cfg.groups) for (const it of g.items) flat.push({ ...it, group: g });
const bySlug = new Map(flat.map((p) => [p.slug, p]));
const urlOf = (slug) => (slug ? `${slug}/` : "");

// ---------- front matter ----------
function parsePage(slug) {
  const file = join(SRC, "pages", `${slug || "index"}.html`);
  const raw = readFileSync(file, "utf8");
  const m = /^---\n([\s\S]*?)\n---\n/.exec(raw);
  if (!m) throw new Error(`${file}: missing front matter`);
  const meta = {};
  for (const line of m[1].split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return { meta, body: raw.slice(m[0].length) };
}

// ---------- components ----------
const ICONS = {
  note: '<path d="M12 8h.01M11 12h1v5h1"/><circle cx="12" cy="12" r="9"/>',
  tip: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  caution: '<path d="M12 4 2.8 20h18.4L12 4zM12 10v4.5M12 17.5h.01"/>',
  important: '<path d="M8.5 3h7L21 8.5v7L15.5 21h-7L3 15.5v-7L8.5 3zM12 8v5M12 16h.01"/>',
};
const icon = (name, size = 18) => `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

function parseAttrs(s) {
  const out = {};
  for (const m of s.matchAll(/([\w-]+)="([^"]*)"/g)) out[m[1]] = m[2];
  return out;
}

function pngSize(rel) {
  const buf = readFileSync(join(SITE, rel));
  return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
}

function expandComponents(body, ctx) {
  const holds = [];
  const hold = (html) => {
    holds.push(html);
    return `\u0000H${holds.length - 1}\u0000`;
  };

  body = body.replace(/<include\s+src="([^"]+)"\s*\/>/g, (_, rel) => readFileSync(join(SRC, rel), "utf8"));

  body = body.replaceAll("%%VERSION%%", esc(cfg.version)).replaceAll("%%RELEASE_URL%%", esc(releaseUrl()));

  // Not held: the TOC and heading anchors must see the changelog headings.
  body = body.replace(/<changelog\s*\/>/g, () => renderChangelog(readFileSync(join(SITE, "..", "CHANGELOG.md"), "utf8"), cfg.repo));

  body = body.replace(/<contributors\s*\/>/g, () => {
    const snap = JSON.parse(readFileSync(join(SRC, "contributors.json"), "utf8"));
    return hold(renderContributors(contributorItems(snap.contributors || [])));
  });

  body = body.replace(/<codeblock([^>]*)>([\s\S]*?)<\/codeblock>/g, (_, a, code) => {
    const at = parseAttrs(a);
    const lang = at.lang || "text";
    const h = highlight(code.replace(/^\n/, "").replace(/\n[ \t]*$/, ""), lang);
    const title = at.title ? `<span class="code-title">${esc(at.title)}</span>` : `<span class="code-title code-lang">${esc(lang === "bash" ? "terminal" : lang)}</span>`;
    return hold(
      `<figure class="code" data-lang="${esc(lang)}"><figcaption class="code-bar">${title}<button class="copy" type="button" data-copy="${esc(h.copy)}" aria-label="Copy code"><span class="copy-label">Copy</span></button></figcaption><pre tabindex="0"><code>${h.html}</code></pre></figure>`,
    );
  });

  body = body.replace(/<diagram\s+([^>]*?)\/>/g, (_, a) => {
    const at = parseAttrs(a);
    const d = renderDiagram(at.name);
    ctx.fig += 1;
    const n = ctx.fig;
    const bare = "bare" in at || /\bbare\b/.test(a);
    const minw = Math.round(d.w * (bare ? 0.7 : 0.72));
    const svg = d.svg.replace("<svg ", `<svg style="--minw:${minw}px" `);
    const legend = (d.legend || []).map((l) => `<li class="lg lg-${l.kind}"><i></i>${esc(l.text)}</li>`).join("");
    const legendHtml = legend ? `<ul class="legend" aria-label="Legend">${legend}</ul>` : "";
    const alt = d.alt.map((t) => `<li>${esc(t)}</li>`).join("");
    const listTag = d.ordered ? "ol" : "ul";
    const caption = at.caption ? `<figcaption>${at.caption}</figcaption>` : "";
    if (bare) {
      return hold(`<figure class="fig fig-bare" id="fig-${esc(at.name)}"><div class="fig-canvas">${svg}</div>${legendHtml}</figure>`);
    }
    return hold(
      `<figure class="fig" id="fig-${esc(at.name)}"><div class="fig-bar"><span class="fig-tag">Figure ${n}</span><span class="fig-title">${esc(d.title)}</span><button class="fig-expand" type="button" aria-label="Expand figure ${n}">Expand</button></div><div class="fig-canvas" tabindex="0" role="group" aria-label="${esc(d.title)} (scroll to pan)">${svg}</div>${caption}${legendHtml}<details class="fig-alt"><summary>Text description</summary><${listTag}>${alt}</${listTag}></details></figure>`,
    );
  });

  body = body.replace(/<screenshot\s+([^>]*?)\/>/g, (_, a) => {
    const at = parseAttrs(a);
    const { w, h } = pngSize(`assets/img/screens/${at.name}-light.png`);
    const src = (theme) => `@/assets/img/screens/${at.name}-${theme}.png`;
    const img = (theme) => `<img class="shot-${theme}" src="${src(theme)}" width="${w / 2}" height="${h / 2}" alt="${esc(at.alt || "")}" loading="lazy" decoding="async">`;
    const caption = at.caption ? `<figcaption>${at.caption}</figcaption>` : "";
    return hold(
      `<figure class="shot"><div class="shot-frame"><div class="shot-bar"><div class="shot-chrome" aria-hidden="true"><span></span><span></span><span></span></div><p>${esc(at.url || "cryo.local")}</p><em class="shot-demo">Demo data</em></div><button class="shot-zoom" type="button" aria-label="Enlarge screenshot: ${esc(at.alt || at.name)}">${img("light")}${img("dark")}</button></div>${caption}</figure>`,
    );
  });

  body = body.replace(/<callout([^>]*)>([\s\S]*?)<\/callout>/g, (_, a, inner) => {
    const at = parseAttrs(a);
    const type = at.type || "note";
    const title = at.title || { note: "Note", tip: "Tip", caution: "Caution", important: "Important" }[type];
    return `<aside class="callout callout-${type}" role="note">${icon(type)}<div class="callout-body"><p class="callout-title">${esc(title)}</p>${inner.trim()}</div></aside>`;
  });

  body = body.replace(/<tabs([^>]*)>([\s\S]*?)<\/tabs>/g, (_, a, inner) => {
    const at = parseAttrs(a);
    const tabs = [...inner.matchAll(/<tab\s+title="([^"]*)">([\s\S]*?)<\/tab>/g)];
    const gid = `tabs-${++ctx.tabs}`;
    const list = tabs.map((t, i) => `<button class="tab" role="tab" id="${gid}-t${i}" aria-controls="${gid}-p${i}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}" data-label="${esc(t[1])}">${esc(t[1])}</button>`).join("");
    const panels = tabs.map((t, i) => `<div class="tabpanel" role="tabpanel" id="${gid}-p${i}" aria-labelledby="${gid}-t${i}"${i ? " hidden" : ""}>${t[2].trim()}</div>`).join("");
    return `<div class="tabs" data-group="${esc(at.group || gid)}"><div class="tablist" role="tablist">${list}</div>${panels}</div>`;
  });

  body = body.replace(/<steps>([\s\S]*?)<\/steps>/g, (_, inner) => {
    const steps = [...inner.matchAll(/<step\s+title="([^"]*)">([\s\S]*?)<\/step>/g)];
    return `<ol class="steps">${steps.map((s) => `<li><p class="step-title">${s[1]}</p>${s[2].trim()}</li>`).join("")}</ol>`;
  });

  body = body.replace(/<linkcards>([\s\S]*?)<\/linkcards>/g, (_, inner) => {
    const cards = [...inner.matchAll(/<linkcard\s+href="([^"]*)"\s+title="([^"]*)">([\s\S]*?)<\/linkcard>/g)];
    return `<div class="linkcards">${cards.map((c) => `<a class="linkcard" href="${c[1]}"><span class="lc-title">${c[2]}</span><span class="lc-desc">${c[3].trim()}</span></a>`).join("")}</div>`;
  });

  return { body, holds };
}

function restore(html, holds) {
  return html.replace(/\u0000H(\d+)\u0000/g, (_, i) => holds[Number(i)]);
}

// ---------- headings + toc ----------
function processHeadings(html) {
  const seen = new Map();
  const toc = [];
  const out = html.replace(/<h([23])([^>]*)>([\s\S]*?)<\/h\1>/g, (full, lvl, attrs, inner) => {
    let id = /id="([^"]+)"/.exec(attrs)?.[1];
    const text = stripTags(inner);
    if (!id) {
      id = slugify(text) || "section";
      const n = seen.get(id) || 0;
      seen.set(id, n + 1);
      if (n) id = `${id}-${n + 1}`;
    }
    toc.push({ level: Number(lvl), id, text });
    const cleanAttrs = attrs.replace(/\s*id="[^"]*"/, "");
    return `<h${lvl} id="${id}"${cleanAttrs}>${inner}<a class="anchor" href="#${id}" aria-label="Link to ${esc(text)}">#</a></h${lvl}>`;
  });
  return { html: out, toc };
}

function oneLine(s) {
  return String(s || "").replace(/\s+/g, " ").trim();
}

const PAGE_SUMMARIES = {
  "": "Self-hosted Apache Iceberg maintenance. Reads catalogs, plans the work, admits it when the engine has headroom.",
  quickstart: "Clone the repo, run make up, seed five demo tables, and open localhost:5173.",
  benchmark: "Measured results: Cryo versus scheduled and hand-run Spark on fleet health, a 100 GiB table, live CDC and query impact, with trade-offs.",
  concepts: "A source is where a table lives. A destination is where maintenance runs. Cryo holds the plan between them.",
  architecture: "Catalogs on the read side, engines on the write side, and the planner in between.",
  maintenance: "Compact first, then clean up, in one fixed order no preset can change.",
  health: "Grades run from A to F. A warning costs 8 points and a critical finding costs 20.",
  engines: "The planner emits no SQL. Trino and Spark each render the same plan in their own dialect.",
  deployment: "Helm on Kubernetes. Cryo plans and admits; Trino and Spark stay where they already run.",
  reference: "What is in the box, what is not, and how Cryo compares with adjacent tools.",
};

// Code is held out as placeholders until the outermost call returns. Otherwise the tag and
// indent stripping below eats YAML indentation and anything in code that looks like a tag.
function htmlToMarkdown(html, holds = null) {
  const top = holds === null;
  if (top) holds = [];
  const keep = (text) => {
    holds.push(text);
    return `\u0001${holds.length - 1}\u0001`;
  };
  let s = dropNamedElement(dropNamedElement(dropNamedElement(html, "script"), "style"), "svg")
    .replace(/<button(?![^>]*data-go)[\s\S]*?<\/button>/gi, "")
    .replace(/<aside class="tj-[a-z]+"[\s\S]*?<\/aside>/gi, "")
    .replace(/<em class="shot-demo">[\s\S]*?<\/em>/gi, "")
    .replace(/<div class="shot-bar">[\s\S]*?<\/div>/gi, "")
    .replace(/<figure class="code"[\s\S]*?<\/figure>/gi, (fig) => {
      const title = /<span class="code-title[^"]*">([\s\S]*?)<\/span>/i.exec(fig);
      const code = /<code>([\s\S]*?)<\/code>/i.exec(fig);
      const label = title ? inlineText(title[1]) : "";
      const body = code ? decodeEntities(dropHtmlTags(code[1])).replace(/\n$/, "") : "";
      return `\n\n${label ? `**${label}**\n\n` : ""}${keep(`\`\`\`\n${body}\n\`\`\``)}\n\n`;
    })
    .replace(/<a class="anchor"[^>]*>[\s\S]*?<\/a>/gi, "");
  s = s.replace(/<pre[^>]*><code[^>]*>([\s\S]*?)<\/code><\/pre>/gi, (_, code) => `\n\n${keep(`\`\`\`\n${decodeEntities(dropHtmlTags(code)).replace(/\n$/, "")}\n\`\`\``)}\n\n`);
  s = s.replace(/<code[^>]*>([\s\S]*?)<\/code>/gi, (_, c) => keep(`\`${decodeEntities(dropHtmlTags(c))}\``));
  s = s.replace(/<h([1-6])[^>]*>([\s\S]*?)<\/h\1>/gi, (_, lvl, inner) => `\n\n${"#".repeat(Number(lvl))} ${inlineText(inner)}\n\n`);
  s = s.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_, inner) => {
    const step = /<p class="step-title"[^>]*>([\s\S]*?)<\/p>/i.exec(inner);
    if (step) {
      const rest = inner.replace(/<p class="step-title"[\s\S]*?<\/p>/i, "");
      return `\n\n### ${inlineText(step[1])}\n\n${htmlToMarkdown(rest, holds)}\n`;
    }
    const link = /<a [^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/i.exec(inner);
    const note = /<p[^>]*>([\s\S]*?)<\/p>/i.exec(inner);
    if (link && note) return `\n- [${inlineText(link[2])}](${link[1]}): ${inlineText(note[1])}\n`;
    return `\n- ${inlineText(inner)}\n`;
  });
  s = s.replace(/<dt[^>]*>([\s\S]*?)<\/dt>/gi, (_, inner) => `\n\n**${inlineText(inner)}**\n`);
  s = s.replace(/<dd[^>]*>([\s\S]*?)<\/dd>/gi, (_, inner) => `\n${inlineText(inner)}\n`);
  s = s.replace(/<p class="ops-phase"[^>]*>([\s\S]*?)<\/p>/gi, (_, inner) => `\n\n**${inlineText(inner)}**\n\n`);
  s = s.replace(/<thead>[\s\S]*?<\/thead>/gi, (head) => {
    const cells = [...head.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/gi)].map((m) => escapePipes(inlineText(m[1])));
    if (!cells.length) return "";
    return `\n| ${cells.join(" | ")} |\n| ${cells.map(() => "---").join(" | ")} |\n`;
  });
  s = s.replace(/<tr[^>]*>([\s\S]*?)<\/tr>/gi, (_, row) => {
    const cells = [...row.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map((m) => escapePipes(inlineText(m[1])));
    return cells.length ? `| ${cells.join(" | ")} |\n` : "";
  });
  s = s.replace(/<figcaption[^>]*>([\s\S]*?)<\/figcaption>/gi, (_, inner) => `\n\n*${inlineText(inner)}*\n\n`);
  s = s.replace(/<p[^>]*>([\s\S]*?)<\/p>/gi, (_, inner) => `\n\n${inlineText(inner)}\n\n`);
  s = s.replace(/<br\s*\/?>/gi, "\n");
  s = dropHtmlTags(s).replace(/ +/g, " ");
  const md = decodeEntities(s)
    .replace(/[ \t]+\n/g, "\n")
    .replace(/^[ \t]+/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    // Table rows and list items come out one block each; join them so markdown reads one table or list.
    .replace(/^(\|.*\|)\n\n(?=\|)/gm, "$1\n")
    .replace(/^(- .*)\n\n(?=- )/gm, "$1\n")
    .trim();
  return top ? md.replace(/\u0001(\d+)\u0001/g, (_, i) => holds[Number(i)]) : md;
}

function escapePipes(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/\|/g, "\\|");
}

function inlineText(html) {
  const marked = dropNamedElement(String(html), "svg")
    .replace(/<a class="anchor"[^>]*>[\s\S]*?<\/a>/gi, "")
    .replace(/<code[^>]*>([\s\S]*?)<\/code>/gi, (_, c) => `\`${decodeEntities(c)}\``)
    .replace(/<(strong|b)[^>]*>([\s\S]*?)<\/\1>/gi, (_, _t, c) => `**${inlineText(c)}**`)
    .replace(/<(em|i)[^>]*>([\s\S]*?)<\/\1>/gi, (_, _t, c) => `*${inlineText(c)}*`)
    .replace(/<a [^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi, (_, href, label) => `[${inlineText(label)}](${href})`);
  return decodeEntities(dropHtmlTags(marked).replace(/\s+/g, " ").trim());
}

function decodeEntities(s) {
  return String(s).replace(/&(?:nbsp|amp|lt|gt|quot|#39);/g, (entity) => {
    if (entity === "&nbsp;") return " ";
    if (entity === "&amp;") return "&";
    if (entity === "&lt;") return "<";
    if (entity === "&gt;") return ">";
    if (entity === "&quot;") return '"';
    return "'";
  });
}

function pageMarkdown({ title, lede, description, updated, canonical, mdUrl, html }) {
  const body = htmlToMarkdown(html);
  return `---
title: ${title}
description: ${oneLine(description || lede)}
canonical: ${canonical}
updated: ${updated || "2026-10-06"}
---

# ${title}

${oneLine(lede)}

${body}

Source: ${canonical}
Markdown: ${mdUrl}
`;
}

function llmsTxt(pages) {
  const lines = [
    `# ${cfg.name}`,
    "",
    `> ${oneLine(cfg.description)}`,
    "",
    "Cryo reads Apache Iceberg catalogs, ranks what is worth maintaining from real query workload, and admits the work to its native engine when the cluster has headroom. Sources and engines stay separate. The planner emits no SQL. Every decision is explained. These pages are the public docs.",
    "",
    "Read this file, then answer only from the pages below. If a fact is not on one of them, say so.",
    "",
    "## Pages",
    "",
  ];
  for (const p of pages) {
    lines.push(`- [${p.title}](${cfg.url}/${p.url}index.md): ${p.summary}`);
  }
  lines.push("", `Full index: ${cfg.url}/llms.txt`, "");
  return lines.join("\n");
}

function rewriteLinks(html, root) {
  return html
    .replace(/href="@\/([^"]*)"/g, (_, p) => `href="${root}${p}"`)
    .replace(/src="@\/([^"]*)"/g, (_, p) => `src="${root}${p}"`)
    .replace(/<a ([^>]*?)href="(?!https?:|#|mailto:|\.\.?\/)([^"@][^"]*)"/g, (m, pre, p) => (p.startsWith(root) ? m : `<a ${pre}href="${root}${p}"`));
}

// ---------- chrome ----------
const GH_ICON = '<svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 005.47 7.59c.4.07.55-.17.55-.38v-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.92-.89-1.16-.89-1.16-.73-.5.06-.49.06-.49.8.06 1.22.83 1.22.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.13 0 0 .67-.22 2.2.82a7.6 7.6 0 014 0c1.53-1.04 2.2-.82 2.2-.82.44 1.11.16 1.93.08 2.13.51.56.82 1.28.82 2.15 0 3.07-1.87 3.74-3.65 3.94.29.25.54.74.54 1.5v2.22c0 .21.15.46.55.38A8 8 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>';
const SEARCH_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';
const SUN = '<svg class="i-sun" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg class="i-moon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></svg>';
const MENU = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>';
const HOME_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></svg>';
const PEN_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>';
const SHARE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7M16 6l-4-4-4 4M12 2v13"/></svg>';
const BUG_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="7" y="7" width="10" height="13" rx="5"/><path d="M9 7a3 3 0 0 1 6 0M3 13h4M17 13h4M4 7l3 2M20 7l-3 2M4 19l3-2M20 19l-3-2"/></svg>';
const COLLAPSE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m11 17-5-5 5-5M18 17l-5-5 5-5"/></svg>';
const LOGO_WORD = '<span class="logo-word"><span class="logo-cr">Cr</span><span class="logo-yo">yo</span></span>';
function logoMark() {
  return `<svg class="logo-mark" xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 64 64" aria-hidden="true">${LOGO_MARK_INNER}</svg>`;
}

// The released version shown in the header. Bump `version` in site.json when a tag is cut.
function releaseUrl() {
  return `${cfg.repo}/releases/tag/${cfg.version}`;
}

function header(root, activeGroup) {
  const tabs = cfg.groups
    .map((g) => {
      const first = g.items[g.id === "start" ? 1 : 0];
      const cur = g.id === activeGroup ? ' aria-current="true"' : "";
      return `<a class="hdr-tab" href="${root}${urlOf(first.slug)}"${cur}>${esc(g.label)}</a>`;
    })
    .join("");
  return `<header class="hdr"><div class="hdr-in"><button class="hdr-menu" type="button" aria-label="Open navigation" aria-expanded="false" aria-controls="side">${MENU}</button><a class="logo" href="${root}" aria-label="Cryo home">${logoMark(root)}${LOGO_WORD}</a><nav class="hdr-tabs" aria-label="Sections">${tabs}</nav><div class="hdr-end"><a class="hdr-ver" href="${esc(releaseUrl())}" title="Latest release on GitHub">${esc(cfg.version)}</a><button class="search-open" type="button" aria-label="Search the docs" aria-haspopup="dialog">${SEARCH_ICON}<span class="so-text">Search</span><kbd class="so-kbd">⌘K</kbd></button><a class="icon-btn" href="${cfg.repo}" aria-label="Cryo on GitHub">${GH_ICON}</a><button class="icon-btn theme-toggle" type="button" aria-label="Toggle colour theme">${SUN}${MOON}</button></div></div></header>`;
}

function searchKeys(url, headingId, group, title) {
  const keys = [group, title, url.replaceAll("/", " ").replaceAll("-", " ")];
  if (url.startsWith("quickstart") || url === "") keys.push("get started");
  if (url.startsWith("concepts") || group === "Concepts") keys.push("concepts");
  if (headingId === "maintenance-wave") keys.push("off-peak");
  if (headingId === "scan-path") keys.push("$files");
  if (headingId === "three-parts") keys.push("lakehouses connections");
  return keys.join(" ");
}

function sidebar(root, slug) {
  const groups = cfg.groups
    .map((g) => {
      const items = g.items
        .map((it) => `<li><a class="nav-link" href="${root}${urlOf(it.slug)}"${it.slug === slug ? ' aria-current="page"' : ""}>${esc(it.label)}</a></li>`)
        .join("");
      return `<div class="nav-group"><p class="nav-label">${esc(g.label)}</p><ul>${items}</ul></div>`;
    })
    .join("");
  return `<nav class="side" id="side" aria-label="Documentation"><div class="side-in">${groups}</div><button class="side-collapse" type="button" aria-label="Collapse sidebar" aria-pressed="false">${COLLAPSE_ICON}</button></nav><div class="scrim" hidden></div>`;
}

function footer(root) {
  const link = (p, t) => `<li><a href="${root}${p}">${t}</a></li>`;
  return `<footer class="foot"><div class="foot-in"><div class="foot-brand"><a class="logo" href="${root}">${logoMark(root)}${LOGO_WORD}</a><p>Apache-2.0, self-hosted Iceberg maintenance. Every decision explained.</p></div><div><p class="foot-h">Docs</p><ul>${link("quickstart/", "Quickstart")}${link("benchmark/", "Benchmark")}${link("concepts/", "Concepts")}${link("architecture/", "Architecture")}${link("reference/", "Reference")}</ul></div><div><p class="foot-h">Operate</p><ul>${link("maintenance/", "Maintenance")}${link("health/", "Health and cost")}${link("engines/", "Engines")}${link("deployment/", "Deployment")}</ul></div><div><p class="foot-h">Project</p><ul><li><a href="${cfg.repo}">GitHub</a></li><li><a href="${cfg.repo}/issues">Issues</a></li><li><a href="${root}llms.txt">llms.txt</a></li><li><a href="https://www.apache.org/licenses/LICENSE-2.0">Apache-2.0 licence</a></li></ul></div></div><p class="foot-fine">Apache, Apache Iceberg, Iceberg and Apache Spark are trademarks of The Apache Software Foundation. Other names may be trademarks of their respective owners.</p></footer>`;
}

const SEARCH_DIALOG = `<dialog class="search" id="search" aria-label="Search the docs"><form method="dialog" class="search-box"><div class="search-field">${SEARCH_ICON}<input type="search" class="search-input" placeholder="Search the docs" aria-label="Search the docs" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="search-list"><kbd>Esc</kbd></div><ul class="search-list" id="search-list" role="listbox"></ul><p class="search-foot"><span><kbd>↑</kbd><kbd>↓</kbd> to navigate</span><span><kbd>↵</kbd> to open</span></p></form></dialog>`;

const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("cryo-site-theme");if(t!=="light"&&t!=="dark")t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="light"}})()`;
const assetRev = (rel) => createHash("sha256").update(readFileSync(join(SITE, rel))).digest("hex").slice(0, 10);
const CSS_REV = assetRev("assets/css/site.css");
const JS_REV = assetRev("assets/js/site.js");

function head({ root, title, desc, canonical, slug, abs = false, markdown = "" }) {
  const base = abs ? `${cfg.url}/` : root;
  const full = slug ? `${title} · ${cfg.name}` : `${cfg.name}: ${cfg.tagline}`;
  const alt = markdown ? `<link rel="alternate" type="text/markdown" href="${markdown}" title="Markdown version">` : "";
  return `<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(full)}</title><meta name="description" content="${esc(desc)}"><link rel="canonical" href="${canonical}">${alt}<meta name="theme-color" content="#F5FAFD" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0B0F16" media="(prefers-color-scheme: dark)"><meta property="og:type" content="website"><meta property="og:site_name" content="${cfg.name}"><meta property="og:title" content="${esc(full)}"><meta property="og:description" content="${esc(desc)}"><meta property="og:url" content="${canonical}"><meta property="og:image" content="${cfg.url}/assets/img/og.png"><meta name="twitter:card" content="summary_large_image"><link rel="icon" href="${base}assets/svg/favicon.svg" type="image/svg+xml"><link rel="preload" href="${base}assets/fonts/ibm-plex-sans-latin-400-normal.woff2" as="font" type="font/woff2" crossorigin><link rel="preload" href="${base}assets/fonts/ibm-plex-sans-latin-600-normal.woff2" as="font" type="font/woff2" crossorigin><script>${THEME_SCRIPT}</script><link rel="stylesheet" href="${base}assets/css/site.css?v=${CSS_REV}"></head>`;
}

function tocHtml(toc) {
  if (toc.length < 2) return { rail: "", mobile: "" };
  const items = toc.map((t) => `<li class="toc-l${t.level}"><a href="#${t.id}">${esc(t.text)}</a></li>`).join("");
  return {
    rail: `<aside class="toc" aria-label="On this page"><p class="toc-h">On this page</p><ul>${items}</ul><a class="toc-top" href="#main">Back to top ↑</a></aside>`,
    mobile: `<details class="toc-m"><summary>On this page</summary><ul>${items}</ul></details>`,
  };
}

// ---------- page render ----------
const searchDocs = [];
const sitemap = [];

function renderDoc(entry) {
  const { slug } = entry;
  const { meta, body } = parsePage(slug);
  const depth = slug ? 1 : 0;
  const root = depth ? "../" : "./";
  const ctx = { fig: 0, tabs: 0 };
  const ex = expandComponents(body, ctx);
  const hd = processHeadings(ex.body);
  let content = rewriteLinks(restore(hd.html, ex.holds), root);
  const idx = flat.findIndex((p) => p.slug === slug);
  const prev = flat[idx - 1];
  const next = flat[idx + 1];
  const t = tocHtml(hd.toc);
  const pager = `<nav class="pager" aria-label="Previous and next pages">${prev ? `<a class="pg pg-prev" href="${root}${urlOf(prev.slug)}"><span class="pg-k">Previous</span><span class="pg-t">${esc(prev.label)}</span></a>` : "<span></span>"}${next ? `<a class="pg pg-next" href="${root}${urlOf(next.slug)}"><span class="pg-k">Next</span><span class="pg-t">${esc(next.label)}</span></a>` : "<span></span>"}</nav>`;
  const title = meta.title;
  // A group with one page named after itself would read "Reference › Reference".
  const here = entry.label === entry.group.label ? "" : `<span class="crumb-sep" aria-hidden="true">›</span><span class="crumb-here" aria-current="page">${esc(entry.label)}</span>`;
  const crumbs = `<nav class="crumbs" aria-label="Breadcrumb"><a href="${root}" aria-label="Cryo home">${HOME_ICON}</a><span class="crumb-sep" aria-hidden="true">›</span><span${here ? "" : ` class="crumb-here" aria-current="page"`}>${esc(entry.group.label)}</span>${here}</nav>`;
  const updated = meta.updated || "2026-10-05";
  const edit = `${cfg.editBase}${slug || "index"}.html`;
  const words = stripTags(content.replace(/<svg[\s\S]*?<\/svg>/g, " ")).split(/\s+/).length;
  const mins = Math.max(1, Math.round(words / 220));
  const issue = `${cfg.repo}/issues/new?title=${encodeURIComponent(`docs: ${title}`)}&body=${encodeURIComponent(`Page: ${cfg.url}/${urlOf(slug)}\n\nWhat is wrong or missing?\n`)}&labels=documentation`;
  const mdHref = `${root}${urlOf(slug)}index.md`;
  const metaRow = `<div class="doc-meta"><span>Updated <time datetime="${updated}">${updated}</time></span><span class="dot" aria-hidden="true"></span><span>${mins} min read</span><span class="meta-links"><a href="${edit}">${PEN_ICON}Edit this page</a><button class="share-page" type="button">${SHARE_ICON}<span>Share</span></button><a href="${issue}">${BUG_ICON}Report an issue</a><a class="md-page" href="${mdHref}">View as Markdown</a></span><button class="copy-page" type="button" data-md="${mdHref}">Copy page</button></div>`;
  const community = `<aside class="community"><div><p class="cm-h">Stuck, or have an idea?</p><p>Cryo is built in the open. Check Troubleshooting, open an issue, or send a pull request.</p></div><div class="cm-actions"><a class="btn btn-sm" href="${root}${urlOf("troubleshooting")}">Troubleshooting</a><a class="btn btn-sm" href="${cfg.repo}/issues">Issues</a><a class="btn btn-sm btn-primary" href="${cfg.repo}">${GH_ICON}Star on GitHub</a></div></aside>`;
  const canonical = `${cfg.url}/${urlOf(slug)}`;
  const mdUrl = `${canonical}index.md`;
  const html = `<!doctype html><html lang="en">${head({ root, title, desc: meta.description || meta.lede, canonical, slug, markdown: mdHref })}<body class="docs" data-page="${slug || "home"}" data-root="${root}"><a class="skip" href="#main">Skip to content</a>${header(root, entry.group.id)}<div class="shell">${sidebar(root, slug)}<main class="main" id="main"><article class="doc"><header class="doc-head">${crumbs}<h1>${esc(title)}</h1><p class="lede">${meta.lede}</p>${metaRow}</header>${t.mobile}<div class="prose">${content}</div>${community}${pager}</article></main>${t.rail}</div>${footer(root)}${SEARCH_DIALOG}<script src="${root}assets/js/site.js?v=${JS_REV}" defer></script></body></html>`;

  // search entries: one per h2/h3 section plus the page itself
  const plain = (frag) => stripTags(frag.replace(/<svg[\s\S]*?<\/svg>/g, " ").replace(/<button[\s\S]*?<\/button>/g, " ").replace(/<div class="fig-bar">[\s\S]*?<\/div>/g, " ")).slice(0, 420);
  const secs = content.split(/(?=<h[23] id=")/);
  searchDocs.push({ t: title, h: "", u: urlOf(slug), x: stripTags(meta.lede), g: entry.group.label, k: searchKeys(urlOf(slug), "", entry.group.label, title) });
  for (const sec of secs) {
    const m = /^<h([23]) id="([^"]+)"[^>]*>([\s\S]*?)<a class="anchor"/.exec(sec);
    if (!m) continue;
    searchDocs.push({ t: title, h: stripTags(m[3]), u: `${urlOf(slug)}#${m[2]}`, x: plain(sec.replace(/^<h[23][\s\S]*?<\/h[23]>/, "")), g: entry.group.label, k: searchKeys(urlOf(slug), m[2], entry.group.label, title) });
  }
  sitemap.push({ loc: canonical, lastmod: updated });
  const markdown = pageMarkdown({ title, lede: meta.lede, description: meta.description, updated, canonical, mdUrl, html: rewriteLinks(restore(hd.html, ex.holds), `${cfg.url}/`) });
  return {
    path: join(SITE, slug ? `${slug}/index.html` : "index.html"),
    html,
    markdown: { path: join(SITE, slug, "index.md"), html: markdown, title, summary: PAGE_SUMMARIES[slug] || oneLine(meta.description || meta.lede), url: `${urlOf(slug)}` },
  };
}

function renderHome(entry) {
  const { meta, body } = parsePage("");
  const root = "./";
  const ctx = { fig: 0, tabs: 0 };
  const ex = expandComponents(body, ctx);
  const hd = processHeadings(ex.body);
  const content = rewriteLinks(restore(hd.html, ex.holds), root);
  const canonical = `${cfg.url}/`;
  const mdHref = `${root}index.md`;
  const html = `<!doctype html><html lang="en">${head({ root, title: meta.title, desc: meta.description, canonical, slug: "", markdown: mdHref })}<body class="home-page" data-page="home" data-root="${root}"><a class="skip" href="#main">Skip to content</a>${header(root, "")}${sidebar(root, "")}<main class="home" id="main">${content}</main>${footer(root)}${SEARCH_DIALOG}<script src="${root}assets/js/site.js?v=${JS_REV}" defer></script></body></html>`;
  searchDocs.push({ t: "Overview", h: "", u: "", x: stripTags(meta.description), g: "Get started", k: searchKeys("", "", "Get started", "Overview") });
  for (const sec of content.split(/(?=<h2 id=")/)) {
    const m = /^<h2 id="([^"]+)"[^>]*>([\s\S]*?)<a class="anchor"/.exec(sec);
    if (m) searchDocs.push({ t: "Overview", h: stripTags(m[2]), u: `#${m[1]}`, x: stripTags(sec.replace(/^<h2[\s\S]*?<\/h2>/, "")).slice(0, 420), g: "Get started", k: searchKeys("", m[1], "Get started", "Overview") });
  }
  sitemap.push({ loc: canonical, lastmod: meta.updated || "2026-10-05" });
  const markdown = pageMarkdown({ title: meta.title, lede: meta.lede, description: meta.description, updated: meta.updated, canonical, mdUrl: `${canonical}index.md`, html: rewriteLinks(restore(hd.html, ex.holds), `${cfg.url}/`) });
  return {
    path: join(SITE, "index.html"),
    html,
    markdown: { path: join(SITE, "index.md"), html: markdown, title: "Overview", summary: PAGE_SUMMARIES[""], url: "" },
  };
}

function render404() {
  const abs = cfg.url + "/";
  const root = abs;
  const html = `<!doctype html><html lang="en">${head({ root, title: "Page not found", desc: "This page does not exist.", canonical: `${cfg.url}/404.html`, slug: "404", abs: true })}<body class="docs notfound" data-page="404" data-root="${root}">${header(root, "")}${sidebar(root, "404")}<main class="main nf" id="main"><p class="nf-code">404</p><h1>This page is not in the core sample.</h1><p class="lede">The link may be old, or the page moved. Try search, or start from the overview.</p><p class="nf-actions"><a class="btn btn-primary" href="${root}">Go to the overview</a><a class="btn" href="${root}quickstart/">Open the quickstart</a></p></main>${footer(root)}${SEARCH_DIALOG}<script src="${root}assets/js/site.js?v=${JS_REV}" defer></script></body></html>`;
  return { path: join(SITE, "404.html"), html };
}

// ---------- run ----------
const outputs = [];
for (const entry of flat) outputs.push(entry.slug === "" ? renderHome(entry) : renderDoc(entry));
outputs.push(render404());
outputs.push({ path: join(SITE, "assets/search.json"), html: JSON.stringify({ v: 1, docs: searchDocs }) });
outputs.push({
  path: join(SITE, "sitemap.xml"),
  html: `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${sitemap.map((s) => `  <url><loc>${s.loc}</loc><lastmod>${s.lastmod}</lastmod></url>`).join("\n")}\n</urlset>\n`,
});
outputs.push({ path: join(SITE, "robots.txt"), html: `User-agent: *\nAllow: /\nSitemap: ${cfg.url}/sitemap.xml\n` });
const mdPages = outputs.filter((o) => o.markdown).map((o) => o.markdown);
for (const page of mdPages) outputs.push({ path: page.path, html: page.html });
outputs.push({ path: join(SITE, "llms.txt"), html: llmsTxt(mdPages) });

let stale = 0;
for (const o of outputs) {
  if (CHECK) {
    const cur = existsSync(o.path) ? readFileSync(o.path, "utf8") : null;
    if (cur !== o.html) {
      stale += 1;
      console.error(`stale: ${o.path.replace(SITE + "/", "site/")}`);
    }
  } else {
    mkdirSync(dirname(o.path), { recursive: true });
    writeFileSync(o.path, o.html);
  }
}
if (CHECK) {
  if (stale) {
    console.error(`${stale} file(s) out of date. Run: node site/scripts/build.mjs`);
    process.exit(1);
  }
  console.log("site output is up to date");
} else {
  console.log(`built ${outputs.length} files (${searchDocs.length} search entries)`);
}
