import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  FileText,
  HardDrive,
  Network,
  Trash2
} from "lucide-react";
import type { TableStatsRead } from "../api/types";
import { HintLabel } from "./common";
import { scoreFactorColor, type ScoreFactor } from "./HealthScoreBreakdown";
import { Badge } from "./ui/badge";
import { formatNumber } from "../lib/format";

const SKEW_HINT_THRESHOLD = 2;

export interface TableMetadataGridProps {
  stats?: TableStatsRead | null;
  factors: ScoreFactor[];
}

export const TableMetadataGrid = ({ stats, factors }: TableMetadataGridProps) => {
  const skew = stats?.partition_skew_ratio;
  const isSkewed = skew != null && skew > SKEW_HINT_THRESHOLD;
  const skewFactor = factors.find((factor) => factor.label === "Partition skew");

  return (
    <div className="td-meta-tiles">
      <div className="td-meta-tile">
        <Network className="td-meta-tile-icon" />
        <div className="td-meta-tile-body">
          <span className="td-meta-tile-label">Partition col</span>
          <span className="td-meta-tile-value">
            {stats?.partition_col ?? "-"}
            {stats?.partition_col_type ? ` (${stats.partition_col_type})` : ""}
          </span>
        </div>
      </div>

      <div className="td-meta-tile">
        <Clock className="td-meta-tile-icon" />
        <div className="td-meta-tile-body">
          <span className="td-meta-tile-label">Snapshots</span>
          <span className="td-meta-tile-value">{formatNumber(stats?.total_snapshots)}</span>
        </div>
      </div>

      <div className="td-meta-tile">
        <FileText className="td-meta-tile-icon" />
        <div className="td-meta-tile-body">
          <span className="td-meta-tile-label">Manifests</span>
          <span className="td-meta-tile-value">{formatNumber(stats?.manifest_count)}</span>
        </div>
      </div>

      <div className="td-meta-tile">
        <Trash2 className="td-meta-tile-icon" />
        <div className="td-meta-tile-body">
          <span className="td-meta-tile-label">
            <HintLabel label="Delete files" tooltip="Position delete files used for row-level deletes." />
          </span>
          <span className="td-meta-tile-value">{formatNumber(stats?.total_delete_files)}</span>
        </div>
      </div>

      <div className="td-meta-tile">
        <Database className="td-meta-tile-icon" />
        <div className="td-meta-tile-body">
          <span className="td-meta-tile-label">Records</span>
          <span className="td-meta-tile-value">{formatNumber(stats?.total_records, true)}</span>
        </div>
      </div>

      <div className="td-meta-tile">
        <HardDrive className="td-meta-tile-icon" />
        <div className="td-meta-tile-body">
          <span className="td-meta-tile-label">Avg file size</span>
          <span className="td-meta-tile-value">
            {stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(1)} MB` : "-"}
          </span>
        </div>
      </div>

      <div className="td-meta-tile td-meta-tile-wide">
        {isSkewed ? (
          <AlertTriangle className="td-meta-tile-icon" style={{ color: "var(--gradeC, #a9791e)" }} />
        ) : (
          <CheckCircle2 className="td-meta-tile-icon" style={{ color: "var(--gradeA, #1e8e5a)" }} />
        )}
        <div className="td-meta-tile-body" style={{ flex: 1 }}>
          <span className="td-meta-tile-label">Partition skew</span>
          <span className="td-meta-tile-value">
            {skew != null ? `${skew.toFixed(2)}x` : "-"}
            {isSkewed ? (
              <Badge variant="warning" className="td-metric-hint-tag ml-1">
                rebalance candidate
              </Badge>
            ) : null}
          </span>
          {skewFactor ? (
            <span className="td-part-bar-wrap" style={{ width: 140 }}>
              <span
                className="td-part-bar"
                style={{ width: `${skewFactor.pct}%`, background: scoreFactorColor(skewFactor.level) }}
              />
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
};
