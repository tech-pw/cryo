import { Card, Col, Row, Tag, Typography } from "antd";
import { DollarOutlined } from "@ant-design/icons";
import type { TableStatsRead } from "../../api/types";
import { getTableCost, getTableS3Tiering } from "../../api/client";
import { CostPrecisionBadge } from "../CostPrecisionBadge";
import { CostSavingsWaterfall } from "../CostSavingsWaterfall";
import { SavingsByOperation } from "../SavingsByOperation";
import { EmptyState } from "../common";
import { StorageDonut } from "./StorageDonut";
import { isOrphanScanStale } from "../../lib/costStaleness";
import { formatCurrency } from "../../lib/format";

type CostView = Awaited<ReturnType<typeof getTableCost>> | undefined;
type S3TieringView = Awaited<ReturnType<typeof getTableS3Tiering>> | null | undefined;
type CostByOp = NonNullable<CostView>["by_operation"];

interface CostTabProps {
  costView: CostView;
  stats: TableStatsRead | null | undefined;
  storageBytes: number;
  hasStatsCostFallback: boolean;
  currentCostUsd: number;
  projectedCostUsd: number;
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
      <Card
        className={`td-chart-card${showContent ? "" : " td-chart-card-quiet"}`}
        classNames={{ body: "td-chart-card-body" }}
      >
        <div className="td-chart-card-header">
          <span className="td-chart-card-title">Cost &amp; Savings</span>
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
        </div>
        <div className="td-chart-card-content">
          {showContent ? (
            <Row gutter={[16, 16]}>
              <Col xs={24} xl={12}>
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
              </Col>
              <Col xs={24} xl={12}>
                {costByOperation.length ? (
                  <CostSavingsWaterfall
                    currentCostUsd={currentCostUsd}
                    currentStorageBytes={storageBytes || (stats?.total_size_gb ?? 0) * 1024 ** 3}
                    byOperation={costByOperation}
                  />
                ) : null}
                {costByOperation.length ? <SavingsByOperation items={costByOperation} /> : null}
              </Col>
              {s3Tiering ? (
                <Col span={24}>
                  <Tag color="processing">{s3Tiering.recommendation}</Tag>
                  <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                    {s3Tiering.reason}
                  </Typography.Text>
                </Col>
              ) : null}
            </Row>
          ) : (
            <EmptyState
              title="No cost breakdown yet"
              description="Cost breakdown appears after the scanner writes table cost details."
            />
          )}
        </div>
      </Card>
    </div>
  );
};

export const CostTabLabel = () => (
  <span>
    <DollarOutlined />
    Cost &amp; Savings
  </span>
);

export default CostTab;