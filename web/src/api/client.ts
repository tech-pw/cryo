import type {
  AuditEntry,
  CostDashboard,
  DatalakeDashboard,
  DestinationRead,
  DestinationWrite,
  EffectivePresetResponse,
  EngineType,
  JobRead,
  JsonObject,
  MaintenancePreviewResponse,
  MaintenanceRunResponse,
  Mode,
  OperationType,
  PaginatedTables,
  PartitionRead,
  PresetRead,
  PresetRuleRead,
  PresetSimulationResponse,
  PresetWrite,
  RoleToken,
  RunRead,
  ScannerConfigRead,
  ScannerConfigUpdate,
  ScannerStatusRead,
  ScanTriggerRequest,
  ScanTriggerResponse,
  SourceRead,
  SourceTestResult,
  SourceWrite,
  AnomalyResponse,
  AutoCompCompareResponse,
  AWSConnectionTestResponse,
  AWSCostIngestResponse,
  AWSCostInsightConfigPatch,
  AWSCostInsightConfigRead,
  AWSCostInsightConfigWrite,
  AWSIntegrationRead,
  AWSIntegrationWrite,
  AWSUsageResponse,
  CostByBucketItem,
  CostByBucketResponse,
  CostDailySeriesItem,
  CostDailySeriesResponse,
  SizeByPrefixItem,
  SizeByPrefixResponse,
  BanditStatusResponse,
  CopilotAskResponse,
  CopilotNaturalResponse,
  CopilotQuestion,
  CopilotSearchResponse,
  CostModelEvaluateResponse,
  DataQualityProfileRead,
  ExplainHealthResponse,
  HealthClassifierResponse,
  PiiClassificationResponse,
  SimilarTablesResponse,
  CompactionCandidatesResponse,
  CompactionCandidateRead,
  UnionableResponse,
  UnionScoreRead,
  SkillsetListResponse,
  SkillsetRead,
  SkillsetWrite,
  SkillsetRollbackRequest,
  SkillsetOutcomeWrite,
  SkillsetOutcomeRead,
  TableAlertSubscriptionRead,
  TableCostRead,
  TableRead,
  UUID,
  PlanVerdict,
  PlanVerifyRequest,
  ObservationSearchResponse,
  ObservationStatsResponse,
  ObservationCompactResponse,
  ColumnStatsResponse,
  ColumnSketchRead,
  ColumnSketchBlob,
  SilentDataLossReportRead,
  SilentDataLossRepairResponse,
  QPPPredictRequest,
  QPPPredictResponse,
  QPPFeaturesPayload,
  QPPObservationPayload,
  QPPConfidenceResponse
} from "./types";
import { serializePredicate, type Predicate } from "../lib/predicate";

export const API_PREFIX = "/api/v1";

export interface ApiOptions {
  token: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  signal?: AbortSignal;
}

export const apiFetch = async <T>(
  path: string,
  { token }: ApiOptions,
  options: RequestOptions = {}
): Promise<T> => {
  const url = new URL(`${API_PREFIX}${path}`, window.location.origin);
  Object.entries(options.query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  });

  const response = await fetch(`${url.pathname}${url.search}`, {
    method: options.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" })
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal
  });

  if (response.status === 204) return undefined as T;

  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null ? (payload as JsonObject).detail : payload;
    throw new ApiError(response.status, String(detail || response.statusText), detail);
  }

  return payload as T;
};

export const listTables = (
  api: ApiOptions,
  args: { predicate?: Predicate | null; sort?: string; limit?: number; offset?: number; signal?: AbortSignal }
) =>
  apiFetch<PaginatedTables>("/tables", api, {
    query: {
      filter: serializePredicate(args.predicate),
      sort: args.sort,
      limit: args.limit ?? 50,
      offset: args.offset ?? 0
    },
    signal: args.signal
  });

const TABLE_IDS_PAGE_SIZE = 500;

export const fetchAllMatchingTableIds = async (
  api: ApiOptions,
  args: { predicate?: Predicate | null; sort?: string; total: number; signal?: AbortSignal }
): Promise<UUID[]> => {
  const ids: UUID[] = [];
  for (let offset = 0; offset < args.total; offset += TABLE_IDS_PAGE_SIZE) {
    const batch = await listTables(api, {
      predicate: args.predicate,
      sort: args.sort,
      limit: Math.min(TABLE_IDS_PAGE_SIZE, args.total - offset),
      offset,
      signal: args.signal
    });
    ids.push(...batch.items.map((table) => table.id));
  }
  return ids;
};

export const getTable = (api: ApiOptions, id: UUID) => apiFetch<TableRead>(`/tables/${id}`, api);
export const patchTable = (api: ApiOptions, id: UUID, body: { owner_email?: string | null }) =>
  apiFetch<TableRead>(`/tables/${id}`, api, { method: "PATCH", body });
export const listTableAlertSubscriptions = (api: ApiOptions, id: UUID) =>
  apiFetch<TableAlertSubscriptionRead[]>(`/tables/${id}/alert-subscriptions`, api);
export const createTableAlertSubscription = (api: ApiOptions, id: UUID, email: string) =>
  apiFetch<TableAlertSubscriptionRead>(`/tables/${id}/alert-subscriptions`, api, {
    method: "POST",
    body: { email }
  });
export const deleteTableAlertSubscription = (api: ApiOptions, id: UUID, subscriptionId: UUID) =>
  apiFetch<void>(`/tables/${id}/alert-subscriptions/${subscriptionId}`, api, { method: "DELETE" });
export const getTablePartitions = (api: ApiOptions, id: UUID) =>
  apiFetch<{ items: PartitionRead[] }>(`/tables/${id}/partitions`, api).then((payload) => payload.items);
export const getTableAudit = (api: ApiOptions, id: UUID) =>
  apiFetch<{ items: AuditEntry[] }>(`/tables/${id}/audit`, api).then((payload) => payload.items);
export const getTableHistory = (api: ApiOptions, id: UUID) =>
  apiFetch<{ items: Array<Record<string, unknown>> }>(`/tables/${id}/history`, api).then((payload) => payload.items);

export interface HealthTrendPoint {
  timestamp: string;
  grade: string;
  score: number;
  small_files: number;
  total_size_gb: number;
  snapshot_count: number | null;
  projected_savings_usd_month: number | null;
}

export interface TableHealthTrends {
  table_id: UUID;
  table_name: string;
  current_grade: string;
  current_score: number;
  trend_points: HealthTrendPoint[];
  degradation_rate: number | null;
  days_to_critical: number | null;
}

export const getTableHealthTrends = (api: ApiOptions, id: UUID, days = 30) =>
  apiFetch<TableHealthTrends>(`/tables/${id}/health-trends?days=${days}`, api);

export const getEffectivePreset = (api: ApiOptions, id: UUID) =>
  apiFetch<EffectivePresetResponse>(`/tables/${id}/effective-preset`, api);

export const listSources = (api: ApiOptions) => apiFetch<SourceRead[]>("/sources", api);
export const createSource = (api: ApiOptions, body: SourceWrite) =>
  apiFetch<SourceRead>("/sources", api, { method: "POST", body });
