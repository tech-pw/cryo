import {
  VISUAL_NOW_ISO,
  VISUAL_PRESET_ID,
  VISUAL_SOURCE_ID,
  VISUAL_TRINO_DESTINATION_ID,
  visualUuid
} from "../visual/fixtures/constants";

// Synthetic demo lake for docs-site screenshots. Names mirror the shapes the local `make seed`
// lake produces; nothing here comes from a real deployment.

const NOW = Date.parse(VISUAL_NOW_ISO);
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
const GB = 1024 ** 3;

interface DemoSpec {
  schema: string;
  table: string;
  grade: "A" | "B" | "C" | "D" | "F";
  score: number;
  sizeGb: number;
  files: number;
  small: number;
  snapshots: number;
}

const SPECS: DemoSpec[] = [
  { schema: "analytics", table: "events", grade: "F", score: 28, sizeGb: 412.6, files: 48_210, small: 41_930, snapshots: 1_284 },
  { schema: "analytics", table: "page_views", grade: "D", score: 52, sizeGb: 268.1, files: 19_877, small: 14_102, snapshots: 642 },
  { schema: "billing", table: "invoices", grade: "C", score: 68, sizeGb: 96.4, files: 4_120, small: 1_860, snapshots: 310 },
  { schema: "billing", table: "payments", grade: "B", score: 80, sizeGb: 58.2, files: 1_240, small: 210, snapshots: 96 },
  { schema: "crm", table: "accounts", grade: "A", score: 92, sizeGb: 12.8, files: 96, small: 4, snapshots: 21 },
  { schema: "crm", table: "contacts", grade: "B", score: 84, sizeGb: 22.5, files: 310, small: 48, snapshots: 44 },
  { schema: "ops", table: "metrics", grade: "D", score: 48, sizeGb: 181.9, files: 22_640, small: 18_005, snapshots: 2_010 },
  { schema: "ops", table: "audit_log", grade: "C", score: 64, sizeGb: 74.3, files: 6_450, small: 3_920, snapshots: 488 },
  { schema: "sales", table: "orders", grade: "C", score: 64, sizeGb: 133.7, files: 7_812, small: 4_105, snapshots: 402 },
  { schema: "sales", table: "order_items", grade: "B", score: 76, sizeGb: 88.0, files: 2_204, small: 640, snapshots: 188 },
  { schema: "sales", table: "returns", grade: "A", score: 92, sizeGb: 6.1, files: 38, small: 2, snapshots: 12 },
  { schema: "product", table: "catalog", grade: "A", score: 100, sizeGb: 3.4, files: 18, small: 0, snapshots: 9 },
  { schema: "product", table: "inventory_snapshots", grade: "F", score: 32, sizeGb: 305.2, files: 61_380, small: 57_400, snapshots: 3_120 },
  { schema: "marketing", table: "campaign_touches", grade: "C", score: 60, sizeGb: 47.9, files: 5_010, small: 3_015, snapshots: 266 }
];

export const DEMO_TABLES = SPECS.map((s, i) => ({
  id: visualUuid(0x10 + i),
  source_id: VISUAL_SOURCE_ID,
  schema_name: s.schema,
  table_name: s.table,
  catalog_name: "lakehouse",
  source_name: "demo-rest-catalog",
  qualified_table_name: `lakehouse.${s.schema}.${s.table}`,
  location: `s3://demo-lake/warehouse/${s.schema}/${s.table}`,
  first_seen_at: ago(60 * 24 * 40),
  is_active: true,
  stats: {
    scan_at: ago(25 + i),
    grade: s.grade,
    overall_score: s.score,
    total_files: s.files,
    small_files_count: s.small,
    total_size_gb: s.sizeGb,
    total_size_bytes: Math.round(s.sizeGb * GB),
    total_snapshots: s.snapshots,
    last_snapshot_committed_at: ago(5 + i * 7),
    needs_compaction: s.grade === "D" || s.grade === "F" || s.grade === "C"
  }
}));

const byWorst = [...DEMO_TABLES].sort((a, b) => a.stats.overall_score - b.stats.overall_score);
const totalGb = SPECS.reduce((n, s) => n + s.sizeGb, 0);
const totalSmall = SPECS.reduce((n, s) => n + s.small, 0);
const s3Cost = totalGb * 0.023;

