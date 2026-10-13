import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Tooltip, XAxis, YAxis } from "recharts";
import type { CostByOperation, OperationType } from "../api/types";
import { ChartFrame } from "./common";
import { bytesToGb, formatCurrency, formatGb } from "../lib/format";
import { chartAxisTick, cryoColors, operationLabels } from "../lib/theme";
import { Segmented } from "./ui/segmented";

type Measure = "cost" | "storage";

const MEASURE_KEY = "cryo.cost.waterfall-measure";

const readMeasure = (): Measure => {
  if (typeof sessionStorage === "undefined") return "cost";
  return sessionStorage.getItem(MEASURE_KEY) === "storage" ? "storage" : "cost";
};

export interface WaterfallRow {
  name: string;
  base: number;
  value: number;
  kind: "total" | "save";
  amount: number;
  label: string;
}

const OP_ORDER: OperationType[] = [
  "expire_snapshots",
  "remove_orphan_files",
  "optimize",
  "optimize_manifests"
];

export const buildWaterfallRows = (current: number, byOp: Map<string, number>): WaterfallRow[] => {
  const rows: WaterfallRow[] = [
    { name: "Now", base: 0, value: current, kind: "total", amount: current, label: "" }
  ];
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
      amount,
      label: ""
    });
    running = after;
  }
  rows.push({ name: "After maintenance", base: 0, value: running, kind: "total", amount: running, label: "" });
  return rows;
};

const colorFor = (row: WaterfallRow): string => {
  if (row.name === "After maintenance") return cryoColors.costProjected;
  if (row.kind === "total") return cryoColors.costCurrent;
  return cryoColors.savings;
};

const compactAmount = (value: number, measure: Measure): string => {
  const abs = Math.abs(value);
  if (measure === "storage") {
    if (abs >= 1024) return `${(value / 1024).toFixed(1)} TB`;
    if (abs >= 10) return `${Math.round(value)} GB`;
    return `${value.toFixed(1)} GB`;
  }
  if (abs >= 1000) {
    const thousands = value / 1000;
    return `${thousands >= 10 ? Math.round(thousands) : thousands.toFixed(1)}k`;
  }
  return `$${Math.round(value)}`;
};

const stepLabel = (row: WaterfallRow, measure: Measure): string =>
  row.kind === "save" ? `−${compactAmount(row.amount, measure)}` : compactAmount(row.amount, measure);

export const CostSavingsWaterfall = ({
  currentCostUsd,
  currentStorageBytes,
  byOperation
}: {
  currentCostUsd: number;
  currentStorageBytes: number;
  byOperation: CostByOperation[];
}) => {
  const [measure, setMeasure] = useState<Measure>(readMeasure);
  const chooseMeasure = (next: string) => {
    const value: Measure = next === "storage" ? "storage" : "cost";
    setMeasure(value);
    try {
      sessionStorage.setItem(MEASURE_KEY, value);
    } catch {
      // Private mode can reject storage; the toggle still works for this view.
    }
  };
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

  const labeledRows = rows.map((row) => ({ ...row, label: stepLabel(row, measure) }));
  const maxValue = labeledRows.reduce((max, row) => Math.max(max, row.base + row.value), 0);
  const yMax = maxValue > 0 ? maxValue * 1.16 : undefined;

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <Segmented
          size="small"
          aria-label="Waterfall measure"
          value={measure}
          onChange={chooseMeasure}
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
        <BarChart data={labeledRows} margin={{ top: 22, right: 8, bottom: 0, left: 4 }} barCategoryGap="28%">
          <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
          <XAxis dataKey="name" tickLine={false} axisLine={false} tick={chartAxisTick} interval={0} />
          <YAxis
            tickLine={false}
            axisLine={false}
            tick={chartAxisTick}
            width={52}
            domain={yMax == null ? undefined : [0, yMax]}
            tickFormatter={(value) => compactAmount(Number(value), measure)}
          />
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
          <Bar dataKey="value" stackId="w" radius={[4, 4, 0, 0]} minPointSize={8}>
            {labeledRows.map((row, index) => (
              <Cell key={row.name || `row-${index}`} fill={colorFor(row)} />
            ))}
            <LabelList
              dataKey="label"
              position="top"
              offset={6}
              style={{ fill: cryoColors.inkSoft, fontSize: 11, fontWeight: 600 }}
            />
          </Bar>
        </BarChart>
      </ChartFrame>
    </div>
  );
};
