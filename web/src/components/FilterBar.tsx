import { Search, SlidersHorizontal, X } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { buildFilterChips, filterScopeLabel, type CatalogFilterOption } from "../lib/filterChips";
import { NO_PRESET_FILTER, type TableFilterState } from "../lib/predicate";
import { FacetFilter } from "./FacetFilter";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { useDebouncedValue } from "./ui/data-table";
import { InputNumber } from "./ui/input-number";
import { Label } from "./ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Segmented } from "./ui/segmented";
import { Select } from "./ui/select";

export type { CatalogFilterOption };

interface Props {
  value: TableFilterState;
  onChange: (value: TableFilterState) => void;
  onReset?: () => void;
  catalogOptions?: CatalogFilterOption[];
  presetOptions?: CatalogFilterOption[];
  matchingTotal?: number;
  hasSelection?: boolean;
  filterSelectionActive?: boolean;
  onSelectAllMatching?: () => void;
  onClearSelection?: () => void;
  bulkActions?: ReactNode;
  leading?: ReactNode;
  /** Rendered after the filters (e.g. the plain-English query toggle). */
  trailing?: ReactNode;
  /** Right-aligned view controls (grouping, columns, export) that share the filter row. */
  actions?: ReactNode;
  hideSummary?: boolean;
  /** Hide the needs-compaction toggle when the page offers it as a quick view instead. */
  hideNeedsToggle?: boolean;
}

/** Chips for values the bar can't show on its own triggers (search, numeric thresholds, ...). */
const FACET_CHIP_KEYS = new Set(["catalog", "preset", "grade", "priority", "active-only"]);

const ADVANCED_KEYS = [
  "minSizeGb",
  "minSmallFiles",
  "minMonthlySavings",
  "minReclaimableGb",
  "costPrecision"
] as const satisfies readonly (keyof TableFilterState)[];

const GRADE_OPTIONS = ["A", "B", "C", "D", "F"].map((grade) => ({ label: grade, value: grade }));
const PRIORITY_OPTIONS = [
  { label: "High", value: "HIGH" },
  { label: "Medium", value: "MEDIUM" },
  { label: "Low", value: "LOW" }
];

const NumberField = ({
  id,
  label,
  unit,
  value,
  onChange
}: {
  id: string;
  label: string;
  unit: string;
  value: number | null | undefined;
  onChange: (next: number | undefined) => void;
}) => (
  <div className="flex flex-col gap-1">
    <Label htmlFor={id} className="text-xs text-muted-foreground">
      {label}
    </Label>
    <div className="relative">
      <InputNumber id={id} min={0} value={value} onChange={(next) => onChange(next ?? undefined)} className="pr-12" />
      <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-muted-foreground">
        {unit}
      </span>
    </div>
  </div>
);

