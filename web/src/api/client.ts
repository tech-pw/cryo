import type {
  AuditEntry,
  CapabilityManifest,
  CostDashboard,
  DatalakeDashboard,
  FleetTrendsResponse,
  DestinationRead,
  DestinationWrite,
  EffectiveParamsResponse,
  EffectivePresetResponse,
  EngineType,
  EffectReceiptRead,
  EffectResolutionRead,
  EffectResolutionWrite,
  EffectBatchResolutionRead,
  EffectBatchResolutionWrite,
  JobRead,
  JsonObject,
  MaintenanceControlRead,
  MaintenanceControlWrite,
  MaintenanceIntentRead,
  MaintenancePreviewResponse,
  MaintenanceRunResponse,
  MaintenanceSafetySummary,
  Mode,
  OperationType,
  PaginatedTables,
  PartitionRead,
  TableGroupByField,
  TableGroupByResponse,
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
  SkillsetActivateRequest,
  SkillsetPromoteRequest,
  SkillsetRollbackRequest,
  SkillsetOutcomeWrite,
  SkillsetOutcomeRead,
  LLMObservationListResponse,
  RunFailureDiagnosisRead,
  RunFailureDiagnosisFeedbackWrite,
  TableFilterTranslationRequest,
  TableFilterTranslationResponse,
  PlanNarrationRequest,
  PlanNarrationResponse,
  AnomalyRootCauseBriefRead,
  ResourcePredictionRead,
  TableAlertSubscriptionRead,
  TableCostRead,
  TableRead,
  TableRunRead,
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
  QPPConfidenceResponse,
  ABACPolicyListResponse,
  GitOpsStatusResponse,
  S3LogConfigRead,
  S3LogConfigPatch,
  S3LogConfigCheckResponse,
  S3LogConfigProbeResponse,
  S3LogIngestResponse,
  S3LogProvisionRequest,
  S3LogProvisionResponse,
  S3LogTeardownRequest,
  S3LogTeardownResponse,
  SimilarityClustersResponse,
  SimilarityPairRead,
  SimilarityPipelineRunResponse,
  SimilarityVerifyRequest,
  SimilarityVerifyResponse,
  DarkTableCandidatesResponse,
  DarkTableDismissRequest,
  DarkTableDismissResponse,
  ScanCostAttributionResponse
} from "./types";
import { serializePredicate, type Predicate } from "../lib/predicate";

export const API_PREFIX = "/api/v1";

export interface ApiOptions {
  token: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: unknown;
  readonly correlationId?: string;

  constructor(status: number, message: string, detail?: unknown, correlationId?: string | null) {
    const normalizedCorrelationId = correlationId?.trim() || undefined;
    super(normalizedCorrelationId ? `${message} · Correlation ${normalizedCorrelationId}` : message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.correlationId = normalizedCorrelationId;
  }
}

const responseError = (response: Response, message: string, detail?: unknown): ApiError =>
  new ApiError(response.status, message, detail, response.headers.get("x-correlation-id"));

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
      "X-Correlation-ID": crypto.randomUUID(),
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
    throw responseError(response, String(detail || response.statusText), detail);
  }

  return payload as T;
};

export const listTables = (
  api: ApiOptions,
  args: {
    predicate?: Predicate | null;
    sort?: string;
    limit?: number;
    offset?: number;
    cursor?: string;
    signal?: AbortSignal;
  }
) =>
  apiFetch<PaginatedTables>("/tables", api, {
    query: {
      filter: serializePredicate(args.predicate),
      sort: args.sort,
      limit: args.limit ?? 50,
      offset: args.offset ?? 0,
      cursor: args.cursor
    },
    signal: args.signal
  });

export const exportTablesCsv = (
  api: ApiOptions,
  args: { predicate?: Predicate | null; sort?: string; signal?: AbortSignal }
) =>
  apiFetch<string>("/tables/export.csv", api, {
    query: {
      filter: serializePredicate(args.predicate),
      sort: args.sort
    },
    signal: args.signal
  });

