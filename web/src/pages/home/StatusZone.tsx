import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import type { CryoVitals } from "../../api/vitals";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber } from "../../lib/format";
import type { NightlyRunView, ReclaimDisplay } from "../../lib/nowStrip";
import { vitalsScoreLabel, vitalsStatusLabel } from "../../lib/nowStrip";

export interface StatusZoneProps {
  vitals?: CryoVitals | null;
  vitalsLoading: boolean;
  vitalsError: boolean;
  nightly: NightlyRunView | null;
  nightlyLoading: boolean;
  nightlyError: boolean;
  failedCount: number | null;
  reclaim: ReclaimDisplay;
  scannerLabel: string | null;
  scannerHref: string;
  queueDepth: number | null;
  engineLabel: string | null;
  engineHref: string;
}

const finite = (value: number | null | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value);

const topDeduction = (vitals: CryoVitals | null | undefined) => {
  const rows = vitals?.deductions ?? [];
  if (rows.length === 0) return null;
  return rows.reduce((best, row) => (row.points > best.points ? row : best));
};

const reasonText = (reason: string): string => {
  const split = reason.indexOf(": ");
  return split >= 0 ? reason.slice(split + 2) : reason;
};

const StatusIcon = ({ status }: { status: string | null | undefined }) => {
  if (status === "CRITICAL") {
    return <XCircle className="size-5 text-[var(--color-status-danger)]" aria-hidden="true" />;
  }
  if (status === "DEGRADED") {
    return <AlertTriangle className="size-3.5 text-[var(--color-status-warning)]" aria-hidden="true" />;
  }
  if (status === "HEALTHY") {
    return <CheckCircle2 className="size-3.5 text-[var(--color-status-success)]" aria-hidden="true" />;
  }
  return null;
};

