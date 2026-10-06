import { ChartCard, useChartMotion } from "./ChartCard";
import type { ChartEvent } from "./EventMarkers";
import { formatTimeTick, timeScale, toEpochMs } from "./timeScale";
import type { PlottedRow } from "./types";

export interface TimelineWrite {
  id: string;
  at: string | null;
  label: string;
}

export interface TimelineRead {
  id: string;
  at: string | null;
  queries: number | null;
}

export interface TimelineAction {
  id: string;
  at: string | null;
  label: string;
}

export interface TableTimelineProps {
  writes: readonly TimelineWrite[];
  reads: readonly TimelineRead[];
  actions: readonly TimelineAction[];
  period?: string;
  timeZone?: string;
}

type Lane = "writes" | "reads" | "cryo";

interface Stamp {
  id: string;
  lane: Lane;
  at: string | null;
  epoch: number | null;
  label: string;
  value: number | null;
}

const LANE_LABEL: Record<Lane, string> = {
  writes: "Writes",
  reads: "Reads",
  cryo: "Cryo"
};

function measured(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(value) || value < 0) return null;
  return value;
}

function recordedEpoch(value: string | null | undefined): number | null {
  if (value == null || value.trim() === "") return null;
  const epoch = toEpochMs(value);
  if (epoch == null || epoch === 0) return null;
  return epoch;
}

function xPercent(epoch: number, start: number, end: number): number {
  if (end === start) return 50;
  return ((epoch - start) / (end - start)) * 100;
}

function stampsOf(props: TableTimelineProps): Stamp[] {
  return [
    ...props.writes.map((write) => ({
      id: write.id,
      lane: "writes" as const,
      at: write.at,
      epoch: recordedEpoch(write.at),
      label: write.label,
      value: null
    })),
    ...props.reads.map((read) => ({
      id: read.id,
      lane: "reads" as const,
      at: read.at,
      epoch: recordedEpoch(read.at),
      label: read.queries == null ? "Not measured" : `${measured(read.queries) ?? "Not measured"} queries`,
      value: measured(read.queries)
    })),
    ...props.actions.map((action) => ({
      id: action.id,
      lane: "cryo" as const,
      at: action.at,
      epoch: recordedEpoch(action.at),
      label: action.label,
      value: null
    }))
  ];
}

