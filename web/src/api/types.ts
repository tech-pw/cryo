export type UUID = string;
export type RoleToken = "viewer" | "operator" | "admin";
export type EngineType = "trino" | "spark_emr" | "native";
export type Mode = "light" | "standard" | "deep" | "manifest_only";
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
  maintenance_schema_names: string[];
  created_at: string;
  updated_at: string;
}

export type SourceWrite = Pick<
  SourceRead,
  "name" | "type" | "config" | "enabled" | "maintenance_schema_names"
> & {
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
  query_log_config?: JsonObject | null;
  query_log_retention_days?: number | null;
  created_at: string;
  updated_at: string;
}

export type DestinationWrite = Pick<
  DestinationRead,
  | "name"
  | "engine"
  | "config"
  | "enabled"
  | "offpeak_start"
  | "offpeak_end"
  | "query_log_config"
  | "query_log_retention_days"
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
  is_default?: boolean;
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
  catalog_name?: string | null;
  source_name?: string | null;
  qualified_table_name?: string | null;
  location?: string | null;
  first_seen_at: string;
  is_active: boolean;
  consecutive_scan_failures?: number;
  last_scan_error?: string | null;
  last_scan_error_at?: string | null;
  owner_email?: string | null;
  stats?: TableStatsRead | null;
  effective_preset?: string | null;
}

