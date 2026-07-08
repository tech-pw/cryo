import { Alert, Button, Card, Drawer, Result, Skeleton, Space, Statistic, Tag, Typography, message } from "antd";
import {
  CloudOutlined,
  ExperimentOutlined,
  ExclamationCircleOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getAwsUsage, listSources, testAwsIntegration } from "../../api/client";
import type { AWSUsageResponse, SourceRead, UUID } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { formatBytesGb, formatCurrency } from "../../lib/format";
import { OverviewPage } from "../../components/layout/OverviewPage";
import { EmptyState, HintLabel, StatStrip } from "../../components/common";
import { AwsIntegrationPanel } from "../../components/AwsIntegrationPanel";
import {
  AwsStatusBadge,
  useAwsSourceStatuses,
  type AwsSourceStatus
} from "../../components/AwsStatusBadge";

interface PerSourceState {
  integrationStatus: AwsSourceStatus;
  usage?: AWSUsageResponse;
  usageStatus: "idle" | "loading" | "ok" | "error";
}

const SourceCard = ({
  source,
  state,
  onManage
}: {
  source: SourceRead;
  state: PerSourceState;
  onManage: () => void;
}) => {
  const usage = state.usage;
  const status = state.integrationStatus;
  const connected = status.kind === "connected";
  return (
    <Card
      size="small"
      className="aws-source-card"
      title={
        <Space size={8} align="center">
          <CloudOutlined />
          <Typography.Text strong>{source.name}</Typography.Text>
          <Tag>{source.type}</Tag>
        </Space>
      }
      extra={<AwsStatusBadge status={status} />}
    >
      <Space orientation="vertical" size={12} className="full-width">
        {connected ? (
          <>
            <Space size={20} wrap>
              <Statistic title="Bucket" value={status.bucket} valueStyle={{ fontSize: 14 }} />
              <Statistic title="Region" value={status.region} valueStyle={{ fontSize: 14 }} />
              <Statistic
                title="Bucket size"
                value={usage ? formatBytesGb(usage.total_size_bytes) : "—"}
                loading={state.usageStatus === "loading"}
                valueStyle={{ fontSize: 14 }}
              />
              <Statistic
                title={
                  <HintLabel
                    label="$ / GB-month"
                    tooltip="Real regional price from the free AWS Price List API. Falls back to a Cryo heuristic when unavailable."
                  />
                }
                value={
                  usage?.price_per_gb_month != null
                    ? formatCurrency(usage.price_per_gb_month)
                    : "—"
                }
                valueStyle={{ fontSize: 14 }}
              />
              <Statistic
                title="Objects"
                value={
                  usage?.object_count != null ? usage.object_count.toLocaleString() : "—"
                }
                valueStyle={{ fontSize: 14 }}
              />
            </Space>
            <Space size={8} wrap>
              <Button size="small" type="primary" onClick={onManage}>
                Manage
              </Button>
              <Link to="/connections">
                <Button size="small" icon={<LinkOutlined />}>
                  Open in Connections
                </Button>
              </Link>
            </Space>
          </>
        ) : (
          <Space orientation="vertical" size={8} className="full-width">
            <Typography.Text type="secondary">
              Connect this lakehouse's S3 bucket to pull free CloudWatch storage metrics and real
              regional pricing.
            </Typography.Text>
            <Space>
              <Button
                size="small"
                type="primary"
                icon={<PlusOutlined />}
                onClick={onManage}
              >
                Connect AWS
              </Button>
            </Space>
          </Space>
        )}
      </Space>
    </Card>
  );
};

/**
 * AWS Integrations tab of the consolidated Cost page. Body lifted verbatim from the
 * legacy AwsIntegrations page. The legacy AWS integration panel is also mounted in the
 * Connections drawer (per-source configuration); both back to the same
 * <AwsIntegrationPanel> component, so the duplication is contained.
 */
