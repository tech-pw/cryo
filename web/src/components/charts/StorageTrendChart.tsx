import { CartesianGrid, Line, LineChart, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { formatShortDate } from "../../lib/format";
import { formatTb, type TrendDatum } from "../../lib/storageOverview";

/** Total storage, live data and cumulative freed bytes, all in TB on one axis. */
export const StorageTrendChart = ({ rows, hasLive }: { rows: TrendDatum[]; hasLive: boolean }) => (
  <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
    <CartesianGrid vertical={false} stroke="var(--color-chart-grid)" />
    <XAxis dataKey="day" tickFormatter={(d: string) => formatShortDate(d)} tick={{ fontSize: 11 }} stroke="var(--color-muted)" tickLine={false} minTickGap={24} />
    <YAxis tickFormatter={(v: number) => (v >= 1 ? `${Math.round(v)} TB` : formatTb(v))} tick={{ fontSize: 11 }} stroke="var(--color-muted)" tickLine={false} axisLine={false} width={64} />
    <ChartTooltip
      formatter={(value, name) => [formatTb(typeof value === "number" ? value : null), String(name)]}
      labelFormatter={(label) => formatShortDate(String(label))}
      contentStyle={{ background: "var(--color-surface-raised)", border: "1px solid var(--color-border-default)", borderRadius: 6, fontSize: 12 }}
    />
    <Line type="monotone" dataKey="total" name="Total storage" stroke="var(--storage-total)" strokeWidth={2} dot={false} isAnimationActive={false} />
    {hasLive ? <Line type="monotone" dataKey="live" name="Live data" stroke="var(--storage-live)" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} /> : null}
    <Line type="monotone" dataKey="freed" name="Freed by Cryo (cumulative)" stroke="var(--color-savings)" strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
  </LineChart>
);
