import { apiFetch, type ApiOptions } from "./client";

export interface SnapshotSummary {
  total_records: number | null;
  total_files_size: number | null;
  added_data_files: number | null;
  deleted_data_files: number | null;
}

export interface SnapshotRef {
  name: string;
  type: "branch" | "tag";
  min_snapshots_to_keep: number | null;
  max_snapshot_age_ms: number | null;
  max_ref_age_ms: number | null;
}

export interface TableSnapshot {
  snapshot_id: number;
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
  current_snapshot_id: number | null;
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
