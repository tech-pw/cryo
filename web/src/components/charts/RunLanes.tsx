import { ChartCard } from "./ChartCard";
import { getDisplayTimeZone } from "../../lib/format";

export interface RunLaneJob {
  id: string;
  table: string;
  engine: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
}

const parse = (value: string | null): number | null => {
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
};

export const RunLanes = ({ jobs }: { jobs: RunLaneJob[] }) => {
  const times = jobs.flatMap((job) => [parse(job.startedAt), parse(job.finishedAt)]).filter((t): t is number => t != null);
  const start = times.length ? Math.min(...times) : null;
  const end = times.length ? Math.max(...times) : null;
  const span = start != null && end != null ? Math.max(end - start, 1) : 1;
  const rows = jobs.map((job) => ({
    id: job.id,
    table: job.table,
    engine: job.engine,
    status: job.status,
    started: job.startedAt ?? "Not recorded",
    finished: job.finishedAt ?? "Not recorded"
  }));

  return (
    <ChartCard
      title="When did each table run?"
      subtitle={{ unit: "time", timeZone: getDisplayTimeZone() }}
      table={rows}
      columns={[
        { key: "table", header: "Table" },
        { key: "engine", header: "Engine" },
        { key: "status", header: "Status" },
        { key: "started", header: "Started" },
        { key: "finished", header: "Finished" }
      ]}
    >
      <ul className="flex flex-col gap-1">
        {jobs.map((job) => {
          const jobStart = parse(job.startedAt);
          const jobEnd = parse(job.finishedAt);
          const left = jobStart != null && start != null ? ((jobStart - start) / span) * 100 : 0;
          const width =
            jobStart != null && jobEnd != null ? Math.max(2, ((jobEnd - jobStart) / span) * 100) : jobStart == null ? 0 : 8;
          return (
            <li key={job.id} className="grid grid-cols-[8rem_minmax(0,1fr)] items-center gap-2 text-xs">
              <span className="truncate" title={job.table}>
                {job.table}
              </span>
              <span className="relative h-3 rounded-sm bg-[var(--color-surface-sunken)]">
                {jobStart == null ? (
                  <span className="px-1 text-[10px] text-muted-foreground">Not recorded</span>
                ) : (
                  <span
                    className="absolute inset-y-0 rounded-sm bg-[var(--color-primary)]"
                    style={{ left: `${left}%`, width: `${width}%` }}
                    title={`${job.engine} · ${job.status}`}
                  />
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </ChartCard>
  );
};