const ScoreGauge = ({ score, status }: { score: number; status: string | null | undefined }) => {
  const clamped = Math.min(100, Math.max(0, score));
  const arc = 150.8;
  const color =
    status === "CRITICAL"
      ? "var(--color-status-danger)"
      : status === "DEGRADED"
        ? "var(--color-status-warning)"
        : "var(--color-status-success)";
  const label = vitalsStatusLabel(status);
  return (
    <div className="relative h-[108px] w-[168px]">
      <svg viewBox="0 0 120 78" className="h-full w-full" role="img" aria-label={`${vitalsScoreLabel(score)} out of 100`}>
        <path
          d="M 14 66 A 46 46 0 0 1 106 66"
          fill="none"
          stroke="var(--color-chart-grid)"
          strokeWidth="10"
          strokeLinecap="round"
        />
        <path
          d="M 14 66 A 46 46 0 0 1 106 66"
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${(clamped / 100) * arc} ${arc}`}
        />
      </svg>
      <div className="absolute inset-x-0 bottom-1 text-center">
        <p className="text-lg font-semibold leading-6 text-foreground [font-variant-numeric:tabular-nums]">
          {vitalsScoreLabel(score)}
          <span className="ml-1 text-sm font-normal text-[var(--color-muted)]">/ 100</span>
        </p>
        {label !== "Not measured" ? (
          <p
            className={`text-xs font-medium ${
              status === "CRITICAL"
                ? "text-[var(--color-status-danger)]"
                : status === "DEGRADED"
                  ? "text-[var(--color-status-warning)]"
                  : "text-foreground"
            }`}
          >
            {label}
          </p>
        ) : null}
      </div>
    </div>
  );
};

const VitalsBlock = ({
  vitals,
  loading,
  error
}: {
  vitals?: CryoVitals | null;
  loading: boolean;
  error: boolean;
}) => {
  const score = finite(vitals?.score) ? vitals.score : null;
  const deduction = topDeduction(vitals);
  const rest = (vitals?.deductions ?? []).filter((row) => row !== deduction);

  return (
    <div>
      <p className="text-[11px] font-medium leading-4 tracking-[0.015em] text-[var(--color-muted)]">Cryo</p>
      {loading ? (
        <div className="mt-1" aria-busy="true">
          <Skeleton className="h-[26px] w-40" />
          <Skeleton className="mt-2 h-2 w-full rounded-[var(--radius-sm)]" />
        </div>
      ) : error ? (
        <p className="mt-1 text-sm leading-[22px] text-foreground">Vitals unavailable</p>
      ) : score == null ? (
        <p className="mt-1 text-lg font-medium leading-[26px] text-[var(--color-muted)]">Not measured</p>
      ) : (
        <div className="mt-1 flex items-end gap-3">
          <ScoreGauge score={score} status={vitals?.status} />
          <StatusIcon status={vitals?.status} />
        </div>
      )}
      {deduction ? (
        <p className="mt-2 text-sm leading-5 text-foreground" title={deduction.reason}>
          {reasonText(deduction.reason)}
          <span className="ml-1 font-medium text-[var(--color-muted)] [font-variant-numeric:tabular-nums]">
            (−{deduction.points})
          </span>
          {rest.length > 0 ? (
            <Popover>
              <PopoverTrigger asChild>
                <button type="button" className="ml-2 text-xs font-medium text-[var(--color-link)] hover:underline">
                  {rest.length} more
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-80">
                <p className="text-xs font-semibold">Other deductions</p>
                <ul className="mt-2 space-y-1 text-xs">
                  {rest.map((row) => (
                    <li key={row.reason}>
                      {reasonText(row.reason)} (−{row.points})
                    </li>
                  ))}
                </ul>
              </PopoverContent>
            </Popover>
          ) : null}
        </p>
      ) : null}
    </div>
  );
};

const NightlyClause = ({
  nightly,
  loading,
  error,
  failedCount
}: {
  nightly: NightlyRunView | null;
  loading: boolean;
  error: boolean;
  failedCount: number | null;
}) => {
  if (loading) return <Skeleton className="mt-2 h-[22px] w-80" />;
  if (error) return <p className="mt-2 text-sm text-foreground">Nightly run unavailable</p>;
  if (!nightly || nightly.status === "No nightly run") {
    return <p className="mt-2 text-sm text-foreground">No nightly run</p>;
  }
  const failed = failedCount != null && failedCount > 0 ? failedCount : null;
  const body = (
    <>
      Nightly {nightly.succeededSelected} succeeded
      {failed != null ? (
        <>
          {" · "}
          <span className="text-[var(--color-status-danger)] [font-variant-numeric:tabular-nums]">{failed}</span>
          {" failed · "}
        </>
      ) : (
        " · "
      )}
      {nightly.finished}
    </>
  );
  return nightly.href ? (
    <p className="mt-2 text-sm leading-5 [font-variant-numeric:tabular-nums]">
      <Link to={nightly.href} className="text-[var(--color-link)] hover:underline">
        {body}
      </Link>
    </p>
  ) : (
    <p className="mt-2 text-sm leading-5 text-foreground [font-variant-numeric:tabular-nums]">{body}</p>
  );
};

const ReclaimLine = ({ reclaim }: { reclaim: ReclaimDisplay }) => (
  <div className="mt-2">
    <div className="flex items-baseline gap-3">
      <p className="text-xs font-medium leading-4 text-muted-foreground">Reclaimed yesterday</p>
      <p
        className={
          reclaim.tone === "number"
            ? "text-lg font-semibold leading-[26px] text-foreground [font-variant-numeric:tabular-nums]"
            : "text-sm font-normal text-muted-foreground"
        }
      >
        {reclaim.hero}
        {reclaim.unit ? (
          <span className="ml-1 text-sm font-normal text-muted-foreground">{reclaim.unit}</span>
        ) : null}
      </p>
    </div>
    {reclaim.caption ? (
      <p className="mt-0.5 text-[11px] font-medium leading-4 text-muted-foreground">{reclaim.caption}</p>
    ) : null}
  </div>
);

export const StatusZone = ({
  vitals,
  vitalsLoading,
  vitalsError,
  nightly,
  nightlyLoading,
  nightlyError,
  failedCount,
  reclaim,
  scannerLabel,
  scannerHref,
  queueDepth,
  engineLabel,
  engineHref
}: StatusZoneProps) => {
  const clauses = [
    scannerLabel
      ? { key: "scanner", href: scannerHref, label: "Scanner", value: scannerLabel }
      : null,
    queueDepth != null
      ? { key: "queue", href: "/runs", label: "Queue", value: formatNumber(queueDepth) }
      : null,
    engineLabel ? { key: "engines", href: engineHref, label: "Engines", value: engineLabel } : null
  ].filter((clause): clause is { key: string; href: string; label: string; value: string } => clause != null);

  return (
    <section aria-label="Cryo" className="mb-6 border-b border-border pb-3">
      <VitalsBlock vitals={vitals} loading={vitalsLoading} error={vitalsError} />
      <NightlyClause nightly={nightly} loading={nightlyLoading} error={nightlyError} failedCount={failedCount} />
      <ReclaimLine reclaim={reclaim} />
      {clauses.length > 0 ? (
        <nav aria-label="Platform" className="mt-2 flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-xs leading-4 text-muted-foreground">
          {clauses.map((clause, index) => (
            <span key={clause.key}>
              {index > 0 ? <span aria-hidden="true"> · </span> : null}
              <Link to={clause.href} className="hover:underline">
                <span>{clause.label} </span>
                <span className="text-foreground [font-variant-numeric:tabular-nums]">{clause.value}</span>
              </Link>
            </span>
          ))}
        </nav>
      ) : null}
    </section>
  );
};
