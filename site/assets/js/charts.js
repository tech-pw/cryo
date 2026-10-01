/* Cryo charts — brand-palette SVG primitives for inline data viz.
 *
 * JSON spec is read from <script type="application/json"> inside each
 * <figure data-chart="kind">. Each primitive accepts a JSON spec and
 * appends inline SVG to the figure, plus a caption and source line.
 *
 * Kinds (data-chart values):
 *   rail          — horizontal numbered steps with focus step highlighted
 *   bars          — horizontal bars with optional focus
 *   bands         — A/B/C grade blocks
 *   figure        — grouped nodes with edges (the old "flow" diagram)
 *   matrix        — boolean matrix (feature presence)
 *   gauge         — semi-circle gauge with value
 *   funnel        — horizontal funnel
 *   sparkline     — small trend line
 *   receipt       — vertical receipt with label / value pairs
 *   timeline      — event axis with ticks and labels (NEW)
 *   donut         — donut showing parts of a whole (NEW)
 *   waterfall     — cumulative bar chart with deltas (NEW)
 *   compare       — paired bars with delta arrow between (NEW)
 *   dot-matrix    — graded comparison with colour-graded dots (NEW)
 *   treemap       — nested tile sizes with optional label (NEW)
 *   core          — small standalone Core marker (loose -> solid) (NEW)
 *   layer-rule    — three thin parallel firn lines as a section divider (NEW)
 *   progress-rail — vertical progress rail with section labels (NEW)
 *
 * Palette is locked to brand tokens defined in site.css. No new colours
 * are introduced here; the brand guide §4 is the single source of truth.
 */
