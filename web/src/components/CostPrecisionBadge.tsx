import { CheckCircle2, Clock3, Database, Loader2, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import type { CostPrecision } from "../api/types";
import { formatTime } from "../lib/format";
import { Badge } from "./ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

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
  { variant: "secondary" | "default" | "success" | "warning"; icon: ReactNode; staleIcon: ReactNode }
> = {
  heuristic: {
    variant: "secondary",
    icon: <Clock3 className="size-3" />,
    staleIcon: <TriangleAlert className="size-3" />
  },
  metadata: {
    variant: "default",
    icon: <Database className="size-3" />,
    staleIcon: <TriangleAlert className="size-3" />
  },
  storage_truth: {
    variant: "success",
    icon: <CheckCircle2 className="size-3" />,
    staleIcon: <TriangleAlert className="size-3" />
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
  if (!precision) {
    // Every other numeric column in this table falls back to "—" (see CatalogDashboard's
    // Est. Savings cell) rather than rendering nothing -- an unscanned/no-cost-precision
    // table should read as "not yet computed," not as a blank cell under a lone tooltip icon.
    return <span className="text-sm text-muted-foreground">—</span>;
  }
  const label = LABELS[precision] ?? precision;
  const style = TIER_STYLE[precision] ?? TIER_STYLE.heuristic;
  const variant = orphanScanStale ? "warning" : style.variant;
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
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={variant} className="max-w-full gap-1 whitespace-nowrap">
          {icon}
          <span>{label}</span>
          {orphanScanStale ? <span className="font-normal opacity-80">· stale</span> : null}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  );
};

export const AwsStatusSpinner = () => <Loader2 className="size-3 animate-spin" aria-hidden />;
