import { readFileSync } from "node:fs";

import {
  DEFAULT_JOB_PAGE_SIZE,
  DEFAULT_SNAPSHOT_PAGE_SIZE,
  DEFAULT_TABLE_PAGE_SIZE,
  JOB_LIST_TOTAL,
  NEUTRAL_TRAITS,
  PREVIOUS_YEAR_COMMITTED_AT,
  SNAPSHOT_HISTORY_TOTAL,
  SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER,
  STALE_AS_OF,
  TABLE_LIST_TOTAL,
  TINY_SIZE_BYTES,
  TINY_SIZE_GB,
  VISUAL_FLINK_DESTINATION_ID,
  VISUAL_NOW_ISO,
  VISUAL_PRESET_ID,
  VISUAL_SOURCE_ID,
  VISUAL_SUBJECT,
  VISUAL_TIMEZONE,
  VISUAL_TRINO_DESTINATION_ID,
  type TraitFlags,
  type TraitMode,
  type VisualRole,
  visualAccessToken
} from "./constants";
import {
  clampPageSize,
  jobsPage,
  snapshotsPage,
  tablesPage,
  visualRun,
  type VisualJob,
  type VisualRun,
  type VisualSnapshot,
  type VisualTable
} from "./generate";

export const VISUAL_SCENARIOS = [
  "populated",
  "unknown",
  "partial",
  "stale",
  "api-failure",
  "viewer",
  "write-disabled",
  "unsupported-engine",
  "first-use-empty",
  "tables-10k",
  "run-1500",
  "snapshot-history",
  "long-names",
  "previous-year",
  "tiny-bytes"
] as const;

export type VisualScenario = (typeof VISUAL_SCENARIOS)[number];

export interface VisualHttpResponse {
  status: number;
  body: unknown;
}

export interface VisualProfile {
  scenario: VisualScenario;
  role: VisualRole;
  writesEnabled: boolean;
  empty: boolean;
  apiFailure: boolean;
  tablesTotal: number;
  jobsTotal: number;
  snapshotTotal: number;
  flags: TraitFlags;
}

const healthzBody = JSON.parse(readFileSync(new URL("./healthz.json", import.meta.url), "utf8")) as {
  status: string;
  fixture: string;
  service: string;
};

export function visualHealthz(): typeof healthzBody {
  return healthzBody;
}

const PUBLIC_PATHS = new Set(["/healthz", "/api/v1/healthz", "/api/v1/auth/config", "/api/v1/auth/token"]);

function traitMode(scenario: VisualScenario, kind: "longNames" | "previousYear" | "tinyBytes"): TraitMode {
  if (scenario === "long-names" && kind === "longNames") return "all";
  if (scenario === "previous-year" && kind === "previousYear") return "all";
  if (scenario === "tiny-bytes" && kind === "tinyBytes") return "all";
  if (
    scenario === "populated" ||
    scenario === "tables-10k" ||
    scenario === "run-1500" ||
    scenario === "snapshot-history"
  ) {
    return "first";
  }
  return "none";
}

export function profileFor(scenario: VisualScenario): VisualProfile {
  return {
    scenario,
    role: scenario === "viewer" ? "viewer" : "operator",
    writesEnabled: scenario !== "write-disabled",
    empty: scenario === "first-use-empty",
    apiFailure: scenario === "api-failure",
    tablesTotal: scenario === "first-use-empty" ? 0 : scenario === "tables-10k" ? TABLE_LIST_TOTAL : 3,
    jobsTotal: scenario === "first-use-empty" ? 0 : scenario === "run-1500" ? JOB_LIST_TOTAL : 3,
    snapshotTotal: scenario === "first-use-empty" ? 0 : scenario === "snapshot-history" ? SNAPSHOT_HISTORY_TOTAL : 4,
    flags: {
      ...NEUTRAL_TRAITS,
      unknown: scenario === "unknown",
      partial: scenario === "partial",
      stale: scenario === "stale",
      longNames: traitMode(scenario, "longNames"),
      previousYear: traitMode(scenario, "previousYear"),
      tinyBytes: traitMode(scenario, "tinyBytes"),
      unsupportedEngine: scenario === "unsupported-engine"
    }
  };
}

function asRole(value: unknown): VisualRole | null {
  return value === "viewer" || value === "operator" || value === "admin" ? value : null;
}

function tokenBody(profile: VisualProfile, posted: unknown): Record<string, unknown> {
  const record = posted && typeof posted === "object" ? (posted as { role?: unknown }) : {};
  const role = profile.role === "viewer" ? "viewer" : (asRole(record.role) ?? profile.role);
  return {
    access_token: visualAccessToken(role),
    token_type: "bearer",
    role
  };
}