(function () {
  const ice = "#143E60";
  const slate = "#5C667A";
  const frost = "#EAF7FD";
  const firn = "#B8E1F3";
  const core = "#277ABE";
  const snow = "#F5FAFD";
  const white = "#FFFFFF";
  const marker = "#EA580C";
  const glacier = "#68C1EA";
  const fills = { cryo: core, source: frost, engine: white, store: white, muted: snow };
  const strokes = { cryo: ice, source: core, engine: ice, store: slate, muted: firn };
  const labelFill = { cryo: white, source: ice, engine: ice, store: ice, muted: ice };
  let markerSeq = 0;

  function el(name, attrs, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text != null) node.textContent = text;
    return node;
  }

  function specOf(node) {
    const raw = node.querySelector('script[type="application/json"]');
    if (!raw) return null;
    try { return JSON.parse(raw.textContent); }
    catch (_) { return null; }
  }

  function caption(node, spec) {
    if (spec.caption) {
      const cap = document.createElement("figcaption");
      cap.textContent = spec.caption;
      node.appendChild(cap);
    }
    if (spec.source) {
      const src = document.createElement("p");
      src.className = "source";
      src.textContent = spec.source;
      node.appendChild(src);
    }
  }

  function arrowDefs(svg, color) {
    const id = `arrow-${markerSeq += 1}`;
    const defs = el("defs");
    const mark = el("marker", { id, markerWidth: 8, markerHeight: 8, refX: 6, refY: 3, orient: "auto" });
    mark.appendChild(el("path", { d: "M0 0 L6 3 L0 6 Z", fill: color || slate }));
    defs.appendChild(mark);
    svg.appendChild(defs);
    return id;
  }

  /* ---------- primitives ---------- */

  function rail(node, spec) {
    const steps = spec.steps || [];
    // Tighter chip width math: 8.4 px per char + 18 px padding, with a floor of 110 and ceiling of 200.
    // This keeps 7 long operation names fitting in a 720-px-wide .viz--wide.
    const widths = steps.map((step) => {
      const len = (step.label || "").length;
      return Math.min(200, Math.max(110, Math.round(len * 8.4) + 22));
    });
    const width = 24 + widths.reduce((sum, item) => sum + item + 18, 0);
    const svg = el("svg", { viewBox: `0 0 ${width} 120`, role: "img" });
    const arrow = arrowDefs(svg);
    svg.appendChild(el("title", {}, spec.title || "Steps"));
    let x = 16;
    steps.forEach((step, index) => {
      const w = widths[index];
      const focus = step.id === spec.focus;
      svg.appendChild(el("rect", {
        x, y: 28, width: w, height: 56, rx: 8, fill: step.color || core,
        stroke: focus ? marker : ice, "stroke-width": focus ? 3 : 1.5,
      }));
      svg.appendChild(el("text", {
        x: x + 10, y: 52, fill: white,
        "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 12,
      }, step.label));
      svg.appendChild(el("text", {
        x: x + 10, y: 70, fill: frost,
        "font-family": "IBM Plex Sans, sans-serif", "font-size": 12,
      }, String(index + 1)));
      if (index < steps.length - 1) {
        svg.appendChild(el("path", {
          d: `M${x + w + 2} 56 H${x + w + 16}`,
          fill: "none", stroke: slate, "stroke-width": 1.5, "marker-end": `url(#${arrow})`,
        }));
      }
      x += w + 18;
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  function bars(node, spec) {
    const series = spec.series || [];
    const max = Math.max(...series.map((item) => Number(item.value) || 0), 1);
    const width = 720;
    const height = 24 + series.length * 36;
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Chart"));
    series.forEach((item, index) => {
      const y = 12 + index * 36;
      const w = ((Number(item.value) || 0) / max) * 340;
      svg.appendChild(el("text", {
        x: 8, y: y + 16, fill: ice, "font-family": "IBM Plex Sans, sans-serif", "font-size": 13,
      }, item.label));
      svg.appendChild(el("rect", {
        x: 150, y, width: Math.max(w, 2), height: 22, rx: 4,
        fill: item.tone === "frost" ? frost : item.tone === "firn" ? firn : core,
        stroke: item.focus ? marker : ice, "stroke-width": item.focus ? 3 : 1,
      }));
      svg.appendChild(el("text", {
        x: 160 + w, y: y + 16, fill: ice, "font-family": "IBM Plex Sans, sans-serif", "font-size": 13,
      }, `${item.value}${spec.unit || ""}`));
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  function bands(node, spec) {
    const items = spec.bands || [];
    const width = Math.max(640, items.length * 120);
    const svg = el("svg", { viewBox: `0 0 ${width} 110`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Bands"));
    const w = (width - 32) / Math.max(items.length, 1);
    items.forEach((item, index) => {
      const x = 16 + index * w;
      const focus = item.focus || (index === 0 && spec.focus === item.label);
      svg.appendChild(el("rect", {
        x, y: 24, width: w - 8, height: 48, rx: 8,
        fill: item.tone === "core" ? core : item.tone === "firn" ? firn : frost,
        stroke: focus ? marker : ice,
        "stroke-width": focus ? 3 : 1.5,
      }));
      svg.appendChild(el("text", {
        x: x + 12, y: 54, fill: item.tone === "core" ? white : ice,
        "font-family": "IBM Plex Sans, sans-serif", "font-size": 18, "font-weight": 600,
      }, item.label));
      if (item.count != null) {
        svg.appendChild(el("text", {
          x: x + 12, y: 44, fill: item.tone === "core" ? frost : slate,
          "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11,
        }, String(item.count)));
      }
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  function figure(node, spec) {
    const groups = spec.groups || [];
    const gap = 64;
    const groupW = 196;
    const nodeH = 36;
    const top = 40;
    const maxNodes = Math.max(...groups.map((group) => (group.nodes || []).length), 1);
    const height = top + maxNodes * (nodeH + 10) + 20;
    const width = 20 + groups.length * (groupW + gap);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    const arrow = arrowDefs(svg);
    svg.appendChild(el("title", {}, spec.title || "Diagram"));
    const centers = {};
    const edges = spec.edges || [];
    groups.forEach((group, index) => {
      const x = 12 + index * (groupW + gap);
      const nodes = group.nodes || [];
      svg.appendChild(el("rect", {
        x, y: 8, width: groupW, height: 32 + nodes.length * (nodeH + 10), rx: 10, fill: snow, stroke: firn,
      }));
      svg.appendChild(el("text", {
        x: x + 12, y: 26, fill: slate, "font-family": "IBM Plex Sans, sans-serif", "font-size": 12,
      }, group.label));
      nodes.forEach((item, nodeIndex) => {
        const y = top + nodeIndex * (nodeH + 10);
        const kind = item.kind || "cryo";
        const focus = item.id === spec.focus;
        const nx = x + 12;
        const nw = groupW - 24;
        const stroke = focus ? marker : strokes[kind] || ice;
        const widthStroke = focus ? 3 : 1.5;
        if (kind === "store") {
          svg.appendChild(el("rect", {
            x: nx, y: y + 6, width: nw, height: 24, fill: white, stroke, "stroke-width": widthStroke,
          }));
          svg.appendChild(el("ellipse", {
            cx: nx + nw / 2, cy: y + 6, rx: nw / 2, ry: 7, fill: white, stroke, "stroke-width": widthStroke,
          }));
        } else {
          svg.appendChild(el("rect", {
            x: nx, y, width: nw, height: nodeH, rx: 6,
            fill: fills[kind] || frost, stroke, "stroke-width": widthStroke,
          }));
        }
        svg.appendChild(el("text", {
          x: nx + 8, y: y + 23, fill: labelFill[kind] || ice,
          "font-family": "IBM Plex Sans, sans-serif", "font-size": 13,
        }, item.label));
        centers[item.id] = { right: nx + nw, left: nx, mid: y + nodeH / 2 };
      });
    });
    const seen = {};
    edges.forEach((edge) => {
      const from = centers[edge.from];
      const to = centers[edge.to];
      if (!from || !to) return;
      const key = `${Math.round(from.right)}-${Math.round(to.left)}`;
      const slot = seen[key] || 0;
      seen[key] = slot + 1;
      const y2 = to.mid + (slot - 0.5) * 8;
      const x1 = from.right + 2;
      const x2 = to.left - 8;
      const mid = x1 + Math.max(18, (x2 - x1) / 2);
      const d = x2 > x1 + 8
        ? `M${x1} ${from.mid} H${mid} V${y2} H${x2}`
        : `M${x1} ${from.mid} H${x1 + 16} V${y2} H${x2}`;
      svg.appendChild(el("path", {
        d, fill: "none", stroke: slate, "stroke-width": 1.5,
        "stroke-dasharray": edge.style === "read" ? "5 4" : "none",
        "marker-end": `url(#${arrow})`,
      }));
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  function matrix(node, spec) {
    const wrap = document.createElement("div");
    wrap.className = "matrix";
    const table = document.createElement("table");
    const head = document.createElement("tr");
    head.appendChild(document.createElement("th"));
    (spec.columns || []).forEach((col) => {
      const cell = document.createElement("th");
      cell.textContent = col;
      head.appendChild(cell);
    });
    table.appendChild(head);
    (spec.rows || []).forEach((row) => {
      const tr = document.createElement("tr");
      const label = document.createElement("th");
      label.textContent = row.label;
      tr.appendChild(label);
      (row.marks || []).forEach((on) => {
        const td = document.createElement("td");
        const mark = document.createElement("span");
        mark.className = on ? "mark on" : "mark";
        mark.setAttribute("aria-label", on ? "yes" : "no");
        td.appendChild(mark);
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    node.appendChild(wrap);
    caption(node, spec);
  }

  function gauge(node, spec) {
    const value = Math.max(0, Math.min(Number(spec.value) || 0, Number(spec.max) || 100));
    const max = Number(spec.max) || 100;
    const ratio = value / max;
    const radius = 48;
    const cx = 82;
    const cy = 68;
    const start = Math.PI * 0.78;
    const end = Math.PI * 2.22;
    const point = (angle, r) => [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r];
    const arc = (to) => {
      const [sx, sy] = point(start, radius);
      const [ex, ey] = point(to, radius);
      const large = to - start > Math.PI ? 1 : 0;
      return `M${sx} ${sy} A${radius} ${radius} 0 ${large} 1 ${ex} ${ey}`;
    };
    const svg = el("svg", { viewBox: "0 0 240 142", role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Gauge"));
    svg.appendChild(el("path", { d: arc(end), fill: "none", stroke: firn, "stroke-width": 12, "stroke-linecap": "round" }));
    svg.appendChild(el("path", { d: arc(start + (end - start) * ratio), fill: "none", stroke: spec.tone === "slate" ? slate : core, "stroke-width": 12, "stroke-linecap": "round" }));
    svg.appendChild(el("text", { x: cx, y: 72, fill: ice, "font-family": "IBM Plex Sans, sans-serif", "font-size": 28, "font-weight": 600, "text-anchor": "middle" }, spec.label || `${Math.round(ratio * 100)}%`));
    svg.appendChild(el("text", { x: cx, y: 94, fill: slate, "font-family": "IBM Plex Sans, sans-serif", "font-size": 11, "text-anchor": "middle" }, spec.detail || `${value}/${max}`));
    svg.appendChild(el("text", { x: 154, y: 53, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11 }, `${value}/${max}`));
    if (spec.thresholds) {
      const tx = 154;
      const ty = 65;
      svg.appendChild(el("text", { x: tx, y: ty, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 10 }, spec.thresholds));
    }
    node.appendChild(svg);
    caption(node, spec);
  }

  function funnel(node, spec) {
    const steps = spec.steps || [];
    const max = Math.max(...steps.map((step) => Number(step.value) || 0), 1);
    const svg = el("svg", { viewBox: `0 0 520 ${Math.max(120, steps.length * 34 + 20)}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Funnel"));
    steps.forEach((step, index) => {
      const y = 10 + index * 34;
      const width = ((Number(step.value) || 0) / max) * 300;
      const x = 150 + (300 - width) / 2;
      svg.appendChild(el("text", { x: 8, y: y + 17, fill: ice, "font-family": "IBM Plex Sans, sans-serif", "font-size": 12 }, step.label));
      svg.appendChild(el("rect", { x, y, width: Math.max(width, 4), height: 22, rx: 4, fill: index === steps.length - 1 ? core : index === 0 ? frost : firn, stroke: ice, "stroke-width": 1 }));
      svg.appendChild(el("text", { x: 470, y: y + 17, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11, "text-anchor": "end" }, String(step.value)));
      if (step.detail) {
        svg.appendChild(el("text", { x: 470, y: y + 28, fill: slate, "font-family": "IBM Plex Sans, sans-serif", "font-size": 10, "text-anchor": "end" }, step.detail));
      }
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  function sparkline(node, spec) {
    const values = (spec.values || []).map((value) => Number(value) || 0);
    if (!values.length) return;
    const max = Math.max(...values, 1);
    const width = 520;
    const height = 132;
    const pad = 12;
    const points = values.map((value, index) => {
      const x = pad + (index * (width - pad * 2)) / Math.max(values.length - 1, 1);
      const y = height - pad - (value / max) * (height - pad * 2);
      return [x, y];
    });
    const d = points.map(([x, y], index) => `${index ? "L" : "M"}${x} ${y}`).join(" ");
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Trend"));
    svg.appendChild(el("line", { x1: pad, y1: height - pad, x2: width - pad, y2: height - pad, stroke: firn, "stroke-width": 1 }));
    if (spec.bands) {
      const gridY = [0.25, 0.5, 0.75].map((f) => height - pad - f * (height - pad * 2));
      gridY.forEach((y) => svg.appendChild(el("line", { x1: pad, y1: y, x2: width - pad, y2: y, stroke: snow, "stroke-width": 1 })));
    }
    svg.appendChild(el("path", { d, fill: "none", stroke: core, "stroke-width": 3, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    points.forEach(([x, y]) => svg.appendChild(el("circle", { cx: x, cy: y, r: 3.5, fill: white, stroke: core, "stroke-width": 2 })));
    svg.appendChild(el("text", { x: width - pad, y: 20, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11, "text-anchor": "end" }, spec.label || "trend"));
    if (spec.latest != null) {
      svg.appendChild(el("text", { x: pad, y: 20, fill: ice, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11, "font-weight": 600 }, spec.latest));
    }
    node.appendChild(svg);
    caption(node, spec);
  }

  function receipt(node, spec) {
    const items = spec.items || [];
    const svg = el("svg", { viewBox: `0 0 520 ${Math.max(120, items.length * 34 + 16)}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Receipt"));
    items.forEach((item, index) => {
      const y = 8 + index * 34;
      svg.appendChild(el("circle", { cx: 16, cy: y + 13, r: 5, fill: index === items.length - 1 ? core : frost, stroke: ice, "stroke-width": 1.5 }));
      svg.appendChild(el("text", { x: 32, y: y + 17, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 12 }, item.label));
      svg.appendChild(el("text", { x: 188, y: y + 17, fill: ice, "font-family": "IBM Plex Sans, sans-serif", "font-size": 13, "font-weight": 600 }, item.value));
      if (index < items.length - 1) svg.appendChild(el("line", { x1: 16, y1: y + 19, x2: 16, y2: y + 34, stroke: firn, "stroke-width": 1.5 }));
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  /* ---------- NEW primitives (Phase 2) ---------- */

  // timeline: horizontal event axis with tick marks and labels.
  // spec = { title, events: [{ at, label, detail, tone }], caption, source, compact }
  function timeline(node, spec) {
    const events = spec.events || [];
    if (!events.length) return;
    const compact = !!spec.compact;
    const width = compact ? 720 : 880;
    const height = compact ? 96 : 110;
    const padX = compact ? 28 : 32;
    const padTop = compact ? 26 : 30;
    const axisY = height - compact ? 30 : 36;
    const min = Math.min(...events.map((e) => e.at));
    const max = Math.max(...events.map((e) => e.at));
    const span = max - min || 1;
    const xOf = (at) => padX + ((at - min) / span) * (width - padX * 2);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Timeline"));
    svg.appendChild(el("line", { x1: padX, y1: axisY, x2: width - padX, y2: axisY, stroke: firn, "stroke-width": 1.5 }));
    // Stagger labels so they don't collide
    const placed = [];
    events.forEach((event, index) => {
      const x = xOf(event.at);
      const tone = event.tone || "core";
      const fill = tone === "marker" ? marker : tone === "firn" ? firn : tone === "frost" ? frost : core;
      svg.appendChild(el("circle", { cx: x, cy: axisY, r: compact ? 5 : 7, fill, stroke: ice, "stroke-width": 1.5 }));
      svg.appendChild(el("line", { x1: x, y1: axisY - (compact ? 5 : 7), x2: x, y2: padTop + 6, stroke: firn, "stroke-width": 1, "stroke-dasharray": "3 3" }));
      svg.appendChild(el("text", {
        x, y: padTop - (compact ? 4 : 4), fill: ice, "font-family": "IBM Plex Mono, ui-monospace, monospace",
        "font-size": compact ? 10 : 11, "text-anchor": "middle",
      }, event.atLabel || String(event.at)));
      // Stagger label baseline so close events don't collide
      let labelY = axisY + (compact ? 18 : 24);
      for (const px of placed) {
        if (Math.abs(px - x) < 50) labelY += compact ? 12 : 14;
      }
      placed.push(x);
      svg.appendChild(el("text", {
        x, y: labelY, fill: ice, "font-family": "IBM Plex Sans, sans-serif",
        "font-size": compact ? 11 : 12, "font-weight": 600, "text-anchor": "middle",
      }, event.label));
      if (event.detail && !compact) {
        svg.appendChild(el("text", {
          x, y: labelY + 14, fill: slate, "font-family": "IBM Plex Sans, sans-serif",
          "font-size": 11, "text-anchor": "middle",
        }, event.detail));
      }
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  // donut: parts-of-whole donut with optional centre label.
  // spec = { title, parts: [{ label, value, tone }], centre, unit, caption, source, compact }
  function donut(node, spec) {
    const parts = spec.parts || [];
    const total = parts.reduce((sum, item) => sum + (Number(item.value) || 0), 0) || 1;
    const compact = !!spec.compact;
    const cx = compact ? 80 : 110, cy = compact ? 80 : 110;
    const rOuter = compact ? 64 : 90, rInner = compact ? 42 : 60;
    const width = compact ? 320 : 520, height = compact ? 160 : 220;
    const lx = compact ? 160 : 240;
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Donut"));
    let angle = -Math.PI / 2;
    const arc = (from, to) => {
      const large = (to - from) > Math.PI ? 1 : 0;
      const x1 = cx + Math.cos(from) * rOuter;
      const y1 = cy + Math.sin(from) * rOuter;
      const x2 = cx + Math.cos(to) * rOuter;
      const y2 = cy + Math.sin(to) * rOuter;
      const x3 = cx + Math.cos(to) * rInner;
      const y3 = cy + Math.sin(to) * rInner;
      const x4 = cx + Math.cos(from) * rInner;
      const y4 = cy + Math.sin(from) * rInner;
      return `M${x1} ${y1} A${rOuter} ${rOuter} 0 ${large} 1 ${x2} ${y2} L${x3} ${y3} A${rInner} ${rInner} 0 ${large} 0 ${x4} ${y4} Z`;
    };
    parts.forEach((part) => {
      const sweep = (Number(part.value) || 0) / total * Math.PI * 2;
      const tone = part.tone || "core";
      const fill = tone === "firn" ? firn : tone === "frost" ? frost : tone === "ice" ? ice : core;
      if (sweep > 0) svg.appendChild(el("path", { d: arc(angle, angle + sweep), fill, stroke: white, "stroke-width": 1.5 }));
      angle += sweep;
    });
    if (spec.centre) {
      svg.appendChild(el("text", {
        x: cx, y: cy - (compact ? 2 : 4),
        fill: ice, "font-family": "IBM Plex Sans, sans-serif",
        "font-size": compact ? 18 : 24, "font-weight": 600, "text-anchor": "middle",
      }, spec.centre));
      if (spec.centreSub) {
        svg.appendChild(el("text", {
          x: cx, y: cy + (compact ? 14 : 16),
          fill: slate, "font-family": "IBM Plex Sans, sans-serif",
          "font-size": compact ? 9 : 11, "text-anchor": "middle",
        }, spec.centreSub));
      }
    }
    // legend on the right
    let ly = compact ? 22 : 36;
    const rowH = compact ? 18 : 24;
    parts.forEach((part) => {
      const tone = part.tone || "core";
      const fill = tone === "firn" ? firn : tone === "frost" ? frost : tone === "ice" ? ice : core;
      svg.appendChild(el("rect", { x: lx, y: ly, width: compact ? 10 : 12, height: compact ? 10 : 12, rx: 2, fill, stroke: ice, "stroke-width": 1 }));
      svg.appendChild(el("text", {
        x: lx + 18, y: ly + (compact ? 9 : 10),
        fill: ice, "font-family": "IBM Plex Sans, sans-serif",
        "font-size": compact ? 11 : 13,
      }, part.label));
      svg.appendChild(el("text", {
        x: width - 16, y: ly + (compact ? 9 : 10),
        fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace",
        "font-size": compact ? 10 : 12, "text-anchor": "end",
      }, `${part.value}${part.unit || ""}`));
      ly += rowH;
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  // waterfall: cumulative bar chart with positive/negative deltas.
  // spec = { title, items: [{ label, value, type }], unit, caption, source, compact }
  function waterfall(node, spec) {
    const items = spec.items || [];
    if (!items.length) return;
    const compact = !!spec.compact;
    const width = compact ? 540 : 720;
    const barH = compact ? 18 : 22;
    const gap = compact ? 9 : 12;
    const padTop = compact ? 22 : 28;
    const height = padTop + items.length * (barH + gap) + 24;
    // Use a per-bar absolute-value scale so small positives are still visible.
    const max = Math.max(...items.map((i) => Math.abs(Number(i.value) || 0)), 1);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Waterfall"));
    let cumulative = spec.start != null ? Number(spec.start) : 0;
    const xStart = compact ? 180 : 220;
    const maxBar = compact ? 220 : 260;
    items.forEach((item, index) => {
      const y = padTop + index * (barH + gap);
      const v = Number(item.value) || 0;
      const isNeg = v < 0;
      const isTotal = item.type === "total";
      // Width has a floor so even tiny values show as a visible chip.
      const w = Math.max(Math.abs(v) / max * maxBar, isTotal ? 60 : 4);
      const start = isTotal
        ? xStart
        : isNeg ? xStart + (Math.max(0, cumulative + v) / max) * maxBar : xStart + (cumulative / max) * maxBar;
      const fill = isTotal ? core : isNeg ? firn : core;
      const textFill = isTotal ? white : ice;
      svg.appendChild(el("text", {
        x: 12, y: y + barH - (compact ? 6 : 6), fill: ice,
        "font-family": "IBM Plex Sans, sans-serif", "font-size": compact ? 11 : 13,
      }, item.label));
      svg.appendChild(el("rect", {
        x: start, y, width: w, height: barH, rx: 4,
        fill, stroke: ice, "stroke-width": 1,
      }));
      svg.appendChild(el("text", {
        x: start + w + 8, y: y + barH - 6, fill: slate,
        "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": compact ? 10 : 12,
      }, `${isNeg ? "" : (isTotal ? "→ " : "+")}${v}${spec.unit || ""}`));
      if (!isTotal) cumulative += v;
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  // compare: two paired bars with delta arrow showing before/after or A/B.
  // spec = { title, pairs: [{ label, before, after, unit, tone }], caption, source }
  function compare(node, spec) {
    const pairs = spec.pairs || [];
    if (!pairs.length) return;
    const width = 720;
    const height = 24 + pairs.length * 78;
    const max = Math.max(...pairs.flatMap((p) => [p.before, p.after].map((v) => Math.abs(Number(v) || 0))), 1);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Compare"));
    const arrow = arrowDefs(svg);
    pairs.forEach((pair, index) => {
      const y = 12 + index * 78;
      const yMid = y + 28;
      const wb = (Math.abs(Number(pair.before) || 0) / max) * 280;
      const wa = (Math.abs(Number(pair.after) || 0) / max) * 280;
      svg.appendChild(el("text", {
        x: 12, y: y + 18, fill: ice, "font-family": "IBM Plex Sans, sans-serif", "font-size": 13, "font-weight": 600,
      }, pair.label));
      // before
      svg.appendChild(el("rect", { x: 220, y: yMid - 18, width: Math.max(wb, 2), height: 16, rx: 3, fill: firn, stroke: ice, "stroke-width": 1 }));
      svg.appendChild(el("text", { x: 226 + wb, y: yMid - 5, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11 }, `${pair.before}${pair.unit || ""}`));
      // after
      svg.appendChild(el("rect", { x: 220, y: yMid + 4, width: Math.max(wa, 2), height: 16, rx: 3, fill: core, stroke: ice, "stroke-width": 1 }));
      svg.appendChild(el("text", { x: 226 + wa, y: yMid + 17, fill: ice, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11, "font-weight": 600 }, `${pair.after}${pair.unit || ""}`));
      // arrow + delta
      svg.appendChild(el("text", { x: 12, y: yMid + 4, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 10 }, "before → after"));
      if (pair.delta != null) {
        const dx = pair.delta > 0 ? "+" : "";
        svg.appendChild(el("text", {
          x: width - 14, y: y + 18, fill: pair.delta < 0 ? marker : slate,
          "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 13, "font-weight": 600, "text-anchor": "end",
        }, `${dx}${pair.delta}${pair.unit || ""}`));
      }
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  // dot-matrix: graded comparison matrix where each cell is a colour-graded dot.
  // spec = { title, columns, rows: [{ label, marks: [0..3] }], legend, caption, source }
  function dotMatrix(node, spec) {
    const columns = spec.columns || [];
    const rows = spec.rows || [];
    const legend = spec.legend || ["no", "partial", "yes", "strong"];
    const wrap = document.createElement("div");
    wrap.className = "matrix dot-matrix";
    const table = document.createElement("table");
    const head = document.createElement("tr");
    head.appendChild(document.createElement("th"));
    columns.forEach((col) => {
      const cell = document.createElement("th");
      cell.textContent = col;
      head.appendChild(cell);
    });
    table.appendChild(head);
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      const label = document.createElement("th");
      label.textContent = row.label;
      tr.appendChild(label);
      (row.marks || []).forEach((mark) => {
        const td = document.createElement("td");
        const dot = document.createElement("span");
        dot.className = `dot-cell grade-${mark}`;
        const labels = ["no", "partial", "yes", "strong"];
        dot.setAttribute("aria-label", labels[mark] || "—");
        td.appendChild(dot);
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    if (legend.length) {
      const legendEl = document.createElement("div");
      legendEl.className = "dot-matrix-legend";
      legend.forEach((item, i) => {
        const span = document.createElement("span");
        const dot = document.createElement("span");
        dot.className = `dot-cell grade-${i}`;
        span.appendChild(dot);
        const text = document.createElement("span");
        text.textContent = item;
        span.appendChild(text);
        legendEl.appendChild(span);
      });
      wrap.appendChild(legendEl);
    }
    node.appendChild(wrap);
    caption(node, spec);
  }

  // treemap: nested tiles sized by value, simple squarified layout.
  // spec = { title, items: [{ label, value, tone }], caption, source, unit }
  function treemap(node, spec) {
    const items = (spec.items || []).slice().sort((a, b) => (b.value || 0) - (a.value || 0));
    if (!items.length) return;
    const total = items.reduce((sum, item) => sum + (Number(item.value) || 0), 0) || 1;
    const width = 720;
    const height = 280;
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Treemap"));
    // Simple recursive slice — split horizontally then vertically.
    let rects = [{ x: 8, y: 8, w: width - 16, h: height - 16 }];
    items.forEach((item) => {
      const ratio = (Number(item.value) || 0) / total;
      const target = rects.shift();
      if (!target) return;
      const horizontal = target.w >= target.h;
      const size = horizontal ? target.w * ratio : target.h * ratio;
      const tone = item.tone || "core";
      const fill = tone === "firn" ? firn : tone === "frost" ? frost : tone === "ice" ? ice : core;
      const textFill = tone === "core" || tone === "ice" ? white : ice;
      const tileWidth = Math.max(size - 4, 2);
      svg.appendChild(el("rect", {
        x: target.x, y: target.y, width: tileWidth, height: target.h, rx: 6,
        fill, stroke: white, "stroke-width": 2,
      }));
      // A narrow slice (common once 5+ items share the width) can't fit a 13px label plus
      // its value line without overflowing into the next tile -- omit both rather than
      // render text that visually collides with a neighbour.
      if (tileWidth >= 64 && target.h >= 40) {
        svg.appendChild(el("text", {
          x: target.x + 12, y: target.y + 22, fill: textFill,
          "font-family": "IBM Plex Sans, sans-serif", "font-size": 13, "font-weight": 600,
        }, item.label));
        svg.appendChild(el("text", {
          x: target.x + 12, y: target.y + 38, fill: textFill,
          "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11,
        }, `${item.value}${spec.unit || ""}`));
      } else {
        svg.appendChild(el("title", {}, `${item.label}: ${item.value}${spec.unit || ""}`));
      }
      const rest = horizontal
        ? { x: target.x + size, y: target.y, w: target.w - size, h: target.h }
        : { x: target.x, y: target.y + size, w: target.w, h: target.h - size };
      if (rest.w > 4 && rest.h > 4) rects.unshift(rest);
    });
    node.appendChild(svg);
    caption(node, spec);
  }

  // core: small standalone Core marker (loose -> solid) with one marker flag.
  // spec = { title, label, caption, source }
  function coreMark(node, spec) {
    const w = 240, h = 80;
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
    svg.appendChild(el("title", {}, spec.title || "Core"));
    // ground line
    svg.appendChild(el("line", { x1: 8, y1: h - 6, x2: w - 8, y2: h - 6, stroke: slate, "stroke-width": 1 }));
    // core body (cylindrical)
    svg.appendChild(el("rect", { x: 24, y: 18, width: w - 48, height: 44, rx: 22, fill: frost, stroke: ice, "stroke-width": 1.5 }));
    // loose squares on left
    const loose = [
      [38, 28, 8], [50, 36, 8], [38, 44, 8], [50, 28, 8], [62, 36, 8], [38, 52, 6],
      [50, 50, 6], [62, 48, 6], [74, 30, 6], [74, 44, 6],
    ];
    loose.forEach(([x, y, s]) => {
      svg.appendChild(el("rect", { x, y, width: s, height: s, rx: 1, fill: frost, stroke: ice, "stroke-width": 0.8 }));
    });
    // packed rectangles
    [
      [88, 24, 22, 32], [114, 24, 22, 32], [140, 24, 22, 32],
    ].forEach(([x, y, ww, hh]) => svg.appendChild(el("rect", { x, y, width: ww, height: hh, rx: 2, fill: firn, stroke: ice, "stroke-width": 0.8 })));
    // solid blocks
    svg.appendChild(el("rect", { x: 168, y: 22, width: w - 192, height: 36, rx: 3, fill: core, stroke: ice, "stroke-width": 1.2 }));
    svg.appendChild(el("rect", { x: 168, y: 24, width: w - 196, height: 1.5, fill: frost }));
    // layer lines across the full length
    [38, 52].forEach((yy) => svg.appendChild(el("line", { x1: 30, y1: yy, x2: w - 30, y2: yy, stroke: firn, "stroke-width": 0.8, "stroke-dasharray": "4 4" })));
    // marker flag on the solid block
    svg.appendChild(el("line", { x1: w - 60, y1: 22, x2: w - 60, y2: 4, stroke: marker, "stroke-width": 1.5 }));
    svg.appendChild(el("path", { d: `M${w - 60} 4 L${w - 46} 9 L${w - 60} 14 Z`, fill: marker }));
    if (spec.label) {
      svg.appendChild(el("text", {
        x: 12, y: 76, fill: slate, "font-family": "IBM Plex Mono, ui-monospace, monospace",
        "font-size": 10, "text-anchor": "start",
      }, spec.label));
    }
    node.appendChild(svg);
    caption(node, spec);
  }

  // layer-rule: three thin parallel firn lines (brand §10 section divider).
  // spec = { gap?, length?, tone? }
  function layerRule(node, spec) {
    const wrap = document.createElement("div");
    wrap.className = `layer-rule ${spec.tone === "deep" ? "layer-rule--deep" : ""}`;
    node.appendChild(wrap);
    if (spec.caption) caption(node, spec);
  }

  // progress-rail: vertical progress rail with section labels.
  // spec = { title, sections: [{ id, label }], active, caption, source }
  function progressRail(node, spec) {
    const sections = spec.sections || [];
    if (!sections.length) return;
    const svg = el("svg", {
      viewBox: `0 0 220 ${sections.length * 36 + 8}`,
      role: "img", class: "progress-rail-svg",
    });
    svg.appendChild(el("title", {}, spec.title || "On this page"));
    sections.forEach((section, index) => {
      const y = 18 + index * 36;
      const active = section.id === spec.active;
      const passed = sections.findIndex((s) => s.id === spec.active) > index;
      const fill = active ? marker : passed ? core : firn;
      svg.appendChild(el("circle", { cx: 14, cy: y, r: active ? 7 : 5, fill, stroke: white, "stroke-width": 2 }));
      svg.appendChild(el("line", { x1: 14, y1: y + (active ? 7 : 5), x2: 14, y2: y + 36 - 4, stroke: firn, "stroke-width": 1.5 }));
      svg.appendChild(el("text", {
        x: 32, y: y + 5, fill: ice, "font-family": "IBM Plex Sans, sans-serif",
        "font-size": 13, "font-weight": active ? 600 : 500,
      }, section.label));
    });
    node.appendChild(svg);
    if (spec.caption) {
      const cap = document.createElement("p");
      cap.className = "source";
      cap.textContent = spec.caption;
      node.appendChild(cap);
    }
  }

  const kinds = {
    rail, bars, bands, figure, matrix, gauge, funnel, sparkline, receipt,
    timeline, donut, waterfall, compare, "dot-matrix": dotMatrix,
    treemap, core: coreMark, "layer-rule": layerRule, "progress-rail": progressRail,
  };

  function mount(node) {
    if (node.dataset.ready === "1") return;
    const spec = specOf(node);
    const draw = kinds[node.dataset.chart];
    if (!draw) return;
    draw(node, spec || {});
    node.dataset.ready = "1";
  }

  window.CryoChart = {
    mountAll(root) {
      (root || document).querySelectorAll(".viz").forEach(mount);
    },
    mount,
  };
})();
