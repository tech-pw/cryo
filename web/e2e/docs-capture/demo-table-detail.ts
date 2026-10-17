import type { components } from "../../src/api/openapi";
import { VISUAL_NOW_ISO, visualUuid } from "../visual/fixtures/constants";
import { DEMO_TABLES } from "./demo-data";

// Per-tab responses for the docs-site table detail screenshots. Synthetic: the numbers agree
// with the `analytics.events` row in demo-data.ts, nothing comes from a real deployment.

type S = components["schemas"];

const NOW = Date.parse(VISUAL_NOW_ISO);
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
const day = (daysAgo: number) => new Date(NOW - daysAgo * 86_400_000).toISOString().slice(0, 10);
const MIB = 2 ** 20;
const GB = 1024 ** 3;

const EVENTS = DEMO_TABLES[0];
const name = `${EVENTS.schema_name}.${EVENTS.table_name}`;

// bin i holds files of [2^i, 2^(i+1)) bytes; totals match 48,210 files / 41,930 small.
const bins = Array.from({ length: 40 }, (_, i) =>
  ({ 18: 4_130, 19: 3_800, 20: 11_000, 21: 13_000, 22: 7_000, 23: 3_000, 24: 3_600, 25: 1_500, 26: 700, 27: 300, 28: 180 })[i] ?? 0
);

export function demoTableDetail(id: string) {
  const t = DEMO_TABLES.find((x) => x.id === id) ?? EVENTS;
  if (t.id !== EVENTS.id) return t;
  return {
    ...t,
    owner_email: "analytics-eng@acme.io",
    stats: {
      ...t.stats,
      table_id: t.id,
      total_partitions: 412,
      max_files_per_partition: 1_380,
      avg_files_per_partition: 117,
      partition_skew_ratio: 11.8,
      total_records: 9_420_000_000,
      avg_file_size_mb: 8.8,
      total_delete_files: 1_940,
      position_delete_files: 1_940,
      equality_delete_files: 0,
      delete_file_pct: 3.9,
      manifest_count: 96,
      partition_col: "event_date",
      partition_col_type: "date",
      write_distribution_mode: "none",
      has_sort_order: true,
      target_file_size_bytes: 128 * MIB,
      query_count_30d: 18_240,
      raw: {
        reduction_target_bytes: 128 * MIB,
        file_size_bins: { data: bins },
        commits_per_day: 42.8,
        snapshot_overhead_gb: 128.4,
        health_deductions: [
          { label: "Small-file fragmentation", deduction: 20, severity: "critical", reason: "87% of data files are under the 128 MiB write target." },
          { label: "Snapshot retention buildup", deduction: 20, severity: "critical", reason: "1,284 snapshots retained; the table policy keeps 7 days." },
          { label: "Delete files", deduction: 8, severity: "warning", reason: "1,940 position-delete files are merged on every read." },
          { label: "Partition skew", deduction: 8, severity: "warning", reason: "The largest partition holds 11.8× the average file count." },
          { label: "Metadata overhead", deduction: 8, severity: "warning", reason: "96 manifests; several cover a single day." },
          { label: "Orphan files", deduction: 8, severity: "warning", reason: "6.2 GB under the table location is not referenced by any snapshot." }
        ]
      }
    }
  };
}

export function demoTableHistory(id: string) {
  if (id !== EVENTS.id) return { items: [] };
  return {
    items: Array.from({ length: 30 }, (_, i) => {
      const decay = i / 29;
      return {
        scan_at: ago((29 - i) * 24 * 60 + 25),
        small_files_count: Math.round(6_000 + decay * 35_930),
        total_files: Math.round(12_000 + decay * 36_210),
        total_size_gb: Math.round((360 + decay * 52.6) * 10) / 10,
        total_snapshots: Math.round(200 + decay * 1_084)
      };
    })
  };
}

