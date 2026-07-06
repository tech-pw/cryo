import { Button, Checkbox, Input, InputNumber, Popover, Segmented, Select, Space, Tag, Tooltip, Typography } from "antd";
import { useMemo } from "react";
import { buildFilterChips, filterScopeLabel, type CatalogFilterOption } from "../lib/filterChips";
import type { TableFilterState } from "../lib/predicate";

export type { CatalogFilterOption };

interface Props {
  value: TableFilterState;
  onChange: (value: TableFilterState) => void;
  catalogOptions?: CatalogFilterOption[];
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
  matchingTotal = 0,
  hasSelection = false,
  filterSelectionActive = false,
  onSelectAllMatching,
  onClearSelection
}: Props) => {
  const patch = (next: Partial<TableFilterState>) => onChange({ ...value, ...next });
  const showBulkSelect = onSelectAllMatching !== undefined;

  const chips = useMemo(
    () => buildFilterChips(value, catalogOptions, patch),
    [value, catalogOptions]
  );
  const scopeLabel = useMemo(() => filterScopeLabel(value, chips), [value, chips]);
  const matchLabel =
    matchingTotal === 1 ? "1 table matches" : `${matchingTotal.toLocaleString()} tables match`;

  return (
    <div className="filter-bar">
      <div className="filter-bar__controls">
        <Space size={8} wrap>
          {showBulkSelect ? (
            <Space size={6}>
              {filterSelectionActive ? <Tag color="processing">Filter selection</Tag> : null}
              {!filterSelectionActive ? (
                <Tooltip title="Select every table that matches the current filters, including tables on other pages.">
                  <Button disabled={matchingTotal === 0} onClick={onSelectAllMatching}>
                    Select all matching tables
                  </Button>
                </Tooltip>
              ) : null}
              {hasSelection ? (
                <Tooltip title="Clear all selected tables.">
                  <Button size="small" onClick={onClearSelection}>
                    Clear selection
                  </Button>
                </Tooltip>
              ) : null}
            </Space>
          ) : null}
          <Input.Search
            allowClear
            placeholder="Schema or table"
            value={value.schemaSearch}
            onChange={(event) => patch({ schemaSearch: event.target.value || undefined })}
            style={{ width: 220 }}
          />
          {catalogOptions.length > 0 ? (
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              aria-label="Filter by catalog"
              placeholder="Catalog"
              value={value.sourceIds}
              onChange={(sourceIds) => patch({ sourceIds })}
              options={catalogOptions}
              showSearch
              filterOption={(input, option) =>
                (option?.label ?? "").toString().toLowerCase().includes(input.toLowerCase())
              }
              style={{ minWidth: 180 }}
            />
          ) : null}
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            aria-label="Filter by health grade"
            placeholder="Grade"
            value={value.grades}
            onChange={(grades) => patch({ grades })}
            options={["A", "B", "C", "D", "F"].map((grade) => ({ label: grade, value: grade }))}
            style={{ minWidth: 140 }}
          />
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            aria-label="Filter by compaction priority"
            placeholder="Priority"
            value={value.priorities}
            onChange={(priorities) => patch({ priorities })}
            options={["HIGH", "MEDIUM", "LOW"].map((priority) => ({ label: priority, value: priority }))}
            style={{ minWidth: 160 }}
          />
          <Segmented
            value={typeof value.needsCompaction === "boolean" ? String(value.needsCompaction) : "all"}
            onChange={(next) =>
              patch({ needsCompaction: next === "all" ? undefined : next === "true" })
            }
            options={[
              { label: "All", value: "all" },
              { label: "Needs compaction", value: "true" },
              { label: "Healthy", value: "false" }
            ]}
          />
          <Popover
            trigger="click"
            placement="bottomLeft"
            content={
              <Space direction="vertical" size={10}>
                <InputNumber
                  min={0}
                  addonBefore="Min GB"
                  value={value.minSizeGb}
                  onChange={(minSizeGb) => patch({ minSizeGb })}
                />
                <InputNumber
                  min={0}
                  addonBefore="Min small files"
                  value={value.minSmallFiles}
                  onChange={(minSmallFiles) => patch({ minSmallFiles })}
                />
                <InputNumber
                  min={0}
                  addonBefore="Min savings $/mo"
                  value={value.minMonthlySavings}
                  onChange={(minMonthlySavings) => patch({ minMonthlySavings })}
                />
                <InputNumber
                  min={0}
                  addonBefore="Min reclaim GB"
                  value={value.minReclaimableGb}
                  onChange={(minReclaimableGb) => patch({ minReclaimableGb })}
                />
                <Select
                  allowClear
                  placeholder="Cost precision"
                  value={value.costPrecision ?? undefined}
                  onChange={(costPrecision) => patch({ costPrecision: costPrecision ?? null })}
                  options={[
                    { label: "Estimated", value: "heuristic" },
                    { label: "Metadata precise", value: "metadata" },
                    { label: "Storage verified", value: "storage_truth" }
                  ]}
                  style={{ width: "100%" }}
                />
                <Checkbox
                  checked={value.activeOnly !== false}
                  onChange={(event) => patch({ activeOnly: event.target.checked })}
                >
                  Active tables only
                </Checkbox>
              </Space>
            }
          >
            <Button>Advanced</Button>
          </Popover>
          <Button onClick={() => onChange({ activeOnly: true })}>Reset</Button>
        </Space>
      </div>
      <div className="filter-bar__footer">
        <div className="filter-bar__summary">
          <Typography.Text type="secondary" className="filter-bar__scope">
            {scopeLabel}
          </Typography.Text>
          {chips.length ? (
            <Space size={[6, 6]} wrap className="filter-bar__chips">
              {chips.map((chip) => (
                <Tag key={chip.key} closable onClose={chip.onRemove}>
                  {chip.label}
                </Tag>
              ))}
            </Space>
          ) : null}
        </div>
        {showBulkSelect ? (
          <Typography.Text type="secondary" className="filter-bar__match-count">
            {matchLabel}
          </Typography.Text>
        ) : null}
      </div>
    </div>
  );
};
