import type { FreshnessBucketKey } from "../api/types";

export const BROKEN_TABLE_FAILURE_THRESHOLD = 2;

export const FRESHNESS_LABELS: Record<FreshnessBucketKey, string> = {
  within_24h: "Updated in last 24 hours",
  "1_3d": "Not updated 1-3 days",
  "3_7d": "Not updated 3-7 days",
  "7_15d": "Not updated 7-15 days",
  "15_30d": "Not updated 15-30 days",
  "1_3m": "Not updated 1-3 months",
  "3_6m": "Not updated 3-6 months",
  "6_12m": "Not updated 6-12 months",
  over_1y: "Not updated over 1 year",
  unknown: "No snapshot timestamp",
  failed: "Failed / corrupt tables"
};

const BUCKET_KEYS = new Set(Object.keys(FRESHNESS_LABELS));

export const isFreshnessBucketKey = (value: string | null): value is FreshnessBucketKey =>
  value !== null && BUCKET_KEYS.has(value);

export const freshnessCatalogHref = (key: FreshnessBucketKey, asOf: string): string => {
  const params = new URLSearchParams({ freshness: key, freshnessAsOf: asOf });
  return `/catalogs?${params.toString()}`;
};

export const freshnessAgeBoundsDays = (
  key: FreshnessBucketKey
): { min?: number; max?: number } | null => {
  switch (key) {
    case "within_24h":
      return { max: 1 };
    case "1_3d":
      return { min: 1, max: 3 };
    case "3_7d":
      return { min: 3, max: 7 };
    case "7_15d":
      return { min: 7, max: 15 };
    case "15_30d":
      return { min: 15, max: 30 };
    case "1_3m":
      return { min: 30, max: 90 };
    case "3_6m":
      return { min: 90, max: 180 };
    case "6_12m":
      return { min: 180, max: 365 };
    case "over_1y":
      return { min: 365 };
    case "unknown":
    case "failed":
      return null;
  }
};
