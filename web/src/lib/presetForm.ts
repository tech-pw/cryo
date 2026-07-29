import type { DestinationRead, JsonObject, Mode, PresetRead, PresetWrite } from "../api/types";
import { validateCronExpression } from "./cron";
import {
  EDITABLE_GUARDRAIL_KEYS,
  EDITABLE_PARAM_KEYS,
  PRESET_GUARDRAIL_DEFAULTS,
  PRESET_PARAM_DEFAULTS
} from "./presetOptions";

export interface RoutingRuleFormRow {
  field: string;
  op: string;
  value: number | string;
  destination: string;
}

export interface PresetFormValues {
  name: string;
  description?: string;
  destination_id: string;
  mode: Mode;
  operations: string[];
  schedule_cron?: string | null;
  priority: number;
  enabled: boolean;
  is_default: boolean;
  min_small_files: number;
  chunk_size_gb: number;
  max_partitions_per_chunk: number;
  max_chunks?: number | null;
  delete_ratio_trigger: number;
  partition_write_idle_hours: number;
  retention_days: number;
  retain_last: number;
  orphan_retention_days?: number | null;
  file_size_mb: number;
  compute_statistics: boolean;
  stats_columns?: string[];
  bloom_filter_columns?: string[];
  stats_compute_ndv: boolean;
  cooldown_hours: number;
  deadline_minutes?: number | null;
  sla_freshness_hours: number;
  sla_maintenance_days: number;
  sla_p95_latency_ms: number;
  adaptive_routing: boolean;
  engine_pool?: string[];
  spark_min_gb: number;
  native_min_gb: number;
  latency_p99_ms: number;
  routing_rules?: RoutingRuleFormRow[];
}

const DEFAULT_OPERATIONS = [
  "expire_snapshots",
  "remove_orphan_files",
  "optimize",
  "optimize_manifests"
];

const asNumber = (value: unknown, fallback: number): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

const optionalNumber = (value: unknown): number | null | undefined => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const asStringArray = (value: unknown): string[] | undefined => {
  if (!Array.isArray(value)) return undefined;
  const items = value.map((item) => String(item)).filter((item) => item.length > 0);
  return items.length ? items : undefined;
};

const readEnginePreferences = (guardrails: JsonObject) => {
  const prefs = guardrails.engine_preferences;
  if (!prefs || typeof prefs !== "object" || Array.isArray(prefs)) {
    return {
      spark_min_gb: PRESET_GUARDRAIL_DEFAULTS.spark_min_gb,
      native_min_gb: PRESET_GUARDRAIL_DEFAULTS.native_min_gb,
      latency_p99_ms: PRESET_GUARDRAIL_DEFAULTS.latency_p99_ms
    };
  }
  const record = prefs as Record<string, unknown>;
  return {
    spark_min_gb: asNumber(record.spark_min_gb, PRESET_GUARDRAIL_DEFAULTS.spark_min_gb),
    native_min_gb: asNumber(record.native_min_gb, PRESET_GUARDRAIL_DEFAULTS.native_min_gb),
    latency_p99_ms: asNumber(record.latency_p99_ms, PRESET_GUARDRAIL_DEFAULTS.latency_p99_ms)
  };
};

const readRoutingRules = (guardrails: JsonObject): RoutingRuleFormRow[] => {
  const raw = guardrails.routing_rules;
  if (!raw) return [];
  const rows = Array.isArray(raw) ? raw : [raw];
  return rows.flatMap((row) => {
    if (!row || typeof row !== "object" || Array.isArray(row)) return [];
    const record = row as Record<string, unknown>;
    const predicate = (record.predicate ?? record.when) as Record<string, unknown> | undefined;
    const destination = String(record.destination ?? record.destination_name ?? "");
    if (!predicate || !destination) return [];
    const field = String(predicate.field ?? "");
    const op = String(predicate.op ?? "=");
    const value = predicate.value;
    if (!field || value === undefined || value === null) return [];
    return [{ field, op, value: typeof value === "number" ? value : String(value), destination }];
  });
};

