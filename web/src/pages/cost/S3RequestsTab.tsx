import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../lib/auth";
import {
  getSourceS3RequestCostRollup,
  listSourceS3LogConfigs,
  listSources,
  type S3LogConfigBucket,
  type S3RequestCostRollup
} from "../../api/client";
import type { SourceRead } from "../../api/types";
import { EmptyState } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { RankedBars } from "../../components/charts";
import { DataTable } from "../../components/ui/data-table";
import { MetricGrid } from "../../components/ui/metric-grid";
import { Skeleton } from "../../components/ui/skeleton";
import { MetricTile } from "../../components/ui/stat";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";

import { formatCurrency, formatNumber, formatTime } from "../../lib/format";

interface BucketRow {
  key: string;
  sourceName: string;
  sourceType: string;
  bucket: string;
  region?: string | null;
  loggingStatus: string;
  requestCount: number | null;
  tier1Count: number | null;
  tier2Count: number | null;
  costUsdMonth: number | null;
  approximate: boolean;
  lastIngestedAt?: string | null;
}

const loggingBadgeVariant = (status: string): "success" | "secondary" | "warning" => {
  if (status === "enabled") return "success";
  if (status === "disabled") return "secondary";
  return "warning";
};

const bucketColumns: ColumnDef<BucketRow, unknown>[] = [
  {
    id: "source",
    header: "Source",
    cell: ({ row }) => (
      <div className="flex items-center gap-1.5">
        <span className="font-medium">{row.original.sourceName}</span>
        <Badge variant="secondary">{row.original.sourceType}</Badge>
      </div>
    )
  },
  {
    id: "bucket",
    header: "Bucket",
    cell: ({ row }) => row.original.bucket
  },
  {
    id: "logging",
    header: "Logging",
    cell: ({ row }) => (
      <Badge variant={loggingBadgeVariant(row.original.loggingStatus)}>{row.original.loggingStatus}</Badge>
    )
  },
  {
    id: "count",
    header: "Requests (30d)",
    cell: ({ row }) =>
      row.original.requestCount !== null ? formatNumber(row.original.requestCount) : "Not measured",
    meta: { align: "right" }
  },
  {
    id: "tier1",
    header: "PUT/LIST (30d)",
    cell: ({ row }) => (row.original.tier1Count !== null ? formatNumber(row.original.tier1Count) : "Not measured"),
    meta: { align: "right" }
  },
  {
    id: "tier2",
    header: "GET (30d)",
    cell: ({ row }) => (row.original.tier2Count !== null ? formatNumber(row.original.tier2Count) : "Not measured"),
    meta: { align: "right" }
  },
  {
    id: "cost",
    header: "Estimated $ / mo",
    cell: ({ row }) =>
      row.original.costUsdMonth !== null ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className={
                row.original.approximate ? "font-semibold text-[var(--color-status-warning)]" : "font-semibold"
              }
            >
              {formatCurrency(row.original.costUsdMonth)}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {row.original.approximate
              ? "Estimated using the default rate. Grant pricing:GetProducts for live regional pricing."
              : "Live AWS pricing for this account/region."}
          </TooltipContent>
        </Tooltip>
      ) : (
        "Cost data unavailable"
      ),
    meta: { align: "right" }
  },
  {
    id: "last_ingested",
    header: "Last ingest",
    cell: ({ row }) => (row.original.lastIngestedAt ? formatTime(row.original.lastIngestedAt) : "Not measured")
  }
];