export const patchSource = (api: ApiOptions, id: UUID, body: Partial<SourceWrite>) =>
  apiFetch<SourceRead>(`/sources/${id}`, api, { method: "PATCH", body });
export const testSource = (
  api: ApiOptions,
  body: { type: string; config: Record<string, unknown>; source_id?: UUID }
) => apiFetch<SourceTestResult>("/sources/test", api, { method: "POST", body });

export interface SourceNamespacesResult {
  ok: boolean;
  namespaces: string[];
  error?: string | null;
}

export const getSourceNamespaces = (api: ApiOptions, id: UUID) =>
  apiFetch<SourceNamespacesResult>(`/sources/${id}/namespaces`, api);

export interface SourceNamespaceTablesResult {
  ok: boolean;
  tables: string[];
  error?: string | null;
}

export const getSourceNamespaceTables = (api: ApiOptions, id: UUID, namespace: string) =>
  apiFetch<SourceNamespaceTablesResult>(
    `/sources/${id}/namespaces/${encodeURIComponent(namespace)}/tables`,
    api
  );

// Permanent delete -- unlike patchSource({ enabled: false }), this cannot be undone.
export const purgeSource = (api: ApiOptions, id: UUID) =>
  apiFetch<void>(`/sources/${id}/purge`, api, { method: "DELETE" });

export const listDestinations = (api: ApiOptions) => apiFetch<DestinationRead[]>("/destinations", api);
export const createDestination = (api: ApiOptions, body: DestinationWrite) =>
  apiFetch<DestinationRead>("/destinations", api, { method: "POST", body });
export const patchDestination = (api: ApiOptions, id: UUID, body: Partial<DestinationWrite>) =>
  apiFetch<DestinationRead>(`/destinations/${id}`, api, { method: "PATCH", body });
export const testDestination = (api: ApiOptions, id: UUID) =>
  apiFetch<Record<string, unknown>>(`/destinations/${id}/test`, api, { method: "POST" });

// Permanent delete -- unlike patchDestination({ enabled: false }), this cannot be undone. Fails
// with a 409 if presets/maintenance runs still reference this engine.
export const purgeDestination = (api: ApiOptions, id: UUID) =>
  apiFetch<void>(`/destinations/${id}/purge`, api, { method: "DELETE" });

export interface DestinationTestResult {
  ok: boolean;
  capacity?: number | null;
  used?: number | null;
  available?: number | null;
  message?: string | null;
}

export const testDestinationConfig = (
  api: ApiOptions,
  body: { engine: string; config: Record<string, unknown>; destination_id?: UUID }
) => apiFetch<DestinationTestResult>("/destinations/test", api, { method: "POST", body });

export const listPresets = (api: ApiOptions) => apiFetch<PresetRead[]>("/presets", api);
export const createPreset = (api: ApiOptions, body: PresetWrite) =>
  apiFetch<PresetRead>("/presets", api, { method: "POST", body });
export const patchPreset = (api: ApiOptions, id: UUID, body: Partial<PresetWrite>) =>
  apiFetch<PresetRead>(`/presets/${id}`, api, { method: "PATCH", body });
export const deletePreset = (api: ApiOptions, id: UUID) =>
  apiFetch<void>(`/presets/${id}`, api, { method: "DELETE" });
export const listPresetRules = (api: ApiOptions, id: UUID) =>
  apiFetch<PresetRuleRead[]>(`/presets/${id}/rules`, api);
export const createPresetRule = (api: ApiOptions, id: UUID, body: { predicate: Predicate; enabled: boolean }) =>
  apiFetch<PresetRuleRead>(`/presets/${id}/rules`, api, { method: "POST", body });
export const deletePresetRule = (api: ApiOptions, presetId: UUID, ruleId: UUID) =>
  apiFetch<void>(`/presets/${presetId}/rules/${ruleId}`, api, { method: "DELETE" });
export const simulatePreset = (api: ApiOptions, id: UUID) =>
  apiFetch<PresetSimulationResponse>(`/presets/${id}/simulate`, api, { method: "POST" });

export const assignTables = (
  api: ApiOptions,
  body: { selection: { mode: "ids"; ids: UUID[] } | { mode: "filter"; filter: Predicate | null }; preset_id: UUID }
) => apiFetch<{ affected: number }>("/assignments", api, { method: "POST", body });

export const unassignTables = (
  api: ApiOptions,
  body: { selection: { mode: "ids"; ids: UUID[] } | { mode: "filter"; filter: Predicate | null } }
) => apiFetch<{ affected: number }>("/assignments", api, { method: "DELETE", body });

export const previewMaintenance = (
  api: ApiOptions,
  body: {
    engine: EngineType;
    mode: Mode;
    params: JsonObject;
    tables: Array<{
      schema_name: string;
      table_name: string;
      catalog?: string;
      stats?: JsonObject;
      partitions?: JsonObject[];
    }>;
  }
) => apiFetch<MaintenancePreviewResponse>("/maintenance/preview", api, { method: "POST", body });

export const runMaintenance = (
  api: ApiOptions,
  body:
    | {
        table_ids: UUID[];
        destination_id: UUID;
        preset_id: UUID;
        mode: Mode;
        trigger?: string;
        /** One-off future run time, distinct from a Preset's recurring schedule_cron. Must be
         * in the future -- the API rejects a past timestamp with 400. */
        scheduled_at?: string;
      }
    | {
        selection: { mode: "filter"; filter: Predicate | null };
        destination_id: UUID;
        preset_id: UUID;
        mode: Mode;
        trigger?: string;
        scheduled_at?: string;
      }
) => apiFetch<MaintenanceRunResponse>("/maintenance/run", api, { method: "POST", body });

export const runManifestRewrite = (
  api: ApiOptions,
  body:
    | { table_ids: UUID[]; destination_id: UUID; preset_id: UUID; trigger?: string }
    | {
        selection: { mode: "filter"; filter: Predicate | null };
        destination_id: UUID;
        preset_id: UUID;
        trigger?: string;
      }
) => apiFetch<MaintenanceRunResponse>("/maintenance/manifest-rewrite", api, { method: "POST", body });

export const listRuns = (
  api: ApiOptions,
  args: { kind?: string; source_id?: UUID; limit?: number; offset?: number } = {}
) =>
  apiFetch<RunRead[]>("/runs", api, {
    query: {
      kind: args.kind,
      source_id: args.source_id,
      limit: args.limit,
      offset: args.offset
    }
  });
export const getRun = (api: ApiOptions, id: UUID) => apiFetch<RunRead>(`/runs/${id}`, api);
export const getRunJobs = (api: ApiOptions, id: UUID) =>
  apiFetch<{ jobs: JobRead[] }>(`/runs/${id}/jobs`, api).then((payload) => payload.jobs);
export const cancelRun = (api: ApiOptions, id: UUID) =>
  apiFetch<RunRead>(`/runs/${id}/cancel`, api, { method: "POST" });
export const getJob = (api: ApiOptions, id: UUID) => apiFetch<JobRead>(`/jobs/${id}`, api);

