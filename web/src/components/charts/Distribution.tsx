import { ChartCard, useChartMotion } from "./ChartCard";
import { plotValue } from "./plotValue";
import { toEpochMs } from "./timeScale";
import type { PlottedColumn, PlottedRow } from "./types";

export interface Log2Bin {
  lowerBytes: number;
  count: number | null;
}

export interface KindDistribution {
  bins: readonly Log2Bin[];
  unknown: number | null;
}

export interface DistributionScan {
  scannedAt: string | null;
  fileCount?: number | null;
  dataFiles: KindDistribution;
  deleteFiles: KindDistribution;
}

export interface DistributionProps {
  scan: DistributionScan;
  before?: DistributionScan | null;
  writerTargetBytes?: number | null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

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

function binExponent(lowerBytes: number): number {
  if (!(lowerBytes > 0)) return -1;
  return Math.log2(lowerBytes);
}

function formatLog2Bytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  if (!Number.isFinite(bytes) || bytes < 0) return "Not measured";
  const shift = Math.min(40, Math.max(0, Math.floor(Math.log2(bytes) / 10) * 10));
  const unit = shift === 40 ? "TiB" : shift === 30 ? "GiB" : shift === 20 ? "MiB" : shift === 10 ? "KiB" : "B";
  const value = bytes / 2 ** shift;
  const rendered = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return `${rendered} ${unit}`;
}

