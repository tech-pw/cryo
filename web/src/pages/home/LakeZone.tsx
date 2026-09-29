import { Link } from "react-router-dom";
import { MetricGrid, MetricTile } from "../../components/ui/metric-grid";
import { Skeleton } from "../../components/ui/skeleton";
import { formatBytesGb, formatGb, formatNumber } from "../../lib/format";

export interface GradeCount {
  grade: string;
  count: number;
}

export interface LakeZoneProps {
  tracked: number | null;
  distribution: GradeCount[] | null;
  loading: boolean;
  error: boolean;
  sizeGb: number | null;
  smallFiles: number | null;
  needsCompaction: number | null;
  reclaimableBytes: number | null;
  previousDf: number | null;
  hotDf: number | null;
}

const NOT_MEASURED = "Not measured";
const MINUS = "\u2212";
const GRADE_ORDER = ["F", "D", "C", "B", "A"] as const;
const GRADE_SET = new Set<string>(GRADE_ORDER);

const DATA_SIZE_TITLE = "Combined size of every table Cryo is tracking, across all schemas.";
const SMALL_FILES_TITLE = "Files under the target size threshold — too many of these slows down queries.";
const NEEDS_COMPACTION_TITLE =
  "Active tables with TableStats.needs_compaction true; not a small-file threshold, and not the catalogs sum of that flag over enabled sources.";
const RECLAIMABLE_TITLE =
  "Dead/expired snapshots, orphan files, and metadata bloat accumulated across each table's full unexpired history.";

const finite = (value: number | null): value is number => value != null && Number.isFinite(value);

const gradeColor = (grade: string): string => {
  const letter = grade.toLowerCase();
  if (letter === "a" || letter === "b" || letter === "c" || letter === "d" || letter === "f") {
    return `var(--grade-${letter})`;
  }
  return "var(--color-muted-subtle)";
};

const tablesAtGradeDf = (distribution: GradeCount[]): number | null => {
  let total = 0;
  for (const entry of distribution) {
    if (entry.grade !== "D" && entry.grade !== "F") continue;
    if (!Number.isFinite(entry.count)) return null;
    total += entry.count;
  }
  return total;
};

const formatCompact = (value: number): string => formatNumber(value, true);

const formatChipCount = (value: number): string => formatNumber(Math.abs(value), Math.abs(value) >= 10_000);

type Sentence =
  | { kind: "unknown"; trackedLabel: string | null }
  | { kind: "none" }
  | { kind: "count"; count: number; tracked: number | null };

const resolveSentence = (distribution: GradeCount[] | null, tracked: number | null): Sentence => {
  const trackedKnown = finite(tracked) ? tracked : null;
  const trackedLabel = trackedKnown == null ? null : formatCompact(trackedKnown);
  if (distribution == null) return { kind: "unknown", trackedLabel };
  if (distribution.length === 0) {
    if (trackedKnown == null) return { kind: "unknown", trackedLabel: null };
    if (trackedKnown === 0) return { kind: "none" };
  }
  const count = tablesAtGradeDf(distribution);
  if (count == null) return { kind: "unknown", trackedLabel };
  return { kind: "count", count, tracked: trackedKnown };
};

type Segment = { key: string; label: string; count: number; color: string };

