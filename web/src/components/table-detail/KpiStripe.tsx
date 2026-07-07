import {
  ApartmentOutlined,
  DatabaseOutlined,
  DollarOutlined,
  FileTextOutlined,
  HeartOutlined,
  WarningOutlined
} from "@ant-design/icons";
import type { CSSProperties } from "react";
import type { TableStatsRead } from "../../api/types";
import { healthScoreColor } from "../status";
import { formatBytesGb, formatCurrency, formatGb, formatNumber } from "../../lib/format";

interface KpiStripeProps {
  stats?: TableStatsRead | null;
  savingsUsd: number;
  reclaimableBytes: number;
}

const smallFileRate = (stats?: TableStatsRead | null): number => {
  if (!stats?.total_files || !stats?.small_files_count) return 0;
  return stats.small_files_count / stats.total_files;
};

const impactClassForSmallFiles = (rate: number): string => {
  if (rate >= 0.3) return "td-kpi-impact-hot";
  if (rate >= 0.15) return "td-kpi-impact-warm";
  return "td-kpi-impact-cool";
};

export const KpiStripe = ({ stats, savingsUsd, reclaimableBytes }: KpiStripeProps) => {
  const score = stats?.overall_score ?? 0;
  const smallRate = smallFileRate(stats);
  return (
    <div className="td-kpi-stripe">
      <div
        className="td-kpi-tile"
        style={{ "--kpi-accent": healthScoreColor(score) } as CSSProperties}
      >
        <HeartOutlined className="td-kpi-icon" />
        <div className="td-kpi-content">
          <span className="td-kpi-label">Health Score</span>
          <span className="td-kpi-value">{formatNumber(score)}</span>
        </div>
      </div>

      <div className="td-kpi-tile td-kpi-impact-cool">
        <DatabaseOutlined className="td-kpi-icon" />
        <div className="td-kpi-content">
          <span className="td-kpi-label">Total Size</span>
          <span className="td-kpi-value">{formatGb(stats?.total_size_gb)}</span>
        </div>
      </div>

      <div className="td-kpi-tile td-kpi-impact-cool">
        <FileTextOutlined className="td-kpi-icon" />
        <div className="td-kpi-content">
          <span className="td-kpi-label">Files</span>
          <span className="td-kpi-value">{formatNumber(stats?.total_files, true)}</span>
          <span className="td-kpi-subtle">
            {stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(0)} MB avg` : ""}
          </span>
        </div>
      </div>

      <div className={`td-kpi-tile ${impactClassForSmallFiles(smallRate)}`}>
        <WarningOutlined className="td-kpi-icon" />
        <div className="td-kpi-content">
          <span className="td-kpi-label">Small Files</span>
          <span className="td-kpi-value">{formatNumber(stats?.small_files_count, true)}</span>
          <span className="td-kpi-subtle">{(smallRate * 100).toFixed(0)}% of total</span>
        </div>
      </div>

      <div className="td-kpi-tile td-kpi-impact-cool">
        <ApartmentOutlined className="td-kpi-icon" />
        <div className="td-kpi-content">
          <span className="td-kpi-label">Partitions</span>
          <span className="td-kpi-value">{formatNumber(stats?.total_partitions)}</span>
        </div>
      </div>

      <div className={`td-kpi-tile ${savingsUsd > 0 ? "td-kpi-impact-cool" : "td-kpi-impact-muted"}`}>
        <DollarOutlined className="td-kpi-icon" />
        <div className="td-kpi-content">
          <span className="td-kpi-label">Monthly Savings</span>
          <span className="td-kpi-value">{formatCurrency(savingsUsd)}</span>
          <span className="td-kpi-subtle">{formatBytesGb(reclaimableBytes)} reclaimable</span>
        </div>
      </div>
    </div>
  );
};
