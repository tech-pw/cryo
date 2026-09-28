import type { PropertyRecommendationRead } from "../../../api/client";
import type {
  ColumnSketchRead,
  DataQualityProfileRead,
  PartitionRead,
  PiiMatchRead,
  TableStatsRead
} from "../../../api/types";
import { formatPartitionColumnLabel } from "../helpers";

export const NOT_MEASURED = "Not measured";

export interface ScanStats extends TableStatsRead {
  has_sort_order?: boolean | null;
  target_file_size_bytes?: number | null;
  write_distribution_mode?: string | null;
}

export interface SchemaColumnRow {
  name: string;
  type: string;
  nullRate: number | null;
  lowerBound: string | null;
  upperBound: string | null;
  ndv: number | null;
  p50: number | null;
  p95: number | null;
  isPartition: boolean;
  isSort: boolean;
  piiCategory: string | null;
  drift: boolean;
  bloom: boolean;
}

export interface NamedMeasure {
  label: string;
  value: string;
}

export interface NamedCheck {
  name: "Alignment" | "Granularity";
  observed: string;
  status: "Unknown" | "Too fine" | "Too granular";
}

export interface RankedPartition {
  id: string;
  label: string;
  value: number | null;
  smallFileRate: string;
}

export interface SmallFileScan {
  scanAt: string;
  smallFiles: number | null;
}

interface PartitionField {
  base_column?: unknown;
  partition_field?: unknown;
  transform?: unknown;
  transform_param?: unknown;
}

export const measuredNumber = (value: unknown): number | null => {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
};

export const formatCount = (value: number | null): string =>
  value == null ? NOT_MEASURED : value.toLocaleString();

export const formatMegabytes = (value: number | null): string =>
  value == null ? NOT_MEASURED : `${value.toFixed(1)} MB`;

export const formatByteCount = (bytes: number | null): string => {
  if (bytes == null) return NOT_MEASURED;
  if (bytes === 0) return "0 B";
  const mb = bytes / (1024 * 1024);
  if (Math.abs(mb) >= 1) {
    const rendered = Number.isInteger(mb) ? String(mb) : mb.toFixed(1);
    return `${rendered} MB`;
  }
  return `${bytes.toLocaleString()} B`;
};

export const formatNullRate = (rate: number | null): string => {
  if (rate == null) return NOT_MEASURED;
  return `${(rate * 100).toFixed(1)}% Null`;
};

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
};

const icebergProperties = (raw: unknown): Record<string, string> => {
  const iceberg = asRecord(asRecord(raw)?.iceberg_properties);
  if (!iceberg) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(iceberg)) {
    if (value == null) continue;
    out[key] = String(value);
  }
  return out;
};

const partitionFields = (raw: unknown): PartitionField[] => {
  const fields = asRecord(raw)?.partition_fields;
  if (!Array.isArray(fields)) return [];
  return fields.filter((field): field is PartitionField => Boolean(asRecord(field)));
};

const fieldColumn = (field: PartitionField): string => {
  const raw =
    typeof field.base_column === "string"
      ? field.base_column
      : typeof field.partition_field === "string"
        ? field.partition_field
        : null;
  return formatPartitionColumnLabel(raw);
};

const fieldWords = (field: PartitionField): string => {
  const column = fieldColumn(field);
  const transform = typeof field.transform === "string" && field.transform.trim() ? field.transform.trim() : null;
  if (!transform) return column;
  if (transform === "bucket" && field.transform_param != null && field.transform_param !== "") {
    return `bucket[${String(field.transform_param)}](${column})`;
  }
  return `${transform}(${column})`;
};

export const partitionSpecInWords = (stats: ScanStats | null | undefined): string => {
  if (!stats) return NOT_MEASURED;
  const fields = partitionFields(stats.raw);
  if (fields.length) return fields.map(fieldWords).join(", ");
  return formatPartitionColumnLabel(stats.partition_col);
};

export const isUnpartitionedTable = (stats: ScanStats | null | undefined): boolean => {
  if (!stats) return false;
  if (partitionFields(stats.raw).length > 0) return false;
  return formatPartitionColumnLabel(stats.partition_col) === "Unpartitioned";
};