function TimelineFigure({
  stamps,
  timeZone
}: {
  stamps: readonly Stamp[];
  timeZone: string;
}) {
  const motion = useChartMotion();
  const epochs = stamps.flatMap((stamp) => (stamp.epoch == null ? [] : [stamp.epoch]));
  const start = epochs.length ? Math.min(...epochs) : null;
  const end = epochs.length ? Math.max(...epochs) : null;
  const scale =
    start == null || end == null
      ? null
      : timeScale(
          stamps.flatMap((stamp) => (stamp.epoch == null || stamp.at == null ? [] : [stamp.at])),
          { tickCount: 4, timeZone }
        );
  const readValues = stamps.flatMap((stamp) => (stamp.lane === "reads" && stamp.value != null ? [stamp.value] : []));
  const maxQueries = readValues.length ? Math.max(...readValues) : 0;
  const unmeasuredRead = stamps.some((stamp) => stamp.lane === "reads" && stamp.epoch != null && stamp.value == null);
  const scaleStart = start == null ? "" : String(start);

  return (
    <div data-testid="table-timeline" data-animation-duration={String(motion.durationMs)}>
      <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-y-2">
        {(["writes", "reads", "cryo"] as const).map((lane) => {
          const laneStamps = stamps.filter((stamp) => stamp.lane === lane);
          const missing = laneStamps.some((stamp) => stamp.epoch == null);
          return (
            <div key={lane} className="contents">
              <span className="text-xs text-foreground">{LANE_LABEL[lane]}</span>
              <div
                data-testid="timeline-lane"
                data-lane={lane}
                data-scale-start={scaleStart}
                data-scale-end={end == null ? "" : String(end)}
                className="relative h-6"
              >
                {start != null && end != null
                  ? laneStamps.map((stamp) => {
                      if (stamp.epoch == null) return null;
                      const x = xPercent(stamp.epoch, start, end);
                      const unmeasured = stamp.lane === "reads" && stamp.value == null;
                      const opacity =
                        stamp.lane === "reads" && stamp.value != null
                          ? maxQueries <= 0
                            ? 0.45
                            : 0.3 + (stamp.value / maxQueries) * 0.7
                          : 1;
                      return (
                        <span
                          key={stamp.id}
                          data-testid="timeline-mark"
                          data-id={stamp.id}
                          data-lane={lane}
                          data-x={x.toFixed(2)}
                          data-epoch={String(stamp.epoch)}
                          data-value={unmeasured ? "unmeasured" : stamp.value == null ? "event" : String(stamp.value)}
                          title={unmeasured ? "Not measured" : stamp.label}
                          className={
                            unmeasured
                              ? "absolute top-1 h-4 w-1.5 -translate-x-1/2 border border-dashed border-[var(--color-muted)]"
                              : "absolute top-1 h-4 w-1.5 -translate-x-1/2 bg-[var(--color-chart-primary)]"
                          }
                          style={{ left: `${x}%`, opacity }}
                        />
                      );
                    })
                  : null}
                {missing ? (
                  <span data-testid="timeline-missing" className="text-[10px] text-muted-foreground">
                    Not recorded
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
        <span />
        {scale && start != null && end != null ? (
          <div
            data-testid="timeline-axis"
            data-axis="time"
            data-scale-type="time"
            data-start={String(start)}
            data-end={String(end)}
            className="relative h-4"
          >
            {scale.ticks.map((tick, index) => (
              <span
                key={`${tick}-${index}`}
                className="absolute top-0 -translate-x-1/2 whitespace-nowrap text-[10px] text-muted-foreground"
                style={{ left: `${xPercent(tick, start, end)}%` }}
              >
                {formatTimeTick(tick, { timeZone })}
              </span>
            ))}
          </div>
        ) : (
          <p data-testid="timeline-axis" data-axis="time" className="text-[10px] text-muted-foreground">
            Not recorded
          </p>
        )}
      </div>
      {unmeasuredRead ? <p className="mt-2 text-xs text-muted-foreground">Not measured</p> : null}
      <p className="mt-1 text-[10px] text-muted-foreground">Darker reads are more queries. One time axis.</p>
    </div>
  );
}

export function TableTimeline({
  writes,
  reads,
  actions,
  period = "last 30 days",
  timeZone = "UTC"
}: TableTimelineProps) {
  const stamps = stampsOf({ writes, reads, actions, period, timeZone });
  const rows: PlottedRow[] = stamps.map((stamp) => ({
    id: `${stamp.lane}-${stamp.id}`,
    lane: LANE_LABEL[stamp.lane],
    label: stamp.label,
    at: stamp.epoch == null ? "Not recorded" : (stamp.at ?? "Not recorded"),
    value: stamp.lane === "reads" ? (stamp.value == null ? "Not measured" : stamp.value) : stamp.label
  }));
  const events: ChartEvent[] = stamps.flatMap((stamp) => {
    if (stamp.lane !== "cryo" || stamp.epoch == null || stamp.at == null) return [];
    return [{ id: stamp.id, at: stamp.at, kind: "maintenance" as const, label: stamp.label }];
  });

  return (
    <ChartCard
      title="What happened to this table?"
      subtitle={{ unit: "time", period, timeZone }}
      provenance="measured"
      table={rows}
      columns={[
        { key: "lane", header: "Lane" },
        { key: "label", header: "Label" },
        { key: "at", header: "Time" },
        { key: "value", header: "Value" }
      ]}
      events={events}
    >
      <TimelineFigure stamps={stamps} timeZone={timeZone} />
    </ChartCard>
  );
}