function formatScanTime(value: string | null): string {
  const epoch = recordedEpoch(value);
  if (epoch == null) return "Not recorded";
  const date = new Date(epoch);
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

function sumKind(kind: KindDistribution): number | null {
  const unknown = measured(kind.unknown);
  if (unknown == null) return null;
  let total = unknown;
  for (const bin of kind.bins) {
    const count = measured(bin.count);
    if (count == null) return null;
    total += count;
  }
  return total;
}

function fileCountOf(scan: DistributionScan): number | null {
  const provided = measured(scan.fileCount);
  if (scan.fileCount != null) return provided;
  const data = sumKind(scan.dataFiles);
  const deletes = sumKind(scan.deleteFiles);
  if (data == null || deletes == null) return null;
  return data + deletes;
}

function lookup(kind: KindDistribution, lowerBytes: number): number | null {
  const found = kind.bins.find((bin) => bin.lowerBytes === lowerBytes);
  if (!found) return null;
  return measured(found.count);
}

function sharedLowers(scan: DistributionScan, before: DistributionScan | null): number[] {
  const lowers = new Set<number>();
  for (const source of [scan, before]) {
    if (!source) continue;
    for (const kind of [source.dataFiles, source.deleteFiles]) {
      for (const bin of kind.bins) {
        if (Number.isFinite(bin.lowerBytes) && bin.lowerBytes >= 0) lowers.add(bin.lowerBytes);
      }
    }
  }
  return [...lowers].sort((left, right) => left - right);
}

function unknownLine(label: string, count: number | null): string | null {
  if (count === 0) return null;
  if (count == null) return `${label}: Not measured`;
  return `${label}: Not measured · ${count}`;
}

function axisSpan(lowers: readonly number[], targetBytes: number | null): { start: number; end: number } | null {
  const exponents = lowers.map(binExponent).filter((value) => Number.isFinite(value));
  const target = measured(targetBytes);
  const targetExp = target != null && target > 0 ? Math.log2(target) : null;
  if (!exponents.length) return null;
  const start = Math.min(...exponents);
  let end = Math.max(...exponents.map((exponent) => exponent + 1));
  if (targetExp != null) end = Math.max(end, targetExp);
  if (targetExp != null && end - targetExp < 0.25) end += 0.5;
  if (end <= start) end = start + 1;
  return { start, end };
}

function percentAlong(exponent: number, span: { start: number; end: number }): number {
  const width = span.end - span.start;
  if (width <= 0) return 50;
  return Math.min(100, Math.max(0, ((exponent - span.start) / width) * 100));
}

function countCell(count: number | null): number | string {
  return count == null ? "Not measured" : count;
}

function DistributionBars({
  kind,
  columns,
  showBefore,
  max,
  span
}: {
  kind: "data" | "delete";
  columns: ReadonlyArray<{ lowerBytes: number; before: number | null; after: number | null }>;
  showBefore: boolean;
  max: number;
  span: { start: number; end: number };
}) {
  return (
    <div className="relative h-24" data-baseline="zero" data-unit="files">
      {columns.map((column) => {
        const left = percentAlong(binExponent(column.lowerBytes), span);
        const width = 100 / (span.end - span.start);
        return (
          <div
            key={column.lowerBytes}
            className="absolute bottom-0 flex items-end justify-center gap-0.5"
            style={{ left: `${left}%`, width: `${width}%`, height: "100%" }}
          >
            {showBefore ? (
              <BinBar kind={kind} series="before" count={column.before} max={max} lowerBytes={column.lowerBytes} />
            ) : null}
            <BinBar kind={kind} series="after" count={column.after} max={max} lowerBytes={column.lowerBytes} />
          </div>
        );
      })}
    </div>
  );
}

function BinBar({
  kind,
  series,
  count,
  max,
  lowerBytes
}: {
  kind: "data" | "delete";
  series: "before" | "after";
  count: number | null;
  max: number;
  lowerBytes: number;
}) {
  const plotted = plotValue(count);
  if (plotted == null || plotted < 0) {
    return (
      <span
        data-testid="distribution-unmeasured"
        data-kind={kind}
        data-series={series}
        data-value="unmeasured"
        data-lower={String(lowerBytes)}
        className="text-[10px] leading-none text-muted-foreground"
      >
        Not measured
      </span>
    );
  }
  const height = max <= 0 ? 0 : (plotted / max) * 100;
  return (
    <span
      data-testid="distribution-bar"
      data-kind={kind}
      data-series={series}
      data-value={String(plotted)}
      data-lower={String(lowerBytes)}
      className={
        series === "before"
          ? "block w-1.5 border border-[var(--color-chart-primary)] bg-transparent"
          : "block w-1.5 bg-[var(--color-chart-primary)]"
      }
      style={{ height: `${height}%` }}
    />
  );
}

function DistributionFigure({
  columns,
  showBefore,
  span,
  target,
  unknownText,
  caption
}: {
  columns: ReadonlyArray<{
    lowerBytes: number;
    dataBefore: number | null;
    dataAfter: number | null;
    deleteBefore: number | null;
    deleteAfter: number | null;
  }>;
  showBefore: boolean;
  span: { start: number; end: number } | null;
  target: { label: string; x: number } | null;
  unknownText: string | null;
  caption: string;
}) {
  const motion = useChartMotion();
  const dataMax = Math.max(
    0,
    ...columns.flatMap((column) => [column.dataAfter, showBefore ? column.dataBefore : null].filter((value): value is number => value != null))
  );
  const deleteMax = Math.max(
    0,
    ...columns.flatMap((column) =>
      [column.deleteAfter, showBefore ? column.deleteBefore : null].filter((value): value is number => value != null)
    )
  );

  return (
    <div data-animation-duration={String(motion.durationMs)} data-reduced-motion={motion.reducedMotion ? "true" : "false"}>
      {span && columns.length ? (
        <>
          <p className="mb-2 text-[10px] text-muted-foreground">files</p>
          <section data-testid="distribution-plot" data-kind="data" aria-label="Data files">
            <p className="mb-1 text-xs text-foreground">Data files</p>
            <DistributionBars
              kind="data"
              showBefore={showBefore}
              max={dataMax}
              span={span}
              columns={columns.map((column) => ({
                lowerBytes: column.lowerBytes,
                before: column.dataBefore,
                after: column.dataAfter
              }))}
            />
          </section>
          <section data-testid="distribution-plot" data-kind="delete" aria-label="Delete files" className="mt-3">
            <p className="mb-1 text-xs text-foreground">Delete files</p>
            <DistributionBars
              kind="delete"
              showBefore={showBefore}
              max={deleteMax}
              span={span}
              columns={columns.map((column) => ({
                lowerBytes: column.lowerBytes,
                before: column.deleteBefore,
                after: column.deleteAfter
              }))}
            />
          </section>
          <div data-testid="distribution-x-axis" data-axis="x" className="relative mt-1 h-10">
            {columns.map((column) => {
              const left = percentAlong(binExponent(column.lowerBytes) + 0.5, span);
              return (
                <span
                  key={column.lowerBytes}
                  data-testid="distribution-bin-label"
                  className="absolute bottom-0 -translate-x-1/2 whitespace-nowrap text-[10px] text-muted-foreground"
                  style={{ left: `${left}%` }}
                >
                  {formatLog2Bytes(column.lowerBytes)}
                </span>
              );
            })}
            {target ? (
              <span
                data-testid="writer-target-tick"
                data-axis="x"
                data-x={target.x.toFixed(2)}
                className="absolute top-0 flex -translate-x-1/2 flex-col items-center"
                style={{ left: `${target.x}%` }}
              >
                <span aria-hidden="true" className="h-2 border-l border-dashed border-[var(--color-border-strong)]" />
                <span className="whitespace-nowrap text-[10px] text-foreground">target {target.label}</span>
              </span>
            ) : null}
          </div>
        </>
      ) : null}
      {showBefore ? (
        <p data-testid="distribution-legend" className="mt-2 text-[10px] text-muted-foreground">
          <span className="mr-2 inline-block w-2 border border-[var(--color-chart-primary)] align-middle" />
          before
          <span className="ml-3 mr-2 inline-block h-2 w-2 bg-[var(--color-chart-primary)] align-middle" />
          after
        </p>
      ) : null}
      {unknownText ? (
        <p data-testid="distribution-unknown" className="mt-2 text-xs text-muted-foreground">
          {unknownText}
        </p>
      ) : null}
      <p data-testid="distribution-caption" className="mt-1 text-xs text-muted-foreground">
        {caption}
      </p>
    </div>
  );
}

export function Distribution({ scan, before = null, writerTargetBytes = null }: DistributionProps) {
  const showBefore = before != null;
  const lowers = sharedLowers(scan, before);
  const span = axisSpan(lowers, writerTargetBytes ?? null);
  const columns = lowers.map((lowerBytes) => ({
    lowerBytes,
    dataBefore: showBefore && before ? lookup(before.dataFiles, lowerBytes) : null,
    dataAfter: lookup(scan.dataFiles, lowerBytes),
    deleteBefore: showBefore && before ? lookup(before.deleteFiles, lowerBytes) : null,
    deleteAfter: lookup(scan.deleteFiles, lowerBytes)
  }));
  const targetBytes = measured(writerTargetBytes);
  const targetLabel = targetBytes != null && targetBytes > 0 ? formatLog2Bytes(targetBytes) : null;
  const target =
    span && targetLabel != null && targetBytes != null
      ? { label: targetLabel, x: percentAlong(Math.log2(targetBytes), span) }
      : null;
  const unknownText = [unknownLine("Data files", measured(scan.dataFiles.unknown)), unknownLine("Delete files", measured(scan.deleteFiles.unknown))]
    .filter((line): line is string => line != null)
    .join(" · ");
  const count = fileCountOf(scan);
  const caption = [
    count == null ? "File count not measured" : `${count} files`,
    `scanned ${formatScanTime(scan.scannedAt)}`,
    showBefore && before ? `before ${formatScanTime(before.scannedAt)}` : null,
    targetLabel ? `writer target ${targetLabel}` : "writer target Not measured"
  ]
    .filter((part): part is string => part != null)
    .join(" · ");
  const rows: PlottedRow[] = columns.map((column) => ({
    id: `bin-${column.lowerBytes}`,
    bin: formatLog2Bytes(column.lowerBytes),
    ...(showBefore ? { dataBefore: countCell(column.dataBefore), deleteBefore: countCell(column.deleteBefore) } : {}),
    dataAfter: countCell(column.dataAfter),
    deleteAfter: countCell(column.deleteAfter)
  }));
  if (unknownText) {
    rows.push({
      id: "unknown",
      bin: "Not measured",
      ...(showBefore ? { dataBefore: "Not measured", deleteBefore: "Not measured" } : {}),
      dataAfter: countCell(measured(scan.dataFiles.unknown)),
      deleteAfter: countCell(measured(scan.deleteFiles.unknown))
    });
  }
  const columnsForTable: PlottedColumn[] = [
    { key: "bin", header: "Bin" },
    ...(showBefore
      ? [
          { key: "dataBefore", header: "Data before", align: "right" as const },
          { key: "deleteBefore", header: "Delete before", align: "right" as const }
        ]
      : []),
    { key: "dataAfter", header: "Data after", align: "right" as const },
    { key: "deleteAfter", header: "Delete after", align: "right" as const }
  ];
  const scanned = recordedEpoch(scan.scannedAt);

  return (
    <ChartCard
      title="How are file sizes distributed?"
      subtitle={{ unit: "files", asOf: scanned == null ? undefined : formatScanTime(scan.scannedAt), timeZone: "UTC" }}
      provenance="measured"
      table={rows}
      columns={columnsForTable}
    >
      <DistributionFigure
        columns={columns}
        showBefore={showBefore}
        span={span}
        target={target}
        unknownText={unknownText || null}
        caption={caption}
      />
    </ChartCard>
  );
}
