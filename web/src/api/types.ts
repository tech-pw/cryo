export type UUID = string;
export type RoleToken = "viewer" | "operator" | "admin";
export type EngineType = "trino" | "spark_emr";
export type Mode = "light" | "standard" | "deep";
export type Grade = "A" | "B" | "C" | "D" | "F" | string;
export type Priority = "HIGH" | "MEDIUM" | "LOW" | string;
export type JsonObject = Record<string, unknown>;

export interface SourceRead {
  id: UUID;
  name: string;
  type: string;
  config: JsonObject;
  secret_ref?: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export type SourceWrite = Pick<SourceRead, "name" | "type" | "config" | "enabled"> & {
  secret_ref?: string | null;
};

export interface DestinationRead {
  id: UUID;
  name: string;
  engine: EngineType;
  config: JsonObject;
  secret_ref?: string | null;
  offpeak_start?: number | null;
  offpeak_end?: number | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export type DestinationWrite = Pick<
  DestinationRead,
  "name" | "engine" | "config" | "enabled" | "offpeak_start" | "offpeak_end"
> & { secret_ref?: string | null };

export interface PresetRead {
  id: UUID;
  name: string;
  destination_id: UUID;
  description?: string | null;
  mode: Mode;
  operations: string[];
  params: JsonObject;
  schedule_cron?: string | null;
  priority: number;
  enabled: boolean;
  guardrails: JsonObject;
  created_at: string;
  updated_at: string;
}

export type PresetWrite = Omit<PresetRead, "id" | "created_at" | "updated_at">;

export interface PresetRuleRead {
  id: UUID;
  preset_id: UUID;
  predicate: JsonObject;
  enabled: boolean;
}

export interface PresetSimulationResponse {
  preset_id: UUID;
  matched_count: number;
  total_size_gb: number;
  small_files_count: number;
  sample: Array<Pick<TableRead, "id" | "schema_name" | "table_name"> & {
    total_size_gb?: number;
    small_files_count?: number;
    grade?: string | null;
  }>;
}

export interface TableStatsRead {
  needs_compaction?: boolean;
  compaction_priority?: Priority;
  grade?: Grade;
  overall_score?: number;
  total_files?: number;
  small_files_count?: number;
  total_partitions?: number;
  max_files_per_partition?: number;
  avg_files_per_partition?: number;
  partition_skew_ratio?: number;
  total_records?: number;
  total_size_gb?: number;
  avg_file_size_mb?: number;
  total_snapshots?: number;
  total_delete_files?: number;
  delete_file_pct?: number;
  manifest_count?: number;
  partition_col?: string | null;
  partition_col_type?: string | null;
  estimated_monthly_s3_cost_usd?: number;
  query_count_30d?: number;
  raw?: JsonObject | null;
}

export interface TableRead {
  id: UUID;
  source_id: UUID;
  schema_name: string;
  table_name: string;
  first_seen_at: string;
  is_active: boolean;
  stats?: TableStatsRead | null;
  effective_preset?: string | null;
}

export interface PaginatedTables {
  items: TableRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface PartitionRead {
  id?: number | string;
  partition_value?: string | null;
  files_per_partition?: number;
  small_files_in_partition_count?: number;
  partition_size_gb?: number;
  compaction_status?: string | null;
}

export interface AuditEntry {
  id?: string | number;
  created_at?: string;
  ts?: string;
  action?: string;
  status?: string;
  detail?: string | JsonObject | null;
  engine?: string | null;
  duration_ms?: number | null;
}

export interface HistoryPoint {
  scan_at?: string;
  small_files_count?: number;
  total_files?: number;
  total_size_gb?: number;
  grade?: string;
  overall_score?: number;
}

export interface EffectivePresetResponse {
  table_id: UUID;
  preset_id?: UUID | null;
  preset_name?: string | null;
  provenance: "assignment" | "rule" | "none";
  rule_id?: UUID | null;
}

export interface RenderedStatementRead {
  action: string;
  engine: EngineType;
  sql: string;
}

export interface TablePreviewRead {
  table_ref: string;
  notes: string[];
  statements: RenderedStatementRead[];
}

export interface MaintenancePreviewResponse {
  items: TablePreviewRead[];
}

export interface MaintenanceRunResponse {
  run_id: UUID;
  job_ids: UUID[];
  status: string;
}

export interface RunRead {
  id: UUID;
  kind: string;
  status: string;
  preset_id?: UUID | null;
  trigger?: string | null;
  deadline_at?: string | null;
  created_at: string;
  finished_at?: string | null;
  summary?: JsonObject | null;
}

export interface JobRead {
  id: UUID;
  run_id: UUID;
  table_id: UUID;
  destination_id: UUID;
  schema_name?: string | null;
  table_name?: string | null;
  destination_name?: string | null;
  destination_engine?: string | null;
  mode: string;
  status: string;
  engine_ref?: string | null;
  replica?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  plan?: JsonObject | null;
}

export interface DatalakeDashboard {
  totals?: {
    tables?: number;
    size_gb?: number;
    small_files?: number;
    monthly_cost_usd?: number;
    needs_compaction?: number;
    running_jobs?: number;
  };
  grade_distribution?: Array<{ grade: string; count: number }>;
  cost_by_schema?: Array<{ schema_name: string; cost_usd?: number; size_gb?: number }>;
  top_offenders?: TableRead[];
  drift?: Array<HistoryPoint & { table_id?: UUID; schema_name?: string; table_name?: string }>;
}