export const getDatalakeDashboard = (
  api: ApiOptions,
  args: { top_limit?: number; drift_limit?: number } = {}
) =>
  apiFetch<RawDatalakeDashboard>("/dashboards/datalake", api, {
    query: { top_limit: args.top_limit, drift_limit: args.drift_limit }
  }).then(normalizeDashboard);
export const getSchemaCost = (api: ApiOptions) =>
  apiFetch<RawSchemaCost>("/dashboards/schema-cost", api).then((payload) =>
    payload.items.map((item) => ({
      schema_name: item.schema_name,
      cost_usd: item.estimated_monthly_s3_cost_usd,
      size_gb: item.total_size_gb
    }))
  );

export const wsRunUrl = (runId: UUID, token: string): string => {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL(`${proto}//${window.location.host}${API_PREFIX}/ws/runs/${runId}`);
  url.searchParams.set("token", token);
  return url.toString();
};

interface RawDatalakeDashboard {
  totals: {
    total_tables: number;
    total_size_gb: number;
    small_files_count: number;
    estimated_monthly_s3_cost_usd: number;
    projected_monthly_s3_cost_usd?: number;
    projected_savings_usd_month?: number;
    reclaimable_bytes?: number;
    needs_compaction_count: number;
  };
  grade_distribution: Array<{ grade: string; count: number }>;
  top_offenders: Array<{
    table_id: UUID;
    schema_name: string;
    table_name: string;
    grade?: string | null;
    overall_score?: number | null;
    total_size_gb?: number;
    total_files?: number;
    small_files_count?: number;
    estimated_monthly_s3_cost_usd?: number | null;
  }>;
  drift: Array<Record<string, unknown>>;
}

interface RawSchemaCost {
  items: Array<{
    schema_name: string;
    total_size_gb: number;
    estimated_monthly_s3_cost_usd: number;
  }>;
}

const normalizeDashboard = (payload: RawDatalakeDashboard): DatalakeDashboard => ({
  totals: {
    tables: payload.totals.total_tables,
    size_gb: payload.totals.total_size_gb,
    small_files: payload.totals.small_files_count,
    monthly_cost_usd: payload.totals.estimated_monthly_s3_cost_usd,
    projected_monthly_cost_usd: payload.totals.projected_monthly_s3_cost_usd,
    projected_savings_usd_month: payload.totals.projected_savings_usd_month,
    reclaimable_bytes: payload.totals.reclaimable_bytes,
    needs_compaction: payload.totals.needs_compaction_count
  },
  grade_distribution: payload.grade_distribution,
  top_offenders: payload.top_offenders.map((item) => ({
    id: item.table_id,
    source_id: "",
    schema_name: item.schema_name,
    table_name: item.table_name,
    first_seen_at: "",
    is_active: true,
    stats: {
      grade: item.grade ?? undefined,
      overall_score: item.overall_score ?? undefined,
      total_size_gb: item.total_size_gb,
      total_files: item.total_files,
      small_files_count: item.small_files_count,
      estimated_monthly_s3_cost_usd: item.estimated_monthly_s3_cost_usd ?? undefined
    }
  })),
  drift: payload.drift
});

export const getCostDashboard = (api: ApiOptions) =>
  apiFetch<CostDashboard>("/dashboards/cost", api);

export const getTableCost = (api: ApiOptions, id: UUID) =>
  apiFetch<TableCostRead>(`/tables/${id}/cost`, api);

export const getScannerConfig = (api: ApiOptions) =>
  apiFetch<ScannerConfigRead>("/scanner/config", api);

export const patchScannerConfig = (api: ApiOptions, body: ScannerConfigUpdate) =>
  apiFetch<ScannerConfigRead>("/scanner/config", api, { method: "PATCH", body });

export const getScannerStatus = (api: ApiOptions) =>
  apiFetch<ScannerStatusRead>("/scanner/status", api);

export const triggerScan = (api: ApiOptions, body: ScanTriggerRequest = {}) =>
  apiFetch<ScanTriggerResponse>("/scans", api, {
    method: "POST",
    body: {
      scope: body.scope ?? "all",
      source_id: body.source_id,
      table_ids: body.table_ids ?? [],
      table_names: body.table_names ?? [],
      schema_names: body.schema_names ?? [],
      selection: body.selection,
      full_scan: body.full_scan ?? true,
      cost_full_scan: body.cost_full_scan ?? false,
      num_chunks: body.num_chunks,
      trigger: body.trigger ?? "web"
    }
  });

export const triggerCostScan = (api: ApiOptions, body: ScanTriggerRequest = {}) =>
  triggerScan(api, {
    ...body,
    scope: body.scope ?? "all",
    cost_full_scan: body.cost_full_scan ?? true,
    trigger: body.trigger ?? "web-cost"
  });

export const savingsOpportunityFilter = {
  field: "projected_savings_usd_month",
  op: ">",
  value: 0
} as const;

// AI Providers
export interface AIProviderRead {
  id: UUID;
  name: string;
  provider_type: string;
  model: string;
  config: Record<string, unknown>;
  enabled: boolean;
  is_default: boolean;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
}

export interface AIProviderWrite {
  name: string;
  provider_type: string;
  api_key?: string;  // Optional for updates (leave blank to keep existing)
  model: string;
  config?: Record<string, unknown>;
  enabled?: boolean;
  is_default?: boolean;
}

export interface AIProviderConfigField {
  name: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  default?: string;
}

export interface AIProviderType {
  type: string;
  name: string;
  models: string[];
  default_model: string;
  config_fields?: AIProviderConfigField[];
}

export interface AIProviderTestResult {
  success: boolean;
  response?: string;
  error?: string;
  model?: string;
  tokens_used?: number;
}

export const listAIProviders = (api: ApiOptions) =>
  apiFetch<AIProviderRead[]>("/ai/providers", api);

export const listAIProviderTypes = (api: ApiOptions) =>
  apiFetch<AIProviderType[]>("/ai/providers/types", api);

export const createAIProvider = (api: ApiOptions, body: AIProviderWrite) =>
  apiFetch<AIProviderRead>("/ai/providers", api, { method: "POST", body });

export const patchAIProvider = (api: ApiOptions, id: UUID, body: Partial<AIProviderWrite>) =>
  apiFetch<AIProviderRead>(`/ai/providers/${id}`, api, { method: "PATCH", body });

export const deleteAIProvider = (api: ApiOptions, id: UUID) =>
  apiFetch<void>(`/ai/providers/${id}`, api, { method: "DELETE" });

export const testAIProvider = (api: ApiOptions, id: UUID, prompt?: string) =>
  apiFetch<AIProviderTestResult>(`/ai/providers/${id}/test`, api, {
    method: "POST",
    body: { prompt: prompt ?? "Say hello in one sentence." }
  });

export const testAIProviderConfig = (
  api: ApiOptions,
  body: {
    provider_type: string;
    api_key: string;
    model: string;
    config?: Record<string, unknown>;
    prompt?: string;
    provider_id?: UUID;
  }
) => apiFetch<AIProviderTestResult>("/ai/providers/test", api, { method: "POST", body });