export const AwsIntegrationsTab = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const [managingSourceId, setManagingSourceId] = useState<UUID | null>(null);

  const sources = useQuery({
    queryKey: ["sources", api.token],
    queryFn: () => listSources(api),
    retry: 1
  });

  const sourceIds = useMemo(() => (sources.data ?? []).map((s) => s.id), [sources.data]);
  const awsStatuses = useAwsSourceStatuses(sourceIds);

  const usageIds = useMemo(
    () =>
      sourceIds.filter(
        (id) =>
          awsStatuses[id]?.kind === "connected" &&
          (awsStatuses[id] as Extract<AwsSourceStatus, { kind: "connected" }>).enabled
      ),
    [sourceIds, awsStatuses]
  );

  const usageResults = useQuery({
    queryKey: ["aws-usages-bulk", api.token, usageIds],
    queryFn: async () => {
      const pairs = await Promise.all(
        usageIds.map(async (id) => {
          try {
            const usage = await getAwsUsage(api, id);
            return [id, { usage, status: "ok" as const }];
          } catch (err) {
            return [id, { usage: undefined, status: "error" as const, error: (err as Error).message }];
          }
        })
      );
      return Object.fromEntries(pairs) as Record<
        UUID,
        { usage?: AWSUsageResponse; status: "ok" | "error"; error?: string }
      >;
    },
    enabled: usageIds.length > 0,
    refetchInterval: 60_000
  });

  const perSource: Record<UUID, PerSourceState> = useMemo(() => {
    const map: Record<UUID, PerSourceState> = {};
    sourceIds.forEach((id) => {
      const status = awsStatuses[id] ?? { kind: "loading" };
      const usageEntry = usageResults.data?.[id];
      map[id] = {
        integrationStatus: status,
        usage: usageEntry?.usage,
        usageStatus: usageResults.isLoading
          ? "loading"
          : !usageEntry
            ? "idle"
            : usageEntry.status === "ok"
              ? "ok"
              : "error"
      };
    });
    return map;
  }, [sourceIds, awsStatuses, usageResults.data, usageResults.isLoading]);

  const managedSource = useMemo(
    () => (sources.data ?? []).find((s) => s.id === managingSourceId) ?? null,
    [sources.data, managingSourceId]
  );

  const connectedSources = useMemo(
    () =>
      Object.entries(perSource).filter(
        ([, state]) =>
          state.integrationStatus.kind === "connected" &&
          (state.integrationStatus as Extract<AwsSourceStatus, { kind: "connected" }>).enabled
      ),
    [perSource]
  );

  const totalBytes = useMemo(
    () =>
      connectedSources.reduce(
        (sum, [, state]) => sum + (state.usage?.total_size_bytes ?? 0),
        0
      ),
    [connectedSources]
  );

  const failingAuth = useMemo(
    () =>
      Object.values(perSource).filter(
        (state) =>
          state.integrationStatus.kind === "connected" &&
          (state.integrationStatus as Extract<AwsSourceStatus, { kind: "connected" }>).enabled &&
          (state.integrationStatus as Extract<AwsSourceStatus, { kind: "connected" }>)
            .lastTestOk === false
      ),
    [perSource]
  );

  const refreshing = useMutation({
    mutationFn: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["sources"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-integration"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usage"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usages-bulk"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-integration-badge"] })
      ]);
    },
    onSuccess: () => message.success("Refreshed"),
    onError: (error) => message.error((error as Error).message)
  });

  const testAll = useMutation({
    mutationFn: async () => {
      const connectedIds = sourceIds.filter((id) => {
        const s = awsStatuses[id];
        return s?.kind === "connected" && (s as Extract<AwsSourceStatus, { kind: "connected" }>).enabled;
      });
      if (connectedIds.length === 0) {
        throw new Error("No AWS integrations to test");
      }
      const results = await Promise.all(
        connectedIds.map((id) =>
          testAwsIntegration(api, id)
            .then((res) => ({ id, ok: res.ok, error: res.error ?? null }))
            .catch((err) => ({ id, ok: false, error: (err as Error).message }))
        )
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["aws-integration"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-integration-badge"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usage"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usages-bulk"] })
      ]);
      const failed = results.filter((r) => !r.ok);
      if (failed.length === 0) {
        return { okCount: results.length, failCount: 0 };
      }
      throw new Error(
        `${failed.length} of ${results.length} connections failed: ${failed
          .map((f) => f.error)
          .filter(Boolean)
          .slice(0, 2)
          .join("; ")}`
      );
    },
    onSuccess: (summary) =>
      message.success(`All ${summary.okCount} connection${summary.okCount === 1 ? "" : "s"} OK`),
    onError: (error) => message.error((error as Error).message)
  });

  const stats = [
    {
      label: "Connected lakehouses",
      value: connectedSources.length,
      tooltip: "Lakehouses with an enabled AWS integration."
    },
    {
      label: "Tracked S3 size",
      value: formatBytesGb(totalBytes),
      tooltip: "Sum of BucketSizeBytes across all connected buckets."
    },
    {
      label: "Auth failures",
      value: failingAuth.length,
      tooltip: "Integrations whose most recent STS test failed."
    },
    {
      label: "Unconfigured lakehouses",
      value: Math.max((sources.data?.length ?? 0) - connectedSources.length, 0),
      tooltip: "Lakehouses without an AWS integration configured."
    }
  ];

  return (
    <OverviewPage
      title="AWS integrations"
      subtitle="Connect S3 buckets to lakehouses so Cryo can show real CloudWatch storage metrics and AWS Price List rates — free tier only, no AWS charges."
      actions={
        <Space wrap>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refreshing.mutate()}
            loading={refreshing.isPending}
          >
            Refresh
          </Button>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={() => testAll.mutate()}
            loading={testAll.isPending}
            disabled={connectedSources.length === 0 || role === "viewer"}
          >
            Test all
          </Button>
        </Space>
      }
      loading={sources.isLoading}
      error={sources.error}
      errorTitle="Failed to load lakehouses"
      kpis={<StatStrip items={stats} />}
      preContent={
        failingAuth.length > 0 ? (
          <Alert
            type="error"
            showIcon
            icon={<ExclamationCircleOutlined />}
            title={`${failingAuth.length} integration${failingAuth.length === 1 ? "" : "s"} failing auth`}
            description="The most recent STS connection test failed for these buckets. Credentials may be rotated or revoked — open Manage to re-test."
            className="page-alert"
          />
        ) : null
      }
    >
      <Space orientation="vertical" size={12} className="full-width">
        <Alert
          type="info"
          showIcon
          title="Free-tier AWS APIs only"
          description="Cryo only calls GetMetricStatistics (not GetMetricData), the AWS Price List API, and sts:GetCallerIdentity — all free. See cryo/integrations/aws_s3_metrics.py for the full policy."
        />

        {sources.data && sources.data.length === 0 ? (
          <EmptyState
            title="No lakehouses yet"
            description="Create a lakehouse source first, then come back to connect its AWS bucket."
            action={
              <Link to="/connections">
                <Button type="primary" icon={<PlusOutlined />}>
                  Add a lakehouse
                </Button>
              </Link>
            }
          />
        ) : sources.isLoading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : (
          <div className="aws-source-grid">
            {(sources.data ?? []).map((source) => {
              const state = perSource[source.id] ?? {
                integration: null,
                integrationStatus: "loading",
                usageStatus: "idle"
              };
              return (
                <SourceCard
                  key={source.id}
                  source={source}
                  state={state}
                  onManage={() => setManagingSourceId(source.id)}
                />
              );
            })}
          </div>
        )}
      </Space>

      <Drawer
        title={
          managedSource ? (
            <Space>
              <CloudOutlined />
              <span>AWS integration · {managedSource.name}</span>
            </Space>
          ) : (
            "AWS integration"
          )
        }
        width={520}
        open={managingSourceId !== null}
        onClose={() => setManagingSourceId(null)}
        destroyOnClose
      >
        {managedSource ? (
          <AwsIntegrationPanel sourceId={managedSource.id} />
        ) : (
          <Result icon={<ExperimentOutlined />} title="Pick a lakehouse" />
        )}
      </Drawer>
    </OverviewPage>
  );
};