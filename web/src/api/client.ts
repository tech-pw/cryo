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
  SourceRead,
  SourceTestResult,
  SourceWrite,
  TableCostRead,
  TableRead,
  UUID
} from "./types";
import { serializePredicate, type Predicate } from "../lib/predicate";

export const API_PREFIX = "/api/v1";

export interface ApiOptions {
  token: RoleToken;
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
  args: { predicate?: Predicate | null; sort?: string; limit?: number; offset?: number }
) =>
  apiFetch<PaginatedTables>("/tables", api, {
    query: {
      filter: serializePredicate(args.predicate),
      sort: args.sort,
      limit: args.limit ?? 50,
      offset: args.offset ?? 0
    }
  });

export const getTable = (api: ApiOptions, id: UUID) => apiFetch<TableRead>(`/tables/${id}`, api);
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
export const testSource = (api: ApiOptions, body: { type: string; config: Record<string, unknown> }) =>
  apiFetch<SourceTestResult>("/sources/test", api, { method: "POST", body });

export const listDestinations = (api: ApiOptions) => apiFetch<DestinationRead[]>("/destinations", api);
export const createDestination = (api: ApiOptions, body: DestinationWrite) =>
  apiFetch<DestinationRead>("/destinations", api, { method: "POST", body });
export const patchDestination = (api: ApiOptions, id: UUID, body: Partial<DestinationWrite>) =>
  apiFetch<DestinationRead>(`/destinations/${id}`, api, { method: "PATCH", body });
export const testDestination = (api: ApiOptions, id: UUID) =>
  apiFetch<Record<string, unknown>>(`/destinations/${id}/test`, api, { method: "POST" });

export const listPresets = (api: ApiOptions) => apiFetch<PresetRead[]>("/presets", api);
export const createPreset = (api: ApiOptions, body: PresetWrite) =>
  apiFetch<PresetRead>("/presets", api, { method: "POST", body });
export const patchPreset = (api: ApiOptions, id: UUID, body: Partial<PresetWrite>) =>
  apiFetch<PresetRead>(`/presets/${id}`, api, { method: "PATCH", body });
export const listPresetRules = (api: ApiOptions, id: UUID) =>
  apiFetch<PresetRuleRead[]>(`/presets/${id}/rules`, api);
export const createPresetRule = (api: ApiOptions, id: UUID, body: { predicate: Predicate; enabled: boolean }) =>
  apiFetch<PresetRuleRead>(`/presets/${id}/rules`, api, { method: "POST", body });
export const simulatePreset = (api: ApiOptions, id: UUID) =>
  apiFetch<PresetSimulationResponse>(`/presets/${id}/simulate`, api, { method: "POST" });

export const assignTables = (
  api: ApiOptions,
  body: { selection: { mode: "ids"; ids: UUID[] } | { mode: "filter"; filter: Predicate | null }; preset_id: UUID }
) => apiFetch<{ affected: number }>("/assignments", api, { method: "POST", body });

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
    | { table_ids: UUID[]; destination_id: UUID; preset_id?: UUID | null; mode: Mode; trigger?: string }
    | {
        selection: { mode: "filter"; filter: Predicate | null };
        destination_id: UUID;
        preset_id?: UUID | null;
        mode: Mode;
        trigger?: string;
      }
) => apiFetch<MaintenanceRunResponse>("/maintenance/run", api, { method: "POST", body });

export const listRuns = (api: ApiOptions) => apiFetch<RunRead[]>("/runs", api);
export const getRun = (api: ApiOptions, id: UUID) => apiFetch<RunRead>(`/runs/${id}`, api);
export const getRunJobs = (api: ApiOptions, id: UUID) =>
  apiFetch<{ jobs: JobRead[] }>(`/runs/${id}/jobs`, api).then((payload) => payload.jobs);
export const cancelRun = (api: ApiOptions, id: UUID) =>
  apiFetch<RunRead>(`/runs/${id}/cancel`, api, { method: "POST" });
export const getJob = (api: ApiOptions, id: UUID) => apiFetch<JobRead>(`/jobs/${id}`, api);

export const getDatalakeDashboard = (api: ApiOptions) =>
  apiFetch<RawDatalakeDashboard>("/dashboards/datalake", api).then(normalizeDashboard);
export const getSchemaCost = (api: ApiOptions) =>
  apiFetch<RawSchemaCost>("/dashboards/schema-cost", api).then((payload) =>
    payload.items.map((item) => ({
      schema_name: item.schema_name,
      cost_usd: item.estimated_monthly_s3_cost_usd,
      size_gb: item.total_size_gb
    }))
  );

export const wsRunUrl = (runId: UUID, token: RoleToken): string => {
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

export const getTableQueryPatterns = (api: ApiOptions, tableId: UUID) =>
  apiFetch<TableQueryPatternsResponse>(`/tables/${tableId}/query-patterns`, api);

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
