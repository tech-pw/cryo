import { Info } from "lucide-react";
import { type ReactNode } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { Alert } from "./ui/alert";
import { Button } from "./ui/button";
import { EmptyState } from "./ui/empty-state";

export { ChartFrame } from "./ui/chart-panel";
export { PageHeader } from "./ui/page-header";

export { EmptyState };

export interface StatItem {
  label: string;
  value: ReactNode;
  suffix?: ReactNode;
  /** Shown as a hoverable info icon next to the label — use for any metric whose name alone
   * doesn't say what it measures (e.g. "why is this called that"). */
  tooltip?: string;
}

/** A label with a hoverable info icon — use for any field name that doesn't say what it means
 * on its own (e.g. "Live" bytes, "Precision coverage"). Reused by StatStrip and Descriptions. */
export const HintLabel = ({ label, tooltip }: { label: ReactNode; tooltip?: string }) =>
  tooltip ? (
    <span className="inline-flex items-center gap-1">
      <span>{label}</span>
      <span
        title={tooltip}
        className="inline-flex cursor-help text-muted-foreground"
        aria-label={typeof label === "string" ? `About ${label}` : "More information"}
      >
        <Info className="size-3.5" aria-hidden="true" />
      </span>
    </span>
  ) : (
    <>{label}</>
  );

export const StatStrip = ({ items }: { items: StatItem[] }) => (
  <div className="stat-strip">
    {items.map((item) => (
      <div key={item.label} className="px-3 py-3">
        <p className="text-xs text-muted-foreground">
          <HintLabel label={item.label} tooltip={item.tooltip} />
        </p>
        <p className="mt-1 text-lg font-semibold tabular-nums text-foreground">
          {item.value}
          {item.suffix ? <span className="ml-1 text-sm font-normal text-muted-foreground">{item.suffix}</span> : null}
        </p>
      </div>
    ))}
  </div>
);

export const JsonHelpButton = ({ value }: { value: unknown }) => (
  <Button
    size="sm"
    variant="secondary"
    onClick={() => void navigator.clipboard.writeText(JSON.stringify(value, null, 2))}
  >
    Copy JSON
  </Button>
);

export const QueryErrorAlert = ({
  error,
  title = "Failed to load data",
  className = "page-alert"
}: {
  error: unknown;
  title?: string;
  className?: string;
}) => {
  if (!error) return null;
  const detail =
    error instanceof ApiError
      ? String(error.detail ?? error.message)
      : error instanceof Error
        ? error.message
        : "Unknown error";
  const isAuth = error instanceof ApiError && (error.status === 401 || error.status === 403);
  return (
    <Alert
      className={className}
      variant="error"
      title={isAuth ? "Authentication or permission error" : title}
      description={
        <>
          <p>{detail}</p>
          {isAuth ? (
            <p className="mt-1">Sign in again or switch to a role with sufficient permissions in the header.</p>
          ) : null}
        </>
      }
    />
  );
};

export const TruncatedResultsBanner = ({
  shown,
  total,
  noun = "results"
}: {
  shown: number;
  total: number;
  noun?: string;
}) =>
  total > shown ? (
    <Alert
      className="page-alert"
      variant="warning"
      title={`Showing ${shown.toLocaleString()} of ${total.toLocaleString()} ${noun}`}
      description="Refine filters or use search to narrow the list — the view is capped for performance."
    />
  ) : null;

export const FirstRunEmptyState = () => (
  <EmptyState
    title="No tables scanned yet"
    description="Connect a catalog source, then run a scan to populate health and cost metrics."
    action={
      <Link to="/connections">
        <Button variant="primary">Connect a catalog</Button>
      </Link>
    }
  />
);

/**
 * Move 24 — surfaces that depend on query-log ingestion (e.g. the Query
 * Performance page, the Layout optimization recommendations) should
 * never look "broken" when telemetry hasn't been ingested yet. This
 * banner makes the dependency explicit: silence is the wrong answer,
 * but so is pretending the page is empty for an unrelated reason.
 */
export const TelemetryRequiredNotice = ({
  surface
}: {
  surface: string;
}) => (
  <Alert
    className="mb-4"
    variant="info"
    title={`${surface} requires query-log ingestion`}
    description={
      <span>
        Connect a Trino or Spark access log via <strong>Connections → Query log ingestion</strong> to populate this
        view. See the docs for ingestion setup; this view shows empty until at least one telemetry source is wired in.
      </span>
    }
  />
);
