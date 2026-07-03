export type PredicateOp = "=" | "!=" | "<" | "<=" | ">" | ">=" | "in" | "like" | "glob";

export interface PredicateLeaf {
  field: string;
  op: PredicateOp;
  value: string | number | boolean | Array<string | number | boolean>;
}

export type Predicate =
  | PredicateLeaf
  | { all: Predicate[] }
  | { any: Predicate[] }
  | { not: Predicate };

export type CostPrecision = "heuristic" | "metadata" | "storage_truth";

export interface TableFilterState {
  grades?: string[];
  priorities?: string[];
  needsCompaction?: boolean;
  schemaSearch?: string;
  minSizeGb?: number | null;
  minSmallFiles?: number | null;
  minMonthlySavings?: number | null;
  minReclaimableGb?: number | null;
  costPrecision?: CostPrecision | null;
  activeOnly?: boolean;
}

export const leaf = (
  field: string,
  op: PredicateOp,
  value: PredicateLeaf["value"]
): PredicateLeaf => ({ field, op, value });

export const all = (children: Array<Predicate | null | undefined>): Predicate | null => {
  const compacted = children.filter(Boolean) as Predicate[];
  if (compacted.length === 0) return null;
  if (compacted.length === 1) return compacted[0];
  return { all: compacted };
};

export const any = (children: Array<Predicate | null | undefined>): Predicate | null => {
  const compacted = children.filter(Boolean) as Predicate[];
  if (compacted.length === 0) return null;
  if (compacted.length === 1) return compacted[0];
  return { any: compacted };
};

export const not = (child: Predicate): Predicate => ({ not: child });

export const serializePredicate = (predicate: Predicate | null | undefined): string | undefined =>
  predicate ? JSON.stringify(predicate) : undefined;

export const tableFiltersToPredicate = (filters: TableFilterState): Predicate | null =>
  all([
    filters.activeOnly === false ? null : leaf("is_active", "=", true),
    filters.grades?.length ? leaf("grade", "in", filters.grades) : null,
    filters.priorities?.length ? leaf("compaction_priority", "in", filters.priorities) : null,
    typeof filters.needsCompaction === "boolean"
      ? leaf("needs_compaction", "=", filters.needsCompaction)
      : null,
    filters.schemaSearch?.trim()
      ? any([
          leaf("schema_name", "glob", `*${escapeGlob(filters.schemaSearch.trim())}*`),
          leaf("table_name", "glob", `*${escapeGlob(filters.schemaSearch.trim())}*`)
        ])
      : null,
    typeof filters.minSizeGb === "number" && Number.isFinite(filters.minSizeGb)
      ? leaf("total_size_gb", ">=", filters.minSizeGb)
      : null,
    typeof filters.minSmallFiles === "number" && Number.isFinite(filters.minSmallFiles)
      ? leaf("small_files_count", ">=", filters.minSmallFiles)
      : null,
    typeof filters.minMonthlySavings === "number" && Number.isFinite(filters.minMonthlySavings)
      ? leaf("projected_savings_usd_month", ">=", filters.minMonthlySavings)
      : null,
    typeof filters.minReclaimableGb === "number" && Number.isFinite(filters.minReclaimableGb)
      ? leaf("reclaimable_bytes", ">=", Math.round(filters.minReclaimableGb * 1024 ** 3))
      : null,
    filters.costPrecision ? leaf("cost_precision", "=", filters.costPrecision) : null
  ]);

export const predicateSummary = (filters: TableFilterState): string => {
  const parts = [
    filters.grades?.length ? `grade ${filters.grades.join(",")}` : null,
    filters.priorities?.length ? `priority ${filters.priorities.join(",")}` : null,
    typeof filters.needsCompaction === "boolean"
      ? filters.needsCompaction
        ? "needs compaction"
        : "healthy"
      : null,
    filters.schemaSearch ? `schema/table "${filters.schemaSearch}"` : null,
    filters.minSizeGb ? `>= ${filters.minSizeGb} GB` : null,
    filters.minSmallFiles ? `>= ${filters.minSmallFiles} small files` : null,
    filters.minMonthlySavings ? `>= $${filters.minMonthlySavings}/mo savings` : null,
    filters.minReclaimableGb ? `>= ${filters.minReclaimableGb} GB reclaimable` : null,
    filters.costPrecision ? `precision ${filters.costPrecision}` : null
  ].filter(Boolean);

  return parts.length ? parts.join(" · ") : "all active tables";
};

const escapeGlob = (value: string): string => value.replace(/([?*])/g, "\\$1");
