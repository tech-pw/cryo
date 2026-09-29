import {
  DEFAULT_JOB_PAGE_SIZE,
  DEFAULT_SNAPSHOT_PAGE_SIZE,
  DEFAULT_TABLE_PAGE_SIZE,
  LONG_SCHEMA_NAME,
  LONG_TABLE_NAME,
  MAX_PAGE_SIZE,
  PREVIOUS_YEAR_COMMITTED_AT,
  SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER,
  STALE_AS_OF,
  TINY_SIZE_BYTES,
  TINY_SIZE_GB,
  VISUAL_FLINK_DESTINATION_ID,
  VISUAL_NOW_ISO,
  VISUAL_PRESET_ID,
  VISUAL_RUN_ID,
  VISUAL_SEED,
  VISUAL_SOURCE_ID,
  VISUAL_TRINO_DESTINATION_ID,
  type TraitFlags,
  visualUuid
} from "./constants";

export interface VisualTableStats {
  scan_at: string | null;
  grade: string | null;
  overall_score: number | null;
  total_files: number | null;
  small_files_count: number | null;
  total_size_gb: number | null;
  total_size_bytes: number | null;
  total_snapshots: number | null;
  last_snapshot_committed_at: string | null;
  needs_compaction: boolean | null;
}

export interface VisualTable {
  id: string;
  source_id: string;
  schema_name: string;
  table_name: string;
  catalog_name: string;
  source_name: string;
  qualified_table_name: string;
  location: string;
  first_seen_at: string;
  is_active: boolean;
  stats: VisualTableStats | null;
}