export function demoTableRuns(id: string): S["TableRunsResponse"] {
  if (id !== EVENTS.id) return { table_id: id, items: [], total: 0, limit: 20, offset: 0 };
  const run = (
    n: number,
    trigger: string,
    startedMin: number,
    minutes: number,
    before: [number, number, number],
    after: [number, number, number]
  ): S["TableRunRead"] => ({
    job_id: visualUuid(0xa0 + n),
    run_id: visualUuid(0xb0 + n),
    status: "succeeded",
    trigger,
    mode: "standard",
    started_at: ago(startedMin),
    finished_at: ago(startedMin - minutes),
    duration_seconds: minutes * 60,
    error: null,
    steps: ["optimize", "compute_statistics", "optimize_manifests", "expire_snapshots", "remove_orphan_files"].map((operation_type, step_index, all) => ({
      step_index,
      operation_type,
      status: "succeeded",
      started_at: ago(startedMin - (minutes * step_index) / all.length),
      finished_at: ago(startedMin - (minutes * (step_index + 1)) / all.length)
    })),
    before_stats: { scan_at: ago(startedMin + 5), grade: "F", overall_score: before[0], total_files: before[1], small_files_count: before[2], total_size_gb: 412.6 },
    after_stats: { scan_at: ago(startedMin - minutes - 2), grade: "F", overall_score: after[0], total_files: after[1], small_files_count: after[2], total_size_gb: 412.6 }
  });
  const items = [run(1, "manual", 30, 16, [20, 76_337, 70_057], [28, 48_210, 41_930]), run(2, "schedule", 24 * 60 + 150, 41, [20, 74_120, 68_400], [20, 61_204, 55_880])];
  return { table_id: id, items, total: items.length, limit: 20, offset: 0 };
}

const maintenanceStatus = (id: string): S["MaintenanceStatusRead"] => ({
  table_id: id,
  eligible: true,
  evaluated_at: ago(3),
  eligibility_basis: "Latest scan 25 minutes ago; small-file count above the preset minimum.",
  blocking_reasons: [],
  thresholds: { cooldown_hours: 12, max_stats_age_days: 2, min_delete_file_pct: 5 },
  last_actions: [],
  last_actions_note: "Last maintenance finished 14 minutes ago.",
  next_nightly_at: new Date(NOW + 20.5 * 3_600_000).toISOString(),
  next_nightly_note: "Nightly selection runs at 02:00 IST inside the off-peak window.",
  omitted: []
});

const partitions = (id: string): S["TablePartitionsResponse"] => {
  const items = Array.from({ length: 12 }, (_, i): S["TablePartitionRead"] => {
    const files = [1_380, 1_204, 1_166, 980, 912, 864, 410, 322, 204, 118, 64, 22][i];
    return {
      id: 9_000 + i,
      table_id: id,
      scan_at: ago(25),
      partition_value: day(i),
      partition_values: { event_date_day: day(i) },
      partition_fields: [{ name: "event_date_day", source_column: "event_date", transform: "day" }],
      partition_spec_id: 0,
      partition_path: `event_date_day=${day(i)}`,
      partition_key: `event_date_day=${day(i)}`,
      base_column: "event_date",
      base_column_type: "date",
      transform: "day",
      files_per_partition: files,
      small_files_in_partition_count: Math.round(files * (i < 6 ? 0.94 : 0.4)),
      partition_size_gb: Math.round(files * (i < 6 ? 0.0062 : 0.04) * 10) / 10,
      compaction_status: i < 6 ? "needs_compaction" : "healthy"
    };
  });
  return { table_id: id, scan_at: ago(25), items, total: 412, limit: 50, offset: 0 };
};

const COLUMNS: Array<[string, string]> = [
  ["event_id", "string"],
  ["user_id", "long"],
  ["session_id", "string"],
  ["event_date", "date"],
  ["event_ts", "timestamptz"],
  ["event_type", "string"],
  ["country", "string"],
  ["device", "string"]
];

const dataQuality = (id: string): S["DataQualityProfileRead"] => ({
  table_id: id,
  table_name: name,
  profiled_at: ago(25),
  column_metrics: COLUMNS.map(([column_name, column_type], i) => ({
    column_name,
    column_type,
    null_rate_pct: [0, 0, 0.4, 0, 0, 0, 6.8, 1.2][i],
    null_rate_baseline_pct: [0, 0, 0.4, 0, 0, 0, 1.1, 1.3][i],
    null_rate_spike: column_name === "country",
    min_value: column_name === "event_date" ? day(411) : null,
    max_value: column_name === "event_date" ? day(0) : null,
    value_range_changed: false,
    value_range_explanation: ""
  })),
  anomalies_detected: 1,
  schema_version_changed: true,
  new_columns: ["device"],
  dropped_columns: []
});

