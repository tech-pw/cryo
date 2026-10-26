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

type DemoState = "running" | "queued" | "failed" | "skipped" | "needs_work" | "healthy" | "scan_failing" | "held";

const NIGHTLY_RUN_ID = visualUuid(0x90);

// One row per spec, chosen so every triage view and funnel branch has at least one table.
const MAINTENANCE: { state: DemoState; reason?: string; delta?: number; reads: number; savings?: number }[] = [
  { state: "failed", reason: "Engine timeout during OPTIMIZE", delta: -14, reads: 18_420, savings: 214 },
  { state: "skipped", reason: "Cooldown after a recent run", delta: -6, reads: 9_310, savings: 96 },
  { state: "needs_work", reason: "Waiting for the next nightly run", delta: 2, reads: 2_140, savings: 12 },
  { state: "healthy", delta: 0, reads: 7_802 },
  { state: "healthy", delta: 1, reads: 31_004 },
  { state: "healthy", delta: 0, reads: 4_117 },
  { state: "running", delta: -11, reads: 1_208, savings: 61 },
  { state: "skipped", reason: "Over the nightly budget", delta: -3, reads: 640, savings: 23 },
  { state: "queued", reason: "Waiting for engine capacity", delta: 0, reads: 12_560, savings: 41 },
  { state: "healthy", delta: 4, reads: 3_051, savings: 0.4 },
  { state: "healthy", delta: 0, reads: 210 },
  { state: "healthy", delta: 0, reads: 88 },
  { state: "scan_failing", reason: "Last scan failing", reads: 0, savings: 188 },
  { state: "held", reason: "Metadata missing in storage; retry after Oct 19 02:00 UTC", delta: -2, reads: 455, savings: 18 }
];

const jobRef = (status: string, minutesAgo: number, trigger = "schedule") => ({
  run_id: NIGHTLY_RUN_ID,
  job_id: visualUuid(0x91),
  status,
  trigger,
  started_at: ago(minutesAgo + 12),
  finished_at: ["queued", "running"].includes(status) ? null : ago(minutesAgo)
});

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
    needs_compaction: s.grade === "D" || s.grade === "F" || s.grade === "C",
    query_count_30d: MAINTENANCE[i].reads,
    projected_savings_usd_month: MAINTENANCE[i].savings ?? null
  },
  consecutive_scan_failures: MAINTENANCE[i].state === "scan_failing" ? 3 : 0,
  owner_email: `${s.schema}-team@example.com`,
  maintenance: {
    state: MAINTENANCE[i].state,
    reason: MAINTENANCE[i].reason ?? null,
    workflow_name: i % 3 === 0 ? "nightly-standard" : null,
    workflow_pinned: i % 3 === 0,
    score_delta_7d: MAINTENANCE[i].delta ?? null,
    active_run: ["running", "queued"].includes(MAINTENANCE[i].state) ? jobRef(MAINTENANCE[i].state, 4, "web") : null,
    last_run:
      MAINTENANCE[i].state === "failed"
        ? jobRef("failed", 380)
        : MAINTENANCE[i].state === "scan_failing" || MAINTENANCE[i].state === "needs_work"
          ? null
          : jobRef("succeeded", 380 + i * 90, i % 4 === 0 ? "web" : "schedule"),
    last_selection: null
  }
}));

type DemoTable = (typeof DEMO_TABLES)[number];

const matchesFilter = (table: DemoTable, filter: string): boolean => {
  const state = table.maintenance.state;
  if (filter.includes('"last_job_status"') && filter.includes('"failed"')) return state === "failed";
  if (filter.includes('"last_job_status"')) return state === "running" || state === "queued";
  if (filter.includes('"last_selection_reason"')) return state === "skipped";
  if (filter.includes('"score_delta_7d"')) return (table.maintenance.score_delta_7d ?? 0) <= -10;
  if (filter.includes('"scan_at","op":"="')) return state === "scan_failing";
  if (filter.includes('"scan_at","op":"<"')) return false;
  if (filter.includes('"projected_savings_usd_month","op":">"')) return (table.stats.projected_savings_usd_month ?? 0) > 0;
  if (filter.includes('"needs_compaction","op":"=","value":true')) return table.stats.needs_compaction;
  return true;
};

