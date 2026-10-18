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
      total_delete_files: 640,
      position_delete_files: 640,
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
        last_snapshot_committed_at: ago(5),
        snapshot_overhead_gb: 128.4,
        manifest_stats: { manifest_count: 96, avg_files_per_manifest: 502.19, manifest_total_bytes: 96 * 410_000, avg_manifest_bytes: 410_000 },
        iceberg_properties: { "write.metadata.previous-versions-max": "100", "format-version": "2" },
        health_deductions: [
          { label: "Small-file fragmentation", deduction: 20, severity: "critical", reason: "87% of data files are under the 128 MiB write target." },
          { label: "Snapshot retention buildup", deduction: 20, severity: "critical", reason: "1,284 snapshots retained; the table policy keeps 7 days." },
          { label: "Delete files", deduction: 8, severity: "warning", reason: "640 position-delete files are merged on every read." },
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
        total_snapshots: Math.round(200 + decay * 1_084),
        total_delete_files: Math.round(60 + decay * 580),
        delete_file_pct: Math.round((0.5 + decay * 3.4) * 10) / 10,
        manifest_count: Math.round(40 + decay * 56)
      };
    })
  };
}

const RUN_STEPS = ["optimize", "compute_statistics", "optimize_manifests", "expire_snapshots", "remove_orphan_files"];