// Slack Configs
export interface SlackConfigRead {
  id: UUID;
  name: string;
  channel: string | null;
  daily_report_enabled: boolean;
  daily_report_cron: string;
  daily_report_use_ai: boolean;
  enabled: boolean;
  is_default: boolean;
  has_webhook: boolean;
  created_at: string;
  updated_at: string;
}

export interface SlackConfigWrite {
  name: string;
  webhook_url?: string;  // Optional for updates (leave blank to keep existing)
  channel?: string | null;
  daily_report_enabled?: boolean;
  daily_report_cron?: string;
  daily_report_use_ai?: boolean;
  enabled?: boolean;
  is_default?: boolean;
}

export interface SlackTestResult {
  status: string;
  message: string;
}

export const listSlackConfigs = (api: ApiOptions) =>
  apiFetch<SlackConfigRead[]>("/slack/configs", api);

export const createSlackConfig = (api: ApiOptions, body: SlackConfigWrite) =>
  apiFetch<SlackConfigRead>("/slack/configs", api, { method: "POST", body });

export const patchSlackConfig = (api: ApiOptions, id: UUID, body: Partial<SlackConfigWrite>) =>
  apiFetch<SlackConfigRead>(`/slack/configs/${id}`, api, { method: "PATCH", body });

export const deleteSlackConfig = (api: ApiOptions, id: UUID) =>
  apiFetch<void>(`/slack/configs/${id}`, api, { method: "DELETE" });

export const testSlackConfig = (api: ApiOptions, id: UUID) =>
  apiFetch<SlackTestResult>(`/slack/configs/${id}/test`, api, { method: "POST" });

export const testSlackConfigValues = (
  api: ApiOptions,
  body: { webhook_url: string; channel?: string | null; config_id?: UUID }
) => apiFetch<SlackTestResult>("/slack/test", api, { method: "POST", body });

export const deriveCostDashboard = (tables: TableRead[]): CostDashboard => {
  const bySchema = new Map<string, { current_usd: number; projected_usd: number; savings_usd: number }>();
  const ops: Record<OperationType, number> = {
    optimize: 0,
    expire_snapshots: 0,
    remove_orphan_files: 0,
    optimize_manifests: 0
  };
  let current = 0;
  let projected = 0;
  let savings = 0;
  let reclaimable = 0;
  let storage = 0;

  for (const table of tables) {
    const stats = table.stats;
    if (!stats) continue;
    const cur = stats.estimated_monthly_s3_cost_usd ?? 0;
    const proj = stats.projected_monthly_s3_cost_usd ?? cur;
    const sav = stats.projected_savings_usd_month ?? Math.max(0, cur - proj);
    current += cur;
    projected += proj;
    savings += sav;
    reclaimable += stats.reclaimable_bytes ?? 0;
    storage += (stats.total_size_gb ?? 0) * 1024 ** 3;
    const row = bySchema.get(table.schema_name) ?? { current_usd: 0, projected_usd: 0, savings_usd: 0 };
    row.current_usd += cur;
    row.projected_usd += proj;
    row.savings_usd += sav;
    bySchema.set(table.schema_name, row);
  }

  return {
    totals: {
      current_monthly_cost_usd: current,
      projected_monthly_cost_usd: projected,
      projected_savings_usd_month: savings,
      current_storage_bytes: storage,
      reclaimable_bytes: reclaimable
    },
    savings_by_operation: Object.entries(ops).map(([operation, savings_usd_month]) => ({
      operation: operation as OperationType,
      savings_usd_month,
      reclaimable_bytes: 0
    })),
    cost_by_schema: Array.from(bySchema.entries())
      .map(([schema_name, values]) => ({ schema_name, ...values }))
      .sort((a, b) => b.savings_usd - a.savings_usd),
    top_opportunities: [...tables]
      .sort(
        (a, b) =>
          (b.stats?.projected_savings_usd_month ?? 0) - (a.stats?.projected_savings_usd_month ?? 0)
      )
      .slice(0, 10)
  };
};

// ============================================================================
// Layout Optimization
// ============================================================================

export interface QueryPatternRead {
  column_name: string;
  filter_count: number;
  equality_filter_count: number;
  range_filter_count: number;
  join_count: number;
  projection_count: number;
  avg_selectivity?: number | null;
}

export interface TableQueryPatternsResponse {
  table_id: UUID;
  schema_name: string;
  table_name: string;
  total_queries: number;
  patterns: QueryPatternRead[];
  dominant_filter_column?: string | null;
  multi_column_filter_ratio: number;
}