export function demoTablesFunnel() {
  const nodes = [
    ["active", "Active tables", 14],
    ["not_in_run", "Not in this run", 1],
    ["no_work", "No work needed", 6],
    ["needs_work", "Needs work", 7],
    ["skip_cooldown", "Cooldown", 1, "skipped"],
    ["skip_budget_cap", "Over budget", 1, "skipped"],
    ["skip_excluded", "Not eligible", 1, "skipped"],
    ["selected", "Selected", 4],
    ["succeeded", "Succeeded", 2],
    ["failed", "Failed", 1, "failed"],
    ["in_progress", "In progress", 1, "in-progress"]
  ] as const;
  const link = (source: string, target: string, value: number) => ({ source, target, value });
  return {
    run_id: NIGHTLY_RUN_ID,
    run_status: "partial",
    run_started_at: ago(400),
    run_finished_at: ago(360),
    total_active: 14,
    nodes: nodes.map(([id, label, count, view]) => ({
      id,
      label,
      count,
      view: view ?? (id === "needs_work" ? "needs-work" : null)
    })),
    links: [
      link("active", "not_in_run", 1),
      link("active", "no_work", 6),
      link("active", "needs_work", 7),
      link("needs_work", "skip_cooldown", 1),
      link("needs_work", "skip_budget_cap", 1),
      link("needs_work", "skip_excluded", 1),
      link("needs_work", "selected", 4),
      link("selected", "succeeded", 2),
      link("selected", "failed", 1),
      link("selected", "in_progress", 1)
    ]
  };
}

const byWorst = [...DEMO_TABLES].sort((a, b) => a.stats.overall_score - b.stats.overall_score);
const totalGb = SPECS.reduce((n, s) => n + s.sizeGb, 0);
const totalSmall = SPECS.reduce((n, s) => n + s.small, 0);
const s3Cost = totalGb * 0.023;
const totalFiles = SPECS.reduce((n, s) => n + s.files, 0);

const QUERY_COUNTS: Record<string, number> = { events: 18_420, page_views: 9_310, metrics: 2_115, inventory_snapshots: 640 };
const HOT_AT_RISK = DEMO_TABLES.filter((t) => t.stats.grade === "D" || t.stats.grade === "F").sort(
  (a, b) => (QUERY_COUNTS[b.table_name] ?? 0) - (QUERY_COUNTS[a.table_name] ?? 0)
);
const improved = (index: number, from: [string, number], to: [string, number], minutes: number) => ({
  table_id: DEMO_TABLES[index].id,
  schema_name: DEMO_TABLES[index].schema_name,
  table_name: DEMO_TABLES[index].table_name,
  previous_grade: from[0],
  previous_score: from[1],
  grade: to[0],
  overall_score: to[1],
  improved_at: ago(minutes)
});
const RECENTLY_IMPROVED = [
  improved(9, ["D", 52], ["B", 76], 18),
  improved(3, ["C", 64], ["B", 80], 31),
  improved(5, ["C", 68], ["B", 84], 47)
];

export function demoTablesBody(url: URL) {
  const limit = Number(url.searchParams.get("limit") ?? 50) || 50;
  const offset = Number(url.searchParams.get("offset") ?? 0) || 0;
  const sort = url.searchParams.get("sort") ?? url.searchParams.get("order_by") ?? "";
  const filter = url.searchParams.get("filter") ?? "";
  const ordered = sort.includes("score") || sort.includes("grade") ? byWorst : DEMO_TABLES;
  const rows = ordered.filter((table) => matchesFilter(table, filter));
  const items = rows.slice(offset, offset + limit);
  return { items, total: rows.length, limit, offset, cursor: null, has_more: offset + items.length < rows.length };
}

