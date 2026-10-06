import { useQueries } from "@tanstack/react-query";
import type { AwsSourceStatus } from "../../components/AwsStatusBadge";
import { MetricTile } from "../../components/ui/stat";
import { getAwsIntegration } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { useConnectionsContext } from "./ConnectionsContext";

const formatRelative = (value?: string | null): string => {
  if (!value) return "Never";
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return "Never";
  const deltaSec = Math.round((Date.now() - ts) / 1000);
  if (deltaSec < 60) return `${deltaSec}s ago`;
  const min = Math.round(deltaSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
};

const isAwsUnhealthy = (status: AwsSourceStatus | undefined): boolean => {
  if (!status) return false;
  if (status.kind === "error") return true;
  if (status.kind === "connected" && status.enabled && status.lastTestOk === false) return true;
  return false;
};

const integrationHealthy = (
  item: { enabled: boolean; last_test_ok?: boolean | null; has_api_key?: boolean; has_webhook?: boolean }
): boolean => {
  if (!item.enabled) return true;
  if (item.last_test_ok === false) return false;
  if (item.has_api_key === false) return false;
  if (item.has_webhook === false) return false;
  return true;
};

export const ConnectionsKpiRow = () => {
  const { token } = useAuth();
  const { api, sources, awsStatuses, destinations, aiProviders, slackConfigs } = useConnectionsContext();

  const enabledSources = (sources.data ?? []).filter((s) => s.enabled);
  const awsUnhealthyCount = enabledSources.filter((s) => isAwsUnhealthy(awsStatuses[s.id])).length;
  const lakehousesOk = enabledSources.length - awsUnhealthyCount;

  const enabledDestinations = (destinations.data ?? []).filter((d) => d.enabled);

  const enabledIntegrations = [
    ...(aiProviders.data ?? []).filter((p) => p.enabled),
    ...(slackConfigs.data ?? []).filter((c) => c.enabled)
  ];
  const integrationsOk = enabledIntegrations.filter((item) => integrationHealthy(item)).length;

  const sourceIds = (sources.data ?? []).map((s) => s.id);
  const awsIntegrationQueries = useQueries({
    queries: sourceIds.map((id) => ({
      queryKey: ["aws-integration-badge", id, token],
      queryFn: () => getAwsIntegration(api, id),
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 30_000
    }))
  });

  const lastTestedAt = awsIntegrationQueries.reduce<string | null>((latest, result) => {
    const testedAt = result.data?.last_tested_at;
    if (!testedAt) return latest;
    if (!latest || new Date(testedAt).getTime() > new Date(latest).getTime()) return testedAt;
    return latest;
  }, null);

  const loading =
    sources.isLoading ||
    destinations.isLoading ||
    aiProviders.isLoading ||
    slackConfigs.isLoading ||
    awsIntegrationQueries.some((q) => q.isLoading);

  const lakehouseDelta =
    awsUnhealthyCount > 0
      ? { value: `${awsUnhealthyCount} AWS unhealthy`, tone: "bad" as const }
      : enabledSources.length === 0
        ? { value: "No enabled lakehouses", tone: "neutral" as const }
        : undefined;

  const integrationDelta =
    integrationsOk < enabledIntegrations.length
      ? {
          value: `${enabledIntegrations.length - integrationsOk} need attention`,
          tone: "bad" as const
        }
      : enabledIntegrations.length === 0
        ? { value: "No enabled integrations", tone: "neutral" as const }
        : undefined;

  const engineDelta =
    enabledDestinations.length === 0
      ? { value: "No enabled engines", tone: "neutral" as const }
      : undefined;

  return (
    <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricTile
        label="Lakehouses OK"
        value={enabledSources.length ? `${lakehousesOk}/${enabledSources.length}` : "0"}
        loading={sources.isLoading}
        emphasis={awsUnhealthyCount > 0}
        delta={lakehouseDelta}
      />
      <MetricTile
        label="Engines OK"
        value={enabledDestinations.length ? `${enabledDestinations.length}/${enabledDestinations.length}` : "0"}
        loading={destinations.isLoading}
        delta={engineDelta}
      />
      <MetricTile
        label="Integrations OK"
        value={enabledIntegrations.length ? `${integrationsOk}/${enabledIntegrations.length}` : "0"}
        loading={aiProviders.isLoading || slackConfigs.isLoading}
        emphasis={integrationsOk < enabledIntegrations.length}
        delta={integrationDelta}
      />
      <MetricTile
        label="Last connection test"
        value={formatRelative(lastTestedAt)}
        loading={loading}
      />
    </div>
  );
};
