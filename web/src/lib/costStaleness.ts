import type { CostPrecision } from "../api/types";

const DEFAULT_STALE_HOURS = 168;

export const isOrphanScanStale = (
  precision?: CostPrecision | null,
  lastFullCostScanAt?: string | null,
  staleHours = DEFAULT_STALE_HOURS
): boolean => {
  if (precision !== "storage_truth" || !lastFullCostScanAt) return false;
  const scannedAt = Date.parse(lastFullCostScanAt);
  if (Number.isNaN(scannedAt)) return false;
  return Date.now() - scannedAt > staleHours * 3_600_000;
};
