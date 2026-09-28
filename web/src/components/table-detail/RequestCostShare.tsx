import { formatCurrency, formatNumber } from "../../lib/format";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";

interface RequestCostShareProps {
  storageCostUsd: number | null | undefined;
  requestCostUsd?: number | null;
  getRequests30d?: number | null;
  putRequests30d?: number | null;
  listRequests30d?: number | null;
  pricingSource?: string | null;
}

const TITLE = "estimated storage $ vs measured request $ (30 d)";

const measuredMoney = (value: number | null | undefined): number | null =>
  value != null && Number.isFinite(value) ? value : null;

const countLabel = (value: number | null | undefined): string =>
  value != null && Number.isFinite(value) ? formatNumber(value) : "Not measured";

export const RequestCostShare = ({
  storageCostUsd,
  requestCostUsd,
  getRequests30d,
  putRequests30d,
  listRequests30d,
  pricingSource
}: RequestCostShareProps) => {
  const storage = measuredMoney(storageCostUsd);
  const requests = measuredMoney(requestCostUsd);
  const isApproximate = pricingSource != null && pricingSource !== "live" && pricingSource !== "aws_price_list_api";
  const bothMeasured = storage != null && requests != null;
  const total = bothMeasured ? storage + requests : 0;
  const requestSharePct = bothMeasured && total > 0 ? Math.round((requests / total) * 100) : null;
  const storageSharePct = requestSharePct == null ? null : 100 - requestSharePct;

  return (
    <div className="td-request-cost-share">
      <div className="td-chart-card-header" style={{ marginBottom: 8 }}>
        <span className="td-chart-card-title">{TITLE}</span>
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
      {!bothMeasured ? (
        <p className="text-sm text-muted-foreground">Not measured</p>
      ) : (
        <>
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
              Storage {formatCurrency(storage)} ({storageSharePct}%)
            </span>
            <span>
              <span className="td-request-cost-swatch td-request-cost-swatch-requests" />
              Requests {formatCurrency(requests)} ({requestSharePct}%)
            </span>
          </div>
        </>
      )}
      <span className="mt-1.5 block text-xs text-muted-foreground">
        Last 30 days: {countLabel(getRequests30d)} GET, {countLabel(putRequests30d)} PUT,{" "}
        {countLabel(listRequests30d)} LIST
      </span>
    </div>
  );
};

export default RequestCostShare;
