export type UUID = string;
export type RoleToken = "viewer" | "operator" | "admin";
export type EngineType = "trino" | "spark_emr";
export type Mode = "light" | "standard" | "deep";
export type Grade = "A" | "B" | "C" | "D" | "F" | string;
export type Priority = "HIGH" | "MEDIUM" | "LOW" | string;
export type CostPrecision = "heuristic" | "metadata" | "storage_truth";
export type OperationType =
  | "optimize"
  | "expire_snapshots"
  | "remove_orphan_files"
  | "optimize_manifests";
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

export interface SourceTestResult {
  ok: boolean;
  namespaces: string[];
  // No longer populated by the backend -- table enumeration was what made this slow. Kept for
  // response-shape compatibility; table/schema discovery happens via the async Scanner instead.
  table_count: number;
  sample_tables: string[];
  error?: string | null;
  note?: string | null;
}

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
  scan_at?: string;
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
  projected_monthly_s3_cost_usd?: number;
  projected_savings_usd_month?: number;
  reclaimable_bytes?: number;
  cost_precision?: CostPrecision;
  cost_last_full_scan_at?: string | null;
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

export interface PiiMatchRead {
  column_name: string;
  column_type?: string;
  pii_category: string;
  similarity: number;
  already_tagged?: boolean;
}

export interface PiiClassificationResponse {
  table_id: UUID;
  matches: PiiMatchRead[];
}

export interface SimilarTableRead {
  table_id: string;
  schema_name: string;
  table_name: string;
  similarity: number;
  shared_signals?: string[];
}

export interface SimilarTablesResponse {
  table_id: UUID;
  similar: SimilarTableRead[];
}

export interface HealthClassifierResponse {
  table_id: UUID;
  needs_emergency_compaction: boolean;
  probability: number;
  method: string;
}

export interface BanditStatusResponse {
  observations: number;
  confident: boolean;
  config: Record<string, number>;
}

export interface CostModelEvaluateResponse {
  mae_learned: number;
  mae_heuristic: number;
  beats_heuristic: boolean;
  band_coverage: number;
  n_samples: number;
}

export interface AnomalyRead {
  metric: string;
  value: number;
  baseline: number;
  z_score: number;
  severity: string;
}

export interface AnomalyResponse {
  table_id: UUID;
  anomalies: AnomalyRead[];
}

export interface ExplainHealthResponse {
  table_id: UUID;
  explanation: string;
}

export interface AutoCompCompareResponse {
  table_id: UUID;
  static_order: string[];
  autocomp_order: string[];
  improvement_ratio: number;
  autocomp_wins: boolean;
}

export interface CopilotQuestion {
  id: string;
  prompt: string;
}

export interface CopilotAskResponse {
  question_id: string;
  result: JsonObject;
}

export interface CopilotSearchResponse {
  source: string;
  question_id?: string | null;
  similarity?: number | null;
  matched_text?: string | null;
  result?: JsonObject | null;
  answer?: string | null;
}

export interface CopilotNaturalResponse {
  answer: string;
  context_used?: JsonObject | null;
  model: string;
  provider: string;
}

export interface ColumnQualityMetricRead {
  column_name: string;
  null_rate_pct: number;
  null_rate_baseline_pct?: number | null;
  null_rate_spike?: boolean;
  min_value?: string | number | null;
  max_value?: string | number | null;
  value_range_changed?: boolean;
  value_range_explanation?: string | null;
}

export interface DataQualityProfileRead {
  table_id: string;
  table_name: string;
  profiled_at: string;
  column_metrics: ColumnQualityMetricRead[];
  anomalies_detected: number;
  schema_version_changed: boolean;
  new_columns: string[];
  dropped_columns: string[];
}

export interface AWSIntegrationRead {
  id: UUID;
  source_id: UUID;
  bucket: string;
  region: string;
  access_key_id?: string | null;
  enabled: boolean;
  last_tested_at?: string | null;
  last_test_ok?: boolean | null;
  created_at: string;
  updated_at: string;
}

export interface AWSIntegrationWrite {
  bucket: string;
  region: string;
  access_key_id?: string;
  secret_access_key?: string;
  enabled?: boolean;
}

export interface AWSConnectionTestResponse {
  ok: boolean;
  account_id?: string | null;
  arn?: string | null;
  error?: string | null;
}

