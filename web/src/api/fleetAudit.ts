import { apiFetch, type ApiOptions } from "./client";

export interface FleetAuditItem {
  id: number;
  ts: string;
  table_id?: string | null;
  schema_name?: string | null;
  table_name?: string | null;
  action: string;
  status: string;
  engine?: string | null;
  duration_ms?: number | null;
  actual_bytes_freed?: number | null;
  input_files_count?: number | null;
  output_files_count?: number | null;
}

export interface FleetAuditResponse {
  items: FleetAuditItem[];
  limit: number;
}

export const listFleetAudit = (
  api: ApiOptions,
  query: { limit?: number; action?: string; status?: string; engine?: string }
) => apiFetch<FleetAuditResponse>("/audit", api, { query });