const columnStats = (id: string): S["ColumnStatsResponse"] => ({
  table_id: id,
  columns: {
    user_id: { field_type: "long", ndv_estimate: 18_400_000, quantiles: {}, sketch_count: 1, puffin_blobs: [] },
    session_id: { field_type: "string", ndv_estimate: 212_000_000, quantiles: {}, sketch_count: 1, puffin_blobs: [] },
    country: { field_type: "string", ndv_estimate: 214, quantiles: {}, sketch_count: 1, puffin_blobs: [] }
  }
});

const bloom = (id: string): S["BloomFilterRecommendationsResponse"] => ({
  table_id: id,
  table_name: name,
  telemetry_source: "query_log",
  recommendations: [
    {
      column: "session_id",
      reason: "Point lookups on a high-cardinality column that min/max statistics can't prune.",
      estimated_skip_rate: 0.82,
      cardinality: "high",
      equality_filter_count: 1_460,
      spark_sql: `ALTER TABLE lakehouse.${name} SET TBLPROPERTIES ('write.parquet.bloom-filter-enabled.column.session_id'='true')`
    }
  ]
});

const partitionAccess = (id: string): S["PartitionAccessResponse"] => {
  const paths = Array.from({ length: 6 }, (_, i) => `event_date_day=${day(i)}`);
  return {
    table_id: id,
    since: ago(30 * 24 * 60),
    points: Array.from({ length: 14 }, (_, d) =>
      paths.map((partition_path, i) => ({ day: day(13 - d), partition_path, request_count: Math.max(0, 640 - i * 110 + ((d * 37) % 90)) }))
    ).flat(),
    partitions: paths.map((partition_path, i) => ({
      partition_path,
      total_requests: 9_200 - i * 1_450,
      first_seen: ago((i + 1) * 24 * 60),
      last_seen: ago(40 + i * 10),
      days_since_last_access: 0
    }))
  };
};

const snapshots = (id: string, url: URL): S["SnapshotListResponse"] => {
  const page = Number(url.searchParams.get("page") ?? 1);
  const pageSize = Number(url.searchParams.get("page_size") ?? 25);
  const ops = ["append", "append", "overwrite", "append", "delete", "append", "replace", "append"];
  const rows = Array.from({ length: pageSize }, (_, i): S["SnapshotRead"] => {
    const minutes = 5 + i * 34;
    const snapshot_id = String(8_812_334_102 - i * 7_919);
    const op = ops[i % ops.length];
    const refs: S["SnapshotRefRead"][] = [];
    if (i === 0) refs.push({ name: "main", type: "branch", snapshot_id });
    if (i === 9) refs.push({ name: "q3-close", type: "tag", snapshot_id });
    return {
      snapshot_id,
      parent_snapshot_id: String(8_812_334_102 - (i + 1) * 7_919),
      timestamp_ms: NOW - minutes * 60_000,
      committed_at: ago(minutes),
      sequence_number: 4_120 - i,
      operation: op,
      summary: {
        total_records: 9_420_000_000 - i * 1_800_000,
        total_files_size: Math.round((412.6 - i * 0.12) * GB),
        added_data_files: op === "replace" ? 165 : op === "delete" ? 0 : 38 + ((i * 7) % 20),
        deleted_data_files: op === "replace" ? 15_882 : op === "delete" ? 4 : 0
      },
      is_current: i === 0,
      refs,
      protected: i === 9,
      protected_label: i === 9 ? "Quarter close audit" : null
    };
  });
  return {
    table_id: id,
    current_snapshot_id: rows[0].snapshot_id,
    total: EVENTS.stats.total_snapshots,
    pinned_count: 1,
    latest_committed_at: rows[0].committed_at,
    oldest_committed_at: ago(60 * 24 * 38),
    page,
    page_size: pageSize,
    snapshots: rows
  };
};

const protectedSnapshots = (id: string): S["ProtectedSnapshotListResponse"] => ({
  table_id: id,
  snapshots: [{ snapshot_id: String(8_812_334_102 - 9 * 7_919), label: "Quarter close audit", protected_at: ago(60 * 24 * 3), protected_by: "priya@acme.io" }]
});