const gradeSegments = (distribution: GradeCount[], tracked: number | null): Segment[] => {
  const totals = new Map<string, number>();
  const otherOrder: string[] = [];
  for (const entry of distribution) {
    if (!Number.isFinite(entry.count) || entry.count <= 0) continue;
    totals.set(entry.grade, (totals.get(entry.grade) ?? 0) + entry.count);
    if (!GRADE_SET.has(entry.grade) && !otherOrder.includes(entry.grade)) otherOrder.push(entry.grade);
  }
  const segments: Segment[] = [
    ...GRADE_ORDER.flatMap((grade) => {
      const count = totals.get(grade) ?? 0;
      if (count <= 0) return [];
      return [{ key: grade, label: grade, count, color: gradeColor(grade) }];
    }),
    ...otherOrder.map((grade) => ({
      key: grade,
      label: grade,
      count: totals.get(grade) ?? 0,
      color: "var(--color-muted-subtle)"
    }))
  ];
  if (finite(tracked)) {
    const graded = distribution.reduce((sum, entry) => sum + (Number.isFinite(entry.count) ? entry.count : 0), 0);
    const notGraded = Math.max(0, tracked - graded);
    if (notGraded > 0) {
      segments.push({
        key: "not-graded",
        label: "Not graded",
        count: notGraded,
        color: "var(--color-muted-subtle)"
      });
    }
  }
  return segments;
};

const measured = (value: number | null, format: (value: number) => string): { text: string; missing: boolean } => {
  if (!finite(value)) return { text: NOT_MEASURED, missing: true };
  return { text: format(value), missing: false };
};

const CountSentence = ({ count, tracked }: { count: number; tracked: number | null }) => {
  const linked = count > 0;
  const rest = tracked == null ? " tables at D or F" : ` of ${formatCompact(tracked)} tables at D or F`;
  const body = (
    <>
      <span
        className={
          linked
            ? "font-semibold text-foreground tabular-nums underline decoration-[var(--color-link)] decoration-1 underline-offset-[3px]"
            : "font-semibold text-foreground tabular-nums"
        }
      >
        {formatCompact(count)}
      </span>
      {rest}
    </>
  );
  if (!linked) return <span className="font-normal text-muted-foreground">{body}</span>;
  return (
    <Link
      to="/catalogs?grades=D,F"
      className="font-normal text-muted-foreground no-underline hover:text-muted-foreground hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--focus-ring-color)]"
    >
      {body}
    </Link>
  );
};

const pieSlice = (cx: number, cy: number, r: number, start: number, end: number): string => {
  const sweep = end - start;
  if (sweep >= 359.99) {
    return `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx - 0.01} ${cy - r} Z`;
  }
  const rad = (angle: number) => ((angle - 90) * Math.PI) / 180;
  const x1 = cx + r * Math.cos(rad(start));
  const y1 = cy + r * Math.sin(rad(start));
  const x2 = cx + r * Math.cos(rad(end));
  const y2 = cy + r * Math.sin(rad(end));
  const large = sweep > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
};

const GradePie = ({ segments }: { segments: Segment[] }) => {
  const total = segments.reduce((sum, segment) => sum + segment.count, 0);
  if (total <= 0) return null;
  let cursor = 0;
  const slices = segments.map((segment) => {
    const start = (cursor / total) * 360;
    cursor += segment.count;
    const end = (cursor / total) * 360;
    return { ...segment, start, end };
  });
  return (
    <div className="mt-3 flex flex-wrap items-center gap-4">
      <svg viewBox="0 0 120 120" className="h-[140px] w-[140px] shrink-0" role="img" aria-label="Grade distribution">
        {slices.map((slice) => (
          <path key={slice.key} d={pieSlice(60, 60, 52, slice.start, slice.end)} fill={slice.color}>
            <title>{`${slice.label}: ${slice.count.toLocaleString()}`}</title>
          </path>
        ))}
      </svg>
      <ul aria-label="Grade distribution" className="flex list-none flex-col gap-1 p-0 text-xs text-muted-foreground tabular-nums">
        {segments.map((segment) => (
          <li key={segment.key} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: segment.color }} aria-hidden="true" />
            {segment.label} {segment.count.toLocaleString()}
          </li>
        ))}
      </ul>
    </div>
  );
};

const LakeZoneSkeleton = () => (
  <section aria-label="Lake" aria-busy="true" className="mb-6 font-sans">
    <Skeleton className="h-[22px] w-64" />
    <Skeleton className="mt-2 h-2 w-full" />
    <Skeleton className="mt-2 h-4 w-72" />
    <Skeleton className="mt-3 h-5 w-full max-w-3xl" />
  </section>
);