export function demoTablesBody(url: URL) {
  const limit = Number(url.searchParams.get("limit") ?? 50) || 50;
  const offset = Number(url.searchParams.get("offset") ?? 0) || 0;
  const sort = url.searchParams.get("sort") ?? url.searchParams.get("order_by") ?? "";
  const rows = sort.includes("score") || sort.includes("grade") ? byWorst : DEMO_TABLES;
  const items = rows.slice(offset, offset + limit);
  return { items, total: rows.length, limit, offset, cursor: null, has_more: offset + items.length < rows.length };
}

export function demoDashboardBody() {
  const count = (g: string) => SPECS.filter((s) => s.grade === g).length;
  return {
    totals: {
      total_tables: SPECS.length,
      total_size_gb: totalGb,
      small_files_count: totalSmall,
      estimated_monthly_s3_cost_usd: s3Cost,
      projected_monthly_s3_cost_usd: s3Cost * 0.78,
      projected_savings_usd_month: s3Cost * 0.22,
      reclaimable_bytes: Math.round(totalGb * 0.19 * GB),
      needs_compaction_count: SPECS.filter((s) => ["C", "D", "F"].includes(s.grade)).length
    },
    grade_distribution: ["A", "B", "C", "D", "F"].map((grade) => ({ grade, count: count(grade) })).concat([{ grade: "Not graded", count: 0 }]),
    freshness_distribution: [],
    freshness_as_of: VISUAL_NOW_ISO,
    top_offenders: byWorst.slice(0, 6).map((t) => ({
      table_id: t.id,
      schema_name: t.schema_name,
      table_name: t.table_name,
      grade: t.stats.grade,
      overall_score: t.stats.overall_score,
      total_size_gb: t.stats.total_size_gb,
      total_files: t.stats.total_files,
      small_files_count: t.stats.small_files_count,
      estimated_monthly_s3_cost_usd: t.stats.total_size_gb * 0.023
    })),
    drift: [],
    measurement_state: "complete",
    measured_actions: null,
    succeeded_actions: null,
    stale: false,
    setup_required: false,
    timezone: "Asia/Kolkata"
  };
}

export function demoVitalsBody() {
  return {
    generated_at: VISUAL_NOW_ISO,
    score: 71,
    status: "DEGRADED",
    headline: "Two tables need compaction before the next peak window",
    deductions: [],
    impact: { direction: "improving", measurement_state: "complete", gb_freed_yesterday: 38.4, measured: 9, succeeded: 9 },
    stale: false
  };
}

const OPS = ["optimize", "expire_snapshots", "remove_orphan_files", "optimize_manifests"] as const;
const JOB_TABLES = byWorst.slice(0, 9);

export const DEMO_RUN_ID = "7f3c9a21-4b8e-4d2a-9c1f-2e6b8d0a5c43";

export const DEMO_RUN = {
  id: DEMO_RUN_ID,
  kind: "maintenance",
  status: "succeeded",
  preset_id: VISUAL_PRESET_ID,
  trigger: "schedule",
  deadline_at: ago(-240),
  scheduled_at: ago(95),
  created_at: ago(95),
  finished_at: ago(12),
  summary: { jobs_total: JOB_TABLES.length, measurement_state: "complete", measured_actions: 34, succeeded_actions: 33 },
  realized_bytes_freed: Math.round(38.4 * GB),
  realized_actions_measured: 34,
  realized_actions_succeeded: 33
};

const HISTORY_PREFIXES = ["a92e1c40", "5d7b3f18", "e03a9b67", "1fc84d2a", "b6e5027c", "48d1a9e3", "9a3f6c05"];

const HISTORY_SHAPE: Array<{ status: string; minutes: number; freedGb: number }> = [
  { status: "succeeded", minutes: 78, freedGb: 31.2 },
  { status: "succeeded", minutes: 91, freedGb: 44.7 },
  { status: "failed", minutes: 23, freedGb: 0 },
  { status: "succeeded", minutes: 69, freedGb: 27.5 },
  { status: "partial", minutes: 104, freedGb: 18.9 },
  { status: "succeeded", minutes: 74, freedGb: 35.1 },
  { status: "succeeded", minutes: 88, freedGb: 40.3 }
];