const rawFlag = (raw: unknown, key: string): boolean | null => {
  const value = asRecord(raw)?.[key];
  if (typeof value === "boolean") return value;
  return null;
};

export const sortOrderLabel = (stats: ScanStats | null | undefined): string => {
  if (!stats) return NOT_MEASURED;
  const written = icebergProperties(stats.raw)["write.sort-order"];
  if (written && written !== "set") return written;
  const flag = typeof stats.has_sort_order === "boolean" ? stats.has_sort_order : rawFlag(stats.raw, "has_sort_order");
  if (flag === true) return "Set";
  if (flag === false) return "No sort order";
  return NOT_MEASURED;
};

export const sortColumnNames = (raw: unknown): string[] => {
  const order = icebergProperties(raw)["write.sort-order"];
  if (!order || order === "set") return [];
  return order
    .split(",")
    .map((part) => part.trim().split(/\s+/)[0] ?? "")
    .map((name) => name.replace(/[(),]/g, ""))
    .filter((name) => name.length > 0);
};

const partitionColumnNames = (stats: ScanStats | null | undefined): Set<string> => {
  const names = new Set<string>();
  for (const field of partitionFields(stats?.raw)) {
    if (typeof field.base_column === "string" && field.base_column.trim()) {
      names.add(field.base_column.trim().toLowerCase());
    }
    if (typeof field.partition_field === "string" && field.partition_field.trim()) {
      names.add(field.partition_field.trim().toLowerCase());
    }
  }
  const col = stats?.partition_col;
  if (col) {
    const inner = col.match(/\(([^)]+)\)/);
    const token = (inner?.[1] ?? col).trim().toLowerCase();
    if (token && token !== "unpartitioned" && token !== "_unpartitioned") names.add(token);
  }
  return names;
};

const granularityObserved = (stats: ScanStats, unpartitioned: boolean): string => {
  if (unpartitioned) return "Unpartitioned";
  const fields = partitionFields(stats.raw);
  const transform = fields.length
    ? fields.map((field) => (typeof field.transform === "string" && field.transform ? field.transform : NOT_MEASURED)).join(", ")
    : stats.partition_col_type?.trim() || NOT_MEASURED;
  const partitions = measuredNumber(stats.total_partitions);
  const avg = measuredNumber(stats.avg_files_per_partition);
  return [
    `transform ${transform}`,
    partitions == null ? `${NOT_MEASURED} partitions` : `${partitions.toLocaleString()} partitions`,
    avg == null ? `${NOT_MEASURED} files/partition` : `${avg.toFixed(1)} files/partition`
  ].join(", ");
};

const granularityStatus = (stats: ScanStats, unpartitioned: boolean): NamedCheck["status"] => {
  if (unpartitioned) return "Unknown";
  const partitions = measuredNumber(stats.total_partitions);
  const avg = measuredNumber(stats.avg_files_per_partition);
  if (partitions != null && partitions > 10_000) return "Too fine";
  if (partitions != null && avg != null && avg < 2 && partitions > 500) return "Too granular";
  return "Unknown";
};

export const layoutChecks = (stats: ScanStats | null | undefined): NamedCheck[] => {
  if (!stats) {
    return [
      { name: "Alignment", observed: NOT_MEASURED, status: "Unknown" },
      { name: "Granularity", observed: NOT_MEASURED, status: "Unknown" }
    ];
  }
  const unpartitioned = isUnpartitionedTable(stats);
  const spec = partitionSpecInWords(stats);
  return [
    { name: "Alignment", observed: spec, status: "Unknown" },
    { name: "Granularity", observed: granularityObserved(stats, unpartitioned), status: granularityStatus(stats, unpartitioned) }
  ];
};

const pickCount = (primary: unknown, fallback: unknown): number | null => {
  if (primary !== undefined) return measuredNumber(primary);
  return measuredNumber(fallback);
};

