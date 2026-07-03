import { Tag, Tooltip } from "antd";
import type { CostPrecision } from "../api/types";
import { formatTime } from "../lib/format";

const LABELS: Record<CostPrecision, string> = {
  heuristic: "Estimated",
  metadata: "Metadata precise",
  storage_truth: "Storage verified"
};

const TOOLTIPS: Record<CostPrecision, string> = {
  heuristic: "Tier-0 estimate from table stats. Run a metadata cost scan for finer projections.",
  metadata: "Snapshot and manifest walk. Orphan bytes may be carried forward until a storage scan.",
  storage_truth: "Orphan detection included a storage listing under the table prefix."
};

export const CostPrecisionBadge = ({
  precision,
  lastFullCostScanAt,
  orphanScanStale
}: {
  precision?: CostPrecision | null;
  lastFullCostScanAt?: string | null;
  orphanScanStale?: boolean;
}) => {
  if (!precision) return null;
  const label = LABELS[precision] ?? precision;
  const color = precision === "storage_truth" ? "green" : precision === "metadata" ? "blue" : "default";
  const tooltip = [
    TOOLTIPS[precision],
    lastFullCostScanAt ? `Last full cost scan: ${formatTime(lastFullCostScanAt)}` : null,
    orphanScanStale ? "Storage-truth scan is stale; rerun with --cost-full-scan for orphan accuracy." : null
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Tooltip title={tooltip}>
      <Tag color={color}>{label}</Tag>
    </Tooltip>
  );
};