export const DEMO_RUN_HISTORY = [
  {
    ...DEMO_RUN,
    id: "c41d07e2-5a9b-4f13-8e2d-61b0f7a3d915",
    status: "running",
    preset_id: null,
    trigger: "manual",
    scheduled_at: ago(6),
    created_at: ago(6),
    finished_at: null,
    summary: { jobs_total: 3 },
    realized_bytes_freed: null
  },
  DEMO_RUN,
  ...HISTORY_SHAPE.map((shape, i) => {
    const started = 95 + (i + 1) * 1440;
    return {
      ...DEMO_RUN,
      id: `${HISTORY_PREFIXES[i]}-4b8e-4d2a-9c1f-2e6b8d0a5c4${i}`,
      status: shape.status,
      scheduled_at: ago(started),
      created_at: ago(started),
      finished_at: ago(started - shape.minutes),
      realized_bytes_freed: shape.status === "failed" ? null : Math.round(shape.freedGb * GB)
    };
  })
];

export function demoMaintenanceControls() {
  return [
    {
      scope_type: "destination",
      scope_id: VISUAL_TRINO_DESTINATION_ID,
      paused: true,
      generation: 3,
      reason: "Trino upgrade window, hold rewrites until the coordinator is back",
      changed_by: "demo",
      updated_at: ago(40)
    }
  ];
}

export function demoSafetySummary() {
  return {
    intents_by_status: {},
    effects_by_state: {},
    ambiguous_effects: 0,
    active_attempts: 0,
    active_pauses: 1
  };
}

export function demoJobsBody(runId: string) {
  const jobs = JOB_TABLES.map((t, i) => ({
    id: visualUuid(0x10000 + i),
    run_id: runId,
    table_id: t.id,
    destination_id: VISUAL_TRINO_DESTINATION_ID,
    schema_name: t.schema_name,
    table_name: t.table_name,
    destination_name: "demo-trino",
    destination_engine: "trino",
    mode: "standard",
    status: i === 4 ? "failed" : i === 7 ? "skipped" : "succeeded",
    started_at: ago(90 - i * 8),
    finished_at: ago(84 - i * 8),
    error: i === 4 ? "expire_snapshots: retention would drop a snapshot referenced by tag 'q3-close'" : null,
    operations: OPS
  }));
  return { run: { ...DEMO_RUN, id: runId }, jobs, total: jobs.length, limit: 50, offset: 0, has_more: false };
}

export function demoScannerStatus() {
  return {
    config: {
      id: 1,
      enabled: true,
      schedule_cron: "30 2 * * *",
      timezone: "Asia/Kolkata",
      num_chunks: 4,
      full_scan_default: false,
      cost_full_scan_default: false,
      max_concurrent_runs: 1,
      created_at: VISUAL_NOW_ISO,
      updated_at: VISUAL_NOW_ISO
    },
    queue_depth: 0,
    latest_run: { ...DEMO_RUN, kind: "scan" },
    active_run: null,
    last_successful_run: { ...DEMO_RUN, kind: "scan" },
    next_scan_at: ago(-600),
    total_tables: SPECS.length,
    tables_with_stats: SPECS.length,
    latest_table_scan_at: ago(25)
  };
}

export function demoSources() {
  return [
    {
      id: VISUAL_SOURCE_ID,
      name: "demo-rest-catalog",
      type: "rest",
      config: { uri: "http://iceberg-rest:8181" },
      secret_ref: null,
      enabled: true,
      maintenance_schema_names: ["analytics", "billing", "crm", "ops", "sales", "product", "marketing"],
      created_at: VISUAL_NOW_ISO,
      updated_at: VISUAL_NOW_ISO
    }
  ];
}

