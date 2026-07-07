/** User-facing preset option metadata and planner/runtime defaults. */

export const PRESET_PARAM_DEFAULTS = {
  min_small_files: 128,
  chunk_size_gb: 100,
  max_partitions_per_chunk: 100,
  delete_ratio_trigger: 0,
  partition_write_idle_hours: 0,
  retention_days: 7,
  retain_last: 1,
  file_size_mb: 256,
  compute_statistics: false,
  stats_compute_ndv: true
} as const;

export const PRESET_GUARDRAIL_DEFAULTS = {
  cooldown_hours: 24,
  sla_freshness_hours: 24,
  sla_maintenance_days: 7,
  sla_p95_latency_ms: 5000,
  adaptive_routing: false,
  spark_min_gb: 100,
  native_min_gb: 50,
  latency_p99_ms: 500
} as const;

/** Params the UI edits explicitly; other keys are preserved on save. */
export const EDITABLE_PARAM_KEYS = [
  "min_small_files",
  "chunk_size_gb",
  "max_partitions_per_chunk",
  "max_chunks",
  "delete_ratio_trigger",
  "partition_write_idle_hours",
  "retention_days",
  "retain_last",
  "orphan_retention_days",
  "file_size_mb",
  "compute_statistics",
  "stats_columns",
  "bloom_filter_columns",
  "stats_compute_ndv"
] as const;

/** Guardrails the UI edits explicitly; other keys are preserved on save. */
export const EDITABLE_GUARDRAIL_KEYS = [
  "cooldown_hours",
  "deadline_minutes",
  "sla_freshness_hours",
  "sla_maintenance_days",
  "sla_p95_latency_ms",
  "adaptive_routing",
  "engine_pool",
  "engine_preferences",
  "routing_rules"
] as const;

export const ROUTING_RULE_FIELDS = [
  { label: "Total size (GB)", value: "total_size_gb" },
  { label: "Small files", value: "small_files_count" },
  { label: "Total files", value: "total_files" },
  { label: "Grade", value: "grade" },
  { label: "Health score", value: "overall_score" },
  { label: "Query count (30d)", value: "query_count_30d" },
  { label: "Needs compaction", value: "needs_compaction" }
] as const;

export const ROUTING_RULE_OPERATORS = [
  { label: "=", value: "=" },
  { label: "!=", value: "!=" },
  { label: "<", value: "<" },
  { label: "<=", value: "<=" },
  { label: ">", value: ">" },
  { label: ">=", value: ">=" }
] as const;
