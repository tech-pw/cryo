import { ChartCard, useChartMotion } from "./ChartCard";
import { toEpochMs } from "./timeScale";
import type { PlottedColumn, PlottedRow } from "./types";

export const HEAT_STRIP_WINDOW = 120;

export interface HeatStripPartition {
  id: string;
  key: string;
  smallFiles: number | null;
  files: number | null;
  queries: number | null;
  lastCompactionAt: string | null;
  nextRun?: boolean;
}

export interface HeatStripProps {
  partitions: readonly HeatStripPartition[];
  unpartitioned?: boolean;
}

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

function keyOrder(partitions: readonly HeatStripPartition[]): HeatStripPartition[] {
  return partitions
    .map((partition, index) => ({ partition, index }))
    .sort((left, right) => {
      const byKey = left.partition.key.localeCompare(right.partition.key, "en", { numeric: true, sensitivity: "base" });
      if (byKey !== 0) return byKey;
      return left.index - right.index;
    })
    .map((item) => item.partition);
}

function windowOf(ordered: readonly HeatStripPartition[]): HeatStripPartition[] {
  if (ordered.length <= HEAT_STRIP_WINDOW) return [...ordered];
  const firstTouch = ordered.findIndex((partition) => partition.nextRun);
  const start = firstTouch < 0 ? 0 : Math.min(firstTouch, ordered.length - HEAT_STRIP_WINDOW);
  return ordered.slice(start, start + HEAT_STRIP_WINDOW);
}

function pressureOf(partition: HeatStripPartition): number | null {
  const small = measured(partition.smallFiles);
  const files = measured(partition.files);
  if (small == null || files == null) return null;
  if (files === 0) return small === 0 ? 0 : null;
  return Math.min(1, small / files);
}

function cellTitle(partition: HeatStripPartition): string {
  const files = measured(partition.files);
  const small = measured(partition.smallFiles);
  const compaction =
    recordedEpoch(partition.lastCompactionAt) == null ? "Not recorded" : (partition.lastCompactionAt ?? "").trim();
  return `${partition.key} · files ${files == null ? "Not measured" : String(files)} · small files ${
    small == null ? "Not measured" : String(small)
  } · last compaction ${compaction}`;
}

function windowCaption(shown: readonly HeatStripPartition[], total: number): string {
  if (shown.length >= total) return `${total} partitions in key order.`;
  const start = shown[0]?.key ?? "";
  return `Window of ${shown.length} of ${total} partitions in key order, starting at ${start}, not the full set.`;
}

function cellFill(pressure: number): string {
  const mix = Math.round(12 + pressure * 88);
  return `color-mix(in srgb, var(--color-chart-primary) ${mix}%, var(--color-primary-soft))`;
}

