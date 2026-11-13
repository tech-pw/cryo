import type { StorageBreakdown, StorageTrendPoint } from "../../api/storageOverview";

export interface CleanupProgress {
  reclaimed: number;
  remaining: number;
  /** Share of the known opportunity already reclaimed, 0–100; null when there is nothing to compare. */
  pct: number | null;
}

/**
 * Reclaimed so far against what is still freeable. Remaining counts dead files and orphans (freeable
 * by expiry and orphan cleanup) plus the compaction estimate, so "done" never reads as 100% while
 * known waste is still on disk.
 */
export const cleanupProgress = (cumulativeFreed: number | null | undefined, breakdown: StorageBreakdown): CleanupProgress => {
  const reclaimed = Math.max(0, cumulativeFreed ?? 0);
  const remaining = Math.max(0, breakdown.dead_bytes) + Math.max(0, breakdown.orphan_bytes) + Math.max(0, breakdown.compaction_savings_bytes);
  const whole = reclaimed + remaining;
  return { reclaimed, remaining, pct: whole > 0 ? Math.round((reclaimed / whole) * 100) : null };
};

export interface TrendDelta {
  bytes: number;
  pct: number;
  days: number;
}

/** Change in total storage between the first and last recorded day; null with fewer than two. */
export const storageTrendDelta = (trend: readonly StorageTrendPoint[]): TrendDelta | null => {
  const points = trend.filter((point) => point.total_bytes != null).sort((a, b) => a.day.localeCompare(b.day));
  if (points.length < 2) return null;
  const first = points[0];
  const last = points[points.length - 1];
  const start = first.total_bytes as number;
  const end = last.total_bytes as number;
  if (start <= 0) return null;
  const days = Math.round((Date.parse(`${last.day}T00:00:00Z`) - Date.parse(`${first.day}T00:00:00Z`)) / 86_400_000);
  return { bytes: end - start, pct: Math.round(((end - start) / start) * 1000) / 10, days };
};

/**
 * Splits `cells` waffle squares across segments by largest remainder. Any segment with bytes keeps at
 * least one square so small but freeable slices stay visible.
 */
export const waffleCells = <K extends string>(segments: ReadonlyArray<{ key: K; bytes: number }>, cells = 100): K[] => {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.bytes), 0);
  if (total <= 0) return [];
  const raw = segments.map((s) => (Math.max(0, s.bytes) / total) * cells);
  const counts = raw.map((r, i) => (segments[i].bytes > 0 ? Math.max(1, Math.floor(r)) : 0));
  let left = cells - counts.reduce((a, b) => a + b, 0);
  const order = raw.map((r, i) => [r - Math.floor(r), i] as const).sort((a, b) => b[0] - a[0]);
  for (let k = 0; left > 0; k = (k + 1) % order.length) {
    const i = order[k][1];
    if (segments[i].bytes > 0) { counts[i] += 1; left -= 1; }
  }
  while (left < 0) {
    const i = counts.indexOf(Math.max(...counts));
    counts[i] -= 1; left += 1;
  }
  return segments.flatMap((s, i) => Array<K>(counts[i]).fill(s.key));
};
