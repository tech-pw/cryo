import { Typography } from "antd";
import { Cell, Pie, PieChart, Tooltip } from "recharts";
import type { CostByOperation } from "../api/types";
import { ChartFrame, EmptyState } from "./common";
import { formatBytesGb, formatCurrency } from "../lib/format";
import { chartTooltipProps, operationColors, operationLabels } from "../lib/theme";

export const SavingsByOperation = ({ items }: { items: CostByOperation[] }) => {
  const active = items.filter((item) => item.savings_usd_month > 0);
  const total = active.reduce((sum, item) => sum + item.savings_usd_month, 0);

  if (!active.length || total <= 0) {
    return (
      <EmptyState
        title="No per-operation split"
        description="Operation-level savings appear after the cost dashboard aggregates table_cost rows."
      />
    );
  }

  // A donut only earns its keep as a part-to-whole comparison across several categories --
  // at 1-2 items it's just a redundant ring duplicating what the list below already says.
  const showChart = active.length >= 3;

  return (
    <div className="savings-by-operation">
      {showChart ? (
        <ChartFrame
          empty={false}
          size="xs"
          ariaLabel={`Savings by operation totaling ${formatCurrency(total)} per month`}
        >
          <PieChart>
            <Pie data={active} dataKey="savings_usd_month" nameKey="operation" innerRadius={36} outerRadius={64}>
              {active.map((item, index) => (
                <Cell
                  key={item.operation ?? `operation-${index}`}
                  fill={operationColors[item.operation] ?? "#8b95a8"}
                />
              ))}
            </Pie>
            <Tooltip
              {...chartTooltipProps}
              formatter={(value, _name, entry) => {
                const row = entry?.payload as CostByOperation | undefined;
                return [
                  `${formatCurrency(Number(value))}/mo · ${formatBytesGb(row?.reclaimable_bytes)}`,
                  operationLabels[row?.operation ?? ""] ?? row?.operation
                ];
              }}
            />
          </PieChart>
        </ChartFrame>
      ) : null}
      <div className="operation-list">
        {active.map((item, index) => (
          <div key={item.operation ?? `operation-${index}`} className="operation-list-row">
            <span className="operation-swatch" style={{ background: operationColors[item.operation] }} />
            <Typography.Text>{operationLabels[item.operation] ?? item.operation}</Typography.Text>
            <Typography.Text type="secondary">{formatCurrency(item.savings_usd_month)}/mo</Typography.Text>
          </div>
        ))}
      </div>
    </div>
  );
};
