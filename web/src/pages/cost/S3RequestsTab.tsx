import { Alert, Card, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from "antd";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  XAxis,
  YAxis
} from "recharts";
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
import { chartAxisTick, chartTooltipProps, cryoColors } from "../../lib/theme";
import { formatBytesGb, formatCurrency, formatNumber, formatTime } from "../../lib/format";

/**
 * S3 Requests tab — fleet-wide rollup of per-bucket S3 request costs (PUT/COPY/POST/LIST
 * + GET/SELECT/HEAD) sourced from the per-bucket S3 access-log ingestion pipeline
 * (docs/s3-request-cost-tracking.md §1, §3, §5).
 */
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
            getSourceS3RequestCostRollup(api, source.id, { lookback_days: 30 }).catch(
              () => null
            )
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

  const fleetCount = (rollups.data ?? []).reduce(
    (sum, r) => sum + (r.rollup?.total_request_count_30d ?? 0),
    0
  );
  const fleetCost = (rollups.data ?? []).reduce(
    (sum, r) => sum + (r.rollup?.total_cost_usd_month ?? 0),
    0
  );
  const fleetBuckets = (rollups.data ?? []).reduce(
    (sum, r) => sum + r.buckets.filter((b) => b.enabled).length,
    0
  );
  const fleetApproximate = (rollups.data ?? []).some((r) => r.rollup?.approximate === true);

  const barData = (rollups.data ?? [])
    .flatMap((r) =>
      r.rollup
        ? [
            {
              bucket: `${r.source.name}/${r.rollup.bucket}`,
              count: r.rollup.total_request_count_30d,
              cost: r.rollup.total_cost_usd_month
            }
          ]
        : []
    )
    .sort((a, b) => b.cost - a.cost)
    .slice(0, 12);

  return (
    <>
      {fleetApproximate ? (
        <Alert
          type="warning"
          showIcon
          className="page-alert"
          title="Some request costs are estimated using the default rate"
          description="One or more buckets used the per-call default rate because the AWS Price List API returned no usable results for their region/storage class. Grant pricing:GetProducts for live regional pricing."
        />
      ) : null}

      <Space wrap style={{ marginBottom: 12 }}>
        <Statistic
          title="Buckets with tracking enabled"
          value={fleetBuckets}
          loading={rollups.isLoading}
        />
        <Statistic
          title="Requests (30d, fleet)"
          value={fleetCount}
          loading={rollups.isLoading}
          formatter={(value) => formatNumber(Number(value))}
        />
        <Statistic
          title="Estimated monthly request cost"
          value={formatCurrency(fleetCost)}
          loading={rollups.isLoading}
          valueStyle={fleetApproximate ? { color: "#f59e0b" } : undefined}
        />
      </Space>

      <Card title="Top buckets by request cost (last 30 days)" style={{ marginBottom: 12 }}>
        {barData.length > 0 ? (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={barData} margin={{ top: 8, right: 16, bottom: 64, left: 0 }}>
              <CartesianGrid stroke={cryoColors.chartGrid} />
              <XAxis
                dataKey="bucket"
                stroke={cryoColors.inkFaint}
                tick={{ ...chartAxisTick, fontSize: 11 }}
                angle={-30}
                textAnchor="end"
                interval={0}
                height={70}
              />
              <YAxis stroke={cryoColors.inkFaint} tick={chartAxisTick} />
              <RechartsTooltip {...chartTooltipProps} />
              <Bar dataKey="cost" fill={cryoColors.primary} name="$ / 30d" />
            </BarChart>
          </ResponsiveContainer>
        ) : rollups.isLoading ? (
          <Skeleton active />
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
      </Card>

      <Card title="Per-bucket status">
        {rollups.isLoading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : (
          <Table
            rowKey={(row) =>
              `${row.source.id}__${row.rollup?.bucket ?? row.buckets[0]?.bucket ?? "unknown"}`
            }
            pagination={false}
            size="small"
            dataSource={rollups.data ?? []}
            columns={[
              {
                title: "Source",
                key: "source",
                render: (_: unknown, row) => (
                  <Space size={6}>
                    <Typography.Text strong>{row.source.name}</Typography.Text>
                    <Tag>{row.source.type}</Tag>
                  </Space>
                )
              },
              {
                title: "Bucket",
                key: "bucket",
                render: (_: unknown, row) =>
                  row.rollup ? row.rollup.bucket : row.buckets[0]?.bucket ?? "—"
              },
              {
                title: "Logging",
                key: "logging",
                render: (_: unknown, row) => {
                  const status =
                    row.rollup?.logging_status ?? row.buckets[0]?.logging_status ?? "unknown";
                  const color =
                    status === "enabled"
                      ? "success"
                      : status === "disabled"
                        ? "default"
                        : "warning";
                  return <Tag color={color}>{status}</Tag>;
                }
              },
              {
                title: "Requests (30d)",
                key: "count",
                align: "right",
                render: (_: unknown, row) =>
                  row.rollup ? formatNumber(row.rollup.total_request_count_30d) : "—"
              },
              {
                title: "PUT/LIST (30d)",
                key: "tier1",
                align: "right",
                render: (_: unknown, row) =>
                  row.rollup ? formatNumber(row.rollup.by_tier.tier1_count) : "—"
              },
              {
                title: "GET (30d)",
                key: "tier2",
                align: "right",
                render: (_: unknown, row) =>
                  row.rollup ? formatNumber(row.rollup.by_tier.tier2_count) : "—"
              },
              {
                title: "Estimated $ / mo",
                key: "cost",
                align: "right",
                render: (_: unknown, row) =>
                  row.rollup ? (
                    <Tooltip
                      title={
                        row.rollup.approximate
                          ? "Estimated using the default rate. Grant pricing:GetProducts for live regional pricing."
                          : "Live AWS pricing for this account/region."
                      }
                    >
                      <Typography.Text
                        type={row.rollup.approximate ? "warning" : undefined}
                        strong
                      >
                        {formatCurrency(row.rollup.total_cost_usd_month)}
                      </Typography.Text>
                    </Tooltip>
                  ) : (
                    "—"
                  )
              },
              {
                title: "Last ingest",
                key: "last_ingested",
                render: (_: unknown, row) =>
                  row.rollup?.last_ingested_at ? formatTime(row.rollup.last_ingested_at) : "—"
              }
            ]}
          />
        )}
      </Card>
    </>
  );
};