function HeatStripFigure({
  shown,
  total,
  caption,
  nextRunOutside
}: {
  shown: readonly HeatStripPartition[];
  total: number;
  caption: string;
  nextRunOutside: boolean;
}) {
  const motion = useChartMotion();
  const queries = shown.map((partition) => measured(partition.queries)).filter((value): value is number => value != null);
  const maxQueries = queries.length ? Math.max(...queries) : 0;
  const unmeasured = shown.some((partition) => pressureOf(partition) == null || measured(partition.queries) == null);

  return (
    <div
      data-testid="heat-strip"
      data-rendered={String(shown.length)}
      data-total={String(total)}
      data-animation-duration={String(motion.durationMs)}
    >
      <p data-testid="heatstrip-caption" className="mb-2 text-xs text-muted-foreground">
        {caption}
      </p>
      <div
        className="grid gap-px"
        style={{ gridTemplateColumns: `repeat(${Math.max(shown.length, 1)}, minmax(0, 1fr))` }}
      >
        {shown.map((partition) => {
          const pressure = pressureOf(partition);
          const title = cellTitle(partition);
          const nextRun = partition.nextRun === true;
          return (
            <button
              key={partition.id}
              type="button"
              data-testid="heat-cell"
              data-key={partition.key}
              data-pressure={pressure == null ? "unmeasured" : String(pressure)}
              data-next-run={nextRun ? "true" : "false"}
              title={title}
              aria-label={nextRun ? `${title}. next run touches these` : title}
              className="h-4 min-w-0 rounded-sm border border-border p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]"
              style={{
                backgroundColor: pressure == null ? undefined : cellFill(pressure),
                backgroundImage:
                  pressure == null
                    ? "repeating-linear-gradient(-45deg, transparent, transparent 2px, var(--color-muted) 2px, var(--color-muted) 3px)"
                    : undefined,
                boxShadow: nextRun ? "inset 0 0 0 1px var(--color-foreground)" : undefined
              }}
            />
          );
        })}
      </div>
      <div
        data-testid="heat-query-row"
        className="mt-1 grid h-3 gap-px"
        style={{ gridTemplateColumns: `repeat(${Math.max(shown.length, 1)}, minmax(0, 1fr))` }}
      >
        {shown.map((partition) => {
          const count = measured(partition.queries);
          if (count == null) {
            return (
              <span
                key={partition.id}
                data-testid="heat-query-tick"
                data-queries="unmeasured"
                title="Not measured"
                className="block h-2 border border-dashed border-[var(--color-muted)]"
              />
            );
          }
          const height = maxQueries <= 0 ? 0 : (count / maxQueries) * 100;
          return (
            <span key={partition.id} className="relative block h-3" title={count === 0 ? "0 queries" : `${count} queries`}>
              <span
                data-testid="heat-query-tick"
                data-queries={String(count)}
                className="absolute bottom-0 left-0 right-0 bg-[var(--color-chart-primary)]"
                style={{ height: `${height}%` }}
              />
            </span>
          );
        })}
      </div>
      <p data-testid="heat-legend" className="mt-2 text-[10px] text-muted-foreground">
        small-file pressure
        <span
          aria-hidden="true"
          className="ml-1 inline-block h-2 w-6 align-middle"
          style={{ backgroundColor: cellFill(0) }}
        />
        <span
          aria-hidden="true"
          className="inline-block h-2 w-6 align-middle"
          style={{ backgroundColor: cellFill(1) }}
        />
        <span className="ml-3">query ticks{shown.length < total ? " scaled to this window" : ""}</span>
        <span className="ml-3">next run touches these</span>
      </p>
      {unmeasured ? <p className="mt-1 text-xs text-muted-foreground">Not measured</p> : null}
      {nextRunOutside ? (
        <p className="mt-1 text-xs text-muted-foreground">Some partitions the next run touches are outside this window.</p>
      ) : null}
    </div>
  );
}

const TABLE_COLUMNS: PlottedColumn[] = [
  { key: "key", header: "Partition" },
  { key: "files", header: "Files", align: "right" },
  { key: "smallFiles", header: "Small files", align: "right" },
  { key: "queries", header: "Queries", align: "right" },
  { key: "lastCompaction", header: "Last compaction" },
  { key: "nextRun", header: "Next run" }
];

function tableValue(value: number | null): number | string {
  return value == null ? "Not measured" : value;
}

export function HeatStrip({ partitions, unpartitioned = false }: HeatStripProps) {
  if (unpartitioned) {
    return (
      <ChartCard
        title="Which partitions are under pressure?"
        provenance="measured"
        table={[{ id: "layout", layout: "Unpartitioned table" }]}
        columns={[{ key: "layout", header: "Layout" }]}
      >
        <p>Unpartitioned table</p>
      </ChartCard>
    );
  }

  const ordered = keyOrder(partitions);
  const shown = windowOf(ordered);
  const shownIds = new Set(shown.map((partition) => partition.id));
  const nextRunOutside = ordered.some((partition) => partition.nextRun === true && !shownIds.has(partition.id));
  const caption = windowCaption(shown, ordered.length);
  const rows: PlottedRow[] = shown.map((partition) => ({
    id: partition.id,
    key: partition.key,
    files: tableValue(measured(partition.files)),
    smallFiles: tableValue(measured(partition.smallFiles)),
    queries: tableValue(measured(partition.queries)),
    lastCompaction: recordedEpoch(partition.lastCompactionAt) == null ? "Not recorded" : (partition.lastCompactionAt ?? "").trim(),
    nextRun: partition.nextRun ? "yes" : "no"
  }));

  return (
    <ChartCard
      title="Which partitions are under pressure?"
      subtitle={{ unit: "small-file pressure" }}
      provenance="measured"
      table={rows}
      columns={TABLE_COLUMNS}
    >
      <HeatStripFigure shown={shown} total={ordered.length} caption={caption} nextRunOutside={nextRunOutside} />
    </ChartCard>
  );
}
