import { Area, CartesianGrid, ComposedChart, Line, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { formatShortDate } from "../../lib/format";
import { formatTb, type TrendDatum } from "../../lib/storageOverview";

/** Total storage, live data and cumulative freed bytes, all in TB on one axis. */
export const StorageTrendChart = ({ rows, hasLive }: { rows: TrendDatum[]; hasLive: boolean }) => (
  <ComposedChart data={rows.map((r) => ({ ...r, gap: r.live != null && r.total != null ? [r.live, r.total] : null }))} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
    <defs>
      <linearGradient id="storage-total-fill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="var(--storage-total)" stopOpacity={0.28} />
        <stop offset="100%" stopColor="var(--storage-total)" stopOpacity={0} />
      </linearGradient>
      <pattern id="storage-gap-fill" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="6" height="6" fill="var(--storage-dead)" fillOpacity={0.12} />
        <line x1="0" y1="0" x2="0" y2="6" stroke="var(--storage-dead)" strokeOpacity={0.45} strokeWidth={2} />
      </pattern>
      <linearGradient id="storage-freed-fill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="var(--color-savings)" stopOpacity={0.3} />
        <stop offset="100%" stopColor="var(--color-savings)" stopOpacity={0} />
      </linearGradient>
    </defs>
    <CartesianGrid vertical={false} stroke="var(--color-chart-grid)" />
    <XAxis dataKey="day" tickFormatter={(d: string) => formatShortDate(d)} tick={{ fontSize: 11 }} stroke="var(--color-muted)" tickLine={false} minTickGap={24} />
    <YAxis tickFormatter={(v: number) => (v >= 1 ? `${Math.round(v)} TB` : formatTb(v))} tick={{ fontSize: 11 }} stroke="var(--color-muted)" tickLine={false} axisLine={false} width={64} />
    <ChartTooltip
      formatter={(value, name) => [Array.isArray(value) ? formatTb(Number(value[1]) - Number(value[0])) : formatTb(typeof value === "number" ? value : null), String(name)]}
      labelFormatter={(label) => formatShortDate(String(label))}
      contentStyle={{ background: "var(--color-surface-raised)", border: "1px solid var(--color-border-default)", borderRadius: 6, fontSize: 12 }}
    />
    <Area type="monotone" dataKey="total" name="Total storage" stroke="var(--storage-total)" strokeWidth={2} fill="url(#storage-total-fill)" dot={false} isAnimationActive={false} />
    {hasLive ? <Area type="monotone" dataKey="gap" name="Gap (history, dead, orphans)" stroke="none" fill="url(#storage-gap-fill)" connectNulls={false} isAnimationActive={false} activeDot={false} /> : null}
    {hasLive ? <Line type="monotone" dataKey="live" name="Live data" stroke="var(--storage-live)" strokeWidth={2.5} dot={false} connectNulls={false} isAnimationActive={false} /> : null}
    <Area type="monotone" dataKey="freed" name="Freed by Cryo (cumulative)" stroke="var(--color-savings)" strokeWidth={2} strokeDasharray="5 4" fill="url(#storage-freed-fill)" dot={false} isAnimationActive={false} />
  </ComposedChart>
);