export const presetToFormValues = (
  preset: PresetRead | null,
  destinations: DestinationRead[]
): Partial<PresetFormValues> => {
  const params = preset?.params ?? {};
  const guardrails = preset?.guardrails ?? {};
  const prefs = readEnginePreferences(guardrails);
  const enginePool = asStringArray(guardrails.engine_pool);

  return {
    name: preset?.name,
    description: preset?.description ?? undefined,
    destination_id: preset?.destination_id ?? destinations[0]?.id,
    mode: preset?.mode ?? "standard",
    operations: preset?.operations?.length ? preset.operations : DEFAULT_OPERATIONS,
    schedule_cron: preset?.schedule_cron,
    priority: preset?.priority ?? 100,
    enabled: preset?.enabled ?? true,
    is_default: preset?.is_default ?? false,
    min_small_files: asNumber(params.min_small_files, PRESET_PARAM_DEFAULTS.min_small_files),
    chunk_size_gb: asNumber(params.chunk_size_gb, PRESET_PARAM_DEFAULTS.chunk_size_gb),
    max_partitions_per_chunk: asNumber(
      params.max_partitions_per_chunk,
      PRESET_PARAM_DEFAULTS.max_partitions_per_chunk
    ),
    max_chunks: optionalNumber(params.max_chunks),
    delete_ratio_trigger: asNumber(params.delete_ratio_trigger, PRESET_PARAM_DEFAULTS.delete_ratio_trigger),
    partition_write_idle_hours: asNumber(
      params.partition_write_idle_hours,
      PRESET_PARAM_DEFAULTS.partition_write_idle_hours
    ),
    retention_days: asNumber(params.retention_days, PRESET_PARAM_DEFAULTS.retention_days),
    retain_last: asNumber(params.retain_last, PRESET_PARAM_DEFAULTS.retain_last),
    orphan_retention_days: optionalNumber(params.orphan_retention_days),
    file_size_mb: asNumber(params.file_size_mb, PRESET_PARAM_DEFAULTS.file_size_mb),
    compute_statistics: Boolean(params.compute_statistics ?? PRESET_PARAM_DEFAULTS.compute_statistics),
    stats_columns: asStringArray(params.stats_columns),
    bloom_filter_columns: asStringArray(params.bloom_filter_columns),
    stats_compute_ndv: Boolean(params.stats_compute_ndv ?? PRESET_PARAM_DEFAULTS.stats_compute_ndv),
    cooldown_hours: asNumber(guardrails.cooldown_hours, PRESET_GUARDRAIL_DEFAULTS.cooldown_hours),
    deadline_minutes: optionalNumber(guardrails.deadline_minutes),
    sla_freshness_hours: asNumber(
      guardrails.sla_freshness_hours,
      PRESET_GUARDRAIL_DEFAULTS.sla_freshness_hours
    ),
    sla_maintenance_days: asNumber(
      guardrails.sla_maintenance_days,
      PRESET_GUARDRAIL_DEFAULTS.sla_maintenance_days
    ),
    sla_p95_latency_ms: asNumber(
      guardrails.sla_p95_latency_ms,
      PRESET_GUARDRAIL_DEFAULTS.sla_p95_latency_ms
    ),
    adaptive_routing: Boolean(guardrails.adaptive_routing ?? PRESET_GUARDRAIL_DEFAULTS.adaptive_routing),
    engine_pool: enginePool,
    spark_min_gb: prefs.spark_min_gb,
    native_min_gb: prefs.native_min_gb,
    latency_p99_ms: prefs.latency_p99_ms,
    routing_rules: readRoutingRules(guardrails)
  };
};