export const S3RequestsTab = () => {
  const { api, token } = useAuth();

  const sources = useQuery({
    queryKey: ["sources", token],
    queryFn: () => listSources(api),
    retry: 1
  });

  const rollups = useQuery({
    queryKey: ["s3-request-rollups", token],
    queryFn: async () => {
      const list = sources.data ?? [];
      const results: Array<{
        source: SourceRead;
        buckets: S3LogConfigBucket[];
        rollup: S3RequestCostRollup | null;
        error?: string;
      }> = [];
      for (const source of list) {
        try {
          const [buckets, rollup] = await Promise.all([
            listSourceS3LogConfigs(api, source.id),
            getSourceS3RequestCostRollup(api, source.id, { lookback_days: 30 }).catch(() => null)
          ]);
          results.push({ source, buckets, rollup });
        } catch (err) {
          results.push({ source, buckets: [], rollup: null, error: (err as Error).message });
        }
      }
      return results;
    },
    enabled: sources.isSuccess
  });

  const rollupRows = rollups.data ?? [];
  const measuredCounts = rollupRows.filter(
    (row) => row.rollup?.total_request_count_30d != null && Number.isFinite(row.rollup.total_request_count_30d)
  );
  const measuredCosts = rollupRows.filter(
    (row) => row.rollup?.total_cost_usd_month != null && Number.isFinite(row.rollup.total_cost_usd_month)
  );
  const fleetCount = measuredCounts.reduce((sum, row) => sum + (row.rollup?.total_request_count_30d ?? 0), 0);
  const fleetCost = measuredCosts.reduce((sum, row) => sum + (row.rollup?.total_cost_usd_month ?? 0), 0);
  const fleetCountLabel =
    rollupRows.length === 0 || measuredCounts.length === 0
      ? "Not measured"
      : measuredCounts.length < rollupRows.length
        ? `${formatNumber(fleetCount)} (${measuredCounts.length} of ${rollupRows.length} measured)`
        : formatNumber(fleetCount);
  const fleetCostLabel =
    rollupRows.length === 0 || measuredCosts.length === 0
      ? "Cost data unavailable"
      : measuredCosts.length < rollupRows.length
        ? `${formatCurrency(fleetCost)} (${measuredCosts.length} of ${rollupRows.length} measured)`
        : formatCurrency(fleetCost);
  const fleetBuckets = (rollups.data ?? []).reduce(
    (sum, r) => sum + r.buckets.filter((b) => b.enabled).length,
    0
  );
  const fleetApproximate = (rollups.data ?? []).some((r) => r.rollup?.approximate === true);

  const bucketRows: BucketRow[] = (rollups.data ?? []).flatMap((r): BucketRow[] => {
    if (r.rollup && (r.rollup.by_bucket?.length ?? 0) > 0) {
      const rollup = r.rollup;
      return rollup.by_bucket.map(
        (b): BucketRow => ({
          key: `${r.source.id}__${b.bucket}`,
          sourceName: r.source.name,
          sourceType: r.source.type,
          bucket: b.bucket,
          region: b.region,
          loggingStatus: b.logging_status,
          requestCount: b.total_request_count_30d,
          tier1Count: b.by_tier.tier1_count,
          tier2Count: b.by_tier.tier2_count,
          costUsdMonth: b.total_cost_usd_month,
          approximate: rollup.approximate,
          lastIngestedAt: b.last_ingested_at
        })
      );
    }
    return r.buckets.map(
      (b): BucketRow => ({
        key: `${r.source.id}__${b.bucket}`,
        sourceName: r.source.name,
        sourceType: r.source.type,
        bucket: b.bucket,
        region: b.region,
        loggingStatus: b.logging_status,
        requestCount: null,
        tier1Count: null,
        tier2Count: null,
        costUsdMonth: null,
        approximate: false,
        lastIngestedAt: b.last_ingested_at
      })
    );
  });

  const barData = bucketRows
    .filter((row) => row.costUsdMonth != null && Number.isFinite(row.costUsdMonth))
    .map((row) => ({
      bucket: `${row.sourceName}/${row.bucket}`,
      count: row.requestCount,
      cost: row.costUsdMonth as number
    }))
    .sort((a, b) => b.cost - a.cost)
    .slice(0, 12);

  return (
    <>
      {fleetApproximate ? (
        <Alert
          variant="warning"
          className="page-alert mb-3"
          title="Some request costs are estimated using the default rate"
          description="One or more buckets used the per-call default rate because the AWS Price List API returned no usable results for their region/storage class. Grant pricing:GetProducts for live regional pricing."
        />
      ) : null}

      <MetricGrid columns={3} className="mb-3">
        <MetricTile
          label="Buckets with tracking enabled"
          value={fleetBuckets}
          loading={rollups.isLoading}
        />
        <MetricTile
          label="Requests (30d, fleet)"
          value={fleetCountLabel}
          loading={rollups.isLoading}
        />
        <MetricTile
          label="Estimated monthly request cost"
          value={fleetCostLabel}
          loading={rollups.isLoading}
          emphasis={fleetApproximate}
        />
      </MetricGrid>

      <Card className="mb-3">
        <CardHeader className="pb-2">
          <CardTitle>Top buckets by request cost (last 30 days)</CardTitle>
        </CardHeader>
        <CardContent>
          {barData.length > 0 ? (
            <RankedBars
              title="Top buckets by estimated request cost"
              unit="estimated USD/month"
              provenance="estimated-pricing"
              items={barData.map((row) => ({
                id: row.bucket,
                label: row.bucket,
                value: row.cost
              }))}
            />
          ) : rollups.isLoading ? (
            <Skeleton className="h-[260px] w-full rounded-[var(--radius-default)]" />
          ) : (
            <EmptyState
              title="No S3 request-cost data yet"
              description={
                rollups.data && rollups.data.length > 0
                  ? "Buckets are discovered but no enabled integrations exist. Open a bucket's S3 log config panel on the Connections page to enable tracking."
                  : "Add a lakehouse source first, then enable S3 access-log ingestion on at least one bucket to start collecting request counts."
              }
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>Per-bucket status</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pt-0">
          {rollups.isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <DataTable
              columns={bucketColumns}
              data={bucketRows}
              getRowId={(row) => row.key}
              emptyTitle="No buckets discovered"
              emptyDescription="Enable S3 access-log ingestion on a lakehouse bucket to see per-bucket request costs."
            />
          )}
        </CardContent>
      </Card>
    </>
  );
};
