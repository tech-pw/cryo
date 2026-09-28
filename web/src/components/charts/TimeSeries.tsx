import { type JSX } from "react";
import { ChartCard, useChartMotion } from "./ChartCard";
import type { ChartEvent, ChartEventKind } from "./EventMarkers";
import { formatChangeAnswer, formatFixedUnit } from "./format";
import { plotValue } from "./plotValue";
import { formatTimeTick, timeScale, toEpochMs } from "./timeScale";
import type { ChangeAnswerInput } from "./types";

export interface TimeSeriesPoint {
  at: string;
  value: number | null;
  grade?: string | null;
}

export interface TimeSeriesMarker {
  at: string;
  label: string;
}

const GRADE_FILL: Record<string, string> = {
  A: "var(--grade-a)",
  B: "var(--grade-b)",
  C: "var(--grade-c)",
  D: "var(--grade-d)",
  F: "var(--grade-f)"
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
const WIDTH = 640;
const HEIGHT = 160;
const PAD_X = 16;
const PAD_Y = 16;
const TIME_ZONE = "UTC";

function gradeFill(grade: string | null | undefined): string {
  if (!grade) return "var(--color-chart-primary)";
  return GRADE_FILL[grade.trim().toUpperCase()] ?? "var(--color-chart-primary)";
}

function measuredPoints(points: readonly TimeSeriesPoint[]): Array<{ at: string; value: number; grade: string | null }> {
  return points.flatMap((point) => {
    const value = plotValue(point.value);
    if (value == null) return [];
    return [{ at: point.at, value, grade: point.grade ?? null }];
  });
}

function sinceLabel(at: string): string {
  const epoch = toEpochMs(at);
  if (epoch == null) return at;
  const date = new Date(epoch);
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

function changeInput(points: readonly TimeSeriesPoint[], unit: string): ChangeAnswerInput | undefined {
  const measured = measuredPoints(points);
  if (measured.length < 2) return undefined;
  const first = measured[0];
  const last = measured[measured.length - 1];
  return { label: unit, previous: first.value, current: last.value, since: sinceLabel(first.at) };
}

function chartEvents(
  markers: readonly TimeSeriesMarker[],
  events: readonly { at: string; label: string; id?: string; kind?: ChartEventKind }[]
): ChartEvent[] {
  return [
    ...markers.map((marker, index) => ({
      id: `maintenance-${marker.at}-${index}`,
      at: marker.at,
      kind: "maintenance" as const,
      label: marker.label
    })),
    ...events.map((event, index) => ({
      id: event.id ?? `maintenance-${event.at}-${index}`,
      at: event.at,
      kind: event.kind ?? "maintenance",
      label: event.label
    }))
  ];
}

function fallbackAnswer(points: readonly TimeSeriesPoint[], unit: string): string {
  const measured = measuredPoints(points);
  if (!measured.length) return "Not measured";
  const latest = measured[measured.length - 1];
  const grade = latest.grade?.trim() ? `, grade ${latest.grade.trim().toUpperCase()}` : "";
  return `${formatFixedUnit(latest.value, unit)}${grade}`;
}

function TimeSeriesPlot({
  title,
  points,
  markers
}: {
  title: string;
  points: TimeSeriesPoint[];
  markers: TimeSeriesMarker[];
}) {
  const motion = useChartMotion();
  const duration = motion.reducedMotion ? 0 : motion.durationMs;
  const epochs = [...points, ...markers]
    .map((item) => toEpochMs(item.at))
    .filter((value): value is number => value != null);
  const values = measuredPoints(points).map((point) => point.value);
  const t0 = epochs.length ? Math.min(...epochs) : null;
  const t1 = epochs.length ? Math.max(...epochs) : null;
  const v0 = values.length ? Math.min(...values) : null;
  const v1 = values.length ? Math.max(...values) : null;
  const xOf = (epoch: number): number | null => {
    if (t0 == null || t1 == null) return null;
    if (t0 === t1) return WIDTH / 2;
    return PAD_X + ((epoch - t0) / (t1 - t0)) * (WIDTH - PAD_X * 2);
  };
  const yOf = (value: number): number | null => {
    if (v0 == null || v1 == null) return null;
    if (v0 === v1) return HEIGHT / 2;
    const pad = (v1 - v0) * 0.08;
    const min = v0 - pad;
    const max = v1 + pad;
    return HEIGHT - PAD_Y - ((value - min) / (max - min)) * (HEIGHT - PAD_Y * 2);
  };
  const segments: string[] = [];
  let current: string[] = [];
  const flush = () => {
    if (current.length >= 2) segments.push(current.join(" "));
    current = [];
  };
  for (const point of points) {
    const epoch = toEpochMs(point.at);
    const value = plotValue(point.value);
    const x = epoch == null ? null : xOf(epoch);
    const y = value == null ? null : yOf(value);
    if (x == null || y == null) {
      flush();
      continue;
    }
    current.push(`${x},${y}`);
  }
  flush();
  const scale = timeScale(
    [...points.map((point) => point.at), ...markers.map((marker) => marker.at)],
    { tickCount: 4, timeZone: TIME_ZONE }
  );
  const hasGap = points.some((point) => plotValue(point.value) == null);

  return (
    <div>
      {hasGap ? <p className="mb-2 text-xs text-muted-foreground">Gaps are not measured.</p> : null}
      <svg
        role="img"
        aria-label={hasGap ? `${title} Gaps are not measured.` : title}
        data-testid="timeseries-plot"
        data-plotted-values={values.join(",")}
        data-connect-nulls="false"
        data-animation-duration={String(duration)}
        data-reduced-motion={motion.reducedMotion ? "true" : "false"}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-40 w-full"
        style={{ animation: "none", transition: duration > 0 ? `opacity ${duration}ms` : "none" }}
      >
        {segments.map((segment, index) => (
          <polyline
            key={index}
            data-testid="timeseries-segment"
            fill="none"
            stroke="var(--color-chart-primary)"
            strokeWidth="2"
            points={segment}
          />
        ))}
        {markers.map((marker, index) => {
          const epoch = toEpochMs(marker.at);
          const x = epoch == null ? null : xOf(epoch);
          if (x == null) return null;
          return (
            <line
              key={`${marker.at}-${index}`}
              data-testid="timeseries-marker"
              data-kind="maintenance"
              data-label={marker.label}
              x1={x}
              x2={x}
              y1={PAD_Y}
              y2={HEIGHT - PAD_Y}
              stroke="var(--color-border-strong)"
              strokeDasharray="3 3"
            >
              <title>{marker.label}</title>
            </line>
          );
        })}
        {points.map((point, index) => {
          const epoch = toEpochMs(point.at);
          const value = plotValue(point.value);
          const x = epoch == null ? null : xOf(epoch);
          const y = value == null ? null : yOf(value);
          if (x == null || y == null || value == null) {
            return (
              <g key={`gap-${point.at}-${index}`} data-testid="timeseries-gap" data-at={point.at}>
                {x != null ? (
                  <line x1={x} x2={x} y1={PAD_Y} y2={PAD_Y + 8} stroke="var(--color-muted)" />
                ) : null}
                <title>Not measured</title>
              </g>
            );
          }
          return (
            <circle
              key={`${point.at}-${index}`}
              data-testid="timeseries-point"
              data-at={point.at}
              data-value={String(value)}
              cx={x}
              cy={y}
              r="3.5"
              fill={gradeFill(point.grade)}
            />
          );
        })}
      </svg>
      {scale ? (
        <div className="mt-1 flex justify-between gap-2 text-[10px] text-muted-foreground">
          {scale.ticks.map((tick, index) => (
            <span key={`${tick}-${index}`}>{scale.formatTick(tick)}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export const TimeSeries = ({
  title,
  answer,
  unit,
  points,
  markers = [],
  events = [],
  provenance
}: {
  title: string;
  answer?: string;
  unit: string;
  points: TimeSeriesPoint[];
  markers?: TimeSeriesMarker[];
  events?: readonly { at: string; label: string; id?: string; kind?: ChartEventKind }[];
  provenance?: "measured" | "modelled";
}): JSX.Element => {
  const plottedMarkers = [...markers, ...events.map((event) => ({ at: event.at, label: event.label }))];
  const provided = answer?.trim() ? answer.trim() : undefined;
  const change = provided ? undefined : changeInput(points, unit);
  const changeLine = change ? formatChangeAnswer(change) : null;
  const bodyAnswer = provided ?? (changeLine ? undefined : fallbackAnswer(points, unit));
  const measuredCount = measuredPoints(points).length;
  const hasGap = points.length > 0 && measuredCount < points.length;
  const rows = points.map((point, index) => {
    const epoch = toEpochMs(point.at);
    return {
      id: `${point.at}-${index}`,
      at: epoch == null ? point.at : formatTimeTick(epoch, { timeZone: TIME_ZONE }),
      value: plotValue(point.value),
      grade: point.grade ?? null
    };
  });

  return (
    <ChartCard
      title={title}
      answer={change && changeLine ? change : undefined}
      provenance={provenance}
      subtitle={{ unit }}
      coverage={hasGap ? { measured: measuredCount, total: points.length, noun: "points" } : undefined}
      state={points.length === 0 ? "empty" : hasGap ? "partial" : "ready"}
      table={rows}
      columns={[
        { key: "at", header: "Time" },
        { key: "value", header: unit, align: "right" },
        { key: "grade", header: "Grade" }
      ]}
      events={chartEvents(markers, events)}
    >
      {bodyAnswer ? (
        <p data-testid="chart-answer" className="mb-2 text-sm text-foreground">
          {bodyAnswer}
        </p>
      ) : null}
      <TimeSeriesPlot title={title} points={points} markers={plottedMarkers} />
    </ChartCard>
  );
};
