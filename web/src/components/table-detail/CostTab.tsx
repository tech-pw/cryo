import { DollarSign } from "lucide-react";
import type { TableStatsRead } from "../../api/types";
import { getTableCost, getTableS3Tiering } from "../../api/client";
import { CostPrecisionBadge } from "../CostPrecisionBadge";
import { CostSavingsWaterfall } from "../CostSavingsWaterfall";
import { SavingsByOperation } from "../SavingsByOperation";
import { EmptyState } from "../common";
import { Badge } from "../ui/badge";
import { RequestCostShare } from "./RequestCostShare";
import { StorageDonut } from "./StorageDonut";
import { ChartCard } from "./ChartCard";
import { isOrphanScanStale } from "../../lib/costStaleness";
import { formatCurrency } from "../../lib/format";

type CostView = Awaited<ReturnType<typeof getTableCost>> | undefined;
type S3TieringView = Awaited<ReturnType<typeof getTableS3Tiering>> | null | undefined;
type CostByOp = NonNullable<CostView>["by_operation"];

interface CostTabProps {
  costView: CostView;
  stats: TableStatsRead | null | undefined;
  storageBytes: number | null;
  hasStatsCostFallback: boolean;
  currentCostUsd: number | null;
  projectedCostUsd: number | null;
  savingsUsd: number;
  costByOperation: CostByOp;
  s3Tiering: S3TieringView;
}

export const CostTab = ({
  costView,
  stats,
  storageBytes,
  hasStatsCostFallback,
  currentCostUsd,
  projectedCostUsd,
  savingsUsd,
  costByOperation,
  s3Tiering
}: CostTabProps) => {
  const showContent = Boolean(costView || hasStatsCostFallback);
  return (
    <div className="td-tab-content">
      <ChartCard
        quiet={!showContent}
        title="Cost & Savings"
        actions={
          <CostPrecisionBadge
            precision={costView?.precision ?? stats?.cost_precision}
            lastFullCostScanAt={
              costView?.last_full_cost_scan_at ?? stats?.cost_last_full_scan_at
            }
            orphanScanStale={isOrphanScanStale(
              costView?.precision ?? stats?.cost_precision,
              costView?.last_full_cost_scan_at ?? stats?.cost_last_full_scan_at
            )}
          />
        }
      >
        {showContent ? (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div>
              <div
                className="td-cost-summary"
                style={{ paddingLeft: 0, paddingRight: 0, marginBottom: 12 }}
              >
                <div className="td-cost-row">
                  <span className="td-cost-delta">
                    <span className="td-cost-label">Current</span>
                    <span className="td-cost-value">{formatCurrency(currentCostUsd)}</span>
                  </span>
                  <span className="td-cost-arrow">→</span>
                  <span className="td-cost-delta td-cost-projected">
                    <span className="td-cost-label">Projected</span>
                    <span className="td-cost-value">{formatCurrency(projectedCostUsd)}</span>
                  </span>
                  <span className="td-cost-save">
                    Save <strong>{formatCurrency(savingsUsd)}</strong>/month
                  </span>
                </div>
              </div>
              {costView ? (
                <StorageDonut
                  liveBytes={costView.live_bytes}
                  deadBytes={costView.dead_bytes}
                  metadataBytes={costView.metadata_bytes}
                  orphanBytes={costView.orphan_bytes}
                  pricePerGbMonth={costView.price_per_gb_month}
                />
              ) : null}
              {costView && currentCostUsd != null ? (
                <RequestCostShare
                  storageCostUsd={currentCostUsd}
                  requestCostUsd={costView.request_cost_usd_month}
                  getRequests30d={costView.get_requests_30d}
                  putRequests30d={costView.put_requests_30d}
                  listRequests30d={costView.list_requests_30d}
                  pricingSource={costView.request_cost_pricing_source}
                />
              ) : null}
            </div>
            <div>
              {costByOperation.length && currentCostUsd != null ? (
                <CostSavingsWaterfall
                  currentCostUsd={currentCostUsd}
                  currentStorageBytes={storageBytes ?? (stats?.total_size_gb != null ? stats.total_size_gb * 1024 ** 3 : 0)}
                  byOperation={costByOperation}
                />
              ) : null}
              {costByOperation.length ? <SavingsByOperation items={costByOperation} /> : null}
            </div>
            {s3Tiering ? (
              <div className="col-span-full flex flex-wrap items-center gap-2">
                <Badge variant="default">{s3Tiering.recommendation}</Badge>
                <span className="text-sm text-muted-foreground">{s3Tiering.reason}</span>
              </div>
            ) : null}
          </div>
        ) : (
          <EmptyState
            title="No cost breakdown yet"
            description="Cost breakdown appears after the scanner writes table cost details."
          />
        )}
      </ChartCard>
    </div>
  );
};

export const CostTabLabel = () => (
  <span>
    <DollarSign className="mr-1 inline size-3.5" />
    Cost &amp; Savings
  </span>
);

export default CostTab;
