import type {
  AuditEntry,
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
  PaginatedTables,
  PartitionRead,
  PresetRead,
  PresetRuleRead,
  PresetSimulationResponse,
  PresetWrite,
  RoleToken,
  RunRead,
  SourceRead,
  SourceWrite,
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
export const getEffectivePreset = (api: ApiOptions, id: UUID) =>
  apiFetch<EffectivePresetResponse>(`/tables/${id}/effective-preset`, api);

export const listSources = (api: ApiOptions) => apiFetch<SourceRead[]>("/sources", api);
export const createSource = (api: ApiOptions, body: SourceWrite) =>
  apiFetch<SourceRead>("/sources", api, { method: "POST", body });
export const patchSource = (api: ApiOptions, id: UUID, body: Partial<SourceWrite>) =>
  apiFetch<SourceRead>(`/sources/${id}`, api, { method: "PATCH", body });

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
