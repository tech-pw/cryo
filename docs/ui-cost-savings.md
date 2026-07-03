# Cost & Savings in the UI — Implementation Guide

How to visualize **storage gains** and **cost savings** in the web UI. Today the app shows only the
**present** cost (an `S3 monthly` stat and a `Cost by schema` bar on the Datalake Health page). This
guide adds the *"what will maintenance save?"* story: a **current → projected** cost waterfall, a
savings-by-operation breakdown, current-vs-projected per schema, a cost trend, and a "biggest
opportunities" list.

- **Backend design**: `lld.md` §16 + `docs/cost-savings.md` (byte accounting, per-op savings, tiers).
- **Frontend contract**: `lld.md` §12 (`/dashboards/cost`, `/tables/{id}/cost`) and §13 (screens).
- This doc is the **frontend build spec**; it reuses the app's existing patterns (AntD `Card`/`Row`/
  `Col`, Recharts, `cryoColors`, `chartTooltipProps`, `formatCurrency`, the `deriveDashboard`
  fallback) so it drops into the current codebase.

> **Data readiness gate.** These panels light up as the backend cost tiers land. Until
> `/dashboards/cost` exists, the UI derives what it can from `/tables` (existing pattern) and shows
> graceful empty states — never a blank or broken chart. See §8.

---

## 1. What we're adding (at a glance)

On **Datalake Health** (`web/src/pages/DatalakeHealth.tsx`, route `/health`):

1. **KPI strip** gains two stats: **Projected monthly cost** and **Monthly savings** (+ reclaimable
   storage), beside the existing present-cost stat.
2. **Hero chart — Cost-Savings Waterfall**: `Current → −Expire → −Orphans → −Compaction → −Manifests
   → Projected`, with a **$/mo ⇄ TB** toggle so the *same* chart tells both the **cost savings** and
   the **storage gains** story.
3. **Savings by operation** donut (share of savings per maintenance op).
4. **Cost by schema** upgraded to **current vs projected** (grouped bars) — replaces the single-series
   bar that's there now.
5. **Biggest opportunities** table (top tables by projected savings → select-all → Run).
6. **Cost trend** line (current cost, available savings, realized savings over scans).

On **Table Detail** (`web/src/pages/TableDetail.tsx`): a **Cost & savings** card (per-op breakdown +
mini waterfall + precision badge).

Optional: a dedicated **Cost & Savings** page (§10) if the health page gets crowded — the components
are identical, just composed on their own route.

---

## 2. Choosing the right chart

The core question — *"how much does each maintenance operation save?"* — is a **part-to-whole
reduction**. The canonical visualization is a **waterfall**: start at current cost, subtract a
labeled bar per operation, land on the projected cost. It reads left-to-right as a story and makes
the total savings obvious (the drop from first to last bar).

| Need | Chart | Why |
|------|-------|-----|
| Current → projected, attributed per op (**hero**) | **Waterfall** (Recharts stacked bar, transparent base) | Shows each op's contribution *and* the running total; the "gap" is the headline savings. |
| Share of savings per op | **Donut** | Quick "compaction vs expiry vs orphans" split; pairs with the waterfall. |
| Where the savings are | **Grouped bars per schema** (current vs projected) | Extends the existing `Cost by schema`; targets the fattest schemas. |
| Savings over time / realized | **Line** (current, available, realized) | Trust: projections vs what runs actually freed. |
| Act on it | **Table** sorted by `projected_savings_usd_month` | Select-all matching → Run (reuses `BulkAssignBar`). |

Recharts has **no native waterfall**, so we build it from a stacked `BarChart`: a transparent
`base` series positions each floating bar, and a colored `value` series is the visible delta. This is
the standard Recharts waterfall recipe and needs no new dependency (Recharts is already used across
`DatalakeHealth`, `TableDetail`, `Runs`).

Avoid: a single stacked bar with no baseline (hides the running total), or two disconnected
"current" and "projected" numbers (the reduction story is lost).

---

## 3. Data contract (types)