export function demoTableRuns(id: string): S["TableRunsResponse"] {
  if (id !== EVENTS.id) return { table_id: id, items: [], total: 0, limit: 20, offset: 0 };
  // Step boundaries in minutes-ago; run 1 lines up with the audit receipts in demo-data.ts.
  const run = (
    n: number,
    trigger: string,
    bounds: number[],
    before: [number, number, number],
    after: [number, number, number]
  ): S["TableRunRead"] => {
    const startedMin = bounds[0];
    const finishedMin = bounds[bounds.length - 1];
    return {
      job_id: visualUuid(0xa0 + n),
      run_id: visualUuid(0xb0 + n),
      status: "succeeded",
      trigger,
      mode: "standard",
      started_at: ago(startedMin),
      finished_at: ago(finishedMin),
      duration_seconds: (startedMin - finishedMin) * 60,
      error: null,
      steps: RUN_STEPS.map((operation_type, step_index) => ({
        step_index,
        operation_type,
        status: "succeeded",
        started_at: ago(bounds[step_index]),
        finished_at: ago(bounds[step_index + 1])
      })),
      before_stats: { scan_at: ago(startedMin + 5), grade: "F", overall_score: before[0], total_files: before[1], small_files_count: before[2], total_size_gb: 412.6 },
      after_stats: { scan_at: ago(finishedMin - 2), grade: "F", overall_score: after[0], total_files: after[1], small_files_count: after[2], total_size_gb: 412.6 }
    };
  };
  const day1 = 24 * 60;
  const items = [
    run(1, "ui", [42, 24, 19, 18, 16, 14], [20, 76_337, 70_057], [28, 48_210, 41_930]),
    run(2, "schedule", [day1 + 150, day1 + 121, day1 + 116, day1 + 114.5, day1 + 112, day1 + 109], [20, 74_120, 68_400], [20, 61_204, 55_880]),
    run(3, "schedule", [6 * day1 + 150, 6 * day1 + 118, 6 * day1 + 114, 6 * day1 + 112, 6 * day1 + 110, 6 * day1 + 107], [28, 70_400, 63_900], [36, 52_310, 45_120])
  ];
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

const snapshotSummary = (i: number, op: string): S["SnapshotSummaryRead"] => {
  const added = op === "replace" ? 165 : op === "delete" ? 0 : 38 + ((i * 7) % 20);
  const removed = op === "replace" ? 15_882 : op === "delete" ? 4 : 0;
  const rows = op === "append" ? 1_800_000 : 0;
  const deletes = op === "overwrite" || op === "delete" ? 6 + (i % 5) : 0;
  const compaction = op === "replace";
  return {
    total_records: 9_420_000_000 - i * 1_800_000,
    total_files_size: Math.round((412.6 - i * 0.12) * GB),
    added_data_files: added,
    deleted_data_files: removed,
    added_records: compaction ? null : rows,
    deleted_records: op === "delete" ? 42_000 : op === "overwrite" ? 9_300 : compaction ? null : 0,
    added_files_size: Math.round(added * (compaction ? 128 : 9) * MIB),
    removed_files_size: Math.round(removed * 8.8 * MIB),
    added_delete_files: deletes,
    added_position_delete_files: deletes,
    total_delete_files: 640 - i * 2,
    changed_partition_count: compaction ? 31 : op === "append" ? 1 : 3,
    writer: compaction
      ? { engine: "trino", engine_version: "480", user: "cryo-maintenance", query_id: `20260928_0${i}1122_00${i}_cryo`, job_id: null }
      : op === "append"
        ? { engine: "flink", engine_version: "1.20", user: null, query_id: null, job_id: "c7e1b0e2f6a4" }
        : { engine: "spark", engine_version: "3.5.3", user: "etl", query_id: null, job_id: "app-20260928-0042" }
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
      summary: snapshotSummary(i, op),
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
  dry_run: false,
  executes: true,
  mode: "manual",
  policy_name: "cold-after-180d",
  policy_scope: "schema",
  target_class: "GLACIER_IR",
  target_label: "Archive-instant",
  read_evidence: "trusted",
  read_evidence_copy: null,
  bytes_by_tier: { GLACIER_IR: 4.2 },
  projected_savings_usd_month: 2.84,
  realized_savings_usd_month: null,
  partition_count: 412,
  eligible_count: 234,
  truncated: false,
  archived_partitions: 4,
  archived_gb: 4.2
});

const tieringPartitions = (url: URL): S["ArchivePartitionPage"] => {
  const row = (age: number, size: number, saving: number, movedDaysAgo: number | null): S["ArchivePartitionRead"] => ({
    partition_key: `event_date_day=${day(age)}`,
    partition_value: day(age),
    size_gb: size,
    age_days: age,
    suggestion: "archive_instant",
    suggestion_label: "Archive-instant",
    storage_class: movedDaysAgo == null ? "STANDARD" : "GLACIER_IR",
    current_class: movedDaysAgo == null ? null : "GLACIER_IR",
    moved_at: movedDaysAgo == null ? null : ago(60 * 24 * movedDaysAgo),
    eligible: movedDaysAgo == null,
    reason_codes: ["zero_reads"],
    evidence_copy: null,
    net_saving_usd_month: saving,
    break_even_read_share: 0.18
  });
  const sizes = [1.1, 1.0, 1.2, 0.9, 1.0, 1.3, 1.1, 0.8];
  const savings = [0.012, 0.011, 0.013, 0.01, 0.011, 0.014, 0.012, 0.009];
  const limit = Number(url.searchParams.get("limit") ?? 10);
  const offset = Number(url.searchParams.get("offset") ?? 0);
  if (url.searchParams.get("state") === "archived") {
    const items = sizes.slice(0, 4).map((size, i) => row(405 + i, size, savings[i], 3));
    return { items, total: items.length, limit, offset, truncated: false };
  }
  const items = Array.from({ length: limit }, (_, i) => row(400 - offset - i, sizes[i % 8], savings[i % 8], null));
  return { items, total: 235, limit, offset, truncated: false };
};

const tieringJobs = (): S["ArchiveJobList"] => ({
  items: [
    {
      id: visualUuid(0xc1),
      action: "archive",
      target_class: "GLACIER_IR",
      target_label: "Archive-instant",
      partitions: 4,
      status: "succeeded",
      stale: false,
      objects_total: 412,
      objects_moved: 412,
      objects_skipped: 0,
      objects_failed: 0,
      gb_moved: 4.2,
      errors: [],
      actor: "priya@acme.io",
      reason: "Quarter-old event days with no reads in 90 days",
      created_at: ago(60 * 24 * 3 + 6),
      started_at: ago(60 * 24 * 3 + 5),
      finished_at: ago(60 * 24 * 3)
    }
  ]
});

// Same shape as cryo.tiering.executor.iam_policy("demo-lake").
const ARCHIVE_POLICY = JSON.stringify({
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "CryoArchiveReadAndCopy",
        "Effect": "Allow",
        "Action": [
          "s3:GetObject",
          "s3:GetObjectTagging",
          "s3:PutObjectTagging"
        ],
        "Resource": "arn:aws:s3:::demo-lake/*"
      },
      {
        "Sid": "CryoArchiveInstantClassesOnly",
        "Effect": "Allow",
        "Action": "s3:PutObject",
        "Resource": "arn:aws:s3:::demo-lake/*",
        "Condition": {
          "StringEquals": {
            "s3:x-amz-storage-class": [
              "GLACIER_IR",
              "INTELLIGENT_TIERING",
              "STANDARD",
              "STANDARD_IA"
            ]
          }
        }
      },
      {
        "Sid": "CryoArchiveBucketChecks",
        "Effect": "Allow",
        "Action": [
          "s3:GetBucketVersioning",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetReplicationConfiguration",
          "s3:GetLifecycleConfiguration"
        ],
        "Resource": "arn:aws:s3:::demo-lake"
      }
    ]
  }, null, 2);