export function demoDestinations() {
  return [
    {
      id: VISUAL_TRINO_DESTINATION_ID,
      name: "demo-trino",
      engine: "trino",
      config: { host: "trino", port: 8080, catalog: "lakehouse" },
      secret_ref: null,
      offpeak_start: 1,
      offpeak_end: 7,
      enabled: true,
      query_log_config: null,
      query_log_retention_days: null,
      created_at: VISUAL_NOW_ISO,
      updated_at: VISUAL_NOW_ISO,
      support: "supported",
      unsupported_reason: null
    },
    {
      id: "00000000-0000-4000-8000-0000000000e2",
      name: "cryo-native",
      engine: "native",
      config: { slot_cap: 4 },
      secret_ref: null,
      offpeak_start: null,
      offpeak_end: null,
      enabled: true,
      query_log_config: null,
      query_log_retention_days: null,
      created_at: VISUAL_NOW_ISO,
      updated_at: VISUAL_NOW_ISO,
      support: "supported",
      unsupported_reason: null
    }
  ];
}

export function demoPresets() {
  return [
    {
      id: VISUAL_PRESET_ID,
      name: "nightly-standard",
      destination_id: VISUAL_TRINO_DESTINATION_ID,
      description: "Compact, then clean up. Off-peak, load-aware.",
      mode: "standard",
      operations: ["optimize", "compute_statistics", "optimize_manifests", "expire_snapshots", "remove_orphan_files"],
      params: {},
      schedule_cron: "30 2 * * *",
      priority: 1,
      enabled: true,
      guardrails: {},
      is_default: true,
      created_at: VISUAL_NOW_ISO,
      updated_at: VISUAL_NOW_ISO
    }
  ];
}

export function demoAccessToken(): string {
  const encode = (value: object) => Buffer.from(JSON.stringify(value), "utf8").toString("base64");
  const payload = { sub: "demo", preferred_username: "demo", roles: ["operator"], exp: 1893456000 };
  return `${encode({ alg: "none", typ: "JWT" })}.${encode(payload)}.visual`;
}

const AUDIT_ROWS: Array<[string, string, string, number, number | null, number | null, number | null]> = [
  // action, status, detail, minutes ago, duration ms, bytes freed, input files
  ["remove_orphan_files", "succeeded", "Deleted 1,912 unreferenced files older than 3 days", 14, 51_000, 6.2 * GB, null],
  ["expire_snapshots", "succeeded", "Expired 1,170 snapshots; kept 7 days and tag q3-close", 16, 18_000, 9.8 * GB, null],
  ["optimize_manifests", "succeeded", "Rewrote 412 manifests into 9", 18, 9_000, null, null],
  ["compute_statistics", "succeeded", "Refreshed column statistics for 14 columns", 19, 22_000, null, null],
  ["optimize", "succeeded", "Chunk 3/3: 11 partitions, event_date 2026-09-19..2026-09-27", 24, 98_000, 4.1 * GB, 12_410],
  ["optimize", "succeeded", "Chunk 2/3: 14 partitions, event_date 2026-09-05..2026-09-18", 29, 141_000, 5.6 * GB, 15_882],
  ["optimize", "deferred", "Trino busy: 6/6 slots used; retried after 4m", 37, null, null, null],
  ["optimize", "succeeded", "Chunk 1/3: 16 partitions, event_date 2026-08-20..2026-09-04", 41, 133_000, 6.3 * GB, 13_638]
];

export function demoAuditBody() {
  return {
    items: AUDIT_ROWS.map(([action, status, detail, mins, duration, freed, input], i) => ({
      id: 9000 + i,
      created_at: ago(mins),
      action,
      status,
      detail,
      engine: "trino",
      job_id: visualUuid(0xa1),
      duration_ms: duration,
      actual_bytes_freed: freed === null ? null : Math.round(freed),
      input_files_count: input,
      output_files_count: input === null ? null : Math.round(input / 96),
      actor: "scheduler",
      effect_status: status === "succeeded" ? (freed ? "bytes_reported" : "files_reported") : "unverified"
    }))
  };
}

// Scores are 100 minus 8-point warnings and 20-point criticals, so only these values exist.
const REACHABLE = Array.from({ length: 13 }, (_, b) => b).flatMap((b) => Array.from({ length: 13 }, (_, a) => 100 - 8 * a - 20 * b)).filter((v) => v >= 0);
const snapScore = (raw: number) => REACHABLE.reduce((best, v) => (Math.abs(v - raw) < Math.abs(best - raw) ? v : best), 100);

