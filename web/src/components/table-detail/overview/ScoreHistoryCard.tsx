import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartDataTable } from "../../ui/chart-data-table";
import { Skeleton } from "../../ui/skeleton";
import { QueryErrorAlert } from "../../common";
import { formatTime } from "../../../lib/format";
import { OverviewCard } from "./OverviewCard";
import type { TrendPoint } from "./gradeReasons";

export const ScoreHistoryCard = ({ points, answer, loading, error }: { points: TrendPoint[]; answer: string; loading: boolean; error: unknown }) => {
  const ordered = [...points].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const measured = ordered.filter((point) => point.value != null);
  const rows = ordered.map((point) => ({ ...point, time: Date.parse(point.at) }));
  const tick = (epoch: number) => new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(epoch);
  return (
    <OverviewCard id="overview-score" title="Score over time" description="Last 30 days · fixed 0–100 scale; higher is healthier." className="td-overview-trend">
      <QueryErrorAlert error={error} title="Couldn't load the score trend" />
      {loading ? <Skeleton className="h-48 w-full" /> : error ? null : <>
        <p className="td-trend-answer">{answer}</p>
        {measured.length > 1 ? <div className="td-score-chart" aria-hidden="true" inert><ResponsiveContainer width="100%" height="100%"><LineChart data={rows} margin={{ top: 10, right: 12, bottom: 2, left: -12 }} accessibilityLayer={false}>
          <CartesianGrid stroke="var(--color-chart-grid)" strokeDasharray="3 4" vertical={false} />
          <XAxis dataKey="time" type="number" domain={["dataMin", "dataMax"]} tickFormatter={tick} tick={{ fill: "var(--color-muted)", fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={36} />
          <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tick={{ fill: "var(--color-muted)", fontSize: 11 }} tickLine={false} axisLine={false} />
          <Tooltip labelFormatter={(value) => formatTime(new Date(Number(value)).toISOString())} contentStyle={{ background: "var(--primitive-surface)", border: "1px solid var(--color-border-default)", borderRadius: 8, fontSize: 12 }} />
          <Line name="Health score" type="linear" dataKey="value" stroke="var(--color-chart-primary)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--primitive-surface)", strokeWidth: 2 }} activeDot={false} connectNulls={false} isAnimationActive={false} />
        </LineChart></ResponsiveContainer></div> : <div className="td-trend-empty"><strong>{measured.length === 1 ? `${measured[0].value} / 100` : "No trend yet"}</strong><p>{measured.length === 1 ? `Recorded ${formatTime(measured[0].at)}. Another scan is needed to show a trend.` : "History appears as table scans record health scores."}</p></div>}
        {ordered.some((point) => point.value == null) ? <p className="td-overview-caption">Gaps indicate missing measurements.</p> : null}
        <ChartDataTable caption="Recorded table health scores" toggleLabel="View score history" rows={ordered} getRowKey={(row, index) => `${row.at}-${index}`} columns={[
          { key: "at", header: "Recorded", render: (row) => formatTime(row.at) },
          { key: "score", header: "Score / 100", align: "right", render: (row) => row.value ?? "Not measured" },
          { key: "grade", header: "Grade", render: (row) => row.grade ?? "Not graded" }
        ]} />
      </>}
    </OverviewCard>
  );
};
