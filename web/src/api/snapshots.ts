import { apiFetch, type ApiOptions } from "./client";
import type { components } from "./openapi";

export type SnapshotSummary = components["schemas"]["SnapshotSummaryRead"];
export type SnapshotWriter = components["schemas"]["SnapshotWriterRead"];

export interface SnapshotRef {
  name: string;
  type: "branch" | "tag";
  snapshot_id: string;
  min_snapshots_to_keep: number | null;
  max_snapshot_age_ms: number | null;
  max_ref_age_ms: number | null;
}

export interface TableSnapshot {
  snapshot_id: string;
  parent_snapshot_id: string | null;
  timestamp_ms: number | null;
  committed_at: string | null;
  sequence_number: number | null;
  operation: string | null;
  summary: SnapshotSummary;
  is_current: boolean;
  refs: SnapshotRef[];
  protected: boolean;
  protected_label: string | null;
}

export interface TableSnapshotsResponse {
  table_id: string;
  current_snapshot_id: string | null;
  total: number;
  pinned_count: number;
  latest_committed_at: string | null;
  oldest_committed_at: string | null;
  page: number;
  page_size: number;
  snapshots: TableSnapshot[];
}

export const listTableSnapshots = (
  api: ApiOptions,
  tableId: string,
  query: { page?: number; page_size?: number } = {}
) => apiFetch<TableSnapshotsResponse>(`/tables/${tableId}/snapshots`, api, { query });

export const protectTableSnapshot = (api: ApiOptions, tableId: string, snapshotId: string) =>
  apiFetch<{ table_id: string; snapshot_id: string; label: string | null }>(
    `/tables/${tableId}/protected-snapshots`,
    api,
    {
      method: "POST",
      body: { snapshot_id: snapshotId }
    }
  );

export interface ProtectedSnapshotEntry {
  snapshot_id: string;
  label: string | null;
  protected_at: string;
  protected_by: string | null;
}

export const listProtectedSnapshots = (api: ApiOptions, tableId: string) =>
  apiFetch<{ table_id: string; snapshots: ProtectedSnapshotEntry[] }>(
    `/tables/${tableId}/protected-snapshots`,
    api
  );

export const unprotectTableSnapshot = (api: ApiOptions, tableId: string, snapshotId: string) =>
  apiFetch<{ table_id: string; snapshot_id: string; removed: boolean }>(
    `/tables/${tableId}/protected-snapshots/${encodeURIComponent(snapshotId)}`,
    api,
    { method: "DELETE" }
  );

export interface RollbackSnapshotResponse {
  table_id: string;
  snapshot_id: string;
  previous_snapshot_id: string | null;
}

/** Moves the table's current pointer back to `snapshotId`. Admin-gated; requires confirm: true. */
export const rollbackTableSnapshot = (api: ApiOptions, tableId: string, snapshotId: string) =>
  apiFetch<RollbackSnapshotResponse>(
    `/tables/${tableId}/snapshots/${encodeURIComponent(snapshotId)}/rollback`,
    api,
    {
      method: "POST",
      body: { snapshot_id: snapshotId, confirm: true }
    }
  );
