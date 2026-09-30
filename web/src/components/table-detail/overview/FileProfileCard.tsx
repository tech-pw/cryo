import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { TableStatsRead } from "../../../api/types";
import { formatDecimal, formatNumber } from "../../../lib/format";
import { OverviewCard } from "./OverviewCard";

const known = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;

export const candidateFileShare = (small: number | null | undefined, total: number | null | undefined): number | null =>
  known(small) && known(total) && total > 0 && small <= total ? small / total * 100 : null;

export const FileProfileCard = ({ stats, tableId }: { stats: TableStatsRead | null | undefined; tableId: string }) => {
  const share = candidateFileShare(stats?.small_files_count, stats?.total_files);
  const metrics = [
    { label: "Average data file", value: known(stats?.avg_file_size_mb) ? `${formatDecimal(stats.avg_file_size_mb)} MiB` : "Not measured", hint: "Data bytes divided by data files." },
    { label: "Delete files", value: known(stats?.total_delete_files) ? formatNumber(stats.total_delete_files) : "Not measured", hint: "Position and equality delete files." },
    { label: "Manifests", value: known(stats?.manifest_count) ? formatNumber(stats.manifest_count) : "Not measured", hint: "Metadata files used to plan reads." },
    { label: "Partition file skew", value: known(stats?.partition_skew_ratio) && known(stats?.total_partitions) && stats.total_partitions > 1 ? `${formatDecimal(stats.partition_skew_ratio)}×` : stats?.total_partitions === 1 ? "Single partition" : "Not measured", hint: "Largest file count ÷ max(median, 1)." }
  ];
  return (
    <OverviewCard id="overview-files" title="File profile" description="How the table is laid out for reads and maintenance." className="td-overview-files">
      <div className="td-file-candidates"><div><span>Small-file candidates</span><strong>{share == null ? "Not measured" : `${formatDecimal(share)}%`}</strong></div><p>{known(stats?.small_files_count) && known(stats?.total_files) ? `${formatNumber(stats.small_files_count)} of ${formatNumber(stats.total_files)} data files` : "Candidate counts unavailable"}</p></div>
      {share != null ? <div className="td-candidate-track" role="img" aria-label={`${formatDecimal(share)} percent of data files are scanner-reported small-file compaction candidates`}><span style={{ width: `${share}%` }} /></div> : null}
      <p className="td-overview-caption">Candidates in partitions selected by the scanner. The health score uses separate small-file checks.</p>
      <dl className="td-file-profile-metrics">{metrics.map((metric) => <div key={metric.label}><dt>{metric.label}</dt><dd>{metric.value}</dd><p>{metric.hint}</p></div>)}</dl>
      <Link className="td-overview-link" to={`/tables/${tableId}?tab=files`}>Explore schema & files <ArrowUpRight size={14} aria-hidden="true" /></Link>
    </OverviewCard>
  );
};