const buildParamsPatch = (values: PresetFormValues): JsonObject => {
  const patch: JsonObject = {
    min_small_files: values.min_small_files,
    chunk_size_gb: values.chunk_size_gb,
    max_partitions_per_chunk: values.max_partitions_per_chunk,
    delete_ratio_trigger: values.delete_ratio_trigger,
    partition_write_idle_hours: values.partition_write_idle_hours,
    retention_days: values.retention_days,
    retain_last: values.retain_last,
    file_size_mb: values.file_size_mb,
    compute_statistics: values.compute_statistics,
    stats_compute_ndv: values.stats_compute_ndv
  };

  if (values.max_chunks != null) patch.max_chunks = values.max_chunks;
  if (values.orphan_retention_days != null) patch.orphan_retention_days = values.orphan_retention_days;
  if (values.stats_columns?.length) patch.stats_columns = values.stats_columns;
  if (values.bloom_filter_columns?.length) patch.bloom_filter_columns = values.bloom_filter_columns;

  return patch;
};

const buildGuardrailsPatch = (values: PresetFormValues): JsonObject => {
  const patch: JsonObject = {
    cooldown_hours: values.cooldown_hours,
    sla_freshness_hours: values.sla_freshness_hours,
    sla_maintenance_days: values.sla_maintenance_days,
    sla_p95_latency_ms: values.sla_p95_latency_ms,
    adaptive_routing: values.adaptive_routing
  };

  if (values.deadline_minutes != null) patch.deadline_minutes = values.deadline_minutes;
  if (values.engine_pool?.length) patch.engine_pool = values.engine_pool;

  const preferences: JsonObject = {
    spark_min_gb: values.spark_min_gb,
    native_min_gb: values.native_min_gb,
    latency_p99_ms: values.latency_p99_ms
  };
  patch.engine_preferences = preferences;

  const routingRules = (values.routing_rules ?? [])
    .filter((row) => row.field && row.op && row.destination && row.value !== "" && row.value != null)
    .map((row) => ({
      predicate: { field: row.field, op: row.op, value: row.value },
      destination: row.destination
    }));
  if (routingRules.length) patch.routing_rules = routingRules;

  return patch;
};

const mergePreservingUnknown = (
  existing: JsonObject | undefined,
  patch: JsonObject,
  editableKeys: readonly string[]
): JsonObject => {
  const merged: JsonObject = { ...(existing ?? {}) };
  for (const key of editableKeys) {
    if (key in patch) {
      merged[key] = patch[key];
      continue;
    }
    delete merged[key];
  }
  return merged;
};

export const formValuesToPresetWrite = (
  values: PresetFormValues,
  existingPreset?: PresetRead | null
): PresetWrite => {
  const paramsPatch = buildParamsPatch(values);
  const guardrailsPatch = buildGuardrailsPatch(values);

  return {
    name: values.name,
    description: values.description || null,
    destination_id: values.destination_id,
    mode: values.mode,
    operations: values.operations,
    schedule_cron: values.schedule_cron || null,
    priority: values.priority,
    enabled: values.enabled,
    is_default: values.is_default,
    params: mergePreservingUnknown(existingPreset?.params, paramsPatch, EDITABLE_PARAM_KEYS),
    guardrails: mergePreservingUnknown(
      existingPreset?.guardrails,
      guardrailsPatch,
      EDITABLE_GUARDRAIL_KEYS
    )
  };
};