const autocomp = (id: string): S["AutoCompCandidatesResponse"] => ({
  table_id: id,
  scan_at: ago(25),
  items: Array.from({ length: 6 }, (_, i) => ({
    partition_value: `event_date_day=${day(i)}`,
    small_files: [1_297, 1_132, 1_096, 921, 857, 812][i],
    size_gb: [8.6, 7.5, 7.2, 6.1, 5.7, 5.4][i],
    query_frequency: [310, 262, 204, 166, 120, 96][i],
    benefit_score: [0.94, 0.88, 0.81, 0.72, 0.64, 0.58][i],
    eligible: true
  })),
  skipped: [{ partition_value: `event_date_day=${day(0)}`, skip_reason: "Partition is still being written; compaction waits for it to settle." }],
  effectiveness: null,
  compare: {}
});

const compactionCandidates = (id: string): S["CompactionCandidatesResponse"] => ({
  table_id: id,
  generated_at: ago(25),
  candidates: Array.from({ length: 5 }, (_, i) => ({
    partition_path: `event_date_day=${day(i + 1)}`,
    partition_size_gb: [7.5, 7.2, 6.1, 5.7, 5.4][i],
    small_files: [1_132, 1_096, 921, 857, 812][i],
    query_freq_per_day: [262, 204, 166, 120, 96][i],
    p95_latency_ms: [8_400, 7_900, 6_100, 5_200, 4_800][i],
    score: [0.88, 0.81, 0.72, 0.64, 0.58][i],
    priority: i < 2 ? "high" : "medium",
    savings_estimate_usd: [1.9, 1.7, 1.3, 1.1, 0.9][i],
    reasons: ["Many small files in a frequently read partition"]
  }))
});

const queryPerformance = (): S["QueryPerformanceResponse"] => ({
  queries: Array.from({ length: 6 }, (_, i) => ({
    query_id: `20260928_04${String(10 + i)}_0000${i}_demo`,
    query_text: [
      "SELECT country, count(*) FROM analytics.events WHERE event_date = current_date - interval '1' day GROUP BY 1",
      "SELECT * FROM analytics.events WHERE session_id = ? AND event_date >= current_date - interval '7' day",
      "SELECT event_type, approx_distinct(user_id) FROM analytics.events WHERE event_date >= date '2026-09-01' GROUP BY 1",
      "SELECT device, count(*) FROM analytics.events WHERE event_date = current_date GROUP BY 1",
      "SELECT * FROM analytics.events WHERE user_id = ? ORDER BY event_ts DESC LIMIT 50",
      "SELECT count(*) FROM analytics.events WHERE event_date BETWEEN date '2026-08-01' AND date '2026-08-31'"
    ][i],
    engine: "trino",
    user: ["bi-dashboards", "growth-api", "analyst.meera", "bi-dashboards", "support-tools", "analyst.rohan"][i],
    duration_ms: [4_120, 8_640, 21_300, 1_980, 6_410, 12_700][i],
    bytes_scanned: [3.1, 11.8, 64.2, 1.4, 9.6, 38.5][i] * GB,
    rows_produced: [214, 37, 18, 6, 50, 1][i],
    status: i === 2 ? "FAILED" : "FINISHED",
    started_at: ago(12 + i * 47),
    table_name: name,
    table_id: EVENTS.id
  })),
  total: 18_240,
  p50_latency_ms: 4_100,
  p95_latency_ms: 18_900,
  p99_latency_ms: 41_200,
  avg_bytes_scanned: 9.8 * GB,
  failed_count: 37
});

const tiering = (id: string): S["ArchiveSummaryRead"] => ({
  table_id: id,
  dry_run: true,
  executes: false,
  mode: "advisory",
  policy_name: "cold-after-180d",
  policy_scope: "schema",
  target_class: "GLACIER_IR",
  target_label: "Archive-instant",
  read_evidence: "trusted",
  read_evidence_copy: null,
  bytes_by_tier: null,
  projected_savings_usd_month: 2.84,
  realized_savings_usd_month: null,
  partition_count: 412,
  eligible_count: 230,
  truncated: false
});