export interface TableAlertSubscriptionRead {
  id: UUID;
  table_id: UUID;
  email: string;
  created_at: string;
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
  column_type?: string | null;
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

export interface AWSCostInsightConfigRead {
  id: UUID;
  source_id: UUID;
  cur_bucket?: string | null;
  cur_prefix?: string | null;
  cur_report_name?: string | null;
  fixture_path?: string | null;
  inventory_bucket?: string | null;
  inventory_prefix?: string | null;
  inventory_format: string;
  access_key_id?: string | null;
  enabled: boolean;
  last_ingested_at?: string | null;
  last_ingested_through?: string | null;
  last_ingest_ok?: boolean | null;
  last_ingest_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AWSCostInsightConfigWrite {
  cur_bucket?: string | null;
  cur_prefix?: string | null;
  cur_report_name?: string | null;
  fixture_path?: string | null;
  inventory_bucket?: string | null;
  inventory_prefix?: string | null;
  inventory_format?: string;
  access_key_id?: string | null;
  secret_access_key?: string | null;
  enabled?: boolean;
}

export interface AWSCostInsightConfigPatch {
  cur_bucket?: string | null;
  cur_prefix?: string | null;
  cur_report_name?: string | null;
  fixture_path?: string | null;
  inventory_bucket?: string | null;
  inventory_prefix?: string | null;
  inventory_format?: string;
  access_key_id?: string | null;
  secret_access_key?: string | null;
  enabled?: boolean;
}

export interface S3LogConfigRead {
  id: UUID;
  source_id: UUID;
  bucket: string;
  region?: string | null;
  access_key_id?: string | null;
  logging_status: "enabled" | "disabled" | "unknown";
  log_target_bucket?: string | null;
  log_target_prefix?: string | null;
  manual_override: boolean;
  enabled: boolean;
  last_checked_at?: string | null;
  last_ingested_at?: string | null;
  last_ingested_date?: string | null;
  last_error?: string | null;
  last_error_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface S3LogConfigPatch {
  region?: string | null;
  access_key_id?: string | null;
  secret_access_key?: string | null;
  log_target_bucket?: string | null;
  log_target_prefix?: string | null;
  manual_override?: boolean;
  enabled?: boolean;
}

export interface S3LogConfigCheckResponse {
  logging_status: "enabled" | "disabled" | "unknown";
  log_target_bucket?: string | null;
  log_target_prefix?: string | null;
  error?: string | null;
}

export interface S3LogConfigProbeResponse {
  ok: boolean;
  files_found: number;
  truncated: boolean;
  error?: string | null;
}

export interface S3LogIngestResponse {
  bucket: string;
  rows_ingested: number;
  tables_matched: number;
  unmatched_requests: number;
  through_date?: string | null;
  error?: string | null;
}

export interface S3LogProvisionRequest {
  target_bucket?: string | null;
}

export interface S3LogProvisionResponse {
  ok: boolean;
  log_target_bucket?: string | null;
  log_target_prefix?: string | null;
  error?: string | null;
}

export interface S3LogTeardownRequest {
  revert_policy?: boolean;
}

export interface S3LogTeardownResponse {
  ok: boolean;
  policy_reverted: boolean;
  warnings: string[];
  error?: string | null;
}

export interface AWSCostIngestResponse {
  rows_ingested: number;
  files_processed: number;
  through_date?: string | null;
  inventory_date?: string | null;
  error?: string | null;
}

export interface CostByBucketItem {
  bucket: string;
  total_cost_usd: number;
  line_count: number;
  last_usage_date?: string | null;
}

export interface CostByBucketResponse {
  source_id: UUID;
  start?: string | null;
  end?: string | null;
  total_cost_usd: number;
  items: CostByBucketItem[];
}

export interface CostDailySeriesItem {
  usage_date: string;
  total_cost_usd: number;
}

export interface CostDailySeriesResponse {
  source_id: UUID;
  items: CostDailySeriesItem[];
}

export interface SizeByPrefixItem {
  table_prefix: string;
  size_bytes: number;
  object_count: number;
  last_modified?: string | null;
}

export interface SizeByPrefixResponse {
  source_id: UUID;
  inventory_date?: string | null;
  items: SizeByPrefixItem[];
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
  provenance: "assignment" | "rule" | "default" | "none";
  rule_id?: UUID | null;
}

export interface RenderedStatementRead {
  action: string;
  engine: EngineType;
  sql: string;
  payload?: JsonObject;
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
  scheduled_at?: string | null;
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

export interface CompactionCandidateRead {
  partition_path: string;
  partition_size_gb: number;
  small_files: number;
  query_freq_per_day: number;
  p95_latency_ms: number;
  score: number;
  priority: "HIGH" | "MED" | "LOW" | string;
  savings_estimate_usd: number;
  reasons: string[];
}

export interface CompactionCandidatesResponse {
  table_id: UUID;
  candidates: CompactionCandidateRead[];
  generated_at: string;
}

export interface UnionScoreRead {
  target_table_id: string;
  candidate_table_id: string;
  score: number;
  overlap_columns: string[];
  union_sql: string;
  rationale: string;
  candidate_schema: string;
  candidate_table_name: string;
  candidate_schema_name: string;
}

export interface UnionableResponse {
  target_table_id: UUID;
  results: UnionScoreRead[];
  generated_at: string;
}

export interface SkillsetRead {
  id: string;
  name: string;
  version: string;
  prompts: Record<string, string>;
  tools: JsonObject[];
  created_at: string;
  is_active: boolean;
  metrics: Record<string, number>;
}

export interface SkillsetListResponse {
  skillsets: SkillsetRead[];
  generated_at: string;
}

export interface SkillsetWrite {
  name: string;
  version: string;
  prompts: Record<string, string>;
  tools: JsonObject[];
  activate: boolean;
}

export interface SkillsetRollbackRequest {
  version: string;
}

export interface SkillsetActivateRequest {
  version: string;
}

export interface SkillsetPromoteRequest {
  source_version: string;
  target_version: string;
  activate?: boolean;
}

export interface SkillsetOutcomeWrite {
  prompt_name: string;
  recommendation: string;
  decision: "accepted" | "rejected" | "pending";
  score?: number | null;
  table_id?: UUID | null;
}

export interface SkillsetOutcomeRead {
  id: UUID;
  skillset_id: UUID;
  recorded_at: string;
  table_id?: UUID | null;
  prompt_name: string;
  recommendation: string;
  decision: string;
  score?: number | null;
}

export interface LLMObservationRead {
  id: UUID;
  recorded_at: string;
  provider_type: string;
  model_id: string;
  feature: string;
  prompt_name?: string | null;
  prompt_hash: string;
  skillset_id?: UUID | null;
  skillset_name?: string | null;
  skillset_version?: string | null;
  run_id?: UUID | null;
  table_id?: UUID | null;
  outcome_id?: UUID | null;
  latency_ms?: number | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd?: number | null;
  status: string;
  budget_denied: boolean;
  error?: string | null;
  extra: JsonObject;
}

export interface LLMObservationListResponse {
  items: LLMObservationRead[];
  total: number;
}

export interface RunFailureDiagnosisRead {
  id: UUID;
  run_id: UUID;
  job_id?: UUID | null;
  table_id?: UUID | null;
  diagnosis_class: string;
  confidence: number;
  summary?: string | null;
  runbook_step?: string | null;
  evidence: string[];
  feedback?: "up" | "down" | null;
  feedback_note?: string | null;
  diagnosed_at: string;
  skillset_id?: UUID | null;
  observation_id?: UUID | null;
}

export interface RunFailureDiagnosisFeedbackWrite {
  feedback: "up" | "down";
  note?: string | null;
}

export interface TableFilterTranslationRequest {
  query: string;
  use_llm?: boolean;
}

export interface TableFilterTranslationResponse {
  filter: JsonObject;
  chips: string[];
  warnings: string[];
  errors: string[];
  used_llm: boolean;
  provider?: string | null;
  model?: string | null;
  observation_id?: UUID | null;
}

export interface PlanNarrationRequest {
  table_ref: string;
  notes: string[];
  operations: string[];
  sql_statements: string[];
}

export interface PlanNarrationResponse {
  deterministic_summary: string;
  llm_narration?: string | null;
  used_llm: boolean;
  observation_id?: UUID | null;
}

export interface AnomalyHypothesisRead {
  title: string;
  confidence: number;
  because: string;
}

export interface AnomalyRootCauseBriefRead {
  table_id: UUID;
  evidence: string[];
  hypotheses: AnomalyHypothesisRead[];
  used_llm: boolean;
  observation_id?: UUID | null;
}

export interface ResourcePredictionRead {
  table_id: UUID;
  predicted_p50_seconds: number;
  predicted_p90_seconds: number;
  recommended_cpu_cores: number;
  recommended_memory_gb: number;
  confidence: number;
  samples: number;
  shadow_mode: boolean;
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
  get_requests_30d?: number | null;
  put_requests_30d?: number | null;
  list_requests_30d?: number | null;
  request_cost_usd_month?: number | null;
  request_cost_pricing_source?: string | null;
}

// ============================================================================
// Plan Verifier (arXiv:2510.09567, proof-carrying plans)
// ============================================================================

export type PlanViolationSeverity = "critical" | "warning";

export interface PartitionPredicateTerm {
  base_column: string;
  transform: string;
  value?: unknown;
  col_type?: string;
  transform_param?: number | null;
  partition_field?: string | null;
  unsupported_reason?: string | null;
}

export interface PartitionPredicate {
  terms: PartitionPredicateTerm[];
  unsupported_reason?: string | null;
}

export interface PlanOperation {
  type: OperationType | "compute_statistics";
  params: Record<string, unknown>;
  predicates?: PartitionPredicate[] | null;
}

export interface MaintenancePlan {
  table_ref: string;
  mode: Mode;
  operations: PlanOperation[];
  notes?: string[];
}

export interface PlanViolation {
  invariant: string;
  severity: PlanViolationSeverity;
  message: string;
  location: string;
}

export interface PlanVerdict {
  verified: boolean;
  plan_hash: string;
  verifier_version: string;
  invariants_checked: string[];
  violations: PlanViolation[];
}

export interface PlanVerifyRequest {
  plan: MaintenancePlan;
  signature?: string | null;
}

// ============================================================================
// Observation Lakehouse (SANER 2026, arXiv:2512.02795)
// ============================================================================

export interface ObservationEvent {
  id: string;
  recorded_at: string;
  run_id?: string | null;
  event_type: string;
  level: string;
  table_id?: string | null;
  subject: string;
  payload: Record<string, unknown>;
  correlation_id?: string | null;
  actor?: string | null;
}

export interface ObservationSearchResponse {
  items: ObservationEvent[];
  total: number;
}

export interface ObservationStatsResponse {
  total_files: number;
  total_bytes: number;
  oldest_event: string | null;
  newest_event: string | null;
}

export interface ObservationCompactResponse {
  files_merged: number;
  rows: number;
  bytes_in: number;
  bytes_out: number;
}

// ============================================================================
// Puffin-backed column sketches (FORMATS '26, arXiv:2605.30308)
// ============================================================================

export interface ColumnSketchBlob {
  path: string;
  size: number;
  offset: number;
  sketch_type: string;
}

export interface ColumnSketchRead {
  field_type?: string | null;
  ndv_estimate: number;
  quantiles: Record<string, number>;
  sketch_count: number;
  puffin_blobs: ColumnSketchBlob[];
}

export interface ColumnStatsResponse {
  table_id: UUID;
  columns: Record<string, ColumnSketchRead>;
}

// ============================================================================
// Silent-data-loss detector (arXiv:2604.20081)
// ============================================================================

export interface SilentDataLossReportRead {
  table_id: UUID;
  snapshot_id: number | null;
  orphan_data_files: string[];
  orphan_size_bytes: number;
  missing_files: string[];
  detected_at: string;
  root_cause: string;
  is_clean: boolean;
}

export interface SilentDataLossRepairResponse {
  table_id: UUID;
  audit_log_id: number | null;
  status: string;
  message: string;
}

// ============================================================================
// Variance-aware QPP (CLOUD 2026, arXiv:2606.03464)
// ============================================================================

export interface QPPFeaturesPayload {
  partition_count: number;
  files_scanned: number;
  is_cached: boolean;
  cotenant_load_pct: number;
  hour_of_day: number;
  day_of_week: number;
}

export interface QPPPredictRequest {
  table_id: UUID;
  query_text?: string | null;
  features: QPPFeaturesPayload;
}

export interface QPPPredictResponse {
  table_id: UUID;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  confidence: number;
  admit: boolean;
  target_p95_ms: number;
  exceeded_tolerance: boolean;
}

export interface QPPObservationPayload {
  table_id: UUID;
  actual_p95_ms: number;
  features: QPPFeaturesPayload;
}

export interface QPPConfidenceResponse {
  observations: number;
  confidence: number;
  min_observations: number;
  using_trained_model: boolean;
  qpp_enabled: boolean;
}