export const groupTables = (
  api: ApiOptions,
  args: { field: TableGroupByField; predicate?: Predicate | null; signal?: AbortSignal }
) =>
  apiFetch<TableGroupByResponse>("/tables/group-by", api, {
    query: { field: args.field, filter: serializePredicate(args.predicate) },
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
export const getTableRuns = (api: ApiOptions, id: UUID) =>
  apiFetch<{ items: TableRunRead[] }>(`/tables/${id}/runs`, api).then((payload) => payload.items);

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

export const getEffectiveParams = (api: ApiOptions, id: UUID) =>
  apiFetch<EffectiveParamsResponse>(`/tables/${id}/effective-params`, api);

export const patchTableParamOverrides = (
  api: ApiOptions,
  id: UUID,
  paramOverrides: Record<string, unknown>
) =>
  apiFetch<EffectiveParamsResponse>(`/tables/${id}/param-overrides`, api, {
    method: "PATCH",
    body: { param_overrides: paramOverrides }
  });

export const listSources = (api: ApiOptions) => apiFetch<SourceRead[]>("/sources", api);
export const getCapabilities = (api: ApiOptions) =>
  apiFetch<CapabilityManifest>("/capabilities", api);
export const getMaintenanceSafetySummary = (api: ApiOptions) =>
  apiFetch<MaintenanceSafetySummary>("/maintenance/safety/summary", api);
export const listMaintenanceIntents = (
  api: ApiOptions,
  options: { status?: string; limit?: number } = {}
) =>
  apiFetch<MaintenanceIntentRead[]>("/maintenance/safety/intents", api, {
    query: options
  });
export const listMaintenanceEffects = (
  api: ApiOptions,
  options: { state?: string; ambiguousOnly?: boolean; limit?: number } = {}
) =>
  apiFetch<EffectReceiptRead[]>("/maintenance/safety/effects", api, {
    query: {
      state: options.state,
      ambiguous_only: options.ambiguousOnly,
      limit: options.limit
    }
  });
export const resolveMaintenanceEffect = (
  api: ApiOptions,
  receiptId: UUID,
  body: EffectResolutionWrite
) =>
  apiFetch<EffectResolutionRead>(`/maintenance/safety/effects/${receiptId}/resolve`, api, {
    method: "POST",
    body
  });
export const resolveMaintenanceEffectsBatch = (
  api: ApiOptions,
  body: EffectBatchResolutionWrite
) =>
  apiFetch<EffectBatchResolutionRead>("/maintenance/safety/effects/resolve-batch", api, {
    method: "POST",
    body
  });
export const listMaintenanceControls = (api: ApiOptions) =>
  apiFetch<MaintenanceControlRead[]>("/maintenance/control", api);
export const pauseMaintenance = (api: ApiOptions, body: MaintenanceControlWrite) =>
  apiFetch<MaintenanceControlRead>("/maintenance/control/pause", api, {
    method: "POST",
    body
  });
export const resumeMaintenance = (api: ApiOptions, body: MaintenanceControlWrite) =>
  apiFetch<MaintenanceControlRead>("/maintenance/control/resume", api, {
    method: "POST",
    body
  });
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

export interface S3LogConfigBucket {
  bucket: string;
  region?: string | null;
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
}

export interface S3RequestCostByBucket {
  bucket: string;
  region?: string | null;
  enabled: boolean;
  logging_status: string;
  last_ingested_at?: string | null;
  total_request_count_30d: number;
  total_cost_usd_month: number;
  by_tier: { tier1_count: number; tier2_count: number; tier1_cost_usd: number; tier2_cost_usd: number };
}

export interface S3RequestCostRollup {
  bucket: string;
  source_id: UUID;
  source_name: string;
  region?: string | null;
  enabled: boolean;
  logging_status: string;
  last_ingested_at?: string | null;
  total_request_count_30d: number;
  total_cost_usd_month: number;
  by_tier: { tier1_count: number; tier2_count: number; tier1_cost_usd: number; tier2_cost_usd: number };
  by_day: Array<{ day: string; count: number; cost_usd: number }>;
  by_bucket: S3RequestCostByBucket[];
  approximate: boolean;
}

// Same endpoint as listS3LogConfigs() below (defined once the S3 log config section of this
// file is reached) -- this alias exists so the Cost page's fleet-wide "S3 requests" tab has
// its own narrower return type (S3LogConfigBucket) without a second implementation to drift
// out of sync with the per-source panel's.
export const listSourceS3LogConfigs = (api: ApiOptions, sourceId: UUID): Promise<S3LogConfigBucket[]> =>
  listS3LogConfigs(api, sourceId);

export const getSourceS3RequestCostRollup = (
  api: ApiOptions,
  sourceId: UUID,
  params?: { lookback_days?: number }
) => {
  const qs = params?.lookback_days ? `?lookback_days=${params.lookback_days}` : "";
  return apiFetch<S3RequestCostRollup>(`/sources/${sourceId}/s3-request-cost-rollup${qs}`, api);
};

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
export const listCompatiblePresets = (api: ApiOptions, tableId: UUID) =>
  apiFetch<PresetRead[]>(`/tables/${tableId}/compatible-presets`, api);
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
export const forceAdmitJob = (api: ApiOptions, id: UUID) =>
  apiFetch<JobRead>(`/jobs/${id}/force-admit`, api, { method: "POST" });

export const getDatalakeDashboard = (
  api: ApiOptions,
  args: { top_limit?: number; drift_limit?: number } = {}
) =>
  apiFetch<RawDatalakeDashboard>("/dashboards/datalake", api, {
    query: { top_limit: args.top_limit, drift_limit: args.drift_limit }
  }).then(normalizeDashboard);
export const getFleetTrends = (api: ApiOptions, args: { days?: number } = {}) =>
  apiFetch<FleetTrendsResponse>("/dashboard/fleet-trends", api, {
    query: { days: args.days ?? 30 }
  });
export const getSchemaCost = (api: ApiOptions) =>
  apiFetch<RawSchemaCost>("/dashboards/schema-cost", api).then((payload) =>
    payload.items.map((item) => ({
      schema_name: item.schema_name,
      cost_usd: item.estimated_monthly_s3_cost_usd,
      size_gb: item.total_size_gb
    }))
  );

// Table deduplication / similarity (docs/TABLE_DEDUPLICATION_DESIGN.md)
export const runDedupPipeline = (api: ApiOptions, args: { sample_rows?: boolean } = {}) =>
  apiFetch<SimilarityPipelineRunResponse>("/dedup/run", api, {
    method: "POST",
    query: { sample_rows: args.sample_rows }
  });
export const getSimilarityClusters = (api: ApiOptions, args: { min_reclaimable_gb?: number } = {}) =>
  apiFetch<SimilarityClustersResponse>("/dedup/clusters", api, {
    query: { min_reclaimable_gb: args.min_reclaimable_gb }
  });
export const getSimilarityPairs = (
  api: ApiOptions,
  args: { min_score?: number; limit?: number; table_ids?: UUID[] } = {}
) => {
  // table_ids needs repeated ?table_ids=x&table_ids=y query params (FastAPI's list[UUID]
  // query-param convention) -- apiFetch's `query` option only supports scalar values via
  // URLSearchParams.set (last-write-wins, not .append), so this one call builds its path
  // manually rather than widening the shared helper's type for a single caller.
  let path = "/dedup/pairs";
  const params = new URLSearchParams();
  if (args.min_score !== undefined) params.set("min_score", String(args.min_score));
  if (args.limit !== undefined) params.set("limit", String(args.limit));
  (args.table_ids ?? []).forEach((id) => params.append("table_ids", id));
  const qs = params.toString();
  if (qs) path += `?${qs}`;
  return apiFetch<SimilarityPairRead[]>(path, api);
};
export const verifySimilarityPair = (api: ApiOptions, body: SimilarityVerifyRequest) =>
  apiFetch<SimilarityVerifyResponse>("/dedup/verify", api, {
    method: "POST",
    body
  });

// Dark table detection (sustained zero-query-activity tables)
export const getDarkTables = (api: ApiOptions) =>
  apiFetch<DarkTableCandidatesResponse>("/dark-tables", api);
export const dismissDarkTable = (api: ApiOptions, tableId: UUID, body: DarkTableDismissRequest) =>
  apiFetch<DarkTableDismissResponse>(`/dark-tables/${tableId}/dismiss`, api, {
    method: "POST",
    body
  });

// Query-driven scan cost attribution
export const getScanCostAttribution = (api: ApiOptions, args: { days?: number } = {}) =>
  apiFetch<ScanCostAttributionResponse>("/cost-attribution", api, {
    query: { days: args.days }
  });

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
  freshness_distribution?: DatalakeDashboard["freshness_distribution"];
  freshness_as_of?: string | null;
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
  cost_by_schema?: Array<{
    schema_name: string;
    current_usd: number;
    projected_usd: number;
    savings_usd: number;
  }>;
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
  freshness_distribution: payload.freshness_distribution,
  freshness_as_of: payload.freshness_as_of,
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
  drift: payload.drift,
  cost_by_schema: payload.cost_by_schema?.map((item) => ({
    schema_name: item.schema_name,
    cost_usd: item.current_usd,
    projected_usd: item.projected_usd,
    savings_usd: item.savings_usd
  }))
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
  const ops = {
    optimize: 0,
    expire_snapshots: 0,
    remove_orphan_files: 0,
    optimize_manifests: 0
  } satisfies Partial<Record<OperationType, number>>;
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

export interface SmartMaintenanceOverviewRead {
  generated_at: string;
  tables_with_stats: number;
  tiers: {
    hot: number;
    warm: number;
    cold: number;
    dormant: number;
    unknown: number;
  };
  roi_shadow_decisions_30d: number;
  roi_enforced: boolean;
  pending_layout_recommendations: number;
}

export interface DetectionTierRowRead {
  table_id: UUID;
  table_name: string;
  schema_name: string;
  tier: string;
  score?: number | null;
  commits_24h: number;
  commits_7d: number;
  delete_file_pct?: number | null;
  query_count_30d: number;
}

export interface DetectionTierResponseRead {
  items: DetectionTierRowRead[];
}

export interface RoiBreakdownRead {
  table_id: UUID;
  decision: {
    action: string;
    reason: string;
    bcr: number;
    threshold: number;
    shadow: boolean;
    benefit_usd_month: number;
    cost_usd_month: number;
    ts?: string | null;
  };
  monthly_benefit_usd: number;
  monthly_cost_usd: number;
  details: Record<string, number>;
  effectiveness?: { effectiveness_ratio?: number; effectiveness?: string; run_count?: number } | null;
  top_moop_partitions?: Array<{
    partition_value?: string | null;
    moop_score?: number | null;
    small_files?: number | null;
    size_gb?: number | null;
  }>;
}

export interface WorkloadForecastRead {
  table_id: UUID;
  generated_at: string;
  horizon_hours: number;
  expected_total_queries: number;
  points: Array<{ hour_start: string; expected_queries: number }>;
}

export interface SmartLayoutRecommendationRead {
  id: UUID;
  table_id: UUID;
  table_ref: string;
  recommended_strategy: string;
  recommended_columns: string[];
  status: string;
  confidence: number;
  estimated_prune_improvement?: number | null;
  observed_prune_improvement?: number | null;
  approved_at?: string | null;
  dismissed_at?: string | null;
  applied_at?: string | null;
  applied_run_id?: UUID | null;
}

export type PartitionEvolutionTrigger =
  | "filter_frequency"
  | "scan_ratio"
  | "granularity"
  | "bucket_sizing";

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
  scan_ratio?: number | null;
  avg_selectivity?: number | null;
  trigger?: PartitionEvolutionTrigger | null;
}

export interface FastPathFindingRead {
  table_id: UUID;
  table_name: string;
  finding_type: string;
  reason: string;
  severity: string;
  metric_value?: number | null;
  detected_at: string;
}

export interface BloomFilterRecommendationRead {
  column: string;
  reason: string;
  estimated_skip_rate: number;
  cardinality: string;
  equality_filter_count: number;
  spark_sql: string;
}

export interface BloomFilterRecommendationsResponse {
  table_id: UUID;
  table_name: string;
  telemetry_source: string;
  message?: string | null;
  recommendations: BloomFilterRecommendationRead[];
}

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

export const getFastPathFindings = async (api: ApiOptions): Promise<FastPathFindingRead[]> => {
  const payload = await apiFetch<
    FastPathFindingRead[] | { findings: FastPathFindingRead[] } | { items: FastPathFindingRead[] }
  >("/advanced/fast-path-findings", api);
  if (Array.isArray(payload)) return payload;
  if (payload && "findings" in payload && Array.isArray(payload.findings)) return payload.findings;
  if (payload && "items" in payload && Array.isArray(payload.items)) return payload.items;
  return [];
};

export const getBloomFilterRecommendations = (api: ApiOptions, tableId: UUID) =>
  apiFetch<BloomFilterRecommendationsResponse>(`/advanced/bloom-filter-recommendations/${tableId}`, api);

export const getSmartMaintenanceOverview = (api: ApiOptions) =>
  apiFetch<SmartMaintenanceOverviewRead>("/smart-maintenance/overview", api);

export const getDetectionTiers = (api: ApiOptions, limit?: number) =>
  apiFetch<DetectionTierResponseRead>("/smart-maintenance/detection-tiers", api, {
    query: { limit }
  });

export const getTableRoiBreakdown = (api: ApiOptions, tableId: UUID) =>
  apiFetch<RoiBreakdownRead>(`/smart-maintenance/tables/${tableId}/roi`, api);

export const getTableWorkloadForecast = (
  api: ApiOptions,
  tableId: UUID,
  horizonHours?: number
) =>
  apiFetch<WorkloadForecastRead>(`/smart-maintenance/tables/${tableId}/workload-forecast`, api, {
    query: { horizon_hours: horizonHours }
  });

export const listSmartLayoutRecommendations = (api: ApiOptions, status?: string) =>
  apiFetch<{ items: SmartLayoutRecommendationRead[] }>(
    "/smart-maintenance/layout-recommendations",
    api,
    { query: { status } }
  );

export const approveSmartLayoutRecommendation = (
  api: ApiOptions,
  recId: UUID,
  note?: string
) =>
  apiFetch<SmartLayoutRecommendationRead>(
    `/smart-maintenance/layout-recommendations/${recId}/approve`,
    api,
    { method: "POST", body: { note } }
  );

export const dismissSmartLayoutRecommendation = (
  api: ApiOptions,
  recId: UUID,
  reason: string
) =>
  apiFetch<SmartLayoutRecommendationRead>(
    `/smart-maintenance/layout-recommendations/${recId}/dismiss`,
    api,
    { method: "POST", body: { reason } }
  );

export const executeSmartLayoutRecommendation = (
  api: ApiOptions,
  recId: UUID,
  mode: "light" | "standard" | "deep" = "standard"
) =>
  apiFetch<MaintenanceRunResponse>(
    `/smart-maintenance/layout-recommendations/${recId}/execute`,
    api,
    { method: "POST", body: { mode } }
  );

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

export const listS3LogConfigs = (api: ApiOptions, sourceId: UUID) =>
  apiFetch<S3LogConfigRead[]>(`/sources/${sourceId}/s3-log-configs`, api);

export const patchS3LogConfig = (api: ApiOptions, sourceId: UUID, bucket: string, body: S3LogConfigPatch) =>
  apiFetch<S3LogConfigRead>(`/sources/${sourceId}/s3-log-configs/${encodeURIComponent(bucket)}`, api, {
    method: "PATCH",
    body
  });

export const checkS3LogConfigLogging = (api: ApiOptions, sourceId: UUID, bucket: string) =>
  apiFetch<S3LogConfigCheckResponse>(
    `/sources/${sourceId}/s3-log-configs/${encodeURIComponent(bucket)}/check-logging`,
    api,
    { method: "POST" }
  );

export const probeS3LogConfigTarget = (api: ApiOptions, sourceId: UUID, bucket: string) =>
  apiFetch<S3LogConfigProbeResponse>(
    `/sources/${sourceId}/s3-log-configs/${encodeURIComponent(bucket)}/probe`,
    api,
    { method: "POST" }
  );

export const ingestS3LogConfig = (api: ApiOptions, sourceId: UUID, bucket: string) =>
  apiFetch<S3LogIngestResponse>(
    `/sources/${sourceId}/s3-log-configs/${encodeURIComponent(bucket)}/ingest`,
    api,
    { method: "POST" }
  );

export const provisionS3LogConfig = (
  api: ApiOptions,
  sourceId: UUID,
  bucket: string,
  body: S3LogProvisionRequest = {}
) =>
  apiFetch<S3LogProvisionResponse>(
    `/sources/${sourceId}/s3-log-configs/${encodeURIComponent(bucket)}/provision`,
    api,
    { method: "POST", body }
  );

export const teardownS3LogConfig = (
  api: ApiOptions,
  sourceId: UUID,
  bucket: string,
  body: S3LogTeardownRequest = {}
) =>
  apiFetch<S3LogTeardownResponse>(
    `/sources/${sourceId}/s3-log-configs/${encodeURIComponent(bucket)}/teardown`,
    api,
    { method: "POST", body }
  );

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
    throw responseError(response, String(detail || response.statusText), detail);
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
    table_id: string | null;
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
    schema_name: string;
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
    has_preset: boolean;
  }>;
  total_breached: number;
  total_monitored: number;
}

