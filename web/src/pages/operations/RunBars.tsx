import { Link } from "react-router-dom";
import type { RunRead } from "../../api/types";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { cn } from "../../lib/cn";
import { formatDuration, formatTime } from "../../lib/format";
import { runDurationMs, shortRunId } from "../../lib/operations";
import { countByTone, RUN_TONE_LABEL, RUN_TONE_ORDER, runTone, type RunTone } from "./runState";
import "./runBars.css";

const runLabel = (run: RunRead) =>
  `Run ${shortRunId(run.id)}, ${RUN_TONE_LABEL[runTone(run.status)]}, started ${formatTime(run.created_at)}, ${formatDuration(runDurationMs(run))}`;

/** Bar height in percent; a square root keeps a 2-minute run visible next to a 2-hour batch. */
const barHeight = (run: RunRead, longest: number) => {
  const duration = runDurationMs(run) ?? 0;
  if (longest <= 0) return 12;
  return Math.max(12, Math.round(Math.sqrt(duration / longest) * 100));
};

export const RunStateDot = ({ status, className }: { status?: string | null; className?: string }) => (
  <span className={cn("run-dot", `run-tone--${runTone(status)}`, className)} aria-hidden="true" />
);

export const RunStateLabel = ({ status }: { status?: string | null }) => (
  <span className="run-state">
    <RunStateDot status={status} />
    {RUN_TONE_LABEL[runTone(status)]}
  </span>
);

/** Airflow's DAG-list "Runs" column: one counted circle per state that occurred. */
export const RunStateCounts = ({ runs }: { runs: RunRead[] }) => {
  const counts = countByTone(runs);
  const present = RUN_TONE_ORDER.filter((tone) => counts[tone] > 0);
  if (present.length === 0) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <ul className="run-counts" aria-label="Runs by state">
      {present.map((tone) => (
        <li
          key={tone}
          className={cn("run-counts__item", `run-tone--${tone}`)}
          title={`${counts[tone]} ${RUN_TONE_LABEL[tone].toLowerCase()}`}
        >
          <span className="sr-only">{RUN_TONE_LABEL[tone]}: </span>
          {counts[tone]}
        </li>
      ))}
    </ul>
  );
};

/** Compact duration bars for a workflow row, oldest on the left. `runs` is newest-first. */
export const RunDurationBars = ({ runs, max = 14 }: { runs: RunRead[]; max?: number }) => {
  const shown = runs.slice(0, max).reverse();
  const longest = Math.max(0, ...shown.map((run) => runDurationMs(run) ?? 0));
  const placeholders = Math.max(0, max - shown.length);
  return (
    <ul className="run-bars" aria-label={`Last ${shown.length} runs`}>
      {Array.from({ length: placeholders }).map((_, idx) => (
        <li key={`ph-${idx}`} className="run-bars__slot run-bars__slot--empty" aria-hidden="true" />
      ))}
      {shown.map((run) => (
        <li key={run.id} className="run-bars__slot">
          <Link
            to={`/runs/${run.id}`}
            className={cn("run-bars__bar", `run-tone--${runTone(run.status)}`)}
            style={{ height: `${barHeight(run, longest)}%` }}
            aria-label={runLabel(run)}
            title={runLabel(run)}
            onClick={(event) => event.stopPropagation()}
          />
        </li>
      ))}
    </ul>
  );
};

/** Airflow grid-view header: every run as a bar, height by duration, colour by state. */
export const RunTimeline = ({ runs, max = 60 }: { runs: RunRead[]; max?: number }) => {
  const shown = runs.slice(0, max).reverse();
  const longest = Math.max(0, ...shown.map((run) => runDurationMs(run) ?? 0));
  const counts = countByTone(shown);
  const legend = RUN_TONE_ORDER.filter((tone) => counts[tone] > 0);
  if (shown.length === 0) return null;
  return (
    <figure className="run-timeline">
      <figcaption className="run-timeline__header">
        <span className="run-timeline__title">
          Last {shown.length} run{shown.length === 1 ? "" : "s"}
        </span>
        <ul className="run-timeline__legend" aria-label="Legend">
          {legend.map((tone: RunTone) => (
            <li key={tone}>
              <span className={cn("run-dot", `run-tone--${tone}`)} aria-hidden="true" />
              {RUN_TONE_LABEL[tone]} <span className="run-timeline__legend-count">{counts[tone]}</span>
            </li>
          ))}
        </ul>
      </figcaption>
      <div className="run-timeline__plot">
        <div className="run-timeline__axis" aria-hidden="true">
          <span>{formatDuration(longest)}</span>
          <span>0</span>
        </div>
        <ol className="run-timeline__bars" aria-label="Runs, oldest first">
          {shown.map((run) => (
            <li key={run.id} className="run-timeline__slot">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Link
                    to={`/runs/${run.id}`}
                    className={cn("run-timeline__bar", `run-tone--${runTone(run.status)}`)}
                    style={{ height: `${barHeight(run, longest)}%` }}
                    aria-label={runLabel(run)}
                  />
                </TooltipTrigger>
                <TooltipContent side="top" className="run-timeline__tip">
                  <span className="run-state">
                    <RunStateDot status={run.status} />
                    {RUN_TONE_LABEL[runTone(run.status)]}
                  </span>
                  <span className="font-mono">{shortRunId(run.id)}</span>
                  <span>{formatTime(run.created_at)}</span>
                  <span className="tabular-nums">{formatDuration(runDurationMs(run))}</span>
                </TooltipContent>
              </Tooltip>
            </li>
          ))}
        </ol>
      </div>
      <div className="run-timeline__dates" aria-hidden="true">
        <span>{formatTime(shown[0].created_at)}</span>
        <span>{formatTime(shown[shown.length - 1].created_at)}</span>
      </div>
    </figure>
  );
};

/** Inline duration bar for a table cell, relative to the longest run on the page. */
export const DurationCell = ({ run, longest }: { run: RunRead; longest: number }) => {
  const duration = runDurationMs(run);
  const width = duration && longest > 0 ? Math.max(4, Math.round((duration / longest) * 100)) : 0;
  return (
    <span className="run-duration">
      <span className="run-duration__value">{formatDuration(duration)}</span>
      <span className="run-duration__track" aria-hidden="true">
        <span className={cn("run-duration__fill", `run-tone--${runTone(run.status)}`)} style={{ width: `${width}%` }} />
      </span>
    </span>
  );
};
