import { useQuery } from "@tanstack/react-query";

import { getTableMaintenanceImpact } from "../../../api/client";
import type { ImpactWindowRead } from "../../../api/types";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatNumber } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { Skeleton } from "../../ui/skeleton";
import { OPERATION_SHORT } from "../runs/runModel";

const WINDOW_DAYS = 30;

const filesReduced = (window: ImpactWindowRead) => window.files_in - window.files_out;

const coverage = (window: ImpactWindowRead) => {
  const measured = Math.max(window.ops_with_bytes, window.ops_with_files);
  return measured === window.ops
    ? `All ${formatNumber(window.ops)} reported their effect`
    : `${formatNumber(measured)} of ${formatNumber(window.ops)} reported their effect`;
};

const WindowColumn = ({ label, window }: { label: string; window: ImpactWindowRead }) => (
  <div className="td-impact-window">
    <p className="td-impact-label">{label}</p>
    {window.ops ? (
      <>
        <p className="td-impact-figure">
          <b>{window.ops_with_bytes ? formatBytesGb(window.bytes_freed) : "—"}</b> freed
        </p>
        <p className="td-impact-figure">
          <b>{window.ops_with_files ? formatNumber(filesReduced(window)) : "—"}</b> fewer files
        </p>
        <p className="td-impact-coverage">
          {formatNumber(window.ops)} successful operation{window.ops === 1 ? "" : "s"}. {coverage(window)}.
        </p>
      </>
    ) : (
      <p className="td-impact-coverage">No successful maintenance in this window.</p>
    )}
  </div>
);

/**
 * Only what engines reported in their audit receipts. Operations that reported nothing are
 * counted as unmeasured rather than as zero, so a Trino expiry that freed space but didn't
 * say how much never makes maintenance look useless.
 */
export const MaintenanceImpact = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const impact = useQuery({
    queryKey: tableKeys.maintenanceImpact(tableId, token, WINDOW_DAYS),
    queryFn: () => getTableMaintenanceImpact(api, tableId, WINDOW_DAYS)
  });

  if (impact.isPending) return <Skeleton className="h-24 w-full" />;
  if (impact.isError) return <QueryErrorAlert error={impact.error} title="Couldn't load what maintenance achieved" />;
  const { recent, lifetime } = impact.data;
  if (!lifetime.ops) return null;

  const operations = lifetime.by_operation.filter((item) => item.ops_with_bytes || item.ops_with_files);
  return (
    <section className="td-impact" aria-labelledby="overview-impact">
      <h3 id="overview-impact" className="td-history-subhead">What maintenance achieved</h3>
      <div className="td-impact-grid">
        <WindowColumn label={`Last ${WINDOW_DAYS} days`} window={recent} />
        <WindowColumn
          label={lifetime.since ? `Since ${new Date(lifetime.since).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}` : "All time"}
          window={lifetime}
        />
      </div>
      {operations.length ? (
        <ul className="td-impact-ops">
          {operations.map((item) => (
            <li key={item.operation}>
              <span>{OPERATION_SHORT[item.operation] ?? item.operation}</span>
              <span>
                {[
                  item.ops_with_bytes ? `${formatBytesGb(item.bytes_freed)} freed` : null,
                  item.ops_with_files ? `${formatNumber(item.files_in)} → ${formatNumber(item.files_out)} files` : null
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
};