function numberParam(url: URL, key: string, fallback: number): number {
  const raw = url.searchParams.get(key);
  if (raw === null || raw === "") return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

function sourceRow(enabled: boolean) {
  return {
    id: VISUAL_SOURCE_ID,
    name: "visual-lakehouse",
    type: "rest",
    config: { uri: "http://127.0.0.1:9/catalog" },
    secret_ref: null,
    enabled,
    maintenance_schema_names: ["visual_schema"],
    created_at: VISUAL_NOW_ISO,
    updated_at: VISUAL_NOW_ISO
  };
}

function destinationRow(engine: "trino" | "flink") {
  const flink = engine === "flink";
  return {
    id: flink ? VISUAL_FLINK_DESTINATION_ID : VISUAL_TRINO_DESTINATION_ID,
    name: flink ? "visual-flink" : "visual-trino",
    engine,
    config: {},
    secret_ref: null,
    offpeak_start: 1,
    offpeak_end: 7,
    enabled: true,
    query_log_config: null,
    query_log_retention_days: null,
    created_at: VISUAL_NOW_ISO,
    updated_at: VISUAL_NOW_ISO,
    support: flink ? "unsupported" : "supported",
    unsupported_reason: flink ? "Flink maintenance is not executed by Cryo." : null
  };
}

function capabilitiesBody(unsupported: boolean) {
  return {
    schema_version: "visual-1",
    source: "visual-fixtures",
    maturity_definitions: [{ tier: unsupported ? "blocked" : "ga", meaning: "Visual fixture maturity." }],
    operations: [
      {
        operation: "optimize",
        label: "Optimize",
        description: "Rewrite data files.",
        destructive: false
      }
    ],
    engines: [
      {
        engine: unsupported ? "flink" : "trino",
        label: unsupported ? "Flink" : "Trino",
        maturity: unsupported ? "blocked" : "ga",
        execution_available: !unsupported,
        summary: unsupported
          ? "Flink is not an execution engine for Cryo maintenance."
          : "Synthetic Trino destination.",
        limitations: unsupported ? ["Unsupported for maintenance writes."] : [],
        owner: "visual-fixtures",
        evidence: []
      }
    ],
    catalogs: [],
    matrix: [
      {
        engine: unsupported ? "flink" : "trino",
        operation: "optimize",
        support: unsupported ? "unsupported" : "supported",
        maturity: unsupported ? "blocked" : "ga",
        execution_mode: unsupported ? "none" : "trino_sql",
        effect_strategy: "no_external_effect",
        runtime_conditions: [],
        limitations: unsupported ? ["Flink maintenance is not executed by Cryo."] : [],
        fallback_engines: [],
        owner: "visual-fixtures",
        evidence: []
      }
    ]
  };
}

function measurementTotals(profile: VisualProfile) {
  if (profile.empty || profile.flags.unknown) {
    return {
      total_tables: profile.empty ? 0 : null,
      total_size_gb: null,
      small_files_count: null,
      estimated_monthly_s3_cost_usd: null,
      projected_monthly_s3_cost_usd: null,
      projected_savings_usd_month: null,
      reclaimable_bytes: null,
      needs_compaction_count: null
    };
  }
  const tiny = profile.flags.tinyBytes !== "none";
  return {
    total_tables: profile.tablesTotal,
    total_size_gb: tiny ? TINY_SIZE_GB : 12.5,
    small_files_count: tiny ? 3 : 42,
    estimated_monthly_s3_cost_usd: null,
    projected_monthly_s3_cost_usd: null,
    projected_savings_usd_month: null,
    reclaimable_bytes: tiny ? TINY_SIZE_BYTES : 13_421_772_800,
    needs_compaction_count: 1
  };
}

function dashboardBody(profile: VisualProfile) {
  const asOf = profile.flags.stale ? STALE_AS_OF : profile.flags.unknown || profile.empty ? null : VISUAL_NOW_ISO;
  const featured = profile.empty ? [] : tablesPage(0, 1, profile.tablesTotal, profile.flags);
  return {
    totals: measurementTotals(profile),
    grade_distribution: profile.flags.unknown
      ? [{ grade: "Not graded", count: null }]
      : profile.empty
        ? []
        : [
            { grade: "A", count: 1 },
            { grade: "B", count: 0 },
            { grade: "C", count: 1 },
            { grade: "D", count: 0 },
            { grade: "F", count: 0 },
            { grade: "Not graded", count: 1 }
          ],
    freshness_distribution: [],
    freshness_as_of: asOf,
    top_offenders: featured.map((table) => ({
      table_id: table.id,
      schema_name: table.schema_name,
      table_name: table.table_name,
      grade: table.stats?.grade ?? null,
      overall_score: table.stats?.overall_score ?? null,
      total_size_gb: table.stats?.total_size_gb ?? null,
      total_files: table.stats?.total_files ?? null,
      small_files_count: table.stats?.small_files_count ?? null,
      estimated_monthly_s3_cost_usd: null
    })),
    drift: profile.flags.partial
      ? [{ day: "2026-09-27", is_partial: true, tables_counted: 8, tables_expected: 12 }]
      : [],
    measurement_state: profile.flags.partial ? "partial" : profile.flags.unknown ? "unknown" : profile.empty ? "empty" : "complete",
    measured_actions: profile.flags.partial ? 8 : null,
    succeeded_actions: profile.flags.partial ? 12 : null,
    stale: profile.flags.stale,
    setup_required: profile.empty,
    timezone: VISUAL_TIMEZONE
  };
}

function vitalsBody(profile: VisualProfile) {
  return {
    generated_at: profile.flags.stale ? STALE_AS_OF : VISUAL_NOW_ISO,
    score: profile.flags.unknown || profile.empty ? null : 72,
    status: profile.flags.unknown || profile.empty ? null : "DEGRADED",
    headline: profile.empty ? null : "visual fixture",
    deductions: [],
    impact: {
      direction: null,
      measurement_state: profile.flags.partial ? "partial" : profile.flags.unknown ? "unknown" : "complete",
      gb_freed_yesterday: profile.flags.unknown ? null : profile.flags.tinyBytes !== "none" ? TINY_SIZE_GB : 1.5,
      measured: profile.flags.partial ? 8 : profile.flags.unknown ? null : 1,
      succeeded: profile.flags.partial ? 12 : profile.flags.unknown ? null : 1
    },
    stale: profile.flags.stale
  };
}

function tablesBody(profile: VisualProfile, url: URL) {
  const limit = clampPageSize(numberParam(url, "limit", DEFAULT_TABLE_PAGE_SIZE), DEFAULT_TABLE_PAGE_SIZE);
  const offset = Math.max(0, Math.floor(numberParam(url, "offset", 0)));
  const items = tablesPage(offset, limit, profile.tablesTotal, profile.flags);
  return {
    items,
    total: profile.tablesTotal,
    limit,
    offset,
    cursor: null,
    has_more: offset + items.length < profile.tablesTotal
  };
}

function jobsBody(profile: VisualProfile, url: URL, runId: string) {
  const limit = clampPageSize(numberParam(url, "limit", DEFAULT_JOB_PAGE_SIZE), DEFAULT_JOB_PAGE_SIZE);
  const offset = Math.max(0, Math.floor(numberParam(url, "offset", 0)));
  const run = { ...visualRun(profile.jobsTotal, profile.flags), id: runId };
  const jobs = jobsPage(offset, limit, profile.jobsTotal, profile.flags).map((job) => ({ ...job, run_id: runId }));
  return {
    run,
    jobs,
    total: profile.jobsTotal,
    limit,
    offset,
    has_more: offset + jobs.length < profile.jobsTotal
  };
}

function snapshotsBody(profile: VisualProfile, url: URL, tableId: string) {
  const page = Math.max(1, Math.floor(numberParam(url, "page", 1)));
  const pageSize = clampPageSize(numberParam(url, "page_size", DEFAULT_SNAPSHOT_PAGE_SIZE), DEFAULT_SNAPSHOT_PAGE_SIZE);
  const snapshots = snapshotsPage(page, pageSize, profile.snapshotTotal, profile.flags);
  return {
    table_id: tableId,
    current_snapshot_id: profile.snapshotTotal === 0 ? null : SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER,
    total: profile.snapshotTotal,
    pinned_count: profile.snapshotTotal === 0 ? 0 : 1,
    latest_committed_at: profile.flags.unknown || profile.empty ? null : VISUAL_NOW_ISO,
    oldest_committed_at:
      profile.snapshotTotal > 1 && profile.flags.previousYear !== "none" ? PREVIOUS_YEAR_COMMITTED_AT : null,
    page,
    page_size: pageSize,
    snapshots
  };
}

function normalizePath(pathname: string): string {
  if (pathname.length > 1 && pathname.endsWith("/")) return pathname.slice(0, -1);
  return pathname;
}

export function resolveVisualResponse(
  scenario: VisualScenario,
  method: string,
  url: URL,
  posted?: unknown
): VisualHttpResponse {
  const path = normalizePath(url.pathname);
  const verb = method.toUpperCase();
  if (scenario === "api-failure" && !PUBLIC_PATHS.has(path)) {
    return { status: 503, body: { detail: "visual fixture: upstream unavailable" } };
  }
  const profile = profileFor(scenario);
  if (path === "/healthz" || path === "/api/v1/healthz") return { status: 200, body: healthzBody };
  if (path === "/api/v1/auth/config") {
    return {
      status: 200,
      body: {
        mode: "dev",
        dev_token_enabled: true,
        dev_token_enabled_in_prod: false,
        oidc: null
      }
    };
  }
  if (path === "/api/v1/auth/token") return { status: 200, body: tokenBody(profile, posted) };
  if (path === "/api/v1/runtime") {
    return {
      status: 200,
      body: {
        native_engine_enabled: !profile.flags.unsupportedEngine,
        maintenance_writes_enabled: profile.writesEnabled,
        native_engine_fallback: "the configured Spark SQL fallback"
      }
    };
  }
  if (path === "/api/v1/capabilities") {
    return { status: 200, body: capabilitiesBody(profile.flags.unsupportedEngine) };
  }
  if (path === "/api/v1/sources") {
    return { status: 200, body: profile.empty ? [] : [sourceRow(true)] };
  }
  if (path === "/api/v1/destinations") {
    return {
      status: 200,
      body: profile.empty ? [] : [destinationRow(profile.flags.unsupportedEngine ? "flink" : "trino")]
    };
  }
  if (path === "/api/v1/tables") return { status: 200, body: tablesBody(profile, url) };
  const tableSnapshots = path.match(/^\/api\/v1\/tables\/([^/]+)\/snapshots$/);
  if (tableSnapshots) {
    if (profile.empty) return { status: 404, body: { detail: "Table not found" } };
    return { status: 200, body: snapshotsBody(profile, url, tableSnapshots[1]) };
  }
  const tableOne = path.match(/^\/api\/v1\/tables\/([^/]+)$/);
  if (tableOne && tableOne[1] !== "export.csv" && tableOne[1] !== "group-by") {
    if (profile.empty) return { status: 404, body: { detail: "Table not found" } };
    const featured = tablesPage(0, 1, Math.max(profile.tablesTotal, 1), profile.flags)[0];
    return { status: 200, body: { ...featured, id: tableOne[1] } };
  }
  if (path === "/api/v1/runs") {
    return { status: 200, body: profile.empty ? [] : [visualRun(profile.jobsTotal, profile.flags)] };
  }
  const runJobs = path.match(/^\/api\/v1\/runs\/([^/]+)\/jobs$/);
  if (runJobs) return { status: 200, body: jobsBody(profile, url, runJobs[1]) };
  const runOne = path.match(/^\/api\/v1\/runs\/([^/]+)$/);
  if (runOne) {
    return { status: 200, body: { ...visualRun(profile.jobsTotal, profile.flags), id: runOne[1] } };
  }
  if (path === "/api/v1/dashboards/datalake") return { status: 200, body: dashboardBody(profile) };
  if (path === "/api/v1/vitals") return { status: 200, body: vitalsBody(profile) };
  if (path === "/api/v1/scanner/status") {
    const run = profile.empty ? null : visualRun(profile.jobsTotal, profile.flags);
    return {
      status: 200,
      body: {
        config: {
          id: 1,
          enabled: !profile.empty,
          schedule_cron: "30 2 * * *",
          timezone: VISUAL_TIMEZONE,
          num_chunks: 4,
          full_scan_default: false,
          cost_full_scan_default: false,
          max_concurrent_runs: 1,
          created_at: VISUAL_NOW_ISO,
          updated_at: VISUAL_NOW_ISO
        },
        queue_depth: profile.empty ? 0 : 1,
        latest_run: run,
        active_run: null,
        last_successful_run: run,
        next_scan_at: profile.flags.stale ? STALE_AS_OF : VISUAL_NOW_ISO,
        total_tables: profile.empty ? 0 : profile.tablesTotal,
        tables_with_stats: profile.flags.unknown ? null : profile.empty ? 0 : profile.tablesTotal,
        latest_table_scan_at: profile.empty || profile.flags.unknown ? null : profile.flags.stale ? STALE_AS_OF : VISUAL_NOW_ISO
      }
    };
  }
  if (path === "/api/v1/presets") {
    return {
      status: 200,
      body: profile.empty
        ? []
        : [
            {
              id: VISUAL_PRESET_ID,
              name: "visual-nightly",
              destination_id: profile.flags.unsupportedEngine
                ? VISUAL_FLINK_DESTINATION_ID
                : VISUAL_TRINO_DESTINATION_ID,
              description: "Synthetic visual fixture preset",
              mode: "standard",
              operations: ["optimize", "expire_snapshots", "remove_orphan_files", "optimize_manifests"],
              params: {},
              schedule_cron: "30 2 * * *",
              priority: 1,
              enabled: true,
              guardrails: {},
              is_default: true,
              created_at: VISUAL_NOW_ISO,
              updated_at: VISUAL_NOW_ISO
            }
          ]
    };
  }
  if (verb === "GET") return { status: 200, body: [] };
  return { status: 200, body: { ok: true, subject: VISUAL_SUBJECT } };
}

export type { VisualJob, VisualRun, VisualSnapshot, VisualTable };