/** Preflight for the archive dialog: one permission is missing so the docs show the policy to add. */
export function demoArchivePreflight(body: { action?: string; target_class?: string | null } | null): S["ArchivePreflightRead"] {
  const target = body?.target_class ?? "GLACIER_IR";
  const labels: Record<string, string> = { GLACIER_IR: "Archive-instant", STANDARD_IA: "Cool", INTELLIGENT_TIERING: "Auto" };
  const monthly: Record<string, number> = { GLACIER_IR: -4.37, STANDARD_IA: -2.88, INTELLIGENT_TIERING: -0.31 };
  return {
    action: body?.action ?? "archive",
    target_class: target,
    target_label: labels[target] ?? target,
    bucket: "demo-lake",
    partitions_selected: 230,
    object_estimate: 23_640,
    gb_estimate: 236.1,
    request_cost_usd: 0.47,
    storage_change_usd_month: monthly[target] ?? -1,
    retrieval_cost_usd: 0,
    early_delete_usd: 0,
    permissions: [
      { action: "s3:GetObject", status: "ok", purpose: "Read each file's class" },
      { action: "s3:PutObject", status: "missing", purpose: "Copy files to a new class" },
      { action: "s3:GetBucketVersioning", status: "ok", purpose: "Check the bucket is safe to re-tier" },
      { action: "s3:GetBucketObjectLockConfiguration", status: "ok", purpose: "Check the bucket is safe to re-tier" }
    ],
    guardrails: [
      { name: "versioning", status: "pass", detail: "Not versioned." },
      { name: "object_lock", status: "pass", detail: "Object Lock is off." },
      { name: "replication", status: "pass", detail: "No replication." }
    ],
    missing_permissions: ["s3:PutObject"],
    iam_policy: ARCHIVE_POLICY,
    warnings: ["Archive-instant bills 90 days minimum. Restoring a partition sooner bills the remaining days."],
    blockers: [],
    requires_confirmation: false,
    can_apply: false
  };
}

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
    latest: { status, scanned_at: ago(25), evaluated_at: ago(24), reason }
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

const propertyRecommendations = (id: string): S["PropertyRecommendationsResponse"] => {
  const apply = (property_key: string, current_value: string, recommended_value: string, reason: string) => ({
    property_key,
    current_value,
    recommended_value,
    reason,
    tier: "tier1" as const,
    action: "apply" as const,
    layout_recommendation_id: null,
    bloom_filter_column: null,
    needs_confirmation: false
  });
  return {
    table_id: id,
    generated_at: ago(25),
    gc_blocked: false,
    items: [
      apply(
        "write.metadata.delete-after-commit.enabled",
        "false",
        "true",
        "42.8 commits a day each leave a metadata.json behind. With this on, each commit trims versions beyond write.metadata.previous-versions-max."
      ),
      apply(
        "commit.retry.num-retries",
        "4",
        "10",
        "Flink appends every few minutes while compaction commits; 3 commits in the last week failed after 4 retries."
      )
    ]
  };
};

const partitionEvolution = (id: string): S["PartitionEvolutionRecommendationRead"] => ({
  table_id: id,
  table_name: name,
  current_partition_spec: "event_date",
  recommended_partition_spec: "event_date, bucket(16, user_id)",
  reason: "71% of queries filter on user_id with equality predicates, and each day partition holds 117 files.",
  confidence: 0.71,
  estimated_pruning_improvement_pct: 49.7,
  query_patterns_analyzed: 18_240,
  cardinality_estimate: "high",
  warnings: ["Existing files keep the event_date-only layout until compaction rewrites them."],
  scan_ratio: null,
  avg_selectivity: 0.004,
  trigger: "filter_frequency"
});

const columnUsage = (id: string): S["ColumnUsageResponse"] => {
  const use = (column: string, filter: number, eq: number, join: number, proj: number, selectivity: number | null) => ({
    column,
    filter_count: filter,
    equality_filter_count: eq,
    range_filter_count: filter - eq,
    join_count: join,
    projection_count: proj,
    avg_selectivity: selectivity
  });
  return {
    table_id: id,
    window_days: 30,
    queries: 18_240,
    aggregated_at: ago(40),
    columns: [
      use("event_date", 17_150, 2_100, 0, 9_800, 0.03),
      use("user_id", 12_960, 12_960, 6_420, 15_100, 0.004),
      use("session_id", 4_210, 4_210, 2_880, 7_300, 0.001),
      use("event_type", 3_900, 3_900, 0, 16_200, 0.12),
      use("country", 1_120, 1_120, 0, 5_400, 0.2),
      use("device", 310, 310, 0, 4_100, 0.4)
    ]
  };
};

