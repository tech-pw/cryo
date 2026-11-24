import type { StorageBreakdown, StorageOverview, StorageTrendPoint } from "../api/storageOverview";

const TB = 1e12;
const GB = 1e9;
const MB = 1e6;

/** Decimal units (1 TB = 10^12 bytes), matching `formatBytesGb` and S3 billing. */
export const formatStorage = (bytes: number | null | undefined): string => {
  if (bytes == null || !Number.isFinite(bytes)) return "-";
  const abs = Math.abs(bytes);
  if (abs >= TB) return `${(bytes / TB).toFixed(abs >= 100 * TB ? 0 : 1)} TB`;
  if (abs >= GB) return `${(bytes / GB).toFixed(abs >= 100 * GB ? 0 : 1)} GB`;
  if (abs >= MB) return `${(bytes / MB).toFixed(0)} MB`;
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
  /** Bytes carried forward from listings older than the fresh window; 0 for segments that need no listing. */
  estimatedBytes: number;
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
  const estimated: Partial<Record<SegmentKey, number>> = {
    history: b.retained_history_bytes_estimated,
    orphan: b.orphan_bytes_estimated
  };
  const total = b.total_bytes;
  return (Object.keys(bytes) as SegmentKey[]).map((key) => ({
    key,
    ...SEGMENT_META[key],
    bytes: bytes[key],
    pct: total > 0 ? (bytes[key] / total) * 100 : 0,
    estimatedBytes: estimated[key] ?? 0
  }));
};

export const freeableBytes = (b: StorageBreakdown) => b.dead_bytes + b.orphan_bytes;

/** Coverage by listings newer than `listing_fresh_days`; carried-forward listings do not count. */
export type CoverageState = "none" | "partial" | "full";
export const listingCoverage = (b: StorageBreakdown): CoverageState =>
  b.tables_with_listing === 0 ? "none" : b.tables_listed_recently >= b.tables_total ? "full" : "partial";

/** Tables whose orphan and retained-history figures come from a listing older than the fresh window. */
export const staleListingTables = (b: StorageBreakdown) => Math.max(0, b.tables_with_listing - b.tables_listed_recently);

export type HeroState = "idle" | "unmeasured" | "estimated" | "measured";
export interface HeroView {
  state: HeroState;
  measuredPct: number | null;
  /** Share of the freed bytes priced from scan data rather than counted, 0-100. */
  estimatedPct: number;
}

/**
 * A byte count on an action is not the same as a measurement: `scan_estimate` bytes are priced
 * from the last scan. Untagged bytes predate source tags and come from the engine's before/after
 * file count, so they count as measured. Mostly-estimated totals must not wear a Measured badge.
 */
export const heroView = (freed: StorageOverview["freed_24h"]): HeroView => {
  if (freed.actions_succeeded === 0) return { state: "idle", measuredPct: null, estimatedPct: 0 };
  const measuredPct = Math.round((freed.actions_measured / freed.actions_succeeded) * 100);
  if (freed.actions_measured === 0) return { state: "unmeasured", measuredPct, estimatedPct: 0 };
  const estimated = freed.bytes_by_source?.scan_estimate ?? 0;
  const estimatedPct = freed.bytes_freed > 0 ? Math.round((estimated / freed.bytes_freed) * 100) : 0;
  return { state: estimatedPct > 50 ? "estimated" : "measured", measuredPct, estimatedPct };
};

export const SOURCE_LABELS: Record<string, string> = {
  engine: "measured by the engine",
  scan_confirmed: "confirmed by the next scan",
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
    total: p.total_bytes == null ? null : p.total_bytes / TB,
    live: p.live_bytes == null ? null : p.live_bytes / TB,
    freed: p.cumulative_freed_bytes / TB
  }));

export const formatTb = (tb: number | null | undefined) => (tb == null ? "-" : formatStorage(tb * TB));
