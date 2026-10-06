import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Tooltip, XAxis, YAxis } from "recharts";
import type { CostByOperation, OperationType } from "../api/types";
import { ChartFrame } from "./common";
import { bytesToGb, formatCurrency, formatGb } from "../lib/format";
import { chartAxisTick, cryoColors, operationLabels } from "../lib/theme";
import { Segmented } from "./ui/segmented";

type Measure = "cost" | "storage";

export interface WaterfallRow {
  name: string;
  base: number;
  value: number;
  kind: "total" | "save";
  amount: number;
}

const OP_ORDER: OperationType[] = [
  "expire_snapshots",
  "remove_orphan_files",
  "optimize",
  "optimize_manifests"
];

export const buildWaterfallRows = (current: number, byOp: Map<string, number>): WaterfallRow[] => {
  const rows: WaterfallRow[] = [{ name: "Now", base: 0, value: current, kind: "total", amount: current }];
  let running = current;
  for (const op of OP_ORDER) {
    const amount = byOp.get(op) ?? 0;
    if (amount <= 0) continue;
    const after = Math.max(0, running - amount);
    rows.push({
      name: operationLabels[op] ?? op,
      base: after,
      value: running - after,
      kind: "save",
      amount
    });
    running = after;
  }
  rows.push({ name: "After maintenance", base: 0, value: running, kind: "total", amount: running });
  return rows;
};

const colorFor = (row: WaterfallRow): string => {
  if (row.name === "After maintenance") return cryoColors.costProjected;
  if (row.kind === "total") return cryoColors.costCurrent;
  return cryoColors.savings;
};

export const CostSavingsWaterfall = ({
  currentCostUsd,
  currentStorageBytes,
  byOperation
}: {
  currentCostUsd: number;
  currentStorageBytes: number;
  byOperation: CostByOperation[];
}) => {
  const [measure, setMeasure] = useState<Measure>("cost");
  const fmt = measure === "cost" ? formatCurrency : (value: number) => formatGb(value);

  const rows = useMemo(() => {
    if (measure === "cost") {
      const byOp = new Map(byOperation.map((item) => [item.operation, item.savings_usd_month]));
      return buildWaterfallRows(currentCostUsd, byOp);
    }
    const byOp = new Map(
      byOperation
        .filter((item) => item.reclaimable_bytes != null)
        .map((item) => [item.operation, bytesToGb(item.reclaimable_bytes)])
    );
    return buildWaterfallRows(bytesToGb(currentStorageBytes), byOp);
  }, [measure, byOperation, currentCostUsd, currentStorageBytes]);

  const hasSavings = byOperation.some(
    (item) => item.savings_usd_month > 0 || (item.reclaimable_bytes ?? 0) > 0
  );

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <Segmented
          value={measure}
          onChange={setMeasure}
          options={[
            { label: "Cost / mo", value: "cost" },
            { label: "Storage", value: "storage" }
          ]}
        />
      </div>
      <ChartFrame
        empty={!hasSavings && currentCostUsd <= 0}
        emptyTitle="No savings projection yet"
        emptyDescription="Run a scanner pass with cost accounting to populate savings."
        ariaLabel={`Projected savings ${formatCurrency(
          byOperation.reduce((sum, item) => sum + item.savings_usd_month, 0)
        )} per month from current ${formatCurrency(currentCostUsd)}`}
      >
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: 8 }}>
          <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
          <XAxis dataKey="name" tickLine={false} axisLine={false} tick={chartAxisTick} interval={0} />
          <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} tickFormatter={(value) => fmt(Number(value))} />
          <Tooltip
            cursor={{ fill: cryoColors.panel }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as WaterfallRow;
              const label =
                row.kind === "save"
                  ? measure === "cost"
                    ? `Saves ${formatCurrency(row.amount)}/mo`
                    : `Reclaims ${formatGb(row.amount)}`
                  : fmt(row.amount);
              return (
                <div className="recharts-default-tooltip" style={{ padding: "6px 10px" }}>
                  <div style={{ fontWeight: 600 }}>{row.name}</div>
                  <div>{label}</div>
                </div>
              );
            }}
          />
          <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="value" stackId="w" radius={[4, 4, 0, 0]}>
            {rows.map((row, index) => (
              <Cell key={row.name || `row-${index}`} fill={colorFor(row)} />
            ))}
          </Bar>
        </BarChart>
      </ChartFrame>
    </div>
  );
};