const SearchField = ({ value, onChange }: { value: string; onChange: (next: string | undefined) => void }) => {
  const [draft, setDraft] = useState(value);
  const debounced = useDebouncedValue(draft, 300);
  useEffect(() => setDraft(value), [value]);
  useEffect(() => {
    if (debounced !== value) onChange(debounced.trim() ? debounced : undefined);
    // Only the settled draft should push upstream; `value` changes flow back through the effect above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  return (
    <div className="relative w-full sm:w-64">
      <Search
        className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <input
        type="search"
        aria-label="Schema or table search"
        placeholder="Search schema or table"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        className="h-[var(--control-height-sm)] w-full rounded-[var(--radius-default)] border border-[var(--color-control-border)] bg-background pl-8 pr-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]"
      />
    </div>
  );
};

export const FilterBar = ({
  value,
  onChange,
  onReset,
  catalogOptions = [],
  presetOptions = [],
  matchingTotal = 0,
  hasSelection = false,
  filterSelectionActive = false,
  onSelectAllMatching,
  onClearSelection,
  bulkActions,
  leading,
  trailing,
  actions,
  hideSummary = false,
  hideNeedsToggle = false
}: Props) => {
  const patch = (next: Partial<TableFilterState>) => onChange({ ...value, ...next });
  const chips = useMemo(
    () => buildFilterChips(value, catalogOptions, patch, presetOptions),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [value, catalogOptions, presetOptions]
  );
  const showBulkSelect = onSelectAllMatching !== undefined;
  const filterActive = chips.length > 0;
  const showSelectAll = showBulkSelect && (filterActive || hasSelection) && !filterSelectionActive;
  const showClear = showBulkSelect && hasSelection;
  const showBulk = showSelectAll || showClear || (showBulkSelect && filterSelectionActive);
  const scopeLabel = useMemo(() => filterScopeLabel(value, chips), [value, chips]);
  const matchLabel =
    matchingTotal === 1 ? "1 table matches" : `${matchingTotal.toLocaleString()} tables match`;
  const extraChips = chips.filter((chip) => !FACET_CHIP_KEYS.has(chip.key));
  const advancedCount = ADVANCED_KEYS.filter((key) => value[key] != null).length;
  const activeOnly = value.activeOnly !== false;
  const showCatalogFacet = catalogOptions.length > 1 || Boolean(value.sourceIds?.length);

  const presetSelectOptions = useMemo(
    () => [{ label: "No workflow", value: NO_PRESET_FILTER }, ...presetOptions],
    [presetOptions]
  );

  return (
    <div className="filter-bar">
      <div className="filter-bar__controls">
        <div className="flex flex-wrap items-center gap-2">
          {leading ? <div className="min-w-[16rem] flex-1">{leading}</div> : null}
          <SearchField value={value.schemaSearch ?? ""} onChange={(schemaSearch) => patch({ schemaSearch })} />
          {showCatalogFacet ? (
            <FacetFilter
              label="Catalog"
              options={catalogOptions}
              value={value.sourceIds ?? []}
              onChange={(sourceIds) => patch({ sourceIds: sourceIds.length ? sourceIds : undefined })}
            />
          ) : null}
          <FacetFilter
            label="Workflow"
            ariaLabel="Filter by preset assignment"
            options={presetSelectOptions}
            value={value.presetFilter ?? []}
            onChange={(presetFilter) => patch({ presetFilter: presetFilter.length ? presetFilter : undefined })}
          />
          <FacetFilter
            label="Grade"
            ariaLabel="Filter by health grade"
            options={GRADE_OPTIONS}
            value={value.grades ?? []}
            onChange={(grades) => patch({ grades: grades.length ? grades : undefined })}
          />
          <FacetFilter
            label="Priority"
            ariaLabel="Filter by compaction priority"
            options={PRIORITY_OPTIONS}
            value={value.priorities ?? []}
            onChange={(priorities) => patch({ priorities: priorities.length ? priorities : undefined })}
          />
          {hideNeedsToggle ? null : (
            <Segmented
              size="small"
              value={typeof value.needsCompaction === "boolean" ? String(value.needsCompaction) : "all"}
              onChange={(next) => patch({ needsCompaction: next === "all" ? undefined : next === "true" })}
              options={[
                { label: "All", value: "all" },
                { label: "Needs compaction", value: "true" },
                { label: "Healthy", value: "false" }
              ]}
            />
          )}
          <Popover>
            <PopoverTrigger asChild>
              <Button size="sm" variant={advancedCount ? "outline" : "ghost"} className="gap-1.5">
                <SlidersHorizontal className="size-3.5" aria-hidden="true" />
                More filters
                {advancedCount ? (
                  <span className="rounded-full bg-[var(--color-primary-soft)] px-1.5 text-[11px] tabular-nums text-[var(--color-on-tint)]">
                    {advancedCount}
                  </span>
                ) : null}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-[22rem] p-3">
              <p className="mb-2.5 text-xs font-medium text-muted-foreground">Show tables with at least…</p>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
                <NumberField
                  id="filter-min-size"
                  label="Size"
                  unit="GB"
                  value={value.minSizeGb}
                  onChange={(minSizeGb) => patch({ minSizeGb })}
                />
                <NumberField
                  id="filter-min-small-files"
                  label="Small files"
                  unit="files"
                  value={value.minSmallFiles}
                  onChange={(minSmallFiles) => patch({ minSmallFiles })}
                />
                <NumberField
                  id="filter-min-savings"
                  label="Savings"
                  unit="$/mo"
                  value={value.minMonthlySavings}
                  onChange={(minMonthlySavings) => patch({ minMonthlySavings })}
                />
                <NumberField
                  id="filter-min-reclaim"
                  label="Reclaimable"
                  unit="GB"
                  value={value.minReclaimableGb}
                  onChange={(minReclaimableGb) => patch({ minReclaimableGb })}
                />
                <div className="col-span-2 flex flex-col gap-1">
                  <Label htmlFor="filter-cost-precision" className="text-xs text-muted-foreground">
                    Savings basis
                  </Label>
                  <Select
                    id="filter-cost-precision"
                    allowClear
                    placeholder="Any basis"
                    value={value.costPrecision ?? undefined}
                    onChange={(costPrecision) =>
                      patch({ costPrecision: (costPrecision as TableFilterState["costPrecision"]) ?? null })
                    }
                    options={[
                      { label: "Estimated", value: "heuristic" },
                      { label: "Metadata precise", value: "metadata" },
                      { label: "Storage verified", value: "storage_truth" }
                    ]}
                  />
                </div>
              </div>
              {advancedCount ? (
                <div className="mt-3 flex justify-end border-t border-border pt-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      patch(Object.fromEntries(ADVANCED_KEYS.map((key) => [key, undefined])) as Partial<TableFilterState>)
                    }
                  >
                    Clear these
                  </Button>
                </div>
              ) : null}
            </PopoverContent>
          </Popover>
          <button
            type="button"
            aria-pressed={activeOnly}
            title={activeOnly ? "Hiding tables dropped from the catalog" : "Including tables dropped from the catalog"}
            onClick={() => patch({ activeOnly: !activeOnly })}
            className="inline-flex h-[var(--control-height-sm)] cursor-pointer items-center gap-1.5 rounded-[var(--radius-default)] border border-border px-2.5 text-xs text-muted-foreground hover:text-foreground aria-pressed:border-transparent aria-pressed:bg-[var(--color-surface-sunken)] aria-pressed:text-foreground"
          >
            <span
              className={`size-1.5 rounded-full ${activeOnly ? "bg-[var(--color-status-success)]" : "bg-muted-foreground/40"}`}
              aria-hidden="true"
            />
            {activeOnly ? "Active only" : "Including dropped"}
          </button>
          {trailing}
          {filterActive || !activeOnly ? (
            <Button size="sm" variant="ghost" onClick={() => (onReset ? onReset() : onChange({ activeOnly: true }))}>
              Clear all
            </Button>
          ) : null}
          {showBulk || bulkActions ? (
            <div className="flex items-center gap-1.5">
              {filterSelectionActive ? <Badge variant="default">Filter selection</Badge> : null}
              {showSelectAll ? (
                <Button
                  size="sm"
                  title="Select every table that matches the current filters, including tables on other pages."
                  disabled={matchingTotal === 0}
                  onClick={onSelectAllMatching}
                >
                  Select all matching tables
                </Button>
              ) : null}
              {showClear ? (
                <Button title="Clear all selected tables." size="sm" onClick={onClearSelection}>
                  Clear selection
                </Button>
              ) : null}
              {bulkActions}
            </div>
          ) : null}
          {actions ? <div className="ml-auto flex flex-wrap items-center gap-1.5">{actions}</div> : null}
        </div>
        {extraChips.length ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {extraChips.map((chip, idx) => (
              <Badge key={`${chip.key}-${idx}`} variant="secondary" className="gap-1 pr-1">
                {chip.label}
                <button
                  type="button"
                  className="inline-flex cursor-pointer rounded-full p-0.5 hover:bg-[var(--color-panel)]"
                  onClick={chip.onRemove}
                  aria-label={`Remove ${chip.label}`}
                >
                  <X className="size-3" aria-hidden="true" />
                </button>
              </Badge>
            ))}
          </div>
        ) : null}
      </div>
      {hideSummary ? null : (
        <div className="filter-bar__footer">
          <div className="filter-bar__summary">
            <p className="filter-bar__scope text-sm text-muted-foreground">{scopeLabel}</p>
          </div>
          {showBulkSelect ? <p className="filter-bar__match-count text-sm text-muted-foreground">{matchLabel}</p> : null}
        </div>
      )}
    </div>
  );
};