const impactWindow = (days: number | null, since: string | null, scale: number): S["ImpactWindowRead"] => {
  const op = (operation: string, ops: number, withBytes: number, withFiles: number, bytes: number, filesIn: number, filesOut: number) => ({
    operation,
    ops,
    ops_with_bytes: withBytes,
    ops_with_files: withFiles,
    bytes_freed: Math.round(bytes * scale),
    files_in: Math.round(filesIn * scale),
    files_out: Math.round(filesOut * scale)
  });
  const by_operation = [
    op("optimize", Math.round(4 * scale), 0, Math.round(4 * scale), 0, 61_400, 1_820),
    op("compute_statistics", Math.round(4 * scale), 0, 0, 0, 0, 0),
    op("optimize_manifests", Math.round(4 * scale), 0, 0, 0, 0, 0),
    op("expire_snapshots", Math.round(4 * scale), Math.round(4 * scale), 0, 86 * GB, 0, 0),
    op("remove_orphan_files", Math.round(4 * scale), Math.round(3 * scale), 0, 11.4 * GB, 0, 0)
  ];
  const sum = (key: "ops" | "ops_with_bytes" | "ops_with_files" | "bytes_freed" | "files_in" | "files_out") =>
    by_operation.reduce((total, item) => total + item[key], 0);
  return {
    days,
    since,
    ops: sum("ops"),
    ops_with_bytes: sum("ops_with_bytes"),
    ops_with_files: sum("ops_with_files"),
    bytes_freed: sum("bytes_freed"),
    files_in: sum("files_in"),
    files_out: sum("files_out"),
    by_operation
  };
};

const maintenanceImpact = (id: string): S["MaintenanceImpactResponse"] => ({
  table_id: id,
  recent: impactWindow(30, ago(30 * 24 * 60), 1),
  lifetime: impactWindow(null, ago(96 * 24 * 60), 3)
});

const skipEvaluations = (id: string): S["SkipEvaluationPage"] => {
  const night = (daysAgo: number, stage: string, reason: string | null): S["SkipEvaluationRead"] => ({
    id: visualUuid(0xd0 + daysAgo),
    run_id: visualUuid(0xc0 + daysAgo),
    table_id: id,
    stage,
    reason,
    evaluated_at: ago(daysAgo * 24 * 60 + 150)
  });
  const items = [
    night(2, "needs_work", "budget_cap"),
    night(3, "needs_work", "budget_cap"),
    night(4, "needs_work", "budget_cap"),
    night(5, "evaluated", "cooldown")
  ];
  return { items, total: items.length, limit: 120, offset: 0 };
};

/** Returns a body for a table-detail tab endpoint, or undefined when the path isn't one. */
export function demoTableTabResponse(path: string, url: URL): unknown {
  const id = path.match(/\/api\/v1\/(?:tables|advanced\/[a-z-]+)\/([0-9a-f-]{36})/)?.[1] ?? EVENTS.id;
  const tail = (suffix: string) => path.endsWith(suffix);
  if (tail("/maintenance-status")) return maintenanceStatus(id);
  if (tail("/compliance")) {
    return {
      table_id: id,
      table_ref: `lakehouse.${name}`,
      pending_deletion: {
        pending_rows: 18_420,
        blocked_until: new Date(NOW + 2 * 86_400_000).toISOString(),
        reason: "Position deletes still referenced by retained snapshots"
      },
      abac_policy_count: 0,
      abac_enforcement_note: null,
      abac_enforced: false,
      protected_snapshot_count: 1
    };
  }
  if (tail("/property-recommendations")) return propertyRecommendations(id);
  if (tail("/tiering/partitions")) return tieringPartitions(url);
  if (tail("/tiering/jobs")) return tieringJobs();
  if (tail("/partitions")) return partitions(id);
  if (tail("/pii-classification")) return { table_id: id, matches: [] };
  if (path.includes("/advanced/data-quality/")) return dataQuality(id);
  if (path.includes("/advanced/column-stats/")) return columnStats(id);
  if (path.includes("/advanced/bloom-filter-recommendations/")) return bloom(id);
  if (path.includes("/advanced/partition-evolution/")) return partitionEvolution(id);
  if (tail("/column-usage")) return columnUsage(id);
  if (tail("/maintenance-impact")) return maintenanceImpact(id);
  if (tail("/maintenance-hold")) return { held: false, max_hold_days: 90 };
  if (tail("/skip-evaluations")) return skipEvaluations(id);
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
