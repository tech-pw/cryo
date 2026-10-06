import type { PartitionAccessPoint, PartitionAccessSummary } from "../../api/client";
import { ChartDataTable } from "../ui/chart-data-table";

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
  return Number.isNaN(parsed) ? day : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" }).format(parsed);
};

export const partitionDays = (points: PartitionAccessChartProps["points"]) => {
  const byDay = new Map<string, number>();
  for (const point of points) {
    const requests = measuredCount(point.request_count);
    if (requests == null || !/^\d{4}-\d{2}-\d{2}$/.test(point.day) || !Number.isFinite(Date.parse(point.day))) continue;
    byDay.set(point.day, (byDay.get(point.day) ?? 0) + requests);
  }
  const recorded = [...byDay.keys()].sort();
  if (!recorded.length) return [];
  const first = Date.parse(recorded[0]);
  const last = Date.parse(recorded[recorded.length - 1]);
  const result: Array<{ day: string; requests: number | null }> = [];
  // This endpoint covers 90 days; bound rendering if a malformed timestamp slips through.
  for (let date = Math.max(first, last - 365 * 86_400_000); date <= last; date += 86_400_000) {
    const day = new Date(date).toISOString().slice(0, 10);
    result.push({ day, requests: byDay.get(day) ?? null });
  }
  return result;
};

export const PartitionAccessChart = ({ points, partitions }: PartitionAccessChartProps) => {
  if (!points.length && !partitions.length) {
    return (
      <span className="text-sm text-muted-foreground">
        No partition access data yet. Enable S3 request-cost tracking on the source in Connections, then wait for an ingest.
      </span>
    );
  }

  const days = partitionDays(points);
  const dayPeak = Math.max(0, ...days.map(({ requests }) => requests ?? 0));

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
          <strong>{ranked.length.toLocaleString()}</strong> partition{ranked.length === 1 ? "" : "s"} with recorded request counts
          {total > 0 && ranked.length > 10 ? (
            <>
              {" "}· top 10 take <strong>{Math.round((topTen / total) * 100)}%</strong> of requests
            </>
          ) : null}
          {cold > 0 ? (
            <>
              {" "}· <strong>{cold.toLocaleString()}</strong> with no recorded access in {COLD_AFTER_DAYS}+ days
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
            aria-label={`Requests per day from ${dayLabel(days[0].day)} to ${dayLabel(days[days.length - 1].day)}, peaking at ${dayPeak.toLocaleString()}. Missing days are not measured.`}
          >
            {days.map(({ day, requests }) => (
              <span key={day} className={requests == null ? "is-unmeasured" : undefined} title={`${dayLabel(day)}: ${requests == null ? "Not measured" : `${requests.toLocaleString()} requests`}`} style={{ height: requests == null ? "100%" : `${dayPeak > 0 ? (requests / dayPeak) * 100 : 0}%` }} />
            ))}
          </div>
          <div className="td-pa-day-axis" aria-hidden="true">
            <span>{dayLabel(days[0].day)}</span>
            <span>{dayLabel(days[days.length - 1].day)}</span>
          </div>
          {days.some((day) => day.requests == null) ? <p className="td-usage-caption">Outlined slots have no recorded count. Missing telemetry is not zero activity.</p> : null}
          <ChartDataTable caption="Storage requests by calendar day" toggleLabel="Daily request counts" rows={days} getRowKey={(row) => row.day} columns={[
            { key: "day", header: "Day (UTC)", render: (row) => row.day },
            { key: "requests", header: "Requests", align: "right", render: (row) => countText(row.requests) }
          ]} />
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

      <ChartDataTable
        className="td-pa-details"
        caption="Storage requests per partition"
        toggleLabel={`All ${partitions.length.toLocaleString()} partitions as a table`}
        rows={[...partitions].sort((a, b) => (measuredCount(b.total_requests) ?? -1) - (measuredCount(a.total_requests) ?? -1))}
        getRowKey={(row) => row.partition_path || "(unpartitioned)"}
        columns={[
          { key: "partition", header: "Partition", render: (row) => decodePartitionPath(row.partition_path) },
          { key: "requests", header: "Requests", align: "right", render: (row) => countText(row.total_requests) },
          { key: "last", header: "Last accessed", align: "right", render: (row) => lastAccessText(row.days_since_last_access) }
        ]}
      />
    </div>
  );
};
