import type { StorageBreakdown, StorageOverview, StorageTrendPoint } from "../api/storageOverview";

const TIB = 1024 ** 4;
const GIB = 1024 ** 3;
const MIB = 1024 ** 2;

/** Binary units, labelled TB/GB/MB like the rest of the app. */
export const formatStorage = (bytes: number | null | undefined): string => {
  if (bytes == null || !Number.isFinite(bytes)) return "-";
  const abs = Math.abs(bytes);
  if (abs >= TIB) return `${(bytes / TIB).toFixed(abs >= 100 * TIB ? 0 : 1)} TB`;
  if (abs >= GIB) return `${(bytes / GIB).toFixed(abs >= 100 * GIB ? 0 : 1)} GB`;
  if (abs >= MIB) return `${(bytes / MIB).toFixed(0)} MB`;
  return bytes > 0 ? "< 1 MB" : "0 GB";
};

export type SegmentKey = "live" | "history" | "dead" | "orphan" | "metadata";

export interface StorageSegment {
  key: SegmentKey;
  label: string;
  hint: string;
  bytes: number;
  pct: number;
  freeable: boolean;
}

export const SEGMENT_META: Record<SegmentKey, { label: string; hint: string; freeable: boolean }> = {
  live: { label: "Live data", hint: "Files the current snapshot reads.", freeable: false },
  history: { label: "Snapshot history", hint: "Older snapshots still inside retention. Kept on purpose for time travel.", freeable: false },
  dead: { label: "Dead", hint: "Only referenced by snapshots past retention. Snapshot expiry frees it.", freeable: true },
  orphan: { label: "Orphans", hint: "Files no snapshot references. Orphan cleanup frees them.", freeable: true },
  metadata: { label: "Metadata", hint: "Manifests and manifest lists.", freeable: false }
};

export const storageSegments = (b: StorageBreakdown): StorageSegment[] => {
  const bytes: Record<SegmentKey, number> = {
    live: b.live_bytes,
    history: b.retained_history_bytes,
    dead: b.dead_bytes,
    orphan: b.orphan_bytes,
    metadata: b.metadata_bytes
  };
  const total = b.total_bytes;
  return (Object.keys(bytes) as SegmentKey[]).map((key) => ({
    key,
    ...SEGMENT_META[key],
    bytes: bytes[key],
    pct: total > 0 ? (bytes[key] / total) * 100 : 0
  }));
};

export const freeableBytes = (b: StorageBreakdown) => b.dead_bytes + b.orphan_bytes;

export type CoverageState = "none" | "partial" | "full";
export const listingCoverage = (b: StorageBreakdown): CoverageState =>
  b.tables_with_listing === 0 ? "none" : b.tables_with_listing >= b.tables_total ? "full" : "partial";

export type HeroState = "idle" | "unmeasured" | "measured";
export interface HeroView {
  state: HeroState;
  measuredPct: number | null;
}

export const heroView = (freed: StorageOverview["freed_24h"]): HeroView => {
  if (freed.actions_succeeded === 0) return { state: "idle", measuredPct: null };
  const measuredPct = Math.round((freed.actions_measured / freed.actions_succeeded) * 100);
  return { state: freed.actions_measured === 0 ? "unmeasured" : "measured", measuredPct };
};

export const SOURCE_LABELS: Record<string, string> = {
  engine: "measured by the engine",
  listing_confirmed: "confirmed by S3 listing",
  scan_estimate: "estimated from scan data"
};

export interface TrendDatum {
  day: string;
  total: number | null;
  live: number | null;
  freed: number;
}

/** Points in TB for the chart. Days before the storage snapshot existed have no live value. */
export const trendData = (points: StorageTrendPoint[]): TrendDatum[] =>
  points.map((p) => ({
    day: p.day,
    total: p.total_bytes == null ? null : p.total_bytes / TIB,
    live: p.live_bytes == null ? null : p.live_bytes / TIB,
    freed: p.cumulative_freed_bytes / TIB
  }));

export const formatTb = (tb: number | null | undefined) => (tb == null ? "-" : formatStorage(tb * TIB));
