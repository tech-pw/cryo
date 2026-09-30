import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { TableStatsRead } from "../../../api/types";
import { formatNumber } from "../../../lib/format";
import { ChartDataTable } from "../../ui/chart-data-table";
import { buildSizeHistogram, formatTarget, type SizeBand } from "./fileSizeBuckets";
import { OverviewCard } from "./OverviewCard";

const BAND_LABEL: Record<SizeBand, string> = {
  small: "Tiny",
  below: "Undersized",
  target: "Near target",
  over: "Oversized"
};

/** Band edges in words, so the legend never reads like the scanner's candidate count. */
const bandRange = (band: SizeBand, target: number): string => {
  if (band === "small") return `under ${formatTarget(target / 8)}`;
  if (band === "below") return `${formatTarget(target / 8)}–${formatTarget(target / 2)}`;
  if (band === "target") return `${formatTarget(target / 2)}–${formatTarget(target * 2)}`;
  return `over ${formatTarget(target * 2)}`;
};

const known = (value: number | null | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

const Fact = ({ label, value, tone }: { label: string; value: string; tone?: "attention" }) => (
  <div className="td-layout-fact" data-tone={tone}>
    <dt>{label}</dt>
    <dd>{value}</dd>
  </div>
);

export const FileLayoutCard = ({ stats, tableId }: { stats: TableStatsRead | null | undefined; tableId: string }) => {
  const histogram = buildSizeHistogram(stats);
  const peak = histogram ? Math.max(...histogram.buckets.map((bucket) => bucket.files), 1) : 1;
  const partitions = known(stats?.total_partitions) ? stats.total_partitions : null;
  const skew = known(stats?.partition_skew_ratio) && partitions != null && partitions > 1 ? stats.partition_skew_ratio : null;
  const deletes = known(stats?.total_delete_files) ? stats.total_delete_files : null;
  const avg = known(stats?.avg_file_size_mb) ? stats.avg_file_size_mb : null;
  const offTarget = histogram ? histogram.byBand.small + histogram.byBand.below : null;

  return (
    <OverviewCard
      id="overview-layout"
      title="File layout"
      description="How data files are sized against the write target. Many small files mean slow planning and more S3 requests."
      className="td-ov-layout"
    >
      {histogram ? (
        <>
          <p className="td-layout-headline">
            <strong>{formatNumber(offTarget ?? 0)}</strong> of {formatNumber(histogram.totalFiles)} files are below{" "}
            {formatTarget(histogram.targetBytes)}
            {histogram.targetAssumed ? " (Iceberg default target)" : " target"}
          </p>
          <div
            className="td-hist"
            role="img"
            aria-label={`File size distribution: ${(Object.keys(BAND_LABEL) as SizeBand[])
              .map((band) => `${histogram.byBand[band]} ${BAND_LABEL[band].toLowerCase()}`)
              .join(", ")}.`}
          >
            {histogram.buckets.map((bucket) => (
              <div key={bucket.key} className="td-hist-col" data-band={bucket.band} data-target={bucket.containsTarget || undefined}>
                <span className="td-hist-count">{bucket.files > 0 ? formatNumber(bucket.files) : ""}</span>
                <div className="td-hist-track">
                  <span style={{ height: `${bucket.files === 0 ? 0 : Math.max(3, (bucket.files / peak) * 100)}%` }} />
                </div>
                <span className="td-hist-label">{bucket.label}</span>
              </div>
            ))}
          </div>
          <ul className="td-hist-legend">
            {(Object.keys(BAND_LABEL) as SizeBand[]).map((band) =>
              histogram.byBand[band] > 0 || band === "target" ? (
                <li key={band} data-band={band}>
                  <i aria-hidden="true" />
                  {BAND_LABEL[band]} <span className="td-hist-range">{bandRange(band, histogram.targetBytes)}</span>{" "}
                  <b>{formatNumber(histogram.byBand[band])}</b>
                </li>
              ) : null
            )}
            <li className="td-hist-target-key"><i aria-hidden="true" />Target bucket</li>
          </ul>
          <ChartDataTable
            caption="Data files by size range"
            toggleLabel="View distribution as table"
            rows={histogram.buckets}
            getRowKey={(row) => row.key}
            columns={[
              { key: "range", header: "Size range", render: (row) => row.label },
              { key: "band", header: "Band", render: (row) => BAND_LABEL[row.band] },
              { key: "files", header: "Files", align: "right", render: (row) => formatNumber(row.files) }
            ]}
          />
        </>
      ) : (
        <p className="td-overview-caption">
          The size distribution wasn't recorded by the last scan. A full rescan records it.
        </p>
      )}
      <dl className="td-layout-facts">
        <Fact label="Average file" value={avg == null ? "—" : `${avg.toFixed(avg >= 10 ? 0 : 1)} MiB`} />
        <Fact label="Partitions" value={partitions == null ? "—" : formatNumber(partitions)} />
        <Fact
          label="Partition skew"
          value={skew == null ? (partitions === 1 ? "Unpartitioned" : "—") : `${skew.toFixed(skew >= 10 ? 0 : 1)}×`}
          tone={skew != null && skew > 10 ? "attention" : undefined}
        />
        <Fact label="Delete files" value={deletes == null ? "—" : formatNumber(deletes)} tone={deletes ? "attention" : undefined} />
      </dl>
      <Link className="td-overview-link" to={`/tables/${tableId}?tab=files`}>
        Partitions and files <ArrowUpRight size={14} aria-hidden="true" />
      </Link>
    </OverviewCard>
  );
};
