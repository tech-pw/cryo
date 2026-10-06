import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";

import { QueryErrorAlert } from "../../components/common";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/cn";
import { formatTime } from "../../lib/format";

export interface Discovery {
  table_id: string;
  table_name: string;
  severity: string;
  finding_type: string;
  reason: string;
  detected_at: string;
}

const DESCRIPTION = "Fast-path flags for new or barely-scanned tables. These are not auto-compacted.";
const PREVIEW_LIMIT = 5;
const STALL_MS = 5000;

const severityRank = (severity: string): number => {
  const normalized = severity.trim().toLowerCase();
  if (normalized === "critical" || normalized === "error") return 0;
  if (normalized === "warning") return 1;
  if (normalized === "info") return 2;
  return 3;
};

const severityWord = (severity: string): string => {
  const normalized = severity.trim().toLowerCase();
  if (normalized === "critical" || normalized === "error") return "Critical";
  if (normalized === "warning") return "Warning";
  if (normalized === "info") return "Info";
  if (!normalized) return "Info";
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
};

const severityDotClass = (severity: string): string => {
  const normalized = severity.trim().toLowerCase();
  if (normalized === "critical" || normalized === "error") return "bg-[var(--color-status-danger)]";
  if (normalized === "warning") return "bg-[var(--color-status-warning)]";
  return "bg-[var(--color-muted-subtle)]";
};

const typeLabel = (findingType: string): string => {
  if (findingType === "small_files") return "Small files";
  if (findingType === "missing_target_file_size") return "No target size";
  return findingType.replaceAll("_", " ");
};

const detectedMs = (value: string): number => {
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms;
};

const formatAge = (value: string, now = Date.now()): string => {
  const time = Date.parse(value);
  if (Number.isNaN(time)) return "—";
  const delta = now - time;
  if (delta < 60_000) return "now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h`;
  return `${Math.floor(delta / 86_400_000)}d`;
};

const tooltipText = (finding: Discovery): string => {
  const lines = [finding.table_name, finding.reason, formatTime(finding.detected_at)];
  if (finding.finding_type === "missing_target_file_size") lines.push("Open property recommendations");
  return lines.join("\n");
};

const sortFindings = (findings: Discovery[]): Discovery[] =>
  findings
    .map((finding, index) => ({ finding, index }))
    .sort((left, right) => {
      const rank = severityRank(left.finding.severity) - severityRank(right.finding.severity);
      if (rank !== 0) return rank;
      const time = detectedMs(right.finding.detected_at) - detectedMs(left.finding.detected_at);
      if (time > 0) return 1;
      if (time < 0) return -1;
      return left.index - right.index;
    })
    .map(({ finding }) => finding);

const rowKey = (finding: Discovery, index: number): string =>
  `${finding.table_id}-${finding.finding_type}-${finding.detected_at}-${index}`;

const FindingRow = ({ finding, reasonId }: { finding: Discovery; reasonId: string }) => {
  const [focused, setFocused] = useState(false);
  const tooltip = tooltipText(finding);
  const missingTarget = finding.finding_type === "missing_target_file_size";
  const label = typeLabel(finding.finding_type);
  return (
    <li
      className={cn(
        "relative min-h-8 border-b border-border last:border-b-0 hover:bg-[var(--color-panel)]",
        focused && "z-20"
      )}
    >
      <div className="grid min-h-8 grid-cols-[minmax(0,1fr)_88px_120px_36px] items-center gap-x-4 whitespace-nowrap px-4 max-sm:grid-cols-[minmax(0,1fr)_88px] max-sm:gap-y-1 max-sm:py-2">
        <div className="relative min-w-0">
          <Link
            to={`/tables/${finding.table_id}`}
            title={tooltip}
            aria-describedby={reasonId}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            className="block truncate font-mono text-sm font-medium text-[var(--color-link)] hover:underline"
          >
            {finding.table_name}
          </Link>
          {focused ? (
            <span
              aria-hidden="true"
              className="absolute left-0 top-full z-20 mt-1 max-w-[320px] whitespace-normal rounded-[var(--radius-sm)] border border-border bg-popover px-3 py-1.5 font-sans text-xs font-normal text-popover-foreground shadow-sm"
            >
              <span className="block font-mono">{finding.table_name}</span>
              <span className="block">{finding.reason}</span>
              <span className="block">{formatTime(finding.detected_at)}</span>
              {missingTarget ? <span className="block">Open property recommendations</span> : null}
            </span>
          ) : null}
        </div>
        <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          <span aria-hidden="true" className={cn("h-2 w-2 shrink-0 rounded-full", severityDotClass(finding.severity))} />
          {severityWord(finding.severity)}
        </span>
        {missingTarget ? (
          <Link
            to={`/tables/${finding.table_id}?tab=files#files-properties`}
            className="block min-w-0 truncate whitespace-nowrap text-sm text-[var(--color-link)] hover:underline"
            aria-label={`Open property recommendations for ${finding.table_name}`}
          >
            No target size
          </Link>
        ) : (
          <span className="block min-w-0 truncate text-sm text-[var(--color-muted)]" title={label}>
            {label}
          </span>
        )}
        <time
          dateTime={finding.detected_at}
          className="text-right text-xs font-medium leading-4 tabular-nums text-[var(--color-muted)]"
        >
          {formatAge(finding.detected_at)}
        </time>
      </div>
    </li>
  );
};