export function demoDashboardBody() {
  const count = (g: string) => SPECS.filter((s) => s.grade === g).length;
  return {
    totals: {
      total_tables: SPECS.length,
      total_size_gb: totalGb,
      total_files: totalFiles,
      small_files_count: totalSmall,
      running_jobs: 3,
      estimated_monthly_s3_cost_usd: s3Cost,
      projected_monthly_s3_cost_usd: s3Cost * 0.78,
      projected_savings_usd_month: s3Cost * 0.22,
      reclaimable_bytes: Math.round(totalGb * 0.19 * GB),
      needs_compaction_count: SPECS.filter((s) => ["C", "D", "F"].includes(s.grade)).length
    },
    grade_distribution: ["A", "B", "C", "D", "F"].map((grade) => ({ grade, count: count(grade) })).concat([{ grade: "Not graded", count: 0 }]),
    freshness_distribution: [
      ["within_24h", "Updated in last 24 hours", 6], ["1_3d", "Not updated 1-3 days", 3],
      ["3_7d", "Not updated 3-7 days", 2], ["7_15d", "Not updated 7-15 days", 1],
      ["15_30d", "Not updated 15-30 days", 0], ["1_3m", "Not updated 1-3 months", 1],
      ["3_6m", "Not updated 3-6 months", 0], ["6_12m", "Not updated 6-12 months", 0],
      ["over_1y", "Not updated over 1 year", 1], ["unknown", "No snapshot timestamp", 0], ["failed", "Failed / corrupt tables", 0]
    ].map(([key, label, count]) => ({ key, label, count })),
    freshness_as_of: VISUAL_NOW_ISO,
    hot_at_risk: HOT_AT_RISK.map((t) => ({
      table_id: t.id,
      schema_name: t.schema_name,
      table_name: t.table_name,
      grade: t.stats.grade,
      overall_score: t.stats.overall_score,
      total_size_gb: t.stats.total_size_gb,
      total_files: t.stats.total_files,
      small_files_count: t.stats.small_files_count,
      estimated_monthly_s3_cost_usd: t.stats.total_size_gb * 0.023,
      query_count_30d: QUERY_COUNTS[t.table_name] ?? 0
    })),
    hot_at_risk_count: HOT_AT_RISK.length,
    recently_improved: RECENTLY_IMPROVED,
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
    deductions: [
      { reason: "jobs: 1 table failed in the latest nightly run.", points: 8 },
      { reason: "scanner: 2 tables have not been scanned in 36 hours.", points: 8 },
      { reason: "health: 2 tables are at grade F.", points: 8 },
      { reason: "dispatch: Admission waited 4 minutes for a Trino slot.", points: 5 }
    ],
    impact: { direction: "improving", measurement_state: "complete", gb_freed_yesterday: 38.4, wow_pct: 12.4, measured: 9, succeeded: 9 },
    stale: false
  };
}

const OPS = ["optimize", "expire_snapshots", "remove_orphan_files", "optimize_manifests"] as const;
const JOB_TABLES = byWorst.slice(0, 9);

export const DEMO_RUN_ID = "7f3c9a21-4b8e-4d2a-9c1f-2e6b8d0a5c43";

function batchSummary(selected: number, failed: number, partial: number, skipped: number) {
  return {
    pipeline: "batch",
    selected,
    succeeded: selected - failed - partial - skipped,
    failed,
    partial,
    skipped,
    planning_failed: 0,
    nothing_to_do: 0
  };
}

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
  summary: {
    jobs_total: JOB_TABLES.length,
    measurement_state: "complete",
    measured_actions: 34,
    succeeded_actions: 33,
    ...batchSummary(JOB_TABLES.length, 1, 0, 1)
  },
  realized_bytes_freed: Math.round(38.4 * GB),
  realized_actions_measured: 34,
  realized_actions_succeeded: 33
};

const HISTORY_PREFIXES = [
  "a92e1c40", "5d7b3f18", "e03a9b67", "1fc84d2a", "b6e5027c", "48d1a9e3", "9a3f6c05",
  "3c7e91ab", "d2f4086e", "7b19c3d5", "f08a2e64", "26d5b7c9", "8e3a10f2"
];