const tieringPartitions = (): S["ArchivePartitionPage"] => ({
  items: Array.from({ length: 5 }, (_, i) => ({
    partition_key: `event_date_day=${day(400 - i)}`,
    partition_value: day(400 - i),
    size_gb: [1.1, 1.0, 1.2, 0.9, 1.0][i],
    age_days: 400 - i,
    suggestion: "archive_instant",
    suggestion_label: "Archive-instant",
    storage_class: "STANDARD",
    eligible: true,
    reason_codes: ["zero_reads"],
    evidence_copy: null,
    net_saving_usd_month: [0.012, 0.011, 0.013, 0.01, 0.011][i],
    break_even_read_share: 0.18
  })),
  total: 230,
  limit: 5,
  offset: 0,
  truncated: false
});

const lineage = (id: string): S["LineageGraphRead"] => ({
  root_table_id: id,
  root_table_name: name,
  upstream: [
    { table_id: visualUuid(0x80), table_name: "raw.clickstream", depth: 1, node_type: "table" },
    { table_id: visualUuid(0x81), table_name: "raw.app_events", depth: 1, node_type: "table" }
  ],
  downstream: [
    { table_id: DEMO_TABLES[1].id, table_name: "analytics.page_views", depth: 1, node_type: "table" },
    { table_id: visualUuid(0x82), table_name: "marts.daily_active_users", depth: 2, node_type: "table" }
  ]
});

const constraints = (id: string): S["QualityConstraintsResponse"] => {
  const row = (
    n: number,
    label: string,
    kind: S["QualityConstraintRead"]["kind"],
    column: string | null,
    params: Record<string, unknown>,
    status: "passed" | "failed",
    reason: string
  ): S["QualityConstraintRead"] => ({
    id: visualUuid(0x90 + n),
    table_id: id,
    name: label,
    kind,
    field_id: column ? COLUMNS.findIndex(([c]) => c === column) + 1 : null,
    column_name: column,
    params,
    severity: status === "failed" ? "warn" : "error",
    enabled: true,
    created_by: "priya@acme.io",
    created_at: ago(60 * 24 * 20),
    updated_at: ago(60 * 24 * 20),
    latest: { status, evaluated_at: ago(25), reason }
  });
  return {
    table_id: id,
    constraints: [
      row(1, "Fresh within 6 hours", "freshness", null, { max_hours: 6 }, "passed", "last data commit 0.1h before the scan (limit 6h)"),
      row(2, "event_id is never null", "completeness", "event_id", { min: 1 }, "passed", "0 nulls across 9,420,000,000 rows"),
      row(3, "country mostly filled", "completeness", "country", { min: 0.98 }, "failed", "93.2% non-null (minimum 98%)"),
      row(4, "Row count doesn't drop sharply", "size_change", null, { max_decrease: 0.4 }, "passed", "rows changed +0.2% since the previous scan")
    ],
    columns: COLUMNS.map(([n, type], i) => ({ field_id: i + 1, name: n, type, kinds: ["completeness", "range"] }))
  };
};

const checklist = (id: string): S["TableChecklistResponse"] => {
  const item = (
    key: string,
    label: string,
    weight_pct: number,
    tier: number,
    status: "green" | "yellow" | "red" | "unknown",
    reason: string,
    metric_value: number | null = null,
    threshold: number | null = null
  ): S["ChecklistItemResponse"] => ({ key, label, weight_pct, tier, status, reason, metric_value, threshold });
  const items = [
    item("file_size", "File size vs target", 25, 1, "red", "87% small files", 87, 30),
    item("delete_files", "Delete-file count/ratio", 20, 1, "yellow", "3.9% delete files", 3.9, 5),
    item("partition_match", "Partition scheme matches query predicates", 15, 1, "green", "event_date is filtered by 94% of queries"),
    item("cadence_match", "Compaction cadence matches write velocity", 10, 1, "red", "42.8 commits/day against a nightly compaction", 42.8),
    item("sort_order", "Sort order matches filter columns", 6, 2, "green", "Sort order configured"),
    item("distribution_mode", "write.distribution-mode set", 5, 2, "yellow", "write.distribution-mode='none'"),
    item("manifest_bloat", "Manifest count/size", 5, 2, "green", "96 manifests", 96),
    item("stats_current", "Column/NDV statistics current", 4, 2, "green", "Metadata-to-data ratio 0.4%", 0.004),
    item("snapshot_retention", "Snapshot retention tuned", 3, 3, "red", "1,284 snapshots retained", 1_284, 500),
    item("orphan_cleanup", "Orphan file cleanup runs", 3, 3, "green", "remove_orphan_files last ran 0d ago", 0),
    item("schema_width", "Schema width / unused columns", 2, 3, "green", "14 columns"),
    item("storage_class", "S3 storage class reachable", 2, 3, "unknown", "No file sample on the latest scan")
  ];
  const counts = { green: 6, yellow: 2, red: 3, unknown: 1 };
  return { table_id: id, checked_at: ago(25), items, summary: { counts, weighted_score: 48.5 } };
};