export const formatPresetSummary = (preset: PresetRead): string => {
  const params = preset.params ?? {};
  const guardrails = preset.guardrails ?? {};
  const parts: string[] = [];

  const minSmall = params.min_small_files ?? PRESET_PARAM_DEFAULTS.min_small_files;
  const chunkGb = params.chunk_size_gb ?? PRESET_PARAM_DEFAULTS.chunk_size_gb;
  parts.push(`${minSmall} small files · ${chunkGb} GB chunks`);

  const retention = params.retention_days ?? PRESET_PARAM_DEFAULTS.retention_days;
  const retainLast = params.retain_last ?? PRESET_PARAM_DEFAULTS.retain_last;
  const orphanDays = params.orphan_retention_days ?? retention;
  parts.push(`expire ${retention}d · retain ${retainLast} · orphan ${orphanDays}d`);

  const cooldown = guardrails.cooldown_hours;
  if (cooldown != null) parts.push(`${cooldown}h cooldown`);

  if (guardrails.adaptive_routing) {
    const prefs =
      guardrails.engine_preferences && typeof guardrails.engine_preferences === "object"
        ? (guardrails.engine_preferences as Record<string, unknown>)
        : {};
    const sparkMin = prefs.spark_min_gb ?? PRESET_GUARDRAIL_DEFAULTS.spark_min_gb;
    parts.push(`adaptive routing · Spark ≥ ${sparkMin} GB`);
  }

  const routingRules = guardrails.routing_rules;
  if (Array.isArray(routingRules) && routingRules.length > 0) {
    parts.push(`${routingRules.length} routing rule${routingRules.length === 1 ? "" : "s"}`);
  }

  return parts.join(" · ");
};

const validateNonNegative = (label: string, value?: number | null): string | null => {
  if (value == null) return null;
  if (!Number.isFinite(value) || value < 0) {
    return `${label} must be a non-negative number`;
  }
  return null;
};

const validatePositive = (label: string, value?: number | null): string | null => {
  if (value == null) return null;
  if (!Number.isFinite(value) || value < 1) {
    return `${label} must be at least 1`;
  }
  return null;
};

const validateDeleteRatio = (value?: number): string | null => {
  if (value == null) return null;
  if (!Number.isFinite(value) || value < 0 || value > 100) {
    return "Delete ratio trigger must be between 0 and 100";
  }
  return null;
};

const validateRoutingRules = (value?: RoutingRuleFormRow[]): string | null => {
  if (!value?.length) return null;
  for (const [index, row] of value.entries()) {
    const hasAny = Boolean(row.field || row.op || row.destination || (row.value !== "" && row.value != null));
    if (!hasAny) continue;
    if (!row.field || !row.op || !row.destination || row.value === "" || row.value == null) {
      return `Routing rule ${index + 1} needs field, operator, value, and destination`;
    }
  }
  return null;
};

export const buildDefaultPresetFormValues = (destinations: DestinationRead[]): PresetFormValues => {
  const base = presetToFormValues(null, destinations);
  return {
    name: "",
    description: "",
    destination_id: base.destination_id ?? destinations[0]?.id ?? "",
    mode: base.mode ?? "standard",
    operations: base.operations ?? DEFAULT_OPERATIONS,
    schedule_cron: base.schedule_cron ?? null,
    priority: base.priority ?? 100,
    enabled: base.enabled ?? true,
    is_default: base.is_default ?? false,
    min_small_files: base.min_small_files ?? PRESET_PARAM_DEFAULTS.min_small_files,
    chunk_size_gb: base.chunk_size_gb ?? PRESET_PARAM_DEFAULTS.chunk_size_gb,
    max_partitions_per_chunk:
      base.max_partitions_per_chunk ?? PRESET_PARAM_DEFAULTS.max_partitions_per_chunk,
    max_chunks: base.max_chunks ?? null,
    delete_ratio_trigger: base.delete_ratio_trigger ?? PRESET_PARAM_DEFAULTS.delete_ratio_trigger,
    partition_write_idle_hours:
      base.partition_write_idle_hours ?? PRESET_PARAM_DEFAULTS.partition_write_idle_hours,
    retention_days: base.retention_days ?? PRESET_PARAM_DEFAULTS.retention_days,
    retain_last: base.retain_last ?? PRESET_PARAM_DEFAULTS.retain_last,
    orphan_retention_days: base.orphan_retention_days ?? null,
    file_size_mb: base.file_size_mb ?? PRESET_PARAM_DEFAULTS.file_size_mb,
    compute_statistics: base.compute_statistics ?? PRESET_PARAM_DEFAULTS.compute_statistics,
    stats_columns: base.stats_columns,
    bloom_filter_columns: base.bloom_filter_columns,
    stats_compute_ndv: base.stats_compute_ndv ?? PRESET_PARAM_DEFAULTS.stats_compute_ndv,
    cooldown_hours: base.cooldown_hours ?? PRESET_GUARDRAIL_DEFAULTS.cooldown_hours,
    deadline_minutes: base.deadline_minutes ?? null,
    sla_freshness_hours: base.sla_freshness_hours ?? PRESET_GUARDRAIL_DEFAULTS.sla_freshness_hours,
    sla_maintenance_days: base.sla_maintenance_days ?? PRESET_GUARDRAIL_DEFAULTS.sla_maintenance_days,
    sla_p95_latency_ms: base.sla_p95_latency_ms ?? PRESET_GUARDRAIL_DEFAULTS.sla_p95_latency_ms,
    adaptive_routing: base.adaptive_routing ?? PRESET_GUARDRAIL_DEFAULTS.adaptive_routing,
    engine_pool: base.engine_pool,
    spark_min_gb: base.spark_min_gb ?? PRESET_GUARDRAIL_DEFAULTS.spark_min_gb,
    native_min_gb: base.native_min_gb ?? PRESET_GUARDRAIL_DEFAULTS.native_min_gb,
    latency_p99_ms: base.latency_p99_ms ?? PRESET_GUARDRAIL_DEFAULTS.latency_p99_ms,
    routing_rules: base.routing_rules ?? []
  };
};