Follow the repo's current pattern: hand-write the shapes in `web/src/api/types.ts` and normalize the
raw backend payload in `web/src/api/client.ts` (exactly how `DatalakeDashboard` /
`RawDatalakeDashboard` work today). Once `/dashboards/cost` is in the OpenAPI schema, regenerate
`web/src/api/openapi.d.ts` and swap these hand-written types for the generated ones (per `lld.md`
§13).

### 3.1 Extend `TableStatsRead` (denormalized cost summary — `lld.md` §3.2)

```ts
export type CostPrecision = "heuristic" | "metadata" | "storage_truth";

export interface TableStatsRead {
  // ...existing fields...
  estimated_monthly_s3_cost_usd?: number;      // present cost (already exists)
  projected_monthly_s3_cost_usd?: number;      // after standard maintenance
  projected_savings_usd_month?: number;        // current - projected
  reclaimable_bytes?: number;                  // expire + orphan + optimize + manifests
  cost_precision?: CostPrecision;
}
```

### 3.2 New cost dashboard + per-table cost shapes

```ts
export type OperationType =
  | "optimize"
  | "expire_snapshots"
  | "remove_orphan_files"
  | "optimize_manifests";

export interface CostByOperation {
  operation: OperationType;
  savings_usd_month: number;
  reclaimable_bytes: number;
}

export interface CostDashboard {
  totals: {
    current_monthly_cost_usd: number;
    projected_monthly_cost_usd: number;
    projected_savings_usd_month: number;
    current_storage_bytes: number;
    reclaimable_bytes: number;
    precision_coverage?: Partial<Record<CostPrecision, number>>; // table counts by tier
    orphan_scan_stale?: boolean;                                 // last storage-truth scan is old
    last_full_cost_scan_at?: string | null;
  };
  savings_by_operation: CostByOperation[];
  cost_by_schema: Array<{
    schema_name: string;
    current_usd: number;
    projected_usd: number;
    savings_usd: number;
  }>;
  top_opportunities: TableRead[];               // sorted by projected_savings_usd_month desc
  trend?: Array<{
    scan_at: string;
    current_monthly_cost_usd?: number;
    projected_savings_usd_month?: number;
    realized_usd_month?: number;                // from actual_bytes_freed post-run
  }>;
}

// GET /tables/{id}/cost  -> per-table breakdown for the Table Detail card
export interface TableCostRead {
  precision: CostPrecision;
  price_per_gb_month: number;
  current_monthly_cost_usd: number;
  projected_monthly_cost_usd: number;
  projected_savings_usd_month: number;
  query_request_savings_usd_month: number;
  live_bytes: number;
  dead_bytes: number;
  metadata_bytes: number;
  orphan_bytes?: number | null;
  by_operation: CostByOperation[];
  last_full_cost_scan_at?: string | null;
}
```

Also extend `DatalakeDashboard.totals` so the health KPI strip can show savings even from the
existing endpoint before `/dashboards/cost` ships:

```ts
export interface DatalakeDashboard {
  totals?: {
    // ...existing...
    projected_monthly_cost_usd?: number;
    projected_savings_usd_month?: number;
    reclaimable_bytes?: number;
  };
  // ...existing...
}
```

---

## 4. API client wiring

Add to `web/src/api/client.ts`, mirroring `getDatalakeDashboard` / `normalizeDashboard`:

```ts
import type { CostDashboard, TableCostRead, UUID } from "./types";

export const getCostDashboard = (api: ApiOptions) =>
  apiFetch<CostDashboard>("/dashboards/cost", api);

export const getTableCost = (api: ApiOptions, id: UUID) =>
  apiFetch<TableCostRead>(`/tables/${id}/cost`, api);
```

**Fallback when `/dashboards/cost` is missing** (mirrors `deriveDashboard`): derive a partial
`CostDashboard` from `/tables` using the denormalized `stats.projected_*` fields. If those aren't
populated yet, the totals come out `0` and the panels render their empty state (§8).

