import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
  ZAxis
} from "recharts";
import type { PartitionAccessPoint, PartitionAccessSummary } from "../../api/client";
import { chartTooltipProps, cryoColors } from "../../lib/theme";
import { ChartDataTable } from "../ui/chart-data-table";

interface PartitionAccessChartProps {
  points: PartitionAccessPoint[];
  partitions: PartitionAccessSummary[];
}

/**
 * Partition-key-vs-access-time scatter chart -- docs/PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md,
 * inspired by Yelp's engineering blog post on the same idea. Visual patterns fall out of the
 * shape rather than any computation here: a steady diagonal is a daily batch job walking
 * forward through partitions in lockstep with wall-clock time, a vertical line is a
 * backfill/reprocess hitting many partitions at once, and scattered points are ad hoc queries.
 */
export const PartitionAccessChart = ({ points, partitions }: PartitionAccessChartProps) => {
  if (!points.length) {
    return <span className="text-sm text-muted-foreground">No partition access data yet.</span>;
  }

  // Y-axis needs a numeric position per partition for recharts' scatter -- ordered by most
  // recently accessed first (matches the summary table below) so the chart's vertical axis
  // reads top-to-bottom the same way the table does, rather than an arbitrary/alphabetical
  // order that would make the two views feel disconnected.
  const orderedPaths = partitions.map((p) => p.partition_path);
  const pathIndex = new Map(orderedPaths.map((path, index) => [path, index]));

  const data = points
    .filter((point) => pathIndex.has(point.partition_path))
    .map((point) => ({
      x: new Date(point.day).getTime(),
      y: pathIndex.get(point.partition_path) ?? 0,
      z: point.request_count,
      day: point.day,
      partitionPath: point.partition_path || "(unpartitioned)"
    }));

  return (
    <div
      className="td-partition-access-chart"
      role="img"
      aria-label={`Partition access for ${orderedPaths.length} partitions across ${data.length} request points.`}
    >
      <ResponsiveContainer width="100%" height={Math.max(220, orderedPaths.length * 18)}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
          <XAxis
            dataKey="x"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(ts) =>
              new Date(Number(ts)).toLocaleDateString(undefined, { month: "short", day: "numeric" })
            }
            tick={{ fontSize: 11 }}
            stroke="var(--muted, #5c667a)"
          />
          <YAxis
            dataKey="y"
            type="number"
            domain={[0, Math.max(orderedPaths.length - 1, 0)]}
            tickFormatter={(index) => orderedPaths[Number(index)] || ""}
            tick={{ fontSize: 10 }}
            width={110}
            stroke="var(--muted, #5c667a)"
          />
          {/* Bubble size communicates request volume without needing a color scale --
              consistent with this codebase not using color as the only channel for a value. */}
          <ZAxis dataKey="z" range={[20, 220]} name="Requests" />
          <RechartsTooltip
            {...chartTooltipProps}
            cursor={{ strokeDasharray: "3 3" }}
            formatter={(value, name, item) => {
              if (name === "z") {
                const payload = (item as { payload?: { partitionPath?: string } })?.payload;
                return [String(value ?? ""), `Requests · ${payload?.partitionPath ?? ""}`];
              }
              return [String(value ?? ""), String(name)];
            }}
            labelFormatter={() => ""}
          />
          <Scatter data={data} fill={cryoColors.chartPrimary} fillOpacity={0.75} />
        </ScatterChart>
      </ResponsiveContainer>
      <ChartDataTable
        caption="Partitions ranked by recency of access, with total requests over the window"
        rows={partitions}
        getRowKey={(row) => row.partition_path || "(unpartitioned)"}
        columns={[
          {
            key: "partition",
            header: "Partition",
            render: (row) => row.partition_path || "(unpartitioned)"
          },
          {
            key: "requests",
            header: "Requests",
            align: "right",
            render: (row) => row.total_requests.toLocaleString()
          },
          {
            key: "last-seen",
            header: "Last accessed",
            align: "right",
            render: (row) =>
              row.days_since_last_access === 0 ? "Today" : `${row.days_since_last_access}d ago`
          }
        ]}
      />
    </div>
  );
};
