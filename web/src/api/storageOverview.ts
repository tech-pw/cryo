import { apiFetch, type ApiOptions } from "./client";
import type { components } from "./openapi";

export type StorageOverview = components["schemas"]["StorageOverviewResponse"];
export type StorageBreakdown = components["schemas"]["StorageBreakdownRead"];
export type StorageTrendPoint = components["schemas"]["StorageTrendPointRead"];

export const getStorageOverview = (api: ApiOptions) => apiFetch<StorageOverview>("/impact/storage-overview", api);