```ts
export const deriveCostDashboard = (tables: TableRead[]): CostDashboard => {
  const bySchema = new Map<string, { current_usd: number; projected_usd: number; savings_usd: number }>();
  const ops = { optimize: 0, expire_snapshots: 0, remove_orphan_files: 0, optimize_manifests: 0 };
  let current = 0;
  let projected = 0;
  let savings = 0;
  let reclaimable = 0;
  let storage = 0;

  for (const t of tables) {
    const s = t.stats;
    if (!s) continue;
    const cur = s.estimated_monthly_s3_cost_usd ?? 0;
    const proj = s.projected_monthly_s3_cost_usd ?? cur;
    const sav = s.projected_savings_usd_month ?? Math.max(0, cur - proj);
    current += cur;
    projected += proj;
    savings += sav;
    reclaimable += s.reclaimable_bytes ?? 0;
    storage += (s.total_size_gb ?? 0) * 1024 ** 3;
    const row = bySchema.get(t.schema_name) ?? { current_usd: 0, projected_usd: 0, savings_usd: 0 };
    row.current_usd += cur;
    row.projected_usd += proj;
    row.savings_usd += sav;
    bySchema.set(t.schema_name, row);
  }

  return {
    totals: {
      current_monthly_cost_usd: current,
      projected_monthly_cost_usd: projected,
      projected_savings_usd_month: savings,
      current_storage_bytes: storage,
      reclaimable_bytes: reclaimable
    },
    // Per-op split is unavailable from /tables alone; the estimator fills this in via /dashboards/cost.
    savings_by_operation: Object.entries(ops).map(([operation, savings_usd_month]) => ({
      operation: operation as OperationType,
      savings_usd_month,
      reclaimable_bytes: 0
    })),
    cost_by_schema: Array.from(bySchema.entries())
      .map(([schema_name, v]) => ({ schema_name, ...v }))
      .sort((a, b) => b.savings_usd - a.savings_usd),
    top_opportunities: [...tables]
      .sort((a, b) => (b.stats?.projected_savings_usd_month ?? 0) - (a.stats?.projected_savings_usd_month ?? 0))
      .slice(0, 10)
  };
};
```

---

## 5. Formatters & theme additions

Add to `web/src/lib/format.ts` (reuse `formatCurrency`/`formatGb`; add byte + percent helpers):

```ts
export const bytesToGb = (bytes?: number | null): number =>
  bytes && Number.isFinite(bytes) ? bytes / 1024 ** 3 : 0;

export const formatBytesGb = (bytes?: number | null): string => formatGb(bytesToGb(bytes));

export const formatPercent = (value?: number | null, digits = 0): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${value.toFixed(digits)}%`;
};
```

Add to `web/src/lib/theme.ts` — one color per waterfall role + a per-operation palette for the donut:

```ts
export const cryoColors = {
  // ...existing...
  costCurrent: "#3d6fd4",   // present cost (matches primary)
  costProjected: "#0f9b8e", // teal: what remains after maintenance
  savings: "#2f9e6b"        // green: money/storage reclaimed
} as const;

export const operationColors: Record<string, string> = {
  optimize: "#3d6fd4",            // compaction
  expire_snapshots: "#2f9e6b",    // snapshot expiry
  remove_orphan_files: "#c9a227", // orphan removal
  optimize_manifests: "#8b5cf6"   // manifest rewrite
};

export const operationLabels: Record<string, string> = {
  optimize: "Compaction",
  expire_snapshots: "Snapshot expiry",
  remove_orphan_files: "Orphan removal",
  optimize_manifests: "Manifest rewrite"
};
```

---

## 6. The hero: Cost-Savings Waterfall

New component `web/src/components/CostSavingsWaterfall.tsx`. It renders the descending staircase and
toggles between **cost ($/mo)** and **storage (TB)** — same operation order, different measure — so a
single chart carries both requested stories.

```tsx
import { Segmented } from "antd";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CostByOperation } from "../api/types";
import { bytesToGb, formatCurrency, formatGb } from "../lib/format";
import { chartAxisTick, cryoColors, operationLabels } from "../lib/theme";

type Measure = "cost" | "storage";

interface WaterfallRow {
  name: string;
  base: number;   // transparent spacer that floats the visible bar
  value: number;  // visible delta (the reduction, or a total)
  kind: "total" | "save";
  amount: number; // raw amount for the tooltip
}

