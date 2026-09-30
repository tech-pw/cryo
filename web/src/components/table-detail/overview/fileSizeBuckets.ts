import type { TableStatsRead } from "../../../api/types";

export type SizeBand = "small" | "below" | "target" | "over";

export interface SizeBucket {
  key: string;
  label: string;
  files: number;
  band: SizeBand;
  containsTarget: boolean;
}

export interface SizeHistogram {
  buckets: SizeBucket[];
  targetBytes: number;
  targetAssumed: boolean;
  totalFiles: number;
  byBand: Record<SizeBand, number>;
}

const MIB = 2 ** 20;
const ICEBERG_DEFAULT_TARGET = 512 * MIB;
/** Files under 1 MiB are one bucket: splitting kilobyte ranges adds bars without adding a decision. */
const FIRST_DETAILED_BIN = 20;

const unit = (bytes: number): string => {
  if (bytes >= 2 ** 40) return `${bytes / 2 ** 40}T`;
  if (bytes >= 2 ** 30) return `${bytes / 2 ** 30}G`;
  if (bytes >= MIB) return `${bytes / MIB}M`;
  if (bytes >= 1024) return `${bytes / 1024}K`;
  return `${bytes}B`;
};

export const bandFor = (lower: number, upper: number, target: number): SizeBand => {
  if (lower >= target * 2) return "over";
  if (upper >= target) return "target";
  if (upper <= target / 8) return "small";
  return "below";
};

const positive = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;

/**
 * Reads raw.file_size_bins (cryo/scanner/analyze.py: bin i holds data files of
 * [2^i, 2^(i+1)) bytes). Returns null when the bins are missing or all zero on a table
 * that has files -- the Go compatibility adapter zero-fills legacy payloads, and a
 * zero-filled array is not a measurement.
 */
export const buildSizeHistogram = (stats: TableStatsRead | null | undefined): SizeHistogram | null => {
  const raw = (stats?.raw ?? {}) as Record<string, unknown>;
  const bins = (raw.file_size_bins as { data?: unknown } | undefined)?.data;
  if (!Array.isArray(bins) || bins.length === 0) return null;
  const data = bins.map((value) => (typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0));
  const totalFiles = data.reduce((sum, value) => sum + value, 0);
  if (totalFiles === 0) return null;

  const configured =
    positive(raw.reduction_target_bytes) ?? positive((stats as Record<string, unknown> | undefined)?.target_file_size_bytes);
  const targetBytes = configured ?? ICEBERG_DEFAULT_TARGET;
  const targetBin = Math.min(data.length - 1, Math.max(FIRST_DETAILED_BIN, Math.floor(Math.log2(targetBytes)) - 1));

  const nonZero = data.flatMap((value, index) => (value > 0 ? [index] : []));
  const first = Math.max(FIRST_DETAILED_BIN, Math.min(nonZero[0], targetBin - 3));
  const last = Math.max(nonZero[nonZero.length - 1], targetBin + 1);

  const buckets: SizeBucket[] = [];
  const tiny = data.slice(0, FIRST_DETAILED_BIN).reduce((sum, value) => sum + value, 0);
  if (tiny > 0) {
    buckets.push({ key: "lt-1m", label: "<1M", files: tiny, band: bandFor(0, MIB, targetBytes), containsTarget: false });
  }
  for (let index = first; index <= Math.min(last, data.length - 1); index += 1) {
    const lower = 2 ** index;
    const upper = 2 ** (index + 1);
    buckets.push({
      key: `b${index}`,
      label: `${unit(lower)}–${unit(upper)}`,
      files: data[index],
      band: bandFor(lower, upper, targetBytes),
      containsTarget: targetBytes > lower && targetBytes <= upper
    });
  }

  const byBand: Record<SizeBand, number> = { small: 0, below: 0, target: 0, over: 0 };
  for (const bucket of buckets) byBand[bucket.band] += bucket.files;
  return { buckets, targetBytes, targetAssumed: configured == null, totalFiles, byBand };
};

export const formatTarget = (bytes: number): string =>
  bytes >= 2 ** 30 ? `${(bytes / 2 ** 30).toFixed(bytes % 2 ** 30 === 0 ? 0 : 1)} GiB` : `${Math.round(bytes / MIB)} MiB`;
