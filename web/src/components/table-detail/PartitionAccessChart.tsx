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

interface PartitionAccessChartProps {
  points: Array<Omit<PartitionAccessPoint, "request_count"> & { request_count: number | null }>;
  partitions: Array<
    Omit<PartitionAccessSummary, "total_requests" | "days_since_last_access"> & {
      total_requests: number | null;
      days_since_last_access: number | null;
    }
  >;
}

const measuredCount = (value: number | null | undefined): number | null =>
  value != null && Number.isFinite(value) ? value : null;

const countText = (value: number | null | undefined): string =>
  measuredCount(value) == null ? "Not measured" : measuredCount(value)!.toLocaleString();

const lastAccessText = (days: number | null | undefined): string => {
  const measured = measuredCount(days);
  if (measured == null) return "Not measured";
  return measured === 0 ? "Today" : `${measured}d ago`;
};

const POINT_CAP = 180;

const byPath = (left: string, right: string) => left.localeCompare(right);

export const PartitionAccessChart = ({ points, partitions }: PartitionAccessChartProps) => {
  if (!points.length && !partitions.length) {
    return <span className="text-sm text-muted-foreground">No partition access data yet.</span>;
  }

  const orderedPartitions = [...partitions].sort((a, b) => byPath(a.partition_path || "", b.partition_path || ""));
  const orderedPaths = orderedPartitions.map((p) => p.partition_path);
  const pathIndex = new Map(orderedPaths.map((path, index) => [path, index]));
  const shownPoints = points.slice(0, POINT_CAP);

  const plotted = shownPoints.flatMap((point) => {
    const index = pathIndex.get(point.partition_path);
    const requests = measuredCount(point.request_count);
    if (index == null || requests == null) return [];
    return [
      {
        x: new Date(point.day).getTime(),
        y: index,
        z: requests,
        day: point.day,
        partitionPath: point.partition_path || "(unpartitioned)"
      }
    ];
  });

  return (
    <div className="td-partition-access-chart">
      <p className="mb-2 text-xs text-muted-foreground">
        Y is not time. Partitions stay in a stable order. Day is on the horizontal axis. Marker size is request count.
      </p>
      {points.length ? (
        <p className="mb-2 text-xs text-muted-foreground">
          Showing {Math.min(points.length, POINT_CAP)} of {points.length} request points.
        </p>
      ) : null}
      {plotted.length ? (
        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground" aria-label="Request intensity">
          <span className="inline-block size-2 rounded-full" style={{ background: cryoColors.chartPrimary, opacity: 0.35 }} />
          <span>Fewer requests</span>
          <span className="inline-block size-3 rounded-full" style={{ background: cryoColors.chartPrimary }} />
          <span>More requests</span>
        </div>
      ) : null}
      {plotted.length ? (
        <div
          role="img"
          aria-label={`Partition access for ${orderedPaths.length} partitions across ${plotted.length} request points. Y is not time.`}
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
              <ZAxis dataKey="z" range={[20, 220]} name="Requests" />
              <RechartsTooltip
                {...chartTooltipProps}
                cursor={{ strokeDasharray: "3 3" }}
                formatter={(value, name, item) => {
                  if (name === "z") {
                    const payload = (item as { payload?: { partitionPath?: string } })?.payload;
                    const requests = measuredCount(typeof value === "number" ? value : null);
                    return [
                      requests == null ? "Not measured" : String(requests),
                      `Requests · ${payload?.partitionPath ?? ""}`
                    ];
                  }
                  return [value == null ? "Not measured" : String(value), String(name)];
                }}
                labelFormatter={() => ""}
              />
              <Scatter data={plotted} fill={cryoColors.chartPrimary} fillOpacity={0.75} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Not measured</p>
      )}
      <table className="mt-3 w-full text-sm">
        <caption className="sr-only">Plotted partition access points. Y is not time.</caption>
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="py-1.5 pr-3 font-medium">Day</th>
            <th className="py-1.5 pr-3 font-medium">Partition</th>
            <th className="py-1.5 text-right font-medium">Requests</th>
          </tr>
        </thead>
        <tbody>
          {shownPoints.map((point, index) => (
            <tr key={`${point.day}-${point.partition_path}-${index}`} className="border-b border-border/60">
              <td className="py-1.5 pr-3">{point.day || "Not measured"}</td>
              <td className="py-1.5 pr-3">{point.partition_path || "(unpartitioned)"}</td>
              <td className="py-1.5 text-right tabular-nums">{countText(point.request_count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {partitions.length ? (
        <table className="mt-3 w-full text-sm">
          <caption className="mb-1 text-left text-xs text-muted-foreground">
            Partitions in stable order
          </caption>
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th className="py-1.5 pr-3 font-medium">Partition</th>
              <th className="py-1.5 pr-3 text-right font-medium">Requests</th>
              <th className="py-1.5 text-right font-medium">Last accessed</th>
            </tr>
          </thead>
          <tbody>
            {orderedPartitions.map((row) => (
              <tr key={row.partition_path || "(unpartitioned)"} className="border-b border-border/60">
                <td className="py-1.5 pr-3">{row.partition_path || "(unpartitioned)"}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{countText(row.total_requests)}</td>
                <td className="py-1.5 text-right">{lastAccessText(row.days_since_last_access)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
};
