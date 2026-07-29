import {
  Eraser,
  Flame,
  History,
  LayoutGrid,
  Radar
} from "lucide-react";
import type { ReactNode } from "react";
import type { BenefitScoreResponse, TableSignalsResponse } from "../../api/client";
import type { TableCostRead } from "../../api/types";
import { formatBytesGb, formatCurrency, formatNumber } from "../../lib/format";
import { operationLabels } from "../../lib/theme";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

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
  if (operation === "optimize") return <Flame className="size-3.5" />;
  if (operation === "remove_orphan_files") return <Eraser className="size-3.5" />;
  if (operation === "expire_snapshots") return <History className="size-3.5" />;
  if (operation === "optimize_manifests") return <LayoutGrid className="size-3.5" />;
  return <Radar className="size-3.5" />;
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
      description: `Save ${formatCurrency(op.savings_usd_month)}/month · reclaim ${formatBytesGb(op.reclaimable_bytes)}`,
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
      icon: <Radar className="size-3.5" />
    });
  }

  const topPartition = benefitScores?.top_candidates?.[0];
  if (topPartition?.benefit_score && topPartition.benefit_score > 0.05) {
    actionItems.push({
      key: "hot-partition",
      title: "Compact Hottest Partition",
      description: `${topPartition.partition_value ?? "(table)"} · score ${topPartition.benefit_score.toFixed(3)} · ${formatNumber(topPartition.small_files)} small files`,
      savingsUsd: 0,
      confidence: "MED",
      icon: <Flame className="size-3.5" />
    });
  }

  const ranked = actionItems
    .sort((a, b) => b.savingsUsd - a.savingsUsd)
    .slice(0, 3);

  if (!ranked.length) return null;

  return (
    <div className="td-recommended-actions">
      {ranked.map((item, index) => (
        <div
          className={`td-action-card td-action-card-${index === 0 ? "top" : "secondary"}`}
          key={item.key}
        >
          <div className="td-action-card-head">
            <span className="td-action-icon">{item.icon}</span>
            <strong>{item.title}</strong>
            <Badge className={`td-confidence td-confidence-${item.confidence.toLowerCase()}`}>
              {item.confidence}
            </Badge>
          </div>
          <span className="td-action-desc text-sm text-muted-foreground">{item.description}</span>
          <div className="mt-2 flex gap-2">
            <Button type="button" size="sm" variant="secondary" onClick={onPreview}>
              Preview
            </Button>
            {index === 0 ? (
              <Button type="button" size="sm" variant="primary" onClick={onRun}>
                Run Now
              </Button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
};