export interface JobDetailDashboard {
  job_id: string;
  run_id: string;
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
  force_admit?: boolean;
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

export interface SharedS3TableMember {
  source_id: string;
  source_name: string;
  source_type: string;
  schema_name: string;
  table_name: string;
  location: string;
  table_id: string | null;
  qualified_name: string;
}

export interface SharedS3Group {
  shared_path: string;
  kind: "exact" | "nested";
  table_count: number;
  tables: SharedS3TableMember[];
}

export interface SharedS3SourceSummary {
  source_id: string;
  source_name: string;
  source_type: string;
  mode: "live" | "db_fallback";
  table_count: number;
  skipped_count: number;
  error: string | null;
}

export interface SharedS3Report {
  id: string | null;
  generated_at: string | null;
  sources_harvested: number;
  table_count: number;
  overlapping_group_count: number;
  overlapping_table_count: number;
  skipped_count: number;
  sources: SharedS3SourceSummary[];
  groups: SharedS3Group[];
  slack_sent?: boolean | null;
  slack_skipped_reason?: string | null;
  slack_targets?: number | null;
}

export const getSharedS3Anomalies = (api: ApiOptions) =>
  apiFetch<SharedS3Report>("/anomalies/shared-s3", api);

export const refreshSharedS3Anomalies = (api: ApiOptions) =>
  apiFetch<SharedS3Report>("/anomalies/shared-s3/refresh", api, { method: "POST" });

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

export type PropertyRecommendationTier = "tier1" | "tier2" | "tier3";
export type PropertyRecommendationAction =
  | "apply"
  | "link_layout"
  | "link_bloom"
  | "link_run"
  | "read_only"
  | "banner"
  | "informational";

export interface PropertyRecommendationRead {
  property_key: string;
  current_value: string | null;
  recommended_value: string | null;
  reason: string;
  tier: PropertyRecommendationTier;
  action: PropertyRecommendationAction;
  layout_recommendation_id: UUID | null;
  bloom_filter_column: string | null;
  needs_confirmation: boolean;
}

export interface PropertyRecommendationsResponse {
  table_id: UUID;
  generated_at: string;
  items: PropertyRecommendationRead[];
  gc_blocked: boolean;
}

export interface ApplyTablePropertiesResponse {
  table_id: UUID;
  applied: Record<string, string>;
  audit_log_id: number;
  message: string;
}

export const getPropertyRecommendations = (api: ApiOptions, tableId: UUID) =>
  apiFetch<PropertyRecommendationsResponse>(`/tables/${tableId}/property-recommendations`, api);

export const applyTableProperties = (
  api: ApiOptions,
  tableId: UUID,
  body: { properties: Record<string, string>; confirm_risky?: boolean; note?: string }
) =>
  apiFetch<ApplyTablePropertiesResponse>(`/tables/${tableId}/properties/apply`, api, {
    method: "POST",
    body
  });

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

// docs/PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md -- inspired by Yelp's engineering blog
// post on partition access visualizations (S3 access logs -> partition-vs-time scatter plot).
export interface PartitionAccessPoint {
  day: string;
  partition_path: string;
  request_count: number;
}

export interface PartitionAccessSummary {
  partition_path: string;
  total_requests: number;
  first_seen: string;
  last_seen: string;
  days_since_last_access: number;
}

export interface PartitionAccessResponse {
  table_id: UUID;
  since: string;
  points: PartitionAccessPoint[];
  partitions: PartitionAccessSummary[];
}

export const getTablePartitionAccess = (api: ApiOptions, tableId: UUID, days = 90) =>
  dashboardFetch<PartitionAccessResponse>(`/tables/${tableId}/partition-access?days=${days}`, api);

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
  status: "CLEAR" | "WATCH" | "CRITICAL";
  summary: JsonObject;
  grade_distribution: JsonObject;
  maintenance_stats: JsonObject;
  top_issues: JsonObject[];
  critical_anomalies: JsonObject[];
  action_items: JsonObject[];
  cost: JsonObject;
  bottom_line: string | null;
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

export interface AutoCompCandidateItem {
  partition_value: string;
  small_files: number;
  size_gb: number;
  query_frequency: number;
  benefit_score: number;
  moop_score?: number | null;
  traits?: Record<string, number | string> | null;
  eligible: boolean;
  skip_reason?: string | null;
}

export interface AutoCompSkippedItem {
  partition_value: string;
  skip_reason: string;
}

export interface AutoCompCandidatesResponse {
  table_id: UUID;
  scan_at?: string | null;
  items: AutoCompCandidateItem[];
  skipped: AutoCompSkippedItem[];
  effectiveness?: Record<string, unknown> | null;
  compare: AutoCompCompareResponse;
}

export const getAutoCompCandidates = (api: ApiOptions, tableId: UUID, minScore = 1.0) =>
  apiFetch<AutoCompCandidatesResponse>(`/tables/${tableId}/autocomp/candidates`, api, {
    query: { min_score: minScore }
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
    throw responseError(response, "Health check failed", payload);
  }
  return payload as { status: string };
};

export const getDeepHealth = async (): Promise<DeepHealthResponse> => {
  const response = await fetch("/healthz/deep");
  const payload = await response.json();
  if (!response.ok) {
    throw responseError(response, "Deep health check failed", payload);
  }
  return payload as DeepHealthResponse;
};

export const getMetricsText = async (maxLines = 60): Promise<string> => {
  const response = await fetch("/metrics");
  const text = await response.text();
  if (!response.ok) {
    throw responseError(response, "Failed to fetch metrics", text);
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
    throw responseError(response, String(detail || response.statusText), detail);
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

export const activateSkillset = (
  api: ApiOptions,
  name: string,
  body: SkillsetActivateRequest
): Promise<SkillsetRead> =>
  apiFetch<SkillsetRead>(`/advanced/skillsets/${name}/activate`, api, {
    method: "POST",
    body
  });

export const promoteSkillset = (
  api: ApiOptions,
  name: string,
  body: SkillsetPromoteRequest
): Promise<SkillsetRead> =>
  apiFetch<SkillsetRead>(`/advanced/skillsets/${name}/promote`, api, {
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

export const listLlmObservations = (
  api: ApiOptions,
  query: {
    feature?: string;
    provider_type?: string;
    skillset_name?: string;
    limit?: number;
    offset?: number;
  } = {}
): Promise<LLMObservationListResponse> =>
  apiFetch<LLMObservationListResponse>("/advanced/llm-observations", api, { query });

export const diagnoseRunFailure = (
  api: ApiOptions,
  runId: UUID,
  options: { force?: boolean } = {}
): Promise<RunFailureDiagnosisRead> =>
  apiFetch<RunFailureDiagnosisRead>(`/ai/runs/${runId}/diagnose`, api, {
    query: { force: options.force ?? false },
    method: "POST"
  });

export const getLatestRunDiagnosis = (
  api: ApiOptions,
  runId: UUID
): Promise<RunFailureDiagnosisRead | null> =>
  apiFetch<RunFailureDiagnosisRead | null>(`/ai/runs/${runId}/diagnosis-latest`, api);

export const submitDiagnosisFeedback = (
  api: ApiOptions,
  diagnosisId: UUID,
  body: RunFailureDiagnosisFeedbackWrite
): Promise<RunFailureDiagnosisRead> =>
  apiFetch<RunFailureDiagnosisRead>(`/ai/runs/diagnoses/${diagnosisId}/feedback`, api, {
    method: "POST",
    body
  });

export const translateTableFilter = (
  api: ApiOptions,
  body: TableFilterTranslationRequest
): Promise<TableFilterTranslationResponse> =>
  apiFetch<TableFilterTranslationResponse>("/ai/table-filter/translate", api, {
    method: "POST",
    body
  });

export const narratePlan = (
  api: ApiOptions,
  body: PlanNarrationRequest
): Promise<PlanNarrationResponse> =>
  apiFetch<PlanNarrationResponse>("/ai/plan/narrate", api, {
    method: "POST",
    body
  });

export const getAnomalyRootCauseBrief = (
  api: ApiOptions,
  tableId: UUID
): Promise<AnomalyRootCauseBriefRead> =>
  apiFetch<AnomalyRootCauseBriefRead>(`/ai/tables/${tableId}/anomaly-brief`, api);

export const getResourcePrediction = (
  api: ApiOptions,
  tableId: UUID
): Promise<ResourcePredictionRead> =>
  apiFetch<ResourcePredictionRead>(`/ai/tables/${tableId}/resource-prediction`, api);

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
    { method: "POST", body: { commit: false } }
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

export const listAbacPolicies = (
  api: ApiOptions,
  tableId?: UUID
): Promise<ABACPolicyListResponse> =>
  apiFetch<ABACPolicyListResponse>("/security/abac/policies", api, {
    query: tableId ? { table_id: tableId } : undefined
  });

export const getGitOpsStatus = (api: ApiOptions): Promise<GitOpsStatusResponse> =>
  apiFetch<GitOpsStatusResponse>("/advanced/gitops/status", api);

// ============================================================================
// Global catalog search (Part 6.2.1)
// ============================================================================

export type SearchAssetType = "catalogs" | "schemas" | "tables" | "columns";

export type SearchMatchReason = "name" | "schema" | "semantic" | "catalog" | "column";

type SearchAssetTypeRaw = "catalog" | "schema" | "table" | "column";

interface SearchMatchRaw {
  asset_type: SearchAssetTypeRaw;
  name: string;
  qualified_name?: string | null;
  catalog_id?: string | null;
  catalog_name?: string | null;
  schema_name?: string | null;
  table_id?: string | null;
  column_name?: string | null;
  data_type?: string | null;
  match_reason: SearchMatchReason;
  score?: number | null;
}

interface SearchGroupRaw {
  asset_type: SearchAssetType;
  total: number;
  hits: SearchMatchRaw[];
}

interface SearchResponseRaw {
  query: string;
  groups: SearchGroupRaw[];
}

const SEARCH_GROUP_LABELS: Record<SearchAssetType, string> = {
  catalogs: "Catalogs",
  schemas: "Schemas",
  tables: "Tables",
  columns: "Columns"
};

const SEARCH_HIT_GROUP: Record<SearchAssetTypeRaw, SearchAssetType> = {
  catalog: "catalogs",
  schema: "schemas",
  table: "tables",
  column: "columns"
};

const searchHitId = (hit: SearchMatchRaw): string => {
  if (hit.table_id && hit.column_name) return `${hit.table_id}:${hit.column_name}`;
  if (hit.table_id) return hit.table_id;
  if (hit.catalog_id && hit.schema_name) return `${hit.catalog_id}:${hit.schema_name}`;
  if (hit.catalog_id) return hit.catalog_id;
  return hit.qualified_name ?? hit.name;
};

const normalizeSearchHit = (hit: SearchMatchRaw): SearchHit => ({
  id: searchHitId(hit),
  type: SEARCH_HIT_GROUP[hit.asset_type],
  name: hit.name,
  qualified_name: hit.qualified_name,
  catalog: hit.catalog_name,
  schema_name: hit.schema_name,
  table_id: hit.table_id,
  column_name: hit.column_name,
  data_type: hit.data_type,
  match_reason: hit.match_reason,
  similarity_score: hit.score
});

const normalizeSearchResponse = (raw: SearchResponseRaw): SearchResponse => {
  const groups = raw.groups.map((group) => ({
    type: group.asset_type,
    label: SEARCH_GROUP_LABELS[group.asset_type],
    total: group.total,
    items: group.hits.map(normalizeSearchHit)
  }));
  return {
    query: raw.query,
    groups,
    total: groups.reduce((sum, group) => sum + group.total, 0)
  };
};

export interface SearchHit {
  id: string;
  type: SearchAssetType;
  name: string;
  qualified_name?: string | null;
  catalog?: string | null;
  schema_name?: string | null;
  table_id?: string | null;
  column_name?: string | null;
  data_type?: string | null;
  match_reason: SearchMatchReason;
  similarity_score?: number | null;
}

export interface SearchGroup {
  type: SearchAssetType;
  label: string;
  total: number;
  items: SearchHit[];
}

export interface SearchResponse {
  query: string;
  groups: SearchGroup[];
  total: number;
}

export const searchCatalog = async (
  api: ApiOptions,
  args: {
    q: string;
    limit?: number;
    group?: SearchAssetType;
    offset?: number;
    signal?: AbortSignal;
  }
): Promise<SearchResponse> => {
  const raw = await apiFetch<SearchResponseRaw>("/search", api, {
    query: {
      q: args.q,
      limit: args.limit,
      group: args.group,
      offset: args.offset
    },
    signal: args.signal
  });
  return normalizeSearchResponse(raw);
};
