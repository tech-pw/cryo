import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from "recharts";
import type { PartitionRead } from "../../api/types";
import { chartTooltipProps } from "../../lib/theme";
import { formatGb, formatNumber } from "../../lib/format";
import { ChartDataTable } from "../ui/chart-data-table";

interface PartitionHistogramProps {
  partitions: PartitionRead[];
}

const colorForSmallRate = (rate: number) => {
  if (rate >= 0.3) return "var(--gradeF, #b23a2e)";
  if (rate >= 0.15) return "var(--gradeC, #a9791e)";
  return "var(--primary, #3d6fd4)";
};

export const PartitionHistogram = ({ partitions }: PartitionHistogramProps) => {
  const top = [...partitions]
    .sort((a, b) => (b.partition_size_gb ?? 0) - (a.partition_size_gb ?? 0))
    .slice(0, 15)
    .map((item, idx) => {
      const files = Math.max(item.files_per_partition ?? 1, 1);
      const small = item.small_files_in_partition_count ?? 0;
      const rate = small / files;
      return {
        key: `${item.partition_value ?? "table"}-${idx}`,
        label: item.partition_value ?? "(table)",
        sizeGb: item.partition_size_gb ?? 0,
        smallFiles: small,
        smallRate: rate,
        fill: colorForSmallRate(rate)
      };
    });

  if (!top.length) {
    return <span className="text-sm text-muted-foreground">No Partition Histogram Yet.</span>;
  }

  return (
    <div className="td-partition-histogram">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={top}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
          <XAxis dataKey="label" hide />
          <YAxis tick={{ fontSize: 11 }} width={48} />
          <RechartsTooltip
            {...chartTooltipProps}
            formatter={(value, name, item) => {
              if (name === "sizeGb") return [formatGb(Number(value ?? 0)), "Size"];
              if (name === "smallFiles") return [formatNumber(Number(value ?? 0), true), "Small Files"];
              return [String(value ?? ""), (item as { payload?: { label?: string } })?.payload?.label ?? "Partition"];
            }}
            labelFormatter={(value) => String(value)}
          />
          <Bar dataKey="sizeGb" radius={[6, 6, 0, 0]}>
            {top.map((entry) => (
              <Cell key={entry.key} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <ChartDataTable
        caption="Top partitions by data size and small-file pressure"
        rows={top}
        getRowKey={(row) => row.key}
        columns={[
          { key: "partition", header: "Partition", render: (row) => row.label },
          { key: "size", header: "Size", align: "right", render: (row) => formatGb(row.sizeGb) },
          {
            key: "small-files",
            header: "Small files",
            align: "right",
            render: (row) => formatNumber(row.smallFiles, true)
          },
          {
            key: "small-rate",
            header: "Small-file rate",
            align: "right",
            render: (row) => `${Math.round(row.smallRate * 100)}%`
          }
        ]}
      />
    </div>
  );
};
