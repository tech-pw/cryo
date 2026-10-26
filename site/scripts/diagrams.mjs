// Build-time SVG diagram renderer for the Cryo docs site.
//
// Two layouts: `graph` (lanes, cards, labelled edges) and `sequence` (lifelines + numbered messages).
// Output is static inline SVG that takes every colour from CSS variables (see .dgm in site.css),
// so one drawing serves light and dark. Node coordinates are authored by hand on purpose: it keeps
// each diagram deliberate and reviewable. Sources: site/diagrams/*.json (Archify) and hld.md.
//
// Diagrams are drawn close to the article width (about 820 units) so they render near 1:1 and the
// 1.5px hairlines stay sharp. Visual language and references: docs/DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md §10.
// The `platform` spec is the Amoro-style stacked lakehouse map: actors, the stack you run, Cryo, stores, lake.

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const R = 8; // corner radius on routed edges
const NODE_H = 56;
const TILE = 26;
const TEXT_X = 50; // label inset: 14 padding + 26 tile + 10 gap

// Glyphs are 24-unit line icons; geometry follows Lucide (ISC licence).
const GLYPHS = {
  book: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z",
  layers: "M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  scan: "M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M9 12a3 3 0 1 0 6 0a3 3 0 1 0-6 0M14.2 14.2 17 17",
  plan: "M9 2h6a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2M12 11h4M12 16h4M8 11h.01M8 16h.01",
  gauge: "m12 14 4-4M3.34 19a10 10 0 1 1 17.32 0",
  cpu: "M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM9 9h6v6H9zM15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2",
  clock: "M2 12a10 10 0 1 0 20 0a10 10 0 1 0-20 0M12 6v6l4 2",
  braces: "M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1",
  window: "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM2 9h20M6 6.5h.01M9 6.5h.01",
  db: "M3 5a9 3 0 1 0 18 0a9 3 0 1 0-18 0M3 5v14a9 3 0 0 0 18 0V5M3 12a9 3 0 0 0 18 0",
  stack: "M4 3h16a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM4 10h16a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1zM4 17h16a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2a1 1 0 0 1 1-1z",
  bolt: "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
  archive: "M3 3h18a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4",
  table: "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM3 9h18M3 15h18M9 3v18",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  funnel: "M22 3H2l8 9.46V19l4 2v-8.54L22 3z",
  checks: "M18 6 7 17l-5-5M22 10l-7.5 7.5L13 16",
  tag: "M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4zM7.5 7.5h.01",
  eye: "M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7zM9 12a3 3 0 1 0 6 0a3 3 0 1 0-6 0",
  play: "M6 3l14 9-14 9V3z",
  merge: "M15 18a3 3 0 1 0 6 0a3 3 0 1 0-6 0M3 6a3 3 0 1 0 6 0a3 3 0 1 0-6 0M6 21V9a9 9 0 0 0 9 9",
  bars: "M4 5h16M4 10h12M4 15h8M4 20h4",
  boxes: "M4 3h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM15 3h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM4 14h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1zM15 14h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-5a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1z",
  expand: "M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3M9 8h6a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z",
  skip: "M5 4l10 8-10 8V4zM19 5v14",
  code: "m16 18 6-6-6-6M8 6l-6 6 6 6",
  terminal: "m4 17 6-6-6-6M12 19h8",
  calc: "M6 2h12a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM8 6h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 19h.01M12 19h4",
  user: "M8 7a4 4 0 1 0 8 0a4 4 0 1 0-8 0M4 21a8 8 0 0 1 16 0",
  hourglass: "M5 22h14M5 2h14M17 22v-4.2a2 2 0 0 0-.6-1.4L12 12l-4.4 4.4a2 2 0 0 0-.6 1.4V22M7 2v4.2a2 2 0 0 0 .6 1.4L12 12l4.4-4.4a2 2 0 0 0 .6-1.4V2",
  login: "M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  pause: "M8 4v16M16 4v16",
};

const DEFAULT_GLYPH = { cryo: "cpu", source: "book", engine: "bolt", store: "db", ui: "window", state: "check", ghost: "archive" };

