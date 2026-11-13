import { apiFetch, type ApiOptions } from "./client";
import type { components } from "./openapi";

export type StorageOverview = components["schemas"]["StorageOverviewResponse"];
export type StorageBreakdown = components["schemas"]["StorageBreakdownRead"];
export type StorageTrendPoint = components["schemas"]["StorageTrendPointRead"];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/**
 * True when the payload has every section the card reads. An older API, a proxy error page or a
 * stub answering `[]` must surface as an error state, not crash Home or render invented zeros.
 */
export const isStorageOverview = (value: unknown): value is StorageOverview => {
  if (!isRecord(value)) return false;
  const { breakdown, freed_24h: freed, cumulative, trend, versioning_findings: findings } = value;
  return (
    isRecord(breakdown) &&
    ["total_bytes", "live_bytes", "retained_history_bytes", "dead_bytes", "orphan_bytes", "metadata_bytes", "tables_total", "tables_with_listing", "tables_with_cost"].every((key) => isNumber(breakdown[key])) &&
    isRecord(freed) &&
    isNumber(freed.actions_succeeded) &&
    isNumber(freed.actions_measured) &&
    isRecord(cumulative) &&
    isNumber(cumulative.days_recorded) &&
    Array.isArray(trend) &&
    (findings == null || Array.isArray(findings))
  );
};

export const getStorageOverview = async (api: ApiOptions): Promise<StorageOverview> => {
  const payload = await apiFetch<unknown>("/impact/storage-overview", api);
  if (!isStorageOverview(payload)) throw new Error("Unexpected storage overview response");
  return payload;
};