const OP_ORDER: CostByOperation["operation"][] = [
  "expire_snapshots",
  "remove_orphan_files",
  "optimize",
  "optimize_manifests"
];

const buildRows = (current: number, byOp: Map<string, number>): WaterfallRow[] => {
  const rows: WaterfallRow[] = [
    { name: "Current", base: 0, value: current, kind: "total", amount: current }
  ];
  let running = current;
  for (const op of OP_ORDER) {
    const amount = byOp.get(op) ?? 0;
    if (amount <= 0) continue;
    const after = Math.max(0, running - amount);
    rows.push({ name: operationLabels[op] ?? op, base: after, value: running - after, kind: "save", amount });
    running = after;
  }
  rows.push({ name: "Projected", base: 0, value: running, kind: "total", amount: running });
  return rows;
};

const colorFor = (row: WaterfallRow): string => {
  if (row.name === "Projected") return cryoColors.costProjected;
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
  const fmt = measure === "cost" ? formatCurrency : (v: number) => formatGb(v);

  const rows = useMemo(() => {
    if (measure === "cost") {
      const byOp = new Map(byOperation.map((o) => [o.operation, o.savings_usd_month]));
      return buildRows(currentCostUsd, byOp);
    }
    const byOp = new Map(byOperation.map((o) => [o.operation, bytesToGb(o.reclaimable_bytes)]));
    return buildRows(bytesToGb(currentStorageBytes), byOp);
  }, [measure, byOperation, currentCostUsd, currentStorageBytes]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
        <Segmented
          size="small"
          value={measure}
          onChange={(value) => setMeasure(value as Measure)}
          options={[
            { label: "Cost / mo", value: "cost" },
            { label: "Storage", value: "storage" }
          ]}
        />
      </div>
      <div className="chart-frame">
        <ResponsiveContainer>
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
            <XAxis dataKey="name" tickLine={false} axisLine={false} tick={chartAxisTick} interval={0} />
            <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} tickFormatter={(v) => fmt(Number(v))} />
            <Tooltip
              cursor={{ fill: cryoColors.panel }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const row = payload[0].payload as WaterfallRow;
                const label = row.kind === "save" ? `Saves ${fmt(row.amount)}` : fmt(row.amount);
                return (
                  <div className="recharts-default-tooltip" style={{ padding: "6px 10px" }}>
                    <div style={{ fontWeight: 600 }}>{row.name}</div>
                    <div>{label}</div>
                  </div>
                );
              }}
            />
            {/* transparent spacer floats each visible bar to its running-total position */}
            <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
            <Bar dataKey="value" stackId="w" radius={[4, 4, 0, 0]}>
              {rows.map((row) => (
                <Cell key={row.name} fill={colorFor(row)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
```

Notes:
- The `base` bar is `fill="transparent"` and excluded from the custom tooltip — only the visible
  `value` (a total or a per-op saving) shows.
- `OP_ORDER` matches the engine execution order in `lld.md` (expire → orphans → optimize →
  manifests) so the visual matches what actually runs. Zero-savings ops are dropped.
- Storage mode reuses `formatGb`, which already rolls up to **TB** above 1024 GB.

---

## 7. Savings-by-operation donut

New component `web/src/components/SavingsByOperation.tsx` — a compact companion to the waterfall.

```tsx
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { CostByOperation } from "../api/types";
import { EmptyState } from "./common";
import { formatCurrency } from "../lib/format";
import { chartTooltipProps, operationColors, operationLabels } from "../lib/theme";

export const SavingsByOperation = ({ items }: { items: CostByOperation[] }) => {
  const data = items.filter((item) => item.savings_usd_month > 0);
  if (!data.length) {
    return <EmptyState title="No savings yet" description="Projected savings appear after a cost scan." />;
  }
  return (
    <div className="chart-frame">
      <ResponsiveContainer>
        <PieChart>
          <Pie data={data} dataKey="savings_usd_month" nameKey="operation" innerRadius={46} outerRadius={82}>
            {data.map((entry) => (
              <Cell key={entry.operation} fill={operationColors[entry.operation] ?? "#8b95a8"} />
            ))}
          </Pie>
          <Tooltip
            {...chartTooltipProps}
            formatter={(value, name) => [formatCurrency(Number(value)), operationLabels[String(name)] ?? String(name)]}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
};
```

---

## 8. Wiring into the Datalake Health page

Edit `web/src/pages/DatalakeHealth.tsx`:

1. **Fetch cost** alongside the existing dashboard query, with the `/tables` fallback:

```tsx
const cost = useQuery({
  queryKey: ["cost-dashboard", token],
  queryFn: () => getCostDashboard(api),
  retry: 1
});
const costData =
  cost.data ?? deriveCostDashboard(tables.data?.items ?? []);
const hasSavings = (costData.totals.projected_savings_usd_month ?? 0) > 0;
```

2. **Extend the KPI strip** — add projected cost + savings next to the present-cost stat (which stays):

```tsx
<StatStrip
  items={[
    { label: "Tables", value: formatNumber(data.totals?.tables, true) },
    { label: "Fleet size", value: formatGb(data.totals?.size_gb) },
    { label: "S3 monthly (now)", value: formatCurrency(costData.totals.current_monthly_cost_usd) },
    { label: "Projected", value: formatCurrency(costData.totals.projected_monthly_cost_usd) },
    { label: "Monthly savings", value: formatCurrency(costData.totals.projected_savings_usd_month) }
  ]}
/>
```

3. **Add the hero waterfall + donut row** (place above or beside `Cost by schema`):

```tsx
<Col xs={24} lg={16}>
  <Card title="Cost savings after maintenance">
    {hasSavings ? (
      <CostSavingsWaterfall
        currentCostUsd={costData.totals.current_monthly_cost_usd}
        currentStorageBytes={costData.totals.current_storage_bytes}
        byOperation={costData.savings_by_operation}
      />
    ) : (
      <EmptyState
        title="No projected savings yet"
        description="Run a scan with cost accounting to project savings from compaction, expiry, and orphan removal."
      />
    )}
  </Card>
</Col>
<Col xs={24} lg={8}>
  <Card title="Savings by operation">
    <SavingsByOperation items={costData.savings_by_operation} />
  </Card>
</Col>
```

4. **Upgrade `Cost by schema`** to current-vs-projected grouped bars (edit the existing `BarChart`):

```tsx
<BarChart data={costData.cost_by_schema.slice(0, 12)}>
  <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
  <XAxis dataKey="schema_name" tickLine={false} axisLine={false} tick={chartAxisTick} />
  <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} tickFormatter={(v) => formatCurrency(Number(v))} />
  <Tooltip {...chartTooltipProps} formatter={(value) => formatCurrency(Number(value))} />
  <Legend />
  <Bar name="Current" dataKey="current_usd" fill={cryoColors.costCurrent} radius={[4, 4, 0, 0]} />
  <Bar name="Projected" dataKey="projected_usd" fill={cryoColors.costProjected} radius={[4, 4, 0, 0]} />
</BarChart>
```

5. **Precision badge + stale-orphan hint** near the KPI strip so users trust the numbers (from
   `costData.totals.precision_coverage` / `orphan_scan_stale`). A single AntD `Tag` is enough, e.g.
   `metadata-precise` (green) vs `heuristic` (default) vs `storage-truth` (blue); show a small
   `Tooltip` "orphan estimate is N days old — run a full cost scan" when stale.

---

## 9. Empty / loading / error / precision states

Match the page's current behavior (it already falls back to `/tables` and shows an `Alert`):

- **Loading** — reuse the existing `Skeleton active` gate.
- **Endpoint missing** — `deriveCostDashboard` from `/tables`; keep the existing info `Alert`
  ("Dashboard endpoint not available; showing a fallback view").
- **No savings data** (`hasSavings === false`) — `EmptyState` with a CTA to run a cost scan; do NOT
  render an all-zero waterfall.
- **Partial precision** — if `precision_coverage.heuristic` dominates, show the numbers but tag them
  "estimated"; when orphans haven't been storage-scanned, the orphan step is absent from the
  waterfall and the badge explains why (Tier-2 opt-in, `lld.md` §16).
- **Request-cost savings** (`query_request_savings_usd_month`, compaction only) — show as a secondary
  stat / tooltip line, clearly separated from storage savings (it's an estimate, not reclaimed bytes).

---

## 10. Table Detail card + optional dedicated page

**Table Detail** (`web/src/pages/TableDetail.tsx`): add a `Cost & savings` `Card` using
`getTableCost(api, tableId)`. Render a `Descriptions` of `current → projected → savings`, a small
`SavingsByOperation` donut (pass `by_operation`), the storage split (`live / dead / metadata /
orphan` via `formatBytesGb`), and the `precision` tag. This mirrors the existing `History` and
`Audit timeline` cards' placement.

**Dedicated page (optional)** — if Health gets busy, add `web/src/pages/CostSavings.tsx` composing
the same components at full width, and register it:

- Route in `web/src/App.tsx`: `<Route path="/cost" element={<CostSavings />} />`.
- Nav item in `navItems`: `{ key: "/cost", icon: <FundOutlined />, label: <Link to="/cost">Cost</Link> }`.
- Add a **top-opportunities** table there wired to `BulkAssignBar` (select-all matching
  `projected_savings_usd_month > X` → Run) — reuses the Explorer's selection-spec flow (`lld.md` §12).

---

## 11. Accessibility & responsiveness

- Color is not the only signal: the waterfall labels each bar (`Current`, op names, `Projected`) and
  the tooltip states "Saves $X"; the donut tooltip names the operation. Keep the `operationColors`
  distinct in hue *and* lightness.
- Give charts an `aria-label`/`role="img"` summary (e.g. "Projected savings $X/month, TB reclaimed
  Y") for screen readers, like the existing `Sparkline`.
- Use the established `.chart-frame` (260px) / `.chart-frame.compact` (220px) heights and AntD `Col`
  breakpoints (`xs`/`lg`/`xl`) so panels reflow on narrow screens exactly like the current cards.

---

## 12. Testing

Follow the existing Vitest + Testing Library setup (`web/src/test/setup.ts`, see
`web/src/components/BulkAssignBar.test.tsx` and `web/src/lib/predicate.test.ts`):

- **`buildRows` unit test** — pure waterfall math: current `$100`, savings `{expire 30, orphan 10,
  optimize 5}` ⇒ rows `Current 100`, three descending `save` bars, `Projected 55`; each `save` row's
  `base + value` equals the prior running total; totals never go negative.
- **`deriveCostDashboard`** — sums `stats.projected_*`; returns zeroed totals (→ empty state) when
  projected fields are absent; sorts `top_opportunities` by savings desc.
- **Formatters** — `bytesToGb`, `formatBytesGb` (GB→TB rollover), `formatPercent` edge cases.
- **Component render** — `SavingsByOperation` shows `EmptyState` for all-zero input; waterfall renders
  the right number of bars and hides the transparent `base` from the tooltip.
- **Health page** — with a mocked `getCostDashboard` the KPI strip shows current + projected +
  savings; with the endpoint erroring it falls back to `deriveCostDashboard` and still renders.

---

## 13. Build order / checklist

1. Types (`types.ts`) + client (`getCostDashboard`, `getTableCost`, `deriveCostDashboard`).
2. Formatters (`format.ts`) + theme colors (`theme.ts`).
3. `CostSavingsWaterfall.tsx` + `SavingsByOperation.tsx` (+ `buildRows` unit tests).
4. Integrate into `DatalakeHealth.tsx` (KPI strip, hero row, current-vs-projected schema bars, badge).
5. `Cost & savings` card on `TableDetail.tsx` (`getTableCost`).
6. (Optional) dedicated `/cost` page + nav + top-opportunities → `BulkAssignBar`.
7. Regenerate `openapi.d.ts` once the backend `/dashboards/cost` + `/tables/{id}/cost` are live; swap
   hand-written cost types for generated ones.
8. `cd web && npm run build && npm run test`.

**Backend dependencies** (see `docs/cost-savings.md` build order): `/dashboards/cost` and
`/tables/{id}/cost`, plus the `table_stats.projected_*` / `reclaimable_bytes` columns. The UI ships
behind graceful empty states until those land, so frontend and backend can proceed in parallel.