export interface VisualJob {
  id: string;
  run_id: string;
  table_id: string;
  destination_id: string;
  schema_name: string;
  table_name: string;
  destination_name: string;
  destination_engine: string;
  mode: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface VisualSnapshot {
  snapshot_id: string;
  parent_snapshot_id: string | null;
  timestamp_ms: number | null;
  committed_at: string | null;
  sequence_number: number | null;
  operation: string;
  summary: {
    total_records: number | null;
    total_files_size: number | null;
    added_data_files: number | null;
    deleted_data_files: number | null;
  };
  is_current: boolean;
  refs: Array<{
    name: string;
    type: "branch" | "tag";
    min_snapshots_to_keep: number | null;
    max_snapshot_age_ms: number | null;
    max_ref_age_ms: number | null;
  }>;
  protected: boolean;
  protected_label: string | null;
}

export interface VisualRun {
  id: string;
  kind: string;
  status: string;
  preset_id: string;
  trigger: string;
  deadline_at: string | null;
  scheduled_at: string;
  created_at: string;
  finished_at: string | null;
  summary: {
    jobs_total: number;
    measurement_state: "partial" | "unknown" | "complete";
    measured_actions: number | null;
    succeeded_actions: number | null;
  };
  realized_bytes_freed: number | null;
  realized_actions_measured: number | null;
  realized_actions_succeeded: number | null;
}

const GRADES = ["A", "B", "C", "D", "F"] as const;
const JOB_STATUSES = ["succeeded", "failed", "skipped"] as const;
const SNAPSHOT_OPERATIONS = ["append", "overwrite", "replace", "delete", "unknown"] as const;

function mix(index: number): number {
  let hash = (VISUAL_SEED ^ Math.imul(index + 1, 0x9e3779b1)) >>> 0;
  hash = Math.imul(hash ^ (hash >>> 16), 0x7feb352d);
  hash = Math.imul(hash ^ (hash >>> 15), 0x846ca68b);
  return (hash ^ (hash >>> 16)) >>> 0;
}

function traitOn(index: number, mode: TraitFlags["longNames"]): boolean {
  if (mode === "all") return true;
  if (mode === "first") return index === 0;
  return false;
}

export function clampPageSize(requested: number, fallback: number): number {
  const value = Number.isFinite(requested) && requested > 0 ? requested : fallback;
  return Math.min(MAX_PAGE_SIZE, Math.max(1, Math.floor(value)));
}

function pageSlice(total: number, offset: number, limit: number): { start: number; count: number } {
  const start = Math.min(Math.max(0, Math.floor(offset)), Math.max(0, total));
  return { start, count: Math.min(limit, Math.max(0, total - start)) };
}

function unknownStats(): VisualTableStats {
  return {
    scan_at: null,
    grade: null,
    overall_score: null,
    total_files: null,
    small_files_count: null,
    total_size_gb: null,
    total_size_bytes: null,
    total_snapshots: null,
    last_snapshot_committed_at: null,
    needs_compaction: null
  };
}

export function tableAt(index: number, flags: TraitFlags): VisualTable {
  const long = traitOn(index, flags.longNames);
  const schemaName = long ? LONG_SCHEMA_NAME : "visual_schema";
  const tableName = long ? LONG_TABLE_NAME : `visual_table_${String(index).padStart(5, "0")}`;
  const previous = traitOn(index, flags.previousYear);
  const tiny = traitOn(index, flags.tinyBytes);
  const grade = GRADES[mix(index) % GRADES.length];
  return {
    id: visualUuid(0x10 + index),
    source_id: VISUAL_SOURCE_ID,
    schema_name: schemaName,
    table_name: tableName,
    catalog_name: "visual_catalog",
    source_name: "visual-lakehouse",
    qualified_table_name: `visual_catalog.${schemaName}.${tableName}`,
    location: `s3://visual-fixtures/warehouse/${schemaName}/${tableName}`,
    first_seen_at: previous ? PREVIOUS_YEAR_COMMITTED_AT : VISUAL_NOW_ISO,
    is_active: true,
    stats: flags.unknown
      ? unknownStats()
      : {
          scan_at: flags.stale ? STALE_AS_OF : VISUAL_NOW_ISO,
          grade,
          overall_score: 40 + (mix(index) % 55),
          total_files: tiny ? 4 : 120 + (index % 20),
          small_files_count: tiny ? 3 : 10 + (index % 8),
          total_size_gb: tiny ? TINY_SIZE_GB : 12.5,
          total_size_bytes: tiny ? TINY_SIZE_BYTES : 13_421_772_800,
          total_snapshots: 4,
          last_snapshot_committed_at: previous ? PREVIOUS_YEAR_COMMITTED_AT : VISUAL_NOW_ISO,
          needs_compaction: grade === "D" || grade === "F"
        }
  };
}

// One page only. Totals such as 10_000 are counters; this never builds the full list.
export function tablesPage(offset: number, limit: number, total: number, flags: TraitFlags): VisualTable[] {
  const size = clampPageSize(limit, DEFAULT_TABLE_PAGE_SIZE);
  const { start, count } = pageSlice(total, offset, size);
  return Array.from({ length: count }, (_, index) => tableAt(start + index, flags));
}

export function jobsPage(offset: number, limit: number, total: number, flags: TraitFlags): VisualJob[] {
  const size = clampPageSize(limit, DEFAULT_JOB_PAGE_SIZE);
  const { start, count } = pageSlice(total, offset, size);
  return Array.from({ length: count }, (_, index) => {
    const ordinal = start + index;
    const table = tableAt(ordinal, flags);
    const status = flags.unknown ? "unknown" : JOB_STATUSES[mix(ordinal) % JOB_STATUSES.length];
    return {
      id: visualUuid(0x10000 + ordinal),
      run_id: VISUAL_RUN_ID,
      table_id: table.id,
      destination_id: flags.unsupportedEngine ? VISUAL_FLINK_DESTINATION_ID : VISUAL_TRINO_DESTINATION_ID,
      schema_name: table.schema_name,
      table_name: table.table_name,
      destination_name: flags.unsupportedEngine ? "visual-flink" : "visual-trino",
      destination_engine: flags.unsupportedEngine ? "flink" : "trino",
      mode: "standard",
      status,
      started_at: flags.unknown ? null : flags.stale ? STALE_AS_OF : VISUAL_NOW_ISO,
      finished_at: flags.unknown ? null : VISUAL_NOW_ISO,
      error: status === "failed" ? "visual fixture: engine rejected the plan" : null
    };
  });
}

export function visualRun(jobsTotal: number, flags: TraitFlags): VisualRun {
  const measurementState = flags.partial ? "partial" : flags.unknown ? "unknown" : "complete";
  return {
    id: VISUAL_RUN_ID,
    kind: "maintenance",
    status: flags.partial ? "partial" : flags.unknown ? "unknown" : "succeeded",
    preset_id: VISUAL_PRESET_ID,
    trigger: "schedule",
    deadline_at: null,
    scheduled_at: flags.stale ? STALE_AS_OF : VISUAL_NOW_ISO,
    created_at: flags.previousYear === "all" ? PREVIOUS_YEAR_COMMITTED_AT : VISUAL_NOW_ISO,
    finished_at: flags.unknown ? null : VISUAL_NOW_ISO,
    summary: {
      jobs_total: jobsTotal,
      measurement_state: measurementState,
      measured_actions: flags.partial ? 8 : flags.unknown ? null : jobsTotal,
      succeeded_actions: flags.partial ? 12 : flags.unknown ? null : jobsTotal
    },
    realized_bytes_freed: flags.unknown ? null : flags.tinyBytes === "none" ? 13_421_772_800 : TINY_SIZE_BYTES,
    realized_actions_measured: flags.partial ? 8 : flags.unknown ? null : 1,
    realized_actions_succeeded: flags.partial ? 12 : flags.unknown ? null : 1
  };
}

export function snapshotsPage(
  page: number,
  pageSize: number,
  total: number,
  flags: TraitFlags
): VisualSnapshot[] {
  const size = clampPageSize(pageSize, DEFAULT_SNAPSHOT_PAGE_SIZE);
  const safePage = Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
  const { start, count } = pageSlice(total, (safePage - 1) * size, size);
  return Array.from({ length: count }, (_, index) => snapshotAt(start + index, flags));
}

function snapshotAt(ordinal: number, flags: TraitFlags): VisualSnapshot {
  const previous = flags.previousYear === "all" || (flags.previousYear !== "none" && ordinal === 1);
  const tiny = flags.tinyBytes === "all" || (flags.tinyBytes !== "none" && ordinal === 0);
  const committed = flags.unknown ? null : previous ? PREVIOUS_YEAR_COMMITTED_AT : VISUAL_NOW_ISO;
  return {
    snapshot_id: ordinal === 0 ? SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER : String(1_000_000 + ordinal),
    parent_snapshot_id:
      ordinal === 0 ? null : ordinal === 1 ? SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER : String(1_000_000 + ordinal - 1),
    timestamp_ms: committed === null ? null : Date.parse(committed),
    committed_at: committed,
    sequence_number: flags.unknown ? null : ordinal + 1,
    operation: SNAPSHOT_OPERATIONS[ordinal % SNAPSHOT_OPERATIONS.length],
    summary: {
      total_records: flags.unknown ? null : 1_000 + ordinal,
      total_files_size: flags.unknown ? null : tiny ? TINY_SIZE_BYTES : 13_421_772_800,
      added_data_files: flags.unknown ? null : 2,
      deleted_data_files: flags.unknown ? null : 1
    },
    is_current: ordinal === 0,
    refs:
      ordinal === 0
        ? [
            {
              name: "main",
              type: "branch",
              min_snapshots_to_keep: 1,
              max_snapshot_age_ms: null,
              max_ref_age_ms: null
            }
          ]
        : [],
    protected: ordinal === 2,
    protected_label: ordinal === 2 ? "Excluded from Cryo expiry" : null
  };
}