export interface LayoutRecommendationRead {
  id: UUID;
  table_id: UUID;
  current_sort_order?: Array<{ column: string }> | null;
  recommended_strategy: string;
  recommended_columns: Array<{ column: string }>;
  estimated_prune_improvement?: number | null;
  current_prune_efficiency?: number | null;
  analysis_query_count: number;
  confidence: number;
  status: string;
  applied_at?: string | null;
  detail?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ClusteringPlanRequest {
  strategy?: string;
  columns?: string[] | null;
  partition_filter?: string | null;
  target_file_size_mb?: number;
  rewrite_all?: boolean;
}

export interface ClusteringPlanResponse {
  table_id: UUID;
  table_ref: string;
  strategy: string;
  columns: string[];
  spark_sql: string;
  spark_python: string;
  trino_sql?: string | null;
}

export interface PartitionEvolutionRecommendationRead {
  table_id: UUID;
  table_name: string;
  current_partition_spec: string;
  recommended_partition_spec: string;
  reason: string;
  confidence: number;
  estimated_pruning_improvement_pct: number;
  query_patterns_analyzed: number;
  cardinality_estimate: string;
  warnings: string[];
}

export const getTableQueryPatterns = (api: ApiOptions, tableId: UUID) =>
  apiFetch<TableQueryPatternsResponse>(`/tables/${tableId}/query-patterns`, api);

export const getPartitionEvolutionRecommendation = (
  api: ApiOptions,
  tableId: UUID,
  minQueryThreshold?: number
) =>
  apiFetch<PartitionEvolutionRecommendationRead | null>(
    `/advanced/partition-evolution/${tableId}`,
    api,
    { query: { min_query_threshold: minQueryThreshold } }
  );

export const getLayoutRecommendation = (api: ApiOptions, tableId: UUID, refresh?: boolean) =>
  apiFetch<LayoutRecommendationRead>(`/tables/${tableId}/layout-recommendation`, api, {
    query: { refresh: refresh ?? false }
  });

export const createClusteringPlan = (api: ApiOptions, tableId: UUID, body: ClusteringPlanRequest) =>
  apiFetch<ClusteringPlanResponse>(`/tables/${tableId}/clustering-plan`, api, {
    method: "POST",
    body
  });

export const listLayoutRecommendations = (
  api: ApiOptions,
  params?: { status?: string; min_confidence?: number; limit?: number }
) =>
  apiFetch<LayoutRecommendationRead[]>("/optimization/recommendations", api, {
    query: params
  });

export const updateRecommendationStatus = (api: ApiOptions, recId: UUID, status: string) =>
  apiFetch<LayoutRecommendationRead>(`/optimization/recommendations/${recId}`, api, {
    method: "PATCH",
    query: { status }
  });

export const ingestQueryTelemetry = (
  api: ApiOptions,
  body: { source_id: UUID; since_hours?: number; limit?: number }
) =>
  apiFetch<{ queries_ingested: number; patterns_updated: number }>("/telemetry/ingest", api, {
    method: "POST",
    body
  });

export const getTablePiiClassification = (api: ApiOptions, tableId: UUID) =>
  apiFetch<PiiClassificationResponse>(`/tables/${tableId}/pii-classification`, api);

export const getSimilarTables = (api: ApiOptions, tableId: UUID) =>
  apiFetch<SimilarTablesResponse>(`/tables/${tableId}/similar`, api);

export const getTableHealthClassification = (api: ApiOptions, tableId: UUID) =>
  apiFetch<HealthClassifierResponse>(`/tables/${tableId}/health-classification`, api);

export const getBanditStatus = (api: ApiOptions) =>
  apiFetch<BanditStatusResponse>("/governor/bandit/status", api);

export const getCostModelEvaluate = (api: ApiOptions) =>
  apiFetch<CostModelEvaluateResponse>("/ai/cost-model/evaluate", api);

export const getTableAnomalies = (api: ApiOptions, tableId: UUID) =>
  apiFetch<AnomalyResponse>(`/tables/${tableId}/anomalies`, api);

export const explainTableHealth = (api: ApiOptions, tableId: UUID, body?: { preset_name?: string }) =>
  apiFetch<ExplainHealthResponse>(`/tables/${tableId}/explain-health`, api, {
    method: "POST",
    body: body ?? {}
  });

export const getAutoCompCompare = (api: ApiOptions, tableId: UUID) =>
  apiFetch<AutoCompCompareResponse>(`/tables/${tableId}/autocomp/compare`, api);

export const getCopilotQuestions = (api: ApiOptions) =>
  apiFetch<CopilotQuestion[]>("/ai/copilot/questions", api);

export const askCopilotQuestion = (
  api: ApiOptions,
  body: { question_id: string; context?: Record<string, unknown> }
) => apiFetch<CopilotAskResponse>("/ai/copilot/ask", api, { method: "POST", body });

export const searchCopilot = (api: ApiOptions, body: { question: string; min_similarity?: number }) =>
  apiFetch<CopilotSearchResponse>("/ai/copilot/search", api, { method: "POST", body });

export const askCopilotNatural = (
  api: ApiOptions,
  body: { question: string; include_context?: boolean }
) => apiFetch<CopilotNaturalResponse>("/ai/copilot/natural", api, { method: "POST", body });

export const getDataQualityProfile = (api: ApiOptions, tableId: UUID, lookbackDays?: number) =>
  apiFetch<DataQualityProfileRead>(`/advanced/data-quality/${tableId}`, api, {
    query: { lookback_days: lookbackDays ?? 30 }
  });

export const getAwsIntegration = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<AWSIntegrationRead>(`/sources/${sourceId}/aws-integration`, api);

export const createAwsIntegration = (api: ApiOptions, sourceId: UUID, body: AWSIntegrationWrite) =>
  apiFetch<AWSIntegrationRead>(`/sources/${sourceId}/aws-integration`, api, { method: "POST", body });

export const patchAwsIntegration = (
  api: ApiOptions,
  sourceId: UUID,
  body: Partial<AWSIntegrationWrite>
) => apiFetch<AWSIntegrationRead>(`/sources/${sourceId}/aws-integration`, api, { method: "PATCH", body });

export const deleteAwsIntegration = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<void>(`/sources/${sourceId}/aws-integration`, api, { method: "DELETE" });

export const testAwsIntegration = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<AWSConnectionTestResponse>(`/sources/${sourceId}/aws-integration/test`, api, {
    method: "POST"
  });

export const getAwsUsage = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<AWSUsageResponse>(`/sources/${sourceId}/aws-usage`, api);

export const getAwsCostInsightConfig = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<AWSCostInsightConfigRead>(`/sources/${sourceId}/cost-insight-config`, api);

export const createAwsCostInsightConfig = (
  api: ApiOptions,
  sourceId: UUID,
  body: AWSCostInsightConfigWrite
) =>
  apiFetch<AWSCostInsightConfigRead>(`/sources/${sourceId}/cost-insight-config`, api, {
    method: "POST",
    body
  });

export const patchAwsCostInsightConfig = (
  api: ApiOptions,
  sourceId: UUID,
  body: AWSCostInsightConfigPatch
) =>
  apiFetch<AWSCostInsightConfigRead>(`/sources/${sourceId}/cost-insight-config`, api, {
    method: "PATCH",
    body
  });

export const deleteAwsCostInsightConfig = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<void>(`/sources/${sourceId}/cost-insight-config`, api, { method: "DELETE" });

export const ingestAwsCostInsightCur = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<AWSCostIngestResponse>(`/sources/${sourceId}/cost-insight/ingest-cur`, api, {
    method: "POST"
  });

export const ingestAwsCostInsightInventory = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<AWSCostIngestResponse>(`/sources/${sourceId}/cost-insight/ingest-inventory`, api, {
    method: "POST"
  });

export const getCostByBucket = (
  api: ApiOptions,
  sourceId: UUID,
  query?: { start?: string; end?: string; limit?: number }
) =>
  apiFetch<CostByBucketResponse>(`/sources/${sourceId}/cost-insight/by-bucket`, api, {
    query
  });

export const getCostDailySeries = (
  api: ApiOptions,
  sourceId: UUID,
  query?: { start?: string; end?: string }
) => apiFetch<CostDailySeriesResponse>(`/sources/${sourceId}/cost-insight/daily`, api, { query });

export const getSizeByPrefix = (
  api: ApiOptions,
  sourceId: UUID,
  query?: { inventory_date?: string; limit?: number }
) => apiFetch<SizeByPrefixResponse>(`/sources/${sourceId}/cost-insight/by-prefix`, api, { query });

/** Dashboard routes live under the same `/api/v1` prefix as everything else. */
const DASHBOARD_PREFIX = "/api/v1";

async function dashboardFetch<T>(path: string, api: ApiOptions): Promise<T> {
  const response = await fetch(`${DASHBOARD_PREFIX}${path}`, {
    headers: { Authorization: `Bearer ${api.token}` }
  });
  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null ? (payload as JsonObject).detail : payload;
    throw new ApiError(response.status, String(detail || response.statusText), detail);
  }
  return payload as T;
}

export interface MultiCatalogDashboard {
  catalogs: Array<{
    catalog_id: string;
    catalog_name: string;
    catalog_type: string;
    table_count: number;
    total_size_gb: number;
    small_files_count: number;
    needs_compaction_count: number;
    monthly_cost_usd: number;
    projected_savings_usd_month: number | null;
    avg_health_score: number;
    enabled: boolean;
  }>;
  total_catalogs: number;
  total_tables: number;
  total_cost_usd: number;
}

export const getMultiCatalogDashboard = (api: ApiOptions) =>
  dashboardFetch<MultiCatalogDashboard>("/multi-catalog", api);