export function demoHealthTrends(tableId: string) {
  const t = DEMO_TABLES.find((x) => x.id === tableId) ?? DEMO_TABLES[0];
  const points = Array.from({ length: 30 }, (_, i) => {
    const day = 29 - i;
    const decay = i / 29;
    const score = snapScore(80 - decay * 52);
    const grade = score >= 90 ? "A" : score >= 75 ? "B" : score >= 60 ? "C" : score >= 45 ? "D" : "F";
    return {
      timestamp: ago(day * 24 * 60),
      grade,
      score,
      small_files: Math.round(6_000 + decay * 36_000),
      total_size_gb: Math.round((360 + decay * 52) * 10) / 10,
      snapshot_count: Math.round(200 + decay * 1_080),
      projected_savings_usd_month: Math.round((1.2 + decay * 4.6) * 100) / 100
    };
  });
  return {
    table_id: t.id,
    table_name: `${t.schema_name}.${t.table_name}`,
    current_grade: t.stats.grade,
    current_score: t.stats.overall_score,
    trend_points: points,
    degradation_rate: -1.6,
    days_to_critical: null
  };
}

export function demoEffectivePreset(tableId: string) {
  return { table_id: tableId, preset_id: VISUAL_PRESET_ID, preset_name: "nightly-standard", provenance: "default", rule_id: null };
}

export function demoSignals(tableId: string) {
  return {
    table_id: tableId,
    checked_at: VISUAL_NOW_ISO,
    any_triggered: true,
    signals: [
      { triggered: true, signal_name: "small_files_ratio", reason: "87% of data files are under the target size", severity: "critical", metric_value: 0.87, threshold: 0.3 },
      { triggered: true, signal_name: "snapshot_count", reason: "1,284 snapshots retained; policy keeps 7 days", severity: "warning", metric_value: 1284, threshold: 500 },
      { triggered: false, signal_name: "manifest_count", reason: "Manifest count within budget", severity: "info", metric_value: 96, threshold: 400 }
    ]
  };
}

export function demoTableCost(tableId: string) {
  const t = DEMO_TABLES.find((x) => x.id === tableId) ?? DEMO_TABLES[0];
  const live = t.stats.total_size_bytes;
  const dead = Math.round(live * 0.31);
  const monthly = ((live + dead) / GB) * 0.023;
  const orphan = Math.round(6.2 * GB);
  const rate = (bytes: number) => (bytes / GB) * 0.023;
  return {
    table_id: t.id,
    precision: "estimated",
    price_per_gb_month: 0.023,
    current_monthly_cost_usd: monthly,
    projected_monthly_cost_usd: (live / GB) * 0.023,
    projected_savings_usd_month: rate(dead + orphan),
    query_request_savings_usd_month: 3.4,
    live_bytes: live,
    dead_bytes: dead,
    metadata_bytes: Math.round(1.8 * GB),
    orphan_bytes: orphan,
    listed_bytes: null,
    reclaimable_bytes: dead + orphan,
    by_operation: [
      { operation: "expire_snapshots", reclaimable_bytes: dead, savings_usd_month: rate(dead) },
      { operation: "remove_orphan_files", reclaimable_bytes: orphan, savings_usd_month: rate(orphan) }
    ],
    last_full_cost_scan_at: ago(60 * 26),
    scan_at: ago(25)
  };
}

export function demoEngineLoad() {
  return {
    engines: [
      { destination_id: VISUAL_TRINO_DESTINATION_ID, name: "demo-trino", engine: "trino", slots_used: 2, slots_limit: 6, admission: "admitting", live_probe: "4 running queries, 0 queued" }
    ]
  };
}

export function demoMultiCatalog() {
  return {
    catalogs: [
      {
        catalog_id: VISUAL_SOURCE_ID,
        catalog_name: "demo-rest-catalog",
        catalog_type: "rest",
        table_count: SPECS.length,
        total_size_gb: totalGb,
        small_files_count: totalSmall,
        needs_compaction_count: SPECS.filter((x) => ["C", "D", "F"].includes(x.grade)).length,
        monthly_cost_usd: s3Cost,
        projected_savings_usd_month: s3Cost * 0.22,
        avg_health_score: Math.round(SPECS.reduce((n, x) => n + x.score, 0) / SPECS.length),
        enabled: true
      }
    ],
    total_catalogs: 1,
    total_tables: SPECS.length,
    total_cost_usd: s3Cost
  };
}
