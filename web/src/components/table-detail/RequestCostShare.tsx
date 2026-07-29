import { formatCurrency, formatNumber } from "../../lib/format";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";

interface RequestCostShareProps {
  storageCostUsd: number;
  requestCostUsd?: number | null;
  getRequests30d?: number | null;
  putRequests30d?: number | null;
  listRequests30d?: number | null;
  pricingSource?: string | null;
}

/**
 * Storage/request cost-share breakdown (docs/s3-request-cost-tracking.md §9.2). request_cost_usd_month
 * is null (not 0) when no ingestion has run for this table yet -- that must not render as "0% requests",
 * so this renders nothing at all in that case rather than a misleading number.
 */
export const RequestCostShare = ({
  storageCostUsd,
  requestCostUsd,
  getRequests30d,
  putRequests30d,
  listRequests30d,
  pricingSource
}: RequestCostShareProps) => {
  if (requestCostUsd == null) {
    return null;
  }

  const total = storageCostUsd + requestCostUsd;
  const requestSharePct = total > 0 ? Math.round((requestCostUsd / total) * 100) : 0;
  const storageSharePct = 100 - requestSharePct;
  const isApproximate = pricingSource != null && pricingSource !== "live" && pricingSource !== "aws_price_list_api";

  return (
    <div className="td-request-cost-share">
      <div className="td-chart-card-header" style={{ marginBottom: 8 }}>
        <span className="td-chart-card-title">Storage vs. request cost</span>
        {isApproximate ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-xs text-muted-foreground">Approximate</span>
            </TooltipTrigger>
            <TooltipContent>
              Live AWS pricing wasn&apos;t available for this account/region — using a default rate instead.
            </TooltipContent>
          </Tooltip>
        ) : null}
      </div>
      <div className="td-request-cost-bar">
        <div
          className="td-request-cost-bar-segment td-request-cost-bar-storage"
          style={{ width: `${storageSharePct}%` }}
        />
        <div
          className="td-request-cost-bar-segment td-request-cost-bar-requests"
          style={{ width: `${requestSharePct}%` }}
        />
      </div>
      <div className="td-request-cost-legend">
        <span>
          <span className="td-request-cost-swatch td-request-cost-swatch-storage" />
          Storage {formatCurrency(storageCostUsd)} ({storageSharePct}%)
        </span>
        <span>
          <span className="td-request-cost-swatch td-request-cost-swatch-requests" />
          Requests {formatCurrency(requestCostUsd)} ({requestSharePct}%)
        </span>
      </div>
      {getRequests30d != null || putRequests30d != null || listRequests30d != null ? (
        <span className="mt-1.5 block text-xs text-muted-foreground">
          Last 30 days: {formatNumber(getRequests30d ?? 0)} GET, {formatNumber(putRequests30d ?? 0)} PUT,{" "}
          {formatNumber(listRequests30d ?? 0)} LIST
        </span>
      ) : null}
    </div>
  );
};

export default RequestCostShare;
