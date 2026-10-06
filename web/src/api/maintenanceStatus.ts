import { apiFetch, type ApiOptions } from "./client";

export interface BlockingReasonRead {
  code: string;
  summary: string;
  predicate: string;
  blocks_nightly_selection: boolean;
  until: string | null;
  values: Record<string, unknown>;
}

export interface OmittedGateRead {
  code: string;
  where: string;
  why: string;
}

export interface MaintenanceThresholdsRead {
  cooldown_hours: number;
  max_stats_age_days: number;
  min_delete_file_pct: number;
}

export interface MaintenanceActionReceipt {
  id?: string | number | null;
  ts?: string | null;
  action?: string | null;
  operation?: string | null;
  status?: string | null;
  engine?: string | null;
  duration_ms?: number | null;
  actual_bytes_freed?: number | null;
  bytes_freed?: number | null;
  input_files_count?: number | null;
  output_files_count?: number | null;
}

export interface MaintenanceStatusRead {
  table_id: string;
  eligible: boolean;
  evaluated_at: string;
  eligibility_basis: string;
  blocking_reasons: BlockingReasonRead[];
  thresholds: MaintenanceThresholdsRead;
  last_actions: MaintenanceActionReceipt[];
  last_actions_note: string;
  next_nightly_at: string | null;
  next_nightly_note: string;
  omitted: OmittedGateRead[];
}

export const getTableMaintenanceStatus = (api: ApiOptions, tableId: string) =>
  apiFetch<MaintenanceStatusRead>(`/tables/${tableId}/maintenance-status`, api);