const HISTORY_SHAPE: Array<{ status: string; minutes: number; freedGb: number; failed: number; partial: number }> = [
  { status: "succeeded", minutes: 78, freedGb: 31.2, failed: 0, partial: 0 },
  { status: "succeeded", minutes: 91, freedGb: 44.7, failed: 0, partial: 0 },
  { status: "failed", minutes: 23, freedGb: 0, failed: 9, partial: 0 },
  { status: "succeeded", minutes: 69, freedGb: 27.5, failed: 0, partial: 0 },
  { status: "partial", minutes: 104, freedGb: 18.9, failed: 0, partial: 2 },
  { status: "succeeded", minutes: 74, freedGb: 35.1, failed: 0, partial: 0 },
  { status: "succeeded", minutes: 88, freedGb: 40.3, failed: 0, partial: 0 },
  { status: "succeeded", minutes: 81, freedGb: 29.8, failed: 0, partial: 0 },
  { status: "partial", minutes: 97, freedGb: 22.4, failed: 1, partial: 1 },
  { status: "succeeded", minutes: 72, freedGb: 33.0, failed: 0, partial: 0 },
  { status: "succeeded", minutes: 76, freedGb: 30.6, failed: 0, partial: 0 },
  { status: "succeeded", minutes: 84, freedGb: 37.9, failed: 0, partial: 0 },
  { status: "succeeded", minutes: 79, freedGb: 34.2, failed: 0, partial: 0 }
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
      summary: { ...DEMO_RUN.summary, ...batchSummary(JOB_TABLES.length, shape.failed, shape.partial, 0) },
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
      config: { host: "trino", port: 8080, catalog: "lakehouse", catalog_bindings: [{ source_id: VISUAL_SOURCE_ID }] },
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

// Jobless operator changes; the first three land back to back between two nightly runs.
const CHANGE_ROWS: Array<[action: string, detail: string, minsAgo: number, actor: string]> = [
  ["table_config_change", "cooldown_hours: 24 -> 12", 5 * 24 * 60 + 40, "priya@acme.io"],
  ["apply_table_properties", "write.target-file-size-bytes: 134217728 -> 536870912", 5 * 24 * 60 + 35, "property-autopilot"],
  ["table_config_change", "min_small_files: 50 -> 20", 4 * 24 * 60 + 300, "priya@acme.io"],
  ["table_config_change", "retention_days: 7 -> 5", 9 * 60, "dev@acme.io"]
];

export function demoAuditBody() {
  return {
    items: [
      ...CHANGE_ROWS.map(([action, detail, mins, actor], i) => ({
        id: 8900 + i,
        created_at: ago(mins),
        ts: ago(mins),
        action,
        status: "OK",
        detail,
        engine: null,
        job_id: null,
        duration_ms: null,
        actual_bytes_freed: null,
        input_files_count: null,
        output_files_count: null,
        actor,
        effect_status: null
      })),
      ...auditReceipts()
    ]
  };
}

function auditReceipts() {
  return AUDIT_ROWS.map(([action, status, detail, mins, duration, freed, input], i) => ({
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
  }));
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
      { destination_id: VISUAL_TRINO_DESTINATION_ID, name: "demo-trino", engine: "trino", slots_used: 2, slots_limit: 6, admission: "open", live_probe: "4 running queries, 0 queued" },
      { destination_id: "00000000-0000-4000-8000-0000000000e2", name: "cryo-native", engine: "native", slots_used: 1, slots_limit: 4, admission: "open", live_probe: "not recorded" }
    ],
    workers_online: 4
  };
}

export function demoPendingApprovals() {
  return [{ id: visualUuid(0xb1), table_id: DEMO_TABLES[12].id, operation: "remove_orphan_files", requested_at: ago(55) }];
}

export function demoFastPathFindings() {
  return [
    { table_id: DEMO_TABLES[13].id, table_name: "marketing.campaign_touches", severity: "warning", finding_type: "small_files", reason: "3,015 of 5,010 data files are under the target size.", detected_at: ago(140) },
    { table_id: DEMO_TABLES[7].id, table_name: "ops.audit_log", severity: "info", finding_type: "missing_target_file_size", reason: "write.target-file-size-bytes is not set; Trino writes 128 MB files.", detected_at: ago(310) }
  ];
}

export function demoFleetTrends() {
  const dfByDay = [7, 7, 6, 6, 6, 5, 6, 5, 5, 5, 5, 4, 6, 4];
  const day = (offset: number) => new Date(NOW - offset * 86_400_000).toISOString().slice(0, 10);
  return {
    days: 14,
    health_trend: dfByDay.map((df, i) => ({
      day: day(dfByDay.length - 1 - i),
      avg_health_score: 60 + i,
      tables_at_grade_df: df,
      is_partial: i === dfByDay.length - 1,
      tables_counted: SPECS.length
    })),
    cost_trend: []
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