const sla = (): S["SLAResponse"] => ({
  slas: [
    {
      table_id: EVENTS.id,
      table_name: EVENTS.table_name,
      schema_name: EVENTS.schema_name,
      freshness_hours: 0.1,
      freshness_sla_hours: 6,
      freshness_breached: false,
      scan_staleness_hours: 0.4,
      maintenance_days: 0.01,
      maintenance_sla_days: 2,
      maintenance_breached: false,
      p95_latency_ms: 18_900,
      p95_latency_sla_ms: 15_000,
      p95_latency_breached: true,
      has_preset: true
    }
  ],
  total_breached: 1,
  total_monitored: 1
});

const benefitScores = (id: string): S["BenefitScoreResponse"] => ({
  table_id: id,
  partitions_scored: 412,
  top_candidates: autocomp(id).items.map((c) => ({
    partition_value: c.partition_value,
    benefit_score: c.benefit_score,
    small_files: c.small_files,
    size_gb: c.size_gb,
    query_frequency: c.query_frequency
  }))
});

const s3Tiering = (id: string): S["S3TieringRecommendationRead"] => ({
  table_id: id,
  table_name: name,
  current_size_gb: 412.6,
  avg_file_size_kb: 8.8 * 1024,
  days_since_last_access: 0,
  current_monthly_cost_usd: 12.43,
  intelligent_tiering_monthly_cost_usd: 12.43,
  estimated_annual_savings_usd: 0,
  recommendation: "not_suitable",
  reason: "Table accessed 0 days ago (< 30 days threshold)"
});

/** Returns a body for a table-detail tab endpoint, or undefined when the path isn't one. */
export function demoTableTabResponse(path: string, url: URL): unknown {
  const id = path.match(/\/api\/v1\/(?:tables|advanced\/[a-z-]+)\/([0-9a-f-]{36})/)?.[1] ?? EVENTS.id;
  const tail = (suffix: string) => path.endsWith(suffix);
  if (tail("/maintenance-status")) return maintenanceStatus(id);
  if (tail("/compliance")) return { table_id: id, table_ref: `lakehouse.${name}`, pending_deletion: null, abac_policy_count: 0 };
  if (tail("/property-recommendations")) return { table_id: id, generated_at: ago(25), items: [], gc_blocked: false };
  if (tail("/tiering/partitions")) return tieringPartitions();
  if (tail("/partitions")) return partitions(id);
  if (tail("/pii-classification")) return { table_id: id, matches: [] };
  if (path.includes("/advanced/data-quality/")) return dataQuality(id);
  if (path.includes("/advanced/column-stats/")) return columnStats(id);
  if (path.includes("/advanced/bloom-filter-recommendations/")) return bloom(id);
  if (path.includes("/advanced/partition-evolution/")) return null;
  if (tail("/partition-access")) return partitionAccess(id);
  if (tail("/protected-snapshots")) return protectedSnapshots(id);
  if (tail("/snapshots")) return snapshots(id, url);
  if (tail("/autocomp/candidates")) return autocomp(id);
  if (path.includes("/advanced/compaction-candidates/")) return compactionCandidates(id);
  if (tail("/api/v1/query-performance")) return queryPerformance();
  if (tail("/tiering")) return tiering(id);
  if (path.includes("/advanced/lineage/")) return lineage(id);
  if (path.includes("/advanced/unionable/")) return { target_table_id: id, results: [], generated_at: ago(25) };
  if (tail("/quality-constraints")) return constraints(id);
  if (tail("/checklist")) return checklist(id);
  if (tail("/api/v1/sla-status")) return sla();
  if (tail("/benefit-scores")) return benefitScores(id);
  if (tail("/s3-tiering")) return s3Tiering(id);
  if (tail("/api/v1/advanced/fast-path-findings")) return { findings: [] };
  return undefined;
}
