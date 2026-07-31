import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { TableRunRead } from "../../api/types";
import { GradePill, StatusTag } from "../status";
import { EmptyState } from "../common";
import { MetricTile } from "../ui/stat";
import { Skeleton } from "../ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { formatDuration, formatNumber, formatTime } from "../../lib/format";
import { ChartCard } from "./ChartCard";

interface Props {
  runs: TableRunRead[];
  isLoading?: boolean;
  isError?: boolean;
  error?: unknown;
}

const formatDurationSeconds = (seconds?: number | null): string =>
  seconds == null ? "—" : formatDuration(seconds * 1000);

const formatDelta = (before?: number | null, after?: number | null, unit = ""): string => {
  if (before == null || after == null) return "—";
  const delta = after - before;
  if (delta === 0) return `no change`;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${formatNumber(delta)}${unit}`;
};

const StatsDelta = ({ run }: { run: TableRunRead }) => {
  const { before_stats: before, after_stats: after } = run;
  if (!before && !after) {
    return <span className="text-xs text-muted-foreground">No stats snapshot around this run.</span>;
  }
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
      <div>
        <div className="text-muted-foreground">Grade</div>
        <div className="flex items-center gap-1.5">
          <GradePill grade={before?.grade} />
          <ChevronRight className="size-3 text-muted-foreground" aria-hidden />
          <GradePill grade={after?.grade} />
        </div>
      </div>
      <div>
        <div className="text-muted-foreground">Total files</div>
        <div>
          {formatNumber(before?.total_files)} → {formatNumber(after?.total_files)}
          <span className="ml-1 text-muted-foreground">
            ({formatDelta(before?.total_files, after?.total_files)})
          </span>
        </div>
      </div>
      <div>
        <div className="text-muted-foreground">Small files</div>
        <div>
          {formatNumber(before?.small_files_count)} → {formatNumber(after?.small_files_count)}
          <span className="ml-1 text-muted-foreground">
            ({formatDelta(before?.small_files_count, after?.small_files_count)})
          </span>
        </div>
      </div>
      <div>
        <div className="text-muted-foreground">Delete files</div>
        <div>
          {formatNumber(before?.total_delete_files)} → {formatNumber(after?.total_delete_files)}
          <span className="ml-1 text-muted-foreground">
            ({formatDelta(before?.total_delete_files, after?.total_delete_files)})
          </span>
        </div>
      </div>
    </div>
  );
};

const ResourceUsage = ({ run }: { run: TableRunRead }) => {
  const est = run.resource_estimate;
  if (!est) return null;
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
      <div>
        <span className="text-muted-foreground">Engine: </span>
        {est.engine ?? "—"}
      </div>
      <div>
        <span className="text-muted-foreground">Active compute time: </span>
        {formatDurationSeconds(est.active_duration_seconds)}
      </div>
      {est.vcpu_hours != null ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="cursor-help underline decoration-dotted">
              <span className="text-muted-foreground">vCPU-hours: </span>
              {est.vcpu_hours.toFixed(1)}
            </div>
          </TooltipTrigger>
          <TooltipContent>
            Reserved capacity estimate (configured executor/driver shape × active wall-clock
            time) — not measured utilization.
          </TooltipContent>
        </Tooltip>
      ) : null}
      {est.memory_gib_hours != null ? (
        <div>
          <span className="text-muted-foreground">Memory GiB-hours: </span>
          {est.memory_gib_hours.toFixed(1)}
        </div>
      ) : null}
    </div>
  );
};

const RunRow = ({ run }: { run: TableRunRead }) => {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <tr
        className="cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <td>
          {expanded ? (
            <ChevronDown className="size-3.5" aria-hidden />
          ) : (
            <ChevronRight className="size-3.5" aria-hidden />
          )}
        </td>
        <td>
          <StatusTag status={run.status} />
        </td>
        <td>{run.mode}</td>
        <td className="text-xs text-muted-foreground">{run.trigger ?? "—"}</td>
        <td>{formatTime(run.started_at)}</td>
        <td>{formatDurationSeconds(run.duration_seconds)}</td>
        <td className="text-xs text-muted-foreground">
          {run.steps.map((s) => s.operation_type).join(", ") || "—"}
        </td>
      </tr>
      {expanded ? (
        <tr>
          <td colSpan={7} className="bg-[var(--color-surface-muted)] px-4 py-3">
            <div className="flex flex-col gap-3">
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  Before → after
                </div>
                <StatsDelta run={run} />
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  Resource usage
                </div>
                <ResourceUsage run={run} />
              </div>
              {run.error ? (
                <div className="text-xs text-[var(--color-status-danger)]">{run.error}</div>
              ) : null}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
};

export const RunsTab = ({ runs, isLoading, isError, error }: Props) => {
  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <ChartCard title="Runs">
        <p className="text-sm text-[var(--color-status-danger)]">
          Failed to load run history: {error instanceof Error ? error.message : "unknown error"}
        </p>
      </ChartCard>
    );
  }

  if (runs.length === 0) {
    return (
      <ChartCard title="Runs">
        <EmptyState
          title="No maintenance runs yet"
          description="Once a scan or maintenance job touches this table, its run history — status, operations, before/after stats, and resource usage — will show up here."
        />
      </ChartCard>
    );
  }

  const succeeded = runs.filter((r) => r.status === "succeeded").length;
  const totalVcpuHours = runs.reduce((sum, r) => sum + (r.resource_estimate?.vcpu_hours ?? 0), 0);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricTile label="Runs" value={runs.length} />
        <MetricTile label="Succeeded" value={`${succeeded}/${runs.length}`} />
        <MetricTile label="Total vCPU-hours" value={totalVcpuHours.toFixed(1)} />
      </div>
      <ChartCard title="Run history" subtitle="Click a row for before/after stats and resource usage">
        <div className="overflow-x-auto">
          <table className="td-schema-table">
            <thead>
              <tr>
                <th aria-hidden />
                <th>Status</th>
                <th>Mode</th>
                <th>Trigger</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Operations</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <RunRow key={run.job_id} run={run} />
              ))}
            </tbody>
          </table>
        </div>
      </ChartCard>
    </div>
  );
};
