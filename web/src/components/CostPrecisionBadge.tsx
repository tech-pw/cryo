import { Tag, Tooltip } from "antd";
import type { CostPrecision } from "../api/types";
import { formatTime } from "../lib/format";

const LABELS: Record<CostPrecision, string> = {
  heuristic: "Quick estimate",
  metadata: "Iceberg metadata",
  storage_truth: "Storage checked"
};

const TOOLTIPS: Record<CostPrecision, string> = {
  heuristic:
    "Cryo used basic table stats. Good for a quick view, but savings and reclaimable storage may move after a deeper scan.",
  metadata:
    "Cryo read Iceberg snapshots, manifests, and file metadata. This is usually precise for table-managed files, but orphan files need a storage check.",
  storage_truth:
    "Cryo also listed the table's storage path, so orphan files outside Iceberg metadata were checked too."
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
  const color = precision === "storage_truth" ? "processing" : "default";
  const tooltip = [
    TOOLTIPS[precision],
    precision !== "storage_truth"
      ? "Only some tables show Storage checked because full storage scans are opt-in or cadence-based."
      : null,
    lastFullCostScanAt ? `Last storage check: ${formatTime(lastFullCostScanAt)}` : null,
    orphanScanStale ? "The last storage check is stale; rerun a full cost scan for orphan accuracy." : null
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Tooltip title={tooltip}>
      <Tag color={color}>{label}</Tag>
    </Tooltip>
  );
};