const FindingList = ({
  findings,
  idPrefix,
  label,
  className
}: {
  findings: Discovery[];
  idPrefix: string;
  label: string;
  className?: string;
}) => (
  <>
    <ul aria-label={label} className={cn("m-0 list-none p-0", className)}>
      {findings.map((finding, index) => (
        <FindingRow key={rowKey(finding, index)} finding={finding} reasonId={`${idPrefix}-${index}`} />
      ))}
    </ul>
    <div className="sr-only">
      {findings.map((finding, index) => (
        <span key={rowKey(finding, index)} id={`${idPrefix}-${index}`}>
          {tooltipText(finding)}
        </span>
      ))}
    </div>
  </>
);

const LoadingRows = () => {
  const [stalled, setStalled] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setStalled(true), STALL_MS);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <div aria-busy="true" aria-live="polite" aria-label="Loading">
      {Array.from({ length: PREVIEW_LIMIT }, (_, index) => (
        <Skeleton key={index} className={cn("h-8 w-full rounded-none", stalled && "animate-none")} />
      ))}
      {stalled ? <p className="px-4 py-2 text-sm text-[var(--color-muted)]">Still loading…</p> : null}
    </div>
  );
};

const Count = ({ count }: { count: number }) => (
  <>
    {" "}
    <span className="font-medium tabular-nums text-[var(--color-muted)]">· {count}</span>
  </>
);

export const DiscoveredList = ({
  findings,
  loading,
  error
}: {
  findings: Discovery[];
  loading: boolean;
  error: unknown;
}) => {
  const baseId = useId();
  const headingId = `${baseId}-heading`;
  const [open, setOpen] = useState(false);
  const failed = Boolean(error);
  const sorted = sortFindings(findings);
  const count = sorted.length;
  const showCount = !loading && !failed;
  const preview = sorted.slice(0, PREVIEW_LIMIT);

  return (
    <section className="font-sans text-foreground" aria-labelledby={headingId}>
      <span className="sr-only">{DESCRIPTION}</span>
      <div className="flex h-7 items-center justify-between gap-4 border-b border-border px-4">
        <h2 id={headingId} className="font-sans text-sm font-semibold leading-5 text-foreground">
          Recently discovered
          {showCount ? <Count count={count} /> : null}
        </h2>
        {showCount && count > PREVIEW_LIMIT ? (
          <button
            type="button"
            className="shrink-0 border-0 bg-transparent p-0 font-sans text-sm font-medium leading-5 text-[var(--color-link)] hover:underline"
            onClick={() => setOpen(true)}
          >
            Show all
          </button>
        ) : null}
      </div>
      {failed ? (
        <div className="px-4 py-3">
          <QueryErrorAlert error={error} title="Failed to load fast-path findings" />
        </div>
      ) : loading ? (
        <LoadingRows />
      ) : count === 0 ? (
        <div className="px-4 py-3">
          <p className="text-sm font-semibold text-foreground">No recently discovered flags</p>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            New tables that fail an early file-count or target-file-size check will show up here.
          </p>
        </div>
      ) : (
        <FindingList findings={preview} idPrefix={`${baseId}-preview`} label="Recently discovered" />
      )}
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="overflow-y-auto font-sans sm:max-w-lg">
          <SheetHeader className="pr-8">
            <SheetTitle className="font-sans text-sm font-semibold leading-5 text-foreground">
              Recently discovered
              <Count count={count} />
            </SheetTitle>
            <SheetDescription>{DESCRIPTION}</SheetDescription>
          </SheetHeader>
          <FindingList
            findings={sorted}
            idPrefix={`${baseId}-sheet`}
            label="All recently discovered"
            className="-mx-6"
          />
        </SheetContent>
      </Sheet>
    </section>
  );
};
