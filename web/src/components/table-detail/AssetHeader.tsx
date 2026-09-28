import {
  AlertTriangle,
  ChevronDown,
  Columns3,
  Copy,
  Database,
  DollarSign,
  FileText,
  GitBranch,
  Heart,
  History,
  Info,
  LayoutGrid,
  Loader2,
  SlidersHorizontal,
  Table,
  Trash2
} from "lucide-react";
import { toast } from "sonner";
import { CompactionTag, GradePill, PriorityTag, healthScoreColor } from "../status";
import { formatBytesGb, formatGb, formatNumber, formatTime } from "../../lib/format";
import { useWriteControl } from "../../lib/useWriteControl";
import { qualifiedTableName } from "../../lib/tableLabels";
import type { TableDetailState } from "../../pages/table-detail/useTableDetail";
import { Button } from "../ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";

type AssetHeaderProps = Pick<
  TableDetailState,
  | "navigate"
  | "row"
  | "stats"
  | "sourceName"
  | "lastSnapshotCommittedAt"
  | "score"
  | "savingsDisplay"
  | "reclaimableBytes"
  | "partitionColLabel"
  | "hideSinglePartitionKpi"
  | "scan"
  | "queuedScanRunId"
  | "handleScanAction"
>;

export const AssetHeader = ({
  navigate,
  row,
  stats,
  sourceName,
  lastSnapshotCommittedAt,
  score,
  savingsDisplay,
  reclaimableBytes,
  partitionColLabel,
  hideSinglePartitionKpi,
  scan,
  queuedScanRunId,
  handleScanAction
}: AssetHeaderProps) => {
  const scanAccess = useWriteControl("triggerScan");
  return (
  <>
    <div className="td-breadcrumbs">
      <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate("/")}>
        Home
      </span>
      <span className="td-breadcrumb-separator">/</span>
      <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate("/catalogs")}>
        Catalogs
      </span>
      <span className="td-breadcrumb-separator">/</span>
      <span
        className="td-breadcrumb-item td-breadcrumb-interactive"
        onClick={() => navigate(`/catalogs?source=${row?.source_id}`)}
      >
        {sourceName}
      </span>
      <span className="td-breadcrumb-separator">/</span>
      <span className="td-breadcrumb-item">{row?.schema_name ?? "schema"}</span>
      <span className="td-breadcrumb-separator">/</span>
      <span className="td-breadcrumb-item active">{row?.table_name ?? "table"}</span>
    </div>

    <div className="td-header-row">
      <div className="td-header-info">
        <div className="td-header-primary">
          <h1 className="td-header-title">{row ? qualifiedTableName(row) : "Loading..."}</h1>
          <div className="td-header-tags">
            {stats?.grade ? <GradePill grade={stats.grade} /> : null}
            {stats?.compaction_priority ? <PriorityTag priority={stats.compaction_priority} /> : null}
            <CompactionTag needsCompaction={stats?.needs_compaction} />
          </div>
        </div>
        <div className="td-hero-meta">
          {stats?.scan_at ? (
            <span>
              Last scan: <strong>{formatTime(stats.scan_at)}</strong>
            </span>
          ) : null}
          {lastSnapshotCommittedAt ? (
            <span>
              Last commit: <strong>{formatTime(lastSnapshotCommittedAt)}</strong>
            </span>
          ) : null}
          {row?.location ? (
            <span>
              Path:{" "}
              <Tooltip>
                <TooltipTrigger asChild>
                  <code className="td-path-value inline-block max-w-[360px] truncate align-middle text-xs">
                    {row.location}
                  </code>
                </TooltipTrigger>
                <TooltipContent>{row.location}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="td-path-copy h-6 w-6 align-middle text-muted-foreground"
                    aria-label="Copy table path"
                    onClick={() => {
                      void navigator.clipboard.writeText(row.location ?? "").then(
                        () => toast.success("Path copied"),
                        () => toast.error("Could not copy to clipboard")
                      );
                    }}
                  >
                    <Copy className="size-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Copy path</TooltipContent>
              </Tooltip>
            </span>
          ) : null}
        </div>
      </div>

      <div className="td-interaction-toolbar">
        <Button
          variant="ghost"
          size="sm"
          loading={scan.isPending}
          onClick={handleScanAction}
          disabled={!scanAccess.allowed || !row}
          title={scanAccess.reason ?? undefined}
          className="td-refresh-btn text-xs font-medium text-muted-foreground"
          aria-label={scan.isPending ? "Refreshing stats" : queuedScanRunId ? "Open scan run" : "Refresh stats"}
        >
          {!scan.isPending ? <Loader2 className="size-3.5" /> : null}
          {scan.isPending ? "Refreshing stats..." : queuedScanRunId ? "Open scan run" : "Refresh stats"}
        </Button>
      </div>
    </div>

    <section className="td-metrics-panel" aria-label="Table summary">
      <div className="td-key-metrics">
        <AttributionItem
          icon={<Heart style={{ color: healthScoreColor(score) }} className="size-3.5" />}
          label="Health Score"
          value={formatNumber(score)}
          valueClassName="td-health-score"
          valueStyle={{ color: healthScoreColor(score) }}
        />
        <AttributionItem
          icon={<AlertTriangle className="size-3.5" />}
          label="Small files"
          iconBg={stats?.small_files_count ? "rgba(240, 160, 32, 0.08)" : undefined}
          iconColor={stats?.small_files_count ? "var(--warning, #f0a020)" : undefined}
          value={
           <>
             {formatNumber(stats?.small_files_count, true)}
             {stats?.total_files && stats.small_files_count != null ? (
               <span className="td-attribution-subtle">
                 {((stats.small_files_count / stats.total_files) * 100).toFixed(0)}%
               </span>
             ) : null}
           </>
          }
        />
        <AttributionItem
          icon={<DollarSign className="size-3.5" />}
          iconBg="rgba(30, 142, 90, 0.06)"
          iconColor="var(--savings, #1e8e5a)"
          label="Potential savings"
          value={savingsDisplay}
          valueClassName="td-savings-value"
        />
        <AttributionItem
          icon={<Database className="size-3.5" />}
          label="Total size"
          value={formatGb(stats?.total_size_gb)}
        />
      </div>
      <details className="td-metrics-disclosure">
        <summary>
          <span>More table metrics</span>
          <span className="td-metrics-summary-hint">
           {reclaimableBytes > 0
             ? `${formatBytesGb(reclaimableBytes)} reclaimable`
             : "Storage, partitioning and metadata"}
          </span>
          <ChevronDown className="size-3.5" />
        </summary>
        <div className="td-secondary-metrics">
          <AttributionItem icon={<Table className="size-3.5" />} label="Records" value={formatNumber(stats?.total_records, true)} />
          <AttributionItem
           icon={<Columns3 className="size-3.5" />}
           label="Partition column"
           value={
             <>
               {partitionColLabel}
               {stats?.partition_col_type ? (
                 <span className="td-attribution-subtle">({stats.partition_col_type})</span>
               ) : null}
             </>
           }
           title={partitionColLabel}
          />
          {!hideSinglePartitionKpi ? (
          <AttributionItem
            icon={<LayoutGrid className="size-3.5" />}
            label="Partitions"
            value={formatNumber(stats?.total_partitions)}
          />
          ) : null}
          <AttributionItem icon={<FileText className="size-3.5" />} label="Files" value={formatNumber(stats?.total_files, true)} />
          <AttributionItem icon={<History className="size-3.5" />} label="Snapshots" value={formatNumber(stats?.total_snapshots)} />
          <AttributionItem icon={<GitBranch className="size-3.5" />} label="Manifests" value={formatNumber(stats?.manifest_count)} />
          <AttributionItem
           icon={<Trash2 className="size-3.5" />}
           label="Delete files"
           iconBg={stats?.total_delete_files ? "rgba(220, 53, 69, 0.08)" : undefined}
           iconColor={stats?.total_delete_files ? "var(--error, #dc3545)" : undefined}
           value={formatNumber(stats?.total_delete_files)}
          />
          <AttributionItem
           icon={<Info className="size-3.5" />}
           label="Average file size"
           value={stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(1)} MB` : "-"}
          />
          <AttributionItem
           icon={<SlidersHorizontal className="size-3.5" />}
           label="Partition skew"
           iconBg={
             stats?.partition_skew_ratio && stats.partition_skew_ratio > 4
               ? "rgba(240, 160, 32, 0.08)"
               : undefined
           }
           iconColor={
             stats?.partition_skew_ratio && stats.partition_skew_ratio > 4
               ? "var(--warning, #f0a020)"
               : undefined
           }
           value={stats?.partition_skew_ratio != null ? `${stats.partition_skew_ratio.toFixed(2)}x` : "-"}
          />
        </div>
      </details>
    </section>
  </>
  );
};

const AttributionItem = ({
  icon,
  label,
  value,
  title,
  valueClassName,
  valueStyle,
  iconBg,
  iconColor
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  title?: string;
  valueClassName?: string;
  valueStyle?: React.CSSProperties;
  iconBg?: string;
  iconColor?: string;
}) => (
  <div className="td-attribution-item">
    <div
      className="td-attribution-icon"
      style={{ backgroundColor: iconBg, color: iconColor }}
    >
      {icon}
    </div>
    <div className="td-attribution-content">
      <span className="td-attribution-label">{label}</span>
      <span className={`td-attribution-value${valueClassName ? ` ${valueClassName}` : ""}`} style={valueStyle} title={title}>
        {value}
      </span>
    </div>
  </div>
);
