import {
  AppstoreOutlined,
  ClearOutlined,
  FireOutlined,
  HistoryOutlined,
  RadarChartOutlined
} from "@ant-design/icons";
import type { ReactNode } from "react";
import { Button, Card, Space, Tag, Typography } from "antd";
import type { BenefitScoreResponse, TableSignalsResponse } from "../../api/client";
import type { TableCostRead } from "../../api/types";
import { formatBytesGb, formatCurrency, formatNumber } from "../../lib/format";
import { operationLabels } from "../../lib/theme";

type Confidence = "HIGH" | "MED" | "LOW";

interface ActionItem {
  key: string;
  title: string;
  description: string;
  savingsUsd: number;
  confidence: Confidence;
  icon: ReactNode;
}

interface RecommendedActionsProps {
  costView?: TableCostRead;
  signals?: TableSignalsResponse;
  benefitScores?: BenefitScoreResponse;
  onPreview: () => void;
  onRun: () => void;
}

const confidenceFromPrecision = (precision?: string): Confidence => {
  if (precision === "metadata" || precision === "storage_truth") return "HIGH";
  if (precision === "heuristic") return "MED";
  return "LOW";
};

const iconForOperation = (operation: string): ReactNode => {
  if (operation === "optimize") return <FireOutlined />;
  if (operation === "remove_orphan_files") return <ClearOutlined />;
  if (operation === "expire_snapshots") return <HistoryOutlined />;
  if (operation === "optimize_manifests") return <AppstoreOutlined />;
  return <RadarChartOutlined />;
};

export const RecommendedActions = ({
  costView,
  signals,
  benefitScores,
  onPreview,
  onRun
}: RecommendedActionsProps) => {
  const confidence = confidenceFromPrecision(costView?.precision);
  const actionItems: ActionItem[] = [];

  for (const op of costView?.by_operation ?? []) {
    if (op.savings_usd_month <= 0) continue;
    actionItems.push({
      key: `op-${op.operation}`,
      title: operationLabels[op.operation] ?? op.operation,
      description: `Save ${formatCurrency(op.savings_usd_month)}/mo · reclaim ${formatBytesGb(op.reclaimable_bytes)}`,
      savingsUsd: op.savings_usd_month,
      confidence,
      icon: iconForOperation(op.operation)
    });
  }

  for (const signal of signals?.signals ?? []) {
    if (!signal.triggered || signal.severity === "info") continue;
    actionItems.push({
      key: `signal-${signal.signal_name}`,
      title: `Investigate ${signal.signal_name}`,
      description: signal.reason,
      savingsUsd: 0,
      confidence: signal.severity === "critical" ? "HIGH" : "MED",
      icon: <RadarChartOutlined />
    });
  }

  const topPartition = benefitScores?.top_candidates?.[0];
  if (topPartition?.benefit_score && topPartition.benefit_score > 0.05) {
    actionItems.push({
      key: "hot-partition",
      title: "Compact hottest partition first",
      description: `${topPartition.partition_value ?? "(table)"} · score ${topPartition.benefit_score.toFixed(3)} · ${formatNumber(topPartition.small_files)} small files`,
      savingsUsd: 0,
      confidence: "MED",
      icon: <FireOutlined />
    });
  }

  const ranked = actionItems
    .sort((a, b) => b.savingsUsd - a.savingsUsd)
    .slice(0, 3);

  if (!ranked.length) return null;

  return (
    <div className="td-recommended-actions">
      {ranked.map((item, index) => (
        <Card className={`td-action-card td-action-card-${index === 0 ? "top" : "secondary"}`} key={item.key}>
          <div className="td-action-card-head">
            <span className="td-action-icon">{item.icon}</span>
            <Typography.Text strong>{item.title}</Typography.Text>
            <Tag className={`td-confidence td-confidence-${item.confidence.toLowerCase()}`}>{item.confidence}</Tag>
          </div>
          <Typography.Text type="secondary" className="td-action-desc">
            {item.description}
          </Typography.Text>
          <Space size={8} style={{ marginTop: 8 }}>
            <Button size="small" onClick={onPreview}>Preview</Button>
            {index === 0 ? (
              <Button size="small" type="primary" onClick={onRun}>
                Run now
              </Button>
            ) : null}
          </Space>
        </Card>
      ))}
    </div>
  );
};
