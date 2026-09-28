import { createContext, useContext, useId, useMemo, type ReactNode } from "react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { ChartDataTable } from "../ui/chart-data-table";
import { EmptyState } from "../ui/empty-state";
import { cn } from "../../lib/cn";
import { MOTION_MS, useAnimateOnce, useReducedMotion } from "../../lib/motion";
import { EventMarkers, type ChartEvent } from "./EventMarkers";
import {
  formatCap,
  formatChangeAnswer,
  formatCoverage,
  formatSubtitle,
  formatTableCell,
  isUnknownPlotValue,
  provenanceLabel,
  UNKNOWN_MARK
} from "./format";
import { MissingPattern } from "./MissingPattern";
import "./motion-tokens.css";
import type {
  ChangeAnswerInput,
  ChartCap,
  ChartCardState,
  ChartProvenance,
  ChartSubtitle,
  CoverageInput,
  PlottedColumn,
  PlottedRow
} from "./types";

export type { ChartCardState };

export interface ChartMotion {
  reducedMotion: boolean;
  isAnimationActive: boolean;
  durationMs: number;
}

const ChartMotionContext = createContext<ChartMotion>({
  reducedMotion: false,
  isAnimationActive: false,
  durationMs: 0
});

export function useChartMotion(): ChartMotion {
  return useContext(ChartMotionContext);
}

export interface ChartCardProps {
  title: string;
  answer?: ChangeAnswerInput;
  subtitle?: ChartSubtitle;
  coverage?: CoverageInput;
  provenance?: ChartProvenance;
  table: readonly PlottedRow[];
  columns?: readonly PlottedColumn[];
  state?: ChartCardState;
  emptyTitle?: string;
  emptyDescription?: string;
  errorMessage?: string;
  onRetry?: () => void;
  staleMessage?: string;
  cap?: ChartCap;
  events?: readonly ChartEvent[];
  className?: string;
  children?: ReactNode;
}

function resolveState(state: ChartCardState, rowCount: number): ChartCardState {
  if (state === "loading" || state === "error") return state;
  if (state === "empty" || rowCount === 0) return "empty";
  return state;
}

function deriveColumns(rows: readonly PlottedRow[], columns?: readonly PlottedColumn[]): PlottedColumn[] {
  if (columns?.length) return [...columns];
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!keys.includes(key)) keys.push(key);
    }
  }
  return keys.map((key) => ({
    key,
    header: key,
    align: rows.some((row) => typeof row[key] === "number") ? "right" : "left"
  }));
}

export function ChartCard({
  title,
  answer,
  subtitle,
  coverage,
  provenance,
  table,
  columns,
  state = "ready",
  emptyTitle = "Nothing measured yet",
  emptyDescription = "Run a scan to plot this. Missing values stay blank. They are not zero.",
  errorMessage = "This chart could not be loaded.",
  onRetry,
  staleMessage = "Stale",
  cap,
  events,
  className,
  children
}: ChartCardProps) {
  const reducedMotion = useReducedMotion();
  const isAnimationActive = useAnimateOnce();
  const titleId = useId();
  const motion = useMemo<ChartMotion>(
    () => ({
      reducedMotion,
      isAnimationActive,
      durationMs: isAnimationActive ? MOTION_MS.slow : 0
    }),
    [reducedMotion, isAnimationActive]
  );

  if (table == null) return null;

  const resolved = resolveState(state, table.length);
  const showPlot = resolved === "ready" || resolved === "partial" || resolved === "stale";
  const answerLine = answer && typeof answer === "object" ? formatChangeAnswer(answer) : null;
  const subtitleLine = subtitle ? formatSubtitle(subtitle) : "";
  const coverageLine = coverage ? formatCoverage(coverage) : null;
  const capLine = cap ? formatCap(cap) : null;
  const hasUnknown = table.some((row) => Object.values(row).some((value) => isUnknownPlotValue(value)));
  const plottedColumns = deriveColumns(table, columns);

  return (
    <ChartMotionContext.Provider value={motion}>
      <section
        aria-labelledby={titleId}
        aria-busy={resolved === "loading" || undefined}
        data-testid="chart-card"
        data-state={resolved}
        data-animation-active={isAnimationActive ? "true" : "false"}
        data-animation-duration={String(motion.durationMs)}
        className={cn(
          "rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-1)]",
          className
        )}
      >
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h3 id={titleId} className="text-[13px] font-semibold leading-5 text-foreground">
              {title}
            </h3>
            {answerLine ? (
              <p data-testid="chart-answer" className="mt-1 text-sm text-foreground">
                {answerLine}
              </p>
            ) : null}
            {subtitleLine ? (
              <p data-testid="chart-subtitle" className="mt-0.5 text-xs text-muted-foreground">
                {subtitleLine}
              </p>
            ) : null}
            {coverageLine ? (
              <p data-testid="chart-coverage" className="mt-0.5 text-xs text-muted-foreground">
                {coverageLine}
              </p>
            ) : null}
            {capLine ? (
              <p data-testid="chart-cap" className="mt-0.5 text-xs text-muted-foreground">
                {capLine}
              </p>
            ) : null}
            {resolved === "partial" && !coverageLine ? (
              <p data-testid="chart-partial" className="mt-0.5 text-xs text-muted-foreground">
                Partial
              </p>
            ) : null}
            {resolved === "stale" ? (
              <p data-testid="chart-stale" className="mt-0.5 text-xs text-muted-foreground">
                {staleMessage}
              </p>
            ) : null}
          </div>
          {provenance ? (
            <Badge variant="outline" data-testid="chart-provenance" className="shrink-0">
              {provenanceLabel(provenance)}
            </Badge>
          ) : null}
        </header>
        <div className="px-4 py-3">
          {resolved === "loading" ? (
            <div role="status" className="py-8 text-sm text-muted-foreground">
              Loading
            </div>
          ) : null}
          {resolved === "error" ? (
            <div role="alert" className="py-6">
              <p className="text-sm text-foreground">{errorMessage}</p>
              <Button type="button" size="sm" variant="secondary" className="mt-3" onClick={onRetry}>
                Retry
              </Button>
            </div>
          ) : null}
          {resolved === "empty" ? <EmptyState title={emptyTitle} description={emptyDescription} /> : null}
          {showPlot ? <div data-testid="chart-plot">{children}</div> : null}
          {showPlot && hasUnknown ? (
            <div className="mt-2">
              <MissingPattern />
            </div>
          ) : null}
          {showPlot && events?.length ? <EventMarkers events={events} className="mt-3" /> : null}
          {showPlot ? (
            <ChartDataTable
              caption={title}
              columns={plottedColumns.map((column) => ({
                key: column.key,
                header: column.header,
                align: column.align,
                render: (row: PlottedRow) => {
                  const text = formatTableCell(row[column.key]);
                  if (text === UNKNOWN_MARK) return <span title="Not measured">{UNKNOWN_MARK}</span>;
                  return text;
                }
              }))}
              rows={[...table]}
              getRowKey={(row, index) => (typeof row.id === "string" && row.id ? row.id : String(index))}
            />
          ) : null}
        </div>
      </section>
    </ChartMotionContext.Provider>
  );
}
