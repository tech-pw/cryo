import type { PartitionAccessPoint, PartitionAccessSummary } from "../../api/client";

interface PartitionAccessChartProps {
  points: Array<Omit<PartitionAccessPoint, "request_count"> & { request_count: number | null }>;
  partitions: Array<
    Omit<PartitionAccessSummary, "total_requests" | "days_since_last_access"> & {
      total_requests: number | null;
      days_since_last_access: number | null;
    }
  >;
}

const TOP_PARTITIONS = 12;
const COLD_AFTER_DAYS = 30;

const measuredCount = (value: number | null | undefined): number | null =>
  value != null && Number.isFinite(value) && value >= 0 ? value : null;

const countText = (value: number | null | undefined): string => {
  const measured = measuredCount(value);
  return measured == null ? "Not measured" : measured.toLocaleString();
};

const lastAccessText = (days: number | null | undefined): string => {
  const measured = measuredCount(days);
  if (measured == null) return "Not measured";
  return measured === 0 ? "Today" : `${measured}d ago`;
};

/** S3 access logs double-encode keys ("ingest_date%253D2026-09-16"); show what the user wrote. */
export const decodePartitionPath = (path: string | null | undefined): string => {
  if (!path) return "(unpartitioned)";
  let current = path;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const next = decodeURIComponent(current);
      if (next === current) break;
      current = next;
    } catch {
      break;
    }
  }
  return current.replace(/\/$/, "") || "(unpartitioned)";
};

const dayLabel = (day: string) => {
  const parsed = Date.parse(day);
  return Number.isNaN(parsed) ? day : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(parsed);
};

export const PartitionAccessChart = ({ points, partitions }: PartitionAccessChartProps) => {
  if (!points.length && !partitions.length) {
    return <span className="text-sm text-muted-foreground">No partition access data yet.</span>;
  }

  const byDay = new Map<string, number>();
  for (const point of points) {
    const requests = measuredCount(point.request_count);
    if (requests == null || !point.day) continue;
    byDay.set(point.day, (byDay.get(point.day) ?? 0) + requests);
  }
  const days = [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b));
  const dayPeak = Math.max(1, ...days.map(([, value]) => value));

  const measured = partitions.filter((row) => measuredCount(row.total_requests) != null);
  const ranked = [...measured].sort((a, b) => (b.total_requests ?? 0) - (a.total_requests ?? 0));
  const top = ranked.slice(0, TOP_PARTITIONS);
  const total = ranked.reduce((sum, row) => sum + (row.total_requests ?? 0), 0);
  const topTen = ranked.slice(0, 10).reduce((sum, row) => sum + (row.total_requests ?? 0), 0);
  const peak = Math.max(1, top[0]?.total_requests ?? 0);
  const cold = partitions.filter((row) => (measuredCount(row.days_since_last_access) ?? -1) >= COLD_AFTER_DAYS).length;

  return (
    <div className="td-pa">
      {ranked.length ? (
        <p className="td-pa-headline">
          <strong>{ranked.length.toLocaleString()}</strong> partition{ranked.length === 1 ? "" : "s"} read
          {total > 0 && ranked.length > 10 ? (
            <>
              {" "}· top 10 take <strong>{Math.round((topTen / total) * 100)}%</strong> of requests
            </>
          ) : null}
          {cold > 0 ? (
            <>
              {" "}· <strong>{cold.toLocaleString()}</strong> not read in {COLD_AFTER_DAYS}+ days
            </>
          ) : null}
        </p>
      ) : null}

      {days.length > 1 ? (
        <figure className="td-pa-days">
          <figcaption>Requests per day</figcaption>
          <div
            className="td-pa-day-bars"
            role="img"
            aria-label={`Requests per day from ${dayLabel(days[0][0])} to ${dayLabel(days[days.length - 1][0])}, peaking at ${dayPeak.toLocaleString()}.`}
          >
            {days.map(([day, value]) => (
              <span key={day} title={`${dayLabel(day)}: ${value.toLocaleString()} requests`} style={{ height: `${Math.max(2, (value / dayPeak) * 100)}%` }} />
            ))}
          </div>
          <div className="td-pa-day-axis" aria-hidden="true">
            <span>{dayLabel(days[0][0])}</span>
            <span>{dayLabel(days[days.length - 1][0])}</span>
          </div>
        </figure>
      ) : null}

      {top.length ? (
        <figure className="td-pa-top">
          <figcaption>Most-read partitions</figcaption>
          <ol>
            {top.map((row) => {
              const label = decodePartitionPath(row.partition_path);
              return (
                <li key={row.partition_path || "(unpartitioned)"}>
                  <span className="td-pa-name" title={label}>
                    {label}
                  </span>
                  <span className="td-pa-bar" aria-hidden="true">
                    <span style={{ width: `${((row.total_requests ?? 0) / peak) * 100}%` }} />
                  </span>
                  <span className="td-pa-value">{countText(row.total_requests)}</span>
                  <span className="td-pa-last">{lastAccessText(row.days_since_last_access)}</span>
                </li>
              );
            })}
          </ol>
        </figure>
      ) : (
        <p className="text-sm text-muted-foreground">Request counts not measured.</p>
      )}

      <details className="td-pa-details">
        <summary>All {partitions.length.toLocaleString()} partitions as a table</summary>
        <div className="td-pa-table">
          <table className="w-full text-sm">
            <caption className="sr-only">Storage requests per partition</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="py-1.5 pr-3 font-medium">Partition</th>
                <th className="py-1.5 pr-3 text-right font-medium">Requests</th>
                <th className="py-1.5 text-right font-medium">Last accessed</th>
              </tr>
            </thead>
            <tbody>
              {[...partitions]
                .sort((a, b) => (b.total_requests ?? -1) - (a.total_requests ?? -1))
                .map((row) => (
                  <tr key={row.partition_path || "(unpartitioned)"} className="border-b border-border/60">
                    <td className="py-1.5 pr-3">{decodePartitionPath(row.partition_path)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{countText(row.total_requests)}</td>
                    <td className="py-1.5 text-right">{lastAccessText(row.days_since_last_access)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
};
