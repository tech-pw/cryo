import type { DashboardImprovedTable, TableRead } from "../../api/types";

export interface MapPoint {
  id: string;
  name: string;
  grade: string;
  queries: number;
  smallFiles: number;
  sizeGb: number;
  x: number;
  y: number;
  r: number;
  fixFirst: boolean;
  priority: number;
}

export interface MapLayout {
  points: MapPoint[];
  splitX: number;
  splitY: number;
  xTicks: Array<{ value: number; x: number }>;
  yTicks: Array<{ value: number; y: number }>;
}

const log = (value: number) => Math.log10(Math.max(1, value));

/** Powers of ten inside [min, max] (log units); 2× and 5× steps join in when the span is narrow. */
export const decadeTicks = (min: number, max: number) => {
  const decades: number[] = [];
  for (let exp = Math.floor(min); exp <= Math.ceil(max); exp += 1) decades.push(10 ** exp);
  const inside = (value: number) => Math.log10(value) >= min && Math.log10(value) <= max;
  if (decades.filter(inside).length >= 3) return decades;
  return decades.flatMap((value) => [value, value * 2, value * 5]);
};

/**
 * Log-log placement of hot tables: query load across, small-file debt up, bubble area by size.
 * The quadrant split sits at the median of each axis so "fix first" is always the busiest and
 * most fragmented quarter of what is shown, not an arbitrary threshold.
 */
export const layoutFixFirst = (
  tables: readonly TableRead[],
  box: { width: number; height: number; left: number; right: number; top: number; bottom: number }
): MapLayout | null => {
  const rows = tables
    .map((table) => ({
      id: table.id,
      name: `${table.schema_name}.${table.table_name}`,
      grade: table.stats?.grade ?? "?",
      queries: table.stats?.query_count_30d ?? 0,
      smallFiles: table.stats?.small_files_count ?? 0,
      sizeGb: table.stats?.total_size_gb ?? 0
    }))
    .filter((row) => row.queries > 0);
  if (!rows.length) return null;
  const xs = rows.map((row) => log(row.queries));
  const ys = rows.map((row) => log(row.smallFiles));
  const pad = (lo: number, hi: number) => (hi - lo < 0.5 ? [lo - 0.5, hi + 0.5] : [lo - (hi - lo) * 0.08, hi + (hi - lo) * 0.08]);
  const [x0, x1] = pad(Math.min(...xs), Math.max(...xs));
  const [y0, y1] = pad(Math.min(...ys), Math.max(...ys));
  const plotW = box.width - box.left - box.right;
  const plotH = box.height - box.top - box.bottom;
  const sx = (value: number) => box.left + ((value - x0) / (x1 - x0)) * plotW;
  const sy = (value: number) => box.top + plotH - ((value - y0) / (y1 - y0)) * plotH;
  const median = (values: number[]) => {
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  };
  const mx = median(xs);
  const my = median(ys);
  const maxSize = Math.max(...rows.map((row) => row.sizeGb), 1);
  const points = rows.map((row, index) => ({
    ...row,
    x: sx(xs[index]),
    y: sy(ys[index]),
    r: 5 + Math.sqrt(row.sizeGb / maxSize) * 13,
    fixFirst: xs[index] >= mx && ys[index] >= my,
    priority: (xs[index] - x0) / (x1 - x0) + (ys[index] - y0) / (y1 - y0)
  }));
  return {
    points: points.sort((a, b) => b.r - a.r),
    splitX: sx(mx),
    splitY: sy(my),
    xTicks: decadeTicks(x0, x1).filter((value) => log(value) >= x0 && log(value) <= x1).map((value) => ({ value, x: sx(log(value)) })),
    yTicks: decadeTicks(y0, y1).filter((value) => log(value) >= y0 && log(value) <= y1).map((value) => ({ value, y: sy(log(value)) }))
  };
};

export interface SlopeLine {
  id: string;
  name: string;
  from: { grade: string; score: number; y: number };
  to: { grade: string; score: number; y: number };
  labelY: number;
  gain: number;
}

/** Before/after rows on a shared 0–100 axis, with right-hand labels nudged apart so none overlap. */
export const layoutSlope = (
  rows: readonly DashboardImprovedTable[],
  box: { top: number; bottom: number },
  minGap = 18
): { lines: SlopeLine[]; totalGain: number; min: number } => {
  const valid = rows.filter((row) => typeof row.previous_score === "number" && typeof row.overall_score === "number");
  if (!valid.length) return { lines: [], totalGain: 0, min: 0 };
  const scores = valid.flatMap((row) => [row.previous_score as number, row.overall_score as number]);
  const min = Math.max(0, Math.floor((Math.min(...scores) - 5) / 10) * 10);
  const max = 100;
  const scale = (score: number) => box.bottom - ((score - min) / (max - min)) * (box.bottom - box.top);
  const lines = valid.map((row) => ({
    id: row.table_id,
    name: `${row.schema_name}.${row.table_name}`,
    from: { grade: row.previous_grade ?? "?", score: row.previous_score as number, y: scale(row.previous_score as number) },
    to: { grade: row.grade ?? "?", score: row.overall_score as number, y: scale(row.overall_score as number) },
    labelY: scale(row.overall_score as number),
    gain: Math.round((row.overall_score as number) - (row.previous_score as number))
  }));
  const ordered = [...lines].sort((a, b) => a.to.y - b.to.y);
  for (let i = 1; i < ordered.length; i += 1) {
    ordered[i].labelY = Math.max(ordered[i].labelY, ordered[i - 1].labelY + minGap);
  }
  const overflow = ordered.length ? ordered[ordered.length - 1].labelY - box.bottom : 0;
  if (overflow > 0) {
    for (let i = ordered.length - 1; i >= 0; i -= 1) {
      const limit = i === ordered.length - 1 ? box.bottom : ordered[i + 1].labelY - minGap;
      ordered[i].labelY = Math.min(ordered[i].labelY, limit);
    }
  }
  return { lines, totalGain: lines.reduce((sum, line) => sum + Math.max(0, line.gain), 0), min };
};
