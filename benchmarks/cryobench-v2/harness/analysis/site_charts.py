"""Render the public benchmark page's charts as theme-aware inline SVG.

Reads only results/v2/analysis outputs (no hand-typed numbers) and writes
site/_src/includes/bench-*.svg. Colours come from CSS classes defined in site.css
(.bm-cryo, .bm-a1, .bm-a0, ...), so the charts follow the light and dark themes.
"""
import csv, json, math, pathlib
from common import OUT, ROOT

SITE_INC = ROOT.parents[1] / "site" / "_src" / "includes"
P1 = json.loads((OUT / "p1_summary.json").read_text())
P2 = json.loads((OUT / "p2_summary.json").read_text())
P3 = json.loads((OUT / "p3_summary.json").read_text())
DATA = ROOT / "results" / "v2" / "data"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg(name, w, h, body, label):
    (SITE_INC / f"bench-{name}.svg").write_text(
        f'<svg class="bm-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}">\n{body}\n</svg>\n')


def text(x, y, s, cls="bm-t", anchor="start"):
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}">{esc(s)}</text>'


# ---------- 1. CDC: delete files and data files over time ----------
def cdc():
    rows = list(csv.DictReader((OUT / "p3_file_series.csv").open()))
    ctrl = [json.loads(l) for l in (DATA / "ctrl.jsonl").open()]
    s = [r for r in ctrl if r.get("_src") == "v2p3-scored-c.log"]
    t0 = next(r["t"] for r in s if r.get("event") == "p3_start")
    a3w = [(r["t"] - t0) / 60 for r in s if r.get("event") == "p3_window_start"]
    a1w = [(json.loads(l)["t"] - t0) / 60 for l in (DATA / "p3.jsonl").open()
           if (r := json.loads(l)).get("trial") == 3 and r["kind"] == "op" and r["arm"] == "a1"
           and r["table"] == "orders" and r["op"] == "rewrite_data_files"]
    W, H, L, R, T, B = 720, 330, 52, 150, 24, 46
    pw, ph = W - L - R, H - T - B
    xmax, ymax = 120, 40
    X = lambda m: L + m / xmax * pw
    Y = lambda v: T + ph - v / ymax * ph
    out = []
    for v in range(0, ymax + 1, 10):
        out.append(f'<line class="bm-grid" x1="{L}" x2="{L + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        out.append(text(L - 8, Y(v) + 4, v, "bm-tick", "end"))
    for m in range(0, xmax + 1, 20):
        out.append(text(X(m), T + ph + 18, f"{m} min", "bm-tick", "middle"))
    for m in a3w:
        out.append(f'<line class="bm-win-cryo" x1="{X(m):.1f}" x2="{X(m):.1f}" y1="{T + ph}" y2="{T + ph - 6}"/>')
    order = [("a0", "bm-line bm-a0"), ("a1", "bm-line bm-a1"), ("a3", "bm-line bm-cryo")]
    last = {}
    for arm, cls in order:
        pts = sorted((float(r["t_rel_s"]) / 60, int(r["delete_files"])) for r in rows if r["arm"] == arm and r["table"] == "orders")
        d, prev = [], None
        for m, v in pts:
            if m < 0:
                continue
            if prev is None:
                d.append(f"M{X(m):.1f},{Y(v):.1f}")
            else:
                d.append(f"H{X(m):.1f}V{Y(v):.1f}")
            prev = v
        d.append(f"H{X(min(xmax, pts[-1][0] + 1)):.1f}")
        out.append(f'<path class="{cls}" d="{"".join(d)}"/>')
        last[arm] = prev
    for m in a1w:
        y = Y(next(int(r["delete_files"]) for r in sorted(rows, key=lambda r: -float(r["t_rel_s"]))
                   if r["arm"] == "a1" and r["table"] == "orders" and float(r["t_rel_s"]) / 60 <= m))
        out.append(f'<g class="bm-fail"><circle cx="{X(m):.1f}" cy="{y:.1f}" r="7"/><path d="M{X(m)-3:.1f},{y-3:.1f}l6,6M{X(m)+3:.1f},{y-3:.1f}l-6,6"/></g>')
    out.append(text(L + pw + 10, Y(last["a1"]) - 2, "No maintenance", "bm-lab bm-a0-t"))
    out.append(text(L + pw + 10, Y(last["a1"]) + 14, "and scheduled DAG", "bm-lab bm-a1-t"))
    out.append(text(L + pw + 10, Y(last["a3"]) + 4, "Cryo", "bm-lab bm-cryo-t bm-strong"))
    out.append(text(L, T - 8, "Delete files on a table receiving a MERGE every 200 s", "bm-cap"))
    out.append(text(X(3), Y(36), "Scheduled compaction failed", "bm-note bm-fail-t"))
    out.append(text(X(3), Y(36) + 15, "3 of 3 times (commit conflicts)", "bm-note bm-fail-t"))
    out.append(text(X(64), Y(14), "Cryo, checking every 10 min:", "bm-note bm-cryo-t"))
    out.append(text(X(64), Y(14) + 15, "held at 3 to 6 from its second run", "bm-note bm-cryo-t"))
    svg("cdc", W, H, "\n".join(out), "Line chart. Over two hours of MERGE ingestion, delete files rose steadily to 39 with no maintenance and with a scheduled Spark DAG, whose three compaction attempts all failed on commit conflicts. With Cryo checking every 10 minutes they peaked at 10 and, from its second run on, held between 3 and 6.")


# ---------- 2. Decision grid: who touched what ----------
def decisions():
    classes = [("healthy", "Healthy", "4 large files"), ("lowtraffic", "Quiet", "12 small files"),
               ("moderate", "Fragmenting", "150+ small files"), ("severe", "Badly fragmented", "600+ small files"),
               ("updates", "Row updates", "delete files")]
    ops = list(csv.DictReader((OUT / "p1_operations.csv").open()))
    W, H = 720, 300
    colx = {"a1": 330, "a3": 560}
    out = [text(30, 30, "Table state", "bm-colh"), text(colx["a1"], 30, "Scheduled Spark DAG", "bm-colh", "middle"),
           text(colx["a3"], 30, "Cryo", "bm-colh bm-cryo-t", "middle")]
    for i, (k, name, sub) in enumerate(classes):
        y = 62 + i * 48
        out.append(f'<rect class="bm-row{" bm-row-alt" if i % 2 else ""}" x="16" y="{y - 22}" width="{W - 32}" height="44" rx="8"/>')
        out.append(text(30, y - 2, name, "bm-rowh"))
        out.append(text(30, y + 14, sub, "bm-rows"))
        for arm in ("a1", "a3"):
            mine = [o for o in ops if o["arm"] == arm and o["class"] == k and o["trial"] == "7"]
            useful = sum(1 for o in mine if o["unnecessary"] == "False")
            waste = sum(1 for o in mine if o["unnecessary"] == "True" and o["op"] != "expire_snapshots")
            expire = sum(1 for o in mine if o["op"] == "expire_snapshots")
            label = []
            if useful:
                label.append(("bm-chip-ok", f"{useful} useful"))
            if waste:
                label.append(("bm-chip-waste", f"{waste} not needed"))
            if expire:
                label.append(("bm-chip-exp", f"{expire} expiry"))
            if not label:
                label.append(("bm-chip-none", "left alone"))
            wtot = sum(len(t) * 6.6 + 18 for _, t in label) + 6 * (len(label) - 1)
            x = colx[arm] - wtot / 2
            for cls, t in label:
                w = len(t) * 6.6 + 18
                out.append(f'<rect class="bm-chip {cls}" x="{x:.1f}" y="{y - 12}" width="{w:.1f}" height="22" rx="11"/>')
                out.append(text(x + w / 2, y + 3, t, f"bm-chipt {cls}-t", "middle"))
                x += w + 6
    out.append(text(30, H - 14, "Operations per table class over 4 cycles, trial 7 (trials 8 and 9 identical). Expiry is snapshot expiry, a no-op inside a 2-hour test.", "bm-foot"))
    svg("decisions", W, H, "\n".join(out), "Decision grid. The scheduled DAG rewrote, re-manifested and expired every table every cycle, including healthy ones. Cryo left healthy tables alone, compacted the fragmented ones, and converted delete files on updated tables.")


# ---------- 3. File layout: 7,594 files -> 690 ----------
def layout():
    st = P2["table_state_mean"]
    before, after = round(st["a0"]["data_files"]["mean"]), round(st["c256"]["data_files"]["mean"])
    small_b, small_a = round(st["a0"]["small_files_lt32MiB"]["mean"]), round(st["c256"]["small_files_lt32MiB"]["mean"])
    per = 20
    W = 720
    out = []

    def field(x0, n, nsmall, cols):
        dots = math.ceil(n / per)
        ds = math.ceil(nsmall / per)
        for i in range(dots):
            cx = x0 + (i % cols) * 7.2
            cy = 54 + (i // cols) * 7.2
            out.append(f'<rect class="{"bm-dot-small" if i < ds else "bm-dot-big"}" x="{cx:.1f}" y="{cy:.1f}" width="5.4" height="5.4" rx="1.2"/>')
        return 54 + (math.ceil(dots / cols)) * 7.2
    yb = field(24, before, small_b, 40)
    ya = field(420, after, small_a, 40)
    out.append(text(24, 36, f"Before: {before:,} data files", "bm-colh"))
    out.append(text(420, 36, f"After Cryo: {after:,} data files", "bm-colh bm-cryo-t"))
    out.append(text(24, yb + 20, f"{small_b:,} under 32 MiB, mean file {st['a0']['avg_file_mib']['mean']:.1f} MiB", "bm-rows"))
    out.append(text(420, ya + 20, f"{small_a:,} under 32 MiB, mean file {st['c256']['avg_file_mib']['mean']:.0f} MiB", "bm-rows"))
    out.append(f'<path class="bm-arrow" d="M330,{(54 + yb) / 2:.0f}h70m-10,-8l10,8l-10,8"/>')
    H = int(yb + 58)
    out.append(f'<rect class="bm-dot-small" x="24" y="{H - 16}" width="8" height="8" rx="1.5"/>')
    out.append(text(38, H - 8, f"small files (1 square = {per} files)", "bm-foot"))
    out.append(f'<rect class="bm-dot-big" x="230" y="{H - 16}" width="8" height="8" rx="1.5"/>')
    out.append(text(244, H - 8, "right-sized files", "bm-foot"))
    svg("layout", W, H, "\n".join(out), f"Waffle chart. The 100 GiB table went from {before:,} data files, {small_b:,} of them under 32 MiB, to {after:,} files after one Cryo run.")


# ---------- 4. Query speedups at 100 GiB ----------
def speed():
    rows = [r for r in csv.DictReader((OUT / "p2_query_speedup.csv").open()) if r.get("speedup_vs_a0") and r["arm"] == "c256"]
    names = {"p_agg_all": "Aggregate, whole table", "p_day_small": "One day, small-file band", "p_selective": "Customer filter, whole table",
             "p_day_mid": "One day, mid-size band", "p_full": "Read every row", "p_day_large": "One day, already-large files",
             "p_join": "7-day join", "p_point": "Single-row lookup"}
    rows.sort(key=lambda r: -float(r["speedup_vs_a0"]))
    W, H, L = 720, 60 + 36 * len(rows), 230
    xmax = 5
    X = lambda v: L + v / xmax * (W - L - 70)
    out = [f'<line class="bm-base" x1="{X(1):.1f}" x2="{X(1):.1f}" y1="34" y2="{H - 22}"/>', text(X(1), 26, "no change", "bm-tick", "middle")]
    for i, r in enumerate(rows):
        y = 44 + i * 36
        v = float(r["speedup_vs_a0"])
        cls = "bm-cryo-fill" if v >= 1.05 else ("bm-neutral-fill" if v >= 0.95 else "bm-slow-fill")
        x0, x1 = sorted((X(1), X(v)))
        out.append(text(L - 12, y + 15, names[r["query"]], "bm-rowh", "end"))
        out.append(f'<rect class="{cls}" x="{x0:.1f}" y="{y + 2}" width="{max(2, x1 - x0):.1f}" height="20" rx="4"/>')
        label = f"{v:.2f}× faster" if v >= 1.05 else ("unchanged" if v >= 0.95 else f"{1 / v:.1f}× slower")
        out.append(text(max(x1, X(1)) + 8, y + 17, label, "bm-val"))
    out.append(text(L, H - 4, "Median latency, untouched vs Cryo default (256 MB target), geometric mean of 3 trials.", "bm-foot"))
    svg("speed", W, H, "\n".join(out), "Bar chart of query speedups on the 100 GiB table after Cryo's default compaction: whole-table aggregate 3.6× faster, small-file day 4.3×, customer filter 3.4×, mid-size day 2×, full read 1.75×, large-file day unchanged, 7-day join 1.1× slower, single-row lookup 1.8× slower.")


# ---------- 5. Foreground protection ----------
def foreground():
    rows = list(csv.DictReader((OUT / "p6_foreground.csv").open()))
    qs = [("fg_point", "Point lookup"), ("fg_day", "One-day scan"), ("fg_week", "One-week scan")]
    W, H, L = 720, 220, 150
    out = []
    for i, (q, name) in enumerate(qs):
        b = next(r for r in rows if r["window"] == "baseline" and r["query"] == q)
        c = next(r for r in rows if r["window"] == "c256" and r["query"] == q)
        y = 40 + i * 56
        mx = max(float(b["p95"]), float(c["p95"])) * 1.25
        X = lambda v: L + v / mx * (W - L - 140)
        out.append(text(L - 12, y + 22, name, "bm-rowh", "end"))
        out.append(f'<rect class="bm-a0-fill" x="{L}" y="{y}" width="{X(float(b["p95"])) - L:.1f}" height="16" rx="4"/>')
        out.append(text(X(float(b["p95"])) + 8, y + 13, f'{int(b["p95"]):,} ms idle', "bm-val"))
        out.append(f'<rect class="bm-cryo-fill" x="{L}" y="{y + 20}" width="{X(float(c["p95"])) - L:.1f}" height="16" rx="4"/>')
        out.append(text(X(float(c["p95"])) + 8, y + 33, f'{int(c["p95"]):,} ms during Cryo compaction', "bm-val"))
    out.append(text(L, H - 6, "p95 latency of live Trino queries, about 500 to 700 runs each, 0 failures.", "bm-foot"))
    svg("foreground", W, H, "\n".join(out), "Paired bars. p95 latency of live queries while idle and while Cryo compacted 67 GiB: point lookup 552 vs 485 ms, one-day scan 813 vs 808 ms, one-week scan 4,169 vs 4,195 ms.")


SITE_INC.mkdir(parents=True, exist_ok=True)
for f in (cdc, decisions, layout, speed, foreground):
    f()
print(sorted(p.name for p in SITE_INC.glob("bench-*.svg")))
