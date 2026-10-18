import type { CostByOperation } from "../api/types";
import { EmptyState } from "./common";
import { formatCurrency } from "../lib/format";
import { cryoColors, operationLabels } from "../lib/theme";

export const SavingsByOperation = ({ items }: { items: CostByOperation[] }) => {
  const active = items
    .filter((item) => Number.isFinite(item.savings_usd_month) && item.savings_usd_month > 0)
    .sort((left, right) => right.savings_usd_month - left.savings_usd_month);
  const total = active.reduce((sum, item) => sum + item.savings_usd_month, 0);
  const max = active.reduce((peak, item) => Math.max(peak, item.savings_usd_month), 0);

  if (!active.length) {
    return (
      <EmptyState
        title="No per-operation split"
        description="Operation-level savings appear after the cost dashboard aggregates table_cost rows."
      />
    );
  }

  return (
    <div>
      <p className="text-[11px] font-medium leading-4 text-muted-foreground">Projected each month</p>
      <p className="mt-0.5 text-2xl font-semibold leading-8 tracking-[-0.025em] [font-variant-numeric:tabular-nums]">
        {formatCurrency(total)}
        <span className="ml-1 text-sm font-normal text-muted-foreground">/mo</span>
      </p>
      <ol className="mt-4 flex flex-col gap-3">
        {active.map((item, index) => {
          const share = max > 0 ? (item.savings_usd_month / max) * 100 : 0;
          return (
            <li key={item.operation ?? `operation-${index}`}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm">{operationLabels[item.operation] ?? item.operation}</span>
                <span className="text-sm font-medium [font-variant-numeric:tabular-nums]">
                  {formatCurrency(item.savings_usd_month)}
                  <span className="font-normal text-muted-foreground">/mo</span>
                </span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--color-surface-sunken)]">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${share}%`, background: cryoColors.savings }}
                />
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
};