export const getQueryPerformanceDashboard = (
  api: ApiOptions,
  hours = 24,
  engine?: string,
  tableId?: UUID
) => {
  const params = new URLSearchParams({ hours: String(hours), limit: "100" });
  if (engine) params.set("engine", engine);
  if (tableId) params.set("table_id", tableId);
  return dashboardFetch<QueryPerformanceData>(`/query-performance?${params}`, api);
};

export interface QueryPerformanceData {
  queries: Array<{
    query_id: string;
    query_text: string | null;
    engine: string;
    user: string | null;
    duration_ms: number;
    bytes_scanned: number | null;
    rows_produced: number | null;
    status: string;
    started_at: string;
    table_name: string | null;
  }>;
  total: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  p99_latency_ms: number | null;
  avg_bytes_scanned: number | null;
  failed_count: number;
}

export interface CostAttributionResponse {
  attributions: Array<{
    cost_center: string | null;
    department: string | null;
    storage_cost_usd: number;
    compute_cost_usd: number;
    maintenance_cost_usd: number;
    total_cost_usd: number;
    table_count: number;
    month: string;
  }>;
  total_cost_usd: number;
  unattributed_cost_usd: number;
  month: string;
}

export interface SLAResponse {
  slas: Array<{
    table_id: string;
    table_name: string;
    freshness_hours: number | null;
    freshness_sla_hours: number | null;
    freshness_breached: boolean;
    scan_staleness_hours: number | null;
    maintenance_days: number | null;
    maintenance_sla_days: number | null;
    maintenance_breached: boolean;
    p95_latency_ms: number | null;
    p95_latency_sla_ms: number | null;
    p95_latency_breached: boolean;
  }>;
  total_breached: number;
  total_monitored: number;
}

export interface JobDetailDashboard {
  job_id: string;
  table_id: string;
  table_name: string;
  operation_type: string;
  status: string;
  engine: string;
  replica: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  input_files_count: number | null;
  output_files_count: number | null;
  effectiveness_ratio: number | null;
  error_message: string | null;
  sql_statements: string[] | null;
  partition_predicates: string[] | null;
}

export const getCostAttributionDashboard = (api: ApiOptions, month?: string) =>
  dashboardFetch<CostAttributionResponse>(
    month ? `/cost-attribution?month=${encodeURIComponent(month)}` : "/cost-attribution",
    api
  );

export const getSlaStatusDashboard = (api: ApiOptions, breachedOnly = false, tableId?: UUID) => {
  const params = new URLSearchParams();
  if (breachedOnly) params.set("breached_only", "true");
  if (tableId) params.set("table_id", tableId);
  const qs = params.toString();
  return dashboardFetch<SLAResponse>(qs ? `/sla-status?${qs}` : "/sla-status", api);
};

export const getJobDetailDashboard = (api: ApiOptions, jobId: UUID) =>
  dashboardFetch<JobDetailDashboard>(`/jobs/${jobId}/detail`, api);

export interface PendingApprovalRead {
  id: string;
  table_id: string;
  table_name: string;
  operation_type: string;
  status: string;
  requested_by: string;
  impact_preview: Record<string, unknown> | null;
  created_at: string;
  expires_at: string | null;
}

export interface AnomalyAlertRead {
  table_id: string;
  table_name: string;
  metric_name: string;
  current_value: number;
  expected_value: number;
  deviation_sigma: number;
  severity: string;
  description: string;
  detected_at: string;
}

export interface CostForecastRead {
  forecast_month: string;
  current_month_storage_cost_usd: number;
  current_month_compute_cost_usd: number;
  current_month_maintenance_cost_usd: number;
  forecast_storage_cost_usd: number;
  forecast_compute_cost_usd: number;
  forecast_maintenance_cost_usd: number;
  total_forecast_usd: number;
  budget_limit_usd: number | null;
  alert_level: string;
}

export interface TierPolicyRead {
  id: string;
  name: string;
  source_id: string;
  storage_class: string;
  min_age_days: number;
  enabled: boolean;
}

export interface TierPolicyWrite {
  source_id: string;
  name: string;
  storage_class: string;
  min_age_days: number;
  predicate?: JsonObject | null;
  enabled?: boolean;
}

export interface TierPolicyPatch {
  name?: string;
  storage_class?: string;
  min_age_days?: number;
  predicate?: JsonObject | null;
  enabled?: boolean;
}

export interface LineageNodeRead {
  table_id: string;
  table_name: string;
  depth: number;
  node_type: string;
}

export interface LineageGraphRead {
  root_table_id: string;
  root_table_name: string;
  upstream: LineageNodeRead[];
  downstream: LineageNodeRead[];
}

export const listPendingApprovals = (api: ApiOptions) =>
  apiFetch<PendingApprovalRead[]>("/advanced/approvals/pending", api);

export const listFleetAnomalies = (api: ApiOptions) =>
  apiFetch<AnomalyAlertRead[]>("/advanced/anomalies", api);

export const getCostForecast = (api: ApiOptions, budgetLimitUsd?: number) =>
  apiFetch<CostForecastRead>("/advanced/finops/forecast", api, {
    query: { budget_limit_usd: budgetLimitUsd }
  });

export const listTierPolicies = (api: ApiOptions) =>
  apiFetch<TierPolicyRead[]>("/tier-policies", api);

export const createTierPolicy = (api: ApiOptions, body: TierPolicyWrite) =>
  apiFetch<TierPolicyRead>("/tier-policies", api, { method: "POST", body });

export const patchTierPolicy = (api: ApiOptions, id: string, body: TierPolicyPatch) =>
  apiFetch<TierPolicyRead>(`/tier-policies/${id}`, api, { method: "PATCH", body });

export const deleteTierPolicy = (api: ApiOptions, id: string) =>
  apiFetch<void>(`/tier-policies/${id}`, api, { method: "DELETE" });

export const getTableLineage = (api: ApiOptions, tableId: string, depth = 3) =>
  apiFetch<LineageGraphRead>(`/advanced/lineage/${tableId}`, api, { query: { depth } });

export interface ComplianceStatusRead {
  table_id: UUID;
  table_ref: string;
  pending_deletion: {
    pending_rows?: number;
    blocked_until?: string | null;
    reason?: string;
  } | null;
  abac_policy_count: number;
  abac_enforcement_note: string | null;
}

export const getComplianceStatus = (api: ApiOptions, tableId: UUID) =>
  apiFetch<ComplianceStatusRead>(`/tables/${tableId}/compliance`, api);

export const protectSnapshot = (
  api: ApiOptions,
  tableId: UUID,
  body: { snapshot_id: number; label?: string | null }
) =>
  apiFetch<{ table_id: UUID; snapshot_id: number; label: string | null }>(
    `/tables/${tableId}/protected-snapshots`,
    api,
    { method: "POST", body }
  );

export interface S3TieringRecommendationRead {
  table_id: UUID;
  table_name: string;
  current_size_gb: number;
  avg_file_size_kb: number;
  days_since_last_access: number;
  current_monthly_cost_usd: number;
  intelligent_tiering_monthly_cost_usd: number;
  estimated_annual_savings_usd: number;
  recommendation: string;
  reason: string;
}