export const validatePresetFormValues = (values: PresetFormValues): Record<string, string> | null => {
  const errors: Record<string, string> = {};

  if (!values.name?.trim()) errors.name = "Name is required";
  if (!values.destination_id) errors.destination_id = "Engine is required";
  if (!values.mode) errors.mode = "Mode is required";

  const cronError = validateCronExpression(values.schedule_cron, { allowEmpty: true });
  if (cronError) errors.schedule_cron = cronError;

  const fieldChecks: Array<[keyof PresetFormValues, string | null]> = [
    ["min_small_files", validateNonNegative("Min small files", values.min_small_files)],
    ["chunk_size_gb", validatePositive("Chunk size", values.chunk_size_gb)],
    ["max_partitions_per_chunk", validatePositive("Max partitions per chunk", values.max_partitions_per_chunk)],
    ["max_chunks", validatePositive("Max chunks", values.max_chunks)],
    ["delete_ratio_trigger", validateDeleteRatio(values.delete_ratio_trigger)],
    ["partition_write_idle_hours", validateNonNegative("Partition idle hours", values.partition_write_idle_hours)],
    ["file_size_mb", validatePositive("Target file size", values.file_size_mb)],
    ["retention_days", validatePositive("Snapshot retention", values.retention_days)],
    ["retain_last", validatePositive("Retain last snapshots", values.retain_last)],
    ["orphan_retention_days", validatePositive("Orphan retention", values.orphan_retention_days)],
    ["cooldown_hours", validateNonNegative("Cooldown", values.cooldown_hours)],
    ["deadline_minutes", validatePositive("Run deadline", values.deadline_minutes)],
    ["sla_freshness_hours", validateNonNegative("Freshness SLA", values.sla_freshness_hours)],
    ["sla_maintenance_days", validateNonNegative("Maintenance SLA", values.sla_maintenance_days)],
    ["sla_p95_latency_ms", validateNonNegative("P95 latency SLA", values.sla_p95_latency_ms)],
    ["spark_min_gb", validateNonNegative("Spark threshold", values.spark_min_gb)],
    ["native_min_gb", validateNonNegative("Native threshold", values.native_min_gb)],
    ["latency_p99_ms", validateNonNegative("Catalog latency threshold", values.latency_p99_ms)]
  ];

  for (const [field, message] of fieldChecks) {
    if (message) errors[field] = message;
  }

  const routingError = validateRoutingRules(values.routing_rules);
  if (routingError) errors.routing_rules = routingError;

  return Object.keys(errors).length ? errors : null;
};
