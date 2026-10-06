import type { CostByOperation } from "../api/types";
import { EmptyState } from "./common";
import { formatCurrency } from "../lib/format";
import { operationColors, operationLabels } from "../lib/theme";

export const SavingsByOperation = ({ items }: { items: CostByOperation[] }) => {
  const active = items
    .filter((item) => Number.isFinite(item.savings_usd_month) && item.savings_usd_month > 0)
    .sort((left, right) => right.savings_usd_month - left.savings_usd_month);

  if (!active.length) {
    return (
      <EmptyState
        title="No per-operation split"
        description="Operation-level savings appear after the cost dashboard aggregates table_cost rows."
      />
    );
  }

  return (
    <div className="savings-by-operation">
      <ol className="operation-list">
        {active.map((item, index) => (
          <li key={item.operation ?? `operation-${index}`} className="operation-list-row">
            <span className="operation-swatch" style={{ background: operationColors[item.operation] }} />
            <span>{operationLabels[item.operation] ?? item.operation}</span>
            <span className="text-muted-foreground">{formatCurrency(item.savings_usd_month)}/mo</span>
          </li>
        ))}
      </ol>
    </div>
  );
};