export interface S3TieringResponse {
  recommendations: S3TieringRecommendationRead[];
  total_potential_savings_usd: number;
}

export const getS3TieringRecommendations = (
  api: ApiOptions,
  args: { min_size_gb?: number; lookback_days?: number } = {}
) => {
  const params = new URLSearchParams();
  if (args.min_size_gb !== undefined) params.set("min_size_gb", String(args.min_size_gb));
  if (args.lookback_days !== undefined) params.set("lookback_days", String(args.lookback_days));
  const query = params.toString();
  return dashboardFetch<S3TieringResponse>(
    query ? `/s3-tiering-recommendations?${query}` : "/s3-tiering-recommendations",
    api
  );
};

export const getTableS3Tiering = (api: ApiOptions, tableId: UUID, lookbackDays = 90) =>
  dashboardFetch<S3TieringRecommendationRead>(
    `/tables/${tableId}/s3-tiering?lookback_days=${lookbackDays}`,
    api
  );

export interface TierCandidateRead {
  table_ref: string;
  partition_value: string | null;
  age_days: number;
  target_storage_class: string;
}

export interface TierCandidatesResponse {
  table_id: UUID;
  candidates: TierCandidateRead[];
  summary: JsonObject;
}

export const getTierCandidates = (api: ApiOptions, tableId: UUID) =>
  apiFetch<TierCandidatesResponse>(`/tables/${tableId}/tier-candidates`, api);

export const createApprovalRequest = (
  api: ApiOptions,
  body: {
    table_id: UUID;
    operation_type: string;
    operation_params?: JsonObject;
    requested_by: string;
    reason?: string | null;
  }
) => apiFetch<PendingApprovalRead>("/advanced/approvals/request", api, { method: "POST", body });

export interface McpToolRead {
  name: string;
  description: string;
}

export const listMcpTools = (api: ApiOptions) => apiFetch<McpToolRead[]>("/mcp/tools", api);

export const callMcpTool = (api: ApiOptions, body: { name: string; arguments?: JsonObject }) =>
  apiFetch<{ result: JsonObject }>("/mcp/tools/call", api, { method: "POST", body });

export interface AIChatResponse {
  response: string;
  model: string;
  provider: string;
  tokens_used: number;
}

export const aiChat = (api: ApiOptions, body: { prompt: string; context?: JsonObject }) =>
  apiFetch<AIChatResponse>("/ai/chat", api, { method: "POST", body });

export interface CatalogLatencyRead {
  source_id: UUID;
  source_name: string;
  operations: JsonObject;
}

export const getCatalogLatency = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<CatalogLatencyRead>(`/sources/${sourceId}/catalog-latency`, api);

export const resetBandit = (api: ApiOptions) =>
  apiFetch<{ status: string }>("/auth/governor/bandit/reset", api, { method: "POST" });

export interface DailyReportResponse {
  report_date: string;
  summary: JsonObject;
  grade_distribution: JsonObject;
  maintenance_stats: JsonObject;
  top_issues: JsonObject[];
  critical_anomalies: JsonObject[];
  cost: JsonObject;
  ai_summary: string | null;
  slack_sent: boolean;
}

export const triggerDailyReport = (
  api: ApiOptions,
  args: { send_to_slack?: boolean; use_ai?: boolean } = {}
) => {
  const params = new URLSearchParams();
  if (args.send_to_slack) params.set("send_to_slack", "true");
  if (args.use_ai === false) params.set("use_ai", "false");
  const query = params.toString();
  return apiFetch<DailyReportResponse>(
    query ? `/reporting/daily?${query}` : "/reporting/daily",
    api,
    { method: "POST" }
  );
};

export interface RankedDestinationRead {
  destination_id: string;
  engine: string;
  score: number;
  reason: string;
}

export interface RoutingPreviewResponse {
  table_id: UUID;
  preset_id: UUID;
  selected_destination_id: string;
  selected_reason: string;
  ranked_destinations: RankedDestinationRead[];
}

export const previewRouting = (api: ApiOptions, body: { table_id: UUID; preset_id: UUID }) =>
  apiFetch<RoutingPreviewResponse>("/routing/preview", api, { method: "POST", body });

export interface BenefitScoreCandidate {
  partition_value?: string | null;
  small_files?: number;
  size_gb?: number;
  query_frequency?: number;
  benefit_score?: number;
}

export interface BenefitScoreResponse {
  table_id: UUID;
  partitions_scored: number;
  top_candidates: BenefitScoreCandidate[];
}

export const getTableBenefitScores = (api: ApiOptions, tableId: UUID, topN = 10) =>
  apiFetch<BenefitScoreResponse>(`/tables/${tableId}/benefit-scores`, api, { query: { top_n: topN } });

export const updateTableBenefitScores = (api: ApiOptions, tableId: UUID) =>
  apiFetch<BenefitScoreResponse>(`/tables/${tableId}/benefit-scores/update`, api, { method: "POST" });

export interface EffectivenessStatsResponse {
  table_id: UUID;
  total_input_files: number;
  total_output_files: number;
  run_count: number;
  effectiveness_ratio: number;
  effectiveness: string;
  avg_duration_ms: number;
  recommendation: string;
}

export const getTableEffectiveness = (api: ApiOptions, tableId: UUID, lookbackDays = 30) =>
  apiFetch<EffectivenessStatsResponse>(`/tables/${tableId}/effectiveness`, api, {
    query: { lookback_days: lookbackDays }
  });

export interface TableSignalRead {
  triggered: boolean;
  signal_name: string;
  reason: string;
  severity: string;
  metric_value: number | null;
  threshold: number | null;
}

export interface TableSignalsResponse {
  table_id: UUID;
  checked_at: string;
  signals: TableSignalRead[];
  any_triggered: boolean;
}

export const getTableSignals = (api: ApiOptions, tableId: UUID) =>
  apiFetch<TableSignalsResponse>(`/tables/${tableId}/signals`, api);

export interface MaintenanceSignalRead {
  table_id: string;
  table_name: string;
  signal_type: string;
  severity: string;
  current_value: number;
  baseline_value: number;
  threshold: number;
  description: string;
  detected_at: string;
}

export const listMaintenanceSignals = (api: ApiOptions) =>
  apiFetch<MaintenanceSignalRead[]>("/advanced/signals", api);

export interface DeepHealthResponse {
  status: string;
  checks: Record<string, { status: string; detail?: string }>;
}

export const getShallowHealth = async (): Promise<{ status: string }> => {
  const response = await fetch("/healthz");
  const payload = await response.json();
  if (!response.ok) {
    throw new ApiError(response.status, "Health check failed", payload);
  }
  return payload as { status: string };
};

export const getDeepHealth = async (): Promise<DeepHealthResponse> => {
  const response = await fetch("/healthz/deep");
  const payload = await response.json();
  if (!response.ok) {
    throw new ApiError(response.status, "Deep health check failed", payload);
  }
  return payload as DeepHealthResponse;
};

