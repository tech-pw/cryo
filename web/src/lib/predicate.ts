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
  sourceIds?: string[];
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
    filters.sourceIds?.length ? leaf("source_id", "in", filters.sourceIds) : null,
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

export const predicateSummary = (
  filters: TableFilterState,
  catalogOptions: Array<{ label: string; value: string }> = []
): string => {
  const catalogById = new Map(catalogOptions.map((option) => [option.value, option.label]));
  const parts = [
    filters.sourceIds?.length
      ? `Catalog: ${filters.sourceIds.map((id) => catalogById.get(id) ?? id).join(", ")}`
      : null,
    filters.grades?.length ? `Grade: ${filters.grades.join(", ")}` : null,
    filters.priorities?.length
      ? `Priority: ${filters.priorities.map((p) => p.charAt(0) + p.slice(1).toLowerCase()).join(", ")}`
      : null,
    typeof filters.needsCompaction === "boolean"
      ? filters.needsCompaction
        ? "Needs compaction"
        : "Healthy only"
      : null,
    filters.schemaSearch?.trim() ? `Name contains "${filters.schemaSearch.trim()}"` : null,
    typeof filters.minSizeGb === "number" && Number.isFinite(filters.minSizeGb)
      ? `Size ≥ ${filters.minSizeGb} GB`
      : null,
    typeof filters.minSmallFiles === "number" && Number.isFinite(filters.minSmallFiles)
      ? `Small files ≥ ${filters.minSmallFiles}`
      : null,
    typeof filters.minMonthlySavings === "number" && Number.isFinite(filters.minMonthlySavings)
      ? `Savings ≥ $${filters.minMonthlySavings}/mo`
      : null,
    typeof filters.minReclaimableGb === "number" && Number.isFinite(filters.minReclaimableGb)
      ? `Reclaimable ≥ ${filters.minReclaimableGb} GB`
      : null,
    filters.costPrecision
      ? `Cost: ${filters.costPrecision === "heuristic" ? "estimated" : filters.costPrecision.replace("_", " ")}`
      : null
  ].filter(Boolean);

  if (parts.length) {
    if (filters.activeOnly === false) {
      parts.unshift("Include inactive tables");
    }
    return parts.join(" · ");
  }
  return filters.activeOnly === false ? "All tables" : "Active tables";
};

const escapeGlob = (value: string): string => value.replace(/([?*])/g, "\\$1");
