import type { CSSProperties } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";

const STATUS_COLORS: Record<string, string> = {
  succeeded: "var(--status-success, #3d8f5f)",
  success: "var(--status-success, #3d8f5f)",
  running: "var(--status-running, #3d6fd4)",
  admitted: "var(--status-running, #3d6fd4)",
  failed: "var(--status-failed, #c44c4c)",
  cancelled: "var(--status-muted, #8a96a6)",
  queued: "var(--status-muted, #8a96a6)",
  deferred: "var(--status-warning, #c9a227)"
};

export interface RunDurationBarProps {
  startedAt?: string | null;
  finishedAt?: string | null;
  runStartedAt?: string | null;
  runFinishedAt?: string | null;
  status?: string | null;
  label?: string;
}

const parseTs = (value?: string | null): number | null => {
  if (!value) return null;
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : null;
};

export const RunDurationBar = ({
  startedAt,
  finishedAt,
  runStartedAt,
  runFinishedAt,
  status,
  label
}: RunDurationBarProps) => {
  const jobStart = parseTs(startedAt);
  const jobEnd = parseTs(finishedAt) ?? Date.now();
  const runStart = parseTs(runStartedAt) ?? jobStart;
  const runEnd = parseTs(runFinishedAt) ?? jobEnd;

  if (!jobStart || !runStart || !runEnd || runEnd <= runStart) {
    return <span className="ops-duration-bar ops-duration-bar--empty">—</span>;
  }

  const runSpan = runEnd - runStart;
  const offsetPct = Math.max(0, Math.min(100, ((jobStart - runStart) / runSpan) * 100));
  const widthPct = Math.max(4, Math.min(100 - offsetPct, ((jobEnd - jobStart) / runSpan) * 100));
  const color = STATUS_COLORS[(status ?? "").toLowerCase()] ?? STATUS_COLORS.queued;

  const barStyle: CSSProperties = {
    left: `${offsetPct}%`,
    width: `${widthPct}%`,
    backgroundColor: color
  };

  const tooltipText = label ?? `${Math.round(jobEnd - jobStart)}ms within run`;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="ops-duration-bar" role="img" aria-label={label ?? "Job duration within run"}>
          <div className="ops-duration-bar__track">
            <div className="ops-duration-bar__fill" style={barStyle} />
          </div>
        </div>
      </TooltipTrigger>
      <TooltipContent>{tooltipText}</TooltipContent>
    </Tooltip>
  );
};
