import { Button, Checkbox, Input, InputNumber, Popover, Segmented, Select, Space } from "antd";
import { useMemo } from "react";
import { predicateSummary, type TableFilterState } from "../lib/predicate";

interface Props {
  value: TableFilterState;
  onChange: (value: TableFilterState) => void;
}

export const FilterBar = ({ value, onChange }: Props) => {
  const summary = useMemo(() => predicateSummary(value), [value]);

  const patch = (next: Partial<TableFilterState>) => onChange({ ...value, ...next });

  return (
    <div className="filter-bar">
      <Space size={8} wrap>
        <Input.Search
          allowClear
          placeholder="Schema or table"
          value={value.schemaSearch}
          onChange={(event) => patch({ schemaSearch: event.target.value || undefined })}
          style={{ width: 220 }}
        />
        <Select
          mode="multiple"
          allowClear
          maxTagCount="responsive"
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
      <span className="filter-summary">{summary}</span>
    </div>
  );
};