export const fileMeasures = (stats: ScanStats | null | undefined): NamedMeasure[] => {
  const raw = asRecord(stats?.raw);
  const total = measuredNumber(stats?.total_files);
  const small = measuredNumber(stats?.small_files_count);
  const other = total != null && small != null ? total - small : null;
  const target = pickCount(stats?.target_file_size_bytes, raw?.target_file_size_bytes);
  const propertyTarget = icebergProperties(stats?.raw)["write.target-file-size-bytes"];
  const targetBytes =
    target ?? (propertyTarget && /^\d+$/.test(propertyTarget) ? Number(propertyTarget) : null);
  return [
    { label: "Data files", value: formatCount(total) },
    {
      label: "Position delete files",
      value: formatCount(pickCount(stats?.position_delete_files, raw?.position_delete_files))
    },
    {
      label: "Equality delete files",
      value: formatCount(pickCount(stats?.equality_delete_files, raw?.equality_delete_files))
    },
    { label: "Manifests", value: formatCount(measuredNumber(stats?.manifest_count)) },
    { label: "Average file size", value: formatMegabytes(measuredNumber(stats?.avg_file_size_mb)) },
    { label: "Writer target", value: formatByteCount(targetBytes) },
    { label: "Small files", value: formatCount(small) },
    { label: "Other files", value: formatCount(other) }
  ];
};

const boundText = (value: unknown): string | null => {
  if (value == null || value === "") return null;
  return String(value);
};

const columnQualityRows = (raw: unknown): Array<Record<string, unknown>> => {
  const columns = asRecord(raw)?.column_quality;
  if (!Array.isArray(columns)) return [];
  return columns
    .map(asRecord)
    .filter((col): col is Record<string, unknown> => typeof col?.column_name === "string" && col.column_name.length > 0);
};

interface ColumnAccum {
  name: string;
  type: string | null;
  nullRate: number | null;
  nullRateSeen: boolean;
  lowerBound: string | null;
  upperBound: string | null;
  ndv: number | null;
  p50: number | null;
  p95: number | null;
  isPartition: boolean;
  isSort: boolean;
  piiCategory: string | null;
  drift: boolean;
  bloom: boolean;
}

const blankColumn = (name: string): ColumnAccum => ({
  name,
  type: null,
  nullRate: null,
  nullRateSeen: false,
  lowerBound: null,
  upperBound: null,
  ndv: null,
  p50: null,
  p95: null,
  isPartition: false,
  isSort: false,
  piiCategory: null,
  drift: false,
  bloom: false
});

export const compareSchemaColumns = (a: { name: string; nullRate: number | null }, b: { name: string; nullRate: number | null }): number => {
  const aKnown = a.nullRate != null;
  const bKnown = b.nullRate != null;
  if (aKnown !== bKnown) return aKnown ? -1 : 1;
  return a.name.localeCompare(b.name);
};