export const getMetricsText = async (maxLines = 60): Promise<string> => {
  const response = await fetch("/metrics");
  const text = await response.text();
  if (!response.ok) {
    throw new ApiError(response.status, "Failed to fetch metrics", text);
  }
  const lines = text.split("\n");
  if (lines.length <= maxLines) return text;
  return `${lines.slice(0, maxLines).join("\n")}\n… (${lines.length - maxLines} more lines)`;
};

export const triggerGitOpsWebhook = async (
  api: ApiOptions,
  body: JsonObject,
  options: { gitopsSecret?: string } = {}
): Promise<JsonObject> => {
  const response = await fetch(`${API_PREFIX}/advanced/gitops/webhook`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${api.token}`,
      "Content-Type": "application/json",
      ...(options.gitopsSecret ? { "X-Cryo-GitOps-Secret": options.gitopsSecret } : {})
    },
    body: JSON.stringify(body)
  });
  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null ? (payload as JsonObject).detail : payload;
    throw new ApiError(response.status, String(detail || response.statusText), detail);
  }
  return payload as JsonObject;
};

export const approvalAction = (
  api: ApiOptions,
  requestId: string,
  body: { approved: boolean; approver: string; reason?: string }
) =>
  apiFetch<{ status: string; message: string }>(`/advanced/approvals/${requestId}/action`, api, {
    method: "POST",
    body
  });

// ==================== Workload-Aware Compaction Candidates (AutoComp) ====================

export interface GetCompactionCandidatesOptions {
  horizonDays?: number;
  k?: number;
}

export const getCompactionCandidates = (
  api: ApiOptions,
  tableId: UUID,
  options: GetCompactionCandidatesOptions = {}
): Promise<CompactionCandidatesResponse> =>
  apiFetch<CompactionCandidatesResponse>(
    `/advanced/compaction-candidates/${tableId}`,
    api,
    {
      query: {
        horizon_days: options.horizonDays ?? 30,
        k: options.k ?? 10
      }
    }
  );

// ==================== Unionable Tables Search (EasyTUS) ====================

export interface GetUnionableOptions {
  k?: number;
  useLlm?: boolean;
}

export const getUnionable = (
  api: ApiOptions,
  tableId: UUID,
  options: GetUnionableOptions = {}
): Promise<UnionableResponse> =>
  apiFetch<UnionableResponse>(`/advanced/unionable/${tableId}`, api, {
    query: { k: options.k ?? 10, use_llm: options.useLlm ?? true }
  });

// ==================== AI Skillset Versioning ====================

export const listSkillsets = (api: ApiOptions): Promise<SkillsetListResponse> =>
  apiFetch<SkillsetListResponse>("/advanced/skillsets", api);

export const getActiveSkillset = (
  api: ApiOptions,
  name: string
): Promise<SkillsetRead | null> =>
  apiFetch<SkillsetRead | null>(`/advanced/skillsets/${name}/active`, api);

export const createSkillset = (
  api: ApiOptions,
  body: SkillsetWrite
): Promise<SkillsetRead> =>
  apiFetch<SkillsetRead>("/advanced/skillsets", api, { method: "POST", body });

export const rollbackSkillset = (
  api: ApiOptions,
  name: string,
  body: SkillsetRollbackRequest
): Promise<SkillsetRead> =>
  apiFetch<SkillsetRead>(`/advanced/skillsets/${name}/rollback`, api, {
    method: "POST",
    body
  });

export const recordSkillsetOutcome = (
  api: ApiOptions,
  name: string,
  body: SkillsetOutcomeWrite
): Promise<SkillsetOutcomeRead> =>
  apiFetch<SkillsetOutcomeRead>(`/advanced/skillsets/${name}/outcome`, api, {
    method: "POST",
    body
  });

// ============================================================================
// Plan Verifier (arXiv:2510.09567)
// ============================================================================

export const verifyPlan = (
  api: ApiOptions,
  body: PlanVerifyRequest
): Promise<PlanVerdict> =>
  apiFetch<PlanVerdict>("/advanced/plan-verify", api, { method: "POST", body });

// ============================================================================
// Observation Lakehouse (SANER 2026)
// ============================================================================

export const searchObservations = (
  api: ApiOptions,
  args: {
    table_id?: string;
    event_type?: string;
    since?: string;
    until?: string;
    limit?: number;
  } = {}
): Promise<ObservationSearchResponse> =>
  apiFetch<ObservationSearchResponse>("/advanced/observability/search", api, {
    query: {
      table_id: args.table_id,
      event_type: args.event_type,
      since: args.since,
      until: args.until,
      limit: args.limit ?? 100
    }
  });

export const getObservationStats = (
  api: ApiOptions
): Promise<ObservationStatsResponse> =>
  apiFetch<ObservationStatsResponse>("/advanced/observability/stats", api);

export const compactObservations = (
  api: ApiOptions
): Promise<ObservationCompactResponse> =>
  apiFetch<ObservationCompactResponse>(
    "/advanced/observability/compact",
    api,
    { method: "POST" }
  );

// ============================================================================
// Puffin-backed column sketches (FORMATS '26, arXiv:2605.30308)
// ============================================================================

export const getColumnStats = (
  api: ApiOptions,
  tableId: UUID,
  columns?: string
): Promise<ColumnStatsResponse> =>
  apiFetch<ColumnStatsResponse>(
    `/advanced/column-stats/${tableId}`,
    api,
    { query: columns ? { columns } : undefined }
  );

// ============================================================================
// Silent-data-loss detector (arXiv:2604.20081)
// ============================================================================

export const getSilentDataLoss = (
  api: ApiOptions,
  tableId: UUID
): Promise<SilentDataLossReportRead | null> =>
  apiFetch<SilentDataLossReportRead | null>(
    `/advanced/silent-data-loss/${tableId}`,
    api
  );

export const repairSilentDataLoss = (
  api: ApiOptions,
  tableId: UUID
): Promise<SilentDataLossRepairResponse> =>
  apiFetch<SilentDataLossRepairResponse>(
    `/advanced/silent-data-loss/${tableId}/repair`,
    api,
    { method: "POST" }
  );

// ============================================================================
// Variance-aware QPP (CLOUD 2026, arXiv:2606.03464)
// ============================================================================

export const predictQPP = (
  api: ApiOptions,
  body: QPPPredictRequest
): Promise<QPPPredictResponse> =>
  apiFetch<QPPPredictResponse>("/governor/qpp/predict", api, {
    method: "POST",
    body: JSON.stringify(body)
  });

export const recordQPPObservation = (
  api: ApiOptions,
  body: QPPObservationPayload
): Promise<{ id: UUID }> =>
  apiFetch<{ id: UUID }>("/governor/qpp/record", api, {
    method: "POST",
    body: JSON.stringify(body)
  });

export const getQPPConfidence = (
  api: ApiOptions,
  tableId?: UUID
): Promise<QPPConfidenceResponse> =>
  apiFetch<QPPConfidenceResponse>(
    "/governor/qpp/confidence",
    api,
    { query: tableId ? { table_id: tableId } : undefined }
  );