export interface AWSUsageResponse {
  bucket: string;
  region: string;
  measured_at?: string | null;
  size_bytes_by_storage_class?: Record<string, number>;
  total_size_bytes: number;
  object_count?: number | null;
  price_per_gb_month?: number | null;
  price_source?: string | null;
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
  actor?: string | null;
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

export interface SavingsProjectionRead {
  precision: CostPrecision;
  current_monthly_cost_usd: number;
  projected_monthly_cost_usd: number;
  projected_savings_usd_month: number;
  reclaimable_bytes: number;
  query_request_savings_usd_month?: number;
  by_operation?: Record<string, number>;
  assumptions?: Record<string, unknown>;
}

export interface TablePreviewRead {
  table_ref: string;
  notes: string[];
  statements: RenderedStatementRead[];
  savings?: SavingsProjectionRead | null;
}

export interface MaintenancePreviewResponse {
  items: TablePreviewRead[];
}

export interface MaintenanceRunResponse {
  run_id: UUID;
  job_ids: UUID[];
  status: string;
}

export interface ScannerConfigRead {
  id: number;
  enabled: boolean;
  schedule_cron: string;
  timezone: string;
  num_chunks: number;
  full_scan_default: boolean;
  cost_full_scan_default: boolean;
  max_concurrent_runs: number;
  created_at: string;
  updated_at: string;
}

export type ScannerConfigUpdate = Partial<
  Pick<
    ScannerConfigRead,
    | "enabled"
    | "schedule_cron"
    | "num_chunks"
    | "full_scan_default"
    | "cost_full_scan_default"
    | "max_concurrent_runs"
  >
>;

export interface TableNameRef {
  schema_name: string;
  table_name: string;
}

export interface ScanTriggerRequest {
  scope?: "all" | "source" | "tables" | "schemas";
  source_id?: UUID | null;
  table_ids?: UUID[];
  // Targets a table by name instead of id -- unlike table_ids, doesn't require the table to
  // have already been scanned once (no Cryo-assigned id needed). Requires source_id.
  table_names?: TableNameRef[];
  schema_names?: string[];
  selection?: { mode: "ids"; ids: UUID[] } | { mode: "filter"; filter: unknown };
  trigger?: string;
  requested_by?: string | null;
  full_scan?: boolean;
  cost_full_scan?: boolean;
  num_chunks?: number | null;
}

export interface ScanTriggerResponse {
  run_id: UUID;
  status: string;
  scope: "all" | "source" | "tables" | "schemas";
  queued: boolean;
  message: string;
  cost_full_scan?: boolean;
  full_scan?: boolean;
  num_chunks?: number | null;
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

export interface ScannerStatusRead {
  config: ScannerConfigRead;
  queue_depth?: number | null;
  latest_run?: RunRead | null;
  active_run?: RunRead | null;
  last_successful_run?: RunRead | null;
  next_scan_at?: string | null;
  total_tables: number;
  tables_with_stats: number;
  latest_table_scan_at?: string | null;
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
    projected_monthly_cost_usd?: number;
    projected_savings_usd_month?: number;
    reclaimable_bytes?: number;
    needs_compaction?: number;
    running_jobs?: number;
  };
  grade_distribution?: Array<{ grade: string; count: number }>;
  cost_by_schema?: Array<{
    schema_name: string;
    cost_usd?: number;
    projected_usd?: number;
    savings_usd?: number;
    size_gb?: number;
  }>;
  top_offenders?: TableRead[];
  drift?: Array<HistoryPoint & { table_id?: UUID; schema_name?: string; table_name?: string }>;
}

export interface CostByOperation {
  operation: OperationType;
  savings_usd_month: number;
  reclaimable_bytes: number;
}

export interface CostTrendPoint {
  scan_at: string;
  current_monthly_cost_usd?: number;
  projected_savings_usd_month?: number;
  realized_usd_month?: number | null;
}

export interface CostDashboard {
  totals: {
    current_monthly_cost_usd: number;
    projected_monthly_cost_usd: number;
    projected_savings_usd_month: number;
    current_storage_bytes: number;
    reclaimable_bytes: number;
    precision_coverage?: Partial<Record<CostPrecision, number>>;
    orphan_scan_stale?: boolean;
    last_full_cost_scan_at?: string | null;
  };
  savings_by_operation: CostByOperation[];
  cost_by_schema: Array<{
    schema_name: string;
    current_usd: number;
    projected_usd: number;
    savings_usd: number;
  }>;
  top_opportunities: TableRead[];
  trend?: CostTrendPoint[];
}

export interface TableCostRead {
  table_id: UUID;
  precision: CostPrecision;
  price_per_gb_month: number;
  current_monthly_cost_usd: number;
  projected_monthly_cost_usd: number;
  projected_savings_usd_month: number;
  query_request_savings_usd_month: number;
  live_bytes: number;
  dead_bytes: number;
  metadata_bytes: number;
  orphan_bytes?: number | null;
  listed_bytes?: number | null;
  reclaimable_bytes: number;
  by_operation: CostByOperation[];
  last_full_cost_scan_at?: string | null;
  scan_at?: string | null;
}