export const mergeSchemaColumns = (input: {
  stats: ScanStats | null | undefined;
  pii: PiiMatchRead[];
  quality: DataQualityProfileRead | null | undefined;
  sketches: Record<string, ColumnSketchRead> | null | undefined;
  bloomColumns: string[];
}): SchemaColumnRow[] => {
  const byName = new Map<string, ColumnAccum>();
  const ensure = (name: string): ColumnAccum => {
    const key = name.toLowerCase();
    const existing = byName.get(key);
    if (existing) return existing;
    const created = blankColumn(name);
    byName.set(key, created);
    return created;
  };

  for (const col of columnQualityRows(input.stats?.raw)) {
    const row = ensure(String(col.column_name));
    if (typeof col.column_type === "string" && col.column_type) row.type = col.column_type;
    if ("null_rate" in col) {
      row.nullRateSeen = true;
      row.nullRate = measuredNumber(col.null_rate);
    }
    row.lowerBound = boundText(col.lower_bound) ?? row.lowerBound;
    row.upperBound = boundText(col.upper_bound) ?? row.upperBound;
  }

  const partitionNames = partitionColumnNames(input.stats);
  const sortNames = new Set(sortColumnNames(input.stats?.raw).map((name) => name.toLowerCase()));
  const bloomNames = new Set(input.bloomColumns.map((name) => name.toLowerCase()));
  const added = new Set((input.quality?.new_columns ?? []).map((name) => name.toLowerCase()));
  const dropped = new Set((input.quality?.dropped_columns ?? []).map((name) => name.toLowerCase()));

  for (const metric of input.quality?.column_metrics ?? []) {
    const row = ensure(metric.column_name);
    if (!row.type && metric.column_type) row.type = metric.column_type;
    if (!row.nullRateSeen && metric.null_rate_pct != null && Number.isFinite(metric.null_rate_pct)) {
      row.nullRate = metric.null_rate_pct / 100;
      row.nullRateSeen = true;
    }
    row.lowerBound = row.lowerBound ?? boundText(metric.min_value);
    row.upperBound = row.upperBound ?? boundText(metric.max_value);
    if (metric.null_rate_spike || metric.value_range_changed) row.drift = true;
  }

  for (const name of [...added, ...dropped]) {
    const match = [...byName.values()].find((row) => row.name.toLowerCase() === name);
    if (match) match.drift = true;
    else ensure(name).drift = true;
  }

  for (const match of input.pii) {
    const row = ensure(match.column_name);
    if (!row.type && match.column_type) row.type = match.column_type;
    if (match.pii_category && match.pii_category !== "NONE") row.piiCategory = match.pii_category;
  }

  for (const [name, sketch] of Object.entries(input.sketches ?? {})) {
    const row = ensure(name);
    if (!row.type && sketch.field_type) row.type = sketch.field_type;
    row.ndv = measuredNumber(sketch.ndv_estimate);
    row.p50 = measuredNumber(sketch.quantiles?.p50);
    row.p95 = measuredNumber(sketch.quantiles?.p95);
  }

  for (const row of byName.values()) {
    const key = row.name.toLowerCase();
    row.isPartition = partitionNames.has(key);
    row.isSort = sortNames.has(key);
    row.bloom = bloomNames.has(key);
    if (added.has(key) || dropped.has(key)) row.drift = true;
  }

  return [...byName.values()]
    .map((row) => ({
      name: row.name,
      type: row.type || NOT_MEASURED,
      nullRate: row.nullRate,
      lowerBound: row.lowerBound,
      upperBound: row.upperBound,
      ndv: row.ndv,
      p50: row.p50,
      p95: row.p95,
      isPartition: row.isPartition,
      isSort: row.isSort,
      piiCategory: row.piiCategory,
      drift: row.drift,
      bloom: row.bloom
    }))
    .sort(compareSchemaColumns);
};

const smallFileRate = (small: number | null, files: number | null): string => {
  if (small == null || files == null || files === 0) return NOT_MEASURED;
  return `${((small / files) * 100).toFixed(1)}%`;
};

type PartitionRow = PartitionRead & {
  partition_key?: string | null;
  partition_path?: string | null;
};

export const rankPartitions = (rows: PartitionRead[]): { metric: string; ranked: RankedPartition[] } => {
  const useSmall = rows.some((row) => measuredNumber(row.small_files_in_partition_count) != null);
  const metric = useSmall ? "small files" : "files";
  const ranked = rows.map((row, index) => {
    const source = row as PartitionRow;
    const small = measuredNumber(source.small_files_in_partition_count);
    const files = measuredNumber(source.files_per_partition);
    const label = source.partition_value || source.partition_path || source.partition_key || `Partition ${index + 1}`;
    return {
      id: String(source.id ?? `${label}-${index}`),
      label,
      value: useSmall ? small : files,
      smallFileRate: smallFileRate(small, files)
    };
  });
  ranked.sort((a, b) => {
    if (a.value == null && b.value == null) return a.label.localeCompare(b.label);
    if (a.value == null) return 1;
    if (b.value == null) return -1;
    if (a.value !== b.value) return b.value - a.value;
    return a.label.localeCompare(b.label);
  });
  return { metric, ranked };
};

export const smallFileSeries = (items: Array<Record<string, unknown>> | undefined): SmallFileScan[] =>
  (items ?? [])
    .map((item) => {
      const scanAt = typeof item.scan_at === "string" ? item.scan_at : null;
      if (!scanAt) return null;
      return { scanAt, smallFiles: measuredNumber(item.small_files_count) };
    })
    .filter((item): item is SmallFileScan => item != null)
    .sort((a, b) => a.scanAt.localeCompare(b.scanAt));

export const propertySource = (item: PropertyRecommendationRead & { source?: string | null }): string => {
  if (typeof item.source === "string" && item.source.trim()) return item.source.trim();
  return "Source not recorded";
};

export const propertyValue = (value: string | null | undefined): string => {
  if (value == null || value === "") return NOT_MEASURED;
  return value;
};
