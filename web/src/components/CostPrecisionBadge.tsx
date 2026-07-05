import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  WarningOutlined
} from "@ant-design/icons";
import { Tag, Tooltip } from "antd";
import type { ReactNode } from "react";
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

const TIER_STYLE: Record<
  CostPrecision,
  { color: string; icon: ReactNode; staleColor: string; staleIcon: ReactNode }
> = {
  heuristic: {
    color: "default",
    icon: <ClockCircleOutlined />,
    staleColor: "warning",
    staleIcon: <WarningOutlined />
  },
  metadata: {
    color: "blue",
    icon: <DatabaseOutlined />,
    staleColor: "warning",
    staleIcon: <WarningOutlined />
  },
  storage_truth: {
    color: "success",
    icon: <CheckCircleOutlined />,
    staleColor: "warning",
    staleIcon: <WarningOutlined />
  }
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
  const style = TIER_STYLE[precision] ?? TIER_STYLE.heuristic;
  const color = orphanScanStale ? style.staleColor : style.color;
  const icon = orphanScanStale ? style.staleIcon : style.icon;
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
      <Tag color={color} icon={icon}>
        {orphanScanStale ? `${label} · stale` : label}
      </Tag>
    </Tooltip>
  );
};
