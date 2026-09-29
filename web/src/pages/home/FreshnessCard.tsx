import { Link } from "react-router-dom";
import { Card } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/cn";
import { formatNumber, formatPercent, formatTime } from "../../lib/format";

export interface FreshnessRow {
  key: string;
  label: string;
  count: number;
  percentage: number;
  href: string;
}

const fillToken = (key: string): string => {
  if (key === "failed") return "var(--color-status-danger)";
  if (key === "unknown") return "var(--color-ink-faint)";
  return "var(--color-chart-primary)";
};

const fillClass = (key: string): string => {
  if (key === "failed") return "bg-[var(--color-status-danger)]";
  if (key === "unknown") return "bg-[var(--color-ink-faint)]";
  return "bg-[var(--color-chart-primary)]";
};

const barWidth = (count: number, maxCount: number): string => {
  if (maxCount <= 0 || count <= 0) return "0%";
  return `${(count / maxCount) * 100}%`;
};

const rowName = (row: FreshnessRow): string => {
  const count = formatNumber(row.count, true);
  const noun = row.count === 1 ? "table" : "tables";
  return `${row.label}: ${count} ${noun}. Open matching tables.`;
};

const FreshnessSkeleton = () => (
  <div className="flex flex-col gap-1" aria-busy="true" aria-label="Loading">
    {Array.from({ length: 4 }, (_, index) => (
      <div key={index} className="flex h-7 items-center gap-3 max-sm:gap-2" aria-hidden="true">
        <Skeleton className="h-3 w-[184px] max-sm:w-[128px]" />
        <Skeleton className="h-4 min-w-0 flex-1 rounded-[4px]" />
        <Skeleton className="h-4 w-12 max-sm:w-10" />
      </div>
    ))}
  </div>
);

const FreshnessEmpty = () => (
  <p className="text-left text-sm font-normal leading-5 text-[var(--color-ink)]">
    No snapshot ages yet.{" "}
    <Link to="/runs/scanner" className="text-[var(--color-link)] no-underline hover:underline">
      Run a catalog scan
    </Link>
  </p>
);

const FreshnessRows = ({ rows }: { rows: FreshnessRow[] }) => {
  const maxCount = rows.reduce((max, row) => Math.max(max, row.count), 0);
  return (
    <div role="group" aria-label="Tables by latest snapshot age" className="flex flex-col gap-1">
      {rows.map((row) => (
        <Link
          key={row.key}
          to={row.href}
          aria-label={rowName(row)}
          className="group flex h-7 min-w-0 items-center gap-3 rounded-[var(--radius-sm)] px-1 no-underline hover:bg-[var(--color-primary-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--focus-ring-color)] max-sm:gap-2"
        >
          <span className="w-[184px] max-sm:w-[128px] shrink-0 whitespace-nowrap text-xs font-normal text-muted-foreground group-hover:text-[var(--color-link)] group-hover:underline group-hover:decoration-1 max-sm:truncate">
            {row.label}
          </span>
          <span className="h-4 min-w-0 flex-1 overflow-hidden rounded-[4px] bg-[var(--color-chart-grid)]">
            <span
              data-freshness-bar=""
              className={cn("block h-full rounded-[4px]", fillClass(row.key))}
              style={{
                width: barWidth(row.count, maxCount),
                minWidth: row.count > 0 ? "2px" : undefined,
                backgroundColor: fillToken(row.key)
              }}
            />
          </span>
          <span className="w-12 max-sm:w-10 shrink-0 text-right text-sm font-semibold tabular-nums text-[var(--color-ink)]">
            {formatNumber(row.count, true)}
          </span>
        </Link>
      ))}
    </div>
  );
};

const FreshnessShare = ({ rows }: { rows: FreshnessRow[] }) => (
  <details className="mt-3">
    <summary className="cursor-pointer select-none text-xs font-medium leading-4 text-[var(--color-link)]">
      Counts and share
    </summary>
    <div className="mt-3 overflow-x-auto rounded-[var(--radius-sm)] border border-border">
      <table className="w-full border-collapse text-xs">
        <caption className="sr-only">
          Counts and fleet share by snapshot age. Share is one decimal. Bars scale to the largest bucket, not to 100%.
        </caption>
        <thead className="bg-[var(--color-table-header)]">
          <tr>
            <th scope="col" className="px-2.5 py-1.5 text-left font-medium text-muted-foreground">
              Label
            </th>
            <th scope="col" className="px-2.5 py-1.5 text-right font-medium text-muted-foreground">
              Count
            </th>
            <th scope="col" className="px-2.5 py-1.5 text-right font-medium text-muted-foreground">
              Share
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-t border-border">
              <td className="px-2.5 py-1.5 text-left text-foreground">{row.label}</td>
              <td className="px-2.5 py-1.5 text-right tabular-nums text-foreground">
                {formatNumber(row.count, true)}
              </td>
              <td className="px-2.5 py-1.5 text-right tabular-nums text-foreground">
                {formatPercent(row.percentage, 1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </details>
);

export const FreshnessCard = ({
  rows,
  asOf,
  loading
}: {
  rows: FreshnessRow[];
  asOf: string | null;
  loading: boolean;
}) => {
  const empty = !loading && rows.length === 0;
  const asOfLabel = !empty && asOf ? formatTime(asOf) : null;

  return (
    <Card className="mb-0" aria-busy={loading ? true : undefined}>
      <div className="p-4 max-sm:p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[18px] font-semibold leading-[26px] text-[var(--color-ink)]">
              Table freshness and staleness
            </h2>
            <p className="mt-1 text-xs font-normal leading-4 text-[var(--color-muted)]">
              Latest snapshot age. Each bar opens those tables.
            </p>
          </div>
          {asOfLabel ? (
            <span className="shrink-0 text-xs font-normal leading-4 text-[var(--color-muted)] tabular-nums">
              As of {asOfLabel}
            </span>
          ) : null}
        </div>
        <div className="mt-3">
          {loading ? (
            <FreshnessSkeleton />
          ) : empty ? (
            <FreshnessEmpty />
          ) : (
            <>
              <FreshnessRows rows={rows} />
              <FreshnessShare rows={rows} />
            </>
          )}
        </div>
      </div>
    </Card>
  );
};
