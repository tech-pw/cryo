import { useMemo } from "react";
import { buildFilterChips, filterScopeLabel, type CatalogFilterOption } from "../lib/filterChips";
import { NO_PRESET_FILTER, type TableFilterState } from "../lib/predicate";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { InputNumber } from "./ui/input-number";
import { MultiSelect, Select } from "./ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Segmented } from "./ui/segmented";

export type { CatalogFilterOption };

interface Props {
  value: TableFilterState;
  onChange: (value: TableFilterState) => void;
  catalogOptions?: CatalogFilterOption[];
  presetOptions?: CatalogFilterOption[];
  matchingTotal?: number;
  hasSelection?: boolean;
  filterSelectionActive?: boolean;
  onSelectAllMatching?: () => void;
  onClearSelection?: () => void;
}

export const FilterBar = ({
  value,
  onChange,
  catalogOptions = [],
  presetOptions = [],
  matchingTotal = 0,
  hasSelection = false,
  filterSelectionActive = false,
  onSelectAllMatching,
  onClearSelection
}: Props) => {
  const patch = (next: Partial<TableFilterState>) => onChange({ ...value, ...next });
  const showBulkSelect = onSelectAllMatching !== undefined;

  const chips = useMemo(
    () => buildFilterChips(value, catalogOptions, patch, presetOptions),
    [value, catalogOptions, presetOptions]
  );
  const scopeLabel = useMemo(() => filterScopeLabel(value, chips), [value, chips]);
  const matchLabel =
    matchingTotal === 1 ? "1 table matches" : `${matchingTotal.toLocaleString()} tables match`;

  const presetSelectOptions = useMemo(
    () => [{ label: "No preset", value: NO_PRESET_FILTER }, ...presetOptions],
    [presetOptions]
  );

  return (
    <div className="filter-bar">
      <div className="filter-bar__controls">
        <div className="flex flex-wrap items-center gap-2">
          {showBulkSelect ? (
            <div className="flex items-center gap-1.5">
              {filterSelectionActive ? <Badge variant="default">Filter selection</Badge> : null}
              {!filterSelectionActive ? (
                <Button
                  title="Select every table that matches the current filters, including tables on other pages."
                  disabled={matchingTotal === 0}
                  onClick={onSelectAllMatching}
                >
                  Select all matching tables
                </Button>
              ) : null}
              {hasSelection ? (
                <Button title="Clear all selected tables." size="sm" onClick={onClearSelection}>
                  Clear selection
                </Button>
              ) : null}
            </div>
          ) : null}
          <Input
            aria-label="Schema or table search"
            placeholder="Schema or table"
            value={value.schemaSearch ?? ""}
            onChange={(event) => patch({ schemaSearch: event.target.value || undefined })}
            style={{ width: 220 }}
          />
          {catalogOptions.length > 0 ? (
            <MultiSelect
              aria-label="Filter by catalog"
              placeholder="Catalog"
              value={value.sourceIds ?? []}
              onChange={(sourceIds) => patch({ sourceIds })}
              options={catalogOptions}
              style={{ minWidth: 180 }}
            />
          ) : null}
          <MultiSelect
            aria-label="Filter by preset assignment"
            placeholder="Preset"
            value={value.presetFilter ?? []}
            onChange={(presetFilter) => patch({ presetFilter: presetFilter.length ? presetFilter : undefined })}
            options={presetSelectOptions}
            style={{ minWidth: 180 }}
          />
          <MultiSelect
            aria-label="Filter by health grade"
            placeholder="Grade"
            value={value.grades ?? []}
            onChange={(grades) => patch({ grades })}
            options={["A", "B", "C", "D", "F"].map((grade) => ({ label: grade, value: grade }))}
            style={{ minWidth: 140 }}
          />
          <MultiSelect
            aria-label="Filter by compaction priority"
            placeholder="Priority"
            value={value.priorities ?? []}
            onChange={(priorities) => patch({ priorities })}
            options={["HIGH", "MEDIUM", "LOW"].map((priority) => ({ label: priority, value: priority }))}
            style={{ minWidth: 160 }}
          />
          <Segmented
            value={typeof value.needsCompaction === "boolean" ? String(value.needsCompaction) : "all"}
            onChange={(next) => patch({ needsCompaction: next === "all" ? undefined : next === "true" })}
            options={[
              { label: "All", value: "all" },
              { label: "Needs compaction", value: "true" },
              { label: "Healthy", value: "false" }
            ]}
          />
          <Popover>
            <PopoverTrigger asChild>
              <Button>Advanced</Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-80 space-y-2.5">
              <InputNumber
                min={0}
                addonBefore="Min GB"
                value={value.minSizeGb}
                onChange={(minSizeGb) => patch({ minSizeGb: minSizeGb ?? undefined })}
              />
              <InputNumber
                min={0}
                addonBefore="Min small files"
                value={value.minSmallFiles}
                onChange={(minSmallFiles) => patch({ minSmallFiles: minSmallFiles ?? undefined })}
              />
              <InputNumber
                min={0}
                addonBefore="Min savings $/mo"
                value={value.minMonthlySavings}
                onChange={(minMonthlySavings) => patch({ minMonthlySavings: minMonthlySavings ?? undefined })}
              />
              <InputNumber
                min={0}
                addonBefore="Min reclaim GB"
                value={value.minReclaimableGb}
                onChange={(minReclaimableGb) => patch({ minReclaimableGb: minReclaimableGb ?? undefined })}
              />
              <Select
                allowClear
                placeholder="Cost precision"
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
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={value.activeOnly !== false}
                  onCheckedChange={(checked) => patch({ activeOnly: checked === true })}
                />
                Active tables only
              </label>
            </PopoverContent>
          </Popover>
          <Button onClick={() => onChange({ activeOnly: true })}>Reset</Button>
        </div>
      </div>
      <div className="filter-bar__footer">
        <div className="filter-bar__summary">
          <p className="filter-bar__scope text-sm text-muted-foreground">{scopeLabel}</p>
          {chips.length ? (
            <div className="filter-bar__chips flex flex-wrap gap-1.5">
              {chips.map((chip, idx) => (
                <Badge key={`${chip.key ?? "chip"}-${idx}`} variant="secondary" className="gap-1">
                  {chip.label}
                  <button type="button" className="ml-1 text-xs" onClick={chip.onRemove} aria-label={`Remove ${chip.label}`}>
                    ×
                  </button>
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
        {showBulkSelect ? (
          <p className="filter-bar__match-count text-sm text-muted-foreground">{matchLabel}</p>
        ) : null}
      </div>
    </div>
  );
};