export const LakeZone = ({
  tracked,
  distribution,
  loading,
  error,
  sizeGb,
  smallFiles,
  needsCompaction,
  reclaimableBytes,
  previousDf,
  hotDf
}: LakeZoneProps) => {
  if (error) return null;
  if (loading) return <LakeZoneSkeleton />;

  const sentence = resolveSentence(distribution, tracked);
  const segments = sentence.kind === "count" && distribution ? gradeSegments(distribution, sentence.tracked) : [];
  const barTotal = segments.reduce((sum, segment) => sum + segment.count, 0);
  const delta =
    sentence.kind === "count" && finite(previousDf) ? sentence.count - previousDf : null;
  const showDelta = delta != null && delta !== 0;
  const showHot = finite(hotDf);
  const size = measured(sizeGb, formatGb);
  const files = measured(smallFiles, (value) => formatNumber(value, true));
  const compaction = measured(needsCompaction, (value) => formatNumber(value));
  const reclaimable = measured(reclaimableBytes, formatBytesGb);
  const compactionHref = finite(needsCompaction) && needsCompaction > 0 ? "/catalogs?needsCompaction=1" : undefined;

  return (
    <section aria-label="Lake" className="mb-6 font-sans">
      <div className="flex min-h-[26px] flex-wrap items-baseline gap-2 text-[14px] leading-[22px]">
        {sentence.kind === "unknown" ? (
          <span className="font-normal text-muted-foreground">
            Grade split unknown
            {sentence.trackedLabel ? ` · ${sentence.trackedLabel} tracked` : null}
          </span>
        ) : null}
        {sentence.kind === "none" ? (
          <span className="font-normal text-muted-foreground">No tracked tables</span>
        ) : null}
        {sentence.kind === "count" ? <CountSentence count={sentence.count} tracked={sentence.tracked} /> : null}
        {showDelta && delta != null ? (
          <span
            className={
              delta > 0
                ? "inline-flex h-5 items-center whitespace-nowrap rounded-full border border-[var(--color-border-default)] bg-transparent px-1.5 text-[11px] font-medium leading-4 tabular-nums text-[var(--color-status-danger)]"
                : "inline-flex h-5 items-center whitespace-nowrap rounded-full border border-[var(--color-border-default)] bg-transparent px-1.5 text-[11px] font-medium leading-4 tabular-nums text-[var(--color-status-success)]"
            }
          >
            {delta > 0 ? `+${formatChipCount(delta)} since yesterday` : `${MINUS}${formatChipCount(delta)} since yesterday`}
          </span>
        ) : null}
        {showHot ? (
          <span className="whitespace-nowrap text-[11px] font-medium leading-4 text-muted-foreground tabular-nums">
            {formatChipCount(hotDf)} hot
          </span>
        ) : null}
      </div>
      {barTotal > 0 ? <GradePie segments={segments} /> : null}
      <MetricGrid columns={4} className="mt-3">
        <MetricTile label="Data size" value={size.text} hint={<span title={DATA_SIZE_TITLE}>ⓘ</span>} />
        <MetricTile label="Small files" value={files.text} hint={<span title={SMALL_FILES_TITLE}>ⓘ</span>} />
        <MetricTile
          label="Needs compaction"
          value={compaction.text}
          emphasis={Boolean(compactionHref)}
          hint={
            compactionHref ? (
              <Link to={compactionHref} className="text-xs text-primary hover:underline">
                Fix in catalog
              </Link>
            ) : (
              <span title={NEEDS_COMPACTION_TITLE}>ⓘ</span>
            )
          }
        />
        <MetricTile label="Reclaimable now" value={reclaimable.text} hint={<span title={RECLAIMABLE_TITLE}>ⓘ</span>} />
      </MetricGrid>
    </section>
  );
};
