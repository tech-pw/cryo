import type { TableFilterState } from "./predicate";
import { NO_PRESET_FILTER } from "./predicate";

export interface CatalogFilterOption {
  label: string;
  value: string;
}

export interface FilterChip {
  key: string;
  label: string;
  onRemove: () => void;
}

const costPrecisionLabels: Record<string, string> = {
  heuristic: "Estimated cost",
  metadata: "Metadata cost",
  storage_truth: "Storage-verified cost"
};

const titleCasePriority = (value: string) =>
  value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();

export const buildFilterChips = (
  filters: TableFilterState,
  catalogOptions: CatalogFilterOption[],
  onPatch: (next: Partial<TableFilterState>) => void,
  presetOptions: CatalogFilterOption[] = []
): FilterChip[] => {
  const chips: FilterChip[] = [];

  if (filters.activeOnly === false) {
    chips.push({
      key: "active-only",
      label: "Include inactive tables",
      onRemove: () => onPatch({ activeOnly: true })
    });
  }

  if (filters.sourceIds?.length) {
    const catalogById = new Map(catalogOptions.map((option) => [option.value, option.label]));
    const names = filters.sourceIds.map((id) => catalogById.get(id) ?? id);
    chips.push({
      key: "catalog",
      label: `Catalog: ${names.join(", ")}`,
      onRemove: () => onPatch({ sourceIds: undefined })
    });
  }

  if (filters.presetFilter?.length) {
    const presetById = new Map(presetOptions.map((option) => [option.value, option.label]));
    const names = filters.presetFilter.map((id) =>
      id === NO_PRESET_FILTER ? "No preset" : (presetById.get(id) ?? id)
    );
    chips.push({
      key: "preset",
      label: `Preset: ${names.join(", ")}`,
      onRemove: () => onPatch({ presetFilter: undefined })
    });
  }

  if (filters.grades?.length) {
    chips.push({
      key: "grade",
      label: `Grade: ${filters.grades.join(", ")}`,
      onRemove: () => onPatch({ grades: undefined })
    });
  }

  if (filters.priorities?.length) {
    chips.push({
      key: "priority",
      label: `Priority: ${filters.priorities.map(titleCasePriority).join(", ")}`,
      onRemove: () => onPatch({ priorities: undefined })
    });
  }

  if (typeof filters.needsCompaction === "boolean") {
    chips.push({
      key: "compaction",
      label: filters.needsCompaction ? "Needs compaction" : "Healthy only",
      onRemove: () => onPatch({ needsCompaction: undefined })
    });
  }

  if (filters.schemaSearch?.trim()) {
    chips.push({
      key: "search",
      label: `Name contains "${filters.schemaSearch.trim()}"`,
      onRemove: () => onPatch({ schemaSearch: undefined })
    });
  }

  if (typeof filters.minSizeGb === "number" && Number.isFinite(filters.minSizeGb)) {
    chips.push({
      key: "min-size",
      label: `Size ≥ ${filters.minSizeGb} GB`,
      onRemove: () => onPatch({ minSizeGb: null })
    });
  }

  if (typeof filters.minSmallFiles === "number" && Number.isFinite(filters.minSmallFiles)) {
    chips.push({
      key: "min-small-files",
      label: `Small files ≥ ${filters.minSmallFiles.toLocaleString()}`,
      onRemove: () => onPatch({ minSmallFiles: null })
    });
  }

  if (typeof filters.minMonthlySavings === "number" && Number.isFinite(filters.minMonthlySavings)) {
    chips.push({
      key: "min-savings",
      label: `Savings ≥ $${filters.minMonthlySavings}/mo`,
      onRemove: () => onPatch({ minMonthlySavings: null })
    });
  }

  if (typeof filters.minReclaimableGb === "number" && Number.isFinite(filters.minReclaimableGb)) {
    chips.push({
      key: "min-reclaim",
      label: `Reclaimable ≥ ${filters.minReclaimableGb} GB`,
      onRemove: () => onPatch({ minReclaimableGb: null })
    });
  }

  if (filters.costPrecision) {
    chips.push({
      key: "cost-precision",
      label: costPrecisionLabels[filters.costPrecision] ?? `Cost: ${filters.costPrecision}`,
      onRemove: () => onPatch({ costPrecision: null })
    });
  }

  return chips;
};

export const filterScopeLabel = (filters: TableFilterState, chips: FilterChip[]): string => {
  if (chips.length > 0) return "Filtered view";
  return filters.activeOnly === false ? "All tables" : "Active tables";
};