// Compact 24-unit product marks for the platform map. Geometry is original (not
// copied trademark art); role colour still comes from the tile, so light and dark work.
const MARKS = {
  cryo: {
    kind: "svg",
    svg: (s) =>
      `<g transform="scale(${s / 32})"><rect x="2" y="9" width="28" height="14" rx="7" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M12 9v14" fill="none" stroke="currentColor" stroke-width="2.2"/><rect class="cryo-core" x="12.8" y="10" width="16" height="12" rx="6"/></g>`,
  },
  iceberg: { d: "M12 2 22 17H2L12 2zM3.5 19h17v3h-17z", fill: true },
  hive: { d: "M12 2 21.5 7.5v9L12 22 2.5 16.5v-9L12 2z", fill: false },
  glue: { d: "M9 2h6v3.2l3.2 6.4A5.4 5.4 0 1 1 5.8 11.6L9 5.2V2zM9 5h6M10 14.5h4", fill: false },
  trino: { d: "M12 8.2a4.4 4.4 0 1 0 0 8.8 4.4 4.4 0 0 0 0-8.8zM8.4 6.2 6.2 3.2M15.6 6.2 17.8 3.2M9 17.6c.9 1.6 5.1 1.6 6 0", fill: false },
  spark: { d: "M12 2v5.5M12 16.5V22M2 12h5.5M16.5 12H22M5.2 5.2l3.9 3.9M14.9 14.9l3.9 3.9M18.8 5.2l-3.9 3.9M9.1 14.9l-3.9 3.9", fill: false },
  k8s: { d: "M12 2.2 19.4 6v8.4L12 21.8 4.6 14.4V6L12 2.2zM12 7.6a4.4 4.4 0 1 0 0 8.8 4.4 4.4 0 0 0 0-8.8zM12 2.2v5.4M19.4 6l-3.6 5.2M19.4 14.4 15.8 12M12 21.8v-5.4M4.6 14.4 8.2 12M4.6 6l3.6 6", fill: false },
  s3: { d: "M12 3 21 8v8l-9 5-9-5V8l9-5zM12 3v18M3 8l9 5 9-5", fill: false },
  postgres: { d: "M4 6c0-1.6 3.6-3 8-3s8 1.4 8 3v12c0 1.6-3.6 3-8 3s-8-1.4-8-3V6zM4 6c0 1.6 3.6 3 8 3s8-1.4 8-3M4 12c0 1.6 3.6 3 8 3s8-1.4 8-3", fill: false },
  redis: { d: "M4 16.2 12 20.2 20 16.2 12 12.2zM4 11.1 12 15.1 20 11.1 12 7.1zM4 6 12 10 20 6 12 2z", fill: false },
  helm: { d: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM12 7.4a4.6 4.6 0 1 0 0 9.2 4.6 4.6 0 0 0 0-9.2zM12 3.5v3.9M12 16.6v3.9M3.5 12h3.9M16.6 12h3.9M6.1 6.1l2.7 2.7M15.2 15.2l2.7 2.7M17.9 6.1l-2.7 2.7M8.8 15.2 6.1 17.9", fill: false },
};

function mark(name, cx, cy, size = 16) {
  const m = MARKS[name];
  if (!m) throw new Error(`unknown mark "${name}"`);
  const k = size / 24;
  if (m.kind === "svg") {
    return `<g class="n-mark n-mark-svg" transform="translate(${cx - size / 2} ${cy - size / 2})">${m.svg(size)}</g>`;
  }
  const cls = m.fill ? "n-mark is-fill" : "n-mark is-line";
  const rule = m.evenodd ? ' fill-rule="evenodd"' : "";
  return `<path class="${cls}" d="${m.d}"${rule} transform="translate(${cx - size / 2} ${cy - size / 2}) scale(${k})"/>`;
}

// Text width estimates for IBM Plex at the sizes in site.css. Used only to warn at build time.
const est = (s, mono, size) => String(s).length * size * (mono ? 0.6 : 0.52);

function glyph(name, cx, cy, size = 16) {
  const d = GLYPHS[name];
  if (!d) throw new Error(`unknown glyph "${name}"`);
  const k = size / 24;
  return `<path class="n-glyph" d="${d}" transform="translate(${cx - size / 2} ${cy - size / 2}) scale(${k})"/>`;
}

function anchorPoint(nodes, ref) {
  const m = /^([\w-]+)\.([trbl])([+-]\d+)?$/.exec(ref);
  if (!m) throw new Error(`bad anchor "${ref}"`);
  const n = nodes.get(m[1]);
  if (!n) throw new Error(`unknown node in anchor "${ref}"`);
  const off = Number(m[3] || 0);
  const cx = n.x + n.w / 2;
  const cy = n.y + n.h / 2;
  switch (m[2]) {
    case "t": return { x: cx + off, y: n.y, s: "t" };
    case "b": return { x: cx + off, y: n.y + n.h, s: "b" };
    case "l": return { x: n.x, y: cy + off, s: "l" };
    default: return { x: n.x + n.w, y: cy + off, s: "r" };
  }
}

function route(p0, p1, via) {
  if (via?.length) return [p0, ...via.map(([x, y]) => ({ x, y })), p1];
  const h0 = p0.s === "l" || p0.s === "r";
  const h1 = p1.s === "l" || p1.s === "r";
  if (h0 && h1) {
    if (Math.abs(p0.y - p1.y) < 1) return [p0, p1];
    const mx = Math.round((p0.x + p1.x) / 2);
    return [p0, { x: mx, y: p0.y }, { x: mx, y: p1.y }, p1];
  }
  if (!h0 && !h1) {
    if (Math.abs(p0.x - p1.x) < 1) return [p0, p1];
    const my = Math.round((p0.y + p1.y) / 2);
    return [p0, { x: p0.x, y: my }, { x: p1.x, y: my }, p1];
  }
  return h0 ? [p0, { x: p1.x, y: p0.y }, p1] : [p0, { x: p0.x, y: p1.y }, p1];
}

function roundedPath(pts) {
  let d = `M${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const a = pts[i - 1], b = pts[i], c = pts[i + 1];
    const l1 = Math.hypot(b.x - a.x, b.y - a.y);
    const l2 = Math.hypot(c.x - b.x, c.y - b.y);
    const r = Math.min(R, l1 / 2, l2 / 2);
    const p = { x: b.x - ((b.x - a.x) / l1) * r, y: b.y - ((b.y - a.y) / l1) * r };
    const q = { x: b.x + ((c.x - b.x) / l2) * r, y: b.y + ((c.y - b.y) / l2) * r };
    d += ` L${p.x} ${p.y} Q${b.x} ${b.y} ${q.x} ${q.y}`;
  }
  const last = pts[pts.length - 1];
  return `${d} L${last.x} ${last.y}`;
}

function segment(pts, pick) {
  const segs = [];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    segs.push({ a, b, len: Math.hypot(b.x - a.x, b.y - a.y), h: Math.abs(a.y - b.y) < 1 });
  }
  if (pick !== undefined) return segs[(pick + segs.length) % segs.length];
  return segs.reduce((best, s) => (s.len > best.len ? s : best));
}

// The halo behind a label must match the fill it sits on, so pick the innermost lane under it.
function surfaceAt(lanes, x, y) {
  let hit = null;
  for (const l of lanes) {
    if (l.kind === "title") continue;
    if (x >= l.x && x <= l.x + l.w && y >= l.y && y <= l.y + l.h && (!hit || l.w * l.h < hit.w * hit.h)) hit = l;
  }
  return hit ? `on-${hit.kind || "plain"}` : "on-canvas";
}

const stepBadge = (n, x, y) =>
  `<g class="step"><circle cx="${x}" cy="${y}" r="9"/><text x="${x}" y="${y + 4}" text-anchor="middle">${n}</text></g>`;

// A numbered edge carries its badge on the line itself, so the label moves clear of it.
function edgeLabel(e, pts, lanes) {
  const seg = segment(pts, e.lseg);
  let x, y;
  if (e.lpos) [x, y] = e.lpos;
  else {
    x = (seg.a.x + seg.b.x) / 2;
    y = (seg.a.y + seg.b.y) / 2;
  }
  const side = e.lside || (seg.h ? "t" : "r");
  const g = e.step ? 7 : 0;
  const at = {
    t: { x, y: y - 7 - g, a: "middle" },
    b: { x, y: y + 16 + g, a: "middle" },
    r: { x: x + 9 + g, y: y + 4, a: "start" },
    l: { x: x - 9 - g, y: y + 4, a: "end" },
    c: { x, y: y + 4, a: "middle" },
  }[side];
  const badge = e.step ? stepBadge(e.step, x, y) : "";
  const text = e.label ? `<text class="elabel ${surfaceAt(lanes, x, y)}" x="${at.x}" y="${at.y}" text-anchor="${at.a}">${esc(e.label)}</text>` : "";
  return badge + text;
}

function laneLabel(text) {
  const m = /^(.*?)\s*\((.*)\)$/.exec(text);
  if (!m) return esc(text);
  return `${esc(m[1])}<tspan class="lane-q" dx="6">${esc(m[2])}</tspan>`;
}

function tileKind(n) {
  if (n.kind === "state") return `tone-${n.tone || "info"}`;
  return `t-${n.kind || "cryo"}`;
}

function nodeEmblem(n, cx, cy, size) {
  return n.mark ? mark(n.mark, cx, cy, size) : glyph(n.icon || DEFAULT_GLYPH[n.kind || "cryo"], cx, cy, size);
}

function renderNode(n, warn) {
  const kind = n.kind || "cryo";
  const cy = n.y + n.h / 2;
  const icon = n.icon || DEFAULT_GLYPH[kind];
  let body = kind === "ghost" ? "" : `<rect class="n-shadow" x="${n.x}" y="${n.y + 2}" width="${n.w}" height="${n.h}" rx="10"/>`;
  body += `<rect class="n-box" x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="10"/>`;
  const labelMono = n.code === "label";
  const subMono = n.code === "sub";
  if (n.face === "logo") {
    const cx = n.x + n.w / 2;
    const tile = 32;
    const top = n.y + (n.h >= 100 ? 18 : 10);
    body += `<g class="n-tile ${tileKind(n)}"><rect x="${cx - tile / 2}" y="${top}" width="${tile}" height="${tile}" rx="8"/>${nodeEmblem(n, cx, top + tile / 2, 18)}</g>`;
    body += `<text class="n-label" x="${cx}" y="${top + tile + 16}" text-anchor="middle">${esc(n.label)}</text>`;
    if (n.sub) body += `<text class="n-sub" x="${cx}" y="${top + tile + 30}" text-anchor="middle">${esc(n.sub)}</text>`;
    const need = Math.max(est(n.label, false, 13), n.sub ? est(n.sub, false, 11) : 0) + 12;
    if (need > n.w) warn(`${n.id}: text needs about ${Math.ceil(need)} of ${n.w}`);
  } else if (n.stack) {
    const cx = n.x + n.w / 2;
    body += `<g class="n-tile ${tileKind(n)}"><rect x="${cx - 12}" y="${n.y + 12}" width="24" height="24" rx="7"/>${nodeEmblem({ ...n, icon }, cx, n.y + 24, 14)}</g>`;
    body += `<text class="n-label" x="${cx}" y="${n.y + 56}" text-anchor="middle">${esc(n.label)}</text>`;
    if (n.sub) body += `<text class="n-sub" x="${cx}" y="${n.y + 72}" text-anchor="middle">${esc(n.sub)}</text>`;
    const need = Math.max(est(n.label, false, 14), n.sub ? est(n.sub, false, 12) : 0) + 16;
    if (need > n.w) warn(`${n.id}: text needs about ${Math.ceil(need)} of ${n.w}`);
  } else {
    const tx = n.x + 14;
    body += `<g class="n-tile ${tileKind(n)}"><rect x="${tx}" y="${cy - TILE / 2}" width="${TILE}" height="${TILE}" rx="7"/>${nodeEmblem({ ...n, icon }, tx + TILE / 2, cy)}</g>`;
    const x = n.x + TEXT_X;
    const ly = n.sub ? cy - 4 : cy + 5;
    body += `<text class="n-label${labelMono ? " n-code" : ""}" x="${x}" y="${ly}">${esc(n.label)}</text>`;
    if (n.sub) body += `<text class="n-sub${subMono ? " n-code" : ""}" x="${x}" y="${cy + 13}">${esc(n.sub)}</text>`;
    const need = TEXT_X + 12 + Math.max(est(n.label, labelMono, labelMono ? 13 : 14), n.sub ? est(n.sub, subMono, subMono ? 11.5 : 12) : 0);
    if (need > n.w) warn(`${n.id}: text needs about ${Math.ceil(need)} of ${n.w}`);
  }
  if (n.step) body += stepBadge(n.step, n.x, cy);
  if (n.marker) {
    const fx = n.x + n.w - 18;
    const fy = n.y;
    body += `<g class="marker" aria-hidden="true"><path class="marker-pole" d="M${fx} ${fy}V${fy - 14}"/><path class="marker-flag" d="M${fx} ${fy - 14}l10 4l-10 4z"/></g>`;
  }
  const attrs = `class="node n-${kind}${n.face === "logo" ? " n-logo" : ""}" data-id="${n.id}"`;
  return n.href
    ? `<a ${attrs} href="${esc(n.href)}" aria-label="${esc(n.label + (n.sub ? ", " + n.sub : ""))}">${body}</a>`
    : `<g ${attrs}>${body}</g>`;
}

function defs(id) {
  const head = (k) =>
    `<marker id="${id}-ah-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto"><path class="ah ah-${k}" d="M1 1.2 L9.5 5 L1 8.8 L2.6 5 z"/></marker>`;
  return `<defs>${head("read")}${head("write")}${head("ctl")}</defs>`;
}

function edgeAlt(spec, e) {
  const label = (id) => {
    const key = id.split(".")[0];
    const n = spec.nodes.find((x) => x.id === key) || spec.lanes?.find((x) => x.id === key);
    return n ? n.label.replace(/\s*\(.*\)$/, "") : key;
  };
  if (e.kind === "no") return `${label(e.from)} is not read by ${label(e.to)}`;
  const text = `${label(e.from)} to ${label(e.to)}${e.label ? ` (${e.label})` : ""}`;
  return e.step ? `Step ${e.step}: ${text}` : text;
}

const byStep = (edges) =>
  [...edges].sort((a, b) => (a.step ?? Infinity) - (b.step ?? Infinity));

function laneHeading(l) {
  const tx = l.mark ? l.x + 40 : l.x + 16;
  const badge = l.mark ? `<g class="lane-mark">${mark(l.mark, l.x + 26, l.y + 20, 16)}</g>` : "";
  return `${badge}<text x="${tx}" y="${l.y + 25}">${laneLabel(l.label)}</text>`;
}

function chevronAt(x, y) {
  return `<g class="chevron" transform="translate(${x} ${y})"><rect x="-13" y="-11" width="26" height="22" rx="7"/><path d="M-5 -5.5 2 0 -5 5.5M3 -5.5 10 0 3 5.5"/></g>`;
}

function stripSvg(s) {
  const n = s.items.length;
  const inner = s.items
    .map((t, i) => {
      const x = s.x + ((i + 0.5) * s.w) / n;
      const rule = i === 0 ? "" : `<path class="strip-rule" d="M${s.x + (i * s.w) / n} ${s.y + 10}V${s.y + s.h - 10}"/>`;
      return `${rule}<text class="strip-item" x="${x}" y="${s.y + s.h / 2 + 4}" text-anchor="middle">${esc(t)}</text>`;
    })
    .join("");
  return `<g class="strip"><rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="8"/>${inner}</g>`;
}

export function renderGraph(id, spec, warn) {
  const nodes = new Map();
  for (const n of spec.nodes) nodes.set(n.id, { w: 168, h: n.face === "logo" ? 80 : NODE_H, ...n });
  const lanes = spec.lanes || [];
  for (const l of lanes) nodes.set(l.id, { ...l });
  const laneSvg = lanes
    .map((l) => {
      if (l.kind === "title") return `<text class="lane-title" x="${l.x}" y="${l.y}">${laneLabel(l.label)}</text>`;
      const clickable = l.href ? ` data-href="${esc(l.href)}"` : "";
      return `<g class="lane lane-${l.kind || "plain"}"${clickable}><rect x="${l.x}" y="${l.y}" width="${l.w}" height="${l.h}" rx="14"/>${laneHeading(l)}</g>`;
    })
    .join("");
  const chevrons = (spec.chevrons || []).map((c) => chevronAt(c.x, c.y)).join("");
  const strips = (spec.strips || []).map(stripSvg).join("");
  // Once a diagram numbers its main path, every unnumbered edge is a side path and recedes.
  const stepped = (spec.edges || []).some((e) => e.step);
  const layer = (e) => (e.step ? 2 : e.main ? 1 : 0);
  const edges = [...(spec.edges || [])]
    .sort((a, b) => layer(a) - layer(b))
    .map((e) => {
      const p0 = anchorPoint(nodes, e.from);
      const p1 = anchorPoint(nodes, e.to);
      const pts = route(p0, p1, e.via);
      const kind = e.kind || "write";
      const from = e.from.split(".")[0];
      const to = e.to.split(".")[0];
      const label = e.label || e.step ? edgeLabel(e, pts, lanes) : "";
      const role = e.step || e.main ? " is-main" : stepped ? " is-aside" : "";
      if (kind === "no") {
        const s = segment(pts);
        const mx = (s.a.x + s.b.x) / 2, my = (s.a.y + s.b.y) / 2;
        return `<g class="edge e-no" data-from="${from}" data-to="${to}"><path d="${roundedPath(pts)}"/><g class="no-mark"><circle cx="${mx}" cy="${my}" r="8"/><path d="M${mx - 3} ${my - 3}l6 6m0 -6l-6 6"/></g>${label}</g>`;
      }
      const port = e.port === false ? "" : `<circle class="port" cx="${p0.x}" cy="${p0.y}" r="2.75"/>`;
      return `<g class="edge e-${kind}${role}" data-from="${from}" data-to="${to}"><path d="${roundedPath(pts)}" marker-end="url(#${id}-ah-${kind})"/>${port}${label}</g>`;
    })
    .join("");
  const nodeSvg = spec.nodes.map((n) => renderNode(nodes.get(n.id), warn)).join("");
  const klass = spec.klass ? ` ${spec.klass}` : "";
  const svg = `<svg class="dgm${klass}" viewBox="0 0 ${spec.w} ${spec.h}" role="img" aria-labelledby="${id}-t ${id}-d" data-diagram="${id}"><title id="${id}-t">${esc(spec.title)}</title><desc id="${id}-d">${esc(spec.desc)}</desc>${defs(id)}<g class="lanes">${laneSvg}</g><g class="strips">${strips}</g><g class="edges">${edges}</g><g class="chevrons">${chevrons}</g><g class="nodes">${nodeSvg}</g></svg>`;
  const alt = spec.alt || byStep(spec.edges || []).map((e) => edgeAlt(spec, e));
  return { svg, alt, legend: spec.legend, w: spec.w };
}

export function renderSequence(id, spec, warn) {
  const n = spec.participants.length;
  const left = spec.left ?? 100;
  const step = (spec.w - left - 60) / (n - 1);
  const xs = spec.xs || spec.participants.map((_, i) => Math.round(left + i * step));
  const bw = spec.headW ?? 116;
  const top = 24;
  const bh = 84;
  const rowH = spec.rowH ?? 40;
  const my0 = top + bh + 52;
  const my = (i) => my0 + i * rowH;
  const idx = new Map(spec.participants.map((p, i) => [p.id, i]));
  const bottom = my(spec.messages.length - 1) + 30;
  const bands = (spec.bands || [])
    .map((b) => {
      const y1 = my(b.from) - 26;
      const y2 = my(b.to) + 10;
      return `<g class="band"><rect x="8" y="${y1}" width="${spec.w - 16}" height="${y2 - y1}" rx="10"/><text x="20" y="${y1 + 19}">${esc(b.label)}</text></g>`;
    })
    .join("");
  const lifelines = spec.participants.map((p, i) => `<path class="lifeline" d="M${xs[i]} ${top + bh + 2}V${bottom}"/>`).join("");
  const heads = spec.participants
    .map((p, i) => renderNode({ ...p, kind: p.kind || "cryo", x: xs[i] - bw / 2, y: top, w: bw, h: bh, stack: true }, warn))
    .join("");
  const msgs = spec.messages
    .map((m, i) => {
      const a = xs[idx.get(m.from)];
      const b = xs[idx.get(m.to)];
      const y = my(i);
      const dir = Math.sign(b - a) || 1;
      const kind = m.kind || "write";
      const bx = a + dir * 17;
      const tx = a + dir * 32;
      const span = Math.abs(b - a) - 32;
      if (est(m.label, false, 12.5) > span) warn(`message ${i + 1} "${m.label}" overruns its arrow`);
      return `<g class="edge e-${kind}" data-from="${m.from}" data-to="${m.to}"><path d="M${a + dir * 3} ${y}H${b - dir * 3}" marker-end="url(#${id}-ah-${kind})"/><g class="step"><circle cx="${bx}" cy="${y - 13}" r="9"/><text x="${bx}" y="${y - 9}" text-anchor="middle">${i + 1}</text></g><text class="mlabel" x="${tx}" y="${y - 9}" text-anchor="${dir > 0 ? "start" : "end"}">${esc(m.label)}</text></g>`;
    })
    .join("");
  const h = bottom + 16;
  const svg = `<svg class="dgm dgm-seq" viewBox="0 0 ${spec.w} ${h}" role="img" aria-labelledby="${id}-t ${id}-d" data-diagram="${id}"><title id="${id}-t">${esc(spec.title)}</title><desc id="${id}-d">${esc(spec.desc)}</desc>${defs(id)}<g class="bands">${bands}</g><g class="lifelines">${lifelines}</g><g class="edges">${msgs}</g><g class="nodes">${heads}</g></svg>`;
  const alt = spec.messages.map((m) => `${spec.participants[idx.get(m.from)].label} to ${spec.participants[idx.get(m.to)].label}: ${m.alt || m.label}`);
  return { svg, alt, legend: spec.legend, w: spec.w, ordered: true };
}

const L_READ = { kind: "read", text: "Read path" };
const L_WRITE = { kind: "write", text: "Write or execute" };
const L_CTL = { kind: "ctl", text: "Control or probe" };

export const diagrams = {
  overview: {
    type: "graph",
    w: 1056, h: 256,
    title: "Cryo at a glance",
    desc: "Sources feed the Scanner. The Governor admits work to Workers. Workers ask the Planner for a plan and execute it on Trino or Spark.",
    legend: [L_READ, L_WRITE, L_CTL],
    lanes: [
      { id: "src", label: "Sources (read)", x: 16, y: 16, w: 188, h: 224, kind: "read" },
      { id: "cryo", label: "Cryo", x: 284, y: 16, w: 488, h: 224, kind: "cryo" },
      { id: "dst", label: "Destinations (write)", x: 852, y: 16, w: 188, h: 224, kind: "write" },
    ],
    nodes: [
      { id: "iceberg", label: "Iceberg REST", sub: "catalog", x: 32, y: 56, w: 156, kind: "source", icon: "book" },
      { id: "hive", label: "Hive or Glue", sub: "metastore", x: 32, y: 168, w: 156, kind: "source", icon: "layers" },
      { id: "scanner", label: "Scanner", sub: "reads metadata", x: 300, y: 56, w: 200, icon: "scan", href: "concepts/#scan-path" },
      { id: "governor", label: "Governor", sub: "admits against live load", x: 556, y: 56, w: 200, icon: "gauge", marker: true, href: "maintenance/#admission" },
      { id: "planner", label: "Planner", sub: "emits no SQL", x: 300, y: 168, w: 200, icon: "plan", href: "maintenance/#chunking" },
      { id: "workers", label: "Workers", sub: "one table at a time", x: 556, y: 168, w: 200, icon: "cpu", href: "architecture/#component-jobs" },
      { id: "trino", label: "Trino", sub: "Trino SQL", x: 868, y: 56, w: 156, kind: "engine", href: "engines/" },
      { id: "spark", label: "Spark on EMR", sub: "Spark SQL", x: 868, y: 168, w: 156, kind: "engine", href: "engines/" },
    ],
    edges: [
      { from: "iceberg.r", to: "scanner.l", label: "metadata", lpos: [244, 84], kind: "read" },
      { from: "hive.r", to: "scanner.l", via: [[244, 196], [244, 84]], kind: "read" },
      { from: "governor.b", to: "workers.t", label: "admit", kind: "write" },
      { from: "workers.l", to: "planner.r", label: "plan", kind: "write" },
      { from: "governor.r", to: "trino.l", label: "probe load", kind: "ctl" },
      { from: "workers.r", to: "spark.l", label: "execute", kind: "write" },
    ],
  },

  platform: {
    type: "graph",
    klass: "dgm-platform",
    w: 1056, h: 528,
    title: "Cryo in the lakehouse",
    desc: "Operators, catalogs and engines you already run sit on the left. Cryo is the control plane in the middle: it scans catalogs, plans work, and admits it to Trino or Spark. Object storage stays on the lake side. Cryo is not a catalog.",
    legend: [],
    alt: [
      "Operators use the Web UI. Catalogs, destinations and monitors sit beside them and point into the stack Cryo talks to.",
      "The stack you already run is Iceberg REST, Apache Hive, AWS Glue, Trino and Spark on EMR.",
      "Cryo’s control plane is the HTTP API, Scanner, Planner, Governor, Scheduler and Workers. The planner emits no SQL.",
      "PostgreSQL, Redis, Kubernetes and Helm sit under Cryo as stores and deploy.",
      "The data lake is Amazon S3 holding Iceberg data files. Engines write there. The scanner does not read those files.",
    ],
    lanes: [
      { id: "stack", label: "You already run", x: 176, y: 16, w: 164, h: 496, kind: "read" },
      { id: "cryo", label: "Cryo control plane", x: 360, y: 16, w: 456, h: 300, kind: "cryo", mark: "cryo" },
      { id: "plug", label: "Stores and deploy", x: 360, y: 332, w: 456, h: 180, kind: "plain" },
      { id: "lake", label: "Data lake (write)", x: 836, y: 16, w: 204, h: 496, kind: "write" },
    ],
    chevrons: [
      { x: 154, y: 92 },
      { x: 154, y: 214 },
      { x: 154, y: 336 },
      { x: 154, y: 458 },
    ],
    strips: [
      { x: 376, y: 260, w: 424, h: 40, items: ["Iceberg tables", "No SQL in the planner", "A receipt per action"] },
    ],
    nodes: [
      { id: "ops", label: "Operators", x: 16, y: 52, w: 124, face: "logo", kind: "ui", icon: "user", href: "architecture/#component-jobs" },
      { id: "cats", label: "Catalogs", x: 16, y: 174, w: 124, face: "logo", kind: "source", icon: "layers", href: "concepts/#three-parts" },
      { id: "dest", label: "Engines", x: 16, y: 296, w: 124, face: "logo", kind: "engine", icon: "bolt", href: "engines/" },
      { id: "obs", label: "Monitors", x: 16, y: 418, w: 124, face: "logo", kind: "store", icon: "eye", href: "health/" },

      { id: "iceberg", label: "Iceberg REST", x: 186, y: 52, w: 144, face: "logo", mark: "iceberg", kind: "source" },
      { id: "hive", label: "Apache Hive", x: 186, y: 144, w: 144, face: "logo", mark: "hive", kind: "source" },
      { id: "glue", label: "AWS Glue", x: 186, y: 236, w: 144, face: "logo", mark: "glue", kind: "source" },
      { id: "trino", label: "Trino", x: 186, y: 328, w: 144, face: "logo", mark: "trino", kind: "engine", href: "engines/" },
      { id: "spark", label: "Spark on EMR", x: 186, y: 420, w: 144, face: "logo", mark: "spark", kind: "engine", href: "engines/" },

      { id: "api", label: "HTTP API", sub: "FastAPI", x: 376, y: 54, w: 208, icon: "braces", href: "reference/#public-surfaces" },
      { id: "scanner", label: "Scanner", sub: "metadata only", x: 592, y: 54, w: 208, icon: "scan", href: "concepts/#scan-path" },
      { id: "planner", label: "Planner", sub: "emits no SQL", x: 376, y: 118, w: 208, icon: "plan", href: "maintenance/#chunking" },
      { id: "governor", label: "Governor", sub: "admits against load", x: 592, y: 118, w: 208, icon: "gauge", marker: true, href: "maintenance/#admission" },
      { id: "scheduler", label: "Scheduler", sub: "due presets", x: 376, y: 182, w: 208, icon: "clock" },
      { id: "workers", label: "Workers", sub: "one table at a time", x: 592, y: 182, w: 208, icon: "cpu", href: "architecture/#component-jobs" },

      { id: "pg", label: "PostgreSQL", x: 370, y: 376, w: 100, h: 112, face: "logo", mark: "postgres", kind: "store" },
      { id: "rd", label: "Redis", x: 482, y: 376, w: 100, h: 112, face: "logo", mark: "redis", kind: "store" },
      { id: "k8s", label: "Kubernetes", x: 594, y: 376, w: 100, h: 112, face: "logo", mark: "k8s", kind: "cryo" },
      { id: "helm", label: "Helm", x: 706, y: 376, w: 100, h: 112, face: "logo", mark: "helm", kind: "cryo" },

      { id: "s3", label: "Amazon S3", sub: "object storage", x: 852, y: 64, w: 172, h: 112, face: "logo", mark: "s3", kind: "store" },
      { id: "files", label: "Iceberg files", sub: "engines write here", x: 852, y: 196, w: 172, h: 112, face: "logo", mark: "iceberg", kind: "store" },
      { id: "noread", label: "Unread by scan", sub: "data files stay put", x: 852, y: 360, w: 172, h: 112, face: "logo", kind: "ghost", icon: "archive" },
    ],
    edges: [],
  },

  system: {
    type: "graph",
    w: 904, h: 644,
    title: "Cryo reads your catalogs, then maintains tables through your engines",
    desc: "Numbered steps: the Scanner reads catalog metadata and saves stats to PostgreSQL. The Scheduler queues jobs in Redis, the Governor admits them, Workers get a plan from the Planner and execute it on Trino or Spark, which write to object storage. Side paths: the API serves the Web UI from PostgreSQL, and the Governor checks live engine load.",
    legend: [L_READ, L_WRITE, L_CTL],
    lanes: [
      { id: "sources", label: "Sources (read)", x: 16, y: 16, w: 424, h: 112, kind: "read" },
      { id: "op", label: "Operator", x: 464, y: 16, w: 200, h: 112, kind: "plain" },
      { id: "cryo", label: "Cryo", x: 16, y: 168, w: 872, h: 216, kind: "cryo" },
      { id: "dest", label: "Destinations (write)", x: 464, y: 424, w: 424, h: 112, kind: "write" },
    ],
    nodes: [
      { id: "iceberg", label: "Iceberg REST", sub: "catalog", x: 32, y: 56, kind: "source", icon: "book" },
      { id: "hive", label: "Hive or Glue", sub: "metastore", x: 256, y: 56, kind: "source", icon: "layers" },
      { id: "web", label: "Web UI", sub: "operator UI", x: 480, y: 56, kind: "ui" },
      { id: "scanner", label: "Scanner", sub: "reads metadata", x: 32, y: 208, icon: "scan", href: "concepts/#scan-path" },
      { id: "postgres", label: "PostgreSQL", sub: "stats and audit", x: 256, y: 208, kind: "store" },
      { id: "api", label: "API", sub: "serves the UI", x: 480, y: 208, icon: "braces", href: "reference/#public-surfaces" },
      { id: "planner", label: "Planner", sub: "builds the plan", x: 704, y: 208, icon: "plan", href: "maintenance/#chunking" },
      { id: "scheduler", label: "Scheduler", sub: "starts presets", x: 32, y: 312, icon: "clock" },
      { id: "redis", label: "Redis", sub: "queue and locks", x: 256, y: 312, kind: "store", icon: "stack" },
      { id: "governor", label: "Governor", sub: "checks live load", x: 480, y: 312, icon: "gauge", marker: true, href: "maintenance/#admission" },
      { id: "workers", label: "Workers", sub: "one table each", x: 704, y: 312, icon: "cpu" },
      { id: "trino", label: "Trino", sub: "runs Trino SQL", x: 480, y: 464, kind: "engine", href: "engines/" },
      { id: "spark", label: "Spark on EMR", sub: "runs Spark SQL", x: 704, y: 464, kind: "engine", href: "engines/" },
      { id: "storage", label: "Object storage", sub: "Iceberg data files", x: 480, y: 572, w: 392, kind: "store", icon: "archive" },
    ],
    edges: [
      { from: "iceberg.b-24", to: "scanner.t-24", label: "metadata", lpos: [92, 148], lside: "l", kind: "read", step: 1 },
      { from: "hive.b", to: "scanner.t+28", via: [[340, 148], [144, 148]], kind: "read", main: true },
      { from: "scanner.r", to: "postgres.l", label: "stats", kind: "write", step: 2 },
      { from: "scheduler.r", to: "redis.l", label: "queue", kind: "write", step: 3 },
      { from: "governor.r", to: "workers.l", label: "admit", kind: "write", step: 4 },
      { from: "workers.t", to: "planner.b", label: "plan", kind: "write", step: 5 },
      { from: "workers.b", to: "dest.t+112", label: "execute", kind: "write", step: 6 },
      { from: "trino.b", to: "storage.t-112", label: "write files", kind: "write", step: 7 },
      { from: "spark.b", to: "storage.t+112", kind: "write", main: true },
      { from: "web.b", to: "api.t", label: "REST", lpos: [564, 148], kind: "read" },
      { from: "api.l", to: "postgres.r", label: "reads", kind: "read" },
      { from: "governor.l", to: "redis.r", label: "slots", kind: "ctl" },
      { from: "governor.b", to: "dest.t-112", label: "probe live load", kind: "ctl" },
    ],
  },

  scan: {
    type: "graph",
    w: 820, h: 284,
    title: "A scan reads catalog metadata, never your data files",
    desc: "Step 1: the Scanner reads catalog metadata from Iceberg REST or Hive and Glue. Step 2: it saves table stats to PostgreSQL. Object storage is not read on this path.",
    legend: [L_READ, L_WRITE],
    lanes: [
      { id: "t1", label: "Catalog (read)", x: 32, y: 28, kind: "title" },
      { id: "t2", label: "Scan", x: 340, y: 28, kind: "title" },
      { id: "t3", label: "Store", x: 612, y: 28, kind: "title" },
    ],
    nodes: [
      { id: "iceberg", label: "Iceberg REST", sub: "catalog", x: 32, y: 48, w: 176, kind: "source", icon: "book" },
      { id: "hive", label: "Hive or Glue", sub: "metastore", x: 32, y: 128, w: 176, kind: "source", icon: "layers" },
      { id: "scanner", label: "Scanner", sub: "file counts, sizes", x: 340, y: 88, w: 192, icon: "scan", marker: true },
      { id: "postgres", label: "PostgreSQL", sub: "table and partition", x: 612, y: 88, w: 192, kind: "store" },
      { id: "storage", label: "Object storage", sub: "data files are never opened", x: 340, y: 212, w: 464, h: 52, kind: "ghost" },
    ],
    edges: [
      { from: "iceberg.r", to: "scanner.l", via: [[248, 76], [248, 116]], label: "metadata", lseg: -1, kind: "read", step: 1 },
      { from: "hive.r", to: "scanner.l", via: [[248, 156], [248, 116]], kind: "read", main: true },
      { from: "scanner.r", to: "postgres.l", label: "stats", kind: "write", step: 2 },
      { from: "storage.t-136", to: "scanner.b", kind: "no" },
    ],
  },

  lifecycle: {
    type: "graph",
    w: 820, h: 232,
    title: "Every job ends recorded, even when it fails or waits",
    desc: "A job is queued, admitted by the Governor, then runs on a Worker and succeeds or fails. A queued job can be deferred and later re-queued. A failure is recorded and does not stop the run.",
    legend: [L_READ, L_WRITE, L_CTL],
    nodes: [
      { id: "queued", label: "Queued", sub: "enqueued", x: 16, y: 24, w: 170, kind: "state", icon: "hourglass" },
      { id: "admitted", label: "Admitted", sub: "by the Governor", x: 222, y: 24, w: 170, kind: "state", icon: "login", marker: true },
      { id: "running", label: "Running", sub: "on a Worker", x: 428, y: 24, w: 170, kind: "state", icon: "play" },
      { id: "succeeded", label: "Succeeded", sub: "audit written", x: 634, y: 24, w: 170, kind: "state", icon: "check", tone: "ok" },
      { id: "deferred", label: "Deferred", sub: "window or budget", x: 16, y: 152, w: 170, kind: "state", icon: "pause", tone: "warn" },
      { id: "failed", label: "Failed", sub: "run continues", x: 428, y: 152, w: 170, kind: "state", icon: "x", tone: "bad" },
    ],
    edges: [
      { from: "queued.r", to: "admitted.l", kind: "write" },
      { from: "admitted.r", to: "running.l", kind: "write" },
      { from: "running.r", to: "succeeded.l", kind: "write" },
      { from: "queued.b-40", to: "deferred.t-40", label: "deadline", kind: "ctl" },
      { from: "deferred.t+40", to: "queued.b+40", label: "re-queue", kind: "read" },
      { from: "running.b", to: "failed.t", label: "recorded", kind: "ctl" },
    ],
  },

  workflow: {
    type: "graph",
    w: 820, h: 448,
    title: "Filter, assign, preview: nothing runs until you confirm",
    desc: "In the web UI an operator filters tables, selects all matching, assigns a preset, previews the plan, then runs. The API resolves the filter into assignments. The Planner builds the preview without executing.",
    legend: [L_WRITE, L_READ, L_CTL],
    lanes: [
      { id: "ui", label: "Web UI", x: 16, y: 16, w: 260, h: 416, kind: "plain" },
      { id: "apil", label: "API", x: 300, y: 16, w: 244, h: 416, kind: "cryo" },
      { id: "pl", label: "Planner", x: 568, y: 16, w: 236, h: 416, kind: "cryo" },
    ],
    nodes: [
      { id: "filter", label: "Filter tables", sub: "server-side", x: 32, y: 56, w: 228, kind: "ui", icon: "funnel", step: 1 },
      { id: "select", label: "Select all matching", sub: "a filter spec, not ids", x: 32, y: 132, w: 228, kind: "ui", icon: "checks", step: 2 },
      { id: "assign", label: "Assign preset", sub: "preset_id", code: "sub", x: 32, y: 208, w: 228, kind: "ui", icon: "tag", step: 3 },
      { id: "preview", label: "Preview plan", sub: "dry run", x: 32, y: 284, w: 228, kind: "ui", icon: "eye", step: 4 },
      { id: "run", label: "Run", sub: "after you confirm", x: 32, y: 360, w: 228, kind: "ui", icon: "play", marker: true, step: 5 },
      { id: "resolve", label: "Resolve filter", sub: "upsert assignments", x: 316, y: 208, w: 212, icon: "merge" },
      { id: "plan", label: "Build plan", sub: "executes nothing", x: 584, y: 284, w: 204, icon: "plan" },
    ],
    edges: [
      { from: "filter.b", to: "select.t", kind: "write" },
      { from: "select.b", to: "assign.t", kind: "write" },
      { from: "assign.b", to: "preview.t", kind: "write" },
      { from: "preview.b", to: "run.t", kind: "write" },
      { from: "assign.r", to: "resolve.l", label: "preset", kind: "read" },
      { from: "preview.r", to: "plan.l", label: "dry run", kind: "ctl" },
    ],
  },

  readbindwrite: {
    type: "graph",
    w: 820, h: 432,
    title: "Read from a source, bind with a preset, write through a destination",
    desc: "Step 1: the Scanner reads the Iceberg catalog. Step 2: it stores each table's grade and stats. Step 3: a preset binds those tables to a destination. Step 4: the destination engine runs the maintenance.",
    legend: [L_READ, L_WRITE],
    lanes: [
      { id: "src", label: "Source (read)", x: 16, y: 16, w: 252, h: 112, kind: "read" },
      { id: "cryo", label: "Cryo", x: 16, y: 160, w: 788, h: 112, kind: "cryo" },
      { id: "dst", label: "Destination (write)", x: 552, y: 304, w: 252, h: 112, kind: "write" },
    ],
    nodes: [
      { id: "catalog", label: "Iceberg catalog", sub: "where tables live", x: 32, y: 56, w: 220, kind: "source", icon: "book" },
      { id: "scanner", label: "Scanner", sub: "stores table stats", x: 32, y: 200, w: 220, icon: "scan" },
      { id: "tables", label: "Tables", sub: "grade and stats", x: 300, y: 200, w: 220, kind: "store", icon: "table" },
      { id: "preset", label: "Preset", sub: "binds to a destination", x: 568, y: 200, w: 220, icon: "sliders", marker: true },
      { id: "engine", label: "Trino or Spark", sub: "runs maintenance", x: 568, y: 344, w: 220, kind: "engine" },
    ],
    edges: [
      { from: "catalog.b", to: "scanner.t", label: "read", lpos: [142, 144], kind: "read", step: 1 },
      { from: "scanner.r", to: "tables.l", kind: "write", step: 2 },
      { from: "tables.r", to: "preset.l", kind: "write", step: 3 },
      { from: "preset.b", to: "engine.t", label: "write", lpos: [678, 288], kind: "write", step: 4 },
    ],
  },

  wave: {
    type: "graph",
    w: 896, h: 288,
    title: "A wave: queue, admit, plan, execute",
    desc: "Step 1: the Scheduler queues due presets. Step 2: the Governor admits a job. Step 3: a Worker gets a plan from the Planner. Step 4: the Worker executes it on the destination engine. On the side, the Governor probes the engine for live load before admitting.",
    legend: [L_WRITE, L_CTL],
    lanes: [
      { id: "cryo", label: "Cryo", x: 16, y: 16, w: 864, h: 112, kind: "cryo" },
      { id: "dst", label: "Destination (write)", x: 16, y: 160, w: 864, h: 112, kind: "write" },
    ],
    nodes: [
      { id: "scheduler", label: "Scheduler", sub: "finds due runs", x: 32, y: 56, w: 166, icon: "clock" },
      { id: "governor", label: "Governor", sub: "admits or defers", x: 254, y: 56, w: 166, icon: "gauge", marker: true },
      { id: "worker", label: "Worker", sub: "one table", x: 476, y: 56, w: 166, icon: "cpu" },
      { id: "planner", label: "Planner", sub: "builds the plan", x: 698, y: 56, w: 166, icon: "plan" },
      { id: "engine", label: "Trino or Spark on EMR", sub: "renders SQL and executes", x: 254, y: 200, w: 388, kind: "engine" },
    ],
    edges: [
      { from: "scheduler.r", to: "governor.l", label: "queue", kind: "write", step: 1 },
      { from: "governor.r", to: "worker.l", label: "admit", kind: "write", step: 2 },
      { from: "worker.r", to: "planner.l", label: "plan", kind: "write", step: 3 },
      { from: "worker.b", to: "engine.t+111", label: "execute", lpos: [559, 144], kind: "write", step: 4 },
      { from: "governor.b", to: "engine.t-111", label: "probe load", lpos: [337, 144], kind: "ctl" },
    ],
  },

  chunking: {
    type: "graph",
    w: 820, h: 288,
    title: "The planner never sends one giant OPTIMIZE to a big table",
    desc: "Partitions are ranked by small-file count, then the Planner takes one of three paths: usually it chunks partitions under a GB budget; a partition bigger than the budget runs alone; a small table gets one full-table OPTIMIZE.",
    legend: [L_WRITE],
    lanes: [
      { id: "in", label: "Scan data (read)", x: 16, y: 16, w: 228, h: 256, kind: "read" },
      { id: "paths", label: "Planner picks one path per run", x: 300, y: 16, w: 504, h: 256, kind: "cryo" },
    ],
    nodes: [
      { id: "ranked", label: "Ranked partitions", sub: "most small files", x: 32, y: 128, w: 196, kind: "source", icon: "bars" },
      { id: "chunk", label: "Chunk under a GB budget", sub: "usually · a capped number of partitions per chunk", x: 316, y: 56, w: 472, icon: "boxes" },
      { id: "oversize", label: "Run the partition alone", sub: "when one partition is bigger than the budget", x: 316, y: 128, w: 472, icon: "expand" },
      { id: "bypass", label: "One full-table OPTIMIZE", sub: "when the whole table is small", x: 316, y: 200, w: 472, icon: "skip" },
    ],
    edges: [
      { from: "ranked.r", to: "chunk.l", kind: "write" },
      { from: "ranked.r", to: "oversize.l", kind: "write", port: false },
      { from: "ranked.r", to: "bypass.l", kind: "write", port: false },
    ],
  },

  admission: {
    type: "graph",
    w: 860, h: 380,
    title: "A job starts only when the engine has room for it",
    desc: "Step 1: a Worker asks for a job. Step 2: the Governor probes live engine load, Trino system.runtime.queries or Spark job slots. Step 3: with headroom the job is admitted; otherwise it is deferred.",
    legend: [L_WRITE, L_CTL],
    lanes: [
      { id: "probe", label: "Probe live load", x: 16, y: 252, w: 608, h: 112, kind: "write" },
    ],
    nodes: [
      { id: "worker", label: "Worker wants a job", sub: "pull model", x: 16, y: 100, w: 204, icon: "cpu" },
      { id: "governor", label: "Governor", sub: "decides", x: 272, y: 100, w: 168, icon: "gauge", marker: true },
      { id: "admit", label: "Admit", sub: "headroom available", x: 584, y: 36, w: 260, kind: "state", icon: "check", tone: "ok" },
      { id: "defer", label: "Defer", sub: "cooldown, off-peak, no room", x: 584, y: 164, w: 260, kind: "state", icon: "pause", tone: "warn" },
      { id: "trino", label: "Trino", sub: "system.runtime.queries", code: "sub", x: 48, y: 292, w: 252, kind: "engine" },
      { id: "spark", label: "Spark on EMR", sub: "job slots", x: 340, y: 292, w: 252, kind: "engine" },
    ],
    edges: [
      { from: "worker.r", to: "governor.l", label: "ask", kind: "write", step: 1 },
      { from: "governor.b-24", to: "trino.t", label: "probe", lseg: 0, lside: "l", kind: "ctl", step: 2 },
      { from: "governor.b+24", to: "spark.t", kind: "ctl", main: true },
      { from: "governor.r-12", to: "admit.l", via: [[512, 116], [512, 64]], label: "room", lseg: -1, kind: "write", step: 3 },
      { from: "governor.r+12", to: "defer.l", via: [[512, 140], [512, 192]], label: "no room", lseg: -1, kind: "ctl", main: true },
    ],
  },

  renderers: {
    type: "graph",
    w: 844, h: 224,
    title: "One plan, two dialects: only the renderers write SQL",
    desc: "The Planner emits operations and partition predicates, never SQL. The Trino renderer and the Spark renderer each turn that plan into their own dialect.",
    legend: [L_WRITE],
    lanes: [
      { id: "plan", label: "Plan", x: 16, y: 16, w: 248, h: 192, kind: "cryo" },
      { id: "rend", label: "Renderers", x: 304, y: 16, w: 244, h: 192, kind: "cryo" },
      { id: "out", label: "SQL the engine receives", x: 588, y: 16, w: 240, h: 192, kind: "write" },
    ],
    nodes: [
      { id: "planner", label: "Planner", sub: "operations, predicates", x: 32, y: 96, w: 216, icon: "plan", marker: true },
      { id: "tr", label: "Trino renderer", sub: "engines/trino/sql.py", code: "sub", x: 320, y: 56, w: 212, icon: "code" },
      { id: "sr", label: "Spark renderer", sub: "engines/spark/sql.py", code: "sub", x: 320, y: 136, w: 212, icon: "code" },
      { id: "to", label: "EXECUTE optimize", sub: "Trino SQL", code: "label", x: 604, y: 56, w: 208, kind: "engine", icon: "terminal" },
      { id: "so", label: "rewrite_data_files", sub: "Spark SQL", code: "label", x: 604, y: 136, w: 208, kind: "engine", icon: "terminal" },
    ],
    edges: [
      { from: "planner.r", to: "tr.l", kind: "write" },
      { from: "planner.r", to: "sr.l", kind: "write", port: false },
      { from: "tr.r", to: "to.l", label: "render", kind: "write" },
      { from: "sr.r", to: "so.l", label: "render", kind: "write" },
    ],
  },

  projection: {
    type: "graph",
    w: 820, h: 216,
    title: "Cost is estimated from scan stats, without reading object storage",
    desc: "The Scanner feeds the cost estimator, which writes a stored projection. Object storage is not an input to the projection.",
    legend: [L_WRITE],
    nodes: [
      { id: "scanner", label: "Scanner", sub: "metadata only", x: 16, y: 24, w: 212, icon: "scan" },
      { id: "est", label: "Cost estimator", sub: "classifies bytes", x: 304, y: 24, w: 212, icon: "calc", marker: true },
      { id: "proj", label: "Stored projection", sub: "PostgreSQL", x: 592, y: 24, w: 212, kind: "store" },
      { id: "obj", label: "Object storage", sub: "not an input", x: 304, y: 144, w: 212, h: 52, kind: "ghost" },
    ],
    edges: [
      { from: "scanner.r", to: "est.l", label: "stats", kind: "write", step: 1 },
      { from: "est.r", to: "proj.l", label: "store", kind: "write", step: 2 },
      { from: "obj.t", to: "est.b", kind: "no" },
    ],
  },

  k8s: {
    type: "graph",
    w: 856, h: 456,
    title: "Helm installs Cryo; PostgreSQL, Redis and the engines stay yours",
    desc: "The Helm release runs the API, Web, Scheduler, Governor, Worker and a Scanner CronJob. PostgreSQL and Redis are required but live outside the chart. Trino and Spark stay outside the chart too.",
    legend: [L_WRITE],
    lanes: [
      { id: "rel", label: "Cryo Helm release (deploy/helm/cryo)", x: 16, y: 16, w: 824, h: 256, kind: "cryo" },
      { id: "stores", label: "Required stores (bring your own)", x: 16, y: 328, w: 400, h: 112, kind: "plain" },
      { id: "engines", label: "Engines you already run", x: 440, y: 328, w: 400, h: 112, kind: "write" },
    ],
    nodes: [
      { id: "api", label: "API", sub: "FastAPI", x: 32, y: 56, w: 388, icon: "braces" },
      { id: "web", label: "Web", sub: "operator UI", x: 436, y: 56, w: 388, kind: "ui" },
      { id: "scheduler", label: "Scheduler", sub: "leader-elected", x: 32, y: 128, w: 388, icon: "clock" },
      { id: "governor", label: "Governor", sub: "leader-elected, bounds concurrency", x: 436, y: 128, w: 388, icon: "gauge", marker: true },
      { id: "worker", label: "Worker", sub: "scale for throughput, not concurrency", x: 32, y: 200, w: 388, icon: "cpu" },
      { id: "scan", label: "Scanner CronJob", sub: "separate from the scanner workers", x: 436, y: 200, w: 388, icon: "scan" },
      { id: "pg", label: "PostgreSQL", sub: "state and stats", x: 32, y: 368, w: 176, kind: "store" },
      { id: "rd", label: "Redis", sub: "queue and locks", x: 224, y: 368, w: 176, kind: "store", icon: "stack" },
      { id: "tr", label: "Trino", sub: "Trino SQL", x: 456, y: 368, w: 176, kind: "engine" },
      { id: "sp", label: "Spark on EMR", sub: "Spark SQL", x: 648, y: 368, w: 176, kind: "engine" },
    ],
    edges: [
      { from: "rel.b-212", to: "stores.t", label: "state", kind: "write" },
      { from: "rel.b+212", to: "engines.t", label: "execute", kind: "write" },
    ],
  },

  surfaces: {
    type: "graph",
    w: 820, h: 216,
    title: "Everything goes through the API",
    desc: "The Web UI talks only to the API. The API reaches the platform services: Scanner, Scheduler, Governor and Worker.",
    legend: [L_READ],
    lanes: [
      { id: "plat", label: "Platform services", x: 484, y: 16, w: 320, h: 184, kind: "cryo" },
    ],
    nodes: [
      { id: "web", label: "Web UI", sub: "React", x: 16, y: 80, w: 164, kind: "ui" },
      { id: "api", label: "API", sub: "the only entry point", x: 244, y: 80, w: 200, icon: "braces", marker: true },
      { id: "scan", label: "Scanner", x: 500, y: 56, w: 136, icon: "scan" },
      { id: "sch", label: "Scheduler", x: 652, y: 56, w: 136, icon: "clock" },
      { id: "gov", label: "Governor", x: 500, y: 128, w: 136, icon: "gauge" },
      { id: "wrk", label: "Worker", x: 652, y: 128, w: 136, icon: "cpu" },
    ],
    edges: [
      { from: "web.r", to: "api.l", label: "REST", kind: "read" },
      { from: "api.r", to: "plat.l", kind: "read" },
    ],
  },

  local: {
    type: "graph",
    w: 860, h: 228,
    title: "make up runs the whole stack against a demo lake",
    desc: "make up starts the API, Web UI, PostgreSQL, Redis and a demo Trino. You open the Web UI. The Scanner reads the demo catalog.",
    legend: [L_READ],
    lanes: [
      { id: "stack", label: "Local stack (docker compose)", x: 188, y: 16, w: 424, h: 196, kind: "cryo" },
      { id: "cat", label: "Demo lake (read)", x: 652, y: 16, w: 192, h: 196, kind: "read" },
    ],
    nodes: [
      { id: "you", label: "You", sub: "browser", x: 16, y: 68, w: 132, kind: "ui", icon: "user" },
      { id: "web", label: "Web UI", sub: "localhost:5173", code: "sub", x: 204, y: 68, w: 168, kind: "ui", marker: true },
      { id: "api", label: "API", sub: "FastAPI", x: 428, y: 68, w: 168, icon: "braces" },
      { id: "trino", label: "Trino (demo)", sub: "demo engine", x: 204, y: 140, w: 168, kind: "engine" },
      { id: "scanner", label: "Scanner", sub: "reads metadata", x: 428, y: 140, w: 168, icon: "scan" },
      { id: "demo", label: "Demo catalog", sub: "seeded tables", x: 668, y: 140, w: 160, kind: "source", icon: "book" },
    ],
    edges: [
      { from: "you.r", to: "web.l", label: "open", kind: "read" },
      { from: "web.r", to: "api.l", label: "REST", kind: "read" },
      { from: "scanner.r", to: "demo.l", kind: "read" },
    ],
  },

  sequence: {
    type: "sequence",
    w: 960,
    xs: [84, 254, 384, 530, 656, 776, 896],
    headW: 104,
    rowH: 40,
    title: "Follow one scheduled job: enqueue, admit, execute",
    desc: "The Scheduler creates a run and enqueues jobs. The Governor probes live load and admits jobs. A Worker reads the latest scan, asks the Planner for a plan, executes on the destination, and writes the audit.",
    legend: [L_WRITE, L_READ, L_CTL],
    participants: [
      { id: "scheduler", label: "Scheduler", sub: "presets", icon: "clock" },
      { id: "postgres", label: "PostgreSQL", sub: "runs and audit", kind: "store" },
      { id: "redis", label: "Redis", sub: "queue", kind: "store", icon: "stack" },
      { id: "governor", label: "Governor", sub: "admission", icon: "gauge", marker: true },
      { id: "worker", label: "Worker", sub: "one table", icon: "cpu" },
      { id: "planner", label: "Planner", sub: "no SQL", icon: "plan" },
      { id: "engine", label: "Destination", sub: "Trino or EMR", kind: "engine" },
    ],
    bands: [
      { label: "Enqueue", from: 0, to: 2 },
      { label: "Admit", from: 3, to: 5 },
      { label: "Execute", from: 6, to: 11 },
    ],
    messages: [
      { from: "scheduler", to: "postgres", label: "find due presets", alt: "find due presets and resolve tables from rules and assignments", kind: "read" },
      { from: "scheduler", to: "postgres", label: "create run and jobs", alt: "create the run and its jobs as queued", kind: "write" },
      { from: "scheduler", to: "redis", label: "enqueue jobs", kind: "write" },
      { from: "governor", to: "engine", label: "probe live load", alt: "probe live load: runtime.queries for Trino, job slots for EMR", kind: "ctl" },
      { from: "governor", to: "redis", label: "free slots", alt: "compute free slots from caps, off-peak window, budget and cooldown", kind: "ctl" },
      { from: "governor", to: "worker", label: "admit job", alt: "admit and dispatch the job", kind: "write" },
      { from: "worker", to: "postgres", label: "read latest scan", alt: "read the latest scan and partitions", kind: "read" },
      { from: "worker", to: "planner", label: "build plan", alt: "build the MaintenancePlan from mode and thresholds", kind: "write" },
      { from: "worker", to: "engine", label: "render SQL, execute", alt: "render dialect SQL and execute it, partition-filtered", kind: "write" },
      { from: "worker", to: "postgres", label: "write audit and result", kind: "write" },
      { from: "worker", to: "redis", label: "update live state", alt: "update live counters and state", kind: "ctl" },
      { from: "governor", to: "postgres", label: "finalize run", alt: "finalize the run summary as ok, failed or deferred", kind: "read" },
    ],
  },
};

export function renderDiagram(name) {
  const spec = diagrams[name];
  if (!spec) throw new Error(`unknown diagram "${name}"`);
  const id = `dgm-${name}`;
  // Overflow means a label is wider than the box that holds it, so the text is clipped or
  // spills over a neighbour. Collect every problem and fail the build, rather than
  // printing a warning nobody reads (#225).
  const problems = [];
  const warn = (msg) => {
    problems.push(msg);
    console.warn(`diagram ${name}: ${msg}`);
  };
  const out = spec.type === "sequence" ? renderSequence(id, spec, warn) : renderGraph(id, spec, warn);
  if (problems.length) {
    throw new Error(
      `diagram "${name}" has ${problems.length} layout problem(s):\n` +
        problems.map((p) => `  - ${p}`).join("\n") +
        "\nWiden the node or shorten the label, then rebuild.",
    );
  }
  return { ...out, title: spec.title, desc: spec.desc };
}
